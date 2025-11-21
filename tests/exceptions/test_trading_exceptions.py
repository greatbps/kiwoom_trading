"""
거래 예외 클래스 테스트
"""
import pytest
from exceptions.trading_exceptions import (
    TradingException,
    APIException,
    ConnectionError,
    TimeoutError,
    AuthenticationError,
    OrderFailedError,
    InsufficientFundsError,
    DataValidationError,
    InvalidStockCodeError,
    ConfigurationError,
    DatabaseError
)


class TestTradingException:
    """TradingException 기본 클래스 테스트"""

    def test_create_with_message_only(self):
        """메시지만으로 예외 생성"""
        # Given
        message = "Something went wrong"

        # When
        exc = TradingException(message)

        # Then
        assert exc.message == message
        assert exc.details == {}
        assert str(exc) == message

    def test_create_with_details(self):
        """메시지와 상세 정보로 예외 생성"""
        # Given
        message = "Error occurred"
        details = {"code": 123, "reason": "test"}

        # When
        exc = TradingException(message, details)

        # Then
        assert exc.message == message
        assert exc.details == details
        assert "details:" in str(exc)

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        # Given
        message = "Test error"
        details = {"key": "value"}
        exc = TradingException(message, details)

        # When
        result = exc.to_dict()

        # Then
        assert result['type'] == 'TradingException'
        assert result['message'] == message
        assert result['details'] == details


class TestAPIException:
    """APIException 테스트"""

    def test_create_with_status_code(self):
        """상태 코드와 함께 예외 생성"""
        # Given
        message = "API error"
        status_code = 500

        # When
        exc = APIException(message, status_code=status_code)

        # Then
        assert exc.message == message
        assert exc.status_code == status_code
        assert exc.details['status_code'] == status_code

    def test_create_with_response_data(self):
        """응답 데이터와 함께 예외 생성"""
        # Given
        message = "API error"
        response_data = {"error": "Internal server error"}

        # When
        exc = APIException(message, response_data=response_data)

        # Then
        assert exc.response_data == response_data
        assert exc.details['response_data'] == response_data

    def test_create_with_all_parameters(self):
        """모든 파라미터로 예외 생성"""
        # Given
        message = "API error"
        status_code = 400
        response_data = {"error": "Bad request"}
        details = {"custom": "value"}

        # When
        exc = APIException(
            message,
            status_code=status_code,
            response_data=response_data,
            details=details
        )

        # Then
        assert exc.status_code == status_code
        assert exc.response_data == response_data
        assert exc.details['status_code'] == status_code
        assert exc.details['response_data'] == response_data
        assert exc.details['custom'] == "value"


class TestConnectionError:
    """ConnectionError 테스트"""

    def test_default_message(self):
        """기본 메시지 테스트"""
        # When
        exc = ConnectionError()

        # Then
        assert "Failed to connect" in exc.message

    def test_custom_message(self):
        """커스텀 메시지 테스트"""
        # Given
        message = "Connection refused"

        # When
        exc = ConnectionError(message)

        # Then
        assert exc.message == message


class TestTimeoutError:
    """TimeoutError 테스트"""

    def test_default_message(self):
        """기본 메시지 테스트"""
        # When
        exc = TimeoutError()

        # Then
        assert "timed out" in exc.message

    def test_with_timeout_seconds(self):
        """타임아웃 시간 포함 테스트"""
        # Given
        timeout = 30

        # When
        exc = TimeoutError(timeout_seconds=timeout)

        # Then
        assert exc.details['timeout_seconds'] == timeout


class TestAuthenticationError:
    """AuthenticationError 테스트"""

    def test_default_message(self):
        """기본 메시지 테스트"""
        # When
        exc = AuthenticationError()

        # Then
        assert "Authentication failed" in exc.message

    def test_status_code_is_401(self):
        """상태 코드가 401인지 테스트"""
        # When
        exc = AuthenticationError()

        # Then
        assert exc.status_code == 401


class TestOrderFailedError:
    """OrderFailedError 테스트"""

    def test_create_with_order_info(self):
        """주문 정보와 함께 예외 생성"""
        # Given
        message = "Order execution failed"
        order_id = "ORD123"
        stock_code = "005930"
        order_type = "buy"

        # When
        exc = OrderFailedError(
            message,
            order_id=order_id,
            stock_code=stock_code,
            order_type=order_type
        )

        # Then
        assert exc.message == message
        assert exc.order_id == order_id
        assert exc.stock_code == stock_code
        assert exc.order_type == order_type
        assert exc.details['order_id'] == order_id
        assert exc.details['stock_code'] == stock_code
        assert exc.details['order_type'] == order_type


class TestInsufficientFundsError:
    """InsufficientFundsError 테스트"""

    def test_create_with_amounts(self):
        """필요/가능 금액으로 예외 생성"""
        # Given
        required = 1000000.0
        available = 500000.0

        # When
        exc = InsufficientFundsError(
            required_amount=required,
            available_amount=available
        )

        # Then
        assert exc.required_amount == required
        assert exc.available_amount == available
        assert "Insufficient funds" in exc.message
        # Message uses comma-formatted numbers (1,000,000)
        assert "1,000,000" in exc.message
        assert "500,000" in exc.message

    def test_shortage_calculation(self):
        """부족 금액 계산 테스트"""
        # Given
        required = 1000000.0
        available = 600000.0

        # When
        exc = InsufficientFundsError(
            required_amount=required,
            available_amount=available
        )

        # Then
        expected_shortage = required - available
        assert exc.details['shortage'] == expected_shortage

    def test_with_stock_code(self):
        """종목 코드 포함 테스트"""
        # Given
        stock_code = "005930"

        # When
        exc = InsufficientFundsError(
            required_amount=1000000.0,
            available_amount=500000.0,
            stock_code=stock_code
        )

        # Then
        assert exc.stock_code == stock_code


