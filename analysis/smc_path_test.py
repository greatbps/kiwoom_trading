#!/usr/bin/env python3
"""
analysis/smc_path_test.py — SMC 파이프라인 독립 검증 도구

실시장/오케스트레이터/EC 완전 분리 상태에서 SMC check_entry_signal()을
단계별로 실행하고 중간값을 출력.

목적:
  "이 데이터에서 SMC가 원래 신호가 나와야 하는가?"를 독립 검증.
  버그 / threshold 과도 / watchlist 문제를 실시간 시스템 없이 재현.

사용:
  # 오늘 ACCEPT 종목 테스트
  python3 -m analysis.smc_path_test --symbol 456010

  # 특정 날짜 테스트
  python3 -m analysis.smc_path_test --symbol 005930 --date 2026-02-10

  # DB 기반 known-good 케이스 회귀 테스트 (2026-02-10 실거래 재현)
  python3 -m analysis.smc_path_test --known-good

  # 다수 종목 배치
  python3 -m analysis.smc_path_test --symbols 456010 111770 005930

  # 오늘 데이터를 fixture로 저장 (historical replay 준비)
  python3 -m analysis.smc_path_test --symbols 005930 010060 --save-fixture

  # compare 실행 + fixture 동시 저장
  python3 -m analysis.smc_path_test --symbols 005930 010060 --compare --save-fixture
"""
import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance 미설치. pip install yfinance")
    sys.exit(1)

from analyzers.smc.smc_signals import SMCStrategy

# ── Known-good cases (DB에서 확인된 실거래) ──────────────────────────────────
KNOWN_GOOD = [
    # (symbol, date_str, entry_time, grade) — 2026-02-10 실거래
    ("111770", "20260210", "12:50", "A"),
    ("039440", "20260210", "12:42", "A"),
    ("001120", "20260210", "12:30", "A"),
    ("281740", "20260210", "12:01", "A"),
    ("189300", "20260210", "10:35", "A"),
    ("130660", "20260213", "11:08", "B"),
    ("417840", "20260219", "13:29", "B"),
    ("474650", "20260219", "13:23", "B"),
    ("095610", "20260225", "10:16", "A"),
]

# ── 설정 ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    p = Path("config/strategy_hybrid.yaml")
    if not p.exists():
        p = Path(__file__).parent.parent / "config/strategy_hybrid.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)

# ── SMCStrategy 초기화 (main_auto_trading.py 동일 방식) ─────────────────────

def build_smc_strategy(cfg: dict) -> SMCStrategy:
    smc_cfg   = cfg.get("smc", {})
    grade_cfg = smc_cfg.get("choch_grade", {})
    filt_cfg  = smc_cfg.get("additional_filters", {})
    mtf_cfg   = smc_cfg.get("mtf_bias", {})
    pf_cfg    = smc_cfg.get("entry_prefilter", {})

    strat = SMCStrategy(
        swing_lookback                  = smc_cfg.get("swing_lookback", 5),
        min_swing_size_pct              = smc_cfg.get("min_swing_size_pct", 0.3),
        sweep_threshold_pct             = smc_cfg.get("sweep_threshold_pct", 0.1),
        sweep_lookback                  = smc_cfg.get("sweep_lookback", 20),
        require_liquidity_sweep         = smc_cfg.get("require_liquidity_sweep", True),
        long_only                       = smc_cfg.get("long_only", True),
        min_choch_grade                 = grade_cfg.get("min_grade", "B"),
        require_squeeze_on              = filt_cfg.get("require_squeeze_on", False),
        require_vwap_above              = filt_cfg.get("require_vwap_above", False),
        grade_b_weight                  = grade_cfg.get("grade_b_weight", 0.5),
        mtf_bias_enabled                = mtf_cfg.get("enabled", True),
        mtf_timeframe                   = mtf_cfg.get("timeframe", "30min"),
        prefilter_enabled               = pf_cfg.get("enabled", True),
        prefilter_min_conditions        = pf_cfg.get("min_conditions", 2),
        prefilter_require_htf_trend     = pf_cfg.get("require_htf_trend", True),
        prefilter_require_liquidity_sweep = pf_cfg.get("require_liquidity_sweep", True),
        prefilter_require_reclaim       = pf_cfg.get("require_reclaim", True),
        reclaim_lookback                = pf_cfg.get("reclaim_lookback", 5),
        reclaim_tolerance_pct           = pf_cfg.get("reclaim_tolerance_pct", 0.3),
        sweep_fallback_enabled          = smc_cfg.get("sweep_fallback_enabled", False),
        sweep_fallback_size_mult        = smc_cfg.get("sweep_fallback_size_mult", 0.5),
        sweep_fallback_confidence       = smc_cfg.get("sweep_fallback_confidence", 0.60),
    )
    strat._raw_config = smc_cfg
    return strat

# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """한글/영문 컬럼 → 소문자 영문 통일."""
    # yfinance multi-level columns 처리 (Price/Ticker 레벨)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    rename = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
    df = df.rename(columns=rename)
    df.columns = [c.lower() for c in df.columns]
    return df

_FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"

def _fixture_path(symbol: str, date_str: str) -> Path:
    return _FIXTURE_DIR / f"smc_{symbol}_{date_str}_5m.csv"

def save_fixture(df: pd.DataFrame, symbol: str, date_str: str):
    """5분봉 DataFrame → tests/fixtures/smc_{symbol}_{date}_5m.csv 저장."""
    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = _fixture_path(symbol, date_str)
    df.to_csv(path)
    print(f"  💾 fixture 저장: {path.name} ({len(df)}봉)")

def load_fixture(symbol: str, date_str: str) -> pd.DataFrame | None:
    """저장된 fixture 로드. 없으면 None."""
    path = _fixture_path(symbol, date_str)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"  ⚠ fixture 로드 오류 {path.name}: {e}")
        return None

def load_5m(symbol: str, date_str: str, bars: int = 120,
            save: bool = False) -> pd.DataFrame | None:
    """5분봉 로드. fixture 우선, 없으면 yfinance. save=True 시 fixture 저장."""
    # fixture 먼저 확인 (historical replay 지원)
    df = load_fixture(symbol, date_str)
    if df is not None:
        return df[-bars:].copy()

    # yfinance fallback (최근 60일)
    from datetime import timedelta
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        start = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        end   = (dt + timedelta(days=2)).strftime("%Y-%m-%d")
        ticker = f"{symbol}.KS"
        df = yf.download(ticker, start=start, end=end, interval="5m", progress=False, auto_adjust=True)
        if df is None or len(df) < 5:
            return None
        df = _normalize_cols(df)
        # 해당 날짜만 필터
        day_str = dt.strftime("%Y-%m-%d")
        df = df[df.index.strftime("%Y-%m-%d") == day_str]
        if len(df) < 5:
            return None
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                return None
        df = df[-bars:].copy()
        if save:
            save_fixture(df, symbol, date_str)
        return df
    except Exception as e:
        print(f"  ⚠ 5m 로드 오류 {symbol} {date_str}: {e}")
        return None

def load_30m(symbol: str, date_str: str, bars: int = 50) -> pd.DataFrame | None:
    """5분봉 → 30분봉 resample."""
    df5 = load_5m(symbol, date_str, bars=bars * 6 + 6)
    if df5 is None or len(df5) < 6:
        return None
    try:
        df5.index = pd.to_datetime(df5.index)
        df30 = df5.resample("30min").agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"),
            volume=("volume", "sum"),
        ).dropna()
        return df30[-bars:].copy() if len(df30) >= 3 else None
    except Exception as e:
        print(f"  ⚠ 30m resample 오류: {e}")
        return None

# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _icon(passed) -> str:
    if passed is True:  return "✅"
    if passed is False: return "❌"
    return "📋"

def _hdr(title: str, passed=None):
    print(f"\n[{_icon(passed)} {title}]")

