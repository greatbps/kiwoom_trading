"""
L5: Squeeze Momentum Pro (존 카터 전략)
- 볼린저 밴드 수축 (BB Squeeze)
- Keltner Channel 비교
- 모멘텀 히스토그램 상승
- 손익비 극도로 좋은 전략 (+3~10%)
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict, Optional
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console

console = Console()


class SqueezeMomentumPro:
    """Squeeze Momentum Pro 전략"""

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        kc_period: int = 20,
        kc_atr_mult: float = 1.5,
        momentum_period: int = 20
    ):
        """
        Args:
            bb_period: 볼린저 밴드 기간
            bb_std: 볼린저 밴드 표준편차 배수
            kc_period: Keltner Channel 기간
            kc_atr_mult: Keltner Channel ATR 배수
            momentum_period: 모멘텀 계산 기간
        """
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_atr_mult = kc_atr_mult
        self.momentum_period = momentum_period

    def calculate_bollinger_bands(
        self,
        df: pd.DataFrame,
        period: int = None,
        std_dev: float = None
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        볼린저 밴드 계산

        Args:
            df: OHLCV 데이터
            period: 기간 (None이면 self.bb_period)
            std_dev: 표준편차 배수 (None이면 self.bb_std)

        Returns:
            (bb_upper, bb_middle, bb_lower)
        """
        period = period or self.bb_period
        std_dev = std_dev or self.bb_std

        close = df['close'] if 'close' in df.columns else df['Close']

        bb_middle = close.rolling(window=period).mean()
        bb_std = close.rolling(window=period).std()

        bb_upper = bb_middle + (bb_std * std_dev)
        bb_lower = bb_middle - (bb_std * std_dev)

        return bb_upper, bb_middle, bb_lower

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        ATR (Average True Range) 계산

        Args:
            df: OHLCV 데이터
            period: 기간

        Returns:
            ATR 시리즈
        """
        high = df['high'] if 'high' in df.columns else df['High']
        low = df['low'] if 'low' in df.columns else df['Low']
        close = df['close'] if 'close' in df.columns else df['Close']

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()

        return atr

    def calculate_keltner_channel(
        self,
        df: pd.DataFrame,
        period: int = None,
        atr_mult: float = None
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Keltner Channel 계산

        Args:
            df: OHLCV 데이터
            period: 기간 (None이면 self.kc_period)
            atr_mult: ATR 배수 (None이면 self.kc_atr_mult)

        Returns:
            (kc_upper, kc_middle, kc_lower)
        """
        period = period or self.kc_period
        atr_mult = atr_mult or self.kc_atr_mult

        close = df['close'] if 'close' in df.columns else df['Close']

        kc_middle = close.ewm(span=period, adjust=False).mean()
        atr = self.calculate_atr(df, period)

        kc_upper = kc_middle + (atr * atr_mult)
        kc_lower = kc_middle - (atr * atr_mult)

        return kc_upper, kc_middle, kc_lower

    def calculate_momentum(
        self,
        df: pd.DataFrame,
        period: int = None
    ) -> pd.Series:
        """
        모멘텀 히스토그램 계산

        Momentum = Close - SMA(Close, period)

        Args:
            df: OHLCV 데이터
            period: 기간 (None이면 self.momentum_period)

        Returns:
            모멘텀 시리즈
        """
        period = period or self.momentum_period

        close = df['close'] if 'close' in df.columns else df['Close']
        highest = close.rolling(window=period).max()
        lowest = close.rolling(window=period).min()

        # 정규화된 모멘텀
        momentum = close - ((highest + lowest) / 2 + close.rolling(window=period).mean()) / 2

        return momentum

    def check_squeeze(
        self,
        df: pd.DataFrame
    ) -> Tuple[bool, bool, Dict]:
        """
        Squeeze 조건 체크

        Squeeze On: BB가 KC 안에 들어감 (변동성 수축)
        Squeeze Off: BB가 KC 밖으로 나감 (변동성 확대)

        Args:
            df: OHLCV 데이터

        Returns:
            (squeeze_on, momentum_up, details)
        """
        if len(df) < max(self.bb_period, self.kc_period, self.momentum_period):
            return False, False, {}

        # 볼린저 밴드
        bb_upper, bb_middle, bb_lower = self.calculate_bollinger_bands(df)

        # Keltner Channel
        kc_upper, kc_middle, kc_lower = self.calculate_keltner_channel(df)

        # Squeeze 판단: BB가 KC 안에 들어가 있는가?
        # BB upper < KC upper AND BB lower > KC lower
        squeeze_on = (bb_upper.iloc[-1] < kc_upper.iloc[-1]) and (bb_lower.iloc[-1] > kc_lower.iloc[-1])

        # 모멘텀 히스토그램
        momentum = self.calculate_momentum(df)

        # 모멘텀 상승: 최근 2봉 연속 상승
        if len(momentum) >= 2:
            momentum_up = (momentum.iloc[-1] > momentum.iloc[-2]) and (momentum.iloc[-2] > momentum.iloc[-3] if len(momentum) >= 3 else True)
        else:
            momentum_up = False

        # BB Width (변동성 지표)
        close = df['close'].iloc[-1] if 'close' in df.columns else df['Close'].iloc[-1]
        bb_width = (bb_upper.iloc[-1] - bb_lower.iloc[-1]) / close if close > 0 else 0
        bb_width_ma = ((bb_upper - bb_lower) / (df['close'] if 'close' in df.columns else df['Close'])).rolling(20).mean().iloc[-1]

        # 상세 정보
        details = {
            'squeeze_on': squeeze_on,
            'momentum_up': momentum_up,
            'momentum_value': momentum.iloc[-1],
            'bb_width': bb_width,
            'bb_width_ma': bb_width_ma,
            'bb_upper': bb_upper.iloc[-1],
            'bb_lower': bb_lower.iloc[-1],
            'kc_upper': kc_upper.iloc[-1],
            'kc_lower': kc_lower.iloc[-1]
        }

        return squeeze_on, momentum_up, details

    def generate_signal(
        self,
        df: pd.DataFrame,
        current_price: float = None
    ) -> Tuple[bool, str, int]:
        """
        Squeeze Momentum 시그널 생성

        Args:
            df: OHLCV 데이터
            current_price: 현재가 (None이면 df의 마지막 종가)

        Returns:
            (signal, reason, tier)
            signal: 진입 시그널 여부
            tier: 시그널 강도 (1=강, 2=중, 3=약)
        """
        if current_price is None:
            current_price = df['close'].iloc[-1] if 'close' in df.columns else df['Close'].iloc[-1]

        squeeze_on, momentum_up, details = self.check_squeeze(df)

        # VWAP 조건 (있으면)
        vwap_ok = True
        if 'vwap' in df.columns:
            vwap = df['vwap'].iloc[-1]
            vwap_ok = current_price > vwap

        # 진입 조건: Squeeze On + Momentum Up + VWAP 위
        signal = squeeze_on and momentum_up and vwap_ok

        if signal:
            # Tier 판단
            # BB Width가 평균보다 작고, 모멘텀이 강하면 Tier 1
            if details['bb_width'] < details['bb_width_ma'] * 0.8 and details['momentum_value'] > 0:
                tier = 1
                reason = f"Squeeze Pro Tier1: BB수축 강함, 모멘텀 상승"
            else:
                tier = 2
                reason = f"Squeeze Pro Tier2: BB수축, 모멘텀 상승"
        else:
            tier = 0
            status = []
            if not squeeze_on:
                status.append("수축 없음")
            if not momentum_up:
                status.append("모멘텀 하락")
            if not vwap_ok:
                status.append("VWAP 이탈")

            reason = f"Squeeze 미발생: {', '.join(status)}"

        return signal, reason, tier


