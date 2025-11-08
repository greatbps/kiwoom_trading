#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enhanced_market_schedule_manager.py

강화된 시장 시간 인지 시스템 - 정규 장시간 외 작업 방지
"""

import asyncio
import json
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass
from enum import Enum
import pytz
from pathlib import Path

# Rich for beautiful output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from utils.logger import get_logger

class MarketStatus(Enum):
    """확장된 시장 상태"""
    CLOSED = "closed"                    # 휴장 (휴일, 휴장일)
    PRE_MARKET = "pre_market"           # 장 시작 전 (08:00~09:00)
    OPENING_AUCTION = "opening_auction"  # 개장 동시호가 (08:30~09:00)
    OPEN = "open"                       # 정규 장 (09:00~15:30)
    LUNCH_BREAK = "lunch"               # 점심 시간 (12:00~13:00)
    CLOSING_AUCTION = "closing_auction"  # 마감 동시호가 (15:20~15:30)
    AFTER_HOURS = "after_hours"         # 장 마감 후 (15:30~16:00)
    AFTER_HOURS_TRADING = "after_hours_trading"  # 시간외 거래 (16:00~18:00)
    WEEKEND = "weekend"                 # 주말
    HOLIDAY = "holiday"                 # 공휴일
    MAINTENANCE = "maintenance"         # 시스템 점검

class TradingPermission(Enum):
    """거래 허용 수준"""
    FULL_TRADING = "full_trading"       # 모든 거래 허용
    LIMITED_TRADING = "limited_trading" # 제한적 거래 (시간외 등)
    MONITORING_ONLY = "monitoring_only" # 모니터링만 허용
    NO_ACTIVITY = "no_activity"         # 모든 활동 금지

@dataclass
class TradingHours:
    """확장된 거래 시간"""
    # 장 시작 전
    pre_market_start: time = time(8, 0)      # 08:00
    opening_auction_start: time = time(8, 30) # 08:30

    # 정규 장
    market_open: time = time(9, 0)           # 09:00
    lunch_start: time = time(12, 0)          # 12:00
    lunch_end: time = time(13, 0)            # 13:00
    closing_auction_start: time = time(15, 20) # 15:20
    market_close: time = time(15, 30)        # 15:30

    # 장 마감 후
    after_hours_end: time = time(16, 0)      # 16:00
    after_hours_trading_end: time = time(18, 0) # 18:00

@dataclass
class MarketGate:
    """시장 시간 게이트"""
    name: str
    required_status: List[MarketStatus]
    required_permission: TradingPermission
    description: str
    bypass_allowed: bool = False

@dataclass
class ActivityLog:
    """활동 로그"""
    timestamp: datetime
    activity_type: str
    market_status: MarketStatus
    permission_level: TradingPermission
    allowed: bool
    gate_name: str
    details: str

class EnhancedMarketScheduleManager:
    """강화된 시장 시간 관리자"""

    def __init__(self, config=None, data_dir: str = "data"):
        """관리자 초기화"""
        self.config = config
        self.logger = get_logger("EnhancedMarketScheduleManager")
        self.console = Console() if RICH_AVAILABLE else None

        # 데이터 디렉토리
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        # 한국 시간대
        self.kst = pytz.timezone('Asia/Seoul')

        # 거래 시간 설정
        self.trading_hours = TradingHours()

        # 시장 상태 캐시
        self.market_cache = {}
        self.cache_file = self.data_dir / "market_cache.json"

        # 현재 상태
        self.current_status = MarketStatus.CLOSED
        self.current_permission = TradingPermission.NO_ACTIVITY
        self.last_status_update = None

        # 게이트 설정
        self.gates = self._initialize_gates()

        # 활동 로그
        self.activity_logs: List[ActivityLog] = []

        # 상태 변경 콜백
        self.status_callbacks: List[Callable] = []

        # 모니터링 태스크
        self.monitoring_task = None
        self.monitoring_enabled = False

    def _initialize_gates(self) -> Dict[str, MarketGate]:
        """시장 시간 게이트 초기화"""
        return {
            "trading": MarketGate(
                name="trading",
                required_status=[MarketStatus.OPEN],
                required_permission=TradingPermission.FULL_TRADING,
                description="정규 거래 시간",
                bypass_allowed=False
            ),
            "monitoring": MarketGate(
                name="monitoring",
                required_status=[
                    MarketStatus.PRE_MARKET,
                    MarketStatus.OPENING_AUCTION,
                    MarketStatus.OPEN,
                    MarketStatus.LUNCH_BREAK,
                    MarketStatus.CLOSING_AUCTION,
                    MarketStatus.AFTER_HOURS
                ],
                required_permission=TradingPermission.MONITORING_ONLY,
                description="시장 모니터링",
                bypass_allowed=True
            ),
            "order_management": MarketGate(
                name="order_management",
                required_status=[
                    MarketStatus.PRE_MARKET,
                    MarketStatus.OPENING_AUCTION,
                    MarketStatus.OPEN,
                    MarketStatus.CLOSING_AUCTION,
                    MarketStatus.AFTER_HOURS
                ],
                required_permission=TradingPermission.LIMITED_TRADING,
                description="주문 관리",
                bypass_allowed=False
            ),
            "data_collection": MarketGate(
                name="data_collection",
                required_status=[
                    MarketStatus.PRE_MARKET,
                    MarketStatus.OPENING_AUCTION,
                    MarketStatus.OPEN,
                    MarketStatus.LUNCH_BREAK,
                    MarketStatus.CLOSING_AUCTION,
                    MarketStatus.AFTER_HOURS,
                    MarketStatus.AFTER_HOURS_TRADING
                ],
                required_permission=TradingPermission.MONITORING_ONLY,
                description="데이터 수집",
                bypass_allowed=True
            ),
            "portfolio_analysis": MarketGate(
                name="portfolio_analysis",
                required_status=[
                    MarketStatus.OPEN,
                    MarketStatus.LUNCH_BREAK,
                    MarketStatus.AFTER_HOURS
                ],
                required_permission=TradingPermission.MONITORING_ONLY,
                description="포트폴리오 분석",
                bypass_allowed=True
            ),
            "emergency_liquidation": MarketGate(
                name="emergency_liquidation",
                required_status=[MarketStatus.OPEN],
                required_permission=TradingPermission.FULL_TRADING,
                description="긴급 청산",
                bypass_allowed=False
            )
        }

    async def initialize(self):
        """시스템 초기화"""
        try:
            self.logger.info("🕒 강화된 시장 시간 관리자 초기화 중...")

            # 캐시 로드
            await self._load_cache()

            # 현재 상태 업데이트
            await self.update_market_status()

            # 모니터링 시작
            if not self.monitoring_enabled:
                await self.start_monitoring()

            if self.console:
                self.console.print(Panel.fit(
                    f"🕒 시장 시간 관리자 초기화 완료\n"
                    f"현재 상태: {self.current_status.value}\n"
                    f"허용 수준: {self.current_permission.value}",
                    style="bold blue"
                ))

            self.logger.info("✅ 강화된 시장 시간 관리자 초기화 완료")

        except Exception as e:
            self.logger.error(f"❌ 시장 시간 관리자 초기화 실패: {e}")

    async def update_market_status(self) -> MarketStatus:
        """시장 상태 업데이트"""
        try:
            now = datetime.now(self.kst)
            current_time = now.time()
            current_date = now.date()

            # 주말 확인
            if current_date.weekday() >= 5:  # 토요일(5), 일요일(6)
                new_status = MarketStatus.WEEKEND
                new_permission = TradingPermission.NO_ACTIVITY

            # 휴장일 확인 (실제 구현에서는 KIS API 호출)
            elif await self._is_holiday(current_date):
                new_status = MarketStatus.HOLIDAY
                new_permission = TradingPermission.NO_ACTIVITY

            # 시장 시간 확인
            else:
                new_status, new_permission = self._determine_market_status(current_time)

            # 상태 변경 감지
            if new_status != self.current_status:
                await self._handle_status_change(self.current_status, new_status)

            self.current_status = new_status
            self.current_permission = new_permission
            self.last_status_update = now

            return new_status

        except Exception as e:
            self.logger.error(f"❌ 시장 상태 업데이트 실패: {e}")
            return self.current_status

    def _determine_market_status(self, current_time: time) -> Tuple[MarketStatus, TradingPermission]:
        """현재 시간 기준 시장 상태 결정"""
        hours = self.trading_hours

        if current_time < hours.pre_market_start:
            return MarketStatus.CLOSED, TradingPermission.NO_ACTIVITY

        elif hours.pre_market_start <= current_time < hours.opening_auction_start:
            return MarketStatus.PRE_MARKET, TradingPermission.MONITORING_ONLY

        elif hours.opening_auction_start <= current_time < hours.market_open:
            return MarketStatus.OPENING_AUCTION, TradingPermission.LIMITED_TRADING

        elif hours.market_open <= current_time < hours.lunch_start:
            return MarketStatus.OPEN, TradingPermission.FULL_TRADING

        elif hours.lunch_start <= current_time < hours.lunch_end:
            return MarketStatus.LUNCH_BREAK, TradingPermission.MONITORING_ONLY

        elif hours.lunch_end <= current_time < hours.closing_auction_start:
            return MarketStatus.OPEN, TradingPermission.FULL_TRADING

        elif hours.closing_auction_start <= current_time < hours.market_close:
            return MarketStatus.CLOSING_AUCTION, TradingPermission.LIMITED_TRADING

        elif hours.market_close <= current_time < hours.after_hours_end:
            return MarketStatus.AFTER_HOURS, TradingPermission.LIMITED_TRADING

        elif hours.after_hours_end <= current_time < hours.after_hours_trading_end:
            return MarketStatus.AFTER_HOURS_TRADING, TradingPermission.LIMITED_TRADING

        else:
            return MarketStatus.CLOSED, TradingPermission.NO_ACTIVITY

    async def check_gate(self, gate_name: str, bypass: bool = False) -> Tuple[bool, str]:
        """
        시장 시간 게이트 확인

        Args:
            gate_name: 게이트 이름
            bypass: 강제 우회 여부

        Returns:
            (허용 여부, 사유 메시지)
        """
        try:
            gate = self.gates.get(gate_name)
            if not gate:
                return False, f"알 수 없는 게이트: {gate_name}"

            # 현재 상태 업데이트
            await self.update_market_status()

            # 우회 허용 확인
            if bypass and gate.bypass_allowed:
                message = f"⚠️ {gate.description} 우회 허용"
                await self._log_activity(gate_name, True, message)
                return True, message

            # 상태 확인
            status_allowed = self.current_status in gate.required_status
            permission_allowed = self._check_permission(gate.required_permission)

            allowed = status_allowed and permission_allowed

            # 로그 기록
            if allowed:
                message = f"✅ {gate.description} 허용"
            else:
                reasons = []
                if not status_allowed:
                    reasons.append(f"시장 상태 부적합 (현재: {self.current_status.value})")
                if not permission_allowed:
                    reasons.append(f"권한 부족 (현재: {self.current_permission.value})")
                message = f"❌ {gate.description} 거부 - {', '.join(reasons)}"

            await self._log_activity(gate_name, allowed, message)

            return allowed, message

        except Exception as e:
            error_msg = f"❌ 게이트 확인 실패: {e}"
            self.logger.error(error_msg)
            return False, error_msg

    def _check_permission(self, required_permission: TradingPermission) -> bool:
        """권한 확인"""
        permission_hierarchy = {
            TradingPermission.FULL_TRADING: 4,
            TradingPermission.LIMITED_TRADING: 3,
            TradingPermission.MONITORING_ONLY: 2,
            TradingPermission.NO_ACTIVITY: 1
        }

        current_level = permission_hierarchy.get(self.current_permission, 0)
        required_level = permission_hierarchy.get(required_permission, 0)

        return current_level >= required_level

    async def _handle_status_change(self, old_status: MarketStatus, new_status: MarketStatus):
        """상태 변경 처리"""
        try:
            self.logger.info(f"🔄 시장 상태 변경: {old_status.value} → {new_status.value}")

            # 상태 변경 콜백 실행
            for callback in self.status_callbacks:
                try:
                    await callback(old_status, new_status)
                except Exception as e:
                    self.logger.error(f"❌ 상태 변경 콜백 실행 실패: {e}")

            # 중요한 상태 변경 알림
            if new_status == MarketStatus.OPEN and old_status != MarketStatus.LUNCH_BREAK:
                await self._notify_market_open()
            elif new_status == MarketStatus.CLOSED and old_status == MarketStatus.AFTER_HOURS:
                await self._notify_market_close()

        except Exception as e:
            self.logger.error(f"❌ 상태 변경 처리 실패: {e}")

    async def _notify_market_open(self):
        """장 개장 알림"""
        if self.console:
            self.console.print(Panel.fit(
                "🔔 한국 주식 시장 개장\n"
                "정규 거래가 시작되었습니다.",
                style="bold green"
            ))

    async def _notify_market_close(self):
        """장 마감 알림"""
        if self.console:
            self.console.print(Panel.fit(
                "🔔 한국 주식 시장 마감\n"
                "정규 거래가 종료되었습니다.",
                style="bold red"
            ))

    async def _log_activity(self, gate_name: str, allowed: bool, details: str):
        """활동 로그 기록"""
        try:
            log_entry = ActivityLog(
                timestamp=datetime.now(self.kst),
                activity_type=gate_name,
                market_status=self.current_status,
                permission_level=self.current_permission,
                allowed=allowed,
                gate_name=gate_name,
                details=details
            )

            self.activity_logs.append(log_entry)

            # 로그 크기 제한 (최근 1000개만 유지)
            if len(self.activity_logs) > 1000:
                self.activity_logs = self.activity_logs[-1000:]

            # 로그 출력
            log_level = "info" if allowed else "warning"
            getattr(self.logger, log_level)(details)

        except Exception as e:
            self.logger.error(f"❌ 활동 로그 기록 실패: {e}")

    async def _is_holiday(self, date) -> bool:
        """휴장일 확인 (실제 구현에서는 KIS API 사용)"""
        try:
            # 데모용 휴장일 (실제로는 KIS API 호출)
            holidays_2024 = {
                "2024-01-01",  # 신정
                "2024-02-09",  # 설날 연휴
                "2024-02-10",  # 설날
                "2024-02-11",  # 설날 연휴
                "2024-02-12",  # 설날 대체휴일
                "2024-03-01",  # 삼일절
                "2024-04-10",  # 국회의원선거
                "2024-05-05",  # 어린이날
                "2024-05-06",  # 어린이날 대체휴일
                "2024-05-15",  # 석가탄신일
                "2024-06-06",  # 현충일
                "2024-08-15",  # 광복절
                "2024-09-16",  # 추석 연휴
                "2024-09-17",  # 추석
                "2024-09-18",  # 추석 연휴
                "2024-10-03",  # 개천절
                "2024-10-09",  # 한글날
                "2024-12-25",  # 크리스마스
            }

            date_str = date.strftime("%Y-%m-%d")
            return date_str in holidays_2024

        except Exception as e:
            self.logger.error(f"❌ 휴장일 확인 실패: {e}")
            return False

    async def start_monitoring(self, interval: int = 60):
        """시장 상태 모니터링 시작"""
        try:
            if self.monitoring_enabled:
                return

            self.monitoring_enabled = True
            self.monitoring_task = asyncio.create_task(self._monitoring_loop(interval))

            self.logger.info(f"📡 시장 상태 모니터링 시작 (간격: {interval}초)")

        except Exception as e:
            self.logger.error(f"❌ 모니터링 시작 실패: {e}")

    async def stop_monitoring(self):
        """시장 상태 모니터링 중지"""
        try:
            self.monitoring_enabled = False

            if self.monitoring_task:
                self.monitoring_task.cancel()
                try:
                    await self.monitoring_task
                except asyncio.CancelledError:
                    pass

            self.logger.info("📡 시장 상태 모니터링 중지")

        except Exception as e:
            self.logger.error(f"❌ 모니터링 중지 실패: {e}")

    async def _monitoring_loop(self, interval: int):
        """모니터링 루프"""
        try:
            while self.monitoring_enabled:
                await self.update_market_status()
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            self.logger.info("모니터링 루프가 취소되었습니다")
        except Exception as e:
            self.logger.error(f"❌ 모니터링 루프 오류: {e}")

    async def get_market_info(self) -> Dict[str, Any]:
        """현재 시장 정보 반환"""
        await self.update_market_status()

        now = datetime.now(self.kst)

        return {
            "current_time": now.isoformat(),
            "market_status": self.current_status.value,
            "permission_level": self.current_permission.value,
            "is_trading_allowed": self.current_permission in [
                TradingPermission.FULL_TRADING,
                TradingPermission.LIMITED_TRADING
            ],
            "is_market_open": self.current_status == MarketStatus.OPEN,
            "next_status_change": await self._get_next_status_change(),
            "trading_hours": {
                "market_open": self.trading_hours.market_open.strftime("%H:%M"),
                "market_close": self.trading_hours.market_close.strftime("%H:%M"),
                "lunch_start": self.trading_hours.lunch_start.strftime("%H:%M"),
                "lunch_end": self.trading_hours.lunch_end.strftime("%H:%M")
            }
        }

    async def _get_next_status_change(self) -> Optional[str]:
        """다음 상태 변경 시간 예측"""
        try:
            now = datetime.now(self.kst)
            current_time = now.time()

            # 다음 상태 변경 시간들
            changes = [
                (self.trading_hours.pre_market_start, "장 시작 전"),
                (self.trading_hours.opening_auction_start, "개장 동시호가"),
                (self.trading_hours.market_open, "정규 장 개장"),
                (self.trading_hours.lunch_start, "점심시간"),
                (self.trading_hours.lunch_end, "오후 장 시작"),
                (self.trading_hours.closing_auction_start, "마감 동시호가"),
                (self.trading_hours.market_close, "정규 장 마감"),
                (self.trading_hours.after_hours_end, "시간외 거래 마감")
            ]

            for change_time, description in changes:
                if current_time < change_time:
                    next_change = datetime.combine(now.date(), change_time)
                    return f"{description} ({next_change.strftime('%H:%M')})"

            # 오늘의 모든 시간이 지났으면 내일 첫 번째 시간
            tomorrow = now.date() + timedelta(days=1)
            next_change = datetime.combine(tomorrow, self.trading_hours.pre_market_start)
            return f"장 시작 전 ({next_change.strftime('%m/%d %H:%M')})"

        except Exception as e:
            self.logger.error(f"❌ 다음 상태 변경 시간 계산 실패: {e}")
            return None

    async def display_status(self):
        """현재 상태 표시"""
        try:
            if not self.console:
                return

            market_info = await self.get_market_info()

            # 상태 테이블
            status_table = Table(title="시장 현황")
            status_table.add_column("항목", style="cyan")
            status_table.add_column("상태", style="magenta")

            status_table.add_row("현재 시간", market_info["current_time"][:19])
            status_table.add_row("시장 상태", market_info["market_status"])
            status_table.add_row("권한 수준", market_info["permission_level"])
            status_table.add_row("거래 허용", "✅" if market_info["is_trading_allowed"] else "❌")
            status_table.add_row("다음 변경", market_info["next_status_change"] or "정보 없음")

            self.console.print(status_table)

            # 거래 시간 테이블
            hours_table = Table(title="거래 시간")
            hours_table.add_column("구분", style="cyan")
            hours_table.add_column("시간", style="yellow")

            hours = market_info["trading_hours"]
            hours_table.add_row("장 개장", hours["market_open"])
            hours_table.add_row("점심시간", f"{hours['lunch_start']} ~ {hours['lunch_end']}")
            hours_table.add_row("장 마감", hours["market_close"])

            self.console.print(hours_table)

        except Exception as e:
            self.logger.error(f"❌ 상태 표시 실패: {e}")

    async def _load_cache(self):
        """캐시 로드"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.market_cache = json.load(f)
                self.logger.info("✅ 시장 캐시 로드 완료")
        except Exception as e:
            self.logger.error(f"❌ 캐시 로드 실패: {e}")

    async def _save_cache(self):
        """캐시 저장"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.market_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"❌ 캐시 저장 실패: {e}")

# 사용 예시 및 데코레이터
def require_market_gate(gate_name: str, bypass: bool = False):
    """시장 시간 게이트 데코레이터"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 첫 번째 인자에서 market_manager 찾기
            manager = None
            for arg in args:
                if hasattr(arg, 'market_manager'):
                    manager = arg.market_manager
                    break

            if manager and isinstance(manager, EnhancedMarketScheduleManager):
                allowed, message = await manager.check_gate(gate_name, bypass)
                if not allowed:
                    raise Exception(f"시장 시간 제한: {message}")

            return await func(*args, **kwargs)
        return wrapper
    return decorator

# 사용 예시
async def main():
    """테스트 함수"""
    try:
        # 관리자 초기화
        manager = EnhancedMarketScheduleManager()
        await manager.initialize()

        # 상태 표시
        await manager.display_status()

        # 게이트 테스트
        gates_to_test = ["trading", "monitoring", "emergency_liquidation"]

        for gate in gates_to_test:
            allowed, message = await manager.check_gate(gate)
            print(f"{gate}: {message}")

        # 5초간 모니터링
        await asyncio.sleep(5)

        # 종료
        await manager.stop_monitoring()

    except Exception as e:
        print(f"❌ 테스트 실행 실패: {e}")

if __name__ == "__main__":
    asyncio.run(main())