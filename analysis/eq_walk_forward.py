"""
analysis/eq_walk_forward.py — EQ 모델 Walk Forward 검증

목적: LightGBM EQ 모델이 시간축에서도 안정적인지 검증 (과최적화 방지)

흐름:
    entry_features (labeled) → N개 시간 윈도우 분할
    → 각 윈도우: IS 학습 → OOS 예측 → AUC/승률 측정
    → 윈도우별 안정성 리포트

사용법:
    python -m analysis.eq_walk_forward [--windows 5] [--test-ratio 0.3] [--min-samples 20]
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


# ── 피처 인코딩 (eq_model.py와 동일) ─────────────────────────────

_CHOCH_ENC  = {'A': 2, 'A+': 2, 'B': 1, 'C': 0}
_EQ_ENC     = {'A': 2, 'B': 1, 'C': 0}
_REGIME_ENC = {'BULL': 2, 'TREND': 2, 'SIDEWAYS': 1, 'NEUTRAL': 1,
               'BEAR': 0, 'REVERSAL': 0, 'UNKNOWN': 1}
_GUARD_ENC  = {'normal': 0, 'lsg': 1, 'conservative': 2}

FEATURES = [
    'entry_confidence', 'r_pct', 'htf_trend', 'sweep',
    'atr_pct', 'volume_ratio', 'rsi', 'squeeze_on', 'time_slot',
    'choch_grade_enc', 'eq_grade_enc', 'regime_enc', 'guard_enc',
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
    ]


def _roc_auc(y_true: list, y_score: list) -> float:
    try:
        from sklearn.metrics import roc_auc_score
        return float(roc_auc_score(y_true, y_score))
    except Exception:
        # 직접 계산 (sklearn 없을 때)
        pairs = [(s, t) for s, t in zip(y_score, y_true)]
        pos = [s for s, t in pairs if t == 1]
        neg = [s for s, t in pairs if t == 0]
        if not pos or not neg:
            return 0.5
        n_correct = sum(1 for p in pos for n in neg if p > n)
        return n_correct / (len(pos) * len(neg))


# ── Walk Forward 실행 ─────────────────────────────────────────────

def run_walk_forward(
    data: list[dict],
    n_windows: int = 5,
    test_ratio: float = 0.3,
    min_train: int = 20,
) -> list[dict]:
    """
    시간순 정렬된 data를 n_windows로 순차 분할.
    각 윈도우: 앞 (1-test_ratio) IS 학습, 뒤 test_ratio OOS 테스트.

    Returns: 윈도우별 결과 dict 리스트
    """
    import lightgbm as lgb

    results = []
    n = len(data)
    window_size = n // n_windows

    for i in range(n_windows):
        start = i * window_size
        end   = start + window_size if i < n_windows - 1 else n
        window = data[start:end]

        split = int(len(window) * (1 - test_ratio))
        train_data = window[:split]
        test_data  = window[split:]

        if len(train_data) < min_train or len(test_data) < 5:
            results.append({
                'window': i + 1,
                'skipped': True,
                'reason': f'부족: train={len(train_data)} test={len(test_data)}',
            })
            continue

        X_train = np.array([_encode(r) for r in train_data], dtype=np.float32)
        y_train = np.array([r['outcome_win'] for r in train_data], dtype=np.int32)
        X_test  = np.array([_encode(r) for r in test_data], dtype=np.float32)
        y_test  = np.array([r['outcome_win'] for r in test_data], dtype=np.int32)

        wr = y_train.mean()
        scale = (1 - wr) / wr if wr > 0 else 1.0

        model = lgb.LGBMClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.05,
            num_leaves=15, min_child_samples=5,
            scale_pos_weight=scale, random_state=42, verbose=-1,
        )
        model.fit(X_train, y_train)

        proba = model.predict_proba(X_test)[:, 1]
        auc   = _roc_auc(y_test.tolist(), proba.tolist())

        # threshold 0.40 기준 precision/recall
        preds = (proba >= 0.40).astype(int)
        tp = int(((preds == 1) & (y_test == 1)).sum())
        fp = int(((preds == 1) & (y_test == 0)).sum())
        fn = int(((preds == 0) & (y_test == 1)).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0

        # IS win rate vs OOS win rate (드리프트 체크)
        is_wr  = float(y_train.mean())
        oos_wr = float(y_test.mean())

        results.append({
            'window':     i + 1,
            'skipped':    False,
            'is_size':    len(train_data),
            'oos_size':   len(test_data),
            'is_wr':      round(is_wr * 100, 1),
            'oos_wr':     round(oos_wr * 100, 1),
            'auc':        round(auc, 3),
            'precision':  round(precision, 3),
            'recall':     round(recall, 3),
            'period': {
                'from': train_data[0]['timestamp'][:10],
                'to':   test_data[-1]['timestamp'][:10],
            },
        })

    return results


# ── 리포트 출력 ───────────────────────────────────────────────────

def print_report(results: list[dict], data: list[dict]):
    valid = [r for r in results if not r.get('skipped')]

    print("\n" + "=" * 60)
    print("  EQ 모델 Walk Forward 검증 결과")
    print("=" * 60)
    print(f"  전체 레이블 데이터: {len(data)}건")
    print(f"  실행 윈도우: {len(valid)}/{len(results)}개\n")

    if not valid:
        print("  ⚠️  검증 데이터 부족 (거래 50건 이상 필요)")
        return

    header = f"{'윈도우':^6} {'기간':^22} {'IS':>5} {'OOS':>5} {'IS WR':>7} {'OOS WR':>7} {'AUC':>6} {'Prec':>6}"
    print(header)
    print("-" * len(header))

    for r in valid:
        period = f"{r['period']['from']} ~ {r['period']['to']}"
        wr_flag = '⚠️' if abs(r['oos_wr'] - r['is_wr']) > 15 else '  '
        auc_flag = '⚠️' if r['auc'] < 0.55 else '✅'
        print(
            f"  {r['window']:>3}   {period:22s} "
            f"{r['is_size']:>5} {r['oos_size']:>5} "
            f"{r['is_wr']:>6.1f}% {r['oos_wr']:>6.1f}% {wr_flag}"
            f"{r['auc']:>6.3f}{auc_flag}"
            f"{r['precision']:>6.3f}"
        )

    aucs = [r['auc'] for r in valid]
    print("\n" + "-" * len(header))
    print(f"  평균 AUC: {np.mean(aucs):.3f}  (std: {np.std(aucs):.3f})")

    mean_auc = np.mean(aucs)
    if mean_auc >= 0.60:
        verdict = "✅ PASS — 실전 필터 전환 가능 (shadow_mode: false)"
    elif mean_auc >= 0.55:
        verdict = "⚠️  MARGINAL — 데이터 더 수집 후 재검증"
    else:
        verdict = "❌ FAIL — 모델 미성숙, shadow_mode 유지"
    print(f"\n  판정: {verdict}")
    print("=" * 60 + "\n")


# ── 저장 ─────────────────────────────────────────────────────────

def save_results(results: list[dict], data: list[dict]):
    LOG_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path  = LOG_DIR / f"eq_wfo_{today}.json"
    valid = [r for r in results if not r.get('skipped')]
    aucs  = [r['auc'] for r in valid] if valid else []
    out = {
        'generated_at': datetime.now().isoformat(),
        'total_samples': len(data),
        'windows':       results,
        'summary': {
            'mean_auc': round(float(np.mean(aucs)), 3) if aucs else 0,
            'std_auc':  round(float(np.std(aucs)), 3)  if aucs else 0,
            'pass':     float(np.mean(aucs)) >= 0.60 if aucs else False,
        },
    }
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"  → 결과 저장: {path}")
    return out


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    parser = argparse.ArgumentParser(description='EQ 모델 Walk Forward 검증')
    parser.add_argument('--windows',     type=int,   default=5,   help='윈도우 수')
    parser.add_argument('--test-ratio',  type=float, default=0.3, help='OOS 비율')
    parser.add_argument('--min-samples', type=int,   default=20,  help='윈도우 최소 학습 건수')
    parser.add_argument('--no-save',     action='store_true')
    args = parser.parse_args()

    data = _load_labeled(DB_PATH)
    if len(data) < 10:
        print(f"⚠️  레이블 데이터 부족: {len(data)}건 (최소 10건 필요)")
        print("   거래 누적 후 재실행하세요.")
        raise SystemExit(0)

    results = run_walk_forward(
        data,
        n_windows=args.windows,
        test_ratio=args.test_ratio,
        min_train=args.min_samples,
    )
    print_report(results, data)

    if not args.no_save:
        save_results(results, data)
