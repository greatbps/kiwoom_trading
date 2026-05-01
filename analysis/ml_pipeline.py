"""
진입 품질 예측 LightGBM 파이프라인.

컬럼 우선 / JSONB 보조 원칙으로 피처 구성.
LightGBM은 NaN 기본 지원 → 구버전 NULL 피처도 안전하게 학습.

사용법:
    python -m analysis.ml_pipeline [--days 90] [--min-samples 30] [--no-save]
"""

import os
import sys
import json
import pickle
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MODELS_DIR = Path(__file__).parent.parent / 'models'
_PG_DSN = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "trading_system"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

# 수치 피처: 컬럼 우선, JSONB 보조
NUM_COLS = ['rvol', 'vwap_distance', 'price_vs_breakout', 'ema_slope', 'atr_ratio', 'rsi', 'gap_pct']
# 범주형 피처: LightGBM categorical_feature로 처리
CAT_COLS = ['entry_type', 'choch_grade', 'market_context', 'volume_trend']
FEATURE_COLS = NUM_COLS + CAT_COLS

TARGET = 'label_binary'
MIN_SAMPLES = 30


def _get_conn():
    import psycopg2
    return psycopg2.connect(**_PG_DSN)


def load_dataset(days: int = 90) -> pd.DataFrame:
    """
    ml_dataset에서 학습 데이터 로드.
    라벨이 있는 실제 거래 데이터만 사용.
    """
    since = (datetime.now() - timedelta(days=days)).isoformat()
    sql = """
        SELECT
            md.id,
            md.trade_id,
            md.stock_code,
            md.entry_time,
            md.features,
            md.label_binary,
            md.label_quality,
            md.label_pnl_pct,
            md.rvol,
            md.price_vs_breakout,
            md.vwap_distance,
            md.ema_slope,
            md.atr_ratio,
            md.volume_trend,
            md.entry_type,
            md.holding_minutes,
            md.exit_reason
        FROM ml_dataset md
        WHERE md.label_binary IS NOT NULL
          AND md.source_type = 'trade'
          AND (md.entry_time IS NULL OR md.entry_time >= %(since)s)
        ORDER BY md.entry_time DESC NULLS LAST
    """
    import psycopg2.extras
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, {'since': since})
    rows = cur.fetchall()
    conn.close()
    return pd.DataFrame([dict(r) for r in rows])


def _jf(row) -> dict:
    """row의 JSONB features 컬럼 파싱."""
    v = row.get('features')
    if not v:
        return {}
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    if isinstance(v, dict):
        return v
    return {}


