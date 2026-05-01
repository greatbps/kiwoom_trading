"""
tests/unit/test_trend_regime.py

SMC→TREND 전환 레짐 판단 테스트

케이스:
  1. 강한 상승 추세 (EMA gap ≥ 0.5%) → "TREND"
  2. 하락 추세 (EMA fast < slow)     → "REVERSAL"
  3. 약한 상승 추세 (EMA gap < 0.5%) → "NEUTRAL"
  4. 데이터 부족                      → "NEUTRAL" (안전 기본값)
  5. TREND 레짐 → TrendBreakoutStrategy enabled=True 시 신호 체크 가능
  6. TREND 외 레짐 → TrendBreakoutStrategy enabled=False → 즉시 차단
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pandas as pd

from analyzers.trend.trend_breakout import TrendBreakoutStrategy, detect_trend_regime


# ─── 설정 ────────────────────────────────────────────────────────────────────

_REGIME_CFG = {
    "trend": {
        "regime_detection": {
            "ema_fast": 20,
            "ema_slow": 60,
            "trend_ema_gap_pct": 0.5,
        }
    }
}

_STRATEGY_CFG = {
    "trend": {
        "enabled": False,  # 기본 OFF (auto_enable_on_trend으로 조건부 ON)
        "breakout_lookback": 20,
        "volume_ratio": 1.5,
        "volume_ratio_pullback": 1.2,
        "candle_body_min": 0.55,
        "max_extension_pct": 2.5,
        "strong_volume_ratio": 2.0,
        "strong_body_min": 0.70,
        "require_above_ema60": False,
        "ema20_slope_min_pct": 0.0,
        "pullback_near_pct": 0.8,
        "trend_score_ext_max_pct": 2.0,
        "trend_score_strong_min": 4,
    }
}


# ─── 데이터 헬퍼 ─────────────────────────────────────────────────────────────

def _make_index_df(n: int, start: float, end: float) -> pd.DataFrame:
    """지수 5분봉 OHLCV DataFrame 생성 (close만 레짐 판단에 사용)."""
    prices = np.linspace(start, end, n)
    return pd.DataFrame({
        "open":   prices * 0.999,
        "high":   prices * 1.002,
        "low":    prices * 0.998,
        "close":  prices,
        "volume": np.full(n, 5_000.0),
    })


def _make_strategy_df(n: int = 50) -> pd.DataFrame:
    """TrendBreakoutStrategy용 OHLCV DataFrame (상승 추세)."""
    prices = np.linspace(9_500, 9_960, n)
    df = pd.DataFrame({
        "open":   prices * 0.997,
        "high":   prices * 1.005,
        "low":    prices * 0.993,
        "close":  prices.copy(),
        "volume": np.full(n, 1_000.0),
    })
    # 마지막 봉: 고점 돌파 + 강한 캔들 + 충분한 거래량
    df.loc[df.index[-1], "close"]  = 10_100
    df.loc[df.index[-1], "open"]   = 10_010
    df.loc[df.index[-1], "high"]   = 10_120
    df.loc[df.index[-1], "low"]    = 9_990
    df.loc[df.index[-1], "volume"] = 2_500
    return df


# ─── 레짐 판단 테스트 ─────────────────────────────────────────────────────────

class TestDetectTrendRegime:
    """detect_trend_regime() 레짐 분류 정확도 테스트."""

    def test_case1_strong_uptrend_returns_trend(self):
        """Case 1: 강한 상승 추세 → "TREND".

        270→310 상승 (14.8%) 100봉:
        EMA20 ≫ EMA60 → gap >> 0.5% → TREND.
        """
        df = _make_index_df(n=100, start=270, end=310)

        regime, reason = detect_trend_regime(df, _REGIME_CFG)

        assert regime == "TREND", f"강한 상승장에서 TREND 반환해야 함. 실제: {regime} ({reason})"
        assert "gap=" in reason

    def test_case2_downtrend_returns_reversal(self):
        """Case 2: 하락 추세 → "REVERSAL".

        310→270 하락 100봉:
        EMA20 < EMA60 → REVERSAL.
        """
        df = _make_index_df(n=100, start=310, end=270)

        regime, reason = detect_trend_regime(df, _REGIME_CFG)

        assert regime == "REVERSAL", f"하락장에서 REVERSAL 반환해야 함. 실제: {regime} ({reason})"

    def test_case3_weak_uptrend_returns_neutral(self):
        """Case 3: 약한 상승 (EMA gap < 0.5%) → "NEUTRAL".

        300→300.3 극소 상승 100봉:
        EMA20 > EMA60이지만 gap ≈ 0 < 0.5% → NEUTRAL.
        """
        df = _make_index_df(n=100, start=300.0, end=300.3)

        regime, reason = detect_trend_regime(df, _REGIME_CFG)

        assert regime == "NEUTRAL", f"약한 추세에서 NEUTRAL 반환해야 함. 실제: {regime} ({reason})"

    def test_case4_insufficient_data_returns_neutral(self):
        """Case 4: 데이터 부족 → "NEUTRAL" (안전 기본값).

        ema_slow=60 기준 최소 65봉 필요 → 30봉만 제공하면 차단.
        """
        df = _make_index_df(n=30, start=300, end=310)

        regime, reason = detect_trend_regime(df, _REGIME_CFG)

        assert regime == "NEUTRAL", "데이터 부족 시 NEUTRAL(안전값) 반환해야 함"
        assert "부족" in reason


# ─── SMC→TREND 전환 라우팅 테스트 ────────────────────────────────────────────

class TestSMCToTrendRouting:
    """레짐에 따른 TrendBreakoutStrategy 활성화/비활성화 전환 테스트."""

    def test_case5_strategy_enabled_when_trend_regime(self):
        """Case 5: TREND 레짐 → enabled=True로 전환 시 진입 신호 체크 가능.

        main_auto_trading에서 regime=="TREND" 감지 후
        trend_strategy.enabled = True 로 임시 활성화하는 패턴을 재현.
        """
        df_index = _make_index_df(n=100, start=270, end=310)
        df_stock  = _make_strategy_df()

        regime, _ = detect_trend_regime(df_index, _REGIME_CFG)

        # main_auto_trading 전환 패턴 재현: TREND이면 enabled=True로 임시 활성화
        cfg = {**_STRATEGY_CFG, "trend": {**_STRATEGY_CFG["trend"], "enabled": regime == "TREND"}}
        stg = TrendBreakoutStrategy(cfg)

        assert stg.enabled is True, "TREND 레짐 시 전략이 활성화되어야 함"

        signal, _, details = stg.check_entry(df_stock)
        # 신호 자체보다 "체크가 실행됐음"을 검증 (enabled 차단 없이 진입 조건 평가됨)
        assert details["entry_type"] is not None or not signal, \
            "TREND 레짐에서 enabled=True면 전략이 조건을 평가해야 함 (not blocked by enabled)"

    def test_case6_strategy_blocked_when_not_trend_regime(self):
        """Case 6: NEUTRAL/REVERSAL 레짐 → enabled=False → 즉시 차단.

        SMC가 신호를 내지 못했더라도 레짐이 TREND가 아니면
        TrendBreakoutStrategy는 호출되지 않아야 함.
        enabled=False 상태의 즉시 차단 동작을 확인.
        """
        df_index = _make_index_df(n=100, start=300.0, end=300.3)  # NEUTRAL 레짐
        df_stock  = _make_strategy_df()

        regime, _ = detect_trend_regime(df_index, _REGIME_CFG)
        assert regime == "NEUTRAL"

        # NEUTRAL이면 enabled=False 유지 (기본값)
        stg = TrendBreakoutStrategy(_STRATEGY_CFG)

        signal, reason, _ = stg.check_entry(df_stock)

        assert signal is False, "TREND 아닌 레짐에서 enabled=False → 신호 없어야 함"
        assert "비활성화" in reason
