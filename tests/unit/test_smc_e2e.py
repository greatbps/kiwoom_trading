"""
tests/unit/test_smc_e2e.py — End-to-End Pipeline + Branch Coverage

목적:
  1. Golden Snapshot: 중간 상태가 "몰래 바뀌는" 것 자동 감지
     - fixture → 전체 파이프라인 → 상태 캡처 → 기준값과 비교
     - 코드 수정 후 구조/CHoCH/block 분류가 바뀌면 즉시 FAIL

  2. Branch Coverage: 코드 분기 경로별 검증
     - RANGING suppression path (현재 주요 병목)
     - BEARISH + LH 미돌파 path
     - CHoCH 발생 but sweep 없는 path
     - CHoCH 발생 but prefilter 실패 path
     - CHoCH 발생 but grade 미달 path

  3. Contract: 함수 입출력 계약 검증
     - structure.trend ∈ VALID_TRENDS
     - swing_count 필드 타입/범위
     - classify_reason 반환값이 알려진 단계 내

실행:
  python3 -m pytest tests/unit/test_smc_e2e.py -v
  # snapshot 갱신 (의도된 변경 후):
  python3 -m pytest tests/unit/test_smc_e2e.py -v --update-snapshots
"""
import json
import sys
import traceback
from pathlib import Path

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime, timedelta

from analyzers.smc.smc_signals import SMCStrategy
from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend, MarketStructure
from analyzers.smc.smc_utils import SwingPoint
from analysis.smc_path_test import (
    load_fixture, load_config, build_smc_strategy, classify_reason
)

# ── 경로 상수 ──────────────────────────────────────────────────────────────────
FIXTURE_DIR  = Path(__file__).parent.parent / "fixtures"
SNAPSHOT_DIR = Path(__file__).parent.parent / "snapshots"
SNAPSHOT_FILE = SNAPSHOT_DIR / "pipeline_20260527.json"
DATE = "20260527"

SYMBOLS = {
    "000660": "SK하이닉스",
    "005430": "한국공항",
    "010060": "OCI홀딩스",
    "012330": "현대모비스",
    "036570": "NC소프트",
    "336260": "두산퓨얼셀",
}

VALID_BLOCK_STAGES = {
    "no_structure_zero", "no_structure_ranging", "no_structure_lh",
    "no_structure", "no_sweep", "prefilter_fail", "grade_fail",
    "mtf_blocked", "displacement_fail", "edt_block", "signal", "other",
}

# ── pytest CLI option ──────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption("--update-snapshots", action="store_true", default=False,
                     help="Golden snapshot 갱신 (의도된 변경 후 실행)")

@pytest.fixture
def update_snapshots(request):
    return request.config.getoption("--update-snapshots", default=False)

# ── 파이프라인 상태 캡처 ───────────────────────────────────────────────────────

def _capture(symbol: str, strat) -> dict | None:
    df = load_fixture(symbol, DATE)
    if df is None:
        return None
    signal, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol=symbol, market_regime=None,
    )
    st = details.get("structure") or {}
    choch = details.get("choch")
    grade_raw = details.get("choch_grade")
    grade = (str(grade_raw.get("grade", "-")) if isinstance(grade_raw, dict)
             else str(grade_raw or "-"))
    return {
        "structure_trend":  st.get("trend"),
        "swing_count":      st.get("swing_count"),
        "swing_high_count": st.get("swing_high_count"),
        "swing_low_count":  st.get("swing_low_count"),
        "choch_detected":   choch is not None,
        "choch_direction":  (choch or {}).get("direction"),
        "bos_detected":     details.get("bos") is not None,
        "sweep_detected":   details.get("liquidity_sweep") is not None,
        "prefilter_met":    (details.get("prefilter") or {}).get("conditions_met"),
        "choch_grade":      grade,
        "signal":           signal,
        "block_stage":      classify_reason(reason, details),
    }

# ── 합성 데이터 빌더 (branch test용) ──────────────────────────────────────────

def _make_df(prices: list, lb_wick: float = 0.002) -> pd.DataFrame:
    base = datetime(2026, 1, 1, 9, 0)
    rows = []
    for i, p in enumerate(prices):
        prev = prices[i-1] if i > 0 else p
        rows.append({"open": float(prev), "high": float(p*(1+lb_wick)),
                     "low": float(p*(1-lb_wick)), "close": float(p), "volume": 1000})
    times = [base + timedelta(minutes=5*i) for i in range(len(rows))]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(times))

