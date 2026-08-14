"""
analysis/condition_signal_outcome_collector.py — Signal Outcome Collector (WI-13)

research.strategy_signals에 기록된 Signal에 대해 +1D/+2D/+3D/+5D(거래일 기준)
종가를 채워 research.signal_outcomes에 저장한다.

Live 트레이딩 프로세스에는 연결하지 않는다 — 별도 오프라인/크론 스크립트.
(크론 등록은 이번 WI-13 구현 범위에 포함하지 않음 — 별도 확인 후 진행)

⚠️ analysis/returns_collector.py의 +D1/+D3/+D5/+D10 horizon은 정의만 있고
   실제 다음 거래일 이후 가격 수집 로직은 "별도 처리 생략"으로 미구현
   상태다(해당 파일 `collect_for_date()` 주석 확인). 그래서 이 스크립트는
   그 부분을 그대로 재사용하지 않고, yfinance 일봉(daily)을 이용해 직접
   "N번째 거래일 종가"를 계산한다 — 일봉은 원래 거래일만 포함되므로 주말/
   휴장일을 별도로 계산할 필요가 없다.

실행:
    python3 -m analysis.condition_signal_outcome_collector
    python3 -m analysis.condition_signal_outcome_collector --dry-run
    python3 -m analysis.condition_signal_outcome_collector --days 14
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

logger = logging.getLogger(__name__)

HORIZONS = ['+1D', '+2D', '+3D', '+5D']
HORIZON_N = {'+1D': 1, '+2D': 2, '+3D': 3, '+5D': 5}


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _query_pending_signals(conn, lookback_days: int) -> List[Dict]:
    """outcome이 4개 horizon 전부 채워지지 않은 최근 Signal 조회."""
    since = datetime.now() - timedelta(days=lookback_days)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.signal_id, s.stock_code, s.observed_at,
                   s.entry_price_reference, s.condition_seq
            FROM research.strategy_signals s
            WHERE s.observed_at >= %s
              AND s.entry_price_reference IS NOT NULL
              AND s.entry_price_reference > 0
              AND (
                  SELECT COUNT(*) FROM research.signal_outcomes o
                  WHERE o.signal_id = s.signal_id
              ) < %s
            ORDER BY s.observed_at
        """, (since, len(HORIZONS)))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _query_collected_horizons(conn, signal_id: str) -> set:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT horizon_label FROM research.signal_outcomes
            WHERE signal_id = %s::uuid
        """, (signal_id,))
        return {r[0] for r in cur.fetchall()}


def _fetch_daily_prices(stock_code: str, from_date: datetime) -> Optional[object]:
    """
    yfinance 일봉 조회. 신호일부터 +20 캘린더일(거래일 5일 여유 확보).
    KOSPI(.KS)/KOSDAQ(.KQ) 순서로 시도.
    """
    try:
        import yfinance as yf
        end = from_date + timedelta(days=20)
        for suffix in ('.KS', '.KQ'):
            df = yf.download(
                f"{stock_code}{suffix}",
                start=from_date.strftime('%Y-%m-%d'),
                end=end.strftime('%Y-%m-%d'),
                interval='1d', progress=False, auto_adjust=True,
            )
            if df is not None and len(df) > 0:
                if hasattr(df.columns, 'levels'):
                    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                                  for c in df.columns]
                else:
                    df.columns = df.columns.str.lower()
                return df
        return None
    except Exception as e:
        logger.warning(f"[SIGNAL_OUTCOME] price fetch failed {stock_code}: {e}")
        return None


def _price_n_trading_days_after(df, signal_date: datetime, n: int) -> Optional[float]:
    """
    일봉 DataFrame에서 signal_date '이후' n번째 거래일 종가.
    df 인덱스는 거래일만 포함하므로(주말/휴장 제외) 행 번호 이동만으로 충분하다.
    """
    if df is None or len(df) == 0:
        return None
    import pandas as pd
    idx = pd.to_datetime(df.index).tz_localize(None) if getattr(df.index, 'tz', None) \
        else pd.to_datetime(df.index)
    sig_date = pd.Timestamp(signal_date.date())
    pos = idx.searchsorted(sig_date, side='right')  # signal_date 당일 제외, 다음날부터
    target_pos = pos + n - 1
    if target_pos >= len(df):
        return None
    try:
        return float(df['close'].iloc[target_pos])
    except Exception:
        return None


def _mfe_mae(df, signal_date: datetime, n: int, ref_price: float) -> tuple:
    """signal_date 다음날부터 n거래일 구간 내 최대favorable/최대adverse 변동률."""
    if df is None or len(df) == 0 or ref_price <= 0:
        return None, None
    import pandas as pd
    idx = pd.to_datetime(df.index).tz_localize(None) if getattr(df.index, 'tz', None) \
        else pd.to_datetime(df.index)
    sig_date = pd.Timestamp(signal_date.date())
    pos = idx.searchsorted(sig_date, side='right')
    window = df.iloc[pos:pos + n]
    if len(window) == 0:
        return None, None
    try:
        mfe = float((window['high'].max() - ref_price) / ref_price * 100)
        mae = float((window['low'].min() - ref_price) / ref_price * 100)
        return round(mfe, 4), round(mae, 4)
    except Exception:
        return None, None


def _record_outcome(conn, signal_id: str, stock_code: str, horizon_label: str,
                     reference_price: float, price_at_horizon: Optional[float],
                     mfe_pct: Optional[float], mae_pct: Optional[float]) -> bool:
    return_pct = None
    if price_at_horizon is not None and reference_price > 0:
        return_pct = round((price_at_horizon - reference_price) / reference_price * 100, 4)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO research.signal_outcomes (
                    signal_id, stock_code, horizon_label,
                    reference_price, price_at_horizon, return_pct,
                    mfe_pct, mae_pct
                ) VALUES (
                    %s::uuid, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (signal_id, horizon_label) DO NOTHING
            """, (signal_id, stock_code, horizon_label,
                  reference_price, price_at_horizon, return_pct,
                  mfe_pct, mae_pct))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.warning(f"[SIGNAL_OUTCOME] insert failed {stock_code} {horizon_label}: {e}")
        return False


