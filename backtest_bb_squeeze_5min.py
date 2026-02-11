#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BB(30,1) + Squeeze Momentum 5분봉 백테스트

목적: BB(30,1)이 설계된 타임프레임(5분봉)에서 유효한지 검증
비교: Squeeze Only vs BB(30,1) + Squeeze
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os
from dataclasses import dataclass
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.squeeze_momentum import calculate_squeeze_momentum


@dataclass
class Trade:
    """거래 기록"""
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    profit_pct: float
    reason: str


def generate_intraday_from_daily(daily_df: pd.DataFrame, bars_per_day: int = 78) -> pd.DataFrame:
    """
    일봉 데이터에서 리얼리스틱한 5분봉 데이터 생성

    Args:
        daily_df: 일봉 OHLCV 데이터
        bars_per_day: 하루 봉 수 (9:00-15:30 = 6.5시간 = 78개 5분봉)

    Returns:
        5분봉 데이터프레임
    """
    intraday_data = []

    for idx, row in daily_df.iterrows():
        day_open = row['open']
        day_high = row['high']
        day_low = row['low']
        day_close = row['close']
        day_volume = row['volume']

        # 하루 범위
        day_range = day_high - day_low
        if day_range == 0:
            day_range = day_open * 0.01  # 최소 1% 변동

        # 일중 가격 패턴 생성 (실제 시장 패턴 모사)
        # 9:00-10:00: 높은 변동성 (오프닝)
        # 10:00-14:00: 점심 시간 포함 낮은 변동성
        # 14:00-15:30: 마감 랠리/조정

        prices = []
        current_price = day_open

        # 랜덤 워크 + 추세로 가격 생성
        trend_direction = 1 if day_close > day_open else -1

        for i in range(bars_per_day):
            # 시간대별 변동성 조정
            if i < 12:  # 첫 1시간 (고변동)
                volatility = 0.4
            elif i < 60:  # 중간 4시간 (저변동)
                volatility = 0.2
            else:  # 마지막 1.5시간 (중변동)
                volatility = 0.3

            # 추세 반영 + 랜덤
            trend_pull = trend_direction * (day_range / bars_per_day) * 0.3
            random_move = np.random.randn() * (day_range / bars_per_day) * volatility

            current_price = current_price + trend_pull + random_move

            # 범위 내로 제한
            current_price = max(day_low, min(day_high, current_price))
            prices.append(current_price)

        # 마지막 가격을 종가로 조정
        prices[-1] = day_close

        # 5분봉 OHLCV 생성
        for i in range(bars_per_day):
            bar_time = idx + timedelta(hours=9, minutes=i*5)

            if i == 0:
                bar_open = day_open
            else:
                bar_open = prices[i-1]

            bar_close = prices[i]

            # 봉 내 고가/저가 (랜덤 스파이크)
            spike = abs(np.random.randn()) * (day_range / bars_per_day) * 0.3
            bar_high = max(bar_open, bar_close) + spike
            bar_low = min(bar_open, bar_close) - spike

            # 일봉 범위 내로 제한
            bar_high = min(bar_high, day_high)
            bar_low = max(bar_low, day_low)

            # 거래량 분배 (U자형 패턴)
            if i < 12 or i >= 66:
                vol_weight = 0.02
            else:
                vol_weight = 0.008
            bar_volume = int(day_volume * vol_weight)

            intraday_data.append({
                'datetime': bar_time,
                'open': bar_open,
                'high': bar_high,
                'low': bar_low,
                'close': bar_close,
                'volume': bar_volume
            })

    df = pd.DataFrame(intraday_data)
    df.set_index('datetime', inplace=True)
    return df