def _val(key, value, indent=2, note=""):
    pad = " " * indent
    tail = f"  ← {note}" if note else ""
    print(f"{pad}{key}: {value}{tail}")

# ── 핵심: 단계별 결과 출력 ────────────────────────────────────────────────────

def print_pipeline_result(symbol: str, date_str: str, df5: pd.DataFrame,
                          signal: bool, reason: str, details: dict,
                          cfg: dict, expected_grade: str = None):
    smc_cfg   = cfg.get("smc", {})
    grade_cfg = smc_cfg.get("choch_grade", {})
    pf_cfg    = smc_cfg.get("entry_prefilter", {})

    print(f"\n{'═'*60}")
    print(f"  SYMBOL  : {symbol}")
    print(f"  DATE    : {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}")
    if expected_grade:
        print(f"  EXPECTED: 실거래 발생 (grade={expected_grade}) — regression check")
    print(f"{'═'*60}")

    # ── INPUT
    _hdr("INPUT")
    _val("5m candles", len(df5))
    _val("range", f"{df5.index[0]} ~ {df5.index[-1]}")
    _val("last close", f"{df5['close'].iloc[-1]:,.0f}")
    vol_ratio = None
    if len(df5) >= 21:
        vn = df5["volume"].iloc[-1]
        va = df5["volume"].iloc[-21:-1].mean()
        vol_ratio = round(vn / va, 2) if va > 0 else None
    _val("vol_ratio (vs 20avg)", vol_ratio)

    # ── STRUCTURE
    st = details.get("structure", {})
    trend = st.get("trend", "N/A")
    _hdr("STRUCTURE ANALYSIS", passed=trend not in (None, "N/A", "ranging", "sideways"))
    _val("trend", trend)
    _origin = st.get("transition_origin")
    _val("prev_trend", st.get("prev_trend"),
         note=(f"{st.get('transition_note', 'stable')}  [origin={_origin}]"
               if _origin else st.get("transition_note", "stable")))
    _cand = details.get("ranging_choch_candidate", False)
    if _cand:
        _val("ranging_choch_cand", "True", note="EXP-002 활성화 시 CHoCH 평가 가능")
    _val("swing_count", st.get("swing_count", 0),
         note=f"lookback={smc_cfg.get('swing_lookback', 5)}, min_size={smc_cfg.get('min_swing_size_pct', 0.3)}%")
    _val("last_hh", st.get("last_hh"))
    _val("last_hl", st.get("last_hl"))
    _val("last_lh", st.get("last_lh"))
    _val("last_ll", st.get("last_ll"))

    # ── CHoCH / BOS
    choch = details.get("choch")
    bos   = details.get("bos")
    _hdr("CHoCH DETECTION", passed=bool(choch))
    if choch:
        _val("type",          choch.get("type"))
        _val("direction",     choch.get("direction"))
        _val("broken_level",  choch.get("broken_level"))
        _val("price",         choch.get("price"))
        _val("choch_mode",    details.get("choch_mode"),
             note="NORMAL=정상경로 | T9a_RECOVERED=RANGING 복구 경로")
    elif bos:
        print("  → BOS 감지 (추세 지속, CHoCH 대기)")
        _val("bos.direction",    bos.get("direction"))
        _val("bos.broken_level", bos.get("broken_level"))
    else:
        print("  → 구조 변화 없음 (CHoCH/BOS 미발생)")
        _transition = st.get("transition_note", "stable")
        if _transition != "stable":
            print(f"     Hint: T9a 전환 감지 — {_transition} (last candle이 CHoCH 평가 구조를 변경)")
        else:
            print("     Hint: swing_count 부족하거나 swing_size_pct 임계값 초과")

    # ── SWEEP
    sweep = details.get("liquidity_sweep")
    _hdr("LIQUIDITY SWEEP", passed=bool(sweep))
    if sweep:
        _val("swept_level",  sweep.get("swept_level"))
        _val("direction",    sweep.get("direction"))
        _val("sweep_type",   sweep.get("sweep_type"))
    else:
        if choch:
            _val("result", "sweep 없음",
                 note=f"threshold={smc_cfg.get('sweep_threshold_pct', 0.1)}%, lookback={smc_cfg.get('sweep_lookback', 20)}")

    # ── PREFILTER
    pf = details.get("prefilter", {})
    if pf:
        met  = pf.get("conditions_met", 0)
        req  = pf.get("min_required", pf_cfg.get("min_conditions", 2))
        _hdr("PREFILTER", passed=(met >= req))
        _val("conditions_met", f"{met}/{req}")
        _val("htf_trend_alive",    pf.get("htf_trend_alive"),   note="30m 추세 일치")
        _val("liquidity_swept",    pf.get("liquidity_swept"),   note="Sweep 확인")
        _val("reclaim_detected",   pf.get("reclaim_detected"),  note="되돌림 확인")
        _val("volume_confirmed",   pf.get("volume_confirmed"),  note="vol > 20MA")
        _val("rvol_at_prefilter",  pf.get("rvol_at_prefilter"))
        rvol_thresh = smc_cfg.get("entry_prefilter", {}).get("min_rvol", 0.8)
        _val("min_rvol_threshold", rvol_thresh)

    # ── GRADE
    gd = details.get("choch_grade", {})
    if gd:
        grade   = gd.get("grade", "-")
        score   = gd.get("score", 0)
        min_g   = grade_cfg.get("min_grade", "B")
        a_thr   = grade_cfg.get("a_threshold", 80)
        b_thr   = grade_cfg.get("b_threshold", 50)
        passed  = grade in ("A", "B") if min_g == "B" else grade == "A"
        _hdr(f"CHoCH GRADE (min={min_g}, A≥{a_thr}, B≥{b_thr})", passed=passed)
        _val("grade", grade)
        _val("score", score, note=f"A≥{a_thr} / B≥{b_thr}")
        for f in gd.get("factors", []):
            print(f"     + {f}")

    # ── MTF BIAS
    mtf = details.get("mtf_bias", {})
    if mtf:
        _hdr("MTF BIAS (30m)", passed=mtf.get("allowed"))
        _val("allowed",   mtf.get("allowed"))
        _val("htf_trend", mtf.get("htf_trend"))
        _val("reason",    mtf.get("reason"))

    # ── DISPLACEMENT
    disp = details.get("displacement", {})
    if disp:
        _hdr("DISPLACEMENT FILTER", passed=disp.get("passed"))
        _val("passed",   disp.get("passed"))
        if not disp.get("passed"):
            for r in disp.get("reasons", []):
                print(f"     ✗ {r}")

    # ── FINAL
    print(f"\n{'─'*60}")
    status = "✅ SIGNAL" if signal else "❌ NO SIGNAL"
    print(f"  [FINAL]  {status}")
    print(f"  reason : {reason}")
    if expected_grade and not signal:
        print(f"  ⚠  REGRESSION: 실거래 발생 날인데 신호 없음 → 코드 변경 의심")
    elif expected_grade and signal:
        print(f"  ✅ REGRESSION OK: 실거래 날 신호 재현 성공")
    print(f"{'─'*60}")

