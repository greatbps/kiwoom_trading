#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Error Recovery and Retry Mechanisms for Trading System
Provides robust error handling, retry logic, and system recovery
"""

import asyncio
import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, List, Dict
from functools import wraps
from pathlib import Path
import json

logger = logging.getLogger(__name__)


class RetryConfig:
    """Configuration for retry mechanisms"""
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_backoff: bool = True,
        jitter: bool = True
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_backoff = exponential_backoff
        self.jitter = jitter


class ErrorRecoveryManager:
    """Manages error recovery and system resilience"""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.error_log_file = self.log_dir / "error_recovery.log"
        self.recovery_stats = {
            "total_errors": 0,
            "recovered_errors": 0,
            "failed_recoveries": 0,
            "last_error": None,
            "recovery_rate": 0.0
        }
        self._setup_logging()
    
    def _setup_logging(self):
        """Setup dedicated error recovery logging"""
        recovery_logger = logging.getLogger("error_recovery")
        recovery_logger.setLevel(logging.INFO)
        
        # File handler for error recovery logs
        handler = logging.FileHandler(self.error_log_file, encoding='utf-8')
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        recovery_logger.addHandler(handler)
    
    def log_error(self, error: Exception, context: str = "", attempt: int = 1):
        """Log error with context"""
        error_info = {
            "timestamp": datetime.now().isoformat(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context,
            "attempt": attempt,
            "traceback": traceback.format_exc()
        }
        
        self.recovery_stats["total_errors"] += 1
        self.recovery_stats["last_error"] = error_info
        
        logger.error(f"Error in {context}: {error} (attempt {attempt})")
        
        # Log to error recovery file
        recovery_logger = logging.getLogger("error_recovery")
        recovery_logger.error(json.dumps(error_info, ensure_ascii=False, indent=2))
    
    def log_recovery(self, context: str, attempts_used: int):
        """Log successful recovery"""
        self.recovery_stats["recovered_errors"] += 1
        self.recovery_stats["recovery_rate"] = (
            self.recovery_stats["recovered_errors"] / 
            max(1, self.recovery_stats["total_errors"])
        )
        
        logger.info(f"Successfully recovered from error in {context} after {attempts_used} attempts")
    
    def log_failed_recovery(self, context: str, final_error: Exception):
        """Log failed recovery attempt"""
        self.recovery_stats["failed_recoveries"] += 1
        logger.error(f"Failed to recover from error in {context}: {final_error}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get recovery statistics"""
        return self.recovery_stats.copy()


# Global error recovery manager instance
error_recovery = ErrorRecoveryManager()


