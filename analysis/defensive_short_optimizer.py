"""
DEFENSIVE + SHORT 파라미터 자동 튜닝 (Grid Search)

Usage:
    python -m analysis.defensive_short_optimizer [--days 60] [--strategy defensive|short|both]

점수 함수:
    score = expectancy×2.0 + win_rate×0.5 - mdd×1.5 + trade_count_bonus

과최적화 방지:
    - 학습 70% / 검증 30% 기간 분리
    - 최소 거래 수 10회 (그 이하는 penalty)
    - 결과: YAML 업데이트 권고 파라미터 출력
"""

import argparse
import itertools
import json
import os
import warnings
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np

warnings.filterwarnings("ignore")

from analysis.defensive_short_backtest import (
    DefensiveShortBacktest,
    BacktestResult,
    _calc_metrics,
)


# ── 점수 함수 ────────────────────────────────────────────────────────

def calc_score(r: BacktestResult) -> float:
    """균형 점수 (수익 ↑ / 낙폭 ↓ / 거래수 부족 패널티)"""
    if r.num_trades == 0:
        return -999.0

    # 거래 수 패널티 (10회 미만)
    trade_penalty = 0.0
    if r.num_trades < 10:
        trade_penalty = -0.5
    elif r.num_trades < 5:
        trade_penalty = -2.0

    score = (
        r.expectancy  * 2.0
        + r.win_rate  * 0.5
        - r.mdd       * 1.5
        + trade_penalty
    )
    return round(score, 4)


# ── 파라미터 그리드 ───────────────────────────────────────────────────

PARAM_GRID_DEFENSIVE = {
    "max_rsi":                  [25, 30, 35],
    "min_ema20_deviation_pct":  [-1.0, -1.5, -2.0],
    "min_volume_ratio":         [1.5, 2.0, 2.5],
    "stop_loss_pct":            [0.6, 0.8, 1.0],
    "take_profit_pct":          [0.8, 1.0, 1.2],
    "max_hold_minutes":         [8, 10, 12],
}

PARAM_GRID_SHORT = {
    "min_breakdown_pct":  [-0.3, -0.5, -0.7],
    "min_volume_ratio":   [1.0, 1.2, 1.5],
    "stop_loss_pct":      [1.0, 1.2, 1.5],
    "take_profit_pct":    [1.5, 2.0, 2.5],
    "trailing_stop_pct":  [0.5, 0.8, 1.0],
}


# ── Grid Search ───────────────────────────────────────────────────────

def grid_search(
    bt: DefensiveShortBacktest,
    strategy: str,
    param_grid: Dict,
    use_train_only: bool = True,
) -> List[Tuple[Dict, float, BacktestResult]]:
    """Grid Search 실행

    Args:
        bt: 이미 데이터 로드된 백테스트 인스턴스
        strategy: "defensive" | "short"
        param_grid: 탐색 범위
        use_train_only: True → 전체 데이터의 70%만 학습에 사용

    Returns:
        [(params, score, result)] 점수 내림차순 정렬
    """
    keys   = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))
    total  = len(combos)
    print(f"\n[OPT] {strategy.upper()} 그리드 탐색: {total}개 조합")

    # 학습 구간 제한 (70%)
    if use_train_only and bt.sig_df is not None:
        n_train = int(len(bt.sig_df) * 0.7)
        orig_sig = bt.sig_df
        orig_mkt = bt.mkt_df
        orig_inv = bt.inv_df
        bt.sig_df = bt.sig_df.iloc[:n_train].copy()
        bt.mkt_df = bt.mkt_df.iloc[:n_train].copy()
        if bt.inv_df is not None:
            # inv_df는 시간 기반 슬라이스
            cutoff = bt.sig_df.index[-1]
            bt.inv_df = orig_inv[orig_inv.index <= cutoff].copy()

    results: List[Tuple[Dict, float, BacktestResult]] = []

    for idx, values in enumerate(combos, 1):
        params = dict(zip(keys, values))
        try:
            if strategy == "defensive":
                r = bt.run_defensive(params)
            else:
                r = bt.run_short(params)
            s = calc_score(r)
            results.append((params, s, r))
        except Exception as e:
            results.append((params, -999.0, None))

        if idx % 50 == 0 or idx == total:
            print(f"  [{idx}/{total}] 진행 중...")

    # 복원
    if use_train_only and bt.sig_df is not None:
        bt.sig_df = orig_sig
        bt.mkt_df = orig_mkt
        bt.inv_df = orig_inv

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def validate_best(
    bt: DefensiveShortBacktest,
    best_params: Dict,
    strategy: str,
) -> BacktestResult:
    """검증 구간 (나머지 30%)에서 best_params 성능 확인"""
    if bt.sig_df is None:
        return _calc_metrics([], strategy.upper(), best_params)

    n_train = int(len(bt.sig_df) * 0.7)
    orig_sig = bt.sig_df
    orig_mkt = bt.mkt_df
    orig_inv = bt.inv_df

    cutoff = bt.sig_df.index[n_train]
    bt.sig_df = orig_sig[orig_sig.index >= cutoff].copy()
    bt.mkt_df = orig_mkt[orig_mkt.index >= cutoff].copy()
    if bt.inv_df is not None:
        bt.inv_df = orig_inv[orig_inv.index >= cutoff].copy()

    if strategy == "defensive":
        r = bt.run_defensive(best_params)
    else:
        r = bt.run_short(best_params)

    bt.sig_df = orig_sig
    bt.mkt_df = orig_mkt
    bt.inv_df = orig_inv
    return r


