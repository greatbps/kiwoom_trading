#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest Validator 단위 테스트
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backtesting.backtest_validator import (
    BacktestValidator,
    BacktestTrade,
    BacktestMetrics,
    BacktestResult
)


@pytest.fixture
def backtest_validator():
    """BacktestValidator 픽스처"""
    return BacktestValidator(
        initial_capital=10_000_000,
        commission_rate=0.003,
        slippage_rate=0.001
    )


@pytest.fixture
def sample_signals():
    """샘플 신호 데이터"""
    return pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=5),
        'symbol': ['005930', '000660', '035420', '005930', '000660'],
        'strategy': ['squeeze_momentum'] * 5,
        'direction': ['LONG'] * 5,
        'confidence': [0.85, 0.75, 0.80, 0.90, 0.70]
    })


@pytest.fixture
def sample_price_data():
    """샘플 가격 데이터"""
    dates = pd.date_range('2024-01-01', periods=30)
    symbols = ['005930', '000660', '035420']

    data = []
    for symbol in symbols:
        base_price = 70000 if symbol == '005930' else 50000
        for date in dates:
            data.append({
                'date': date,
                'symbol': symbol,
                'open': base_price,
                'high': base_price * 1.02,
                'low': base_price * 0.98,
                'close': base_price * 1.01  # 1% 상승
            })

    return pd.DataFrame(data)


@pytest.fixture
def sample_trades():
    """샘플 거래 기록"""
    return [
        BacktestTrade(
            entry_date='2024-01-02',
            exit_date='2024-01-07',
            symbol='005930',
            strategy='squeeze_momentum',
            entry_price=70000,
            exit_price=72000,
            position_size=7.14,
            pnl=14280,
            pnl_pct=2.86,
            holding_days=5,
            win=True
        ),
        BacktestTrade(
            entry_date='2024-01-03',
            exit_date='2024-01-08',
            symbol='000660',
            strategy='squeeze_momentum',
            entry_price=50000,
            exit_price=48000,
            position_size=10.0,
            pnl=-20000,
            pnl_pct=-4.0,
            holding_days=5,
            win=False
        ),
        BacktestTrade(
            entry_date='2024-01-04',
            exit_date='2024-01-09',
            symbol='035420',
            strategy='squeeze_momentum',
            entry_price=60000,
            exit_price=63000,
            position_size=8.33,
            pnl=24990,
            pnl_pct=4.99,
            holding_days=5,
            win=True
        )
    ]


