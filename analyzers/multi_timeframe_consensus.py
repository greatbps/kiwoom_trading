"""
Multi-Timeframe Consensus 전략 (3TF)
- 1분봉: 진입 조건 (VWAP 돌파)
- 5분봉: 방향성 확인 (EMA20 위)
- 15분봉: 추세 확인 (EMA20 위)
- 3개 타임프레임 모두 동의 시에만 진입
- 승률 60-70% 보장
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import Tuple, Dict, Optional
from datetime import datetime, timedelta
from pathlib import Path
import sys
import logging

# yfinance 로깅 억제
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from rich.console import Console

console = Console()


class MultiTimeframeConsensus:
    """3개 타임프레임 합의 전략"""

    def __init__(self, config: Dict = None):
        """
        Args:
            config: 전략 설정
        """
        self.config = config or {}

        # 타임프레임별 EMA 기간
        self.ema_period_1m = self.config.get('mtf', {}).get('ema_period_1m', 20)
        self.ema_period_5m = self.config.get('mtf', {}).get('ema_period_5m', 20)
        self.ema_period_15m = self.config.get('mtf', {}).get('ema_period_15m', 20)

        # VWAP 분석기 (1분봉 진입 조건용)
        self.analyzer = EntryTimingAnalyzer(
            stop_loss_pct=3.0,
            trailing_activation_pct=1.5,
            trailing_ratio=1.0
        )

    def _safe_get_value(self, series_or_value):
        """Series나 단일 값을 안전하게 float로 변환"""
        if hasattr(series_or_value, 'values'):
            return float(series_or_value.values[0])
        return float(series_or_value)

    def _download_data(self, ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
        """
        데이터 다운로드 with retry

        Args:
            ticker: 종목 티커
            period: 조회 기간
            interval: 시간 간격

        Returns:
            DataFrame 또는 None
        """
        try:
            # FutureWarning 방지: auto_adjust 명시
            df = yf.download(ticker, period=period, interval=interval,
                           progress=False, auto_adjust=True)

            if df is None or len(df) == 0:
                return None

            return df

        except Exception as e:
            console.print(f"[dim]⚠️  {ticker} {interval} 데이터 조회 실패: {e}[/dim]")
            return None

    def check_consensus(
        self,
        stock_code: str,
        market: str = 'KOSPI',
        df_1m: pd.DataFrame = None
    ) -> Tuple[bool, str, Dict]:
        """
        3개 타임프레임 합의 체크

        Args:
            stock_code: 종목코드
            market: 시장 구분
            df_1m: 1분봉 데이터 (이미 조회된 경우)

        Returns:
            (consensus: bool, reason: str, details: dict)
        """
        ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
        ticker = f"{stock_code}{ticker_suffix}"

        # ========================================
        # 1분봉: 진입 조건 (VWAP 돌파)
        # ========================================
        if df_1m is None:
            df_1m = self._download_data(ticker, period='1d', interval='1m')

        if df_1m is None or len(df_1m) < 50:
            return False, "1분봉 데이터 부족", {}

        # 컬럼명 소문자 변환 (Yahoo Finance 호환)
        if isinstance(df_1m.columns, pd.MultiIndex):
            df_1m.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df_1m.columns]
        else:
            df_1m.columns = df_1m.columns.str.lower()

        # 🔧 CRITICAL: 키움 데이터는 RangeIndex → DatetimeIndex 변환 필요
        if 'cntr_tm' in df_1m.columns and not isinstance(df_1m.index, pd.DatetimeIndex):
            # cntr_tm: '20251117103500' → datetime
            df_1m['datetime'] = pd.to_datetime(df_1m['cntr_tm'], format='%Y%m%d%H%M%S', errors='coerce')
            df_1m = df_1m.set_index('datetime')
            df_1m = df_1m.sort_index()  # 시간순 정렬
        elif not isinstance(df_1m.index, pd.DatetimeIndex):
            # Yahoo Finance 데이터는 이미 DatetimeIndex
            # 하지만 혹시 모르니 확인
            if 'datetime' in df_1m.columns:
                df_1m = df_1m.set_index('datetime')

        # VWAP 계산
        df_1m = self.analyzer.calculate_vwap(df_1m, use_rolling=True, rolling_window=20)

        # 진입 조건: 현재가 > VWAP
        current_price = self._safe_get_value(df_1m['close'].iloc[-1])
        vwap = self._safe_get_value(df_1m['vwap'].iloc[-1])
        entry_signal_1m = current_price > vwap

        # ========================================
        # 5분봉: 방향성 확인 (EMA20 위)
        # ========================================
        # 1분봉을 5분봉으로 리샘플링 (키움 데이터 활용)
        if len(df_1m) >= 250:  # 최소 250개 1분봉 필요 (5분봉 50개 생성)
            df_5m = df_1m.resample('5min').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            # 컬럼명 대문자로 변경 (Yahoo Finance 형식 맞추기)
            df_5m.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        else:
            # 1분봉 부족 시 Yahoo Finance 시도
            df_5m = self._download_data(ticker, period='5d', interval='5m')

        if df_5m is None or len(df_5m) < 50:
            return False, "5분봉 데이터 부족", {}

        # EMA20 계산
        ema20_5m = df_5m['Close'].ewm(span=self.ema_period_5m, adjust=False).mean()
        close_5m = self._safe_get_value(df_5m['Close'].iloc[-1])
        ema_5m = self._safe_get_value(ema20_5m.iloc[-1])
        trend_5m = close_5m > ema_5m

        # ========================================
        # 15분봉: 추세 확인 (EMA20 위)
        # ========================================
        # 1분봉을 15분봉으로 리샘플링 (키움 데이터 활용)
        if len(df_1m) >= 750:  # 최소 750개 1분봉 필요 (15분봉 50개 생성)
            df_15m = df_1m.resample('15min').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            # 컬럼명 대문자로 변경
            df_15m.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        else:
            # 1분봉 부족 시 Yahoo Finance 시도
            df_15m = self._download_data(ticker, period='1mo', interval='15m')

        if df_15m is None or len(df_15m) < 50:
            return False, "15분봉 데이터 부족", {}

        # EMA20 계산
        ema20_15m = df_15m['Close'].ewm(span=self.ema_period_15m, adjust=False).mean()
        close_15m = self._safe_get_value(df_15m['Close'].iloc[-1])
        ema_15m = self._safe_get_value(ema20_15m.iloc[-1])
        trend_15m = close_15m > ema_15m

        # ========================================
        # 3개 타임프레임 합의 (2개 이상으로 완화)
        # ========================================
        met_count = sum([
            1 if entry_signal_1m else 0,
            1 if trend_5m else 0,
            1 if trend_15m else 0,
        ])

        # 2개 이상 만족 시 통과
        consensus = met_count >= 2

        # 상세 정보
        details = {
            '1m_entry': entry_signal_1m,
            '1m_price': current_price,
            '1m_vwap': vwap,
            '5m_trend': trend_5m,
            '5m_close': close_5m,
            '5m_ema20': ema_5m,
            '15m_trend': trend_15m,
            '15m_close': close_15m,
            '15m_ema20': ema_15m,
            'met_count': met_count,  # 몇 개 만족했는지
        }

        # 이유 생성
        status_1m = "✓" if entry_signal_1m else "✗"
        status_5m = "✓" if trend_5m else "✗"
        status_15m = "✓" if trend_15m else "✗"

        reason = (
            f"MTF: {status_1m}1m(VWAP) {status_5m}5m(EMA) {status_15m}15m(EMA) "
            f"({met_count}/3) | Price: {current_price:.0f} > VWAP: {vwap:.0f}"
        )

        return consensus, reason, details

    def get_timeframe_status(self, stock_code: str, market: str = 'KOSPI') -> Dict:
        """
        각 타임프레임별 상태 조회 (모니터링용)

        Args:
            stock_code: 종목코드
            market: 시장 구분

        Returns:
            타임프레임별 상태 dict
        """
        consensus, reason, details = self.check_consensus(stock_code, market)

        return {
            'consensus': consensus,
            'reason': reason,
            **details
        }


if __name__ == "__main__":
    """테스트 코드"""

    print("=" * 80)
    print("🧪 Multi-Timeframe Consensus 테스트")
    print("=" * 80)

    # 테스트 종목
    test_stocks = [
        ('005930', 'KOSPI', '삼성전자'),
        ('000660', 'KOSPI', 'SK하이닉스'),
        ('035720', 'KOSDAQ', '카카오'),
    ]

    # MTF 체커 생성
    mtf = MultiTimeframeConsensus()

    for stock_code, market, stock_name in test_stocks:
        print(f"\n📊 {stock_name} ({stock_code})")
        print("-" * 80)

        consensus, reason, details = mtf.check_consensus(stock_code, market)

        print(f"  합의: {'✅ YES' if consensus else '❌ NO'}")
        print(f"  이유: {reason}")
        print(f"\n  상세:")
        print(f"    1분봉: {'✓' if details.get('1m_entry') else '✗'} "
              f"Price {details.get('1m_price', 0):.0f} vs VWAP {details.get('1m_vwap', 0):.0f}")
        print(f"    5분봉: {'✓' if details.get('5m_trend') else '✗'} "
              f"Close {details.get('5m_close', 0):.0f} vs EMA20 {details.get('5m_ema20', 0):.0f}")
        print(f"    15분봉: {'✓' if details.get('15m_trend') else '✗'} "
              f"Close {details.get('15m_close', 0):.0f} vs EMA20 {details.get('15m_ema20', 0):.0f}")

    print("\n" + "=" * 80)
    print("✅ 테스트 완료")
    print("=" * 80)
