"""
rebuild_trade_timeline.py — t0 / t2 과거 복원

각 BUY 거래에 대해:
  t0 = 당일(또는 전일) 최초 ACCEPT 시점 (후보 확정)
  t2 = 실제 매수 시각 (DB trade_time)

t0 복원 소스 우선순위:
  1. DB candidate_first_time (instrumented cohort, 신뢰도 HIGH)
  2. auto_trading 로그 ✅ ACCEPT 패턴 파싱 (LOG, 신뢰도 HIGH/MEDIUM)
  3. 스윙 09:00 진입 → 전일 15:35 추정 (INFERRED, 신뢰도 LOW)
  4. 없음 → t0 = t2 (UNKNOWN, 신뢰도 NONE)

Usage:
    python -m analysis.rebuild_trade_timeline [--rebuild-log-cache]

Output:
    logs/trade_timeline_rebuilt_YYYYMMDD.csv
"""
import argparse
import os
import re
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"
REPORTS_DIR = BASE / "reports"
DB_ARGS = dict(dbname="trading_system", user="postgres", password=os.getenv("POSTGRES_PASSWORD"), host="localhost")

LOG_ACCEPT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - \w+ - ✅ ACCEPT (\d+) @(\d+)원"
)
CAND_ACCEPT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - \w+ - 🟡 CANDIDATE_ACCEPT (\d+) @(\d+)원"
)

SWING_RUNNER_HOUR = 15
SWING_RUNNER_MIN = 35


