#!/usr/bin/env python3
"""
LazyBear Squeeze Momentum Indicator (1:1 Python 구현)

원본 Pine Script: LazyBear's Squeeze Momentum Indicator
- Bollinger Bands vs Keltner Channel 비교
- Linear Regression 기반 모멘텀 계산
- Squeeze ON/OFF 상태 판단

30분봉 방향 + 하위봉 진입 전략 포함
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict, Optional, List
from dataclasses import dataclass
from rich.console import Console

console = Console()


@dataclass
class SqueezeState:
    """Squeeze 상태 데이터"""
    squeeze_on: bool           # BB가 KC 안에 있음 (변동성 수축)
    squeeze_off: bool          # BB가 KC 밖에 있음 (변동성 확대)
    momentum: float            # 모멘텀 값
    momentum_direction: str    # 'up' | 'down' | 'flat'
    momentum_color: str        # 'lime' | 'green' | 'red' | 'maroon'


class SqueezeMomentumLazyBear:
    """
    LazyBear Squeeze Momentum Indicator (1:1 Python 구현)

    Pine Script 원본과 동일한 파라미터:
    - BB Length: 20, MultFactor: 2.0
    - KC Length: 20, MultFactor: 1.5
    - Linear Regression Length: 20
    """

    def __init__(
        self,
        bb_length: int = 20,
        bb_mult: float = 2.0,
        kc_length: int = 20,
        kc_mult: float = 1.5,
        use_true_range: bool = True
    ):
        """
        Args:
            bb_length: Bollinger Bands 기간 (기본 20)
            bb_mult: Bollinger Bands 표준편차 배수 (기본 2.0)
            kc_length: Keltner Channel 기간 (기본 20)
            kc_mult: Keltner Channel ATR 배수 (기본 1.5)
            use_true_range: True Range 사용 여부 (기본 True)
        """
        self.bb_length = bb_length
        self.bb_mult = bb_mult
        self.kc_length = kc_length
        self.kc_mult = kc_mult
        self.use_true_range = use_true_range

    def _get_column(self, df: pd.DataFrame, names: List[str]) -> pd.Series:
        """컬럼명 대소문자 호환"""
        for name in names:
            if name in df.columns:
                return df[name]
        raise KeyError(f"Column not found: {names}")

    def _sma(self, series: pd.Series, length: int) -> pd.Series:
        """Simple Moving Average"""
        return series.rolling(window=length).mean()

    def _stdev(self, series: pd.Series, length: int) -> pd.Series:
        """Standard Deviation"""
        return series.rolling(window=length).std()

    def _tr(self, df: pd.DataFrame) -> pd.Series:
        """True Range 계산"""
        high = self._get_column(df, ['high', 'High'])
        low = self._get_column(df, ['low', 'Low'])
        close = self._get_column(df, ['close', 'Close'])

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))

        return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    def _linreg(self, series: pd.Series, length: int) -> pd.Series:
        """
        Linear Regression (Pine Script linreg 함수 동일)

        y = linreg(source, length, 0) 계산
        """
        def calc_linreg(window):
            if len(window) < length:
                return np.nan

            y = window.values
            x = np.arange(length)

            # 선형 회귀 계수 계산
            x_mean = x.mean()
            y_mean = y.mean()

            numerator = np.sum((x - x_mean) * (y - y_mean))
            denominator = np.sum((x - x_mean) ** 2)

            if denominator == 0:
                return y_mean

            slope = numerator / denominator
            intercept = y_mean - slope * x_mean

            # offset=0이므로 마지막 값 반환
            return intercept + slope * (length - 1)

        return series.rolling(window=length).apply(calc_linreg, raw=False)

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Squeeze Momentum 전체 계산

        Args:
            df: OHLCV DataFrame

        Returns:
            DataFrame with columns:
                - squeeze_on: bool (BB inside KC)
                - squeeze_off: bool (BB outside KC)
                - momentum: float (Linear Regression value)
                - momentum_color: str ('lime', 'green', 'red', 'maroon')
        """
        df = df.copy()

        close = self._get_column(df, ['close', 'Close'])
        high = self._get_column(df, ['high', 'High'])
        low = self._get_column(df, ['low', 'Low'])

        # Bollinger Bands
        basis = self._sma(close, self.bb_length)
        dev = self.bb_mult * self._stdev(close, self.bb_length)
        bb_upper = basis + dev
        bb_lower = basis - dev

        # Keltner Channel
        ma = self._sma(close, self.kc_length)

        if self.use_true_range:
            range_val = self._tr(df)
        else:
            range_val = high - low

        range_ma = self._sma(range_val, self.kc_length)
        kc_upper = ma + range_ma * self.kc_mult
        kc_lower = ma - range_ma * self.kc_mult

        # Squeeze 상태
        # sqzOn = BB가 KC 안에 들어감
        # sqzOff = BB가 KC 밖으로 나감
        sqz_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
        sqz_off = (bb_lower < kc_lower) & (bb_upper > kc_upper)
        no_sqz = ~sqz_on & ~sqz_off

        # Momentum (Linear Regression)
        # val = linreg(source - avg(avg(highest(high, lengthKC), lowest(low, lengthKC)), sma(close, lengthKC)), lengthKC, 0)
        highest_high = high.rolling(window=self.kc_length).max()
        lowest_low = low.rolling(window=self.kc_length).min()
        avg_hl = (highest_high + lowest_low) / 2
        avg_sma = self._sma(close, self.kc_length)
        avg_val = (avg_hl + avg_sma) / 2

        source = close - avg_val
        momentum = self._linreg(source, self.kc_length)

        # 결과 DataFrame
        result = pd.DataFrame(index=df.index)
        result['squeeze_on'] = sqz_on
        result['squeeze_off'] = sqz_off
        result['no_squeeze'] = no_sqz
        result['momentum'] = momentum

        # Momentum Color 계산
        # lime: momentum > 0 and momentum > prev momentum (상승 강세)
        # green: momentum > 0 and momentum <= prev momentum (상승 약세)
        # red: momentum < 0 and momentum < prev momentum (하락 강세)
        # maroon: momentum < 0 and momentum >= prev momentum (하락 약세)
        momentum_prev = momentum.shift(1)

        colors = []
        for i in range(len(momentum)):
            if pd.isna(momentum.iloc[i]) or pd.isna(momentum_prev.iloc[i]):
                colors.append('none')
            elif momentum.iloc[i] > 0:
                if momentum.iloc[i] > momentum_prev.iloc[i]:
                    colors.append('lime')      # 상승 강세
                else:
                    colors.append('green')     # 상승 약세
            else:
                if momentum.iloc[i] < momentum_prev.iloc[i]:
                    colors.append('red')       # 하락 강세
                else:
                    colors.append('maroon')    # 하락 약세

        result['momentum_color'] = colors

        # BB, KC 값도 저장 (디버깅용)
        result['bb_upper'] = bb_upper
        result['bb_lower'] = bb_lower
        result['kc_upper'] = kc_upper
        result['kc_lower'] = kc_lower

        return result

    def get_current_state(self, df: pd.DataFrame) -> SqueezeState:
        """
        현재 Squeeze 상태 반환

        Args:
            df: OHLCV DataFrame

        Returns:
            SqueezeState 객체
        """
        result = self.calculate(df)

        if len(result) < 2:
            return SqueezeState(
                squeeze_on=False,
                squeeze_off=False,
                momentum=0.0,
                momentum_direction='flat',
                momentum_color='none'
            )

        current = result.iloc[-1]
        prev = result.iloc[-2]

        # 모멘텀 방향 판단
        if pd.isna(current['momentum']) or pd.isna(prev['momentum']):
            momentum_direction = 'flat'
        elif current['momentum'] > prev['momentum']:
            momentum_direction = 'up'
        elif current['momentum'] < prev['momentum']:
            momentum_direction = 'down'
        else:
            momentum_direction = 'flat'

        return SqueezeState(
            squeeze_on=bool(current['squeeze_on']),
            squeeze_off=bool(current['squeeze_off']),
            momentum=float(current['momentum']) if not pd.isna(current['momentum']) else 0.0,
            momentum_direction=momentum_direction,
            momentum_color=current['momentum_color']
        )