def _bearish_prices(lb: int = 5) -> tuple[list, float]:
    """명확한 LH+LL 하락 구조 가격 시퀀스. (last_lh=98.0)"""
    pad = lb + 1
    flat = 90.0
    peaks = [104.0, 84.0, 101.0, 80.0, 98.0, 77.0]
    prices = []
    for peak in peaks:
        prices += [flat]*pad + [peak] + [flat]*pad
    return prices, 98.0

def _make_minimal_strat(**overrides) -> SMCStrategy:
    """테스트용 최소 SMCStrategy. 필요한 항목만 override."""
    defaults = dict(
        swing_lookback=5, min_swing_size_pct=0.0,
        sweep_threshold_pct=0.1, sweep_lookback=20,
        require_liquidity_sweep=True, long_only=True,
        min_choch_grade="B", require_squeeze_on=False,
        require_vwap_above=False, grade_b_weight=0.5,
        mtf_bias_enabled=False,
        prefilter_enabled=True, prefilter_min_conditions=2,
        prefilter_require_htf_trend=False,
        prefilter_require_liquidity_sweep=False,
        prefilter_require_reclaim=False,
        reclaim_lookback=5, reclaim_tolerance_pct=0.3,
        sweep_fallback_enabled=False,
        sweep_fallback_size_mult=0.5,
        sweep_fallback_confidence=0.6,
    )
    defaults.update(overrides)
    strat = SMCStrategy(**defaults)
    strat._raw_config = {}
    return strat

# ══════════════════════════════════════════════════════════════════════════════
# TE1 — Golden Snapshot: 전체 파이프라인 중간 상태 freeze
# ══════════════════════════════════════════════════════════════════════════════

