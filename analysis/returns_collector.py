"""
Future Returns Collector

게이트별 Opportunity Cost + PASS 전략 성과를 future_return_events에 기록.

REJECT 수집 목적 (Opportunity Cost):
  "VOLUME 필터가 차단한 종목이 실제로 얼마나 올랐는가?"
  "어떤 게이트가 좋은 기회를 가장 많이 놓치고 있는가?"

PASS 수집 목적 (Outcome Tracking):
  "진입한 종목의 실제 수익률은 어떤가?"

호라이즌:
  REJECT: +30m, +1h, EOD
  PASS:   +30m, +1h, EOD, +D1, +D3, +D5, +D10

실행:
  python3 -m analysis.returns_collector                   # 오늘 수집
  python3 -m analysis.returns_collector --date 2026-07-14
  python3 -m analysis.returns_collector --dry-run         # DB 기록 없이 미리보기

크론 (권장):
  */30 9-15 * * 1-5   python3 -m analysis.returns_collector   # 장중 30분마다
  40 15 * * 1-5        python3 -m analysis.returns_collector   # 장 마감 후 EOD
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

logger = logging.getLogger(__name__)

# ─── Horizon 정의 ─────────────────────────────────────────────────

# (label, horizon_minutes, 적용 decision 타입)
# horizon_minutes=None → EOD (당일 종가 사용)
REJECT_HORIZONS: List[Tuple[str, Optional[int]]] = [
    ('+30m',  30),
    ('+1h',   60),
    ('+EOD',  None),
]

PASS_HORIZONS: List[Tuple[str, Optional[int]]] = [
    ('+30m',  30),
    ('+1h',   60),
    ('+EOD',  None),
    ('+D1',   1440),      # 다음날 종가 (1거래일)
    ('+D3',   4320),      # 3거래일 후 종가
    ('+D5',   7200),      # 5거래일 후 종가
    ('+D10',  14400),     # 10거래일 후 종가
]

# 장 마감 시각 (KST)
MARKET_CLOSE_HOUR   = 15
MARKET_CLOSE_MINUTE = 30


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── DB Query ────────────────────────────────────────────────────

def _query_pending_decisions(conn, target_date: date) -> List[Dict]:
    """
    당일 결정 중 미래수익률 수집이 필요한 결정 조회.
    - price > 0 조건: GLOBAL_GATE/STOCK_GATE 조기 거절(price=0.0)은 제외
      → 이 게이트들은 데이터 로딩 전 거절이므로 reference price 없음
    - REJECT + PASS 모두 조회
    """
    d = str(target_date)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                d.decision_id,
                d.stock_code,
                d.trace_id,
                d.decision,
                d.lifecycle_status,
                d.decision_reason_code,
                c.price         AS reference_price,
                c.market,
                c.observed_at
            FROM research.decision_ledger d
            JOIN research.candidates c ON c.candidate_id = d.candidate_id
            WHERE d.decided_at::date = %s
              AND c.price > 0
              AND d.lifecycle_status NOT IN ('AUDIT_COMPLETED', 'KNOWLEDGE_EXTRACTED')
            ORDER BY c.observed_at
        """, (d,))
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _query_collected_horizons(conn, decision_id: str) -> set:
    """이미 수집된 horizon_label 집합."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT horizon_label FROM research.future_return_events
            WHERE decision_id = %s::uuid
        """, (decision_id,))
        return {r[0] for r in cur.fetchall()}


# ─── Price Fetching ───────────────────────────────────────────────

def _fetch_intraday_prices(stock_code: str, market: str, target_date: date) -> Optional[object]:
    """
    yfinance 5분봉 조회.
    반환: DataFrame(index=datetime, columns=[open,high,low,close,volume])
    """
    try:
        import yfinance as yf
        import pandas as pd
        suffix = '.KS' if (market or '').upper() == 'KOSPI' else '.KQ'
        ticker = f"{stock_code}{suffix}"
        d_str = str(target_date)
        d_next = str(target_date + timedelta(days=1))
        df = yf.download(
            ticker, start=d_str, end=d_next,
            interval='5m', progress=False, auto_adjust=True
        )
        if df is None or len(df) == 0:
            # KOSPI↔KOSDAQ 반대 시도
            alt = '.KQ' if suffix == '.KS' else '.KS'
            df = yf.download(
                f"{stock_code}{alt}", start=d_str, end=d_next,
                interval='5m', progress=False, auto_adjust=True
            )
        if df is None or len(df) == 0:
            return None
        # Multi-level columns → flatten
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                          for c in df.columns]
        else:
            df.columns = df.columns.str.lower()
        return df
    except Exception as e:
        logger.warning(f"[RETURNS] price fetch failed {stock_code}: {e}")
        return None


