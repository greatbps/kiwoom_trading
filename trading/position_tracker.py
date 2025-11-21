"""
포지션 추적 및 관리

보유 포지션의 상태, 수익률, 청산 단계 등을 관리
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List
from enum import Enum


class ExitStage(Enum):
    """청산 단계"""
    NONE = 0          # 청산 없음
    PARTIAL_1 = 1     # 1차 부분 청산 (30%)
    PARTIAL_2 = 2     # 2차 부분 청산 (30%)
    FULL = 3          # 전량 청산


@dataclass
class Position:
    """포지션 정보"""
    stock_code: str
    stock_name: str
    entry_price: float
    quantity: int
    entry_time: datetime

    # 수익률 추적
    current_price: float = 0.0
    profit_pct: float = 0.0
    max_profit_pct: float = 0.0  # 최고 수익률 (트레일링 스탑용)

    # 청산 단계
    exit_stage: ExitStage = ExitStage.NONE
    partial_exit_stage: int = 0  # 🔧 FIX: 부분 청산 단계 추적 (0, 1, 2, ...)
    remaining_quantity: int = 0  # 남은 수량

    # 매도 내역
    partial_sells: List[Dict] = field(default_factory=list)  # [{'stage': 1, 'quantity': 10, 'price': 50000, 'time': ...}]

    def __post_init__(self):
        """초기화 후 처리"""
        if self.remaining_quantity == 0:
            self.remaining_quantity = self.quantity

    def update_price(self, current_price: float) -> None:
        """
        현재가 업데이트 및 수익률 계산

        Args:
            current_price: 현재가
        """
        self.current_price = current_price

        if self.entry_price > 0:
            self.profit_pct = ((current_price - self.entry_price) / self.entry_price) * 100

            # 최고 수익률 업데이트
            if self.profit_pct > self.max_profit_pct:
                self.max_profit_pct = self.profit_pct

    def record_partial_sell(self, stage: int, quantity: int, price: float) -> None:
        """
        부분 청산 기록

        Args:
            stage: 청산 단계 (1, 2)
            quantity: 청산 수량
            price: 청산 가격
        """
        self.partial_sells.append({
            'stage': stage,
            'quantity': quantity,
            'price': price,
            'time': datetime.now(),
            'profit_pct': ((price - self.entry_price) / self.entry_price) * 100
        })

        self.remaining_quantity -= quantity

        # 🔧 FIX: 부분 청산 단계 추적 업데이트
        self.partial_exit_stage = stage

        # 청산 단계 업데이트
        if stage == 1:
            self.exit_stage = ExitStage.PARTIAL_1
        elif stage == 2:
            self.exit_stage = ExitStage.PARTIAL_2

    def record_full_sell(self, price: float) -> None:
        """
        전량 청산 기록

        Args:
            price: 청산 가격
        """
        self.exit_stage = ExitStage.FULL
        self.remaining_quantity = 0
        self.current_price = price
        self.update_price(price)

    def get_total_profit(self) -> float:
        """
        총 실현 수익 계산 (부분 청산 포함)

        Returns:
            총 실현 수익 (원)
        """
        total_profit = 0.0

        # 부분 청산 수익
        for sell in self.partial_sells:
            profit = (sell['price'] - self.entry_price) * sell['quantity']
            total_profit += profit

        # 보유 중 수익 (미실현)
        if self.remaining_quantity > 0 and self.current_price > 0:
            unrealized = (self.current_price - self.entry_price) * self.remaining_quantity
            total_profit += unrealized

        return total_profit

    def get_realized_profit(self) -> float:
        """
        실현 수익만 계산

        Returns:
            실현 수익 (원)
        """
        total_profit = 0.0

        for sell in self.partial_sells:
            profit = (sell['price'] - self.entry_price) * sell['quantity']
            total_profit += profit

        return total_profit

    def to_dict(self) -> Dict:
        """딕셔너리로 변환 (DB 저장용)"""
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'entry_price': self.entry_price,
            'quantity': self.quantity,
            'entry_time': self.entry_time.isoformat(),
            'current_price': self.current_price,
            'profit_pct': self.profit_pct,
            'max_profit_pct': self.max_profit_pct,
            'exit_stage': self.exit_stage.value,
            'remaining_quantity': self.remaining_quantity,
            'partial_sells': self.partial_sells
        }


class PositionTracker:
    """포지션 추적 관리자"""

    def __init__(self):
        """초기화"""
        self.positions: Dict[str, Position] = {}

    def add_position(self, stock_code: str, stock_name: str, entry_price: float,
                    quantity: int, entry_time: datetime = None) -> Position:
        """
        포지션 추가

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            entry_price: 진입 가격
            quantity: 수량
            entry_time: 진입 시간 (None이면 현재 시간)

        Returns:
            생성된 Position 객체
        """
        if entry_time is None:
            entry_time = datetime.now()

        position = Position(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=entry_time
        )

        self.positions[stock_code] = position
        return position

    def remove_position(self, stock_code: str) -> Optional[Position]:
        """
        포지션 제거

        Args:
            stock_code: 종목 코드

        Returns:
            제거된 Position 객체 (없으면 None)
        """
        return self.positions.pop(stock_code, None)

    def get_position(self, stock_code: str) -> Optional[Position]:
        """
        포지션 조회

        Args:
            stock_code: 종목 코드

        Returns:
            Position 객체 (없으면 None)
        """
        return self.positions.get(stock_code)

    def has_position(self, stock_code: str) -> bool:
        """
        포지션 보유 여부 확인

        Args:
            stock_code: 종목 코드

        Returns:
            보유 여부
        """
        return stock_code in self.positions

    def update_price(self, stock_code: str, current_price: float) -> None:
        """
        특정 종목 가격 업데이트

        Args:
            stock_code: 종목 코드
            current_price: 현재가
        """
        position = self.get_position(stock_code)
        if position:
            position.update_price(current_price)

    def update_all_prices(self, price_dict: Dict[str, float]) -> None:
        """
        모든 포지션 가격 일괄 업데이트

        Args:
            price_dict: {stock_code: current_price}
        """
        for stock_code, current_price in price_dict.items():
            self.update_price(stock_code, current_price)

    def get_all_positions(self) -> List[Position]:
        """
        모든 포지션 조회

        Returns:
            Position 리스트
        """
        return list(self.positions.values())

    def get_active_positions(self) -> List[Position]:
        """
        활성 포지션 조회 (남은 수량이 있는 것만)

        Returns:
            활성 Position 리스트
        """
        return [p for p in self.positions.values() if p.remaining_quantity > 0]

    def get_total_invested(self) -> float:
        """
        총 투자 금액 계산 (남은 수량 기준)

        Returns:
            총 투자 금액 (원)
        """
        total = 0.0
        for position in self.positions.values():
            total += position.entry_price * position.remaining_quantity
        return total

    def get_total_value(self) -> float:
        """
        총 평가 금액 계산 (현재가 기준)

        Returns:
            총 평가 금액 (원)
        """
        total = 0.0
        for position in self.positions.values():
            if position.remaining_quantity > 0 and position.current_price > 0:
                total += position.current_price * position.remaining_quantity
        return total

    def get_total_profit(self) -> float:
        """
        총 손익 계산 (실현 + 미실현)

        Returns:
            총 손익 (원)
        """
        total = 0.0
        for position in self.positions.values():
            total += position.get_total_profit()
        return total

    def get_total_realized_profit(self) -> float:
        """
        총 실현 손익 계산

        Returns:
            총 실현 손익 (원)
        """
        total = 0.0
        for position in self.positions.values():
            total += position.get_realized_profit()
        return total

    def get_position_count(self) -> int:
        """
        포지션 개수 조회

        Returns:
            활성 포지션 개수
        """
        return len(self.get_active_positions())

    def clear_all(self) -> None:
        """모든 포지션 제거"""
        self.positions.clear()

    def to_dict(self) -> Dict[str, Dict]:
        """
        모든 포지션을 딕셔너리로 변환

        Returns:
            {stock_code: position_dict}
        """
        return {
            stock_code: position.to_dict()
            for stock_code, position in self.positions.items()
        }
