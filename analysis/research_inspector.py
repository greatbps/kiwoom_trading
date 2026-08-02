"""
Research Inspector — Decision Ledger 운영 조회 도구

조회 축:
    --trace TR-20260701-000183       단일 Trace 전체 흐름
    --date  2026-07-01               해당일 모든 Decision 요약
    --ticker 005930                  종목별 Decision 이력
    --reason CHOCH_MISSING           거절 사유별 패턴

실행:
    python3 -m analysis.research_inspector --trace TR-20260701-000183
    python3 -m analysis.research_inspector --date 2026-07-01
    python3 -m analysis.research_inspector --ticker 005930
    python3 -m analysis.research_inspector --reason CHOCH_MISSING
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── View: Single Trace ──────────────────────────────────────────

def inspect_trace(trace_id: str, conn=None) -> None:
    """trace_id 하나로 Decision 전체 흐름을 한 화면에 출력."""
    close = False
    if conn is None:
        conn = _get_conn()
        close = True
    cur = conn.cursor()

    print(f"\n{'='*60}")
    print(f"  Research Inspector — {trace_id}")
    print(f"{'='*60}")

    cur.execute("""
        SELECT candidate_id, stock_code, stock_name, sector,
               observed_at, price, rs_score, rvol, lifecycle_status
        FROM research.candidates WHERE trace_id=%s ORDER BY observed_at
    """, (trace_id,))
    candidates = cur.fetchall()
    if not candidates:
        print("  [!] No candidates found for this trace_id")
        if close:
            conn.close()
        return

    for c in candidates:
        print(f"\n  CANDIDATE  {c[0]}")
        print(f"    stock    : {c[1]}  {c[2] or ''}  [{c[3] or '-'}]")
        obs_str = c[4].strftime('%Y-%m-%d %H:%M:%S') if c[4] else '-'
        print(f"    observed : {obs_str}")
        if c[5]:
            print(f"    price    : {float(c[5]):,.0f}")
        if c[6]:
            print(f"    rs_score : {float(c[6]):.1f}  rvol={float(c[7]):.2f}" if c[7] else f"    rs_score : {float(c[6]):.1f}")
        print(f"    status   : {c[8]}")

    cur.execute("""
        SELECT d.decision_id, d.stock_code, d.decision, d.decision_reason_code,
               d.policy_version, d.decided_at, d.lifecycle_status,
               d.confidence, d.expected_rr, d.risk_score, d.decision_verdict,
               d.decision_auditor_result->>'quality',
               d.outcome_auditor_result->>'quality'
        FROM research.decision_ledger d WHERE d.trace_id=%s ORDER BY d.decided_at
    """, (trace_id,))
    decisions = cur.fetchall()

    for d in decisions:
        print(f"\n  DECISION   {d[0]}")
        print(f"    stock    : {d[1]}")
        print(f"    decision : {d[2]}  reason={d[3]}")
        print(f"    policy   : {d[4]}")
        dec_str = d[5].strftime('%H:%M:%S') if d[5] else '-'
        print(f"    decided  : {dec_str}")
        print(f"    status   : {d[6]}")
        if d[7] is not None:
            print(f"    scores   : confidence={float(d[7]):.2f}  RR={d[8]}  risk={float(d[9]):.2f}" if d[8] else f"    scores   : confidence={float(d[7]):.2f}")
        if d[10]:
            print(f"    verdict  : {str(d[10])[:80]}...")
        if d[11]:
            print(f"    audit    : decision={d[11]}  outcome={d[12]}")

        cur.execute("""
            SELECT horizon_label, return_pct FROM research.future_return_events
            WHERE decision_id=%s::uuid ORDER BY recorded_at
        """, (d[0],))
        returns = cur.fetchall()
        if returns:
            ret_str = '  '.join(
                f"{r[0]}:{float(r[1]):+.1f}%" for r in returns
            )
            print(f"    returns  : {ret_str}")

        cur.execute("""
            SELECT outcome_label FROM research.decision_outcomes WHERE decision_id=%s::uuid
        """, (d[0],))
        lbl = cur.fetchone()
        if lbl and lbl[0]:
            print(f"    outcome  : {lbl[0]}")

    print(f"\n  TIMELINE (event_store)")
    cur.execute("""
        SELECT occurred_at, event_type, entity_type, source
        FROM research.event_store
        WHERE payload->>'trace_id'=%s ORDER BY occurred_at
    """, (trace_id,))
    for e in cur.fetchall():
        ts = e[0].strftime('%H:%M:%S.%f')[:12] if e[0] else '-'
        print(f"    {ts}  {e[1]:<35}  [{e[2]}] via {e[3]}")

    print(f"\n{'='*60}\n")
    if close:
        conn.close()


# ─── View: By Date ────────────────────────────────────────────────

def inspect_date(target_date: date, conn=None) -> None:
    """해당일 모든 Decision 요약 테이블."""
    close = False
    if conn is None:
        conn = _get_conn()
        close = True
    cur = conn.cursor()
    d = str(target_date)

    print(f"\n{'='*70}")
    print(f"  Research Inspector — {d}  (모든 Decision)")
    print(f"{'='*70}")

    cur.execute("""
        SELECT
            d.trace_id,
            d.stock_code,
            c.stock_name,
            d.decision,
            d.decision_reason_code,
            d.decided_at,
            d.lifecycle_status,
            d.confidence,
            EXTRACT(epoch FROM (d.recorded_at - d.decided_at)) * 1000 AS latency_ms
        FROM research.decision_ledger d
        LEFT JOIN research.candidates c ON c.candidate_id = d.candidate_id
        WHERE d.decided_at::date = %s
        ORDER BY d.decided_at
    """, (d,))
    rows = cur.fetchall()

    if not rows:
        print(f"  [!] {d} Decision 없음")
        if close:
            conn.close()
        return

    pass_cnt = sum(1 for r in rows if r[3] == 'PASS')
    rej_cnt  = sum(1 for r in rows if r[3] == 'REJECT')

    print(f"\n  총 {len(rows)}건  (PASS={pass_cnt}, REJECT={rej_cnt})\n")
    print(f"  {'TIME':8}  {'TICKER':8}  {'NAME':12}  {'DEC':7}  {'REASON':30}  {'STATUS':20}  {'LAT':7}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*12}  {'-'*7}  {'-'*30}  {'-'*20}  {'-'*7}")

    for r in rows:
        t    = r[5].strftime('%H:%M:%S') if r[5] else '-'
        tick = r[1] or '-'
        name = (r[2] or '-')[:12]
        dec  = r[3] or '-'
        rsn  = (r[4] or '-')[:30]
        sta  = (r[6] or '-')[:20]
        lat  = f"{float(r[8]):.1f}ms" if r[8] is not None else '-'
        print(f"  {t:8}  {tick:8}  {name:12}  {dec:7}  {rsn:30}  {sta:20}  {lat:7}")

    print()
    _reject_reason_summary(cur, d)
    if close:
        conn.close()


# ─── View: By Ticker ─────────────────────────────────────────────

def inspect_ticker(stock_code: str, limit: int = 20, conn=None) -> None:
    """종목별 최근 Decision 이력."""
    close = False
    if conn is None:
        conn = _get_conn()
        close = True
    cur = conn.cursor()

    print(f"\n{'='*70}")
    print(f"  Research Inspector — Ticker {stock_code}  (최근 {limit}건)")
    print(f"{'='*70}")

    cur.execute("""
        SELECT
            d.trace_id,
            d.decision,
            d.decision_reason_code,
            d.decided_at::date AS dt,
            d.decided_at,
            d.lifecycle_status,
            d.confidence,
            d.feature_snapshot->>'choch_grade' AS choch_grade,
            d.feature_snapshot->>'rvol' AS rvol,
            d.feature_snapshot->>'regime' AS regime,
            do2.outcome_label
        FROM research.decision_ledger d
        LEFT JOIN research.decision_outcomes do2 ON do2.decision_id = d.decision_id
        WHERE d.stock_code = %s
        ORDER BY d.decided_at DESC
        LIMIT %s
    """, (stock_code, limit))
    rows = cur.fetchall()

    if not rows:
        print(f"  [!] {stock_code} Decision 없음")
        if close:
            conn.close()
        return

    pass_cnt = sum(1 for r in rows if r[1] == 'PASS')
    rej_cnt  = sum(1 for r in rows if r[1] == 'REJECT')
    print(f"\n  {len(rows)}건  (PASS={pass_cnt}, REJECT={rej_cnt})\n")

    print(f"  {'DATE':10}  {'TIME':8}  {'DEC':7}  {'REASON':25}  {'CHG':5}  {'RVOL':5}  {'REGIME':12}  {'OUTCOME'}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*7}  {'-'*25}  {'-'*5}  {'-'*5}  {'-'*12}  {'-'*20}")

    for r in rows:
        dt  = str(r[3]) if r[3] else '-'
        t   = r[4].strftime('%H:%M:%S') if r[4] else '-'
        dec = r[1] or '-'
        rsn = (r[2] or '-')[:25]
        cg  = (r[7] or '-')[:5]
        rv  = f"{float(r[8]):.1f}" if r[8] else '-'
        reg = (r[9] or '-')[:12]
        out = (r[10] or '-')[:20]
        print(f"  {dt:10}  {t:8}  {dec:7}  {rsn:25}  {cg:5}  {rv:5}  {reg:12}  {out}")

    print()
    if close:
        conn.close()


# ─── View: By Reason ─────────────────────────────────────────────

def inspect_reason(reason_code: str, days: int = 7, conn=None) -> None:
    """거절 사유별 패턴 분석 (최근 N일)."""
    close = False
    if conn is None:
        conn = _get_conn()
        close = True
    cur = conn.cursor()

    print(f"\n{'='*60}")
    print(f"  Research Inspector — Reason={reason_code}  (최근 {days}일)")
    print(f"{'='*60}")

    cur.execute("""
        SELECT
            d.stock_code,
            c.stock_name,
            d.decided_at,
            d.feature_snapshot->>'choch_grade' AS choch_grade,
            d.feature_snapshot->>'rvol'        AS rvol,
            d.feature_snapshot->>'regime'      AS regime,
            d.trace_id,
            do2.outcome_label
        FROM research.decision_ledger d
        LEFT JOIN research.candidates c ON c.candidate_id = d.candidate_id
        LEFT JOIN research.decision_outcomes do2 ON do2.decision_id = d.decision_id
        WHERE d.decision_reason_code = %s
          AND d.decided_at >= NOW() - INTERVAL '%s days'
        ORDER BY d.decided_at DESC
        LIMIT 50
    """, (reason_code, days))
    rows = cur.fetchall()

    if not rows:
        print(f"  [!] {reason_code} 거절 없음 (최근 {days}일)")
        if close:
            conn.close()
        return

    print(f"\n  총 {len(rows)}건\n")
    print(f"  {'DATE':10}  {'TIME':8}  {'TICKER':8}  {'NAME':12}  {'CHG':5}  {'RVOL':5}  {'REGIME':12}  {'OUTCOME'}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*12}  {'-'*5}  {'-'*5}  {'-'*12}  {'-'*20}")

    regime_counts: dict = {}
    for r in rows:
        dt  = r[2].strftime('%Y-%m-%d') if r[2] else '-'
        t   = r[2].strftime('%H:%M:%S') if r[2] else '-'
        tc  = (r[0] or '-')[:8]
        nm  = (r[1] or '-')[:12]
        cg  = (r[3] or '-')[:5]
        rv  = f"{float(r[4]):.1f}" if r[4] else '-'
        reg = (r[5] or '-')[:12]
        out = (r[7] or '-')[:20]
        regime_counts[reg] = regime_counts.get(reg, 0) + 1
        print(f"  {dt:10}  {t:8}  {tc:8}  {nm:12}  {cg:5}  {rv:5}  {reg:12}  {out}")

    print(f"\n  Regime 분포:")
    for reg, cnt in sorted(regime_counts.items(), key=lambda x: -x[1]):
        bar = '█' * cnt
        print(f"    {reg:15} {cnt:4}  {bar}")
    print()

    if close:
        conn.close()


# ─── Helper ───────────────────────────────────────────────────────

def _reject_reason_summary(cur, target_date: str) -> None:
    cur.execute("""
        SELECT decision_reason_code, COUNT(*) AS cnt
        FROM research.decision_ledger
        WHERE decided_at::date = %s AND decision = 'REJECT'
        GROUP BY decision_reason_code ORDER BY cnt DESC
    """, (target_date,))
    rows = cur.fetchall()
    if not rows:
        return
    print(f"  거절 사유 분포:")
    for r in rows:
        bar = '█' * min(r[1], 30)
        print(f"    {(r[0] or '-'):35} {r[1]:4}  {bar}")
    print()


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Research Inspector — Decision Ledger 운영 조회',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python3 -m analysis.research_inspector --trace TR-20260701-000183
  python3 -m analysis.research_inspector --date 2026-07-01
  python3 -m analysis.research_inspector --ticker 005930
  python3 -m analysis.research_inspector --reason CHOCH_MISSING --days 14
"""
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--trace',  help='Trace ID (TR-YYYYMMDD-NNNNNN)')
    group.add_argument('--date',   help='날짜 (YYYY-MM-DD)')
    group.add_argument('--ticker', help='종목 코드 (예: 005930)')
    group.add_argument('--reason', help='거절 사유 코드 (예: CHOCH_MISSING)')

    parser.add_argument('--days',  type=int, default=7, help='--reason 검색 기간 (기본: 7일)')
    parser.add_argument('--limit', type=int, default=20, help='--ticker 최대 결과 수 (기본: 20)')

    args = parser.parse_args()

    if args.trace:
        inspect_trace(args.trace)
    elif args.date:
        inspect_date(date.fromisoformat(args.date))
    elif args.ticker:
        inspect_ticker(args.ticker, limit=args.limit)
    elif args.reason:
        inspect_reason(args.reason, days=args.days)
