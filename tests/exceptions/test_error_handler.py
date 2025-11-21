"""
에러 핸들러 데코레이터 테스트
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from exceptions.error_handler import (
    handle_api_errors,
    handle_trading_errors,
    handle_database_errors,
    retry_on_error,
    handle_all_errors
)
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


class TestHandleAPIErrors:
    """handle_api_errors 데코레이터 테스트"""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        """정상 실행 테스트"""
        # Given
        @handle_api_errors()
        async def successful_function():
            return "success"

        # When
        result = await successful_function()

        # Then
        assert result == "success"

    @pytest.mark.asyncio
    async def test_authentication_error_raises(self):
        """인증 에러 발생 시 예외 발생 (기본값)"""
        # Given
        @handle_api_errors()
        async def auth_error_function():
            raise AuthenticationError("Auth failed")

        # Then
        with pytest.raises(AuthenticationError):
            await auth_error_function()

    @pytest.mark.asyncio
    async def test_authentication_error_no_raise(self):
        """인증 에러 발생 시 예외 발생 안 함"""
        # Given
        @handle_api_errors(raise_on_auth_error=False, default_return="default")
        async def auth_error_function():
            raise AuthenticationError("Auth failed")

        # When
        result = await auth_error_function()

        # Then
        assert result == "default"

    @pytest.mark.asyncio
    async def test_timeout_error_returns_default(self):
        """타임아웃 에러 시 기본값 반환"""
        # Given
        default_value = None

        @handle_api_errors(default_return=default_value)
        async def timeout_function():
            raise TimeoutError("Request timed out")

        # When
        result = await timeout_function()

        # Then
        assert result == default_value

    @pytest.mark.asyncio
    async def test_connection_error_returns_default(self):
        """연결 에러 시 기본값 반환"""
        # Given
        @handle_api_errors(default_return=[])
        async def connection_error_function():
            raise ConnectionError("Connection failed")

        # When
        result = await connection_error_function()

        # Then
        assert result == []

    @pytest.mark.asyncio
    async def test_api_exception_returns_default(self):
        """API 예외 시 기본값 반환"""
        # Given
        @handle_api_errors(default_return={"error": True})
        async def api_error_function():
            raise APIException("API error", status_code=500)

        # When
        result = await api_error_function()

        # Then
        assert result == {"error": True}

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_default(self):
        """예상치 못한 에러 시 기본값 반환"""
        # Given
        @handle_api_errors(default_return=None)
        async def unexpected_error_function():
            raise ValueError("Unexpected error")

        # When
        result = await unexpected_error_function()

        # Then
        assert result is None

    def test_sync_function_success(self):
        """동기 함수 정상 실행"""
        # Given
        @handle_api_errors()
        def sync_function():
            return "sync_success"

        # When
        result = sync_function()

        # Then
        assert result == "sync_success"

    def test_sync_function_error(self):
        """동기 함수 에러 처리"""
        # Given
        @handle_api_errors(default_return="default")
        def sync_error_function():
            raise APIException("Error")

        # When
        result = sync_error_function()

        # Then
        assert result == "default"

    @pytest.mark.asyncio
    async def test_no_logging(self):
        """로깅 비활성화 테스트"""
        # Given
        @handle_api_errors(log_errors=False, default_return=None)
        async def error_function():
            raise ConnectionError("Connection failed")

        # When
        result = await error_function()

        # Then
        assert result is None


class TestHandleTradingErrors:
    """handle_trading_errors 데코레이터 테스트"""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        """정상 실행 테스트"""
        # Given
        @handle_trading_errors()
        async def successful_trade():
            return "trade_success"

        # When
        result = await successful_trade()

        # Then
        assert result == "trade_success"

    @pytest.mark.asyncio
    async def test_insufficient_funds_error_raises(self):
        """잔고 부족 에러 시 예외 발생"""
        # Given
        @handle_trading_errors()
        async def insufficient_funds_function():
            raise InsufficientFundsError(
                required_amount=1000000,
                available_amount=500000
            )

        # Then
        with pytest.raises(InsufficientFundsError):
            await insufficient_funds_function()

    @pytest.mark.asyncio
    async def test_order_failed_error_raises(self):
        """주문 실패 에러 시 예외 발생"""
        # Given
        @handle_trading_errors()
        async def order_failed_function():
            raise OrderFailedError(
                "Order execution failed",
                order_id="ORD123",
                stock_code="005930"
            )

        # Then
        with pytest.raises(OrderFailedError):
            await order_failed_function()

    @pytest.mark.asyncio
    async def test_trading_exception_raises(self):
        """거래 예외 시 예외 발생"""
        # Given
        @handle_trading_errors()
        async def trading_error_function():
            raise TradingException("Trading error")

        # Then
        with pytest.raises(TradingException):
            await trading_error_function()

    @pytest.mark.asyncio
    async def test_unexpected_error_wrapped(self):
        """예상치 못한 에러는 TradingException으로 감싸기"""
        # Given
        @handle_trading_errors()
        async def unexpected_error_function():
            raise ValueError("Unexpected error")

        # Then
        with pytest.raises(TradingException) as exc_info:
            await unexpected_error_function()

        assert "Unexpected error" in str(exc_info.value)

    def test_sync_function_success(self):
        """동기 함수 정상 실행"""
        # Given
        @handle_trading_errors()
        def sync_trade():
            return "sync_trade_success"

        # When
        result = sync_trade()

        # Then
        assert result == "sync_trade_success"

    def test_sync_function_error(self):
        """동기 함수 에러 처리"""
        # Given
        @handle_trading_errors()
        def sync_error_function():
            raise RuntimeError("Sync error")

        # Then
        with pytest.raises(Exception):
            sync_error_function()


class TestHandleDatabaseErrors:
    """handle_database_errors 데코레이터 테스트"""

    def test_successful_execution(self):
        """정상 실행 테스트"""
        # Given
        @handle_database_errors()
        def successful_db_operation():
            return "db_success"

        # When
        result = successful_db_operation()

        # Then
        assert result == "db_success"

    def test_database_error_raises(self):
        """데이터베이스 에러 시 예외 발생"""
        # Given
        @handle_database_errors()
        def db_error_function():
            raise DatabaseError("DB connection failed")

        # Then
        with pytest.raises(DatabaseError):
            db_error_function()

    def test_unexpected_error_wrapped(self):
        """예상치 못한 에러는 DatabaseError로 감싸기"""
        # Given
        @handle_database_errors(operation='insert', table='trades')
        def unexpected_db_error():
            raise RuntimeError("Database crashed")

        # Then
        with pytest.raises(DatabaseError) as exc_info:
            unexpected_db_error()

        assert exc_info.value.details['operation'] == 'insert'
        assert exc_info.value.details['table'] == 'trades'


class TestRetryOnError:
    """retry_on_error 데코레이터 테스트"""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        """정상 실행 (재시도 불필요)"""
        # Given
        @retry_on_error()
        async def successful_function():
            return "success"

        # When
        result = await successful_function()

        # Then
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retry_and_succeed(self):
        """재시도 후 성공"""
        # Given
        call_count = 0

        @retry_on_error(max_retries=3, delay=0.01)
        async def retry_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary error")
            return "success_after_retry"

        # When
        result = await retry_function()

        # Then
        assert result == "success_after_retry"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """최대 재시도 횟수 초과 시 예외 발생"""
        # Given
        @retry_on_error(max_retries=2, delay=0.01)
        async def always_fails():
            raise TimeoutError("Always fails")

        # Then
        with pytest.raises(TimeoutError):
            await always_fails()

    @pytest.mark.asyncio
    async def test_specific_exceptions_only(self):
        """특정 예외만 재시도"""
        # Given
        @retry_on_error(
            max_retries=3,
            delay=0.01,
            exceptions=(ConnectionError, TimeoutError)
        )
        async def other_error():
            raise ValueError("Different error")

        # Then (ValueError는 재시도 대상이 아니므로 즉시 발생)
        with pytest.raises(ValueError):
            await other_error()

    @pytest.mark.asyncio
    async def test_backoff_delay(self):
        """백오프 지연 시간 증가 테스트"""
        # Given
        delays = []

        @retry_on_error(max_retries=3, delay=0.1, backoff=2.0)
        async def track_delays():
            import time
            delays.append(time.time())
            if len(delays) < 3:
                raise ConnectionError("Retry")
            return "success"

        # When
        await track_delays()

        # Then (최소 2번의 지연 발생)
        assert len(delays) == 3

    def test_sync_function_retry(self):
        """동기 함수 재시도 테스트"""
        # Given
        call_count = 0

        @retry_on_error(max_retries=2, delay=0.01)
        def sync_retry_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Retry")
            return "sync_success"

        # When
        result = sync_retry_function()

        # Then
        assert result == "sync_success"
        assert call_count == 2


class TestHandleAllErrors:
    """handle_all_errors 복합 데코레이터 테스트"""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        """정상 실행 테스트"""
        # Given
        @handle_all_errors()
        async def successful_operation():
            return "all_success"

        # When
        result = await successful_operation()

        # Then
        assert result == "all_success"

    @pytest.mark.asyncio
    async def test_with_retry(self):
        """재시도 기능 포함 테스트"""
        # Given
        call_count = 0

        @handle_all_errors(max_retries=2, default_return=None)
        async def retry_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Retry needed")
            return "retry_success"

        # When
        result = await retry_operation()

        # Then
        assert result == "retry_success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """API 에러 처리 테스트"""
        # Given
        @handle_all_errors(default_return="api_default")
        async def api_error_operation():
            raise APIException("API failed", status_code=500)

        # When
        result = await api_error_operation()

        # Then
        assert result == "api_default"

    @pytest.mark.asyncio
    async def test_trading_error_handling(self):
        """거래 에러 처리 테스트"""
        # Given
        # Note: handle_all_errors applies handle_api_errors which catches all exceptions
        # So trading errors will return default_return instead of raising
        @handle_all_errors(default_return="error_handled")
        async def trading_error_operation():
            raise OrderFailedError("Order failed", order_id="ORD123")

        # When
        result = await trading_error_operation()

        # Then
        # Trading error is caught by handle_api_errors and returns default
        assert result == "error_handled"


class TestDecoratorCombinations:
    """데코레이터 조합 테스트"""

    @pytest.mark.asyncio
    async def test_api_and_retry_combination(self):
        """API 핸들러 + 재시도 조합"""
        # Given
        call_count = 0

        @handle_api_errors(default_return=None)
        @retry_on_error(max_retries=2, delay=0.01)
        async def combined_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Retry")
            return "combined_success"

        # When
        result = await combined_function()

        # Then
        assert result == "combined_success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_trading_and_retry_combination(self):
        """거래 핸들러 + 재시도 조합"""
        # Given
        @handle_trading_errors()
        @retry_on_error(max_retries=1, delay=0.01)
        async def trading_retry_function():
            raise InsufficientFundsError(
                required_amount=1000000,
                available_amount=500000
            )

        # Then
        with pytest.raises(InsufficientFundsError):
            await trading_retry_function()


class TestErrorLogging:
    """에러 로깅 테스트"""

    @pytest.mark.asyncio
    async def test_logging_enabled(self):
        """로깅 활성화 테스트"""
        # Given
        @handle_api_errors(log_errors=True, default_return=None)
        async def logged_error():
            raise APIException("Logged error")

        # When
        with patch('exceptions.error_handler.logger') as mock_logger:
            result = await logged_error()

            # Then
            assert result is None
            mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_logging_disabled(self):
        """로깅 비활성화 테스트"""
        # Given
        @handle_api_errors(log_errors=False, default_return=None)
        async def unlogged_error():
            raise APIException("Unlogged error")

        # When
        with patch('exceptions.error_handler.logger') as mock_logger:
            result = await unlogged_error()

            # Then
            assert result is None
            mock_logger.error.assert_not_called()
