#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils/feature_calculator.py

백테스트 및 실시간 거래를 위한 Feature 계산
"""

import asyncio
import logging
from typing import Dict, Optional, List
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FeatureCalculator:
    """
    종목별 Feature 실시간 계산

    Features:
    - vwap_backtest_winrate: VWAP 백테스트 승률 (from PreTradeValidator)
    - vwap_avg_profit: VWAP 평균 수익률 (from PreTradeValidator)
    - current_vwap_distance: 현재가-VWAP 괴리율
    - volume_z_score: 거래량 Z-score (20일 평균 대비)
    - recent_return_5d: 최근 5일 수익률
    - market_volatility: 시장 변동성 (KOSPI ATR)
    - sector_strength: 업종 강도
    - price_momentum: 가격 모멘텀
    """

    def __init__(self, api_client):
        """
        Args:
            api_client: KiwoomRESTClient 인스턴스
        """
        self.api_client = api_client

        # 캐시 (API 호출 최소화)
        self.market_volatility_cache = None
        self.market_volatility_cache_time = None
        self.cache_ttl = 3600  # 1시간

    async def calculate_all_features(
        self,
        stock_code: str,
        vwap_stats: Optional[Dict] = None,
        current_price: Optional[float] = None,
        current_vwap: Optional[float] = None
    ) -> Dict[str, float]:
        """
        종목의 모든 feature 계산

        Args:
            stock_code: 종목코드
            vwap_stats: VWAP 백테스트 통계 (PreTradeValidator 결과)
            current_price: 현재가 (None이면 API 조회)
            current_vwap: 현재 VWAP (None이면 계산)

        Returns:
            Feature 딕셔너리
        """
        try:
            # 1. VWAP 백테스트 통계 (외부에서 제공 or 기본값)
            vwap_backtest_winrate = 0.5
            vwap_avg_profit = 0.0

            if vwap_stats:
                vwap_backtest_winrate = vwap_stats.get('win_rate', 50.0) / 100.0
                vwap_avg_profit = vwap_stats.get('avg_profit_pct', 0.0)

            # 2. 현재가 조회
            if current_price is None:
                current_price = await self._get_current_price(stock_code)
                if current_price is None:
                    logger.warning(f"{stock_code}: 현재가 조회 실패, 기본값 사용")
                    return self._get_default_features()

            # 3. 일봉 데이터 조회 (최근 30일)
            chart_data = await self._get_recent_chart_data(stock_code, days=30)

            if not chart_data:
                logger.warning(f"{stock_code}: 차트 데이터 조회 실패, 기본값 사용")
                return self._get_default_features()

            # 4. VWAP 계산
            if current_vwap is None:
                current_vwap = self._calculate_vwap_from_chart(chart_data)

            # 5. Feature 계산
            features = {
                'vwap_backtest_winrate': vwap_backtest_winrate,
                'vwap_avg_profit': vwap_avg_profit,
                'current_vwap_distance': self._calculate_vwap_distance(current_price, current_vwap),
                'volume_z_score': self._calculate_volume_z_score(chart_data),
                'recent_return_5d': self._calculate_recent_return(chart_data, days=5),
                'market_volatility': await self._calculate_market_volatility(),
                'sector_strength': await self._calculate_sector_strength(stock_code),
                'price_momentum': self._calculate_price_momentum(chart_data),
            }

            logger.info(f"{stock_code} features: vwap_dist={features['current_vwap_distance']:.2f}%, "
                       f"vol_z={features['volume_z_score']:.2f}, "
                       f"ret_5d={features['recent_return_5d']:.2f}%")

            return features

        except Exception as e:
            logger.error(f"{stock_code} feature 계산 실패: {e}", exc_info=True)
            return self._get_default_features()

    async def _get_current_price(self, stock_code: str) -> Optional[float]:
        """현재가 조회"""
        try:
            # 최근 10일 데이터 조회 (주말 고려, 마지막 종가를 현재가로 사용)
            chart_data = await self.api_client.get_historical_data_for_backtest(
                stock_code=stock_code,
                start_date=datetime.now() - timedelta(days=10),
                end_date=datetime.now(),
                interval='D'
            )

            if chart_data and len(chart_data) > 0:
                last_data = chart_data[-1]
                # 키움 API 응답 필드명 확인 필요
                price = float(last_data.get('stk_close_prc', last_data.get('close', 0)))
                if price > 0:
                    logger.debug(f"{stock_code} 현재가: {price}")
                    return price

            logger.warning(f"{stock_code} 현재가 데이터 없음 (조회: {len(chart_data) if chart_data else 0}건)")
            return None

        except Exception as e:
            logger.error(f"{stock_code} 현재가 조회 실패: {e}")
            return None

    async def _get_recent_chart_data(
        self,
        stock_code: str,
        days: int = 30
    ) -> Optional[List[Dict]]:
        """최근 N일 차트 데이터 조회"""
        try:
            # 주말/공휴일 고려하여 충분한 여유 (+20일)
            start_date = datetime.now() - timedelta(days=days + 20)
            end_date = datetime.now()

            chart_data = await self.api_client.get_historical_data_for_backtest(
                stock_code=stock_code,
                start_date=start_date,
                end_date=end_date,
                interval='D'
            )

            if chart_data:
                logger.debug(f"{stock_code} 차트 데이터: {len(chart_data)}건 조회")
            else:
                logger.warning(f"{stock_code} 차트 데이터 없음")

            return chart_data

        except Exception as e:
            logger.error(f"{stock_code} 차트 데이터 조회 실패: {e}")
            return None

    def _calculate_vwap_from_chart(self, chart_data: List[Dict]) -> float:
        """차트 데이터에서 VWAP 계산 (최근 20일)"""
        try:
            recent_data = chart_data[-20:]  # 최근 20일

            total_volume = 0
            total_vwap_volume = 0

            for data in recent_data:
                # 키움 API 필드명 확인 필요
                close = float(data.get('stk_close_prc', data.get('close', 0)))
                high = float(data.get('stk_high_prc', data.get('high', close)))
                low = float(data.get('stk_low_prc', data.get('low', close)))
                volume = float(data.get('volume', data.get('stk_trd_qty', 0)))

                if volume > 0:
                    typical_price = (close + high + low) / 3
                    total_vwap_volume += typical_price * volume
                    total_volume += volume

            if total_volume > 0:
                return total_vwap_volume / total_volume
            else:
                # 평균 종가로 폴백
                prices = [float(d.get('stk_close_prc', d.get('close', 0))) for d in recent_data]
                return np.mean(prices) if prices else 0

        except Exception as e:
            logger.error(f"VWAP 계산 실패: {e}")
            return 0

    def _calculate_vwap_distance(self, current_price: float, vwap: float) -> float:
        """현재가-VWAP 괴리율 (%)"""
        if vwap <= 0:
            return 0.0
        return (current_price - vwap) / vwap * 100

    def _calculate_volume_z_score(self, chart_data: List[Dict]) -> float:
        """거래량 Z-score (20일 평균 대비)"""
        try:
            if len(chart_data) < 20:
                return 0.0

            # 최근 20일 거래량
            volumes = []
            for data in chart_data[-20:]:
                volume = float(data.get('volume', data.get('stk_trd_qty', 0)))
                volumes.append(volume)

            if not volumes or len(volumes) < 2:
                return 0.0

            current_volume = volumes[-1]
            mean_volume = np.mean(volumes[:-1])  # 현재일 제외한 평균
            std_volume = np.std(volumes[:-1])

            if std_volume <= 0:
                return 0.0

            z_score = (current_volume - mean_volume) / std_volume
            return z_score

        except Exception as e:
            logger.error(f"거래량 Z-score 계산 실패: {e}")
            return 0.0

    def _calculate_recent_return(self, chart_data: List[Dict], days: int = 5) -> float:
        """최근 N일 수익률 (%)"""
        try:
            if len(chart_data) < days + 1:
                return 0.0

            # N일 전 종가
            start_price = float(chart_data[-(days+1)].get('stk_close_prc',
                                                          chart_data[-(days+1)].get('close', 0)))
            # 현재 종가
            end_price = float(chart_data[-1].get('stk_close_prc',
                                                  chart_data[-1].get('close', 0)))

            if start_price <= 0:
                return 0.0

            return (end_price - start_price) / start_price * 100

        except Exception as e:
            logger.error(f"최근 수익률 계산 실패: {e}")
            return 0.0

    async def _calculate_market_volatility(self) -> float:
        """시장 변동성 (KOSPI ATR, %)"""
        try:
            # 캐시 확인
            if (self.market_volatility_cache is not None and
                self.market_volatility_cache_time is not None and
                (datetime.now() - self.market_volatility_cache_time).total_seconds() < self.cache_ttl):
                return self.market_volatility_cache

            # KOSPI 지수 데이터 조회 (코드: "0001" 또는 특수 코드)
            # 키움 API에서 KOSPI 조회 방법 확인 필요
            # 현재는 기본값 반환
            volatility = 15.0  # 기본값 (%)

            # TODO: 실제 KOSPI 데이터로 ATR 계산
            # kospi_data = await self._get_recent_chart_data("0001", days=20)
            # volatility = self._calculate_atr(kospi_data)

            # 캐시 저장
            self.market_volatility_cache = volatility
            self.market_volatility_cache_time = datetime.now()

            return volatility

        except Exception as e:
            logger.error(f"시장 변동성 계산 실패: {e}")
            return 15.0  # 기본값

    async def _calculate_sector_strength(self, stock_code: str) -> float:
        """업종 강도"""
        try:
            # TODO: 종목의 업종 확인 및 업종 지수 수익률 계산
            # 현재는 기본값 반환
            return 0.5

        except Exception as e:
            logger.error(f"업종 강도 계산 실패: {e}")
            return 0.5

    def _calculate_price_momentum(self, chart_data: List[Dict]) -> float:
        """가격 모멘텀 (20일 이동평균 대비 현재가 위치)"""
        try:
            if len(chart_data) < 20:
                return 0.0

            # 최근 20일 종가
            prices = []
            for data in chart_data[-20:]:
                price = float(data.get('stk_close_prc', data.get('close', 0)))
                prices.append(price)

            if not prices:
                return 0.0

            current_price = prices[-1]
            ma20 = np.mean(prices)

            if ma20 <= 0:
                return 0.0

            # 20일 이동평균 대비 괴리율
            momentum = (current_price - ma20) / ma20 * 100

            return momentum

        except Exception as e:
            logger.error(f"가격 모멘텀 계산 실패: {e}")
            return 0.0

    def _calculate_atr(self, chart_data: List[Dict], period: int = 14) -> float:
        """ATR (Average True Range) 계산"""
        try:
            if len(chart_data) < period + 1:
                return 0.0

            true_ranges = []

            for i in range(1, len(chart_data)):
                high = float(chart_data[i].get('stk_high_prc', chart_data[i].get('high', 0)))
                low = float(chart_data[i].get('stk_low_prc', chart_data[i].get('low', 0)))
                prev_close = float(chart_data[i-1].get('stk_close_prc',
                                                       chart_data[i-1].get('close', 0)))

                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)

            if not true_ranges:
                return 0.0

            # 최근 period일의 ATR
            atr = np.mean(true_ranges[-period:])

            # 종가 대비 ATR (%)
            current_price = float(chart_data[-1].get('stk_close_prc',
                                                     chart_data[-1].get('close', 1)))

            if current_price > 0:
                return (atr / current_price) * 100
            else:
                return 0.0

        except Exception as e:
            logger.error(f"ATR 계산 실패: {e}")
            return 0.0

    def _get_default_features(self) -> Dict[str, float]:
        """기본 feature 값 (계산 실패 시)"""
        return {
            'vwap_backtest_winrate': 0.5,
            'vwap_avg_profit': 0.0,
            'current_vwap_distance': 0.0,
            'volume_z_score': 0.0,
            'recent_return_5d': 0.0,
            'market_volatility': 15.0,
            'sector_strength': 0.5,
            'price_momentum': 0.0,
        }


async def main():
    """테스트 코드"""
    import os
    from dotenv import load_dotenv
    from core.kiwoom_rest_client import KiwoomRESTClient

    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    if not app_key or not app_secret:
        print("환경변수 설정 필요")
        return

    async with KiwoomRESTClient(app_key, app_secret) as client:
        calculator = FeatureCalculator(client)

        # 삼성전자 feature 계산
        features = await calculator.calculate_all_features(
            stock_code="005930",
            vwap_stats={'win_rate': 65.0, 'avg_profit_pct': 2.3}
        )

        print("\n=== 삼성전자 Features ===")
        for key, value in features.items():
            print(f"{key}: {value:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
