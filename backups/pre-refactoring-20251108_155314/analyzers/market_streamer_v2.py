# market_streamer_v2.py
"""
v2 버전: Yahoo Finance 폴백 및 .KS/.KQ 자동 전환

주요 기능:
- 오전장 시작 시 데이터 부족 → Yahoo Finance에서 과거 데이터 가져오기
- 종목 검색 실패 시 .KS → .KQ 자동 전환
- EWMA 기반 거래량 z-score 계산
- 비동기 처리
- 가격 캐싱
"""
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
import pytz
import yfinance as yf
import logging
from typing import Optional, Dict
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class MarketDataStreamerV2:
    """Enhanced streamer with Yahoo Finance fallback

    Features:
      - Adaptive EWMA z-score for volumes
      - Limited concurrency for external requests
      - Yahoo Finance fallback with .KS/.KQ handling
      - Pre/post market estimated volume
    """

    def __init__(self, broker=None, timeframe: str = '1min', max_concurrency: int = 6):
        """
        초기화

        Args:
            broker: 브로커 어댑터 (Kiwoom API 등)
            timeframe: 타임프레임 ('1min', '5min', '15min', '1hour')
            max_concurrency: 최대 동시 요청 수
        """
        self.broker = broker
        self.timeframe = timeframe
        self.latest_prices = {}
        self.price_timestamps = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._executor = ThreadPoolExecutor(max_workers=4)

    async def get_current_price(self, exchange_code: str, symbol: str) -> float:
        """현재가 조회 (캐싱 포함)

        Args:
            exchange_code: 거래소 코드 (예: 'KRX')
            symbol: 종목 코드

        Returns:
            현재가
        """
        # 캐시 확인 (30초 이내)
        if symbol in self.latest_prices and symbol in self.price_timestamps:
            elapsed = (datetime.now() - self.price_timestamps[symbol]).total_seconds()
            if elapsed < 30:
                logger.debug(f"{symbol} 캐시 가격 사용: {self.latest_prices[symbol]} ({elapsed:.0f}초 전)")
                return self.latest_prices[symbol]

        # Yahoo Finance 시도 (.KS/.KQ 처리)
        price = await self._get_price_yfinance(symbol)
        if price and price > 0:
            self._update_price_cache(symbol, price)
            return price

        # Broker API 시도
        if self.broker:
            try:
                price_data = await self.broker.get_current_price(symbol)
                if price_data and price_data > 0:
                    self._update_price_cache(symbol, price_data)
                    return price_data
            except Exception as e:
                logger.debug(f"Broker 가격 조회 실패: {e}")

        # 캐시된 가격 반환
        return self.latest_prices.get(symbol, 0.0)

    async def _get_price_yfinance(self, symbol: str) -> Optional[float]:
        """Yahoo Finance에서 가격 조회 (.KS/.KQ 자동 처리)"""
        async with self._semaphore:
            # .KS 시도
            price = await self._fetch_yf_price(f"{symbol}.KS")
            if price and price > 0:
                return price

            # .KQ 시도
            price = await self._fetch_yf_price(f"{symbol}.KQ")
            if price and price > 0:
                return price

        return None

    async def _fetch_yf_price(self, ticker_symbol: str) -> Optional[float]:
        """Yahoo Finance API 호출 (threadpool 사용)"""
        loop = asyncio.get_event_loop()
        try:
            ticker = await loop.run_in_executor(self._executor, yf.Ticker, ticker_symbol)
            info = await loop.run_in_executor(self._executor, lambda: ticker.info)

            # 시장 상태에 따라 가격 선택
            market_state = info.get('marketState', '')
            price = None

            if market_state == 'PRE':
                price = info.get('preMarketPrice') or info.get('currentPrice') or info.get('regularMarketPrice')
            elif market_state == 'POST':
                price = info.get('postMarketPrice') or info.get('currentPrice') or info.get('regularMarketPrice')
            elif market_state == 'REGULAR':
                price = info.get('currentPrice') or info.get('regularMarketPrice')
            else:
                price = info.get('regularMarketPrice') or info.get('currentPrice')

            if price and price > 0:
                logger.debug(f"{ticker_symbol} 가격: {price} (상태: {market_state})")
                return float(price)

        except Exception as e:
            logger.debug(f"{ticker_symbol} 조회 실패: {e}")

        return None

    async def get_price_data(self, exchange_code: str, symbol: str, limit: int = 300) -> pd.DataFrame:
        """가격 데이터 조회 (Broker → Yahoo Finance 폴백)

        Args:
            exchange_code: 거래소 코드
            symbol: 종목 코드
            limit: 데이터 개수

        Returns:
            DataFrame with columns: open, high, low, close, volume
        """
        logger.info(f"{symbol} 데이터 수집 중 (목표: {limit}개)...")

        # Broker API 시도
        if self.broker:
            try:
                df = await self._get_price_from_broker(exchange_code, symbol, limit)
                if not df.empty and len(df) >= limit * 0.5:  # 50% 이상 수집되면 성공
                    logger.info(f"{symbol} Broker에서 {len(df)}개 데이터 수집")
                    return df
                elif not df.empty:
                    logger.warning(f"{symbol} Broker 데이터 부족 ({len(df)}개) - Yahoo Finance 보충 시도")
            except Exception as e:
                logger.warning(f"Broker 데이터 수집 실패: {e}")

        # Yahoo Finance 폴백
        logger.info(f"{symbol} Yahoo Finance에서 데이터 수집...")
        df = await self._get_price_from_yfinance(symbol, limit)

        if df.empty:
            logger.error(f"{symbol} 데이터 수집 실패")
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

        logger.info(f"{symbol} Yahoo Finance에서 {len(df)}개 데이터 수집")
        return df

    async def _get_price_from_broker(self, exchange_code: str, symbol: str, limit: int) -> pd.DataFrame:
        """Broker API에서 데이터 조회"""
        # 예: Kiwoom API 호출
        # 실제 구현은 broker adapter에 따라 다름
        try:
            if hasattr(self.broker, 'get_minute_chart'):
                data = await self.broker.get_minute_chart(symbol, limit=limit)
                if data:
                    df = pd.DataFrame(data)
                    # 컬럼명 변환 (Kiwoom 형식 → 표준)
                    if 'dt' in df.columns and 'cur_prc' in df.columns:
                        df = df.rename(columns={
                            'open_pric': 'open',
                            'high_pric': 'high',
                            'low_pric': 'low',
                            'cur_prc': 'close',
                            'trde_qty': 'volume'
                        })
                        # 숫자 변환
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors='coerce')
                        return df[['open', 'high', 'low', 'close', 'volume']]
        except Exception as e:
            logger.debug(f"Broker 데이터 조회 실패: {e}")

        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

    async def _get_price_from_yfinance(self, symbol: str, limit: int) -> pd.DataFrame:
        """Yahoo Finance에서 데이터 조회 (.KS/.KQ 자동 처리)"""
        interval_map = {'1min': '1m', '5min': '5m', '15min': '15m', '1hour': '1h'}
        interval = interval_map.get(self.timeframe, '1m')

        # 데이터 기간 계산
        if interval in ['1m', '2m', '5m']:
            period = '7d'  # 1분봉은 최대 7일
        elif interval in ['15m', '30m']:
            period = '60d'
        else:
            period = '730d'

        # .KS 시도
        df = await self._fetch_yf_history(f"{symbol}.KS", period, interval)
        if not df.empty and len(df) >= limit * 0.3:
            return df.tail(limit)

        # .KQ 시도
        df = await self._fetch_yf_history(f"{symbol}.KQ", period, interval)
        if not df.empty and len(df) >= limit * 0.3:
            return df.tail(limit)

        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

    async def _fetch_yf_history(self, ticker_symbol: str, period: str, interval: str) -> pd.DataFrame:
        """Yahoo Finance history 조회 (threadpool)"""
        loop = asyncio.get_event_loop()
        try:
            ticker = await loop.run_in_executor(self._executor, yf.Ticker, ticker_symbol)
            data = await loop.run_in_executor(
                self._executor,
                lambda: ticker.history(period=period, interval=interval, prepost=True)
            )

            if data.empty:
                return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

            # 컬럼명 표준화
            data = data.rename(columns={
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            })

            return data[['open', 'high', 'low', 'close', 'volume']]

        except Exception as e:
            logger.debug(f"{ticker_symbol} 히스토리 조회 실패: {e}")
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

    def compute_ewma_volume_z(self, df: pd.DataFrame, span: int = 20) -> pd.Series:
        """EWMA 기반 거래량 z-score 계산

        Args:
            df: DataFrame with 'volume' column
            span: EWMA span

        Returns:
            z-score Series
        """
        if df.empty or 'volume' not in df.columns:
            return pd.Series(dtype=float)

        vol = df['volume'].fillna(0).astype(float)
        ewma_mean = vol.ewm(span=span, adjust=False).mean()
        ewma_var = vol.ewm(span=span, adjust=False).var()
        ewma_std = np.sqrt(ewma_var.replace(0, np.nan)).fillna(1.0)

        z_score = (vol - ewma_mean) / ewma_std
        return z_score

    def _update_price_cache(self, symbol: str, price: float):
        """가격 캐시 업데이트"""
        self.latest_prices[symbol] = price
        self.price_timestamps[symbol] = datetime.now()

    def get_cached_price(self, symbol: str) -> Optional[float]:
        """캐시된 가격 반환"""
        return self.latest_prices.get(symbol)


if __name__ == "__main__":
    # 간단한 테스트
    import sys
    logging.basicConfig(level=logging.INFO)

    async def test():
        streamer = MarketDataStreamerV2()

        # 현재가 조회 테스트
        symbol = "005930"  # 삼성전자
        price = await streamer.get_current_price("KRX", symbol)
        print(f"{symbol} 현재가: {price}")

        # 데이터 조회 테스트
        df = await streamer.get_price_data("KRX", symbol, limit=100)
        print(f"\n데이터 개수: {len(df)}")
        print(df.tail())

        # 거래량 z-score
        if not df.empty:
            z_score = streamer.compute_ewma_volume_z(df)
            print(f"\n최근 거래량 z-score: {z_score.tail()}")

    asyncio.run(test())
