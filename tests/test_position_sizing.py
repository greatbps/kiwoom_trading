"""
포지션 사이징 테스트 (core/risk_manager.py → calculate_position_size)

검증 대상 (사이징 오류 = 리스크 폭발):
  - 기본 리스크 기반 계산 (RISK_PER_TRADE=2%)
  - confidence floor at 0.5 (낮은 신뢰도도 최소 50% 크기)
  - 최소 1주 보장 (multiplier가 0으로 만들어도 1주)
  - 주간 손실 -3% → 0.5x 자동 축소
  - position_size_multiplier (LSG/Conservative 연동)
  - Conservative × LSG 중첩 (0.25x)
  - 구조 손절 -3% cap (초과 시 capping)
  - HARD_MAX_POSITION (200K) 상한
"""

import pytest
from core.risk_manager import RiskManager


# ─────────────────────────── 픽스처 ───────────────────────────────────

@pytest.fixture
def rm(tmp_path):
    """1M 잔고, 임시 storage (파일 IO 격리)"""
    return RiskManager(
        initial_balance=1_000_000,
        storage_path=str(tmp_path / "risk_log.json"),
    )


# ─────────────────────────── 기본 계산 ────────────────────────────────

class TestBasicCalculation:
    def test_risk_amount_is_2pct_of_balance(self, rm):
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['risk_amount'] == pytest.approx(20_000)

    def test_quantity_bounded_by_max_position(self, rm):
        """risk_qty=40 but max_qty=20 (200K/10K) → 20"""
        # risk=20K, per_share=500 → risk_qty=40; max_invest=min(300K,200K)=200K → max_qty=20
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['quantity'] == 20
        assert result['investment'] == 200_000

    def test_risk_qty_wins_when_smaller(self, rm):
        """risk_qty < max_qty → risk_qty used"""
        # balance=100K, price=10K, stop=5K → risk_per=5K, risk_qty=100K*0.02/5K=0.4→0
        # Actually: risk_qty=2, max_qty=3 → final=2
        result = rm.calculate_position_size(100_000, 10_000, 5_000)
        # risk_qty = 2000/5000 = 0; max_qty=3 → final=0 → bumped to 1
        # Let me use tighter stop: stop=8000 → risk_per=2000, risk_qty=1; max_qty=3 → 1
        result = rm.calculate_position_size(100_000, 10_000, 8_000)
        assert result['quantity'] == 1  # risk_qty=1 < max_qty=3


# ─────────────────────────── Confidence 조정 ──────────────────────────

class TestConfidenceAdjustment:
    def test_confidence_10pct_floored_to_50pct(self, rm):
        """confidence=0.1 → factor=0.5 → 10주 (20의 절반)"""
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500, entry_confidence=0.1)
        assert result['quantity'] == 10

    def test_confidence_zero_floored_to_50pct(self, rm):
        """confidence=0.0 → factor=max(0.5,0.0)=0.5"""
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500, entry_confidence=0.0)
        assert result['quantity'] == 10

    def test_confidence_50pct_exact(self, rm):
        """confidence=0.5 → floor와 동일, 10주"""
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500, entry_confidence=0.5)
        assert result['quantity'] == 10

    def test_confidence_80pct_applied(self, rm):
        """confidence=0.8 → factor=0.8 → int(20*0.8)=16"""
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500, entry_confidence=0.8)
        assert result['quantity'] == 16

    def test_confidence_100pct_no_reduction(self, rm):
        """confidence=1.0 (기본) → 20주"""
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500, entry_confidence=1.0)
        assert result['quantity'] == 20


# ─────────────────────────── 최소 1주 보장 ────────────────────────────

class TestMinimumOneShare:
    def test_psm_rounds_to_zero_but_bumped_to_one(self, rm):
        """PSM=0.1 → int(3*1.0*1.0*0.1)=0 → max_qty=3≥1 → 1주"""
        rm.position_size_multiplier = 0.1
        # balance=100K, price=10K, stop=9.5K → max_qty=3; 3*0.1=0 → bump to 1
        result = rm.calculate_position_size(100_000, 10_000, 9_500)
        assert result['quantity'] == 1

    def test_no_bump_when_max_qty_is_zero(self, rm):
        """max_qty=0 → 최소 1주 보장 조건 미충족 → 0주"""
        # balance=1000, price=10000 → max_invest=min(300,200K)=300 < 10K → max_qty=0
        result = rm.calculate_position_size(1_000, 10_000, 9_500)
        assert result['quantity'] == 0


# ─────────────────────────── 주간 손실 조정 ───────────────────────────

