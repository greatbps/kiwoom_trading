# indicators.py
"""
기술적 지표 계산 모듈

포함 지표:
- RSI (Relative Strength Index)
- Stochastic Oscillator
- Squeeze Momentum
- Volume Momentum
- Price Velocity
- ATR (Average True Range)
- Bollinger Bands
"""
import pandas as pd
import numpy as np
from typing import Dict, Any


def add_momentum_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """모멘텀 지표 추가

    Args:
        df: OHLCV DataFrame

    Returns:
        지표가 추가된 DataFrame
    """
    df = df.copy()

    # RSI
    df = calculate_rsi(df, period=14)

    # Stochastic
    df = calculate_stochastic(df, k_period=14, d_period=3)

    # Volume momentum (거래량 모멘텀)
    df = calculate_volume_momentum(df, period=10)

    # Price velocity (가격 변화율)
    df = calculate_price_velocity(df, period=5)

    return df


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """RSI 계산

    Args:
        df: OHLCV DataFrame
        period: RSI 기간

    Returns:
        'rsi' 컬럼이 추가된 DataFrame
    """
    df = df.copy()

    if 'close' not in df.columns or len(df) < period + 1:
        df['rsi'] = 50.0
        return df

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    df['rsi'] = rsi.fillna(50)

    return df


def calculate_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """Stochastic Oscillator 계산

    Args:
        df: OHLCV DataFrame
        k_period: %K 기간
        d_period: %D 기간

    Returns:
        'stoch_k', 'stoch_d' 컬럼이 추가된 DataFrame
    """
    df = df.copy()

    if 'close' not in df.columns or 'high' not in df.columns or 'low' not in df.columns:
        df['stoch_k'] = 50.0
        df['stoch_d'] = 50.0
        return df

    if len(df) < k_period:
        df['stoch_k'] = 50.0
        df['stoch_d'] = 50.0
        return df

    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()

    stoch_k = 100 * (df['close'] - low_min) / (high_max - low_min).replace(0, np.nan)
    stoch_d = stoch_k.rolling(window=d_period).mean()

    df['stoch_k'] = stoch_k.fillna(50)
    df['stoch_d'] = stoch_d.fillna(50)

    return df


def calculate_volume_momentum(df: pd.DataFrame, period: int = 10) -> pd.DataFrame:
    """거래량 모멘텀 계산 (z-score 기반)

    Args:
        df: OHLCV DataFrame
        period: 계산 기간

    Returns:
        'volume_momentum' 컬럼이 추가된 DataFrame
    """
    df = df.copy()

    if 'volume' not in df.columns or len(df) < period:
        df['volume_momentum'] = 0.0
        return df

    vol = df['volume'].astype(float)
    vol_mean = vol.rolling(window=period).mean()
    vol_std = vol.rolling(window=period).std().replace(0, np.nan)

    # z-score 계산
    vol_momentum = ((vol - vol_mean) / vol_std).fillna(0) * 10  # 스케일 조정

    df['volume_momentum'] = vol_momentum

    return df


def calculate_price_velocity(df: pd.DataFrame, period: int = 5) -> pd.DataFrame:
    """가격 변화율 (velocity) 계산

    Args:
        df: OHLCV DataFrame
        period: 계산 기간

    Returns:
        'price_velocity' 컬럼이 추가된 DataFrame
    """
    df = df.copy()

    if 'close' not in df.columns or len(df) < period + 1:
        df['price_velocity'] = 0.0
        return df

    # period 기간 동안의 퍼센트 변화
    price_change = df['close'].pct_change(periods=period) * 100
    df['price_velocity'] = price_change.fillna(0)

    return df


