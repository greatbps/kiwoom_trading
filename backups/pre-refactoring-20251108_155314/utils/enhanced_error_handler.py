#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enhanced_error_handler.py

강화된 에러 처리 및 자동 복구 시스템
"""

import asyncio
import logging
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Union, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
import time
from functools import wraps
import weakref
from collections import deque, defaultdict

from utils.logger import get_logger

class ErrorSeverity(Enum):
    """에러 심각도"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class RecoveryStrategy(Enum):
    """복구 전략"""
    RETRY = "retry"
    FALLBACK = "fallback"
    SKIP = "skip"
    SHUTDOWN = "shutdown"
    MANUAL = "manual"

@dataclass
class ErrorInfo:
    """에러 정보"""
    error_id: str
    timestamp: datetime
    error_type: str
    error_message: str
    severity: ErrorSeverity
    module: str
    function: str
    traceback_str: str
    context: Dict[str, Any] = field(default_factory=dict)
    recovery_attempts: int = 0
    max_recovery_attempts: int = 3
    last_recovery_attempt: Optional[datetime] = None
    recovery_strategy: RecoveryStrategy = RecoveryStrategy.RETRY

@dataclass
class RecoveryAction:
    """복구 액션"""
    action_id: str
    error_id: str
    strategy: RecoveryStrategy
    action_func: Callable
    parameters: Dict[str, Any] = field(default_factory=dict)
    success_count: int = 0
    failure_count: int = 0
    last_executed: Optional[datetime] = None

