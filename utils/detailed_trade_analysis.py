"""
상세 거래 분석 - 청산 로직 최적화를 위한 데이터 추출

요청사항:
1. 각 트레이드별 상세 로그 (CSV)
2. 최대익/최대손 분포
3. VWAP 청산 트레이드 중 +2% 이상 도달 비율
4. 시간대별 성과
"""
import sys
from pathlib import Path
import pandas as pd
import json
from datetime import datetime, timedelta

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase

db = TradingDatabase(db_path="data/trading.db")

print("=" * 100)
print("📊 상세 거래 분석 - 청산 로직 최적화")
print("=" * 100)

# 1. 전체 거래 조회
trades = db.get_trades()

if not trades:
    print("\n❌ 거래 데이터가 없습니다.")
    exit(0)

print(f"\n총 거래 건수: {len(trades)}건")

# 매수/매도 분리
buys = [t for t in trades if t['trade_type'] == 'BUY']
sells = [t for t in trades if t['trade_type'] == 'SELL']

print(f"  - 매수: {len(buys)}건")
print(f"  - 매도: {len(sells)}건")

# 2. 매수-매도 페어 매칭 (간단한 방식)
print("\n" + "=" * 100)
print("1️⃣  각 트레이드별 상세 로그")
print("=" * 100)

trade_pairs = []

for sell in sells:
    # 같은 종목의 이전 매수 찾기
    matching_buy = None
    for buy in buys:
        if (buy['stock_code'] == sell['stock_code'] and
            buy['trade_time'] <= sell['trade_time']):
            # 가장 가까운 매수 찾기
            if matching_buy is None or buy['trade_time'] > matching_buy['trade_time']:
                matching_buy = buy

    if matching_buy:
        # 진입/청산 시간 파싱
        try:
            entry_time = datetime.fromisoformat(matching_buy['trade_time'])
            exit_time = datetime.fromisoformat(sell['trade_time'])
            holding_minutes = (exit_time - entry_time).total_seconds() / 60
        except:
            entry_time = None
            exit_time = None
            holding_minutes = sell.get('holding_duration', 0) / 60 if sell.get('holding_duration') else 0

        # 바이너리 데이터 안전 변환 (정수로 저장됨)
        def safe_price(price):
            if isinstance(price, bytes):
                import struct
                # 8바이트 정수 (little-endian)
                return float(struct.unpack('<q', price)[0])
            return float(price)

        entry_price = safe_price(matching_buy['price'])
        exit_price = safe_price(sell['price'])
        profit_pct = sell.get('profit_rate', 0)

        # 실제 캔들 데이터에서 최고/최저 계산
        import yfinance as yf
        stock_code = sell['stock_code']

        # 시장 판단 (0으로 시작하면 KOSPI, 아니면 KOSDAQ)
        ticker_suffix = '.KS' if stock_code.startswith('0') else '.KQ'
        ticker = f"{stock_code}{ticker_suffix}"

        # 실제 최대 수익/손실 계산
        try:
            # 거래 기간 데이터 조회
            if entry_time and exit_time:
                # 여유있게 전날부터 다음날까지 조회
                start_date = (entry_time - timedelta(days=1)).strftime('%Y-%m-%d')
                end_date = (exit_time + timedelta(days=1)).strftime('%Y-%m-%d')

                df_candle = yf.download(ticker, start=start_date, end=end_date, interval='1m', progress=False)

                if df_candle is not None and len(df_candle) > 0:
                    # 진입-청산 시간 사이의 캔들만 필터링
                    df_candle = df_candle[(df_candle.index >= entry_time) & (df_candle.index <= exit_time)]

                    if len(df_candle) > 0:
                        highest_price = df_candle['High'].max()
                        lowest_price = df_candle['Low'].min()

                        max_profit_pct = ((highest_price - entry_price) / entry_price) * 100
                        max_loss_pct = ((lowest_price - entry_price) / entry_price) * 100
                    else:
                        # 캔들 없으면 진입/청산가로 추정
                        max_profit_pct = max(profit_pct, 0)
                        max_loss_pct = min(profit_pct, 0)
                else:
                    # 데이터 조회 실패시 진입/청산가로 추정
                    max_profit_pct = max(profit_pct, 0)
                    max_loss_pct = min(profit_pct, 0)
            else:
                # 시간 정보 없으면 청산 수익률로 추정
                max_profit_pct = max(profit_pct, 0)
                max_loss_pct = min(profit_pct, 0)

        except Exception as e:
            # 에러 발생시 청산 수익률로 추정
            max_profit_pct = max(profit_pct, 0)
            max_loss_pct = min(profit_pct, 0)

        pair = {
            'stock_code': sell['stock_code'],
            'stock_name': sell['stock_name'],
            'entry_time': matching_buy['trade_time'],
            'entry_price': entry_price,
            'exit_time': sell['trade_time'],
            'exit_price': exit_price,
            'profit_pct': profit_pct,
            'holding_minutes': holding_minutes,
            'exit_reason': sell.get('exit_reason', 'Unknown'),
            'quantity': sell['quantity'],
            'realized_profit': sell.get('realized_profit', 0),

            # 실제 캔들 데이터 기반
            'max_profit_pct': max_profit_pct,
            'max_loss_pct': max_loss_pct,

            # ATR 등 지표 (없으면 N/A)
            'entry_atr': None,
            'daily_volatility': None,
        }

        trade_pairs.append(pair)

