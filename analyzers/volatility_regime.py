"""
L1: Realized Volatility 환경 필터
- 오늘이 추세장인지 횡보장인지 판단
- 고변동성: 추세 전략 FULL
- 저변동성: 추세 전략 LIMITED
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import Tuple, Dict
from datetime import datetime, timedelta
from pathlib import Path
import sys
import logging

# yfinance 로깅 억제
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console

console = Console()


class VolatilityRegimeDetector:
    """Realized Volatility 기반 장세 판단기"""

    def __init__(
        self,
        rv_window: int = 10,
        rv_lookback: int = 100,
        high_vol_percentile: float = 0.6,
        low_vol_percentile: float = 0.4
    ):
        """
        Args:
            rv_window: RV 계산 윈도우 (기본 10일)
            rv_lookback: RV 백분위 계산 기간 (기본 100일)
            high_vol_percentile: 고변동성 임계값 (60%)
            low_vol_percentile: 저변동성 임계값 (40%)
        """
        self.rv_window = rv_window
        self.rv_lookback = rv_lookback
        self.high_vol_percentile = high_vol_percentile
        self.low_vol_percentile = low_vol_percentile

        # 캐시
        self.cache: Dict[str, Dict] = {}
        self.cache_expiry: Dict[str, datetime] = {}

    def _safe_get_value(self, series_or_value):
        """Series나 단일 값을 안전하게 float로 변환"""
        if hasattr(series_or_value, 'values'):
            if len(series_or_value.values) > 0:
                return float(series_or_value.values[0])
            return 0.0
        return float(series_or_value)

    def calculate_realized_volatility(self, df: pd.DataFrame) -> pd.Series:
        """
        Realized Volatility 계산

        RV = sqrt(sum(log_return^2))

        Args:
            df: OHLCV 데이터

        Returns:
            RV 시리즈
        """
        # 로그 수익률
        log_returns = np.log(df['Close'] / df['Close'].shift(1))

        # RV = sqrt(sum(log_return^2))
        rv = np.sqrt((log_returns ** 2).rolling(window=self.rv_window).sum())

        return rv

    def get_market_regime(self, market: str = 'KOSPI') -> Tuple[str, float, Dict]:
        """
        시장 변동성 체제 판단

        Args:
            market: 'KOSPI' or 'KOSDAQ'

        Returns:
            (regime, rv_percentile, details)
            regime: 'HIGH_VOL', 'LOW_VOL', 'NORMAL'
        """
        # 캐시 확인 (5분간 유효)
        now = datetime.now()
        cache_key = f"market_regime_{market}"

        if cache_key in self.cache:
            if cache_key in self.cache_expiry and self.cache_expiry[cache_key] > now:
                cached = self.cache[cache_key]
                return cached['regime'], cached['rv_percentile'], cached['details']

        # 시장 지수
        ticker = '^KS11' if market == 'KOSPI' else '^KQ11'

        # 데이터 조회 (lookback + 여유)
        period = f"{int(self.rv_lookback * 1.5)}d"

        try:
            # FutureWarning 방지: auto_adjust 명시
            df = yf.download(ticker, period=period, interval='1d',
                           progress=False, auto_adjust=True)

            if df is None:
                console.print(f"[yellow]⚠️  {market} 데이터 조회 실패[/yellow]")
                return 'NORMAL', 50.0, {}

            if len(df) < self.rv_lookback:
                console.print(f"[yellow]⚠️  {market} 데이터 부족 ({len(df)}일)[/yellow]")
                return 'NORMAL', 50.0, {}

            # MultiIndex 처리 (단일 종목 조회 시)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]

            # RV 계산
            rv_series = self.calculate_realized_volatility(df)

            # 최신 RV
            current_rv = self._safe_get_value(rv_series.iloc[-1])

            # RV 백분위
            rv_historical = rv_series.tail(self.rv_lookback).dropna()

            if len(rv_historical) == 0:
                console.print(f"[yellow]⚠️  {market} RV 데이터 없음[/yellow]")
                return 'NORMAL', 50.0, {}

            rv_percentile = (rv_historical < current_rv).astype(int).sum() / len(rv_historical)

            # 체제 판단
            if rv_percentile >= self.high_vol_percentile:
                regime = 'HIGH_VOL'
            elif rv_percentile <= self.low_vol_percentile:
                regime = 'LOW_VOL'
            else:
                regime = 'NORMAL'

            # 상세 정보
            details = {
                'current_rv': current_rv,
                'rv_mean': self._safe_get_value(rv_historical.mean()),
                'rv_std': self._safe_get_value(rv_historical.std()),
                'rv_percentile': rv_percentile * 100,
                'regime': regime
            }

            # 캐시 저장 (5분)
            self.cache[cache_key] = {
                'regime': regime,
                'rv_percentile': rv_percentile * 100,
                'details': details
            }
            self.cache_expiry[cache_key] = now + timedelta(minutes=5)

            return regime, rv_percentile * 100, details

        except Exception as e:
            console.print(f"[red]❌ {market} RV 계산 실패: {e}[/red]")
            return 'NORMAL', 50.0, {}

    def should_use_trend_strategy(self, market: str = 'KOSPI') -> Tuple[bool, str, float]:
        """
        추세 전략 사용 가능 여부

        Args:
            market: 시장 구분

        Returns:
            (use_trend, reason, confidence)
            use_trend: True면 추세 전략 사용 권장
            confidence: 0.0~1.0 (포지션 사이즈 조정용)
        """
        regime, rv_percentile, details = self.get_market_regime(market)

        if regime == 'HIGH_VOL':
            return True, f"고변동성 ({rv_percentile:.1f}% 백분위)", 1.0

        elif regime == 'LOW_VOL':
            return False, f"저변동성 ({rv_percentile:.1f}% 백분위)", 0.3

        else:  # NORMAL
            return True, f"보통 변동성 ({rv_percentile:.1f}% 백분위)", 0.7


if __name__ == "__main__":
    """테스트 코드"""

    print("=" * 80)
    print("🧪 Realized Volatility 환경 필터 테스트")
    print("=" * 80)

    # 체제 감지기 생성
    regime_detector = VolatilityRegimeDetector(
        rv_window=10,
        rv_lookback=100,
        high_vol_percentile=0.6,
        low_vol_percentile=0.4
    )

    # KOSPI 테스트
    print("\n📊 KOSPI 시장")
    print("-" * 80)

    regime, percentile, details = regime_detector.get_market_regime('KOSPI')

    print(f"  변동성 체제: {regime}")
    print(f"  RV 백분위: {percentile:.1f}%")
    print(f"  현재 RV: {details.get('current_rv', 0):.6f}")
    print(f"  평균 RV: {details.get('rv_mean', 0):.6f}")
    print(f"  표준편차: {details.get('rv_std', 0):.6f}")

    use_trend, reason, confidence = regime_detector.should_use_trend_strategy('KOSPI')

    print(f"\n  추세 전략 사용: {'✅ YES' if use_trend else '❌ NO'}")
    print(f"  이유: {reason}")
    print(f"  포지션 사이즈 조정: {confidence * 100:.0f}%")

    # KOSDAQ 테스트
    print("\n📊 KOSDAQ 시장")
    print("-" * 80)

    regime, percentile, details = regime_detector.get_market_regime('KOSDAQ')

    print(f"  변동성 체제: {regime}")
    print(f"  RV 백분위: {percentile:.1f}%")

    use_trend, reason, confidence = regime_detector.should_use_trend_strategy('KOSDAQ')

    print(f"\n  추세 전략 사용: {'✅ YES' if use_trend else '❌ NO'}")
    print(f"  이유: {reason}")
    print(f"  포지션 사이즈 조정: {confidence * 100:.0f}%")

    print("\n" + "=" * 80)
    print("✅ 테스트 완료")
    print("=" * 80)
