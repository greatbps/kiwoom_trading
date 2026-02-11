"""
VWAP 시그널 생성 디버깅
"""
from dotenv import load_dotenv
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from utils.config_loader import ConfigLoader
import pandas as pd

load_dotenv()

# 샘플 데이터 생성 (변동성 있는 가격 데이터)
# VWAP 크로스를 유도하기 위해 상승 후 하락 패턴
sample_data = {
    'datetime': ['20251103'] * 200,
    'time': [f"{9 + i//12:02d}{(i%12)*5:02d}00" for i in range(200)],
    'open': [],
    'high': [],
    'low': [],
    'close': [],
    'volume': []
}

# 가격 패턴: 상승 → 하락 → 재상승
for i in range(200):
    base_price = 10000
    if i < 50:
        # 초반: 횡보
        price = base_price + (i % 10) * 5
    elif i < 100:
        # 중반: 급상승
        price = base_price + (i - 50) * 20
    elif i < 150:
        # 후반: 하락
        price = base_price + 1000 - (i - 100) * 10
    else:
        # 말: 재상승
        price = base_price + 500 + (i - 150) * 15

    sample_data['close'].append(price)
    sample_data['open'].append(price - 5)
    sample_data['high'].append(price + 10)
    sample_data['low'].append(price - 10)
    sample_data['volume'].append(1000 + (i % 100) * 10)

df = pd.DataFrame(sample_data)

print("📊 테스트 데이터 생성:")
print(f"  길이: {len(df)}개 봉")
print(f"  close 범위: {df['close'].min()} ~ {df['close'].max()}")
print(f"\n가격 패턴:")
print(f"  [0-50봉] 횡보: ~{df['close'].iloc[25]:.0f}원")
print(f"  [50-100봉] 상승: ~{df['close'].iloc[75]:.0f}원")
print(f"  [100-150봉] 하락: ~{df['close'].iloc[125]:.0f}원")
print(f"  [150-200봉] 재상승: ~{df['close'].iloc[175]:.0f}원")

# Analyzer 초기화
print(f"\n" + "="*80)
print("🔧 Analyzer 초기화...")
print("="*80 + "\n")

config = ConfigLoader()
analyzer_config = config.get_analyzer_config()
analyzer = EntryTimingAnalyzer(**analyzer_config)

# VWAP 계산
df = analyzer.calculate_vwap(df)
print(f"✅ VWAP 계산 완료")

# ATR 계산
df = analyzer.calculate_atr(df)
print(f"✅ ATR 계산 완료")

# Signal generation config
signal_config = config.get_signal_generation_config()
print(f"\n신호 생성 설정:")
print(f"  volume_ma_period: {signal_config.get('volume_ma_period')}")
print(f"  volume_multiplier: {signal_config.get('volume_multiplier')}")
print(f"  min_distance_from_vwap_pct: {signal_config.get('min_distance_from_vwap_pct')}")

# 시그널 생성
df = analyzer.generate_signals(df, **signal_config)
print(f"\n✅ 시그널 생성 완료")

# 시그널 분석
signal_counts = df['signal'].value_counts()
print(f"\n📊 생성된 시그널 분포:")
for sig, count in signal_counts.items():
    if sig == 1:
        label = "매수"
    elif sig == -1:
        label = "매도"
    else:
        label = "중립"
    print(f"  {label} ({sig}): {count}개")

# 매수 시그널 위치 확인
buy_signals = df[df['signal'] == 1]
if len(buy_signals) > 0:
    print(f"\n✅ 매수 시그널 발견: {len(buy_signals)}개")
    print(f"\n첫 5개 매수 시그널:")
    for idx, row in buy_signals.head(5).iterrows():
        print(f"  [{idx}] 가격: {row['close']:.0f}원 | VWAP: {row.get('vwap', 0):.0f}원 | "
              f"Volume: {row.get('volume', 0)}")
else:
    print(f"\n❌ 매수 시그널 없음!")
    print(f"\n샘플 데이터 (처음 10봉):")
    cols_to_show = ['close', 'vwap', 'signal']
    if 'vwap' in df.columns:
        print(df[cols_to_show].head(10))
    else:
        print("  ⚠️  VWAP 컬럼 없음!")
        print(df[['close', 'volume', 'signal']].head(10))

# 매도 시그널 위치 확인
sell_signals = df[df['signal'] == -1]
if len(sell_signals) > 0:
    print(f"\n✅ 매도 시그널 발견: {len(sell_signals)}개")
    print(f"\n첫 5개 매도 시그널:")
    for idx, row in sell_signals.head(5).iterrows():
        print(f"  [{idx}] 가격: {row['close']:.0f}원 | VWAP: {row.get('vwap', 0):.0f}원 | "
              f"Volume: {row.get('volume', 0)}")