def calculate_improved_squeeze_momentum(df: pd.DataFrame, bb_length: int = 20, bb_mult: float = 2.0,
                                       kc_length: int = 20, kc_mult: float = 1.5) -> pd.DataFrame:
    """Improved Squeeze Momentum 계산

    Bollinger Bands와 Keltner Channels의 관계를 분석하여
    시장의 squeeze (압축) 상태를 판단합니다.

    Args:
        df: OHLCV DataFrame
        bb_length: Bollinger Bands 기간
        bb_mult: Bollinger Bands 표준편차 배수
        kc_length: Keltner Channels 기간
        kc_mult: Keltner Channels ATR 배수

    Returns:
        'squeeze_signal', 'momentum' 컬럼이 추가된 DataFrame
    """
    df = df.copy()

    if len(df) < max(bb_length, kc_length):
        df['squeeze_signal'] = 'HOLD'
        df['momentum'] = 0.0
        return df

    # Bollinger Bands
    bb_basis = df['close'].rolling(window=bb_length).mean()
    bb_std = df['close'].rolling(window=bb_length).std()
    bb_upper = bb_basis + (bb_std * bb_mult)
    bb_lower = bb_basis - (bb_std * bb_mult)

    # Keltner Channels (ATR 기반)
    df = calculate_atr(df, period=kc_length)
    kc_basis = df['close'].rolling(window=kc_length).mean()
    kc_upper = kc_basis + (df['atr'] * kc_mult)
    kc_lower = kc_basis - (df['atr'] * kc_mult)

    # Squeeze 상태: BB가 KC 내부에 있을 때
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Momentum 계산 (선형 회귀 기반)
    momentum = calculate_momentum_linreg(df['close'], length=bb_length)

    # 신호 생성
    signals = []
    for i in range(len(df)):
        if i < bb_length:
            signals.append('HOLD')
            continue

        mom = momentum.iloc[i]
        prev_mom = momentum.iloc[i-1] if i > 0 else 0

        if mom > 0 and mom > prev_mom:
            signals.append('STRONG_BUY')
        elif mom > 0:
            signals.append('BUY')
        elif mom < 0 and mom < prev_mom:
            signals.append('STRONG_SELL')
        elif mom < 0:
            signals.append('SELL')
        else:
            signals.append('HOLD')

    df['squeeze_signal'] = signals
    df['momentum'] = momentum

    return df


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average True Range 계산

    Args:
        df: OHLCV DataFrame
        period: ATR 기간

    Returns:
        'atr' 컬럼이 추가된 DataFrame
    """
    df = df.copy()

    if len(df) < period:
        df['atr'] = 0.0
        return df

    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())

    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()

    df['atr'] = atr.fillna(0)

    return df


def calculate_momentum_linreg(series: pd.Series, length: int = 20) -> pd.Series:
    """선형 회귀 기반 모멘텀 계산

    Args:
        series: 가격 Series
        length: 계산 기간

    Returns:
        모멘텀 Series
    """
    momentum = pd.Series(index=series.index, dtype=float)

    for i in range(length, len(series)):
        y = series.iloc[i-length:i].values
        x = np.arange(length)

        # 선형 회귀
        slope, intercept = np.polyfit(x, y, 1)

        # 현재 값과 회귀선 값의 차이
        predicted = slope * (length - 1) + intercept
        actual = y[-1]
        momentum.iloc[i] = actual - predicted

    return momentum.fillna(0)


def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands 계산

    Args:
        df: OHLCV DataFrame
        period: 기간
        std: 표준편차 배수

    Returns:
        'bb_upper', 'bb_middle', 'bb_lower' 컬럼이 추가된 DataFrame
    """
    df = df.copy()

    if len(df) < period:
        df['bb_upper'] = 0.0
        df['bb_middle'] = 0.0
        df['bb_lower'] = 0.0
        return df

    sma = df['close'].rolling(window=period).mean()
    rolling_std = df['close'].rolling(window=period).std()

    df['bb_middle'] = sma
    df['bb_upper'] = sma + (rolling_std * std)
    df['bb_lower'] = sma - (rolling_std * std)

    return df.fillna(0)


if __name__ == "__main__":
    # 간단한 테스트
    import yfinance as yf

    # 샘플 데이터 로드
    ticker = yf.Ticker("005930.KS")
    data = ticker.history(period="1mo", interval="1d")

    if not data.empty:
        data = data.rename(columns={
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })

        # 지표 계산
        data = add_momentum_indicators(data)
        data = calculate_improved_squeeze_momentum(data)

        print("최근 데이터:")
        print(data[['close', 'rsi', 'stoch_k', 'volume_momentum', 'squeeze_signal', 'momentum']].tail(10))
