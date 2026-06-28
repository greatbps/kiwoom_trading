#!/usr/bin/env python3
"""
analysis/structure_replay.py — Structure State Timeline Reconstruction

목적:
  캔들 단위로 analyze_structure() 상태가 어떻게 진화했는지 재현.
  "T9a 전환이 정확히 어느 캔들에서 발생했는가, 그 캔들이 CHoCH 조건을 충족했는가"
  에 답한다.

핵심 관측:
  - T9a: prev=BEARISH → curr=RANGING (last_candle이 HH 분류되어 구조 붕괴)
  - CHoCH window: T9a 전환 캔들이 prev_structure.last_lh를 실제로 돌파했는가?
    → True면 EXP-002 활성화 시 CHoCH 복구 가능

사용:
  python3 -m analysis.structure_replay --symbol 036570
  python3 -m analysis.structure_replay --symbol 036570 --date 20260527
  python3 -m analysis.structure_replay --symbols 036570 012330 --date 20260527
  python3 -m analysis.structure_replay --symbol 036570 --verbose   # 전체 타임라인
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from analyzers.smc.smc_structure import SMCStructureAnalyzer, MarketTrend


# ── fixture / 설정 ────────────────────────────────────────────────────────────

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"

def _load_fixture(symbol: str, date: str) -> pd.DataFrame | None:
    path = FIXTURE_DIR / f"smc_{symbol}_{date}_5m.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def _load_config_lookback() -> tuple[int, float]:
    """strategy_hybrid.yaml에서 swing_lookback / min_swing_size_pct 읽기."""
    import yaml
    p = Path("config/strategy_hybrid.yaml")
    if not p.exists():
        p = Path(__file__).parent.parent / "config/strategy_hybrid.yaml"
    try:
        cfg = yaml.safe_load(open(p, encoding="utf-8"))
        smc = cfg.get("smc", {})
        return smc.get("swing_lookback", 10), smc.get("min_swing_size_pct", 0.3)
    except Exception:
        return 10, 0.3


# ── 1종목 replay ──────────────────────────────────────────────────────────────

def replay_symbol(
    symbol: str,
    date: str,
    *,
    verbose: bool = False,
    lookback: int | None = None,
    min_swing_pct: float | None = None,
) -> dict:
    """
    캔들별 structure 상태 재현 → 요약 dict 반환.

    Returns:
        {
          'symbol': str,
          'date': str,
          'total_candles': int,
          'timeline': list[dict],      # 캔들별 상태 (verbose=True 시 전체)
          't9a_events': list[dict],    # T9a 전환 이벤트만
          'choch_window_events': list[dict],  # T9a + CHoCH 조건 동시 충족
        }
    """
    df = _load_fixture(symbol, date)
    if df is None:
        return {"symbol": symbol, "date": date, "error": "fixture 없음"}

    _lb, _sp = _load_config_lookback()
    lb  = lookback     if lookback      is not None else _lb
    msp = min_swing_pct if min_swing_pct is not None else _sp

    az = SMCStructureAnalyzer(swing_lookback=lb, min_swing_size_pct=msp)

    # find_swing_points threshold: swing_lookback * 2 + 5 + 1 (prev slice는 +6)
    _min_rows = lb * 2 + 6

    timeline = []
    prev_structure = None
    prev_trend = None

    for i in range(1, len(df) + 1):
        df_slice = df.iloc[:i]
        structure = az.analyze_structure(df_slice)
        curr_trend = structure.trend

        # detect_choch가 평가하는 캔들: df_slice.iloc[i-2] (= df.iloc[i-2])
        checked_idx = i - 2
        checked_ts  = df.index[checked_idx] if checked_idx >= 0 else None

        # T9a: 이전 단계에서는 BEARISH였으나 현재는 RANGING
        t9a = (prev_structure is not None
               and prev_structure.trend == MarketTrend.BEARISH
               and curr_trend == MarketTrend.RANGING)

        # CHoCH window (EXP-002 조건):
        # 현재 구조는 RANGING이지만, prev_structure.last_lh를 체크 캔들이 돌파했는가?
        choch_window = False
        choch_close  = None
        choch_lh     = None
        if (t9a
                and prev_structure.last_lh is not None
                and checked_idx >= 0):
            choch_lh  = prev_structure.last_lh.price
            ck = df.iloc[checked_idx]
            c_range    = ck["high"] - ck["low"]
            c_body     = abs(ck["close"] - ck["open"])
            body_ratio = c_body / c_range if c_range > 0 else 0
            choch_close = ck["close"]
            choch_window = (
                ck["high"]  > choch_lh
                and ck["close"] > choch_lh
                and body_ratio >= 0.5
            )

        entry = {
            "step":        i,
            "candle_ts":   str(df.index[i - 1]),
            "checked_ts":  str(checked_ts) if checked_ts is not None else None,
            "trend":       curr_trend.value,
            "prev_trend":  prev_structure.trend.value if prev_structure else None,
            "swing_count": len(structure.swing_points),
            "last_lh":     structure.last_lh.price if structure.last_lh else None,
            "prev_lh":     prev_structure.last_lh.price if prev_structure and prev_structure.last_lh else None,
            "t9a":         t9a,
            "choch_window":       choch_window,
            "choch_lh_level":     choch_lh,
            "choch_candle_close": choch_close,
        }
        timeline.append(entry)
        prev_structure = structure
        prev_trend = curr_trend

    t9a_events        = [e for e in timeline if e["t9a"]]
    choch_window_evts = [e for e in timeline if e["choch_window"]]

    return {
        "symbol":              symbol,
        "date":                date,
        "total_candles":       len(df),
        "lookback":            lb,
        "timeline":            timeline if verbose else [],
        "t9a_events":          t9a_events,
        "choch_window_events": choch_window_evts,
    }


# ── 출력 ──────────────────────────────────────────────────────────────────────

def _fmt_ts(ts_str: str | None) -> str:
    if not ts_str:
        return "??"
    try:
        return pd.Timestamp(ts_str).strftime("%H:%M")
    except Exception:
        return str(ts_str)[-8:]


def print_summary(result: dict, symbol_name: str = "") -> None:
    label = f"{result['symbol']}" + (f" ({symbol_name})" if symbol_name else "")
    date  = result["date"]
    sep   = "─" * 60

    if "error" in result:
        print(f"[{label}] {result['error']}")
        return

    print(f"\n{sep}")
    print(f"  {label}  {date}  lb={result['lookback']}")
    print(sep)

    t9a = result["t9a_events"]
    cw  = result["choch_window_events"]

    if not t9a:
        print("  T9a 전환 없음 — 구조 안정")
        return

    print(f"  T9a 전환: {len(t9a)}회")
    for e in t9a:
        cw_flag = "  ★ CHoCH 조건 충족 (EXP-002 복구 가능)" if e["choch_window"] else ""
        print(f"    step={e['step']:3d}  "
              f"candle={_fmt_ts(e['candle_ts'])}  "
              f"checked={_fmt_ts(e['checked_ts'])}  "
              f"bearish→ranging  swings={e['swing_count']}"
              f"{cw_flag}")
        if e["choch_window"]:
            print(f"         prev_lh={e['choch_lh_level']:.0f}  "
                  f"candle_close={e['choch_candle_close']:.0f}")

    if cw:
        print(f"\n  [EXP-002 영향] CHoCH 조건 충족 T9a: {len(cw)}회")
        print("  → enabled=true 전환 시 이 캔들들에서 CHoCH 복구됨")
    else:
        print("\n  [EXP-002 영향] T9a 발생했으나 CHoCH 조건 미충족 (penetration/body 기준 미달)")


def print_verbose_timeline(result: dict, around_t9a: bool = True) -> None:
    """T9a 전후 ±3 캔들 또는 전체 타임라인 출력."""
    tl = result.get("timeline") or []
    if not tl:
        print("  (verbose 모드에서만 타임라인 출력 — --verbose 옵션 사용)")
        return

    t9a_steps = {e["step"] for e in result["t9a_events"]}
    if around_t9a and t9a_steps:
        # T9a 주변 ±4 캔들만
        show_steps = set()
        for s in t9a_steps:
            show_steps.update(range(s - 4, s + 5))
        tl = [e for e in tl if e["step"] in show_steps]

    print(f"\n  {'step':>4}  {'candle':>5}  {'trend':>8}  {'swings':>6}  "
          f"{'last_lh':>8}  {'T9a':>4}  {'CHoCH?':>6}")
    print("  " + "─" * 56)
    for e in tl:
        flag = ""
        if e["t9a"]:
            flag = "T9a" + (" ★" if e["choch_window"] else "  ")
        elif e["choch_window"]:
            flag = "    ★"
        print(f"  {e['step']:4d}  {_fmt_ts(e['candle_ts']):>5}  "
              f"{e['trend']:>8}  {e['swing_count']:6d}  "
              f"{str(e['last_lh'] or ''):>8}  "
              f"{flag}")


# ── CLI ───────────────────────────────────────────────────────────────────────

SYMBOL_NAMES = {
    "000660": "SK하이닉스", "005430": "한국공항", "010060": "OCI홀딩스",
    "012330": "현대모비스", "036570": "NC소프트",  "336260": "두산퓨얼셀",
}


def main() -> None:
    from datetime import date as _date

    parser = argparse.ArgumentParser(description="SMC Structure State Replay")
    parser.add_argument("--symbol",  help="단일 종목 코드")
    parser.add_argument("--symbols", nargs="+", help="복수 종목 코드")
    parser.add_argument("--date",    default=None,
                        help="날짜 (YYYYMMDD 또는 YYYY-MM-DD, 기본: 오늘)")
    parser.add_argument("--all-fixtures", action="store_true",
                        help="tests/fixtures의 모든 종목 실행")
    parser.add_argument("--verbose", action="store_true",
                        help="T9a 전후 캔들 타임라인 출력")
    parser.add_argument("--lookback", type=int, default=None)
    args = parser.parse_args()

    # 날짜 정규화
    if args.date:
        _d = args.date.replace("-", "")
    else:
        _d = _date.today().strftime("%Y%m%d")

    # 종목 목록
    if args.all_fixtures:
        symbols = [p.name.split("_")[1]
                   for p in FIXTURE_DIR.glob(f"smc_*_{_d}_5m.csv")]
    elif args.symbols:
        symbols = args.symbols
    elif args.symbol:
        symbols = [args.symbol]
    else:
        parser.print_help()
        sys.exit(1)

    if not symbols:
        print(f"fixture 없음: {FIXTURE_DIR}/*_{_d}_5m.csv")
        sys.exit(0)

    for sym in symbols:
        result = replay_symbol(sym, _d, verbose=args.verbose,
                               lookback=args.lookback)
        name = SYMBOL_NAMES.get(sym, "")
        print_summary(result, name)
        if args.verbose:
            print_verbose_timeline(result, around_t9a=True)

    print()


if __name__ == "__main__":
    main()
