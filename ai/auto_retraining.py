#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/auto_retraining.py

자동 재학습 시스템
- 주간 자동 재학습 (매주 토요일)
- 모델 성능 검증
- 자동 배포
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pathlib import Path

import pandas as pd

from ai.ml_model_trainer import MLModelTrainer, ModelMetrics
from ai.feature_engineer import FeatureEngineer

logger = logging.getLogger(__name__)


class AutoRetrainingScheduler:
    """자동 재학습 스케줄러"""

    def __init__(
        self,
        model_trainer: MLModelTrainer,
        feature_engineer: FeatureEngineer,
        data_dir: str = "./data",
        min_samples: int = 1000,  # 최소 학습 샘플 수
        performance_threshold: float = 0.60,  # 최소 정확도
    ):
        """
        초기화

        Args:
            model_trainer: ML 모델 트레이너
            feature_engineer: Feature 생성기
            data_dir: 데이터 디렉토리
            min_samples: 최소 학습 샘플 수
            performance_threshold: 최소 성능 기준 (정확도)
        """
        self.model_trainer = model_trainer
        self.feature_engineer = feature_engineer
        self.data_dir = Path(data_dir)
        self.min_samples = min_samples
        self.performance_threshold = performance_threshold

        # 재학습 기록
        self.last_retrain: Optional[datetime] = None
        self.retrain_history: list = []

    async def collect_training_data(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        학습 데이터 수집

        Args:
            start_date: 시작일
            end_date: 종료일

        Returns:
            Feature DataFrame (with target)
        """
        logger.info("학습 데이터 수집 시작...")

        # 기본값: 최근 6개월
        if not end_date:
            end_date = datetime.now()
        if not start_date:
            start_date = end_date - timedelta(days=180)

        # TODO: 실제 데이터 수집 로직
        # 1. 키움 API에서 과거 데이터 수집
        # 2. Feature 생성
        # 3. Target 레이블링 (익절/손절 여부)

        # 임시: 샘플 데이터 생성
        from ai.feature_engineer import generate_sample_data
        df = generate_sample_data(n_days=180)

        # Target 레이블 추가 (예시: 다음날 수익률 > 2% → 1, 아니면 0)
        df['target'] = (df['close'].pct_change().shift(-1) > 0.02).astype(int)
        df = df.dropna()

        logger.info(f"데이터 수집 완료: {len(df)} 샘플")
        return df

    async def validate_model(
        self,
        model: Any,
        X_test: pd.DataFrame,
        y_test: pd.Series,
    ) -> bool:
        """
        모델 검증

        Args:
            model: 학습된 모델
            X_test, y_test: 테스트 데이터

        Returns:
            검증 통과 여부
        """
        from sklearn.metrics import accuracy_score

        # 예측
        if self.model_trainer.model_type == "lightgbm":
            y_pred_proba = model.predict(X_test)
        else:  # xgboost
            import xgboost as xgb
            dmatrix = xgb.DMatrix(X_test)
            y_pred_proba = model.predict(dmatrix)

        y_pred = (y_pred_proba > 0.5).astype(int)

        # 정확도 검증
        accuracy = accuracy_score(y_test, y_pred)

        passed = accuracy >= self.performance_threshold

        logger.info(
            f"모델 검증: Accuracy={accuracy:.3f}, "
            f"Threshold={self.performance_threshold:.3f}, "
            f"Result={'PASS' if passed else 'FAIL'}"
        )

        return passed

    async def retrain(
        self,
        force: bool = False,
        deploy: bool = True,
    ) -> Dict[str, Any]:
        """
        모델 재학습

        Args:
            force: 강제 재학습 여부
            deploy: 자동 배포 여부

        Returns:
            재학습 결과
        """
        start_time = datetime.now()

        logger.info("=" * 80)
        logger.info("자동 재학습 시작")
        logger.info("=" * 80)

        try:
            # 1. 데이터 수집
            df = await self.collect_training_data()

            if len(df) < self.min_samples:
                raise ValueError(
                    f"학습 샘플 부족: {len(df)} < {self.min_samples}"
                )

            # 2. 모델 학습
            model, metrics = self.model_trainer.train(
                df,
                target_column='target',
                test_size=0.2,
            )

            # 3. 모델 검증
            X_train, X_test, y_train, y_test = self.model_trainer.prepare_data(
                df, 'target', test_size=0.2
            )

            validation_passed = await self.validate_model(model, X_test, y_test)

            if not validation_passed and not force:
                raise ValueError(
                    f"모델 검증 실패: Accuracy={metrics.accuracy:.3f} < "
                    f"{self.performance_threshold:.3f}"
                )

            # 4. 모델 저장
            version = f"v{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            model_path = self.model_trainer.save_model(
                version=version,
                metrics=metrics,
                hyperparameters=None,
            )

            # 5. 배포 (옵션)
            if deploy and validation_passed:
                logger.info("모델 자동 배포 완료")

            # 6. 재학습 기록
            self.last_retrain = datetime.now()
            retrain_record = {
                'timestamp': start_time.isoformat(),
                'version': version,
                'samples': len(df),
                'metrics': metrics.to_dict(),
                'validation_passed': validation_passed,
                'deployed': deploy and validation_passed,
                'duration': (datetime.now() - start_time).total_seconds(),
            }
            self.retrain_history.append(retrain_record)

            logger.info("=" * 80)
            logger.info("자동 재학습 완료")
            logger.info(f"  버전: {version}")
            logger.info(f"  정확도: {metrics.accuracy:.3f}")
            logger.info(f"  AUC: {metrics.roc_auc:.3f}")
            logger.info(f"  소요 시간: {retrain_record['duration']:.1f}초")
            logger.info("=" * 80)

            return retrain_record

        except Exception as e:
            logger.error(f"재학습 실패: {e}")
            return {
                'timestamp': start_time.isoformat(),
                'error': str(e),
                'success': False,
            }

    async def schedule_weekly_retrain(self):
        """주간 자동 재학습 스케줄러 (매주 토요일 오전 2시)"""
        logger.info("주간 자동 재학습 스케줄러 시작")

        while True:
            try:
                now = datetime.now()

                # 다음 토요일 오전 2시 계산
                days_until_saturday = (5 - now.weekday()) % 7
                if days_until_saturday == 0 and now.hour >= 2:
                    days_until_saturday = 7

                next_saturday = now + timedelta(days=days_until_saturday)
                next_retrain = next_saturday.replace(hour=2, minute=0, second=0, microsecond=0)

                # 대기 시간
                wait_seconds = (next_retrain - now).total_seconds()

                logger.info(
                    f"다음 재학습 예정: {next_retrain.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"({wait_seconds / 3600:.1f}시간 후)"
                )

                # 대기
                await asyncio.sleep(wait_seconds)

                # 재학습 실행
                await self.retrain(deploy=True)

            except Exception as e:
                logger.error(f"스케줄러 오류: {e}")
                await asyncio.sleep(3600)  # 1시간 후 재시도

    def get_retrain_history(self, limit: int = 10) -> list:
        """재학습 기록 조회"""
        return self.retrain_history[-limit:]
