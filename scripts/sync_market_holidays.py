#!/usr/bin/env python3
"""
scripts/sync_market_holidays.py — pykrx 기반 시장 휴장일 DB 동기화

pykrx에서 실제 KRX 거래일 데이터를 가져와 market_holidays 테이블을 갱신.
DB를 혼자 판단해 수동 입력하지 않고 pykrx를 authoritative source로 사용.

사용:
  python3 scripts/sync_market_holidays.py --year 2026
  python3 scripts/sync_market_holidays.py --year 2026 --dry-run

동작:
  1. pykrx에서 해당 연도 삼성전자(005930) OHLCV 조회 → 거래일 목록 확인
  2. 모든 평일(월~금)에서 거래일 제외 → 휴장일 목록
  3. 고정 공휴일(신정/삼일절 등)은 이름 매핑, 나머지는 '시장휴일'
  4. DB UPSERT (이미 있는 항목은 이름 보존)
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from market_utils import get_db_connection

try:
    from pykrx import stock as krx_stock
except ImportError:
    print("ERROR: pykrx 미설치. pip install pykrx")
    sys.exit(1)

# 고정 날짜 공휴일 (MM-DD → 이름)
FIXED_HOLIDAYS = {
    "01-01": "신정",
    "03-01": "삼일절",
    "05-05": "어린이날",
    "06-06": "현충일",
    "08-15": "광복절",
    "10-03": "개천절",
    "10-09": "한글날",
    "12-25": "성탄절",
}


def get_krx_trading_days(year: int) -> set[date]:
    """pykrx에서 해당 연도 KRX 거래일 집합 조회."""
    start = f"{year}0101"
    end   = f"{year}1231"
    print(f"pykrx 조회 중: {year}년 거래일...", flush=True)
    try:
        df = krx_stock.get_market_ohlcv_by_date(start, end, "005930")
        if df is None or df.empty:
            print("ERROR: pykrx 데이터 없음 (삼성전자 OHLCV 조회 실패)")
            return set()
        trading_days = {d.date() for d in df.index}
        print(f"  → 거래일 {len(trading_days)}일 확인")
        return trading_days
    except Exception as e:
        print(f"ERROR: pykrx 조회 실패 — {e}")
        return set()


def get_weekdays(year: int) -> list[date]:
    """해당 연도 모든 평일(월~금) 목록."""
    days = []
    d = date(year, 1, 1)
    end = date(year, 12, 31)
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def guess_holiday_name(d: date, existing_name: str | None) -> str:
    """공휴일 이름 추론. 기존 DB 이름 > 고정 매핑 > '시장휴일' 순."""
    if existing_name:
        return existing_name
    key = d.strftime("%m-%d")
    return FIXED_HOLIDAYS.get(key, "시장휴일")


def sync(year: int, dry_run: bool = False) -> None:
    trading_days = get_krx_trading_days(year)
    if not trading_days:
        return

    # pykrx는 미래 날짜 데이터 없음 → 어제까지만 분석 (확정된 과거만)
    cutoff = date.today() - timedelta(days=1)
    weekdays = [d for d in get_weekdays(year) if d <= cutoff]
    holidays = [d for d in weekdays if d not in trading_days]

    future_weekdays = [d for d in get_weekdays(year) if d > cutoff]

    print(f"\n과거 평일 {len(weekdays)}일 (≤{cutoff}) 기준")
    print(f"  거래일: {len(trading_days & set(weekdays))}일")
    print(f"  휴장일: {len(holidays)}일")
    print(f"  미래 평일: {len(future_weekdays)}일 (pykrx 데이터 없음 — 제외)")

    conn = get_db_connection()
    cur  = conn.cursor()

    # 기존 DB 항목 조회 (해당 연도 전체)
    cur.execute(
        "SELECT holiday_date, holiday_name FROM market_holidays "
        "WHERE EXTRACT(YEAR FROM holiday_date) = %s",
        (year,)
    )
    existing = {row[0]: row[1] for row in cur.fetchall()}

    to_insert, to_skip = [], []
    for d in holidays:
        name = guess_holiday_name(d, existing.get(d))
        if d in existing:
            to_skip.append((d, name))
        else:
            to_insert.append((d, name))

    # DB에 있는데 pykrx가 실제 거래일로 확인한 날짜 = DB 오류
    # 주말은 제외 (DB에 있어도 무해), 평일만 체크
    stale = [
        d for d in existing
        if d <= cutoff and d.weekday() < 5 and d in trading_days
    ]

    print(f"\n  신규 추가: {len(to_insert)}건")
    print(f"  기존 유지: {len(to_skip)}건")
    if stale:
        print(f"  DB 오류 (pykrx=거래일 확인, DB=휴장으로 잘못 등록): {len(stale)}건")
        for d in sorted(stale):
            print(f"    [삭제 예정] {d}  {existing[d]}")

    if dry_run:
        print("\n[DRY RUN] 실제 DB 변경 없음.")
        if to_insert:
            print("추가 예정:")
            for d, name in sorted(to_insert):
                print(f"  {d} ({d.strftime('%a')})  {name}")
        conn.close()
        return

    # 신규 추가
    for d, name in to_insert:
        cur.execute(
            "INSERT INTO market_holidays (holiday_date, holiday_name, holiday_type) "
            "VALUES (%s, %s, 'regular') ON CONFLICT (holiday_date) DO NOTHING",
            (d, name)
        )

    # DB 오류 항목 삭제
    for d in stale:
        cur.execute("DELETE FROM market_holidays WHERE holiday_date = %s", (d,))
        print(f"  [삭제] {d}  {existing[d]}")

    conn.commit()
    conn.close()

    print(f"\n완료: {year}년 과거 휴장일 {len(holidays)}건 동기화")
    print(f"\n[{year}년 확인된 휴장일 (평일 기준)]")
    for d in sorted(holidays):
        name = guess_holiday_name(d, existing.get(d))
        print(f"  {d} ({d.strftime('%a')})  {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pykrx 기반 KRX 휴장일 DB 동기화")
    parser.add_argument("--year",    type=int, default=date.today().year)
    parser.add_argument("--dry-run", action="store_true", help="DB 변경 없이 미리보기")
    args = parser.parse_args()

    sync(args.year, dry_run=args.dry_run)
