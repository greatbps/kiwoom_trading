"""
DEF + RS 통합 성과 리포트
"어떤 날 어떤 전략이 돈 벌었는지" 자동 분석

사용법:
    python3 -m analysis.strategy_report                # 오늘
    python3 -m analysis.strategy_report --days 7       # 최근 7일
    python3 -m analysis.strategy_report --days 30      # 월간
    python3 -m analysis.strategy_report --date 20260403
"""
import re
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

LOG_DIR = Path(__file__).parent.parent / "logs"

# 패턴 (DEF_RESULT, RS_RESULT 재사용)
DEF_PATTERN = re.compile(
    r"\[DEF_RESULT\] (\w+) \| exit=(\w+) \| pnl=(-?\d+\.\d+)% \| hold=(\d+\.\d+)m"
)
RS_PATTERN = re.compile(
    r"\[RS_RESULT\] (\w+) \| exit=(\w+) \| pnl=(-?\d+\.\d+)% \| hold=(\d+\.\d+)m"
)
SMC_BUY_PATTERN  = re.compile(r"\[매수완료\].*?([A-Z_]+).*?([+-]?\d+\.\d+)%")


def parse_day(log_path: Path) -> dict:
    """단일 로그 파일 → 당일 전략별 결과."""
    date_str = log_path.stem[-8:]
    result = {
        "date": date_str,
        "def_trades": [],
        "rs_trades": [],
        "smc_trades": [],   # 나중을 위해 (현재 구조에서는 DB 기반이 더 정확)
    }

    if not log_path.exists():
        return result

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if "[DEF_RESULT]" in line:
                m = DEF_PATTERN.search(line)
                if m:
                    result["def_trades"].append({
                        "code": m.group(1),
                        "exit": m.group(2),
                        "pnl": float(m.group(3)),
                        "hold": float(m.group(4)),
                    })
            elif "[RS_RESULT]" in line:
                m = RS_PATTERN.search(line)
                if m:
                    result["rs_trades"].append({
                        "code": m.group(1),
                        "exit": m.group(2),
                        "pnl": float(m.group(3)),
                        "hold": float(m.group(4)),
                    })

    return result


def day_summary(day: dict) -> dict:
    """당일 요약 통계."""
    def _stats(trades):
        if not trades:
            return {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0}
        n = len(trades)
        total = sum(t["pnl"] for t in trades)
        wins  = sum(1 for t in trades if t["pnl"] > 0)
        return {"n": n, "pnl": round(total, 3), "wins": wins, "losses": n - wins}

    return {
        "date":       day["date"],
        "def":        _stats(day["def_trades"]),
        "rs":         _stats(day["rs_trades"]),
        "total_pnl":  round(
            sum(t["pnl"] for t in day["def_trades"]) +
            sum(t["pnl"] for t in day["rs_trades"]), 3
        ),
    }


def format_date(d: str) -> str:
    try:
        return datetime.strptime(d, "%Y%m%d").strftime("%m/%d(%a)")
    except Exception:
        return d


def print_report(days_data: list, period: str):
    sep = "=" * 70

    print(f"\n{sep}")
    print(f"  DEF + RS 통합 성과 리포트  ({period})")
    print(sep)

    # 데이터 있는 날만
    active_days = [d for d in days_data if d["def"]["n"] > 0 or d["rs"]["n"] > 0]

    if not active_days:
        print("  ⚠️  거래 기록 없음")
        print(f"{sep}\n")
        return

    # ── 일별 테이블 ──────────────────────────────────────────────────
    print()
    print(f"  {'날짜':<12} {'DEF':>14} {'RS':>14} {'합계':>8}  {'우세전략'}")
    print(f"  {'':─<12} {'':─>14} {'':─>14} {'':─>8}  {'':─<10}")

    total_def_pnl = 0.0
    total_rs_pnl  = 0.0

    for d in active_days:
        dt     = format_date(d["date"])
        def_s  = d["def"]
        rs_s   = d["rs"]
        total  = d["total_pnl"]

        def_str = (
            f"{def_s['n']}건 {def_s['pnl']:+.2f}%"
            if def_s["n"] > 0 else "─"
        )
        rs_str = (
            f"{rs_s['n']}건 {rs_s['pnl']:+.2f}%"
            if rs_s["n"] > 0 else "─"
        )
        total_str = f"{total:+.2f}%"

        # 우세 전략
        if def_s["pnl"] > 0 and rs_s["pnl"] > 0:
            winner = "DEF+RS ✅"
        elif def_s["pnl"] > rs_s["pnl"] and def_s["n"] > 0:
            winner = "DEF" + (" 🛡️" if def_s["pnl"] > 0 else " ❌")
        elif rs_s["n"] > 0:
            winner = "RS" + (" 📈" if rs_s["pnl"] > 0 else " ❌")
        else:
            winner = "─"

        sign = "+" if total > 0 else ""
        pnl_color = "▲" if total > 0 else ("▼" if total < 0 else "─")

        print(f"  {dt:<12} {def_str:>14} {rs_str:>14} {sign}{total:.2f}% {pnl_color}  {winner}")
        total_def_pnl += def_s["pnl"]
        total_rs_pnl  += rs_s["pnl"]

    # ── 집계 ────────────────────────────────────────────────────────
    print()
    print(f"  {'':─<60}")
    total_days = len(active_days)
    def_win_days = sum(1 for d in active_days if d["def"]["pnl"] > 0)
    rs_win_days  = sum(1 for d in active_days if d["rs"]["pnl"] > 0)
    total_pnl    = total_def_pnl + total_rs_pnl

    print(f"  기간 합산: DEF {total_def_pnl:+.2f}%  |  RS {total_rs_pnl:+.2f}%  |  합계 {total_pnl:+.2f}%")
    print()

    # 전략별 요약
    all_def = [t for d in days_data for t in d.get("def_trades", [])]
    all_rs  = [t for d in days_data for t in d.get("rs_trades", [])]

    print(f"  ─────────────  전략별 종합  ────────────────────────────")
    _print_strategy_summary("DEFENSIVE", all_def, "🛡️")
    _print_strategy_summary("RS       ", all_rs,  "📈")

    # ── 레짐 매칭 분석 ───────────────────────────────────────────────
    print()
    print(f"  ─────────────  레짐 매칭  ──────────────────────────────")
    def_only_days = [d for d in active_days if d["def"]["n"] > 0 and d["rs"]["n"] == 0]
    rs_only_days  = [d for d in active_days if d["rs"]["n"] > 0 and d["def"]["n"] == 0]
    both_days     = [d for d in active_days if d["def"]["n"] > 0 and d["rs"]["n"] > 0]
    neither_days  = [d for d in active_days if d["def"]["n"] == 0 and d["rs"]["n"] == 0]

    print(f"  DEF 단독: {len(def_only_days)}일  RS 단독: {len(rs_only_days)}일  "
          f"동시: {len(both_days)}일  공백: {len(neither_days)}일")

    if both_days:
        both_pnl = [d["total_pnl"] for d in both_days]
        print(f"  동시 진입일 평균 수익: {sum(both_pnl)/len(both_pnl):+.2f}%")

    # ── 권고사항 ─────────────────────────────────────────────────────
    print()
    print(f"  ─────────────  운용 권고사항  ──────────────────────────")
    recs = _generate_recs(all_def, all_rs, active_days)
    for r in recs:
        print(f"  {r}")

    print(f"\n{sep}\n")