class TestDataValidationError:
    """DataValidationError 테스트"""

    def test_create_with_field_info(self):
        """필드 정보로 예외 생성"""
        # Given
        message = "Validation failed"
        field = "price"
        expected = "positive number"
        actual = -100

        # When
        exc = DataValidationError(
            message,
            field=field,
            expected=expected,
            actual=actual
        )

        # Then
        assert exc.message == message
        assert exc.details['field'] == field
        assert exc.details['expected'] == expected
        assert exc.details['actual'] == actual

    def test_without_expected_actual(self):
        """expected/actual 없이 생성"""
        # Given
        message = "Invalid data"
        field = "quantity"

        # When
        exc = DataValidationError(message, field=field)

        # Then
        assert exc.message == message
        assert exc.details['field'] == field
        assert 'expected' not in exc.details
        assert 'actual' not in exc.details


class TestInvalidStockCodeError:
    """InvalidStockCodeError 테스트"""

    def test_create_with_stock_code(self):
        """종목 코드로 예외 생성"""
        # Given
        stock_code = "INVALID"

        # When
        exc = InvalidStockCodeError(stock_code)

        # Then
        assert stock_code in exc.message
        assert exc.details['stock_code'] == stock_code

    def test_with_reason(self):
        """실패 사유 포함 테스트"""
        # Given
        stock_code = "000000"
        reason = "Stock not found"

        # When
        exc = InvalidStockCodeError(stock_code, reason=reason)

        # Then
        assert stock_code in exc.message
        assert reason in exc.message
        assert exc.details['reason'] == reason


class TestConfigurationError:
    """ConfigurationError 테스트"""

    def test_create_with_config_key(self):
        """설정 키로 예외 생성"""
        # Given
        message = "Invalid configuration"
        config_key = "api.timeout"

        # When
        exc = ConfigurationError(message, config_key=config_key)

        # Then
        assert exc.message == message
        assert exc.details['config_key'] == config_key

    def test_without_config_key(self):
        """설정 키 없이 생성"""
        # Given
        message = "Configuration error"

        # When
        exc = ConfigurationError(message)

        # Then
        assert exc.message == message
        assert 'config_key' not in exc.details


class TestDatabaseError:
    """DatabaseError 테스트"""

    def test_create_with_operation_and_table(self):
        """작업과 테이블 정보로 예외 생성"""
        # Given
        message = "Database operation failed"
        operation = "insert"
        table = "trades"

        # When
        exc = DatabaseError(message, operation=operation, table=table)

        # Then
        assert exc.message == message
        assert exc.details['operation'] == operation
        assert exc.details['table'] == table

    def test_without_operation_table(self):
        """작업/테이블 정보 없이 생성"""
        # Given
        message = "DB error"

        # When
        exc = DatabaseError(message)

        # Then
        assert exc.message == message
        assert 'operation' not in exc.details
        assert 'table' not in exc.details


class TestExceptionHierarchy:
    """예외 계층 구조 테스트"""

    def test_api_exception_is_trading_exception(self):
        """APIException이 TradingException의 하위 클래스인지 확인"""
        assert issubclass(APIException, TradingException)

    def test_connection_error_is_api_exception(self):
        """ConnectionError가 APIException의 하위 클래스인지 확인"""
        assert issubclass(ConnectionError, APIException)

    def test_timeout_error_is_api_exception(self):
        """TimeoutError가 APIException의 하위 클래스인지 확인"""
        assert issubclass(TimeoutError, APIException)

    def test_authentication_error_is_api_exception(self):
        """AuthenticationError가 APIException의 하위 클래스인지 확인"""
        assert issubclass(AuthenticationError, APIException)

    def test_order_failed_error_is_trading_exception(self):
        """OrderFailedError가 TradingException의 하위 클래스인지 확인"""
        assert issubclass(OrderFailedError, TradingException)

    def test_insufficient_funds_error_is_order_failed_error(self):
        """InsufficientFundsError가 OrderFailedError의 하위 클래스인지 확인"""
        assert issubclass(InsufficientFundsError, OrderFailedError)

    def test_data_validation_error_is_trading_exception(self):
        """DataValidationError가 TradingException의 하위 클래스인지 확인"""
        assert issubclass(DataValidationError, TradingException)

    def test_invalid_stock_code_error_is_data_validation_error(self):
        """InvalidStockCodeError가 DataValidationError의 하위 클래스인지 확인"""
        assert issubclass(InvalidStockCodeError, DataValidationError)

    def test_configuration_error_is_trading_exception(self):
        """ConfigurationError가 TradingException의 하위 클래스인지 확인"""
        assert issubclass(ConfigurationError, TradingException)

    def test_database_error_is_trading_exception(self):
        """DatabaseError가 TradingException의 하위 클래스인지 확인"""
        assert issubclass(DatabaseError, TradingException)
