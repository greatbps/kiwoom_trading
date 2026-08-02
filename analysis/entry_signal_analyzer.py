"""
진입 신호 품질 분석기 (DB 기반, 오프라인)

Usage:
    python -m analysis.entry_signal_analyzer                  # 전체 기간
    python -m analysis.entry_signal_analyzer --days 90        # 최근 90일
    python -m analysis.entry_signal_analyzer --from 2026-01-01 --to 2026-07-01
    python -m analysis.entry_signal_analyzer --smc_only       # SMC 진입만

출력:
    logs/entry_quality_summary.csv
    logs/grade_performance_summary.csv
    logs/time_bucket_performance_summary.csv
    콘솔 리포트
"""

import argparse
import csv
import sys
import psycopg2
from datetime import datetime, timedelta
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"

DB_CFG = dict(dbname='trading_system', user='postgres', password=os.getenv('POSTGRES_PASSWORD'), host='localhost')


# ── outcome 분류 ────────────────────────────────────────────────────────────

def classify_outcome(exit_category: str, exit_reason: str, profit_rate: float) -> str:
    """exit_category + exit_reason → outcome 레이블"""
    cat = (exit_category or '').upper()
    rsn = (exit_reason or '').upper()
    if cat == 'ALPHA_EXIT':
        if '부분청산' in (exit_reason or '') or 'TP' in rsn:
            return 'WIN_TP'
        return 'WIN_TRAIL'
    if cat == 'RISK_EXIT':
        if 'HARD STOP' in rsn or 'HARD_STOP' in rsn:
            return 'LOSS_HARD_STOP'
        if 'LCL' in rsn or 'LOSS_CUT' in rsn:
            return 'LOSS_LCL'
        return 'LOSS_STOP'
    if cat == 'EXPERIMENT_EXIT':
        if 'EARLY FAILURE' in rsn or 'EARLY_FAILURE' in rsn or '초기 실패' in (exit_reason or '').upper():
            return 'LOSS_EF'
        return 'LOSS_EF'
    if cat == 'SYSTEM_EXIT':
        if profit_rate is not None and profit_rate > 0.5:
            return 'WIN_SYSTEM'
        if profit_rate is not None and profit_rate < -0.5:
            return 'LOSS_TIME'
        return 'NO_EDGE'
    return 'NO_EDGE'


def classify_entry(entry_reason: str) -> str:
    """entry_reason → 전략 유형"""
    r = (entry_reason or '').upper()
    if 'A급' in (entry_reason or '') or 'CHoCH[A급]' in (entry_reason or ''):
        return 'SMC_A'
    if 'B급' in (entry_reason or '') or 'CHoCH[B급]' in (entry_reason or ''):
        return 'SMC_B'
    if 'CHoCH' in (entry_reason or '') or 'SMC' in r:
        return 'SMC_NO_GRADE'
    if 'EXPLORATION' in r:
        return 'EXPLORATION'
    if 'SWING' in r or 'PULLBACK' in (entry_reason or '').upper():
        return 'SWING_PULLBACK'
    if 'TREND BREAKOUT' in r:
        return 'TREND_BREAKOUT'
    if 'VWAP' in r:
        return 'LEGACY_VWAP'
    return 'UNKNOWN'


def classify_time_bucket(entry_minute: float) -> str:
    if entry_minute < 600:
        return '09:00~10:00'
    if entry_minute < 630:
        return '10:00~10:30'
    if entry_minute < 780:
        return '10:30~13:00'
    return '13:00~'


# ── DB 쿼리 ─────────────────────────────────────────────────────────────────

PAIR_QUERY = """
WITH paired AS (
  SELECT DISTINCT ON (b.trade_id)
    b.trade_id,
    b.stock_code,
    b.stock_name,
    b.trade_time AS buy_time,
    b.price AS buy_price,
    b.entry_reason,
    b.condition_name,
    EXTRACT(HOUR FROM b.trade_time)*60+EXTRACT(MINUTE FROM b.trade_time) AS entry_min,
    s.profit_rate,
    s.exit_reason,
    s.exit_category,
    s.trade_time AS sell_time,
    EXTRACT(EPOCH FROM (s.trade_time - b.trade_time))/60.0 AS hold_minutes
  FROM trades b
  JOIN trades s ON b.stock_code = s.stock_code
    AND s.trade_type = 'SELL'
    AND s.trade_time > b.trade_time
    AND s.trade_time <= b.trade_time + INTERVAL '30 hours'
  WHERE b.trade_type = 'BUY'
    AND s.profit_rate IS NOT NULL
    {date_filter}
  ORDER BY b.trade_id, s.trade_time
)
SELECT * FROM paired ORDER BY buy_time
"""


