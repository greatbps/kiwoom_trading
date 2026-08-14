"""
analysis/wi26_strategy_performance_validation.py — WI-26 8개 전략 독립 성능검증.

seq32~39 각각을 완전히 독립적으로 평가한다(전략을 합쳐서 PF를 계산하지
않는다, §2.1). SMC는 이 계산 어디에도 등장하지 않는다(§2.2/§15/§16).

Virtual Trade 정의는 WI-22에서 고정한 것을 그대로 쓴다(§3):
    Entry = Signal 발생 시점 종가 (research.strategy_signals.entry_price_reference)
    Exit  = +5거래일 종가 (research.signal_outcomes WHERE horizon_label='+5D')

"Valid Outcome"의 정의(§4/§5/§18) — 아래 전부를 만족해야 유효로 센다:
    - entry_price_reference IS NOT NULL AND > 0
    - price_at_horizon IS NOT NULL (NULL이면 DATA_UNAVAILABLE로 간주해 제외.
      research.signal_outcomes에는애초에 조회 실패한 horizon은 행 자체가
      생성되지 않으므로 - condition_signal_outcome_collector.py의
      _price_n_trading_days_after()가 None을 반환하면 INSERT를 스킵함 -
      "가격=0으로 채워 KPI에 섞이는" 경로가 구조적으로 없다) - 그래도 방어적으로
      0 이하 값은 한 번 더 걸러낸다(§18).
    - return_pct IS NOT NULL AND NaN 아님

Live 트레이딩 프로세스에는 연결하지 않는다. 실제 API 호출도 하지 않는다
(오직 이미 DB/research.signal_outcomes에 적재된 값만 읽는다 - WI-25에서
겪은 키움 API 429와 완전히 무관하게 동작한다, §18).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import psycopg2

STRATEGY_NAMES = {
    32: 'Momentum', 33: 'Breakout', 34: 'EOD', 35: 'Supertrend+EMA+RSI',
    36: 'VWAP', 37: 'Squeeze Momentum Pro', 38: 'Bottom', 39: 'ITS',
}
VALID_SEQS = tuple(range(32, 40))
EXIT_HORIZON = '+5D'

# §4 표본 판정
INSUFFICIENT_THRESHOLD = 30
PROVISIONAL_THRESHOLD = 50

# §7 PASS 기준
PASS_MIN_TRADES = 50
PASS_MIN_PF = 1.20
PASS_MIN_EXPECTANCY = 0.0
PASS_MAX_BOOTSTRAP_P_PF_LT_1 = 0.10
PASS_MAX_MDD_ABS = 20.0  # |MDD| <= 20%


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ── §5 데이터 품질 사전검증 ────────────────────────────────────────────────────
def data_quality_check(conn) -> dict:
    out = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_monitor_events me
            LEFT JOIN research.condition_candidates c ON c.candidate_id = me.candidate_id
            WHERE c.candidate_id IS NULL
        """)
        out['orphan_monitor_event'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals s
            LEFT JOIN research.strategy_monitor_events me ON me.event_id = s.monitor_event_id
            WHERE me.event_id IS NULL
        """)
        out['orphan_signal'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.signal_outcomes o
            LEFT JOIN research.strategy_signals s ON s.signal_id = o.signal_id
            WHERE s.signal_id IS NULL
        """)
        out['orphan_outcome'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT condition_seq, stock_code, observed_at, COUNT(*)
                FROM research.strategy_signals GROUP BY 1,2,3 HAVING COUNT(*) > 1
            ) d
        """)
        out['duplicate_signal'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT signal_id, horizon_label, COUNT(*)
                FROM research.signal_outcomes GROUP BY 1,2 HAVING COUNT(*) > 1
            ) d
        """)
        out['duplicate_outcome'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals
            WHERE entry_price_reference IS NULL OR entry_price_reference <= 0
        """)
        out['missing_entry_price'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.signal_outcomes
            WHERE price_at_horizon IS NOT NULL AND price_at_horizon <= 0
        """)
        out['invalid_exit_price'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.signal_outcomes
            WHERE return_pct IS NULL AND price_at_horizon IS NOT NULL
        """)
        out['nan_return'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.signal_outcomes o
            JOIN research.strategy_signals s ON s.signal_id = o.signal_id
            WHERE o.recorded_at < s.observed_at
        """)
        out['lookahead_violation'] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM research.strategy_signals
            WHERE condition_seq NOT BETWEEN 32 AND 39
        """)
        out['invalid_strategy_seq'] = cur.fetchone()[0]

    out['total_errors'] = sum(out.values())
    return out


# ── §4/§18 Valid Outcome 조회 (전략별 독립, §2.1) ────────────────────────────
def fetch_valid_outcomes(conn, seq: int) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.signal_id, s.stock_code, s.observed_at,
                   o.return_pct, o.mfe_pct, o.mae_pct
            FROM research.strategy_signals s
            JOIN research.signal_outcomes o
              ON o.signal_id = s.signal_id AND o.horizon_label = %s
            WHERE s.condition_seq = %s
              AND s.entry_price_reference IS NOT NULL AND s.entry_price_reference > 0
              AND o.price_at_horizon IS NOT NULL AND o.price_at_horizon > 0
              AND o.return_pct IS NOT NULL
            ORDER BY s.observed_at
        """, (EXIT_HORIZON, seq))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def classify_sample_size(n: int) -> str:
    if n < INSUFFICIENT_THRESHOLD:
        return 'INSUFFICIENT_DATA'
    if n < PROVISIONAL_THRESHOLD:
        return 'PROVISIONAL'
    return 'FULL_EVALUATION'