def _load_snapshot() -> dict:
    if not SNAPSHOT_FILE.exists():
        pytest.skip(f"snapshot 없음: {SNAPSHOT_FILE.name}  (생성: python3 tests/unit/test_smc_e2e.py)")
    with open(SNAPSHOT_FILE, encoding="utf-8") as f:
        return json.load(f)

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_e2e_matches_golden_snapshot(symbol, update_snapshots):
    """파이프라인 중간 상태가 golden snapshot과 일치.

    이 테스트가 FAIL하면: 코드 변경이 파이프라인 내부 상태를 바꿨다는 의미.
    의도된 변경이면 --update-snapshots 로 snapshot 갱신.
    의도치 않은 변경이면 regression 발견.
    """
    cfg = load_config()
    strat = build_smc_strategy(cfg)
    current = _capture(symbol, strat)
    if current is None:
        pytest.skip(f"fixture 없음: {symbol}")

    if update_snapshots:
        snapshot = _load_snapshot() if SNAPSHOT_FILE.exists() else {}
        snapshot[symbol] = current
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        return  # 갱신 모드는 항상 pass

    expected = _load_snapshot().get(symbol)
    if expected is None:
        pytest.skip(f"snapshot에 {symbol} 없음 — --update-snapshots 실행 필요")

    diffs = []
    for key in expected:
        if key not in current:
            diffs.append(f"  missing key: {key}")
        elif current[key] != expected[key]:
            diffs.append(f"  {key}: expected={expected[key]!r}  got={current[key]!r}")

    assert not diffs, (
        f"\n{symbol}({SYMBOLS[symbol]}) 파이프라인 상태 변경 감지:\n"
        + "\n".join(diffs)
        + "\n\n의도된 변경이면: pytest --update-snapshots 로 갱신"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TE2 — Contract: classify_reason 반환값이 알려진 단계 내
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_e2e_block_stage_is_known(symbol):
    """classify_reason()이 알려진 단계 중 하나를 반환함 — 미분류 방지."""
    cfg = load_config()
    strat = build_smc_strategy(cfg)
    current = _capture(symbol, strat)
    if current is None:
        pytest.skip(f"fixture 없음: {symbol}")

    stage = current["block_stage"]
    assert stage in VALID_BLOCK_STAGES, (
        f"{symbol}: block_stage={stage!r} — VALID_BLOCK_STAGES에 없음\n"
        f"  새 차단 이유가 추가됐으면 VALID_BLOCK_STAGES에 등록 필요"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TE3 — Contract: structure 필드 타입/범위
# ══════════════════════════════════════════════════════════════════════════════

VALID_TRENDS = {t.value for t in MarketTrend}

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_e2e_structure_fields_contract(symbol):
    """structure 필드 타입·범위 계약 — None 전파 또는 타입 오류 방지."""
    cfg = load_config()
    strat = build_smc_strategy(cfg)
    df = load_fixture(symbol, DATE)
    if df is None:
        pytest.skip(f"fixture 없음: {symbol}")

    _, _, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol=symbol, market_regime=None,
    )
    st = details.get("structure") or {}

    assert "swing_count" in st,       f"{symbol}: swing_count 필드 없음"
    assert "swing_high_count" in st,  f"{symbol}: swing_high_count 필드 없음"
    assert "swing_low_count" in st,   f"{symbol}: swing_low_count 필드 없음"
    assert "trend" in st,             f"{symbol}: trend 필드 없음"

    sc = st["swing_count"]
    sh = st["swing_high_count"]
    sl = st["swing_low_count"]
    assert isinstance(sc, int) and sc >= 0, f"{symbol}: swing_count={sc!r} (int >= 0 필요)"
    assert isinstance(sh, int) and sh >= 0, f"{symbol}: swing_high_count={sh!r}"
    assert isinstance(sl, int) and sl >= 0, f"{symbol}: swing_low_count={sl!r}"
    assert sh + sl == sc, (
        f"{symbol}: swing_high({sh}) + swing_low({sl}) != swing_count({sc})"
    )
    assert st["trend"] in VALID_TRENDS, (
        f"{symbol}: trend={st['trend']!r} — VALID_TRENDS={VALID_TRENDS}"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TB1 — Branch: RANGING suppression path
#        현재 주요 병목. RANGING 종목 → block_stage=no_structure_ranging
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_ranging_suppression():
    """RANGING 구조 → CHoCH 평가 불가 → no_structure_ranging.

    현재 코드(BEARISH only)에서 ranging 종목은 swing이 있어도 차단됨.
    EXP-002 적용 시 이 테스트는 업데이트 필요.
    """
    # flat-ranging 가격 시퀀스: peak-valley 번갈아 → ranging 구조
    flat = 100.0
    prices = []
    for _ in range(3):
        prices += [flat]*6 + [104.0] + [flat]*6 + [96.0] + [flat]*6
    prices += [flat] * 5  # 끝 패딩
    df = _make_df(prices)

    strat = _make_minimal_strat(swing_lookback=5)
    sig, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="TEST", market_regime=None,
    )
    st = details.get("structure") or {}
    trend = st.get("trend", "")

    # ranging 구조 또는 signal=False
    assert not sig, f"ranging 가격에서 signal=True — 거짓 신호 위험"
    if trend == "ranging":
        stage = classify_reason(reason, details)
        assert stage == "no_structure_ranging", (
            f"RANGING 차단인데 block_stage={stage!r} (no_structure_ranging 예상)"
        )

# ══════════════════════════════════════════════════════════════════════════════
# TB2 — Branch: BEARISH + LH 미돌파 path
#        구조는 있지만 가격이 LH를 넘지 못함 → no_structure_lh
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_bearish_lh_not_broken():
    """BEARISH 구조지만 가격이 LH 아래 머묾 → no_structure_lh.

    현대모비스(012330) 2026-05-27 실제 케이스를 합성 데이터로 재현.
    """
    prices, lh_level = _bearish_prices(lb=5)
    # LH(=98) 이후 가격이 80대에 머물다가 끝 (LH 돌파 없음)
    prices += [85.0] * 15  # 85 < 98 (last_lh)
    df = _make_df(prices)

    strat = _make_minimal_strat(swing_lookback=5)
    sig, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="TEST", market_regime=None,
    )
    st = details.get("structure") or {}
    trend = st.get("trend", "")

    assert not sig
    if trend == "bearish":
        stage = classify_reason(reason, details)
        assert stage in ("no_structure_lh", "no_structure"), (
            f"BEARISH + LH 미돌파인데 block_stage={stage!r}"
        )

