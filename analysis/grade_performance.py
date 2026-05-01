"""
grade_performance.py — SMC 등급별 / 타입별 성과 분해 분석

사용법:
    python -m analysis.grade_performance
    python -m analysis.grade_performance --days 14
    python -m analysis.grade_performance --date 2026-03-20
    python -m analysis.grade_performance --source mfe   # MFE CSV 전체

출력:
    - A/B/C 등급별 승률 / 평균 PnL / 건수
    - Sweep vs Fallback vs C_FALLBACK 성과
    - 시간대별 PnL
    - 손익비 (avg_win / avg_loss)
"""

import csv
import re
import json
import argparse
from pathlib import Path
from datetime import date, timedelta, datetime
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
MFE_CSV = LOG_DIR / "smc_mfe_analysis.csv"


# ──────────────────────────────────────────────
# 데이터 구조
# ──────────────────────────────────────────────

@dataclass
class Trade:
    date: str
    stock_code: str
    stock_name: str = ""
    entry_time: str = ""
    exit_time: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    grade: str = "?"          # A / B / C / ?
    entry_type: str = "?"     # sweep / b_fallback / c_fallback / unknown
    exit_reason: str = ""
    holding_min: float = 0.0
    won: Optional[bool] = None


# ──────────────────────────────────────────────
# MFE CSV 파싱 (주요 데이터 소스)
# ──────────────────────────────────────────────

RE_GRADE = re.compile(r"CHoCH\[([ABC])급\]")
RE_SWEEP = re.compile(r"Liquidity Sweep")
RE_C_FALLBACK = re.compile(r"C_FALLBACK|C등급 fallback")
RE_B_FALLBACK = re.compile(r"FALLBACK|fallback")


def classify_entry_reason(entry_reason: str, grade_field: str = "") -> tuple[str, str]:
    """entry_reason 문자열 → (grade, entry_type)"""
    grade_m = RE_GRADE.search(entry_reason)
    grade = grade_m.group(1) if grade_m else (grade_field if grade_field and grade_field != "-" else "?")

    if RE_C_FALLBACK.search(entry_reason):
        entry_type = "c_fallback"
    elif RE_B_FALLBACK.search(entry_reason) and not RE_SWEEP.search(entry_reason):
        entry_type = "b_fallback"
    elif RE_SWEEP.search(entry_reason):
        entry_type = "sweep"
    else:
        entry_type = "unknown"

    return grade, entry_type


