#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils/retry.py

Advanced Retry Logic with Exponential Backoff
- 지수 백오프 기반 자동 재시도
- 다양한 에러 타입별 재시도 정책
- 비동기 함수 지원
- 메트릭 수집 및 로깅
"""

import asyncio
import functools
import logging
import random
import time
from typing import (
    Callable,
    Optional,
    Tuple,
    Type,
    Union,
    Any,
    TypeVar,
    Awaitable
)
from datetime import datetime, timedelta
from enum import Enum


# Type hints
T = TypeVar('T')
DecoratedFunc = TypeVar('DecoratedFunc', bound=Callable[..., Any])


class RetryStrategy(Enum):
    """재시도 전략"""
    EXPONENTIAL = "exponential"  # 지수 백오프
    LINEAR = "linear"            # 선형 증가
    FIXED = "fixed"              # 고정 지연
    JITTER = "jitter"            # 랜덤 지터 추가


class RetryPolicy:
    """재시도 정책 설정"""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
        retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        non_retryable_exceptions: Tuple[Type[Exception], ...] = (),
        on_retry: Optional[Callable] = None,
        on_failure: Optional[Callable] = None,
    ):
        """
        Args:
            max_attempts: 최대 시도 횟수 (기본: 3)
            initial_delay: 초기 지연 시간 (초)
            max_delay: 최대 지연 시간 (초)
            exponential_base: 지수 백오프 기저 (기본: 2.0)
            jitter: 랜덤 지터 추가 여부
            strategy: 재시도 전략
            retryable_exceptions: 재시도 가능한 예외 타입
            non_retryable_exceptions: 재시도 불가능한 예외 타입 (우선순위 높음)
            on_retry: 재시도 시 호출될 콜백
            on_failure: 모든 시도 실패 시 호출될 콜백
        """
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.strategy = strategy
        self.retryable_exceptions = retryable_exceptions
        self.non_retryable_exceptions = non_retryable_exceptions
        self.on_retry = on_retry
        self.on_failure = on_failure

    def calculate_delay(self, attempt: int) -> float:
        """
        재시도 지연 시간 계산

        Args:
            attempt: 현재 시도 횟수 (0부터 시작)

        Returns:
            지연 시간 (초)
        """
        if self.strategy == RetryStrategy.EXPONENTIAL:
            delay = min(
                self.initial_delay * (self.exponential_base ** attempt),
                self.max_delay
            )
        elif self.strategy == RetryStrategy.LINEAR:
            delay = min(
                self.initial_delay * (attempt + 1),
                self.max_delay
            )
        elif self.strategy == RetryStrategy.FIXED:
            delay = self.initial_delay
        elif self.strategy == RetryStrategy.JITTER:
            delay = min(
                self.initial_delay * (self.exponential_base ** attempt),
                self.max_delay
            )
        else:
            delay = self.initial_delay

        # Jitter 추가 (랜덤성)
        if self.jitter:
            jitter_amount = delay * 0.1  # 10% jitter
            delay += random.uniform(-jitter_amount, jitter_amount)

        return max(0, delay)  # 음수 방지

    def should_retry(self, exception: Exception) -> bool:
        """
        예외 발생 시 재시도 여부 결정

        Args:
            exception: 발생한 예외

        Returns:
            재시도 가능 여부
        """
        # Non-retryable 예외는 우선순위 높음
        if isinstance(exception, self.non_retryable_exceptions):
            return False

        # Retryable 예외 확인
        if isinstance(exception, self.retryable_exceptions):
            return True

        return False


class RetryMetrics:
    """재시도 메트릭 수집"""

    def __init__(self):
        self.total_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0
        self.total_retries = 0
        self.total_delay = 0.0
        self.last_error: Optional[Exception] = None
        self.last_error_time: Optional[datetime] = None

    def record_attempt(self, success: bool, retries: int, delay: float):
        """시도 결과 기록"""
        self.total_calls += 1
        if success:
            self.successful_calls += 1
        else:
            self.failed_calls += 1
        self.total_retries += retries
        self.total_delay += delay

    def record_error(self, error: Exception):
        """에러 기록"""
        self.last_error = error
        self.last_error_time = datetime.now()

    def get_stats(self) -> dict:
        """통계 반환"""
        return {
            'total_calls': self.total_calls,
            'successful_calls': self.successful_calls,
            'failed_calls': self.failed_calls,
            'success_rate': (
                self.successful_calls / self.total_calls
                if self.total_calls > 0 else 0
            ),
            'total_retries': self.total_retries,
            'avg_retries': (
                self.total_retries / self.total_calls
                if self.total_calls > 0 else 0
            ),
            'total_delay': self.total_delay,
            'avg_delay': (
                self.total_delay / self.total_calls
                if self.total_calls > 0 else 0
            ),
            'last_error': str(self.last_error) if self.last_error else None,
            'last_error_time': self.last_error_time.isoformat() if self.last_error_time else None,
        }


def retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    non_retryable_exceptions: Tuple[Type[Exception], ...] = (),
    logger: Optional[logging.Logger] = None,
    log_level: int = logging.WARNING,
    collect_metrics: bool = False,
) -> Callable[[DecoratedFunc], DecoratedFunc]:
    """
    재시도 데코레이터 (동기/비동기 함수 모두 지원)

    Example:
        @retry(max_attempts=5, initial_delay=2.0)
        def api_call():
            # API 호출 로직
            pass

        @retry(max_attempts=3, retryable_exceptions=(ConnectionError,))
        async def async_api_call():
            # 비동기 API 호출
            pass
    """

    # 로거 설정
    if logger is None:
        logger = logging.getLogger(__name__)

    # 정책 생성
    policy = RetryPolicy(
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        max_delay=max_delay,
        exponential_base=exponential_base,
        jitter=jitter,
        strategy=strategy,
        retryable_exceptions=retryable_exceptions,
        non_retryable_exceptions=non_retryable_exceptions,
    )

    # 메트릭 수집기
    metrics = RetryMetrics() if collect_metrics else None

    def decorator(func: DecoratedFunc) -> DecoratedFunc:
        # 비동기 함수 처리
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                attempt = 0
                total_delay = 0.0
                last_exception = None

                while attempt < policy.max_attempts:
                    try:
                        result = await func(*args, **kwargs)

                        # 성공 메트릭 기록
                        if metrics:
                            metrics.record_attempt(True, attempt, total_delay)

                        return result

                    except Exception as e:
                        last_exception = e

                        # 재시도 불가능한 예외인지 확인
                        if not policy.should_retry(e):
                            logger.log(
                                log_level,
                                f"Non-retryable error in {func.__name__}: {e}"
                            )
                            if metrics:
                                metrics.record_error(e)
                                metrics.record_attempt(False, attempt, total_delay)
                            raise

                        attempt += 1

                        # 최대 시도 횟수 도달
                        if attempt >= policy.max_attempts:
                            logger.log(
                                log_level,
                                f"Max attempts ({policy.max_attempts}) reached for {func.__name__}"
                            )
                            if metrics:
                                metrics.record_error(e)
                                metrics.record_attempt(False, attempt, total_delay)
                            raise

                        # 지연 시간 계산
                        delay = policy.calculate_delay(attempt - 1)
                        total_delay += delay

                        logger.log(
                            log_level,
                            f"Retry {attempt}/{policy.max_attempts} for {func.__name__} "
                            f"after {delay:.2f}s delay. Error: {e}"
                        )

                        # 지연 후 재시도
                        await asyncio.sleep(delay)

                # 모든 시도 실패 (이론상 도달 불가)
                if metrics:
                    metrics.record_error(last_exception)
                    metrics.record_attempt(False, attempt, total_delay)
                raise last_exception

            async_wrapper.metrics = metrics  # 메트릭 접근 제공
            return async_wrapper

        # 동기 함수 처리
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                attempt = 0
                total_delay = 0.0
                last_exception = None

                while attempt < policy.max_attempts:
                    try:
                        result = func(*args, **kwargs)

                        # 성공 메트릭 기록
                        if metrics:
                            metrics.record_attempt(True, attempt, total_delay)

                        return result

                    except Exception as e:
                        last_exception = e

                        # 재시도 불가능한 예외인지 확인
                        if not policy.should_retry(e):
                            logger.log(
                                log_level,
                                f"Non-retryable error in {func.__name__}: {e}"
                            )
                            if metrics:
                                metrics.record_error(e)
                                metrics.record_attempt(False, attempt, total_delay)
                            raise

                        attempt += 1

                        # 최대 시도 횟수 도달
                        if attempt >= policy.max_attempts:
                            logger.log(
                                log_level,
                                f"Max attempts ({policy.max_attempts}) reached for {func.__name__}"
                            )
                            if metrics:
                                metrics.record_error(e)
                                metrics.record_attempt(False, attempt, total_delay)
                            raise

                        # 지연 시간 계산
                        delay = policy.calculate_delay(attempt - 1)
                        total_delay += delay

                        logger.log(
                            log_level,
                            f"Retry {attempt}/{policy.max_attempts} for {func.__name__} "
                            f"after {delay:.2f}s delay. Error: {e}"
                        )

                        # 지연 후 재시도
                        time.sleep(delay)

                # 모든 시도 실패 (이론상 도달 불가)
                if metrics:
                    metrics.record_error(last_exception)
                    metrics.record_attempt(False, attempt, total_delay)
                raise last_exception

            sync_wrapper.metrics = metrics  # 메트릭 접근 제공
            return sync_wrapper

    return decorator


# 자주 사용되는 재시도 프리셋
def retry_on_network_error(
    max_attempts: int = 5,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Callable[[DecoratedFunc], DecoratedFunc]:
    """네트워크 에러 전용 재시도"""
    import aiohttp

    return retry(
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        max_delay=max_delay,
        retryable_exceptions=(
            ConnectionError,
            TimeoutError,
            aiohttp.ClientConnectionError,
            aiohttp.ServerTimeoutError,
        ),
        strategy=RetryStrategy.EXPONENTIAL,
        jitter=True,
    )


def retry_on_api_rate_limit(
    max_attempts: int = 3,
    initial_delay: float = 5.0,
    max_delay: float = 60.0,
) -> Callable[[DecoratedFunc], DecoratedFunc]:
    """API Rate Limit 전용 재시도"""
    return retry(
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        max_delay=max_delay,
        exponential_base=3.0,  # 더 긴 백오프
        strategy=RetryStrategy.EXPONENTIAL,
        jitter=True,
    )


def retry_with_metrics(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
) -> Callable[[DecoratedFunc], DecoratedFunc]:
    """메트릭 수집 포함 재시도"""
    return retry(
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        collect_metrics=True,
        log_level=logging.INFO,
    )


# 편의 함수
def get_retry_metrics(func: Callable) -> Optional[RetryMetrics]:
    """
    재시도 데코레이터가 적용된 함수의 메트릭 반환

    Args:
        func: 재시도 데코레이터가 적용된 함수

    Returns:
        메트릭 객체 (없으면 None)
    """
    return getattr(func, 'metrics', None)
