"""
analysis/daily_strategy_data_quality.py — WI-23 §5/§6/§7/§8/§10/§11/§18 일일 품질감사

seq32~39(SMC 제외) HTS Candidate → Router → Strategy Monitor → Strategy Signal →
Virtual Trade → Outcome 파이프라인의 하루치 데이터를 SQL로 직접 조회해 무결성을
검사하고, phase1/reports/strategy_performance/에 감사 산출물을 남긴다.

Live 트레이딩 프로세스에는 연결하지 않는다 — 읽기 전용 오프라인 스크립트
(analysis/condition_signal_outcome_collector.py, analysis/strategy_kpi_report.py와
동일한 성격).

실행:
    python3 -m analysis.daily_strategy_data_quality --date 2026-08-12
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

VALID_SEQS = list(range(32, 40))  # 32~39
STRATEGY_NAMES = {
    32: 'Momentum', 33: 'Breakout', 34: 'EOD', 35: 'Supertrend+EMA+RSI',
    36: 'VWAP', 37: 'Squeeze Momentum Pro', 38: 'Bottom', 39: 'ITS',
}


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _weekday_days_since(d: date) -> int:
    """d(신호일)로부터 오늘까지 경과한 평일(월~금) 수 — 공휴일 캘린더는 반영하지
    않는 근사치. §18 'Outcome=0을 실패로 볼지' 판단용 게이트로만 쓴다(실제 +1D~+5D
    가격 계산은 condition_signal_outcome_collector.py가 거래일 인덱스 기반으로
    정확히 처리 — 여기서는 재구현하지 않는다)."""
    today = date.today()
    n = 0
    cur = d
    while cur < today:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


# ── §6 데이터 연결 무결성 ─────────────────────────────────────────────────────
def check_integrity(conn, date_str: str) -> Dict:
    out = {}
    with conn.cursor() as cur:
        # Candidate
        cur.execute("""
            SELECT COUNT(*) FROM research.condition_candidates c
            LEFT JOIN research.strategy_monitor_events me ON me.candidate_id = c.candidate_id
            WHERE c.observed_at::date = %s AND me.event_id IS NULL
        """, (date_str,))
        out['orphan_candidate'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT stock_code, condition_seq, observed_at, COUNT(*)
                FROM research.condition_candidates
                WHERE observed_at::date = %s
                GROUP BY 1,2,3 HAVING COUNT(*) > 1
            ) d
        """, (date_str,))
        out['duplicate_candidate'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.condition_candidates
            WHERE observed_at::date = %s AND condition_seq NOT BETWEEN 32 AND 39
        """, (date_str,))
        out['invalid_strategy_seq'] = cur.fetchone()[0]

        # Monitor
        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_monitor_events me
            LEFT JOIN research.condition_candidates c ON c.candidate_id = me.candidate_id
            WHERE me.observed_at::date = %s AND c.candidate_id IS NULL
        """, (date_str,))
        out['orphan_monitor_event'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_monitor_events
            WHERE observed_at::date = %s AND candidate_id IS NULL
        """, (date_str,))
        out['monitor_event_missing_candidate_id'] = cur.fetchone()[0]

        # Signal
        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals s
            LEFT JOIN research.strategy_monitor_events me ON me.event_id = s.monitor_event_id
            WHERE s.observed_at::date = %s AND me.event_id IS NULL
        """, (date_str,))
        out['orphan_signal'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT condition_seq, stock_code, observed_at, COUNT(*)
                FROM research.strategy_signals
                WHERE observed_at::date = %s
                GROUP BY 1,2,3 HAVING COUNT(*) > 1
            ) d
        """, (date_str,))
        out['duplicate_signal'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals
            WHERE observed_at::date = %s AND candidate_id IS NULL
        """, (date_str,))
        out['signal_missing_candidate_id'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals
            WHERE observed_at::date = %s AND condition_seq IS NULL
        """, (date_str,))
        out['signal_missing_strategy_seq'] = cur.fetchone()[0]

        # Outcome (전체 - 날짜 스코프 없음, signal_id로 연결)
        cur.execute("""
            SELECT COUNT(*) FROM research.signal_outcomes o
            LEFT JOIN research.strategy_signals s ON s.signal_id = o.signal_id
            WHERE s.signal_id IS NULL
        """)
        out['orphan_outcome'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT signal_id, horizon_label, COUNT(*)
                FROM research.signal_outcomes
                GROUP BY 1,2 HAVING COUNT(*) > 1
            ) d
        """)
        out['duplicate_outcome'] = cur.fetchone()[0]

        # future_data_reference: outcome recorded_at이 signal observed_at보다 과거인 경우(비정상)
        cur.execute("""
            SELECT COUNT(*) FROM research.signal_outcomes o
            JOIN research.strategy_signals s ON s.signal_id = o.signal_id
            WHERE o.recorded_at < s.observed_at
        """)
        out['invalid_exit_date'] = cur.fetchone()[0]

        # future_data_reference: price_at_horizon 조회 시점이 signal_timestamp보다
        # 미래인 것은 정상(그게 목적) — 여기서는 "signal 자체"가 자신보다 미래의
        # outcome 값을 참조해 만들어졌는지(즉 entry_price_reference가 outcome
        # 테이블에서 역참조된 값인지)를 검사한다. 구조적으로 entry_price_reference는
        # Signal INSERT 시점에 이미 확정되고 Outcome은 별도 배치가 나중에 채우므로
        # 코드 경로상 불가능 — 0건이어야 정상.
        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals s
            WHERE s.observed_at::date = %s
              AND s.entry_price_reference IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM research.signal_outcomes o
                  WHERE o.signal_id = s.signal_id
                    AND o.reference_price != s.entry_price_reference
              )
        """, (date_str,))
        out['future_data_reference'] = cur.fetchone()[0]

    out['total_integrity_errors'] = sum(out.values())
    return out


# ── §7 Signal 중복 상세 ───────────────────────────────────────────────────────
def check_signal_duplicates_detail(conn, date_str: str) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT condition_seq, stock_code, observed_at, COUNT(*) as n,
                   array_agg(signal_id) as signal_ids
            FROM research.strategy_signals
            WHERE observed_at::date = %s
            GROUP BY 1,2,3 HAVING COUNT(*) > 1
        """, (date_str,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── §8 Multi-Strategy Candidate 독립성 ────────────────────────────────────────
def check_multi_strategy_independence(conn, date_str: str) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT stock_code, array_agg(DISTINCT condition_seq ORDER BY condition_seq) as seqs,
                   COUNT(DISTINCT condition_seq) as n
            FROM research.condition_candidates
            WHERE observed_at::date = %s
            GROUP BY 1 HAVING COUNT(DISTINCT condition_seq) > 1
            ORDER BY n DESC
        """, (date_str,))
        multi = [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]

        for m in multi:
            cur.execute("""
                SELECT me.condition_seq, me.monitor_status
                FROM research.strategy_monitor_events me
                JOIN research.condition_candidates c ON c.candidate_id = me.candidate_id
                WHERE c.stock_code = %s AND c.observed_at::date = %s
                  AND me.condition_seq = ANY(%s)
            """, (m['stock_code'], date_str, m['seqs']))
            m['seq_status'] = dict(cur.fetchall())
        return multi


# ── §11 Virtual Trade 생성 검증 ────────────────────────────────────────────────
def check_virtual_trade_generation(conn, date_str: str) -> Dict:
    """Signal이 있는데 Virtual Entry(entry_price_reference)가 없는 경우 = FAIL 대상(§18)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals
            WHERE observed_at::date = %s AND (entry_price_reference IS NULL OR entry_price_reference <= 0)
        """, (date_str,))
        missing_entry = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals
            WHERE observed_at::date = %s
        """, (date_str,))
        total_signals = cur.fetchone()[0]
    return {
        'total_signals': total_signals,
        'virtual_trade_missing_entry_price': missing_entry,
        'virtual_trade_generated': total_signals - missing_entry,
    }


