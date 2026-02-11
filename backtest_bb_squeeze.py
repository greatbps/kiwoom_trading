#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BB(30,1) + Squeeze Momentum 통합 전략 백테스트

비교 대상:
- Squeeze Only: 기존 스퀴즈 모멘텀만 사용
- BB(30,1) + Squeeze: BB(30,1) 돌파 + 스퀴즈 필터 결합
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.squeeze_momentum import calculate_squeeze_momentum


@dataclass
class TradeResult:
    """거래 결과"""
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    profit_pct: float
    reason: str


class BBSqueezeBacktester:
    """BB(30,1) + Squeeze Momentum 백테스터"""

    def __init__(
        self,
        initial_capital: float = 10000000,
        bb_length: int = 30,
        bb_std: float = 1.0,
        min_squeeze_bars: int = 5,
        stop_loss_pct: float = -2.0,
        take_profit_pct: float = 3.0
    ):
        self.initial_capital = initial_capital
        self.bb_length = bb_length
        self.bb_std = bb_std
        self.min_squeeze_bars = min_squeeze_bars
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

        self.capital = initial_capital
        self.position = None
        self.trades: List[TradeResult] = []

    def calculate_bb(self, df: pd.DataFrame) -> pd.DataFrame:
        """볼린저 밴드 계산"""
        df = df.copy()
        df['bb_mid'] = df['close'].rolling(window=self.bb_length).mean()
        df['bb_std'] = df['close'].rolling(window=self.bb_length).std()
        df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * self.bb_std)
        df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * self.bb_std)
        return df

    def count_consecutive_squeeze(self, df: pd.DataFrame, idx: int) -> int:
        """연속 스퀴즈 봉수 계산"""
        count = 0
        for i in range(idx, -1, -1):
            # sqz_on 컬럼 사용 (calculate_squeeze_momentum 반환값)
            if df.iloc[i].get('sqz_on', False):
                count += 1
            else:
                break
        return count

    def check_squeeze_entry(self, df: pd.DataFrame, idx: int) -> bool:
        """Squeeze Only 진입 조건"""
        if idx < 20:
            return False

        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        # 스퀴즈 ON 상태 (sqz_on 컬럼 사용)
        if not row.get('sqz_on', False):
            return False

        # 모멘텀 양수 및 증가 (sqz_momentum 컬럼 사용)
        momentum = row.get('sqz_momentum', 0)
        prev_momentum = prev_row.get('sqz_momentum', 0)

        if momentum <= 0:
            return False
        if momentum <= prev_momentum:
            return False

        # 연속 스퀴즈 봉수 체크
        if self.count_consecutive_squeeze(df, idx) < self.min_squeeze_bars:
            return False

        return True

    def check_bb_squeeze_entry(self, df: pd.DataFrame, idx: int) -> bool:
        """BB(30,1) + Squeeze 진입 조건"""
        if idx < max(20, self.bb_length):
            return False

        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        # 1. 스퀴즈 ON 상태 (연속 min_squeeze_bars 이상)
        if not row.get('sqz_on', False):
            return False
        if self.count_consecutive_squeeze(df, idx) < self.min_squeeze_bars:
            return False

        # 2. 모멘텀 양수 및 증가 (sqz_momentum 컬럼 사용)
        momentum = row.get('sqz_momentum', 0)
        prev_momentum = prev_row.get('sqz_momentum', 0)

        if momentum <= 0:
            return False
        if momentum <= prev_momentum:
            return False

        # 3. BB(30,1) 상단 돌파 (핵심 조건)
        close = row['close']
        bb_upper = row.get('bb_upper', 0)

        if pd.isna(bb_upper) or bb_upper == 0:
            return False
        if close <= bb_upper:
            return False

        return True

    def run_backtest(
        self,
        df: pd.DataFrame,
        strategy: str = 'bb_squeeze'  # 'squeeze_only' or 'bb_squeeze'
    ) -> Dict:
        """백테스트 실행"""
        # 지표 계산
        df = calculate_squeeze_momentum(df)
        df = self.calculate_bb(df)

        # 초기화
        self.capital = self.initial_capital
        self.position = None
        self.trades = []

        for idx in range(max(30, self.bb_length), len(df)):
            row = df.iloc[idx]
            current_price = row['close']
            current_date = df.index[idx] if hasattr(df.index[idx], 'strftime') else idx

            # 포지션 없을 때: 진입 확인
            if self.position is None:
                entry_signal = False

                if strategy == 'squeeze_only':
                    entry_signal = self.check_squeeze_entry(df, idx)
                elif strategy == 'bb_squeeze':
                    entry_signal = self.check_bb_squeeze_entry(df, idx)

                if entry_signal:
                    self.position = {
                        'entry_price': current_price,
                        'entry_date': current_date,
                        'entry_idx': idx
                    }

            # 포지션 있을 때: 청산 확인
            else:
                profit_pct = ((current_price - self.position['entry_price'])
                              / self.position['entry_price']) * 100

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

                # BB 중심선 하향 돌파 (추가 청산 조건)
                elif current_price < row.get('bb_mid', current_price):
                    if idx - self.position['entry_idx'] >= 3:  # 최소 3봉 유지
                        exit_signal = True
                        exit_reason = "BB_MID_BREAK"

                if exit_signal:
                    self.trades.append(TradeResult(
                        entry_date=self.position['entry_date'],
                        exit_date=current_date,
                        entry_price=self.position['entry_price'],
                        exit_price=current_price,
                        profit_pct=profit_pct,
                        reason=exit_reason
                    ))
                    self.position = None

        # 마지막 포지션 청산
        if self.position is not None:
            last_row = df.iloc[-1]
            profit_pct = ((last_row['close'] - self.position['entry_price'])
                          / self.position['entry_price']) * 100
            self.trades.append(TradeResult(
                entry_date=self.position['entry_date'],
                exit_date=df.index[-1],
                entry_price=self.position['entry_price'],
                exit_price=last_row['close'],
                profit_pct=profit_pct,
                reason="FINAL_EXIT"
            ))

        return self._calculate_performance()

    def _calculate_performance(self) -> Dict:
        """성과 계산"""
        total_trades = len(self.trades)

        if total_trades == 0:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'total_return': 0.0,
                'avg_profit': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'profit_factor': 0.0,
                'max_consecutive_loss': 0
            }

        # 승패 분류
        wins = [t for t in self.trades if t.profit_pct > 0]
        losses = [t for t in self.trades if t.profit_pct <= 0]

        win_rate = len(wins) / total_trades * 100
        total_return = sum(t.profit_pct for t in self.trades)
        avg_profit = total_return / total_trades

        avg_win = sum(t.profit_pct for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.profit_pct for t in losses) / len(losses) if losses else 0.0

        # Profit Factor 계산
        gross_profit = sum(t.profit_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.profit_pct for t in losses)) if losses else 0.001
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

        # 최대 연속 손실
        max_consecutive_loss = 0
        current_loss_streak = 0
        for t in self.trades:
            if t.profit_pct <= 0:
                current_loss_streak += 1
                max_consecutive_loss = max(max_consecutive_loss, current_loss_streak)
            else:
                current_loss_streak = 0

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_return': total_return,
            'avg_profit': avg_profit,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_consecutive_loss': max_consecutive_loss,
            'trades': self.trades
        }


