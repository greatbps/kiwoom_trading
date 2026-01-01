#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Squeeze Momentum Indicator (LazyBear)
스퀴즈 모멘텀 지표 계산

References:
- Original: LazyBear's Squeeze Momentum Indicator
- Logic: Bollinger Bands + Keltner Channels + Linear Regression
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional


def calculate_squeeze_momentum(
    df: pd.DataFrame,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    kc_length: int = 20,
    kc_mult: float = 1.5,
    mom_length: int = 20
) -> pd.DataFrame:
    """
    스퀴즈 모멘텀 지표 계산

    Args:
        df: OHLCV 데이터프레임 (columns: open, high, low, close, volume)
        bb_length: Bollinger Bands 기간 (기본 20)
        bb_mult: Bollinger Bands 배수 (기본 2.0)
        kc_length: Keltner Channel 기간 (기본 20)
        kc_mult: Keltner Channel 배수 (기본 1.5)
        mom_length: 모멘텀 계산 기간 (기본 20)

    Returns:
        원본 df에 다음 컬럼 추가:
        - sqz_on: 스퀴즈 발생 (True/False)
        - sqz_off: 스퀴즈 해제 (True/False)
        - sqz_momentum: 모멘텀 값
        - sqz_signal: 매수/매도 시그널
        - sqz_color: 히스토그램 색상 (bright_green, dark_green, dark_red, bright_red)
    """
    df = df.copy()

    # 1. Bollinger Bands 계산
    bb_basis = df['close'].rolling(window=bb_length).mean()
    bb_dev = df['close'].rolling(window=bb_length).std()
    bb_upper = bb_basis + (bb_mult * bb_dev)
    bb_lower = bb_basis - (bb_mult * bb_dev)

    # 2. Keltner Channel 계산
    # True Range
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    # ATR (Average True Range)
    atr = true_range.rolling(window=kc_length).mean()

    # Keltner Channel
    kc_basis = df['close'].rolling(window=kc_length).mean()
    kc_upper = kc_basis + (kc_mult * atr)
    kc_lower = kc_basis - (kc_mult * atr)

    # 3. Squeeze 판단
    # BB가 KC 안에 들어가면 Squeeze On
    sqz_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    sqz_off = (bb_lower < kc_lower) & (bb_upper > kc_upper)

    # 4. 모멘텀 계산 (Linear Regression)
    # Highest high - Lowest low의 중간값
    highest_high = df['high'].rolling(window=kc_length).max()
    lowest_low = df['low'].rolling(window=kc_length).min()
    avg_hl = (highest_high + lowest_low) / 2
    avg_close_hl = (avg_hl + kc_basis) / 2

    # Linear Regression을 통한 모멘텀
    momentum = df['close'] - avg_close_hl

    # Linear Regression 계산 (간소화)
    sqz_momentum = momentum.rolling(window=mom_length).apply(
        lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) == mom_length else 0,
        raw=False
    )

    # 5. 시그널 생성
    sqz_signal = pd.Series('HOLD', index=df.index)
    sqz_color = pd.Series('gray', index=df.index)

    # 이전 값과 비교하여 색상 결정
    mom_diff = sqz_momentum.diff()

    # 밝은 녹색 (Bright Green): 모멘텀 > 0 이고 증가 중
    bright_green = (sqz_momentum > 0) & (mom_diff > 0)
    # 어두운 녹색 (Dark Green): 모멘텀 > 0 이고 감소 중
    dark_green = (sqz_momentum > 0) & (mom_diff <= 0)
    # 어두운 빨강 (Dark Red): 모멘텀 < 0 이고 감소 중
    dark_red = (sqz_momentum < 0) & (mom_diff < 0)
    # 밝은 빨강 (Bright Red): 모멘텀 < 0 이고 증가 중
    bright_red = (sqz_momentum < 0) & (mom_diff >= 0)

    sqz_color[bright_green] = 'bright_green'
    sqz_color[dark_green] = 'dark_green'
    sqz_color[dark_red] = 'dark_red'
    sqz_color[bright_red] = 'bright_red'

    # 6. 매수/매도 시그널
    # 매수: 빨강 → 밝은 녹색 전환 (모멘텀 가속)
    prev_color = sqz_color.shift(1)
    buy_signal = (prev_color.isin(['dark_red', 'bright_red'])) & (sqz_color == 'bright_green')
    # 또는 어두운 녹 → 밝은 녹 (재가속)
    buy_signal |= (prev_color == 'dark_green') & (sqz_color == 'bright_green')

    # 매도 (이익 중일 때만): 밝은 녹 → 어두운 녹 전환 (모멘텀 둔화)
    sell_signal = (prev_color == 'bright_green') & (sqz_color == 'dark_green')
    # 또는 녹 → 빨강 전환 (모멘텀 반전)
    sell_signal |= (sqz_color.isin(['dark_red', 'bright_red'])) & (prev_color.isin(['bright_green', 'dark_green']))

    sqz_signal[buy_signal] = 'BUY'
    sqz_signal[sell_signal] = 'SELL'

    # 결과 추가
    df['sqz_on'] = sqz_on
    df['sqz_off'] = sqz_off
    df['sqz_momentum'] = sqz_momentum
    df['sqz_signal'] = sqz_signal
    df['sqz_color'] = sqz_color

    # 추가 정보
    df['sqz_bb_upper'] = bb_upper
    df['sqz_bb_lower'] = bb_lower
    df['sqz_kc_upper'] = kc_upper
    df['sqz_kc_lower'] = kc_lower

    return df


