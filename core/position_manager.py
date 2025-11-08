"""
포지션 관리자 - 보유 종목 추적 및 관리

이 모듈은 현재 보유 중인 종목들을 추적하고 관리합니다.
trading_system의 코드를 참고하되 더욱 발전되고 깔끔한 구조로 작성되었습니다.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import json


@dataclass
class Position:
    """개별 포지션 정보"""
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: float
    buy_time: datetime

    # 매매 전략 정보
    target1: float
    target2: float
    target3: float
    stop_loss: float

    # 분할 매도 진행 상황
    stage: int = 0  # 0: 진입, 1: 1차익절(40%), 2: 2차익절(40%), 3: trailing(20%)
    remaining_quantity: int = field(init=False)
    is_trailing_active: bool = False
    trailing_stop: Optional[float] = None

    # ATR 정보 (trailing stop 계산용)
    atr: float = 0.0

    # 메타 정보
    entry_signal: str = "BUY"  # BUY, STRONG_BUY
    entry_score: float = 0.0

    def __post_init__(self):
        """초기화 후 처리"""
        self.remaining_quantity = self.quantity

    @property
    def current_value(self) -> float:
        """현재 평가금액"""
        return self.remaining_quantity * self.current_price

    @property
    def invested_value(self) -> float:
        """투자금액"""
        return self.quantity * self.avg_price

    @property
    def profit_loss(self) -> float:
        """손익금액"""
        return (self.current_price - self.avg_price) * self.remaining_quantity

    @property
    def profit_loss_rate(self) -> float:
        """손익률 (%)"""
        return ((self.current_price - self.avg_price) / self.avg_price) * 100

    @property
    def is_closed(self) -> bool:
        """포지션 종료 여부"""
        return self.remaining_quantity == 0

    def update_price(self, new_price: float):
        """현재가 업데이트"""
        self.current_price = new_price

    def update_trailing_stop(self):
        """
        Trailing stop 업데이트

        ATR 2배를 사용한 trailing stop
        가격이 상승하면 trailing stop도 같이 상승
        """
        if not self.is_trailing_active or self.atr == 0:
            return

        # ATR 2배 아래로 trailing stop 설정
        new_trailing = self.current_price - (self.atr * 2.0)

        # Trailing stop은 상승만 가능 (하락 불가)
        if self.trailing_stop is None or new_trailing > self.trailing_stop:
            self.trailing_stop = new_trailing

    def should_exit_by_trailing(self) -> bool:
        """Trailing stop 도달 여부"""
        if not self.is_trailing_active or self.trailing_stop is None:
            return False
        return self.current_price <= self.trailing_stop

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'quantity': self.quantity,
            'avg_price': self.avg_price,
            'current_price': self.current_price,
            'buy_time': self.buy_time.isoformat(),
            'target1': self.target1,
            'target2': self.target2,
            'target3': self.target3,
            'stop_loss': self.stop_loss,
            'stage': self.stage,
            'remaining_quantity': self.remaining_quantity,
            'is_trailing_active': self.is_trailing_active,
            'trailing_stop': self.trailing_stop,
            'atr': self.atr,
            'entry_signal': self.entry_signal,
            'entry_score': self.entry_score,
            'profit_loss': self.profit_loss,
            'profit_loss_rate': self.profit_loss_rate
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        """딕셔너리에서 생성"""
        buy_time = datetime.fromisoformat(data['buy_time']) if isinstance(data['buy_time'], str) else data['buy_time']

        pos = cls(
            stock_code=data['stock_code'],
            stock_name=data['stock_name'],
            quantity=data['quantity'],
            avg_price=data['avg_price'],
            current_price=data['current_price'],
            buy_time=buy_time,
            target1=data['target1'],
            target2=data['target2'],
            target3=data['target3'],
            stop_loss=data['stop_loss'],
            atr=data.get('atr', 0.0),
            entry_signal=data.get('entry_signal', 'BUY'),
            entry_score=data.get('entry_score', 0.0)
        )

        pos.stage = data.get('stage', 0)
        pos.remaining_quantity = data.get('remaining_quantity', pos.quantity)
        pos.is_trailing_active = data.get('is_trailing_active', False)
        pos.trailing_stop = data.get('trailing_stop')

        return pos


class PositionManager:
    """
    포지션 관리자

    보유 종목의 추적, 업데이트, 저장/복원을 담당합니다.
    """

    def __init__(self, storage_path: str = 'data/positions.json'):
        """
        초기화

        Args:
            storage_path: 포지션 저장 경로
        """
        self.storage_path = storage_path
        self.positions: Dict[str, Position] = {}

        # 저장된 포지션 로드
        self.load()

    def add_position(self, position: Position):
        """
        포지션 추가

        Args:
            position: 추가할 포지션
        """
        self.positions[position.stock_code] = position
        self.save()

    def remove_position(self, stock_code: str):
        """
        포지션 제거

        Args:
            stock_code: 종목코드
        """
        if stock_code in self.positions:
            del self.positions[stock_code]
            self.save()

    def get_position(self, stock_code: str) -> Optional[Position]:
        """
        포지션 조회

        Args:
            stock_code: 종목코드

        Returns:
            포지션 객체 (없으면 None)
        """
        return self.positions.get(stock_code)

    def update_price(self, stock_code: str, new_price: float):
        """
        현재가 업데이트

        Args:
            stock_code: 종목코드
            new_price: 새로운 가격
        """
        position = self.get_position(stock_code)
        if position:
            position.update_price(new_price)

            # Trailing stop 활성화 상태라면 업데이트
            if position.is_trailing_active:
                position.update_trailing_stop()

            self.save()

    def update_stage(self, stock_code: str, new_stage: int, sold_quantity: int = 0):
        """
        매도 단계 업데이트

        Args:
            stock_code: 종목코드
            new_stage: 새로운 단계 (1: 1차익절, 2: 2차익절, 3: trailing)
            sold_quantity: 매도 수량
        """
        position = self.get_position(stock_code)
        if position:
            position.stage = new_stage
            position.remaining_quantity -= sold_quantity

            # 2차 익절 후 trailing stop 활성화
            if new_stage == 2:
                position.is_trailing_active = True
                position.update_trailing_stop()

            # 모두 매도됐으면 포지션 제거
            if position.is_closed:
                self.remove_position(stock_code)
            else:
                self.save()

    def get_all_positions(self) -> List[Position]:
        """모든 포지션 조회"""
        return list(self.positions.values())

    def get_active_positions(self) -> List[Position]:
        """활성 포지션 조회 (수량이 남아있는 것만)"""
        return [p for p in self.positions.values() if not p.is_closed]

    def get_total_invested(self) -> float:
        """총 투자금액"""
        return sum(p.invested_value for p in self.positions.values())

    def get_total_value(self) -> float:
        """총 평가금액"""
        return sum(p.current_value for p in self.positions.values())

    def get_total_profit_loss(self) -> float:
        """총 손익금액"""
        return sum(p.profit_loss for p in self.positions.values())

    def get_total_profit_loss_rate(self) -> float:
        """총 손익률 (%)"""
        invested = self.get_total_invested()
        if invested == 0:
            return 0.0
        return (self.get_total_profit_loss() / invested) * 100

    def get_position_count(self) -> int:
        """보유 종목 수"""
        return len(self.get_active_positions())

    def save(self):
        """포지션 저장"""
        import os
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

        data = {
            stock_code: position.to_dict()
            for stock_code, position in self.positions.items()
        }

        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load(self):
        """포지션 로드"""
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.positions = {
                    stock_code: Position.from_dict(pos_data)
                    for stock_code, pos_data in data.items()
                }
        except FileNotFoundError:
            self.positions = {}

    def get_summary(self) -> dict:
        """포트폴리오 요약"""
        return {
            'position_count': self.get_position_count(),
            'total_invested': self.get_total_invested(),
            'total_value': self.get_total_value(),
            'total_profit_loss': self.get_total_profit_loss(),
            'total_profit_loss_rate': self.get_total_profit_loss_rate(),
            'positions': [p.to_dict() for p in self.get_active_positions()]
        }