def load_stock_data(stock_code: str, days: int = 200) -> pd.DataFrame:
    """pykrx로 주식 데이터 로드"""
    try:
        from pykrx import stock

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        df = stock.get_market_ohlcv_by_date(start_str, end_str, stock_code)

        if df is None or df.empty:
            return pd.DataFrame()

        # 컬럼명 변환
        if '시가' in df.columns:
            df = df[['시가', '고가', '저가', '종가', '거래량']]
            df.columns = ['open', 'high', 'low', 'close', 'volume']
        elif len(df.columns) >= 5:
            df = df.iloc[:, :5]
            df.columns = ['open', 'high', 'low', 'close', 'volume']

        return df

    except Exception as e:
        print(f"  ❌ Error loading {stock_code}: {e}")
        return pd.DataFrame()


def run_comparison_backtest(stocks: Dict[str, str], days: int = 200):
    """두 전략 비교 백테스트"""

    print("=" * 90)
    print("BB(30,1) + Squeeze Momentum 전략 백테스트")
    print("=" * 90)
    print(f"테스트 기간: 최근 {days}일")
    print(f"비교: Squeeze Only vs BB(30,1) + Squeeze")
    print()

    squeeze_results = []
    bb_squeeze_results = []

    for code, name in stocks.items():
        print(f"\n📊 {name} ({code})")
        print("-" * 60)

        df = load_stock_data(code, days)
        if df.empty or len(df) < 50:
            print(f"  ⚠️ 데이터 부족 - 건너뜀")
            continue

        print(f"  데이터: {len(df)}일")

        # Squeeze Only
        bt1 = BBSqueezeBacktester()
        result1 = bt1.run_backtest(df.copy(), strategy='squeeze_only')
        squeeze_results.append(result1)

        # BB(30,1) + Squeeze
        bt2 = BBSqueezeBacktester()
        result2 = bt2.run_backtest(df.copy(), strategy='bb_squeeze')
        bb_squeeze_results.append(result2)

        print(f"  Squeeze Only: {result1['total_trades']}건, "
              f"승률 {result1['win_rate']:.1f}%, 수익 {result1['total_return']:+.2f}%")
        print(f"  BB+Squeeze:   {result2['total_trades']}건, "
              f"승률 {result2['win_rate']:.1f}%, 수익 {result2['total_return']:+.2f}%")

    # 종합 결과
    print("\n" + "=" * 90)
    print("📈 종합 결과")
    print("=" * 90)

    def aggregate_results(results: List[Dict]) -> Dict:
        """결과 집계"""
        total_trades = sum(r['total_trades'] for r in results)
        if total_trades == 0:
            return {'total_trades': 0, 'win_rate': 0, 'total_return': 0,
                    'avg_win': 0, 'avg_loss': 0, 'profit_factor': 0}

        all_trades = []
        for r in results:
            if 'trades' in r:
                all_trades.extend(r['trades'])

        wins = [t for t in all_trades if t.profit_pct > 0]
        losses = [t for t in all_trades if t.profit_pct <= 0]

        win_rate = len(wins) / len(all_trades) * 100 if all_trades else 0
        total_return = sum(t.profit_pct for t in all_trades)
        avg_win = sum(t.profit_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.profit_pct for t in losses) / len(losses) if losses else 0

        gross_profit = sum(t.profit_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.profit_pct for t in losses)) if losses else 0.001
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_return': total_return,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor
        }

    agg1 = aggregate_results(squeeze_results)
    agg2 = aggregate_results(bb_squeeze_results)

    print(f"\n{'전략':<20} {'거래':<8} {'승률':<10} {'누적수익':<12} {'PF':<8} {'평균승':<10} {'평균패':<10}")
    print("-" * 90)
    print(f"{'Squeeze Only':<20} {agg1['total_trades']:<8} {agg1['win_rate']:.1f}%{'':<5} "
          f"{agg1['total_return']:+.2f}%{'':<5} {agg1['profit_factor']:.2f}{'':<4} "
          f"{agg1['avg_win']:+.2f}%{'':<4} {agg1['avg_loss']:+.2f}%")
    print(f"{'BB(30,1)+Squeeze':<20} {agg2['total_trades']:<8} {agg2['win_rate']:.1f}%{'':<5} "
          f"{agg2['total_return']:+.2f}%{'':<5} {agg2['profit_factor']:.2f}{'':<4} "
          f"{agg2['avg_win']:+.2f}%{'':<4} {agg2['avg_loss']:+.2f}%")

    print("\n" + "=" * 90)

    # 개선도
    if agg1['total_trades'] > 0 and agg2['total_trades'] > 0:
        wr_diff = agg2['win_rate'] - agg1['win_rate']
        ret_diff = agg2['total_return'] - agg1['total_return']
        pf_diff = agg2['profit_factor'] - agg1['profit_factor']

        print(f"📊 BB(30,1) 결합 효과:")
        print(f"   승률 변화: {wr_diff:+.1f}%p")
        print(f"   수익 변화: {ret_diff:+.2f}%")
        print(f"   PF 변화:   {pf_diff:+.2f}")

    return agg1, agg2


