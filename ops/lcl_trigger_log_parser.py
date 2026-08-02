#!/usr/bin/env python3
"""
LCL Trigger Log Parser — 실시간 트리거 이벤트 추출기

Usage:
  python3 ops/lcl_trigger_log_parser.py                  # 오늘 로그
  python3 ops/lcl_trigger_log_parser.py --date 20260705  # 특정일
  python3 ops/lcl_trigger_log_parser.py --tail            # 최근 50줄 모니터

역할:
  - auto_trading_YYYYMMDD.log에서 LCL 트리거 라인 추출
  - 트리거 타입, pnl, hold_time, 발동 시각 파싱
  - 실행 갭 추정 (트리거 감지 → 청산 완료)
  - 일별 집계 → logs/lcl_trigger_summary_YYYYMMDD.json 저장
"""

import re
import json
import glob
import argparse
from datetime import date, datetime
from pathlib import Path

LOG_DIR     = Path("logs")
SUMMARY_DIR = Path("logs")

# LCL 트리거 패턴 (exit_logic_optimized.py 1-d section에서 생성)
TRIGGER_PATTERNS = [
    ("EARLY_CUT",
     re.compile(r"\[EARLY_CUT\] RSI<(?P<rsi>[\d.]+)↓ VWAP이탈(?P<vwap>\d+)봉 거래량수축↓ pnl=(?P<pnl>[\-\d.]+)%")),
    ("TIME_STOP",
     re.compile(r"\[TIME_STOP\] (?P<elapsed>[\d.]+)분≥(?P<threshold>[\d.]+)분 손실지속 pnl=(?P<pnl>[\-\d.]+)%")),
    ("TIME_STOP_MFE",
     re.compile(r"\[TIME_STOP_MFE\] MFE=(?P<mfe>[\-\d.]+)%<(?P<thr>[\d.]+)% (?P<elapsed>[\d.]+)분 pnl=(?P<pnl>[\-\d.]+)%")),
    ("VOL_TIGHT",
     re.compile(r"\[VOL_TIGHT_STOP\] bdh=(?P<bdh>[\d.]+)%≥[\d.]+% pnl=(?P<pnl>[\-\d.]+)% ≤ -[\d.]+%")),
    ("MAE_WORSENING",
     re.compile(r"\[MAE_WORSENING\] MFE=(?P<mfe>[\-\d.]+)%<[\d.]+% MAE신저점접근 pnl=(?P<pnl>[\-\d.]+)%")),
]

# 청산 완료 시그널 (SELL 주문 체결)
EXEC_PATTERN = re.compile(
    r"(?:체결|SELL|sell.*완료|청산.*완료|매도.*체결).*"
    r"(?P<stock>[A-Z0-9]{6})?.*(?P<price>[\d,]+원)?",
    re.IGNORECASE
)

LINE_TS_RE  = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")

SEP = "=" * 64


