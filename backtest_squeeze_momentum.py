#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Squeeze Momentum 전략 백테스트

기존 전략 vs 스퀴즈 모멘텀 통합 전략 비교
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.squeeze_momentum import (
    calculate_squeeze_momentum,
    should_enter_trade,
    should_exit_trade,
    get_current_squeeze_signal
)


class SqueezeBacktester:
    """스퀴즈 모멘텀 백테스트"""

    def __init__(self, initial_capital: float = 10000000):
        """
        Args:
            initial_capital: 초기 자본금 (기본 1000만원)
        """
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.position = None  # {'code': str, 'shares': int, 'entry_price': float, 'entry_date': datetime}
        self.trades = []
        self.equity_curve = []

    def run_backtest(
        self,
        stock_code: str,
        df: pd.DataFrame,
        use_squeeze: bool = True,
        position_size_pct: float = 0.3,
        stop_loss_pct: float = -3.0,
        trailing_stop_pct: float = -5.0
    ) -> dict:
        """
        백테스트 실행

        Args:
            stock_code: 종목 코드
            df: OHLCV 데이터프레임
            use_squeeze: 스퀴즈 모멘텀 사용 여부
            position_size_pct: 포지션 크기 (자본금 대비 %)
            stop_loss_pct: 손절선 (%)
            trailing_stop_pct: 트레일링 스탑 (고점 대비 %)

        Returns:
            백테스트 결과
        """
        # 스퀴즈 모멘텀 계산
        df = calculate_squeeze_momentum(df)

        max_price_in_position = 0  # 보유 중 최고가

        for idx in range(20, len(df)):  # 최소 20일 데이터 필요
            current_date = df.index[idx]
            current_price = df.iloc[idx]['close']
            current_df = df.iloc[:idx + 1]

            # 포지션 없을 때: 진입 조건 확인
            if self.position is None:
                if use_squeeze:
                    # 스퀴즈 모멘텀 진입 조건
                    sqz_signal = get_current_squeeze_signal(current_df)

                    # 밝은 녹색 시작 (모멘텀 가속)
                    if sqz_signal['color'] == 'bright_green' and sqz_signal['signal'] == 'BUY':
                        self._enter_position(stock_code, current_price, current_date, position_size_pct)
                        max_price_in_position = current_price
                else:
                    # 기존 전략 (예: 5일 이동평균 돌파 등)
                    # 여기서는 간단히 상승 추세일 때 진입으로 가정
                    ma5 = current_df['close'].rolling(5).mean().iloc[-1]
                    ma20 = current_df['close'].rolling(20).mean().iloc[-1]

                    if current_price > ma5 > ma20:
                        self._enter_position(stock_code, current_price, current_date, position_size_pct)
                        max_price_in_position = current_price

            # 포지션 있을 때: 청산 조건 확인
            else:
                # 현재 수익률 계산
                profit_rate = ((current_price - self.position['entry_price']) / self.position['entry_price']) * 100
                max_price_in_position = max(max_price_in_position, current_price)

                # 손절 조건
                if profit_rate <= stop_loss_pct:
                    self._exit_position(current_price, current_date, "STOP_LOSS")
                    max_price_in_position = 0
                    continue

                # 트레일링 스탑 (고점 대비)
                trailing_pct = ((current_price - max_price_in_position) / max_price_in_position) * 100
                if trailing_pct <= trailing_stop_pct:
                    self._exit_position(current_price, current_date, "TRAILING_STOP")
                    max_price_in_position = 0
                    continue

                # 스퀴즈 모멘텀 청산 조건 (이익 중일 때만)
                if use_squeeze:
                    should_exit, exit_reason = should_exit_trade(current_df, profit_rate)

                    if should_exit:
                        self._exit_position(current_price, current_date, exit_reason)
                        max_price_in_position = 0
                        continue

                # 기존 전략 청산 조건
                else:
                    # 예: 5일 이동평균 하향 돌파
                    ma5 = current_df['close'].rolling(5).mean().iloc[-1]
                    if current_price < ma5 and profit_rate > 1.0:
                        self._exit_position(current_price, current_date, "MA5_BREAK")
                        max_price_in_position = 0
                        continue

            # 자산 기록
            current_equity = self.capital
            if self.position is not None:
                current_equity += self.position['shares'] * current_price

            self.equity_curve.append({
                'date': current_date,
                'equity': current_equity,
                'position': 1 if self.position else 0
            })

        # 마지막 포지션 강제 청산
        if self.position is not None:
            last_price = df.iloc[-1]['close']
            last_date = df.index[-1]
            self._exit_position(last_price, last_date, "FINAL_EXIT")

        return self._calculate_performance()

    def _enter_position(self, stock_code: str, price: float, date, position_size_pct: float):
        """포지션 진입"""
        position_value = self.capital * position_size_pct
        shares = int(position_value / price)

        if shares > 0:
            cost = shares * price
            self.capital -= cost

            self.position = {
                'code': stock_code,
                'shares': shares,
                'entry_price': price,
                'entry_date': date
            }

            print(f"[매수] {date} | {stock_code} | 가격: {price:,.0f} | 수량: {shares} | 금액: {cost:,.0f}")

    def _exit_position(self, price: float, date, reason: str):
        """포지션 청산"""
        if self.position is None:
            return

        revenue = self.position['shares'] * price
        self.capital += revenue

        profit = revenue - (self.position['shares'] * self.position['entry_price'])
        profit_pct = (profit / (self.position['shares'] * self.position['entry_price'])) * 100

        self.trades.append({
            'code': self.position['code'],
            'entry_date': self.position['entry_date'],
            'exit_date': date,
            'entry_price': self.position['entry_price'],
            'exit_price': price,
            'shares': self.position['shares'],
            'profit': profit,
            'profit_pct': profit_pct,
            'reason': reason
        })

        print(f"[매도] {date} | {self.position['code']} | 가격: {price:,.0f} | "
              f"수익: {profit:,.0f} ({profit_pct:+.2f}%) | 사유: {reason}")

        self.position = None

    def _calculate_performance(self) -> dict:
        """성과 계산"""
        if len(self.trades) == 0:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'avg_profit_pct': 0.0,
                'total_profit': 0.0,
                'total_return_pct': 0.0,
                'max_drawdown': 0.0,
                'sharpe_ratio': 0.0
            }

        trades_df = pd.DataFrame(self.trades)
        equity_df = pd.DataFrame(self.equity_curve)

        # 기본 통계
        total_trades = len(trades_df)
        winning_trades = len(trades_df[trades_df['profit'] > 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        avg_profit_pct = trades_df['profit_pct'].mean()
        total_profit = trades_df['profit'].sum()
        total_return_pct = ((self.capital - self.initial_capital) / self.initial_capital) * 100

        # MDD 계산
        equity_df['cummax'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = (equity_df['equity'] - equity_df['cummax']) / equity_df['cummax'] * 100
        max_drawdown = equity_df['drawdown'].min()

        # Sharpe Ratio 계산 (간단 버전)
        equity_df['returns'] = equity_df['equity'].pct_change()
        sharpe_ratio = (equity_df['returns'].mean() / equity_df['returns'].std() * np.sqrt(252)) if len(equity_df) > 1 else 0

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'avg_profit_pct': avg_profit_pct,
            'total_profit': total_profit,
            'total_return_pct': total_return_pct,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'final_capital': self.capital,
            'trades': trades_df,
            'equity_curve': equity_df
        }


def load_sample_data(stock_code: str, days: int = 100) -> pd.DataFrame:
    """
    실제 주식 데이터 로드 (pykrx 사용)

    Args:
        stock_code: 종목 코드 (6자리)
        days: 일수

    Returns:
        OHLCV 데이터프레임
    """
    try:
        from pykrx import stock

        # 날짜 설정
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # 날짜를 yyyymmdd 형식으로 변환
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        print(f"  데이터 로드: {stock_code} ({start_str} ~ {end_str})")

        # pykrx로 일봉 데이터 가져오기
        df = stock.get_market_ohlcv_by_date(start_str, end_str, stock_code)

        if df is None or df.empty:
            print(f"  ⚠️  {stock_code} 데이터 없음")
            return pd.DataFrame()

        # 컬럼명 확인 후 처리
        # pykrx는 ['시가', '고가', '저가', '종가', '거래량', '거래대금'] 또는 영문으로 반환
        if '시가' in df.columns:
            # 한글 컬럼명
            df = df[['시가', '고가', '저가', '종가', '거래량']]
            df.columns = ['open', 'high', 'low', 'close', 'volume']
        else:
            # 영문 컬럼명 (또는 다른 형식)
            # 필요한 컬럼만 선택
            if len(df.columns) >= 5:
                df = df.iloc[:, :5]  # 처음 5개 컬럼만 사용
                df.columns = ['open', 'high', 'low', 'close', 'volume']

        # 인덱스가 날짜이므로 그대로 사용
        print(f"  ✅ {len(df)}일 데이터 로드 완료")

        return df

    except Exception as e:
        print(f"  ❌ Error loading data: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()


def generate_dummy_data(days: int = 100) -> pd.DataFrame:
    """임시 테스트 데이터 생성"""
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')

    # 랜덤 가격 생성 (추세 포함)
    np.random.seed(42)
    base_price = 10000
    trend = np.linspace(0, 2000, days)
    noise = np.random.randn(days) * 200

    close = base_price + trend + noise
    high = close + np.random.rand(days) * 100
    low = close - np.random.rand(days) * 100
    open_price = close + np.random.randn(days) * 50
    volume = np.random.randint(100000, 1000000, days)

    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)

    return df


def main():
    """메인 함수"""
    print("=" * 80)
    print("Squeeze Momentum 백테스트 (실제 데이터)")
    print("=" * 80)

    # 테스트할 종목 (docs/1231의 차트와 동일한 종목)
    stocks = {
        "250060": "모비스",
        "012790": "삼보모터스",
        "115960": "아이티센글로벌",
        "215600": "오름테라퓨틱",
        "215560": "재영솔루텍",
        "090710": "휴림로봇"
    }

    results_comparison = []

    for stock_code, stock_name in stocks.items():
        print(f"\n{'=' * 80}")
        print(f"종목: {stock_name} ({stock_code})")
        print(f"{'=' * 80}\n")

        # 데이터 로드 (최근 200일)
        df = load_sample_data(stock_code, days=250)

        if df.empty:
            print(f"⚠️  {stock_code} 데이터 로드 실패")
            continue

        # 1. 기존 전략 (스퀴즈 미사용)
        print("\n[1] 기존 전략 (스퀴즈 미사용)")
        print("-" * 80)
        backtester1 = SqueezeBacktester(initial_capital=10000000)
        result1 = backtester1.run_backtest(stock_code, df.copy(), use_squeeze=False)

        print(f"\n총 거래: {result1['total_trades']}회")
        print(f"승률: {result1['win_rate']:.2f}%")
        print(f"평균 수익률: {result1['avg_profit_pct']:.2f}%")
        print(f"총 수익: {result1['total_profit']:,.0f}원 ({result1['total_return_pct']:.2f}%)")
        print(f"MDD: {result1['max_drawdown']:.2f}%")

        # 2. 스퀴즈 모멘텀 통합 전략
        print(f"\n[2] 스퀴즈 모멘텀 통합 전략")
        print("-" * 80)
        backtester2 = SqueezeBacktester(initial_capital=10000000)
        result2 = backtester2.run_backtest(stock_code, df.copy(), use_squeeze=True)

        print(f"\n총 거래: {result2['total_trades']}회")
        print(f"승률: {result2['win_rate']:.2f}%")
        print(f"평균 수익률: {result2['avg_profit_pct']:.2f}%")
        print(f"총 수익: {result2['total_profit']:,.0f}원 ({result2['total_return_pct']:.2f}%)")
        print(f"MDD: {result2['max_drawdown']:.2f}%")

        # 비교
        results_comparison.append({
            'stock_name': stock_name,
            'stock_code': stock_code,
            'basic_return': result1['total_return_pct'],
            'squeeze_return': result2['total_return_pct'],
            'improvement': result2['total_return_pct'] - result1['total_return_pct'],
            'basic_winrate': result1['win_rate'],
            'squeeze_winrate': result2['win_rate']
        })

    # 종합 결과
    print(f"\n\n{'=' * 80}")
    print("종합 비교 결과")
    print("=" * 80)

    if results_comparison:
        comparison_df = pd.DataFrame(results_comparison)
        print(comparison_df.to_string(index=False))

        avg_improvement = comparison_df['improvement'].mean()
        print(f"\n평균 수익률 개선: {avg_improvement:+.2f}%")
    else:
        print("⚠️  백테스트 결과 없음 (모든 종목 데이터 로드 실패)")


if __name__ == "__main__":
    main()
