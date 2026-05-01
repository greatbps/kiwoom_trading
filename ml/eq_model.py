"""
ml/eq_model.py — Entry Quality LightGBM 모델

역할: 진입 피처 → P(win) 예측 → shadow 로그 or 실제 차단
학습: python -m ml.eq_model --train
"""

import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent.parent / "models" / "eq_model.pkl"

FEATURES = [
    'entry_confidence',
    'r_pct',
    'htf_trend',
    'sweep',
    'atr_pct',
    'volume_ratio',
    'rsi',
    'squeeze_on',
    'time_slot',
    'choch_grade_enc',   # A=2, B=1, C=0
    'eq_grade_enc',      # A=2, B=1, C=0
    'regime_enc',        # BULL=2, SIDEWAYS=1, BEAR=0, UNKNOWN=1
    'guard_enc',         # normal=0, lsg=1, conservative=2
]

_CHOCH_ENC  = {'A': 2, 'A+': 2, 'B': 1, 'C': 0}
_EQ_ENC     = {'A': 2, 'B': 1, 'C': 0}
_REGIME_ENC = {'BULL': 2, 'TREND': 2, 'SIDEWAYS': 1, 'NEUTRAL': 1,
               'BEAR': 0, 'REVERSAL': 0, 'UNKNOWN': 1}
_GUARD_ENC  = {'normal': 0, 'lsg': 1, 'conservative': 2}


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


class EQModel:
    """Entry Quality 모델 — shadow_mode=True이면 로그만, False이면 실제 차단"""

    def __init__(self, config: dict = None):
        cfg = (config or {}).get('eq_ml_filter', {})
        self.enabled       = cfg.get('enabled', False)
        self.shadow_mode   = cfg.get('shadow_mode', True)
        self.threshold     = cfg.get('threshold', 0.40)
        self.min_samples   = cfg.get('min_train_samples', 50)
        self.retrain_every = cfg.get('retrain_interval_trades', 20)

        self._model = None
        self._trade_count = 0
        self._load()

    # ── 예측 ─────────────────────────────────────────────────────

    def predict(self, features: dict) -> float:
        """P(win) 반환. 모델 없으면 0.5."""
        if self._model is None:
            return 0.5
        x = np.array([_encode(features)], dtype=np.float32)
        try:
            return float(self._model.predict(x)[0])
        except Exception as e:
            logger.warning(f"[EQ_MODEL] predict 실패: {e}")
            return 0.5

    def should_block(self, features: dict) -> tuple[bool, float]:
        """
        (차단여부, p_win) 반환.
        shadow_mode=True → 항상 False (로그만)
        shadow_mode=False → p_win < threshold이면 True
        """
        if not self.enabled or self._model is None:
            return False, 0.5

        p_win = self.predict(features)

        if self.shadow_mode:
            return False, p_win

        return p_win < self.threshold, p_win

    # ── 학습 ─────────────────────────────────────────────────────

    def train(self, data: "list[dict]") -> bool:
        """
        레이블된 데이터로 LightGBM 학습.
        Returns True if successful.
        """
        if len(data) < self.min_samples:
            logger.info(f"[EQ_MODEL] 학습 데이터 부족: {len(data)}/{self.min_samples}")
            return False

        try:
            import lightgbm as lgb

            X = np.array([_encode(r) for r in data], dtype=np.float32)
            y = np.array([r['outcome_win'] for r in data], dtype=np.int32)

            win_rate = y.mean()
            scale = (1 - win_rate) / win_rate if win_rate > 0 else 1.0

            model = lgb.LGBMClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                num_leaves=15,
                min_child_samples=10,
                scale_pos_weight=scale,
                random_state=42,
                verbose=-1,
            )
            model.fit(X, y)

            self._model = model
            self._save()

            preds = model.predict_proba(X)[:, 1]
            auc = _roc_auc(y, preds)
            logger.info(
                f"[EQ_MODEL] 학습 완료: n={len(data)} win_rate={win_rate:.1%} "
                f"AUC={auc:.3f} features={FEATURES}"
            )
            return True

        except Exception as e:
            logger.error(f"[EQ_MODEL] 학습 실패: {e}")
            return False

    def maybe_retrain(self, feature_logger):
        """retrain_every 거래마다 자동 재학습 시도"""
        self._trade_count += 1
        if self._trade_count % self.retrain_every == 0:
            data = feature_logger.load_labeled(self.min_samples)
            if data:
                self.train(data)

    # ── 저장/로드 ─────────────────────────────────────────────────

    def _save(self):
        MODEL_PATH.parent.mkdir(exist_ok=True)
        with open(MODEL_PATH, 'wb') as f:
            pickle.dump(self._model, f)
        logger.info(f"[EQ_MODEL] 모델 저장: {MODEL_PATH}")

    def _load(self):
        if MODEL_PATH.exists():
            try:
                with open(MODEL_PATH, 'rb') as f:
                    self._model = pickle.load(f)
                logger.info(f"[EQ_MODEL] 모델 로드: {MODEL_PATH}")
            except Exception as e:
                logger.warning(f"[EQ_MODEL] 모델 로드 실패: {e}")

    def feature_importance(self) -> dict:
        if self._model is None:
            return {}
        imp = self._model.feature_importances_
        return dict(sorted(zip(FEATURES, imp), key=lambda x: -x[1]))


def _roc_auc(y_true, y_score) -> float:
    """sklearn 없이 AUC 계산 (간단 trapz)"""
    try:
        from sklearn.metrics import roc_auc_score
        return roc_auc_score(y_true, y_score)
    except Exception:
        return 0.0


# ── CLI: python -m ml.eq_model --train ───────────────────────────

if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    parser = argparse.ArgumentParser()
    parser.add_argument('--train', action='store_true')
    parser.add_argument('--stats', action='store_true')
    parser.add_argument('--importance', action='store_true')
    args = parser.parse_args()

    from ml.feature_logger import FeatureLogger
    fl = FeatureLogger()

    if args.stats:
        s = fl.stats()
        print(f"전체: {s['total']} | 레이블: {s['labeled']} | 승률: {s['win_rate']}%")

    if args.train:
        data = fl.load_labeled(min_samples=1)
        if not data:
            print("데이터 없음")
        else:
            model = EQModel()
            model.min_samples = 1
            ok = model.train(data)
            if ok and args.importance:
                print("\n[피처 중요도]")
                for f, v in model.feature_importance().items():
                    print(f"  {f:25s} {v:.0f}")