df_pairs = pd.DataFrame(trade_pairs)

if len(df_pairs) == 0:
    print("❌ 매칭된 거래 쌍이 없습니다.")
    exit(0)

print(f"✓ 매칭된 거래 쌍: {len(df_pairs)}건")

# CSV 저장
csv_path = "data/detailed_trade_analysis.csv"
Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
df_pairs.to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f"✓ CSV 저장: {csv_path}")

# 미리보기
print("\n[데이터 미리보기]")
print(df_pairs[['stock_name', 'entry_price', 'exit_price', 'profit_pct',
                 'holding_minutes', 'exit_reason']].head(10).to_string())

# 2. 최대익/최대손 분포
print("\n\n" + "=" * 100)
print("2️⃣  최대 익절/손실 분포")
print("=" * 100)

print("\n[최대 익절 % 히스토그램]")
max_profit_bins = [-999, 0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 999]
max_profit_labels = ['<0%', '0-0.5%', '0.5-1%', '1-1.5%', '1.5-2%', '2-3%', '3-5%', '>5%']
df_pairs['max_profit_bin'] = pd.cut(df_pairs['max_profit_pct'],
                                      bins=max_profit_bins,
                                      labels=max_profit_labels)
max_profit_dist = df_pairs['max_profit_bin'].value_counts().sort_index()

for bin_label, count in max_profit_dist.items():
    pct = count / len(df_pairs) * 100
    bar = '█' * int(pct / 2)
    print(f"  {bin_label:>8}: {count:>3}건 ({pct:>5.1f}%) {bar}")

print("\n[최대 손실 % 히스토그램]")
max_loss_bins = [-999, -5, -3, -2, -1.5, -1.0, -0.5, 0, 999]
max_loss_labels = ['<-5%', '-5~-3%', '-3~-2%', '-2~-1.5%', '-1.5~-1%', '-1~-0.5%', '-0.5~0%', '>0%']
df_pairs['max_loss_bin'] = pd.cut(df_pairs['max_loss_pct'],
                                    bins=max_loss_bins,
                                    labels=max_loss_labels)
max_loss_dist = df_pairs['max_loss_bin'].value_counts().sort_index()

for bin_label, count in max_loss_dist.items():
    pct = count / len(df_pairs) * 100
    bar = '█' * int(pct / 2)
    print(f"  {bin_label:>10}: {count:>3}건 ({pct:>5.1f}%) {bar}")

# 3. VWAP 청산 분석
print("\n\n" + "=" * 100)
print("3️⃣  VWAP 하향 돌파로 청산된 트레이드 분석")
print("=" * 100)

vwap_trades = df_pairs[df_pairs['exit_reason'].str.contains('VWAP', case=False, na=False)]
print(f"\nVWAP 관련 청산: {len(vwap_trades)}건 / 전체 {len(df_pairs)}건 ({len(vwap_trades)/len(df_pairs)*100:.1f}%)")

