"""
Trading Health Audit — 무거래 원인 자동 진단 (읽기 전용)

최근 N거래일 동안 실거래가 없었던(또는 적었던) 원인이 시장/전략/시스템/데이터
중 어디에 있는지 정량 지표로 자동 분류한다.

전략/Entry/Exit/Score/Regime/Position Size/YAML/DB 어느 것도 수정하지 않는다.
읽기 전용 분석 및 리포트 생성만 수행한다.

기존 analysis/gate_health_check.py (후보수/게이트분해/로그태그/거래일 계산)와
analysis/gate_funnel.py (게이트 순서/펀넬)를 재사용하며, 이 스크립트는 그 위에
Score 분포·Regime 분포·건강도 점수·Root Cause 판정만 추가한다.

실행:
    python3 -m analysis.trading_health_audit            # 최근 7거래일
    python3 -m analysis.trading_health_audit --days 14
    python3 -m analysis.trading_health_audit --days 7 --json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from datetime import date
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from analysis.gate_health_check import (
    _get_conn,
    _candidate_count,
    _gate_breakdown,
    _log_tag_counts,
    _recent_trading_days,
)
from analysis.gate_funnel import GATE_ORDER, _query_funnel, build_funnel

logger = logging.getLogger(__name__)

_RECOMMENDATIONS = {
    'PIPELINE_FAILURE':  '파이프라인이 후보를 평가 단계까지 진행시키지 못하고 있음 — 코드/로그 점검 필요',
    'CHECK_EC_HALT':     'EC_HALT가 유의미한 비율로 후보를 차단 중 — equity_controller 상태 점검 필요',
    'ENTRY_TOO_STRICT':  '후보는 충분하나 PASS가 전무 — Entry 게이트 조건이 과도하게 엄격한지 검토 필요',
    'REGIME_DOMINATED':  'REGIME_BLOCK이 탈락 사유 대부분을 차지 — 레짐 필터 임계값 재검토 필요',
    'SCAN_TOO_STRICT':   '평균 후보 수 자체가 적음 — 스캔/유니버스 조건이 과도하게 엄격한지 검토 필요',
    'LOW_SCORE_MARKET':  '통과 결정의 평균 신뢰도가 낮음 — 시장 신호 품질이 낮은 구간일 가능성',
    'NORMAL':            '특이 이상 없음 — 시장 상황에 따른 정상적인 결과로 판단됨',
}


# ─── DB Collectors (I/O — 테스트 대상 아님, run_health_audit()에서만 사용) ──

def _confidence_stats(cur, start: date, end: date) -> Dict[str, Any]:
    cur.execute("""
        SELECT confidence FROM research.decision_ledger
        WHERE decided_at::date BETWEEN %s AND %s
          AND decision = 'PASS' AND confidence IS NOT NULL AND confidence > 0
    """, (str(start), str(end)))
    values = sorted(float(r[0]) for r in cur.fetchall())
    if not values:
        return {'n': 0}
    buckets = [0, 0, 0, 0, 0]  # [0-0.2) [0.2-0.4) [0.4-0.6) [0.6-0.8) [0.8-1.0]
    for v in values:
        idx = min(int(v / 0.2), 4)
        buckets[idx] += 1
    return {
        'n':        len(values),
        'avg':      round(statistics.mean(values), 4),
        'median':   round(statistics.median(values), 4),
        'p95':      round(values[min(int(len(values) * 0.95), len(values) - 1)], 4),
        'max':      round(max(values), 4),
        'min':      round(min(values), 4),
        'histogram': buckets,
    }


def _regime_by_day(cur, start: date, end: date) -> Dict[str, str]:
    cur.execute("""
        SELECT session_date, regime FROM trading_sessions
        WHERE session_date BETWEEN %s AND %s
        ORDER BY session_date
    """, (str(start), str(end)))
    return {str(r[0]): r[1] for r in cur.fetchall()}


# ─── Pure Logic (DB/파일 I/O 없음 — 테스트가 직접 호출) ────────────────────

def classify_root_cause(
    avg_candidates: float,
    pass_count: int,
    total_candidates: int,
    regime_block_rate: float,
    ec_halt_rate: float,
    avg_confidence: Optional[float],
    total_evaluations: int,
) -> str:
    """
    Root Cause 우선순위: F > D > B > C > A > E ("가장 치명적인 실패가 우선").
    총체적 파이프라인 마비 > 안전정지 발동 > 게이트 전량차단 > 특정게이트 독점 >
    후보 자체 부족 > (데이터 신뢰 가능할 때만) 낮은 신호 품질.
    """
    if total_evaluations == 0:
        return 'PIPELINE_FAILURE'
    if ec_halt_rate >= 0.01:
        return 'CHECK_EC_HALT'
    if total_candidates > 0 and pass_count == 0 and avg_candidates >= 5:
        return 'ENTRY_TOO_STRICT'
    if regime_block_rate >= 0.70:
        return 'REGIME_DOMINATED'
    if avg_candidates < 5:
        return 'SCAN_TOO_STRICT'
    if avg_confidence is not None and avg_confidence < 0.20:
        return 'LOW_SCORE_MARKET'
    return 'NORMAL'


def compute_health_score(
    avg_candidates: float,
    total_evaluations: int,
    pass_count: int,
    regime_block_rate: float,
    confidence_stats: Dict[str, Any],
) -> Dict[str, int]:
    """5개 차원 20점씩, 총 100점. Root Cause 판정과 동일 임계값을 앵커로 사용."""
    candidate_score = 20 if avg_candidates >= 5 else round(20 * avg_candidates / 5)
    pipeline_score = 20 if total_evaluations > 0 else 0

    if pass_count > 0:
        gate_score = 20
    else:
        gate_score = max(0, min(20, round(20 * (1 - regime_block_rate))))

    avg_confidence = confidence_stats.get('avg') if confidence_stats.get('n', 0) > 0 else None
    if avg_confidence is None:
        score_score = 20  # 데이터 부재를 페널티로 취급하지 않음
    else:
        score_score = round(20 * min(avg_confidence / 0.20, 1.0))

    trade_score = 20 if pass_count >= 1 else 0

    total = candidate_score + pipeline_score + gate_score + score_score + trade_score
    return {
        'candidate': candidate_score, 'pipeline': pipeline_score, 'gate': gate_score,
        'score': score_score, 'trade': trade_score, 'total': total,
    }


def build_health_report(
    period_start: date,
    period_end: date,
    trading_days: List[date],
    candidates_by_day: Dict[date, int],
    pass_count: int,
    total_evaluations: int,
    gate_funnel_rows: List[Dict],
    reject_counts: Dict[str, int],
    ec_halt_total: int,
    total_candidates: int,
    confidence_stats: Dict[str, Any],
    regime_by_day: Dict[str, str],
) -> Dict[str, Any]:
    """순수 함수 — DB/파일 I/O 없음. 테스트는 이 함수를 직접 호출한다."""
    n_days = len(trading_days) or 1
    avg_candidates = total_candidates / n_days
    max_candidates = max(candidates_by_day.values()) if candidates_by_day else 0
    min_candidates = min(candidates_by_day.values()) if candidates_by_day else 0

    total_rejections = sum(reject_counts.values())
    regime_block_rate = (reject_counts.get('REGIME_BLOCKED', 0) / total_rejections) if total_rejections > 0 else 0.0
    ec_halt_rate = (ec_halt_total / total_candidates) if total_candidates > 0 else 0.0
    avg_confidence = confidence_stats.get('avg') if confidence_stats.get('n', 0) > 0 else None

    root_cause = classify_root_cause(
        avg_candidates=avg_candidates, pass_count=pass_count, total_candidates=total_candidates,
        regime_block_rate=regime_block_rate, ec_halt_rate=ec_halt_rate,
        avg_confidence=avg_confidence, total_evaluations=total_evaluations,
    )
    health_score = compute_health_score(
        avg_candidates=avg_candidates, total_evaluations=total_evaluations, pass_count=pass_count,
        regime_block_rate=regime_block_rate, confidence_stats=confidence_stats,
    )

    top_block_reasons = sorted(reject_counts.items(), key=lambda kv: kv[1], reverse=True)

    return {
        'period_start': str(period_start),
        'period_end':   str(period_end),
        'trading_days': [str(d) for d in trading_days],
        'candidate': {
            'total': total_candidates, 'avg': round(avg_candidates, 2),
            'max': max_candidates, 'min': min_candidates,
            'by_day': {str(d): c for d, c in candidates_by_day.items()},
        },
        'evaluation': {'total': total_evaluations,
                       'rate_vs_candidate': round(total_evaluations / total_candidates, 4) if total_candidates > 0 else 0.0},
        'pass_count': pass_count,
        'trade_count': pass_count,  # PASS = 주문 시도 단계 진입 (실체결 여부는 별도 집계 대상 아님)
        'gate_funnel': gate_funnel_rows,
        'top_block_reasons': top_block_reasons,
        'regime_block_rate': round(regime_block_rate, 4),
        'ec_halt_total': ec_halt_total,
        'ec_halt_rate': round(ec_halt_rate, 4),
        'confidence_stats': confidence_stats,
        'regime_by_day': regime_by_day,
        'health_score': health_score,
        'root_cause': root_cause,
        'recommendation': _RECOMMENDATIONS.get(root_cause, ''),
    }


# ─── I/O Orchestration ───────────────────────────────────────────

def run_health_audit(days: int = 7) -> Dict[str, Any]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        today = date.today()
        trading_days = _recent_trading_days(cur, today, days)
        if not trading_days:
            trading_days = [today]
        start_date, end_date = trading_days[0], trading_days[-1]

        funnel_data = _query_funnel(cur, start_date, end_date)
        funnel_rows = build_funnel(funnel_data)

        candidates_by_day: Dict[date, int] = {}
        ec_halt_total = 0
        total_evaluations = 0
        for d in trading_days:
            candidates_by_day[d] = _candidate_count(cur, d)
            gate_bd = _gate_breakdown(cur, d)
            ec_halt_total += gate_bd.get('EC_HALT', 0)
            logs = _log_tag_counts(d)
            entry_checked = max(0, logs['regime_evaluated'] - logs['regime_block']
                                 - logs['afternoon_cutoff_block'] - logs['early_window_block'])
            total_evaluations += entry_checked

        confidence_stats = _confidence_stats(cur, start_date, end_date)
        regime_by_day = _regime_by_day(cur, start_date, end_date)

        logger.info(f"[TRADING_HEALTH] {len(trading_days)}거래일 조회 완료: "
                    f"후보={funnel_data['total_candidates']} PASS={funnel_data['pass_count']}")

        return build_health_report(
            period_start=start_date, period_end=end_date, trading_days=trading_days,
            candidates_by_day=candidates_by_day, pass_count=funnel_data['pass_count'],
            total_evaluations=total_evaluations, gate_funnel_rows=funnel_rows,
            reject_counts=funnel_data['reject_counts'], ec_halt_total=ec_halt_total,
            total_candidates=funnel_data['total_candidates'], confidence_stats=confidence_stats,
            regime_by_day=regime_by_day,
        )
    finally:
        conn.close()


# ─── Printer ─────────────────────────────────────────────────────

def print_health_report(report: Dict[str, Any]) -> None:
    p = report['period_start'] if report['period_start'] == report['period_end'] \
        else f"{report['period_start']} ~ {report['period_end']}"

    print(f"\n{'='*72}")
    print("  Trading Health Audit")
    print(f"  Period: {p}  ({len(report['trading_days'])}거래일)")
    print(f"{'='*72}")

    c = report['candidate']
    print(f"\n  Candidate   : total={c['total']:,}  avg={c['avg']}  max={c['max']}  min={c['min']}")
    print(f"  Evaluation  : {report['evaluation']['total']:,}  "
          f"(Candidate 대비 {report['evaluation']['rate_vs_candidate']*100:.1f}%)")
    print(f"  PASS        : {report['pass_count']:,}")
    print(f"  Trade       : {report['trade_count']:,}")

    print(f"\n  {'--- Gate Statistics ---':<40}")
    for row in report['gate_funnel']:
        if row['code'] == 'PASS':
            continue
        print(f"    {row['label']:<20} Enter={row['entering']:>6,}  Reject={row['rejected']:>6,}  Drop%={row['drop_pct']:>5.1f}")

    print(f"\n  {'--- Top Block Reasons ---':<40}")
    for code, cnt in report['top_block_reasons'][:5]:
        print(f"    {code:<25} {cnt:,}")
    print(f"    EC_HALT (하위 태그, GLOBAL_GATE_BLOCKED 내)  {report['ec_halt_total']:,}  "
          f"({report['ec_halt_rate']*100:.2f}%)")

    cs = report['confidence_stats']
    print(f"\n  {'--- Score Distribution ---':<40}")
    if cs.get('n', 0) == 0:
        print("    N/A (0 PASS 결정 — confidence는 진입 승인 건에만 기록됨)")
    else:
        print(f"    avg={cs['avg']}  median={cs['median']}  p95={cs['p95']}  max={cs['max']}  min={cs['min']}  (n={cs['n']})")

    print(f"\n  {'--- Regime Distribution ---':<40}")
    if report['regime_by_day']:
        for d, r in sorted(report['regime_by_day'].items()):
            print(f"    {d}: {r}")
    else:
        print("    N/A (trading_sessions 기록 없음)")

    hs = report['health_score']
    print(f"\n  {'--- Health Score ---':<40}")
    print(f"    Candidate={hs['candidate']}  Pipeline={hs['pipeline']}  Gate={hs['gate']}  "
          f"Score={hs['score']}  Trade={hs['trade']}   => {hs['total']}/100")

    print(f"\n  Root Cause     : {report['root_cause']}")
    print(f"  Recommendation : {report['recommendation']}")
    print(f"{'='*72}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Trading Health Audit — 무거래 원인 자동 진단 (읽기 전용)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python3 -m analysis.trading_health_audit             # 최근 7거래일
  python3 -m analysis.trading_health_audit --days 14
  python3 -m analysis.trading_health_audit --days 7 --json
"""
    )
    parser.add_argument('--days', type=int, default=7, help='최근 N거래일 (기본: 7)')
    parser.add_argument('--json', action='store_true', help='JSON 출력')
    args = parser.parse_args()

    report = run_health_audit(days=args.days)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_health_report(report)
