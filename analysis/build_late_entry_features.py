"""
build_late_entry_features.py — H-002/H-003/H-005 피처 계산

rebuild_trade_timeline.py 결과 CSV를 읽어 각 거래별 피처를 생성한다.

피처 목록:
  prior_5d_pct_t0  : t0 기준 이전 5일 상승률 (yfinance 일봉)
  prior_10d_pct_t0 : t0 기준 이전 10일 상승률
  prior_5d_pct_t2  : t2(매수) 기준 이전 5일 상승률
  prior_10d_pct_t2 : t2(매수) 기준 이전 10일 상승률
  time_bucket      : A(09:00~09:30) / B(09:30~10:30) / C(10:30~11:30) / D(11:30+)
  intraday_pos_pct : (buy_price - day_low) / (day_high - day_low) * 100
  t0_to_t2_min     : t0→t2 지연 (분, rebuild에서 복사)
  t0_to_t2_pct     : t0→t2 가격 변화 (%)

Usage:
    python -m analysis.build_late_entry_features [--timeline-file PATH]

Output:
    logs/late_entry_features_YYYYMMDD.csv
"""
import argparse
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"


def get_latest_timeline() -> Path:
    files = sorted(LOG_DIR.glob("trade_timeline_rebuilt_*.csv"))
    if not files:
        raise FileNotFoundError("trade_timeline_rebuilt_*.csv 없음. rebuild_trade_timeline.py 먼저 실행")
    return files[-1]


def code_to_yf(stock_code: str) -> str:
    return f"{stock_code}.KS"


def fetch_daily_data(ticker: str, ref_date: date, lookback: int = 25) -> pd.DataFrame:
    """
    ref_date 포함 이전 lookback 거래일 일봉 반환.
    """
    start = ref_date - timedelta(days=lookback * 2 + 10)
    end = ref_date + timedelta(days=1)
    try:
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        return df
    except Exception:
        return pd.DataFrame()


def prior_n_day_return(df: pd.DataFrame, ref_date: date, n: int) -> float | None:
    """
    ref_date 직전 거래일 종가 기준 n 거래일 전 → ref_date 전일 종가 상승률.
    """
    if df.empty:
        return None
    close = df["Close"].dropna()
    if isinstance(close.index, pd.DatetimeIndex):
        close.index = close.index.date

    # ref_date 이전 거래일만
    hist = close[close.index < ref_date]
    if len(hist) < n + 1:
        return None

    price_now = float(hist.iloc[-1])
    price_n = float(hist.iloc[-n - 1])
    if price_n == 0:
        return None
    return round((price_now - price_n) / price_n * 100, 2)


def intraday_stats(df: pd.DataFrame, ref_date: date, buy_price: float) -> tuple:
    """
    매수일 당일 고저 통계.
    Returns: (intraday_pos_pct, below_day_high_pct, day_high)
      intraday_pos_pct  = (buy - low) / (high - low) * 100  (0~100%)
      below_day_high_pct = (high - buy) / high * 100        (조건2 기준, 작을수록 고점 근처)
      day_high          = 당일 고가 (원)
    """
    if df.empty:
        return None, None, None
    if hasattr(df.index, "date"):
        d_row = df[df.index.date == ref_date]
    else:
        d_row = df[df.index == str(ref_date)]
    if d_row.empty:
        return None, None, None
    high = float(d_row["High"].iloc[0])
    low  = float(d_row["Low"].iloc[0])
    if high == 0:
        return None, None, None
    intraday_pos = round((buy_price - low) / (high - low) * 100, 1) if high != low else 50.0
    below_high   = round((high - buy_price) / high * 100, 2)
    return intraday_pos, below_high, round(high, 0)


def intraday_position(df: pd.DataFrame, ref_date: date, buy_price: float) -> float | None:
    """하위 호환성 유지 wrapper."""
    pos, _, _ = intraday_stats(df, ref_date, buy_price)
    return pos


def time_bucket(dt: datetime) -> str:
    if dt is None:
        return "UNKNOWN"
    h, m = dt.hour, dt.minute
    mins = h * 60 + m
    if mins < 9 * 60 + 30:
        return "A_OPEN"       # 09:00~09:30
    elif mins < 10 * 60 + 30:
        return "B_MID"        # 09:30~10:30
    elif mins < 11 * 60 + 30:
        return "C_LATE"       # 10:30~11:30
    else:
        return "D_AFTERNOON"  # 11:30+