# ── reason 분류 (병목 단계 식별) ──────────────────────────────────────────────

def classify_reason(reason: str, details: dict) -> str:
    """SMC rejection reason → 파이프라인 단계 분류.

    반환값 (파이프라인 순서):
      no_data               — 데이터 없음
      no_structure_zero     — swing_count=0 (구조 자체 미생성)
      no_structure_ranging  — swing 있지만 RANGING 억제로 CHoCH 평가 불가
      no_structure_lh       — BEARISH지만 LH 미돌파 (정상 차단)
      no_structure          — CHoCH 없음 (원인 불명)
      no_sweep              — CHoCH 있지만 sweep 없음
      prefilter_fail        — prefilter 조건 미충족
      grade_fail            — CHoCH 등급 미달
      mtf_blocked           — MTF bias 차단
      displacement_fail     — displacement 필터 탈락
      edt_block             — EDT 필터 차단
      signal                — 신호 발생
      other                 — 분류 불가
    """
    if not reason:
        return "other"
    r = reason.lower()

    # ── 1. 신호 발생 (가장 먼저 — reason에 "sweep"/"choch" 등 포함 가능) ────────
    # 성공 reason: "LONG: CHoCH[B급]..." / "SHORT: CHoCH[B급]..."
    if r.startswith("long:") or r.startswith("short:"):
        return "signal"

    # ── 2. "구조 변화 없음" 3종 분리 (원인 추적 핵심) ─────────────────────────────
    if "구조 변화" in reason or "no choch" in r or "구조 없음" in reason:
        st = details.get("structure") or {}
        swing_count = st.get("swing_count", 0) or 0
        trend = st.get("trend", "")
        if swing_count == 0:
            return "no_structure_zero"      # swing 자체 없음 → collapse
        if trend == "ranging":
            return "no_structure_ranging"   # swing 있지만 RANGING 억제
        if trend == "bearish":
            return "no_structure_lh"        # BEARISH인데 LH 미돌파 → 정상 차단
        return "no_structure"               # 원인 불명 (분류 추가 필요)

    # ── 3. prefilter → no_sweep 순서 (prefilter reason이 "Sweep=❌" 포함 가능)
    if "prefilter" in r or "프리필터" in reason:
        return "prefilter_fail"
    if "sweep" in r or "유동성" in reason:
        return "no_sweep"
    # grade_fail: "grade"/"등급" 또는 "X급 (최소 Y급 필요)" 형태의 reason
    if ("grade" in r or "등급" in reason or "choch grade" in r
            or "급 필요" in reason):
        return "grade_fail"
    if "mtf" in r or "htf" in r or "추세 불일치" in reason:
        return "mtf_blocked"
    if "displacement" in r or "디스플레이스" in reason:
        return "displacement_fail"
    if "edt" in r:
        return "edt_block"
    return "other"

