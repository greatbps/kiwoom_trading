#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/error_handler.py

고급 에러 핸들링 유틸리티
"""

import sys
import logging
from typing import Callable, Any
from datetime import datetime
from functools import wraps

class ErrorHandler:
    """통합 에러 핸들링 클래스"""
    
    def __init__(self, logger_name: str = "ErrorHandler"):
        self.logger = logging.getLogger(logger_name)
        self.error_count = 0
        self.last_error_time = None
        
    def handle_error(self, error: Exception, context: str = "", critical: bool = False) -> bool:
        """에러 처리 및 로깅"""
        self.error_count += 1
        self.last_error_time = datetime.now()

        # 로깅
        if critical:
            self.logger.critical(f"CRITICAL ERROR in {context}: {error}", exc_info=True)
        else:
            self.logger.error(f"ERROR in {context}: {error}", exc_info=True)
        
        # 특정 에러 타입별 처리
        if isinstance(error, UnicodeEncodeError):
            self._handle_encoding_error(error, context)
        elif isinstance(error, ConnectionError):
            self._handle_connection_error(error, context)
        elif isinstance(error, ImportError):
            self._handle_import_error(error, context)
        elif isinstance(error, KeyboardInterrupt):
            self._handle_keyboard_interrupt(error, context)
            return False  # 프로그램 종료 신호
        elif "api" in str(error).lower() or "404" in str(error) or "quota" in str(error).lower():
            self._handle_api_error(error, context)
        elif "timeout" in str(error).lower():
            self._handle_timeout_error(error, context)
        
        return True  # 계속 실행
    
    def _handle_encoding_error(self, error: UnicodeEncodeError, context: str):
        """인코딩 에러 처리"""
        self.logger.warning(f"UTF-8 encoding issue in {context}. Applying encoding fix.")
        
        # 인코딩 수정 자동 적용
        try:
            from utils.encoding_fix import setup_utf8_environment
            setup_utf8_environment()
        except ImportError:
            pass
    
    def _handle_connection_error(self, error: ConnectionError, context: str):
        """연결 에러 처리"""
        self.logger.warning(f"Connection error in {context}. Switching to fallback mode.")
    
    def _handle_import_error(self, error: ImportError, context: str):
        """임포트 에러 처리"""
        module_name = str(error).split("'")[1] if "'" in str(error) else "unknown"
        self.logger.warning(f"Module {module_name} not available in {context}. Using fallback implementation.")
    
    def _handle_keyboard_interrupt(self, error: KeyboardInterrupt, context: str):
        """키보드 인터럽트 처리"""
        self.logger.info(f"User interruption in {context}. Initiating graceful shutdown.")

    def _handle_api_error(self, error: Exception, context: str):
        """API 에러 처리"""
        error_msg = str(error).lower()

        if "404" in error_msg or "not found" in error_msg:
            self.logger.warning(f"API endpoint not found in {context}. 모델명 또는 API 경로를 확인하세요.")
            self._display_user_message("🔍 API 모델명이나 경로를 확인해주세요. 설정을 점검하겠습니다.")
        elif "quota" in error_msg or "rate limit" in error_msg:
            self.logger.warning(f"API quota exceeded in {context}. 대체 서비스로 전환합니다.")
            self._display_user_message("⏱️ API 사용량 한도에 도달했습니다. 24시간 후 다시 시도하거나 대체 서비스를 사용합니다.")
        elif "auth" in error_msg or "permission" in error_msg:
            self.logger.warning(f"API authentication failed in {context}. API 키를 확인하세요.")
            self._display_user_message("🔐 API 인증에 실패했습니다. API 키 설정을 확인해주세요.")
        else:
            self.logger.warning(f"General API error in {context}. 잠시 후 재시도합니다.")
            self._display_user_message("📡 API 서비스에 일시적인 문제가 발생했습니다. 잠시 후 자동으로 재시도됩니다.")

    def _handle_timeout_error(self, error: Exception, context: str):
        """타임아웃 에러 처리"""
        self.logger.warning(f"Timeout error in {context}. 네트워크 연결을 확인합니다.")
        self._display_user_message("🌐 네트워크 연결 시간이 초과되었습니다. 잠시 후 다시 시도합니다.")

    def _display_user_message(self, message: str):
        """사용자에게 친화적인 메시지 표시"""
        try:
            # Rich Console이 사용 가능한 경우
            from rich.console import Console
            from rich.panel import Panel
            console = Console()
            console.print(Panel(message, border_style="yellow"))
        except ImportError:
            # Rich가 없는 경우 일반 출력
            print(f"💡 {message}")
        except Exception:
            # 출력 실패 시 로그만 기록
            self.logger.info(f"User message: {message}")
    
    def get_error_stats(self) -> dict:
        """에러 통계 반환"""
        return {
            'total_errors': self.error_count,
            'last_error_time': self.last_error_time,
            'has_recent_errors': self.last_error_time and 
                                (datetime.now() - self.last_error_time).seconds < 300
        }

# 전역 에러 핸들러
global_error_handler = ErrorHandler("Global")

def safe_execute(func: Callable, *args, default_return=None, context: str = "", **kwargs) -> Any:
    """안전한 함수 실행"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        global_error_handler.handle_error(e, context or func.__name__)
        return default_return