if len(vwap_trades) > 0:
    # +2% 이상 도달한 비율 (실제)
    reached_2pct = vwap_trades[vwap_trades['max_profit_pct'] >= 2.0]
    print(f"\n📊 VWAP 청산 중 한 번이라도 +2% 이상 도달: {len(reached_2pct)}건 ({len(reached_2pct)/len(vwap_trades)*100:.1f}%)")

    # VWAP 청산의 평균 성과
    print(f"\n[VWAP 청산 트레이드 통계]")
    print(f"  - 평균 수익률: {vwap_trades['profit_pct'].mean():+.2f}%")
    print(f"  - 평균 보유시간: {vwap_trades['holding_minutes'].mean():.1f}분")
    print(f"  - 수익 거래: {len(vwap_trades[vwap_trades['profit_pct'] > 0])}건")
    print(f"  - 손실 거래: {len(vwap_trades[vwap_trades['profit_pct'] < 0])}건")
    print(f"  - 평균 최대 익절: {vwap_trades['max_profit_pct'].mean():+.2f}%")

    print("\n[VWAP 청산 트레이드 상세]")
    vwap_detail = vwap_trades[['stock_name', 'profit_pct', 'max_profit_pct',
                                 'holding_minutes', 'exit_reason']].sort_values('max_profit_pct', ascending=False)
    print(vwap_detail.to_string())

# 4. 시간대별 성과
print("\n\n" + "=" * 100)
print("4️⃣  시간대별 성과 분석")
print("=" * 100)

# 진입 시간 파싱
def parse_hour(time_str):
    try:
        dt = datetime.fromisoformat(time_str)
        return dt.hour
    except:
        return None

df_pairs['entry_hour'] = df_pairs['entry_time'].apply(parse_hour)
df_pairs = df_pairs.dropna(subset=['entry_hour'])

# 시간대 구간 생성
def hour_to_period(hour):
    if 9 <= hour < 10:
        return '09:00-10:00'
    elif 10 <= hour < 11:
        return '10:00-11:00'
    elif 11 <= hour < 12:
        return '11:00-12:00'
    elif 12 <= hour < 13:
        return '12:00-13:00'
    elif 13 <= hour < 14:
        return '13:00-14:00'
    elif 14 <= hour < 15:
        return '14:00-15:00'
    else:
        return 'Other'

df_pairs['time_period'] = df_pairs['entry_hour'].apply(hour_to_period)

print("\n[시간대별 통계]")
print(f"{'시간대':<15} {'거래수':<8} {'평균수익률':<12} {'승률':<10} {'손익비':<10}")
print("-" * 65)

time_periods = ['09:00-10:00', '10:00-11:00', '11:00-12:00', '12:00-13:00', '13:00-14:00', '14:00-15:00']

for period in time_periods:
    period_trades = df_pairs[df_pairs['time_period'] == period]

    if len(period_trades) == 0:
        print(f"{period:<15} {'0건':<8} {'-':<12} {'-':<10} {'-':<10}")
        continue

    count = len(period_trades)
    avg_profit = period_trades['profit_pct'].mean()
    win_rate = (period_trades['profit_pct'] > 0).sum() / len(period_trades) * 100

    # 손익비
    wins = period_trades[period_trades['profit_pct'] > 0]['profit_pct']
    losses = period_trades[period_trades['profit_pct'] < 0]['profit_pct']

    if len(wins) > 0 and len(losses) > 0:
        rr_ratio = wins.mean() / abs(losses.mean())
    elif len(losses) == 0:
        rr_ratio = 99.99  # 손실 없음
    else:
        rr_ratio = 0.0

    print(f"{period:<15} {count:<8} {avg_profit:>+6.2f}%{'':>4} {win_rate:>5.1f}%{'':>3} {rr_ratio:>5.2f}")

# 5. 종목별 성향 (간략)
print("\n\n" + "=" * 100)
print("5️⃣  종목별 성향 (거래 2건 이상)")
print("=" * 100)

stock_stats = df_pairs.groupby('stock_name').agg({
    'profit_pct': ['count', 'mean'],
    'realized_profit': 'sum'
}).round(2)

stock_stats.columns = ['거래수', '평균수익률', '실현손익']
stock_stats = stock_stats[stock_stats['거래수'] >= 2].sort_values('거래수', ascending=False)

