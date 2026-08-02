"""
analysis/check_research_integrity.py

Research Schema 무결성 감시 (2026-07-27, 운영 안정성 감사 v3.1 — P0.1)

목적:
  research.candidates/decision_ledger/event_store/reason_dictionary가 예고 없이
  통째로 비워지는 사고(2026-07-27 발견: 테스트 코드가 실거래 DB를 TRUNCATE)를
  조기에 발견한다. database_guard로 재발은 막았지만, 이 스크립트는 "혹시 또
  발생했다면 즉시 알아챌 수 있게" 하는 감시용이다.

판정 로직:
  - TRUNCATE 흔적: pg_stat_user_tables에서 누적 INSERT는 많은데(>=100)
    현재 행(n_live_tup)이 0이면 TRUNCATE 의심(정상 DELETE라면 n_tup_del이
    n_tup_ins에 근접해야 하는데 그렇지 않은 경우).
  - 최신 데이터 없음: 최근 N일(기본 3영업일 근사=7일) 안에 신규 행이 없으면
    WARNING (장마감 후 몇 시간 이내에는 정상일 수 있음 — 판단은 사람이 함).

실행:
    python3 -m analysis.check_research_integrity
    python3 -m analysis.check_research_integrity --stale-days 7
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


_TABLES = {
    # table_name: (time_column_for_latest_check,)
    'candidates':        'observed_at',
    'decision_ledger':   'decided_at',
    'event_store':       'occurred_at',
    'future_return_events': 'recorded_at',
}


def _row_counts(cur) -> dict:
    counts = {}
    for table in list(_TABLES) + ['reason_dictionary']:
        cur.execute(f"SELECT COUNT(*) FROM research.{table}")
        counts[table] = cur.fetchone()[0]
    return counts


def _pg_stat(cur) -> dict:
    cur.execute("""
        SELECT relname, n_live_tup, n_tup_ins, n_tup_del
        FROM pg_stat_user_tables
        WHERE schemaname = 'research'
    """)
    return {r[0]: {'n_live_tup': r[1], 'n_tup_ins': r[2], 'n_tup_del': r[3]} for r in cur.fetchall()}


def _latest_timestamps(cur) -> dict:
    latest = {}
    for table, col in _TABLES.items():
        cur.execute(f"SELECT MAX({col}) FROM research.{table}")
        latest[table] = cur.fetchone()[0]
    return latest


def check_integrity(stale_days: int) -> dict:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        counts = _row_counts(cur)
        stats = _pg_stat(cur)
        latest = _latest_timestamps(cur)
    finally:
        cur.close()
        conn.close()

    warnings = []
    now = datetime.now(timezone.utc)

    for table, stat in stats.items():
        n_live, n_ins, n_del = stat['n_live_tup'], stat['n_tup_ins'], stat['n_tup_del']
        if n_ins >= 100 and n_live == 0 and n_del < n_ins * 0.5:
            warnings.append(
                f"[TRUNCATE 의심] research.{table}: 누적 INSERT={n_ins}, 누적 DELETE={n_del}, "
                f"현재 행={n_live} — DELETE 이력으로 설명 안 되는 데이터 소실"
            )

    for table, ts in latest.items():
        if ts is None:
            continue
        age = now - ts
        if age > timedelta(days=stale_days):
            warnings.append(
                f"[최신 데이터 없음] research.{table}: 마지막 기록 {ts} "
                f"({age.days}일 전, 기준 {stale_days}일)"
            )

    if counts.get('reason_dictionary', 0) < 20:
        warnings.append(
            f"[reason_dictionary 축소 의심] 현재 {counts.get('reason_dictionary', 0)}행 "
            f"— 2026-07-27 마이그레이션 이후 최소 23행이 기대됨"
        )

    return {'counts': counts, 'stats': stats, 'latest': latest, 'warnings': warnings}


def print_report(result: dict) -> bool:
    print()
    print("Research Schema Integrity Check")
    print()
    print("건수:")
    for table, cnt in result['counts'].items():
        print(f"  {table:25} {cnt}")
    print()
    print("최근 기록 시각:")
    for table, ts in result['latest'].items():
        print(f"  {table:25} {ts if ts else '(없음)'}")

    is_ok = not result['warnings']
    print()
    if result['warnings']:
        print("경고:")
        for w in result['warnings']:
            print(f"  ⚠️  {w}")
    else:
        print("이상 없음")
    print()
    print(f"STATUS : {'OK' if is_ok else 'WARNING'}")
    print()
    return is_ok


def main():
    parser = argparse.ArgumentParser(description="Research Schema 무결성 검사")
    parser.add_argument('--stale-days', type=int, default=7,
                         help='최근 N일 이내 데이터 없으면 경고 (기본 7)')
    args = parser.parse_args()

    result = check_integrity(args.stale_days)
    ok = print_report(result)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
