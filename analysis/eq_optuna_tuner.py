"""
analysis/eq_optuna_tuner.py — EQ 모델 임계값 최적화 (Optuna)

목적: P(win) threshold를 0.30~0.60 범위에서 탐색하여
      기대수익(EV = WR × avg_win − LR × avg_loss)을 최대화하는 값을 찾는다.

흐름:
    entry_features (labeled) → Walk Forward 윈도우 분할
    → 각 윈도우: IS 학습 → OOS에서 threshold별 EV 계산
    → Optuna로 최적 threshold 결정
    → 결과 JSON 저장 + 콘솔 출력

사용법:
    python -m analysis.eq_optuna_tuner [--windows 5] [--trials 60] [--test-ratio 0.3]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"
LOG_DIR = Path(__file__).parent.parent / "logs"

# ── 피처 인코딩 (eq_model.py와 동일) ──────────────────────────────

_CHOCH_ENC  = {'A': 2, 'A+': 2, 'B': 1, 'C': 0}
_EQ_ENC     = {'A': 2, 'B': 1, 'C': 0}
_REGIME_ENC = {'BULL': 2, 'TREND': 2, 'SIDEWAYS': 1, 'NEUTRAL': 1,
               'BEAR': 0, 'REVERSAL': 0, 'UNKNOWN': 1}
_GUARD_ENC  = {'normal': 0, 'lsg': 1, 'conservative': 2}

FEATURES = [
    'entry_confidence', 'r_pct', 'htf_trend', 'sweep',
    'atr_pct', 'volume_ratio', 'rsi', 'squeeze_on', 'time_slot',
    'choch_grade_enc', 'eq_grade_enc', 'regime_enc', 'guard_enc',
    'vwap_dist', 'ema_slope', 'vol_zscore',
]


def _encode(row: dict) -> list:
    return [
        row.get('entry_confidence') or 0.5,
        row.get('r_pct') or 0.0,
        row.get('htf_trend') or 0,
        row.get('sweep') or 0,
        row.get('atr_pct') or 0.0,
        row.get('volume_ratio') or 1.0,
        row.get('rsi') or 50.0,
        row.get('squeeze_on') or 0,
        row.get('time_slot') or 60,
        _CHOCH_ENC.get(row.get('choch_grade') or '', 1),
        _EQ_ENC.get(row.get('eq_grade') or '', 1),
        _REGIME_ENC.get(row.get('regime') or '', 1),
        _GUARD_ENC.get(row.get('guard_state') or '', 0),
        row.get('vwap_dist') if row.get('vwap_dist') is not None else float('nan'),
        row.get('ema_slope')  if row.get('ema_slope')  is not None else float('nan'),
        row.get('vol_zscore') if row.get('vol_zscore') is not None else float('nan'),
    ]


# ── DB 로드 ────────────────────────────────────────────────────────

def _load_labeled(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM entry_features
        WHERE outcome_win IS NOT NULL
        ORDER BY timestamp
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 기대수익(EV) 계산 ──────────────────────────────────────────────

def _compute_ev(
    probas: np.ndarray,
    y_true: np.ndarray,
    pnl_pct: np.ndarray,
    threshold: float,
) -> dict:
    """
    threshold 이상 → 진입, 미만 → 필터링
    EV = (진입한 것들의 평균 PnL%)
    추가로 precision / coverage / win_rate 반환
    """
    mask = probas >= threshold
    n_entered = int(mask.sum())

    if n_entered == 0:
        return {
            'ev': 0.0,
            'precision': 0.0,
            'coverage': 0.0,
            'win_rate': 0.0,
            'n_entered': 0,
        }

    y_sel   = y_true[mask]
    pnl_sel = pnl_pct[mask]

    ev        = float(pnl_sel.mean())
    precision = float(y_sel.mean())
    coverage  = n_entered / len(y_true)

    wins   = pnl_sel[pnl_sel > 0]
    losses = pnl_sel[pnl_sel <= 0]
    avg_win  = float(wins.mean())  if len(wins)   > 0 else 0.0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0

    return {
        'ev':        round(ev, 4),
        'precision': round(precision, 3),
        'coverage':  round(coverage, 3),
        'win_rate':  round(precision, 3),
        'n_entered': n_entered,
        'avg_win':   round(avg_win, 4),
        'avg_loss':  round(avg_loss, 4),
    }


# ── Walk Forward 기반 Optuna 목적함수 ─────────────────────────────

def build_objective(
    data: list[dict],
    n_windows: int = 5,
    test_ratio: float = 0.3,
    min_train: int = 20,
):
    """
    Optuna trial.suggest_float('threshold', 0.30, 0.60) 을 받아
    WFO 전 윈도우 평균 EV 반환 (최대화).

    Returns: callable objective
    """
    import lightgbm as lgb

    n = len(data)
    window_size = n // n_windows

    # 윈도우별로 모델과 OOS 데이터를 미리 학습해 캐시 (Optuna 반복 효율화)
    window_cache: list[Optional[dict]] = []

    for i in range(n_windows):
        start  = i * window_size
        end    = start + window_size if i < n_windows - 1 else n
        window = data[start:end]

        split      = int(len(window) * (1 - test_ratio))
        train_data = window[:split]
        test_data  = window[split:]

        if len(train_data) < min_train or len(test_data) < 5:
            window_cache.append(None)
            continue

        X_train = np.array([_encode(r) for r in train_data], dtype=np.float32)
        y_train = np.array([r['outcome_win'] for r in train_data], dtype=np.int32)
        X_test  = np.array([_encode(r) for r in test_data], dtype=np.float32)
        y_test  = np.array([r['outcome_win'] for r in test_data], dtype=np.int32)

        # outcome_pnl_pct가 없으면 win=+1%, loss=-1% 가정
        pnl_test = np.array([
            r.get('outcome_pnl_pct') or (1.0 if r['outcome_win'] == 1 else -1.0)
            for r in test_data
        ], dtype=np.float32)

        wr    = y_train.mean()
        scale = (1 - wr) / wr if wr > 0 else 1.0

        model = lgb.LGBMClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.05,
            num_leaves=15, min_child_samples=5,
            scale_pos_weight=scale, random_state=42, verbose=-1,
        )
        model.fit(X_train, y_train)
        probas = model.predict_proba(X_test)[:, 1]

        window_cache.append({
            'probas':  probas,
            'y_test':  y_test,
            'pnl_test': pnl_test,
        })

    valid_windows = [w for w in window_cache if w is not None]

    def objective(trial):
        threshold = trial.suggest_float('threshold', 0.30, 0.60)
        evs = []
        for w in valid_windows:
            r = _compute_ev(w['probas'], w['y_test'], w['pnl_test'], threshold)
            if r['n_entered'] > 0:
                evs.append(r['ev'])
        return float(np.mean(evs)) if evs else -999.0

    return objective, valid_windows


# ── 임계값 스캔 (Optuna 없이 단순 스캔) ──────────────────────────

def scan_thresholds(
    valid_windows: list[dict],
    thresholds: Optional[list[float]] = None,
) -> list[dict]:
    """0.25~0.65 범위를 0.05 간격으로 브루트포스 스캔"""
    if thresholds is None:
        thresholds = [round(t, 2) for t in np.arange(0.25, 0.66, 0.05)]

    rows = []
    for thr in thresholds:
        evs        = []
        coverages  = []
        win_rates  = []
        n_entered  = []

        for w in valid_windows:
            r = _compute_ev(w['probas'], w['y_test'], w['pnl_test'], thr)
            if r['n_entered'] > 0:
                evs.append(r['ev'])
                coverages.append(r['coverage'])
                win_rates.append(r['win_rate'])
                n_entered.append(r['n_entered'])

        if evs:
            rows.append({
                'threshold': thr,
                'mean_ev':   round(float(np.mean(evs)), 4),
                'mean_wr':   round(float(np.mean(win_rates)), 3),
                'coverage':  round(float(np.mean(coverages)), 3),
                'n_windows': len(evs),
            })
        else:
            rows.append({
                'threshold': thr,
                'mean_ev':   0.0,
                'mean_wr':   0.0,
                'coverage':  0.0,
                'n_windows': 0,
            })

    return rows


# ── Optuna 최적화 실행 ──────────────────────────────────────────────

def run_optuna(
    data: list[dict],
    n_windows: int = 5,
    test_ratio: float = 0.3,
    min_train: int = 20,
    n_trials: int = 60,
) -> dict:
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        logger.warning("[EQ_OPTUNA] optuna 없음 — 브루트포스 스캔으로 대체")
        return _run_bruteforce(data, n_windows, test_ratio, min_train)

    objective_fn, valid_windows = build_objective(
        data, n_windows, test_ratio, min_train
    )

    if not valid_windows:
        return {'error': '유효 윈도우 없음'}

    study = optuna.create_study(direction='maximize')
    study.optimize(objective_fn, n_trials=n_trials, show_progress_bar=False)

    best_thr = study.best_params['threshold']
    best_ev  = study.best_value

    # 모든 trial 결과 정리
    trial_rows = []
    for t in study.trials:
        trial_rows.append({
            'threshold': round(t.params['threshold'], 4),
            'ev':        round(t.value, 4) if t.value is not None else None,
        })
    trial_rows.sort(key=lambda x: x['threshold'])

    # 그리드 스캔도 함께 반환 (가독성)
    grid = scan_thresholds(valid_windows)

    return {
        'method':     'optuna',
        'n_trials':   n_trials,
        'n_windows':  len(valid_windows),
        'best_threshold': round(best_thr, 4),
        'best_ev':    round(best_ev, 4),
        'trials':     trial_rows,
        'grid_scan':  grid,
    }


def _run_bruteforce(
    data: list[dict],
    n_windows: int = 5,
    test_ratio: float = 0.3,
    min_train: int = 20,
) -> dict:
    _, valid_windows = build_objective(data, n_windows, test_ratio, min_train)

    if not valid_windows:
        return {'error': '유효 윈도우 없음'}

    grid = scan_thresholds(valid_windows)
    best = max(grid, key=lambda r: r['mean_ev'])

    return {
        'method':         'bruteforce',
        'n_windows':      len(valid_windows),
        'best_threshold': best['threshold'],
        'best_ev':        best['mean_ev'],
        'grid_scan':      grid,
    }


# ── 리포트 출력 ────────────────────────────────────────────────────

def print_report(result: dict, data: list[dict]):
    if result.get('error'):
        print(f"\n⚠️  {result['error']}")
        return

    print("\n" + "=" * 60)
    print("  EQ 모델 임계값 최적화 결과")
    print("=" * 60)
    print(f"  전체 레이블 데이터: {len(data)}건")
    print(f"  방법: {result['method']}  | 유효 윈도우: {result['n_windows']}")
    print(f"\n  최적 임계값: {result['best_threshold']:.2f}  "
          f"(평균 EV={result['best_ev']:+.4f}%)\n")

    grid = result.get('grid_scan', [])
    if grid:
        print(f"  {'Threshold':>10} {'Mean EV':>10} {'Win Rate':>10} {'Coverage':>10}")
        print("  " + "-" * 44)
        for row in grid:
            marker = " ◀" if abs(row['threshold'] - result['best_threshold']) < 0.01 else ""
            print(
                f"  {row['threshold']:>10.2f} "
                f"{row['mean_ev']:>+10.4f} "
                f"{row['mean_wr']:>10.1%} "
                f"{row['coverage']:>10.1%}"
                f"{marker}"
            )

    best = result['best_threshold']
    current = 0.40
    print(f"\n  현재 설정:  threshold={current:.2f}")
    if abs(best - current) < 0.01:
        print("  → 현재 설정이 최적입니다.")
    elif best > current:
        print(f"  → 임계값 상향 권고: {current:.2f} → {best:.2f}  (더 까다롭게 진입)")
    else:
        print(f"  → 임계값 하향 권고: {current:.2f} → {best:.2f}  (더 많은 진입 허용)")

    print("\n  [적용 방법]")
    print("  config/strategy_hybrid.yaml:")
    print(f"    eq_ml_filter:")
    print(f"      threshold: {best:.2f}")
    print("=" * 60 + "\n")


# ── 저장 ──────────────────────────────────────────────────────────

def save_result(result: dict):
    LOG_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path  = LOG_DIR / f"eq_optuna_{today}.json"
    out   = {'generated_at': datetime.now().isoformat(), **result}
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"  → 결과 저장: {path}")


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    parser = argparse.ArgumentParser(description='EQ 모델 임계값 최적화')
    parser.add_argument('--windows',    type=int,   default=5,    help='WFO 윈도우 수')
    parser.add_argument('--trials',     type=int,   default=60,   help='Optuna 시도 횟수')
    parser.add_argument('--test-ratio', type=float, default=0.3,  help='OOS 비율')
    parser.add_argument('--min-samples',type=int,   default=20,   help='최소 학습 건수')
    parser.add_argument('--no-save',    action='store_true')
    args = parser.parse_args()

    data = _load_labeled(DB_PATH)
    if len(data) < 10:
        print(f"⚠️  레이블 데이터 부족: {len(data)}건 (최소 10건 필요)")
        raise SystemExit(0)

    result = run_optuna(
        data,
        n_windows=args.windows,
        test_ratio=args.test_ratio,
        min_train=args.min_samples,
        n_trials=args.trials,
    )

    print_report(result, data)

    if not args.no_save:
        save_result(result)
