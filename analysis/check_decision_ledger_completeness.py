"""
analysis/check_decision_ledger_completeness.py

Decision Ledger Completeness 검증 (P0 후속검증, 2026-07-27)

목적:
  research.candidates에 생성된 모든 candidate가 최종적으로 research.decision_ledger에
  PASS 또는 REJECT 결정을 남기는지 확인한다. 누락되면 execute_buy()(또는 그 이전 단계)의
  어딘가에서 예외/조기반환이 Decision Ledger 기록 없이 발생했다는 뜻이다.

  "execute_buy() 호출 횟수"는 애플리케이션 메모리(_pending_decision_ctx)에만 존재해
  DB에서 직접 셀 수 없다. 대신 candidate 자체를 기준으로 삼는다 — 모든 candidate는
  (execute_buy에 도달하든 그 이전 게이트에서 거절되든) 결국 정확히 하나의 decision_ledger
  행을 가져야 하므로, 이것이 candidate 단위의 진짜 completeness 지표다.

실행:
    python3 -m analysis.check_decision_ledger_completeness            # 오늘
    python3 -m analysis.check_decision_ledger_completeness --date 2026-07-27
    python3 -m analysis.check_decision_ledger_completeness --days 5   # 최근 5일 각각
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

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


def check_completeness(cur, target_date: date) -> dict:
    d = str(target_date)

    cur.execute(
        "SELECT COUNT(*) FROM research.candidates WHERE observed_at::date = %s", (d,))
    candidate_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT c.candidate_id)
        FROM research.candidates c
        JOIN research.decision_ledger d ON d.candidate_id = c.candidate_id
        WHERE c.observed_at::date = %s
    """, (d,))
    ledger_count = cur.fetchone()[0]

    cur.execute("""
        SELECT c.stock_code, c.candidate_id, c.observed_at
        FROM research.candidates c
        WHERE c.observed_at::date = %s
          AND NOT EXISTS (
              SELECT 1 FROM research.decision_ledger d
              WHERE d.candidate_id = c.candidate_id
          )
        ORDER BY c.observed_at
    """, (d,))
    missing = cur.fetchall()

    return {
        'date': d,
        'candidate_count': candidate_count,
        'ledger_count': ledger_count,
        'missing': missing,
    }


def print_report(result: dict) -> bool:
    """리포트 출력. 반환값: PASS 여부."""
    missing = result['missing']
    is_pass = len(missing) == 0

    print()
    print(f"Decision Ledger Completeness — {result['date']}")
    print()
    print(f"Candidate 생성   : {result['candidate_count']}")
    print(f"Ledger 생성      : {result['ledger_count']}")
    print(f"누락             : {len(missing)}")

    if missing:
        print()
        print("누락 상세:")
        for stock_code, candidate_id, observed_at in missing:
            print(f"  {stock_code}  candidate_id={candidate_id}  observed_at={observed_at}")

    print()
    print(f"STATUS : {'PASS' if is_pass else 'FAIL'}")
    print()

    if result['candidate_count'] == 0:
        print("[참고] 해당일 candidate가 0건입니다 — completeness 검증은 데이터가 있을 때만 의미가 있습니다.")
        print()

    return is_pass


def main():
    parser = argparse.ArgumentParser(description="Decision Ledger Completeness 검증")
    parser.add_argument('--date', type=str, default=None, help='YYYY-MM-DD (기본: 오늘)')
    parser.add_argument('--days', type=int, default=1, help='오늘/지정일 포함 최근 N일 각각 검사 (기본 1)')
    args = parser.parse_args()

    if args.date:
        end_date = date.fromisoformat(args.date)
    else:
        end_date = date.today()

    conn = _get_conn()
    cur = conn.cursor()

    overall_pass = True
    try:
        for offset in range(args.days - 1, -1, -1):
            target_date = end_date - timedelta(days=offset)
            result = check_completeness(cur, target_date)
            ok = print_report(result)
            overall_pass = overall_pass and ok
    finally:
        cur.close()
        conn.close()

    sys.exit(0 if overall_pass else 1)


if __name__ == '__main__':
    main()
