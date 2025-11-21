"""
통합 주식 데이터 수집 모듈

중복 코드 제거:
- main_auto_trading.py의 download_stock_data_sync, download_stock_data_yahoo
- main_condition_filter.py의 download_stock_data_sync, download_stock_data_yahoo
- analyzers/entry_timing_analyzer.py의 유사 로직
"""
import asyncio
import warnings
import logging
from typing import Optional, Literal
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd
from rich.console import Console

console = Console()


class StockDataFetcher:
    """통합 주식 데이터 수집 클래스"""

    def __init__(self, kiwoom_api=None, verbose: bool = True):
        """
        Args:
            kiwoom_api: KiwoomAPI 인스턴스 (선택)
            verbose: 로그 출력 여부
        """
        self.kiwoom_api = kiwoom_api
        self.verbose = verbose

        # yfinance 로거 설정
        self.yf_logger = logging.getLogger('yfinance')
        self.original_log_level = self.yf_logger.level

    def _log(self, message: str, style: str = "dim"):
        """로그 출력"""
        if self.verbose:
            console.print(f"[{style}]{message}[/{style}]")

    async def fetch(
        self,
        stock_code: str,
        days: int = 7,
        source: Literal['auto', 'kiwoom', 'yahoo'] = 'auto',
        interval: str = '5m'
    ) -> Optional[pd.DataFrame]:
        """
        주식 데이터 수집 (우선순위: Kiwoom → Yahoo .KS → Yahoo .KQ)

        Args:
            stock_code: 종목 코드 (6자리, 예: '005930')
            days: 조회 일수 (기본 7일)
            source: 데이터 소스
                - 'auto': 자동 선택 (Kiwoom → Yahoo)
                - 'kiwoom': Kiwoom API만 사용
                - 'yahoo': Yahoo Finance만 사용
            interval: 데이터 간격 ('5m', '1d' 등)

        Returns:
            OHLCV 데이터프레임 또는 None

        Example:
            >>> fetcher = StockDataFetcher()
            >>> data = await fetcher.fetch('005930', days=7)
            >>> print(data.head())
        """
        if source == 'auto':
            # 1. Kiwoom API 시도
            if self.kiwoom_api:
                data = await self._fetch_from_kiwoom(stock_code, days, interval)
                if data is not None and len(data) > 0:
                    self._log(f"✓ Kiwoom API: {stock_code} ({len(data)}개 봉)", "green")
                    return data

            # 2. Yahoo Finance 시도
            return await self._fetch_from_yahoo(stock_code, days, interval)

        elif source == 'kiwoom':
            if not self.kiwoom_api:
                self._log("⚠️  Kiwoom API 인스턴스 없음", "yellow")
                return None
            return await self._fetch_from_kiwoom(stock_code, days, interval)

        elif source == 'yahoo':
            return await self._fetch_from_yahoo(stock_code, days, interval)

        else:
            raise ValueError(f"Invalid source: {source}")

    async def _fetch_from_kiwoom(
        self,
        stock_code: str,
        days: int,
        interval: str
    ) -> Optional[pd.DataFrame]:
        """
        Kiwoom API에서 데이터 수집

        Args:
            stock_code: 종목 코드
            days: 조회 일수
            interval: 간격 ('5m' 등)

        Returns:
            DataFrame 또는 None
        """
        try:
            # 분봉 데이터 조회
            if interval == '5m':
                # 필요한 봉 개수 계산 (하루 약 78개 5분봉)
                required_bars = days * 78

                result = await self.kiwoom_api.get_minute_chart(
                    stock_code=stock_code,
                    tick_range='5',  # 5분봉
                    count=required_bars
                )

                if not result:
                    return None

                # API 응답 파싱
                data_key = None
                for key in ['stk_min_pole_chart_qry', 'output2', 'output']:
                    if key in result and result[key]:
                        data_key = key
                        break

                if not data_key or not result[data_key]:
                    return None

                # DataFrame 변환
                df = pd.DataFrame(result[data_key])

                # 컬럼명 매핑 (Kiwoom → 표준)
                column_map = {
                    'stck_bsop_date': 'date',
                    'stck_cntg_hour': 'time',
                    'stck_prpr': 'close',
                    'stck_oprc': 'open',
                    'stck_hgpr': 'high',
                    'stck_lwpr': 'low',
                    'cntg_vol': 'volume'
                }

                df.rename(columns=column_map, inplace=True)

                # 숫자 변환
                numeric_cols = ['close', 'open', 'high', 'low', 'volume']
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                # 날짜/시간 결합
                if 'date' in df.columns and 'time' in df.columns:
                    df['datetime'] = pd.to_datetime(
                        df['date'].astype(str) + ' ' + df['time'].astype(str),
                        format='%Y%m%d %H%M%S',
                        errors='coerce'
                    )
                    df.set_index('datetime', inplace=True)

                # 음수/0 가격 필터링
                df = self._clean_price_data(df, stock_code)

                return df if len(df) >= 10 else None

            else:
                # 일봉 등 다른 간격은 추후 구현
                self._log(f"⚠️  Kiwoom: {interval} 간격은 미구현", "yellow")
                return None

        except Exception as e:
            if self.verbose:
                self._log(f"⚠️  Kiwoom API 오류 ({stock_code}): {e}", "yellow")
            return None

    async def _fetch_from_yahoo(
        self,
        stock_code: str,
        days: int,
        interval: str
    ) -> Optional[pd.DataFrame]:
        """
        Yahoo Finance에서 데이터 수집 (.KS → .KQ 순서)

        Args:
            stock_code: 종목 코드 (6자리)
            days: 조회 일수
            interval: 간격 ('5m', '1d' 등)

        Returns:
            DataFrame 또는 None
        """
        # 1. .KS (KOSPI) 시도
        ticker_ks = f"{stock_code}.KS"
        df = await self._download_yahoo_sync(ticker_ks, days, interval)
        if df is not None and len(df) > 0:
            self._log(f"✓ Yahoo: {ticker_ks} ({len(df)}개 봉)", "green")
            return df

        # 2. .KQ (KOSDAQ) 시도
        ticker_kq = f"{stock_code}.KQ"
        df = await self._download_yahoo_sync(ticker_kq, days, interval)
        if df is not None and len(df) > 0:
            self._log(f"✓ Yahoo: {ticker_kq} ({len(df)}개 봉)", "green")
            return df

        # 3. 실패
        if self.verbose:
            self._log(f"✗ Yahoo: {stock_code} 데이터 없음", "red")
        return None

    async def _download_yahoo_sync(
        self,
        ticker: str,
        days: int,
        interval: str
    ) -> Optional[pd.DataFrame]:
        """
        Yahoo Finance 동기 다운로드 (비동기 래핑)

        Args:
            ticker: 전체 티커 (예: '005930.KS')
            days: 조회 일수
            interval: 간격

        Returns:
            DataFrame 또는 None
        """
        # yfinance 로거 완전히 비활성화
        self.yf_logger.setLevel(logging.CRITICAL)
        self.yf_logger.disabled = True

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

                # 비동기로 실행
                df = await asyncio.to_thread(
                    self._download_yahoo_internal,
                    ticker,
                    days,
                    interval
                )

                return df

        except Exception as e:
            if self.verbose:
                self._log(f"⚠️  Yahoo 오류 ({ticker}): {e}", "yellow")
            return None

        finally:
            # 로그 레벨 복원
            self.yf_logger.setLevel(self.original_log_level)
            self.yf_logger.disabled = False

    def _download_yahoo_internal(
        self,
        ticker: str,
        days: int,
        interval: str
    ) -> Optional[pd.DataFrame]:
        """
        Yahoo Finance 내부 다운로드 (동기)

        Args:
            ticker: 전체 티커
            days: 조회 일수
            interval: 간격

        Returns:
            DataFrame 또는 None
        """
        import sys
        from io import StringIO

        # stderr 임시 리다이렉트 (yfinance의 "possibly delisted" 메시지 억제)
        old_stderr = sys.stderr
        sys.stderr = StringIO()

        # 모든 yfinance 관련 로거 비활성화
        yf_loggers = [
            logging.getLogger('yfinance'),
            logging.getLogger('yfinance.base_downloader'),
            logging.getLogger('yfinance.data'),
            logging.getLogger('yfinance.utils'),
            logging.getLogger('peewee')  # yfinance가 사용하는 peewee 로거도 비활성화
        ]
        original_levels = [(logger, logger.level, logger.disabled) for logger in yf_loggers]

        for logger in yf_loggers:
            logger.setLevel(logging.CRITICAL)
            logger.disabled = True

        try:
            stock = yf.Ticker(ticker)
            df = stock.history(
                period=f"{days}d",
                interval=interval,
                progress=False
            )

            if df.empty:
                return None

            # 컬럼명 소문자 변환
            df.reset_index(inplace=True)
            df.columns = [col.lower() for col in df.columns]

            # 음수/0 가격 필터링
            df = self._clean_price_data(df, ticker)

            # 최소 데이터 개수 체크
            return df if len(df) >= 10 else None

        except Exception:
            return None
        finally:
            # stderr 복원
            sys.stderr = old_stderr

            # 로거 상태 복원
            for logger, level, disabled in original_levels:
                logger.setLevel(level)
                logger.disabled = disabled

    def _clean_price_data(
        self,
        df: pd.DataFrame,
        identifier: str
    ) -> pd.DataFrame:
        """
        가격 데이터 정제 (음수/0 제거)

        Args:
            df: 원본 DataFrame
            identifier: 종목 식별자 (로그용)

        Returns:
            정제된 DataFrame
        """
        if df is None or df.empty:
            return df

        if 'close' not in df.columns:
            return df

        # 음수 또는 0인 행 찾기
        invalid_mask = df['close'] <= 0
        invalid_count = invalid_mask.sum()

        if invalid_count > 0:
            self._log(
                f"⚠️  {identifier}: {invalid_count}개 비정상 가격 데이터 제거",
                "yellow"
            )
            df = df[~invalid_mask].copy()

        return df

    def fetch_sync(
        self,
        stock_code: str,
        days: int = 7,
        source: Literal['auto', 'kiwoom', 'yahoo'] = 'auto',
        interval: str = '5m'
    ) -> Optional[pd.DataFrame]:
        """
        동기 버전 fetch (편의 함수)

        Args:
            stock_code: 종목 코드
            days: 조회 일수
            source: 데이터 소스
            interval: 간격

        Returns:
            DataFrame 또는 None

        Example:
            >>> fetcher = StockDataFetcher()
            >>> data = fetcher.fetch_sync('005930')
        """
        return asyncio.run(self.fetch(stock_code, days, source, interval))


