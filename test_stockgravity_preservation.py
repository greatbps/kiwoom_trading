#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StockGravity 종목 보존 테스트
DB 복원 → 필터링 후에도 StockGravity 종목이 유지되는지 확인
"""

import sys
from market_utils import get_db_connection


class MockWatchlist:
    """모의 watchlist"""
    def __init__(self):
        self.data = set()

    def add(self, code):
        self.data.add(code)

    def discard(self, code):
        self.data.discard(code)

    def clear(self):
        self.data.clear()

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        return iter(self.data)


def test_preservation():
    """종목 보존 테스트"""

    # Step 1: DB에서 종목 로드 (실제 코드 시뮬레이션)
    print("="*60)
    print("STEP 1: DB에서 모니터링 종목 복원")
    print("="*60)

    watchlist = MockWatchlist()
    validated_stocks = {}

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, name, source, add_reason, created_at
            FROM monitoring_stocks
            WHERE monitoring_active = true
            ORDER BY created_at DESC
        """)

        rows = cur.fetchall()
        print(f"\n📊 DB에서 {len(rows)}개 종목 발견")

        for symbol, name, source, add_reason, created_at in rows:
            watchlist.add(symbol)
            validated_stocks[symbol] = {
                'name': name,
                'source': source,
                'add_reason': add_reason,
                'created_at': created_at
            }

        print(f"✅ DB 복원 완료: {len(rows)}개 종목")
        print(f"  🔍 조건검색: {sum(1 for v in validated_stocks.values() if v.get('source') == 'condition_search')}개")
        print(f"  📦 StockGravity: {sum(1 for v in validated_stocks.values() if v.get('source') == 'stockgravity')}개")
        print(f"\n📌 watchlist 크기: {len(watchlist)}")
        print(f"📌 validated_stocks 크기: {len(validated_stocks)}")

    finally:
        conn.close()

    # Step 2: 필터링 단계 (조건검색 종목만 제거)
    print("\n" + "="*60)
    print("STEP 2: 조건검색 종목 초기화 (StockGravity 유지)")
    print("="*60)

    # StockGravity 종목 추출
    stockgravity_stocks = {
        code: info for code, info in validated_stocks.items()
        if info.get('source') == 'stockgravity'
    }

    # 조건검색 종목만 제거
    condition_codes = [
        code for code, info in validated_stocks.items()
        if info.get('source') == 'condition_search'
    ]

    print(f"\n🔄 제거할 조건검색 종목: {len(condition_codes)}개")
    print(f"✅ 유지할 StockGravity 종목: {len(stockgravity_stocks)}개")

    for code in condition_codes:
        watchlist.discard(code)
        validated_stocks.pop(code, None)

    print(f"\n✓ 필터링 완료")
    print(f"  제거: {len(condition_codes)}개")
    print(f"  유지: {len(stockgravity_stocks)}개")

    # Step 3: 최종 확인
    print("\n" + "="*60)
    print("STEP 3: 최종 검증")
    print("="*60)

    print(f"\n📌 최종 watchlist 크기: {len(watchlist)}")
    print(f"📌 최종 validated_stocks 크기: {len(validated_stocks)}")

    if len(watchlist) == len(stockgravity_stocks):
        print(f"\n✅ 성공! StockGravity {len(stockgravity_stocks)}개 종목이 정상적으로 보존되었습니다.")

        print(f"\n보존된 종목 목록:")
        for code, info in validated_stocks.items():
            print(f"  {code} - {info['name']} (출처: {info['source']})")

        return True
    else:
        print(f"\n❌ 실패! 예상: {len(stockgravity_stocks)}개, 실제: {len(watchlist)}개")
        return False


if __name__ == "__main__":
    success = test_preservation()
    sys.exit(0 if success else 1)