# ── 배치 실행 (reason distribution 집계) ──────────────────────────────────────

def run_batch(symbols: list[str], date_str: str, bars: int,
              cfg: dict, lookbacks: list[int],
              save_fixture: bool = False) -> dict:
    """여러 종목 × 여러 lookback 실행 → reason 분포 반환.

    반환: { lookback: {reason_category: count, ...}, ... }
    """
    from collections import Counter

    results: dict[int, list[dict]] = {lb: [] for lb in lookbacks}

    for symbol in symbols:
        df5  = load_5m(symbol, date_str, bars=bars, save=save_fixture)
        df30 = load_30m(symbol, date_str, bars=50)
        if df5 is None:
            for lb in lookbacks:
                results[lb].append({"symbol": symbol, "stage": "no_data",
                                    "swing_count": 0, "choch": False, "signal": False})
            continue

        for lb in lookbacks:
            # cfg를 복사해 swing_lookback만 교체
            _cfg = dict(cfg)
            _smc = dict(cfg.get("smc", {}))
            _smc["swing_lookback"] = lb
            _cfg["smc"] = _smc

            strat = build_smc_strategy(_cfg)
            sig, reason, details = strat.check_entry_signal(
                df=df5, debug=False, df_htf=df30,
                symbol=symbol, market_regime=None,
            )
            st = details.get("structure", {})
            results[lb].append({
                "symbol":      symbol,
                "stage":       "signal" if sig else classify_reason(reason, details),
                "swing_count": st.get("swing_count", 0),
                "trend":       st.get("trend", "?"),
                "choch":       bool(details.get("choch")),
                "sweep":       bool(details.get("liquidity_sweep")),
                "signal":      sig,
                "reason":      reason,
            })

    return results