class BB5MinBacktester:
    """5분봉 BB(30,1) + Squeeze 백테스터"""

    def __init__(
        self,
        bb_length: int = 30,
        bb_std: float = 1.0,
        min_squeeze_bars: int = 5,
        stop_loss_pct: float = -1.5,
        take_profit_pct: float = 2.5,
        max_hold_bars: int = 36  # 최대 3시간 보유
    ):
        self.bb_length = bb_length
        self.bb_std = bb_std
        self.min_squeeze_bars = min_squeeze_bars
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_bars = max_hold_bars

        self.trades: List[Trade] = []
        self.position = None

    def calculate_bb(self, df: pd.DataFrame) -> pd.DataFrame:
        """볼린저 밴드 계산"""
        df = df.copy()
        df['bb_mid'] = df['close'].rolling(window=self.bb_length).mean()
        df['bb_std'] = df['close'].rolling(window=self.bb_length).std()
        df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * self.bb_std)
        df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * self.bb_std)
        return df

    def count_consecutive_squeeze(self, df: pd.DataFrame, idx: int) -> int:
        """연속 스퀴즈 봉수"""
        count = 0
        for i in range(idx, -1, -1):
            if df.iloc[i].get('sqz_on', False):
                count += 1
            else:
                break
        return count

    def check_squeeze_entry(self, df: pd.DataFrame, idx: int) -> bool:
        """Squeeze Only 진입"""
        if idx < 20:
            return False

        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        if not row.get('sqz_on', False):
            return False

        momentum = row.get('sqz_momentum', 0)
        prev_momentum = prev_row.get('sqz_momentum', 0)

        if momentum <= 0 or momentum <= prev_momentum:
            return False

        if self.count_consecutive_squeeze(df, idx) < self.min_squeeze_bars:
            return False

        return True

    def check_bb_squeeze_entry(self, df: pd.DataFrame, idx: int) -> bool:
        """BB(30,1) + Squeeze 진입"""
        if idx < max(20, self.bb_length):
            return False

        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        # 스퀴즈 조건
        if not row.get('sqz_on', False):
            return False
        if self.count_consecutive_squeeze(df, idx) < self.min_squeeze_bars:
            return False

        # 모멘텀 조건
        momentum = row.get('sqz_momentum', 0)
        prev_momentum = prev_row.get('sqz_momentum', 0)
        if momentum <= 0 or momentum <= prev_momentum:
            return False

        # BB(30,1) 상단 돌파 조건
        close = row['close']
        bb_upper = row.get('bb_upper', 0)
        if pd.isna(bb_upper) or bb_upper == 0:
            return False
        if close <= bb_upper:
            return False

        return True

    def run_backtest(self, df: pd.DataFrame, strategy: str = 'bb_squeeze') -> Dict:
        """백테스트 실행"""
        # 지표 계산
        df = calculate_squeeze_momentum(df)
        df = self.calculate_bb(df)

        self.trades = []
        self.position = None

        for idx in range(max(30, self.bb_length), len(df)):
            row = df.iloc[idx]
            current_price = row['close']
            current_time = df.index[idx]

            # 당일 마감 시간 체크 (15:20 이후 청산)
            if hasattr(current_time, 'hour'):
                if current_time.hour >= 15 and current_time.minute >= 20:
                    if self.position:
                        profit_pct = ((current_price - self.position['entry_price'])
                                      / self.position['entry_price']) * 100
                        self.trades.append(Trade(
                            entry_time=self.position['entry_time'],
                            exit_time=current_time,
                            entry_price=self.position['entry_price'],
                            exit_price=current_price,
                            profit_pct=profit_pct,
                            reason="DAY_END"
                        ))
                        self.position = None
                    continue

            # 포지션 없을 때
            if self.position is None:
                entry_signal = False

                if strategy == 'squeeze_only':
                    entry_signal = self.check_squeeze_entry(df, idx)
                elif strategy == 'bb_squeeze':
                    entry_signal = self.check_bb_squeeze_entry(df, idx)

                if entry_signal:
                    self.position = {
                        'entry_price': current_price,
                        'entry_time': current_time,
                        'entry_idx': idx
                    }

            # 포지션 있을 때
            else:
                profit_pct = ((current_price - self.position['entry_price'])
                              / self.position['entry_price']) * 100
                hold_bars = idx - self.position['entry_idx']

                exit_signal = False
                exit_reason = ""

                # 손절
                if profit_pct <= self.stop_loss_pct:
                    exit_signal = True
                    exit_reason = "STOP_LOSS"

                # 익절
                elif profit_pct >= self.take_profit_pct:
                    exit_signal = True
                    exit_reason = "TAKE_PROFIT"

                # 최대 보유 시간 초과
                elif hold_bars >= self.max_hold_bars:
                    exit_signal = True
                    exit_reason = "MAX_HOLD"

                # BB 중심선 하향 돌파
                elif current_price < row.get('bb_mid', current_price):
                    if hold_bars >= 6:  # 최소 30분 보유
                        exit_signal = True
                        exit_reason = "BB_MID_BREAK"

                if exit_signal:
                    self.trades.append(Trade(
                        entry_time=self.position['entry_time'],
                        exit_time=current_time,
                        entry_price=self.position['entry_price'],
                        exit_price=current_price,
                        profit_pct=profit_pct,
                        reason=exit_reason
                    ))
                    self.position = None

        return self._calculate_performance()

    def _calculate_performance(self) -> Dict:
        """성과 계산"""
        total_trades = len(self.trades)

        if total_trades == 0:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'total_return': 0.0,
                'profit_factor': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'max_drawdown': 0.0,
                'trades': []
            }

        wins = [t for t in self.trades if t.profit_pct > 0]
        losses = [t for t in self.trades if t.profit_pct <= 0]

        win_rate = len(wins) / total_trades * 100
        total_return = sum(t.profit_pct for t in self.trades)

        avg_win = sum(t.profit_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.profit_pct for t in losses) / len(losses) if losses else 0

        gross_profit = sum(t.profit_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.profit_pct for t in losses)) if losses else 0.001
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # MDD 계산
        equity = 100
        peak = 100
        max_dd = 0
        for t in self.trades:
            equity *= (1 + t.profit_pct / 100)
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_return': total_return,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_drawdown': max_dd,
            'trades': self.trades
        }


