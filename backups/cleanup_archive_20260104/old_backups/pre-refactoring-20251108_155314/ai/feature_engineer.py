#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/feature_engineer.py

Advanced Feature Engineering for ML Trading
- 기술 지표 Feature (RSI, Supertrend, EMA, MACD, Bollinger Bands)
- 수급 Feature (외국인/기관 순매수, 거래대금 변화율)
- 변동성 Feature (ATR, stddev, 일중 고저폭)
- 시장 Feature (KOSPI200 변화율, 섹터 강도)
- 패턴 Feature (최근 5봉 양봉비율, 거래량 급등)
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# Local imports
import sys
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class FeatureSet:
    """Feature 데이터셋"""
    symbol: str
    timestamp: datetime

    # 기술 지표
    rsi_14: float = 0.0
    rsi_7: float = 0.0
    supertrend: float = 0.0
    supertrend_direction: int = 0  # 1: 상승, -1: 하락
    ema_5: float = 0.0
    ema_20: float = 0.0
    ema_60: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0

    # VWAP 관련
    vwap: float = 0.0
    price_to_vwap_ratio: float = 0.0
    distance_from_vwap: float = 0.0

    # 수급
    foreign_net_buy: float = 0.0      # 외국인 순매수
    institution_net_buy: float = 0.0  # 기관 순매수
    trading_value_change: float = 0.0  # 거래대금 변화율

    # 변동성
    atr_14: float = 0.0  # Average True Range
    stddev_20: float = 0.0  # 표준편차
    intraday_range: float = 0.0  # 일중 고저폭
    volatility_rank: float = 0.0  # 변동성 순위 (0~1)

    # 시장
    kospi_change: float = 0.0  # KOSPI 변화율
    kosdaq_change: float = 0.0  # KOSDAQ 변화율
    sector_strength: float = 0.0  # 섹터 강도
    market_breadth: float = 0.0  # 시장 폭 (상승/하락 종목 비율)

    # 패턴
    candle_pattern_score: float = 0.0  # 캔들 패턴 점수
    recent_5_bullish_ratio: float = 0.0  # 최근 5봉 양봉 비율
    volume_surge_count: int = 0  # 거래량 급등 횟수 (최근 10일)

    # 가격 관련
    price: float = 0.0
    volume: int = 0
    change_rate: float = 0.0
    volume_ratio: float = 0.0  # 평균 거래량 대비 비율

    # 고급 지표
    money_flow_index: float = 0.0  # MFI (Money Flow Index)
    williams_r: float = 0.0  # Williams %R
    stochastic_k: float = 0.0  # Stochastic %K
    stochastic_d: float = 0.0  # Stochastic %D

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def to_ml_features(self) -> np.ndarray:
        """ML 모델 입력용 Feature 벡터"""
        # Symbol, timestamp 제외
        features = []
        for key, value in self.to_dict().items():
            if key not in ['symbol', 'timestamp']:
                features.append(float(value))
        return np.array(features)


