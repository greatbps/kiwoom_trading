#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Squeeze Momentum Indicator (LazyBear) - 실시간 트레이딩용
스퀴즈 모멘텀 지표 계산 (분봉 데이터 지원)

실제 거래 분석 기반 전략:
- Bright Green (밝은 녹색): 모멘텀 가속 → 진입/보유 신호
- Dark Green (어두운 녹색): 모멘텀 감속 → 부분 익절 신호
- Dark Red (어두운 빨강): 모멘텀 하락 가속 → 전량 청산
- Bright Red (밝은 빨강): 모멘텀 하락 감속 → 관망

References:
- Original: LazyBear's Squeeze Momentum Indicator
- Logic: Bollinger Bands + Keltner Channels + Linear Regression
- Real Trade Analysis: 휴림로봇 (Dark_green 부분익절 성공), 아이티센글로벌 (Bright_green 조기청산 실패)
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict
from rich.console import Console

console = Console()


def calculate_squeeze_momentum(
    df: pd.DataFrame,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    kc_length: int = 20,
    kc_mult: float = 1.5,
    mom_length: int = 20
) -> pd.DataFrame:
    """
    스퀴즈 모멘텀 지표 계산 (분봉/일봉 모두 지원)

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


def get_current_squeeze_signal(df: pd.DataFrame) -> Dict:
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
            'squeeze_off': False,
            'is_accelerating': False,
            'is_decelerating': False
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


def should_enter_trade(df: pd.DataFrame, min_momentum: float = 0.0) -> Tuple[bool, str]:
    """
    진입 조건 확인 (실제 거래 분석 기반)

    Args:
        df: 스퀴즈 모멘텀이 계산된 데이터프레임
        min_momentum: 최소 모멘텀 값 (기본 0)

    Returns:
        (진입 가능 여부, 사유)
    """
    signal = get_current_squeeze_signal(df)

    # 조건:
    # 1. Bright Green (모멘텀 가속)
    # 2. 모멘텀이 최소값 이상
    if signal['color'] == 'bright_green' and signal['momentum'] >= min_momentum:
        return True, f"Bright Green 진입 신호 (모멘텀: {signal['momentum']:.2f})"

    return False, f"진입 조건 미충족 (색상: {signal['color']}, 모멘텀: {signal['momentum']:.2f})"


def should_exit_trade(df: pd.DataFrame, current_profit_rate: float = 0.0) -> Tuple[bool, str, str]:
    """
    청산 조건 확인 (이익 중일 때만) - 실제 거래 분석 기반

    Args:
        df: 스퀴즈 모멘텀이 계산된 데이터프레임
        current_profit_rate: 현재 수익률 (%)

    Returns:
        (청산 여부, 청산 사유, 청산 타입: PARTIAL/FULL)
    """
    if current_profit_rate <= 0:
        return False, "", ""  # 손실 중에는 스퀴즈 무시

    signal = get_current_squeeze_signal(df)

    # Bright Green: 절대 매도 금지! (아이티센글로벌 교훈)
    if signal['color'] == 'bright_green':
        return False, "Bright Green - 보유 필수", ""

    # Dark Green: 부분 익절 시작 (휴림로봇 성공 사례)
    if signal['color'] == 'dark_green' and current_profit_rate > 1.0:
        return True, "Dark Green 감속 - 부분 익절", "PARTIAL"

    # Red (dark_red/bright_red): 전량 청산
    if signal['color'] in ['dark_red', 'bright_red'] and current_profit_rate > 0.5:
        return True, f"{signal['color']} 모멘텀 반전 - 전량 청산", "FULL"

    return False, "", ""


def check_squeeze_momentum_filter(df: pd.DataFrame, for_entry: bool = True) -> Tuple[bool, str, Dict]:
    """
    스퀴즈 모멘텀 필터 체크 (SignalOrchestrator 통합용)

    Args:
        df: OHLCV 데이터프레임
        for_entry: True면 진입 필터, False면 청산 필터

    Returns:
        (통과 여부, 사유, 상세 정보)
    """
    try:
        # 데이터 검증
        if df is None or len(df) < 50:
            return False, "데이터 부족 (50봉 미만)", {}

        # 스퀴즈 모멘텀 계산
        df = calculate_squeeze_momentum(df)
        signal = get_current_squeeze_signal(df)

        details = {
            'signal': signal['signal'],
            'color': signal['color'],
            'momentum': signal['momentum'],
            'squeeze_on': signal['squeeze_on'],
            'is_accelerating': signal['is_accelerating']
        }

        if for_entry:
            # 진입 필터: Bright Green만 허용
            if signal['color'] == 'bright_green':
                return True, f"Squeeze: Bright Green (모멘텀 {signal['momentum']:.2f})", details
            else:
                return False, f"Squeeze: {signal['color']} (진입 불가)", details
        else:
            # 청산 필터: Dark Green/Red 확인
            if signal['color'] == 'dark_green':
                return True, "Squeeze: Dark Green (부분 익절 고려)", details
            elif signal['color'] in ['dark_red', 'bright_red']:
                return True, f"Squeeze: {signal['color']} (전량 청산 권장)", details
            else:
                return False, f"Squeeze: {signal['color']} (보유 권장)", details

    except Exception as e:
        console.print(f"[red]⚠️ Squeeze Momentum 필터 오류: {e}[/red]")
        return False, f"계산 오류: {str(e)}", {}


if __name__ == "__main__":
    """테스트 코드"""
    import yfinance as yf

    print("=" * 80)
    print("🧪 Squeeze Momentum (LazyBear) 실시간 트레이딩 테스트")
    print("=" * 80)

    # 테스트 종목
    test_ticker = "005930.KS"  # 삼성전자

    print(f"\n종목: {test_ticker}")

    # 데이터 다운로드 (5분봉)
    df = yf.download(test_ticker, period="5d", interval="5m", progress=False)

    if df is None or len(df) < 50:
        print("  데이터 부족")
    else:
        # 컬럼명 소문자 변환
        df.columns = df.columns.str.lower()

        # 스퀴즈 모멘텀 계산
        df = calculate_squeeze_momentum(df)

        # 현재 시그널 확인
        signal = get_current_squeeze_signal(df)

        print("\n현재 시그널:")
        print(f"  - 색상: {signal['color']}")
        print(f"  - 신호: {signal['signal']}")
        print(f"  - 모멘텀: {signal['momentum']:.4f}")
        print(f"  - Squeeze ON: {signal['squeeze_on']}")
        print(f"  - 가속: {signal['is_accelerating']}")
        print(f"  - 감속: {signal['is_decelerating']}")

        # 진입 조건 체크
        can_enter, enter_reason = should_enter_trade(df)
        print(f"\n진입 조건: {can_enter}")
        print(f"  사유: {enter_reason}")

        # 청산 조건 체크 (가정: 현재 +2% 수익)
        should_exit, exit_reason, exit_type = should_exit_trade(df, current_profit_rate=2.0)
        print(f"\n청산 조건 (+2% 수익 가정): {should_exit}")
        if should_exit:
            print(f"  사유: {exit_reason}")
            print(f"  타입: {exit_type}")

        # 최근 5봉 추세
        print("\n최근 5봉 추세:")
        recent = df.tail(5)[['close', 'sqz_color', 'sqz_momentum', 'sqz_signal']]
        for idx, row in recent.iterrows():
            color_emoji = {
                'bright_green': '🟢',
                'dark_green': '🟡',
                'dark_red': '🔴',
                'bright_red': '🟠',
                'gray': '⚪'
            }.get(row['sqz_color'], '⚪')

            print(f"  {idx} | {color_emoji} {row['sqz_color']:15} | 모멘텀: {row['sqz_momentum']:>8.2f} | {row['sqz_signal']}")

    print("\n" + "=" * 80)
