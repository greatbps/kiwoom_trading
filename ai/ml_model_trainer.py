#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/ml_model_trainer.py

ML 모델 학습 시스템 (LightGBM/XGBoost)
- 시그널 예측 모델 학습
- 확신도 점수화 (0~100)
- 모델 버전 관리
"""

import os
import json
import pickle
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split, TimeSeriesSplit
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score,
    f1_score, roc_auc_score, confusion_matrix
)

logger = logging.getLogger(__name__)


@dataclass
class ModelMetrics:
    """모델 평가 메트릭"""
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    roc_auc: float
    confusion_matrix: List[List[int]]

    # 추가 메트릭
    win_rate: float  # 실제 승률
    avg_profit: float  # 평균 수익률
    sharpe_ratio: float  # 샤프 비율

    # 메타데이터
    train_samples: int
    test_samples: int
    feature_count: int
    training_time: float

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return asdict(self)


@dataclass
class ModelVersion:
    """모델 버전 정보"""
    version: str
    model_type: str  # 'lightgbm' or 'xgboost'
    created_at: str
    metrics: ModelMetrics
    hyperparameters: Dict[str, Any]
    feature_names: List[str]
    model_path: str

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        data = asdict(self)
        data['metrics'] = self.metrics.to_dict()
        return data


class MLModelTrainer:
    """ML 모델 학습 및 관리"""

    def __init__(
        self,
        model_dir: str = "./ai/models",
        model_type: str = "lightgbm",  # 'lightgbm' or 'xgboost'
    ):
        """
        초기화

        Args:
            model_dir: 모델 저장 디렉토리
            model_type: 모델 타입 ('lightgbm' 또는 'xgboost')
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        self.model_type = model_type
        self.model = None
        self.feature_names: List[str] = []

        # 버전 관리
        self.versions_file = self.model_dir / "versions.json"
        self.versions: List[ModelVersion] = self._load_versions()

    def _load_versions(self) -> List[ModelVersion]:
        """저장된 모델 버전 로드"""
        if not self.versions_file.exists():
            return []

        try:
            with open(self.versions_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            versions = []
            for v in data:
                metrics = ModelMetrics(**v['metrics'])
                v['metrics'] = metrics
                versions.append(ModelVersion(**v))

            return versions
        except Exception as e:
            logger.error(f"버전 로드 실패: {e}")
            return []

    def _save_versions(self):
        """모델 버전 저장"""
        try:
            data = [v.to_dict() for v in self.versions]
            with open(self.versions_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"버전 저장 실패: {e}")

    def prepare_data(
        self,
        df: pd.DataFrame,
        target_column: str = "target",
        test_size: float = 0.2,
        time_series_split: bool = True,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
        """
        학습 데이터 준비

        Args:
            df: Feature DataFrame
            target_column: 타겟 컬럼명
            test_size: 테스트 셋 비율
            time_series_split: 시계열 분할 여부

        Returns:
            (X_train, X_test, y_train, y_test)
        """
        # Feature와 Target 분리
        X = df.drop(columns=[target_column])
        y = df[target_column]

        self.feature_names = list(X.columns)

        # 시계열 분할 또는 랜덤 분할
        if time_series_split:
            split_idx = int(len(X) * (1 - test_size))
            X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
            y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
        else:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=test_size, random_state=42
            )

        logger.info(f"데이터 준비 완료: Train={len(X_train)}, Test={len(X_test)}")
        return X_train, X_test, y_train, y_test

    def train_lightgbm(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        y_test: pd.Series,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, ModelMetrics]:
        """
        LightGBM 모델 학습

        Args:
            X_train, y_train: 학습 데이터
            X_test, y_test: 테스트 데이터
            params: 하이퍼파라미터

        Returns:
            (모델, 메트릭)
        """
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("LightGBM이 설치되지 않았습니다: pip install lightgbm")

        # 기본 하이퍼파라미터
        default_params = {
            'objective': 'binary',
            'metric': 'auc',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.9,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'verbose': -1,
            'random_state': 42,
        }

        if params:
            default_params.update(params)

        # 데이터셋 생성
        train_data = lgb.Dataset(X_train, label=y_train)
        test_data = lgb.Dataset(X_test, label=y_test, reference=train_data)

        # 학습
        start_time = datetime.now()
        model = lgb.train(
            default_params,
            train_data,
            num_boost_round=500,
            valid_sets=[test_data],
            callbacks=[lgb.early_stopping(stopping_rounds=50)],
        )
        training_time = (datetime.now() - start_time).total_seconds()

        # 예측 및 평가
        y_pred_proba = model.predict(X_test)
        y_pred = (y_pred_proba > 0.5).astype(int)

        metrics = self._calculate_metrics(
            y_test, y_pred, y_pred_proba,
            len(X_train), len(X_test),
            len(self.feature_names), training_time
        )

        logger.info(f"LightGBM 학습 완료: Accuracy={metrics.accuracy:.3f}, AUC={metrics.roc_auc:.3f}")
        return model, metrics

    def train_xgboost(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        y_test: pd.Series,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, ModelMetrics]:
        """
        XGBoost 모델 학습

        Args:
            X_train, y_train: 학습 데이터
            X_test, y_test: 테스트 데이터
            params: 하이퍼파라미터

        Returns:
            (모델, 메트릭)
        """
        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("XGBoost가 설치되지 않았습니다: pip install xgboost")

        # 기본 하이퍼파라미터
        default_params = {
            'objective': 'binary:logistic',
            'eval_metric': 'auc',
            'max_depth': 6,
            'learning_rate': 0.05,
            'subsample': 0.8,
            'colsample_bytree': 0.9,
            'random_state': 42,
        }

        if params:
            default_params.update(params)

        # 데이터셋 생성
        dtrain = xgb.DMatrix(X_train, label=y_train)
        dtest = xgb.DMatrix(X_test, label=y_test)

        # 학습
        start_time = datetime.now()
        model = xgb.train(
            default_params,
            dtrain,
            num_boost_round=500,
            evals=[(dtest, 'test')],
            early_stopping_rounds=50,
            verbose_eval=False,
        )
        training_time = (datetime.now() - start_time).total_seconds()

        # 예측 및 평가
        y_pred_proba = model.predict(dtest)
        y_pred = (y_pred_proba > 0.5).astype(int)

        metrics = self._calculate_metrics(
            y_test, y_pred, y_pred_proba,
            len(X_train), len(X_test),
            len(self.feature_names), training_time
        )

        logger.info(f"XGBoost 학습 완료: Accuracy={metrics.accuracy:.3f}, AUC={metrics.roc_auc:.3f}")
        return model, metrics

    def _calculate_metrics(
        self,
        y_true: pd.Series,
        y_pred: np.ndarray,
        y_pred_proba: np.ndarray,
        train_samples: int,
        test_samples: int,
        feature_count: int,
        training_time: float,
    ) -> ModelMetrics:
        """모델 평가 메트릭 계산"""

        accuracy = accuracy_score(y_true, y_pred)
        precision = precision_score(y_true, y_pred, zero_division=0)
        recall = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        roc_auc = roc_auc_score(y_true, y_pred_proba)
        cm = confusion_matrix(y_true, y_pred).tolist()

        # 승률 (Precision과 동일하지만 명시적으로 계산)
        win_rate = precision

        # 평균 수익률 (가정: 예측 성공 시 +2%, 실패 시 -1%)
        avg_profit = (precision * 0.02) + ((1 - precision) * -0.01)

        # 샤프 비율 (간단한 추정)
        sharpe_ratio = (avg_profit / 0.02) * np.sqrt(252) if avg_profit > 0 else 0.0

        return ModelMetrics(
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1_score=f1,
            roc_auc=roc_auc,
            confusion_matrix=cm,
            win_rate=win_rate,
            avg_profit=avg_profit,
            sharpe_ratio=sharpe_ratio,
            train_samples=train_samples,
            test_samples=test_samples,
            feature_count=feature_count,
            training_time=training_time,
        )

    def train(
        self,
        df: pd.DataFrame,
        target_column: str = "target",
        test_size: float = 0.2,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, ModelMetrics]:
        """
        모델 학습 (타입에 따라 자동 선택)

        Args:
            df: Feature DataFrame
            target_column: 타겟 컬럼명
            test_size: 테스트 셋 비율
            params: 하이퍼파라미터

        Returns:
            (모델, 메트릭)
        """
        # 데이터 준비
        X_train, X_test, y_train, y_test = self.prepare_data(
            df, target_column, test_size
        )

        # 모델 학습
        if self.model_type == "lightgbm":
            model, metrics = self.train_lightgbm(
                X_train, y_train, X_test, y_test, params
            )
        elif self.model_type == "xgboost":
            model, metrics = self.train_xgboost(
                X_train, y_train, X_test, y_test, params
            )
        else:
            raise ValueError(f"지원하지 않는 모델 타입: {self.model_type}")

        self.model = model
        return model, metrics

    def predict_confidence(
        self,
        X: pd.DataFrame,
    ) -> np.ndarray:
        """
        시그널 확신도 예측 (0~100)

        Args:
            X: Feature DataFrame

        Returns:
            확신도 배열 (0~100)
        """
        if self.model is None:
            raise ValueError("모델이 학습되지 않았습니다.")

        # 예측 확률
        if self.model_type == "lightgbm":
            probas = self.model.predict(X)
        elif self.model_type == "xgboost":
            import xgboost as xgb
            dmatrix = xgb.DMatrix(X)
            probas = self.model.predict(dmatrix)
        else:
            raise ValueError(f"지원하지 않는 모델 타입: {self.model_type}")

        # 0~100 스케일로 변환
        confidence_scores = probas * 100

        return confidence_scores

    def save_model(
        self,
        version: str,
        metrics: ModelMetrics,
        hyperparameters: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        모델 저장 및 버전 관리

        Args:
            version: 버전 문자열 (예: 'v1.0.0')
            metrics: 평가 메트릭
            hyperparameters: 하이퍼파라미터

        Returns:
            저장된 모델 경로
        """
        if self.model is None:
            raise ValueError("저장할 모델이 없습니다.")

        # 모델 파일명
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_filename = f"{self.model_type}_{version}_{timestamp}.pkl"
        model_path = self.model_dir / model_filename

        # 모델 저장
        with open(model_path, 'wb') as f:
            pickle.dump(self.model, f)

        # 버전 정보 생성
        model_version = ModelVersion(
            version=version,
            model_type=self.model_type,
            created_at=datetime.now().isoformat(),
            metrics=metrics,
            hyperparameters=hyperparameters or {},
            feature_names=self.feature_names,
            model_path=str(model_path),
        )

        # 버전 리스트에 추가
        self.versions.append(model_version)
        self._save_versions()

        logger.info(f"모델 저장 완료: {model_path}")
        return str(model_path)

    def load_model(
        self,
        version: Optional[str] = None,
        model_path: Optional[str] = None,
    ):
        """
        모델 로드

        Args:
            version: 버전 문자열 (최신 버전이면 None)
            model_path: 직접 모델 경로 지정
        """
        if model_path:
            # 직접 경로 지정
            path = Path(model_path)
        elif version:
            # 버전으로 찾기
            model_version = next((v for v in self.versions if v.version == version), None)
            if not model_version:
                raise ValueError(f"버전을 찾을 수 없습니다: {version}")
            path = Path(model_version.model_path)
            self.feature_names = model_version.feature_names
        else:
            # 최신 버전 로드
            if not self.versions:
                raise ValueError("저장된 모델이 없습니다.")
            model_version = self.versions[-1]
            path = Path(model_version.model_path)
            self.feature_names = model_version.feature_names

        # 모델 로드
        with open(path, 'rb') as f:
            self.model = pickle.load(f)

        logger.info(f"모델 로드 완료: {path}")

    def get_feature_importance(
        self,
        top_n: int = 20,
    ) -> pd.DataFrame:
        """
        Feature 중요도 반환

        Args:
            top_n: 상위 N개 Feature

        Returns:
            Feature 중요도 DataFrame
        """
        if self.model is None:
            raise ValueError("모델이 학습되지 않았습니다.")

        if self.model_type == "lightgbm":
            importance = self.model.feature_importance(importance_type='gain')
        elif self.model_type == "xgboost":
            importance = list(self.model.get_score(importance_type='gain').values())
        else:
            raise ValueError(f"지원하지 않는 모델 타입: {self.model_type}")

        df = pd.DataFrame({
            'feature': self.feature_names,
            'importance': importance
        })
        df = df.sort_values('importance', ascending=False).head(top_n)

        return df
