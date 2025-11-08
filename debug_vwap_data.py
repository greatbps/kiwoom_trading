"""
VWAP 백테스트 데이터 디버깅 스크립트
"""
import asyncio
import os
from dotenv import load_dotenv
from kiwoom_api import KiwoomAPI
import pandas as pd

async def debug_stock_data(stock_code: str, stock_name: str):
    """특정 종목의 5분봉 데이터 분석"""
    load_dotenv()
    # KiwoomAPI는 .env에서 자동으로 로드
    api = KiwoomAPI()

    print(f"\n{'='*80}")
    print(f"종목 분석: {stock_name} ({stock_code})")
    print(f"{'='*80}\n")

    # 토큰 발급
    token = api.get_access_token()
    print('✓ 접근 토큰 발급 완료')

    # 5분봉 데이터 수집
    df = await api.fetch_minute_ohlcv(stock_code, interval='5')
    print(f'✓ 5분봉 수집: {len(df)}개\n')

    # 데이터 샘플
    print("📋 데이터 샘플 (최근 10개):")
    print(df[['datetime', 'open', 'high', 'low', 'close', 'volume']].tail(10))

    # 통계 정보
    print(f"\n\n📊 close 가격 통계:")
    print(f"  최소값: {df['close'].min()}")
    print(f"  최대값: {df['close'].max()}")
    print(f"  평균값: {df['close'].mean():.2f}")
    print(f"  중앙값: {df['close'].median():.2f}")
    print(f"  표준편차: {df['close'].std():.2f}")

    # 이상치 확인
    print(f"\n\n🔍 이상치 확인:")
    zero_count = len(df[df['close'] == 0])
    nan_count = df['close'].isna().sum()
    median = df['close'].median()
    small_count = len(df[df['close'] < median * 0.01])

    print(f"  close = 0인 행: {zero_count}개")
    print(f"  close = NaN인 행: {nan_count}개")
    print(f"  close < 중앙값*0.01인 행: {small_count}개")

    if zero_count > 0:
        print(f"\n  ❌ close=0인 데이터 샘플:")
        print(df[df['close'] == 0][['datetime', 'open', 'high', 'low', 'close']].head(10))

    if small_count > 0:
        print(f"\n  ⚠️  close < 중앙값*0.01인 데이터 샘플:")
        print(df[df['close'] < median * 0.01][['datetime', 'open', 'high', 'low', 'close']].head(10))

    # VWAP 시뮬레이션 실행
    print(f"\n\n🔄 VWAP 시뮬레이션 실행...")
    from analyzers.pre_trade_validator import PreTradeValidator
    from utils.config_loader import ConfigLoader

    config = ConfigLoader()
    validator = PreTradeValidator(config)

    # 시뮬레이션 실행
    trades = validator._run_quick_simulation(df)

    print(f"✓ 시뮬레이션 완료: {len(trades)}개 거래 기록")

    if trades:
        print(f"\n📈 거래 기록:")
        for i, trade in enumerate(trades[:5], 1):
            print(f"  [{i}] 진입: {trade['entry_price']:.0f}원 → 청산: {trade['exit_price']:.0f}원 | "
                  f"수익: {trade['profit_pct']:+.2f}% | 보유: {trade['holding_bars']}봉")

        if len(trades) > 5:
            print(f"  ... ({len(trades) - 5}개 거래 더 있음)")
    else:
        print(f"  ⚠️  거래 기록 없음 (시그널 없거나 데이터 부족)")


async def main():
    """메인 실행"""
    # 문제 종목들 분석
    problem_stocks = [
        ('010170', '대한광통신'),
        ('321550', '티움바이오'),
        ('078160', '메디포스트'),
    ]

    for code, name in problem_stocks:
        await debug_stock_data(code, name)
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