def _get_price_at(df, target_dt: datetime) -> Optional[float]:
    """
    DataFrame에서 target_dt 이후의 첫 번째 5분봉 종가 반환.
    target_dt에 timezone 없으면 KST로 가정해 비교한다.
    """
    if df is None or len(df) == 0:
        return None
    try:
        import pandas as pd
        idx = df.index
        # timezone 정규화
        if idx.tzinfo is None or idx.tzinfo.utcoffset(None) is None:
            idx = idx.tz_localize('Asia/Seoul')
        else:
            idx = idx.tz_convert('Asia/Seoul')
        if target_dt.tzinfo is None:
            import pytz
            target_dt = pytz.timezone('Asia/Seoul').localize(target_dt)
        else:
            import pytz
            target_dt = target_dt.astimezone(pytz.timezone('Asia/Seoul'))

        mask = idx >= target_dt
        if not mask.any():
            # target_dt 이후 데이터 없음 → 마지막 봉 사용
            close_col = 'close' if 'close' in df.columns else df.columns[3]
            return float(df[close_col].iloc[-1])
        close_col = 'close' if 'close' in df.columns else df.columns[3]
        return float(df[close_col][mask].iloc[0])
    except Exception as e:
        logger.debug(f"[RETURNS] _get_price_at error: {e}")
        return None


