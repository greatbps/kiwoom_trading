"""
analysis/wfo_optimizer.py — Grid Search + Walk-Forward Optimization (WFO)

역할:
  equity_controller DD 파라미터를 과거 거래 기반으로 자동 튜닝.
  In-Sample(학습) / Out-of-Sample(검증) 분리로 과최적화 방지.

흐름:
  trades.db → Walk-Forward Split → Grid Search(train) → OOS Test
  → 최적 파라미터 선택 → data/wfo_best_params.json 저장

사용법:
    python -m analysis.wfo_optimizer               # 기본 90일
    python -m analysis.wfo_optimizer --days 180
    python -m analysis.wfo_optimizer --workers 4 --no-chart

파라미터 조합 수: 3^4 = 81개 (쾌속 완료)

출력:
    logs/wfo_results_{date}.json    — 전체 결과
    logs/wfo_report_{date}.md       — 해석 보고서
    data/wfo_best_params.json       — equity_ctrl에 바로 적용 가능
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta, date
from itertools import product
from multiprocessing import Pool
from pathlib import Path
from typing import Iterator

import numpy as np

BASE       = Path(__file__).parent.parent
TRADES_DB  = BASE / 'data' / 'trades.db'
LOGS_DIR   = BASE / 'logs'
PARAMS_OUT = BASE / 'data' / 'wfo_best_params.json'

logger = logging.getLogger(__name__)

# ─── 튜닝 대상 파라미터 그리드 (81 combos = 3^4) ───────────────────────
PARAM_GRID = {
    'dd_tier_1':  [-0.03, -0.05, -0.07],   # 경계 DD 임계치
    'dd_tier_2':  [-0.08, -0.10, -0.12],   # 위험 DD 임계치
    'dd_mult_1':  [0.80,  0.85,  0.90],    # 경계 구간 사이징
    'dd_mult_2':  [0.50,  0.65,  0.75],    # 위험 구간 사이징
}

# WFO 비율
TRAIN_RATIO = 0.60
TEST_RATIO  = 0.20
STEP_RATIO  = 0.20

# 평가 가중치 (Sharpe 중시)
SCORE_W = {'sharpe': 0.55, 'equity': 0.30, 'mdd': 0.15}

# 최소 샘플 수
MIN_TRADES_TRAIN = 20
MIN_TRADES_TEST  = 5


# ─── 데이터 로드 ──────────────────────────────────────────────────────

def load_trades(days: int) -> list[dict]:
    """trades.db BUY/SELL 페어 → 실현손익 리스트 (날짜순)."""
    if not TRADES_DB.exists():
        return []
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    rows   = conn.execute(
        """
        SELECT stock_code, trade_type, price, realized_pnl, trade_date, timestamp
        FROM trades
        WHERE trade_date >= ?
        ORDER BY timestamp
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    buys:  dict[tuple, dict] = {}
    pairs: list[dict] = []
    for r in rows:
        key = (r['stock_code'], r['trade_date'])
        if r['trade_type'] == 'BUY':
            buys[key] = {'price': float(r['price']), 'date': r['trade_date'], 'ts': r['timestamp']}
        elif r['trade_type'] == 'SELL' and key in buys:
            buy = buys.pop(key)
            pnl_pct = (float(r['price']) - buy['price']) / buy['price'] * 100
            pairs.append({'date': r['trade_date'], 'pnl_pct': pnl_pct})

    return sorted(pairs, key=lambda x: x['date'])


# ─── Walk-Forward 분할 ───────────────────────────────────────────────

def walk_forward_split(
    trades: list[dict],
    train_ratio: float = TRAIN_RATIO,
    test_ratio:  float = TEST_RATIO,
    step_ratio:  float = STEP_RATIO,
) -> Iterator[tuple[list[dict], list[dict]]]:
    """시간 순서 기반 rolling window split 생성기."""
    n     = len(trades)
    start = 0
    tr    = int(n * train_ratio)
    te    = int(n * test_ratio)
    step  = max(1, int(n * step_ratio))

    while True:
        train_end = start + tr
        test_end  = train_end + te
        if test_end > n:
            break
        if train_end - start >= MIN_TRADES_TRAIN and test_end - train_end >= MIN_TRADES_TEST:
            yield trades[start:train_end], trades[train_end:test_end]
        start += step


# ─── 파라미터 조합 생성 ──────────────────────────────────────────────

def generate_combinations(grid: dict) -> list[dict]:
    keys   = list(grid.keys())
    combos = [dict(zip(keys, vals)) for vals in product(*grid.values())]
    return combos


# ─── 백테스트 엔진 (NumPy 기반) ─────────────────────────────────────

