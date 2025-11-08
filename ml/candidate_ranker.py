#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ml/candidate_ranker.py

Candidate Ranker: 조건검색 + VWAP 통과 종목들의 우선순위 점수화

역할:
- 조건검색 결과를 입력받아 buy_probability와 predicted_return 산출
- 상위 K개 종목만 실제 매매 대상으로 선정
- 백테스트 결과 기반 학습 데이터 생성

Pipeline 위치:
  조건검색 → VWAP 필터 → [Ranker] → 모니터링 → 매매
"""

import os
import json
import logging
import pickle
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    logging.warning("LightGBM not installed. Install with: pip install lightgbm")

logger = logging.getLogger(__name__)


class CandidateRanker:
    """
    조건검색 통과 종목의 우선순위 점수화

    Features (입력):
    - vwap_backtest_winrate: 백테스트 승률
    - vwap_avg_profit: 평균 수익률
    - current_vwap_distance: 현재가와 VWAP 괴리율
    - volume_z_score: 거래량 Z-score (급등 여부)
    - recent_return_5d: 최근 5일 수익률
    - market_volatility: 시장 변동성 (KOSPI)

    Outputs:
    - buy_probability: 매수 추천 확률 (0~1)
    - predicted_return: 예상 수익률 (%)
    - confidence_score: 신뢰도 점수
    """

    def __init__(
        self,
        model_dir: str = "./models/ranker",
        min_train_samples: int = 100
    ):
        """
        Args:
            model_dir: 모델 저장 디렉토리
            min_train_samples: 최소 학습 샘플 수
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        self.min_train_samples = min_train_samples

        # 모델
        self.classifier = None  # buy_probability 예측
        self.regressor = None   # predicted_return 예측

        # Feature 정의
        self.features = [
            'vwap_backtest_winrate',
            'vwap_avg_profit',
            'current_vwap_distance',
            'volume_z_score',
            'recent_return_5d',
            'market_volatility',
            'sector_strength',  # 업종 강도
            'price_momentum',   # 가격 모멘텀
        ]

        self.metadata = {
            'trained_at': None,
            'n_samples': 0,
            'version': '1.0.0'
        }

        # 모델 로드 시도
        self.load_models()

    def prepare_features(self, candidates: pd.DataFrame) -> pd.DataFrame:
        """
        후보 종목 DataFrame에서 Feature 추출

        Args:
            candidates: 조건검색 결과 DataFrame
                       (code, name, current_price, vwap, volume, ...)

        Returns:
            Feature DataFrame
        """
        df = candidates.copy()

        # VWAP 괴리율
        if 'current_price' in df.columns and 'vwap' in df.columns:
            df['current_vwap_distance'] = (
                (df['current_price'] - df['vwap']) / df['vwap'] * 100
            )
        else:
            df['current_vwap_distance'] = 0.0

        # 거래량 Z-score (20일 평균 대비)
        if 'volume' in df.columns and 'volume_avg_20d' in df.columns:
            df['volume_z_score'] = (
                (df['volume'] - df['volume_avg_20d']) /
                (df['volume_std_20d'] + 1e-9)
            )
        else:
            df['volume_z_score'] = 0.0

        # 백테스트 지표는 이미 있다고 가정
        # (main_vwap_backtest.py 결과를 merge한 상태)

        # 없는 컬럼은 기본값 설정
        for feat in self.features:
            if feat not in df.columns:
                df[feat] = 0.0

        return df[self.features]

    def rank_candidates(
        self,
        candidates: pd.DataFrame,
        threshold: float = 0.5,
        top_k: Optional[int] = None
    ) -> pd.DataFrame:
        """
        후보 종목 점수화 및 랭킹

        Args:
            candidates: 조건검색 결과 DataFrame
            threshold: buy_probability 임계값 (기본 0.5)
            top_k: 상위 K개만 반환 (None이면 전체)

        Returns:
            점수가 추가된 DataFrame (정렬됨)
        """
        if self.classifier is None:
            logger.warning("모델이 학습되지 않았습니다. 기본 점수 사용")
            candidates['buy_probability'] = 0.5
            candidates['predicted_return'] = 0.0
            candidates['confidence_score'] = 0.0
            return candidates

        # Feature 준비
        X = self.prepare_features(candidates)

        # 예측
        buy_prob = self.classifier.predict_proba(X)[:, 1]
        pred_return = self.regressor.predict(X)

        # 신뢰도 점수 (buy_prob과 예상수익률 조합)
        confidence = buy_prob * np.clip(pred_return / 5.0, 0, 1)

        # 결과 추가
        result = candidates.copy()
        result['buy_probability'] = buy_prob
        result['predicted_return'] = pred_return
        result['confidence_score'] = confidence

        # 필터링
        result = result[result['buy_probability'] >= threshold]

        # 정렬 (confidence_score 내림차순)
        result = result.sort_values('confidence_score', ascending=False)

        # Top-K
        if top_k is not None:
            result = result.head(top_k)

        logger.info(f"Ranking 완료: {len(result)}/{len(candidates)} 종목 선정")

        return result

    def train(
        self,
        training_data: pd.DataFrame,
        target_col: str = 'actual_profit_pct',
        test_size: float = 0.2
    ) -> Dict[str, Any]:
        """
        백테스트 결과로 모델 학습

        Args:
            training_data: 학습 데이터
                          (features + actual_profit_pct, is_profitable)
            target_col: 수익률 컬럼명
            test_size: 테스트 비율

        Returns:
            학습 결과 메트릭
        """
        if len(training_data) < self.min_train_samples:
            raise ValueError(
                f"학습 데이터 부족: {len(training_data)} < {self.min_train_samples}"
            )

        # Feature 준비
        X = self.prepare_features(training_data)

        # Target 준비
        y_return = training_data[target_col].values
        y_binary = (y_return > 0).astype(int)  # 수익 여부

        # Train/Test Split
        X_train, X_test, y_train_bin, y_test_bin, y_train_ret, y_test_ret = \
            train_test_split(
                X, y_binary, y_return,
                test_size=test_size,
                random_state=42,
                stratify=y_binary
            )

        # 1. Classifier (buy_probability)
        logger.info("Classifier 학습 중...")
        self.classifier = lgb.LGBMClassifier(
            objective='binary',
            metric='auc',
            n_estimators=200,
            learning_rate=0.05,
            max_depth=5,
            num_leaves=31,
            random_state=42,
            verbose=-1
        )

        self.classifier.fit(
            X_train, y_train_bin,
            eval_set=[(X_test, y_test_bin)],
            eval_metric='auc',
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)]
        )

        # 2. Regressor (predicted_return)
        logger.info("Regressor 학습 중...")
        self.regressor = lgb.LGBMRegressor(
            objective='regression',
            metric='rmse',
            n_estimators=200,
            learning_rate=0.05,
            max_depth=5,
            num_leaves=31,
            random_state=42,
            verbose=-1
        )

        self.regressor.fit(
            X_train, y_train_ret,
            eval_set=[(X_test, y_test_ret)],
            eval_metric='rmse',
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)]
        )

        # 평가
        from sklearn.metrics import roc_auc_score, accuracy_score, mean_squared_error

        y_pred_prob = self.classifier.predict_proba(X_test)[:, 1]
        y_pred_bin = (y_pred_prob >= 0.5).astype(int)
        y_pred_ret = self.regressor.predict(X_test)

        metrics = {
            'classifier': {
                'auc': roc_auc_score(y_test_bin, y_pred_prob),
                'accuracy': accuracy_score(y_test_bin, y_pred_bin),
            },
            'regressor': {
                'rmse': np.sqrt(mean_squared_error(y_test_ret, y_pred_ret)),
                'mae': np.mean(np.abs(y_test_ret - y_pred_ret)),
            },
            'n_train': len(X_train),
            'n_test': len(X_test),
        }

        logger.info(f"학습 완료 - AUC: {metrics['classifier']['auc']:.3f}, "
                   f"RMSE: {metrics['regressor']['rmse']:.3f}")

        # 메타데이터 업데이트
        self.metadata['trained_at'] = datetime.now().isoformat()
        self.metadata['n_samples'] = len(training_data)
        self.metadata['metrics'] = metrics

        # 모델 저장
        self.save_models()

        return metrics

    def save_models(self):
        """모델 저장"""
        if self.classifier is not None:
            classifier_path = self.model_dir / "classifier.pkl"
            with open(classifier_path, 'wb') as f:
                pickle.dump(self.classifier, f)
            logger.info(f"Classifier 저장: {classifier_path}")

        if self.regressor is not None:
            regressor_path = self.model_dir / "regressor.pkl"
            with open(regressor_path, 'wb') as f:
                pickle.dump(self.regressor, f)
            logger.info(f"Regressor 저장: {regressor_path}")

        # 메타데이터 저장
        metadata_path = self.model_dir / "metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)

    def load_models(self) -> bool:
        """모델 로드"""
        try:
            classifier_path = self.model_dir / "classifier.pkl"
            regressor_path = self.model_dir / "regressor.pkl"
            metadata_path = self.model_dir / "metadata.json"

            if not all([p.exists() for p in [classifier_path, regressor_path, metadata_path]]):
                logger.info("저장된 모델이 없습니다")
                return False

            with open(classifier_path, 'rb') as f:
                self.classifier = pickle.load(f)

            with open(regressor_path, 'rb') as f:
                self.regressor = pickle.load(f)

            with open(metadata_path, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)

            logger.info(f"모델 로드 완료 (학습일: {self.metadata.get('trained_at')})")
            return True

        except Exception as e:
            logger.error(f"모델 로드 실패: {e}")
            return False

    def get_feature_importance(self) -> pd.DataFrame:
        """Feature Importance 반환"""
        if self.classifier is None:
            return pd.DataFrame()

        importance = pd.DataFrame({
            'feature': self.features,
            'importance_classifier': self.classifier.feature_importances_,
            'importance_regressor': self.regressor.feature_importances_
        })

        importance = importance.sort_values('importance_classifier', ascending=False)
        return importance