def async_retry(
    retry_config: Optional[RetryConfig] = None,
    exceptions: tuple = (Exception,),
    context: str = ""
):
    """Decorator for async functions with retry logic"""
    if retry_config is None:
        retry_config = RetryConfig()
    
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(1, retry_config.max_attempts + 1):
                try:
                    result = await func(*args, **kwargs)
                    
                    # Log successful recovery if this wasn't the first attempt
                    if attempt > 1:
                        error_recovery.log_recovery(
                            context or func.__name__, 
                            attempt - 1
                        )
                    
                    return result
                
                except exceptions as e:
                    last_exception = e
                    error_recovery.log_error(e, context or func.__name__, attempt)
                    
                    # Don't wait after the last attempt
                    if attempt == retry_config.max_attempts:
                        break
                    
                    # Calculate delay
                    delay = retry_config.base_delay
                    if retry_config.exponential_backoff:
                        delay = min(
                            retry_config.base_delay * (2 ** (attempt - 1)),
                            retry_config.max_delay
                        )
                    
                    # Add jitter if enabled
                    if retry_config.jitter:
                        import random
                        delay *= (0.5 + random.random())
                    
                    logger.warning(
                        f"Attempt {attempt} failed for {func.__name__}, "
                        f"retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
            
            # All attempts failed
            error_recovery.log_failed_recovery(
                context or func.__name__, 
                last_exception
            )
            raise last_exception
        
        return wrapper
    return decorator


def sync_retry(
    retry_config: Optional[RetryConfig] = None,
    exceptions: tuple = (Exception,),
    context: str = ""
):
    """Decorator for sync functions with retry logic"""
    if retry_config is None:
        retry_config = RetryConfig()
    
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(1, retry_config.max_attempts + 1):
                try:
                    result = func(*args, **kwargs)
                    
                    # Log successful recovery if this wasn't the first attempt
                    if attempt > 1:
                        error_recovery.log_recovery(
                            context or func.__name__, 
                            attempt - 1
                        )
                    
                    return result
                
                except exceptions as e:
                    last_exception = e
                    error_recovery.log_error(e, context or func.__name__, attempt)
                    
                    # Don't wait after the last attempt
                    if attempt == retry_config.max_attempts:
                        break
                    
                    # Calculate delay
                    delay = retry_config.base_delay
                    if retry_config.exponential_backoff:
                        delay = min(
                            retry_config.base_delay * (2 ** (attempt - 1)),
                            retry_config.max_delay
                        )
                    
                    # Add jitter if enabled
                    if retry_config.jitter:
                        import random
                        delay *= (0.5 + random.random())
                    
                    logger.warning(
                        f"Attempt {attempt} failed for {func.__name__}, "
                        f"retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            
            # All attempts failed
            error_recovery.log_failed_recovery(
                context or func.__name__, 
                last_exception
            )
            raise last_exception
        
        return wrapper
    return decorator


class CircuitBreaker:
    """Circuit breaker pattern implementation"""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: int = 60,
        expected_exception: tuple = (Exception,)
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    def is_circuit_open(self) -> bool:
        """Check if circuit is currently open"""
        if self.state == "OPEN":
            if self.last_failure_time and (
                time.time() - self.last_failure_time > self.timeout
            ):
                self.state = "HALF_OPEN"
                logger.info("Circuit breaker moved to HALF_OPEN state")
                return False
            return True
        return False
    
    def record_success(self):
        """Record successful operation"""
        self.failure_count = 0
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
            logger.info("Circuit breaker reset to CLOSED state")
    
    def record_failure(self):
        """Record failed operation"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            if self.state != "OPEN":
                self.state = "OPEN"
                logger.warning(
                    f"Circuit breaker OPENED after {self.failure_count} failures"
                )
    
    def __call__(self, func):
        """Decorator to apply circuit breaker"""
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if self.is_circuit_open():
                raise Exception(f"Circuit breaker is OPEN for {func.__name__}")
            
            try:
                result = await func(*args, **kwargs)
                self.record_success()
                return result
            except self.expected_exception as e:
                self.record_failure()
                raise e
        
        return wrapper


class HealthChecker:
    """System health monitoring and recovery"""
    
    def __init__(self):
        self.health_checks = []
        self.last_check_time = None
        self.health_status = {}
    
    def add_health_check(self, name: str, check_func: Callable):
        """Add a health check function"""
        self.health_checks.append((name, check_func))
    
    async def run_health_checks(self) -> Dict[str, Any]:
        """Run all health checks"""
        results = {}
        overall_healthy = True
        
        for name, check_func in self.health_checks:
            try:
                if asyncio.iscoroutinefunction(check_func):
                    is_healthy = await check_func()
                else:
                    is_healthy = check_func()
                
                results[name] = {
                    "healthy": is_healthy,
                    "timestamp": datetime.now().isoformat(),
                    "error": None
                }
                
                if not is_healthy:
                    overall_healthy = False
            
            except Exception as e:
                results[name] = {
                    "healthy": False,
                    "timestamp": datetime.now().isoformat(),
                    "error": str(e)
                }
                overall_healthy = False
                logger.error(f"Health check {name} failed: {e}")
        
        self.health_status = {
            "overall_healthy": overall_healthy,
            "checks": results,
            "last_check": datetime.now().isoformat()
        }
        
        self.last_check_time = time.time()
        return self.health_status
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get current health status"""
        return self.health_status


# Default health checker instance
health_checker = HealthChecker()


async def safe_execute(
    func: Callable,
    *args,
    fallback_value: Any = None,
    context: str = "",
    **kwargs
) -> Any:
    """Safely execute a function with error handling"""
    try:
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            return func(*args, **kwargs)
    except Exception as e:
        error_recovery.log_error(e, context or func.__name__)
        logger.error(f"Safe execution failed in {context}: {e}")
        return fallback_value


def get_recovery_stats() -> Dict[str, Any]:
    """Get overall recovery statistics"""
    return error_recovery.get_stats()