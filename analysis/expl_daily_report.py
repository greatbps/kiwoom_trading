"""
EXPLORATION 1봉 확인 진입 일일 리포트
장마감 후 자동 실행: 각 진입 타입별 EF 발생률 / 손익 비교
"""
import re
import sys
from datetime import date
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"


def parse_log(log_path: Path):
    snaps, ef_events, pnl_events = {}, {}, {}
    reject_count = 0

    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            # 진입 스냅샷
            m = re.search(r'\[EXPL_SNAP\] (\S+) type=(\S+) rvol=(\S+) price_vs_bp=(\S+)% vwap_dist=(\S+)% vol=(\S+)', line)
            if m:
                code, typ, rvol, pvb, vwd, vol = m.groups()
                snaps[code] = {"type": typ, "rvol": float(rvol), "price_vs_bp": float(pvb),
                               "vwap_dist": float(vwd), "vol": vol, "ef": None, "pnl": None}

            # 즉시 진입 (RVOL >= 2.8, 스냅샷 없음)
            m2 = re.search(r'\[EXPLORATION_ENTRY\] (\S+) .+\[즉시진입\]', line)
            if m2 and m2.group(1) not in snaps:
                snaps[m2.group(1)] = {"type": "IMMEDIATE", "rvol": 0, "price_vs_bp": 0,
                                      "vwap_dist": 0, "vol": "?", "ef": None, "pnl": None}

            # REJECT 카운트
            if "[EXPL_PEND_REJECT]" in line or "[EXPL_PEND_EXPIRED]" in line:
                m3 = re.search(r'\[EXPL_PEND_(?:REJECT|EXPIRED)\] (\S+)', line)
                if m3:
                    reject_count += 1

            # Early Failure 발생
            m4 = re.search(r'Early Failure.+stock_code=(\S+)|(\S+) .+Early Failure', line)
            ef_m = re.search(r'\[(\d+:\d+)\].+Early Failure.+(\S+)\((\S+)\)', line)
            # 종목코드 기반 EF 매칭 (reason에 종목코드 없으므로 종목명으로)
            if "Early Failure" in line:
                for code in snaps:
                    # 해당 종목이 EF로 청산됐는지 — 종목코드로 매칭
                    pass  # 아래에서 PnL과 함께 처리

            # 매도완료 or 오버나이트 청산 — realized_pnl 추출
            # 로그 패턴: "✅ 매도 완료" 또는 execute_sell 로그
            # trades.db 기반으로 처리 (더 정확)

    return snaps, reject_count