# ── §6 KPI ────────────────────────────────────────────────────────────────────
def _mdd(returns: List[float]) -> float:
    equity = peak = 100.0
    mdd = 0.0
    for r in returns:
        equity *= (1 + r / 100.0)
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak * 100.0)
    return round(mdd, 2)


def compute_basic_kpi(trades: List[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {'trades': 0, 'win_rate': None, 'avg_win_pct': None, 'avg_loss_pct': None,
                'profit_factor': None, 'expectancy_pct': None, 'total_return_pct': None,
                'mdd_pct': None}
    returns = [t['return_pct'] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        'trades': n,
        'win_rate': round(len(wins) / n * 100, 2),
        'avg_win_pct': round(sum(wins) / len(wins), 4) if wins else None,
        'avg_loss_pct': round(sum(losses) / len(losses), 4) if losses else None,
        'profit_factor': (round(gross_win / gross_loss, 4) if gross_loss > 0
                           else (None if gross_win == 0 else float('inf'))),
        'expectancy_pct': round(sum(returns) / n, 4),
        'total_return_pct': round(sum(returns), 4),
        'mdd_pct': _mdd(returns),
    }


# ── §6 Bootstrap ──────────────────────────────────────────────────────────────
def bootstrap_stats(returns: List[float], n_iter: int = 5000, seed: int = 42,
                     min_n: int = 10) -> Optional[dict]:
    if len(returns) < min_n:
        return None
    rng = np.random.default_rng(seed)
    arr = np.array(returns, dtype=float)
    pfs, expectancies = [], []
    for _ in range(n_iter):
        sample = rng.choice(arr, size=len(arr), replace=True)
        gains = sample[sample > 0].sum()
        losses = -sample[sample < 0].sum()
        pf = (gains / losses) if losses > 0 else (float('inf') if gains > 0 else 0.0)
        pfs.append(pf)
        expectancies.append(sample.mean())
    pfs_arr = np.array(pfs)
    finite_pfs = pfs_arr[np.isfinite(pfs_arr)]
    exp_arr = np.array(expectancies)
    return {
        'n_iter': n_iter,
        'bootstrap_pf_mean': round(float(np.mean(finite_pfs)), 4) if len(finite_pfs) else None,
        'bootstrap_p_pf_lt_1': round(float(np.mean(pfs_arr < 1.0)), 4),
        'bootstrap_expectancy_mean': round(float(np.mean(exp_arr)), 4),
        'bootstrap_expectancy_ci95': [
            round(float(np.percentile(exp_arr, 2.5)), 4),
            round(float(np.percentile(exp_arr, 97.5)), 4),
        ],
    }


# ── §8 최근 성능 ────────────────────────────────────────────────────────────
def recent_window_kpi(trades: List[dict], days: int, as_of: Optional[datetime] = None) -> dict:
    as_of = as_of or datetime.now()
    cutoff = as_of - timedelta(days=days)
    window_trades = [t for t in trades if t['observed_at'].replace(tzinfo=None) >= cutoff]
    return compute_basic_kpi(window_trades)


# ── §9 Distribution Shift (기간 부족 시 N/A 명시) ────────────────────────────
def distribution_shift_check(trades: List[dict]) -> dict:
    if len(trades) < 20:
        return {'status': 'INSUFFICIENT_PERIOD',
                'note': f'표본 {len(trades)}건 - 기간 분할 비교에 필요한 최소치(20) 미달. '
                        'Train/Validation/Test 분리는 표본이 더 쌓인 뒤 재실행 필요.'}
    mid = len(trades) // 2
    first_half, second_half = trades[:mid], trades[mid:]
    r1 = [t['return_pct'] for t in first_half]
    r2 = [t['return_pct'] for t in second_half]
    return {
        'status': 'COMPARED',
        'first_half_n': len(first_half), 'second_half_n': len(second_half),
        'first_half_mean_return': round(float(np.mean(r1)), 4),
        'second_half_mean_return': round(float(np.mean(r2)), 4),
        'first_half_std': round(float(np.std(r1)), 4),
        'second_half_std': round(float(np.std(r2)), 4),
        'signal_frequency_first_half_per_day': None,  # 날짜 범위 부족시 계산 보류
        'signal_frequency_second_half_per_day': None,
    }


# ── §10 Walk-Forward (표본 부족 시 실행 안 함) ────────────────────────────────
def walk_forward_check(trades: List[dict], min_n_per_window: int = 20) -> dict:
    if len(trades) < min_n_per_window * 3:
        return {'status': 'SKIPPED',
                'note': f'Train/Validation/Test 3구간 각 최소 {min_n_per_window}건 '
                        f'필요 - 현재 {len(trades)}건으로 미실행(§10 "표본이 충분한 '
                        '전략에 대해서만" 원칙)'}
    n = len(trades)
    third = n // 3
    train, val, test = trades[:third], trades[third:2 * third], trades[2 * third:]
    return {
        'status': 'EXECUTED',
        'train': compute_basic_kpi(train), 'validation': compute_basic_kpi(val),
        'test': compute_basic_kpi(test),
        'note': 'Test 구간은 Threshold 최적화에 사용하지 않았음(§10)',
    }


# ── §7 PASS 판정 ──────────────────────────────────────────────────────────────
def apply_pass_criteria(basic: dict, bootstrap: Optional[dict], sample_class: str) -> tuple:
    if sample_class in ('INSUFFICIENT_DATA', 'PROVISIONAL'):
        return sample_class, f'표본 분류={sample_class} (FULL_EVALUATION 미달, PASS/FAIL 판정 보류)'

    reasons = []
    if basic['trades'] < PASS_MIN_TRADES:
        reasons.append(f"trades {basic['trades']} < {PASS_MIN_TRADES}")
    pf = basic['profit_factor']
    if pf is None or pf <= PASS_MIN_PF:
        reasons.append(f"PF {pf} <= {PASS_MIN_PF}")
    if basic['expectancy_pct'] is None or basic['expectancy_pct'] <= PASS_MIN_EXPECTANCY:
        reasons.append(f"Expectancy {basic['expectancy_pct']} <= {PASS_MIN_EXPECTANCY}")
    if bootstrap is None or bootstrap['bootstrap_p_pf_lt_1'] > PASS_MAX_BOOTSTRAP_P_PF_LT_1:
        p_pf_lt_1 = bootstrap['bootstrap_p_pf_lt_1'] if bootstrap else None
        reasons.append(f"Bootstrap P(PF<1) {p_pf_lt_1} > {PASS_MAX_BOOTSTRAP_P_PF_LT_1}")
    if basic['mdd_pct'] is None or basic['mdd_pct'] > PASS_MAX_MDD_ABS:
        reasons.append(f"MDD {basic['mdd_pct']} > {PASS_MAX_MDD_ABS}")

    if reasons:
        return 'FAIL', '; '.join(reasons)
    return 'PASS', 'PF/Expectancy/Bootstrap/MDD/Trades 전부 기준 충족'


def evaluate_strategy(conn, seq: int) -> dict:
    trades = fetch_valid_outcomes(conn, seq)
    n = len(trades)
    sample_class = classify_sample_size(n)
    basic = compute_basic_kpi(trades)
    returns = [t['return_pct'] for t in trades]
    bootstrap = bootstrap_stats(returns)
    recent_3m = recent_window_kpi(trades, 90)
    recent_6m = recent_window_kpi(trades, 180)
    dist_shift = distribution_shift_check(trades)
    walk_forward = walk_forward_check(trades)
    verdict, verdict_reason = apply_pass_criteria(basic, bootstrap, sample_class)

    live_candidate = {
        'PASS': 'LIVE_CANDIDATE', 'FAIL': 'EXCLUDED',
        'PROVISIONAL': 'CONTINUE_COLLECTING', 'INSUFFICIENT_DATA': 'CONTINUE_COLLECTING',
    }[verdict if verdict in ('PASS', 'FAIL') else sample_class]

    return {
        'seq': seq, 'strategy': STRATEGY_NAMES[seq], 'valid_outcomes': n,
        'sample_class': sample_class, 'basic_kpi': basic, 'bootstrap': bootstrap,
        'recent_3m': recent_3m, 'recent_6m': recent_6m,
        'distribution_shift': dist_shift, 'walk_forward': walk_forward,
        'verdict': verdict, 'verdict_reason': verdict_reason,
        'live_candidate_status': live_candidate,
    }


def run(out_dir: str) -> dict:
    conn = _get_conn()
    dq = data_quality_check(conn)
    results = {seq: evaluate_strategy(conn, seq) for seq in VALID_SEQS}
    conn.close()

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'wi26_data_quality.json'), 'w', encoding='utf-8') as f:
        json.dump(dq, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, 'wi26_strategy_kpi.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(out_dir, 'wi26_bootstrap_results.json'), 'w', encoding='utf-8') as f:
        json.dump({seq: r['bootstrap'] for seq, r in results.items()}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, 'wi26_walk_forward.json'), 'w', encoding='utf-8') as f:
        json.dump({seq: r['walk_forward'] for seq, r in results.items()}, f, ensure_ascii=False, indent=2, default=str)

    import csv
    with open(os.path.join(out_dir, 'wi26_strategy_performance.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['seq', 'strategy', 'valid_outcomes', 'sample_class', 'trades',
                    'win_rate', 'profit_factor', 'expectancy_pct', 'mdd_pct',
                    'bootstrap_p_pf_lt_1', 'recent_3m_pf', 'recent_6m_pf',
                    'verdict', 'live_candidate_status'])
        for seq in VALID_SEQS:
            r = results[seq]
            b = r['basic_kpi']
            bs = r['bootstrap']
            w.writerow([
                seq, r['strategy'], r['valid_outcomes'], r['sample_class'], b['trades'],
                b['win_rate'], b['profit_factor'], b['expectancy_pct'], b['mdd_pct'],
                bs['bootstrap_p_pf_lt_1'] if bs else '',
                r['recent_3m']['profit_factor'], r['recent_6m']['profit_factor'],
                r['verdict'], r['live_candidate_status'],
            ])

    decision = {
        'data_quality': dq, 'data_quality_pass': dq['total_errors'] == 0,
        'per_strategy_verdict': {seq: results[seq]['verdict'] for seq in VALID_SEQS},
        'live_candidates': [seq for seq in VALID_SEQS
                             if results[seq]['live_candidate_status'] == 'LIVE_CANDIDATE'],
        'excluded': [seq for seq in VALID_SEQS
                     if results[seq]['live_candidate_status'] == 'EXCLUDED'],
        'continue_collecting': [seq for seq in VALID_SEQS
                                 if results[seq]['live_candidate_status'] == 'CONTINUE_COLLECTING'],
    }
    with open(os.path.join(out_dir, 'wi26_decision.json'), 'w', encoding='utf-8') as f:
        json.dump(decision, f, ensure_ascii=False, indent=2, default=str)

    print(json.dumps(decision, ensure_ascii=False, indent=2, default=str))
    return {'data_quality': dq, 'results': results, 'decision': decision}


def main():
    ap = argparse.ArgumentParser(description='WI-26 Strategy Performance Validation')
    ap.add_argument('--out-dir', default='phase1/reports/strategy_performance')
    a = ap.parse_args()
    run(a.out_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
