"""
매매 컨텍스트 저장 기능 테스트

DB에 entry_context, exit_context, filter_scores가 올바르게 저장되는지 확인
"""
import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase
from datetime import datetime
import json


def test_context_storage():
    """컨텍스트 저장 및 조회 테스트"""

    print("=" * 80)
    print("매매 컨텍스트 저장 기능 테스트")
    print("=" * 80)

    # 테스트용 DB 생성
    db = TradingDatabase(db_path="data/test_context.db")

    # 1. 매수 거래 데이터 (entry_context 포함)
    entry_context = {
        'price': 10000,
        'vwap': 9950,
        'vwap_diff_pct': 0.5,
        'ma5': 9900,
        'ma20': 9850,
        'rsi14': 58.3,
        'williams_r': -35.2,
        'volume_ratio': 1.45,
        'candle': {'open': 9980, 'high': 10020, 'low': 9970, 'close': 10000},
        'entry_time': datetime.now().isoformat(),
    }

    filter_scores = {
        'vwap_breakout': True,
        'trend_filter': True,
        'volume_filter': True,
        'williams_r_filter': True,
        'volume_multiplier_value': 1.45,
        'williams_r_value': -35.2,
    }

    buy_trade = {
        'stock_code': '005930',
        'stock_name': '삼성전자',
        'trade_type': 'BUY',
        'trade_time': datetime.now().isoformat(),
        'price': 10000,
        'quantity': 10,
        'amount': 100000,
        'condition_name': 'VWAP_TEST',
        'entry_reason': '테스트 진입',
        'entry_context': json.dumps(entry_context, ensure_ascii=False),
        'filter_scores': json.dumps(filter_scores, ensure_ascii=False),
    }

    print("\n1. 매수 거래 저장 (entry_context 포함)")
    trade_id = db.insert_trade(buy_trade)
    print(f"✓ 매수 거래 저장 완료 (trade_id: {trade_id})")

    # 2. 매도 정보 업데이트 (exit_context 포함)
    exit_context = {
        'price': 10150,
        'entry_price': 10000,
        'highest_price': 10200,
        'highest_profit_pct': 2.0,
        'profit_pct': 1.5,
        'profit_preservation_pct': 75.0,
        'trailing_activated': True,
        'trailing_activation_price': 10130,
        'partial_exit_stage': 0,
        'rsi14': 71.2,
        'williams_r': -12.5,
        'volume_ratio': 0.8,
        'exit_time': datetime.now().isoformat(),
        'reason': 'TRAILING_STOP',
        'holding_duration_minutes': 35,
    }

    exit_data = {
        'exit_reason': '트레일링 스탑 (-1.0%)',
        'realized_profit': 1500,
        'profit_rate': 1.5,
        'holding_duration': 2100,  # 35분
        'exit_context': json.dumps(exit_context, ensure_ascii=False),
    }

    print("\n2. 매도 정보 업데이트 (exit_context 포함)")
    db.update_trade_exit(trade_id, exit_data)
    print(f"✓ 매도 정보 업데이트 완료")

    # 3. 조회 및 확인
    print("\n3. 거래 이력 조회 (JSON 파싱)")
    trades = db.get_trades_with_context(stock_code='005930', parse_context=True)

    if trades:
        trade = trades[0]
        print(f"\n종목: {trade['stock_name']} ({trade['stock_code']})")
        print(f"매수가: {trade['price']:,}원")
        print(f"수익률: {trade.get('profit_rate', 0):+.2f}%")
        print(f"보유시간: {trade.get('holding_duration', 0) // 60}분")

        # entry_context 확인
        if trade.get('entry_context'):
            print("\n[진입 컨텍스트]")
            ec = trade['entry_context']
            print(f"  - VWAP: {ec.get('vwap'):,}원 (가격 대비 {ec.get('vwap_diff_pct'):+.2f}%)")
            print(f"  - RSI14: {ec.get('rsi14')}")
            print(f"  - Williams %R: {ec.get('williams_r')}")
            print(f"  - 거래량 비율: {ec.get('volume_ratio')}배")
            print(f"  - 캔들: {ec.get('candle')}")

        # exit_context 확인
        if trade.get('exit_context'):
            print("\n[청산 컨텍스트]")
            xc = trade['exit_context']
            print(f"  - 최고가: {xc.get('highest_price'):,}원 (최고 수익률 {xc.get('highest_profit_pct'):+.2f}%)")
            print(f"  - 수익 보존율: {xc.get('profit_preservation_pct'):.1f}%")
            print(f"  - 트레일링 활성화: {xc.get('trailing_activated')}")
            print(f"  - 청산 시점 RSI14: {xc.get('rsi14')}")
            print(f"  - 청산 시점 Williams %R: {xc.get('williams_r')}")

        # filter_scores 확인
        if trade.get('filter_scores'):
            print("\n[필터 점수]")
            fs = trade['filter_scores']
            print(f"  - VWAP 돌파: {fs.get('vwap_breakout')}")
            print(f"  - 추세 필터: {fs.get('trend_filter')}")
            print(f"  - 거래량 필터: {fs.get('volume_filter')}")
            print(f"  - Williams %R 필터: {fs.get('williams_r_filter')}")

    print("\n" + "=" * 80)
    print("✅ 테스트 완료!")
    print("=" * 80)
    print("\n📁 테스트 DB 파일: data/test_context.db")
    print("🔍 다음 단계:")
    print("  1. 실제 매매 시스템에서 context 수집 확인")
    print("  2. ML 학습용 데이터 추출 스크립트 작성")
    print("  3. 필터 최적화 분석 수행")


if __name__ == "__main__":
    test_context_storage()