def build_features(timeline_file: Path = None) -> pd.DataFrame:
    if timeline_file is None:
        timeline_file = get_latest_timeline()

    print(f"타임라인 로드: {timeline_file}")
    df = pd.read_csv(timeline_file, parse_dates=["t0_time", "t2_time", "sell_time"])
    df = df.dropna(subset=["t2_time", "t2_price"])
    df = df[df["t2_price"] > 500]
    print(f"처리 대상: {len(df)}건")

    # yfinance 디스크 캐시
    import pickle
    _yf_cache_dir = LOG_DIR / "yf_cache"
    _yf_cache_dir.mkdir(exist_ok=True)

    def _get_daily(ticker: str, ref_date: date) -> pd.DataFrame:
        cache_path = _yf_cache_dir / f"{ticker}_{ref_date}.pkl"
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                return pickle.load(f)
        df_fetched = fetch_daily_data(ticker, ref_date, lookback=25)
        with open(cache_path, "wb") as f:
            pickle.dump(df_fetched, f)
        time.sleep(0.15)
        return df_fetched

    records = []
    for idx, row in df.iterrows():
        code = str(row["stock_code"]).zfill(6)
        ticker = code_to_yf(code)
        t2_dt = row["t2_time"]
        if pd.isna(t2_dt):
            continue
        t2_date = t2_dt.date() if hasattr(t2_dt, "date") else t2_dt

        # t0 날짜
        t0_dt = row["t0_time"]
        if pd.isna(t0_dt) or row["t0_confidence"] == "none":
            t0_date = t2_date
        else:
            t0_date = t0_dt.date() if hasattr(t0_dt, "date") else t0_dt

        daily = _get_daily(ticker, t2_date)
        buy_price = float(row["t2_price"])

        intraday_pos, below_high_pct, day_high = intraday_stats(daily, t2_date, buy_price)

        rec = {
            "trade_id": row["trade_id"],
            "stock_code": code,
            "stock_name": row["stock_name"],
            "result": row["result"],
            "is_swing": row.get("is_swing", False),
            "t0_time": row["t0_time"],
            "t0_price": row.get("t0_price"),
            "t0_source": row["t0_source"],
            "t0_confidence": row["t0_confidence"],
            "t2_time": t2_dt,
            "t2_price": buy_price,
            "pnl_pct": row["pnl_pct"],
            "exit_reason": row["exit_reason"],
            "mfe_pct": row.get("mfe_pct"),
            "mae_pct": row.get("mae_pct"),
            "t0_to_t2_min": row.get("t0_to_t2_min"),
            "t0_to_t2_pct": row.get("t0_to_t2_pct"),
            "time_bucket": time_bucket(t2_dt if not pd.isna(t2_dt) else None),
            "prior_5d_pct_t0": prior_n_day_return(daily, t0_date, 5),
            "prior_10d_pct_t0": prior_n_day_return(daily, t0_date, 10),
            "prior_5d_pct_t2": prior_n_day_return(daily, t2_date, 5),
            "prior_10d_pct_t2": prior_n_day_return(daily, t2_date, 10),
            "intraday_pos_pct": intraday_pos,
            "below_day_high_pct": below_high_pct,   # (day_high - buy) / day_high * 100
            "day_high": day_high,
        }
        records.append(rec)

        if (idx + 1) % 10 == 0:
            print(f"  {idx + 1}/{len(df)} 처리 중...")

    feat_df = pd.DataFrame(records)
    out_path = LOG_DIR / f"late_entry_features_{date.today().strftime('%Y%m%d')}.csv"
    feat_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n✅ 저장: {out_path} ({len(feat_df)}건)")

    # 간단 요약
    print("\n=== H-002 예비 요약 (t2 기준 5일 상승률) ===")
    grp = feat_df.dropna(subset=["prior_5d_pct_t2"]).groupby("result")["prior_5d_pct_t2"]
    print(grp.agg(["mean", "median", "count"]).round(2).to_string())

    print("\n=== H-005 시간대별 결과 ===")
    bucket_grp = feat_df.groupby(["time_bucket", "result"]).size().unstack(fill_value=0)
    print(bucket_grp.to_string())

    return feat_df


def main():
    parser = argparse.ArgumentParser(description="진입 지연 피처 생성")
    parser.add_argument("--timeline-file", type=Path, default=None,
                        help="trade_timeline_rebuilt_*.csv 경로 (기본: 최신)")
    args = parser.parse_args()
    build_features(args.timeline_file)


if __name__ == "__main__":
    main()
