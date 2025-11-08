"""
간단한 VWAP 백테스트 디버깅
"""
from dotenv import load_dotenv
from analyzers.pre_trade_validator import PreTradeValidator
from utils.config_loader import ConfigLoader
import pandas as pd

load_dotenv()

# 샘플 데이터 생성 (정상적인 가격 데이터)
sample_data = {
    'datetime': ['20251103'] * 200,
    'time': [f"{9 + i//12:02d}{(i%12)*5:02d}00" for i in range(200)],
    'open': [10000 + i * 10 for i in range(200)],
    'high': [10050 + i * 10 for i in range(200)],
    'low': [9950 + i * 10 for i in range(200)],
    'close': [10000 + i * 10 for i in range(200)],
    'volume': [1000] * 200
}

df = pd.DataFrame(sample_data)

print("📊 테스트 데이터 생성:")
print(f"  길이: {len(df)}개 봉")
print(f"  close 범위: {df['close'].min()} ~ {df['close'].max()}")
print(f"\n샘플 (처음 5개):")
print(df[['datetime', 'time', 'close']].head(5))
print(f"\n샘플 (마지막 5개):")
print(df[['datetime', 'time', 'close']].tail(5))

# VWAP 백테스트 실행
print(f"\n" + "="*80)
print("🔄 VWAP 백테스트 실행...")
print("="*80 + "\n")

config = ConfigLoader()
validator = PreTradeValidator(config)

trades = validator._run_quick_simulation(df)

print(f"✅ 시뮬레이션 완료: {len(trades)}개 거래 기록\n")

if trades:
    print("📈 거래 기록:")
    for i, trade in enumerate(trades, 1):
        print(f"  [{i}] 진입: {trade['entry_price']:.0f}원 → 청산: {trade['exit_price']:.0f}원")
        print(f"      수익: {trade['profit']:+.0f}원 ({trade['profit_pct']:+.2f}%) | 보유: {trade['holding_bars']}봉")
        print()

    # 통계 계산
    stats = validator._calculate_stats(trades)
    print(f"\n📊 통계:")
    print(f"  총 거래: {stats['total_trades']}회")
    print(f"  승률: {stats['win_rate']:.1f}%")
    print(f"  평균 수익률: {stats['avg_profit_pct']:+.2f}%")
    print(f"  Profit Factor: {stats['profit_factor']:.2f}")
else:
    print("  ⚠️  거래 기록 없음")
    print("  → 시그널이 생성되지 않았거나 진입/청산 조건이 충족되지 않음")