def load_daily_data(stock_code: str, days: int = 100) -> pd.DataFrame:
    """pykrx로 일봉 데이터 로드"""
    try:
        from pykrx import stock

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        df = stock.get_market_ohlcv_by_date(
            start_date.strftime('%Y%m%d'),
            end_date.strftime('%Y%m%d'),
            stock_code
        )

        if df is None or df.empty:
            return pd.DataFrame()

        if '시가' in df.columns:
            df = df[['시가', '고가', '저가', '종가', '거래량']]
            df.columns = ['open', 'high', 'low', 'close', 'volume']

        return df

    except Exception as e:
        print(f"  Error loading {stock_code}: {e}")
        return pd.DataFrame()


def run_5min_backtest():
    """5분봉 백테스트 메인"""

    print("=" * 100)
    print("BB(30,1) + Squeeze Momentum 5분봉 백테스트")
    print("=" * 100)
    print("목적: BB(30,1)이 설계된 타임프레임(5분봉)에서 유효한지 검증")
    print()

    # 테스트 종목 (중소형 변동성 종목)
    test_stocks = {
        "250060": "모비스",
        "012790": "삼보모터스",
        "215600": "오름테라퓨틱",
        "090710": "휴림로봇",
        "009520": "포스코엠텍",
        "084690": "대상홀딩스",
        "042700": "한미반도체",
        "003670": "포스코퓨처엠",
        "086520": "에코프로",
        "247540": "에코프로비엠"
    }

    squeeze_results = []
    bb_squeeze_results = []

    for code, name in test_stocks.items():
        print(f"\n📊 {name} ({code})")
        print("-" * 70)

        # 일봉 데이터 로드
        daily_df = load_daily_data(code, days=60)
        if daily_df.empty or len(daily_df) < 20:
            print("  ⚠️ 데이터 부족")
            continue

        # 5분봉 생성
        df_5min = generate_intraday_from_daily(daily_df)
        print(f"  5분봉 데이터: {len(df_5min)}개 ({len(daily_df)}일)")

        # Squeeze Only
        bt1 = BB5MinBacktester(min_squeeze_bars=5)
        result1 = bt1.run_backtest(df_5min.copy(), strategy='squeeze_only')
        squeeze_results.append(result1)

        # BB(30,1) + Squeeze
        bt2 = BB5MinBacktester(min_squeeze_bars=5)
        result2 = bt2.run_backtest(df_5min.copy(), strategy='bb_squeeze')
        bb_squeeze_results.append(result2)

        print(f"  Squeeze Only: {result1['total_trades']:>3}건, "
              f"승률 {result1['win_rate']:>5.1f}%, "
              f"수익 {result1['total_return']:>+7.2f}%, "
              f"PF {result1['profit_factor']:.2f}")
        print(f"  BB+Squeeze:   {result2['total_trades']:>3}건, "
              f"승률 {result2['win_rate']:>5.1f}%, "
              f"수익 {result2['total_return']:>+7.2f}%, "
              f"PF {result2['profit_factor']:.2f}")

    # 종합 결과
    print("\n" + "=" * 100)
    print("📈 5분봉 종합 결과")
    print("=" * 100)

    def aggregate(results):
        total_trades = sum(r['total_trades'] for r in results)
        if total_trades == 0:
            return {'trades': 0, 'win_rate': 0, 'return': 0, 'pf': 0,
                    'avg_win': 0, 'avg_loss': 0, 'mdd': 0}

        all_trades = []
        for r in results:
            all_trades.extend(r.get('trades', []))

        if not all_trades:
            return {'trades': 0, 'win_rate': 0, 'return': 0, 'pf': 0,
                    'avg_win': 0, 'avg_loss': 0, 'mdd': 0}

        wins = [t for t in all_trades if t.profit_pct > 0]
        losses = [t for t in all_trades if t.profit_pct <= 0]

        win_rate = len(wins) / len(all_trades) * 100
        total_return = sum(t.profit_pct for t in all_trades)
        avg_win = sum(t.profit_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.profit_pct for t in losses) / len(losses) if losses else 0

        gross_profit = sum(t.profit_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.profit_pct for t in losses)) if losses else 0.001
        pf = gross_profit / gross_loss if gross_loss > 0 else 0

        max_mdd = max((r['max_drawdown'] for r in results), default=0)

        return {
            'trades': len(all_trades),
            'win_rate': win_rate,
            'return': total_return,
            'pf': pf,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'mdd': max_mdd
        }

    agg1 = aggregate(squeeze_results)
    agg2 = aggregate(bb_squeeze_results)

    print(f"\n{'전략':<20} {'거래':<8} {'승률':<10} {'수익':<12} {'PF':<8} {'MDD':<8}")
    print("-" * 70)
    print(f"{'Squeeze Only':<20} {agg1['trades']:<8} {agg1['win_rate']:.1f}%{'':<4} "
          f"{agg1['return']:+.2f}%{'':<4} {agg1['pf']:.2f}{'':<4} {agg1['mdd']:.1f}%")
    print(f"{'BB(30,1)+Squeeze':<20} {agg2['trades']:<8} {agg2['win_rate']:.1f}%{'':<4} "
          f"{agg2['return']:+.2f}%{'':<4} {agg2['pf']:.2f}{'':<4} {agg2['mdd']:.1f}%")

    # 비교 분석
    print("\n" + "=" * 100)
    print("📊 BB(30,1) 필터 효과 분석 (5분봉)")
    print("=" * 100)

    if agg1['trades'] > 0 and agg2['trades'] > 0:
        trade_reduction = (1 - agg2['trades'] / agg1['trades']) * 100
        wr_diff = agg2['win_rate'] - agg1['win_rate']
        ret_diff = agg2['return'] - agg1['return']
        pf_diff = agg2['pf'] - agg1['pf']
        mdd_diff = agg2['mdd'] - agg1['mdd']

        print(f"\n거래 감소율: {trade_reduction:.1f}% ({agg1['trades']}건 → {agg2['trades']}건)")
        print(f"승률 변화:   {wr_diff:+.1f}%p")
        print(f"수익 변화:   {ret_diff:+.2f}%")
        print(f"PF 변화:     {pf_diff:+.2f}")
        print(f"MDD 변화:    {mdd_diff:+.1f}%p")

        # 판정
        print("\n" + "=" * 100)
        print("🔥 최종 판정")
        print("=" * 100)

        # 성공 기준: 거래 감소 + (승률 유지 or PF 상승 or MDD 감소)
        is_success = (
            agg2['trades'] < agg1['trades'] and  # 거래 감소
            (agg2['pf'] >= agg1['pf'] or         # PF 유지/상승
             agg2['mdd'] <= agg1['mdd'] or       # MDD 감소
             agg2['win_rate'] >= agg1['win_rate'])  # 승률 유지/상승
        )

        if is_success:
            print("\n✅ BB(30,1)은 5분봉에서 유효한 '진입 필터'로 작동")
            print("   → entry_mode: bb_squeeze 채택 권장")
            print("   → 거래 품질 향상 (노이즈 필터링)")
        else:
            print("\n❌ BB(30,1)은 5분봉에서도 효과 미미")
            print("   → BB(30,1) 전략 폐기 권장")
            print("   → Squeeze Only 유지")

    else:
        print("\n⚠️ 거래 수 부족으로 통계적 판단 어려움")

    return agg1, agg2