def print_batch_report(results: dict[int, list[dict]], date_str: str):
    """배치 결과 요약 출력."""
    from collections import Counter

    print(f"\n{'═'*70}")
    print(f"  SMC BATCH REPORT — {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}")
    print(f"{'═'*70}")

    lookbacks = list(results.keys())  # 입력 순서 유지 (compare: old→new 순)
    n = len(results[lookbacks[0]])

    # ── 헤더
    print(f"\n{'stage':<20s}", end="")
    for lb in lookbacks:
        print(f"  lb={lb:2d}({n}종목)", end="")
    print()
    print("─" * 70)

    # ── 전체 stage 목록 (파이프라인 순서)
    STAGE_ORDER = ["no_data", "no_structure", "no_sweep", "prefilter_fail",
                   "grade_fail", "mtf_blocked", "displacement_fail", "edt_block",
                   "other", "signal"]
    all_stages = set()
    for lb_rows in results.values():
        all_stages.update(r["stage"] for r in lb_rows)
    stages = [s for s in STAGE_ORDER if s in all_stages]

    counters = {lb: Counter(r["stage"] for r in rows) for lb, rows in results.items()}

    for stage in stages:
        marker = "✅" if stage == "signal" else ("🔶" if stage in ("no_sweep","prefilter_fail","grade_fail") else "  ")
        print(f"  {marker} {stage:<18s}", end="")
        for lb in lookbacks:
            cnt = counters[lb].get(stage, 0)
            pct = cnt / n * 100
            print(f"  {cnt:3d} ({pct:4.0f}%)", end="")
        print()

    # ── swing_count 평균
    print("─" * 70)
    print(f"  {'avg swing_count':<20s}", end="")
    for lb in lookbacks:
        avg = sum(r["swing_count"] for r in results[lb]) / n
        print(f"  {avg:8.1f}    ", end="")
    print()

    # ── CHoCH 발생 건수
    print(f"  {'choch detected':<20s}", end="")
    for lb in lookbacks:
        cnt = sum(1 for r in results[lb] if r["choch"])
        print(f"  {cnt:3d} ({cnt/n*100:4.0f}%)  ", end="")
    print()

    print(f"{'═'*70}")

    # ── 해석 가이드
    if len(lookbacks) >= 2:
        lb_old, lb_new = lookbacks[0], lookbacks[1]  # compare 모드: [20, 10] 순서
        old_no_struct = counters[lb_old].get("no_structure", 0)
        new_no_struct = counters[lb_new].get("no_structure", 0)
        delta = old_no_struct - new_no_struct
        print(f"\n[해석]")
        print(f"  no_structure 감소: {old_no_struct} → {new_no_struct} (Δ{delta:+d})", end="")
        if delta > 0:
            downstream = sum(counters[lb_new].get(s, 0) for s in
                             ["no_sweep","prefilter_fail","grade_fail","mtf_blocked","signal"])
            print(f"  ✅ 병목이 downstream으로 이동 ({downstream}건)")
        elif delta == 0:
            print(f"  → no_structure 변화 없음. lookback 외 다른 병목 의심")
        else:
            print(f"  ⚠ no_structure 오히려 증가 — 예상치 못한 결과")
        print()

# ── 메인 실행 ─────────────────────────────────────────────────────────────────

def run_single(symbol: str, date_str: str, bars: int,
               cfg: dict, strat: SMCStrategy,
               expected_grade: str = None, save_fixture: bool = False):
    df5  = load_5m(symbol, date_str, bars=bars, save=save_fixture)
    df30 = load_30m(symbol, date_str, bars=50)

    if df5 is None:
        print(f"\n❌ {symbol} {date_str}: 5분봉 데이터 없음 (yfinance 조회 실패 — 60일 초과 또는 상장폐지)")
        return False

    signal, reason, details = strat.check_entry_signal(
        df=df5, debug=False, df_htf=df30,
        symbol=symbol, market_regime=None,
    )
    print_pipeline_result(symbol, date_str, df5, signal, reason, details, cfg, expected_grade)
    return signal


