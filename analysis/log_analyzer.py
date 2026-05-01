"""
analysis/log_analyzer.py — 로그 파일 전용 분석기

DB 없이 auto_trading_YYYYMMDD.log + smc_decision_YYYYMMDD.log만으로
Signal Orchestrator 파이프라인, Market Context, CHoCH, TREND 신호를 분석한다.

사용법:
    python3 -m analysis.log_analyzer             # 오늘
    python3 -m analysis.log_analyzer 20260320    # 특정 날짜
    python3 -m analysis.log_analyzer --days 7    # 최근 7일 트렌드
    python3 -m analysis.log_analyzer --json      # JSON 출력
"""

import re
import os
import sys
import json
from datetime import date, datetime, timedelta
from collections import Counter
from typing import Optional

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')

# ─── 정규식 패턴 ──────────────────────────────────────────────────────────────

_RE_ACCEPT = re.compile(
    r'✅ ACCEPT (\d{6}) @(\d+)원 \| PID:(\d+) \| conf=([\d.]+) alpha=([+-][\d.]+) pos_mult=([\d.]+)'
)
_RE_REJECT = re.compile(
    r'❌ REJECT (\d{6}) \| PID:\d+ \| (\S+) \| (.+)'
)
_RE_TIME_BLOCK = re.compile(
    r'⏰ (\d{6}): 🚫 SMC .+? 진입 차단'
)
_RE_CHOCH = re.compile(
    r'\[CHOCH\] (\d{6}) \| (bullish|bearish) \| level=(\d+) \| wick=(\d+) \| close=(\d+) \| penetration=([\d.]+)%'
)
_RE_NO_TRADE = re.compile(
    r'\[MKT_CTX\] 🚫 NO_TRADE_DAY: (.+)'
)
_RE_TREND_SIG = re.compile(
    r'\[TREND_SIG\] TREND (BREAKOUT|PULLBACK)\[(\w+)\]: (.+)'
)


# ─── 순수 파서 함수 ───────────────────────────────────────────────────────────

def parse_orchestrator_events(lines: list[str]) -> list[dict]:
    """
    ACCEPT / REJECT / TIME_BLOCK 이벤트를 파싱한다.

    중복 라인(timestamp 포함 버전 + 단독 버전)은 stock_code+type 기준 dedup.

    Returns:
        list of dict with keys: type, stock_code, (price|reason|level), conf, pos_mult
    """
    seen: set[tuple] = set()
    results: list[dict] = []

    for line in lines:
        m = _RE_ACCEPT.search(line)
        if m:
            code = m.group(1)
            key  = ("ACCEPT", code, int(m.group(2)))
            if key not in seen:
                seen.add(key)
                results.append({
                    "type":       "ACCEPT",
                    "stock_code": code,
                    "price":      int(m.group(2)),
                    "pid":        m.group(3),
                    "conf":       float(m.group(4)),
                    "pos_mult":   float(m.group(6)),
                })
            continue

        m = _RE_REJECT.search(line)
        if m:
            code   = m.group(1)
            level  = m.group(2)
            reason = m.group(3).strip()
            key    = ("REJECT", code, reason[:30])
            if key not in seen:
                seen.add(key)
                results.append({
                    "type":       "REJECT",
                    "stock_code": code,
                    "level":      level,
                    "reason":     reason,
                })
            continue

        m = _RE_TIME_BLOCK.search(line)
        if m:
            code = m.group(1)
            key  = ("TIME_BLOCK", code)
            if key not in seen:
                seen.add(key)
                results.append({
                    "type":       "TIME_BLOCK",
                    "stock_code": code,
                })

    return results


def parse_choch_events(lines: list[str]) -> list[dict]:
    """
    smc_decision 로그의 [CHOCH] 라인을 파싱한다.

    동일 종목이 반복 감지되는 경우 첫 번째만 포함 (level 변경 시 갱신).

    Returns:
        list of dict: stock_code, direction, level, wick, close, penetration
    """
    seen: dict[str, dict] = {}

    for line in lines:
        m = _RE_CHOCH.search(line)
        if not m:
            continue
        code  = m.group(1)
        level = int(m.group(3))
        entry = {
            "stock_code":  code,
            "direction":   m.group(2),
            "level":       level,
            "wick":        int(m.group(4)),
            "close":       int(m.group(5)),
            "penetration": float(m.group(6)),
        }
        if code not in seen or seen[code]["level"] != level:
            seen[code] = entry

    return list(seen.values())


def parse_mkt_ctx(lines: list[str]) -> dict:
    """
    Market Context 상태를 파싱한다.

    Returns:
        dict: {no_trade_day: bool, reason: str}
    """
    for line in lines:
        m = _RE_NO_TRADE.search(line)
        if m:
            return {"no_trade_day": True, "reason": m.group(1).strip()}
    return {"no_trade_day": False, "reason": ""}


def parse_trend_signals(lines: list[str]) -> list[dict]:
    """
    [TREND_SIG] 태그의 BREAKOUT / PULLBACK 신호를 파싱한다.
    [TREND_NO_SIG]는 포함하지 않는다.

    Returns:
        list of dict: entry_type, grade, detail
    """
    results: list[dict] = []
    for line in lines:
        m = _RE_TREND_SIG.search(line)
        if not m:
            continue
        entry_type = m.group(1).lower()   # "breakout" | "pullback"
        grade      = m.group(2)           # "STRONG" | "NORMAL" | "WEAK"
        detail     = m.group(3).strip()
        results.append({
            "entry_type": entry_type,
            "grade":      grade,
            "detail":     detail,
        })
    return results