def parse_log_file(log_path: Path) -> list[dict]:
    events = []
    try:
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    for i, line in enumerate(lines):
        ts_m = LINE_TS_RE.match(line)
        if not ts_m:
            continue
        ts_str = ts_m.group(1)
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        for trig_name, pattern in TRIGGER_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue

            ev = {
                "ts":       ts,
                "trigger":  trig_name,
                "pnl":      float(m.group("pnl")) if "pnl" in m.groupdict() else None,
                "raw":      line.strip()[:120],
                "line_no":  i + 1,
            }

            # 트리거별 추가 필드
            groups = m.groupdict()
            if "elapsed" in groups: ev["elapsed_min"] = float(groups["elapsed"])
            if "mfe"     in groups: ev["mfe_pct"]     = float(groups["mfe"])
            if "bdh"     in groups: ev["bdh_pct"]      = float(groups["bdh"])
            if "rsi"     in groups: ev["rsi"]          = float(groups["rsi"])
            if "vwap"    in groups: ev["vwap_bars"]    = int(groups["vwap"])

            # 이후 30줄에서 체결 완료 라인 탐색 → execution gap 추정
            for j in range(i+1, min(i+31, len(lines))):
                ts_j_m = LINE_TS_RE.match(lines[j])
                if not ts_j_m: continue
                try:
                    ts_j = datetime.strptime(ts_j_m.group(1), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                # 체결/청산 키워드
                if any(kw in lines[j] for kw in ["체결","SELL 주문","매도 완료","청산 완료","[SELL]"]):
                    ev["exec_ts"]  = ts_j
                    ev["gap_sec"]  = (ts_j - ts).total_seconds()
                    break

            events.append(ev)
            break   # 한 라인에서 첫 번째 매칭만

    return events


def summarize(events: list[dict]) -> dict:
    if not events:
        return {"total": 0}

    by_trig = {}
    for ev in events:
        t = ev["trigger"]
        by_trig.setdefault(t, []).append(ev)

    summary = {"total": len(events), "by_trigger": {}}
    for t, evs in by_trig.items():
        pnls = [e["pnl"] for e in evs if e.get("pnl") is not None]
        gaps = [e["gap_sec"] for e in evs if e.get("gap_sec") is not None]
        summary["by_trigger"][t] = {
            "count":    len(evs),
            "pnl_avg":  round(sum(pnls)/len(pnls), 3) if pnls else None,
            "pnl_min":  round(min(pnls), 3)            if pnls else None,
            "gap_avg_sec": round(sum(gaps)/len(gaps), 1) if gaps else None,
            "gap_max_sec": round(max(gaps), 1)            if gaps else None,
        }
    return summary


def print_report(events: list[dict], summary: dict, target_date: str):
    print(SEP)
    print(f"  LCL Trigger Log Report — {target_date}")
    print(SEP)
    print(f"  총 트리거 이벤트: {summary['total']}건\n")

    if not events:
        print("  LCL 트리거 발동 없음")
        print()
        print("  발동 조건 리마인더:")
        print("    [EARLY_CUT]     RSI<38↓ + VWAP이탈3봉 + 거래량수축 + pnl≤-0.5%")
        print("    [TIME_STOP]     비스윙 + elapsed≥20m + pnl≤-0.8%")
        print("    [TIME_STOP_MFE] 비스윙 + elapsed≥20m + MFE<0.3% + pnl≤-0.3%")
        print("    [VOL_TIGHT]     비스윙 + bdh≥5% + pnl≤-2.5%")
        print("    [MAE_WORSENING] 비스윙 + MFE<0.3% + 신저점접근 + pnl≤-0.3%")
        print(SEP)
        return

    # 트리거별 요약
    print(f"  {'트리거':<18}  {'N':>3}  {'avg pnl':>8}  {'gap avg':>8}  {'기대avg':>8}")
    print(f"  {'-'*18}  {'-'*3}  {'-'*8}  {'-'*8}  {'-'*8}")
    expected = {
        "EARLY_CUT": -0.50, "TIME_STOP": -0.80,
        "TIME_STOP_MFE": -0.30, "VOL_TIGHT": -2.50, "MAE_WORSENING": -0.30,
    }
    for t, s in summary.get("by_trigger", {}).items():
        gap_s  = f"{s['gap_avg_sec']:.1f}s" if s.get("gap_avg_sec") else "—"
        avg_ok = "✅" if (s["pnl_avg"] and s["pnl_avg"] >= expected.get(t,-9)+0.3) else "⚠"
        print(f"  {t:<18}  {s['count']:>3}  {(str(s['pnl_avg'])+('%')):>8}  "
              f"{gap_s:>8}  {expected.get(t,0):>+7.2f}% {avg_ok}")

    # 이벤트 목록
    print(f"\n  이벤트 상세 ({len(events)}건):")
    print(f"  {'시각':>8}  {'트리거':<18}  {'pnl':>7}  {'elapsed':>7}  {'gap':>6}")
    print(f"  {'-'*8}  {'-'*18}  {'-'*7}  {'-'*7}  {'-'*6}")
    for ev in events:
        t_str = ev["ts"].strftime("%H:%M:%S")
        el_s  = f"{ev['elapsed_min']:.0f}m" if ev.get("elapsed_min") else "—"
        gap_s = f"{ev['gap_sec']:.0f}s"    if ev.get("gap_sec") else "—"
        print(f"  {t_str:>8}  {ev['trigger']:<18}  "
              f"{ev['pnl']:>+6.2f}%  {el_s:>7}  {gap_s:>6}")

    # Execution Gap 분석
    all_gaps = [e["gap_sec"] for e in events if e.get("gap_sec")]
    if all_gaps:
        print(f"\n  Execution Gap 분석:")
        print(f"    avg={sum(all_gaps)/len(all_gaps):.1f}s  "
              f"max={max(all_gaps):.1f}s  "
              f"min={min(all_gaps):.1f}s")
        if max(all_gaps) > 60:
            print(f"    ⚠  gap > 60초 감지 — 체결 지연 확인 필요")
        else:
            print(f"    ✅ 체결 지연 정상 범위")

    print(SEP)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,  help="YYYYMMDD (default: today)")
    parser.add_argument("--tail", action="store_true", help="최근 N일 전체")
    parser.add_argument("--save", action="store_true", help="JSON 저장")
    args = parser.parse_args()

    if args.tail:
        # 최근 5일치 전체
        log_files = sorted(glob.glob(str(LOG_DIR / "auto_trading_????????.log")))[-5:]
    else:
        target = args.date or date.today().strftime("%Y%m%d")
        log_files = [str(LOG_DIR / f"auto_trading_{target}.log")]

    all_events = []
    for lf in log_files:
        all_events.extend(parse_log_file(Path(lf)))

    target_date = args.date or date.today().isoformat()
    summary = summarize(all_events)
    print_report(all_events, summary, target_date)

    if args.save and all_events:
        out_path = SUMMARY_DIR / f"lcl_trigger_summary_{target_date.replace('-','')}.json"
        payload  = {
            "date": target_date,
            "summary": summary,
            "events": [
                {**e, "ts": e["ts"].isoformat(),
                 "exec_ts": e.get("exec_ts","").isoformat() if e.get("exec_ts") else None}
                for e in all_events
            ],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  → 저장: {out_path}")


if __name__ == "__main__":
    main()
