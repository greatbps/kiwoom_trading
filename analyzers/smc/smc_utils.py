"""
SMC 유틸리티 함수

- 스윙 포인트 탐지 (실시간 호환)
- 유동성 스윕 탐지
"""

from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
import numpy as np


@dataclass
class SwingPoint:
    """스윙 포인트 데이터"""
    index: int           # DataFrame 인덱스 위치
    price: float         # 가격
    type: str            # 'high' | 'low'
    timestamp: Optional[pd.Timestamp] = None

    def __repr__(self):
        return f"SwingPoint({self.type}@{self.price:.0f}, idx={self.index})"


@dataclass
class LiquiditySweep:
    """유동성 스윕 데이터"""
    index: int
    swept_level: float   # 스윕된 레벨
    sweep_high: float    # 스윕 고점
    sweep_low: float     # 스윕 저점
    direction: str       # 'bullish' | 'bearish' (스윕 후 예상 방향)

    def __repr__(self):
        return f"LiquiditySweep({self.direction}@{self.swept_level:.0f})"


def find_swing_points(
    df: pd.DataFrame,
    lookback: int = 5,
    min_swing_size_pct: float = 0.0
) -> List[SwingPoint]:
    """
    스윙 고점/저점 탐지 (실시간 호환)

    Args:
        df: OHLCV DataFrame (컬럼: open, high, low, close)
        lookback: 좌우 비교 봉 수 (기본 5)
        min_swing_size_pct: 최소 스윙 크기 (%) - 노이즈 제거용

    Returns:
        SwingPoint 리스트 (시간순 정렬)

    Note:
        - shift(-1) 미사용 (실시간 호환)
        - 마지막 봉은 미확정으로 제외
        - 과거 데이터만 참조
    """
    if df is None or len(df) < lookback * 2 + 1:
        return []

    # 컬럼명 소문자 통일
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if 'high' not in df.columns or 'low' not in df.columns:
        return []

    swings: List[SwingPoint] = []
    high_values = df['high'].values
    low_values = df['low'].values

    # 마지막 봉은 미확정이므로 제외
    end_idx = len(df) - 1

    for i in range(lookback, end_idx):
        # 스윙 고점 체크: 좌우 lookback개 봉보다 높아야 함
        is_swing_high = True
        current_high = high_values[i]

        for j in range(1, lookback + 1):
            # 좌측 비교
            if i - j >= 0 and current_high <= high_values[i - j]:
                is_swing_high = False
                break
            # 우측 비교 (확정된 봉만, 마지막 봉 제외)
            if i + j < end_idx and current_high <= high_values[i + j]:
                is_swing_high = False
                break

        # 스윙 저점 체크: 좌우 lookback개 봉보다 낮아야 함
        is_swing_low = True
        current_low = low_values[i]

        for j in range(1, lookback + 1):
            # 좌측 비교
            if i - j >= 0 and current_low >= low_values[i - j]:
                is_swing_low = False
                break
            # 우측 비교 (확정된 봉만)
            if i + j < end_idx and current_low >= low_values[i + j]:
                is_swing_low = False
                break

        # 최소 크기 필터
        if min_swing_size_pct > 0:
            avg_price = (current_high + current_low) / 2
            swing_size = abs(current_high - current_low) / avg_price * 100
            if swing_size < min_swing_size_pct:
                is_swing_high = False
                is_swing_low = False

        # 스윙 포인트 추가
        timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None

        if is_swing_high:
            swings.append(SwingPoint(
                index=i,
                price=current_high,
                type='high',
                timestamp=timestamp
            ))

        if is_swing_low:
            swings.append(SwingPoint(
                index=i,
                price=current_low,
                type='low',
                timestamp=timestamp
            ))

    # 시간순 정렬
    swings.sort(key=lambda x: x.index)

    return swings