print("\n[종목별 통계 (2건 이상)]")
for stock_name, row in stock_stats.head(15).iterrows():
    count = int(row['거래수'])
    avg_rate = row['평균수익률']
    total_profit = row['실현손익']

    # 손익비 계산
    stock_trades = df_pairs[df_pairs['stock_name'] == stock_name]
    wins = stock_trades[stock_trades['profit_pct'] > 0]['profit_pct']
    losses = stock_trades[stock_trades['profit_pct'] < 0]['profit_pct']

    if len(wins) > 0 and len(losses) > 0:
        rr = wins.mean() / abs(losses.mean())
        rr_str = f"RR={rr:.2f}"
    else:
        rr_str = "RR=N/A"

    print(f"  {stock_name:<15}: {count}건, 평균 {avg_rate:>+6.2f}%, 손익 {total_profit:>+9,.0f}원, {rr_str}")

# 6. 핵심 인사이트 요약
print("\n\n" + "=" * 100)
print("🎯 핵심 인사이트 요약")
print("=" * 100)

total_trades = len(df_pairs)
win_trades = df_pairs[df_pairs['profit_pct'] > 0]
loss_trades = df_pairs[df_pairs['profit_pct'] < 0]

print(f"\n📊 전체 통계")
print(f"  - 총 거래: {total_trades}건")
print(f"  - 승률: {len(win_trades)/total_trades*100:.1f}%")
print(f"  - 평균 수익률: {df_pairs['profit_pct'].mean():+.2f}%")
print(f"  - 평균 수익 거래: {win_trades['profit_pct'].mean():+.2f}%")
print(f"  - 평균 손실 거래: {loss_trades['profit_pct'].mean():+.2f}%")
print(f"  - 손익비: {win_trades['profit_pct'].mean() / abs(loss_trades['profit_pct'].mean()):.2f}")

print(f"\n🔍 청산 사유별 분포")
exit_reason_counts = df_pairs['exit_reason'].value_counts()
for reason, count in exit_reason_counts.head(10).items():
    pct = count / total_trades * 100
    print(f"  - {reason}: {count}건 ({pct:.1f}%)")

print(f"\n💡 주요 발견사항")

# 발견 1: VWAP 조기 청산 문제
vwap_early_exits = vwap_trades[
    (vwap_trades['profit_pct'] < 1.5) &
    (vwap_trades['max_profit_pct'] >= 2.0)
]
if len(vwap_early_exits) > 0:
    print(f"  ⚠️  VWAP 조기 청산: {len(vwap_early_exits)}건이 +2% 도달 후 +1.5% 미만에서 청산됨")
    print(f"      → 평균 {vwap_early_exits['profit_pct'].mean():.2f}%에서 청산 (최고 {vwap_early_exits['max_profit_pct'].mean():.2f}%까지 도달)")

# 발견 2: 손실 거래의 보유시간
if len(loss_trades) > 0:
    loss_holding = loss_trades['holding_minutes'].mean()
    win_holding = win_trades['holding_minutes'].mean()
    print(f"  ⚠️  손실 거래 평균 보유시간: {loss_holding:.1f}분 vs 수익 거래: {win_holding:.1f}분")
    if loss_holding > win_holding * 1.2:
        print(f"      → 손실 거래를 더 오래 끌고 있음 (초기 손절 필요)")

# 발견 3: 시간대별 편차
time_stats = []
for period in time_periods:
    period_trades = df_pairs[df_pairs['time_period'] == period]
    if len(period_trades) >= 3:
        time_stats.append((period, period_trades['profit_pct'].mean()))

if len(time_stats) > 0:
    best_period, best_profit = max(time_stats, key=lambda x: x[1])
    worst_period, worst_profit = min(time_stats, key=lambda x: x[1])
    print(f"  ⚠️  시간대별 편차: {best_period} 최고 ({best_profit:+.2f}%), {worst_period} 최저 ({worst_profit:+.2f}%)")

print("\n" + "=" * 100)
print("✅ 분석 완료!")
print("=" * 100)
print(f"\n📁 상세 데이터: {csv_path}")
print("\n🎯 다음 단계:")
print("  1. CSV 파일로 추가 분석")
print("  2. 청산 로직 파라미터 최적화")
print("  3. 시간대별/종목별 필터 적용")
