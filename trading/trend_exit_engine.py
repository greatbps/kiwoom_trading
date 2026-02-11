"""
Trend Exit Engine - Daily Squeeze 기반 중기 전략 청산 로직
==========================================================

B(Pro) 규칙 기반:
- SELL: Momentum 음전 + 기울기 하락 (둘 다 충족 시)
- Momentum 둔화 (양수지만 감소): 무시 (HOLD)
- Squeeze ON: 무조건 HOLD
- 재진입: Momentum 양전환 시 즉시

핵심 철학:
"손절보다 '유지 조건'을 먼저 본다"
"추세의 주도권은 시장에게, 인간은 의심하지 않는 쪽에 베팅"
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from dotenv import load_dotenv
from pathlib import Path

# 환경변수 로드
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# 로거 설정
logger = logging.getLogger(__name__)


class TrendExitAction(Enum):
    """청산 액션 타입"""
    HOLD = "hold"                          # 유지
    EXIT_ALL = "exit_all"                  # 전량 청산
    ACTIVATE_TRAILING = "activate_trail"   # 트레일링 활성화
    TIGHTEN_TRAILING = "tighten_trail"     # 트레일링 강화


class TrendExitReason(Enum):
    """청산 사유"""
    NONE = "none"
    MOMENTUM_REVERSAL = "momentum_reversal"           # 모멘텀 음전 + 기울기 하락
    HTF_STRUCTURE_BREAK = "htf_structure_break"       # HTF 구조 붕괴
    TIMEOUT = "timeout"                               # 시간 초과 (20거래일)
    PROFIT_TARGET = "profit_target"                   # 목표 수익 달성
    TRAILING_STOP = "trailing_stop"                   # 트레일링 스탑
    MANUAL = "manual"                                 # 수동


@dataclass
class TrendPosition:
    """중기 포지션 정보"""
    symbol: str
    stock_name: str
    entry_price: float
    entry_time: datetime
    quantity: int
    account: str

    # 현재 상태
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    max_profit_pct: float = 0.0      # 최고 수익률 (트레일링용)
    holding_days: int = 0

    # Daily Squeeze 상태
    squeeze_on: bool = True
    momentum: float = 0.0
    momentum_prev: float = 0.0       # 전일 모멘텀
    momentum_slope: float = 0.0

    # 트레일링 상태
    trailing_active: bool = False
    trailing_stop_price: float = 0.0
    trailing_atr_multiplier: float = 3.0  # 초기 ATR 배수

    # 메타데이터
    intent: str = "squeeze_trend"
    entry_reason: str = ""


@dataclass
class TrendExitConfig:
    """Trend Exit 설정"""
    # 청산 조건
    min_holding_days: int = 0                    # 최소 보유일 (0 = 당일 청산 가능)
    max_holding_days: int = 20                   # 최대 보유일 (타임아웃)

    # 모멘텀 청산 조건
    momentum_threshold: float = 0.0              # 이 값 미만이면 음전 판정
    slope_threshold: float = 0.0                 # 이 값 미만이면 하락 판정

    # 트레일링 설정
    trailing_activation_pct: float = 4.0         # 트레일링 활성화 수익률 (%)
    trailing_atr_initial: float = 3.0            # 초기 ATR 배수
    trailing_atr_tight: float = 2.0              # 타이트 ATR 배수
    trailing_tighten_pct: float = 8.0            # 타이트 전환 수익률 (%)

    # 손절 설정 (비상용)
    hard_stop_pct: float = -10.0                 # 하드 손절 (비상시에만)


class TrendExitEngine:
    """
    중기 전략용 Exit Engine

    Daily Squeeze Momentum Pro 기반 청산 로직
    """

    def __init__(self, config: Optional[TrendExitConfig] = None):
        self.config = config or TrendExitConfig()
        self.positions: Dict[str, TrendPosition] = {}  # symbol -> position
        self.exit_log: List[Dict[str, Any]] = []

        logger.info("TrendExitEngine 초기화 완료")
        logger.info(f"  최대 보유일: {self.config.max_holding_days}")
        logger.info(f"  트레일링 활성화: +{self.config.trailing_activation_pct}%")

    def add_position(self, position: TrendPosition):
        """포지션 추가"""
        self.positions[position.symbol] = position
        logger.info(f"[TrendExit] 포지션 추가: {position.stock_name} @ {position.entry_price:,}원")

    def remove_position(self, symbol: str) -> Optional[TrendPosition]:
        """포지션 제거"""
        return self.positions.pop(symbol, None)

    def update_position(self, symbol: str, **kwargs):
        """포지션 상태 업데이트"""
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        for key, value in kwargs.items():
            if hasattr(pos, key):
                setattr(pos, key, value)

        # 수익률 자동 계산
        if pos.current_price > 0 and pos.entry_price > 0:
            pos.unrealized_pnl_pct = ((pos.current_price - pos.entry_price) / pos.entry_price) * 100
            pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.quantity

            # 최고 수익률 갱신
            if pos.unrealized_pnl_pct > pos.max_profit_pct:
                pos.max_profit_pct = pos.unrealized_pnl_pct

    def check_exit(self, symbol: str) -> Tuple[TrendExitAction, TrendExitReason, str]:
        """
        청산 조건 체크 (핵심 메서드)

        Returns:
            (action, reason, description)
        """
        if symbol not in self.positions:
            return TrendExitAction.HOLD, TrendExitReason.NONE, "포지션 없음"

        pos = self.positions[symbol]

        # 1. Squeeze ON 이면 무조건 HOLD (최우선)
        if pos.squeeze_on:
            # 단, 트레일링 체크는 함
            if pos.trailing_active:
                trail_result = self._check_trailing_stop(pos)
                if trail_result[0] != TrendExitAction.HOLD:
                    return trail_result

            # 수익률에 따른 트레일링 활성화 체크
            trail_activation = self._check_trailing_activation(pos)
            if trail_activation[0] != TrendExitAction.HOLD:
                return trail_activation

            return TrendExitAction.HOLD, TrendExitReason.NONE, f"Squeeze ON 유지 (Mom:{pos.momentum:.2f})"

        # 2. 모멘텀 음전 + 기울기 하락 체크 (핵심 청산 조건)
        if self._is_momentum_reversal(pos):
            desc = f"Mom음전({pos.momentum:.2f})+기울기하락({pos.momentum_slope:.3f})"
            self._log_exit(pos, TrendExitReason.MOMENTUM_REVERSAL, desc)
            return TrendExitAction.EXIT_ALL, TrendExitReason.MOMENTUM_REVERSAL, desc

        # 3. HTF 구조 붕괴 체크
        # (이 부분은 외부에서 htf_structure 업데이트 필요)

        # 4. 타임아웃 체크
        if pos.holding_days >= self.config.max_holding_days:
            desc = f"보유기간 {pos.holding_days}일 초과"
            self._log_exit(pos, TrendExitReason.TIMEOUT, desc)
            return TrendExitAction.EXIT_ALL, TrendExitReason.TIMEOUT, desc

        # 5. 비상 하드 손절 체크
        if pos.unrealized_pnl_pct <= self.config.hard_stop_pct:
            desc = f"비상손절 {pos.unrealized_pnl_pct:.2f}%"
            self._log_exit(pos, TrendExitReason.TRAILING_STOP, desc)
            return TrendExitAction.EXIT_ALL, TrendExitReason.TRAILING_STOP, desc

        # 6. 트레일링 스탑 체크
        if pos.trailing_active:
            trail_result = self._check_trailing_stop(pos)
            if trail_result[0] != TrendExitAction.HOLD:
                return trail_result

        # 7. 트레일링 활성화 체크
        trail_activation = self._check_trailing_activation(pos)
        if trail_activation[0] != TrendExitAction.HOLD:
            return trail_activation

        # 기본: HOLD
        return TrendExitAction.HOLD, TrendExitReason.NONE, "조건 충족 없음 (HOLD)"

    def _is_momentum_reversal(self, pos: TrendPosition) -> bool:
        """
        모멘텀 역전 조건 체크

        B(Pro) 핵심 규칙:
        - Momentum < 0 (음전)
        - AND 기울기 < 0 (하락)
        - 둘 다 충족해야 청산
        """
        momentum_negative = pos.momentum < self.config.momentum_threshold
        slope_declining = pos.momentum_slope < self.config.slope_threshold

        return momentum_negative and slope_declining

    def _check_trailing_activation(self, pos: TrendPosition) -> Tuple[TrendExitAction, TrendExitReason, str]:
        """트레일링 활성화 체크"""
        if pos.trailing_active:
            # 타이트닝 체크
            if pos.unrealized_pnl_pct >= self.config.trailing_tighten_pct:
                if pos.trailing_atr_multiplier > self.config.trailing_atr_tight:
                    pos.trailing_atr_multiplier = self.config.trailing_atr_tight
                    return TrendExitAction.TIGHTEN_TRAILING, TrendExitReason.NONE, \
                           f"트레일링 타이트닝 (+{pos.unrealized_pnl_pct:.2f}%)"
        else:
            # 활성화 체크
            if pos.unrealized_pnl_pct >= self.config.trailing_activation_pct:
                pos.trailing_active = True
                pos.trailing_atr_multiplier = self.config.trailing_atr_initial
                return TrendExitAction.ACTIVATE_TRAILING, TrendExitReason.NONE, \
                       f"트레일링 활성화 (+{pos.unrealized_pnl_pct:.2f}%)"

        return TrendExitAction.HOLD, TrendExitReason.NONE, ""

    def _check_trailing_stop(self, pos: TrendPosition) -> Tuple[TrendExitAction, TrendExitReason, str]:
        """트레일링 스탑 체크"""
        if not pos.trailing_active or pos.trailing_stop_price <= 0:
            return TrendExitAction.HOLD, TrendExitReason.NONE, ""

        if pos.current_price <= pos.trailing_stop_price:
            desc = f"트레일링스탑 ({pos.trailing_stop_price:,.0f}원)"
            self._log_exit(pos, TrendExitReason.TRAILING_STOP, desc)
            return TrendExitAction.EXIT_ALL, TrendExitReason.TRAILING_STOP, desc

        return TrendExitAction.HOLD, TrendExitReason.NONE, ""

    def _log_exit(self, pos: TrendPosition, reason: TrendExitReason, desc: str):
        """청산 로그 기록"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": pos.symbol,
            "stock_name": pos.stock_name,
            "entry_price": pos.entry_price,
            "exit_price": pos.current_price,
            "profit_pct": pos.unrealized_pnl_pct,
            "max_profit_pct": pos.max_profit_pct,
            "holding_days": pos.holding_days,
            "reason": reason.value,
            "description": desc,
            "squeeze_on": pos.squeeze_on,
            "momentum": pos.momentum,
            "momentum_slope": pos.momentum_slope
        }
        self.exit_log.append(log_entry)
        logger.info(f"[TrendExit] 청산: {pos.stock_name} | {reason.value} | {desc}")

    def get_position(self, symbol: str) -> Optional[TrendPosition]:
        """포지션 조회"""
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[TrendPosition]:
        """전체 포지션 목록"""
        return list(self.positions.values())

    def get_exit_summary(self) -> Dict[str, Any]:
        """청산 현황 요약"""
        if not self.exit_log:
            return {"total": 0}

        profits = [log["profit_pct"] for log in self.exit_log]
        reasons = {}
        for log in self.exit_log:
            r = log["reason"]
            reasons[r] = reasons.get(r, 0) + 1

        return {
            "total": len(self.exit_log),
            "avg_profit": sum(profits) / len(profits),
            "max_profit": max(profits),
            "min_profit": min(profits),
            "by_reason": reasons
        }


