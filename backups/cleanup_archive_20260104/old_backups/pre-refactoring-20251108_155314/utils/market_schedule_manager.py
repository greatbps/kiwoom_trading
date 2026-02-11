#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/market_schedule_manager.py

한국 주식 시장 시간 및 휴장일 관리자
"""

import asyncio
import json
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import pytz

from utils.logger import get_logger

class MarketStatus(Enum):
    """시장 상태"""
    CLOSED = "closed"           # 휴장 (휴일, 휴장일)
    PRE_MARKET = "pre_market"   # 장 시작 전
    OPEN = "open"              # 정규 장
    LUNCH_BREAK = "lunch"      # 점심 시간 (12:00~13:00)
    AFTER_HOURS = "after_hours" # 장 마감 후 (동시호가)
    WEEKEND = "weekend"        # 주말

class TradingSession(Enum):
    """거래 세션"""
    PRE_MARKET = "pre_market"      # 08:00~09:00 (장 시작 전 동시호가)
    REGULAR = "regular"            # 09:00~15:30 (정규 거래)
    LUNCH = "lunch"               # 12:00~13:00 (점심 시간)
    AFTER_HOURS = "after_hours"   # 15:30~16:00 (장 마감 후 동시호가)

@dataclass
class MarketSchedule:
    """시장 일정"""
    date: str
    is_market_open: bool      # opnd_yn
    is_business_day: bool     # bzdy_yn
    is_trading_day: bool      # tr_day_yn
    is_settlement_day: bool   # sttl_day_yn
    weekday_code: str         # wday_dvsn_cd

@dataclass
class TradingHours:
    """거래 시간"""
    pre_market_start: time = time(8, 0)   # 08:00
    market_open: time = time(9, 0)        # 09:00
    lunch_start: time = time(12, 0)       # 12:00
    lunch_end: time = time(13, 0)         # 13:00
    market_close: time = time(15, 30)     # 15:30
    after_hours_end: time = time(16, 0)   # 16:00

class MarketScheduleManager:
    """시장 시간 및 휴장일 관리자"""
    
    def __init__(self, config, kis_collector):
        self.config = config
        self.kis_collector = kis_collector
        self.logger = get_logger("MarketScheduleManager")
        
        # 한국 시간대
        self.kst = pytz.timezone('Asia/Seoul')
        
        # 거래 시간 설정
        self.trading_hours = TradingHours()
        
        # 휴장일 캐시 (메모리 저장)
        self.holiday_cache = {}
        self.cache_expiry = {}
        
        # 상태 변경 콜백들
        self.status_change_callbacks = []
        
        # 현재 상태 추적
        self.current_status = MarketStatus.CLOSED
        self.last_status_check = None
        
        # 모니터링 태스크
        self.monitoring_task = None
        
        self.logger.info("🕒 시장 일정 관리자 초기화 완료")

    async def initialize(self):
        """초기화 및 현재 상태 확인"""
        try:
            # 현재 상태 확인
            await self.update_market_status()
            
            # 오늘과 내일의 휴장일 정보 미리 로드
            today = datetime.now(self.kst).strftime('%Y%m%d')
            tomorrow = (datetime.now(self.kst) + timedelta(days=1)).strftime('%Y%m%d')
            
            await self.get_market_schedule(today)
            await self.get_market_schedule(tomorrow)
            
            self.logger.info(f"✅ 시장 일정 관리자 초기화 완료 - 현재 상태: {self.current_status.value}")
            
        except Exception as e:
            self.logger.error(f"❌ 시장 일정 관리자 초기화 실패: {e}")

    async def start_monitoring(self):
        """시장 상태 모니터링 시작"""
        if self.monitoring_task is None or self.monitoring_task.done():
            self.monitoring_task = asyncio.create_task(self._monitor_market_status())
            self.logger.info("🔄 시장 상태 모니터링 시작")

    async def stop_monitoring(self):
        """시장 상태 모니터링 중지"""
        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.logger.info("⏹️ 시장 상태 모니터링 중지")

    async def _monitor_market_status(self):
        """시장 상태 지속 모니터링"""
        try:
            while True:
                old_status = self.current_status
                await self.update_market_status()
                
                # 상태 변경 시 콜백 실행
                if old_status != self.current_status:
                    self.logger.info(f"📊 시장 상태 변경: {old_status.value} → {self.current_status.value}")
                    await self._notify_status_change(old_status, self.current_status)
                
                # 5분마다 상태 체크 (시장 시간 중에는 더 자주)
                if self.current_status in [MarketStatus.OPEN, MarketStatus.PRE_MARKET, MarketStatus.AFTER_HOURS]:
                    await asyncio.sleep(60)  # 1분
                else:
                    await asyncio.sleep(300)  # 5분
                    
        except asyncio.CancelledError:
            self.logger.info("시장 상태 모니터링 종료")
        except Exception as e:
            self.logger.error(f"❌ 시장 상태 모니터링 오류: {e}")
            await asyncio.sleep(60)  # 오류 시 1분 후 재시도

    async def get_market_schedule(self, date: str) -> Optional[MarketSchedule]:
        """특정 날짜의 시장 일정 조회 - 토큰 방식 (DB 캐시 → API 조회 → DB 저장)"""
        try:
            # 1단계: DB 캐시에서 유효한 데이터 확인
            from database.models import MarketScheduleCache
            from database.database_manager import DatabaseManager
            from config import Config
            
            config = Config()
            db_manager = DatabaseManager(config)
            with db_manager.get_session() as session:
                cached_schedule = MarketScheduleCache.get_valid_schedule(session, date)
                if cached_schedule:
                    self.logger.debug(f"✅ {date} DB 캐시에서 시장 일정 조회")
                    return cached_schedule.to_market_schedule()
            
            # 2단계: DB에 없으면 KIS API 호출
            self.logger.debug(f"📅 {date} KIS API에서 휴장일 정보 조회 중...")
            
            # KIS API 세션 초기화 확인
            await self._ensure_kis_session_initialized()
            
            result = await self.kis_collector._make_api_request(
                method="GET",
                endpoint="/uapi/domestic-stock/v1/quotations/chk-holiday",
                params={
                    "BASS_DT": date,
                    "CTX_AREA_NK": "",
                    "CTX_AREA_FK": ""
                },
                tr_id="CTCA0903R"
            )

            if result.get('rt_cd') != '0':
                error_msg = result.get('msg1', 'Unknown error')
                error_code = result.get('msg_cd', '')

                # EGW00121 토큰 오류인 경우 강제 갱신 시도
                if error_code == 'EGW00121' or "token" in error_msg.lower():
                    self.logger.warning(f"⚠️ 토큰 오류 감지: {error_msg}")
                    try:
                        # 토큰 강제 갱신
                        session = await self.kis_collector.http_session.get_session()
                        await self.kis_collector.token_manager.request_new_token(session)
                        self.logger.info("✅ 토큰 강제 갱신 완료, API 재시도")

                        # 갱신된 토큰으로 재시도
                        result = await self.kis_collector._make_api_request(
                            method="GET",
                            endpoint="/uapi/domestic-stock/v1/quotations/chk-holiday",
                            params={
                                "BASS_DT": date,
                                "CTX_AREA_NK": "",
                                "CTX_AREA_FK": ""
                            },
                            tr_id="CTCA0903R"
                        )

                        if result.get('rt_cd') != '0':
                            self.logger.error(f"❌ 토큰 갱신 후에도 휴장일 조회 실패: {result.get('msg1', 'Unknown error')}")
                            return self._create_fallback_schedule(date)

                    except Exception as token_error:
                        self.logger.error(f"❌ 토큰 갱신 실패: {token_error}")
                        return self._create_fallback_schedule(date)
                else:
                    self.logger.error(f"❌ 휴장일 조회 실패: {error_msg}")
                    return self._create_fallback_schedule(date)
            
            # 3단계: 해당 날짜 데이터 찾기
            output = result.get('output', [])
            api_data = None
            
            for item in output:
                if item.get('bass_dt') == date:
                    api_data = item
                    break
            
            if not api_data:
                self.logger.warning(f"⚠️ {date} 데이터가 API 응답에 없음")
                return self._create_fallback_schedule(date)
            
            # 4단계: DB에 저장하고 MarketSchedule 반환 (UPSERT 방식)
            db_manager = DatabaseManager(config)
            with db_manager.get_session() as session:
                try:
                    cached_schedule = MarketScheduleCache.upsert_from_api(session, date, api_data)
                    self.logger.debug(f"✅ {date} 시장 일정 DB 저장 완료 - 개장: {cached_schedule.is_market_open}")
                    return cached_schedule.to_market_schedule()
                except Exception as db_error:
                    self.logger.error(f"❌ DB 저장 실패: {db_error}")
                    # 세션 롤백 처리
                    try:
                        session.rollback()
                    except:
                        pass
                    # DB 저장 실패해도 API 데이터는 반환
                    return MarketSchedule(
                        date=api_data.get('bass_dt'),
                        is_market_open=api_data.get('opnd_yn') == 'Y',
                        is_business_day=api_data.get('bzdy_yn') == 'Y',
                        is_trading_day=api_data.get('tr_day_yn') == 'Y',
                        is_settlement_day=api_data.get('sttl_day_yn') == 'Y',
                        weekday_code=api_data.get('wday_dvsn_cd')
                    )
            
        except Exception as e:
            self.logger.error(f"❌ {date} KIS API 시장 일정 조회 실패: {e}")

            # Fallback 시스템 사용
            self.logger.info(f"🔄 Fallback 시스템으로 {date} 시장 일정 조회 시도")
            try:
                from utils.fallback_market_schedule import get_fallback_manager

                fallback_manager = get_fallback_manager()
                fallback_result = await fallback_manager.get_market_schedule(date)

                if fallback_result:
                    self.logger.info(f"✅ Fallback으로 {date} 시장 일정 조회 성공 (소스: {fallback_result.source})")
                    return fallback_manager.to_market_schedule(fallback_result)
                else:
                    self.logger.warning(f"⚠️ Fallback도 실패, 기본 스케줄 사용")
                    return self._create_fallback_schedule(date)

            except Exception as fallback_error:
                self.logger.error(f"❌ Fallback 시스템 오류: {fallback_error}")
                return self._create_fallback_schedule(date)

    async def _ensure_kis_session_initialized(self):
        """KIS API 세션 초기화 확인"""
        try:
            if not hasattr(self.kis_collector, 'access_token') or not self.kis_collector.access_token:
                self.logger.info("🔑 KIS API 세션 초기화 중...")
                await self.kis_collector.initialize()
                self.logger.info("✅ KIS API 세션 초기화 완료")
        except Exception as e:
            self.logger.error(f"❌ KIS API 세션 초기화 실패: {e}")
            raise

    def _create_fallback_schedule(self, date: str) -> MarketSchedule:
        """폴백 일정 생성 (주말/공휴일 최소 체크만)"""
        try:
            # 날짜 파싱
            date_obj = datetime.strptime(date, '%Y%m%d')
            weekday = date_obj.weekday()
            
            # 주말이면 휴장
            is_weekend = weekday >= 5  # 토요일(5), 일요일(6)
            
            return MarketSchedule(
                date=date,
                is_market_open=not is_weekend,  # 주말이 아니면 일단 개장으로 가정
                is_business_day=not is_weekend,
                is_trading_day=not is_weekend,
                is_settlement_day=not is_weekend,
                weekday_code=f"{weekday + 1:02d}"  # 01~07 (월~일)
            )
        except Exception:
            # 최후의 폴백: 모든 값 False (안전하게 휴장 처리)
            return MarketSchedule(
                date=date,
                is_market_open=False,
                is_business_day=False,
                is_trading_day=False,
                is_settlement_day=False,
                weekday_code="00"
            )

    async def update_market_status(self) -> MarketStatus:
        """현재 시장 상태 업데이트"""
        try:
            now_kst = datetime.now(self.kst)
            current_date = now_kst.strftime('%Y%m%d')
            current_time = now_kst.time()
            
            # 주말 체크
            if now_kst.weekday() >= 5:  # 토요일(5), 일요일(6)
                self.current_status = MarketStatus.WEEKEND
                return self.current_status
            
            # 시장 일정 조회
            schedule = await self.get_market_schedule(current_date)
            
            if not schedule or not schedule.is_market_open:
                # 휴장일
                self.current_status = MarketStatus.CLOSED
                return self.current_status
            
            # 시간대별 상태 판단
            if current_time < self.trading_hours.pre_market_start:
                # 08:00 이전
                self.current_status = MarketStatus.CLOSED
            elif current_time < self.trading_hours.market_open:
                # 08:00~09:00 (장 시작 전 동시호가)
                self.current_status = MarketStatus.PRE_MARKET
            elif current_time < self.trading_hours.lunch_start:
                # 09:00~12:00 (오전 정규 거래)
                self.current_status = MarketStatus.OPEN
            elif current_time < self.trading_hours.lunch_end:
                # 12:00~13:00 (점심 시간)
                self.current_status = MarketStatus.LUNCH_BREAK
            elif current_time < self.trading_hours.market_close:
                # 13:00~15:30 (오후 정규 거래)
                self.current_status = MarketStatus.OPEN
            elif current_time < self.trading_hours.after_hours_end:
                # 15:30~16:00 (장 마감 후 동시호가)
                self.current_status = MarketStatus.AFTER_HOURS
            else:
                # 16:00 이후
                self.current_status = MarketStatus.CLOSED
            
            self.last_status_check = now_kst
            return self.current_status
            
        except Exception as e:
            self.logger.error(f"❌ 시장 상태 업데이트 실패: {e}")
            self.current_status = MarketStatus.CLOSED
            return self.current_status

    def is_market_open_now(self) -> bool:
        """현재 시장이 열려있는지 확인"""
        return self.current_status == MarketStatus.OPEN

    def is_trading_allowed_now(self) -> bool:
        """현재 거래가 가능한지 확인 (동시호가 포함)"""
        allowed_statuses = [
            MarketStatus.OPEN,
            MarketStatus.PRE_MARKET,
            MarketStatus.AFTER_HOURS
        ]
        return self.current_status in allowed_statuses

    def is_monitoring_allowed_now(self) -> bool:
        """현재 모니터링이 허용되는지 확인"""
        # 장 운영 시간 (09:00~15:30) 중 모니터링 허용
        # 점심시간(12:00~13:00)에도 장은 계속되므로 모니터링 허용
        # AFTER_HOURS는 제외 (15:30 이후 동시호가는 매매 불가)
        allowed_statuses = [
            MarketStatus.OPEN,           # 정규 거래 시간
            MarketStatus.PRE_MARKET,     # 장 시작 전 동시호가  
            MarketStatus.LUNCH_BREAK     # 점심 시간 (장은 계속됨)
        ]
        return self.current_status in allowed_statuses

    async def get_next_market_open(self) -> Optional[datetime]:
        """다음 장 개장 시간 조회"""
        try:
            now_kst = datetime.now(self.kst)
            
            # 오늘부터 최대 7일까지 확인
            for i in range(8):
                check_date = now_kst + timedelta(days=i)
                date_str = check_date.strftime('%Y%m%d')
                
                # 주말 제외
                if check_date.weekday() >= 5:
                    continue
                
                schedule = await self.get_market_schedule(date_str)
                if schedule and schedule.is_market_open:
                    # 해당 날짜의 09:00 반환
                    market_open_time = check_date.replace(
                        hour=9, minute=0, second=0, microsecond=0
                    )
                    
                    # 오늘이고 이미 장이 시작된 경우 다음날 확인
                    if i == 0 and now_kst.time() >= self.trading_hours.market_open:
                        continue
                    
                    return market_open_time
            
            return None
            
        except Exception as e:
            self.logger.error(f"❌ 다음 장 개장 시간 조회 실패: {e}")
            return None

    async def get_market_close_today(self) -> Optional[datetime]:
        """오늘 장 마감 시간 조회"""
        try:
            now_kst = datetime.now(self.kst)
            today_str = now_kst.strftime('%Y%m%d')
            
            schedule = await self.get_market_schedule(today_str)
            if schedule and schedule.is_market_open:
                return now_kst.replace(
                    hour=15, minute=30, second=0, microsecond=0
                )
            
            return None
            
        except Exception as e:
            self.logger.error(f"❌ 오늘 장 마감 시간 조회 실패: {e}")
            return None

    def add_status_change_callback(self, callback):
        """상태 변경 콜백 등록"""
        self.status_change_callbacks.append(callback)

    def remove_status_change_callback(self, callback):
        """상태 변경 콜백 제거"""
        if callback in self.status_change_callbacks:
            self.status_change_callbacks.remove(callback)

    async def _notify_status_change(self, old_status: MarketStatus, new_status: MarketStatus):
        """상태 변경 알림"""
        try:
            for callback in self.status_change_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(old_status, new_status)
                    else:
                        callback(old_status, new_status)
                except Exception as e:
                    self.logger.error(f"❌ 상태 변경 콜백 실행 실패: {e}")
                    
        except Exception as e:
            self.logger.error(f"❌ 상태 변경 알림 실패: {e}")

    def get_current_status_info(self) -> Dict[str, Any]:
        """현재 상태 정보 조회"""
        try:
            now_kst = datetime.now(self.kst)
            
            return {
                'current_time': now_kst.strftime('%Y-%m-%d %H:%M:%S'),
                'market_status': self.current_status.value,
                'market_status_korean': self._get_status_korean(self.current_status),
                'is_market_open': self.is_market_open_now(),
                'is_trading_allowed': self.is_trading_allowed_now(),
                'is_monitoring_allowed': self.is_monitoring_allowed_now(),
                'last_status_check': self.last_status_check.strftime('%Y-%m-%d %H:%M:%S') if self.last_status_check else None,
                'trading_hours': {
                    'pre_market': f"{self.trading_hours.pre_market_start.strftime('%H:%M')}~{self.trading_hours.market_open.strftime('%H:%M')}",
                    'morning': f"{self.trading_hours.market_open.strftime('%H:%M')}~{self.trading_hours.lunch_start.strftime('%H:%M')}",
                    'lunch': f"{self.trading_hours.lunch_start.strftime('%H:%M')}~{self.trading_hours.lunch_end.strftime('%H:%M')}",
                    'afternoon': f"{self.trading_hours.lunch_end.strftime('%H:%M')}~{self.trading_hours.market_close.strftime('%H:%M')}",
                    'after_hours': f"{self.trading_hours.market_close.strftime('%H:%M')}~{self.trading_hours.after_hours_end.strftime('%H:%M')}"
                }
            }
            
        except Exception as e:
            self.logger.error(f"❌ 현재 상태 정보 조회 실패: {e}")
            return {'error': str(e)}

    def _get_status_korean(self, status: MarketStatus) -> str:
        """상태 한글 변환"""
        status_map = {
            MarketStatus.CLOSED: "휴장",
            MarketStatus.PRE_MARKET: "장 시작 전 (동시호가)",
            MarketStatus.OPEN: "정규 거래",
            MarketStatus.LUNCH_BREAK: "점심 시간",
            MarketStatus.AFTER_HOURS: "장 마감 후 (동시호가)",
            MarketStatus.WEEKEND: "주말"
        }
        return status_map.get(status, "알 수 없음")

    async def get_weekly_schedule(self) -> List[Dict[str, Any]]:
        """이번 주 시장 일정 조회"""
        try:
            now_kst = datetime.now(self.kst)
            # 이번 주 월요일부터 시작
            monday = now_kst - timedelta(days=now_kst.weekday())
            
            weekly_schedule = []
            
            for i in range(7):  # 월~일
                check_date = monday + timedelta(days=i)
                date_str = check_date.strftime('%Y%m%d')
                
                schedule = await self.get_market_schedule(date_str)
                
                day_info = {
                    'date': check_date.strftime('%Y-%m-%d'),
                    'weekday': check_date.strftime('%A'),
                    'weekday_korean': ['월', '화', '수', '목', '금', '토', '일'][check_date.weekday()],
                    'is_market_open': schedule.is_market_open if schedule else False,
                    'is_today': check_date.date() == now_kst.date()
                }
                
                weekly_schedule.append(day_info)
            
            return weekly_schedule
            
        except Exception as e:
            self.logger.error(f"❌ 주간 시장 일정 조회 실패: {e}")
            return []

    async def cleanup(self):
        """정리 작업"""
        try:
            await self.stop_monitoring()
            self.status_change_callbacks.clear()
            self.logger.info("🧹 시장 일정 관리자 정리 완료")
        except Exception as e:
            self.logger.error(f"❌ 시장 일정 관리자 정리 실패: {e}")