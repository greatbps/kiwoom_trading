"""
tests/unit/test_regime_engine.py

RegiemeEngine 레짐 판단 + 동적 사이징 테스트

케이스:
  1. VOLATILE 레짐 (ATR > 1.3x)  → DEF mult=1.3, RS mult=0.7
  2. VOLATILE 레짐 (NO_TRADE_DAY) → DEF mult=1.3, RS mult=0.7
  3. TRENDING 레짐                → DEF mult=0.9, RS mult=1.3
  4. NEUTRAL 레짐 (기본값)        → DEF mult=1.0, RS mult=1.0
  5. 성과 기반 사이징: WR > 60%   → ×1.15 (base×regime×perf)
  6. 성과 기반 사이징: WR < 40%   → ×0.85
  7. 샘플 부족 (n < 5)            → perf_mult=1.0 (조정 안 함)
  8. size_cap 적용: 상한선 초과 → cap으로 제한
  9. size_floor 적용: 하한선 미만 → floor로 제한
 10. engine disabled → base_mult 그대로 반환
 11. reset_daily() → 레짐 캐시 초기화 (재계산 강제)
 12. record_trade() + get_status() 확인
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from unittest.mock import MagicMock
from core.regime_engine import RegiemeEngine, VOLATILE, TRENDING, NEUTRAL


# ─── 설정 ────────────────────────────────────────────────────────────────────

_BASE_CFG = {
    "regime_engine": {
        "enabled": True,
        "volatile_atr_threshold": 1.3,
        "cache_ttl_min": 30,
        "perf_lookback": 10,
        "perf_min_samples": 5,
        "perf_high_wr": 60,
        "perf_low_wr": 40,
        "perf_up_mult": 1.15,
        "perf_dn_mult": 0.85,
        "size_floor": 0.5,
        "size_cap": 1.8,
        "regime_mult": {
            "volatile_def": 1.3,
            "volatile_rs": 0.7,
            "trending_def": 0.9,
            "trending_rs": 1.3,
            "neutral_def": 1.0,
            "neutral_rs": 1.0,
        },
    }
}

_DISABLED_CFG = {
    "regime_engine": {
        "enabled": False,
    }
}


# ─── Mock MarketContext 헬퍼 ─────────────────────────────────────────────────

def _make_mc(status: str = "TRADE_OK", atr_ratio: float = 1.0,
             mc_regime: str = "NEUTRAL") -> MagicMock:
    """MarketContextChecker mock 생성.

    Args:
        status: "TRADE_OK" or "NO_TRADE_DAY"
        atr_ratio: ATR비율 값 (e.g. 1.45)
        mc_regime: get_regime() 반환 레짐 ("TREND" / "REVERSAL" / "NEUTRAL")
    """
    mc = MagicMock()
    mc.get_regime.return_value = (mc_regime, f"EMA gap={atr_ratio:.2f}")

    # _cache_result: (status, status_reason, details)
    vol_reason = f"ATR비율={atr_ratio:.2f}(현{atr_ratio*10:.0f}/평10,기준>=0.8)"
    mc._cache_result = (
        status,
        f"status={status}",
        {"volatility": {"reason": vol_reason}},
    )
    return mc


# ─── 레짐 판단 테스트 ─────────────────────────────────────────────────────────

class TestRegimeDetection:
    """_get_regime() / _compute_regime() 레짐 분류 정확도 테스트."""

    def test_case1_volatile_atr_returns_volatile(self):
        """Case 1: ATR ratio > 1.3 → VOLATILE."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        mult, reason = engine.get_def_size_mult(1.0)

        assert "VOLATILE" in reason, f"ATR 1.45 → VOLATILE 기대. reason: {reason}"
        assert abs(mult - 1.3) < 0.01, f"VOLATILE DEF mult=1.3 기대. 실제: {mult}"

    def test_case2_no_trade_day_returns_volatile(self):
        """Case 2: NO_TRADE_DAY → VOLATILE (ATR 무관)."""
        mc = _make_mc(status="NO_TRADE_DAY", atr_ratio=0.8, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        mult, reason = engine.get_rs_size_mult(1.0)

        assert "VOLATILE" in reason, f"NO_TRADE_DAY → VOLATILE 기대. reason: {reason}"
        assert abs(mult - 0.7) < 0.01, f"VOLATILE RS mult=0.7 기대. 실제: {mult}"

    def test_case3_trending_regime_returns_trending(self):
        """Case 3: EMA TREND + ATR 정상 → TRENDING."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.0, mc_regime="TREND")
        engine = RegiemeEngine(mc, _BASE_CFG)

        def_mult, def_reason = engine.get_def_size_mult(1.0)
        rs_mult, rs_reason   = engine.get_rs_size_mult(1.0)

        assert "TRENDING" in def_reason, f"TREND 레짐 → TRENDING 기대. reason: {def_reason}"
        assert abs(def_mult - 0.9) < 0.01, f"TRENDING DEF mult=0.9 기대. 실제: {def_mult}"
        assert abs(rs_mult - 1.3) < 0.01, f"TRENDING RS mult=1.3 기대. 실제: {rs_mult}"

    def test_case4_neutral_regime_returns_1x(self):
        """Case 4: 평범한 시장 → NEUTRAL → 비중 1.0x."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        def_mult, _ = engine.get_def_size_mult(1.0)
        rs_mult, _  = engine.get_rs_size_mult(1.0)

        assert abs(def_mult - 1.0) < 0.01, f"NEUTRAL DEF mult=1.0 기대. 실제: {def_mult}"
        assert abs(rs_mult - 1.0) < 0.01, f"NEUTRAL RS mult=1.0 기대. 실제: {rs_mult}"


# ─── 성과 기반 사이징 테스트 ──────────────────────────────────────────────────

class TestPerfSizing:
    """동적 사이징 (WR 기반 perf_mult) 테스트."""

    def _make_neutral_engine(self):
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        return RegiemeEngine(mc, _BASE_CFG)

    def test_case5_high_edge_increases_size(self):
        """Case 5: edge > 1.2 → perf_mult=1.15.

        edge = win_rate × (avg_win / avg_loss)
        7승(+2.0) 3패(-1.0) → edge = 0.7 × 2.0 = 1.4 > 1.2 → up_mult=1.15
        """
        engine = self._make_neutral_engine()
        for _ in range(7):
            engine.record_trade("def", +2.0)   # 7 wins (avg_win=2.0)
        for _ in range(3):
            engine.record_trade("def", -1.0)   # 3 losses (avg_loss=1.0)
        # edge = 0.7 × (2.0/1.0) = 1.4 > edge_high(1.2) → up_mult

        mult, reason = engine.get_def_size_mult(1.0)

        # NEUTRAL(1.0) × edge_mult(1.15, 첫 계산 seed) = 1.15
        assert abs(mult - 1.15) < 0.02, (
            f"edge 1.4 → 1.15 기대. 실제: {mult:.3f}\nreason: {reason}"
        )
        assert "edge" in reason

    def test_case6_low_win_rate_decreases_size(self):
        """Case 6: WR < 40% → perf_mult=0.85.

        kill_switch 한도를 높게 설정해 연패 차단 없이 순수 WR만 테스트.
        1승 + 4패 = WR 20% (5건, min_samples 충족 / 연패 4 < 한도 10)
        """
        cfg = {
            "regime_engine": {
                **_BASE_CFG["regime_engine"],
                "kill_switch": {"enabled": True, "consecutive_loss_limit": 10},
            }
        }
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, cfg)

        engine.record_trade("rs", +1.0)    # 1 win
        for _ in range(4):
            engine.record_trade("rs", -1.0)  # 4 losses → 연패 4 (한도 미달)

        mult, reason = engine.get_rs_size_mult(1.0)

        # NEUTRAL(1.0) × perf_smoothed(≈0.85, 첫 계산이므로 seed=raw) = ≈0.85
        assert abs(mult - 0.85) < 0.02, (
            f"WR 20% → 0.85 기대. 실제: {mult:.3f}\nreason: {reason}"
        )

    def test_case7_insufficient_samples_no_adjustment(self):
        """Case 7: n < 5 → perf_mult=1.0 (조정 안 함)."""
        engine = self._make_neutral_engine()
        # 4건만 기록 (min_samples=5 미달)
        for i in range(4):
            engine.record_trade("def", +1.0)

        mult, reason = engine.get_def_size_mult(1.0)

        # NEUTRAL(1.0) × perf(1.0) = 1.0
        assert abs(mult - 1.0) < 0.01, (
            f"샘플 4건 → 조정 없음(1.0) 기대. 실제: {mult:.3f}\nreason: {reason}"
        )


