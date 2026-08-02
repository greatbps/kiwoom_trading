"""
W Pattern Filter Evaluation — 백테스트/운영 성과 분석

W Pattern Filter(strategy/w_pattern_filter.py)가 판정한 decision_ledger 건들을
research.decision_outcomes 뷰(future_return_events 조인 완료)와 함께 조회해
PASS/FAIL 성과를 비교한다.

읽기 전용 분석만 수행한다. 전략/게이트 로직에는 관여하지 않는다.

실행:
    python3 -m analysis.evaluate_w_pattern               # 오늘
    python3 -m analysis.evaluate_w_pattern --date 2026-07-20
    python3 -m analysis.evaluate_w_pattern --days 30
    python3 -m analysis.evaluate_w_pattern --days 30 --json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from analysis.performance_metrics import compute_metrics


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _sharpe(returns: List[float]) -> float:
    """단순 Sharpe (무위험수익률=0). 표본 2개 미만이면 0."""
    if len(returns) < 2:
        return 0.0
    mean = statistics.mean(returns)
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return 0.0
    return round(mean / stdev * (len(returns) ** 0.5), 4)


# ─── Query ───────────────────────────────────────────────────────

def _query_w_pattern_rows(cur, start_date: date, end_date: date) -> List[Dict]:
    sd, ed = str(start_date), str(end_date)
    cur.execute("""
        SELECT
            o.decision_id,
            o.decision,
            o.decision_reason_code,
            d.feature_snapshot ->> 'w_pattern_reason'      AS w_reason,
            (d.feature_snapshot ->> 'w_pattern_confidence')::float AS w_confidence,
            o.return_5d
        FROM research.decision_outcomes o
        JOIN research.decision_ledger d ON d.decision_id = o.decision_id
        WHERE o.decided_at::date BETWEEN %s AND %s
          AND d.feature_snapshot ? 'w_pattern_reason'
    """, (sd, ed))
    cols = ['decision_id', 'decision', 'decision_reason_code', 'w_reason', 'w_confidence', 'return_5d']
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─── Report Builder ──────────────────────────────────────────────

def build_w_pattern_report(rows: List[Dict]) -> Dict:
    total = len(rows)
    passed = [r for r in rows if r['w_reason'] == 'PASS']
    failed = [r for r in rows if r['w_reason'] != 'PASS']

    pass_returns = [r['return_5d'] / 100 for r in passed if r['return_5d'] is not None]
    metrics = compute_metrics(pass_returns)

    return {
        'total_signal':  total,
        'pass_count':    len(passed),
        'fail_count':    len(failed),
        'win_rate':      metrics['win_rate'],
        'avg_return':    round(sum(pass_returns) / len(pass_returns), 6) if pass_returns else 0.0,
        'profit_factor': metrics['profit_factor'],
        'max_drawdown':  metrics['max_drawdown'],
        'expectancy':    metrics['expectancy'],
        'sharpe':        _sharpe(pass_returns),
        'sample_with_return': len(pass_returns),
    }


def print_w_pattern_report(data: Dict, report: Dict) -> None:
    sd, ed = data['start_date'], data['end_date']
    period = sd if sd == ed else f"{sd} ~ {ed}"

    print(f"\n{'='*60}")
    print(f"  W Pattern Filter Evaluation — {period}")
    print(f"{'='*60}")
    print(f"  Total Signal   : {report['total_signal']:,}")
    print(f"  PASS           : {report['pass_count']:,}")
    print(f"  FAIL           : {report['fail_count']:,}")

    if report['sample_with_return'] == 0:
        print("\n  [!] PASS 건 중 return_5d 산출 가능한 표본 없음 — 성과 지표 N/A")
        print(f"{'='*60}\n")
        return

    print(f"\n  Win Rate       : {report['win_rate']*100:.2f}%")
    print(f"  Average Return : {report['avg_return']*100:.3f}%")
    print(f"  Profit Factor  : {report['profit_factor']:.3f}")
    print(f"  MDD            : {report['max_drawdown']*100:.3f}%")
    print(f"  Expectancy     : {report['expectancy']*100:.4f}%")
    print(f"  Sharpe         : {report['sharpe']:.3f}")
    print(f"  (n={report['sample_with_return']})")
    print(f"{'='*60}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='W Pattern Filter Evaluation — PASS/FAIL 성과 비교',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python3 -m analysis.evaluate_w_pattern                  # 오늘
  python3 -m analysis.evaluate_w_pattern --date 2026-07-20
  python3 -m analysis.evaluate_w_pattern --days 30
  python3 -m analysis.evaluate_w_pattern --days 30 --json
"""
    )
    parser.add_argument('--date', default=None, help='분석 날짜 (YYYY-MM-DD)')
    parser.add_argument('--days', type=int, default=1, help='최근 N일 합산 (기본: 1)')
    parser.add_argument('--json', action='store_true', help='JSON 출력')
    args = parser.parse_args()

    end_date   = date.fromisoformat(args.date) if args.date else date.today()
    start_date = end_date - timedelta(days=args.days - 1)

    conn = _get_conn()
    cur = conn.cursor()

    rows = _query_w_pattern_rows(cur, start_date, end_date)
    report = build_w_pattern_report(rows)
    data = {'start_date': str(start_date), 'end_date': str(end_date)}

    conn.close()

    if args.json:
        print(json.dumps({'report': report, 'meta': data}, indent=2, default=str))
    else:
        print_w_pattern_report(data, report)