def main():
    ap = argparse.ArgumentParser(description="SMC 파이프라인 독립 검증")
    ap.add_argument("--symbol",     default="456010",  help="단일 종목코드")
    ap.add_argument("--symbols",    nargs="+",         help="다수 종목코드")
    ap.add_argument("--date",       default=None,      help="YYYYMMDD 또는 YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--bars",       type=int, default=120, help="5분봉 수 (기본: 120)")
    ap.add_argument("--known-good", action="store_true",
                    help="DB 실거래 케이스 회귀 테스트 (2026-02-10 기준)")
    ap.add_argument("--batch",      action="store_true",
                    help="--symbols 종목 배치 실행 + reason distribution 집계")
    ap.add_argument("--compare",      action="store_true",
                    help="lb=20(old) vs lb=10(new) 동시 비교 — 병목 이동 측정")
    ap.add_argument("--save-fixture", action="store_true",
                    help="5분봉 데이터를 tests/fixtures/에 저장 (historical replay 준비)")
    args = ap.parse_args()

    cfg = load_config()

    # 날짜 파싱 (공통)
    if args.date:
        date_str = args.date.replace("-", "")
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    # 종목 목록 (공통)
    symbols = args.symbols if args.symbols else [args.symbol]

    save_fix = args.save_fixture

    # ── --compare: lb=20 vs lb=10 병목 이동 측정
    if args.compare:
        print(f"\n[COMPARE] swing_lookback 20(old) vs 10(new) — 병목 이동 측정")
        print(f"  목적: rollback 효과 수치화. no_structure 감소 & downstream 단계 증가 여부 확인")
        print(f"  성공 기준: no_structure 감소 + no_sweep/prefilter_fail 증가")
        if save_fix:
            print(f"  💾 --save-fixture ON: 데이터 → tests/fixtures/ 저장")
        results = run_batch(symbols, date_str, args.bars, cfg, lookbacks=[20, 10],
                            save_fixture=save_fix)
        print_batch_report(results, date_str)
        return

    # ── --batch: 현재 설정으로 reason 분포 측정
    if args.batch:
        lb = cfg.get("smc", {}).get("swing_lookback", 10)
        print(f"\n[BATCH] reason distribution (swing_lookback={lb})")
        if save_fix:
            print(f"  💾 --save-fixture ON: 데이터 → tests/fixtures/ 저장")
        results = run_batch(symbols, date_str, args.bars, cfg, lookbacks=[lb],
                            save_fixture=save_fix)
        print_batch_report(results, date_str)
        return

    strat = build_smc_strategy(cfg)

    if args.known_good:
        print("\n📋 Known-Good Regression Test (DB 실거래 재현)")
        print("   목적: 현재 코드로 과거 실거래 데이터를 재실행했을 때 신호가 나오는가?")
        print("   주의: yfinance 5분봉은 최근 60일만 가능. 그 이전 날짜는 SKIP.\n")
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=58)
        passed = failed = skipped = 0
        for symbol, date_str, _, grade in KNOWN_GOOD:
            dt = datetime.strptime(date_str, "%Y%m%d")
            if dt < cutoff:
                print(f"  ⏭  SKIP {symbol} {date_str}: yfinance 60일 범위 초과")
                skipped += 1
                continue
            result = run_single(symbol, date_str, args.bars, cfg, strat,
                                expected_grade=grade, save_fixture=save_fix)
            if result:
                passed += 1
            else:
                failed += 1
        print(f"\n[REGRESSION SUMMARY]  ✅ {passed}건 재현  ❌ {failed}건 미재현  ⏭ {skipped}건 SKIP(데이터 없음)")
        if failed > 0:
            print("  → 미재현 케이스: threshold 변경 또는 로직 수정 의심")
        if skipped == len(KNOWN_GOOD):
            print("  → 모두 60일 초과. --symbol 456010 으로 오늘 데이터 테스트 권장")
        return

    for sym in symbols:
        run_single(sym, date_str, args.bars, cfg, strat, save_fixture=save_fix)


if __name__ == "__main__":
    main()
