"""
Trade State Manager

목적:
- 당일 거래 상태 추적 (중복 진입 방지)
- 손절 종목 관리 (재매수 방지)
- Bottom 무효화 종목 관리
- 최고 수익률 기록 (성과 분석)
- Pending 진입 대기 관리

작성일: 2025-12-23
"""

from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from rich.console import Console

console = Console()


class TradeAction(Enum):
    """거래 행동 타입"""
    BUY = "BUY"
    SELL = "SELL"
    PARTIAL_SELL = "PARTIAL_SELL"
    STOP_LOSS = "STOP_LOSS"


class InvalidationReason(Enum):
    """무효화 사유"""
    SIGNAL_LOW_BREAK = "신호봉 저가 이탈"
    TIME_EXPIRED = "대기 시간 초과"
    TIME_WINDOW_EXIT = "진입 시간대 이탈"
    MANUAL = "수동 무효화"


@dataclass
class TradeRecord:
    """거래 기록"""
    timestamp: datetime
    stock_code: str
    stock_name: str
    action: TradeAction
    price: float
    quantity: int
    strategy_tag: str
    reason: Optional[str] = None


@dataclass
class StopLossRecord:
    """손절 기록"""
    timestamp: datetime
    stock_code: str
    stock_name: str
    entry_price: float
    exit_price: float
    loss_pct: float
    reason: str


@dataclass
class InvalidatedSignal:
    """무효화된 신호 기록"""
    timestamp: datetime
    stock_code: str
    stock_name: str
    strategy_tag: str
    reason: InvalidationReason
    signal_price: float
    invalidation_price: float


@dataclass
class PendingEntry:
    """Pending 진입 대기"""
    stock_code: str
    stock_name: str
    strategy_tag: str
    signal_time: datetime
    signal_price: float
    required_confirmations: int = 2  # 필요한 확인 캔들 수
    confirmations: int = 0           # 현재 확인된 캔들 수
    last_check_time: Optional[datetime] = None
    conditions_met: Dict[str, bool] = field(default_factory=dict)


