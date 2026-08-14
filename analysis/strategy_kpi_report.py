"""
analysis/strategy_kpi_report.py — WI-22 §13 Strategy KPI Report

seq32~39 각 전략을 SMC/execute_buy와 완전히 독립적으로 평가하기 위한 Virtual
Trade 기반 KPI 계산기. Live 트레이딩 프로세스에는 연결하지 않는다(읽기 전용
오프라인 스크립트).

Virtual Trade 정의(WI-22 §8, wi22_virtual_trade_report.md 참조):
    Virtual Entry  = strategy_signals.entry_price_reference (Signal 발생 시점 종가)
    Virtual Exit   = signal_outcomes.horizon_label='+5D' 의 price_at_horizon
    Holding Period = 5 거래일 (전략 무관 고정값 — 전략별로 유리한 기준을 임의
                     선택하지 않는다는 §8 원칙)
    Exit Return    = signal_outcomes.return_pct (horizon='+5D')
    MFE/MAE        = signal_outcomes.mfe_pct / mae_pct (horizon='+5D' 윈도우 내 최대/최소)

+5D 데이터가 없는 Signal(아직 5거래일이 지나지 않음)은 KPI 계산에서 제외된다
(Virtual Trade가 아직 "청산"되지 않은 상태이므로 미완료로 취급 — 승패 계산에
섞지 않는다).

실행:
    python3 -m analysis.strategy_kpi_report
    python3 -m analysis.strategy_kpi_report --out phase1/reports/strategy_independent_validation/wi22_kpi_readiness.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

MIN_SIGNALS_FOR_KPI = 50
EXIT_HORIZON = '+5D'

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


def _fetch_virtual_trades(conn) -> Dict[int, List[dict]]:
    """seq별 '청산 완료'(=+5D outcome 존재) Virtual Trade 목록."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.condition_seq, s.strategy_name, s.signal_id, s.stock_code,
                   s.observed_at, o.return_pct, o.mfe_pct, o.mae_pct
            FROM research.strategy_signals s
            JOIN research.signal_outcomes o
              ON o.signal_id = s.signal_id AND o.horizon_label = %s
            WHERE o.return_pct IS NOT NULL
            ORDER BY s.condition_seq, s.observed_at
        """, (EXIT_HORIZON,))
        rows = cur.fetchall()

    by_seq: Dict[int, List[dict]] = defaultdict(list)
    for seq, name, sig_id, code, ts, ret, mfe, mae in rows:
        by_seq[seq].append({
            'signal_id': str(sig_id), 'stock_code': code, 'observed_at': ts,
            'return_pct': float(ret), 'mfe_pct': float(mfe) if mfe is not None else None,
            'mae_pct': float(mae) if mae is not None else None,
            'strategy_name': name,
        })
    return by_seq


def _fetch_pending_counts(conn) -> Dict[int, int]:
    """seq별 Signal은 있지만 아직 +5D outcome이 없는(청산 대기) 건수."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.condition_seq, COUNT(*)
            FROM research.strategy_signals s
            LEFT JOIN research.signal_outcomes o
              ON o.signal_id = s.signal_id AND o.horizon_label = %s
            WHERE o.signal_id IS NULL
            GROUP BY 1
        """, (EXIT_HORIZON,))
        return {seq: n for seq, n in cur.fetchall()}


def _mdd(returns: List[float]) -> float:
    """단순 순차 누적(체결순) MDD% — 포지션 사이즈 균등 가정."""
    equity = 100.0
    peak = 100.0
    mdd = 0.0
    for r in returns:
        equity *= (1 + r / 100.0)
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100.0
        mdd = max(mdd, dd)
    return round(mdd, 2)


def _bootstrap_p_pf_gt_1(returns: List[float], n_iter: int = 2000, seed: int = 42) -> Optional[float]:
    if len(returns) < 10:
        return None
    import numpy as np
    rng = np.random.default_rng(seed)
    arr = np.array(returns)
    wins_gt1 = 0
    for _ in range(n_iter):
        sample = rng.choice(arr, size=len(arr), replace=True)
        gains = sample[sample > 0].sum()
        losses = -sample[sample < 0].sum()
        pf = (gains / losses) if losses > 0 else (float('inf') if gains > 0 else 0.0)
        if pf > 1.0:
            wins_gt1 += 1
    return round(wins_gt1 / n_iter, 4)


def compute_kpi(seq: int, trades: List[dict]) -> dict:
    n = len(trades)
    row = {
        'seq': seq, 'strategy': STRATEGY_NAMES.get(seq, f'seq{seq}'),
        'virtual_trade_count': n,
    }
    if n == 0:
        row.update({k: None for k in (
            'win_rate', 'avg_win_pct', 'avg_loss_pct', 'profit_factor',
            'expectancy_pct', 'mdd_pct', 'avg_holding_period_days',
            'avg_mfe_pct', 'avg_mae_pct', 'bootstrap_p_pf_gt_1',
        )})
        row['data_sufficiency'] = 'NO_DATA'
        return row

    returns = [t['return_pct'] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)

    row['win_rate'] = round(len(wins) / n * 100, 2)
    row['avg_win_pct'] = round(sum(wins) / len(wins), 4) if wins else None
    row['avg_loss_pct'] = round(sum(losses) / len(losses), 4) if losses else None
    row['profit_factor'] = round(gross_win / gross_loss, 4) if gross_loss > 0 else (
        None if gross_win == 0 else float('inf'))
    row['expectancy_pct'] = round(sum(returns) / n, 4)
    row['mdd_pct'] = _mdd(returns)
    row['avg_holding_period_days'] = 5  # Virtual Exit = +5D 고정 (§8)
    mfes = [t['mfe_pct'] for t in trades if t['mfe_pct'] is not None]
    maes = [t['mae_pct'] for t in trades if t['mae_pct'] is not None]
    row['avg_mfe_pct'] = round(sum(mfes) / len(mfes), 4) if mfes else None
    row['avg_mae_pct'] = round(sum(maes) / len(maes), 4) if maes else None
    row['bootstrap_p_pf_gt_1'] = _bootstrap_p_pf_gt_1(returns)
    row['data_sufficiency'] = (
        'READY_FOR_PERFORMANCE_VALIDATION' if n >= MIN_SIGNALS_FOR_KPI else 'COLLECTING'
    )
    return row


def run(out_path: Optional[str] = None) -> List[dict]:
    conn = _get_conn()
    by_seq = _fetch_virtual_trades(conn)
    pending = _fetch_pending_counts(conn)
    conn.close()

    rows = []
    for seq in sorted(STRATEGY_NAMES.keys()):
        trades = by_seq.get(seq, [])
        row = compute_kpi(seq, trades)
        row['pending_virtual_trades(청산대기)'] = pending.get(seq, 0)
        rows.append(row)

    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            fieldnames = list(rows[0].keys()) if rows else []
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser(description='WI-22 Strategy KPI Report')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    rows = run(a.out)
    for r in rows:
        print(r)
    return 0


if __name__ == '__main__':
    sys.exit(main())