def parse_trades_db(today_str: str):
    """trades.db에서 오늘 EXPLORATION 진입/청산 조회"""
    import sqlite3
    db_path = BASE / "data" / "trades.db"
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT stock_code, stock_name, trade_type, price, realized_pnl, reason, timestamp
        FROM trades
        WHERE trade_date = ?
        ORDER BY timestamp
    """, (today_str,)).fetchall()
    conn.close()

    buys, result = {}, {}
    for code, name, ttype, price, pnl, reason, ts in rows:
        if not reason:
            continue
        if ttype == "BUY" and "EXPLORATION" in reason:
            buys[code] = {"name": name, "price": price, "reason": reason, "ts": ts}
        elif ttype == "SELL" and code in buys:
            entry_type = "IMMEDIATE"
            if "[강확인]" in buys[code]["reason"]:
                entry_type = "CONFIRM"
            elif "[약확인]" in buys[code]["reason"]:
                entry_type = "SOFT"
            elif "[조기진입]" in buys[code]["reason"]:
                entry_type = "EARLY"
            elif "[1봉확인]" in buys[code]["reason"]:
                entry_type = "CONFIRM"

            is_ef = "Early Failure" in (reason or "")
            is_hardstop = "Hard Stop" in (reason or "")
            result[code] = {
                "name":       name,
                "entry_type": entry_type,
                "buy_price":  buys[code]["price"],
                "sell_price": price,
                "pnl":        float(pnl or 0),
                "exit_reason": reason,
                "is_ef":      is_ef,
                "is_hardstop": is_hardstop,
            }
    return result


def build_report(today_str: str, log_path: Path) -> str:
    snaps, reject_count = parse_log(log_path)
    trades = parse_trades_db(today_str)

    lines = [
        f"=" * 60,
        f"EXPLORATION 1봉 확인 진입 리포트 — {today_str}",
        f"=" * 60,
    ]

    # 진입 타입별 집계
    by_type = defaultdict(lambda: {"count": 0, "ef": 0, "hardstop": 0, "pnl": 0.0, "wins": 0})
    for code, t in trades.items():
        tp = t["entry_type"]
        by_type[tp]["count"] += 1
        if t["is_ef"]:
            by_type[tp]["ef"] += 1
        if t["is_hardstop"]:
            by_type[tp]["hardstop"] += 1
        by_type[tp]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            by_type[tp]["wins"] += 1

    lines.append(f"\n[1] 진입 타입별 성과")
    lines.append(f"{'타입':<12} {'진입':>4} {'EF':>4} {'HS':>4} {'승':>4} {'승률':>6} {'손익':>8}")
    lines.append("-" * 50)
    order = ["IMMEDIATE", "EARLY", "CONFIRM", "SOFT"]
    for tp in order:
        d = by_type.get(tp)
        if not d or d["count"] == 0:
            continue
        wr = d["wins"] / d["count"] * 100
        lines.append(
            f"{tp:<12} {d['count']:>4} {d['ef']:>4} {d['hardstop']:>4} "
            f"{d['wins']:>4} {wr:>5.0f}% {d['pnl']:>+8,.0f}원"
        )

    total = sum(d["count"] for d in by_type.values())
    total_pnl = sum(d["pnl"] for d in by_type.values())
    total_ef = sum(d["ef"] for d in by_type.values())
    lines.append("-" * 50)
    lines.append(f"{'합계':<12} {total:>4} {total_ef:>4}{'':>16} {total_pnl:>+8,.0f}원")

    lines.append(f"\n[2] REJECT / EXPIRED 건수: {reject_count}건")
    if reject_count > total * 1.5 and total > 0:
        lines.append("  ⚠️  REJECT 비율 높음 → 조건 완화 검토 (price_tolerance_soft 확대?)")

    lines.append(f"\n[3] 개별 거래 상세")
    for code, t in trades.items():
        pnl_str = f"+{t['pnl']:,.0f}" if t['pnl'] >= 0 else f"{t['pnl']:,.0f}"
        tag = " [EF]" if t["is_ef"] else " [HS]" if t["is_hardstop"] else ""
        lines.append(
            f"  {t['name']}({code}) [{t['entry_type']}] "
            f"{t['buy_price']:,}→{t['sell_price']:,} {pnl_str}원{tag}"
        )

    lines.append(f"\n[4] 스냅샷 상세 (진입 당시 지표)")
    if snaps:
        lines.append(f"  {'종목':<8} {'타입':<8} {'RVOL':>5} {'vs_bp':>7} {'VWAP':>7} {'VOL':>5}")
        for code, s in snaps.items():
            lines.append(
                f"  {code:<8} {s['type']:<8} {s['rvol']:>5.1f} "
                f"{s['price_vs_bp']:>+6.1f}% {s['vwap_dist']:>+6.1f}% {s['vol']:>5}"
            )
    else:
        lines.append("  (스냅샷 없음 — 즉시 진입만 발생했거나 아직 데이터 없음)")

    lines.append("")
    return "\n".join(lines)


def main():
    today = date.today()
    # 주말이면 가장 최근 거래일로
    if today.weekday() >= 5:
        from datetime import timedelta
        today = today - timedelta(days=today.weekday() - 4)

    today_str = today.strftime("%Y-%m-%d")
    log_date  = today.strftime("%Y%m%d")
    log_path  = LOG_DIR / f"auto_trading_{log_date}.log"

    if not log_path.exists():
        print(f"로그 없음: {log_path}")
        sys.exit(0)

    report = build_report(today_str, log_path)

    out_path = LOG_DIR / f"expl_report_{log_date}.txt"
    out_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n저장: {out_path}")


if __name__ == "__main__":
    main()