class TradeStateManager:
    """
    거래 상태 관리자

    주요 기능:
    1. 당일 거래 이력 추적
    2. 손절 종목 관리
    3. Bottom 무효화 종목 관리
    4. 최고 수익률 기록
    5. Pending 진입 관리
    """

    def __init__(self):
        # 당일 거래 기록
        self.traded_today: Dict[str, List[TradeRecord]] = {}

        # 손절 기록
        self.stoploss_today: Dict[str, StopLossRecord] = {}

        # 무효화된 신호
        self.invalidated_signals: Dict[str, InvalidatedSignal] = {}

        # 최고 수익률 기록 (포지션별)
        self.max_profit_reached: Dict[str, float] = {}

        # Pending 진입 대기
        self.pending_entries: Dict[str, PendingEntry] = {}

        # 현재 날짜 (리셋 체크용)
        self.current_date = datetime.now().date()

        console.print("[dim]✓ TradeStateManager 초기화 완료[/dim]")

    # ==========================================
    # 1. 진입 가능 여부 체크
    # ==========================================

    def can_enter(
        self,
        stock_code: str,
        strategy_tag: str,
        check_stoploss: bool = True,
        check_invalidated: bool = True,
        check_traded: bool = True
    ) -> Tuple[bool, str]:
        """
        진입 가능 여부 체크

        Args:
            stock_code: 종목 코드
            strategy_tag: 전략 태그
            check_stoploss: 손절 체크 여부
            check_invalidated: 무효화 체크 여부 (Bottom 전략)
            check_traded: 당일 거래 체크 여부

        Returns:
            (can_enter, reason)
        """
        # 날짜 체크 및 리셋
        self._check_and_reset_daily()

        # 1. 손절 종목 체크
        if check_stoploss and stock_code in self.stoploss_today:
            record = self.stoploss_today[stock_code]
            return False, f"손절 종목 (${record.exit_price:,.0f}에서 -{record.loss_pct:.2f}%)"

        # 2. 무효화된 신호 체크 (Bottom 전략)
        if check_invalidated and stock_code in self.invalidated_signals:
            signal = self.invalidated_signals[stock_code]
            return False, f"무효화된 신호 ({signal.reason.value})"

        # 3. 당일 거래 이력 체크
        if check_traded and stock_code in self.traded_today:
            trades = self.traded_today[stock_code]
            buy_count = sum(1 for t in trades if t.action == TradeAction.BUY)

            # 전략별 당일 진입 제한
            if strategy_tag == "bottom_pullback":
                # Bottom 전략: 1회만 허용
                if buy_count >= 1:
                    return False, f"Bottom 전략 당일 진입 제한 (이미 {buy_count}회 진입)"
            elif strategy_tag.startswith("momentum"):
                # Momentum 전략: 2회까지 허용
                if buy_count >= 2:
                    return False, f"Momentum 전략 당일 진입 제한 (이미 {buy_count}회 진입)"

        return True, "진입 가능"

    # ==========================================
    # 2. 거래 기록
    # ==========================================

    def mark_traded(
        self,
        stock_code: str,
        stock_name: str,
        action: TradeAction,
        price: float,
        quantity: int,
        strategy_tag: str,
        reason: Optional[str] = None
    ):
        """거래 기록"""
        record = TradeRecord(
            timestamp=datetime.now(),
            stock_code=stock_code,
            stock_name=stock_name,
            action=action,
            price=price,
            quantity=quantity,
            strategy_tag=strategy_tag,
            reason=reason
        )

        if stock_code not in self.traded_today:
            self.traded_today[stock_code] = []

        self.traded_today[stock_code].append(record)

        console.print(
            f"[dim]✓ 거래 기록: {stock_name} ({stock_code}) "
            f"{action.value} {quantity}주 @ {price:,.0f}원[/dim]"
        )

    def mark_stoploss(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        exit_price: float,
        reason: str = "손절 조건 충족"
    ):
        """손절 기록"""
        loss_pct = ((exit_price - entry_price) / entry_price) * 100

        record = StopLossRecord(
            timestamp=datetime.now(),
            stock_code=stock_code,
            stock_name=stock_name,
            entry_price=entry_price,
            exit_price=exit_price,
            loss_pct=loss_pct,
            reason=reason
        )

        self.stoploss_today[stock_code] = record

        # 거래 기록에도 추가
        self.mark_traded(
            stock_code=stock_code,
            stock_name=stock_name,
            action=TradeAction.STOP_LOSS,
            price=exit_price,
            quantity=0,  # 수량은 별도 관리
            strategy_tag="stop_loss",
            reason=reason
        )

        console.print()
        console.print("=" * 80, style="bold red")
        console.print(
            f"🛑 손절 기록: {stock_name} ({stock_code})",
            style="bold red"
        )
        console.print("=" * 80, style="bold red")
        console.print(f"  진입가: {entry_price:,.0f}원")
        console.print(f"  청산가: {exit_price:,.0f}원")
        console.print(f"  손실률: {loss_pct:+.2f}%")
        console.print(f"  사유: {reason}")
        console.print()

    def mark_invalidated(
        self,
        stock_code: str,
        stock_name: str,
        strategy_tag: str,
        reason: InvalidationReason,
        signal_price: float,
        invalidation_price: float
    ):
        """무효화된 신호 기록"""
        record = InvalidatedSignal(
            timestamp=datetime.now(),
            stock_code=stock_code,
            stock_name=stock_name,
            strategy_tag=strategy_tag,
            reason=reason,
            signal_price=signal_price,
            invalidation_price=invalidation_price
        )

        self.invalidated_signals[stock_code] = record

        console.print()
        console.print("=" * 80, style="bold yellow")
        console.print(
            f"⚠️  신호 무효화: {stock_name} ({stock_code})",
            style="bold yellow"
        )
        console.print("=" * 80, style="bold yellow")
        console.print(f"  전략: {strategy_tag}")
        console.print(f"  사유: {reason.value}")
        console.print(f"  신호가: {signal_price:,.0f}원")
        console.print(f"  무효화가: {invalidation_price:,.0f}원")
        console.print()

    # ==========================================
    # 3. 최고 수익률 추적
    # ==========================================

    def update_max_profit(self, stock_code: str, current_profit_pct: float):
        """최고 수익률 업데이트"""
        if stock_code not in self.max_profit_reached:
            self.max_profit_reached[stock_code] = current_profit_pct
        else:
            if current_profit_pct > self.max_profit_reached[stock_code]:
                self.max_profit_reached[stock_code] = current_profit_pct

    def get_max_profit(self, stock_code: str) -> float:
        """최고 수익률 조회"""
        return self.max_profit_reached.get(stock_code, 0.0)

    # ==========================================
    # 4. Pending 진입 관리
    # ==========================================

    def add_pending_entry(
        self,
        stock_code: str,
        stock_name: str,
        strategy_tag: str,
        signal_price: float,
        required_confirmations: int = 2
    ):
        """Pending 진입 추가"""
        pending = PendingEntry(
            stock_code=stock_code,
            stock_name=stock_name,
            strategy_tag=strategy_tag,
            signal_time=datetime.now(),
            signal_price=signal_price,
            required_confirmations=required_confirmations,
            confirmations=0
        )

        self.pending_entries[stock_code] = pending

        console.print(
            f"[yellow]⏳ Pending 진입 등록: {stock_name} ({stock_code}) "
            f"@ {signal_price:,.0f}원 (확인 필요: {required_confirmations}캔들)[/yellow]"
        )

    def update_pending_confirmation(
        self,
        stock_code: str,
        conditions_met: Dict[str, bool]
    ) -> Tuple[bool, str]:
        """
        Pending 진입 확인 업데이트

        Args:
            stock_code: 종목 코드
            conditions_met: 조건 충족 여부
                {
                    'price_maintained': True,
                    'volume_confirmed': True,
                    'vwap_above': True
                }

        Returns:
            (ready_to_enter, reason)
        """
        if stock_code not in self.pending_entries:
            return False, "Pending 진입 없음"

        pending = self.pending_entries[stock_code]
        pending.last_check_time = datetime.now()
        pending.conditions_met = conditions_met

        # 모든 조건 충족 확인
        all_met = all(conditions_met.values())

        if all_met:
            pending.confirmations += 1

            console.print(
                f"[green]✓ {pending.stock_name} ({stock_code}): "
                f"확인 {pending.confirmations}/{pending.required_confirmations}[/green]"
            )

            # 필요한 확인 완료
            if pending.confirmations >= pending.required_confirmations:
                console.print()
                console.print("=" * 80, style="bold green")
                console.print(
                    f"✅ Pending 진입 확정: {pending.stock_name} ({stock_code})",
                    style="bold green"
                )
                console.print("=" * 80, style="bold green")
                console.print(f"  신호가: {pending.signal_price:,.0f}원")
                console.print(f"  확인 완료: {pending.confirmations}캔들")
                console.print(f"  전략: {pending.strategy_tag}")
                console.print()

                return True, "진입 확정"
        else:
            # 조건 미충족 - 확인 카운트 리셋
            if pending.confirmations > 0:
                console.print(
                    f"[yellow]⚠️  {pending.stock_name} ({stock_code}): "
                    f"조건 미충족, 확인 카운트 리셋[/yellow]"
                )
            pending.confirmations = 0

        return False, f"확인 중 ({pending.confirmations}/{pending.required_confirmations})"

    def remove_pending(self, stock_code: str, reason: str = "진입 완료"):
        """Pending 진입 제거"""
        if stock_code in self.pending_entries:
            pending = self.pending_entries[stock_code]
            del self.pending_entries[stock_code]

            console.print(
                f"[dim]✓ Pending 제거: {pending.stock_name} ({stock_code}) - {reason}[/dim]"
            )

    def cleanup_expired_pending(self, timeout_minutes: int = 30):
        """만료된 Pending 진입 정리"""
        now = datetime.now()
        expired = []

        for stock_code, pending in self.pending_entries.items():
            elapsed = (now - pending.signal_time).total_seconds() / 60
            if elapsed > timeout_minutes:
                expired.append(stock_code)

        for stock_code in expired:
            pending = self.pending_entries[stock_code]
            console.print(
                f"[yellow]⏰ Pending 만료: {pending.stock_name} ({stock_code}) "
                f"- {timeout_minutes}분 초과[/yellow]"
            )
            del self.pending_entries[stock_code]

    # ==========================================
    # 5. 조회 메서드
    # ==========================================

    def get_trade_count(self, stock_code: str) -> int:
        """당일 거래 건수"""
        if stock_code not in self.traded_today:
            return 0
        return len(self.traded_today[stock_code])

    def get_buy_count(self, stock_code: str) -> int:
        """당일 매수 건수"""
        if stock_code not in self.traded_today:
            return 0
        return sum(1 for t in self.traded_today[stock_code] if t.action == TradeAction.BUY)

    def is_stoploss_today(self, stock_code: str) -> bool:
        """당일 손절 여부"""
        return stock_code in self.stoploss_today

    def is_invalidated(self, stock_code: str) -> bool:
        """무효화 여부"""
        return stock_code in self.invalidated_signals

    def is_pending(self, stock_code: str) -> bool:
        """Pending 대기 여부"""
        return stock_code in self.pending_entries

    def get_pending_info(self, stock_code: str) -> Optional[PendingEntry]:
        """Pending 정보 조회"""
        return self.pending_entries.get(stock_code)

    # ==========================================
    # 6. 리셋 및 관리
    # ==========================================

    def _check_and_reset_daily(self):
        """날짜 변경 체크 및 리셋"""
        today = datetime.now().date()

        if today != self.current_date:
            self.reset_daily()
            self.current_date = today

    def reset_daily(self):
        """일일 리셋"""
        console.print()
        console.print("=" * 80, style="bold cyan")
        console.print("🔄 TradeStateManager 일일 리셋", style="bold cyan")
        console.print("=" * 80, style="bold cyan")

        # 통계 출력
        total_trades = sum(len(trades) for trades in self.traded_today.values())
        total_stoploss = len(self.stoploss_today)
        total_invalidated = len(self.invalidated_signals)
        total_pending = len(self.pending_entries)

        console.print(f"  거래 기록: {total_trades}건")
        console.print(f"  손절 기록: {total_stoploss}건")
        console.print(f"  무효화 신호: {total_invalidated}건")
        console.print(f"  Pending 대기: {total_pending}건 (만료 처리)")
        console.print()

        # 리셋
        self.traded_today.clear()
        self.stoploss_today.clear()
        self.invalidated_signals.clear()
        self.pending_entries.clear()
        self.max_profit_reached.clear()

    def get_daily_stats(self) -> Dict:
        """일일 통계"""
        return {
            'total_trades': sum(len(trades) for trades in self.traded_today.values()),
            'unique_stocks': len(self.traded_today),
            'buy_count': sum(
                sum(1 for t in trades if t.action == TradeAction.BUY)
                for trades in self.traded_today.values()
            ),
            'sell_count': sum(
                sum(1 for t in trades if t.action in [TradeAction.SELL, TradeAction.PARTIAL_SELL])
                for trades in self.traded_today.values()
            ),
            'stoploss_count': len(self.stoploss_today),
            'invalidated_count': len(self.invalidated_signals),
            'pending_count': len(self.pending_entries)
        }

    def print_summary(self):
        """상태 요약 출력"""
        stats = self.get_daily_stats()

        console.print()
        console.print("=" * 80, style="bold cyan")
        console.print("📊 TradeStateManager 상태 요약", style="bold cyan")
        console.print("=" * 80, style="bold cyan")
        console.print(f"  총 거래: {stats['total_trades']}건")
        console.print(f"  고유 종목: {stats['unique_stocks']}개")
        console.print(f"  매수: {stats['buy_count']}건 | 매도: {stats['sell_count']}건")
        console.print(f"  손절: {stats['stoploss_count']}건")
        console.print(f"  무효화: {stats['invalidated_count']}건")
        console.print(f"  Pending: {stats['pending_count']}건")
        console.print()