def fetch_trades(conn, date_from=None, date_to=None, smc_only=False):
    cur = conn.cursor()
    date_clauses = []
    if date_from:
        date_clauses.append(f"AND b.trade_time >= '{date_from}'")
    if date_to:
        date_clauses.append(f"AND b.trade_time <= '{date_to}'")
    if smc_only:
        date_clauses.append("AND (b.entry_reason ILIKE '%CHoCH%' OR b.entry_reason ILIKE '%SMC%')")
    date_filter = ' '.join(date_clauses)
    q = PAIR_QUERY.format(date_filter=date_filter)
    cur.execute(q)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    trades = []
    for row in rows:
        t = dict(zip(cols, row))
        t['outcome'] = classify_outcome(t['exit_category'], t['exit_reason'], t['profit_rate'])
        t['entry_type'] = classify_entry(t['entry_reason'])
        t['bucket'] = classify_time_bucket(float(t['entry_min'] or 0))
        t['has_sweep'] = 'sweep' in (t['entry_reason'] or '').lower() or 'Liquidity Sweep' in (t['entry_reason'] or '')
        trades.append(t)
    return trades


# ── 집계 함수 ────────────────────────────────────────────────────────────────

def agg(trades):
    if not trades:
        return {'n': 0, 'avg': 0, 'wr': 0, 'pf': 0, 'wins': 0, 'losses': 0, 'alpha_rate': 0}
    n = len(trades)
    pnls = [t['profit_rate'] for t in trades if t['profit_rate'] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    alpha_n = sum(1 for t in trades if t['outcome'] in ('WIN_TRAIL', 'WIN_TP'))
    return {
        'n': n,
        'avg': round(sum(pnls) / len(pnls), 3) if pnls else 0,
        'wr': round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        'pf': round(sum(wins) / sum(losses), 3) if losses and sum(losses) > 0 else 0,
        'wins': round(sum(wins), 2),
        'losses': round(sum(losses), 2),
        'alpha_rate': round(alpha_n / n * 100, 1),
        'alpha_n': alpha_n,
    }


def outcome_breakdown(trades):
    counts = {}
    for t in trades:
        counts[t['outcome']] = counts.get(t['outcome'], 0) + 1
    return counts


# ── 리포트 출력 ──────────────────────────────────────────────────────────────

def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print('='*70)


def run(args):
    conn = psycopg2.connect(**DB_CFG)

    date_from = None
    date_to = None
    if args.days:
        date_from = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
    if hasattr(args, 'from_date') and args.from_date:
        date_from = args.from_date
    if hasattr(args, 'to_date') and args.to_date:
        date_to = args.to_date

    trades = fetch_trades(conn, date_from, date_to, getattr(args, 'smc_only', False))

    if not trades:
        print("분석할 거래 데이터 없음")
        return

    print_section("전체 요약")
    g = agg(trades)
    print(f"  총 거래: {g['n']}건 | avg: {g['avg']}% | WR: {g['wr']}% | PF: {g['pf']}")
    print(f"  총 수익: +{g['wins']}% | 총 손실: -{g['losses']}%")
    print(f"  ALPHA_EXIT 비율: {g['alpha_rate']}% ({g['alpha_n']}/{g['n']}건)")
    print(f"\n  [Outcome 분포]")
    oc = outcome_breakdown(trades)
    for k, v in sorted(oc.items(), key=lambda x: -x[1]):
        pct = round(v / g['n'] * 100, 1)
        print(f"    {k:<18}: {v:>3}건 ({pct}%)")

    # ── 전략 유형별 ─────────────────────────────────────────────────────────
    print_section("전략 유형별 성과")
    print(f"  {'전략':<18} {'n':>4} {'avg':>7} {'WR%':>5} {'PF':>5} {'alpha%':>7}")
    by_strat = {}
    for t in trades:
        key = t['entry_type']
        by_strat.setdefault(key, []).append(t)
    for k in sorted(by_strat.keys(), key=lambda x: -len(by_strat[x])):
        g2 = agg(by_strat[k])
        print(f"  {k:<18} {g2['n']:>4} {g2['avg']:>7} {g2['wr']:>5} {g2['pf']:>5} {g2['alpha_rate']:>7}%")

    # ── 시간대별 ────────────────────────────────────────────────────────────
    print_section("진입 시간대별 성과 (entry time)")
    print(f"  {'시간대':<15} {'n':>4} {'avg':>7} {'WR%':>5} {'PF':>5} {'alpha%':>7} {'risk%':>6} {'ef%':>5}")
    by_bucket = {}
    for t in trades:
        by_bucket.setdefault(t['bucket'], []).append(t)
    for bucket in ['09:00~10:00', '10:00~10:30', '10:30~13:00', '13:00~']:
        ts = by_bucket.get(bucket, [])
        if not ts:
            continue
        g2 = agg(ts)
        risk_n = sum(1 for t in ts if t['outcome'].startswith('LOSS_HARD') or t['outcome'] == 'LOSS_STOP')
        ef_n = sum(1 for t in ts if t['outcome'] == 'LOSS_EF')
        risk_pct = round(risk_n / g2['n'] * 100, 1) if g2['n'] > 0 else 0
        ef_pct = round(ef_n / g2['n'] * 100, 1) if g2['n'] > 0 else 0
        print(f"  {bucket:<15} {g2['n']:>4} {g2['avg']:>7} {g2['wr']:>5} {g2['pf']:>5} {g2['alpha_rate']:>7}% {risk_pct:>6}% {ef_pct:>5}%")

    # ── 월별 추이 ────────────────────────────────────────────────────────────
    print_section("월별 성과 추이")
    print(f"  {'월':<12} {'n':>4} {'avg':>7} {'WR%':>5} {'PF':>5} {'alpha%':>7}")
    by_month = {}
    for t in trades:
        key = t['buy_time'].strftime('%Y-%m')
        by_month.setdefault(key, []).append(t)
    for m in sorted(by_month.keys()):
        g2 = agg(by_month[m])
        print(f"  {m:<12} {g2['n']:>4} {g2['avg']:>7} {g2['wr']:>5} {g2['pf']:>5} {g2['alpha_rate']:>7}%")

    # ── SMC A급 월별 ─────────────────────────────────────────────────────────
    smc_a_trades = [t for t in trades if t['entry_type'] == 'SMC_A']
    if smc_a_trades:
        print_section("SMC A급 진입 상세 (전체)")
        print(f"  {'날짜':<12} {'종목':<8} {'시간':>6} {'pnl':>7} {'outcome':<18} {'exit 요약'}")
        for t in smc_a_trades:
            exit_short = (t['exit_reason'] or '')[:50]
            print(f"  {str(t['buy_time'].date()):<12} {t['stock_code']:<8} "
                  f"{t['buy_time'].strftime('%H:%M'):>6} {t['profit_rate']:>7.3f}% "
                  f"{t['outcome']:<18} {exit_short}")

    # ── CSV 출력 ─────────────────────────────────────────────────────────────
    # 1) entry_quality_summary.csv
    out1 = LOG_DIR / "entry_quality_summary.csv"
    with open(out1, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=[
            'buy_date', 'buy_time', 'stock_code', 'entry_type', 'bucket',
            'has_sweep', 'profit_rate', 'outcome', 'exit_category',
            'exit_reason_short', 'hold_minutes'
        ])
        w.writeheader()
        for t in trades:
            w.writerow({
                'buy_date': t['buy_time'].date(),
                'buy_time': t['buy_time'].strftime('%H:%M'),
                'stock_code': t['stock_code'],
                'entry_type': t['entry_type'],
                'bucket': t['bucket'],
                'has_sweep': t['has_sweep'],
                'profit_rate': t['profit_rate'],
                'outcome': t['outcome'],
                'exit_category': t['exit_category'],
                'exit_reason_short': (t['exit_reason'] or '')[:60],
                'hold_minutes': round(float(t['hold_minutes'] or 0), 0),
            })

    # 2) grade_performance_summary.csv
    out2 = LOG_DIR / "grade_performance_summary.csv"
    with open(out2, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['entry_type', 'n', 'avg_pnl', 'wr_pct', 'pf', 'alpha_rate_pct',
                    'alpha_n', 'risk_n', 'ef_n', 'no_edge_n'])
        for k, ts in sorted(by_strat.items(), key=lambda x: -len(x[1])):
            g2 = agg(ts)
            risk_n = sum(1 for t in ts if t['outcome'].startswith('LOSS_HARD') or t['outcome'] == 'LOSS_STOP')
            ef_n = sum(1 for t in ts if t['outcome'] == 'LOSS_EF')
            no_edge_n = sum(1 for t in ts if t['outcome'] == 'NO_EDGE')
            w.writerow([k, g2['n'], g2['avg'], g2['wr'], g2['pf'], g2['alpha_rate'],
                        g2['alpha_n'], risk_n, ef_n, no_edge_n])

    # 3) time_bucket_performance_summary.csv
    out3 = LOG_DIR / "time_bucket_performance_summary.csv"
    with open(out3, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['bucket', 'n', 'avg_pnl', 'wr_pct', 'pf', 'alpha_rate_pct',
                    'alpha_n', 'risk_n', 'ef_n', 'no_edge_n'])
        for bucket in ['09:00~10:00', '10:00~10:30', '10:30~13:00', '13:00~']:
            ts = by_bucket.get(bucket, [])
            if not ts:
                continue
            g2 = agg(ts)
            risk_n = sum(1 for t in ts if t['outcome'].startswith('LOSS_HARD') or t['outcome'] == 'LOSS_STOP')
            ef_n = sum(1 for t in ts if t['outcome'] == 'LOSS_EF')
            no_edge_n = sum(1 for t in ts if t['outcome'] == 'NO_EDGE')
            w.writerow([bucket, g2['n'], g2['avg'], g2['wr'], g2['pf'], g2['alpha_rate'],
                        g2['alpha_n'], risk_n, ef_n, no_edge_n])

    print(f"\n[CSV 출력 완료]")
    print(f"  {out1}")
    print(f"  {out2}")
    print(f"  {out3}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description='진입 신호 품질 분석기')
    parser.add_argument('--days', type=int, default=None, help='최근 N일')
    parser.add_argument('--from', dest='from_date', default=None, help='시작일 YYYY-MM-DD')
    parser.add_argument('--to', dest='to_date', default=None, help='종료일 YYYY-MM-DD')
    parser.add_argument('--smc_only', action='store_true', help='SMC 진입만 분석')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