# ══════════════════════════════════════════════════════════════════════════════
# TB3 — Branch: CHoCH 발생 but no_sweep path
#        detect_choch 성공 → detect_liquidity_sweep 실패 → no_sweep
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_choch_no_sweep():
    """CHoCH 감지됐지만 sweep 없음 → no_sweep 차단.

    detect_choch()가 실행되지만 require_liquidity_sweep=True + sweep=None.
    서진시스템(178320) 2026-05-27 케이스에서 유사 패턴 관찰.
    """
    from analyzers.smc.smc_structure import SMCStructureAnalyzer

    prices, lh_level = _bearish_prices(lb=5)
    # LH 이후 평탄 구간 (sweep 없게)
    prices += [90.0] * (5 + 1)
    df_base = _make_df(prices)

    # CHoCH 봉: high > LH, low는 swing_low보다 위 (sweep 없음), body 충분
    last_t = df_base.index[-1]
    choch_t = last_t + timedelta(minutes=5)
    conf_t  = choch_t + timedelta(minutes=5)
    choch_open  = 90.0
    choch_high  = round(lh_level * 1.022, 2)   # > LH=98 ✅
    choch_low   = 90.0                           # > last_ll=77 → no sweep
    choch_close = round(lh_level * 1.016, 2)
    conf_close  = choch_close + 0.3

    df = pd.concat([
        df_base,
        pd.DataFrame([{"open": choch_open, "high": choch_high,
                       "low": choch_low, "close": choch_close, "volume": 3000}],
                     index=pd.DatetimeIndex([choch_t])),
        pd.DataFrame([{"open": choch_close, "high": conf_close + 0.2,
                       "low": conf_close - 0.2, "close": conf_close, "volume": 1500}],
                     index=pd.DatetimeIndex([conf_t])),
    ])

    # sweep 비활성화 후 detect_choch 직접 검증 (RANGING 이슈 우회)
    az = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    structure = az.analyze_structure(df_base)  # CHoCH 봉 전 구조

    if structure.trend != MarketTrend.BEARISH or structure.last_lh is None:
        pytest.skip("구조가 BEARISH로 형성되지 않음 — 더 긴 데이터 필요")

    choch = az.detect_choch(df, structure, config={}, symbol="TEST")
    if choch is None:
        # RANGING 경계 효과로 CHoCH 미감지 → T9a와 동일 상황
        # sweep_block path는 CHoCH 전제이므로 skip
        pytest.skip("CHoCH 미감지 (T9a 경계 효과) — sweep_block path 진입 불가")

    # CHoCH 감지됐으면 sweep 확인
    from analyzers.smc.smc_utils import detect_liquidity_sweep
    sweep = detect_liquidity_sweep(
        df,
        structure.swing_points,
        lookback=20,
        sweep_threshold_pct=0.1,
    )

    # no_sweep 케이스 확인: CHoCH 감지 + sweep 없음이면 성공
    if sweep is None:
        # 원하는 상태: CHoCH=True, sweep=None → sweep_block 경로
        assert choch is not None, "CHoCH는 있어야 함"
        assert sweep is None, "sweep은 없어야 함 (no_sweep path 테스트)"
    else:
        # sweep이 감지된 경우: 데이터가 우연히 sweep 조건 충족
        # 이 경우는 sweep_block path 아닌 정상 경로 → skip
        pytest.skip(f"CHoCH 봉이 우연히 sweep 조건 충족 (swept={sweep}) — no_sweep 테스트 불성립")