def _eod_price(df, target_date: date) -> Optional[float]:
    """당일 장 마감(15:30) 직전 마지막 봉 종가."""
    if df is None or len(df) == 0:
        return None
    try:
        import pandas as pd, pytz
        idx = df.index
        if idx.tzinfo is None or idx.tzinfo.utcoffset(None) is None:
            idx = idx.tz_localize('Asia/Seoul')
        else:
            idx = idx.tz_convert('Asia/Seoul')
        kst = pytz.timezone('Asia/Seoul')
        cutoff = kst.localize(
            datetime(target_date.year, target_date.month, target_date.day,
                     MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
        )
        mask = idx <= cutoff
        if not mask.any():
            return None
        close_col = 'close' if 'close' in df.columns else df.columns[3]
        return float(df[close_col][mask].iloc[-1])
    except Exception as e:
        logger.debug(f"[RETURNS] _eod_price error: {e}")
        return None


# ─── Collector ───────────────────────────────────────────────────

def collect_for_date(
    target_date: date,
    dry_run: bool = False,
) -> Dict:
    """
    대상 날짜의 모든 pending 결정에 대해 미래수익률 수집.

    Returns:
        {'processed': int, 'inserted': int, 'skipped': int, 'errors': int}
    """
    conn = _get_conn()
    from repositories.decision_repository import DecisionRepository

    class _DB:
        def _get_conn(self): return conn
        def _put_conn(self, c): pass

    repo = DecisionRepository(_DB())

    decisions = _query_pending_decisions(conn, target_date)
    now_kst   = datetime.now()

    stats = {'processed': 0, 'inserted': 0, 'skipped': 0, 'errors': 0}

    # 종목별 5분봉 캐시 (동일 종목 여러 결정 시 API 중복 호출 방지)
    price_cache: Dict[str, object] = {}

    for dec in decisions:
        decision_id   = str(dec['decision_id'])
        stock_code    = dec['stock_code']
        market        = dec['market'] or 'KOSPI'
        ref_price     = float(dec['reference_price'] or 0)
        observed_at   = dec['observed_at']
        decision_type = dec['decision']
        reason_code   = dec['decision_reason_code']
        trace_id      = dec['trace_id']

        if ref_price <= 0:
            stats['skipped'] += 1
            continue  # price 없는 초기 게이트 거절 제외

        stats['processed'] += 1

        # horizon 목록 (결정 타입에 따라 다름)
        horizons = PASS_HORIZONS if decision_type == 'PASS' else REJECT_HORIZONS

        # 이미 수집된 horizon 확인
        collected = _query_collected_horizons(conn, decision_id)
        pending_horizons = [(lbl, mins) for lbl, mins in horizons if lbl not in collected]

        if not pending_horizons:
            continue  # 모두 수집 완료

        # 5분봉 데이터 (종목당 1회 조회)
        if stock_code not in price_cache:
            price_cache[stock_code] = _fetch_intraday_prices(stock_code, market, target_date)
        df = price_cache[stock_code]

        for horizon_label, horizon_minutes in pending_horizons:
            # 수집 가능 여부 체크 (아직 시간이 안 됐으면 skip)
            if horizon_label == '+EOD':
                # EOD: 장 마감(15:30) 이후에만 수집
                eod_cutoff = observed_at.replace(
                    hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE,
                    second=0, microsecond=0,
                )
                if now_kst < eod_cutoff:
                    continue
                price_at = _eod_price(df, target_date)
            elif horizon_minutes and horizon_minutes <= 1440:
                # 당일 intraday
                target_dt = observed_at + timedelta(minutes=horizon_minutes)
                if now_kst < target_dt:
                    continue  # 아직 해당 시점 미도래
                price_at = _get_price_at(df, target_dt)
            else:
                # D+N (다음날 종가): 다음 거래일 데이터 필요 — 별도 처리 생략
                continue

            if price_at is None or price_at <= 0:
                logger.debug(
                    f"[RETURNS] {stock_code} {horizon_label} price unavailable"
                )
                stats['errors'] += 1
                continue

            return_pct = (price_at - ref_price) / ref_price * 100

            if dry_run:
                print(
                    f"  [DRY] {stock_code} {reason_code:<25} {horizon_label}"
                    f"  ref={ref_price:,.0f}  at={price_at:,.0f}"
                    f"  return={return_pct:+.2f}%"
                )
                stats['inserted'] += 1
                continue

            ok = repo.record_future_return(
                decision_id=decision_id,
                stock_code=stock_code,
                horizon_label=horizon_label,
                decision_price=ref_price,
                return_pct=round(return_pct, 4),
                price_at_horizon=price_at,
                horizon_minutes=horizon_minutes,
                trace_id=trace_id,
                created_by='returns_collector',
            )
            if ok:
                stats['inserted'] += 1
                logger.info(
                    f"[RETURNS] {stock_code} {reason_code} {horizon_label}"
                    f" {return_pct:+.2f}%  (ref={ref_price:.0f}→{price_at:.0f})"
                )
            else:
                stats['errors'] += 1

    conn.close()
    return stats


# ─── Opportunity Cost Report ─────────────────────────────────────

def print_opportunity_cost(target_date: date, days: int = 7) -> None:
    """
    게이트별 Opportunity Cost 요약.

    Gate    건수  평균(+30m)  평균(+1h)  평균(EOD)  놓친_3%이상
    """
    conn = _get_conn()
    sd = str(target_date - timedelta(days=days - 1))
    ed = str(target_date)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                d.decision_reason_code                        AS gate,
                COUNT(DISTINCT d.decision_id)                 AS decisions,
                AVG(CASE WHEN fr.horizon_label='+30m' THEN fr.return_pct END) AS avg_30m,
                AVG(CASE WHEN fr.horizon_label='+1h'  THEN fr.return_pct END) AS avg_1h,
                AVG(CASE WHEN fr.horizon_label='+EOD' THEN fr.return_pct END) AS avg_eod,
                COUNT(CASE WHEN fr.horizon_label='+EOD'
                            AND fr.return_pct >= 3.0 THEN 1 END)              AS missed_3pct
            FROM research.decision_ledger d
            JOIN research.future_return_events fr ON fr.decision_id = d.decision_id
            WHERE d.decided_at::date BETWEEN %s AND %s
              AND d.decision = 'REJECT'
            GROUP BY d.decision_reason_code
            ORDER BY avg_eod DESC NULLS LAST
        """, (sd, ed))
        rows = cur.fetchall()

    conn.close()

    if not rows:
        print(f"\n  [!] {sd} ~ {ed} 미래수익률 데이터 없음 (수집 후 다시 실행)")
        return

    print(f"\n{'='*75}")
    print(f"  Gate Opportunity Cost — {sd} ~ {ed}")
    print(f"{'='*75}")
    print(f"  {'Gate':<25} {'N':>5}  {'avg+30m':>8}  {'avg+1h':>8}  {'avgEOD':>8}  {'놓친≥3%':>8}")
    print(f"  {'-'*25} {'-'*5}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
    for r in rows:
        gate, n, a30, a1h, aeod, m3 = r
        fmt = lambda v: f"{float(v):+.2f}%" if v is not None else '   -'
        print(
            f"  {(gate or '-'):<25} {n:>5}  {fmt(a30):>8}  "
            f"{fmt(a1h):>8}  {fmt(aeod):>8}  {(m3 or 0):>8}"
        )
    print(f"{'='*75}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    parser = argparse.ArgumentParser(
        description='Future Returns Collector — 게이트별 Opportunity Cost 수집',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python3 -m analysis.returns_collector                        # 오늘 수집
  python3 -m analysis.returns_collector --date 2026-07-14
  python3 -m analysis.returns_collector --dry-run              # 미리보기
  python3 -m analysis.returns_collector --report --days 7     # Opportunity Cost 리포트
"""
    )
    parser.add_argument('--date',    default=None, help='수집 날짜 (YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true', help='DB 기록 없이 미리보기')
    parser.add_argument('--report',  action='store_true', help='Opportunity Cost 리포트 출력')
    parser.add_argument('--days',    type=int, default=7, help='리포트 기간 (기본: 7일)')
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()

    if args.report:
        print_opportunity_cost(target, days=args.days)
    else:
        if args.dry_run:
            print(f"\n[DRY RUN] {target} — DB 기록 없이 출력\n")
        stats = collect_for_date(target, dry_run=args.dry_run)
        print(
            f"\n[RETURNS] {target}  "
            f"processed={stats['processed']}  "
            f"inserted={stats['inserted']}  "
            f"skipped={stats['skipped']}  "
            f"errors={stats['errors']}"
        )