def run_parameter_test():
    """파라미터별 5분봉 테스트"""

    print("\n" + "=" * 100)
    print("🔧 5분봉 파라미터 최적화")
    print("=" * 100)

    test_stocks = {
        "250060": "모비스",
        "012790": "삼보모터스",
        "042700": "한미반도체",
        "086520": "에코프로"
    }

    params = [
        {'bb_length': 20, 'bb_std': 1.0, 'min_squeeze_bars': 3},
        {'bb_length': 30, 'bb_std': 1.0, 'min_squeeze_bars': 3},
        {'bb_length': 30, 'bb_std': 1.0, 'min_squeeze_bars': 5},
        {'bb_length': 30, 'bb_std': 1.5, 'min_squeeze_bars': 5},
        {'bb_length': 40, 'bb_std': 1.0, 'min_squeeze_bars': 5},
    ]

    results = []

    for p in params:
        label = f"BB({p['bb_length']},{p['bb_std']}), Sqz>={p['min_squeeze_bars']}"
        all_trades = []

        for code, name in test_stocks.items():
            daily_df = load_daily_data(code, days=60)
            if daily_df.empty or len(daily_df) < 20:
                continue

            df_5min = generate_intraday_from_daily(daily_df)

            bt = BB5MinBacktester(
                bb_length=p['bb_length'],
                bb_std=p['bb_std'],
                min_squeeze_bars=p['min_squeeze_bars']
            )
            result = bt.run_backtest(df_5min.copy(), strategy='bb_squeeze')
            all_trades.extend(result.get('trades', []))

        if all_trades:
            wins = [t for t in all_trades if t.profit_pct > 0]
            losses = [t for t in all_trades if t.profit_pct <= 0]

            win_rate = len(wins) / len(all_trades) * 100
            total_return = sum(t.profit_pct for t in all_trades)

            gp = sum(t.profit_pct for t in wins) if wins else 0
            gl = abs(sum(t.profit_pct for t in losses)) if losses else 0.001
            pf = gp / gl if gl > 0 else 0

            results.append({
                'label': label,
                'trades': len(all_trades),
                'win_rate': win_rate,
                'return': total_return,
                'pf': pf
            })
        else:
            results.append({
                'label': label,
                'trades': 0,
                'win_rate': 0,
                'return': 0,
                'pf': 0
            })

    print(f"\n{'파라미터':<35} {'거래':<8} {'승률':<10} {'수익':<12} {'PF':<8}")
    print("-" * 80)

    for r in sorted(results, key=lambda x: x['return'], reverse=True):
        print(f"{r['label']:<35} {r['trades']:<8} {r['win_rate']:.1f}%{'':<4} "
              f"{r['return']:+.2f}%{'':<4} {r['pf']:.2f}")

    if results:
        best = max(results, key=lambda x: x['return'])
        print(f"\n🏆 최적: {best['label']}")


if __name__ == "__main__":
    # 5분봉 백테스트
    run_5min_backtest()

    # 파라미터 최적화
    run_parameter_test()
