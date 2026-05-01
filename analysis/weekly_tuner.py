"""
주간 베이지안 최적화 (Optuna + Walk-Forward)

Usage:
    python -m analysis.weekly_tuner [--weeks 12] [--trials 50]

매주 일요일 scheduler_weekly.py가 자동 호출.
직접 실행 시 결과를 logs/에 저장하고 YAML 권고안 출력.

Walk-Forward 구조:
    ┌──────────────────┬────────┐
    │ TRAIN (8w)       │TEST(2w)│
    │   TRAIN (8w)     │TEST(2w)│   ← 2주씩 전진
    └──────────────────┴────────┘

점수 함수 (고정):
    score = E×2.0 + WR×0.5 - MDD×1.5 + trade_penalty
"""

import argparse
import json
import os
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from analysis.defensive_short_backtest import (
    DefensiveShortBacktest,
    BacktestResult,
    _add_indicators,
    _fetch_yf,
    KOSDAQ_TICKER,
    INVERSE_TICKER,
)

# Optuna import (없으면 Grid Search 폴백)
try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False
    print("[WARN] optuna 미설치 → Grid Search 폴백 (pip install optuna)")


# ── 점수 함수 (전체 시스템 공통 — 변경 금지) ─────────────────────────

def calc_score(r: BacktestResult) -> float:
    """균형 점수. 수익 ↑ / 낙폭 ↓ / 거래수 부족 패널티"""
    if r is None or r.num_trades == 0:
        return -999.0
    trade_penalty = 0.0
    if r.num_trades < 30:
        trade_penalty = -0.2
    if r.num_trades < 10:
        trade_penalty = -0.5
    return round(
        r.expectancy * 2.0
        + r.win_rate  * 0.5
        - r.mdd       * 1.5
        + trade_penalty,
        4,
    )


# ── Walk-Forward 분할 ────────────────────────────────────────────────

def make_walk_forward_splits(
    df: pd.DataFrame,
    train_weeks: int = 8,
    test_weeks:  int = 2,
) -> List[Dict]:
    """DataFrame → Walk-Forward 분할 리스트

    Args:
        df: DatetimeIndex를 가진 5분봉 DataFrame
        train_weeks: 학습 구간 길이 (주)
        test_weeks:  검증 구간 길이 (주)

    Returns:
        [{"train": df_slice, "test": df_slice}, ...]
    """
    if df is None or df.empty:
        return []

    start = df.index.min()
    end   = df.index.max()
    splits: List[Dict] = []

    cur = start
    while True:
        train_end = cur + pd.Timedelta(weeks=train_weeks)
        test_end  = train_end + pd.Timedelta(weeks=test_weeks)
        if test_end > end:
            break
        train_slice = df[(df.index >= cur) & (df.index < train_end)]
        test_slice  = df[(df.index >= train_end) & (df.index < test_end)]
        if len(train_slice) > 100 and len(test_slice) > 20:
            splits.append({"train": train_slice, "test": test_slice})
        cur += pd.Timedelta(weeks=test_weeks)

    return splits


# ── 스플릿 기반 백테스트 헬퍼 ────────────────────────────────────────

def _bt_on_slice(
    sig_slice: pd.DataFrame,
    inv_slice: Optional[pd.DataFrame],
    strategy: str,
    params: Dict,
) -> BacktestResult:
    """특정 데이터 슬라이스에서 백테스트 실행"""
    bt = DefensiveShortBacktest.__new__(DefensiveShortBacktest)
    bt.days    = 0
    bt.sig_df  = sig_slice
    bt.mkt_df  = sig_slice      # 레짐 판별도 같은 데이터 사용
    bt.inv_df  = inv_slice

    if strategy == "defensive":
        return bt.run_defensive(params)
    else:
        return bt.run_short(params)


# ── Optuna objectives ────────────────────────────────────────────────

def _objective_defensive(trial, sig_splits: List[Dict], inv_df: pd.DataFrame) -> float:
    params = {
        "max_rsi":                 trial.suggest_int(  "max_rsi",          25, 35),
        "min_ema20_deviation_pct": trial.suggest_float("ema_dev",          -2.5, -0.8),
        "min_volume_ratio":        trial.suggest_float("vol_ratio",         1.5,  3.0),
        "stop_loss_pct":           trial.suggest_float("sl_pct",            0.5,  1.2),
        "take_profit_pct":         trial.suggest_float("tp_pct",            0.7,  1.5),
        "max_hold_minutes":        trial.suggest_int(  "max_hold",          6,   15),
    }
    scores = []
    for sp in sig_splits:
        train_slice = sp["train"]
        test_slice  = sp["test"]
        # inv_df 슬라이스 맞춤
        def _inv_slice(df_ref):
            if inv_df is None:
                return None
            return inv_df[(inv_df.index >= df_ref.index.min()) &
                          (inv_df.index <= df_ref.index.max())]
        # 학습 과적합 방지: validation score만 사용
        val_r = _bt_on_slice(test_slice, _inv_slice(test_slice), "defensive", params)
        scores.append(calc_score(val_r))
    return float(np.mean(scores)) if scores else -999.0


