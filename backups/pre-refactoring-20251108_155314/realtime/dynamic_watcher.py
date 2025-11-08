#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
realtime/dynamic_watcher.py

Dynamic Watchlist Manager
- Adaptive 주기 조정 (종목별 변동성 기반)
- 최대 신규추가 개수 제한
- Cool-down 규칙 (재진입 방지)
- 시장 리스크 모드 반영
- 실시간 재검색
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

# Local imports
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class RiskMode(Enum):
    """리스크 모드"""
    NORMAL = "normal"       # 정상
    CAUTIOUS = "cautious"   # 주의
    DEFENSIVE = "defensive" # 방어
    HALT = "halt"           # 중단


@dataclass
class WatchItem:
    """감시 항목"""
    symbol: str
    name: str
    added_at: datetime
    last_checked: datetime
    check_interval: float  # 체크 주기 (초)

    # 변동성 기반 조정
    volatility: float = 0.0
    avg_volume: int = 0

    # 메타데이터
    metadata: Dict = field(default_factory=dict)

    @property
    def age(self) -> float:
        """추가된 후 경과 시간 (초)"""
        return (datetime.now() - self.added_at).total_seconds()

    @property
    def time_since_check(self) -> float:
        """마지막 체크 후 경과 시간 (초)"""
        return (datetime.now() - self.last_checked).total_seconds()

    @property
    def should_check(self) -> bool:
        """체크 시점 도달 여부"""
        return self.time_since_check >= self.check_interval


@dataclass
class MarketRiskMetrics:
    """시장 리스크 지표"""
    kospi_change_rate: float = 0.0      # KOSPI 변화율
    kosdaq_change_rate: float = 0.0     # KOSDAQ 변화율
    market_volatility: float = 0.0      # 시장 변동성
    fear_index: float = 0.0             # 공포 지수 (VIX 등)
    trading_halt_count: int = 0         # 거래정지 종목 수

    @property
    def risk_mode(self) -> RiskMode:
        """리스크 모드 판단"""
        # HALT: 극단적 상황
        if (abs(self.kospi_change_rate) > 0.05 or  # ±5% 이상
            abs(self.kosdaq_change_rate) > 0.07 or  # ±7% 이상
            self.market_volatility > 0.40):         # 변동성 40% 이상
            return RiskMode.HALT

        # DEFENSIVE: 방어적
        if (abs(self.kospi_change_rate) > 0.03 or  # ±3% 이상
            abs(self.kosdaq_change_rate) > 0.05 or  # ±5% 이상
            self.market_volatility > 0.30):
            return RiskMode.DEFENSIVE

        # CAUTIOUS: 주의
        if (abs(self.kospi_change_rate) > 0.02 or  # ±2% 이상
            abs(self.kosdaq_change_rate) > 0.03 or
            self.market_volatility > 0.20):
            return RiskMode.CAUTIOUS

        # NORMAL: 정상
        return RiskMode.NORMAL


