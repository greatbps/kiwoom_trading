"""
에러 핸들러 데코레이터

API 호출, 거래, 데이터베이스 작업 등에 대한 일관된 에러 처리
"""
import asyncio
import logging
import time
from functools import wraps
from typing import Callable, Any, Optional, Type, Tuple
from rich.console import Console

from exceptions.trading_exceptions import (
    TradingException,
    APIException,
    ConnectionError,
    TimeoutError,
    AuthenticationError,
    OrderFailedError,
    InsufficientFundsError,
    DatabaseError
)

console = Console()
logger = logging.getLogger(__name__)


def handle_api_errors(
    default_return: Any = None,
    log_errors: bool = True,
    raise_on_auth_error: bool = True
):
    """
    API 호출 에러 처리 데코레이터

    Args:
        default_return: 에러 발생 시 반환할 기본값
        log_errors: 에러 로깅 여부
        raise_on_auth_error: 인증 에러 시 예외 발생 여부

    Example:
        >>> @handle_api_errors(default_return=None)
        >>> async def get_stock_price(stock_code):
        >>>     response = await api.get(f"/price/{stock_code}")
        >>>     return response.json()
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)

            except AuthenticationError as e:
                if log_errors:
                    logger.error(f"🔐 Authentication error in {func.__name__}: {e}")
                    console.print(f"[red]❌ 인증 실패: {e.message}[/red]")

                if raise_on_auth_error:
                    raise
                return default_return

            except TimeoutError as e:
                if log_errors:
                    logger.warning(f"⏱️  Timeout in {func.__name__}: {e}")
                    console.print(f"[yellow]⚠️  타임아웃: {e.message}[/yellow]")
                return default_return

            except ConnectionError as e:
                if log_errors:
                    logger.error(f"🔌 Connection error in {func.__name__}: {e}")
                    console.print(f"[red]❌ 연결 실패: {e.message}[/red]")
                return default_return

            except APIException as e:
                if log_errors:
                    logger.error(f"🌐 API error in {func.__name__}: {e}")
                    console.print(f"[red]❌ API 오류: {e.message}[/red]")

                    if e.status_code:
                        console.print(f"[dim]   Status Code: {e.status_code}[/dim]")
                    if e.response_data:
                        console.print(f"[dim]   Response: {e.response_data}[/dim]")

                return default_return

            except Exception as e:
                if log_errors:
                    logger.error(f"💥 Unexpected error in {func.__name__}: {e}", exc_info=True)
                    console.print(f"[red]❌ 예상치 못한 오류: {e}[/red]")
                return default_return

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)

            except AuthenticationError as e:
                if log_errors:
                    logger.error(f"🔐 Authentication error in {func.__name__}: {e}")

                if raise_on_auth_error:
                    raise
                return default_return

            except Exception as e:
                if log_errors:
                    logger.error(f"💥 Error in {func.__name__}: {e}", exc_info=True)
                return default_return

        # 비동기 함수인지 확인
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def handle_trading_errors(
    notify_user: bool = True,
    log_errors: bool = True
):
    """
    거래 에러 처리 데코레이터

    Args:
        notify_user: 사용자 알림 여부 (Telegram 등)
        log_errors: 에러 로깅 여부

    Example:
        >>> @handle_trading_errors()
        >>> async def execute_buy_order(stock_code, quantity):
        >>>     return await api.order_buy(stock_code, quantity)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)

            except InsufficientFundsError as e:
                if log_errors:
                    logger.warning(f"💰 Insufficient funds in {func.__name__}: {e}")
                    console.print(f"[yellow]⚠️  잔고 부족: {e.message}[/yellow]")
                    console.print(f"[dim]   필요: {e.required_amount:,.0f}, "
                                  f"가능: {e.available_amount:,.0f}[/dim]")

                if notify_user:
                    # TODO: Telegram 알림
                    pass

                raise

            except OrderFailedError as e:
                if log_errors:
                    logger.error(f"📉 Order failed in {func.__name__}: {e}")
                    console.print(f"[red]❌ 주문 실패: {e.message}[/red]")

                    if e.order_id:
                        console.print(f"[dim]   Order ID: {e.order_id}[/dim]")
                    if e.stock_code:
                        console.print(f"[dim]   Stock: {e.stock_code}[/dim]")

                if notify_user:
                    # TODO: Telegram 알림
                    pass

                raise

            except TradingException as e:
                if log_errors:
                    logger.error(f"📊 Trading error in {func.__name__}: {e}")
                    console.print(f"[red]❌ 거래 오류: {e.message}[/red]")

                raise

            except Exception as e:
                if log_errors:
                    logger.error(f"💥 Unexpected error in {func.__name__}: {e}", exc_info=True)
                    console.print(f"[red]❌ 예상치 못한 오류: {e}[/red]")

                # 예상치 못한 오류를 TradingException으로 감싸기
                raise TradingException(f"Unexpected error in {func.__name__}: {e}")

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # 동기 버전 (필요 시)
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_errors:
                    logger.error(f"💥 Error in {func.__name__}: {e}", exc_info=True)
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def handle_database_errors(
    operation: Optional[str] = None,
    table: Optional[str] = None,
    log_errors: bool = True
):
    """
    데이터베이스 에러 처리 데코레이터

    Args:
        operation: 작업 유형 ('insert', 'update', 'delete', 'select')
        table: 테이블 이름
        log_errors: 에러 로깅 여부

    Example:
        >>> @handle_database_errors(operation='insert', table='trades')
        >>> def save_trade(trade_data):
        >>>     cursor.execute("INSERT INTO trades ...", trade_data)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)

            except DatabaseError as e:
                if log_errors:
                    logger.error(f"🗄️  Database error in {func.__name__}: {e}")
                    console.print(f"[red]❌ DB 오류: {e.message}[/red]")
                raise

            except Exception as e:
                if log_errors:
                    logger.error(f"🗄️  Database error in {func.__name__}: {e}", exc_info=True)

                # 데이터베이스 오류로 변환
                raise DatabaseError(
                    message=f"Database operation failed: {e}",
                    operation=operation,
                    table=table
                )

        return wrapper

    return decorator


def retry_on_error(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    log_retries: bool = True
):
    """
    에러 발생 시 재시도 데코레이터

    Args:
        max_retries: 최대 재시도 횟수
        delay: 초기 대기 시간 (초)
        backoff: 대기 시간 증가 배수
        exceptions: 재시도할 예외 타입 튜플
        log_retries: 재시도 로깅 여부

    Example:
        >>> @retry_on_error(max_retries=3, delay=1.0, exceptions=(ConnectionError, TimeoutError))
        >>> async def fetch_data():
        >>>     return await api.get("/data")
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            current_delay = delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)

                except exceptions as e:
                    if attempt == max_retries:
                        if log_retries:
                            logger.error(
                                f"🔄 Max retries ({max_retries}) reached for {func.__name__}: {e}"
                            )
                        raise

                    if log_retries:
                        logger.warning(
                            f"🔄 Retry {attempt + 1}/{max_retries} for {func.__name__} "
                            f"after {current_delay}s: {e}"
                        )
                        console.print(
                            f"[yellow]🔄 재시도 {attempt + 1}/{max_retries} "
                            f"({current_delay}초 후)[/yellow]"
                        )

                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            current_delay = delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    if attempt == max_retries:
                        if log_retries:
                            logger.error(
                                f"🔄 Max retries ({max_retries}) reached for {func.__name__}: {e}"
                            )
                        raise

                    if log_retries:
                        logger.warning(
                            f"🔄 Retry {attempt + 1}/{max_retries} for {func.__name__}: {e}"
                        )

                    time.sleep(current_delay)
                    current_delay *= backoff

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# 편의 함수: 여러 데코레이터 조합
def handle_all_errors(
    default_return: Any = None,
    max_retries: int = 0,
    notify_user: bool = False
):
    """
    모든 에러 처리 및 재시도 결합 데코레이터

    Args:
        default_return: 에러 시 반환값
        max_retries: 최대 재시도 횟수 (0=재시도 안 함)
        notify_user: 사용자 알림 여부

    Example:
        >>> @handle_all_errors(max_retries=3)
        >>> async def critical_operation():
        >>>     return await api.do_something()
    """
    def decorator(func: Callable) -> Callable:
        # 재시도 데코레이터 적용 (있으면)
        if max_retries > 0:
            func = retry_on_error(
                max_retries=max_retries,
                exceptions=(ConnectionError, TimeoutError)
            )(func)

        # API 에러 핸들러 적용
        func = handle_api_errors(default_return=default_return)(func)

        # 거래 에러 핸들러 적용
        func = handle_trading_errors(notify_user=notify_user)(func)

        return func

    return decorator
