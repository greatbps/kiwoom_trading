"""
tests/test_w_pattern_filter.py

WPatternFilter.evaluate() 케이스 테스트.

케이스:
  1. 정상 W 탐지 → PASS
  2. L2 이탈 → 실패 (L2_BREAK)
  3. 반등 부족 → 실패 (NO_REBOUND)
  4. 거래량 감소 조건 위반 → 실패 (VOLUME_NOT_DECLINED)
  5. CHoCH 없음 → 실패 (NO_CHOCH, 하드 게이트)
  6. BOS 없음 → 통과는 하되 신뢰도만 하락 (하드 게이트 아님)
  7. VWAP 조건 위반 → 실패 (NO_VWAP)
  8. EMA20 조건 위반 → 실패 (NO_EMA20)
  9. Confidence 계산 정확성
  10. Config OFF → enabled 플래그 확인 (실제 우회는 main_auto_trading.py 호출부에서 보장)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from strategy.w_pattern_filter import WPatternFilter, _ema
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer


# ─── 기본 설정 ───────────────────────────────────────────────────────────────

_BASE = {
    "enabled": True,
    "timeframe": "3m",
    "min_drop_pct": -3.0,
    "max_bottom_diff_pct": 0.3,
    "min_rebound_pct": 1.5,
    "min_rvol": 2.0,
    "require_volume_decline": True,
    "require_vwap": True,
    "require_ema20": True,
}


def _cfg(**overrides) -> dict:
    return {"w_pattern": {**_BASE, **overrides}}


# ─── 데이터 헬퍼 ─────────────────────────────────────────────────────────────
#
# 40봉 구성 (find_swing_points lookback=5 기준 L1/L2가 확정 스윙포인트로
# 잡히도록 좌우 각 5봉씩 단조 증감시켜 만든 W자형):
#   idx  0- 9 : 평탄 (base)                       — L1 비교창 밖
#   idx 10-14 : base → L1 로 단조 하락 (좌측창)
#   idx    15 : L1 (최저점)
#   idx 16-22 : L1 → 반등 고점으로 단조 상승
#   idx 23-28 : 반등 고점 → L2 로 단조 하락
#   idx    29 : L2 (두번째 저점)
#   idx 30-39 : L2 → 회복 단조 상승 (우측창 + 꼬리)

_N = 40
_L1_IDX = 15
_PEAK_IDX = 22
_L2_IDX = 29
_BASE_PRICE = 10000.0


def _make_w_df(
    drop_pct: float = -4.0,
    rvol_mult: float = 3.0,
    rebound_pct: float = 3.0,
    l2_diff_pct: float = 0.1,   # (L1-L2)/L1*100 — 양수면 L2가 L1보다 낮음
    l1_vol: float = 5000.0,
    l2_vol: float = 2000.0,
    base_vol: float = 1000.0,
) -> pd.DataFrame:
    low = np.zeros(_N)
    high = np.zeros(_N)
    volume = np.full(_N, base_vol)

    l1_low = _BASE_PRICE * (1 + drop_pct / 100)
    l2_low = l1_low * (1 - l2_diff_pct / 100)
    peak_high = l1_low * (1 + rebound_pct / 100)

    # idx 0-9: 평탄
    low[0:10] = _BASE_PRICE * 0.9995
    # idx 10-14: base -> L1 단조 하락 (좌측창)
    low[10:15] = np.linspace(_BASE_PRICE * 0.999, l1_low * 1.002, 5)
    # idx 15: L1
    low[15] = l1_low
    # idx 16-22: L1 -> 반등 고점 단조 상승
    low[16:23] = np.linspace(l1_low * 1.002, peak_high * 0.995, 7)
    # idx 23-28: 반등 고점 -> L2 단조 하락
    low[23:29] = np.linspace(peak_high * 0.99, l2_low * 1.002, 6)
    # idx 29: L2
    low[29] = l2_low
    # idx 30-39: L2 -> 회복 단조 상승 (우측창 + 꼬리)
    low[30:40] = np.linspace(l2_low * 1.002, l2_low * 1.02, 10)

    high = low * 1.004
    high[_PEAK_IDX] = peak_high  # 반등 고점 (rebound_high 계산의 기준)

    close = (high + low) / 2
    close[max(0, _L1_IDX - 5)] = _BASE_PRICE  # STEP1 drop_pct 계산 기준점 고정
    open_ = close.copy()

    volume[_L1_IDX] = l1_vol
    volume[_L2_IDX] = l2_vol
    avg_window_start = max(0, _L1_IDX - 20)
    volume[avg_window_start:_L1_IDX] = base_vol  # RVOL 분모 고정

    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })
    return df


def _clear_of(df: pd.DataFrame) -> float:
    """df의 vwap/ema20 계산값보다 확실히 높은 current_price (둘 다 통과 보장)."""
    vwap_df = EntryTimingAnalyzer().calculate_vwap(df.copy(), use_rolling=True, rolling_window=20)
    vwap_val = float(vwap_df["vwap"].dropna().iloc[-1])
    ema_val = float(_ema(df["close"], 20).iloc[-1])
    return max(vwap_val, ema_val) * 1.05


def _below_vwap(df: pd.DataFrame) -> float:
    vwap_df = EntryTimingAnalyzer().calculate_vwap(df.copy(), use_rolling=True, rolling_window=20)
    vwap_val = float(vwap_df["vwap"].dropna().iloc[-1])
    return vwap_val * 0.95


def _below_ema(df: pd.DataFrame) -> float:
    ema_val = float(_ema(df["close"], 20).iloc[-1])
    return ema_val * 0.95


# ─── 테스트 ──────────────────────────────────────────────────────────────────

def test_normal_w_detected_passes():
    df = _make_w_df()
    f = WPatternFilter(_cfg())
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is True
    assert result.reason == "PASS"
    assert result.confidence == 100.0
    assert result.l1_price > 0 and result.l2_price > 0


def test_l2_break_fails():
    df = _make_w_df(l2_diff_pct=1.0)  # L1 대비 1% 이탈 > max_bottom_diff_pct(0.3%)
    f = WPatternFilter(_cfg())
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is False
    assert result.reason == "L2_BREAK"


def test_insufficient_rebound_fails():
    df = _make_w_df(rebound_pct=0.5)  # min_rebound_pct(1.5%) 미달
    f = WPatternFilter(_cfg())
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is False
    assert result.reason == "NO_REBOUND"


def test_no_drop_fails():
    df = _make_w_df(drop_pct=-1.0)  # min_drop_pct(-3.0%) 미달
    f = WPatternFilter(_cfg())
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is False
    assert result.reason == "NO_DROP"


def test_volume_decline_required_and_violated_fails():
    df = _make_w_df(l1_vol=3000.0, l2_vol=6000.0)  # L2 거래량이 L1보다 큼
    f = WPatternFilter(_cfg(require_volume_decline=True))
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is False
    assert result.reason == "VOLUME_NOT_DECLINED"


def test_volume_decline_not_required_bypasses():
    df = _make_w_df(l1_vol=3000.0, l2_vol=6000.0)
    f = WPatternFilter(_cfg(require_volume_decline=False))
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is True


def test_no_choch_fails():
    df = _make_w_df()
    f = WPatternFilter(_cfg())
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=False, bos_detected=True)
    assert result.detected is False
    assert result.reason == "NO_CHOCH"


def test_no_bos_does_not_block_but_lowers_confidence():
    df = _make_w_df()
    f = WPatternFilter(_cfg())
    price = _clear_of(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=False)
    assert result.detected is True
    assert result.confidence == 80.0  # 100 - BOS 20점


def test_vwap_required_and_violated_fails():
    df = _make_w_df()
    f = WPatternFilter(_cfg(require_vwap=True, require_ema20=False))
    price = _below_vwap(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is False
    assert result.reason == "NO_VWAP"


def test_ema20_required_and_violated_fails():
    df = _make_w_df()
    f = WPatternFilter(_cfg(require_vwap=False, require_ema20=True))
    price = _below_ema(df)
    result = f.evaluate(df=df, current_price=price, choch_detected=True, bos_detected=True)
    assert result.detected is False
    assert result.reason == "NO_EMA20"


def test_confidence_calculation_all_components():
    df = _make_w_df()
    f = WPatternFilter(_cfg(require_vwap=False, require_ema20=False))
    price_high = _clear_of(df)
    result_all = f.evaluate(df=df, current_price=price_high, choch_detected=True, bos_detected=True)
    assert result_all.detected is True
    assert result_all.confidence == 100.0  # 40 + 20 + 20 + 10 + 10

    price_low = min(_below_vwap(df), _below_ema(df))
    result_min = f.evaluate(df=df, current_price=price_low, choch_detected=True, bos_detected=False)
    assert result_min.detected is True
    assert result_min.confidence == 60.0  # L2 held(40) + CHoCH(20), BOS/VWAP/EMA 미충족


def test_disabled_config_flag():
    """
    enabled=False 는 evaluate() 내부 로직이 아니라 main_auto_trading.py 호출부의
    `if signal and self.w_pattern_filter.enabled:` 가드에서 우회를 보장한다.
    여기서는 플래그 자체만 확인한다.
    """
    f = WPatternFilter(_cfg(enabled=False))
    assert f.enabled is False