class TrendPositionManager:
    """
    중기 포지션 관리자

    TrendExitEngine을 활용한 실제 포지션 관리
    """

    def __init__(self, exit_engine: Optional[TrendExitEngine] = None):
        self.exit_engine = exit_engine or TrendExitEngine()
        self.account = os.getenv('KIWOOM_TREND_ACCOUNT', '5202-2235')

    def open_position(self, symbol: str, stock_name: str, price: float,
                      quantity: int, intent: str = "squeeze_trend",
                      entry_reason: str = "") -> TrendPosition:
        """포지션 오픈"""
        position = TrendPosition(
            symbol=symbol,
            stock_name=stock_name,
            entry_price=price,
            entry_time=datetime.now(),
            quantity=quantity,
            account=self.account,
            current_price=price,
            intent=intent,
            entry_reason=entry_reason
        )
        self.exit_engine.add_position(position)
        return position

    def update_daily_data(self, symbol: str, squeeze_on: bool, momentum: float,
                          momentum_prev: float, current_price: float,
                          holding_days: int, atr: float = 0.0):
        """
        일일 데이터 업데이트 (EOD 평가용)

        매일 장 마감 후 호출하여 Squeeze 상태 및 모멘텀 업데이트
        """
        # 기울기 계산
        momentum_slope = momentum - momentum_prev if momentum_prev != 0 else 0

        self.exit_engine.update_position(
            symbol,
            squeeze_on=squeeze_on,
            momentum=momentum,
            momentum_prev=momentum_prev,
            momentum_slope=momentum_slope,
            current_price=current_price,
            holding_days=holding_days
        )

        # 트레일링 스탑 가격 업데이트
        pos = self.exit_engine.get_position(symbol)
        if pos and pos.trailing_active and atr > 0:
            # 최고가 기준 트레일링 스탑
            high_price = pos.entry_price * (1 + pos.max_profit_pct / 100)
            pos.trailing_stop_price = high_price - (atr * pos.trailing_atr_multiplier)

    def evaluate_position(self, symbol: str) -> Dict[str, Any]:
        """
        포지션 평가 및 청산 결정

        Returns:
            {"action": ..., "reason": ..., "description": ...}
        """
        action, reason, desc = self.exit_engine.check_exit(symbol)
        return {
            "action": action.value,
            "reason": reason.value,
            "description": desc
        }

    def close_position(self, symbol: str, reason: str = "") -> Optional[TrendPosition]:
        """포지션 청산"""
        return self.exit_engine.remove_position(symbol)

    def get_daily_report(self) -> str:
        """일일 리포트 생성"""
        positions = self.exit_engine.get_all_positions()
        if not positions:
            return "보유 포지션 없음"

        lines = [
            "=" * 60,
            "📊 중기 포지션 일일 리포트",
            "=" * 60,
            f"계좌: {self.account}",
            f"포지션 수: {len(positions)}",
            "-" * 60
        ]

        for pos in positions:
            status = "🟢 Squeeze ON" if pos.squeeze_on else "🔴 Squeeze OFF"
            trailing = "🎯 트레일링 활성" if pos.trailing_active else ""

            lines.extend([
                f"\n{pos.stock_name} ({pos.symbol})",
                f"  진입: {pos.entry_price:,}원 | 현재: {pos.current_price:,}원",
                f"  수익률: {pos.unrealized_pnl_pct:+.2f}% | 최고: {pos.max_profit_pct:+.2f}%",
                f"  보유일: D+{pos.holding_days}",
                f"  {status} | Mom: {pos.momentum:.2f} | Slope: {pos.momentum_slope:.3f}",
                f"  {trailing}" if trailing else ""
            ])

        # 청산 요약
        summary = self.exit_engine.get_exit_summary()
        if summary["total"] > 0:
            lines.extend([
                "-" * 60,
                "📈 청산 현황",
                f"  총 청산: {summary['total']}건",
                f"  평균 수익: {summary['avg_profit']:.2f}%",
                f"  사유별: {summary['by_reason']}"
            ])

        lines.append("=" * 60)
        return "\n".join(lines)


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Trend Exit Engine - Test")
    print("=" * 60)

    # 매니저 초기화
    manager = TrendPositionManager()
    print(f"\n계좌: {manager.account}")

    # 테스트 포지션 오픈
    pos = manager.open_position(
        symbol="240810",
        stock_name="원익IPS",
        price=103500,
        quantity=10,
        entry_reason="Squeeze Trend 진입"
    )
    print(f"\n포지션 오픈: {pos.stock_name} @ {pos.entry_price:,}원")

    # 시나리오 1: Squeeze ON + 모멘텀 양수 → HOLD
    print("\n--- 시나리오 1: Squeeze ON ---")
    manager.update_daily_data(
        symbol="240810",
        squeeze_on=True,
        momentum=0.5,
        momentum_prev=0.4,
        current_price=108000,
        holding_days=3
    )
    result = manager.evaluate_position("240810")
    print(f"  결과: {result}")

    # 시나리오 2: 수익 +5% → 트레일링 활성화
    print("\n--- 시나리오 2: 수익 +5% ---")
    manager.update_daily_data(
        symbol="240810",
        squeeze_on=True,
        momentum=0.7,
        momentum_prev=0.5,
        current_price=108700,  # +5%
        holding_days=5,
        atr=2000
    )
    result = manager.evaluate_position("240810")
    print(f"  결과: {result}")

    # 시나리오 3: Squeeze OFF + 모멘텀 음전 + 기울기 하락 → 청산
    print("\n--- 시나리오 3: 모멘텀 역전 ---")
    manager.update_daily_data(
        symbol="240810",
        squeeze_on=False,
        momentum=-0.2,
        momentum_prev=0.1,
        current_price=106000,
        holding_days=7
    )
    result = manager.evaluate_position("240810")
    print(f"  결과: {result}")

    # 일일 리포트
    print("\n" + manager.get_daily_report())

    print("\n" + "=" * 60)
    print("Test Completed!")
    print("=" * 60)
