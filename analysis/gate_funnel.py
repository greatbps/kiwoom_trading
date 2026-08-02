"""
Gate Funnel Analysis — Decision Ledger 게이트별 탈락률 분석

각 게이트에서 몇 건이 탈락했는지, 어느 게이트가 병목인지 정량적으로 보여준다.

운영 2~4주 후 데이터가 충분히 쌓이면 의미 있는 인사이트를 제공한다.

실행:
    python3 -m analysis.gate_funnel               # 오늘
    python3 -m analysis.gate_funnel --date 2026-07-14
    python3 -m analysis.gate_funnel --days 7      # 최근 7일 합산
    python3 -m analysis.gate_funnel --days 7 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

# ─── Gate 순서 정의 ──────────────────────────────────────────────
# (reason_code, 표시 레이블)
# 순서가 곧 Funnel 순서 — 실제 check_entry_signal() 실행 흐름과 일치
GATE_ORDER: List[Tuple[str, str]] = [
    ('GLOBAL_GATE_BLOCKED',   'GLOBAL_GATE'),
    ('MARKET_SENSOR_BLOCKED', 'MARKET_SENSOR'),
    ('STOCK_GATE_BLOCKED',    'STOCK_GATE'),
    ('DATA_INSUFFICIENT',     'DATA'),
    ('VOLUME_INSUFFICIENT',   'VOLUME'),
    ('CHOCH_MISSING',         'SMC: CHoCH'),
    ('SWEEP_MISSING',         'SMC: Sweep'),
    ('FVG_MISSING',           'SMC: FVG'),
    ('CONFIDENCE_LOW',        'SMC: Confidence'),
    ('RISK_SCORE_HIGH',       'SMC: Risk'),
    ('POLICY_MISMATCH',       'Policy'),
    ('OTHER',                 'Other'),
]
GATE_CODES = {code for code, _ in GATE_ORDER}


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── Query ───────────────────────────────────────────────────────

def _query_funnel(cur, start_date: date, end_date: date) -> Dict:
    sd, ed = str(start_date), str(end_date)

    # 전체 Candidate 수
    cur.execute("""
        SELECT COUNT(*) FROM research.candidates
        WHERE observed_at::date BETWEEN %s AND %s
    """, (sd, ed))
    total_candidates = cur.fetchone()[0]

    # 게이트별 REJECT 건수
    cur.execute("""
        SELECT decision_reason_code, COUNT(*) AS cnt
        FROM research.decision_ledger
        WHERE decided_at::date BETWEEN %s AND %s
          AND decision = 'REJECT'
        GROUP BY decision_reason_code
    """, (sd, ed))
    reject_counts: Dict[str, int] = {r[0]: r[1] for r in cur.fetchall()}

    # PASS 건수
    cur.execute("""
        SELECT COUNT(*) FROM research.decision_ledger
        WHERE decided_at::date BETWEEN %s AND %s
          AND decision = 'PASS'
    """, (sd, ed))
    pass_count = cur.fetchone()[0]

    return {
        'total_candidates': total_candidates,
        'reject_counts': reject_counts,
        'pass_count': pass_count,
        'start_date': sd,
        'end_date': ed,
    }


# ─── Funnel Builder ──────────────────────────────────────────────

def build_funnel(data: Dict) -> List[Dict]:
    """
    게이트 순서대로 통과/탈락 계산.

    entering = 이 게이트에 도달한 후보 수
             = total_candidates - sum(rejections at earlier gates)
    """
    total = data['total_candidates']
    reject_counts = data['reject_counts']
    pass_count = data['pass_count']

    rows = []
    cumulative_rejected = 0

    for code, label in GATE_ORDER:
        rejected = reject_counts.get(code, 0)
        entering = total - cumulative_rejected
        passing  = entering - rejected
        drop_pct = (rejected / entering * 100) if entering > 0 else 0.0

        rows.append({
            'code':     code,
            'label':    label,
            'entering': entering,
            'rejected': rejected,
            'passing':  passing,
            'drop_pct': round(drop_pct, 1),
        })
        cumulative_rejected += rejected

    # 기타 reason_code (GATE_ORDER에 없는 것)
    unknown_rejected = sum(
        v for k, v in reject_counts.items() if k not in GATE_CODES
    )
    if unknown_rejected > 0:
        entering = total - cumulative_rejected
        rows.append({
            'code':     'UNKNOWN',
            'label':    'Unknown',
            'entering': entering,
            'rejected': unknown_rejected,
            'passing':  entering - unknown_rejected,
            'drop_pct': round(unknown_rejected / entering * 100, 1) if entering > 0 else 0.0,
        })
        cumulative_rejected += unknown_rejected

    # PASS row (최종 통과)
    final_entering = total - cumulative_rejected
    rows.append({
        'code':     'PASS',
        'label':    'PASS',
        'entering': final_entering,
        'rejected': 0,
        'passing':  pass_count,
        'drop_pct': 0.0,
    })

    return rows


# ─── Printer ─────────────────────────────────────────────────────

def print_funnel(data: Dict, rows: List[Dict]) -> None:
    sd, ed = data['start_date'], data['end_date']
    period = sd if sd == ed else f"{sd} ~ {ed}"
    total = data['total_candidates']

    print(f"\n{'='*72}")
    print(f"  Gate Funnel Analysis — {period}")
    print(f"  Total Candidates: {total:,}")
    print(f"{'='*72}")

    if total == 0:
        print("  [!] 데이터 없음 — 운영 2~4주 후 분석 가능")
        print(f"{'='*72}\n")
        return

    print(f"\n  {'Gate':<22} {'Enter':>7}  {'Reject':>7}  {'Pass':>7}  {'Drop%':>7}  Bar")
    print(f"  {'-'*22} {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*20}")

    max_rejected = max((r['rejected'] for r in rows), default=1)
    for r in rows:
        bar_len = int(r['rejected'] / max(max_rejected, 1) * 20) if r['rejected'] > 0 else 0
        bar     = '█' * bar_len
        label   = r['label']

        if r['code'] == 'PASS':
            print(f"\n  {'PASS':<22} {'':>7}  {'':>7}  {r['passing']:>7}  {'':>7}")
        else:
            drop_flag = ' ⚠️' if r['drop_pct'] >= 50 else ''
            print(f"  {label:<22} {r['entering']:>7,}  {r['rejected']:>7,}  {r['passing']:>7,}  {r['drop_pct']:>6.1f}%{drop_flag}  {bar}")

    # 병목 게이트 강조
    bottleneck = max(
        (r for r in rows if r['code'] not in ('PASS', 'UNKNOWN') and r['rejected'] > 0),
        key=lambda x: x['drop_pct'],
        default=None,
    )
    if bottleneck and bottleneck['drop_pct'] >= 30:
        print(f"\n  병목: {bottleneck['label']}  탈락률={bottleneck['drop_pct']}%  ({bottleneck['rejected']:,}건)")

    print(f"\n  PASS율: {pass_count / total * 100:.2f}% ({data['pass_count']:,}/{total:,})")
    print(f"{'='*72}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Gate Funnel Analysis — 게이트별 탈락률',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python3 -m analysis.gate_funnel                  # 오늘
  python3 -m analysis.gate_funnel --date 2026-07-14
  python3 -m analysis.gate_funnel --days 7         # 최근 7일 합산
  python3 -m analysis.gate_funnel --days 30 --json  # JSON 출력
"""
    )
    parser.add_argument('--date',  default=None, help='분석 날짜 (YYYY-MM-DD)')
    parser.add_argument('--days',  type=int, default=1, help='최근 N일 합산 (기본: 1)')
    parser.add_argument('--json',  action='store_true', help='JSON 출력')
    args = parser.parse_args()

    end_date   = date.fromisoformat(args.date) if args.date else date.today()
    start_date = end_date - timedelta(days=args.days - 1)

    conn = _get_conn()
    cur  = conn.cursor()

    data  = _query_funnel(cur, start_date, end_date)
    rows  = build_funnel(data)
    pass_count = data['pass_count']

    conn.close()

    if args.json:
        print(json.dumps({'funnel': rows, 'meta': data}, indent=2, default=str))
    else:
        print_funnel(data, rows)
