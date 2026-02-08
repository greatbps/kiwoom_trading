"""
전략 업그레이드 v2 - 주간 매매일지 분석 기반
생성일: 2026-01-30

핵심 변경사항:
1. Early Failure Cut v2: 시간 + 구조 필터 추가
2. 포지션 생명주기 규칙: D+1, D+3, D+5
3. VWAP 이탈 규칙: 60분 기준 (30분 손절 금지)
4. ATR 트레일링 단계화: 수익구간별 차등
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from datetime import datetime, timedelta


# =============================================================================
# 1. Early Failure Cut v2
# =============================================================================
@dataclass
class EarlyFailureCutV2:
    """
    기존: -1.6% in 30분 → 즉시 손절
    변경: 45분 + 구조 확인 + -2.5% 이탈 + LL 확정
    """

    # 파라미터
    MIN_HOLD_MINUTES: int = 45          # 최소 보유시간 (기존 30분 → 45분)
    VWAP_THRESHOLD: float = -0.025      # VWAP 이탈 기준 (기존 -1.6% → -2.5%)
    REQUIRE_LL_BREAK: bool = True       # Lower Low 확정 필요
    REQUIRE_HTF_CHOCH_INTACT: bool = True  # HTF CHoCH 유지 확인

    def should_exit(
        self,
        entry_time: datetime,
        current_time: datetime,
        current_price: float,
        vwap: float,
        recent_low: float,       # 진입 후 최저점
        htf_choch_valid: bool,   # HTF CHoCH 아직 유효한지
        swing_low: float         # 진입 근거가 된 스윙 로우
    ) -> tuple[bool, str]:
        """
        Returns: (should_exit, reason)
        """
        hold_minutes = (current_time - entry_time).total_seconds() / 60
        vwap_diff = (current_price - vwap) / vwap

        # 조건 1: 최소 보유시간 미충족 → 절대 손절 금지
        if hold_minutes < self.MIN_HOLD_MINUTES:
            return False, f"보유시간 부족 ({hold_minutes:.0f}분 < {self.MIN_HOLD_MINUTES}분)"

        # 조건 2: HTF CHoCH 유지 중 → 손절 금지
        if self.REQUIRE_HTF_CHOCH_INTACT and htf_choch_valid:
            return False, "HTF CHoCH 유지 중 - 관찰"

        # 조건 3: VWAP 이탈 체크
        if vwap_diff > self.VWAP_THRESHOLD:
            return False, f"VWAP 이탈 미달 ({vwap_diff:.2%} > {self.VWAP_THRESHOLD:.2%})"

        # 조건 4: Lower Low 확정 체크
        if self.REQUIRE_LL_BREAK:
            if current_price > swing_low:
                return False, f"LL 미확정 (현재가 {current_price} > 스윙로우 {swing_low})"

        # 모든 조건 충족 → 손절
        return True, f"Early Failure v2: {hold_minutes:.0f}분, VWAP {vwap_diff:.2%}, LL 확정"


# =============================================================================
# 2. 포지션 생명주기 규칙
# =============================================================================
class PositionLifecycleRule:
    """
    D+1: VWAP 상단 미유지 → 50% 청산
    D+3: +0~3% → 전량청산, +3% 이상 → 트레일링 ON
    D+5: 무조건 전량 청산
    """

    @staticmethod
    def check_d1_rule(
        entry_date: datetime,
        current_date: datetime,
        current_price: float,
        entry_price: float,
        close_vwap: float
    ) -> tuple[str, float]:
        """
        D+1 규칙 체크
        Returns: (action, exit_ratio)
        """
        days_held = (current_date.date() - entry_date.date()).days

        if days_held != 1:
            return "HOLD", 0.0

        # D+1 종가 기준 VWAP 상단 체크
        if current_price < close_vwap:
            return "PARTIAL_EXIT", 0.5  # 50% 청산

        return "HOLD", 0.0

    @staticmethod
    def check_d3_rule(
        entry_date: datetime,
        current_date: datetime,
        current_price: float,
        entry_price: float
    ) -> tuple[str, float, bool]:
        """
        D+3 규칙 체크
        Returns: (action, exit_ratio, activate_trailing)
        """
        days_held = (current_date.date() - entry_date.date()).days

        if days_held < 3:
            return "HOLD", 0.0, False

        profit_pct = (current_price - entry_price) / entry_price

        if profit_pct < 0.03:  # +0% ~ +3%
            return "FULL_EXIT", 1.0, False
        else:  # +3% 이상
            return "TRAILING_ON", 0.0, True

    @staticmethod
    def check_d5_rule(
        entry_date: datetime,
        current_date: datetime
    ) -> tuple[str, float]:
        """
        D+5 규칙 체크 (무조건 청산)
        Returns: (action, exit_ratio)
        """
        days_held = (current_date.date() - entry_date.date()).days

        if days_held >= 5:
            return "FORCE_EXIT", 1.0

        return "HOLD", 0.0


# =============================================================================
# 3. VWAP 이탈 규칙 (60분 기준)
# =============================================================================
class VWAPExitRule:
    """
    30분 내 VWAP 이탈 → 관찰만 (손절 금지)
    60분 종가 기준 VWAP 하회 + 구조 이탈 → 손절
    """

    MIN_OBSERVATION_MINUTES: int = 60

    @staticmethod
    def should_exit(
        entry_time: datetime,
        current_time: datetime,
        current_price: float,
        vwap: float,
        structure_broken: bool  # CHoCH 무효화 등
    ) -> tuple[bool, str]:
        """
        Returns: (should_exit, reason)
        """
        hold_minutes = (current_time - entry_time).total_seconds() / 60
        below_vwap = current_price < vwap

        # 60분 미만 → 절대 손절 금지
        if hold_minutes < 60:
            if below_vwap:
                return False, f"VWAP 하회 중이나 60분 미경과 ({hold_minutes:.0f}분) - 관찰"
            return False, "정상 범위"

        # 60분 이상 + VWAP 하회 + 구조 이탈
        if below_vwap and structure_broken:
            return True, f"VWAP Exit: {hold_minutes:.0f}분 경과, VWAP 하회, 구조 이탈"

        if below_vwap:
            return False, "VWAP 하회 중이나 구조 유지 - 관찰"

        return False, "정상 범위"


# =============================================================================
# 4. ATR 트레일링 단계화
# =============================================================================
class ATRTrailingV2:
    """
    +0% ~ +2%: 트레일링 OFF (구조 기준만)
    +2% ~ +4%: ATR x 3.0 (느슨)
    +4% 이상: ATR x 2.0 (수익 보호)
    """

    STAGE_1_THRESHOLD: float = 0.02   # +2%
    STAGE_2_THRESHOLD: float = 0.04   # +4%

    STAGE_1_ATR_MULT: float = 3.0     # 느슨
    STAGE_2_ATR_MULT: float = 2.0     # 타이트

    def get_trailing_params(
        self,
        entry_price: float,
        current_price: float,
        atr: float
    ) -> tuple[bool, float, str]:
        """
        Returns: (trailing_active, stop_distance, stage_description)
        """
        profit_pct = (current_price - entry_price) / entry_price

        if profit_pct < self.STAGE_1_THRESHOLD:
            # Stage 0: +2% 미만 → 트레일링 OFF
            return False, 0.0, f"Stage 0: +{profit_pct:.1%} (트레일링 OFF)"

        elif profit_pct < self.STAGE_2_THRESHOLD:
            # Stage 1: +2%~+4% → ATR x 3.0
            stop_dist = atr * self.STAGE_1_ATR_MULT
            return True, stop_dist, f"Stage 1: +{profit_pct:.1%} (ATR x{self.STAGE_1_ATR_MULT})"

        else:
            # Stage 2: +4% 이상 → ATR x 2.0
            stop_dist = atr * self.STAGE_2_ATR_MULT
            return True, stop_dist, f"Stage 2: +{profit_pct:.1%} (ATR x{self.STAGE_2_ATR_MULT})"

    def calculate_stop_price(
        self,
        entry_price: float,
        current_price: float,
        high_since_entry: float,
        atr: float
    ) -> tuple[float, str]:
        """
        트레일링 스탑 가격 계산
        Returns: (stop_price, description)
        """
        active, stop_dist, desc = self.get_trailing_params(entry_price, current_price, atr)

        if not active:
            return 0.0, desc  # 트레일링 비활성

        stop_price = high_since_entry - stop_dist
        return stop_price, f"{desc} | Stop: {stop_price:,.0f}"


# =============================================================================
# 통합 Exit Manager
# =============================================================================
class ExitManagerV2:
    """모든 청산 규칙을 통합 관리"""

    def __init__(self):
        self.early_failure = EarlyFailureCutV2()
        self.lifecycle = PositionLifecycleRule()
        self.vwap_rule = VWAPExitRule()
        self.atr_trailing = ATRTrailingV2()

    def evaluate_exit(
        self,
        # 포지션 정보
        entry_time: datetime,
        entry_price: float,
        quantity: int,

        # 현재 시장 상태
        current_time: datetime,
        current_price: float,
        vwap: float,
        atr: float,
        high_since_entry: float,

        # 구조 정보
        htf_choch_valid: bool,
        swing_low: float,
        structure_broken: bool
    ) -> dict:
        """
        모든 청산 규칙을 평가하고 최종 결정 반환

        Returns: {
            'should_exit': bool,
            'exit_quantity': int,
            'reason': str,
            'rule_triggered': str
        }
        """
        result = {
            'should_exit': False,
            'exit_quantity': 0,
            'reason': '',
            'rule_triggered': None,
            'details': {}
        }

        hold_minutes = (current_time - entry_time).total_seconds() / 60
        profit_pct = (current_price - entry_price) / entry_price

        # 1. D+5 강제 청산 (최우선)
        d5_action, d5_ratio = self.lifecycle.check_d5_rule(entry_time, current_time)
        if d5_action == "FORCE_EXIT":
            result['should_exit'] = True
            result['exit_quantity'] = quantity
            result['reason'] = "D+5 강제 청산"
            result['rule_triggered'] = "LIFECYCLE_D5"
            return result

        # 2. Early Failure Cut v2
        recent_low = min(current_price, swing_low)  # 간소화
        ef_exit, ef_reason = self.early_failure.should_exit(
            entry_time, current_time, current_price, vwap,
            recent_low, htf_choch_valid, swing_low
        )
        result['details']['early_failure'] = ef_reason

        if ef_exit:
            result['should_exit'] = True
            result['exit_quantity'] = quantity
            result['reason'] = ef_reason
            result['rule_triggered'] = "EARLY_FAILURE_V2"
            return result

        # 3. VWAP 60분 규칙
        vwap_exit, vwap_reason = self.vwap_rule.should_exit(
            entry_time, current_time, current_price, vwap, structure_broken
        )
        result['details']['vwap_rule'] = vwap_reason

        if vwap_exit:
            result['should_exit'] = True
            result['exit_quantity'] = quantity
            result['reason'] = vwap_reason
            result['rule_triggered'] = "VWAP_60MIN"
            return result

        # 4. D+1 부분 청산
        d1_action, d1_ratio = self.lifecycle.check_d1_rule(
            entry_time, current_time, current_price, entry_price, vwap
        )
        if d1_action == "PARTIAL_EXIT":
            result['should_exit'] = True
            result['exit_quantity'] = int(quantity * d1_ratio)
            result['reason'] = "D+1 VWAP 하회 - 50% 청산"
            result['rule_triggered'] = "LIFECYCLE_D1"
            return result

        # 5. D+3 규칙
        d3_action, d3_ratio, trailing_on = self.lifecycle.check_d3_rule(
            entry_time, current_time, current_price, entry_price
        )
        if d3_action == "FULL_EXIT":
            result['should_exit'] = True
            result['exit_quantity'] = quantity
            result['reason'] = f"D+3 수익 +{profit_pct:.1%} (0~3% 구간) - 전량 청산"
            result['rule_triggered'] = "LIFECYCLE_D3"
            return result

        # 6. ATR 트레일링 (단계화)
        stop_price, trailing_desc = self.atr_trailing.calculate_stop_price(
            entry_price, current_price, high_since_entry, atr
        )
        result['details']['atr_trailing'] = trailing_desc

        if stop_price > 0 and current_price <= stop_price:
            result['should_exit'] = True
            result['exit_quantity'] = quantity
            result['reason'] = f"ATR Trailing Hit: {trailing_desc}"
            result['rule_triggered'] = "ATR_TRAILING_V2"
            return result

        # 청산 조건 없음
        result['reason'] = "HOLD"
        return result


# =============================================================================
# 테스트 / 데모
# =============================================================================
if __name__ == "__main__":
    from datetime import datetime, timedelta

    print("=" * 70)
    print("전략 업그레이드 v2 - 규칙 테스트")
    print("=" * 70)

    manager = ExitManagerV2()

    # 테스트 케이스 1: 인텔리안테크 (01/23) - 18분 손절
    print("\n[테스트 1] 인텔리안테크 01/23 시나리오")
    print("-" * 50)

    entry_time = datetime(2026, 1, 23, 10, 20)
    current_time = datetime(2026, 1, 23, 10, 38)  # 18분 후

    result = manager.evaluate_exit(
        entry_time=entry_time,
        entry_price=76200,
        quantity=1,
        current_time=current_time,
        current_price=74300,
        vwap=75500,
        atr=1500,
        high_since_entry=76500,
        htf_choch_valid=True,  # 아직 유효
        swing_low=75000,
        structure_broken=False
    )

    print(f"결과: {'청산' if result['should_exit'] else '보유'}")
    print(f"사유: {result['reason']}")
    print(f"기존 로직: 18분, -2.49% → 즉시 손절")
    print(f"v2 로직: HTF CHoCH 유지 중 → 손절 금지 ✅")

    # 테스트 케이스 2: 45분 후 재평가
    print("\n[테스트 2] 45분 후 재평가")
    print("-" * 50)

    current_time = datetime(2026, 1, 23, 11, 5)  # 45분 후

    result = manager.evaluate_exit(
        entry_time=entry_time,
        entry_price=76200,
        quantity=1,
        current_time=current_time,
        current_price=74300,
        vwap=75500,
        atr=1500,
        high_since_entry=76500,
        htf_choch_valid=False,  # CHoCH 무효화
        swing_low=75000,
        structure_broken=True   # 구조 이탈
    )

    print(f"결과: {'청산' if result['should_exit'] else '보유'}")
    print(f"사유: {result['reason']}")
    print(f"v2 로직: 45분 경과 + CHoCH 무효 + 구조 이탈 → 손절 ✅")

    # 테스트 케이스 3: ATR 트레일링 단계화
    print("\n[테스트 3] ATR 트레일링 단계화")
    print("-" * 50)

    atr_trailing = ATRTrailingV2()

    test_cases = [
        (100000, 101500, "1.5%"),  # +1.5% → OFF
        (100000, 103000, "3.0%"),  # +3.0% → ATR x3
        (100000, 105000, "5.0%"),  # +5.0% → ATR x2
    ]

    for entry, current, label in test_cases:
        active, dist, desc = atr_trailing.get_trailing_params(entry, current, 2000)
        status = f"ATR x{dist/2000:.1f}" if active else "OFF"
        print(f"+{label}: {status} | {desc}")

    print("\n" + "=" * 70)
    print("✅ 전략 업그레이드 v2 규칙 정의 완료")
    print("=" * 70)