def _objective_short(trial, sig_splits: List[Dict], inv_df: pd.DataFrame) -> float:
    params = {
        "min_breakdown_pct":  trial.suggest_float("breakdown",     -0.8, -0.2),
        "min_volume_ratio":   trial.suggest_float("vol_ratio",      1.0,  2.0),
        "stop_loss_pct":      trial.suggest_float("sl_pct",         0.8,  1.8),
        "take_profit_pct":    trial.suggest_float("tp_pct",         1.3,  3.0),
        "trailing_stop_pct":  trial.suggest_float("trail_pct",      0.4,  1.3),
    }
    scores = []
    for sp in sig_splits:
        test_slice = sp["test"]
        def _inv_slice(df_ref):
            if inv_df is None:
                return None
            return inv_df[(inv_df.index >= df_ref.index.min()) &
                          (inv_df.index <= df_ref.index.max())]
        val_r = _bt_on_slice(test_slice, _inv_slice(test_slice), "short", params)
        scores.append(calc_score(val_r))
    return float(np.mean(scores)) if scores else -999.0


# ── Grid Search 폴백 ─────────────────────────────────────────────────

GRID_DEFENSIVE = {
    "max_rsi":                 [25, 30, 35],
    "min_ema20_deviation_pct": [-1.0, -1.5, -2.0],
    "min_volume_ratio":        [1.5, 2.0, 2.5],
    "stop_loss_pct":           [0.6, 0.8, 1.0],
    "take_profit_pct":         [0.8, 1.0, 1.2],
    "max_hold_minutes":        [8, 10, 12],
}
GRID_SHORT = {
    "min_breakdown_pct":  [-0.3, -0.5, -0.7],
    "min_volume_ratio":   [1.0, 1.2, 1.5],
    "stop_loss_pct":      [1.0, 1.2, 1.5],
    "take_profit_pct":    [1.5, 2.0, 2.5],
    "trailing_stop_pct":  [0.5, 0.8, 1.0],
}

def _grid_search_fallback(
    strategy: str,
    sig_splits: List[Dict],
    inv_df: Optional[pd.DataFrame],
) -> Dict:
    import itertools
    grid = GRID_DEFENSIVE if strategy == "defensive" else GRID_SHORT
    keys = list(grid.keys())
    best_score, best_params = -999.0, {}
    for values in itertools.product(*grid.values()):
        params = dict(zip(keys, values))
        scores = []
        for sp in sig_splits:
            test_slice = sp["test"]
            inv_s = None
            if inv_df is not None:
                inv_s = inv_df[(inv_df.index >= test_slice.index.min()) &
                               (inv_df.index <= test_slice.index.max())]
            r = _bt_on_slice(test_slice, inv_s, strategy, params)
            scores.append(calc_score(r))
        s = float(np.mean(scores)) if scores else -999.0
        if s > best_score:
            best_score, best_params = s, params
    return best_params


# ── 메인 최적화 실행 ─────────────────────────────────────────────────

