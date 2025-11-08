#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/core/auto_mode_controller.py

자동 모니터링 및 매매 모드 제어기
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from enum import Enum
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from utils.logger import get_logger
from utils.market_schedule_manager import MarketScheduleManager, MarketStatus

class AutoMode(Enum):
    """자동 모드 타입"""
    MONITORING = "monitoring"
    TRADING = "trading"

class ModeStatus(Enum):
    """모드 상태"""
    ACTIVE = "active"      # 🟢 활성화
    INACTIVE = "inactive"  # 🔴 비활성화
    STANDBY = "standby"    # 🟡 대기중

@dataclass
class ModeConfig:
    """모드 설정"""
    auto_start: bool = True           # 자동 시작
    auto_stop: bool = True            # 자동 중지
    start_delay_minutes: int = 0      # 시작 지연 (분)
    stop_early_minutes: int = 5       # 조기 중지 (분)
    weekend_mode: bool = False        # 주말 모드
    holiday_mode: bool = False        # 휴일 모드

class AutoModeController:
    """자동 모드 제어기"""
    
    def __init__(self, config, market_schedule_manager: MarketScheduleManager):
        self.config = config
        self.market_manager = market_schedule_manager
        self.logger = get_logger("AutoModeController")
        self.console = Console()
        
        # 모드 상태 추적
        self.modes = {
            AutoMode.MONITORING: ModeStatus.INACTIVE,
            AutoMode.TRADING: ModeStatus.INACTIVE
        }
        
        # 모드별 설정
        self.mode_configs = {
            AutoMode.MONITORING: ModeConfig(
                auto_start=True,
                auto_stop=True,
                start_delay_minutes=0,
                stop_early_minutes=5,
                weekend_mode=False,
                holiday_mode=False
            ),
            AutoMode.TRADING: ModeConfig(
                auto_start=True,
                auto_stop=True,
                start_delay_minutes=5,      # 장 시작 5분 후
                stop_early_minutes=10,      # 장 마감 10분 전
                weekend_mode=False,
                holiday_mode=False
            )
        }
        
        # 상태 변경 콜백들
        self.mode_change_callbacks = {
            AutoMode.MONITORING: [],
            AutoMode.TRADING: []
        }
        
        # 제어 태스크
        self.control_tasks = {}
        
        # 상태 기록
        self.status_history = []
        
        self.logger.info("🎛️ 자동 모드 제어기 초기화 완료")

    async def initialize(self):
        """초기화"""
        try:
            # 시장 일정 관리자 상태 변경 콜백 등록
            self.market_manager.add_status_change_callback(self._on_market_status_change)
            
            # 현재 시장 상태에 따른 초기 모드 설정
            await self._update_modes_for_market_status(self.market_manager.current_status)
            
            # 제어 태스크 시작
            await self._start_control_tasks()
            
            self.logger.info("✅ 자동 모드 제어기 초기화 완료")
            
        except Exception as e:
            self.logger.error(f"❌ 자동 모드 제어기 초기화 실패: {e}")

    async def _start_control_tasks(self):
        """제어 태스크들 시작"""
        try:
            # 각 모드별 제어 태스크 생성
            for mode in AutoMode:
                if mode not in self.control_tasks or self.control_tasks[mode].done():
                    self.control_tasks[mode] = asyncio.create_task(
                        self._mode_control_loop(mode)
                    )
            
            self.logger.info("🔄 자동 모드 제어 태스크 시작")
            
        except Exception as e:
            self.logger.error(f"❌ 제어 태스크 시작 실패: {e}")

    async def _stop_control_tasks(self):
        """제어 태스크들 중지"""
        try:
            for mode, task in self.control_tasks.items():
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            self.control_tasks.clear()
            self.logger.info("⏹️ 자동 모드 제어 태스크 중지")
            
        except Exception as e:
            self.logger.error(f"❌ 제어 태스크 중지 실패: {e}")

    async def _mode_control_loop(self, mode: AutoMode):
        """모드별 제어 루프"""
        try:
            while True:
                current_status = self.modes[mode]
                config = self.mode_configs[mode]
                
                # 시장 상태 확인
                market_status = self.market_manager.current_status
                should_be_active = await self._should_mode_be_active(mode, market_status)
                
                # 상태 변경 필요성 판단
                if should_be_active and current_status == ModeStatus.INACTIVE:
                    # 활성화 필요
                    if config.start_delay_minutes > 0:
                        await self._set_mode_status(mode, ModeStatus.STANDBY)
                        await asyncio.sleep(config.start_delay_minutes * 60)
                    
                    await self._activate_mode(mode)
                    
                elif not should_be_active and current_status in [ModeStatus.ACTIVE, ModeStatus.STANDBY]:
                    # 비활성화 필요
                    await self._deactivate_mode(mode)
                
                # 30초마다 상태 확인
                await asyncio.sleep(30)
                
        except asyncio.CancelledError:
            self.logger.info(f"🛑 {mode.value} 모드 제어 루프 종료")
        except Exception as e:
            self.logger.error(f"❌ {mode.value} 모드 제어 루프 오류: {e}")
            await asyncio.sleep(60)  # 오류 시 1분 대기 후 재시도

    async def _should_mode_be_active(self, mode: AutoMode, market_status: MarketStatus) -> bool:
        """모드가 활성화되어야 하는지 판단"""
        try:
            config = self.mode_configs[mode]
            
            # 주말 체크
            if market_status == MarketStatus.WEEKEND and not config.weekend_mode:
                return False
            
            # 휴장일 체크
            if market_status == MarketStatus.CLOSED and not config.holiday_mode:
                return False
            
            # 자동 시작/중지 설정 체크
            if not config.auto_start:
                return self.modes[mode] == ModeStatus.ACTIVE  # 현재 상태 유지
            
            # 모드별 세부 조건
            if mode == AutoMode.MONITORING:
                return self._should_monitoring_be_active(market_status, config)
            elif mode == AutoMode.TRADING:
                return self._should_trading_be_active(market_status, config)
            
            return False
            
        except Exception as e:
            self.logger.error(f"❌ {mode.value} 모드 활성화 판단 실패: {e}")
            return False

    def _should_monitoring_be_active(self, market_status: MarketStatus, config: ModeConfig) -> bool:
        """모니터링 모드 활성화 판단"""
        # 모니터링은 거래일이면 대부분 활성화
        allowed_statuses = [
            MarketStatus.PRE_MARKET,
            MarketStatus.OPEN,
            MarketStatus.LUNCH_BREAK,
            MarketStatus.AFTER_HOURS
        ]
        
        if market_status in allowed_statuses:
            # 조기 중지 시간 체크
            if config.stop_early_minutes > 0:
                now = datetime.now(self.market_manager.kst)
                close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
                early_stop = close_time - timedelta(minutes=config.stop_early_minutes)
                
                if now >= early_stop:
                    return False
            
            return True
        
        return False

    def _should_trading_be_active(self, market_status: MarketStatus, config: ModeConfig) -> bool:
        """매매 모드 활성화 판단"""
        # 매매는 정규 거래 시간과 동시호가 시간에만 활성화
        allowed_statuses = [
            MarketStatus.PRE_MARKET,
            MarketStatus.OPEN,
            MarketStatus.AFTER_HOURS
        ]
        
        if market_status in allowed_statuses:
            now = datetime.now(self.market_manager.kst)
            
            # 시작 지연 시간 체크
            if config.start_delay_minutes > 0 and market_status == MarketStatus.PRE_MARKET:
                start_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
                delayed_start = start_time + timedelta(minutes=config.start_delay_minutes)
                
                if now < delayed_start:
                    return False
            
            # 조기 중지 시간 체크
            if config.stop_early_minutes > 0:
                if market_status == MarketStatus.OPEN:
                    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
                    early_stop = close_time - timedelta(minutes=config.stop_early_minutes)
                    
                    if now >= early_stop:
                        return False
                elif market_status == MarketStatus.AFTER_HOURS:
                    end_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
                    early_stop = end_time - timedelta(minutes=config.stop_early_minutes)
                    
                    if now >= early_stop:
                        return False
            
            return True
        
        return False

    async def _activate_mode(self, mode: AutoMode):
        """모드 활성화"""
        try:
            old_status = self.modes[mode]
            await self._set_mode_status(mode, ModeStatus.ACTIVE)
            
            # 콜백 실행
            await self._notify_mode_change(mode, old_status, ModeStatus.ACTIVE)
            
            self.logger.info(f"🟢 {mode.value} 모드 활성화")
            
        except Exception as e:
            self.logger.error(f"❌ {mode.value} 모드 활성화 실패: {e}")

    async def _deactivate_mode(self, mode: AutoMode):
        """모드 비활성화"""
        try:
            old_status = self.modes[mode]
            await self._set_mode_status(mode, ModeStatus.INACTIVE)
            
            # 콜백 실행
            await self._notify_mode_change(mode, old_status, ModeStatus.INACTIVE)
            
            self.logger.info(f"🔴 {mode.value} 모드 비활성화")
            
        except Exception as e:
            self.logger.error(f"❌ {mode.value} 모드 비활성화 실패: {e}")

    async def _set_mode_status(self, mode: AutoMode, status: ModeStatus):
        """모드 상태 설정"""
        old_status = self.modes[mode]
        self.modes[mode] = status
        
        # 상태 기록
        self.status_history.append({
            'timestamp': datetime.now(),
            'mode': mode.value,
            'old_status': old_status.value,
            'new_status': status.value
        })
        
        # 최근 100개 기록만 유지
        if len(self.status_history) > 100:
            self.status_history = self.status_history[-100:]

    async def _on_market_status_change(self, old_status: MarketStatus, new_status: MarketStatus):
        """시장 상태 변경 콜백"""
        try:
            self.logger.info(f"📊 시장 상태 변경 감지: {old_status.value} → {new_status.value}")
            
            # 모드 상태 업데이트
            await self._update_modes_for_market_status(new_status)
            
        except Exception as e:
            self.logger.error(f"❌ 시장 상태 변경 처리 실패: {e}")

    async def _update_modes_for_market_status(self, market_status: MarketStatus):
        """시장 상태에 따른 모드 상태 업데이트"""
        try:
            for mode in AutoMode:
                should_be_active = await self._should_mode_be_active(mode, market_status)
                current_status = self.modes[mode]
                
                if should_be_active and current_status == ModeStatus.INACTIVE:
                    # 활성화 대기로 설정 (제어 루프에서 처리)
                    await self._set_mode_status(mode, ModeStatus.STANDBY)
                    
        except Exception as e:
            self.logger.error(f"❌ 모드 상태 업데이트 실패: {e}")

    async def _notify_mode_change(self, mode: AutoMode, old_status: ModeStatus, new_status: ModeStatus):
        """모드 변경 알림"""
        try:
            callbacks = self.mode_change_callbacks.get(mode, [])
            
            for callback in callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(mode, old_status, new_status)
                    else:
                        callback(mode, old_status, new_status)
                except Exception as e:
                    self.logger.error(f"❌ {mode.value} 모드 변경 콜백 실행 실패: {e}")
                    
        except Exception as e:
            self.logger.error(f"❌ 모드 변경 알림 실패: {e}")

    # Public API 메서드들

    def add_mode_change_callback(self, mode: AutoMode, callback: Callable):
        """모드 변경 콜백 등록"""
        if mode not in self.mode_change_callbacks:
            self.mode_change_callbacks[mode] = []
        self.mode_change_callbacks[mode].append(callback)

    def remove_mode_change_callback(self, mode: AutoMode, callback: Callable):
        """모드 변경 콜백 제거"""
        if mode in self.mode_change_callbacks and callback in self.mode_change_callbacks[mode]:
            self.mode_change_callbacks[mode].remove(callback)

    async def manual_activate_mode(self, mode: AutoMode):
        """수동 모드 활성화"""
        try:
            config = self.mode_configs[mode]
            config.auto_start = False  # 자동 시작 비활성화
            
            await self._activate_mode(mode)
            self.logger.info(f"👤 {mode.value} 모드 수동 활성화")
            
        except Exception as e:
            self.logger.error(f"❌ {mode.value} 모드 수동 활성화 실패: {e}")

    async def manual_deactivate_mode(self, mode: AutoMode):
        """수동 모드 비활성화"""
        try:
            config = self.mode_configs[mode]
            config.auto_start = False  # 자동 시작 비활성화
            
            await self._deactivate_mode(mode)
            self.logger.info(f"👤 {mode.value} 모드 수동 비활성화")
            
        except Exception as e:
            self.logger.error(f"❌ {mode.value} 모드 수동 비활성화 실패: {e}")

    def get_mode_status(self, mode: AutoMode) -> ModeStatus:
        """모드 상태 조회"""
        return self.modes.get(mode, ModeStatus.INACTIVE)

    def is_mode_active(self, mode: AutoMode) -> bool:
        """모드 활성화 여부 확인"""
        return self.modes.get(mode) == ModeStatus.ACTIVE

    def get_all_mode_status(self) -> Dict[str, Any]:
        """모든 모드 상태 조회"""
        try:
            market_info = self.market_manager.get_current_status_info()
            
            return {
                'market_info': market_info,
                'modes': {
                    'monitoring': {
                        'status': self.modes[AutoMode.MONITORING].value,
                        'status_korean': self._get_status_korean(self.modes[AutoMode.MONITORING]),
                        'status_icon': self._get_status_icon(self.modes[AutoMode.MONITORING]),
                        'is_active': self.is_mode_active(AutoMode.MONITORING),
                        'config': self.mode_configs[AutoMode.MONITORING].__dict__
                    },
                    'trading': {
                        'status': self.modes[AutoMode.TRADING].value,
                        'status_korean': self._get_status_korean(self.modes[AutoMode.TRADING]),
                        'status_icon': self._get_status_icon(self.modes[AutoMode.TRADING]),
                        'is_active': self.is_mode_active(AutoMode.TRADING),
                        'config': self.mode_configs[AutoMode.TRADING].__dict__
                    }
                },
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            self.logger.error(f"❌ 모드 상태 조회 실패: {e}")
            return {'error': str(e)}

    def _get_status_korean(self, status: ModeStatus) -> str:
        """상태 한글 변환"""
        status_map = {
            ModeStatus.ACTIVE: "활성화",
            ModeStatus.INACTIVE: "비활성화",
            ModeStatus.STANDBY: "대기중"
        }
        return status_map.get(status, "알 수 없음")

    def _get_status_icon(self, status: ModeStatus) -> str:
        """상태 아이콘 반환"""
        icon_map = {
            ModeStatus.ACTIVE: "🟢",
            ModeStatus.INACTIVE: "🔴",
            ModeStatus.STANDBY: "🟡"
        }
        return icon_map.get(status, "⚫")

    def display_status_panel(self):
        """상태 패널 표시"""
        try:
            status_info = self.get_all_mode_status()
            market_info = status_info['market_info']
            modes = status_info['modes']
            
            # 시장 상태 텍스트
            market_text = Text()
            market_text.append(f"🕒 현재 시간: {market_info['current_time']}\n")
            market_text.append(f"📊 시장 상태: {market_info['market_status_korean']}\n")
            market_text.append(f"🔄 거래 가능: {'예' if market_info['is_trading_allowed'] else '아니오'}\n")
            market_text.append(f"👁️ 모니터링 허용: {'예' if market_info['is_monitoring_allowed'] else '아니오'}")
            
            # 모드 상태 텍스트
            mode_text = Text()
            monitoring = modes['monitoring']
            trading = modes['trading']
            
            mode_text.append(f"{monitoring['status_icon']} 매매 모니터링: {monitoring['status_korean']}\n")
            mode_text.append(f"{trading['status_icon']} 매매 모드: {trading['status_korean']}")
            
            # 패널 생성
            market_panel = Panel(
                market_text,
                title="[bold blue]🏛️ 시장 상태[/bold blue]",
                border_style="blue"
            )
            
            mode_panel = Panel(
                mode_text,
                title="[bold green]🎛️ 자동 모드 상태[/bold green]",
                border_style="green"
            )
            
            self.console.print(market_panel)
            self.console.print(mode_panel)
            
        except Exception as e:
            self.logger.error(f"❌ 상태 패널 표시 실패: {e}")
            self.console.print(f"[red]상태 정보를 가져올 수 없습니다: {e}[/red]")

    async def get_recent_status_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """최근 상태 변경 이력 조회"""
        try:
            return self.status_history[-limit:] if len(self.status_history) > limit else self.status_history.copy()
        except Exception as e:
            self.logger.error(f"❌ 상태 이력 조회 실패: {e}")
            return []

    def update_mode_config(self, mode: AutoMode, **kwargs):
        """모드 설정 업데이트"""
        try:
            config = self.mode_configs[mode]
            
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
                    self.logger.info(f"🔧 {mode.value} 모드 설정 업데이트: {key} = {value}")
                else:
                    self.logger.warning(f"⚠️ 알 수 없는 설정 키: {key}")
                    
        except Exception as e:
            self.logger.error(f"❌ {mode.value} 모드 설정 업데이트 실패: {e}")

    async def cleanup(self):
        """정리 작업"""
        try:
            await self._stop_control_tasks()
            self.mode_change_callbacks.clear()
            self.status_history.clear()
            self.logger.info("🧹 자동 모드 제어기 정리 완료")
        except Exception as e:
            self.logger.error(f"❌ 자동 모드 제어기 정리 실패: {e}")