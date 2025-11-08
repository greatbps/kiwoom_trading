"""
리스크 관리자 (Risk Manager)

일일 손실 제한, 드로다운 제어, 거래 횟수 제한 등
실전 매매의 안정성을 위한 리스크 관리 시스템
"""
from typing import Dict, List, Optional
from datetime import datetime, date
import numpy as np


class RiskManager:
    """매매 리스크 관리 시스템"""

    def __init__(
        self,
        initial_capital: float = 10000000,      # 초기 자본
        daily_max_loss_pct: float = 2.0,        # 일일 최대 손실률 (%)
        max_drawdown_pct: float = 10.0,         # 최대 낙폭 허용률 (%)
        max_trades_per_day: int = 5,            # 일일 최대 거래 횟수
        max_consecutive_losses: int = 3,        # 연속 손실 허용 횟수
        position_risk_pct: float = 1.0          # 거래당 리스크 비율 (%)
    ):
        """
        초기화

        Args:
            initial_capital: 초기 자본금
            daily_max_loss_pct: 일일 최대 손실률 (%)
            max_drawdown_pct: 최대 낙폭 허용률 (%)
            max_trades_per_day: 일일 최대 거래 횟수
            max_consecutive_losses: 연속 손실 허용 횟수
            position_risk_pct: 포지션당 리스크 비율 (%)
        """
        self.initial_capital = initial_capital
        self.daily_max_loss_pct = daily_max_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.max_trades_per_day = max_trades_per_day
        self.max_consecutive_losses = max_consecutive_losses
        self.position_risk_pct = position_risk_pct

        # 상태 변수
        self.current_capital = initial_capital
        self.peak_capital = initial_capital
        self.daily_pnl = 0.0
        self.today = date.today()
        self.trades_today = 0
        self.consecutive_losses = 0

        # 거래 히스토리
        self.trade_history: List[Dict] = []
        self.daily_history: List[Dict] = []

    def reset_daily(self):
        """일일 통계 초기화 (날짜 변경 시)"""
        current_date = date.today()

        if current_date != self.today:
            # 일일 기록 저장
            self.daily_history.append({
                'date': self.today,
                'pnl': self.daily_pnl,
                'trades': self.trades_today,
                'capital': self.current_capital
            })

            # 초기화
            self.today = current_date
            self.daily_pnl = 0.0
            self.trades_today = 0

    def can_trade(self) -> tuple[bool, str]:
        """
        거래 가능 여부 체크

        Returns:
            (can_trade: bool, reason: str)
        """
        # 날짜 체크 및 리셋
        self.reset_daily()

        # 1. 일일 손실 한도 체크
        daily_loss_pct = (self.daily_pnl / self.current_capital) * 100
        if daily_loss_pct <= -self.daily_max_loss_pct:
            return False, f"일일 손실 한도 초과 ({daily_loss_pct:.2f}% / -{self.daily_max_loss_pct}%)"

        # 2. 최대 낙폭 체크
        current_drawdown = ((self.peak_capital - self.current_capital) / self.peak_capital) * 100
        if current_drawdown >= self.max_drawdown_pct:
            return False, f"최대 낙폭 초과 ({current_drawdown:.2f}% / {self.max_drawdown_pct}%)"

        # 3. 일일 거래 횟수 체크
        if self.trades_today >= self.max_trades_per_day:
            return False, f"일일 거래 횟수 한도 도달 ({self.trades_today}/{self.max_trades_per_day})"

        # 4. 연속 손실 체크
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, f"연속 손실 한도 도달 ({self.consecutive_losses}회)"

        return True, "거래 가능"

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: Optional[float] = None,
        min_rr_ratio: float = 1.5
    ) -> tuple[int, float, str]:
        """
        포지션 크기 계산 (리스크 기반 + R:R 검증)

        Args:
            entry_price: 진입가
            stop_loss_price: 손절가
            take_profit_price: 목표가 (optional, R:R 계산용)
            min_rr_ratio: 최소 손익비 (기본 1.5)

        Returns:
            (quantity, risk_amount, message)
        """
        # 손절가 유효성 체크
        if entry_price <= stop_loss_price:
            return 0, 0.0, "손절가가 진입가보다 높거나 같음"

        # R:R 비율 체크 (목표가 있는 경우)
        if take_profit_price is not None:
            risk = entry_price - stop_loss_price
            reward = take_profit_price - entry_price

            if risk <= 0:
                return 0, 0.0, "손절가와 진입가가 동일"

            rr_ratio = reward / risk

            if rr_ratio < min_rr_ratio:
                return 0, 0.0, f"손익비 부족 (R:R {rr_ratio:.2f} < {min_rr_ratio})"

        # 거래당 리스크 금액
        risk_amount = self.current_capital * (self.position_risk_pct / 100)

        # 1주당 리스크
        risk_per_share = abs(entry_price - stop_loss_price)

        # 주식 수 계산
        quantity = int(risk_amount / risk_per_share)

        # 최소 수량 체크
        if quantity == 0:
            return 0, 0.0, "리스크 대비 진입가가 너무 높음"

        # 실제 투자 금액이 자본을 초과하지 않도록
        investment = quantity * entry_price
        max_investment = self.current_capital * 0.95  # 자본의 95% 이하

        if investment > max_investment:
            quantity = int(max_investment / entry_price)
            risk_amount = quantity * risk_per_share

        # 메시지 생성
        msg = f"포지션: {quantity}주 (리스크: {risk_amount:,.0f}원"
        if take_profit_price is not None:
            rr_ratio = (take_profit_price - entry_price) / (entry_price - stop_loss_price)
            msg += f", R:R {rr_ratio:.2f}"
        msg += ")"

        return quantity, risk_amount, msg

    def update_trade(
        self,
        profit: float,
        trade_type: str = "trade"
    ):
        """
        거래 결과 업데이트

        Args:
            profit: 손익 (원)
            trade_type: 거래 타입 ("trade", "trailing_stop", "stop_loss" 등)
        """
        # 자본 업데이트
        self.current_capital += profit
        self.daily_pnl += profit

        # 고점 갱신
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

        # 거래 횟수 증가
        self.trades_today += 1

        # 연속 손실 체크
        if profit < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0  # 수익 시 리셋

        # 거래 기록
        self.trade_history.append({
            'timestamp': datetime.now(),
            'type': trade_type,
            'profit': profit,
            'profit_pct': (profit / (self.current_capital - profit)) * 100,
            'capital': self.current_capital,
            'daily_pnl': self.daily_pnl
        })

    def get_current_drawdown(self) -> float:
        """
        현재 낙폭 반환

        Returns:
            낙폭 (%)
        """
        if self.peak_capital == 0:
            return 0.0
        return ((self.peak_capital - self.current_capital) / self.peak_capital) * 100

    def get_daily_loss_pct(self) -> float:
        """
        오늘 손실률 반환

        Returns:
            일일 손실률 (%)
        """
        if self.current_capital == 0:
            return 0.0
        return (self.daily_pnl / self.current_capital) * 100

    def get_statistics(self) -> Dict:
        """
        통계 정보 반환

        Returns:
            {
                'current_capital': 현재 자본,
                'total_return': 총 수익,
                'total_return_pct': 총 수익률,
                'drawdown': 현재 낙폭,
                'daily_pnl': 오늘 손익,
                'daily_pnl_pct': 오늘 손익률,
                'trades_today': 오늘 거래 횟수,
                'consecutive_losses': 연속 손실,
                'total_trades': 총 거래 횟수,
                'win_trades': 수익 거래,
                'win_rate': 승률
            }
        """
        total_return = self.current_capital - self.initial_capital
        total_return_pct = (total_return / self.initial_capital) * 100

        win_trades = len([t for t in self.trade_history if t['profit'] > 0])
        total_trades = len(self.trade_history)
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0

        return {
            'current_capital': self.current_capital,
            'total_return': total_return,
            'total_return_pct': total_return_pct,
            'drawdown': self.get_current_drawdown(),
            'daily_pnl': self.daily_pnl,
            'daily_pnl_pct': self.get_daily_loss_pct(),
            'trades_today': self.trades_today,
            'consecutive_losses': self.consecutive_losses,
            'total_trades': total_trades,
            'win_trades': win_trades,
            'win_rate': win_rate
        }

    def get_status_message(self) -> str:
        """
        현재 상태 메시지 반환

        Returns:
            상태 요약 문자열
        """
        stats = self.get_statistics()

        msg = f"""
=== 리스크 관리 현황 ===
현재 자본: {stats['current_capital']:,.0f}원
총 수익률: {stats['total_return_pct']:+.2f}%
현재 낙폭: {stats['drawdown']:.2f}%

오늘 손익: {stats['daily_pnl']:+,.0f}원 ({stats['daily_pnl_pct']:+.2f}%)
오늘 거래: {stats['trades_today']}/{self.max_trades_per_day}회
연속 손실: {stats['consecutive_losses']}/{self.max_consecutive_losses}회

전체 승률: {stats['win_rate']:.1f}% ({stats['win_trades']}/{stats['total_trades']})
"""
        return msg

    def check_circuit_breaker(self) -> tuple[bool, str]:
        """
        서킷 브레이커 체크 (긴급 중단 조건)

        Returns:
            (should_stop: bool, reason: str)
        """
        # 급격한 손실 체크 (일일 손실의 1.5배)
        if self.daily_pnl <= -(self.current_capital * self.daily_max_loss_pct * 1.5 / 100):
            return True, "긴급 중단: 일일 손실 1.5배 초과"

        # 급격한 낙폭 체크 (최대 낙폭의 1.2배)
        current_dd = self.get_current_drawdown()
        if current_dd >= self.max_drawdown_pct * 1.2:
            return True, f"긴급 중단: 최대 낙폭 1.2배 초과 ({current_dd:.2f}%)"

        # 자본 과도 손실 체크 (초기 자본의 20% 이하)
        if self.current_capital <= self.initial_capital * 0.8:
            return True, "긴급 중단: 자본 20% 이상 손실"

        return False, ""