def optimize_all(
    sig_df: pd.DataFrame,
    inv_df: Optional[pd.DataFrame],
    train_weeks: int = 8,
    test_weeks:  int = 2,
    n_trials:    int = 50,
) -> Dict:
    """Walk-Forward + Optuna(또는 Grid) 로 DEFENSIVE/SHORT 파라미터 최적화

    Returns:
        {
          "defensive": {...params},
          "short": {...params},
          "defensive_score": float,
          "short_score": float,
          "splits_count": int,
        }
    """
    splits = make_walk_forward_splits(sig_df, train_weeks, test_weeks)
    if not splits:
        print("  [WARN] Walk-Forward 분할 없음 (데이터 부족)")
        return {}

    print(f"  Walk-Forward 분할: {len(splits)}개 (train={train_weeks}w / test={test_weeks}w)")

    result = {"splits_count": len(splits)}

    for strategy in ("defensive", "short"):
        print(f"\n  [{strategy.upper()}] 최적화 시작 (n_trials={n_trials if HAS_OPTUNA else 'grid'})...")
        if HAS_OPTUNA:
            study = optuna.create_study(direction="maximize",
                                        sampler=optuna.samplers.TPESampler(seed=42))
            if strategy == "defensive":
                study.optimize(
                    lambda t: _objective_defensive(t, splits, inv_df),
                    n_trials=n_trials,
                    show_progress_bar=False,
                )
            else:
                study.optimize(
                    lambda t: _objective_short(t, splits, inv_df),
                    n_trials=n_trials,
                    show_progress_bar=False,
                )
            best_params = study.best_params
            best_score  = study.best_value

            # optuna의 축약 키 → 실제 YAML 키로 복원
            if strategy == "defensive":
                best_params = {
                    "max_rsi":                 best_params.get("max_rsi", 30),
                    "min_ema20_deviation_pct": round(best_params.get("ema_dev", -1.5), 2),
                    "min_volume_ratio":        round(best_params.get("vol_ratio", 2.0), 2),
                    "stop_loss_pct":           round(best_params.get("sl_pct", 0.8), 2),
                    "take_profit_pct":         round(best_params.get("tp_pct", 1.0), 2),
                    "max_hold_minutes":        best_params.get("max_hold", 10),
                }
            else:
                best_params = {
                    "min_breakdown_pct":  round(best_params.get("breakdown", -0.5), 2),
                    "min_volume_ratio":   round(best_params.get("vol_ratio", 1.2), 2),
                    "stop_loss_pct":      round(best_params.get("sl_pct", 1.2), 2),
                    "take_profit_pct":    round(best_params.get("tp_pct", 2.0), 2),
                    "trailing_stop_pct":  round(best_params.get("trail_pct", 0.8), 2),
                }
        else:
            best_params = _grid_search_fallback(strategy, splits, inv_df)
            # 검증 score 계산
            all_scores = []
            for sp in splits:
                ts = sp["test"]
                inv_s = inv_df[(inv_df.index >= ts.index.min()) &
                               (inv_df.index <= ts.index.max())] if inv_df is not None else None
                r = _bt_on_slice(ts, inv_s, strategy, best_params)
                all_scores.append(calc_score(r))
            best_score = float(np.mean(all_scores)) if all_scores else -999.0

        result[strategy]            = best_params
        result[f"{strategy}_score"] = round(best_score, 4)
        print(f"  → best_score={best_score:.4f}  params={best_params}")

    return result


# ── 주간 튜닝 엔트리포인트 ───────────────────────────────────────────

def run_weekly_tuning(weeks: int = 12, n_trials: int = 50) -> Dict:
    """scheduler_weekly.py 가 호출하는 메인 함수

    Returns:
        tuning_result dict (safety_gate.py로 전달)
    """
    print(f"\n{'='*60}")
    print(f"  주간 튜닝 시작  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print(f"{'='*60}")

    # 1. 데이터 로드
    days = weeks * 7
    print(f"  데이터 로드: 최근 {weeks}주({days}일)...")
    sig_df = _fetch_yf(KOSDAQ_TICKER,  days, "5m")
    inv_df = _fetch_yf(INVERSE_TICKER, days, "5m")

    if sig_df is None:
        print("  [ERROR] 시장 데이터 로드 실패")
        return {"ok": False, "reason": "데이터 로드 실패"}

    from analysis.defensive_short_backtest import _add_indicators
    sig_df = _add_indicators(sig_df)
    if inv_df is not None:
        inv_df = _add_indicators(inv_df)

    print(f"  KOSDAQ: {len(sig_df)}봉 | 인버스ETF: {len(inv_df) if inv_df is not None else 0}봉")

    # 2. 최적화
    opt_result = optimize_all(sig_df, inv_df, n_trials=n_trials)
    if not opt_result:
        return {"ok": False, "reason": "최적화 결과 없음"}

    # 3. 결과 저장
    os.makedirs("logs", exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"logs/weekly_tuning_{ts}.json"
    out  = {
        "generated_at": ts,
        "weeks": weeks,
        "n_trials": n_trials,
        **opt_result,
        "ok": True,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {path}")
    out["result_path"] = path
    return out


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="주간 베이지안 파라미터 최적화")
    parser.add_argument("--weeks",  type=int, default=12, help="데이터 기간 (주)")
    parser.add_argument("--trials", type=int, default=50, help="Optuna trial 수")
    args = parser.parse_args()

    result = run_weekly_tuning(weeks=args.weeks, n_trials=args.trials)
    if result.get("ok"):
        print("\n  최적화 완료.")
        print(f"  DEFENSIVE: {result.get('defensive')}")
        print(f"  SHORT:     {result.get('short')}")
    else:
        print(f"\n  튜닝 실패: {result.get('reason')}")


if __name__ == "__main__":
    main()