def summarize(
    events: list[dict],
    chochs: list[dict],
    mkt_ctx: dict,
    trend_signals: list[dict],
) -> dict:
    """
    파싱 결과를 일일 요약 dict로 집계한다.

    Returns:
        dict with keys:
            accept_count, reject_count, time_block_count,
            choch_count, no_trade_day, trend_count,
            reject_reasons (Counter), trend_grades (Counter)
    """
    accepts     = [e for e in events if e["type"] == "ACCEPT"]
    rejects     = [e for e in events if e["type"] == "REJECT"]
    time_blocks = [e for e in events if e["type"] == "TIME_BLOCK"]

    return {
        "accept_count":     len(accepts),
        "reject_count":     len(rejects),
        "time_block_count": len(time_blocks),
        "choch_count":      len(chochs),
        "no_trade_day":     mkt_ctx.get("no_trade_day", False),
        "mkt_ctx_reason":   mkt_ctx.get("reason", ""),
        "trend_count":      len(trend_signals),
        "reject_reasons":   Counter(e["reason"][:50] for e in rejects),
        "trend_grades":     Counter(s["grade"] for s in trend_signals),
    }


# ─── 로그 로더 ───────────────────────────────────────────────────────────────

def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.readlines()


def analyze_day(target: date) -> dict:
    """하루치 로그를 종합 분석하여 요약 dict를 반환한다."""
    ds = target.strftime("%Y%m%d")

    main_lines  = _read_lines(os.path.join(LOG_DIR, f"auto_trading_{ds}.log"))
    choch_lines = _read_lines(os.path.join(LOG_DIR, f"smc_decision_{ds}.log"))

    events  = parse_orchestrator_events(main_lines)
    chochs  = parse_choch_events(choch_lines)
    mkt_ctx = parse_mkt_ctx(main_lines)
    trends  = parse_trend_signals(main_lines)

    result = summarize(events=events, chochs=chochs, mkt_ctx=mkt_ctx, trend_signals=trends)
    result["date"] = target.isoformat()
    return result


# ─── 출력 포매터 ─────────────────────────────────────────────────────────────

def _print_day(s: dict):
    print(f"\n{'='*55}")
    print(f"  📊 Log Analysis — {s['date']}")
    print(f"{'='*55}")

    if s["no_trade_day"]:
        print(f"  🚫 NO_TRADE_DAY")
        if s["mkt_ctx_reason"]:
            reason_short = s["mkt_ctx_reason"][:80]
            print(f"     {reason_short}")
    else:
        print(f"  🌐 거래 가능일")

    print(f"\n  [Signal Orchestrator]")
    print(f"    ACCEPT       : {s['accept_count']}건")
    print(f"    REJECT       : {s['reject_count']}건")
    print(f"    SMC 시간차단 : {s['time_block_count']}건")

    if s["reject_reasons"]:
        print(f"\n  [REJECT 사유 TOP 5]")
        for reason, cnt in s["reject_reasons"].most_common(5):
            print(f"    {cnt:3d}×  {reason}")

    print(f"\n  [SMC CHoCH]")
    print(f"    고유 종목    : {s['choch_count']}개")

    print(f"\n  [TREND Breakout]")
    if s["trend_count"] == 0:
        print(f"    신호 없음")
    else:
        print(f"    신호 수      : {s['trend_count']}건")
        for grade, cnt in s["trend_grades"].most_common():
            print(f"    {grade:8s}: {cnt}건")

    print(f"{'='*55}")


def _print_multi(summaries: list[dict]):
    print(f"\n{'='*65}")
    print(f"  📈 Log Trend ({len(summaries)}일)")
    print(f"{'='*65}")
    header = f"  {'날짜':10s}  {'거래가능':6s}  {'ACCEPT':6s}  {'REJECT':6s}  {'CHoCH':5s}  {'TREND':5s}"
    print(header)
    print(f"  {'-'*60}")
    for s in summaries:
        ntd = "🚫NO_TRADE" if s["no_trade_day"] else "✅가능"
        print(
            f"  {s['date']:10s}  {ntd:10s}  {s['accept_count']:6d}  "
            f"{s['reject_count']:6d}  {s['choch_count']:5d}  {s['trend_count']:5d}"
        )
    print(f"{'='*65}")


# ─── CLI 진입점 ──────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    args    = [a for a in args if a != "--json"]

    if "--days" in args:
        idx  = args.index("--days")
        days = int(args[idx + 1]) if idx + 1 < len(args) else 7
        today = date.today()
        summaries = [analyze_day(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
        if as_json:
            print(json.dumps(summaries, ensure_ascii=False, indent=2, default=str))
        else:
            _print_multi(summaries)
        return

    target = date.today()
    if args and re.fullmatch(r'\d{8}', args[0]):
        target = datetime.strptime(args[0], "%Y%m%d").date()

    result = analyze_day(target)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_day(result)


if __name__ == "__main__":
    main()