def detect_liquidity_sweep(
    df: pd.DataFrame,
    swing_points: List[SwingPoint],
    lookback: int = 20,
    sweep_threshold_pct: float = 0.1
) -> Optional[LiquiditySweep]:
    """
    유동성 스윕 탐지 (스탑헌팅)

    유동성 스윕이란:
    - 이전 스윙 고점/저점을 잠깐 돌파(wick)
    - 봉 종가는 레벨 안으로 복귀
    - 스탑로스를 털어내고 반전하는 패턴

    Args:
        df: OHLCV DataFrame
        swing_points: 기존 스윙 포인트들
        lookback: 최근 N봉 내 스윕 탐지
        sweep_threshold_pct: 스윕 인정 최소 돌파율 (%)

    Returns:
        최근 발생한 LiquiditySweep 또는 None
    """
    if df is None or len(df) < 2 or len(swing_points) < 2:
        return None

    # 컬럼명 소문자 통일
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # 마지막 확정 봉 (현재 봉은 미확정)
    last_idx = len(df) - 2  # 마지막에서 두 번째 봉 (확정된 봉)
    if last_idx < 0:
        return None

    last_candle = df.iloc[last_idx]

    # 최근 스윙 포인트들 (lookback 범위 내)
    recent_swings = [
        sp for sp in swing_points
        if sp.index < last_idx and last_idx - sp.index <= lookback
    ]

    if not recent_swings:
        return None

    # 저점 스윕 체크 (Bullish Liquidity Sweep)
    # 이전 저점을 잠깐 하회하고 종가는 복귀
    recent_lows = [sp for sp in recent_swings if sp.type == 'low']
    if recent_lows:
        # 가장 최근 저점
        last_swing_low = max(recent_lows, key=lambda x: x.index)
        swing_low_price = last_swing_low.price

        # 스윕 조건: 저가가 스윙 저점 아래, 종가는 위
        if last_candle['low'] < swing_low_price and last_candle['close'] > swing_low_price:
            sweep_depth_pct = (swing_low_price - last_candle['low']) / swing_low_price * 100

            if sweep_depth_pct >= sweep_threshold_pct:
                return LiquiditySweep(
                    index=last_idx,
                    swept_level=swing_low_price,
                    sweep_high=last_candle['high'],
                    sweep_low=last_candle['low'],
                    direction='bullish'  # 저점 스윕 후 상승 예상
                )

    # 고점 스윕 체크 (Bearish Liquidity Sweep)
    # 이전 고점을 잠깐 상회하고 종가는 복귀
    recent_highs = [sp for sp in recent_swings if sp.type == 'high']
    if recent_highs:
        # 가장 최근 고점
        last_swing_high = max(recent_highs, key=lambda x: x.index)
        swing_high_price = last_swing_high.price

        # 스윕 조건: 고가가 스윙 고점 위, 종가는 아래
        if last_candle['high'] > swing_high_price and last_candle['close'] < swing_high_price:
            sweep_depth_pct = (last_candle['high'] - swing_high_price) / swing_high_price * 100

            if sweep_depth_pct >= sweep_threshold_pct:
                return LiquiditySweep(
                    index=last_idx,
                    swept_level=swing_high_price,
                    sweep_high=last_candle['high'],
                    sweep_low=last_candle['low'],
                    direction='bearish'  # 고점 스윕 후 하락 예상
                )

    return None


def get_recent_swing_levels(
    swing_points: List[SwingPoint],
    current_idx: int,
    lookback: int = 20
) -> dict:
    """
    최근 스윙 레벨 조회

    Args:
        swing_points: 스윙 포인트 리스트
        current_idx: 현재 인덱스
        lookback: 탐색 범위

    Returns:
        {'recent_highs': [prices], 'recent_lows': [prices]}
    """
    recent = [
        sp for sp in swing_points
        if sp.index < current_idx and current_idx - sp.index <= lookback
    ]

    return {
        'recent_highs': [sp.price for sp in recent if sp.type == 'high'],
        'recent_lows': [sp.price for sp in recent if sp.type == 'low']
    }