if __name__ == "__main__":
    # 테스트
    logging.basicConfig(level=logging.INFO)

    # 샘플 데이터 생성
    np.random.seed(42)
    n = 500

    sample_data = pd.DataFrame({
        'code': [f"{i:06d}" for i in range(n)],
        'name': [f"종목{i}" for i in range(n)],
        'vwap_backtest_winrate': np.random.uniform(0.3, 0.8, n),
        'vwap_avg_profit': np.random.uniform(-2, 5, n),
        'current_vwap_distance': np.random.uniform(-5, 5, n),
        'volume_z_score': np.random.uniform(-2, 4, n),
        'recent_return_5d': np.random.uniform(-10, 10, n),
        'market_volatility': np.random.uniform(10, 30, n),
        'sector_strength': np.random.uniform(-5, 5, n),
        'price_momentum': np.random.uniform(-3, 3, n),
        'actual_profit_pct': np.random.uniform(-5, 10, n),
    })

    # 학습
    ranker = CandidateRanker()
    metrics = ranker.train(sample_data)

    print(f"\n학습 결과: {metrics}")
    print(f"\nFeature Importance:")
    print(ranker.get_feature_importance())

    # 예측
    candidates = sample_data.head(20)
    ranked = ranker.rank_candidates(candidates, threshold=0.5, top_k=10)

    print(f"\n상위 10개 종목:")
    print(ranked[['name', 'buy_probability', 'predicted_return', 'confidence_score']])
