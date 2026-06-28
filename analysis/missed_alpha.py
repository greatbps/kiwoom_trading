"""
Missed Alpha Tracker — 장 마감 후 실행.

signal_rejections / buy_failures 테이블의 future_return 컬럼을 채우고
Missed Alpha 요약을 출력한다.

Usage:
    python -m analysis.missed_alpha [--date YYYYMMDD] [--days 7]
"""
import argparse
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
import yfinance as yf
import pandas as pd


DB_DSN = "dbname=trading_system user=postgres"

MISSED_ALPHA_THRESHOLD = 3.0  # % — 거절 후 이만큼 오르면 "Missed Alpha"


def _get_conn():
    return psycopg2.connect(DB_DSN)


def _fetch_future_return(stock_code: str, rejected_at: datetime, hours: float) -> float | None:
    """rejected_at 기준 hours 시간 후의 수익률(%)을 yfinance로 조회."""
    try:
        end = rejected_at + timedelta(hours=hours + 0.5)
        # 충분한 여유를 두고 조회
        ticker = f"{stock_code}.KS"
        df = yf.download(ticker, start=rejected_at.date(), end=(end.date() + timedelta(days=1)),
                         interval="5m", progress=False, auto_adjust=True)
        if df.empty:
            ticker = f"{stock_code}.KQ"
            df = yf.download(ticker, start=rejected_at.date(), end=(end.date() + timedelta(days=1)),
                             interval="5m", progress=False, auto_adjust=True)
        if df.empty:
            return None

        df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index

        # 거절 시점 직후 가격
        entry_rows = df[df.index >= rejected_at]
        if entry_rows.empty:
            return None
        entry_price = float(entry_rows['Close'].iloc[0])
        if entry_price <= 0:
            return None

        # 목표 시점 가격
        target_dt = rejected_at + timedelta(hours=hours)
        future_rows = df[df.index >= target_dt]
        if future_rows.empty:
            # 마지막 가격으로 대체 (장 마감 후 실행 시)
            future_rows = df[df.index <= target_dt]
            if future_rows.empty:
                return None
        future_price = float(future_rows['Close'].iloc[0])

        return round((future_price - entry_price) / entry_price * 100, 3)
    except Exception:
        return None


