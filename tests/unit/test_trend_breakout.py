"""
tests/unit/test_trend_breakout.py

TrendBreakoutStrategy.check_entry() BREAKOUT 진입 조건 테스트

케이스:
  1. 고점 돌파 아님   → 진입 거부
  2. 거래량 부족      → 진입 거부
  3. EMA 역배열       → 진입 거부
  4. 강한 캔들 아님   → 진입 거부
  5. 모든 조건 만족   → BREAKOUT 진입 신호
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pandas as pd
import pytest

from analyzers.trend.trend_breakout import TrendBreakoutStrategy


# ─── 기본 설정 ───────────────────────────────────────────────────────────────

_BASE = {
    "enabled": True,
    "breakout_lookback": 20,
    "volume_ratio": 1.5,
    "volume_ratio_pullback": 1.2,
    "candle_body_min": 0.55,
    "max_extension_pct": 2.5,
    "strong_volume_ratio": 2.0,
    "strong_body_min": 0.70,
    "require_above_ema60": False,  # 기본 OFF — 조건 격리 목적
    "ema20_slope_min_pct": 0.0,    # 기본 OFF — 조건 격리 목적
    "pullback_near_pct": 0.8,
    "trend_score_ext_max_pct": 2.0,
    "trend_score_strong_min": 4,
}


def _cfg(**overrides) -> dict:
    """_BASE 위에 오버라이드를 적용한 config dict 반환."""
    return {"trend": {**_BASE, **overrides}}


# ─── 데이터 헬퍼 ─────────────────────────────────────────────────────────────

def _make_df(
    n: int = 50,
    start: float = 9_000.0,
    end: float = 9_950.0,
    *,
    last_close: float = None,
    last_open: float = None,
    last_high: float = None,
    last_low: float = None,
    last_vol: float = None,
    prev_vol: float = None,   # volume.iloc[-2] — TrendBreakoutStrategy가 읽는 "현재" 거래량
    base_vol: float = 1_000.0,
) -> pd.DataFrame:
    """
    start → end 선형 추세 OHLCV DataFrame.

    last_* 파라미터로 마지막 봉(진입 캔들)만 오버라이드.
    prev_vol: volume.iloc[-2] 설정 (TrendBreakoutStrategy는 curr_vol = volume.iloc[-2] 사용).
    나머지 봉은 EMA 정배열이 자연스럽게 형성되는 상승 추세로 구성.
    """
    prices = np.linspace(start, end, n)
    data = {
        "open":   prices * 0.997,
        "high":   prices * 1.005,
        "low":    prices * 0.993,
        "close":  prices.copy(),
        "volume": np.full(n, base_vol),
    }

    if last_close is not None:
        c = last_close
        data["close"][-1]  = c
        data["open"][-1]   = last_open if last_open is not None else c * 0.997
        data["high"][-1]   = last_high if last_high is not None else c * 1.005
        data["low"][-1]    = last_low  if last_low  is not None else c * 0.993

    if last_vol is not None:
        data["volume"][-1] = last_vol

    if prev_vol is not None:
        data["volume"][-2] = prev_vol

    return pd.DataFrame(data)


# ─── 테스트 케이스 ───────────────────────────────────────────────────────────

class TestBreakoutEntry:
    """BREAKOUT 진입 조건 5케이스."""

    def test_case1_no_signal_when_below_lookback_high(self):
        """Case 1: 현재가 직전 N봉 고점 이하 → 진입 거부.

        9000→9950 상승 추세에서 마지막 봉 close=9950.
        lookback_high = max(highs[-21:-1]) ≈ 9930 * 1.005 ≈ 9980.
        current(9950) < lookback_high(9980) → 돌파 실패.
        """
        df  = _make_df(n=50, start=9_000, end=9_950)
        stg = TrendBreakoutStrategy(_cfg())

        signal, reason, details = stg.check_entry(df)

        assert signal is False, "고점 돌파 아닐 때 진입하면 안 됨"
        assert details["entry_type"] is None
        assert "돌파실패" in reason

    def test_case2_no_signal_when_volume_insufficient(self):
        """Case 2: 거래량 부족(vol_ratio < 1.5) → 진입 거부.

        close=10050으로 고점 돌파 조건 충족, 하지만 거래량 300.
        avg_vol=1000 → vol_ratio=0.3 < 1.5 → 차단.
        """
        df = _make_df(
            n=50, start=9_000, end=9_950,
            last_close=10_050,
            last_open=10_000, last_high=10_060, last_low=9_990,
            last_vol=300,
        )
        stg = TrendBreakoutStrategy(_cfg())

        signal, reason, details = stg.check_entry(df)

        assert signal is False, "거래량 부족 시 진입하면 안 됨"
        assert "거래량부족" in reason

    def test_case3_no_signal_when_ema_not_aligned(self):
        """Case 3: EMA 역배열(하락 추세) → 진입 거부.

        11000→9000 하락 추세 → EMA5 < EMA20 < EMA60.
        require_above_ema60=False로 EMA60 게이트 제외 후
        ema_aligned 조건만 격리하여 테스트.
        """
        df  = _make_df(n=80, start=11_000, end=9_000)
        # ema9_filter도 비활성화 → ema_aligned 조건만 격리 테스트
        stg = TrendBreakoutStrategy(_cfg(require_above_ema60=False, ema9_filter={"enabled": False}))

        signal, reason, details = stg.check_entry(df)

        assert signal is False, "EMA 역배열 시 진입하면 안 됨"
        assert details["ema_aligned"] is False
        assert "EMA미정렬" in reason

    def test_case4_no_signal_when_candle_not_strong(self):
        """Case 4: 약한 캔들(몸통 비율 < 55%) → 진입 거부.

        고점 돌파 + 거래량 충분, 하지만 도지 캔들.
        open=10040, close=10050 → body=10
        high=10250, low=9850  → range=400
        body_ratio = 10/400 = 0.025 < 0.55 → 차단.
        """
        df = _make_df(
            n=50, start=9_000, end=9_950,
            last_close=10_050,
            last_open=10_040,
            last_high=10_250,
            last_low=9_850,
            last_vol=2_000,
        )
        stg = TrendBreakoutStrategy(_cfg())

        signal, reason, details = stg.check_entry(df)

        assert signal is False, "약한 캔들(도지) 시 진입하면 안 됨"
        assert "캔들약" in reason

    def test_case5_breakout_signal_when_all_conditions_met(self):
        """Case 5: 모든 조건 충족 → BREAKOUT 진입 신호.

        상승 추세 50봉 (9500→9960) + 마지막 봉 고점 돌파:
          close=10100 > lookback_high≈10000 (돌파 ✓)
          EMA20≈9875 → ext_pct≈2.28% < 2.5% (이격 ✓)
          body = (10100-10010)/(10120-9990) = 90/130 = 0.69 ≥ 0.55 (강한 캔들 ✓)
          vol_ratio = 2500/1000 = 2.5 ≥ 1.5 (거래량 ✓)
            → prev_vol=2500 사용: strategy는 curr_vol=volume.iloc[-2] 읽음
          EMA5 > EMA20 > EMA60 (정배열 ✓)
        """
        df = _make_df(
            n=50, start=9_500, end=9_960,
            last_close=10_100,
            last_open=10_010,
            last_high=10_120,
            last_low=9_990,
            prev_vol=2_500,   # volume.iloc[-2] = curr_vol (strategy 인식 기준)
        )
        stg = TrendBreakoutStrategy(_cfg(require_above_ema60=True))

        signal, reason, details = stg.check_entry(df)

        assert signal is True,               "모든 조건 충족 시 진입해야 함"
        assert details["entry_type"] == "breakout"
        assert details["grade"] in ("STRONG", "NORMAL")
        assert details["size_mult"] > 0
        assert "TREND BREAKOUT" in reason