def collect(lookback_days: int = 30, dry_run: bool = False) -> Dict:
    conn = _get_conn()
    stats = {'processed': 0, 'inserted': 0, 'skipped': 0, 'errors': 0}
    price_cache: Dict[str, object] = {}

    signals = _query_pending_signals(conn, lookback_days)
    now = datetime.now()

    for sig in signals:
        signal_id = str(sig['signal_id'])
        stock_code = sig['stock_code']
        observed_at = sig['observed_at']
        ref_price = float(sig['entry_price_reference'])

        stats['processed'] += 1
        collected = _query_collected_horizons(conn, signal_id)
        pending = [h for h in HORIZONS if h not in collected]
        if not pending:
            continue

        if stock_code not in price_cache:
            price_cache[stock_code] = _fetch_daily_prices(stock_code, observed_at)
        df = price_cache[stock_code]

        for horizon_label in pending:
            n = HORIZON_N[horizon_label]
            # 아직 n거래일이 지나지 않았을 가능성이 높은 경우도 시도는 하되,
            # 데이터가 없으면(target_pos 범위 밖) 자연스럽게 skip된다.
            price_at = _price_n_trading_days_after(df, observed_at, n)
            mfe, mae = _mfe_mae(df, observed_at, n, ref_price)

            if price_at is None:
                stats['skipped'] += 1
                continue

            if dry_run:
                ret = (price_at - ref_price) / ref_price * 100
                print(f"  [DRY] {stock_code} {horizon_label}  ref={ref_price:,.0f}  "
                      f"at={price_at:,.0f}  return={ret:+.2f}%  MFE={mfe} MAE={mae}")
                stats['inserted'] += 1
                continue

            ok = _record_outcome(conn, signal_id, stock_code, horizon_label,
                                  ref_price, price_at, mfe, mae)
            if ok:
                stats['inserted'] += 1
            else:
                stats['errors'] += 1

    conn.close()
    return stats


def main():
    ap = argparse.ArgumentParser(description="Signal Outcome Collector (WI-13)")
    ap.add_argument('--days', type=int, default=30, help='조회 대상 lookback 일수')
    ap.add_argument('--dry-run', action='store_true', help='DB 기록 없이 미리보기')
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    stats = collect(lookback_days=a.days, dry_run=a.dry_run)
    print(f"\n처리 {stats['processed']}건 / 기록 {stats['inserted']}건 / "
          f"스킵(데이터없음) {stats['skipped']}건 / 오류 {stats['errors']}건")
    return 0


if __name__ == '__main__':
    sys.exit(main())