def run_parameter_optimization():
    """파라미터 최적화 백테스트"""

    print("\n" + "=" * 90)
    print("🔧 파라미터 최적화 백테스트")
    print("=" * 90)

    # 테스트 종목 (중소형 변동성 종목)
    test_stocks = {
        "250060": "모비스",
        "012790": "삼보모터스",
        "115960": "아이티센글로벌",
        "215600": "오름테라퓨틱",
        "215560": "재영솔루텍",
        "090710": "휴림로봇",
        "042700": "한미반도체"
    }

    # 파라미터 조합
    param_sets = [
        {'bb_length': 20, 'bb_std': 1.0, 'min_squeeze_bars': 3},
        {'bb_length': 20, 'bb_std': 1.5, 'min_squeeze_bars': 3},
        {'bb_length': 30, 'bb_std': 1.0, 'min_squeeze_bars': 3},
        {'bb_length': 30, 'bb_std': 1.0, 'min_squeeze_bars': 5},
        {'bb_length': 30, 'bb_std': 1.5, 'min_squeeze_bars': 5},
        {'bb_length': 40, 'bb_std': 1.0, 'min_squeeze_bars': 5},
    ]

    results = []

    for params in param_sets:
        param_label = f"BB({params['bb_length']},{params['bb_std']}), Squeeze>={params['min_squeeze_bars']}"
        print(f"\n테스트: {param_label}")

        all_trades = []

        for code, name in test_stocks.items():
            df = load_stock_data(code, days=200)
            if df.empty or len(df) < 50:
                continue

            bt = BBSqueezeBacktester(
                bb_length=params['bb_length'],
                bb_std=params['bb_std'],
                min_squeeze_bars=params['min_squeeze_bars']
            )
            result = bt.run_backtest(df.copy(), strategy='bb_squeeze')

            if 'trades' in result:
                all_trades.extend(result['trades'])

        # 집계
        if all_trades:
            wins = [t for t in all_trades if t.profit_pct > 0]
            losses = [t for t in all_trades if t.profit_pct <= 0]

            win_rate = len(wins) / len(all_trades) * 100
            total_return = sum(t.profit_pct for t in all_trades)
            avg_win = sum(t.profit_pct for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t.profit_pct for t in losses) / len(losses) if losses else 0

            gross_profit = sum(t.profit_pct for t in wins) if wins else 0
            gross_loss = abs(sum(t.profit_pct for t in losses)) if losses else 0.001
            pf = gross_profit / gross_loss if gross_loss > 0 else 0

            results.append({
                'params': param_label,
                'trades': len(all_trades),
                'win_rate': win_rate,
                'total_return': total_return,
                'profit_factor': pf,
                'avg_win': avg_win,
                'avg_loss': avg_loss
            })
        else:
            results.append({
                'params': param_label,
                'trades': 0,
                'win_rate': 0,
                'total_return': 0,
                'profit_factor': 0,
                'avg_win': 0,
                'avg_loss': 0
            })

    # 결과 출력
    print("\n" + "=" * 100)
    print("📊 파라미터 최적화 결과")
    print("=" * 100)
    print(f"\n{'파라미터':<45} {'거래':<8} {'승률':<10} {'수익':<12} {'PF':<8}")
    print("-" * 100)

    for r in sorted(results, key=lambda x: x['total_return'], reverse=True):
        print(f"{r['params']:<45} {r['trades']:<8} {r['win_rate']:.1f}%{'':<5} "
              f"{r['total_return']:+.2f}%{'':<5} {r['profit_factor']:.2f}")

    # 최적 파라미터
    if results:
        best = max(results, key=lambda x: x['total_return'])
        print(f"\n🏆 최적 파라미터: {best['params']}")
        print(f"   거래: {best['trades']}건, 승률: {best['win_rate']:.1f}%, "
              f"수익: {best['total_return']:+.2f}%, PF: {best['profit_factor']:.2f}")


def main():
    """메인 함수"""
    # 중소형 변동성 종목으로 테스트 (실제 거래 대상과 유사)
    test_stocks = {
        "250060": "모비스",
        "012790": "삼보모터스",
        "115960": "아이티센글로벌",
        "215600": "오름테라퓨틱",
        "215560": "재영솔루텍",
        "090710": "휴림로봇",
        "009520": "포스코엠텍",
        "084690": "대상홀딩스",
        "005070": "코스모신소재",
        "042700": "한미반도체"
    }

    # 1. 기본 비교 백테스트
    run_comparison_backtest(test_stocks, days=200)

    # 2. 파라미터 최적화
    run_parameter_optimization()


if __name__ == "__main__":
    main()