if __name__ == "__main__":
    """테스트 코드"""

    print("=" * 80)
    print("🧪 Squeeze Momentum Pro 테스트")
    print("=" * 80)

    # 테스트 데이터 생성
    import yfinance as yf

    ticker = "005930.KS"
    print(f"\n📊 테스트 종목: {ticker}")
    print("-" * 80)

    df = yf.download(ticker, period='1mo', interval='1d', progress=False)

    if df is None or len(df) == 0:
        print("❌ 데이터 조회 실패")
    else:
        # 컬럼명 소문자 변환
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() for col in df.columns]
        else:
            df.columns = df.columns.str.lower()

        # Squeeze Momentum 생성
        squeeze = SqueezeMomentumPro()

        # 시그널 생성
        signal, reason, tier = squeeze.generate_signal(df)

        print(f"  시그널: {'✅ ENTRY' if signal else '❌ NO SIGNAL'}")
        print(f"  Tier: {tier}")
        print(f"  이유: {reason}")

        # Squeeze 상태 상세
        squeeze_on, momentum_up, details = squeeze.check_squeeze(df)

        print(f"\n  상세:")
        print(f"    Squeeze On: {'✅' if squeeze_on else '❌'}")
        print(f"    Momentum Up: {'✅' if momentum_up else '❌'}")
        print(f"    Momentum Value: {details.get('momentum_value', 0):.2f}")
        print(f"    BB Width: {details.get('bb_width', 0):.4f}")
        print(f"    BB Width MA: {details.get('bb_width_ma', 0):.4f}")

    print("\n" + "=" * 80)
    print("✅ 테스트 완료")
    print("=" * 80)