# ─── 클램핑 테스트 ────────────────────────────────────────────────────────────

class TestKillSwitch:
    """Kill-switch (N연패 → 당일 전략 차단) 테스트."""

    def test_kill_switch_fires_after_n_consecutive_losses(self):
        """5연패 → kill-switch 발동 → size_floor 반환."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        # 4패 (한도 5 미달 → 아직 미발동)
        for _ in range(4):
            engine.record_trade("rs", -1.0)
        assert not engine.is_strategy_killed("rs"), "4연패: kill-switch 미발동 확인"

        # 5번째 패 → 발동
        engine.record_trade("rs", -1.0)
        assert engine.is_strategy_killed("rs"), "5연패: kill-switch 발동 확인"

        mult, reason = engine.get_rs_size_mult(0.15)
        floor = _BASE_CFG["regime_engine"]["size_floor"]
        assert abs(mult - floor) < 0.001, f"kill-switch → size_floor={floor} 반환 기대. 실제: {mult}"
        assert "kill-switch" in reason

    def test_kill_switch_resets_after_daily_reset(self):
        """reset_daily() → kill-switch 해제."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        for _ in range(5):
            engine.record_trade("def", -1.0)
        assert engine.is_strategy_killed("def")

        engine.reset_daily()
        assert not engine.is_strategy_killed("def"), "reset_daily 후 kill-switch 해제 확인"

    def test_kill_switch_win_resets_consecutive_count(self):
        """3연패 후 1승 → 연패 카운터 리셋 → 다시 5연패 필요."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        for _ in range(3):
            engine.record_trade("rs", -1.0)
        engine.record_trade("rs", +1.0)    # 수익 → 연패 리셋
        assert engine._rs_consecutive_loss == 0

        for _ in range(4):
            engine.record_trade("rs", -1.0)   # 4연패 → 한도(5) 미달
        assert not engine.is_strategy_killed("rs"), "수익 후 4연패: 한도 미달"

    def test_kill_switch_def_independent_from_rs(self):
        """DEF kill-switch 발동이 RS에 영향 없음 (독립)."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        for _ in range(5):
            engine.record_trade("def", -1.0)
        assert engine.is_strategy_killed("def")
        assert not engine.is_strategy_killed("rs"), "DEF kill-switch가 RS에 영향 없어야 함"


