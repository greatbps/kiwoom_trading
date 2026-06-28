"""
tests/unit/test_smc_synthetic.py — SMC 합성 패턴 자동 검증

목적:
  - 시장 데이터 없이 "코드가 설계대로 실행되는가" 검증
  - assertion 기반: 기대 결과 → 실제 결과 자동 비교
  - 컴포넌트 분리 테스트 (structure / CHoCH / lookup) 독립 검증

실행:
  python3 -m pytest tests/unit/test_smc_synthetic.py -v
  python3 tests/unit/test_smc_synthetic.py        (standalone)
"""

import sys
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
from datetime import datetime, timedelta

from analyzers.smc.smc_utils import SwingPoint, find_swing_points
from analyzers.smc.smc_structure import (
    SMCStructureAnalyzer, MarketStructure, MarketTrend, StructureBreak
)

# ── Synthetic DataFrame 생성 헬퍼 ─────────────────────────────────────────────

def _make_df(prices: list, lb_wick: float = 0.002) -> pd.DataFrame:
    """price 리스트 → 5분봉 OHLCV.
    open = prev close, high = close*(1+lb_wick), low = close*(1-lb_wick)
    """
    base = datetime(2026, 1, 1, 9, 0)
    rows = []
    for i, p in enumerate(prices):
        prev = prices[i - 1] if i > 0 else p
        rows.append({
            "open":   float(prev),
            "high":   float(p * (1 + lb_wick)),
            "low":    float(p * (1 - lb_wick)),
            "close":  float(p),
            "volume": 1000,
        })
    times = [base + timedelta(minutes=5 * i) for i in range(len(rows))]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(times))


def _bearish_structure_prices(lookback: int = 5) -> tuple[list, float]:
    """LH + LL 패턴이 명확한 하락 구조 가격 시퀀스.

    Returns (prices, last_lh_price)
    lookback 양쪽을 padding으로 확보해 스윙 포인트 확정.
    """
    pad = lookback + 1  # 각 스윙 포인트 좌우 padding
    # 배치: [pad개 flat] [고점] [pad개 flat] [저점] [pad개 flat] × 반복
    flat = 90.0
    # 스윙 시퀀스: H1=104, L1=84, LH=101, LL=80, LH=98, LL=77
    peaks   = [104.0, 84.0, 101.0, 80.0, 98.0, 77.0]
    prices  = []
    for peak in peaks:
        prices += [flat] * pad + [peak] + [flat] * pad

    # 마지막 LH 레벨 (구조에서 확인된 LH: 98.0)
    last_lh = 98.0
    return prices, last_lh


def _add_choch_candle(prices: list, lh_level: float) -> list:
    """기존 가격 시퀀스 끝에 CHoCH 봉 + 확정봉 추가.

    CHoCH 봉: open=77(직전 저점 근처), high=lh*1.02, close=lh*1.015, body>50%
    확정봉  : 그냥 다음 봉 (detect_choch가 [-2]를 사용하므로 필요)
    """
    choch_open  = 77.0
    choch_high  = round(lh_level * 1.022, 2)  # LH 강하게 돌파
    choch_low   = 75.0
    choch_close = round(lh_level * 1.016, 2)  # close > LH
    # body ratio = |close-open| / (high-low)
    body = abs(choch_close - choch_open)
    rng  = choch_high - choch_low
    assert body / rng >= 0.5, f"body_ratio={body/rng:.2f} 낮음"

    confirm_close = choch_close + 0.5

    return prices + [choch_close, confirm_close]  # 실제 값은 아래서 직접 교체