def load_mfe_csv(since_date: Optional[str] = None) -> list[Trade]:
    """logs/smc_mfe_analysis.csv 파싱 → Trade 리스트."""
    if not MFE_CSV.exists():
        return []

    trades = []
    with open(MFE_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            buy_time_str = row.get("buy_time", "")
            if not buy_time_str:
                continue

            try:
                buy_dt = datetime.strptime(buy_time_str, "%Y-%m-%d %H:%M")
            except ValueError:
                continue

            trade_date = buy_dt.strftime("%Y-%m-%d")
            if since_date and trade_date < since_date:
                continue

            sell_time_str = row.get("sell_time", "")
            try:
                sell_dt = datetime.strptime(sell_time_str, "%Y-%m-%d %H:%M")
                holding_min = (sell_dt - buy_dt).total_seconds() / 60
            except ValueError:
                holding_min = 0.0

            pnl_pct = float(row.get("profit_rate", 0) or 0)
            grade_field = row.get("grade", "-") or "-"
            entry_reason = row.get("entry_reason", "")
            grade, entry_type = classify_entry_reason(entry_reason, grade_field)

            t = Trade(
                date=trade_date,
                stock_code=row.get("stock_code", ""),
                stock_name=row.get("stock_name", ""),
                entry_time=buy_dt.strftime("%H:%M:%S"),
                exit_time=sell_dt.strftime("%H:%M:%S") if sell_time_str else "",
                entry_price=float(row.get("entry_price", 0) or 0),
                exit_price=float(row.get("exit_price", 0) or 0),
                pnl_pct=pnl_pct,
                grade=grade,
                entry_type=entry_type,
                exit_reason=row.get("exit_reason", ""),
                holding_min=holding_min,
                won=pnl_pct > 0,
            )
            trades.append(t)

    return trades


def load_reentry_reports(days: int = 7) -> list[dict]:
    """logs/reentry_report_*.json 파일들 로드."""
    reports = []
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        path = LOG_DIR / f"reentry_report_{d}.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    reports.append(json.load(f))
            except Exception:
                pass
    return reports


# ──────────────────────────────────────────────
# 분석 함수
# ──────────────────────────────────────────────

def bucket_stats(trades: list[Trade], key_fn) -> dict:
    """trades를 key_fn으로 분류 → 버킷별 통계."""
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        buckets[key_fn(t)].append(t)

    result = {}
    for k, ts in sorted(buckets.items()):
        completed = [t for t in ts if t.won is not None]
        wins = [t for t in completed if t.won]
        losses = [t for t in completed if not t.won]

        win_rate = len(wins) / len(completed) * 100 if completed else 0
        avg_pnl = sum(t.pnl_pct for t in completed) / len(completed) if completed else 0
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

        result[k] = {
            "count": len(ts),
            "completed": len(completed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "rr": rr,
        }
    return result


def time_bucket(entry_time: str) -> str:
    """HH:MM:SS → 시간대 버킷."""
    if not entry_time:
        return "?"
    h, m = int(entry_time[:2]), int(entry_time[3:5])
    if h < 10 or (h == 10 and m < 30):
        return "10:00~10:30"
    elif h == 10 or (h == 11 and m < 0):
        return "10:30~11:00"
    elif h == 11 and m < 30:
        return "11:00~11:30"
    elif h == 11:
        return "11:30~12:00"
    elif h == 12 and m < 30:
        return "12:00~12:30"
    else:
        return "12:30+"


# ──────────────────────────────────────────────
# 출력
# ──────────────────────────────────────────────

def fmt_stat(s: dict) -> str:
    rr_str = f"{s['rr']:.2f}" if s['rr'] != float("inf") else "∞"
    return (
        f"건수:{s['count']:3d}({s['completed']}완결)  "
        f"승률:{s['win_rate']:5.1f}%  "
        f"평균:{s['avg_pnl']:+.2f}%  "
        f"승:{s['avg_win']:+.2f}% / 패:{s['avg_loss']:+.2f}%  "
        f"RR:{rr_str}"
    )


def print_section(title: str, stats: dict):
    bar = "─" * 70
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)
    if not stats:
        print("  데이터 없음")
        return
    for k, s in stats.items():
        warn = ""
        if s["win_rate"] < 40 and s["completed"] >= 3:
            warn = "  ⚠ 승률 위험"
        elif s["rr"] < 0.8 and s["completed"] >= 3:
            warn = "  ⚠ 손익비 위험"
        print(f"  {k:20s} {fmt_stat(s)}{warn}")


def print_report(trades: list[Trade], label: str = ""):
    completed = [t for t in trades if t.won is not None]
    print("\n" + "═" * 70)
    print(f"  📊 SMC 성과 분석 — {label}")
    print(f"  총 {len(trades)}건 (완결 {len(completed)}건)")
    print("═" * 70)

    if not completed:
        print("\n  완결 거래 없음 — 데이터 부족")
        return

    # 1. 등급별
    print_section("① 등급별 (A / B / C)", bucket_stats(trades, lambda t: f"Grade {t.grade}"))

    # 2. 진입 타입별
    type_label = {
        "sweep": "Sweep (정상)",
        "b_fallback": "B_FALLBACK (no sweep)",
        "c_fallback": "C_FALLBACK",
        "unknown": "기타",
    }
    print_section(
        "② 진입 타입별",
        bucket_stats(trades, lambda t: type_label.get(t.entry_type, t.entry_type))
    )

    # 3. 시간대별
    print_section("③ 시간대별", bucket_stats(trades, lambda t: time_bucket(t.entry_time)))

    # 4. 전체 요약
    wins = [t for t in completed if t.won]
    losses = [t for t in completed if not t.won]
    avg_hold = sum(t.holding_min for t in completed) / len(completed) if completed else 0
    win_rate = len(wins) / len(completed) * 100 if completed else 0
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    rr = abs(avg_win / avg_loss) if avg_loss else float("inf")

    print(f"\n{'─'*70}")
    print(f"  📌 전체 요약")
    print(f"{'─'*70}")
    print(f"  승률   : {win_rate:.1f}%  ({len(wins)}승 / {len(losses)}패)")
    print(f"  평균PnL: {sum(t.pnl_pct for t in completed)/len(completed):+.2f}%")
    print(f"  평균수익: {avg_win:+.2f}%  / 평균손실: {avg_loss:+.2f}%")
    print(f"  손익비  : {rr:.2f}")
    print(f"  평균보유: {avg_hold:.0f}분")

    # 5. 경고
    print(f"\n{'─'*70}")
    print(f"  ⚡ 액션 시그널")
    print(f"{'─'*70}")
    c_trades = [t for t in completed if t.entry_type == "c_fallback"]
    if c_trades:
        c_wr = sum(1 for t in c_trades if t.won) / len(c_trades) * 100
        print(f"  C_FALLBACK  {len(c_trades)}건 / 승률 {c_wr:.0f}% → {'✅ OK' if c_wr >= 50 else '🔴 max_c_fallback_per_day: 0 유지'}")
    else:
        print(f"  C_FALLBACK  데이터 없음 (현재 OFF 상태)")

    b_fb = [t for t in completed if t.entry_type == "b_fallback"]
    if b_fb:
        b_wr = sum(1 for t in b_fb if t.won) / len(b_fb) * 100
        print(f"  B_FALLBACK  {len(b_fb)}건 / 승률 {b_wr:.0f}% → {'✅ OK' if b_wr >= 50 else '⚠ sweep_fallback_enabled 검토'}")

    sweep = [t for t in completed if t.entry_type == "sweep"]
    if sweep:
        s_wr = sum(1 for t in sweep if t.won) / len(sweep) * 100
        print(f"  Sweep       {len(sweep)}건 / 승률 {s_wr:.0f}% → {'✅ 핵심 엔진 정상' if s_wr >= 55 else '⚠ SMC 구조 검토'}")

    print()


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SMC 등급별 성과 분석")
    parser.add_argument("--days", type=int, default=90, help="최근 N일 분석 (기본 90)")
    parser.add_argument("--date", type=str, default=None, help="특정 날짜 (YYYY-MM-DD)")
    parser.add_argument("--source", type=str, default="mfe", choices=["mfe", "log"],
                        help="데이터 소스: mfe=CSV(기본), log=auto_trading 로그")
    args = parser.parse_args()

    if args.source == "mfe":
        since_date = None
        if args.date:
            since_date = args.date
        elif args.days:
            since_date = (date.today() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        all_trades = load_mfe_csv(since_date)
        label = f"MFE CSV (since {since_date or '전체'})"
    else:
        all_trades = []

    # 데이터 소스: reentry reports (메타데이터)
    reports = load_reentry_reports(args.days)

    label = label if args.source == "mfe" else f"최근 {args.days}일"
    print_report(all_trades, label)

    # Reentry report 요약
    if reports:
        total_ef = sum(r.get("ef_events", {}).get("total", 0) for r in reports)
        total_blocked = sum(r.get("blocked_count", 0) for r in reports)
        print(f"  [Reentry] {len(reports)}일치 리포트: EF {total_ef}건, 쿨다운차단 {total_blocked}건")

    # 현재 설정 요약
    import yaml
    cfg_path = ROOT / "config" / "strategy_hybrid.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        smc = cfg.get("smc", {})
        print(f"\n  [현재 설정]")
        print(f"    max_c_fallback_per_day : {smc.get('max_c_fallback_per_day', '-')}")
        print(f"    grade_c_fallback_size  : {smc.get('grade_c_fallback_size_mult', '-')}")
        print(f"    sweep_fallback_enabled : {smc.get('sweep_fallback_enabled', '-')}")
        print(f"    max_fallback_per_day   : {smc.get('max_fallback_per_day', '-')}")
        print()


if __name__ == "__main__":
    main()