class TestBacktestValidator:
    """BacktestValidator 테스트"""

    def test_initialization(self, backtest_validator):
        """초기화 테스트"""
        assert backtest_validator.initial_capital == 10_000_000
        assert backtest_validator.commission_rate == 0.003
        assert backtest_validator.slippage_rate == 0.001

    def test_simulate_trades(self, backtest_validator, sample_signals, sample_price_data):
        """거래 시뮬레이션 테스트"""
        trades = backtest_validator.simulate_trades(
            sample_signals,
            sample_price_data,
            position_size_pct=0.05
        )

        assert len(trades) > 0
        assert all(isinstance(t, BacktestTrade) for t in trades)
        assert all(hasattr(t, 'entry_price') for t in trades)
        assert all(hasattr(t, 'exit_price') for t in trades)
        assert all(hasattr(t, 'pnl') for t in trades)

    def test_calculate_metrics(self, backtest_validator, sample_trades):
        """메트릭 계산 테스트"""
        metrics = backtest_validator.calculate_metrics(sample_trades)

        assert metrics.total_trades == 3
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 1
        assert metrics.win_rate == pytest.approx(2/3, rel=1e-3)
        assert metrics.total_pnl > 0  # 전체 수익
        assert metrics.profit_factor > 1  # 승률 > 50%이므로

    def test_calculate_metrics_empty_trades(self, backtest_validator):
        """빈 거래 리스트 메트릭 계산 테스트"""
        with pytest.raises(ValueError, match="No trades"):
            backtest_validator.calculate_metrics([])

    def test_generate_equity_curve(self, backtest_validator, sample_trades):
        """자본 곡선 생성 테스트"""
        equity_curve = backtest_validator.generate_equity_curve(sample_trades)

        assert len(equity_curve) == len(sample_trades) + 1  # 초기값 포함
        assert equity_curve[0]['equity'] == backtest_validator.initial_capital
        assert equity_curve[-1]['equity'] > backtest_validator.initial_capital  # 수익

    def test_generate_drawdown_series(self, backtest_validator, sample_trades):
        """Drawdown 시계열 생성 테스트"""
        equity_curve = backtest_validator.generate_equity_curve(sample_trades)
        drawdown_series = backtest_validator.generate_drawdown_series(equity_curve)

        assert len(drawdown_series) == len(equity_curve)
        assert all('drawdown' in point for point in drawdown_series)
        assert all('drawdown_pct' in point for point in drawdown_series)
        assert all(point['drawdown'] >= 0 for point in drawdown_series)

    def test_calculate_monthly_returns(self, backtest_validator, sample_trades):
        """월별 수익률 계산 테스트"""
        monthly_returns = backtest_validator.calculate_monthly_returns(sample_trades)

        assert len(monthly_returns) > 0
        assert '2024-01' in monthly_returns
        assert isinstance(monthly_returns['2024-01'], float)

    def test_run_backtest(self, backtest_validator, sample_signals, sample_price_data):
        """백테스트 실행 테스트"""
        result = backtest_validator.run_backtest(
            strategy_name='squeeze_momentum',
            signals=sample_signals,
            price_data=sample_price_data,
            position_size_pct=0.05
        )

        assert isinstance(result, BacktestResult)
        assert result.strategy_name == 'squeeze_momentum'
        assert isinstance(result.metrics, BacktestMetrics)
        assert len(result.trades) > 0
        assert len(result.equity_curve) > 0
        assert len(result.drawdown_series) > 0
        assert len(result.monthly_returns) > 0

    def test_run_backtest_no_trades(self, backtest_validator):
        """거래가 없는 백테스트 테스트"""
        empty_signals = pd.DataFrame({
            'date': [],
            'symbol': [],
            'strategy': [],
            'direction': [],
            'confidence': []
        })

        empty_price_data = pd.DataFrame({
            'date': [],
            'symbol': [],
            'open': [],
            'high': [],
            'low': [],
            'close': []
        })

        with pytest.raises(ValueError, match="No trades"):
            backtest_validator.run_backtest(
                'test_strategy',
                empty_signals,
                empty_price_data
            )

    def test_compare_strategies(self, backtest_validator, sample_signals, sample_price_data):
        """전략 비교 테스트"""
        result1 = backtest_validator.run_backtest(
            'strategy_1',
            sample_signals,
            sample_price_data
        )

        result2 = backtest_validator.run_backtest(
            'strategy_2',
            sample_signals,
            sample_price_data
        )

        comparison_df = backtest_validator.compare_strategies([result1, result2])

        assert len(comparison_df) == 2
        assert 'Strategy' in comparison_df.columns
        assert 'Win Rate' in comparison_df.columns
        assert 'Total PnL' in comparison_df.columns
        assert 'Sharpe' in comparison_df.columns

    def test_save_result(self, backtest_validator, sample_trades, tmp_path):
        """결과 저장 테스트"""
        metrics = backtest_validator.calculate_metrics(sample_trades)
        equity_curve = backtest_validator.generate_equity_curve(sample_trades)
        drawdown_series = backtest_validator.generate_drawdown_series(equity_curve)
        monthly_returns = backtest_validator.calculate_monthly_returns(sample_trades)

        result = BacktestResult(
            strategy_name='test_strategy',
            metrics=metrics,
            trades=sample_trades,
            equity_curve=equity_curve,
            drawdown_series=drawdown_series,
            monthly_returns=monthly_returns
        )

        output_dir = str(tmp_path)
        filepath = backtest_validator.save_result(result, output_dir)

        assert os.path.exists(filepath)
        assert filepath.endswith('.json')

        # 파일 내용 검증
        import json
        with open(filepath, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
            assert loaded_data['strategy_name'] == 'test_strategy'
            assert 'metrics' in loaded_data
            assert 'trades' in loaded_data


class TestBacktestMetrics:
    """BacktestMetrics 테스트"""

    def test_metrics_creation(self):
        """메트릭 생성 테스트"""
        metrics = BacktestMetrics(
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=0.6,
            total_pnl=1_000_000,
            avg_pnl=10_000,
            avg_win=25_000,
            avg_loss=-12_500,
            profit_factor=1.5,
            max_drawdown=500_000,
            max_drawdown_pct=5.0,
            sharpe_ratio=1.5,
            sortino_ratio=2.0,
            calmar_ratio=2.5,
            avg_holding_days=5.5,
            max_consecutive_wins=8,
            max_consecutive_losses=5,
            start_date='2024-01-01',
            end_date='2024-12-31',
            total_days=365
        )

        assert metrics.total_trades == 100
        assert metrics.win_rate == 0.6
        assert metrics.sharpe_ratio == 1.5


class TestBacktestTrade:
    """BacktestTrade 테스트"""

    def test_trade_creation(self):
        """거래 생성 테스트"""
        trade = BacktestTrade(
            entry_date='2024-01-01',
            exit_date='2024-01-05',
            symbol='005930',
            strategy='squeeze_momentum',
            entry_price=70000,
            exit_price=72000,
            position_size=10.0,
            pnl=20000,
            pnl_pct=2.86,
            holding_days=4,
            win=True
        )

        assert trade.symbol == '005930'
        assert trade.win is True
        assert trade.pnl > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