def _print_strategy_summary(name: str, trades: list, icon: str):
    if not trades:
        print(f"  {icon} {name}: 데이터 없음")
        return
    n    = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    avg  = sum(t["pnl"] for t in trades) / n
    avg_hold = sum(t["hold"] for t in trades) / n
    print(f"  {icon} {name}: {n}건  승률={wins/n*100:.0f}%  평균={avg:+.2f}%  홀딩={avg_hold:.1f}분")


def _generate_recs(def_trades, rs_trades, active_days) -> list:
    recs = []

    # DEF 평가
    if def_trades:
        def_wr = sum(1 for t in def_trades if t["pnl"] > 0) / len(def_trades) * 100
        if def_wr >= 60:
            recs.append(f"✅ DEF 승률 {def_wr:.0f}% 양호 → max_per_day 3→4, size 0.3→0.4 검토")
        elif def_wr < 40:
            recs.append(f"⚠️  DEF 승률 {def_wr:.0f}% 부진 → 진입 조건 강화 필요")

    # RS 평가
    if rs_trades:
        rs_wr   = sum(1 for t in rs_trades if t["pnl"] > 0) / len(rs_trades) * 100
        rs_hold = sum(t["hold"] for t in rs_trades) / len(rs_trades)
        if rs_wr >= 55 and rs_hold >= 15:
            recs.append(f"✅ RS 승률 {rs_wr:.0f}% + 홀딩 {rs_hold:.0f}분 → size 0.15→0.25, max_per_day 1→2 확대 고려")
        elif rs_hold < 5:
            recs.append(f"⚠️  RS 평균 홀딩 {rs_hold:.1f}분 → 진입 직후 청산 → VWAP breakout 강화 필요")

    # 공백일 경고
    no_trade_days = sum(1 for d in active_days if d["def"]["n"] == 0 and d["rs"]["n"] == 0)
    if no_trade_days > len(active_days) * 0.5:
        recs.append(f"⚠️  공백일 {no_trade_days}일/{len(active_days)}일 → rs_threshold 완화 또는 EXPLORATION 검토")

    if not recs:
        recs.append("ℹ️  데이터 축적 중 — 주 단위 재평가 권장")

    return recs


def collect_log_paths(args) -> list:
    if args.date:
        return [LOG_DIR / f"auto_trading_{args.date}.log"]
    if args.days:
        today = datetime.today()
        return [LOG_DIR / f"auto_trading_{(today - timedelta(days=i)).strftime('%Y%m%d')}.log"
                for i in range(args.days)]
    return [LOG_DIR / f"auto_trading_{datetime.today().strftime('%Y%m%d')}.log"]


def main():
    parser = argparse.ArgumentParser(description="DEF + RS 통합 성과 리포트")
    parser.add_argument("--date", type=str, help="특정 날짜 (YYYYMMDD)")
    parser.add_argument("--days", type=int, help="최근 N일")
    args = parser.parse_args()

    log_paths = collect_log_paths(args)

    days_data = []
    parsed_dates = []

    for lp in log_paths:
        day = parse_day(lp)
        days_data.append(day)
        if day["def_trades"] or day["rs_trades"]:
            parsed_dates.append(day["date"])

    period = (
        parsed_dates[0] if len(parsed_dates) == 1
        else f"{parsed_dates[-1]}~{parsed_dates[0]}" if parsed_dates
        else "기록 없음"
    )

    print_report(days_data, period)


if __name__ == "__main__":
    main()