def _pick(col_val, jf_val):
    """컬럼 값이 유효하면 반환, 아니면 JSONB 값."""
    if col_val is not None and not (isinstance(col_val, float) and np.isnan(col_val)):
        return col_val
    return jf_val


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    컬럼 우선 / JSONB 보조 원칙으로 피처 행렬 구성.
    JSONB의 rsi, gap_pct는 신규 컬럼이 없으므로 항상 JSONB에서 읽음.
    """
    records = []
    for _, row in df.iterrows():
        j = _jf(row)
        feat = {
            # 수치: 신규 컬럼 우선, 없으면 JSONB
            'rvol':              _pick(row.get('rvol'),              j.get('rvol')),
            'vwap_distance':     _pick(row.get('vwap_distance'),     None),
            'price_vs_breakout': _pick(row.get('price_vs_breakout'), None),
            'ema_slope':         _pick(row.get('ema_slope'),         None),
            'atr_ratio':         _pick(row.get('atr_ratio'),         j.get('atr_ratio')),
            # JSONB 전용
            'rsi':               j.get('rsi'),
            'gap_pct':           j.get('gap_pct'),
            # 범주형
            'entry_type':        row.get('entry_type') or j.get('strategy') or 'UNKNOWN',
            'choch_grade':       j.get('choch_grade') or 'N/A',
            'market_context':    j.get('market_context') or 'UNKNOWN',
            'volume_trend':      _pick(row.get('volume_trend'), None) or 'flat',
            # 타겟·부가 정보
            TARGET:              int(row[TARGET]),
            'label_quality':     int(row['label_quality']) if pd.notna(row.get('label_quality') or float('nan')) else 0,
            'label_pnl_pct':     float(row['label_pnl_pct']) if pd.notna(row.get('label_pnl_pct') or float('nan')) else 0.0,
        }
        records.append(feat)

    out = pd.DataFrame(records)
    for col in CAT_COLS:
        out[col] = out[col].fillna('UNKNOWN').astype('category')
    for col in NUM_COLS:
        out[col] = pd.to_numeric(out[col], errors='coerce')
    return out


def train(
    days: int = 90,
    min_samples: int = MIN_SAMPLES,
    save: bool = True,
    model_tag: str = None,
) -> dict:
    """
    LightGBM 훈련.
    Returns: metrics dict
      keys: auc, f1, precision, recall, n_samples, win_rate, feature_importance, [error]
    """
    try:
        import lightgbm as lgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score, f1_score, precision_score, recall_score
    except ImportError as e:
        return {'error': f'패키지 없음: {e}', 'auc': 0.0}

    raw = load_dataset(days=days)
    if len(raw) < min_samples:
        return {
            'error': f'샘플 부족: {len(raw)}건 (최소 {min_samples}건 필요)',
            'n_samples': len(raw),
            'auc': 0.0,
        }

    feat_df = build_feature_matrix(raw)
    X = feat_df[FEATURE_COLS].copy()
    y = feat_df[TARGET].copy()

    stratify = y if y.nunique() > 1 else None
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=stratify
    )

    model = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=5,
        subsample=0.8,
        colsample_bytree=0.8,
        class_weight='balanced',
        random_state=42,
        verbose=-1,
    )
    model.fit(
        X_train, y_train,
        categorical_feature=CAT_COLS,
        eval_set=[(X_val, y_val)],
        callbacks=[
            lgb.early_stopping(30, verbose=False),
            lgb.log_evaluation(period=-1),
        ],
    )

    y_prob = model.predict_proba(X_val)[:, 1]
    y_pred = (y_prob >= 0.5).astype(int)

    auc  = roc_auc_score(y_val, y_prob) if y_val.nunique() > 1 else 0.5
    f1   = f1_score(y_val, y_pred, zero_division=0)
    prec = precision_score(y_val, y_pred, zero_division=0)
    rec  = recall_score(y_val, y_pred, zero_division=0)

    fi_sorted = dict(sorted(
        zip(FEATURE_COLS, model.feature_importances_),
        key=lambda x: x[1], reverse=True
    ))

    metrics = {
        'n_samples':          len(raw),
        'n_train':            len(X_train),
        'n_val':              len(X_val),
        'win_rate':           round(float(y.mean()), 4),
        'auc':                round(float(auc), 4),
        'f1':                 round(float(f1), 4),
        'precision':          round(float(prec), 4),
        'recall':             round(float(rec), 4),
        'feature_importance': fi_sorted,
        'trained_at':         datetime.now().isoformat(),
        'days':               days,
    }

    if save:
        _MODELS_DIR.mkdir(parents=True, exist_ok=True)
        tag = model_tag or datetime.now().strftime('%Y%m%d')
        versioned = _MODELS_DIR / f'lgbm_entry_{tag}.pkl'
        latest    = _MODELS_DIR / 'lgbm_entry_latest.pkl'
        payload   = {
            'model':        model,
            'metrics':      metrics,
            'feature_cols': FEATURE_COLS,
            'cat_cols':     CAT_COLS,
        }
        with open(versioned, 'wb') as f:
            pickle.dump(payload, f)
        with open(latest, 'wb') as f:
            pickle.dump(payload, f)
        metrics['model_path'] = str(versioned)
        logger.info(
            f"[ML] 모델 저장: {versioned.name} "
            f"AUC={auc:.4f} F1={f1:.4f} n={len(raw)}"
        )

    return metrics


def load_latest_model() -> Tuple[Optional[object], Optional[dict]]:
    """최신 모델 로드. 없으면 (None, None)."""
    latest = _MODELS_DIR / 'lgbm_entry_latest.pkl'
    if not latest.exists():
        return None, None
    try:
        with open(latest, 'rb') as f:
            payload = pickle.load(f)
        return payload['model'], payload['metrics']
    except Exception as e:
        logger.warning(f"[ML] 모델 로드 실패: {e}")
        return None, None


def predict_entry_quality(df, price: float, extra: dict = None) -> dict:
    """
    실시간 진입 시점 호출 — 현재 df로 win_prob 예측.
    extra: {'entry_type': 'EXPLORATION', 'choch_grade': 'A', ...}
    Returns: {'win_prob': 0.72, 'quality': 'HIGH'} or {'win_prob': None}
    """
    model, _ = load_latest_model()
    if model is None:
        return {'win_prob': None}
    try:
        from database.decision_trace import _extract_features_from_df
        feats = _extract_features_from_df(df, price)
        ex = extra or {}
        row = {
            'rvol':              feats.get('rvol'),
            'vwap_distance':     feats.get('vwap_distance'),
            'price_vs_breakout': feats.get('price_vs_breakout'),
            'ema_slope':         feats.get('ema_slope'),
            'atr_ratio':         feats.get('atr_ratio'),
            'rsi':               None,
            'gap_pct':           None,
            'entry_type':        ex.get('entry_type', 'UNKNOWN'),
            'choch_grade':       ex.get('choch_grade', 'N/A'),
            'market_context':    ex.get('market_context', 'UNKNOWN'),
            'volume_trend':      feats.get('volume_trend') or 'flat',
        }
        X = pd.DataFrame([row])
        for col in CAT_COLS:
            X[col] = X[col].astype('category')
        prob = float(model.predict_proba(X)[0, 1])
        quality = 'HIGH' if prob >= 0.65 else ('MED' if prob >= 0.50 else 'LOW')
        return {'win_prob': round(prob, 3), 'quality': quality}
    except Exception as e:
        logger.debug(f"[ML] predict_entry_quality 실패: {e}")
        return {'win_prob': None}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    parser = argparse.ArgumentParser(description='LightGBM 진입 품질 파이프라인')
    parser.add_argument('--days',        type=int, default=90,  help='학습 기간 (일)')
    parser.add_argument('--min-samples', type=int, default=30,  help='최소 샘플 수')
    parser.add_argument('--no-save',     action='store_true',   help='모델 저장 안 함')
    args = parser.parse_args()

    metrics = train(days=args.days, min_samples=args.min_samples, save=not args.no_save)
    if 'error' in metrics:
        print(f"[ERROR] {metrics['error']}")
        sys.exit(1)

    print(f"\n=== ML Pipeline 결과 ===")
    print(f"샘플  : {metrics['n_samples']}건  (train {metrics['n_train']} / val {metrics['n_val']})")
    print(f"승률  : {metrics['win_rate']:.1%}")
    print(f"AUC   : {metrics['auc']:.4f}")
    print(f"F1    : {metrics['f1']:.4f}  (prec={metrics['precision']:.3f} rec={metrics['recall']:.3f})")
    print(f"\n피처 중요도 (top 8):")
    fi_vals = list(metrics['feature_importance'].values())
    fi_max  = max(fi_vals) if fi_vals and max(fi_vals) > 0 else 1
    for feat, imp in list(metrics['feature_importance'].items())[:8]:
        bar = '█' * int(imp / fi_max * 20) if imp > 0 else ''
        print(f"  {feat:<22} {bar:<20} {imp:.0f}")
    if 'model_path' in metrics:
        print(f"\n모델 저장: {metrics['model_path']}")