# 하위 호환성을 위한 함수 (기존 코드와 호환)
def download_stock_data_sync(ticker: str, days: int = 7) -> Optional[pd.DataFrame]:
    """
    레거시 함수 (하위 호환성)

    DEPRECATED: StockDataFetcher 클래스 사용 권장

    Args:
        ticker: 전체 티커 (예: '005930.KS')
        days: 조회 일수

    Returns:
        DataFrame 또는 None
    """
    fetcher = StockDataFetcher(verbose=False)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            stock = yf.Ticker(ticker)
            df = stock.history(period=f"{days}d", interval="5m", progress=False)

            if df.empty:
                return None

            df.reset_index(inplace=True)
            df.columns = [col.lower() for col in df.columns]

            # 음수/0 가격 필터링
            if 'close' in df.columns:
                df = df[df['close'] > 0].copy()

            return df if len(df) >= 10 else None

    except Exception:
        return None


async def download_stock_data_yahoo(
    ticker: str,
    days: int = 7,
    try_kq: bool = True
) -> Optional[pd.DataFrame]:
    """
    레거시 함수 (하위 호환성)

    DEPRECATED: StockDataFetcher 클래스 사용 권장

    Args:
        ticker: 종목 코드 (6자리)
        days: 조회 일수
        try_kq: .KQ 시도 여부

    Returns:
        DataFrame 또는 None
    """
    fetcher = StockDataFetcher(verbose=False)
    return await fetcher._fetch_from_yahoo(ticker, days, '5m')