def _make_choch_df(lookback: int = 5) -> tuple[pd.DataFrame, float]:
    """CHoCH 발생 직전 상태 DataFrame.

    구조: BEARISH (LH+LL 확정) + 마지막 2봉 = [CHoCH봉, 확정봉]
    CHoCH봉 = df.iloc[-2] (detect_choch 기준)
    """
    prices, lh_level = _bearish_structure_prices(lookback)
    flat_tail = [90.0] * (lookback + 1)  # LL 확정 후 flat
    prices += flat_tail

    df = _make_df(prices)

    # 끝에 CHoCH 봉 + 확정봉 추가 (직접 행 추가)
    last_t   = df.index[-1]
    choch_t  = last_t + timedelta(minutes=5)
    conf_t   = choch_t + timedelta(minutes=5)

    choch_open  = 77.0
    choch_high  = round(lh_level * 1.022, 2)
    choch_low   = 75.0
    choch_close = round(lh_level * 1.016, 2)

    choch_row = pd.DataFrame(
        [{"open": choch_open, "high": choch_high,
          "low": choch_low, "close": choch_close, "volume": 2000}],
        index=pd.DatetimeIndex([choch_t])
    )
    conf_row = pd.DataFrame(
        [{"open": choch_close, "high": choch_close + 0.5,
          "low": choch_close - 0.5, "close": choch_close + 0.2, "volume": 1500}],
        index=pd.DatetimeIndex([conf_t])
    )
    df = pd.concat([df, choch_row, conf_row])
    return df, lh_level


# ── 테스트 러너 (pytest 없이도 동작) ──────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []

def _test(name: str, fn):
    try:
        fn()
        _results.append((name, True, ""))
        print(f"  ✅  {name}")
    except AssertionError as e:
        _results.append((name, False, str(e)))
        print(f"  ❌  {name}")
        print(f"       → {e}")
    except Exception as e:
        _results.append((name, False, f"EXCEPTION: {e}"))
        print(f"  💥  {name}")
        traceback.print_exc()


# ══════════════════════════════════════════════════════════════════════════════
# T1 — swing_lookback별 감지 수 (EXP-001 핵심 진단 재현)
# ══════════════════════════════════════════════════════════════════════════════

def test_lb20_collapses_swing_count():
    """lb=20 시 lb=5 대비 swing_count 대폭 감소 — EXP-001 핵심 진단 재현."""
    prices, _ = _bearish_structure_prices(lookback=5)
    df = _make_df(prices)

    cnt5  = len(find_swing_points(df, lookback=5,  min_swing_size_pct=0.0))
    cnt10 = len(find_swing_points(df, lookback=10, min_swing_size_pct=0.0))
    cnt20 = len(find_swing_points(df, lookback=20, min_swing_size_pct=0.0))

    assert cnt5 >= 4,    f"lb=5: swing_count={cnt5} (≥4 필요)"
    assert cnt10 >= 2,   f"lb=10: swing_count={cnt10} (≥2 필요)"
    assert cnt20 <= cnt10, f"lb=20({cnt20}) > lb=10({cnt10}) — lookback 증가가 오히려 더 많음"
    assert cnt5 > cnt20, f"lb=5({cnt5}) <= lb=20({cnt20}) — lookback 영향 없음"


# ══════════════════════════════════════════════════════════════════════════════
# T2 — BEARISH 추세 분류 (LH+LL 패턴)
# ══════════════════════════════════════════════════════════════════════════════

def test_bearish_trend_classified():
    """LH+LL 패턴 → trend=BEARISH 분류 검증."""
    prices, _ = _bearish_structure_prices(lookback=5)
    df = _make_df(prices)
    analyzer = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    structure = analyzer.analyze_structure(df)

    assert structure.trend == MarketTrend.BEARISH, (
        f"trend={structure.trend} (BEARISH 필요) "
        f"swing_count={len(structure.swing_points)}"
    )
    assert structure.last_lh is not None, "last_lh=None (LH 미감지)"
    assert structure.last_ll is not None, "last_ll=None (LL 미감지)"
    assert structure.last_hh is None,     f"last_hh={structure.last_hh} (HH가 존재하면 BEARISH 불가)"


# ══════════════════════════════════════════════════════════════════════════════
# T3 — CHoCH 감지 (컴포넌트 분리 테스트)
# MarketStructure를 직접 생성해 detect_choch()만 독립 검증
# ══════════════════════════════════════════════════════════════════════════════

