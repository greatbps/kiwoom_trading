#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Rate Limiter - KIS API 초당 20건 제한 처리
"""

import asyncio
import time
from collections import deque
from typing import Optional
from utils.logger import get_logger

logger = get_logger("RateLimiter")

class RateLimiter:
    """API 호출 속도 제한 관리 (초당 20건)"""
    
    def __init__(self, max_calls: int = 20, time_window: float = 1.0):
        """
        Args:
            max_calls: 시간 윈도우당 최대 호출 수 (기본: 20)
            time_window: 시간 윈도우 (초) (기본: 1.0)
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """API 호출 권한 획득 (필요시 대기)"""
        async with self._lock:
            now = time.time()
            
            # 시간 윈도우 밖의 오래된 호출 기록 제거
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            
            # 제한 초과 시 대기
            if len(self.calls) >= self.max_calls:
                sleep_time = self.calls[0] + self.time_window - now
                if sleep_time > 0:
                    logger.debug(f"⏳ Rate limit 도달, {sleep_time:.2f}초 대기...")
                    await asyncio.sleep(sleep_time)
                    # 대기 후 다시 정리
                    now = time.time()
                    while self.calls and self.calls[0] < now - self.time_window:
                        self.calls.popleft()
            
            # 현재 호출 기록
            self.calls.append(now)
    
    async def batch_acquire(self, count: int):
        """여러 호출을 위한 배치 획득"""
        for _ in range(count):
            await self.acquire()

# 전역 Rate Limiter 인스턴스
_global_limiter: Optional[RateLimiter] = None

def get_rate_limiter() -> RateLimiter:
    """전역 Rate Limiter 인스턴스 반환"""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter(max_calls=18, time_window=1.0)  # 안전 마진: 20 -> 18
    return _global_limiter

async def rate_limited_call(func, *args, **kwargs):
    """Rate limit 적용된 함수 호출"""
    limiter = get_rate_limiter()
    await limiter.acquire()
    return await func(*args, **kwargs)