class TestRegimeMismatchKill:
    """Regime 불일치 Kill-switch 테스트."""

    def test_rs_volatile_mismatch_kills_after_limit(self):
        """VOLATILE 레짐에서 RS 3연패 → Regime 불일치 kill-switch 발동."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="NEUTRAL")
        cfg = {
            "regime_engine": {
                **_BASE_CFG["regime_engine"],
                "kill_switch": {
                    "enabled": True,
                    "consecutive_loss_limit": 10,  # 연패 한도는 높게 (Regime 불일치만 테스트)
                    "regime_mismatch_limit": 3,
                },
            }
        }
        engine = RegiemeEngine(mc, cfg)
        # ATR=1.45 → VOLATILE 레짐 → RS 손실 = 불일치 카운트

        engine.record_trade("rs", -1.0)   # mismatch=1
        engine.record_trade("rs", -1.0)   # mismatch=2
        assert not engine.is_strategy_killed("rs"), "2회: 한도 미달"

        engine.record_trade("rs", -1.0)   # mismatch=3 → 발동
        assert engine.is_strategy_killed("rs"), "VOLATILE 3연패 → RS kill"
        assert "VOLATILE" in engine._rs_kill_reason or "불일치" in engine._rs_kill_reason

    def test_rs_mismatch_resets_on_win(self):
        """RS 불일치 2연패 후 수익 → 카운터 리셋."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="NEUTRAL")
        cfg = {
            "regime_engine": {
                **_BASE_CFG["regime_engine"],
                "kill_switch": {
                    "enabled": True,
                    "consecutive_loss_limit": 10,
                    "regime_mismatch_limit": 3,
                },
            }
        }
        engine = RegiemeEngine(mc, cfg)

        engine.record_trade("rs", -1.0)
        engine.record_trade("rs", -1.0)
        assert engine._rs_regime_mismatch_loss == 2

        engine.record_trade("rs", +1.0)  # 수익 → 리셋
        assert engine._rs_regime_mismatch_loss == 0
        assert not engine.is_strategy_killed("rs")