class TwoTimeframeStrategy:
    """
    2-타임프레임 전략

    - 상위봉 (30분): 방향 결정 (MA5/MA20 + Squeeze)
    - 하위봉 (3분/5분): 진입 타이밍
    """

    def __init__(
        self,
        higher_tf: str = '30min',
        lower_tf: str = '5min',
        ma_short: int = 5,
        ma_long: int = 20
    ):
        """
        Args:
            higher_tf: 상위 타임프레임 ('30min', '1h')
            lower_tf: 하위 타임프레임 ('3min', '5min')
            ma_short: 단기 이동평균 (기본 5)
            ma_long: 장기 이동평균 (기본 20)
        """
        self.higher_tf = higher_tf
        self.lower_tf = lower_tf
        self.ma_short = ma_short
        self.ma_long = ma_long

        # Squeeze Momentum 인디케이터
        self.squeeze = SqueezeMomentumLazyBear()

    def _calculate_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        """이동평균 계산"""
        df = df.copy()
        close = df['close'] if 'close' in df.columns else df['Close']

        df['ma_short'] = close.rolling(window=self.ma_short).mean()
        df['ma_long'] = close.rolling(window=self.ma_long).mean()

        return df

    def check_higher_tf_direction(
        self,
        df_higher: pd.DataFrame,
        debug: bool = True
    ) -> Tuple[str, str, Dict]:
        """
        상위봉 (30분) 방향 체크

        롱 조건:
        1. MA5 > MA20 (골든크로스 유지)
        2. Squeeze OFF (변동성 확대)
        3. 모멘텀 상승 (lime or green)

        Args:
            df_higher: 상위 타임프레임 OHLCV
            debug: 디버그 로그 출력

        Returns:
            (direction, reason, details)
            direction: 'long' | 'short' | 'neutral'
        """
        details = {}

        if len(df_higher) < max(self.ma_short, self.ma_long, 20) + 1:
            return 'neutral', '데이터 부족', details

        # MA 계산
        df_higher = self._calculate_ma(df_higher)

        # Squeeze 상태 확인
        squeeze_state = self.squeeze.get_current_state(df_higher)

        # 현재 값들
        ma_short = df_higher['ma_short'].iloc[-1]
        ma_long = df_higher['ma_long'].iloc[-1]

        # 이전 값들 (크로스 체크용)
        ma_short_prev = df_higher['ma_short'].iloc[-2]
        ma_long_prev = df_higher['ma_long'].iloc[-2]

        # 골든크로스 체크 (🔧 FIX: 실제 크로스오버 발생 여부 체크)
        ma5_above_ma20 = ma_short > ma_long  # 단순 상태
        golden_cross = (ma_short_prev <= ma_long_prev) and (ma_short > ma_long)  # 실제 크로스오버!

        # 데드크로스 체크
        dead_cross = (ma_short_prev >= ma_long_prev) and (ma_short < ma_long)  # 실제 크로스오버!
        ma5_below_ma20 = ma_short < ma_long  # 단순 상태

        details = {
            'ma_short': ma_short,
            'ma_long': ma_long,
            'ma5_above_ma20': ma5_above_ma20,
            'golden_cross': golden_cross,
            'squeeze_on': squeeze_state.squeeze_on,
            'squeeze_off': squeeze_state.squeeze_off,
            'momentum': squeeze_state.momentum,
            'momentum_direction': squeeze_state.momentum_direction,
            'momentum_color': squeeze_state.momentum_color
        }

        if debug:
            console.print(f"[cyan]📊 30분봉 방향 체크[/cyan]")
            console.print(f"  MA{self.ma_short}: {ma_short:,.0f}, MA{self.ma_long}: {ma_long:,.0f}")
            console.print(f"  골든크로스: {'✅' if golden_cross else '❌'}")
            console.print(f"  Squeeze OFF: {'✅' if squeeze_state.squeeze_off else '❌'}")
            console.print(f"  모멘텀: {squeeze_state.momentum:.2f} ({squeeze_state.momentum_color})")

        # 롱 방향 조건 (🔧 2026-01-17 시뮬레이션 결과 반영)
        # 8일 시뮬레이션: 승률 50.7%, 총 수익 +19,840원
        # Squeeze OFF 조건 제거 (수익 거래도 차단하는 문제)
        # 1. 골든크로스 발생 (MA5가 MA20을 상향 돌파) - 필수
        # 2. 모멘텀 상승 (lime or green) - 필수
        # 3. 12시 이전 진입 - main_auto_trading.py에서 별도 체크
        long_conditions = [
            golden_cross,                                            # 🔧 실제 골든크로스 발생!
            squeeze_state.momentum_color in ['lime', 'green']        # 모멘텀 상승
            # squeeze_state.squeeze_off,                             # ❌ 제거: 시뮬레이션 결과 Squeeze OFF가 수익 거래도 차단
        ]

        # 숏 방향 조건 (🔧 2026-01-17 시뮬레이션 결과 반영)
        short_conditions = [
            dead_cross,                                              # 🔧 실제 데드크로스 발생!
            squeeze_state.momentum_color in ['red', 'maroon']        # 모멘텀 하락
            # squeeze_state.squeeze_off,                             # ❌ 제거
        ]

        if all(long_conditions):
            reason = f"롱 방향: 골든크로스(MA{self.ma_short}>MA{self.ma_long}), 모멘텀↑({squeeze_state.momentum_color})"
            if debug:
                console.print(f"[green]  ✅ {reason}[/green]")
            return 'long', reason, details

        elif all(short_conditions):
            reason = f"숏 방향: 데드크로스(MA{self.ma_short}<MA{self.ma_long}), 모멘텀↓({squeeze_state.momentum_color})"
            if debug:
                console.print(f"[red]  ❌ {reason}[/red]")
            return 'short', reason, details

        else:
            # 조건 미충족 이유
            missing = []
            if not golden_cross:
                if ma5_above_ma20:
                    missing.append(f"골든크로스 미발생(이미 MA{self.ma_short}>MA{self.ma_long} 상태)")
                else:
                    missing.append(f"골든크로스 미발생(MA{self.ma_short}<MA{self.ma_long})")
            # Squeeze OFF 조건 제거됨 (2026-01-17 시뮬레이션 결과)
            if squeeze_state.momentum_color not in ['lime', 'green']:
                missing.append(f"모멘텀 하락({squeeze_state.momentum_color})")

            reason = f"중립: {', '.join(missing)}"
            if debug:
                console.print(f"[yellow]  ⚠️ {reason}[/yellow]")
            return 'neutral', reason, details

    def check_lower_tf_entry(
        self,
        df_lower: pd.DataFrame,
        direction: str,
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        하위봉 (3분/5분) 진입 타이밍 체크

        롱 진입 조건 (상위봉 롱 방향일 때):
        1. MA5가 MA20 위에서 눌림 후 반등
        2. 또는 MA5/MA20 골든크로스 발생

        Args:
            df_lower: 하위 타임프레임 OHLCV
            direction: 상위봉 방향 ('long', 'short', 'neutral')
            debug: 디버그 로그 출력

        Returns:
            (signal, reason, details)
        """
        details = {}

        if direction == 'neutral':
            return False, '상위봉 방향 미확정', details

        if len(df_lower) < self.ma_long + 2:
            return False, '데이터 부족', details

        # MA 계산
        df_lower = self._calculate_ma(df_lower)

        # 현재/이전 값
        ma_short_curr = df_lower['ma_short'].iloc[-1]
        ma_long_curr = df_lower['ma_long'].iloc[-1]
        ma_short_prev = df_lower['ma_short'].iloc[-2]
        ma_long_prev = df_lower['ma_long'].iloc[-2]

        close_curr = df_lower['close'].iloc[-1] if 'close' in df_lower.columns else df_lower['Close'].iloc[-1]
        close_prev = df_lower['close'].iloc[-2] if 'close' in df_lower.columns else df_lower['Close'].iloc[-2]

        details = {
            'ma_short': ma_short_curr,
            'ma_long': ma_long_curr,
            'close': close_curr,
            'direction': direction
        }

        if debug:
            console.print(f"[cyan]📊 {self.lower_tf} 진입 체크 (방향: {direction})[/cyan]")
            console.print(f"  MA{self.ma_short}: {ma_short_curr:,.0f}, MA{self.ma_long}: {ma_long_curr:,.0f}")

        if direction == 'long':
            # 롱 진입 조건
            # 1. 골든크로스 발생
            golden_cross = (ma_short_prev <= ma_long_prev) and (ma_short_curr > ma_long_curr)

            # 2. MA5 > MA20 유지 중 눌림 후 반등 (가격이 MA5 위로 올라옴)
            pullback_rebound = (
                ma_short_curr > ma_long_curr and           # MA5 > MA20 유지
                close_prev < ma_short_prev and             # 이전 봉 MA5 아래 (눌림)
                close_curr > ma_short_curr                 # 현재 봉 MA5 위 (반등)
            )

            details['golden_cross'] = golden_cross
            details['pullback_rebound'] = pullback_rebound

            if golden_cross:
                reason = f"{self.lower_tf} 골든크로스 발생 (MA{self.ma_short} > MA{self.ma_long})"
                if debug:
                    console.print(f"[green]  ✅ {reason}[/green]")
                return True, reason, details

            elif pullback_rebound:
                reason = f"{self.lower_tf} 눌림 후 반등 (가격 > MA{self.ma_short} > MA{self.ma_long})"
                if debug:
                    console.print(f"[green]  ✅ {reason}[/green]")
                return True, reason, details

            else:
                reason = f"{self.lower_tf} 진입 조건 미충족"
                if debug:
                    console.print(f"[yellow]  ⚠️ {reason}[/yellow]")
                return False, reason, details

        elif direction == 'short':
            # 숏 진입 조건 (미구현 - 롱온리 전략)
            return False, '숏 진입 미지원', details

        return False, '알 수 없는 방향', details

    def check_entry_signal(
        self,
        df_higher: pd.DataFrame,
        df_lower: pd.DataFrame,
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        2-타임프레임 진입 시그널 체크

        Args:
            df_higher: 상위 타임프레임 (30분) OHLCV
            df_lower: 하위 타임프레임 (3분/5분) OHLCV
            debug: 디버그 로그 출력

        Returns:
            (signal, reason, details)
        """
        if debug:
            console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
            console.print(f"[bold cyan]2-타임프레임 전략 체크 ({self.higher_tf} + {self.lower_tf})[/bold cyan]")
            console.print(f"[bold cyan]{'='*60}[/bold cyan]")

        # 1. 상위봉 방향 체크
        direction, higher_reason, higher_details = self.check_higher_tf_direction(
            df_higher, debug=debug
        )

        # 2. 상위봉 롱 방향일 때만 하위봉 진입 체크
        if direction != 'long':
            return False, f"상위봉 비롱: {higher_reason}", {
                'higher_tf': higher_details,
                'lower_tf': {},
                'direction': direction
            }

        # 3. 하위봉 진입 체크
        signal, lower_reason, lower_details = self.check_lower_tf_entry(
            df_lower, direction, debug=debug
        )

        combined_details = {
            'higher_tf': higher_details,
            'lower_tf': lower_details,
            'direction': direction
        }

        if signal:
            final_reason = f"[{self.higher_tf}] {higher_reason} + [{self.lower_tf}] {lower_reason}"
            if debug:
                console.print(f"\n[bold green]✅ 진입 시그널: {final_reason}[/bold green]")
            return True, final_reason, combined_details
        else:
            final_reason = f"[{self.higher_tf}] 롱 + [{self.lower_tf}] {lower_reason}"
            if debug:
                console.print(f"\n[yellow]⚠️ 대기: {final_reason}[/yellow]")
            return False, final_reason, combined_details

    def check_exit_signal(
        self,
        df_higher: pd.DataFrame,
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        청산 시그널 체크 (30분봉 기준)

        청산 조건:
        1. MA5 < MA20 (데드크로스)
        2. 또는 모멘텀이 음수로 전환 (red, maroon)

        Args:
            df_higher: 상위 타임프레임 (30분) OHLCV
            debug: 디버그 로그 출력

        Returns:
            (should_exit, reason, details)
        """
        if len(df_higher) < max(self.ma_short, self.ma_long, 20) + 1:
            return False, '데이터 부족', {}

        # MA 계산
        df_higher = self._calculate_ma(df_higher)

        # Squeeze 상태 확인
        squeeze_state = self.squeeze.get_current_state(df_higher)

        ma_short = df_higher['ma_short'].iloc[-1]
        ma_long = df_higher['ma_long'].iloc[-1]

        # 이전 값 (크로스 체크)
        ma_short_prev = df_higher['ma_short'].iloc[-2]
        ma_long_prev = df_higher['ma_long'].iloc[-2]

        details = {
            'ma_short': ma_short,
            'ma_long': ma_long,
            'momentum': squeeze_state.momentum,
            'momentum_color': squeeze_state.momentum_color
        }

        # 데드크로스 발생
        dead_cross = (ma_short_prev >= ma_long_prev) and (ma_short < ma_long)

        # 모멘텀 음수 전환
        momentum_negative = squeeze_state.momentum_color in ['red', 'maroon']

        if dead_cross:
            reason = f"30분봉 데드크로스 (MA{self.ma_short} < MA{self.ma_long})"
            if debug:
                console.print(f"[red]❌ {reason}[/red]")
            return True, reason, details

        # 모멘텀만으로 청산하지 않음 (선택적)
        # if momentum_negative and squeeze_state.squeeze_off:
        #     reason = f"모멘텀 음수 전환 ({squeeze_state.momentum_color})"
        #     return True, reason, details

        return False, '청산 조건 미충족', details


if __name__ == "__main__":
    """테스트 코드"""
    import yfinance as yf

    print("=" * 80)
    print("LazyBear Squeeze Momentum 테스트")
    print("=" * 80)

    # 테스트 데이터 다운로드
    ticker = "005930.KS"  # 삼성전자
    print(f"\n테스트 종목: {ticker}")

    df = yf.download(ticker, period='3mo', interval='1d', progress=False)

    if df is not None and len(df) > 0:
        # 컬럼명 소문자 변환
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() for col in df.columns]
        else:
            df.columns = df.columns.str.lower()

        # Squeeze Momentum 계산
        squeeze = SqueezeMomentumLazyBear()
        state = squeeze.get_current_state(df)

        print(f"\n현재 상태:")
        print(f"  Squeeze ON: {state.squeeze_on}")
        print(f"  Squeeze OFF: {state.squeeze_off}")
        print(f"  모멘텀: {state.momentum:.2f}")
        print(f"  모멘텀 방향: {state.momentum_direction}")
        print(f"  모멘텀 색상: {state.momentum_color}")

        # 히스토리 출력
        result = squeeze.calculate(df)
        print(f"\n최근 5일 히스토리:")
        print(result[['squeeze_on', 'squeeze_off', 'momentum', 'momentum_color']].tail())

    print("\n" + "=" * 80)
    print("테스트 완료")
    print("=" * 80)
