"""
tests/unit/test_drawdown_engine.py

DrawdownEngine 드로우다운 리스크 컨트롤 테스트

케이스:
  1. NORMAL: drawdown 없음 → size_mult=1.0, can_enter=True
  2. CAUTION: drawdown -1.5% → size_mult=0.7
  3. DANGER:  drawdown -3.0% → size_mult=0.4
  4. HALT:    drawdown -5.0% → can_enter=False
  5. peak 갱신: 수익 후 추가 손실 → drawdown은 peak 기준 계산
  6. reset_daily(): 모든 지표 초기화
  7. HALT 후에도 record_pnl 가능 (중복 발동 안 함)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.drawdown_engine import DrawdownEngine, LEVEL_NORMAL, LEVEL_CAUTION, LEVEL_DANGER, LEVEL_HALT


_CFG = {
    "drawdown_engine": {
        "enabled": True,
        "warning_pct": -1.5,
        "danger_pct":  -3.0,
        "halt_pct":    -5.0,
        "normal_mult":  1.0,
        "caution_mult": 0.7,
        "danger_mult":  0.4,
        "halt_mult":    0.0,
    }
}


class TestDrawdownLevels:
    """드로우다운 레벨 분류 + size_mult 테스트."""

    def _engine(self):
        return DrawdownEngine(_CFG)

    def test_case1_no_loss_normal_level(self):
        """Case 1: 손실 없음 → NORMAL, size=1.0, can_enter=True."""
        eng = self._engine()
        mult, _ = eng.get_size_mult()
        ok, _   = eng.can_enter()

        assert abs(mult - 1.0) < 0.001
        assert ok is True

    def test_case2_caution_level(self):
        """Case 2: drawdown -2.0% → CAUTION → size=0.7."""
        eng = self._engine()
        eng.record_pnl(-2.0)   # peak=0, daily=-2 → dd=-2 ≤ -1.5

        mult, reason = eng.get_size_mult()
        assert abs(mult - 0.7) < 0.001, f"CAUTION mult=0.7 기대. 실제: {mult}"
        assert "CAUTION" in reason

    def test_case3_danger_level(self):
        """Case 3: drawdown -3.5% → DANGER → size=0.4."""
        eng = self._engine()
        eng.record_pnl(-3.5)

        mult, reason = eng.get_size_mult()
        assert abs(mult - 0.4) < 0.001, f"DANGER mult=0.4 기대. 실제: {mult}"
        assert "DANGER" in reason

    def test_case4_halt_level_blocks_entry(self):
        """Case 4: drawdown -5.0% → HALT → can_enter=False."""
        eng = self._engine()
        eng.record_pnl(-5.0)

        ok, reason = eng.can_enter()
        assert ok is False, "HALT → 진입 차단 기대"
        assert "HALT" in reason or "drawdown" in reason.lower()

    def test_case5_peak_based_drawdown(self):
        """Case 5: 수익(+2%) 후 손실(-4%) → drawdown = -4% - 0% = -4%, peak=2%."""
        eng = self._engine()
        eng.record_pnl(+2.0)   # peak=2, daily=2, dd=0
        eng.record_pnl(-4.0)   # peak=2, daily=-2, dd=-4 → DANGER

        assert abs(eng._peak_pnl - 2.0) < 0.001
        assert abs(eng._drawdown - (-4.0)) < 0.001
        assert abs(eng._daily_pnl - (-2.0)) < 0.001

        mult, _ = eng.get_size_mult()
        assert abs(mult - 0.4) < 0.001, f"peak=2% 후 손실4% → dd=-4% DANGER. 실제mult: {mult}"

    def test_case5b_recovery_raises_peak(self):
        """Case 5b: 손실 후 회복 → peak 갱신 → drawdown 회복."""
        eng = self._engine()
        eng.record_pnl(-1.0)   # dd=-1, NORMAL (경계)
        eng.record_pnl(+3.0)   # peak=2, dd=0 → NORMAL

        mult, _ = eng.get_size_mult()
        assert abs(mult - 1.0) < 0.001, "회복 후 NORMAL 복귀 기대"

    def test_case6_reset_daily_clears_all(self):
        """Case 6: reset_daily() → 모든 지표 초기화."""
        eng = self._engine()
        eng.record_pnl(-5.5)    # HALT 발동
        assert eng._halt_triggered is True

        eng.reset_daily()

        assert abs(eng._daily_pnl) < 0.001
        assert abs(eng._peak_pnl)  < 0.001
        assert abs(eng._drawdown)  < 0.001
        assert eng._halt_triggered is False

        ok, _ = eng.can_enter()
        assert ok is True, "reset 후 can_enter=True 기대"

    def test_case7_halt_not_double_triggered(self):
        """Case 7: HALT 이미 발동된 상태에서 추가 손실 → halt_triggered 중복 없음."""
        eng = self._engine()
        eng.record_pnl(-5.0)
        assert eng._halt_triggered is True

        # 추가 손실 기록해도 플래그 상태 유지
        eng.record_pnl(-1.0)
        assert eng._halt_triggered is True  # 중복 발동 없음


class TestStrategyLevelDrawdown:
    """전략별 독립 drawdown 테스트."""

    def _engine(self):
        return DrawdownEngine(_CFG)

    def test_strategy_halt_blocks_only_that_strategy(self):
        """RS drawdown -4% → RS만 차단, DEF는 통과."""
        eng = self._engine()
        eng.record_pnl(-4.0, strategy="rs")   # rs_halt 발동

        rs_ok, rs_reason  = eng.can_enter(strategy="rs")
        def_ok, _         = eng.can_enter(strategy="def")
        global_ok, _      = eng.can_enter()

        assert rs_ok is False,  "RS -4% → RS 전략 차단"
        assert def_ok is True,  "RS 차단이 DEF에 영향 없어야 함"
        assert global_ok is True, "전체 계좌는 아직 halt 미발동"
        assert "rs" in rs_reason.lower() or "halt" in rs_reason.lower()

    def test_global_halt_blocks_all_strategies(self):
        """전체 계좌 -5% → 전략 무관 전면 차단."""
        eng = self._engine()
        eng.record_pnl(-5.0)   # 전체 halt (strategy 없이)

        assert eng.can_enter(strategy="def")[0] is False
        assert eng.can_enter(strategy="rs")[0]  is False
        assert eng.can_enter()[0] is False

    def test_strategy_pnl_tracked_independently(self):
        """DEF +2%, RS -1% → 각각 독립 추적."""
        eng = self._engine()
        eng.record_pnl(+2.0, strategy="def")
        eng.record_pnl(-1.0, strategy="rs")

        def_tracker = eng._strategy_trackers["def"]
        rs_tracker  = eng._strategy_trackers["rs"]

        assert abs(def_tracker.daily_pnl - 2.0)  < 0.001
        assert abs(rs_tracker.daily_pnl  - (-1.0)) < 0.001
        # 전체 계좌는 합산
        assert abs(eng._daily_pnl - 1.0) < 0.001

    def test_strategy_tracker_reset_on_daily_reset(self):
        """reset_daily() → 전략별 tracker도 초기화."""
        eng = self._engine()
        eng.record_pnl(-4.0, strategy="rs")
        assert eng._strategy_trackers["rs"].halted is True

        eng.reset_daily()
        assert eng._strategy_trackers["rs"].halted is False
        assert abs(eng._strategy_trackers["rs"].daily_pnl) < 0.001


class TestDrawdownStatus:
    """get_status() 반환값 테스트."""

    def test_status_fields(self):
        eng = DrawdownEngine(_CFG)
        eng.record_pnl(+1.5)
        eng.record_pnl(-2.0)   # daily=-0.5, peak=1.5, dd=-2.0

        s = eng.get_status()

        assert "daily_pnl"      in s
        assert "peak_pnl"       in s
        assert "drawdown"       in s
        assert "level"          in s
        assert "halt_triggered" in s
        assert "size_mult"      in s

        assert abs(s["daily_pnl"] - (-0.5)) < 0.001
        assert abs(s["peak_pnl"]  - 1.5)    < 0.001
        assert abs(s["drawdown"]  - (-2.0)) < 0.001
        assert s["level"]    == LEVEL_CAUTION
        assert s["halt_triggered"] is False
