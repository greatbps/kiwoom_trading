#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/realtime_predictor.py

실시간 ML 추론 시스템
- Feature 생성 → ML 모델 예측 → 확신도 점수화
- 실시간 API 연동
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import pandas as pd
import numpy as np

from ai.feature_engineer import FeatureEngineer
from ai.ml_model_trainer import MLModelTrainer

logger = logging.getLogger(__name__)


class RealtimePredictor:
    """실시간 ML 예측기"""

    def __init__(
        self,
        model_trainer: MLModelTrainer,
        feature_engineer: FeatureEngineer,
        confidence_threshold: float = 60.0,  # 최소 확신도 (0~100)
    ):
        """
        초기화

        Args:
            model_trainer: 학습된 ML 모델
            feature_engineer: Feature 생성기
            confidence_threshold: 최소 확신도 임계값
        """
        self.model_trainer = model_trainer
        self.feature_engineer = feature_engineer
        self.confidence_threshold = confidence_threshold

        # 통계
        self.total_predictions = 0
        self.high_confidence_predictions = 0

    async def predict_signal(
        self,
        symbol: str,
        price_data: pd.DataFrame,
        **kwargs
    ) -> Tuple[bool, float, Dict]:
        """
        실시간 시그널 예측

        Args:
            symbol: 종목 코드
            price_data: 가격 데이터 (OHLCV)
            **kwargs: 추가 데이터 (수급, 섹터 등)

        Returns:
            (시그널 여부, 확신도, 상세 정보)
        """
        try:
            # 1. Feature 생성
            features = await self.feature_engineer.generate_features(
                price_data,
                **kwargs
            )

            # Feature를 DataFrame으로 변환
            feature_dict = {
                'rsi_14': features.rsi_14,
                'ema_5': features.ema_5,
                'ema_20': features.ema_20,
                'ema_60': features.ema_60,
                'macd': features.macd,
                'macd_signal': features.macd_signal,
                'macd_histogram': features.macd_histogram,
                'bb_upper': features.bb_upper,
                'bb_middle': features.bb_middle,
                'bb_lower': features.bb_lower,
                'bb_width': features.bb_width,
                'supertrend': features.supertrend,
                'supertrend_direction': features.supertrend_direction,
                'vwap': features.vwap,
                'price_to_vwap': features.price_to_vwap,
                'foreign_net_buy_ratio': features.foreign_net_buy_ratio,
                'inst_net_buy_ratio': features.inst_net_buy_ratio,
                'volume_ratio': features.volume_ratio,
                'atr_14': features.atr_14,
                'stddev_20': features.stddev_20,
                'volatility_ratio': features.volatility_ratio,
                'kospi_change': features.kospi_change,
                'kosdaq_change': features.kosdaq_change,
                'sector_strength': features.sector_strength,
                'recent_5_bullish_ratio': features.recent_5_bullish_ratio,
                'volume_surge_count': features.volume_surge_count,
            }

            X = pd.DataFrame([feature_dict])

            # 2. ML 모델 예측
            confidence_scores = self.model_trainer.predict_confidence(X)
            confidence = float(confidence_scores[0])

            # 3. 통계 업데이트
            self.total_predictions += 1
            if confidence >= self.confidence_threshold:
                self.high_confidence_predictions += 1

            # 4. 시그널 판정
            signal = confidence >= self.confidence_threshold

            # 5. 상세 정보
            details = {
                'symbol': symbol,
                'confidence': confidence,
                'threshold': self.confidence_threshold,
                'signal': signal,
                'timestamp': datetime.now().isoformat(),
                'top_features': self._get_top_features(feature_dict),
                'stats': {
                    'total_predictions': self.total_predictions,
                    'high_confidence_ratio': (
                        self.high_confidence_predictions / self.total_predictions
                        if self.total_predictions > 0 else 0.0
                    ),
                },
            }

            logger.info(
                f"[Prediction] {symbol}: Signal={signal}, "
                f"Confidence={confidence:.1f}%"
            )

            return signal, confidence, details

        except Exception as e:
            logger.error(f"예측 실패 ({symbol}): {e}")
            return False, 0.0, {'error': str(e)}

    def _get_top_features(
        self,
        feature_dict: Dict[str, float],
        top_n: int = 5
    ) -> List[Tuple[str, float]]:
        """상위 Feature 추출"""
        # Feature 중요도가 있으면 사용, 없으면 값 기준 정렬
        try:
            importance_df = self.model_trainer.get_feature_importance(top_n=top_n)
            top_features = [
                (row['feature'], feature_dict.get(row['feature'], 0.0))
                for _, row in importance_df.iterrows()
            ]
        except:
            # 중요도 없으면 절대값 기준
            sorted_features = sorted(
                feature_dict.items(),
                key=lambda x: abs(x[1]),
                reverse=True
            )
            top_features = sorted_features[:top_n]

        return top_features

    async def batch_predict(
        self,
        symbols_data: Dict[str, pd.DataFrame],
        **kwargs
    ) -> Dict[str, Tuple[bool, float, Dict]]:
        """
        여러 종목 배치 예측

        Args:
            symbols_data: {종목코드: 가격데이터}
            **kwargs: 추가 데이터

        Returns:
            {종목코드: (시그널, 확신도, 상세정보)}
        """
        tasks = []
        for symbol, price_data in symbols_data.items():
            task = self.predict_signal(symbol, price_data, **kwargs)
            tasks.append((symbol, task))

        results = {}
        for symbol, task in tasks:
            signal, confidence, details = await task
            results[symbol] = (signal, confidence, details)

        logger.info(f"배치 예측 완료: {len(results)}개 종목")
        return results

    def get_stats(self) -> Dict[str, any]:
        """통계 반환"""
        return {
            'total_predictions': self.total_predictions,
            'high_confidence_predictions': self.high_confidence_predictions,
            'high_confidence_ratio': (
                self.high_confidence_predictions / self.total_predictions
                if self.total_predictions > 0 else 0.0
            ),
            'confidence_threshold': self.confidence_threshold,
        }
