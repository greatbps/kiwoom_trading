#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_balance_monitor.py

자동 잔고 모니터링 및 설정 조정 시스템
조건이 맞으면 자동으로 실행
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
import json
from pathlib import Path

from utils.logger import get_logger
from .dynamic_settings_manager import DynamicSettingsManager, TradingSettings

@dataclass
class AutoTriggerCondition:
    """자동 실행 조건"""
    name: str
    condition_type: str  # balance_change, time_interval, performance_threshold
    threshold: float
    enabled: bool = True
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0

@dataclass
class BalanceChangeEvent:
    """잔고 변화 이벤트"""
    timestamp: datetime
    previous_balance: float
    current_balance: float
    change_amount: float
    change_percentage: float
    trigger_conditions: List[str]

class AutoBalanceMonitor:
    """자동 잔고 모니터링 시스템"""

    def __init__(self, trading_handler=None, config=None):
        """자동 모니터링 시스템 초기화"""
        self.logger = get_logger("AutoBalanceMonitor")
        self.trading_handler = trading_handler
        self.config = config

        # 동적 설정 관리자
        self.settings_manager = DynamicSettingsManager(config)

        # 모니터링 상태
        self.is_monitoring = False
        self.last_balance = 0
        self.monitoring_interval = 30  # 30초마다 체크

        # 자동 실행 조건들
        self.trigger_conditions = self._initialize_trigger_conditions()

        # 이벤트 핸들러들
        self.event_handlers: Dict[str, Callable] = {}
        self._register_default_handlers()

        # 이벤트 히스토리
        self.event_history: List[BalanceChangeEvent] = []

        # 설정 파일
        self.settings_file = Path("data/auto_monitor_settings.json")
        self._load_settings()

    def _initialize_trigger_conditions(self) -> List[AutoTriggerCondition]:
        """자동 실행 조건 초기화"""
        return [
            # 잔고 5% 이상 변화시 자동 조정
            AutoTriggerCondition(
                name="balance_change_5pct",
                condition_type="balance_change",
                threshold=5.0,  # 5%
                enabled=True
            ),

            # 잔고 1000만원 이상 변화시 자동 조정
            AutoTriggerCondition(
                name="balance_change_10m",
                condition_type="balance_change",
                threshold=10_000_000,  # 1000만원
                enabled=True
            ),

            # 1시간마다 정기 체크
            AutoTriggerCondition(
                name="hourly_check",
                condition_type="time_interval",
                threshold=3600,  # 1시간(초)
                enabled=True
            ),

            # 손실 10% 이상시 긴급 조정
            AutoTriggerCondition(
                name="emergency_loss",
                condition_type="performance_threshold",
                threshold=-10.0,  # -10%
                enabled=True
            ),

            # 수익 20% 이상시 적극적 조정
            AutoTriggerCondition(
                name="high_profit",
                condition_type="performance_threshold",
                threshold=20.0,  # +20%
                enabled=True
            )
        ]

    def _register_default_handlers(self):
        """기본 이벤트 핸들러 등록"""
        self.event_handlers = {
            "balance_change_5pct": self._handle_moderate_balance_change,
            "balance_change_10m": self._handle_significant_balance_change,
            "hourly_check": self._handle_regular_check,
            "emergency_loss": self._handle_emergency_loss,
            "high_profit": self._handle_high_profit_opportunity
        }

    async def start_monitoring(self):
        """자동 모니터링 시작"""
        try:
            if self.is_monitoring:
                self.logger.warning("이미 모니터링이 실행 중입니다")
                return

            self.is_monitoring = True
            self.logger.info("🔄 자동 잔고 모니터링 시작")

            # 초기 잔고 설정
            await self._update_current_balance()

            # 모니터링 루프 시작
            await self._monitoring_loop()

        except Exception as e:
            self.logger.error(f"❌ 모니터링 시작 실패: {e}")
            self.is_monitoring = False

    async def stop_monitoring(self):
        """자동 모니터링 중지"""
        self.is_monitoring = False
        self.logger.info("⏹️ 자동 잔고 모니터링 중지")

    async def _monitoring_loop(self):
        """메인 모니터링 루프"""
        try:
            while self.is_monitoring:
                try:
                    # 현재 잔고 업데이트
                    balance_updated = await self._update_current_balance()

                    if balance_updated:
                        # 트리거 조건 체크
                        triggered_conditions = await self._check_trigger_conditions()

                        if triggered_conditions:
                            # 자동 실행
                            await self._execute_auto_actions(triggered_conditions)

                    # 정기 체크 (시간 기반)
                    await self._check_time_based_triggers()

                    # 대기
                    await asyncio.sleep(self.monitoring_interval)

                except Exception as e:
                    self.logger.error(f"❌ 모니터링 루프 오류: {e}")
                    await asyncio.sleep(self.monitoring_interval)

        except asyncio.CancelledError:
            self.logger.info("🛑 모니터링 루프 취소됨")
        except Exception as e:
            self.logger.error(f"❌ 모니터링 루프 치명적 오류: {e}")
        finally:
            self.is_monitoring = False

    async def _update_current_balance(self) -> bool:
        """현재 잔고 업데이트"""
        try:
            if not self.trading_handler:
                return False

            # 잔고 조회
            balance_info = await self.trading_handler.get_balance()
            if not balance_info:
                return False

            # 총 잔고 계산
            current_balance = float(balance_info.get('total_balance', 0))

            # 변화 감지
            if self.last_balance > 0 and current_balance != self.last_balance:
                change_amount = current_balance - self.last_balance
                change_percentage = (change_amount / self.last_balance) * 100

                # 이벤트 생성
                event = BalanceChangeEvent(
                    timestamp=datetime.now(),
                    previous_balance=self.last_balance,
                    current_balance=current_balance,
                    change_amount=change_amount,
                    change_percentage=change_percentage,
                    trigger_conditions=[]
                )

                self.event_history.append(event)

                self.logger.info(
                    f"💰 잔고 변화 감지: {self.last_balance:,.0f}원 → {current_balance:,.0f}원 "
                    f"({change_percentage:+.2f}%)"
                )

                self.last_balance = current_balance
                return True

            elif self.last_balance == 0:
                # 초기 설정
                self.last_balance = current_balance
                self.logger.info(f"💰 초기 잔고 설정: {current_balance:,.0f}원")

            return False

        except Exception as e:
            self.logger.error(f"❌ 잔고 업데이트 실패: {e}")
            return False

    async def _check_trigger_conditions(self) -> List[str]:
        """트리거 조건 체크"""
        triggered = []

        if not self.event_history:
            return triggered

        latest_event = self.event_history[-1]

        for condition in self.trigger_conditions:
            if not condition.enabled:
                continue

            is_triggered = False

            if condition.condition_type == "balance_change":
                # 잔고 변화량 체크
                if condition.name.endswith("pct"):
                    # 퍼센트 기준
                    is_triggered = abs(latest_event.change_percentage) >= condition.threshold
                else:
                    # 절대값 기준
                    is_triggered = abs(latest_event.change_amount) >= condition.threshold

            elif condition.condition_type == "performance_threshold":
                # 성과 임계값 체크
                initial_balance = self.event_history[0].previous_balance if self.event_history else self.last_balance
                if initial_balance > 0:
                    total_return_pct = ((latest_event.current_balance - initial_balance) / initial_balance) * 100

                    if condition.threshold > 0:
                        # 수익 임계값
                        is_triggered = total_return_pct >= condition.threshold
                    else:
                        # 손실 임계값
                        is_triggered = total_return_pct <= condition.threshold

            if is_triggered:
                triggered.append(condition.name)
                condition.last_triggered = datetime.now()
                condition.trigger_count += 1

                # 이벤트에 트리거 조건 추가
                latest_event.trigger_conditions.append(condition.name)

        return triggered

    async def _check_time_based_triggers(self):
        """시간 기반 트리거 체크"""
        current_time = datetime.now()

        for condition in self.trigger_conditions:
            if (condition.condition_type == "time_interval" and
                condition.enabled and
                (condition.last_triggered is None or
                 (current_time - condition.last_triggered).total_seconds() >= condition.threshold)):

                await self._execute_auto_actions([condition.name])

    async def _execute_auto_actions(self, triggered_conditions: List[str]):
        """자동 실행"""
        try:
            self.logger.info(f"🚀 자동 실행 트리거: {triggered_conditions}")

            for condition_name in triggered_conditions:
                handler = self.event_handlers.get(condition_name)
                if handler:
                    try:
                        await handler()
                    except Exception as e:
                        self.logger.error(f"❌ 핸들러 실행 실패 ({condition_name}): {e}")

        except Exception as e:
            self.logger.error(f"❌ 자동 실행 실패: {e}")

    async def _handle_moderate_balance_change(self):
        """중간 수준 잔고 변화 처리 (5% 변화)"""
        try:
            self.logger.info("📊 중간 수준 잔고 변화 - 설정 검토 중...")

            # 현재 잔고로 설정 조정
            await self._auto_adjust_settings()

        except Exception as e:
            self.logger.error(f"❌ 중간 잔고 변화 처리 실패: {e}")

    async def _handle_significant_balance_change(self):
        """큰 잔고 변화 처리 (1000만원 이상)"""
        try:
            self.logger.info("🔥 큰 잔고 변화 - 즉시 설정 조정!")

            # 즉시 설정 조정
            await self._auto_adjust_settings()

            # 백테스팅도 자동 실행
            await self._auto_run_backtesting()

        except Exception as e:
            self.logger.error(f"❌ 큰 잔고 변화 처리 실패: {e}")

    async def _handle_regular_check(self):
        """정기 체크 (1시간마다)"""
        try:
            self.logger.info("⏰ 정기 체크 - 시스템 상태 확인")

            # 성과 분석
            summary = await self.settings_manager.get_balance_summary()
            if summary.get("status") == "available":
                current_pnl = summary.get("latest_pnl_pct", 0)

                # 성과에 따른 조치
                if abs(current_pnl) > 5:  # 5% 이상 변화시
                    await self._auto_adjust_settings()

        except Exception as e:
            self.logger.error(f"❌ 정기 체크 실패: {e}")

    async def _handle_emergency_loss(self):
        """긴급 손실 상황 처리 (-10% 이상)"""
        try:
            self.logger.warning("🚨 긴급 손실 상황 - 보수적 설정으로 전환!")

            # 즉시 보수적 설정으로 변경
            emergency_settings = TradingSettings(
                position_size_ratio=0.03,  # 3%로 축소
                max_positions=2,           # 최대 2개로 제한
                stop_loss_pct=1.5,         # 손절 강화
                take_profit_pct=4.0,       # 익절 보수적
                risk_level="low",
                min_cash_reserve=0.5       # 현금 50% 유지
            )

            # 강제 설정 업데이트
            self.settings_manager.current_settings = emergency_settings
            await self.settings_manager._save_settings()

            self.logger.info("✅ 긴급 보수적 설정 적용 완료")

        except Exception as e:
            self.logger.error(f"❌ 긴급 손실 처리 실패: {e}")

    async def _handle_high_profit_opportunity(self):
        """고수익 기회 처리 (+20% 이상)"""
        try:
            self.logger.info("🎯 고수익 달성 - 적극적 설정으로 전환!")

            # 적극적 설정으로 변경
            aggressive_settings = TradingSettings(
                position_size_ratio=0.15,  # 15%로 확대
                max_positions=8,           # 최대 8개로 확대
                stop_loss_pct=4.0,         # 손절 여유
                take_profit_pct=15.0,      # 익절 확대
                risk_level="high",
                min_cash_reserve=0.15      # 현금 15%로 축소
            )

            # 설정 업데이트
            self.settings_manager.current_settings = aggressive_settings
            await self.settings_manager._save_settings()

            # 추가 백테스팅으로 기회 검증
            await self._auto_run_backtesting()

            self.logger.info("✅ 적극적 설정 적용 완료")

        except Exception as e:
            self.logger.error(f"❌ 고수익 기회 처리 실패: {e}")

    async def _auto_adjust_settings(self):
        """자동 설정 조정"""
        try:
            if not self.event_history:
                return

            latest_event = self.event_history[-1]

            # 설정 자동 조정
            new_settings, adjustment_info = await self.settings_manager.update_balance_and_adjust_settings(
                current_balance=latest_event.current_balance,
                cash_balance=latest_event.current_balance * 0.3,  # 추정
                stock_value=latest_event.current_balance * 0.7,   # 추정
                trading_handler=self.trading_handler
            )

            if adjustment_info.get("adjustments_made"):
                self.logger.info(f"✅ 자동 설정 조정 완료: {len(adjustment_info['adjustments_made'])}개 항목")
            else:
                self.logger.info("ℹ️ 설정 조정 불필요")

        except Exception as e:
            self.logger.error(f"❌ 자동 설정 조정 실패: {e}")

    async def _auto_run_backtesting(self):
        """자동 백테스팅 실행"""
        try:
            self.logger.info("📈 자동 백테스팅 실행 중...")

            # TODO: 실제 백테스팅 엔진과 연동
            # from backtesting.backtesting_engine import BacktestingEngine
            # engine = BacktestingEngine(self.config)
            # results = await engine.run_backtest(...)

            # 시각화도 자동 생성
            await self._auto_generate_visualization()

        except Exception as e:
            self.logger.error(f"❌ 자동 백테스팅 실패: {e}")

    async def _auto_generate_visualization(self):
        """자동 시각화 생성"""
        try:
            from backtesting.enhanced_visualizer import EnhancedVisualizer

            visualizer = EnhancedVisualizer(self.config)

            # 간단한 모니터링 리포트 생성
            monitor_file = await visualizer._create_simple_monitor()
            self.logger.info(f"📊 자동 시각화 생성: {monitor_file}")

        except Exception as e:
            self.logger.error(f"❌ 자동 시각화 생성 실패: {e}")

    def _load_settings(self):
        """설정 로드"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # 트리거 조건 설정 로드
                if "trigger_conditions" in data:
                    for condition_data in data["trigger_conditions"]:
                        condition_name = condition_data.get("name")
                        for condition in self.trigger_conditions:
                            if condition.name == condition_name:
                                condition.enabled = condition_data.get("enabled", True)
                                condition.threshold = condition_data.get("threshold", condition.threshold)
                                break

                # 모니터링 간격 설정
                self.monitoring_interval = data.get("monitoring_interval", 30)

                self.logger.info("✅ 자동 모니터링 설정을 로드했습니다")

        except Exception as e:
            self.logger.error(f"❌ 설정 로드 실패: {e}")

    async def save_settings(self):
        """설정 저장"""
        try:
            data = {
                "monitoring_interval": self.monitoring_interval,
                "trigger_conditions": [
                    {
                        "name": condition.name,
                        "condition_type": condition.condition_type,
                        "threshold": condition.threshold,
                        "enabled": condition.enabled,
                        "trigger_count": condition.trigger_count
                    }
                    for condition in self.trigger_conditions
                ],
                "last_updated": datetime.now().isoformat()
            }

            # 디렉토리 생성
            self.settings_file.parent.mkdir(exist_ok=True)

            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.logger.info("✅ 자동 모니터링 설정 저장 완료")

        except Exception as e:
            self.logger.error(f"❌ 설정 저장 실패: {e}")

    async def get_monitoring_status(self) -> Dict[str, Any]:
        """모니터링 상태 조회"""
        return {
            "is_monitoring": self.is_monitoring,
            "last_balance": self.last_balance,
            "monitoring_interval": self.monitoring_interval,
            "trigger_conditions": [
                {
                    "name": condition.name,
                    "enabled": condition.enabled,
                    "threshold": condition.threshold,
                    "trigger_count": condition.trigger_count,
                    "last_triggered": condition.last_triggered.isoformat() if condition.last_triggered else None
                }
                for condition in self.trigger_conditions
            ],
            "recent_events": [
                {
                    "timestamp": event.timestamp.isoformat(),
                    "change_percentage": event.change_percentage,
                    "change_amount": event.change_amount,
                    "trigger_conditions": event.trigger_conditions
                }
                for event in self.event_history[-10:]  # 최근 10개
            ]
        }

# 사용 예시
async def main():
    """테스트 함수"""
    monitor = AutoBalanceMonitor()

    try:
        # 모니터링 시작
        await monitor.start_monitoring()
    except KeyboardInterrupt:
        print("\n사용자가 중단했습니다.")
        await monitor.stop_monitoring()

if __name__ == "__main__":
    asyncio.run(main())