class CircuitBreaker:
    """회로 차단기 패턴"""

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        expected_exception: Exception = Exception
    ):
        """
        회로 차단기 초기화

        Args:
            failure_threshold: 실패 임계값
            reset_timeout: 재설정 타임아웃 (초)
            expected_exception: 예상 예외 타입
        """
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open

    def __call__(self, func):
        """데코레이터로 사용"""
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await self._call_async(func, *args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            return self._call_sync(func, *args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    async def _call_async(self, func, *args, **kwargs):
        """비동기 함수 호출"""
        if self.state == "open":
            if self._should_attempt_reset():
                self.state = "half-open"
            else:
                raise Exception(f"Circuit breaker is OPEN. Last failure: {self.last_failure_time}")

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e

    def _call_sync(self, func, *args, **kwargs):
        """동기 함수 호출"""
        if self.state == "open":
            if self._should_attempt_reset():
                self.state = "half-open"
            else:
                raise Exception(f"Circuit breaker is OPEN. Last failure: {self.last_failure_time}")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e

    def _should_attempt_reset(self) -> bool:
        """재설정을 시도해야 하는지 확인"""
        return (
            self.last_failure_time and
            time.time() - self.last_failure_time > self.reset_timeout
        )

    def _on_success(self):
        """성공 시 처리"""
        self.failure_count = 0
        self.state = "closed"

    def _on_failure(self):
        """실패 시 처리"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = "open"

class RetryManager:
    """재시도 관리자"""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        """
        재시도 관리자 초기화

        Args:
            max_attempts: 최대 시도 횟수
            base_delay: 기본 지연 시간 (초)
            max_delay: 최대 지연 시간 (초)
            exponential_base: 지수 백오프 기준값
            jitter: 지연 시간에 랜덤 요소 추가
        """
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def calculate_delay(self, attempt: int) -> float:
        """지연 시간 계산"""
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )

        if self.jitter:
            import random
            delay *= (0.5 + random.random() * 0.5)  # 50-100% 범위

        return delay

    async def execute_with_retry(
        self,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """재시도 포함 함수 실행"""
        last_exception = None

        for attempt in range(self.max_attempts):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)

            except Exception as e:
                last_exception = e

                if attempt < self.max_attempts - 1:
                    delay = self.calculate_delay(attempt)
                    await asyncio.sleep(delay)
                else:
                    break

        # 모든 재시도가 실패한 경우
        raise last_exception

class ErrorRecoverySystem:
    """에러 복구 시스템"""

    def __init__(self, max_error_history: int = 1000):
        """
        에러 복구 시스템 초기화

        Args:
            max_error_history: 최대 에러 히스토리 크기
        """
        self.logger = get_logger("ErrorRecoverySystem")
        self.max_error_history = max_error_history

        # 에러 추적
        self.error_history: deque[ErrorInfo] = deque(maxlen=max_error_history)
        self.error_patterns: Dict[str, int] = defaultdict(int)
        self.recovery_actions: Dict[str, RecoveryAction] = {}

        # 회로 차단기들
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}

        # 재시도 관리자
        self.retry_manager = RetryManager()

        # 알림 콜백
        self.alert_callbacks: List[Callable] = []

        # 복구 상태
        self.recovery_state = {
            "active_recoveries": 0,
            "total_recoveries": 0,
            "success_rate": 0.0
        }

    def register_recovery_action(
        self,
        error_pattern: str,
        strategy: RecoveryStrategy,
        action_func: Callable,
        **parameters
    ) -> str:
        """복구 액션 등록"""
        action_id = f"{error_pattern}_{strategy.value}_{time.time()}"

        action = RecoveryAction(
            action_id=action_id,
            error_id=error_pattern,
            strategy=strategy,
            action_func=action_func,
            parameters=parameters
        )

        self.recovery_actions[action_id] = action
        self.logger.info(f"📋 복구 액션 등록: {action_id}")

        return action_id

    def register_circuit_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0
    ) -> CircuitBreaker:
        """회로 차단기 등록"""
        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout
        )

        self.circuit_breakers[name] = breaker
        self.logger.info(f"🔌 회로 차단기 등록: {name}")

        return breaker

    def add_alert_callback(self, callback: Callable[[ErrorInfo], None]):
        """알림 콜백 추가"""
        self.alert_callbacks.append(callback)

    async def handle_error(
        self,
        error: Exception,
        context: Dict[str, Any] = None,
        module: str = "unknown",
        function: str = "unknown"
    ) -> Optional[Any]:
        """
        에러 처리 및 복구 시도

        Args:
            error: 발생한 에러
            context: 에러 컨텍스트
            module: 모듈명
            function: 함수명

        Returns:
            복구 결과 (성공시) 또는 None (실패시)
        """
        try:
            # 에러 정보 생성
            error_info = self._create_error_info(error, context, module, function)

            # 에러 기록
            self.error_history.append(error_info)
            self._update_error_patterns(error_info)

            # 심각도에 따른 알림
            await self._send_alerts(error_info)

            # 복구 시도
            recovery_result = await self._attempt_recovery(error_info)

            return recovery_result

        except Exception as recovery_error:
            self.logger.error(f"❌ 에러 복구 중 추가 에러 발생: {recovery_error}")
            return None

    def _create_error_info(
        self,
        error: Exception,
        context: Dict[str, Any],
        module: str,
        function: str
    ) -> ErrorInfo:
        """에러 정보 생성"""
        error_id = f"{module}_{function}_{type(error).__name__}_{time.time()}"

        # 심각도 결정
        severity = self._determine_severity(error, context)

        # 복구 전략 결정
        recovery_strategy = self._determine_recovery_strategy(error, severity)

        return ErrorInfo(
            error_id=error_id,
            timestamp=datetime.now(),
            error_type=type(error).__name__,
            error_message=str(error),
            severity=severity,
            module=module,
            function=function,
            traceback_str=traceback.format_exc(),
            context=context or {},
            recovery_strategy=recovery_strategy
        )

    def _determine_severity(
        self,
        error: Exception,
        context: Dict[str, Any]
    ) -> ErrorSeverity:
        """에러 심각도 결정"""
        error_type = type(error).__name__

        # 치명적 에러들
        critical_errors = [
            "SystemExit", "KeyboardInterrupt", "MemoryError",
            "RecursionError", "SystemError"
        ]

        if error_type in critical_errors:
            return ErrorSeverity.CRITICAL

        # 높은 심각도
        high_severity_errors = [
            "ConnectionError", "TimeoutError", "PermissionError",
            "FileNotFoundError", "DatabaseError"
        ]

        if error_type in high_severity_errors:
            return ErrorSeverity.HIGH

        # 중간 심각도
        medium_severity_errors = [
            "ValueError", "KeyError", "IndexError", "AttributeError"
        ]

        if error_type in medium_severity_errors:
            return ErrorSeverity.MEDIUM

        # 기본적으로 낮은 심각도
        return ErrorSeverity.LOW

    def _determine_recovery_strategy(
        self,
        error: Exception,
        severity: ErrorSeverity
    ) -> RecoveryStrategy:
        """복구 전략 결정"""
        error_type = type(error).__name__

        # 치명적 에러는 수동 처리
        if severity == ErrorSeverity.CRITICAL:
            return RecoveryStrategy.MANUAL

        # 네트워크/연결 에러는 재시도
        network_errors = ["ConnectionError", "TimeoutError", "URLError"]
        if error_type in network_errors:
            return RecoveryStrategy.RETRY

        # 파일/권한 에러는 대체 방법
        file_errors = ["FileNotFoundError", "PermissionError"]
        if error_type in file_errors:
            return RecoveryStrategy.FALLBACK

        # 일반적인 에러는 재시도
        return RecoveryStrategy.RETRY

    async def _attempt_recovery(self, error_info: ErrorInfo) -> Optional[Any]:
        """복구 시도"""
        try:
            self.recovery_state["active_recoveries"] += 1

            # 재시도 제한 확인
            if error_info.recovery_attempts >= error_info.max_recovery_attempts:
                self.logger.warning(f"⚠️ 최대 복구 시도 횟수 초과: {error_info.error_id}")
                return None

            # 복구 시도 기록 업데이트
            error_info.recovery_attempts += 1
            error_info.last_recovery_attempt = datetime.now()

            # 전략에 따른 복구 실행
            result = None

            if error_info.recovery_strategy == RecoveryStrategy.RETRY:
                result = await self._retry_recovery(error_info)
            elif error_info.recovery_strategy == RecoveryStrategy.FALLBACK:
                result = await self._fallback_recovery(error_info)
            elif error_info.recovery_strategy == RecoveryStrategy.SKIP:
                result = await self._skip_recovery(error_info)
            elif error_info.recovery_strategy == RecoveryStrategy.MANUAL:
                await self._manual_recovery(error_info)

            # 복구 통계 업데이트
            self.recovery_state["total_recoveries"] += 1

            if result is not None:
                self.logger.info(f"✅ 복구 성공: {error_info.error_id}")
            else:
                self.logger.warning(f"⚠️ 복구 실패: {error_info.error_id}")

            return result

        except Exception as e:
            self.logger.error(f"❌ 복구 시도 중 에러: {e}")
            return None

        finally:
            self.recovery_state["active_recoveries"] -= 1

    async def _retry_recovery(self, error_info: ErrorInfo) -> Optional[Any]:
        """재시도 복구"""
        self.logger.info(f"🔄 재시도 복구 실행: {error_info.error_id}")

        # 등록된 복구 액션이 있는지 확인
        pattern_key = f"{error_info.module}_{error_info.error_type}"
        matching_actions = [
            action for action in self.recovery_actions.values()
            if action.error_id == pattern_key and action.strategy == RecoveryStrategy.RETRY
        ]

        if matching_actions:
            action = matching_actions[0]
            try:
                result = await self.retry_manager.execute_with_retry(
                    action.action_func,
                    **action.parameters
                )
                action.success_count += 1
                action.last_executed = datetime.now()
                return result
            except Exception as e:
                action.failure_count += 1
                self.logger.error(f"❌ 복구 액션 실행 실패: {e}")

        return None

    async def _fallback_recovery(self, error_info: ErrorInfo) -> Optional[Any]:
        """대체 방법 복구"""
        self.logger.info(f"🔀 대체 방법 복구 실행: {error_info.error_id}")

        # 대체 방법 실행 로직
        # 실제 구현에서는 각 에러 타입별 대체 방법을 정의
        return None

    async def _skip_recovery(self, error_info: ErrorInfo) -> Optional[Any]:
        """건너뛰기 복구"""
        self.logger.info(f"⏭️ 건너뛰기 복구 실행: {error_info.error_id}")

        # 해당 작업을 건너뛰고 계속 진행
        return "skipped"

    async def _manual_recovery(self, error_info: ErrorInfo):
        """수동 복구 필요"""
        self.logger.critical(f"🚨 수동 복구 필요: {error_info.error_id}")

        # 관리자에게 알림 발송
        await self._send_critical_alert(error_info)

    def _update_error_patterns(self, error_info: ErrorInfo):
        """에러 패턴 업데이트"""
        pattern = f"{error_info.module}_{error_info.error_type}"
        self.error_patterns[pattern] += 1

    async def _send_alerts(self, error_info: ErrorInfo):
        """알림 발송"""
        for callback in self.alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(error_info)
                else:
                    callback(error_info)
            except Exception as e:
                self.logger.error(f"❌ 알림 콜백 실행 실패: {e}")

    async def _send_critical_alert(self, error_info: ErrorInfo):
        """치명적 에러 알림"""
        # 실제 구현에서는 이메일, SMS, 슬랙 등으로 알림
        self.logger.critical(
            f"🚨 CRITICAL ERROR ALERT 🚨\n"
            f"Module: {error_info.module}\n"
            f"Function: {error_info.function}\n"
            f"Error: {error_info.error_message}\n"
            f"Time: {error_info.timestamp}\n"
            f"Manual intervention required!"
        )

    def get_error_statistics(self) -> Dict[str, Any]:
        """에러 통계 조회"""
        if not self.error_history:
            return {"status": "no_data"}

        # 심각도별 분류
        severity_counts = {severity.value: 0 for severity in ErrorSeverity}
        for error in self.error_history:
            severity_counts[error.severity.value] += 1

        # 최근 에러 (최근 24시간)
        recent_cutoff = datetime.now() - timedelta(hours=24)
        recent_errors = [
            error for error in self.error_history
            if error.timestamp >= recent_cutoff
        ]

        # 복구 성공률
        total_recoveries = self.recovery_state["total_recoveries"]
        successful_recoveries = sum(
            action.success_count for action in self.recovery_actions.values()
        )
        recovery_success_rate = (
            successful_recoveries / total_recoveries * 100
            if total_recoveries > 0 else 0
        )

        return {
            "status": "available",
            "total_errors": len(self.error_history),
            "recent_errors_24h": len(recent_errors),
            "severity_distribution": severity_counts,
            "top_error_patterns": dict(
                sorted(self.error_patterns.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
            "recovery_stats": {
                "total_attempts": total_recoveries,
                "success_rate": recovery_success_rate,
                "active_recoveries": self.recovery_state["active_recoveries"]
            },
            "circuit_breakers": {
                name: {
                    "state": breaker.state,
                    "failure_count": breaker.failure_count
                }
                for name, breaker in self.circuit_breakers.items()
            }
        }

def error_handler(
    recovery_system: ErrorRecoverySystem,
    module: str = None,
    reraise: bool = True
):
    """에러 핸들러 데코레이터"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                module_name = module or func.__module__
                await recovery_system.handle_error(
                    error=e,
                    context={"args": str(args), "kwargs": str(kwargs)},
                    module=module_name,
                    function=func.__name__
                )
                if reraise:
                    raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                module_name = module or func.__module__
                # 동기 함수에서는 비동기 처리를 할 수 없으므로 로깅만
                recovery_system.logger.error(
                    f"❌ {module_name}.{func.__name__} 에러: {e}"
                )
                if reraise:
                    raise

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator

# 글로벌 인스턴스
_global_recovery_system = None

def get_global_recovery_system() -> ErrorRecoverySystem:
    """글로벌 에러 복구 시스템 인스턴스"""
    global _global_recovery_system
    if _global_recovery_system is None:
        _global_recovery_system = ErrorRecoverySystem()
    return _global_recovery_system

# 사용 예시
async def main():
    """테스트 함수"""
    # 복구 시스템 초기화
    recovery_system = ErrorRecoverySystem()

    # 알림 콜백 등록
    def alert_callback(error_info: ErrorInfo):
        print(f"🚨 에러 알림: {error_info.error_type} - {error_info.error_message}")

    recovery_system.add_alert_callback(alert_callback)

    # 회로 차단기 등록
    breaker = recovery_system.register_circuit_breaker("test_breaker")

    # 테스트 에러 처리
    try:
        raise ValueError("테스트 에러")
    except Exception as e:
        await recovery_system.handle_error(
            error=e,
            module="test_module",
            function="test_function"
        )

    # 통계 조회
    stats = recovery_system.get_error_statistics()
    print(f"에러 통계: {stats}")

if __name__ == "__main__":
    asyncio.run(main())