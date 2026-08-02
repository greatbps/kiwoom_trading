"""
Gate Analysis — Closed Loop Performance Evaluation

게이트별 Funnel + Opportunity Cost + Precision/Recall 통합 분석.

데이터 흐름:
  Decision DB (REJECT/PASS)
      ↓
  Future Return (returns_collector.py 수집 후)
      ↓
  Gate Analysis (이 파일)
      ↓
  필터 완화 / 강화 의사결정

지표 정의:
  FN Rate (False Negative Rate)
    = REJECT 중 미래수익률 > threshold 비율
    = "좋은 기회를 얼마나 놓쳤는가"
    → 높으면 필터 완화 검토

  TN Rate (True Negative Rate)
    = REJECT 중 미래수익률 ≤ 0% 비율
    = "나쁜 종목을 얼마나 올바르게 차단했는가"
    → 낮으면 필터가 실제로 별로 도움이 안 됨

  Precision (PASS 기준)
    = PASS 중 미래수익률 > threshold 비율
    = "진입 결정의 정확도"

  Recall (전체 기준)
    = 전체 좋은 기회 중 PASS까지 도달한 비율
    = TotalGoodOpportunities / (FN total + PASS good)

실행:
  python3 -m analysis.gate_analysis                   # 최근 7일
  python3 -m analysis.gate_analysis --days 14
  python3 -m analysis.gate_analysis --threshold 3.0   # 3% = "좋은 기회" 기준
  python3 -m analysis.gate_analysis --horizon +1h     # 지표 기준 horizon
  python3 -m analysis.gate_analysis --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import yaml

_CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'research_config.yaml'


def _load_label_schema() -> dict:
    """research_config.yaml에서 label_schema 로드."""
    try:
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8'))
        return cfg.get('label_schema', {})
    except Exception:
        return {}

# Gate 순서 (gate_funnel.py 와 동일 — 리포트 정렬 기준)
GATE_ORDER = [
    'GLOBAL_GATE_BLOCKED',
    'MARKET_SENSOR_BLOCKED',
    'STOCK_GATE_BLOCKED',
    'DATA_INSUFFICIENT',
    'VOLUME_INSUFFICIENT',
    'CHOCH_MISSING',
    'SWEEP_MISSING',
    'FVG_MISSING',
    'CONFIDENCE_LOW',
    'RISK_SCORE_HIGH',
    'POLICY_MISMATCH',
    'OTHER',
]


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── Query ───────────────────────────────────────────────────────

def _query_reject_stats(
    cur, start_date: date, end_date: date,
    horizon: str, threshold: float,
) -> List[Dict]:
    """
    게이트별 REJECT 성과 통계.
    future_return_events가 수집된 결정만 포함 (아직 수집 안 된 것 제외).
    """
    sd, ed = str(start_date), str(end_date)
    cur.execute("""
        SELECT
            d.decision_reason_code                                         AS gate,
            COUNT(*)                                                        AS total,
            AVG(fr.return_pct)                                              AS avg_return,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY fr.return_pct)   AS p25,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY fr.return_pct)   AS median,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY fr.return_pct)   AS p75,
            COUNT(CASE WHEN fr.return_pct > %(thr)s THEN 1 END)           AS fn_count,
            COUNT(CASE WHEN fr.return_pct <= 0      THEN 1 END)           AS tn_count,
            COUNT(CASE WHEN fr.return_pct > 0
                        AND fr.return_pct <= %(thr)s THEN 1 END)          AS small_pos_count
        FROM research.decision_ledger d
        JOIN research.future_return_events fr
            ON  fr.decision_id   = d.decision_id
            AND fr.horizon_label = %(horizon)s
        WHERE d.decision            = 'REJECT'
          AND d.decided_at::date BETWEEN %(sd)s AND %(ed)s
        GROUP BY d.decision_reason_code
    """, {'sd': sd, 'ed': ed, 'horizon': horizon, 'thr': threshold})
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _query_pass_stats(
    cur, start_date: date, end_date: date,
    horizon: str, threshold: float,
) -> Dict:
    """PASS 결정의 Precision 계산."""
    sd, ed = str(start_date), str(end_date)
    cur.execute("""
        SELECT
            COUNT(*)                                              AS total,
            AVG(fr.return_pct)                                   AS avg_return,
            COUNT(CASE WHEN fr.return_pct > %(thr)s THEN 1 END) AS tp_count,
            COUNT(CASE WHEN fr.return_pct <= 0      THEN 1 END) AS fp_count
        FROM research.decision_ledger d
        JOIN research.future_return_events fr
            ON  fr.decision_id   = d.decision_id
            AND fr.horizon_label = %(horizon)s
        WHERE d.decision            = 'PASS'
          AND d.decided_at::date BETWEEN %(sd)s AND %(ed)s
    """, {'sd': sd, 'ed': ed, 'horizon': horizon, 'thr': threshold})
    row = cur.fetchone()
    if not row:
        return {}
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row))


def _query_coverage(
    cur, start_date: date, end_date: date, horizon: str,
) -> Dict[str, int]:
    """
    전체 결정 건수 vs 미래수익률 수집 완료 건수 비교.
    수집률이 낮으면 리포트 신뢰도도 낮음.
    """
    sd, ed = str(start_date), str(end_date)
    cur.execute("""
        SELECT
            COUNT(DISTINCT d.decision_id)                        AS total_decisions,
            COUNT(DISTINCT fr.decision_id)                       AS with_return
        FROM research.decision_ledger d
        LEFT JOIN research.future_return_events fr
            ON  fr.decision_id   = d.decision_id
            AND fr.horizon_label = %(horizon)s
        WHERE d.decided_at::date BETWEEN %(sd)s AND %(ed)s
    """, {'sd': sd, 'ed': ed, 'horizon': horizon})
    row = cur.fetchone()
    return {'total': row[0], 'with_return': row[1]} if row else {}


# ─── Analysis Builder ─────────────────────────────────────────────

def build_analysis(
    reject_stats: List[Dict],
    pass_stats: Dict,
    threshold: float,
) -> Dict[str, Any]:
    """
    게이트별 FN Rate / TN Rate + 전체 Recall 계산.
    """
    # 정렬: GATE_ORDER 기준, 나머지는 뒤에 붙이기
    order_map = {g: i for i, g in enumerate(GATE_ORDER)}
    sorted_rows = sorted(
        reject_stats,
        key=lambda r: (order_map.get(r['gate'], 99), r['gate'] or ''),
    )

    gate_rows = []
    total_fn = 0  # 전체 False Negative 건수 (missed good opportunities)

    for r in sorted_rows:
        total     = int(r['total'])
        fn_count  = int(r['fn_count'])
        tn_count  = int(r['tn_count'])
        fn_rate   = round(fn_count / total * 100, 1) if total > 0 else 0.0
        tn_rate   = round(tn_count / total * 100, 1) if total > 0 else 0.0
        avg_ret   = round(float(r['avg_return']), 2) if r['avg_return'] is not None else None
        median    = round(float(r['median']),     2) if r['median']     is not None else None
        total_fn += fn_count

        gate_rows.append({
            'gate':       r['gate'],
            'total':      total,
            'fn_count':   fn_count,
            'tn_count':   tn_count,
            'fn_rate':    fn_rate,
            'tn_rate':    tn_rate,
            'avg_return': avg_ret,
            'median':     median,
            'p25':        round(float(r['p25']), 2) if r['p25'] is not None else None,
            'p75':        round(float(r['p75']), 2) if r['p75'] is not None else None,
        })

    # PASS Precision
    pass_total  = int(pass_stats.get('total', 0))
    pass_tp     = int(pass_stats.get('tp_count', 0))
    pass_fp     = int(pass_stats.get('fp_count', 0))
    precision   = round(pass_tp / pass_total * 100, 1) if pass_total > 0 else None
    pass_avg    = round(float(pass_stats['avg_return']), 2) if pass_stats.get('avg_return') else None

    # Recall = 진짜 좋은 기회 중 PASS까지 도달한 비율
    #        = pass_tp / (total_fn + pass_tp)
    total_good = total_fn + pass_tp
    recall = round(pass_tp / total_good * 100, 1) if total_good > 0 else None

    return {
        'gates':       gate_rows,
        'pass': {
            'total':      pass_total,
            'tp':         pass_tp,
            'fp':         pass_fp,
            'precision':  precision,
            'avg_return': pass_avg,
        },
        'overall': {
            'recall':     recall,
            'total_fn':   total_fn,
            'total_good': total_good,
        },
    }


# ─── Printer ─────────────────────────────────────────────────────

def print_analysis(
    result: Dict,
    threshold: float,
    horizon: str,
    coverage: Dict,
    start_date: date,
    end_date: date,
) -> None:
    schema    = _load_label_schema()
    schema_v  = schema.get('version', '?')
    eff_from  = schema.get('effective_from', '?')
    sd, ed = str(start_date), str(end_date)
    period = sd if sd == ed else f"{sd} ~ {ed}"
    cov_total = coverage.get('total', 0)
    cov_done  = coverage.get('with_return', 0)
    cov_pct   = round(cov_done / cov_total * 100, 1) if cov_total > 0 else 0.0

    print(f"\n{'='*80}")
    print(f"  Gate Precision/Recall Analysis — {period}")
    print(f"  Label Schema: {schema_v} (from {eff_from})  |  "
          f"Horizon: {horizon}  |  GOOD>={threshold}%")
    print(f"  데이터 커버리지: {cov_done}/{cov_total} ({cov_pct:.1f}%)  "
          f"{'✅' if cov_pct >= 80 else '⚠️ 데이터 부족 — 결과 참고만'}")
    print(f"{'='*80}")

    if not result['gates']:
        print("  [!] 분석 데이터 없음 — returns_collector 실행 후 재시도")
        print(f"{'='*80}\n")
        return

    # ─── Gate별 Reject 분석 ──
    print(f"\n  [REJECT 분석] FN Rate = 놓친 기회 비율, TN Rate = 올바른 차단 비율\n")
    print(f"  {'Gate':<25} {'N':>5}  {'avgRet':>8}  {'median':>7}  "
          f"{'FN_cnt':>7}  {'FN%':>6}  {'TN%':>6}  {'판정'}")
    print(f"  {'-'*25} {'-'*5}  {'-'*8}  {'-'*7}  "
          f"{'-'*7}  {'-'*6}  {'-'*6}  {'-'*12}")

    for r in result['gates']:
        fn_flag  = '⚠️  완화검토' if r['fn_rate'] >= 30 else ''
        tn_flag  = '❓ 효과의심' if r['tn_rate'] < 40 and r['total'] >= 10 else ''
        verdict  = fn_flag or tn_flag or ''
        avg_s    = f"{r['avg_return']:+.1f}%" if r['avg_return'] is not None else '    -'
        med_s    = f"{r['median']:+.1f}%"     if r['median']     is not None else '    -'
        print(
            f"  {(r['gate'] or '-'):<25} {r['total']:>5}  {avg_s:>8}  {med_s:>7}  "
            f"{r['fn_count']:>7}  {r['fn_rate']:>5.1f}%  {r['tn_rate']:>5.1f}%  {verdict}"
        )

    # ─── PASS Precision ──
    p = result['pass']
    o = result['overall']
    print(f"\n  [PASS 분석] Precision = 진입 정확도\n")
    if p['total'] > 0:
        print(f"  PASS 결정   : {p['total']}건")
        print(f"  avg return  : {p['avg_return']:+.2f}%" if p['avg_return'] is not None else "  avg return  : -")
        print(f"  Precision   : {p['precision']}%  "
              f"({p['tp']}건 > {threshold}% / {p['total']}건)")
    else:
        print("  PASS 결정 없음 (데이터 대기)")

    # ─── 전체 Recall ──
    print(f"\n  [Overall Recall]\n")
    if o['recall'] is not None:
        print(f"  Recall      : {o['recall']}%  "
              f"({p['tp']}건 통과 / {o['total_good']}건 전체 좋은 기회)")
        print(f"  놓친 기회   : {o['total_fn']}건 (FN 합계)")
    else:
        print("  Recall 계산 불가 (PASS 데이터 없음)")

    # ─── 전략 시사점 ──
    print(f"\n  [전략 시사점]")
    recommendations = []
    for r in result['gates']:
        if r['total'] < 10:
            continue
        if r['fn_rate'] >= 40:
            recommendations.append(
                f"  ⚠️  {r['gate']}: FN={r['fn_rate']}% — "
                f"필터가 좋은 기회를 {r['fn_count']}건 차단. 임계값 완화 검토."
            )
        elif r['fn_rate'] >= 20:
            recommendations.append(
                f"  🔍 {r['gate']}: FN={r['fn_rate']}% — 주목 (20~40% 범위)."
            )
        if r['tn_rate'] < 35 and r['total'] >= 20:
            recommendations.append(
                f"  ❓ {r['gate']}: TN={r['tn_rate']}% — "
                f"실제 나쁜 종목 차단 효과 낮음. 필터 기여도 재검토."
            )
    if recommendations:
        for rec in recommendations:
            print(rec)
    else:
        print("  데이터 부족 또는 모든 게이트가 적절한 범위 내에 있음.")

    print(f"\n{'='*80}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Gate Precision/Recall Analysis — Closed Loop Evaluation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python3 -m analysis.gate_analysis                     # 최근 7일, EOD 기준
  python3 -m analysis.gate_analysis --days 14
  python3 -m analysis.gate_analysis --horizon +1h       # 1시간 후 기준
  python3 -m analysis.gate_analysis --threshold 5.0     # 5% 초과 = 좋은 기회
  python3 -m analysis.gate_analysis --json
"""
    )
    # label_schema에서 기본값 로드 (args로 override 가능)
    _schema = _load_label_schema()

    parser.add_argument('--days',      type=int,   default=7,
                        help='분석 기간 (기본: 7일)')
    parser.add_argument('--date',      default=None,
                        help='기준 날짜 (YYYY-MM-DD)')
    parser.add_argument('--horizon',   default=_schema.get('good_horizon', '+EOD'),
                        help='수익률 측정 horizon (기본: label_schema.good_horizon)')
    parser.add_argument('--threshold', type=float,
                        default=_schema.get('good_threshold_pct', 3.0),
                        help='"좋은 기회" 기준 수익률 %% (기본: label_schema.good_threshold_pct)')
    parser.add_argument('--json',      action='store_true',        help='JSON 출력')
    args = parser.parse_args()

    end_date   = date.fromisoformat(args.date) if args.date else date.today()
    start_date = end_date - timedelta(days=args.days - 1)

    conn = _get_conn()
    cur  = conn.cursor()

    reject_stats = _query_reject_stats(cur, start_date, end_date, args.horizon, args.threshold)
    pass_stats   = _query_pass_stats(cur, start_date, end_date, args.horizon, args.threshold)
    coverage     = _query_coverage(cur, start_date, end_date, args.horizon)
    conn.close()

    result = build_analysis(reject_stats, pass_stats, args.threshold)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print_analysis(result, args.threshold, args.horizon, coverage, start_date, end_date)