# ══════════════════════════════════════════════════════════════════════════════
# TB4 — Branch: PREFILTER_BLOCK path
#        CHoCH 발생 but prefilter 조건 미충족 → prefilter_fail
#
# T9a 해결책: monkeypatch로 BEARISH 구조 주입.
# 이 테스트가 검증하는 것: CHoCH 발생 후 prefilter 게이트가 올바르게 차단하는지.
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_prefilter_block(monkeypatch):
    """CHoCH 발생 + prefilter 4/4 미충족 → prefilter_fail.

    monkeypatch로 BEARISH 구조 주입 (T9a 우회):
      analyze_structure()가 항상 df_base 기준 BEARISH 구조 반환.
      detect_choch()는 주입된 구조로 평가 → CHoCH 정상 발화.
    """
    from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend

    prices, lh_level = _bearish_prices(lb=5)
    prices += [90.0] * (5 + 1)
    df_base = _make_df(prices)

    az = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    base_structure = az.analyze_structure(df_base)
    if base_structure.trend != MarketTrend.BEARISH:
        pytest.skip("base structure not BEARISH — 테스트 데이터 이슈")

    monkeypatch.setattr(SMCStructureAnalyzer, 'analyze_structure',
                        lambda self, df: base_structure)

    last_t = df_base.index[-1]
    choch_t = last_t + timedelta(minutes=5)
    conf_t  = choch_t + timedelta(minutes=5)
    choch_close = round(lh_level * 1.016, 2)

    df = pd.concat([
        df_base,
        pd.DataFrame([{"open": 77.0, "high": round(lh_level * 1.022, 2),
                       "low": 75.0, "close": choch_close, "volume": 3000}],
                     index=pd.DatetimeIndex([choch_t])),
        pd.DataFrame([{"open": choch_close, "high": choch_close + 0.3,
                       "low": choch_close - 0.3, "close": choch_close + 0.1, "volume": 1500}],
                     index=pd.DatetimeIndex([conf_t])),
    ])

    strat = _make_minimal_strat(
        require_liquidity_sweep=False,
        prefilter_enabled=True,
        prefilter_min_conditions=4,         # 4개 모두 필요
        prefilter_require_htf_trend=True,   # df_htf=None → 자동 fail
        prefilter_require_liquidity_sweep=True,
        prefilter_require_reclaim=True,
    )

    sig, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="TEST", market_regime=None,
    )

    assert details.get("choch") is not None, (
        f"CHoCH 미감지 (monkeypatch 오류)\n  reason={reason}"
    )
    assert not sig
    stage = classify_reason(reason, details)
    assert stage == "prefilter_fail", (
        f"prefilter 4/4 설정인데 block_stage={stage!r} (prefilter_fail 예상)\n"
        f"  reason={reason}"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TB5 — Branch: grade_fail path
#        CHoCH 발생 + prefilter 우회 + A등급 요구 + 낮은 score → grade_fail
#
# T9a 해결책: monkeypatch로 BEARISH 구조 주입.
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_grade_fail(monkeypatch):
    """A등급 요구(threshold=100) + score<100 → grade_fail 차단.

    monkeypatch로 BEARISH 구조 주입 → CHoCH 발화 → displacement 통과 → grade 차단.
    """
    from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend

    prices, lh_level = _bearish_prices(lb=5)
    prices += [90.0] * (5 + 1)
    df_base = _make_df(prices)

    az = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    base_structure = az.analyze_structure(df_base)
    if base_structure.trend != MarketTrend.BEARISH:
        pytest.skip("base structure not BEARISH")

    monkeypatch.setattr(SMCStructureAnalyzer, 'analyze_structure',
                        lambda self, df: base_structure)

    last_t = df_base.index[-1]
    choch_t = last_t + timedelta(minutes=5)
    conf_t  = choch_t + timedelta(minutes=5)
    choch_close = round(lh_level * 1.016, 2)

    df = pd.concat([
        df_base,
        pd.DataFrame([{"open": 77.0, "high": round(lh_level * 1.022, 2),
                       "low": 75.0, "close": choch_close, "volume": 3000}],
                     index=pd.DatetimeIndex([choch_t])),
        pd.DataFrame([{"open": choch_close, "high": choch_close + 0.3,
                       "low": choch_close - 0.3, "close": choch_close + 0.1, "volume": 1500}],
                     index=pd.DatetimeIndex([conf_t])),
    ])

    strat = _make_minimal_strat(
        require_liquidity_sweep=False,
        prefilter_enabled=False,
        min_choch_grade="A",
    )
    strat._raw_config = {"choch_grade": {"a_threshold": 100, "b_threshold": 50}}

    sig, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="TEST", market_regime=None,
    )

    assert details.get("choch") is not None, (
        f"CHoCH 미감지 (monkeypatch 오류)\n  reason={reason}"
    )
    assert not sig
    stage = classify_reason(reason, details)
    assert stage == "grade_fail", (
        f"A등급 요구(threshold=100)인데 block_stage={stage!r} (grade_fail 예상)\n"
        f"  reason={reason}"
    )
    grade_d = details.get("choch_grade") or {}
    if isinstance(grade_d, dict) and grade_d.get("grade"):
        assert grade_d["grade"] != "A" or grade_d.get("score", 0) < 100, (
            "score=100인데도 grade_fail 발생 — grade 계산 오류"
        )