class DynamicWatcher:
    """
    동적 Watchlist 관리자

    주요 기능:
    - Adaptive 주기 조정 (변동성 기반)
    - 최대 신규추가 제한
    - Cool-down 규칙
    - 시장 리스크 모드 반영
    - 실시간 재검색

    Example:
        watcher = DynamicWatcher(
            max_symbols=50,
            max_new_per_interval=3,
            base_check_interval=60.0
        )

        # 종목 추가
        await watcher.add_symbol("005930", "삼성전자")

        # 감시 루프 시작
        await watcher.start()

        # 콜백 등록
        watcher.on_check(lambda symbol, data: print(f"Checked: {symbol}"))
    """

    def __init__(
        self,
        max_symbols: int = 50,
        max_new_per_interval: int = 3,
        new_add_interval: int = 300,  # 5분
        base_check_interval: float = 60.0,  # 60초
        min_check_interval: float = 30.0,
        max_check_interval: float = 120.0,
        cooldown_period: int = 1800,  # 30분
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            max_symbols: 최대 감시 종목 수
            max_new_per_interval: 시간 간격당 최대 신규추가 수
            new_add_interval: 신규추가 제한 간격 (초)
            base_check_interval: 기본 체크 주기 (초)
            min_check_interval: 최소 체크 주기
            max_check_interval: 최대 체크 주기
            cooldown_period: 재진입 금지 기간 (초)
            logger: 로거
        """
        self.max_symbols = max_symbols
        self.max_new_per_interval = max_new_per_interval
        self.new_add_interval = new_add_interval
        self.base_check_interval = base_check_interval
        self.min_check_interval = min_check_interval
        self.max_check_interval = max_check_interval
        self.cooldown_period = cooldown_period
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # Watchlist
        self.watchlist: Dict[str, WatchItem] = {}

        # Cool-down 추적 (최근 제거된 종목)
        self.cooldown_symbols: Dict[str, datetime] = {}

        # 신규추가 추적 (최근 5분간 추가된 종목)
        self.recent_additions: deque = deque(maxlen=100)

        # 시장 리스크 지표
        self.market_risk = MarketRiskMetrics()

        # 콜백
        self._check_callbacks: List[Callable] = []
        self._add_callbacks: List[Callable] = []
        self._remove_callbacks: List[Callable] = []

        # 백그라운드 태스크
        self._watch_task: Optional[asyncio.Task] = None
        self._risk_monitor_task: Optional[asyncio.Task] = None

        # 메트릭
        self.metrics = {
            'total_checks': 0,
            'total_additions': 0,
            'total_removals': 0,
            'rejected_additions': 0,
            'avg_check_interval': base_check_interval,
        }

    # =====================================================
    # 콜백 등록
    # =====================================================

    def on_check(self, callback: Callable):
        """체크 이벤트 콜백"""
        self._check_callbacks.append(callback)

    def on_add(self, callback: Callable):
        """추가 이벤트 콜백"""
        self._add_callbacks.append(callback)

    def on_remove(self, callback: Callable):
        """제거 이벤트 콜백"""
        self._remove_callbacks.append(callback)

    # =====================================================
    # Watchlist 관리
    # =====================================================

    async def add_symbol(
        self,
        symbol: str,
        name: str,
        volatility: float = 0.0,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """
        종목 추가

        Args:
            symbol: 종목 코드
            name: 종목명
            volatility: 변동성
            metadata: 메타데이터

        Returns:
            추가 성공 여부
        """
        # 1. 중복 확인
        if symbol in self.watchlist:
            self.logger.debug(f"Symbol already in watchlist: {symbol}")
            return False

        # 2. Cool-down 확인
        if self._is_in_cooldown(symbol):
            cooldown_until = self.cooldown_symbols[symbol]
            remaining = (cooldown_until - datetime.now()).total_seconds()
            self.logger.info(
                f"Symbol {symbol} in cool-down for {remaining/60:.1f} more minutes"
            )
            self.metrics['rejected_additions'] += 1
            return False

        # 3. 최대 개수 확인
        if len(self.watchlist) >= self.max_symbols:
            self.logger.warning(f"Watchlist full ({self.max_symbols}), cannot add {symbol}")
            self.metrics['rejected_additions'] += 1
            return False

        # 4. 신규추가 제한 확인
        if not self._can_add_new_symbol():
            self.logger.warning(
                f"Max new additions per interval ({self.max_new_per_interval}) reached"
            )
            self.metrics['rejected_additions'] += 1
            return False

        # 5. 시장 리스크 모드 확인
        risk_mode = self.market_risk.risk_mode

        if risk_mode == RiskMode.HALT:
            self.logger.warning(f"Market in HALT mode, new additions suspended")
            self.metrics['rejected_additions'] += 1
            return False

        if risk_mode == RiskMode.DEFENSIVE:
            # 방어 모드에서는 신규추가 개수 반으로 제한
            if not self._can_add_new_symbol(max_count=self.max_new_per_interval // 2):
                self.logger.warning("Defensive mode: Limited new additions")
                self.metrics['rejected_additions'] += 1
                return False

        # 6. 체크 주기 계산 (변동성 기반)
        check_interval = self._calculate_check_interval(volatility)

        # 7. 추가
        item = WatchItem(
            symbol=symbol,
            name=name,
            added_at=datetime.now(),
            last_checked=datetime.now(),
            check_interval=check_interval,
            volatility=volatility,
            metadata=metadata or {},
        )

        self.watchlist[symbol] = item
        self.recent_additions.append((symbol, datetime.now()))

        # 메트릭 업데이트
        self.metrics['total_additions'] += 1

        # 콜백 호출
        for callback in self._add_callbacks:
            try:
                await callback(symbol, item)
            except Exception as e:
                self.logger.error(f"Add callback error: {e}")

        self.logger.info(
            f"✅ Added {symbol} ({name}) | "
            f"Check interval: {check_interval:.0f}s | "
            f"Watchlist size: {len(self.watchlist)}"
        )

        return True

    async def remove_symbol(self, symbol: str, add_to_cooldown: bool = True):
        """종목 제거"""
        if symbol not in self.watchlist:
            return

        item = self.watchlist[symbol]
        del self.watchlist[symbol]

        # Cool-down 추가
        if add_to_cooldown:
            self.cooldown_symbols[symbol] = datetime.now() + timedelta(
                seconds=self.cooldown_period
            )

        # 메트릭 업데이트
        self.metrics['total_removals'] += 1

        # 콜백 호출
        for callback in self._remove_callbacks:
            try:
                await callback(symbol, item)
            except Exception as e:
                self.logger.error(f"Remove callback error: {e}")

        self.logger.info(f"Removed {symbol} from watchlist")

    def _is_in_cooldown(self, symbol: str) -> bool:
        """Cool-down 확인"""
        if symbol not in self.cooldown_symbols:
            return False

        cooldown_until = self.cooldown_symbols[symbol]
        if datetime.now() >= cooldown_until:
            # Cool-down 만료
            del self.cooldown_symbols[symbol]
            return False

        return True

    def _can_add_new_symbol(self, max_count: Optional[int] = None) -> bool:
        """신규추가 가능 여부 확인"""
        if max_count is None:
            max_count = self.max_new_per_interval

        # 최근 interval 내 추가된 종목 수 계산
        cutoff_time = datetime.now() - timedelta(seconds=self.new_add_interval)

        recent_count = sum(
            1 for _, added_time in self.recent_additions
            if added_time >= cutoff_time
        )

        return recent_count < max_count

    def _calculate_check_interval(self, volatility: float) -> float:
        """
        변동성 기반 체크 주기 계산

        높은 변동성 → 짧은 주기
        낮은 변동성 → 긴 주기
        """
        # 변동성 0~1 범위로 정규화
        normalized_vol = min(1.0, max(0.0, volatility))

        # 변동성이 높을수록 짧은 주기
        interval = self.base_check_interval * (1 - 0.5 * normalized_vol)

        # 최소/최대 제한
        interval = max(self.min_check_interval, min(interval, self.max_check_interval))

        return interval

    # =====================================================
    # 감시 루프
    # =====================================================

    async def start(self):
        """감시 시작"""
        if self._watch_task is not None:
            self.logger.warning("Watcher already running")
            return

        self.logger.info("Starting dynamic watcher...")

        # 감시 루프 시작
        self._watch_task = asyncio.create_task(self._watch_loop())

        # 리스크 모니터 시작
        self._risk_monitor_task = asyncio.create_task(self._risk_monitor_loop())

    async def stop(self):
        """감시 종료"""
        self.logger.info("Stopping dynamic watcher...")

        # 태스크 종료
        tasks = [self._watch_task, self._risk_monitor_task]

        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self.logger.info("Dynamic watcher stopped")

    async def _watch_loop(self):
        """감시 루프"""
        self.logger.info("Watch loop started")

        try:
            while True:
                # 체크할 종목 확인
                symbols_to_check = [
                    symbol for symbol, item in self.watchlist.items()
                    if item.should_check
                ]

                if symbols_to_check:
                    # 병렬 체크
                    tasks = [self._check_symbol(symbol) for symbol in symbols_to_check]
                    await asyncio.gather(*tasks, return_exceptions=True)

                # 다음 체크까지 대기 (최소 주기)
                await asyncio.sleep(self.min_check_interval)

        except asyncio.CancelledError:
            self.logger.info("Watch loop cancelled")

    async def _check_symbol(self, symbol: str):
        """종목 체크"""
        if symbol not in self.watchlist:
            return

        item = self.watchlist[symbol]

        try:
            # TODO: 실제 시세 조회 및 분석
            # 현재는 시뮬레이션
            await asyncio.sleep(0.01)

            # 체크 시간 업데이트
            item.last_checked = datetime.now()

            # 메트릭 업데이트
            self.metrics['total_checks'] += 1

            # 콜백 호출
            for callback in self._check_callbacks:
                try:
                    await callback(symbol, item)
                except Exception as e:
                    self.logger.error(f"Check callback error: {e}")

        except Exception as e:
            self.logger.error(f"Failed to check {symbol}: {e}")

    async def _risk_monitor_loop(self):
        """리스크 모니터 루프"""
        self.logger.info("Risk monitor loop started")

        try:
            while True:
                # 시장 리스크 업데이트
                await self._update_market_risk()

                # 5분 대기
                await asyncio.sleep(300)

        except asyncio.CancelledError:
            self.logger.info("Risk monitor loop cancelled")

    async def _update_market_risk(self):
        """시장 리스크 업데이트"""
        try:
            # TODO: 실제 시장 지표 조회
            # 현재는 더미 데이터
            self.market_risk.kospi_change_rate = 0.0
            self.market_risk.kosdaq_change_rate = 0.0
            self.market_risk.market_volatility = 0.15

            risk_mode = self.market_risk.risk_mode

            self.logger.debug(f"Market Risk Mode: {risk_mode.value}")

        except Exception as e:
            self.logger.error(f"Failed to update market risk: {e}")

    # =====================================================
    # 유틸리티
    # =====================================================

    def get_watchlist(self) -> List[WatchItem]:
        """Watchlist 반환"""
        return list(self.watchlist.values())

    def get_stats(self) -> dict:
        """통계 반환"""
        return {
            **self.metrics,
            'watchlist_size': len(self.watchlist),
            'cooldown_size': len(self.cooldown_symbols),
            'market_risk_mode': self.market_risk.risk_mode.value,
        }

    async def __aenter__(self):
        """Context manager 진입"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager 종료"""
        await self.stop()