def _simulate_ec(pnl_pcts: np.ndarray, params: dict) -> np.ndarray:
    """
    equity controller DD 로직을 거래 순서대로 적용.
    Sequential이라 순수 루프 — 거래 수 적어 충분히 빠름.
    """
    n      = len(pnl_pcts)
    equity = 1.0
    peak   = 1.0
    adj    = np.empty(n)

    tier1  = params['dd_tier_1']
    tier2  = params['dd_tier_2']
    mult1  = params['dd_mult_1']
    mult2  = params['dd_mult_2']

    for i in range(n):
        dd = (equity - peak) / peak if peak > 0 else 0.0

        if dd <= tier2:
            dm = mult2
        elif dd <= tier1:
            dm = mult1
        else:
            dm = 1.0

        adj_pnl = pnl_pcts[i] * dm
        adj[i]  = adj_pnl
        equity  *= (1.0 + adj_pnl / 100.0)
        if equity > peak:
            peak = equity

    return adj


def evaluate_performance(adj_pnl: np.ndarray) -> dict:
    """Sharpe + MDD + 최종 수익률 → 복합 점수."""
    if len(adj_pnl) == 0:
        return {'sharpe': 0.0, 'mdd': 0.0, 'equity': 1.0, 'score': 0.0}

    cum     = np.cumsum(adj_pnl)
    running = np.maximum.accumulate(cum)
    drawdowns = cum - running                     # ≤ 0
    mdd       = float(np.min(drawdowns)) / 100    # ratio

    std = float(np.std(adj_pnl))
    sharpe = float(np.mean(adj_pnl)) / (std + 1e-8) * np.sqrt(252)

    equity_gain = float(np.sum(adj_pnl)) / 100   # 단순 합산 수익률

    score = (
        SCORE_W['sharpe']  * sharpe
        + SCORE_W['equity'] * equity_gain
        + SCORE_W['mdd']    * (1.0 + mdd)
    )
    return {
        'sharpe':      round(sharpe, 4),
        'mdd':         round(mdd, 4),
        'equity_gain': round(equity_gain, 4),
        'score':       round(score, 4),
    }


def run_backtest(trades: list[dict], params: dict) -> dict:
    pnls = np.array([t['pnl_pct'] for t in trades], dtype=np.float64)
    adj  = _simulate_ec(pnls, params)
    return evaluate_performance(adj)


# ─── 멀티프로세싱 워커 ───────────────────────────────────────────────

def _worker(args: tuple) -> tuple[dict, dict]:
    """Pool 워커: (params, train_trades) → (params, perf)"""
    params, trades = args
    return params, run_backtest(trades, params)


def grid_search_parallel(
    train: list[dict],
    combos: list[dict],
    n_workers: int = 4,
) -> tuple[dict, dict]:
    """병렬 Grid Search → 최고 점수 파라미터 반환."""
    args = [(p, train) for p in combos]

    with Pool(processes=min(n_workers, len(combos))) as pool:
        results = pool.map(_worker, args)

    best_params, best_perf = max(results, key=lambda x: x[1]['score'])
    return best_params, best_perf


# ─── WFO 통합 실행 ───────────────────────────────────────────────────

def walk_forward_optimization(
    trades:    list[dict],
    param_grid: dict   = None,
    n_workers:  int    = 4,
) -> list[dict]:
    """
    Walk-Forward Optimization 전체 실행.

    Returns:
        [{'window': N, 'params': {...}, 'train': {...}, 'test': {...}}, ...]
    """
    if param_grid is None:
        param_grid = PARAM_GRID
    combos = generate_combinations(param_grid)

    results = []
    for idx, (train, test) in enumerate(walk_forward_split(trades)):
        print(f"  [WFO] Window {idx+1}: train={len(train)} test={len(test)}", flush=True)

        best_params, train_perf = grid_search_parallel(train, combos, n_workers)
        test_perf               = run_backtest(test, best_params)

        results.append({
            'window':       idx + 1,
            'train_n':      len(train),
            'test_n':       len(test),
            'best_params':  best_params,
            'train_perf':   train_perf,
            'test_perf':    test_perf,
            'train_date':   train[0]['date'],
            'test_date':    test[-1]['date'],
        })
        print(f"         best_score(train)={train_perf['score']:.3f} "
              f"OOS_score={test_perf['score']:.3f}")

    return results


# ─── 결과 분석 ──────────────────────────────────────────────────────

def select_robust_params(results: list[dict], param_grid: dict) -> dict:
    """
    각 파라미터별 빈도 분석 → 가장 자주 선택된 값으로 robust 파라미터 구성.
    단일 최적값 대신 "많이 뽑힌 값"을 선택 → 과최적화 방지.
    """
    from collections import Counter
    freq: dict[str, Counter] = {k: Counter() for k in param_grid}

    for r in results:
        for k, v in r['best_params'].items():
            freq[k][v] += 1

    robust = {}
    for k, ctr in freq.items():
        robust[k] = ctr.most_common(1)[0][0]

    return robust