def error_handler(context: str = "", critical: bool = False, default_return=None):
    """에러 핸들링 데코레이터"""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                handler = ErrorHandler(func.__module__)
                should_continue = handler.handle_error(e, context or func.__name__, critical)
                
                if not should_continue:
                    raise
                
                return default_return
        return wrapper
    return decorator

def async_error_handler(context: str = "", critical: bool = False, default_return=None):
    """비동기 함수용 에러 핸들링 데코레이터"""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                handler = ErrorHandler(func.__module__)
                should_continue = handler.handle_error(e, context or func.__name__, critical)
                
                if not should_continue:
                    raise
                
                return default_return
        return wrapper
    return decorator

class SafeImporter:
    """안전한 모듈 임포트"""
    
    @staticmethod
    def safe_import(module_name: str, class_name: str = None, fallback=None):
        """안전한 모듈/클래스 임포트"""
        try:
            import importlib
            module = importlib.import_module(module_name)
            
            if class_name:
                if hasattr(module, class_name):
                    return getattr(module, class_name)
                else:
                    global_error_handler.logger.warning(
                        f"Class {class_name} not found in {module_name}"
                    )
                    return fallback
            else:
                return module
                
        except ImportError as e:
            global_error_handler.handle_error(e, f"importing {module_name}")
            return fallback
    
    @staticmethod
    def try_multiple_imports(import_attempts: list, fallback=None):
        """여러 임포트 시도"""
        for module_name, class_name in import_attempts:
            result = SafeImporter.safe_import(module_name, class_name)
            if result is not None:
                return result
        
        global_error_handler.logger.warning(
            f"All import attempts failed: {import_attempts}"
        )
        return fallback

def setup_global_exception_handler():
    """전역 예외 핸들러 설정"""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            # 키보드 인터럽트는 기본 처리
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        global_error_handler.handle_error(
            exc_value, 
            "global_exception", 
            critical=True
        )
    
    sys.excepthook = handle_exception

if __name__ == "__main__":
    # 테스트
    setup_global_exception_handler()
    
    @error_handler("test_function", default_return="error_occurred")
    def test_function():
        raise ValueError("Test error")
    
    result = test_function()
    print(f"Result: {result}")
    
    # 안전한 임포트 테스트
    pykis = SafeImporter.safe_import("pykis", "Api")
    print(f"PyKis import result: {pykis}")
    
    # 에러 통계
    stats = global_error_handler.get_error_stats()
    print(f"Error stats: {stats}")