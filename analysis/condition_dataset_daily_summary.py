"""
analysis/condition_dataset_daily_summary.py — WI-13 일일 집계 + Backtest-Readiness 판정

research.condition_candidates / strategy_monitor_events / strategy_signals /
signal_outcomes 를 읽어 seq별 집계와 Backtest-Ready 여부를 계산한다.
읽기 전용. Production 코드 무접촉.

실행:
    python3 -m analysis.condition_dataset_daily_summary
    python3 -m analysis.condition_dataset_daily_summary --since 2026-08-11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'phase1', 'reports', 'strategy_dataset')
os.makedirs(OUT_DIR, exist_ok=True)

SEQ_STRATEGY = {
    32: 'Momentum', 33: 'Breakout', 34: 'EOD', 35: 'Supertrend+EMA+RSI',
    36: 'VWAP', 37: 'Squeeze Momentum Pro', 38: 'Bottom', 39: 'ITS',
}
SEQ_MONITOR_CLASS = {
    32: 'MomentumMonitor', 33: 'BreakoutMonitor', 34: 'EODMonitor', 35: 'TrendMonitor',
    36: 'VWAPMonitor', 37: 'SqueezeMonitor', 38: 'BottomMonitor', 39: 'ITSMonitor',
}
NOT_IMPLEMENTED_SEQ = {34, 35, 39}

READY_CANDIDATE_MIN = 100
READY_SIGNAL_MIN = 30


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _rows(conn, sql, params=()):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser(description="WI-13 Dataset 일일 집계")
    ap.add_argument('--since', type=str, default=None,
                     help='YYYY-MM-DD, 생략 시 전체 누적')
    a = ap.parse_args()

    since_dt = datetime.strptime(a.since, '%Y-%m-%d') if a.since else None
    conn = _get_conn()

    where = "WHERE cc.observed_at >= %s" if since_dt else ""
    params = (since_dt,) if since_dt else ()

    # ── §19 raw trace ──
    candidate_monitor = _rows(conn, f"""
        SELECT cc.candidate_id, cc.observed_at, cc.stock_code, cc.stock_name,
               cc.condition_seq, cc.condition_name, cc.condition_sources,
               sme.event_id AS monitor_event_id, sme.monitor_name,
               sme.monitor_status, sme.monitor_state, sme.monitor_error
        FROM research.condition_candidates cc
        LEFT JOIN research.strategy_monitor_events sme
               ON sme.candidate_id = cc.candidate_id
        {where}
        ORDER BY cc.observed_at
    """, params)

    signals = _rows(conn, f"""
        SELECT s.signal_id, s.candidate_id, s.monitor_event_id, s.observed_at,
               s.stock_code, s.condition_seq, s.strategy_name, s.signal_type,
               s.signal_score, s.entry_price_reference, s.signal_reason
        FROM research.strategy_signals s
        JOIN research.condition_candidates cc ON cc.candidate_id = s.candidate_id
        {where}
        ORDER BY s.observed_at
    """, params)

    outcomes = _rows(conn, """
        SELECT o.outcome_event_id, o.signal_id, o.stock_code, o.horizon_label,
               o.reference_price, o.price_at_horizon, o.return_pct,
               o.mfe_pct, o.mae_pct, o.recorded_at
        FROM research.signal_outcomes o
        ORDER BY o.recorded_at
    """)

    # ── §15/§19 seq별 집계 ──
    summary_rows = []
    quality_rows = []
    readiness_rows = []

    for seq, strat in SEQ_STRATEGY.items():
        sub = [r for r in candidate_monitor if r['condition_seq'] == seq]
        candidate_n = len(sub)
        monitor_evaluated = sum(1 for r in sub if r['monitor_event_id'] is not None)
        signal_n = sum(1 for r in sub if r['monitor_status'] == 'SIGNAL')
        no_signal_n = sum(1 for r in sub if r['monitor_status'] == 'NO_SIGNAL')
        error_n = sum(1 for r in sub if r['monitor_status'] == 'ERROR')
        not_impl_n = sum(1 for r in sub if r['monitor_status'] == 'NOT_IMPLEMENTED')

        expected_cls = SEQ_MONITOR_CLASS[seq]
        routing_errors = sum(1 for r in sub if r['monitor_event_id'] is not None
                              and r['monitor_name'] != expected_cls)
        routing_acc = (1.0 - routing_errors / monitor_evaluated) if monitor_evaluated else None

        attribution_errors = sum(
            1 for r in sub
            if r['condition_sources'] is not None
            and seq not in (r['condition_sources'] if isinstance(r['condition_sources'], list)
                             else json.loads(r['condition_sources']))
        )
        attribution_acc = (1.0 - attribution_errors / candidate_n) if candidate_n else None

        summary_rows.append({
            'seq': seq, 'strategy': strat, 'candidate': candidate_n,
            'monitor_evaluated': monitor_evaluated, 'signal': signal_n,
            'no_signal': no_signal_n, 'error': error_n,
            'not_implemented': not_impl_n,
            'routing_accuracy': routing_acc, 'attribution_accuracy': attribution_acc,
        })
        quality_rows.append({
            'seq': seq, 'routing_errors': routing_errors,
            'attribution_errors': attribution_errors, 'monitor_errors': error_n,
        })

        if seq in NOT_IMPLEMENTED_SEQ:
            verdict = 'NOT_IMPLEMENTED'
        elif error_n > 0 or (routing_acc is not None and routing_acc < 1.0) \
                or (attribution_acc is not None and attribution_acc < 1.0):
            verdict = 'BLOCKED'
        elif candidate_n >= READY_CANDIDATE_MIN and signal_n >= READY_SIGNAL_MIN:
            verdict = 'BACKTEST_READY'
        elif candidate_n > 0:
            verdict = 'COLLECTING'
        else:
            verdict = 'NO_OBSERVATION'
        readiness_rows.append({
            'seq': seq, 'strategy': strat, 'candidate': candidate_n,
            'signal': signal_n, 'verdict': verdict,
        })

    # ── §12 전체 데이터 품질 ──
    total_candidate = len(candidate_monitor)
    total_monitor_err = sum(1 for r in candidate_monitor if r['monitor_status'] == 'ERROR')
    total_routing_err = sum(q['routing_errors'] for q in quality_rows)
    total_attr_err = sum(q['attribution_errors'] for q in quality_rows)

    orphan_signals = _rows(conn, """
        SELECT COUNT(*) AS n FROM research.strategy_signals s
        LEFT JOIN research.condition_candidates cc ON cc.candidate_id = s.candidate_id
        WHERE cc.candidate_id IS NULL
    """)[0]['n']

    duplicate_candidates = _rows(conn, """
        SELECT COUNT(*) AS n FROM (
            SELECT stock_code, condition_seq, observed_at, COUNT(*) c
            FROM research.condition_candidates
            GROUP BY stock_code, condition_seq, observed_at
            HAVING COUNT(*) > 1
        ) d
    """)[0]['n']

    unknown_seq = _rows(conn, """
        SELECT COUNT(*) AS n FROM research.condition_candidates
        WHERE condition_seq NOT BETWEEN 32 AND 39
    """)[0]['n']

    overall_routing_acc = (1.0 - total_routing_err / total_candidate) if total_candidate else None
    overall_attr_acc = (1.0 - total_attr_err / total_candidate) if total_candidate else None

    # ── 파일 출력 ──
    import csv

    def write_csv(name, rows, fieldnames):
        with open(os.path.join(OUT_DIR, name), 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)

    write_csv('strategy_dataset_summary.csv', summary_rows,
               ['seq', 'strategy', 'candidate', 'monitor_evaluated', 'signal',
                'no_signal', 'error', 'not_implemented',
                'routing_accuracy', 'attribution_accuracy'])
    write_csv('candidate_monitor_trace.csv', candidate_monitor,
               ['candidate_id', 'observed_at', 'stock_code', 'stock_name',
                'condition_seq', 'condition_name', 'condition_sources',
                'monitor_event_id', 'monitor_name', 'monitor_status',
                'monitor_state', 'monitor_error'])
    write_csv('signal_trace.csv', signals,
               ['signal_id', 'candidate_id', 'monitor_event_id', 'observed_at',
                'stock_code', 'condition_seq', 'strategy_name', 'signal_type',
                'signal_score', 'entry_price_reference', 'signal_reason'])
    write_csv('signal_outcome.csv', outcomes,
               ['outcome_event_id', 'signal_id', 'stock_code', 'horizon_label',
                'reference_price', 'price_at_horizon', 'return_pct',
                'mfe_pct', 'mae_pct', 'recorded_at'])
    write_csv('data_quality.csv', [{
        'total_candidate': total_candidate,
        'total_monitor_errors': total_monitor_err,
        'total_routing_errors': total_routing_err,
        'total_attribution_errors': total_attr_err,
        'overall_routing_accuracy': overall_routing_acc,
        'overall_attribution_accuracy': overall_attr_acc,
        'orphan_signals': orphan_signals,
        'duplicate_candidates': duplicate_candidates,
        'unknown_seq_candidates': unknown_seq,
    }], ['total_candidate', 'total_monitor_errors', 'total_routing_errors',
         'total_attribution_errors', 'overall_routing_accuracy',
         'overall_attribution_accuracy', 'orphan_signals',
         'duplicate_candidates', 'unknown_seq_candidates'])
    write_csv('backtest_readiness.csv', readiness_rows,
               ['seq', 'strategy', 'candidate', 'signal', 'verdict'])

    decision = {
        'total_candidate': total_candidate,
        'overall_routing_accuracy': overall_routing_acc,
        'overall_attribution_accuracy': overall_attr_acc,
        'total_monitor_errors': total_monitor_err,
        'orphan_signals': orphan_signals,
        'duplicate_candidates': duplicate_candidates,
        'unknown_seq_candidates': unknown_seq,
        'per_seq_readiness': {str(r['seq']): r['verdict'] for r in readiness_rows},
        'generated_at': datetime.now().isoformat(timespec='seconds'),
    }
    with open(os.path.join(OUT_DIR, 'decision.json'), 'w') as f:
        json.dump(decision, f, ensure_ascii=False, indent=2, default=str)

    print(f"candidate={total_candidate} routing_acc={overall_routing_acc} "
          f"attribution_acc={overall_attr_acc} monitor_errors={total_monitor_err}")
    for r in readiness_rows:
        print(f"  seq{r['seq']:>2} {r['strategy']:<24} candidate={r['candidate']:>4} "
              f"signal={r['signal']:>3} -> {r['verdict']}")

    conn.close()
    return decision


if __name__ == '__main__':
    main()