def generate_report(results: list[dict], robust: dict, days: int) -> str:
    lines = [
        f"# WFO 최적화 보고서 ({date.today().isoformat()})",
        f"분석 기간: 최근 {days}일 | 창 수: {len(results)}",
        "",
        "## Walk-Forward 결과",
        "| 창 | 학습 점수 | OOS 점수 | 과최적화 |",
        "|---|----------|---------|---------|",
    ]
    for r in results:
        gap = r['train_perf']['score'] - r['test_perf']['score']
        flag = '❌ 과최적화 의심' if gap > 0.5 else '✅ 안정'
        lines.append(
            f"| {r['window']} | {r['train_perf']['score']:.3f} "
            f"| {r['test_perf']['score']:.3f} | {flag} |"
        )

    oos_scores = [r['test_perf']['score'] for r in results]
    oos_sharpes = [r['test_perf']['sharpe'] for r in results]
    oos_mdds    = [r['test_perf']['mdd'] for r in results]

    lines += [
        "",
        "## OOS 통계 요약",
        f"- 평균 Sharpe: {np.mean(oos_sharpes):.3f}",
        f"- 평균 MDD:    {np.mean(oos_mdds):.2%}",
        f"- 평균 Score:  {np.mean(oos_scores):.3f}",
        f"- Score 표준편차: {np.std(oos_scores):.3f} (낮을수록 안정)",
        "",
        "## 권장 파라미터 (Robust — 가장 자주 선택된 값)",
        "```yaml",
        "equity_control:",
        "  drawdown_tiers:",
    ]
    for k, v in robust.items():
        lines.append(f"    {k}: {v}")
    lines += ["```", ""]

    # 과최적화 판정
    if len(results) >= 2:
        train_avg = np.mean([r['train_perf']['score'] for r in results])
        oos_avg   = np.mean(oos_scores)
        gap = train_avg - oos_avg
        if gap > 0.5:
            lines.append("⚠️ **주의**: 학습/OOS 점수 차이 크음 — 파라미터 수 줄이거나 학습 기간 늘려야 함")
        else:
            lines.append("✅ **안정**: 학습/OOS 점수 차이 허용 범위 — 권장 파라미터 적용 가능")

    return "\n".join(lines)


# ─── 저장 ────────────────────────────────────────────────────────────

def save_results(results: list[dict], robust: dict, report: str, today: str):
    LOGS_DIR.mkdir(exist_ok=True)

    # 전체 결과 JSON
    out = LOGS_DIR / f'wfo_results_{today}.json'
    out.write_text(json.dumps({
        'generated_at': datetime.now().isoformat(),
        'robust_params': robust,
        'windows': results,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  💾 결과: {out}")

    # 보고서 MD
    rpt = LOGS_DIR / f'wfo_report_{today}.md'
    rpt.write_text(report, encoding='utf-8')
    print(f"  💾 보고서: {rpt}")

    # equity_ctrl 적용 가능 best params
    PARAMS_OUT.parent.mkdir(exist_ok=True)
    PARAMS_OUT.write_text(json.dumps({
        'generated_at':  datetime.now().isoformat(),
        'robust_params': robust,
        'note': 'equity_control.drawdown_tiers 에 직접 적용 가능',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  💾 권장 파라미터: {PARAMS_OUT}")


# ─── 메인 ────────────────────────────────────────────────────────────

def run(days: int = 90, n_workers: int = 4):
    today = date.today().strftime('%Y%m%d')
    print(f"\n{'='*60}")
    print(f"  WFO 최적화 시작  ({days}일 데이터 | workers={n_workers})")
    print(f"  파라미터 조합: {len(generate_combinations(PARAM_GRID))}개")
    print(f"{'='*60}\n")

    trades = load_trades(days)
    if len(trades) < MIN_TRADES_TRAIN + MIN_TRADES_TEST:
        print(f"  ⚠️ 거래 데이터 부족 ({len(trades)}건 < {MIN_TRADES_TRAIN + MIN_TRADES_TEST}건)")
        return

    print(f"  로드된 거래: {len(trades)}건 ({trades[0]['date']} ~ {trades[-1]['date']})\n")

    results = walk_forward_optimization(trades, PARAM_GRID, n_workers)

    if not results:
        print("  ❌ WFO 결과 없음 — 데이터 부족")
        return

    robust = select_robust_params(results, PARAM_GRID)
    report = generate_report(results, robust, days)
    save_results(results, robust, report, today)

    print(f"\n{'='*60}")
    print("  권장 파라미터 (Robust):")
    for k, v in robust.items():
        print(f"    {k}: {v}")
    print(f"{'='*60}\n")

    return results


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description='Walk-Forward Optimization')
    parser.add_argument('--days',    type=int, default=90,  help='분석 기간(일)')
    parser.add_argument('--workers', type=int, default=4,   help='병렬 프로세스 수')
    args = parser.parse_args()
    run(days=args.days, n_workers=args.workers)
