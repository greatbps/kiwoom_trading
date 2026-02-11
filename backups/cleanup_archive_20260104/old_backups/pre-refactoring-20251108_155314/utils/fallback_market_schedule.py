#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fallback 시장 일정 관리자
KIS API 실패 시 대안으로 사용
"""

import json
import asyncio
import aiohttp
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import pytz

from utils.logger import get_logger

class MarketStatus(Enum):
    """시장 상태"""
    CLOSED = "closed"
    PRE_MARKET = "pre_market"
    OPEN = "open"
    LUNCH_BREAK = "lunch"
    AFTER_HOURS = "after_hours"
    WEEKEND = "weekend"

@dataclass
class FallbackMarketSchedule:
    """Fallback 시장 일정"""
    date: str
    is_market_open: bool
    is_business_day: bool
    is_trading_day: bool
    is_settlement_day: bool
    weekday_code: str
    source: str = "fallback"  # 데이터 소스 표시

class FallbackMarketScheduleManager:
    """Fallback 시장 일정 관리자"""

    def __init__(self):
        self.logger = get_logger("FallbackMarketSchedule")
        self.kst = pytz.timezone('Asia/Seoul')

        # 2025년 한국 휴장일 (확정 + 예상)
        self.korea_holidays_2025 = {
            # 확정된 2025년 휴장일
            "20250101": "신정",
            "20250127": "설날연휴",
            "20250128": "설날",
            "20250129": "설날연휴",
            "20250301": "삼일절",
            "20250505": "어린이날",
            "20250515": "부처님오신날",
            "20250606": "현충일",
            "20250815": "광복절",
            "20250916": "추석연휴",
            "20250917": "추석",
            "20250918": "추석연휴",
            "20251003": "개천절",
            "20251009": "한글날",
            "20251225": "크리스마스",

            # 임시 휴장일 (필요시)
            "20250103": "연휴연장",  # 금요일이면 연휴
            "20250130": "설날연휴연장",  # 목요일이면 연휴
        }

        # 거래 시간
        self.trading_hours = {
            'pre_market_start': time(8, 0),
            'market_open': time(9, 0),
            'lunch_start': time(12, 0),
            'lunch_end': time(13, 0),
            'market_close': time(15, 30),
            'after_hours_end': time(16, 0)
        }

    async def get_market_schedule(self, date: str) -> Optional[FallbackMarketSchedule]:
        """특정 날짜의 시장 일정 조회 (Fallback)"""
        try:
            self.logger.debug(f"📅 Fallback으로 {date} 시장 일정 조회")

            # 1단계: 외부 API 시도
            external_result = await self._try_external_holiday_api(date)
            if external_result:
                self.logger.info(f"✅ 외부 API로 {date} 시장 일정 조회 성공")
                return external_result

            # 2단계: 하드코딩된 휴장일 체크
            hardcoded_result = self._check_hardcoded_holidays(date)
            if hardcoded_result:
                self.logger.info(f"✅ 하드코딩 데이터로 {date} 시장 일정 조회 성공")
                return hardcoded_result

            # 3단계: 기본 규칙 적용 (주말 체크 등)
            default_result = self._apply_default_rules(date)
            self.logger.info(f"✅ 기본 규칙으로 {date} 시장 일정 생성")
            return default_result

        except Exception as e:
            self.logger.error(f"❌ Fallback 시장 일정 조회 실패: {e}")
            return self._create_emergency_schedule(date)

    async def _try_external_holiday_api(self, date: str) -> Optional[FallbackMarketSchedule]:
        """외부 공휴일 API 시도"""
        try:
            # 한국 공휴일 API들 시도
            apis = [
                f"https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo?serviceKey=YOUR_KEY&solYear={date[:4]}&solMonth={date[4:6]}",
                f"https://holidayapi.com/v1/holidays?key=YOUR_KEY&country=KR&year={date[:4]}&month={date[4:6]}&day={date[6:8]}",
            ]

            timeout = aiohttp.ClientTimeout(total=5)  # 빠른 타임아웃

            async with aiohttp.ClientSession(timeout=timeout) as session:
                for api_url in apis:
                    try:
                        # 실제 API 키가 없으므로 빠르게 실패하도록
                        if "YOUR_KEY" in api_url:
                            continue

                        async with session.get(api_url) as response:
                            if response.status == 200:
                                data = await response.json()
                                # API 응답 파싱 로직
                                is_holiday = self._parse_holiday_api_response(data, date)

                                if is_holiday is not None:
                                    return self._create_schedule_from_holiday_status(date, is_holiday, "external_api")

                    except Exception:
                        continue  # 다음 API 시도

            return None

        except Exception as e:
            self.logger.debug(f"외부 API 호출 실패: {e}")
            return None

    def _parse_holiday_api_response(self, data: dict, date: str) -> Optional[bool]:
        """외부 API 응답 파싱"""
        # 실제 구현에서는 각 API별 응답 형식에 맞게 파싱
        # 현재는 스켈레톤만 제공
        return None

    def _check_hardcoded_holidays(self, date: str) -> Optional[FallbackMarketSchedule]:
        """하드코딩된 휴장일 체크"""
        try:
            # 하드코딩된 휴장일 확인
            if date in self.korea_holidays_2025:
                holiday_name = self.korea_holidays_2025[date]
                self.logger.debug(f"하드코딩 휴장일: {date} ({holiday_name})")

                return FallbackMarketSchedule(
                    date=date,
                    is_market_open=False,
                    is_business_day=False,
                    is_trading_day=False,
                    is_settlement_day=False,
                    weekday_code=self._get_weekday_code(date),
                    source=f"hardcoded_holiday_{holiday_name}"
                )

            return None

        except Exception as e:
            self.logger.error(f"하드코딩 휴장일 체크 실패: {e}")
            return None

    def _apply_default_rules(self, date: str) -> FallbackMarketSchedule:
        """기본 규칙 적용 (주말, 평일 구분)"""
        try:
            # 날짜 파싱
            date_obj = datetime.strptime(date, '%Y%m%d')
            weekday = date_obj.weekday()  # 0=월요일, 6=일요일
            weekday_code = str(weekday + 1)  # KIS API 형식 (1=월, 7=일)

            # 주말 체크 (토요일=5, 일요일=6)
            is_weekend = weekday >= 5

            # 기본적으로 주말이 아니면 개장
            is_market_open = not is_weekend
            is_business_day = not is_weekend
            is_trading_day = not is_weekend
            is_settlement_day = not is_weekend

            return FallbackMarketSchedule(
                date=date,
                is_market_open=is_market_open,
                is_business_day=is_business_day,
                is_trading_day=is_trading_day,
                is_settlement_day=is_settlement_day,
                weekday_code=weekday_code,
                source="default_rules"
            )

        except Exception as e:
            self.logger.error(f"기본 규칙 적용 실패: {e}")
            return self._create_emergency_schedule(date)

    def _create_schedule_from_holiday_status(self, date: str, is_holiday: bool, source: str) -> FallbackMarketSchedule:
        """휴일 여부로부터 시장 일정 생성"""
        is_market_open = not is_holiday

        return FallbackMarketSchedule(
            date=date,
            is_market_open=is_market_open,
            is_business_day=is_market_open,
            is_trading_day=is_market_open,
            is_settlement_day=is_market_open,
            weekday_code=self._get_weekday_code(date),
            source=source
        )

    def _create_emergency_schedule(self, date: str) -> FallbackMarketSchedule:
        """긴급 상황용 기본 스케줄 생성"""
        # 최악의 경우: 평일로 가정
        return FallbackMarketSchedule(
            date=date,
            is_market_open=True,
            is_business_day=True,
            is_trading_day=True,
            is_settlement_day=True,
            weekday_code="1",
            source="emergency_default"
        )

    def _get_weekday_code(self, date: str) -> str:
        """날짜로부터 요일 코드 생성"""
        try:
            date_obj = datetime.strptime(date, '%Y%m%d')
            return str(date_obj.weekday() + 1)  # 1=월요일, 7=일요일
        except:
            return "1"  # 기본값

    def get_current_market_status(self) -> MarketStatus:
        """현재 시장 상태 반환"""
        try:
            now_kst = datetime.now(self.kst)
            current_time = now_kst.time()
            current_date = now_kst.strftime('%Y%m%d')

            # 오늘 시장 일정 확인 (동기 버전)
            schedule = asyncio.run(self.get_market_schedule(current_date))

            if not schedule or not schedule.is_market_open:
                return MarketStatus.CLOSED

            # 시간대별 상태 체크
            if current_time < self.trading_hours['pre_market_start']:
                return MarketStatus.CLOSED
            elif current_time < self.trading_hours['market_open']:
                return MarketStatus.PRE_MARKET
            elif current_time < self.trading_hours['lunch_start']:
                return MarketStatus.OPEN
            elif current_time < self.trading_hours['lunch_end']:
                return MarketStatus.LUNCH_BREAK
            elif current_time < self.trading_hours['market_close']:
                return MarketStatus.OPEN
            elif current_time < self.trading_hours['after_hours_end']:
                return MarketStatus.AFTER_HOURS
            else:
                return MarketStatus.CLOSED

        except Exception as e:
            self.logger.error(f"현재 시장 상태 확인 실패: {e}")
            return MarketStatus.CLOSED

    async def get_next_trading_day(self) -> Optional[datetime]:
        """다음 거래일 반환"""
        try:
            now_kst = datetime.now(self.kst)

            for i in range(1, 10):  # 최대 10일까지 확인
                check_date = now_kst + timedelta(days=i)
                date_str = check_date.strftime('%Y%m%d')

                schedule = await self.get_market_schedule(date_str)
                if schedule and schedule.is_trading_day:
                    return check_date.replace(hour=9, minute=0, second=0, microsecond=0)

            return None

        except Exception as e:
            self.logger.error(f"다음 거래일 조회 실패: {e}")
            return None

    def to_market_schedule(self, fallback_schedule: FallbackMarketSchedule):
        """기존 MarketSchedule 형식으로 변환"""
        from utils.market_schedule_manager import MarketSchedule

        return MarketSchedule(
            date=fallback_schedule.date,
            is_market_open=fallback_schedule.is_market_open,
            is_business_day=fallback_schedule.is_business_day,
            is_trading_day=fallback_schedule.is_trading_day,
            is_settlement_day=fallback_schedule.is_settlement_day,
            weekday_code=fallback_schedule.weekday_code
        )

# 전역 인스턴스
_fallback_manager = None

def get_fallback_manager() -> FallbackMarketScheduleManager:
    """Fallback 매니저 싱글톤 인스턴스 반환"""
    global _fallback_manager
    if _fallback_manager is None:
        _fallback_manager = FallbackMarketScheduleManager()
    return _fallback_manager