class TestClamping:
    """size_cap / size_floor 경계값 테스트."""

    def test_case8_size_cap_applied(self):
        """Case 8: base=1.0 × volatile_def(1.3) × perf(1.15) = 1.495 ≤ cap(1.8) → 1.495."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)
        for _ in range(8):
            engine.record_trade("def", +1.0)
        for _ in range(2):
            engine.record_trade("def", -1.0)

        mult, _ = engine.get_def_size_mult(1.0)

        # VOLATILE(1.3) × perf(1.15) = 1.495 → 아직 cap(1.8) 미초과
        assert mult <= 1.8, f"cap 1.8 초과하면 안 됨. 실제: {mult}"
        assert mult > 1.0, f"VOLATILE + 고성과 → 1.0 초과 기대. 실제: {mult}"

    def test_case8b_size_cap_clamped(self):
        """Case 8b: base=2.0 (큰 값) → cap(1.8) 적용."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        mult, _ = engine.get_def_size_mult(2.0)  # base가 이미 큼

        assert mult <= 1.8, f"cap 1.8 적용 기대. 실제: {mult}"

    def test_case9_size_floor_applied(self):
        """Case 9: base=1.0 × volatile_rs(0.7) × perf_dn(0.85) = 0.595 ≥ floor(0.5) → 0.595."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)
        for _ in range(3):
            engine.record_trade("rs", +1.0)
        for _ in range(7):
            engine.record_trade("rs", -1.0)

        mult, _ = engine.get_rs_size_mult(1.0)

        # VOLATILE(0.7) × perf_dn(0.85) = 0.595 → floor(0.5) 이상
        assert mult >= 0.5, f"floor 0.5 미만이면 안 됨. 실제: {mult}"

    def test_case9b_size_floor_clamped(self):
        """Case 9b: base=0.1 (매우 작은 값) → floor(0.5) 적용."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)
        for _ in range(3):
            engine.record_trade("rs", +1.0)
        for _ in range(7):
            engine.record_trade("rs", -1.0)

        mult, _ = engine.get_rs_size_mult(0.1)  # base 매우 작음

        assert mult >= 0.5, f"floor 0.5 적용 기대. 실제: {mult}"


# ─── 비활성화 / 리셋 / 상태 테스트 ───────────────────────────────────────────

class TestEngineControl:
    """enabled=False, reset_daily(), get_status() 테스트."""

    def test_case10_disabled_returns_base_unchanged(self):
        """Case 10: enabled=False → base_mult 그대로 반환."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.45, mc_regime="TREND")
        engine = RegiemeEngine(mc, _DISABLED_CFG)

        def_mult, def_reason = engine.get_def_size_mult(0.3)
        rs_mult, rs_reason   = engine.get_rs_size_mult(0.15)

        assert abs(def_mult - 0.3) < 0.001, f"disabled → base 0.3 반환 기대. 실제: {def_mult}"
        assert abs(rs_mult - 0.15) < 0.001, f"disabled → base 0.15 반환 기대. 실제: {rs_mult}"
        assert "OFF" in def_reason

    def test_case11_reset_daily_clears_cache(self):
        """Case 11: reset_daily() → 캐시 초기화 → 다음 호출 시 재계산."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=1.0, mc_regime="TREND")
        engine = RegiemeEngine(mc, _BASE_CFG)

        # 1차 계산 (TRENDING 캐시됨)
        engine.get_def_size_mult(1.0)
        assert engine._cached_regime == TRENDING

        # reset 후 캐시 소거 확인
        engine.reset_daily()
        assert engine._cached_regime is None
        assert engine._cache_dt is None

        # 2차 계산 (재계산 발생)
        engine.get_def_size_mult(1.0)
        assert engine._cached_regime == TRENDING  # 재캐시됨

    def test_case12_get_status_reflects_records(self):
        """Case 12: record_trade() 후 get_status()에 반영됨."""
        mc = _make_mc(status="TRADE_OK", atr_ratio=0.9, mc_regime="NEUTRAL")
        engine = RegiemeEngine(mc, _BASE_CFG)

        # 5건 기록 (min_samples 충족)
        for _ in range(4):
            engine.record_trade("def", +1.5)
        engine.record_trade("def", -0.5)

        for _ in range(5):
            engine.record_trade("rs", -1.0)

        status = engine.get_status()

        assert status["regime"] == NEUTRAL
        assert status["def_n"] == 5
        assert status["rs_n"] == 5
        assert status["def_wr"] == 80.0, f"DEF WR 80% 기대. 실제: {status['def_wr']}"
        assert status["rs_wr"] == 0.0,   f"RS WR 0% 기대. 실제: {status['rs_wr']}"
        # DEF 고승률 → def_mult > 1.0
        assert status["def_mult"] > 1.0, f"DEF 고승률 → mult > 1.0 기대. 실제: {status['def_mult']}"
        # RS 저승률 → rs_mult < 1.0
        assert status["rs_mult"] < 1.0,  f"RS 저승률 → mult < 1.0 기대. 실제: {status['rs_mult']}"