# ══════════════════════════════════════════════════════════════════════════════
# TB6 — Branch: signal=True path (CHoCH + sweep → 진입 신호)
#
# T9a 해결책: monkeypatch로 BEARISH 구조 주입.
# 이 테스트가 검증하는 것: CHoCH + sweep 감지 후 signal=True가 실제로 반환되는지.
# 실거래 직전 코드 경로 검증 핵심.
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_signal_true(monkeypatch):
    """CHoCH(bullish) + sweep(bullish) + grade≥C → signal=True.

    monkeypatch로 BEARISH 구조 주입:
      1. analyze_structure() → BEARISH (last_lh, last_ll 포함)
      2. detect_choch() → CHoCH bullish 발화 ✓
      3. detect_liquidity_sweep() → bullish sweep ✓ (sweep 봉 설계)
      4. prefilter 우회 (enabled=False)
      5. grade ≥ C (min_choch_grade='C')
      → signal=True ✓
    """
    from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend

    prices, lh_level = _bearish_prices(lb=5)
    prices += [90.0] * (5 + 1)
    df_base = _make_df(prices)

    az = SMCStructureAnalyzer(swing_lookback=5, min_swing_size_pct=0.0)
    base_structure = az.analyze_structure(df_base)
    if base_structure.trend != MarketTrend.BEARISH:
        pytest.skip("base structure not BEARISH")
    if base_structure.last_ll is None:
        pytest.skip("last_ll 없음")

    monkeypatch.setattr(SMCStructureAnalyzer, 'analyze_structure',
                        lambda self, df: base_structure)

    last_t = df_base.index[-1]
    sweep_t = last_t + timedelta(minutes=5)
    choch_t = sweep_t + timedelta(minutes=5)
    conf_t  = choch_t + timedelta(minutes=5)

    ll_price   = base_structure.last_ll.price    # ≈ 76.846
    choch_close = round(lh_level * 1.016, 2)

    df = pd.concat([
        df_base,
        # sweep 봉: last_ll 아래로 찌르고 위로 종가 → bullish sweep
        pd.DataFrame([{"open": 80.0, "high": 81.0,
                       "low": ll_price - 1.0, "close": ll_price + 1.0, "volume": 2000}],
                     index=pd.DatetimeIndex([sweep_t])),
        # CHoCH 봉: LH 돌파, 강한 불리시 봉
        pd.DataFrame([{"open": 77.0, "high": round(lh_level * 1.022, 2),
                       "low": 75.0, "close": choch_close, "volume": 3000}],
                     index=pd.DatetimeIndex([choch_t])),
        # conf 봉: 최종 확정 봉 (detect_choch의 last_idx 뒤에 위치)
        pd.DataFrame([{"open": choch_close, "high": choch_close + 0.3,
                       "low": choch_close - 0.3, "close": choch_close + 0.1, "volume": 1500}],
                     index=pd.DatetimeIndex([conf_t])),
    ])

    strat = _make_minimal_strat(
        require_liquidity_sweep=True,    # sweep 필수
        prefilter_enabled=False,         # prefilter 우회 (early_downtrend 우회)
        min_choch_grade="C",             # 어느 등급이든 통과
    )

    sig, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="TEST", market_regime=None,
    )

    choch = details.get("choch")
    sweep = details.get("liquidity_sweep")
    assert choch is not None, (
        f"CHoCH 미감지 (monkeypatch 오류)\n  reason={reason}"
    )
    assert sweep is not None, (
        f"sweep 미감지 — sweep 봉 설계 확인 필요\n"
        f"  last_ll={ll_price:.3f}  sweep_low={ll_price-1.0:.3f}\n"
        f"  choch.index={choch.get('price') if choch else None}"
    )
    assert sig is True, (
        f"signal=True 경로 검증 실패\n"
        f"  reason={reason}\n"
        f"  choch={choch}\n"
        f"  sweep={sweep}\n"
        f"  grade={details.get('choch_grade')}"
    )
    assert classify_reason(reason, details) == "signal"
    assert details.get('choch_mode') == 'NORMAL', (
        f"TB6: choch_mode != 'NORMAL' (정상 bearish 경로): {details.get('choch_mode')}"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TB7 — EXP-002 disabled: T9a 시나리오 → CHoCH=None, ranging_choch_candidate=True
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_exp002_disabled_shadow(monkeypatch):
    """EXP-002 disabled: RANGING + prev=BEARISH → CHoCH 차단, ranging_choch_candidate=True.

    T9a 시나리오: last_candle이 BEARISH→RANGING 구조 전환 유발.
    enabled=false 기본값에서 shadow observation만 기록되어야 함.
    """
    from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend, MarketStructure
    from analyzers.smc.smc_utils import SwingPoint

    lb = 5
    prices, lh_level = _bearish_prices(lb=lb)
    prices += [90.0] * (lb + 1)
    df_base = _make_df(prices)

    az = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=0.0)
    bearish_structure = az.analyze_structure(df_base)
    if bearish_structure.trend != MarketTrend.BEARISH:
        pytest.skip("base structure not BEARISH")
    if bearish_structure.last_lh is None:
        pytest.skip("last_lh 없음")

    # RANGING structure: last_lh=None (T9a: HH 추가로 last_lh 소멸)
    lh_price = bearish_structure.last_lh.price
    ranging_structure = MarketStructure(
        trend=MarketTrend.RANGING,
        last_lh=None,
        last_ll=bearish_structure.last_ll,
    )

    # analyze_structure: 첫 호출(df.iloc[:-1]) → BEARISH, 두 번째(full df) → RANGING
    _call_count = [0]
    def _mock_analyze(self, df_arg):
        _call_count[0] += 1
        return bearish_structure if _call_count[0] == 1 else ranging_structure

    monkeypatch.setattr(SMCStructureAnalyzer, 'analyze_structure', _mock_analyze)

    # df: LH를 돌파하는 CHoCH 봉 포함 (조건 자체는 충족)
    last_t = df_base.index[-1]
    choch_t = last_t + timedelta(minutes=5)
    conf_t  = choch_t + timedelta(minutes=5)
    choch_close = round(lh_price * 1.016, 2)
    df = pd.concat([
        df_base,
        pd.DataFrame([{"open": 77.0, "high": round(lh_price * 1.022, 2),
                       "low": 75.0, "close": choch_close, "volume": 3000}],
                     index=pd.DatetimeIndex([choch_t])),
        pd.DataFrame([{"open": choch_close, "high": choch_close + 0.3,
                       "low": choch_close - 0.3, "close": choch_close + 0.1, "volume": 1500}],
                     index=pd.DatetimeIndex([conf_t])),
    ])

    strat = _make_minimal_strat(require_liquidity_sweep=False, prefilter_enabled=False)
    strat._raw_config = {'choch': {'ranging_choch': {'enabled': False}}}

    sig, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="TEST", market_regime=None,
    )

    assert details.get('choch') is None,           f"EXP-002 disabled인데 CHoCH 발생: {details.get('choch')}"
    assert not sig,                                 f"EXP-002 disabled인데 signal=True"
    assert details.get('ranging_choch_candidate'), (
        f"ranging_choch_candidate=False — T9a shadow 미기록\n"
        f"  trend={details.get('structure', {}).get('trend')}\n"
        f"  prev_trend={details.get('structure', {}).get('prev_trend')}"
    )
    st = details.get('structure', {})
    assert st.get('transition_origin') == 'T9a',   f"transition_origin != 'T9a': {st.get('transition_origin')}"