def test_choch_detected_on_lh_break():
    """BEARISH 구조 + LH 상향 돌파 봉 → bullish CHoCH 감지."""
    # 1) MarketStructure 직접 구성 (structure 분석 우회 — 컴포넌트 분리)
    lh_level = 98.0
    lh_sp = SwingPoint(index=30, price=lh_level, type="high",
                       timestamp=datetime(2026, 1, 1, 11, 30))
    structure = MarketStructure(
        trend    = MarketTrend.BEARISH,
        last_lh  = lh_sp,
        last_ll  = SwingPoint(index=35, price=77.0, type="low",
                              timestamp=datetime(2026, 1, 1, 12, 0)),
        swing_points=[lh_sp],
    )

    # 2) CHoCH 봉 포함 DataFrame (최소: 2개 봉)
    choch_open  = 77.0
    choch_high  = round(lh_level * 1.022, 2)   # 돌파
    choch_low   = 75.0
    choch_close = round(lh_level * 1.016, 2)   # close > LH
    body  = abs(choch_close - choch_open)
    rng   = choch_high - choch_low
    assert body / rng >= 0.5, f"테스트 설계 오류: body_ratio={body/rng:.2f}"

    base = datetime(2026, 1, 1, 13, 0)
    df_mini = pd.DataFrame([
        {"open": choch_open, "high": choch_high,
         "low": choch_low,   "close": choch_close, "volume": 2000},
        {"open": choch_close, "high": choch_close + 0.5,
         "low": choch_close - 0.5, "close": choch_close + 0.2, "volume": 1500},
    ], index=pd.DatetimeIndex([base, base + timedelta(minutes=5)]))

    # 3) detect_choch 호출 — structure는 직접 주입
    analyzer = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    choch    = analyzer.detect_choch(df_mini, structure, config={})

    assert choch is not None, (
        f"CHoCH=None. "
        f"lh={lh_level}, high={choch_high}, close={choch_close}, "
        f"body_ratio={body/rng:.2f}"
    )
    assert choch.direction == "bullish", f"direction={choch.direction} (bullish 필요)"
    assert abs(choch.broken_level - lh_level) < 0.01, (
        f"broken_level={choch.broken_level} ≠ lh_level={lh_level}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# T4 — CHoCH 미발생: LH 미돌파
# ══════════════════════════════════════════════════════════════════════════════

def test_no_choch_when_below_lh():
    """BEARISH 구조이지만 마지막 봉이 LH 미돌파 → CHoCH=None."""
    lh_level = 98.0
    structure = MarketStructure(
        trend   = MarketTrend.BEARISH,
        last_lh = SwingPoint(index=30, price=lh_level, type="high",
                             timestamp=datetime(2026, 1, 1, 11, 30)),
        last_ll = SwingPoint(index=35, price=77.0, type="low",
                             timestamp=datetime(2026, 1, 1, 12, 0)),
    )

    base = datetime(2026, 1, 1, 13, 0)
    df_mini = pd.DataFrame([
        # high=95 < LH=98 → 돌파 없음
        {"open": 77.0, "high": 95.0, "low": 75.0, "close": 88.0, "volume": 1000},
        {"open": 88.0, "high": 89.0, "low": 87.0, "close": 88.5, "volume": 800},
    ], index=pd.DatetimeIndex([base, base + timedelta(minutes=5)]))

    analyzer = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    choch    = analyzer.detect_choch(df_mini, structure, config={})

    assert choch is None, f"CHoCH={choch} (LH 미돌파인데 감지됨 — 오탐)"


# ══════════════════════════════════════════════════════════════════════════════
# T5 — RANGING에서 CHoCH 없음 (2026-03-07 변경 회귀 테스트)
# ══════════════════════════════════════════════════════════════════════════════

def test_no_choch_in_ranging():
    """trend=RANGING → CHoCH=None. 2026-03-07 RANGING 제거 정책 검증."""
    structure = MarketStructure(
        trend   = MarketTrend.RANGING,
        last_lh = SwingPoint(index=20, price=98.0, type="high",
                             timestamp=datetime(2026, 1, 1, 11, 0)),
        last_hl = SwingPoint(index=25, price=88.0, type="low",
                             timestamp=datetime(2026, 1, 1, 11, 25)),
    )

    base = datetime(2026, 1, 1, 13, 0)
    df_mini = pd.DataFrame([
        {"open": 88.0, "high": 100.0, "low": 87.0, "close": 99.5, "volume": 2000},
        {"open": 99.5, "high": 100.0, "low": 98.0, "close": 99.8, "volume": 1500},
    ], index=pd.DatetimeIndex([base, base + timedelta(minutes=5)]))

    analyzer = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    choch    = analyzer.detect_choch(df_mini, structure, config={})

    assert choch is None, (
        f"CHoCH={choch} (RANGING에서 CHoCH 감지됨 — 2026-03-07 RANGING 제거 정책 위반)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# T6 — choch_penetration_pct=0.2 적용 (2026-03-18 변경 회귀 테스트)
# ══════════════════════════════════════════════════════════════════════════════

def test_choch_penetration_blocks_weak_break():
    """penetration=0.2% 미달 돌파 → CHoCH 차단. 2026-03-18 변경 검증."""
    lh_level = 100.0
    structure = MarketStructure(
        trend   = MarketTrend.BEARISH,
        last_lh = SwingPoint(index=30, price=lh_level, type="high",
                             timestamp=datetime(2026, 1, 1, 11, 30)),
        last_ll = SwingPoint(index=35, price=80.0, type="low",
                             timestamp=datetime(2026, 1, 1, 12, 0)),
    )

    base = datetime(2026, 1, 1, 13, 0)
    # high = 100.1 → penetration = lh*0.002 = 0.2 → 필요 최솟값 = 100.2
    # 100.1 < 100.2 → 차단되어야 함
    df_mini = pd.DataFrame([
        {"open": 80.0, "high": 100.1, "low": 79.0, "close": 100.05, "volume": 2000},
        {"open": 100.05, "high": 100.1, "low": 99.5, "close": 100.0, "volume": 1500},
    ], index=pd.DatetimeIndex([base, base + timedelta(minutes=5)]))

    analyzer = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    cfg_with_pen = {"choch_penetration_pct": 0.2}
    choch_blocked = analyzer.detect_choch(df_mini, structure, config=cfg_with_pen)
    choch_free    = analyzer.detect_choch(df_mini, structure, config={})

    assert choch_blocked is None, (
        f"penetration=0.2%인데 CHoCH 감지됨 (high={100.1}, 필요={lh_level * 1.002:.3f})"
    )
    assert choch_free is not None, (
        "penetration=0.0%에서도 CHoCH 미감지 — 기준 데이터 설계 문제"
    )


# ══════════════════════════════════════════════════════════════════════════════
# T7 — body_ratio < 0.5 차단 (2026-03-10 변경 회귀 테스트)
# ══════════════════════════════════════════════════════════════════════════════

def test_choch_blocked_by_low_body_ratio():
    """body_ratio < 0.5 (위꼬리 돌파) → CHoCH 차단. 2026-03-10 변경 검증."""
    lh_level = 98.0
    structure = MarketStructure(
        trend   = MarketTrend.BEARISH,
        last_lh = SwingPoint(index=30, price=lh_level, type="high",
                             timestamp=datetime(2026, 1, 1, 11, 30)),
        last_ll = SwingPoint(index=35, price=77.0, type="low",
                             timestamp=datetime(2026, 1, 1, 12, 0)),
    )

    base = datetime(2026, 1, 1, 13, 0)
    # 위꼬리 돌파: open=95, high=103, low=94, close=96
    # body = |96-95| = 1, range = |103-94| = 9, ratio = 0.11 < 0.5 → 차단
    df_mini = pd.DataFrame([
        {"open": 95.0, "high": 103.0, "low": 94.0, "close": 96.0, "volume": 2000},
        {"open": 96.0, "high": 97.0,  "low": 95.5, "close": 96.5, "volume": 1500},
    ], index=pd.DatetimeIndex([base, base + timedelta(minutes=5)]))

    body  = abs(96.0 - 95.0)
    rng   = 103.0 - 94.0
    assert body / rng < 0.5, f"테스트 설계 오류: body_ratio={body/rng:.2f} >= 0.5"

    analyzer = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    choch    = analyzer.detect_choch(df_mini, structure, config={})

    assert choch is None, (
        f"위꼬리 돌파인데 CHoCH 감지됨 (body_ratio={body/rng:.2f})"
    )


# ══════════════════════════════════════════════════════════════════════════════
# T8 — Swing Extractor: find_swing_points 직접 검증
# ══════════════════════════════════════════════════════════════════════════════

def test_swing_extractor_high_low_types():
    """find_swing_points가 올바른 type(high/low)으로 스윙 포인트 반환."""
    prices, _ = _bearish_structure_prices(lookback=5)
    df = _make_df(prices)
    swings = find_swing_points(df, lookback=5, min_swing_size_pct=0.0)

    highs = [s for s in swings if s.type == "high"]
    lows  = [s for s in swings if s.type == "low"]

    assert len(highs) >= 2, f"swing high 수={len(highs)} (≥2 필요)"
    assert len(lows)  >= 2, f"swing low 수={len(lows)} (≥2 필요)"
    # 고점은 저점보다 가격이 높아야 함
    assert highs[-1].price > lows[-1].price, (
        f"마지막 고점({highs[-1].price}) <= 마지막 저점({lows[-1].price})"
    )


def test_swing_extractor_min_size_filter():
    """min_swing_size_pct: 작은 스윙 제거, 큰 스윙 유지."""
    lb = 5
    pad = lb + 1
    # 작은 노이즈(0.1% 돌출) + 큰 스윙(2% 돌출)
    base_p = 100.0
    prices = [base_p] * pad
    prices += [base_p * 1.001]          # 0.1% 고점 — 노이즈
    prices += [base_p] * pad
    prices += [base_p * 1.02]           # 2.0% 고점 — 유효 스윙
    prices += [base_p] * pad

    df = _make_df(prices)

    swings_strict = find_swing_points(df, lookback=lb, min_swing_size_pct=1.0)
    swings_loose  = find_swing_points(df, lookback=lb, min_swing_size_pct=0.0)

    highs_strict = [s for s in swings_strict if s.type == "high"]
    highs_loose  = [s for s in swings_loose  if s.type == "high"]

    assert len(highs_loose)  >= 2, f"min_size=0: high 수={len(highs_loose)} (≥2 필요)"
    assert len(highs_strict) < len(highs_loose), (
        f"min_size=1%: 노이즈 제거 안 됨 "
        f"(strict={len(highs_strict)}, loose={len(highs_loose)})"
    )


def test_swing_extractor_lh_ll_labels():
    """LH+LL 패턴 → last_lh/last_ll 정확히 추출."""
    prices, expected_lh = _bearish_structure_prices(lookback=5)
    df = _make_df(prices)

    analyzer  = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    structure = analyzer.analyze_structure(df)

    assert structure.last_lh is not None, "last_lh=None"
    assert structure.last_ll is not None, "last_ll=None"
    # last_lh 가격이 기대값(98.0)과 일치 (±2% 허용)
    lh_pct_diff = abs(structure.last_lh.price - expected_lh) / expected_lh * 100
    assert lh_pct_diff < 2.0, (
        f"last_lh.price={structure.last_lh.price:.1f} ≠ expected={expected_lh} "
        f"(diff={lh_pct_diff:.2f}%)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# T9 — Integration: 전체 체인 CHoCH 파괴 재현 (2026-03-07 근본 원인)
#
# 발견 (2026-05-27):
#   CHoCH 봉 자체가 swing detection에서 HH로 분류됨
#   → trend = RANGING
#   → 2026-03-07 이후: BEARISH만 허용 → CHoCH = None
#   → Feb 25 이후 signal 0건의 구조적 원인
# ══════════════════════════════════════════════════════════════════════════════

def test_integration_choch_candle_becomes_hh_ranging():
    """[현상 고정] CHoCH 봉이 HH로 분류돼 trend=RANGING → 현재 코드에서 CHoCH=None.

    이 테스트는 현재 코드의 동작을 그대로 고정(freeze)한다.
    만약 이 테스트가 통과하면 → 문제가 재현되고 있음을 확인.
    만약 이 테스트가 실패하면 → 코드 수정으로 동작이 바뀐 것.
    """
    lb = 5
    prices = [90.0] * 43
    prices[5]=100.0; prices[15]=84.0; prices[20]=96.0
    prices[25]=80.0; prices[30]=93.0; prices[35]=77.0

    base = datetime(2026, 1, 1, 9, 0)
    rows = [{"open":p,"high":p*1.002,"low":p*0.998,"close":p,"volume":1000}
            for p in prices]
    rows[41] = {"open":77.0,"high":95.0,"low":76.0,"close":94.0,"volume":2000}
    rows[42] = {"open":94.0,"high":95.0,"low":93.0,"close":94.5,"volume":1500}
    df = pd.DataFrame(rows,
                      index=pd.DatetimeIndex([base + timedelta(minutes=5*i)
                                              for i in range(43)]))

    analyzer  = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=0.0)
    structure = analyzer.analyze_structure(df)
    choch     = analyzer.detect_choch(df, structure, config={})

    # 현상 고정: CHoCH 봉이 HH를 만들어 RANGING이 된 상태
    assert structure.trend == MarketTrend.RANGING, (
        f"trend={structure.trend} — 현상 변경됨. CHoCH 봉 HH 분류 로직 바뀌었는지 확인"
    )
    assert choch is None, (
        f"choch={choch} — 현재 코드에서 CHoCH 감지됨. 2026-03-07 RANGING 제거 정책 확인"
    )


def test_integration_ranging_allowed_restores_choch():
    """[해결 방향 검증] RANGING 허용 시 CHoCH 감지됨 (구 코드 동작 재현).

    이 테스트는 2026-03-07 이전 코드 동작을 검증한다.
    RANGING에서도 CHoCH를 허용하면 → 전체 체인이 작동함.
    """
    lb = 5
    prices = [90.0] * 43
    prices[5]=100.0; prices[15]=84.0; prices[20]=96.0
    prices[25]=80.0; prices[30]=93.0; prices[35]=77.0

    base = datetime(2026, 1, 1, 9, 0)
    rows = [{"open":p,"high":p*1.002,"low":p*0.998,"close":p,"volume":1000}
            for p in prices]
    rows[41] = {"open":77.0,"high":95.0,"low":76.0,"close":94.0,"volume":2000}
    rows[42] = {"open":94.0,"high":95.0,"low":93.0,"close":94.5,"volume":1500}
    df = pd.DataFrame(rows,
                      index=pd.DatetimeIndex([base + timedelta(minutes=5*i)
                                              for i in range(43)]))

    analyzer  = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=0.0)
    structure = analyzer.analyze_structure(df)

    # 수동으로 RANGING 포함 조건 시뮬레이션 (구 코드)
    df2 = df.copy()
    df2.columns = [c.lower() for c in df2.columns]
    last_candle = df2.iloc[len(df2) - 2]
    lh_level    = structure.last_lh.price
    c_range     = last_candle["high"] - last_candle["low"]
    c_body      = abs(last_candle["close"] - last_candle["open"])
    body_ratio  = c_body / c_range if c_range > 0 else 0

    choch_would_fire = (
        structure.trend in [MarketTrend.BEARISH, MarketTrend.RANGING]
        and lh_level is not None
        and last_candle["high"] > lh_level
        and last_candle["close"] > lh_level
        and body_ratio >= 0.5
    )

    assert choch_would_fire, (
        f"RANGING 허용해도 CHoCH 미발생. "
        f"trend={structure.trend}, lh={lh_level}, "
        f"high={last_candle['high']:.1f}, close={last_candle['close']:.1f}, "
        f"body_ratio={body_ratio:.2f}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# T10 — Invariant Tests (구조 불변 조건)
# 모든 analyze_structure() 결과가 반드시 만족해야 하는 규칙
# ══════════════════════════════════════════════════════════════════════════════

def _assert_structure_invariants(structure, label: str = ""):
    """MarketStructure 불변 조건 검증 헬퍼."""
    tag = f"[{label}] " if label else ""

    assert structure.trend in [MarketTrend.BULLISH, MarketTrend.BEARISH, MarketTrend.RANGING], (
        f"{tag}trend={structure.trend} — 유효하지 않은 추세값"
    )
    assert len(structure.swing_points) >= 0, f"{tag}swing_count 음수"

    # BULLISH: HH + HL 존재, HH.price > HL.price
    if structure.trend == MarketTrend.BULLISH:
        assert structure.last_hh is not None, f"{tag}BULLISH인데 last_hh=None"
        assert structure.last_hl is not None, f"{tag}BULLISH인데 last_hl=None"
        assert structure.last_hh.price > structure.last_hl.price, (
            f"{tag}BULLISH: hh({structure.last_hh.price}) <= hl({structure.last_hl.price})"
        )

    # BEARISH: LH + LL 존재, LH.price > LL.price
    if structure.trend == MarketTrend.BEARISH:
        assert structure.last_lh is not None, f"{tag}BEARISH인데 last_lh=None"
        assert structure.last_ll is not None, f"{tag}BEARISH인데 last_ll=None"
        assert structure.last_lh.price > structure.last_ll.price, (
            f"{tag}BEARISH: lh({structure.last_lh.price}) <= ll({structure.last_ll.price})"
        )

    # 중복 인덱스 없음
    indices = [sp.index for sp in structure.swing_points]
    high_indices = [sp.index for sp in structure.swing_points if sp.type == "high"]
    low_indices  = [sp.index for sp in structure.swing_points if sp.type == "low"]
    assert len(high_indices) == len(set(high_indices)), f"{tag}중복 swing high 인덱스"
    assert len(low_indices)  == len(set(low_indices)),  f"{tag}중복 swing low 인덱스"


def test_invariant_bullish_structure():
    """BULLISH 구조 불변 조건: last_hh > last_hl, 타입 유효."""
    lb = 5
    pad = lb + 1
    # HH + HL 패턴
    prices = ([90.0] * pad + [100.0] +   # H1
              [90.0] * pad + [85.0]  +   # L1
              [90.0] * pad + [105.0] +   # HH
              [90.0] * pad + [88.0]  +   # HL
              [90.0] * pad + [110.0] +   # HH
              [90.0] * pad + [91.0]  +   # HL
              [90.0] * pad)
    df = _make_df(prices)
    analyzer  = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=0.0)
    structure = analyzer.analyze_structure(df)

    if structure.trend == MarketTrend.BULLISH:
        _assert_structure_invariants(structure, "BULLISH")
    # trend이 RANGING이어도 invariant는 통과해야 함
    _assert_structure_invariants(structure, "any")


def test_invariant_bearish_structure():
    """BEARISH 구조 불변 조건: last_lh > last_ll, 타입 유효."""
    prices, _ = _bearish_structure_prices(lookback=5)
    df = _make_df(prices)
    analyzer  = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    structure = analyzer.analyze_structure(df)

    _assert_structure_invariants(structure, "BEARISH")
    assert structure.trend == MarketTrend.BEARISH, (
        f"예상 BEARISH, 실제 {structure.trend}"
    )


def test_invariant_inside_bar_no_fake_swing():
    """Inside bar(고저가 이전 봉 내부) → 가짜 스윙 포인트 생성 없음."""
    lb = 5
    pad = lb + 1
    # 정상 스윙 하나 (high=100) 후 inside bar 연속
    prices = [90.0] * pad + [100.0] + [90.0] * pad  # 정상 고점
    inside_prices = prices.copy()
    # inside bar: high < 이전 high(90*1.002=90.18), low > 이전 low(90*0.998=89.82)
    # → 그냥 90.0 유지 (이미 surrounding과 같음)
    df_normal = _make_df(prices)
    # inside bar 구간 추가 (가격 변동 없는 5봉)
    inside_extra = [90.0] * 5
    df_with_inside = _make_df(prices + inside_extra)

    analyzer = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=0.0)
    swings_normal = find_swing_points(df_normal, lookback=lb, min_swing_size_pct=0.0)
    swings_inside = find_swing_points(df_with_inside, lookback=lb, min_swing_size_pct=0.0)

    highs_normal = [s for s in swings_normal if s.type == "high"]
    highs_inside = [s for s in swings_inside if s.type == "high"]

    # inside bar 추가가 swing high 수를 늘려선 안 됨 (±1 허용)
    assert len(highs_inside) <= len(highs_normal) + 1, (
        f"inside bar로 인해 swing high 급증: {len(highs_normal)} → {len(highs_inside)}"
    )
    _assert_structure_invariants(analyzer.analyze_structure(df_with_inside), "inside_bar")


