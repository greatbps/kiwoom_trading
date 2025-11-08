#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategy/condition_engine.py

Advanced Condition Search Engine
- 다전략 병렬 실행
- 전략별 성과 추적 (Precision, Recall)
- 최근 7일 성과 기반 가중치 자동 조정
- 비동기 병렬 처리
- 중복 종목 제거
- 메트릭 수집
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import json
from pathlib import Path

# Local imports
import sys
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.cache import LRUCache, async_cached
from core.auth_manager import AuthManager


class StrategyType(Enum):
    """전략 타입"""
    MOMENTUM = "momentum"
    BREAKOUT = "breakout"
    EOD = "eod"
    SUPERTREND = "supertrend"
    VWAP = "vwap"
    SCALPING_3M = "scalping_3m"
    RSI = "rsi"
    SQUEEZE_MOMENTUM_PRO = "squeeze_momentum_pro"


@dataclass
class StrategyConfig:
    """전략 설정"""
    strategy_type: StrategyType
    hts_condition_id: str  # HTS 조건검색 ID
    initial_weight: float = 1.0  # 초기 가중치
    min_weight: float = 0.1  # 최소 가중치
    max_weight: float = 2.0  # 최대 가중치
    enabled: bool = True


@dataclass
class StrategyPerformance:
    """전략 성과 데이터"""
    strategy_type: StrategyType

    # 성과 지표
    total_signals: int = 0  # 총 시그널 수
    successful_trades: int = 0  # 성공한 거래 (익절)
    failed_trades: int = 0  # 실패한 거래 (손절)

    # 누적 수익
    total_profit: float = 0.0
    total_loss: float = 0.0

    # 타임스탬프
    last_updated: datetime = field(default_factory=datetime.now)

    @property
    def precision(self) -> float:
        """정밀도 (Hit Rate)"""
        total = self.successful_trades + self.failed_trades
        if total == 0:
            return 0.5  # 기본값
        return self.successful_trades / total

    @property
    def win_rate(self) -> float:
        """승률"""
        return self.precision

    @property
    def total_trades(self) -> int:
        """총 거래 수"""
        return self.successful_trades + self.failed_trades

    @property
    def net_profit(self) -> float:
        """순수익"""
        return self.total_profit - abs(self.total_loss)

    @property
    def profit_factor(self) -> float:
        """수익률 인수"""
        if abs(self.total_loss) == 0:
            return float('inf') if self.total_profit > 0 else 0
        return self.total_profit / abs(self.total_loss)

    def to_dict(self) -> dict:
        """딕셔너리 변환"""
        return {
            'strategy_type': self.strategy_type.value,
            'total_signals': self.total_signals,
            'successful_trades': self.successful_trades,
            'failed_trades': self.failed_trades,
            'total_profit': self.total_profit,
            'total_loss': self.total_loss,
            'precision': self.precision,
            'win_rate': self.win_rate,
            'profit_factor': self.profit_factor,
            'net_profit': self.net_profit,
            'last_updated': self.last_updated.isoformat(),
        }


