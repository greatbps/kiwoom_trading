"""
브로커 추상화 베이스 클래스
=========================

모든 증권사 API의 공통 인터페이스

원칙:
- 전략 코드는 증권사를 모름
- 브로커만 교체하면 어디서든 동작
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime


# ============================================================================
# 공통 데이터 클래스
# ============================================================================

class Market(Enum):
    """시장 구분"""
    KR = "KR"      # 한국
    US = "US"      # 미국
    HK = "HK"      # 홍콩
    JP = "JP"      # 일본


class OrderSide(Enum):
    """주문 방향"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """주문 유형"""
    MARKET = "MARKET"    # 시장가
    LIMIT = "LIMIT"      # 지정가


class OrderStatus(Enum):
    """주문 상태"""
    PENDING = "PENDING"      # 대기
    SUBMITTED = "SUBMITTED"  # 접수
    FILLED = "FILLED"        # 체결
    PARTIAL = "PARTIAL"      # 부분체결
    CANCELLED = "CANCELLED"  # 취소
    REJECTED = "REJECTED"    # 거부
    FAILED = "FAILED"        # 실패


@dataclass
class Position:
    """보유 포지션"""
    symbol: str
    name: str
    quantity: int
    avg_price: float
    current_price: float
    profit_pct: float
    eval_amount: float
    market: Market
    currency: str = "KRW"

    @property
    def profit_loss(self) -> float:
        return (self.current_price - self.avg_price) * self.quantity


@dataclass
class OrderResult:
    """주문 결과"""
    success: bool
    order_no: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    quantity: int = 0
    price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    message: str = ""
    timestamp: datetime = None
    raw_response: Dict = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class Balance:
    """계좌 잔고"""
    total_eval: float          # 총 평가금액
    total_deposit: float       # 예수금
    available_cash: float      # 주문가능금액
    total_profit: float        # 총 평가손익
    total_profit_pct: float    # 총 수익률
    currency: str = "KRW"


# ============================================================================
# 브로커 베이스 클래스
# ============================================================================

class BrokerBase(ABC):
    """
    브로커 추상화 베이스 클래스

    모든 증권사 API는 이 인터페이스를 구현
    """

    def __init__(self, name: str, market: Market):
        self.name = name
        self.market = market
        self.is_initialized = False

    # ─────────────────────────────────────────────────────────────
    # 필수 구현 메서드
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    def initialize(self) -> bool:
        """
        API 초기화 (토큰 발급 등)

        Returns:
            성공 여부
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """
        보유 포지션 조회

        Returns:
            포지션 리스트
        """
        pass

    @abstractmethod
    def get_balance(self) -> Balance:
        """
        계좌 잔고 조회

        Returns:
            잔고 정보
        """
        pass

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0
    ) -> OrderResult:
        """
        주문 실행

        Args:
            symbol: 종목코드
            side: 매수/매도
            quantity: 수량
            order_type: 주문유형
            price: 가격 (지정가일 때)

        Returns:
            주문 결과
        """
        pass

    @abstractmethod
    def is_market_open(self) -> tuple[bool, str]:
        """
        시장 개장 여부

        Returns:
            (개장여부, 상태메시지)
        """
        pass

    # ─────────────────────────────────────────────────────────────
    # 편의 메서드 (기본 구현 제공)
    # ─────────────────────────────────────────────────────────────

    def place_market_buy(self, symbol: str, quantity: int) -> OrderResult:
        """시장가 매수"""
        return self.place_order(symbol, OrderSide.BUY, quantity, OrderType.MARKET)

    def place_market_sell(self, symbol: str, quantity: int) -> OrderResult:
        """시장가 매도"""
        return self.place_order(symbol, OrderSide.SELL, quantity, OrderType.MARKET)

    def place_limit_buy(self, symbol: str, quantity: int, price: float) -> OrderResult:
        """지정가 매수"""
        return self.place_order(symbol, OrderSide.BUY, quantity, OrderType.LIMIT, price)

    def place_limit_sell(self, symbol: str, quantity: int, price: float) -> OrderResult:
        """지정가 매도"""
        return self.place_order(symbol, OrderSide.SELL, quantity, OrderType.LIMIT, price)

    def get_position(self, symbol: str) -> Optional[Position]:
        """특정 종목 포지션 조회"""
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol:
                return pos
        return None

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name}, market={self.market.value})"