def _backfill_table(table: str, date_from: date, date_to: date):
    """signal_rejections 또는 buy_failures의 future_return 컬럼을 채운다."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        time_col = "rejected_at" if table == "signal_rejections" else "attempted_at"
        fr1_col  = "future_return_1h"
        fr_eod_col = "future_return_3h" if table == "signal_rejections" else "future_return_eod"

        cur.execute(f"""
            SELECT id, stock_code, {time_col}
            FROM {table}
            WHERE {time_col}::date BETWEEN %s AND %s
              AND ({fr1_col} IS NULL OR {fr_eod_col} IS NULL)
            ORDER BY {time_col}
        """, (date_from, date_to))
        rows = cur.fetchall()

        print(f"[{table}] {len(rows)}건 backfill 대상")
        updated = 0

        for row_id, code, ts in rows:
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)

            r1h = _fetch_future_return(code, ts, hours=1)
            r_out = _fetch_future_return(code, ts, hours=3 if table == "signal_rejections" else 6)

            if r1h is None and r_out is None:
                continue

            cur.execute(f"""
                UPDATE {table}
                SET {fr1_col} = %s, {fr_eod_col} = %s
                WHERE id = %s
            """, (r1h, r_out, row_id))
            updated += 1

        conn.commit()
        print(f"[{table}] {updated}건 업데이트 완료")
    finally:
        cur.close()
        conn.close()


def _report(date_from: date, date_to: date):
    """Missed Alpha 요약 출력."""
    conn = _get_conn()
    try:
        cur = conn.cursor()

        print(f"\n{'='*60}")
        print(f"  Missed Alpha Report  {date_from} ~ {date_to}")
        print(f"{'='*60}")

        # signal_rejections
        cur.execute("""
            SELECT
                rejection_stage,
                COUNT(*) AS n,
                ROUND(AVG(future_return_1h)::numeric, 2) AS avg_r1h,
                ROUND(AVG(future_return_3h)::numeric, 2) AS avg_r3h,
                COUNT(CASE WHEN future_return_3h >= %s THEN 1 END) AS missed_alpha_n
            FROM signal_rejections
            WHERE rejected_at::date BETWEEN %s AND %s
              AND future_return_3h IS NOT NULL
            GROUP BY 1
            ORDER BY avg_r3h DESC NULLS LAST
        """, (MISSED_ALPHA_THRESHOLD, date_from, date_to))
        rows = cur.fetchall()

        if rows:
            print(f"\n[신호 거절 — signal_rejections]  (Missed Alpha 기준: +{MISSED_ALPHA_THRESHOLD}%)")
            print(f"{'단계':<20} {'n':>5} {'평균1h':>8} {'평균3h':>8} {'미스드알파':>10}")
            print("-" * 56)
            for stage, n, avg1h, avg3h, miss_n in rows:
                flag = " ◀ 알파 존재" if avg3h and float(avg3h) >= MISSED_ALPHA_THRESHOLD else ""
                print(f"{stage:<20} {n:>5} {str(avg1h or '-'):>8} {str(avg3h or '-'):>8} {miss_n:>10}{flag}")

        # buy_failures
        cur.execute("""
            SELECT
                fail_stage,
                COUNT(*) AS n,
                ROUND(AVG(future_return_1h)::numeric, 2) AS avg_r1h,
                ROUND(AVG(future_return_eod)::numeric, 2) AS avg_reod,
                COUNT(CASE WHEN future_return_eod >= %s THEN 1 END) AS missed_alpha_n
            FROM buy_failures
            WHERE attempted_at::date BETWEEN %s AND %s
              AND future_return_eod IS NOT NULL
            GROUP BY 1
            ORDER BY avg_reod DESC NULLS LAST
        """, (MISSED_ALPHA_THRESHOLD, date_from, date_to))
        rows = cur.fetchall()

        if rows:
            print(f"\n[실행 거절 — buy_failures]  (Missed Alpha 기준: +{MISSED_ALPHA_THRESHOLD}%)")
            print(f"{'단계':<20} {'n':>5} {'평균1h':>8} {'평균EOD':>8} {'미스드알파':>10}")
            print("-" * 56)
            for stage, n, avg1h, avg_eod, miss_n in rows:
                flag = " ◀ 알파 존재" if avg_eod and float(avg_eod) >= MISSED_ALPHA_THRESHOLD else ""
                print(f"{stage:<20} {n:>5} {str(avg1h or '-'):>8} {str(avg_eod or '-'):>8} {miss_n:>10}{flag}")

        # 개별 대형 미스 (3h > 5%)
        cur.execute("""
            SELECT stock_code, stock_name, rejection_stage, rejection_reason,
                   choch_grade, market_regime, rvol, future_return_3h,
                   rejected_at::time AS time
            FROM signal_rejections
            WHERE rejected_at::date BETWEEN %s AND %s
              AND future_return_3h >= 5.0
            ORDER BY future_return_3h DESC
            LIMIT 10
        """, (date_from, date_to))
        rows = cur.fetchall()
        if rows:
            print(f"\n[TOP Missed Alpha — 3h >+5%]")
            print(f"{'코드':<8} {'종목':12} {'단계':16} {'등급':4} {'RVOL':6} {'3h수익':7} 거절이유")
            print("-" * 80)
            for code, name, stage, reason, grade, regime, rvol, r3h, t in rows:
                print(f"{code:<8} {(name or '')[:12]:12} {stage:16} {grade or '-':4} "
                      f"{rvol or 0:6.2f} {r3h:+7.2f}% {(reason or '')[:35]}")

        print(f"\n{'='*60}\n")
    finally:
        cur.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    date_to = datetime.strptime(args.date, "%Y%m%d").date()
    date_from = date_to - timedelta(days=args.days - 1)

    if not args.report_only:
        print(f"future_return backfill: {date_from} ~ {date_to}")
        _backfill_table("signal_rejections", date_from, date_to)
        _backfill_table("buy_failures", date_from, date_to)

    _report(date_from, date_to)


if __name__ == "__main__":
    main()