# ══════════════════════════════════════════════════════════════════════════════
# T11 — Parameter Stability Test (L2-2)
# lb 변화 시 구조 분류가 얼마나 안정적인가
# ══════════════════════════════════════════════════════════════════════════════

def test_parameter_stability_lb():
    """lb=5~20 변화 시 swing_count 단조 감소, 구조 invariant 유지.

    좋은 시스템: lb 약간 변해도 구조 일관성 유지
    나쁜 시스템: lb 변화 → 갑자기 구조 붕괴 (trend flip, invariant 위반)
    """
    prices, _ = _bearish_structure_prices(lookback=5)
    df = _make_df(prices)

    lb_values     = [5, 7, 10, 12, 15, 20]
    swing_counts  = []
    bearish_flags = []

    for lb in lb_values:
        analyzer  = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=0.0)
        structure = analyzer.analyze_structure(df)
        _assert_structure_invariants(structure, f"lb={lb}")  # 모든 lb에서 invariant 유지
        swing_counts.append(len(structure.swing_points))
        bearish_flags.append(structure.trend == MarketTrend.BEARISH)

    # lb 증가 → swing_count 단조 감소 (±1 허용)
    for i in range(len(lb_values) - 1):
        assert swing_counts[i] >= swing_counts[i + 1] - 1, (
            f"lb={lb_values[i]}({swing_counts[i]}) < lb={lb_values[i+1]}({swing_counts[i+1]}) "
            f"— 작은 lb에서 더 적은 스윙"
        )

    # lb=5 는 BEARISH 감지해야 함 (명확한 하락 구조)
    assert bearish_flags[0], (
        f"lb=5에서 BEARISH 미감지 — 합성 데이터 설계 문제"
    )

    # 급격한 구조 붕괴 없음: lb=5 BEARISH이면 lb=7,10도 대부분 유지
    stable_bearish = sum(bearish_flags[:3])  # lb=5,7,10
    assert stable_bearish >= 2, (
        f"lb=5~10 범위에서 BEARISH 유지 횟수={stable_bearish} (≥2 필요) "
        f"— lb 변화에 구조가 너무 민감함"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 실행 진입점
# ══════════════════════════════════════════════════════════════════════════════

TESTS = [
    ("T1 lb=20 swing collapse 재현 (EXP-001 진단)",       test_lb20_collapses_swing_count),
    ("T2 BEARISH 추세 분류 (LH+LL 패턴)",                 test_bearish_trend_classified),
    ("T3 CHoCH 감지 — LH 상향 돌파 (컴포넌트 분리)",      test_choch_detected_on_lh_break),
    ("T4 CHoCH 미발생 — LH 미돌파",                       test_no_choch_when_below_lh),
    ("T5 RANGING CHoCH 없음 (2026-03-07 회귀)",           test_no_choch_in_ranging),
    ("T6 penetration_pct=0.2 차단 (2026-03-18 회귀)",     test_choch_penetration_blocks_weak_break),
    ("T7 body_ratio<0.5 차단 (2026-03-10 회귀)",          test_choch_blocked_by_low_body_ratio),
    # ── Swing Extractor 레이어
    ("T8a swing extractor: high/low type 정확성",          test_swing_extractor_high_low_types),
    ("T8b swing extractor: min_size_pct 노이즈 제거",      test_swing_extractor_min_size_filter),
    ("T8c swing extractor: LH/LL label 추출",              test_swing_extractor_lh_ll_labels),
    # ── Integration (전체 체인)
    ("T9a integration: CHoCH봉→HH→RANGING 현상 고정",     test_integration_choch_candle_becomes_hh_ranging),
    ("T9b integration: RANGING허용 시 CHoCH 복구 검증",    test_integration_ranging_allowed_restores_choch),
    # ── Invariant Tests (L1-2)
    ("T10a invariant: BULLISH 구조 일관성",                 test_invariant_bullish_structure),
    ("T10b invariant: BEARISH 구조 일관성",                 test_invariant_bearish_structure),
    ("T10c invariant: inside bar 가짜 스윙 생성 없음",      test_invariant_inside_bar_no_fake_swing),
    # ── Parameter Stability (L2-2)
    ("T11 parameter stability: lb 변화 시 구조 안정성",     test_parameter_stability_lb),
]


def main():
    print("\n" + "═" * 60)
    print("  SMC Synthetic Pattern Tests")
    print("  시장 데이터 없이 코드 동작 자동 검증")
    print("═" * 60)
    for name, fn in TESTS:
        _test(name, fn)
    print("─" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"  결과: ✅ {passed}건 통과  ❌ {failed}건 실패")
    if failed:
        print("\n  실패 케이스:")
        for name, ok, msg in _results:
            if not ok:
                print(f"    {name}: {msg}")
    print("═" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