class FeatureEngineer:
    """
    Feature 엔지니어링 시스템

    주요 기능:
    - 기술 지표 계산
    - 수급 지표 계산
    - 변동성 지표 계산
    - 시장 지표 계산
    - Feature 정규화 및 스케일링

    Example:
        engineer = FeatureEngineer()

        # OHLCV 데이터에서 Feature 추출
        features = engineer.extract_features(df, symbol="AAPL")

        # ML 입력용 벡터
        X = features.to_ml_features()
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            logger: 로거
        """
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    def extract_features(
        self,
        df: pd.DataFrame,
        symbol: str,
        market_data: Optional[pd.DataFrame] = None,
    ) -> FeatureSet:
        """
        OHLCV 데이터에서 Feature 추출

        Args:
            df: OHLCV 데이터 (컬럼: open, high, low, close, volume)
            symbol: 종목 코드
            market_data: 시장 지수 데이터 (선택)

        Returns:
            FeatureSet
        """
        try:
            if len(df) < 60:
                raise ValueError(f"Insufficient data: {len(df)} rows (need at least 60)")

            # 기본 Feature 세트 생성
            features = FeatureSet(
                symbol=symbol,
                timestamp=datetime.now(),
            )

            # 1. 기술 지표
            features = self._add_technical_indicators(features, df)

            # 2. 수급 지표
            features = self._add_supply_demand_features(features, df)

            # 3. 변동성 지표
            features = self._add_volatility_features(features, df)

            # 4. 시장 지표
            if market_data is not None:
                features = self._add_market_features(features, df, market_data)

            # 5. 패턴 지표
            features = self._add_pattern_features(features, df)

            # 6. 현재 가격 데이터
            latest = df.iloc[-1]
            features.price = float(latest['close'])
            features.volume = int(latest['volume'])
            features.change_rate = float((latest['close'] / df.iloc[-2]['close']) - 1) if len(df) > 1 else 0.0

            return features

        except Exception as e:
            self.logger.error(f"Feature extraction failed for {symbol}: {e}")
            raise

    def _add_technical_indicators(
        self,
        features: FeatureSet,
        df: pd.DataFrame
    ) -> FeatureSet:
        """기술 지표 추가"""

        # RSI (Relative Strength Index)
        features.rsi_14 = self._calculate_rsi(df['close'], period=14)
        features.rsi_7 = self._calculate_rsi(df['close'], period=7)

        # EMA (Exponential Moving Average)
        features.ema_5 = df['close'].ewm(span=5).mean().iloc[-1]
        features.ema_20 = df['close'].ewm(span=20).mean().iloc[-1]
        features.ema_60 = df['close'].ewm(span=60).mean().iloc[-1]

        # MACD
        macd_result = self._calculate_macd(df['close'])
        features.macd = macd_result['macd']
        features.macd_signal = macd_result['signal']
        features.macd_histogram = macd_result['histogram']

        # Bollinger Bands
        bb_result = self._calculate_bollinger_bands(df['close'])
        features.bb_upper = bb_result['upper']
        features.bb_middle = bb_result['middle']
        features.bb_lower = bb_result['lower']
        features.bb_width = (bb_result['upper'] - bb_result['lower']) / bb_result['middle']

        # Supertrend (간소화 버전)
        supertrend_result = self._calculate_supertrend(df)
        features.supertrend = supertrend_result['value']
        features.supertrend_direction = supertrend_result['direction']

        # VWAP
        features.vwap = self._calculate_vwap(df)
        current_price = df['close'].iloc[-1]
        features.price_to_vwap_ratio = current_price / features.vwap if features.vwap > 0 else 1.0
        features.distance_from_vwap = (current_price - features.vwap) / features.vwap if features.vwap > 0 else 0.0

        # MFI (Money Flow Index)
        features.money_flow_index = self._calculate_mfi(df)

        # Williams %R
        features.williams_r = self._calculate_williams_r(df)

        # Stochastic
        stoch_result = self._calculate_stochastic(df)
        features.stochastic_k = stoch_result['k']
        features.stochastic_d = stoch_result['d']

        return features

    def _add_supply_demand_features(
        self,
        features: FeatureSet,
        df: pd.DataFrame
    ) -> FeatureSet:
        """수급 지표 추가 (더미 데이터)"""
        # TODO: 실제 수급 데이터 연동 필요
        features.foreign_net_buy = 0.0
        features.institution_net_buy = 0.0

        # 거래대금 변화율
        if 'volume' in df.columns and len(df) > 1:
            recent_value = df['close'].iloc[-1] * df['volume'].iloc[-1]
            prev_value = df['close'].iloc[-2] * df['volume'].iloc[-2]
            features.trading_value_change = (recent_value / prev_value - 1) if prev_value > 0 else 0.0

        return features

    def _add_volatility_features(
        self,
        features: FeatureSet,
        df: pd.DataFrame
    ) -> FeatureSet:
        """변동성 지표 추가"""

        # ATR (Average True Range)
        features.atr_14 = self._calculate_atr(df, period=14)

        # 표준편차
        features.stddev_20 = df['close'].tail(20).std()

        # 일중 고저폭
        latest = df.iloc[-1]
        features.intraday_range = (latest['high'] - latest['low']) / latest['close']

        # 변동성 순위 (최근 60일 대비)
        recent_volatility = df['close'].tail(60).std()
        all_volatility = df['close'].std()
        features.volatility_rank = recent_volatility / all_volatility if all_volatility > 0 else 0.5

        return features

    def _add_market_features(
        self,
        features: FeatureSet,
        df: pd.DataFrame,
        market_data: pd.DataFrame
    ) -> FeatureSet:
        """시장 지표 추가"""
        # TODO: 실제 시장 데이터 연동
        features.kospi_change = 0.0
        features.kosdaq_change = 0.0
        features.sector_strength = 0.0
        features.market_breadth = 0.5

        return features

    def _add_pattern_features(
        self,
        features: FeatureSet,
        df: pd.DataFrame
    ) -> FeatureSet:
        """패턴 지표 추가"""

        # 최근 5봉 양봉 비율
        recent_5 = df.tail(5)
        bullish_count = sum(recent_5['close'] > recent_5['open'])
        features.recent_5_bullish_ratio = bullish_count / 5.0

        # 거래량 급등 횟수 (평균 대비 2배 이상)
        recent_10 = df.tail(10)
        avg_volume = recent_10['volume'].mean()
        surge_count = sum(recent_10['volume'] > avg_volume * 2)
        features.volume_surge_count = surge_count

        # 거래량 비율
        current_volume = df['volume'].iloc[-1]
        features.volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        # 캔들 패턴 점수 (간단한 버전)
        features.candle_pattern_score = self._calculate_candle_pattern_score(df)

        return features

    # =====================================================
    # 지표 계산 함수들
    # =====================================================

    def _calculate_rsi(self, series: pd.Series, period: int = 14) -> float:
        """RSI 계산"""
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _calculate_macd(
        self,
        series: pd.Series,
        fast=12,
        slow=26,
        signal=9
    ) -> Dict[str, float]:
        """MACD 계산"""
        ema_fast = series.ewm(span=fast).mean()
        ema_slow = series.ewm(span=slow).mean()

        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal).mean()
        histogram = macd_line - signal_line

        return {
            'macd': float(macd_line.iloc[-1]),
            'signal': float(signal_line.iloc[-1]),
            'histogram': float(histogram.iloc[-1]),
        }

    def _calculate_bollinger_bands(
        self,
        series: pd.Series,
        period: int = 20,
        std_dev: float = 2.0
    ) -> Dict[str, float]:
        """Bollinger Bands 계산"""
        middle = series.rolling(window=period).mean()
        std = series.rolling(window=period).std()

        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)

        return {
            'upper': float(upper.iloc[-1]),
            'middle': float(middle.iloc[-1]),
            'lower': float(lower.iloc[-1]),
        }

    def _calculate_supertrend(
        self,
        df: pd.DataFrame,
        period: int = 10,
        multiplier: float = 3.0
    ) -> Dict[str, float]:
        """Supertrend 계산 (간소화)"""
        # ATR 계산
        atr = self._calculate_atr(df, period)

        # 기본 밴드
        hl_avg = (df['high'] + df['low']) / 2
        upper_band = hl_avg + (multiplier * atr)
        lower_band = hl_avg - (multiplier * atr)

        # 현재 가격과 비교
        current_price = df['close'].iloc[-1]
        upper_band_value = upper_band.iloc[-1]
        lower_band_value = lower_band.iloc[-1]

        if current_price > upper_band_value:
            direction = 1  # 상승
            value = lower_band_value
        else:
            direction = -1  # 하락
            value = upper_band_value

        return {
            'value': float(value),
            'direction': direction,
        }

    def _calculate_vwap(self, df: pd.DataFrame) -> float:
        """VWAP 계산"""
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap = (typical_price * df['volume']).sum() / df['volume'].sum()
        return float(vwap)

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR (Average True Range) 계산"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())

        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()

        return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0

    def _calculate_mfi(
        self,
        df: pd.DataFrame,
        period: int = 14
    ) -> float:
        """MFI (Money Flow Index) 계산"""
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        money_flow = typical_price * df['volume']

        # Positive/Negative money flow
        delta = typical_price.diff()
        positive_flow = money_flow.where(delta > 0, 0).rolling(window=period).sum()
        negative_flow = money_flow.where(delta < 0, 0).rolling(window=period).sum()

        mfi_ratio = positive_flow / negative_flow
        mfi = 100 - (100 / (1 + mfi_ratio))

        return float(mfi.iloc[-1]) if not pd.isna(mfi.iloc[-1]) else 50.0

    def _calculate_williams_r(
        self,
        df: pd.DataFrame,
        period: int = 14
    ) -> float:
        """Williams %R 계산"""
        highest_high = df['high'].rolling(window=period).max()
        lowest_low = df['low'].rolling(window=period).min()

        williams_r = -100 * (highest_high - df['close']) / (highest_high - lowest_low)

        return float(williams_r.iloc[-1]) if not pd.isna(williams_r.iloc[-1]) else -50.0

    def _calculate_stochastic(
        self,
        df: pd.DataFrame,
        period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3
    ) -> Dict[str, float]:
        """Stochastic 계산"""
        lowest_low = df['low'].rolling(window=period).min()
        highest_high = df['high'].rolling(window=period).max()

        k = 100 * (df['close'] - lowest_low) / (highest_high - lowest_low)
        k = k.rolling(window=smooth_k).mean()
        d = k.rolling(window=smooth_d).mean()

        return {
            'k': float(k.iloc[-1]) if not pd.isna(k.iloc[-1]) else 50.0,
            'd': float(d.iloc[-1]) if not pd.isna(d.iloc[-1]) else 50.0,
        }

    def _calculate_candle_pattern_score(self, df: pd.DataFrame) -> float:
        """캔들 패턴 점수 계산 (간단한 버전)"""
        # 최근 1봉 분석
        latest = df.iloc[-1]

        score = 0.0

        # 양봉/음봉
        body = latest['close'] - latest['open']
        if body > 0:
            score += 0.5  # 양봉

        # 몸통 대비 전체 범위
        total_range = latest['high'] - latest['low']
        if total_range > 0:
            body_ratio = abs(body) / total_range
            score += body_ratio * 0.5  # 몸통이 클수록 높은 점수

        return min(1.0, score)


def generate_sample_data(days: int = 100) -> pd.DataFrame:
    """샘플 OHLCV 데이터 생성 (테스트용)"""
    dates = pd.date_range(end=datetime.now(), periods=days)

    # 랜덤 가격 생성
    base_price = 100
    returns = np.random.normal(0.001, 0.02, days)
    prices = base_price * np.exp(np.cumsum(returns))

    # OHLCV 생성
    df = pd.DataFrame({
        'date': dates,
        'open': prices * (1 + np.random.uniform(-0.01, 0.01, days)),
        'high': prices * (1 + np.random.uniform(0.00, 0.03, days)),
        'low': prices * (1 + np.random.uniform(-0.03, 0.00, days)),
        'close': prices,
        'volume': np.random.randint(1000000, 10000000, days),
    })

    return df
