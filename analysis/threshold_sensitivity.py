"""
Threshold Sensitivity Analysis

"RVOL 기준을 2.0 → 1.7로 바꾸면 Recall은 +15%, Precision은 -2%"처럼
구체적 수치로 임계값 변경의 효과를 시뮬레이션한다.

원리:
  REJECT 결정의 feature_snapshot에서 feature 값을 추출하고,
  서로 다른 threshold를 적용했을 때 "통과됐을 결정"의 미래수익률 분포를 계산한다.

실행:
  python3 -m analysis.threshold_sensitivity --gate VOLUME_INSUFFICIENT --feature rvol
  python3 -m analysis.threshold_sensitivity --gate CHOCH_MISSING --feature confidence \\
      --range 0.3,0.7,0.05 --current 0.4
  python3 -m analysis.threshold_sensitivity --gate RISK_SCORE_HIGH --feature risk_score \\
      --direction lower   # 값이 낮을수록 통과 (risk_score < threshold = PASS)
  python3 -m analysis.threshold_sensitivity --gate VOLUME_INSUFFICIENT --feature rvol --days 14
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import yaml

_CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'research_config.yaml'


def _load_label_schema() -> dict:
    try:
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8'))
        return cfg.get('label_schema', {})
    except Exception:
        return {}


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── Data Query ──────────────────────────────────────────────────

def _query_reject_features(
    cur,
    gate: str,
    feature: str,
    horizon: str,
    start_date: date,
    end_date: date,
) -> List[Dict]:
    """
    대상 게이트의 REJECT 결정 + feature 값 + 미래수익률 조회.
    feature_snapshot JSONB에서 추출.
    """
    sd, ed = str(start_date), str(end_date)
    cur.execute("""
        SELECT
            d.decision_id,
            d.stock_code,
            d.trace_id,
            (d.feature_snapshot->>%(feature)s)::float  AS feature_val,
            fr.return_pct
        FROM research.decision_ledger d
        JOIN research.future_return_events fr
            ON  fr.decision_id   = d.decision_id
            AND fr.horizon_label = %(horizon)s
        WHERE d.decision            = 'REJECT'
          AND d.decision_reason_code = %(gate)s
          AND d.decided_at::date BETWEEN %(sd)s AND %(ed)s
          AND (d.feature_snapshot->>%(feature)s) IS NOT NULL
          AND (d.feature_snapshot->>%(feature)s) ~ '^-?[0-9]+(\\.[0-9]+)?$'
        ORDER BY (d.feature_snapshot->>%(feature)s)::float
    """, {'gate': gate, 'feature': feature, 'horizon': horizon, 'sd': sd, 'ed': ed})
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _query_pass_baseline(cur, horizon: str, good_thr: float,
                         start_date: date, end_date: date) -> Dict:
    """현재 전략의 PASS 결정 baseline (Precision 기준점)."""
    sd, ed = str(start_date), str(end_date)
    cur.execute("""
        SELECT
            COUNT(*)                                              AS total,
            COUNT(CASE WHEN fr.return_pct > %(thr)s THEN 1 END) AS good_count,
            AVG(fr.return_pct)                                   AS avg_return
        FROM research.decision_ledger d
        JOIN research.future_return_events fr
            ON  fr.decision_id   = d.decision_id
            AND fr.horizon_label = %(horizon)s
        WHERE d.decision = 'PASS'
          AND d.decided_at::date BETWEEN %(sd)s AND %(ed)s
    """, {'horizon': horizon, 'thr': good_thr, 'sd': sd, 'ed': ed})
    row = cur.fetchone()
    if not row:
        return {'total': 0, 'good': 0, 'avg': None}
    return {'total': int(row[0]), 'good': int(row[1]), 'avg': row[2]}


# ─── Simulation ──────────────────────────────────────────────────

def simulate_thresholds(
    records: List[Dict],
    thresholds: List[float],
    current_threshold: float,
    direction: str,       # 'higher' = feature >= thr → PASS, 'lower' = feature < thr → PASS
    good_thr: float,
    bad_thr: float,
    pass_baseline: Dict,
) -> List[Dict]:
    """
    각 threshold 값에서 "통과됐을 결정"을 시뮬레이션.

    direction='higher': feature_val >= threshold → 통과 (RVOL, confidence 등)
    direction='lower':  feature_val <  threshold → 통과 (risk_score 등)
    """
    total_reject = len(records)
    base_pass_good = pass_baseline.get('good', 0)
    base_pass_total = pass_baseline.get('total', 0)

    def would_pass(val: float, thr: float) -> bool:
        if direction == 'higher':
            return val >= thr
        else:
            return val < thr

    results = []
    for thr in thresholds:
        newly_passing = [r for r in records
                         if r['feature_val'] is not None
                         and would_pass(float(r['feature_val']), thr)]
        stays_rejected = [r for r in records
                          if r not in newly_passing]

        n_new     = len(newly_passing)
        n_good    = sum(1 for r in newly_passing if r['return_pct'] is not None and r['return_pct'] > good_thr)
        n_bad     = sum(1 for r in newly_passing if r['return_pct'] is not None and r['return_pct'] <= bad_thr)
        avg_ret   = (
            sum(r['return_pct'] for r in newly_passing if r['return_pct'] is not None) / n_new
            if n_new > 0 else None
        )

        # 새 Precision = (기존 PASS good + newly PASS good) / (기존 PASS + newly PASS)
        new_total_pass = base_pass_total + n_new
        new_good_pass  = base_pass_good  + n_good
        precision = round(new_good_pass / new_total_pass * 100, 1) if new_total_pass > 0 else None

        # 새 Recall = 전체 good 중 통과 비율 (FN 합계 변화)
        total_good = base_pass_good + total_reject  # 근사: 모든 reject가 good일 수 있는 최대
        old_recall = round(base_pass_good / (base_pass_good + total_reject) * 100, 1) if (base_pass_good + total_reject) > 0 else None
        new_recall = round((base_pass_good + n_good) / (base_pass_good + total_reject) * 100, 1) if (base_pass_good + total_reject) > 0 else None

        is_current = abs(thr - current_threshold) < 1e-9

        results.append({
            'threshold':        thr,
            'is_current':       is_current,
            'newly_passing':    n_new,
            'newly_good':       n_good,
            'newly_bad':        n_bad,
            'avg_return':       round(avg_ret, 2) if avg_ret is not None else None,
            'new_precision':    precision,
            'old_recall':       old_recall,
            'new_recall':       new_recall,
            'd_recall':         round(new_recall - old_recall, 1) if (new_recall is not None and old_recall is not None) else None,
            'd_precision':      None,  # 계산됨 아래에서
        })

    # Δ Precision (current 기준)
    current_row = next((r for r in results if r['is_current']), None)
    current_prec = current_row['new_precision'] if current_row else None
    for r in results:
        if current_prec is not None and r['new_precision'] is not None:
            r['d_precision'] = round(r['new_precision'] - current_prec, 1)

    return results


# ─── Printer ─────────────────────────────────────────────────────

def print_sensitivity(
    rows: List[Dict],
    gate: str,
    feature: str,
    current_threshold: float,
    good_thr: float,
    horizon: str,
    schema_version: str,
    data_count: int,
    start_date: date,
    end_date: date,
) -> None:
    sd, ed = str(start_date), str(end_date)
    period = sd if sd == ed else f"{sd} ~ {ed}"

    print(f"\n{'='*80}")
    print(f"  Threshold Sensitivity — {gate} / {feature}")
    print(f"  기간: {period}  |  N={data_count}건  |  Label Schema {schema_version}  |  GOOD>{good_thr}%  Horizon={horizon}")
    print(f"  현재 임계값: {current_threshold}")
    print(f"{'='*80}")

    if data_count == 0:
        print("  [!] 데이터 없음 — returns_collector 실행 후 재시도")
        print(f"{'='*80}\n")
        return

    print(f"\n  {'Threshold':>10}  {'NewPass':>8}  {'NewGood':>8}  {'AvgRet':>8}  "
          f"{'Precision':>10}  {'ΔPrec':>7}  {'Recall':>8}  {'ΔRecall':>8}  {'추천'}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*8}  "
          f"{'-'*10}  {'-'*7}  {'-'*8}  {'-'*8}  {'-'*10}")

    best_row = None
    best_score = -999

    for r in rows:
        mark    = '◀ 현재' if r['is_current'] else ''
        avg_s   = f"{r['avg_return']:+.1f}%" if r['avg_return'] is not None else '     -'
        prec_s  = f"{r['new_precision']:.1f}%" if r['new_precision'] is not None else '     -'
        dprec_s = f"{r['d_precision']:+.1f}%" if r['d_precision'] is not None else '     -'
        rec_s   = f"{r['new_recall']:.1f}%"   if r['new_recall']   is not None else '     -'
        drec_s  = f"{r['d_recall']:+.1f}%"    if r['d_recall']     is not None else '     -'

        # 추천 점수: Recall 증가 - |Precision 감소| * 0.5 (Recall을 더 중시)
        if not r['is_current'] and r['d_recall'] is not None and r['d_precision'] is not None:
            score = r['d_recall'] + r['d_precision'] * 0.5
            if score > best_score and r['d_recall'] > 0:
                best_score = score
                best_row   = r

        recommend = '★ 최적' if (best_row and r is best_row) else mark
        print(f"  {r['threshold']:>10.2f}  {r['newly_passing']:>8}  {r['newly_good']:>8}  "
              f"{avg_s:>8}  {prec_s:>10}  {dprec_s:>7}  {rec_s:>8}  {drec_s:>8}  {recommend}")

    # 추천 요약
    if best_row:
        print(f"\n  ★ 추천 임계값: {best_row['threshold']:.2f}")
        print(
            f"    현재({current_threshold}) → {best_row['threshold']:.2f}로 변경 시:\n"
            f"    Recall {best_row['d_recall']:+.1f}%  |  Precision {best_row['d_precision']:+.1f}%\n"
            f"    추가로 통과되는 종목: {best_row['newly_passing']}건  "
            f"(이 중 GOOD: {best_row['newly_good']}건)"
        )
    else:
        print("\n  현재 임계값이 최적이거나 데이터 부족.")

    print(f"\n{'='*80}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    schema = _load_label_schema()

    parser = argparse.ArgumentParser(
        description='Threshold Sensitivity Analysis — 임계값 최적화',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # RVOL 2.0 기준의 VOLUME 필터 민감도 분석
  python3 -m analysis.threshold_sensitivity \\
      --gate VOLUME_INSUFFICIENT --feature rvol --current 2.0 --range 1.2,3.0,0.1

  # Confidence 0.4 기준 분석
  python3 -m analysis.threshold_sensitivity \\
      --gate CONFIDENCE_LOW --feature confidence --current 0.4 --range 0.2,0.7,0.05

  # Risk Score (낮을수록 좋음 → direction=lower)
  python3 -m analysis.threshold_sensitivity \\
      --gate RISK_SCORE_HIGH --feature risk_score --current 0.6 \\
      --direction lower --range 0.3,0.8,0.05
"""
    )
    parser.add_argument('--gate',      required=True,
                        help='분석할 게이트 reason_code (예: VOLUME_INSUFFICIENT)')
    parser.add_argument('--feature',   required=True,
                        help='feature_snapshot의 JSONB 키 (예: rvol, confidence)')
    parser.add_argument('--current',   type=float, required=True,
                        help='현재 임계값')
    parser.add_argument('--range',     default=None,
                        help='시뮬레이션 범위: min,max,step (예: 1.0,3.0,0.2)')
    parser.add_argument('--direction', choices=['higher', 'lower'], default='higher',
                        help='higher=값≥threshold 통과 / lower=값<threshold 통과 (기본: higher)')
    parser.add_argument('--days',      type=int, default=7,
                        help='분석 기간 (기본: 7일)')
    parser.add_argument('--date',      default=None,
                        help='기준 날짜 (YYYY-MM-DD)')
    parser.add_argument('--horizon',   default=schema.get('good_horizon', '+EOD'),
                        help='수익률 horizon')
    parser.add_argument('--threshold', type=float,
                        default=schema.get('good_threshold_pct', 3.0),
                        help='"좋은 기회" 수익률 기준 (%)')
    parser.add_argument('--json',      action='store_true', help='JSON 출력')
    args = parser.parse_args()

    end_date   = date.fromisoformat(args.date) if args.date else date.today()
    start_date = end_date - timedelta(days=args.days - 1)

    # threshold 범위 생성
    if args.range:
        mn, mx, st = map(float, args.range.split(','))
        thresholds = []
        v = mn
        while v <= mx + 1e-9:
            thresholds.append(round(v, 4))
            v += st
    else:
        # current 기준으로 ±30% 범위 자동 생성
        step = args.current * 0.1
        mn = round(args.current * 0.6, 4)
        mx = round(args.current * 1.4, 4)
        thresholds = []
        v = mn
        while v <= mx + 1e-9:
            thresholds.append(round(v, 4))
            v += step

    conn = _get_conn()
    cur  = conn.cursor()

    records  = _query_reject_features(
        cur, args.gate, args.feature, args.horizon, start_date, end_date
    )
    baseline = _query_pass_baseline(cur, args.horizon, args.threshold, start_date, end_date)
    conn.close()

    rows = simulate_thresholds(
        records, thresholds, args.current,
        args.direction, args.threshold,
        schema.get('bad_threshold_pct', 0.0),
        baseline,
    )

    if args.json:
        print(json.dumps({'rows': rows, 'meta': {
            'gate': args.gate, 'feature': args.feature,
            'current': args.current, 'direction': args.direction,
        }}, indent=2, default=str))
    else:
        print_sensitivity(
            rows, args.gate, args.feature,
            args.current, args.threshold, args.horizon,
            schema.get('version', '?'),
            len(records), start_date, end_date,
        )