class TestWeeklyLossAdjustment:
    def test_weekly_loss_exactly_3pct_not_triggered(self, rm):
        """-3.0% (경계값) → 1.0 (조정 없음, < 가 아니라 =이므로)"""
        rm.weekly_realized_pnl = -30_000  # exactly -3.0%
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['weekly_adjustment'] == 1.0

    def test_weekly_loss_slightly_over_3pct_halves_position(self, rm):
        """-3.1% → adjustment=0.5 → 10주"""
        rm.weekly_realized_pnl = -31_000
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['weekly_adjustment'] == 0.5
        assert result['quantity'] == 10

    def test_weekly_loss_severe_still_50pct(self, rm):
        """-10% (심각) → 그래도 adjustment=0.5 (추가 감소 없음)"""
        rm.weekly_realized_pnl = -100_000
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['weekly_adjustment'] == 0.5

    def test_weekly_profit_no_adjustment(self, rm):
        """주간 수익 → adjustment=1.0"""
        rm.weekly_realized_pnl = 50_000
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['weekly_adjustment'] == 1.0


# ─────────────────────────── position_size_multiplier ─────────────────

class TestPositionSizeMultiplier:
    def test_lsg_half_multiplier(self, rm):
        """PSM=0.5 (LSG) → 20 * 0.5 = 10주"""
        rm.position_size_multiplier = 0.5
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['quantity'] == 10

    def test_conservative_and_lsg_stacked(self, rm):
        """PSM=0.25 (Conservative 0.5 × LSG 0.5) → 20 * 0.25 = 5주"""
        rm.position_size_multiplier = 0.25
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['quantity'] == 5

    def test_default_multiplier_is_one(self, rm):
        """초기값 PSM=1.0 → 감소 없음"""
        assert rm.position_size_multiplier == 1.0
        result = rm.calculate_position_size(1_000_000, 10_000, 9_500)
        assert result['quantity'] == 20


# ─────────────────────────── 구조 손절 Cap ────────────────────────────

class TestStructureStopCap:
    def test_structure_stop_10pct_capped_to_3pct(self, rm):
        """structure_stop 10% 아래 → -3%로 cap → max_loss 줄어듦"""
        result = rm.calculate_position_size(
            1_000_000, 10_000, 9_000, structure_stop_price=9_000
        )
        # 10% > 3% → effective_stop=9700, risk_per_share=300
        # risk_qty=20000/300≈66; max_qty=20 → 20주
        # max_loss = 20 * 300 = 6000 (stop_loss_price=9000이었다면 20000)
        assert result['quantity'] == 20
        assert result['max_loss'] == pytest.approx(20 * 300)

    def test_structure_stop_2pct_used_directly(self, rm):
        """structure_stop 2% 아래 → cap 없이 그대로"""
        result = rm.calculate_position_size(
            1_000_000, 10_000, 9_900, structure_stop_price=9_800
        )
        # 2% ≤ 3% → effective_stop=9800, risk_per_share=200
        assert result['max_loss'] == pytest.approx(20 * 200)

    def test_structure_stop_overrides_stop_loss_price(self, rm):
        """structure_stop 있으면 stop_loss_price 무시됨"""
        r_with_structure = rm.calculate_position_size(
            1_000_000, 10_000, 9_500, structure_stop_price=9_800
        )
        r_without_structure = rm.calculate_position_size(
            1_000_000, 10_000, 9_800
        )
        # 둘 다 effective_stop=9800 → 동일 결과
        assert r_with_structure['quantity'] == r_without_structure['quantity']
        assert r_with_structure['max_loss'] == pytest.approx(r_without_structure['max_loss'])

    def test_structure_stop_none_uses_stop_loss_price(self, rm):
        """structure_stop=None → stop_loss_price 그대로 사용"""
        result = rm.calculate_position_size(
            1_000_000, 10_000, 9_500, structure_stop_price=None
        )
        assert result['max_loss'] == pytest.approx(20 * 500)


# ─────────────────────────── HARD_MAX_POSITION ────────────────────────

class TestHardMaxPosition:
    def test_large_balance_capped_at_200k(self, rm):
        """잔고 10M → max_invest=min(3M, 200K)=200K → 20주"""
        result = rm.calculate_position_size(10_000_000, 10_000, 9_500)
        assert result['investment'] <= 200_000
        assert result['quantity'] == 20  # 200K / 10K

    def test_small_balance_not_capped(self, rm):
        """잔고 100K → max_invest=min(30K, 200K)=30K (HARD_MAX 미적용)"""
        result = rm.calculate_position_size(100_000, 10_000, 9_500)
        assert result['investment'] <= 30_000