def get_current_squeeze_signal(df: pd.DataFrame) -> dict:
    """
    현재 스퀴즈 모멘텀 시그널 반환

    Args:
        df: calculate_squeeze_momentum()로 계산된 데이터프레임

    Returns:
        현재 시그널 정보
    """
    if len(df) == 0:
        return {
            'signal': 'HOLD',
            'color': 'gray',
            'momentum': 0.0,
            'squeeze_on': False,
            'squeeze_off': False
        }

    latest = df.iloc[-1]

    return {
        'signal': latest.get('sqz_signal', 'HOLD'),
        'color': latest.get('sqz_color', 'gray'),
        'momentum': float(latest.get('sqz_momentum', 0.0)),
        'squeeze_on': bool(latest.get('sqz_on', False)),
        'squeeze_off': bool(latest.get('sqz_off', False)),
        'is_accelerating': latest.get('sqz_color') in ['bright_green', 'bright_red'],
        'is_decelerating': latest.get('sqz_color') in ['dark_green', 'dark_red']
    }


def should_enter_trade(df: pd.DataFrame, min_momentum: float = 0.0) -> bool:
    """
    진입 조건 확인

    Args:
        df: 스퀴즈 모멘텀이 계산된 데이터프레임
        min_momentum: 최소 모멘텀 값 (기본 0)

    Returns:
        진입 가능 여부
    """
    signal = get_current_squeeze_signal(df)

    # 조건:
    # 1. 매수 시그널
    # 2. 밝은 녹색 (모멘텀 가속)
    # 3. 모멘텀이 최소값 이상
    return (
        signal['signal'] == 'BUY' and
        signal['color'] == 'bright_green' and
        signal['momentum'] >= min_momentum
    )


def should_exit_trade(df: pd.DataFrame, current_profit_rate: float = 0.0) -> Tuple[bool, str]:
    """
    청산 조건 확인 (이익 중일 때만)

    Args:
        df: 스퀴즈 모멘텀이 계산된 데이터프레임
        current_profit_rate: 현재 수익률 (%)

    Returns:
        (청산 여부, 청산 사유)
    """
    if current_profit_rate <= 0:
        return False, ""  # 손실 중에는 스퀴즈 무시

    signal = get_current_squeeze_signal(df)

    # 어두운 녹색 시작 (모멘텀 둔화) - 부분 익절
    if signal['color'] == 'dark_green' and current_profit_rate > 1.0:
        return True, "PARTIAL_PROFIT"

    # 빨간색 전환 (모멘텀 반전) - 전량 익절
    if signal['color'] in ['dark_red', 'bright_red'] and current_profit_rate > 0.5:
        return True, "FULL_PROFIT"

    return False, ""
