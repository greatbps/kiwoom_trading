#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategy/vwap_filter.py

Dynamic VWAP Backtesting & Filtering System
- Regime-aware 필터링 (시장 국면 반영)
- Dynamic window 백테스트 (50~150일 가변)
- 변동성 기반 파라미터 조정
- 성과 캐싱 최적화
- 메트릭 수집
"""

import asyncio
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Local imports
import sys
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.cache import LRUCache, PersistentCache, async_cached


class MarketRegime(Enum):
    """시장 국면"""
    BULL = "bull"              # 강세장
    BEAR = "bear"              # 약세장
    SIDEWAYS = "sideways"      # 횡보장
    VOLATILE = "volatile"      # 고변동성
    LOW_VOLATILITY = "low_vol" # 저변동성


@dataclass
class RegimeConfig:
    """국면별 설정"""
    regime: MarketRegime
    window_days: int           # 백테스트 기간
    vwap_threshold: float      # VWAP 임계값
    volume_threshold: float    # 거래량 임계값
    min_trades: int            # 최소 거래 수


@dataclass
class VWAPBacktestResult:
    """VWAP 백테스트 결과"""
    symbol: str
    regime: MarketRegime
    window_days: int

    # 성과 지표
    total_signals: int = 0
    winning_signals: int = 0
    losing_signals: int = 0

    avg_return: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0

    # VWAP 통계
    avg_distance_from_vwap: float = 0.0  # VWAP과의 평균 거리
    vwap_breakout_success_rate: float = 0.0

    # 메타데이터
    tested_at: datetime = field(default_factory=datetime.now)
    cache_key: str = ""

    @property
    def is_acceptable(self) -> bool:
        """백테스트 결과가 허용 가능한지 확인"""
        return (
            self.win_rate >= 0.55 and
            self.profit_factor >= 1.2 and
            self.total_signals >= 10
        )

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {
            'symbol': self.symbol,
            'regime': self.regime.value,
            'window_days': self.window_days,
            'total_signals': self.total_signals,
            'winning_signals': self.winning_signals,
            'losing_signals': self.losing_signals,
            'avg_return': self.avg_return,
            'win_rate': self.win_rate,
            'profit_factor': self.profit_factor,
            'sharpe_ratio': self.sharpe_ratio,
            'avg_distance_from_vwap': self.avg_distance_from_vwap,
            'vwap_breakout_success_rate': self.vwap_breakout_success_rate,
            'tested_at': self.tested_at.isoformat(),
            'is_acceptable': self.is_acceptable,
        }


class VWAPFilter:
    """
    동적 VWAP 필터

    주요 기능:
    - Regime-aware 백테스트 (시장 국면 반영)
    - Dynamic window (50~150일 가변)
    - 변동성 기반 파라미터 조정
    - 성과 캐싱 (중복 검증 최소화)
    - 비동기 병렬 처리

    Example:
        filter = VWAPFilter(cache_enabled=True)

        # 시장 국면 감지
        regime = await filter.detect_market_regime("SPY")

        # VWAP 백테스트
        result = await filter.backtest_symbol("AAPL", regime)

        # 필터링
        if result.is_acceptable:
            print(f"{result.symbol} passed VWAP filter")
    """

    def __init__(
        self,
        default_window: int = 100,
        min_window: int = 50,
        max_window: int = 150,
        cache_enabled: bool = True,
        cache_ttl: int = 3600,  # 1시간
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            default_window: 기본 백테스트 기간 (일)
            min_window: 최소 백테스트 기간
            max_window: 최대 백테스트 기간
            cache_enabled: 캐시 활성화 여부
            cache_ttl: 캐시 TTL (초)
            logger: 로거
        """
        self.default_window = default_window
        self.min_window = min_window
        self.max_window = max_window
        self.cache_enabled = cache_enabled
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # 캐시
        if cache_enabled:
            self.memory_cache = LRUCache(max_size=500, default_ttl=cache_ttl)
            self.persistent_cache = PersistentCache(
                db_path=PROJECT_ROOT / "data" / "vwap_backtest_cache.db"
            )

        # 국면별 설정
        self.regime_configs = {
            MarketRegime.BULL: RegimeConfig(
                regime=MarketRegime.BULL,
                window_days=80,
                vwap_threshold=1.02,  # VWAP 대비 2% 이상
                volume_threshold=1.5,  # 평균 거래량의 1.5배
                min_trades=15,
            ),
            MarketRegime.BEAR: RegimeConfig(
                regime=MarketRegime.BEAR,
                window_days=60,
                vwap_threshold=0.98,  # VWAP 대비 -2% 이하
                volume_threshold=1.3,
                min_trades=10,
            ),
            MarketRegime.SIDEWAYS: RegimeConfig(
                regime=MarketRegime.SIDEWAYS,
                window_days=100,
                vwap_threshold=1.01,
                volume_threshold=1.2,
                min_trades=20,
            ),
            MarketRegime.VOLATILE: RegimeConfig(
                regime=MarketRegime.VOLATILE,
                window_days=50,  # 짧은 기간
                vwap_threshold=1.03,
                volume_threshold=2.0,
                min_trades=12,
            ),
            MarketRegime.LOW_VOLATILITY: RegimeConfig(
                regime=MarketRegime.LOW_VOLATILITY,
                window_days=120,  # 긴 기간
                vwap_threshold=1.01,
                volume_threshold=1.1,
                min_trades=25,
            ),
        }

        # 메트릭
        self.metrics = {
            'total_backtests': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'avg_backtest_time': 0.0,
            'regime_distribution': {regime.value: 0 for regime in MarketRegime},
        }

    async def detect_market_regime(
        self,
        market_index: str = "^GSPC",  # S&P 500
        lookback_days: int = 30,
    ) -> MarketRegime:
        """
        시장 국면 감지

        Args:
            market_index: 시장 지수 심볼 (SPY, ^GSPC 등)
            lookback_days: 분석 기간 (일)

        Returns:
            감지된 시장 국면
        """
        try:
            # TODO: 실제 시장 데이터 조회
            # 현재는 시뮬레이션
            market_data = await self._fetch_market_data(market_index, lookback_days)

            if market_data is None:
                self.logger.warning("Market data unavailable, using SIDEWAYS regime")
                return MarketRegime.SIDEWAYS

            # 변동성 계산
            returns = market_data['returns']
            volatility = returns.std() * np.sqrt(252)  # 연율화

            # 추세 계산 (MA20 vs MA50)
            ma20 = market_data['close'].tail(20).mean()
            ma50 = market_data['close'].tail(50).mean() if len(market_data) >= 50 else ma20

            # 수익률 계산
            total_return = (market_data['close'].iloc[-1] / market_data['close'].iloc[0]) - 1

            # 국면 판단
            if volatility > 0.25:  # 25% 이상
                regime = MarketRegime.VOLATILE
            elif volatility < 0.10:  # 10% 이하
                regime = MarketRegime.LOW_VOLATILITY
            elif total_return > 0.05 and ma20 > ma50:  # 5% 이상 상승 & 상승 추세
                regime = MarketRegime.BULL
            elif total_return < -0.05 and ma20 < ma50:  # 5% 이상 하락 & 하락 추세
                regime = MarketRegime.BEAR
            else:
                regime = MarketRegime.SIDEWAYS

            # 메트릭 업데이트
            self.metrics['regime_distribution'][regime.value] += 1

            self.logger.info(
                f"Market Regime Detected: {regime.value.upper()} "
                f"(Volatility: {volatility:.1%}, Return: {total_return:.1%})"
            )

            return regime

        except Exception as e:
            self.logger.error(f"Failed to detect market regime: {e}")
            return MarketRegime.SIDEWAYS

    async def _fetch_market_data(
        self,
        symbol: str,
        days: int
    ) -> Optional[pd.DataFrame]:
        """
        시장 데이터 조회 (시뮬레이션)

        Args:
            symbol: 심볼
            days: 조회 기간 (일)

        Returns:
            OHLCV 데이터프레임
        """
        # TODO: 실제 API 연동 (Yahoo Finance, KIS API 등)
        # 현재는 랜덤 데이터 생성
        await asyncio.sleep(0.1)

        dates = pd.date_range(end=datetime.now(), periods=days)

        # 랜덤 가격 생성 (상승 추세 시뮬레이션)
        base_price = 100
        drift = 0.0005  # 상승 드리프트
        volatility = 0.02

        returns = np.random.normal(drift, volatility, days)
        prices = base_price * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            'date': dates,
            'close': prices,
            'volume': np.random.randint(1000000, 5000000, days),
            'returns': returns,
        })

        return df

    async def backtest_symbol(
        self,
        symbol: str,
        regime: Optional[MarketRegime] = None,
    ) -> VWAPBacktestResult:
        """
        종목 VWAP 백테스트

        Args:
            symbol: 종목 코드
            regime: 시장 국면 (None이면 자동 감지)

        Returns:
            백테스트 결과
        """
        start_time = asyncio.get_event_loop().time()

        # 국면 감지
        if regime is None:
            regime = await self.detect_market_regime()

        # 국면별 설정
        config = self.regime_configs[regime]

        # 캐시 키
        cache_key = f"vwap_backtest:{symbol}:{regime.value}:{config.window_days}"

        # 캐시 조회
        if self.cache_enabled:
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                self.metrics['cache_hits'] += 1
                self.logger.debug(f"Cache hit: {symbol}")
                return cached

        self.metrics['cache_misses'] += 1

        # 백테스트 실행
        result = await self._run_backtest(symbol, config)
        result.cache_key = cache_key

        # 캐시 저장
        if self.cache_enabled:
            self._save_to_cache(cache_key, result)

        # 메트릭 업데이트
        elapsed = asyncio.get_event_loop().time() - start_time
        self.metrics['total_backtests'] += 1

        alpha = 0.2
        self.metrics['avg_backtest_time'] = (
            alpha * elapsed +
            (1 - alpha) * self.metrics['avg_backtest_time']
        )

        self.logger.info(
            f"Backtest {symbol}: Win Rate={result.win_rate:.1%}, "
            f"PF={result.profit_factor:.2f}, "
            f"Pass={result.is_acceptable}"
        )

        return result

    async def _run_backtest(
        self,
        symbol: str,
        config: RegimeConfig,
    ) -> VWAPBacktestResult:
        """
        실제 백테스트 실행

        Args:
            symbol: 종목 코드
            config: 국면별 설정

        Returns:
            백테스트 결과
        """
        # TODO: 실제 과거 데이터 조회 및 VWAP 계산
        # 현재는 시뮬레이션

        await asyncio.sleep(0.1)  # API 호출 시뮬레이션

        # 더미 백테스트 결과
        total_signals = np.random.randint(10, 50)
        win_rate = np.random.uniform(0.45, 0.75)
        winning_signals = int(total_signals * win_rate)
        losing_signals = total_signals - winning_signals

        avg_win = np.random.uniform(0.02, 0.06)
        avg_loss = np.random.uniform(0.01, 0.03)

        profit_factor = (winning_signals * avg_win) / (losing_signals * avg_loss) if losing_signals > 0 else 999
        avg_return = (winning_signals * avg_win - losing_signals * avg_loss) / total_signals

        result = VWAPBacktestResult(
            symbol=symbol,
            regime=config.regime,
            window_days=config.window_days,
            total_signals=total_signals,
            winning_signals=winning_signals,
            losing_signals=losing_signals,
            avg_return=avg_return,
            win_rate=win_rate,
            profit_factor=profit_factor,
            sharpe_ratio=np.random.uniform(0.5, 2.0),
            avg_distance_from_vwap=np.random.uniform(-0.02, 0.05),
            vwap_breakout_success_rate=np.random.uniform(0.5, 0.8),
        )

        return result

    def _get_from_cache(self, cache_key: str) -> Optional[VWAPBacktestResult]:
        """캐시에서 조회"""
        # 메모리 캐시 우선
        cached = self.memory_cache.get(cache_key)
        if cached is not None:
            return cached

        # 영구 캐시
        cached_dict = self.persistent_cache.get(cache_key)
        if cached_dict is not None:
            # 딕셔너리를 객체로 변환
            result = VWAPBacktestResult(
                symbol=cached_dict['symbol'],
                regime=MarketRegime(cached_dict['regime']),
                window_days=cached_dict['window_days'],
                total_signals=cached_dict['total_signals'],
                winning_signals=cached_dict['winning_signals'],
                losing_signals=cached_dict['losing_signals'],
                avg_return=cached_dict['avg_return'],
                win_rate=cached_dict['win_rate'],
                profit_factor=cached_dict['profit_factor'],
                sharpe_ratio=cached_dict['sharpe_ratio'],
                avg_distance_from_vwap=cached_dict['avg_distance_from_vwap'],
                vwap_breakout_success_rate=cached_dict['vwap_breakout_success_rate'],
            )

            # 메모리 캐시에도 저장
            self.memory_cache.set(cache_key, result)

            return result

        return None

    def _save_to_cache(self, cache_key: str, result: VWAPBacktestResult):
        """캐시에 저장"""
        # 메모리 캐시
        self.memory_cache.set(cache_key, result)

        # 영구 캐시
        self.persistent_cache.set(cache_key, result.to_dict(), ttl=86400)  # 24시간

    async def filter_symbols(
        self,
        symbols: List[str],
        regime: Optional[MarketRegime] = None,
        parallel: bool = True,
    ) -> List[Tuple[str, VWAPBacktestResult]]:
        """
        여러 종목 필터링

        Args:
            symbols: 종목 리스트
            regime: 시장 국면
            parallel: 병렬 처리 여부

        Returns:
            (종목, 백테스트 결과) 튜플 리스트 (합격한 종목만)
        """
        if regime is None:
            regime = await self.detect_market_regime()

        if parallel:
            # 병렬 실행
            tasks = [self.backtest_symbol(symbol, regime) for symbol in symbols]
            results = await asyncio.gather(*tasks)
        else:
            # 순차 실행
            results = []
            for symbol in symbols:
                result = await self.backtest_symbol(symbol, regime)
                results.append(result)

        # 합격한 종목만 필터링
        passed = [
            (symbol, result)
            for symbol, result in zip(symbols, results)
            if result.is_acceptable
        ]

        self.logger.info(
            f"VWAP Filter: {len(passed)}/{len(symbols)} symbols passed "
            f"({len(passed)/len(symbols)*100:.1f}%)"
        )

        return passed

    def get_stats(self) -> dict:
        """통계 반환"""
        return {
            **self.metrics,
            'cache_hit_rate': (
                self.metrics['cache_hits'] /
                (self.metrics['cache_hits'] + self.metrics['cache_misses'])
                if self.metrics['cache_hits'] + self.metrics['cache_misses'] > 0
                else 0
            ),
        }