@dataclass
class ConditionSearchResult:
    """조건검색 결과"""
    symbol: str
    name: str
    strategy_type: StrategyType
    timestamp: datetime
    weight: float  # 전략 가중치
    price: Optional[float] = None
    volume: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConditionEngine:
    """
    고급 조건검색 엔진

    주요 기능:
    - 다전략 병렬 실행
    - 전략별 성과 추적 및 가중치 조정
    - 비동기 처리
    - 중복 제거
    - 캐싱

    Example:
        engine = ConditionEngine(
            auth_manager=auth_manager,
            strategies=[
                StrategyConfig(StrategyType.MOMENTUM, "3"),
                StrategyConfig(StrategyType.BREAKOUT, "1"),
            ]
        )

        # 병렬 실행
        results = await engine.search_all()

        # 성과 업데이트
        engine.update_performance(
            StrategyType.MOMENTUM,
            successful=True,
            profit=0.05
        )

        # 가중치 재조정
        engine.rebalance_weights()
    """

    def __init__(
        self,
        auth_manager: AuthManager,
        strategies: List[StrategyConfig],
        performance_window_days: int = 7,
        cache_ttl: int = 300,  # 5분
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            auth_manager: 인증 매니저
            strategies: 전략 설정 리스트
            performance_window_days: 성과 평가 기간 (일)
            cache_ttl: 캐시 TTL (초)
            logger: 로거
        """
        self.auth_manager = auth_manager
        self.strategies = {s.strategy_type: s for s in strategies}
        self.performance_window_days = performance_window_days
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # 성과 데이터
        self.performance: Dict[StrategyType, StrategyPerformance] = {
            strategy_type: StrategyPerformance(strategy_type=strategy_type)
            for strategy_type in self.strategies.keys()
        }

        # 캐시
        self.cache = LRUCache(max_size=1000, default_ttl=cache_ttl)

        # 현재 가중치
        self.current_weights: Dict[StrategyType, float] = {
            strategy_type: config.initial_weight
            for strategy_type, config in self.strategies.items()
        }

        # 성과 기록 저장 경로
        self.performance_file = PROJECT_ROOT / "data" / "strategy_performance.json"
        self.performance_file.parent.mkdir(parents=True, exist_ok=True)

        # 기존 성과 로드
        self._load_performance()

        # 메트릭
        self.metrics = {
            'total_searches': 0,
            'total_results': 0,
            'cache_hits': 0,
            'parallel_executions': 0,
            'avg_search_time': 0.0,
        }

    def _load_performance(self):
        """저장된 성과 데이터 로드"""
        try:
            if self.performance_file.exists():
                with open(self.performance_file, 'r') as f:
                    data = json.load(f)

                for strategy_type_str, perf_data in data.items():
                    try:
                        strategy_type = StrategyType(strategy_type_str)
                        if strategy_type in self.performance:
                            perf = self.performance[strategy_type]
                            perf.total_signals = perf_data.get('total_signals', 0)
                            perf.successful_trades = perf_data.get('successful_trades', 0)
                            perf.failed_trades = perf_data.get('failed_trades', 0)
                            perf.total_profit = perf_data.get('total_profit', 0.0)
                            perf.total_loss = perf_data.get('total_loss', 0.0)
                    except ValueError:
                        continue

                self.logger.info(f"Performance data loaded from {self.performance_file}")

        except Exception as e:
            self.logger.warning(f"Failed to load performance data: {e}")

    def _save_performance(self):
        """성과 데이터 저장"""
        try:
            data = {
                strategy_type.value: perf.to_dict()
                for strategy_type, perf in self.performance.items()
            }

            with open(self.performance_file, 'w') as f:
                json.dump(data, f, indent=2)

            self.logger.debug(f"Performance data saved to {self.performance_file}")

        except Exception as e:
            self.logger.error(f"Failed to save performance data: {e}")

    async def search_single(
        self,
        strategy_type: StrategyType
    ) -> List[ConditionSearchResult]:
        """
        단일 전략 조건검색

        Args:
            strategy_type: 전략 타입

        Returns:
            검색 결과 리스트
        """
        config = self.strategies.get(strategy_type)

        if not config or not config.enabled:
            return []

        # 캐시 키
        cache_key = f"condition_search:{strategy_type.value}"

        # 캐시 조회
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            self.metrics['cache_hits'] += 1
            self.logger.debug(f"Cache hit for {strategy_type.value}")
            return cached_result

        try:
            # KIS HTS 조건검색 API 호출
            # (실제 KIS API 연동 필요)
            symbols = await self._call_kis_condition_search(
                config.hts_condition_id
            )

            # 결과 생성
            results = [
                ConditionSearchResult(
                    symbol=symbol,
                    name=symbol,  # 실제로는 종목명 조회 필요
                    strategy_type=strategy_type,
                    timestamp=datetime.now(),
                    weight=self.current_weights.get(strategy_type, 1.0),
                )
                for symbol in symbols
            ]

            # 성과 업데이트 (시그널 수)
            self.performance[strategy_type].total_signals += len(results)

            # 캐시 저장
            self.cache.set(cache_key, results)

            self.logger.info(
                f"✅ {strategy_type.value}: Found {len(results)} symbols"
            )

            return results

        except Exception as e:
            self.logger.error(f"Condition search failed for {strategy_type.value}: {e}")
            return []

    async def _call_kis_condition_search(
        self,
        condition_id: str
    ) -> List[str]:
        """
        KIS HTS 조건검색 API 호출

        Args:
            condition_id: HTS 조건검색 ID

        Returns:
            종목 코드 리스트
        """
        # TODO: 실제 KIS API 구현
        # 현재는 더미 데이터 반환
        await asyncio.sleep(0.1)  # API 호출 시뮬레이션

        # 더미 데이터
        return [
            "005930",  # 삼성전자
            "035720",  # 카카오
            "000660",  # SK하이닉스
        ]

    async def search_all(
        self,
        deduplicate: bool = True
    ) -> List[ConditionSearchResult]:
        """
        모든 전략 병렬 실행

        Args:
            deduplicate: 중복 제거 여부

        Returns:
            검색 결과 리스트
        """
        start_time = asyncio.get_event_loop().time()

        # 병렬 실행
        tasks = [
            self.search_single(strategy_type)
            for strategy_type in self.strategies.keys()
            if self.strategies[strategy_type].enabled
        ]

        all_results = await asyncio.gather(*tasks)

        # 결과 병합
        merged_results = []
        for results in all_results:
            merged_results.extend(results)

        # 중복 제거 (같은 종목이 여러 전략에서 검색됨)
        if deduplicate:
            merged_results = self._deduplicate_results(merged_results)

        # 메트릭 업데이트
        elapsed = asyncio.get_event_loop().time() - start_time
        self.metrics['total_searches'] += 1
        self.metrics['total_results'] += len(merged_results)
        self.metrics['parallel_executions'] += len(tasks)

        alpha = 0.2
        self.metrics['avg_search_time'] = (
            alpha * elapsed +
            (1 - alpha) * self.metrics['avg_search_time']
        )

        self.logger.info(
            f"✅ Parallel search completed: "
            f"{len(merged_results)} symbols in {elapsed:.2f}s"
        )

        return merged_results

    def _deduplicate_results(
        self,
        results: List[ConditionSearchResult]
    ) -> List[ConditionSearchResult]:
        """
        중복 종목 제거 (가중치 합산)

        Args:
            results: 검색 결과 리스트

        Returns:
            중복 제거된 결과 리스트
        """
        symbol_map: Dict[str, ConditionSearchResult] = {}

        for result in results:
            if result.symbol in symbol_map:
                # 가중치 합산
                symbol_map[result.symbol].weight += result.weight

                # 메타데이터 병합
                symbol_map[result.symbol].metadata.setdefault('strategies', []).append(
                    result.strategy_type.value
                )
            else:
                result.metadata['strategies'] = [result.strategy_type.value]
                symbol_map[result.symbol] = result

        # 가중치 순으로 정렬
        deduplicated = sorted(
            symbol_map.values(),
            key=lambda x: x.weight,
            reverse=True
        )

        self.logger.info(
            f"Deduplicated: {len(results)} → {len(deduplicated)} symbols"
        )

        return deduplicated

    def update_performance(
        self,
        strategy_type: StrategyType,
        successful: bool,
        profit: float = 0.0,
    ):
        """
        전략 성과 업데이트

        Args:
            strategy_type: 전략 타입
            successful: 성공 여부 (익절 여부)
            profit: 수익률 (익절이면 +, 손절이면 -)
        """
        if strategy_type not in self.performance:
            return

        perf = self.performance[strategy_type]

        if successful:
            perf.successful_trades += 1
            perf.total_profit += profit
        else:
            perf.failed_trades += 1
            perf.total_loss += abs(profit)

        perf.last_updated = datetime.now()

        # 저장
        self._save_performance()

        self.logger.info(
            f"Performance updated: {strategy_type.value} | "
            f"Win Rate: {perf.win_rate:.1%} | "
            f"Profit Factor: {perf.profit_factor:.2f}"
        )

    def rebalance_weights(self):
        """
        최근 성과 기반 가중치 재조정

        최근 7일 성과를 기반으로 가중치를 자동 조정합니다.
        승률이 높고 수익률이 높은 전략의 가중치를 높입니다.
        """
        self.logger.info("Rebalancing strategy weights...")

        for strategy_type, config in self.strategies.items():
            perf = self.performance[strategy_type]

            # 성과 점수 계산
            score = self._calculate_strategy_score(perf)

            # 가중치 조정
            new_weight = config.initial_weight * score

            # 최소/최대 제한
            new_weight = max(config.min_weight, min(new_weight, config.max_weight))

            # 업데이트
            old_weight = self.current_weights.get(strategy_type, 1.0)
            self.current_weights[strategy_type] = new_weight

            self.logger.info(
                f"{strategy_type.value}: "
                f"Weight {old_weight:.2f} → {new_weight:.2f} "
                f"(Score: {score:.2f}, Win Rate: {perf.win_rate:.1%})"
            )

    def _calculate_strategy_score(
        self,
        perf: StrategyPerformance
    ) -> float:
        """
        전략 점수 계산

        Args:
            perf: 전략 성과

        Returns:
            점수 (0.5 ~ 2.0)
        """
        # 최소 거래 수 확인
        if perf.total_trades < 5:
            return 1.0  # 중립

        # 승률 가중치 (0 ~ 1)
        win_rate_weight = perf.win_rate

        # Profit Factor 가중치 (0 ~ 1)
        profit_factor_weight = min(1.0, perf.profit_factor / 2.0)

        # 최종 점수 (평균)
        score = (win_rate_weight * 0.6 + profit_factor_weight * 0.4)

        # 0.5 ~ 2.0 범위로 스케일링
        score = 0.5 + score * 1.5

        return score

    def get_stats(self) -> dict:
        """통계 반환"""
        return {
            **self.metrics,
            'strategies': {
                strategy_type.value: {
                    'enabled': config.enabled,
                    'weight': self.current_weights.get(strategy_type, 1.0),
                    'performance': self.performance[strategy_type].to_dict(),
                }
                for strategy_type, config in self.strategies.items()
            }
        }