def load_accept_index() -> dict:
    """
    auto_trading 로그 전체를 파싱해 ACCEPT 이벤트 색인 반환.
    반환: {date_str: {stock_code: [(datetime, price), ...]}}
    first occurrence per code per day
    """
    cache_path = LOG_DIR / "accept_index_cache.pkl"

    # 캐시 존재 시 사용 (직접 재구성 원하면 --rebuild-log-cache 옵션)
    import pickle
    if cache_path.exists():
        with open(cache_path, "rb") as f:
            return pickle.load(f)

    print("ACCEPT 로그 파싱 중 (최초 1회)...")
    index: dict[str, dict[str, list]] = {}

    log_files = sorted(LOG_DIR.glob("auto_trading_2025*.log")) + \
                sorted(LOG_DIR.glob("auto_trading_2026*.log")) + \
                [LOG_DIR / "signal_orchestrator.log"]

    total = 0
    for lf in log_files:
        if not lf.exists():
            continue
        with open(lf, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = LOG_ACCEPT_RE.match(line) or CAND_ACCEPT_RE.match(line)
                if not m:
                    continue
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                code = m.group(2)
                price = int(m.group(3))
                d = ts.strftime("%Y-%m-%d")
                index.setdefault(d, {}).setdefault(code, []).append((ts, price))
                total += 1

    print(f"  ACCEPT 이벤트 {total:,}건 파싱 완료")

    with open(cache_path, "wb") as f:
        import pickle
        pickle.dump(index, f)

    return index


def get_t0_from_log(
    accept_index: dict,
    stock_code: str,
    buy_dt: datetime,
    is_swing: bool,
) -> tuple:
    """
    returns (t0_time, t0_price, source, confidence)
    is_swing=True → 전일 로그에서 찾음
    """
    if is_swing:
        target_date = (buy_dt.date() - timedelta(days=1)).strftime("%Y-%m-%d")
        day_data = accept_index.get(target_date, {})
        events = day_data.get(stock_code, [])
        if events:
            t0_ts, t0_price = events[0]
            return t0_ts, t0_price, "LOG_PREV", "high"
        # 주말/공휴일 건너뛰기 (최대 3일 전)
        for offset in [2, 3]:
            prev = (buy_dt.date() - timedelta(days=offset)).strftime("%Y-%m-%d")
            events = accept_index.get(prev, {}).get(stock_code, [])
            if events:
                t0_ts, t0_price = events[0]
                return t0_ts, t0_price, "LOG_PREV_WEEKEND", "medium"
        # ACCEPT 없음 → swing_runner 시각으로 추정
        prev_day = buy_dt.date() - timedelta(days=1)
        inferred = datetime(prev_day.year, prev_day.month, prev_day.day,
                            SWING_RUNNER_HOUR, SWING_RUNNER_MIN, 0)
        return inferred, None, "INFERRED_SWING", "low"
    else:
        # 당일 첫 ACCEPT (매수 시각 이전)
        d_str = buy_dt.strftime("%Y-%m-%d")
        day_data = accept_index.get(d_str, {})
        events = [e for e in day_data.get(stock_code, []) if e[0] <= buy_dt]
        if events:
            t0_ts, t0_price = events[0]
            return t0_ts, t0_price, "LOG_SAME", "high"
        return buy_dt, None, "UNKNOWN", "none"


def load_trades() -> list[dict]:
    """완성 거래 페어 로드 (per unique BUY, 최초 SELL 기준 LATERAL JOIN)"""
    conn = psycopg2.connect(**DB_ARGS)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            b.trade_id, b.stock_code, b.stock_name,
            b.trade_time AS buy_time, b.price AS buy_price,
            b.candidate_first_time, b.candidate_first_price,
            b.entry_signal_time, b.entry_signal_price,
            s.sell_time, s.sell_price, s.profit_rate, s.exit_reason,
            s.mfe_pct, s.mae_pct
        FROM (
            SELECT DISTINCT ON (stock_code, COALESCE(trade_time, entry_time))
                trade_id, stock_code, stock_name,
                COALESCE(trade_time, entry_time) AS trade_time,
                price, candidate_first_time, candidate_first_price,
                entry_signal_time, entry_signal_price
            FROM trades
            WHERE trade_type = 'BUY'
              AND stock_code NOT LIKE 'TEST%%'
              AND price > 500
              AND COALESCE(trade_time, entry_time) IS NOT NULL
            ORDER BY stock_code, COALESCE(trade_time, entry_time)
        ) b
        JOIN LATERAL (
            SELECT
                COALESCE(s2.trade_time, s2.entry_time) AS sell_time,
                s2.price AS sell_price, s2.profit_rate, s2.exit_reason,
                s2.mfe_pct, s2.mae_pct
            FROM trades s2
            WHERE s2.trade_type = 'SELL'
              AND s2.stock_code = b.stock_code
              AND s2.profit_rate IS NOT NULL
              AND COALESCE(s2.trade_time, s2.entry_time) >= b.trade_time
            ORDER BY COALESCE(s2.trade_time, s2.entry_time)
            LIMIT 1
        ) s ON true
        ORDER BY b.trade_time
    """)

    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def classify_result(profit_rate: float, exit_reason: str) -> str:
    if profit_rate is None:
        return "UNKNOWN"
    er = (exit_reason or "").lower()
    if "early failure" in er or "ef" in er:
        return "EF"
    if profit_rate > 0:
        return "WIN"
    return "LOSS"


def rebuild(rebuild_cache: bool = False):
    if rebuild_cache:
        cache_path = LOG_DIR / "accept_index_cache.pkl"
        if cache_path.exists():
            cache_path.unlink()

    accept_index = load_accept_index()
    trades = load_trades()
    print(f"완성 거래 페어: {len(trades)}건")

    records = []
    for t in trades:
        buy_time: datetime = t["buy_time"]
        if buy_time is None:
            continue

        is_swing = (buy_time.hour == 9 and buy_time.minute == 0)

        # t0 결정
        if t["candidate_first_time"] is not None:
            # instrumented cohort (DB에서 직접)
            t0_time = t["candidate_first_time"]
            if hasattr(t0_time, "tzinfo") and t0_time.tzinfo:
                t0_time = t0_time.replace(tzinfo=None)
            t0_price = t["candidate_first_price"]
            t0_source = "DB"
            t0_conf = "high"
        else:
            t0_time, t0_price, t0_source, t0_conf = get_t0_from_log(
                accept_index, t["stock_code"], buy_time, is_swing
            )

        # t0→t2 gap
        if t0_time and buy_time and t0_conf != "none":
            gap_min = (buy_time - t0_time).total_seconds() / 60.0
        else:
            gap_min = None

        # t0→t2 price change
        if t0_price and t["buy_price"]:
            gap_pct = (t["buy_price"] - t0_price) / t0_price * 100
        else:
            gap_pct = None

        result = classify_result(t["profit_rate"], t["exit_reason"])

        records.append({
            "trade_id": t["trade_id"],
            "stock_code": t["stock_code"],
            "stock_name": t["stock_name"],
            "result": result,
            "is_swing": is_swing,
            "t0_time": t0_time,
            "t0_price": t0_price,
            "t0_source": t0_source,
            "t0_confidence": t0_conf,
            "t2_time": buy_time,
            "t2_price": t["buy_price"],
            "t0_to_t2_min": round(gap_min, 1) if gap_min is not None else None,
            "t0_to_t2_pct": round(gap_pct, 2) if gap_pct is not None else None,
            "sell_time": t["sell_time"],
            "sell_price": t["sell_price"],
            "pnl_pct": t["profit_rate"],
            "exit_reason": t["exit_reason"],
            "mfe_pct": t["mfe_pct"],
            "mae_pct": t["mae_pct"],
        })

    df = pd.DataFrame(records)
    out_path = LOG_DIR / f"trade_timeline_rebuilt_{date.today().strftime('%Y%m%d')}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n✅ 저장: {out_path} ({len(df)}건)")

    # 요약 출력
    print("\n=== t0 소스 분포 ===")
    print(df["t0_source"].value_counts().to_string())

    print("\n=== t0 신뢰도 분포 ===")
    print(df["t0_confidence"].value_counts().to_string())

    print("\n=== 결과 분포 ===")
    print(df["result"].value_counts().to_string())

    reliable = df[df["t0_confidence"].isin(["high", "medium"])]
    print(f"\n신뢰도 HIGH/MEDIUM: {len(reliable)}건")
    if len(reliable) > 0 and "t0_to_t2_min" in reliable.columns:
        by_result = reliable.groupby("result")["t0_to_t2_min"].agg(["mean", "median", "count"])
        print("결과별 t0→t2 평균 지연 (분):")
        print(by_result.to_string())

    return df


def main():
    parser = argparse.ArgumentParser(description="t0/t2 타임라인 복원")
    parser.add_argument("--rebuild-log-cache", action="store_true",
                        help="로그 파싱 캐시 재구성")
    args = parser.parse_args()
    rebuild(rebuild_cache=args.rebuild_log_cache)


if __name__ == "__main__":
    main()
