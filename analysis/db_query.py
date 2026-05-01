"""
analysis/db_query.py — 거래 DB 조회 유틸리티 (PostgreSQL: trading_system.trades)

사용법:
  python3 analysis/db_query.py --today          # 오늘 거래
  python3 analysis/db_query.py --days 7         # 최근 7일
  python3 analysis/db_query.py --days 30        # 최근 30일
  python3 analysis/db_query.py --summary        # 최근 90일
"""

import json
import argparse
import os
from datetime import datetime, timedelta, date
from pathlib import Path

PROJECT = Path(__file__).parent.parent
RISK_LOG = PROJECT / "data" / "risk_log.json"

# PostgreSQL 연결 설정
PG_DSN = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "trading_system"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}


def _get_conn():
    import psycopg2
    return psycopg2.connect(**PG_DSN)


def load_today_from_risk_log():
    """오늘 거래는 risk_log.json이 가장 실시간 (미체결 포지션 포함)"""
    if not RISK_LOG.exists():
        return [], 0, ""
    data = json.loads(RISK_LOG.read_text())
    return data.get("daily_trades", []), data.get("consecutive_losses", 0), data.get("today", "")


def today_summary():
    """오늘 요약 — risk_log.json (실시간) 우선, PG로 교차검증"""
    trades, loss_streak, today = load_today_from_risk_log()

    print(f"\n=== 오늘 거래 ({today}) ===")
    if not trades:
        print("  거래 없음")
    else:
        total_pnl = 0.0
        for t in trades:
            pnl = t.get("realized_pnl", 0)
            total_pnl += pnl
            print(f"  {t['timestamp'][11:16]} | {t['type']:4s} | {t['stock_name']}({t['stock_code']}) "
                  f"@{t['price']:,.0f} | PnL={pnl:+,.0f}원 | {t.get('reason','')[:50]}")
        print(f"\n  연패: {loss_streak}회 | 오늘 손익: {total_pnl:+,.0f}원")

    # PG 교차검증
    try:
        today_str = today or date.today().isoformat()
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*), COALESCE(SUM(realized_profit), 0)
            FROM trades WHERE DATE(trade_time) = %s
        """, (today_str,))
        pg_count, pg_pnl = cur.fetchone()
        conn.close()
        print(f"  [PG 확인] {pg_count}건, 누적 손익: {pg_pnl:+,.0f}원")
    except Exception as e:
        print(f"  [PG 확인 실패] {e}")


def query_trades(days: int = None, date_str: str = None):
    """PostgreSQL trading_system.trades 에서 N일 거래 조회"""
    if date_str:
        cutoff = datetime.strptime(date_str[:8], "%Y%m%d").date()
        end_date = cutoff
        label = str(cutoff)
        where = "DATE(trade_time) = %s"
        params = (cutoff,)
    else:
        days = days or 90
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        label = f"최근 {days}일"
        where = "trade_time >= %s"
        params = (cutoff,)

    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT trade_time, stock_code, stock_name, trade_type,
                   price, quantity, amount,
                   COALESCE(realized_profit, 0) AS realized_profit,
                   entry_reason, exit_reason
            FROM trades
            WHERE {where}
            ORDER BY trade_time
        """, params)
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"\nPostgreSQL 연결 실패: {e}")
        return

    if not rows:
        print(f"\n해당 기간 거래 없음 ({label})")
        return

    print(f"\n=== 거래 내역 ({len(rows)}건, {label}) ===")
    buy_count = sell_count = wins = losses = 0
    total_pnl = 0.0

    for trade_time, code, name, typ, price, qty, amount, pnl, entry_r, exit_r in rows:
        ts = trade_time.strftime("%m-%d %H:%M")
        reason = (entry_r or exit_r or "")[:45]
        print(f"  {ts} | {typ:4s} | {name}({code}) "
              f"@{price:,.0f} | PnL={pnl:+,.0f}원 | {reason}")
        if typ == "BUY":
            buy_count += 1
        elif typ == "SELL":
            sell_count += 1
            total_pnl += float(pnl)
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1

    print(f"\n=== 요약 ===")
    print(f"  매수: {buy_count}건 | 매도: {sell_count}건")
    if sell_count > 0:
        print(f"  승: {wins}건 / 패: {losses}건 / 승률: {wins/sell_count*100:.1f}%")
    print(f"  누적 실현손익: {total_pnl:+,.0f}원")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--days", type=int)
    parser.add_argument("--date", type=str)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.today or (not any([args.days, args.date, args.summary])):
        today_summary()
    if args.days:
        query_trades(days=args.days)
    if args.date:
        query_trades(date_str=args.date)
    if args.summary:
        query_trades(days=90)
