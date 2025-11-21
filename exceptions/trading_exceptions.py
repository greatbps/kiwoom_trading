"""
거래 시스템 커스텀 예외 클래스

예외 계층 구조:
TradingException (기본)
├── APIException (API 관련)
│   ├── ConnectionError
│   ├── TimeoutError
│   └── AuthenticationError
├── OrderFailedError (주문 실패)
│   └── InsufficientFundsError
├── DataValidationError (데이터 검증 실패)
│   └── InvalidStockCodeError
├── ConfigurationError (설정 오류)
└── DatabaseError (데이터베이스 오류)
"""
from typing import Optional, Dict, Any


class TradingException(Exception):
    """거래 시스템 기본 예외 클래스"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Args:
            message: 오류 메시지
            details: 추가 상세 정보
        """
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self):
        if self.details:
            return f"{self.message} (details: {self.details})"
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        """예외 정보를 딕셔너리로 변환"""
        return {
            'type': self.__class__.__name__,
            'message': self.message,
            'details': self.details
        }


# ==========================================
# API 관련 예외
# ==========================================

class APIException(TradingException):
    """API 호출 관련 예외"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_data: Optional[Dict] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            message: 오류 메시지
            status_code: HTTP 상태 코드
            response_data: API 응답 데이터
            details: 추가 상세 정보
        """
        self.status_code = status_code
        self.response_data = response_data

        combined_details = details or {}
        if status_code:
            combined_details['status_code'] = status_code
        if response_data:
            combined_details['response_data'] = response_data

        super().__init__(message, combined_details)


class ConnectionError(APIException):
    """연결 실패 예외"""

    def __init__(self, message: str = "Failed to connect to API", **kwargs):
        super().__init__(message, **kwargs)


class TimeoutError(APIException):
    """타임아웃 예외"""

    def __init__(
        self,
        message: str = "API request timed out",
        timeout_seconds: Optional[int] = None,
        **kwargs
    ):
        details = kwargs.get('details', {})
        if timeout_seconds:
            details['timeout_seconds'] = timeout_seconds
        kwargs['details'] = details
        super().__init__(message, **kwargs)


class AuthenticationError(APIException):
    """인증 실패 예외"""

    def __init__(self, message: str = "Authentication failed", **kwargs):
        super().__init__(message, status_code=401, **kwargs)


# ==========================================
# 주문 관련 예외
# ==========================================

class OrderFailedError(TradingException):
    """주문 실패 예외"""

    def __init__(
        self,
        message: str,
        order_id: Optional[str] = None,
        stock_code: Optional[str] = None,
        order_type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            message: 오류 메시지
            order_id: 주문 ID
            stock_code: 종목 코드
            order_type: 주문 유형 ('buy' or 'sell')
            details: 추가 상세 정보
        """
        self.order_id = order_id
        self.stock_code = stock_code
        self.order_type = order_type

        combined_details = details or {}
        if order_id:
            combined_details['order_id'] = order_id
        if stock_code:
            combined_details['stock_code'] = stock_code
        if order_type:
            combined_details['order_type'] = order_type

        super().__init__(message, combined_details)


class InsufficientFundsError(OrderFailedError):
    """잔고 부족 예외"""

    def __init__(
        self,
        required_amount: float,
        available_amount: float,
        stock_code: Optional[str] = None,
        **kwargs
    ):
        """
        Args:
            required_amount: 필요 금액
            available_amount: 사용 가능 금액
            stock_code: 종목 코드
        """
        self.required_amount = required_amount
        self.available_amount = available_amount

        message = (
            f"Insufficient funds: required {required_amount:,.0f}, "
            f"available {available_amount:,.0f}"
        )

        details = kwargs.get('details', {})
        details.update({
            'required_amount': required_amount,
            'available_amount': available_amount,
            'shortage': required_amount - available_amount
        })
        kwargs['details'] = details

        super().__init__(message, stock_code=stock_code, **kwargs)


# ==========================================
# 데이터 검증 관련 예외
# ==========================================

class DataValidationError(TradingException):
    """데이터 검증 실패 예외"""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        expected: Optional[Any] = None,
        actual: Optional[Any] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            message: 오류 메시지
            field: 검증 실패 필드
            expected: 예상 값
            actual: 실제 값
            details: 추가 상세 정보
        """
        combined_details = details or {}
        if field:
            combined_details['field'] = field
        if expected is not None:
            combined_details['expected'] = expected
        if actual is not None:
            combined_details['actual'] = actual

        super().__init__(message, combined_details)


class InvalidStockCodeError(DataValidationError):
    """유효하지 않은 종목 코드 예외"""

    def __init__(self, stock_code: str, reason: Optional[str] = None, **kwargs):
        """
        Args:
            stock_code: 종목 코드
            reason: 실패 사유
        """
        message = f"Invalid stock code: {stock_code}"
        if reason:
            message += f" ({reason})"

        details = kwargs.get('details', {})
        details['stock_code'] = stock_code
        if reason:
            details['reason'] = reason
        kwargs['details'] = details

        super().__init__(message, field='stock_code', **kwargs)


# ==========================================
# 설정 관련 예외
# ==========================================

class ConfigurationError(TradingException):
    """설정 오류 예외"""

    def __init__(
        self,
        message: str,
        config_key: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            message: 오류 메시지
            config_key: 설정 키
            details: 추가 상세 정보
        """
        combined_details = details or {}
        if config_key:
            combined_details['config_key'] = config_key

        super().__init__(message, combined_details)


# ==========================================
# 데이터베이스 관련 예외
# ==========================================

class DatabaseError(TradingException):
    """데이터베이스 오류 예외"""

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        table: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            message: 오류 메시지
            operation: 작업 유형 ('insert', 'update', 'delete', 'select')
            table: 테이블 이름
            details: 추가 상세 정보
        """
        combined_details = details or {}
        if operation:
            combined_details['operation'] = operation
        if table:
            combined_details['table'] = table

        super().__init__(message, combined_details)
