#!/usr/bin/env python3
"""
거래내역 비교 스크립트

키움 API와 시스템 DB의 거래내역을 비교하여 불일치를 감지합니다.

사용법:
  python scripts/reconcile_trades.py              # 오늘 거래내역 비교
  python scripts/reconcile_trades.py 20260113     # 특정 날짜 비교
"""
import sys
import os
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase
from utils.trade_review_system import TradeReconciliationService
from datetime import datetime
import argparse

# 키움 API import
sys.path.insert(0, str(project_root.parent / "swing_trader_pipeline" / "app"))
from services.kiwoom import KiwoomService


def main():
    parser = argparse.ArgumentParser(description='거래내역 비교 (키움 API vs DB)')
    parser.add_argument('trade_date', nargs='?', default=None,
                       help='거래 날짜 (YYYYMMDD형식, 생략 시 오늘)')
    parser.add_argument('--days', type=int, default=1,
                       help='최근 N일 비교 (기본: 1일)')
    args = parser.parse_args()

    # DB 연결
    print("📂 데이터베이스 연결 중...")
    db = TradingDatabase()

    # 키움 API 연결
    print("🔗 키움 API 연결 중...")
    kiwoom_api = KiwoomService()

    # 거래내역 비교 서비스
    reconciliation_service = TradeReconciliationService(db, kiwoom_api)

    if args.trade_date:
        # 특정 날짜 비교
        trade_dates = [args.trade_date]
    else:
        # 최근 N일 비교
        from datetime import timedelta
        today = datetime.now()
        trade_dates = [
            (today - timedelta(days=i)).strftime("%Y%m%d")
            for i in range(args.days)
        ]

    print(f"\n{'='*80}")
    print(f"거래내역 비교: {len(trade_dates)}일")
    print(f"{'='*80}\n")

    all_matched = True
    total_missing = 0
    total_extra = 0

    for trade_date in trade_dates:
        result = reconciliation_service.compare_daily_trades(trade_date)

        if not result['is_matched']:
            all_matched = False
            total_missing += len(result['missing_trades'])
            total_extra += len(result['extra_trades'])

    print(f"\n{'='*80}")
    if all_matched:
        print("✅ 모든 거래내역이 일치합니다.")
    else:
        print(f"❌ 불일치 발견!")
        print(f"   - 누락된 거래 (API에는 있지만 DB에 없음): {total_missing}건")
        print(f"   - 불일치 거래 (DB에만 있음): {total_extra}건")
        print(f"\n💡 해결 방법:")
        print(f"   1. data/trade_alerts.log 확인")
        print(f"   2. 누락된 거래를 수동으로 DB에 추가")
        print(f"   3. 자동매매 코드의 DB 저장 로직 점검")
    print(f"{'='*80}\n")

    # 비교 이력 조회
    print("📊 최근 비교 이력:")
    history = db.get_reconciliation_history(days=7)

    if history:
        print(f"\n{'날짜':<12} {'DB':<10} {'API':<10} {'일치':<6} {'상세'}")
        print("-" * 60)
        for record in history[:10]:
            match_status = "✅" if record['is_matched'] else "❌"
            detail = record.get('discrepancy_detail', '') or '-'
            print(f"{str(record['trade_date']):<12} "
                  f"{record['db_trade_count']:<10} "
                  f"{record['api_trade_count']:<10} "
                  f"{match_status:<6} "
                  f"{detail}")
    else:
        print("  (비교 이력 없음)")

    db.close()
    print("\n✅ 완료!")


if __name__ == '__main__':
    main()