# ── §5 seq별 카운트 + §18 상태 판정 ────────────────────────────────────────────
def per_seq_counts(conn, date_str: str) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.condition_seq,
                   COUNT(DISTINCT c.candidate_id) AS candidate_count,
                   COUNT(DISTINCT me.event_id) AS monitor_event_count,
                   COUNT(DISTINCT me.event_id) FILTER (WHERE me.monitor_status='SIGNAL') AS signal_count_from_monitor
            FROM research.condition_candidates c
            LEFT JOIN research.strategy_monitor_events me ON me.candidate_id = c.candidate_id
            WHERE c.observed_at::date = %s
            GROUP BY 1
        """, (date_str,))
        base = {row[0]: {'candidate_count': row[1], 'monitor_event_count': row[2],
                          'signal_count_from_monitor': row[3]} for row in cur.fetchall()}

        cur.execute("""
            SELECT s.condition_seq, COUNT(*) AS signal_count,
                   COUNT(*) FILTER (WHERE s.entry_price_reference IS NOT NULL AND s.entry_price_reference > 0) AS virtual_trade_count,
                   MIN(s.observed_at) AS earliest_signal
            FROM research.strategy_signals s
            WHERE s.observed_at::date = %s
            GROUP BY 1
        """, (date_str,))
        sig = {row[0]: {'signal_count': row[1], 'virtual_trade_count': row[2],
                         'earliest_signal': row[3]} for row in cur.fetchall()}

        cur.execute("""
            SELECT s.condition_seq, COUNT(*) FILTER (WHERE o.horizon_label='+5D')
            FROM research.strategy_signals s
            JOIN research.signal_outcomes o ON o.signal_id = s.signal_id
            WHERE s.observed_at::date = %s
            GROUP BY 1
        """, (date_str,))
        outcome_complete = {row[0]: row[1] for row in cur.fetchall()}

    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    weekdays_elapsed = _weekday_days_since(d)

    rows = []
    for seq in VALID_SEQS:
        b = base.get(seq, {'candidate_count': 0, 'monitor_event_count': 0, 'signal_count_from_monitor': 0})
        s = sig.get(seq, {'signal_count': 0, 'virtual_trade_count': 0, 'earliest_signal': None})
        oc = outcome_complete.get(seq, 0)

        # §18 판정
        if s['signal_count'] == 0:
            status = 'OK'
            reason = 'Signal=0 (§18: 실패 아님)'
        elif s['virtual_trade_count'] == 0:
            status = 'DATA_QUALITY_FAIL'
            reason = 'Signal>0인데 Virtual Trade=0 (entry_price_reference 누락)'
        elif oc == 0 and weekdays_elapsed >= 6:
            # +1D~+5D 중 마지막(+5D)까지 필요한 평일 5일 + 여유 1일 = 6일 이상 지났는데도
            # outcome이 전혀 없으면 Outcome Collector가 안 도는 것으로 의심
            status = 'PIPELINE_BUG_SUSPECTED'
            reason = f'{weekdays_elapsed}평일 경과했는데 +5D Outcome 0건 - Outcome Collector 동작 여부 확인 필요'
        elif oc == 0:
            status = 'OK'
            reason = f'Virtual Trade>0, Outcome=0이지만 신호 이후 {weekdays_elapsed}평일만 경과 - +5D 완성까지 최소 5거래일 필요(§17), 실패 아님'
        else:
            status = 'OK'
            reason = f'{oc}건 +5D Outcome 완료'

        rows.append({
            'seq': seq, 'strategy': STRATEGY_NAMES[seq],
            'candidate_count': b['candidate_count'],
            'monitor_event_count': b['monitor_event_count'],
            'signal_count': s['signal_count'],
            'virtual_trade_count': s['virtual_trade_count'],
            'outcome_complete_count(+5D)': oc,
            'status': status,
            'reason': reason,
        })
    return rows


def run(date_str: str, out_dir: str):
    conn = _get_conn()
    seq_rows = per_seq_counts(conn, date_str)
    integrity = check_integrity(conn, date_str)
    dup_detail = check_signal_duplicates_detail(conn, date_str)
    multi = check_multi_strategy_independence(conn, date_str)
    vt = check_virtual_trade_generation(conn, date_str)
    conn.close()

    ymd = date_str.replace('-', '')
    os.makedirs(out_dir, exist_ok=True)

    # daily_strategy_counts_YYYYMMDD.csv
    import csv
    counts_path = os.path.join(out_dir, f'daily_strategy_counts_{ymd}.csv')
    with open(counts_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(seq_rows[0].keys()))
        w.writeheader()
        w.writerows(seq_rows)

    # overall verdict
    # multi-strategy 종목의 seq간 결과 오염 여부는 DB 단일 스냅샷만으로 판단할 수
    # 없다(재평가 반복 호출 비교가 필요) - 그건 tests/unit/test_strategy_monitors.py의
    # isolation 테스트(WI-22 wi22_isolation_report.md)가 이미 증명하므로 여기서는
    # multi_strategy_candidates를 참고 정보로만 decision.json에 남기고 verdict에는
    # 반영하지 않는다.
    fail_seqs = [r for r in seq_rows if r['status'] != 'OK']
    integrity_fail = integrity['total_integrity_errors'] > 0
    dup_fail = len(dup_detail) > 0

    if integrity_fail or dup_fail or vt['virtual_trade_missing_entry_price'] > 0:
        verdict = 'DATA QUALITY FAIL'
    elif any(r['status'] == 'PIPELINE_BUG_SUSPECTED' for r in seq_rows):
        verdict = 'PIPELINE FAIL'
    elif sum(r['signal_count'] for r in seq_rows) == 0:
        verdict = 'CONDITIONAL PASS'
    else:
        verdict = 'PASS'

    decision = {
        'date': date_str,
        'verdict': verdict,
        'integrity': integrity,
        'signal_duplicate_count': len(dup_detail),
        'signal_duplicate_detail': [
            {'seq': d['condition_seq'], 'symbol': d['stock_code'],
             'timestamp': str(d['observed_at']), 'count': d['n'],
             'signal_ids': [str(x) for x in d['signal_ids']]}
            for d in dup_detail
        ],
        'multi_strategy_candidates': [
            {'symbol': m['stock_code'], 'seqs': m['seqs'],
             'seq_status': {str(k): v for k, v in m['seq_status'].items()}}
            for m in multi
        ],
        'virtual_trade_generation': vt,
        'seq_status_summary': {r['seq']: r['status'] for r in seq_rows},
        'fail_seqs': [r['seq'] for r in fail_seqs],
    }
    decision_path = os.path.join(out_dir, f'daily_decision_{ymd}.json')
    with open(decision_path, 'w', encoding='utf-8') as f:
        json.dump(decision, f, ensure_ascii=False, indent=2, default=str)

    # markdown report
    lines = []
    lines.append(f'# Daily Strategy Data Quality — {date_str}\n')
    lines.append(f'**판정: {verdict}**\n')
    lines.append('## seq별 카운트\n')
    lines.append('| seq | 전략 | Candidate | Monitor | Signal | VirtualTrade | Outcome(+5D) | Status |')
    lines.append('|--:|---|--:|--:|--:|--:|--:|---|')
    for r in seq_rows:
        lines.append(f"| {r['seq']} | {r['strategy']} | {r['candidate_count']} | "
                      f"{r['monitor_event_count']} | {r['signal_count']} | "
                      f"{r['virtual_trade_count']} | {r['outcome_complete_count(+5D)']} | {r['status']} |")
    lines.append('\n## 무결성 검사 (§6)\n')
    for k, v in integrity.items():
        mark = '✅' if v == 0 else '❌'
        lines.append(f'- {mark} {k}: {v}')
    lines.append(f'\n## Signal 중복 검사 (§7): {len(dup_detail)}건\n')
    if dup_detail:
        for d in dup_detail:
            lines.append(f"- seq{d['condition_seq']} {d['stock_code']} {d['observed_at']}: {d['n']}건")
    else:
        lines.append('- 중복 없음')
    lines.append(f'\n## Multi-Strategy Candidate 독립성 (§8): {len(multi)}개 종목\n')
    for m in multi:
        lines.append(f"- {m['stock_code']}: seqs={m['seqs']} → {m['seq_status']}")
    lines.append('\n## Virtual Trade 생성 검증 (§11)\n')
    lines.append(f"- 총 Signal: {vt['total_signals']}, Virtual Trade 생성: {vt['virtual_trade_generated']}, "
                  f"Entry가격 누락: {vt['virtual_trade_missing_entry_price']}")

    md_path = os.path.join(out_dir, f'daily_data_quality_{ymd}.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f'판정: {verdict}')
    print(f'산출물: {counts_path}, {decision_path}, {md_path}')
    return decision


def main():
    ap = argparse.ArgumentParser(description='WI-23 Daily Strategy Data Quality Audit')
    ap.add_argument('--date', default=None)
    ap.add_argument('--out-dir', default='phase1/reports/strategy_performance')
    a = ap.parse_args()
    date_str = a.date or date.today().isoformat()
    run(date_str, a.out_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
