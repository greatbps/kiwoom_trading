"""
tests/unit/test_smc_replay.py — SMC Historical Replay 검증

목적:
  - 실제 생산 데이터(fixture)로 "코드가 설계대로 실행되는가" 검증
  - 합성 테스트(test_smc_synthetic.py)와 역할 분리:
      합성: 규칙 하나하나 격리 검증
      replay: 실 데이터에서 파이프라인 전체 검증

검증 방향:
  1. lb=10 rollback 이후 실 데이터에서 swing_count 충분한가?
  2. swing_high_count / swing_low_count가 균형 있게 생성되는가?
  3. structure_trend 필드가 올바르게 채워지는가?
  4. SK하이닉스(BULLISH): CHoCH=None이 맞는 동작인가?
  5. 구조 Invariant가 실 데이터에서도 유지되는가?

실행:
  python3 -m pytest tests/unit/test_smc_replay.py -v
  python3 tests/unit/test_smc_replay.py       (standalone)
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd

from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend

# ── fixture 경로 ───────────────────────────────────────────────────────────────
FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
DATE = "20260527"

SYMBOLS = {
    "000660": "SK하이닉스",
    "005430": "한국공항",
    "010060": "OCI홀딩스",
    "012330": "현대모비스",
    "036570": "NC소프트",
    "336260": "두산퓨얼셀",
}

VALID_TRENDS = {t.value for t in MarketTrend}

# ── fixture 로드 헬퍼 ──────────────────────────────────────────────────────────

def _load(symbol: str) -> pd.DataFrame:
    path = FIXTURE_DIR / f"smc_{symbol}_{DATE}_5m.csv"
    if not path.exists():
        pytest.skip(f"fixture 없음: {path.name}  (--save-fixture로 생성 필요)")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df

def _analyze(symbol: str, lookback: int = 10) -> tuple:
    """fixture → (structure, analyzer)"""
    df = _load(symbol)
    analyzer = SMCStructureAnalyzer(
        swing_lookback=lookback,
        min_swing_size_pct=0.3,
    )
    structure = analyzer.analyze_structure(df)
    return structure, df

# ── Invariant 검사 (test_smc_synthetic.py와 공유 규칙) ─────────────────────────

def _assert_structure_invariants(structure, label: str = ""):
    assert structure.trend.value in VALID_TRENDS, \
        f"{label}: trend={structure.trend.value} 알 수 없음"
    if structure.trend == MarketTrend.BULLISH:
        assert structure.last_hh is not None, f"{label}: BULLISH인데 last_hh=None"
        assert structure.last_hl is not None, f"{label}: BULLISH인데 last_hl=None"
        assert structure.last_hh.price > structure.last_hl.price, \
            f"{label}: HH({structure.last_hh.price}) <= HL({structure.last_hl.price})"
    elif structure.trend == MarketTrend.BEARISH:
        assert structure.last_lh is not None, f"{label}: BEARISH인데 last_lh=None"
        assert structure.last_ll is not None, f"{label}: BEARISH인데 last_ll=None"
        assert structure.last_lh.price > structure.last_ll.price, \
            f"{label}: LH({structure.last_lh.price}) <= LL({structure.last_ll.price})"
    # 인덱스 중복 없음
    indices = [sp.index for sp in structure.swing_points]
    assert len(indices) == len(set(indices)), f"{label}: 스윙 인덱스 중복 존재"

# ══════════════════════════════════════════════════════════════════════════════
# TR1 — lb=10 실 데이터에서 swing_count ≥ 2 확인 (EXP-001 핵심)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_replay_swing_count_lb10(symbol):
    """lb=10에서 실 데이터 swing_count ≥ 2 — EXP-001 lb rollback 효과 확인."""
    structure, _ = _analyze(symbol, lookback=10)
    count = len(structure.swing_points)
    assert count >= 2, (
        f"{symbol}({SYMBOLS[symbol]}): swing_count={count} (lb=10 기준 ≥2 필요)\n"
        f"  → lb=20 수준이면 swing collapse 미해결"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TR2 — lb=20이 lb=10보다 항상 ≤ swing_count (단조 감소 확인)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_replay_lb20_leq_lb10(symbol):
    """lb=20 swing_count ≤ lb=10 — lookback 증가가 단조 감소함을 실 데이터로 확인."""
    st10, _ = _analyze(symbol, lookback=10)
    st20, _ = _analyze(symbol, lookback=20)
    cnt10 = len(st10.swing_points)
    cnt20 = len(st20.swing_points)
    assert cnt20 <= cnt10, (
        f"{symbol}: lb=20({cnt20}) > lb=10({cnt10}) — 단조 감소 위반"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TR3 — swing_high_count + swing_low_count 분리 집계 가능
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_replay_swing_high_low_populated(symbol):
    """swing_high_count / swing_low_count — trend 라벨과 분포 모순 감지.

    강한 추세일 경우 한쪽이 0일 수 있음 (정상).
    BEARISH인데 high=0 → LH 없이 하락 판정 불가 (labeling bug).
    BULLISH인데 low=0  → HL 없이 상승 판정 불가 (labeling bug).
    RANGING은 한쪽만 있어도 허용 (추세 전환 중).
    """
    structure, _ = _analyze(symbol, lookback=10)
    high_cnt = sum(1 for sp in structure.swing_points if sp.type == 'high')
    low_cnt  = sum(1 for sp in structure.swing_points if sp.type == 'low')
    total = high_cnt + low_cnt
    if total == 0:
        pytest.skip(f"{symbol}: swing_count=0 (데이터 부족 가능)")

    trend = structure.trend
    if trend == MarketTrend.BEARISH:
        assert high_cnt >= 1, (
            f"{symbol}: BEARISH인데 swing_high=0 — LH 없이 하락 판정 불가 (labeling bug 가능)"
        )
        assert low_cnt >= 1, (
            f"{symbol}: BEARISH인데 swing_low=0 — LL 없이 하락 판정 불가"
        )
    elif trend == MarketTrend.BULLISH:
        assert high_cnt >= 1, (
            f"{symbol}: BULLISH인데 swing_high=0 — HH 없이 상승 판정 불가 (labeling bug 가능)"
        )
        assert low_cnt >= 1, (
            f"{symbol}: BULLISH인데 swing_low=0 — HL 없이 상승 판정 불가"
        )
    # RANGING: 어느 한쪽만 있어도 허용

# ══════════════════════════════════════════════════════════════════════════════
# TR4 — structure_trend 필드가 details dict에 포함됨 (smc_signals.py 수정 확인)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_replay_structure_details_has_swing_fields(symbol):
    """smc_signals.py의 details['structure']에 swing_high_count, swing_low_count 있음."""
    from analysis.smc_path_test import load_fixture, load_config, build_smc_strategy
    df = load_fixture(symbol, DATE)
    if df is None:
        pytest.skip(f"fixture 없음: {symbol}")

    cfg = load_config()
    strat = build_smc_strategy(cfg)
    _, _, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol=symbol, market_regime=None,
    )
    st = details.get('structure', {})
    assert 'swing_count' in st,       f"{symbol}: details.structure에 swing_count 없음"
    assert 'swing_high_count' in st,  f"{symbol}: details.structure에 swing_high_count 없음"
    assert 'swing_low_count' in st,   f"{symbol}: details.structure에 swing_low_count 없음"

# ══════════════════════════════════════════════════════════════════════════════
# TR4b — prev_trend / transition_note 필드 존재 확인 (structure transition trace)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_replay_structure_transition_fields(symbol):
    """details['structure']에 prev_trend / transition_note 있음 — T9a 진단 필드 확인."""
    from analysis.smc_path_test import load_fixture, load_config, build_smc_strategy
    df = load_fixture(symbol, DATE)
    if df is None:
        pytest.skip(f"fixture 없음: {symbol}")

    cfg = load_config()
    strat = build_smc_strategy(cfg)
    _, _, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol=symbol, market_regime=None,
    )
    st = details.get('structure', {})
    assert 'prev_trend' in st,      f"{symbol}: details.structure에 prev_trend 없음"
    assert 'transition_note' in st, f"{symbol}: details.structure에 transition_note 없음"
    note = st['transition_note']
    assert note == "stable" or note.startswith("last_candle:"), (
        f"{symbol}: transition_note 형식 오류: {note!r}"
    )
    valid_trends = {t.value for t in MarketTrend}
    assert st['prev_trend'] in valid_trends, (
        f"{symbol}: prev_trend={st['prev_trend']!r} 유효하지 않음"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TR8 — T9a 패턴 노출 진단 (RANGING 종목에서 prev_trend가 진단 정보 제공)
# ══════════════════════════════════════════════════════════════════════════════

def test_replay_t9a_pattern_diagnostic():
    """RANGING 종목에서 prev_trend로 T9a 패턴 노출 여부 확인.

    T9a: last candle이 BEARISH→RANGING 전환 유발 → CHoCH 차단.
    이 테스트는 transition_note 필드가 그 전환을 포착하는지 진단한다.
    포착 여부 자체는 fixture에 따라 다름 — 핵심 검증은 필드 형식.
    """
    from analysis.smc_path_test import load_fixture, load_config, build_smc_strategy
    cfg = load_config()
    strat = build_smc_strategy(cfg)

    valid_trends = {t.value for t in MarketTrend}
    notes_seen = set()

    for symbol in SYMBOLS:
        df = load_fixture(symbol, DATE)
        if df is None:
            continue
        _, _, details = strat.check_entry_signal(
            df=df, debug=False, df_htf=None, symbol=symbol, market_regime=None,
        )
        st = details.get('structure', {})
        prev_t = st.get('prev_trend', '')
        note   = st.get('transition_note', '')
        assert prev_t in valid_trends,  f"{symbol}: prev_trend={prev_t!r} 유효하지 않음"
        assert note == "stable" or note.startswith("last_candle:"), (
            f"{symbol}: transition_note 형식 오류: {note!r}"
        )
        notes_seen.add(note)

    if not notes_seen:
        pytest.skip("fixture 없음")

    # 모든 결과가 유효한 형식 — 이미 위에서 검증됨
    # T9a 패턴(bearish→ranging) 포착 시 정보 출력
    t9a = [n for n in notes_seen if 'bearish→ranging' in n]
    if t9a:
        print(f"\n  [T9a 포착] {t9a} — T9a 전환 진단 가능")

# ══════════════════════════════════════════════════════════════════════════════
# TR5 — SK하이닉스 BULLISH 구조 → CHoCH=None (올바른 동작 freeze)
# ══════════════════════════════════════════════════════════════════════════════

def test_replay_sk_hynix_bullish_no_choch():
    """000660 SK하이닉스 (2026-05-27): BULLISH 구조 → CHoCH(long) = None.

    EXP-001 관찰과 일치: BOS(bullish) 21회, CHoCH 0회.
    이건 올바른 동작 — long entry는 intraday reversal 필요, 상승장에서 불가.
    """
    from analysis.smc_path_test import load_fixture, load_config, build_smc_strategy
    df = load_fixture("000660", DATE)
    if df is None:
        pytest.skip("fixture 없음: 000660")

    cfg = load_config()
    strat = build_smc_strategy(cfg)
    _, reason, details = strat.check_entry_signal(
        df=df, debug=False, df_htf=None, symbol="000660", market_regime=None,
    )
    st = details.get('structure', {})
    trend = st.get('trend', '')
    choch = details.get('choch')

    # BULLISH 이거나 적어도 BEARISH가 아닌 상태
    assert trend != 'bearish', (
        f"000660: trend=bearish인데 CHoCH=None? 예상치 못한 상태\n"
        f"  reason={reason}"
    )
    # CHoCH는 없어야 함 (signal도 없어야 함)
    assert choch is None, (
        f"000660: BULLISH 구조인데 CHoCH 발생 = {choch}\n"
        f"  → long setup이 상승장에서 무분별 발생하는 문제"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TR6 — 모든 fixture에서 structure invariants 유지
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("symbol", list(SYMBOLS.keys()))
def test_replay_structure_invariants(symbol):
    """실 데이터에서도 구조 불변량 유지 — HH>HL, LH>LL, 인덱스 중복 없음."""
    structure, _ = _analyze(symbol, lookback=10)
    _assert_structure_invariants(structure, label=f"{symbol}({SYMBOLS[symbol]})")

# ══════════════════════════════════════════════════════════════════════════════
# TR7 — 전체 fixture avg swing_count 측정 (EXP-001 Day 1 기준선)
# ══════════════════════════════════════════════════════════════════════════════

def test_replay_avg_swing_count_baseline():
    """EXP-001 Day 1 기준: lb=10 실 데이터 avg swing_count ≥ 2.5.

    lb=20 시 avg=0.5 (smc_path_test --compare 결과).
    lb=10 이후 개선 목표 ≥ 3.
    이 테스트는 기준선 freeze — 이 이하로 떨어지면 regression.
    """
    counts = []
    for symbol in SYMBOLS:
        path = FIXTURE_DIR / f"smc_{symbol}_{DATE}_5m.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        analyzer = SMCStructureAnalyzer(swing_lookback=10, min_swing_size_pct=0.3)
        structure = analyzer.analyze_structure(df)
        counts.append(len(structure.swing_points))

    if not counts:
        pytest.skip("fixture 없음")

    avg = sum(counts) / len(counts)
    assert avg >= 2.5, (
        f"avg swing_count={avg:.1f} < 2.5 (lb=10 효과 미확인)\n"
        f"  개별: {dict(zip(SYMBOLS, counts))}"
    )

# ══════════════════════════════════════════════════════════════════════════════
# TR9 — Structure Replay: mid-session T9a 타이밍 일관성 (deterministic replay)
# ══════════════════════════════════════════════════════════════════════════════

def test_replay_structure_replay_determinism():
    """structure_replay가 캔들 단위로 재현 가능한가 + T9a 타이밍 일관성.

    핵심 검증:
      1. replay_symbol()이 동일 fixture에서 결정론적(동일 결과)인가
      2. T9a 발생 step의 prev_trend가 실제로 bearish인가
      3. check_entry_signal(full_df)의 end-of-day 상태 ≠ mid-session 상태 (state machine 비일치 허용)

    두산퓨얼셀(336260) 2026-05-27: step 70에서 T9a + CHoCH 조건 충족 확인.
    이는 end-of-day check에서는 보이지 않는 mid-session 이벤트.
    """
    from analysis.structure_replay import replay_symbol

    path = FIXTURE_DIR / f"smc_336260_{DATE}_5m.csv"
    if not path.exists():
        pytest.skip("fixture 없음: 336260")

    # 동일 결과 재현성 확인 (determinism)
    r1 = replay_symbol("336260", DATE, verbose=True)
    r2 = replay_symbol("336260", DATE, verbose=True)

    assert len(r1["timeline"]) == len(r2["timeline"]),  "replay 비결정론적: 타임라인 길이 다름"
    assert r1["t9a_events"]    == r2["t9a_events"],     "replay 비결정론적: T9a 이벤트 다름"

    # T9a 이벤트 형식 검증
    for e in r1["t9a_events"]:
        assert e["prev_trend"] == "bearish",   f"T9a event prev_trend != bearish: {e}"
        assert e["trend"]      == "ranging",   f"T9a event trend != ranging: {e}"
        assert e["step"] >= 1,                 f"T9a event step < 1: {e}"

    # 두산퓨얼셀 특정 케이스: step 70 T9a + CHoCH 조건 충족 확인 (regression freeze)
    cw_events = r1["choch_window_events"]
    assert len(cw_events) >= 1, (
        "336260: CHoCH window 이벤트 없음 (step 70 T9a+CHoCH 미감지 — regression 가능)"
    )
    cw = cw_events[0]
    assert cw["step"] == 70,           f"CHoCH window step != 70: {cw['step']}"
    assert cw["prev_trend"] == "bearish"
    assert cw["choch_lh_level"] is not None
    assert cw["choch_candle_close"] > cw["choch_lh_level"], (
        f"CHoCH candle close({cw['choch_candle_close']}) <= prev_lh({cw['choch_lh_level']})"
    )

    # end-of-day check_entry_signal은 mid-session T9a를 보지 않음 (정상)
    from analysis.smc_path_test import load_fixture, load_config, build_smc_strategy
    df = load_fixture("336260", DATE)
    cfg = load_config()
    strat = build_smc_strategy(cfg)
    _, _, details = strat.check_entry_signal(df=df, debug=False, df_htf=None,
                                              symbol="336260", market_regime=None)
    eod_note = details.get("structure", {}).get("transition_note", "stable")
    # end-of-day state: 최종 캔들 기준 stable (mid-session T9a는 already evolved)
    # 이 assert는 "두 관점이 다를 수 있음"을 문서화 — 어느 쪽도 버그가 아님
    assert eod_note in ("stable", ) or eod_note.startswith("last_candle:"), (
        f"end-of-day transition_note 형식 오류: {eod_note}"
    )


# ── standalone runner ─────────────────────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []

def _test(name, fn):
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
        print(f"       → {e}")
    except Exception as e:
        _results.append((name, False, f"EXCEPTION: {e}"))
        print(f"  💥  {name}")
        traceback.print_exc()


if __name__ == "__main__":
    print("\n=== SMC Historical Replay Tests ===\n")
    for sym in SYMBOLS:
        _test(f"TR1 swing_count_lb10 [{sym}]",
              lambda s=sym: test_replay_swing_count_lb10(s))
        _test(f"TR2 lb20_leq_lb10 [{sym}]",
              lambda s=sym: test_replay_lb20_leq_lb10(s))
        _test(f"TR3 high_low_populated [{sym}]",
              lambda s=sym: test_replay_swing_high_low_populated(s))
        _test(f"TR4 details_fields [{sym}]",
              lambda s=sym: test_replay_structure_details_has_swing_fields(s))
        _test(f"TR4b transition_fields [{sym}]",
              lambda s=sym: test_replay_structure_transition_fields(s))
        _test(f"TR6 invariants [{sym}]",
              lambda s=sym: test_replay_structure_invariants(s))
    _test("TR5 sk_hynix_bullish_no_choch", test_replay_sk_hynix_bullish_no_choch)
    _test("TR7 avg_swing_count_baseline", test_replay_avg_swing_count_baseline)
    _test("TR8 t9a_pattern_diagnostic", test_replay_t9a_pattern_diagnostic)
    _test("TR9 structure_replay_determinism", test_replay_structure_replay_determinism)

    total   = len(_results)
    passed  = sum(1 for _, ok, _ in _results if ok is True)
    skipped = sum(1 for _, ok, _ in _results if ok is None)
    failed  = sum(1 for _, ok, _ in _results if ok is False)
    print(f"\n[REPLAY SUMMARY]  ✅ {passed}  ❌ {failed}  ⏭ {skipped}  / {total}")
    if failed:
        print("\n실패 목록:")
        for name, ok, msg in _results:
            if ok is False:
                print(f"  {name}: {msg[:120]}")
