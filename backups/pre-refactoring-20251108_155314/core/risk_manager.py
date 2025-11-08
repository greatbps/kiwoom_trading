"""
리스크 관리자 - 포트폴리오 전체 리스크 관리

trading_system의 실제 성과 데이터를 기반으로 한 검증된 리스크 관리 시스템:
- 거래당 2% 리스크 제한
- 포지션당 최대 30% 투자
- 하드 리밋: 포지션 20만원, 일일 손실 50만원
- 동적 한도 조정: 실시간 잔고 기반 계산
"""
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional, Dict, List
import json
import os


@dataclass
class DailyTradeLog:
    """일일 거래 로그"""
    date: str
    trades: List[dict]
    realized_pnl: float  # 실현 손익
    unrealized_pnl: float  # 미실현 손익
    total_pnl: float  # 총 손익
    trade_count: int
    win_count: int
    loss_count: int


class RiskManager:
    """
    리스크 관리자

    포트폴리오 전체의 리스크를 관리하고 일일 한도를 추적합니다.
    trading_system의 검증된 리스크 관리 전략을 기반으로 합니다.
    """

    # 문서 명세: 검증된 리스크 파라미터 (trading_system 실전 데이터)
    RISK_PER_TRADE = 0.02          # 거래당 2% 리스크
    MAX_POSITION_SIZE = 0.30       # 포지션당 최대 30%

    # 문서 명세: 하드 리밋 (절대 초과 불가)
    HARD_MAX_POSITION = 200000     # 20만원
    HARD_MAX_DAILY_LOSS = 500000   # 50만원 (일일)
    HARD_MAX_DAILY_TRADES = 10     # 일일 최대 10회

    # 문서 명세: 포트폴리오 제약
    MAX_POSITIONS = 5              # 최대 5종목
    MIN_CASH_RESERVE = 0.20        # 최소 현금 20%

    def __init__(self, initial_balance: float, storage_path: str = 'data/risk_log.json'):
        """
        초기화

        Args:
            initial_balance: 초기 잔고
            storage_path: 리스크 로그 저장 경로
        """
        self.initial_balance = initial_balance
        self.storage_path = storage_path

        # 일일 추적
        self.today = date.today().isoformat()
        self.daily_trades: List[dict] = []
        self.daily_realized_pnl = 0.0  # 오늘 실현 손익

        # 로그 로드
        self.load()

    def can_open_position(
        self,
        current_balance: float,
        current_positions_value: float,
        position_count: int,
        position_size: float
    ) -> tuple[bool, str]:
        """
        신규 포지션 진입 가능 여부 확인

        Args:
            current_balance: 현재 잔고
            current_positions_value: 현재 보유 포지션 총 평가액
            position_count: 현재 보유 종목 수
            position_size: 진입하려는 포지션 크기 (금액)

        Returns:
            (가능 여부, 사유)
        """
        # 1. 보유 종목 수 제한
        if position_count >= self.MAX_POSITIONS:
            return False, f"최대 보유 종목 수 초과 ({position_count}/{self.MAX_POSITIONS})"

        # 2. 일일 거래 횟수 제한
        if len(self.daily_trades) >= self.HARD_MAX_DAILY_TRADES:
            return False, f"일일 최대 거래 횟수 초과 ({len(self.daily_trades)}/{self.HARD_MAX_DAILY_TRADES})"

        # 3. 일일 손실 한도 확인
        if self.daily_realized_pnl < -self.HARD_MAX_DAILY_LOSS:
            return False, f"일일 손실 한도 초과 ({self.daily_realized_pnl:,.0f}원 / -{self.HARD_MAX_DAILY_LOSS:,.0f}원)"

        # 4. 하드 포지션 크기 제한
        if position_size > self.HARD_MAX_POSITION:
            return False, f"하드 포지션 크기 제한 초과 ({position_size:,.0f}원 / {self.HARD_MAX_POSITION:,.0f}원)"

        # 5. 총 자산 대비 포지션 크기 제한 (30%)
        total_assets = current_balance + current_positions_value
        max_position_value = total_assets * self.MAX_POSITION_SIZE

        if position_size > max_position_value:
            return False, f"포지션 크기 제한 초과 ({position_size:,.0f}원 / {max_position_value:,.0f}원)"

        # 6. 현금 보유 비율 확인
        remaining_cash = current_balance - position_size
        cash_ratio = remaining_cash / total_assets

        if cash_ratio < self.MIN_CASH_RESERVE:
            return False, f"현금 보유 비율 부족 ({cash_ratio:.1%} / 최소 {self.MIN_CASH_RESERVE:.1%})"

        return True, "OK"

    def calculate_position_size(
        self,
        current_balance: float,
        current_price: float,
        stop_loss_price: float,
        entry_confidence: float = 1.0
    ) -> dict:
        """
        포지션 크기 계산 (리스크 기반)

        Args:
            current_balance: 현재 잔고
            current_price: 진입 가격
            stop_loss_price: 손절가
            entry_confidence: 진입 신뢰도 (0.0 ~ 1.0)

        Returns:
            {
                'quantity': 매수 수량,
                'investment': 투자 금액,
                'risk_amount': 리스크 금액,
                'position_ratio': 포지션 비율,
                'max_loss': 최대 손실
            }
        """
        # 1. 리스크 기반 계산
        risk_amount = current_balance * self.RISK_PER_TRADE
        risk_per_share = abs(current_price - stop_loss_price)

        if risk_per_share > 0:
            risk_based_quantity = int(risk_amount / risk_per_share)
        else:
            risk_based_quantity = 0

        # 2. 최대 포지션 크기 기반 계산
        max_investment = min(
            current_balance * self.MAX_POSITION_SIZE,
            self.HARD_MAX_POSITION
        )
        max_quantity = int(max_investment / current_price)

        # 3. 신뢰도 조정 (낮은 신뢰도면 포지션 축소)
        confidence_factor = max(0.5, entry_confidence)  # 최소 50%

        # 4. 최종 수량 결정 (더 작은 값 선택)
        final_quantity = min(risk_based_quantity, max_quantity)
        final_quantity = int(final_quantity * confidence_factor)

        # 5. 결과 계산
        investment = final_quantity * current_price
        position_ratio = (investment / current_balance * 100) if current_balance > 0 else 0
        max_loss = final_quantity * risk_per_share

        return {
            'quantity': final_quantity,
            'investment': investment,
            'risk_amount': risk_amount,
            'position_ratio': position_ratio,
            'max_loss': max_loss
        }

    def record_trade(
        self,
        stock_code: str,
        stock_name: str,
        trade_type: str,  # 'BUY' or 'SELL'
        quantity: int,
        price: float,
        realized_pnl: float = 0.0
    ):
        """
        거래 기록

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            trade_type: 거래 유형 (BUY/SELL)
            quantity: 수량
            price: 가격
            realized_pnl: 실현 손익 (매도시만)
        """
        # 날짜가 바뀌면 초기화
        today = date.today().isoformat()
        if today != self.today:
            self._new_day()

        # numpy 타입을 Python 기본 타입으로 변환 (JSON 직렬화 위해)
        trade = {
            'timestamp': datetime.now().isoformat(),
            'stock_code': stock_code,
            'stock_name': stock_name,
            'type': trade_type,
            'quantity': int(quantity),
            'price': float(price),
            'amount': float(quantity * price),
            'realized_pnl': float(realized_pnl) if realized_pnl is not None else 0.0
        }

        self.daily_trades.append(trade)

        # 실현 손익 업데이트 (매도시)
        if trade_type == 'SELL':
            self.daily_realized_pnl += float(realized_pnl) if realized_pnl is not None else 0.0

        self.save()

    def get_daily_summary(self, unrealized_pnl: float = 0.0) -> DailyTradeLog:
        """
        일일 거래 요약

        Args:
            unrealized_pnl: 미실현 손익

        Returns:
            일일 거래 로그
        """
        win_count = sum(1 for t in self.daily_trades if t['type'] == 'SELL' and t['realized_pnl'] > 0)
        loss_count = sum(1 for t in self.daily_trades if t['type'] == 'SELL' and t['realized_pnl'] < 0)

        return DailyTradeLog(
            date=self.today,
            trades=self.daily_trades.copy(),
            realized_pnl=self.daily_realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=self.daily_realized_pnl + unrealized_pnl,
            trade_count=len(self.daily_trades),
            win_count=win_count,
            loss_count=loss_count
        )

    def check_emergency_stop(self, unrealized_pnl: float = 0.0) -> tuple[bool, str]:
        """
        긴급 중지 조건 확인

        Args:
            unrealized_pnl: 미실현 손익

        Returns:
            (중지 여부, 사유)
        """
        total_pnl = self.daily_realized_pnl + unrealized_pnl

        # 1. 일일 손실 한도 초과
        if total_pnl < -self.HARD_MAX_DAILY_LOSS:
            return True, f"일일 손실 한도 초과 ({total_pnl:,.0f}원 / -{self.HARD_MAX_DAILY_LOSS:,.0f}원)"

        # 2. 일일 거래 횟수 초과
        if len(self.daily_trades) >= self.HARD_MAX_DAILY_TRADES:
            return True, f"일일 최대 거래 횟수 초과 ({len(self.daily_trades)}/{self.HARD_MAX_DAILY_TRADES})"

        return False, "OK"

    def get_risk_metrics(self, current_balance: float, positions_value: float, unrealized_pnl: float) -> dict:
        """
        리스크 지표 계산

        Args:
            current_balance: 현재 잔고
            positions_value: 보유 포지션 평가액
            unrealized_pnl: 미실현 손익

        Returns:
            리스크 지표 딕셔너리
        """
        total_assets = current_balance + positions_value
        total_pnl = self.daily_realized_pnl + unrealized_pnl

        cash_ratio = (current_balance / total_assets * 100) if total_assets > 0 else 0
        position_ratio = (positions_value / total_assets * 100) if total_assets > 0 else 0

        # 일일 손실 한도까지 남은 금액
        remaining_loss_allowance = self.HARD_MAX_DAILY_LOSS + total_pnl

        # 일일 수익률
        daily_return = ((total_assets - self.initial_balance) / self.initial_balance * 100) if self.initial_balance > 0 else 0

        return {
            'total_assets': total_assets,
            'current_balance': current_balance,
            'positions_value': positions_value,
            'cash_ratio': cash_ratio,
            'position_ratio': position_ratio,
            'daily_realized_pnl': self.daily_realized_pnl,
            'daily_unrealized_pnl': unrealized_pnl,
            'daily_total_pnl': total_pnl,
            'daily_return': daily_return,
            'remaining_loss_allowance': remaining_loss_allowance,
            'daily_trade_count': len(self.daily_trades),
            'max_daily_trades': self.HARD_MAX_DAILY_TRADES,
            'remaining_trades': self.HARD_MAX_DAILY_TRADES - len(self.daily_trades)
        }

    def update_balance(self, new_balance: float):
        """
        실시간 잔고 업데이트

        Args:
            new_balance: 업데이트된 현금 잔고
        """
        self.initial_balance = new_balance

    def _new_day(self):
        """새로운 날 초기화"""
        self.today = date.today().isoformat()
        self.daily_trades = []
        self.daily_realized_pnl = 0.0

    def save(self):
        """리스크 로그 저장 (원자적 쓰기)"""
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

        # daily_realized_pnl도 float 변환 (안전장치)
        data = {
            'initial_balance': float(self.initial_balance),
            'today': self.today,
            'daily_trades': self.daily_trades,
            'daily_realized_pnl': float(self.daily_realized_pnl)
        }

        # 원자적 쓰기: 임시 파일에 쓴 후 rename
        temp_path = self.storage_path + '.tmp'
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            # rename은 원자적 연산
            os.replace(temp_path, self.storage_path)
        except Exception as e:
            # 에러 발생 시 임시 파일 삭제
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def load(self):
        """리스크 로그 로드"""
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # 날짜가 같으면 복원
                if data.get('today') == self.today:
                    self.daily_trades = data.get('daily_trades', [])
                    self.daily_realized_pnl = data.get('daily_realized_pnl', 0.0)
                else:
                    # 날짜가 다르면 초기화
                    self._new_day()

        except FileNotFoundError:
            self._new_day()