# ── 결과 출력 ────────────────────────────────────────────────────────

def print_top_results(results: List, strategy: str, top_n: int = 5):
    print(f"\n  ── {strategy.upper()} TOP {top_n} 파라미터 ──")
    for rank, (params, score, r) in enumerate(results[:top_n], 1):
        if r is None:
            print(f"  [{rank}] score={score:.3f}  (실패)")
            continue
        print(
            f"  [{rank}] score={score:.4f} | "
            f"E={r.expectancy:+.3f}% WR={r.win_rate*100:.0f}% "
            f"PF={r.profit_factor:.2f} MDD={r.mdd:.1f}% n={r.num_trades}"
        )
        print(f"        params: {params}")


def generate_yaml_recommendation(
    def_best: Dict,
    short_best: Dict,
    def_val: BacktestResult,
    short_val: BacktestResult,
) -> str:
    """YAML 업데이트 권고안 생성"""
    lines = [
        "# ── 자동 튜닝 결과 (YAML 업데이트 권고) ──────────────────────",
        f"# 생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "#",
        "# [주의] 검증 성능이 학습 성능 대비 크게 낮으면 과최적화 의심",
        "",
        "defensive_mode:",
    ]
    for k, v in def_best.items():
        lines.append(f"  {k}: {v}")
    lines += [
        f"  # 검증 성능: E={def_val.expectancy:+.3f}% WR={def_val.win_rate*100:.0f}% n={def_val.num_trades}",
        "",
        "short_mode:",
    ]
    for k, v in short_best.items():
        lines.append(f"  {k}: {v}")
    lines += [
        f"  # 검증 성능: E={short_val.expectancy:+.3f}% WR={short_val.win_rate*100:.0f}% n={short_val.num_trades}",
    ]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DEFENSIVE + SHORT 파라미터 최적화")
    parser.add_argument("--days",     type=int, default=60,   help="백테스트 기간 (일)")
    parser.add_argument("--strategy", type=str, default="both",
                        choices=["defensive", "short", "both"])
    parser.add_argument("--top",      type=int, default=5,    help="상위 N개 출력")
    args = parser.parse_args()

    bt = DefensiveShortBacktest(days=args.days)
    if not bt.load_data():
        return

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  파라미터 최적화 (기간={args.days}일, 학습:검증 = 70:30)")
    print(sep)

    best_def_params   = None
    best_short_params = None
    def_val_r = short_val_r = None

    if args.strategy in ("defensive", "both"):
        def_results = grid_search(bt, "defensive", PARAM_GRID_DEFENSIVE)
        print_top_results(def_results, "defensive", args.top)
        best_def_params = def_results[0][0] if def_results else {}
        print(f"\n  검증 구간 성능 (best params):")
        def_val_r = validate_best(bt, best_def_params, "defensive")
        if def_val_r:
            print(
                f"    DEFENSIVE 검증: E={def_val_r.expectancy:+.3f}% "
                f"WR={def_val_r.win_rate*100:.0f}% n={def_val_r.num_trades}"
            )
            overfitting_warn = ""
            if def_results[0][2] and def_val_r.expectancy < def_results[0][2].expectancy * 0.5:
                overfitting_warn = "  ⚠️ 과최적화 의심 — 검증 성능이 학습의 50% 미만"
            if overfitting_warn:
                print(f"    {overfitting_warn}")

    if args.strategy in ("short", "both"):
        short_results = grid_search(bt, "short", PARAM_GRID_SHORT)
        print_top_results(short_results, "short", args.top)
        best_short_params = short_results[0][0] if short_results else {}
        print(f"\n  검증 구간 성능 (best params):")
        short_val_r = validate_best(bt, best_short_params, "short")
        if short_val_r:
            print(
                f"    SHORT 검증: E={short_val_r.expectancy:+.3f}% "
                f"WR={short_val_r.win_rate*100:.0f}% n={short_val_r.num_trades}"
            )
            if short_results[0][2] and short_val_r.expectancy < short_results[0][2].expectancy * 0.5:
                print("    ⚠️ 과최적화 의심 — 검증 성능이 학습의 50% 미만")

    # YAML 권고안 출력 & 저장
    if best_def_params and best_short_params and def_val_r and short_val_r:
        yaml_rec = generate_yaml_recommendation(
            best_def_params, best_short_params, def_val_r, short_val_r
        )
        print(f"\n{sep}")
        print("  YAML 업데이트 권고안:")
        print(sep)
        print(yaml_rec)

        os.makedirs("logs", exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"logs/optimizer_recommendation_{ts}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml_rec + "\n")
        print(f"\n  권고안 저장: {path}")

    # JSON 결과 저장
    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days": args.days,
        "best_defensive": best_def_params,
        "best_short": best_short_params,
        "defensive_validation": {
            "expectancy":    round(def_val_r.expectancy, 4) if def_val_r else None,
            "win_rate":      round(def_val_r.win_rate, 4)   if def_val_r else None,
            "num_trades":    def_val_r.num_trades            if def_val_r else None,
        },
        "short_validation": {
            "expectancy":    round(short_val_r.expectancy, 4) if short_val_r else None,
            "win_rate":      round(short_val_r.win_rate, 4)   if short_val_r else None,
            "num_trades":    short_val_r.num_trades            if short_val_r else None,
        },
    }
    json_path = f"logs/optimizer_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  JSON 저장: {json_path}")
    print(f"\n{sep}\n")


if __name__ == "__main__":
    main()