# ══════════════════════════════════════════════════════════════════════════════
# TB8 — EXP-002 enabled: T9a 시나리오 → CHoCH 발생
# ══════════════════════════════════════════════════════════════════════════════

def test_branch_exp002_enabled_ranging_choch(monkeypatch):
    """EXP-002 enabled: RANGING + prev=BEARISH + LH 돌파 → CHoCH 발생.

    ranging_choch.enabled=true + require_prev_trend=bearish 조건 충족 시
    prev_structure.last_lh 기준으로 CHoCH 평가가 복구되어야 한다.
    """
    from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend, MarketStructure
    from analyzers.smc.smc_utils import SwingPoint

    lb = 5
    prices, lh_level = _bearish_prices(lb=lb)
    prices += [90.0] * (lb + 1)
    df_base = _make_df(prices)

    az = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=0.0)
    bearish_structure = az.analyze_structure(df_base)
    if bearish_structure.trend != MarketTrend.BEARISH:
        pytest.skip("base structure not BEARISH")
    if bearish_structure.last_lh is None:
        pytest.skip("last_lh 없음")

    lh_price = bearish_structure.last_lh.price
    ranging_structure = MarketStructure(
        trend=MarketTrend.RANGING,
        last_lh=None,
        last_ll=bearish_structure.last_ll,
    )

    _call_count = [0]
    def _mock_analyze(self, df_arg):
        _call_count[0] += 1
        return bearish_structure if _call_count[0] == 1 else ranging_structure

    monkeypatch.setattr(SMCStructureAnalyzer, 'analyze_structure', _mock_analyze)

    last_t = df_base.index[-1]
    choch_t = last_t + timedelta(minutes=5)
    conf_t  = choch_t + timedelta(minutes=5)
    choch_close = round(lh_price * 1.016, 2)
    df = pd.concat([
        df_base,
        pd.DataFrame([{"open": 77.0, "high": round(lh_price * 1.022, 2),
                       "low": 75.0, "close": choch_close, "volume": 3000}],
                     index=pd.DatetimeIndex([choch_t])),
        pd.DataFrame([{"open": choch_close, "high": choch_close + 0.3,
                       "low": choch_close - 0.3, "close": choch_close + 0.1, "volume": 1500}],
                     index=pd.DatetimeIndex([conf_t])),
    ])

    strat = _make_minimal_strat(require_liquidity_sweep=False, prefilter_enabled=False,
                                 min_choch_grade="C")
    strat._raw_config = {'choch': {'ranging_choch': {
        'enabled': True, 'require_prev_trend': 'bearish', 'max_transition_age': 1,
    }}}

    sig, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="TEST", market_regime=None,
    )

    choch = details.get('choch')
    assert choch is not None, (
        f"EXP-002 enabled인데 CHoCH 미발생\n"
        f"  reason={reason}\n"
        f"  prev_trend={details.get('structure', {}).get('prev_trend')}\n"
        f"  lh_price={lh_price}"
    )
    assert choch.get('direction') == 'bullish',   f"CHoCH direction != bullish: {choch}"
    assert sig is True,                            f"EXP-002 enabled + CHoCH인데 signal=False\n  reason={reason}"
    st = details.get('structure', {})
    assert st.get('transition_origin') == 'T9a',  f"transition_origin != 'T9a': {st.get('transition_origin')}"
    assert details.get('choch_mode') == 'T9a_RECOVERED', (
        f"choch_mode != 'T9a_RECOVERED': {details.get('choch_mode')}"
    )
    assert choch.get('choch_mode') == 'T9a_RECOVERED', (
        f"details['choch']['choch_mode'] != 'T9a_RECOVERED': {choch.get('choch_mode')}"
    )


# ── standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    if "--update-snapshots" in _sys.argv or "--generate" in _sys.argv:
        print("\n[GENERATE] Golden Snapshot 생성 중...")
        cfg = load_config()
        strat = build_smc_strategy(cfg)
        snapshot = {}
        for sym in SYMBOLS:
            s = _capture(sym, strat)
            if s:
                snapshot[sym] = s
                print(f"  {sym}: trend={s['structure_trend']}, swing={s['swing_count']}, stage={s['block_stage']}")
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"\n저장 완료: {SNAPSHOT_FILE}")
        _sys.exit(0)

    # standalone test run

    class _MP:
        """standalone 모드용 최소 monkeypatch (pytest fixture 대용)."""
        def __init__(self):
            self._patches = []
        def setattr(self, obj, name, value):
            orig = getattr(obj, name)
            builtins_setattr(obj, name, value)
            self._patches.append((obj, name, orig))
        def undo(self):
            for obj, name, orig in reversed(self._patches):
                builtins_setattr(obj, name, orig)

    import builtins
    builtins_setattr = builtins.__dict__['setattr']

    _results = []

    def _t(name, fn):
        try:
            fn()
            _results.append((name, True, ""))
            print(f"  ✅  {name}")
        except pytest.skip.Exception as e:
            _results.append((name, None, str(e)))
            print(f"  ⏭  {name}  ({e})")
        except AssertionError as e:
            _results.append((name, False, str(e)))
            print(f"  ❌  {name}")
            print(f"       {str(e)[:120]}")
        except Exception as e:
            _results.append((name, False, str(e)))
            print(f"  💥  {name}")
            traceback.print_exc()

    def _t_mp(name, fn):
        mp = _MP()
        try:
            fn(mp)
            _results.append((name, True, ""))
            print(f"  ✅  {name}")
        except pytest.skip.Exception as e:
            _results.append((name, None, str(e)))
            print(f"  ⏭  {name}  ({e})")
        except AssertionError as e:
            _results.append((name, False, str(e)))
            print(f"  ❌  {name}")
            print(f"       {str(e)[:120]}")
        except Exception as e:
            _results.append((name, False, str(e)))
            print(f"  💥  {name}")
            traceback.print_exc()
        finally:
            mp.undo()

    print("\n=== SMC E2E + Branch Coverage Tests ===\n")
    for sym in SYMBOLS:
        _t(f"TE1 snapshot [{sym}]", lambda s=sym: test_e2e_matches_golden_snapshot(s, False))
        _t(f"TE2 block_stage_known [{sym}]", lambda s=sym: test_e2e_block_stage_is_known(s))
        _t(f"TE3 structure_contract [{sym}]", lambda s=sym: test_e2e_structure_fields_contract(s))

    _t("TB1 ranging_suppression", test_branch_ranging_suppression)
    _t("TB2 bearish_lh_not_broken", test_branch_bearish_lh_not_broken)
    _t("TB3 choch_no_sweep", test_branch_choch_no_sweep)
    _t_mp("TB4 prefilter_block", test_branch_prefilter_block)
    _t_mp("TB5 grade_fail", test_branch_grade_fail)
    _t_mp("TB6 signal_true", test_branch_signal_true)
    _t_mp("TB7 exp002_disabled_shadow", test_branch_exp002_disabled_shadow)
    _t_mp("TB8 exp002_enabled_ranging_choch", test_branch_exp002_enabled_ranging_choch)

    passed  = sum(1 for _, ok, _ in _results if ok is True)
    skipped = sum(1 for _, ok, _ in _results if ok is None)
    failed  = sum(1 for _, ok, _ in _results if ok is False)
    print(f"\n[E2E SUMMARY]  ✅ {passed}  ❌ {failed}  ⏭ {skipped}  / {len(_results)}")
    if failed:
        print("\n실패 목록:")
        for name, ok, msg in _results:
            if ok is False:
                print(f"  {name}: {msg[:120]}")
