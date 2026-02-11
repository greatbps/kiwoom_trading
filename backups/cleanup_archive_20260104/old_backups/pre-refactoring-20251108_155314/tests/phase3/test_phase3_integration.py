#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3 통합 테스트
고급 기능 통합 시나리오 검증
"""

import pytest
import pandas as pd
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from signal_processing.adaptive_weight_manager import AdaptiveWeightManager
from monitoring.performance_report_generator import PerformanceReportGenerator
from backtesting.backtest_validator import BacktestValidator
from database.models import Base, SignalPerformance


@pytest.fixture
def in_memory_db():
    """인메모리 DB 세션"""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_strategies():
    """샘플 전략 리스트"""
    return ['squeeze_momentum', 'breakout', 'momentum', 'volume_spike']


@pytest.fixture
def initialize_performance_data(in_memory_db, sample_strategies):
    """성능 데이터 초기화"""
    for strategy in sample_strategies:
        perf = SignalPerformance(
            strategy_name=strategy,
            lookback_days=60,
            precision=0.7,
            recall=0.65,
            f1_score=0.675,
            win_rate=70.0,
            sharpe_ratio=1.2,
            total_signals=100,
            successful_signals=70
        )
        in_memory_db.add(perf)

    in_memory_db.commit()
    return in_memory_db


@pytest.fixture
def temp_output_dir():
    """임시 출력 디렉토리"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestPhase3Integration:
    """Phase 3 통합 테스트"""

    def test_adaptive_weight_and_report_integration(
        self,
        initialize_performance_data,
        sample_strategies,
        temp_output_dir
    ):
        """
        시나리오 1: 적응형 가중치 관리 + 성능 리포트 생성 통합
        """
        session = initialize_performance_data

        # 1. AdaptiveWeightManager 초기화
        weight_manager = AdaptiveWeightManager(
            strategies=sample_strategies,
            lookback_days=60
        )

        # 2. 성능 기반 가중치 계산
        weights = weight_manager.calculate_performance_based_weights(session)

        assert len(weights) == len(sample_strategies)
        assert abs(sum(weights.values()) - 1.0) < 0.01  # 합이 1에 가까워야 함

        # 3. 리밸런싱 실행
        rebalanced, new_weights, reason = weight_manager.rebalance(
            session,
            market_regime='TRENDING_UP',
            force=True
        )

        assert rebalanced is True
        assert len(new_weights) == len(sample_strategies)

        # 4. PerformanceReportGenerator로 리포트 생성
        mock_monitor = MagicMock()
        mock_monitor.get_performance_summary.return_value = {
            "total_signals": 400,
            "avg_consensus_time": 80.0,
            "avg_mtf_time": 50.0,
            "avg_gate_time": 30.0,
            "avg_confidence": 0.75,
            "gate_pass_rates": {"liquidity": 0.85, "news": 0.75, "regime": 0.90},
            "regime_distribution": {"TRENDING_UP": 200, "SIDEWAYS": 150, "VOLATILE": 50},
            "grade_distribution": {"STRONG_BUY": 100, "BUY": 200, "HOLD": 100},
            "risk_distribution": {"LOW": 200, "MEDIUM": 150, "HIGH": 50}
        }

        report_generator = PerformanceReportGenerator(
            db_session=session,
            output_dir=temp_output_dir,
            report_format="json"
        )

        report = report_generator.generate_report(
            lookback_days=60,
            phase1_monitor=mock_monitor,
            weight_manager=weight_manager
        )

        # 검증
        assert report.report_id.startswith("PERF_")
        assert len(report.strategies) == len(sample_strategies)
        assert len(report.weight_adjustments) > 0  # 리밸런싱 기록이 있어야 함
        assert len(report.key_findings) > 0
        assert len(report.recommendations) > 0

        # 리포트 저장
        filepath = report_generator.save_report(report)
        assert Path(filepath).exists()

    def test_backtest_and_report_integration(
        self,
        initialize_performance_data,
        temp_output_dir
    ):
        """
        시나리오 2: 백테스팅 + 성능 리포트 통합
        """
        # 1. 백테스트 실행
        validator = BacktestValidator(initial_capital=10_000_000)

        signals = pd.DataFrame({
            'date': pd.date_range('2024-01-01', periods=10),
            'symbol': ['005930'] * 10,
            'strategy': ['squeeze_momentum'] * 10,
            'direction': ['LONG'] * 10,
            'confidence': [0.8] * 10
        })

        price_data = pd.DataFrame({
            'date': pd.date_range('2024-01-01', periods=20),
            'symbol': ['005930'] * 20,
            'open': [70000] * 20,
            'high': [71000] * 20,
            'low': [69000] * 20,
            'close': [70700] * 20  # 1% 상승
        })

        result = validator.run_backtest(
            'squeeze_momentum',
            signals,
            price_data,
            position_size_pct=0.05
        )

        # 2. 백테스트 결과 검증
        assert result.metrics.total_trades > 0
        assert result.metrics.win_rate >= 0
        assert len(result.equity_curve) > 0

        # 3. 백테스트 결과를 DB에 반영 (시뮬레이션)
        session = initialize_performance_data

        backtest_perf = session.query(SignalPerformance).filter(
            SignalPerformance.strategy_name == 'squeeze_momentum'
        ).first()

        # 백테스트 결과로 성능 업데이트
        backtest_perf.precision = result.metrics.win_rate
        backtest_perf.total_signals = result.metrics.total_trades
        backtest_perf.successful_signals = result.metrics.winning_trades
        session.commit()

        # 4. 업데이트된 성능으로 리포트 생성
        mock_monitor = MagicMock()
        mock_monitor.get_performance_summary.return_value = {
            "total_signals": result.metrics.total_trades,
            "avg_consensus_time": 100.0,
            "avg_mtf_time": 60.0,
            "avg_gate_time": 40.0,
            "avg_confidence": 0.80,
            "gate_pass_rates": {"liquidity": 0.85, "news": 0.75, "regime": 0.90},
            "regime_distribution": {"TRENDING_UP": 50},
            "grade_distribution": {"STRONG_BUY": 20, "BUY": 30},
            "risk_distribution": {"LOW": 30, "MEDIUM": 20}
        }

        report_generator = PerformanceReportGenerator(
            db_session=session,
            output_dir=temp_output_dir,
            report_format="markdown"
        )

        report = report_generator.generate_report(
            lookback_days=60,
            phase1_monitor=mock_monitor
        )

        # 검증
        assert len(report.strategies) > 0
        strategy_snapshot = next(
            (s for s in report.strategies if s.strategy_name == 'squeeze_momentum'),
            None
        )
        assert strategy_snapshot is not None
        assert strategy_snapshot.total_signals == result.metrics.total_trades

    def test_full_workflow_integration(
        self,
        initialize_performance_data,
        sample_strategies,
        temp_output_dir
    ):
        """
        시나리오 3: 전체 워크플로우 통합
        (성능 측정 → 가중치 조정 → 백테스팅 → 리포트 생성)
        """
        session = initialize_performance_data

        # === Step 1: 가중치 관리자 초기화 및 리밸런싱 ===
        weight_manager = AdaptiveWeightManager(
            strategies=sample_strategies,
            lookback_days=60
        )

        # 레짐 기반 가중치 계산
        regime_weights = weight_manager.calculate_regime_adjusted_weights(
            session,
            market_regime='TRENDING_UP'
        )

        assert len(regime_weights) == len(sample_strategies)

        # 리밸런싱
        rebalanced, new_weights, reason = weight_manager.rebalance(
            session,
            market_regime='TRENDING_UP',
            force=True
        )

        assert rebalanced is True

        # === Step 2: 백테스트 실행 (각 전략별) ===
        validator = BacktestValidator(initial_capital=10_000_000)

        backtest_results = {}
        for strategy in sample_strategies[:2]:  # 일부만 테스트
            signals = pd.DataFrame({
                'date': pd.date_range('2024-01-01', periods=5),
                'symbol': ['005930'] * 5,
                'strategy': [strategy] * 5,
                'direction': ['LONG'] * 5,
                'confidence': [0.75] * 5
            })

            price_data = pd.DataFrame({
                'date': pd.date_range('2024-01-01', periods=15),
                'symbol': ['005930'] * 15,
                'open': [70000] * 15,
                'high': [71000] * 15,
                'low': [69000] * 15,
                'close': [70500] * 15
            })

            result = validator.run_backtest(strategy, signals, price_data)
            backtest_results[strategy] = result

        assert len(backtest_results) > 0

        # === Step 3: 백테스트 결과를 전략 비교 ===
        comparison_df = validator.compare_strategies(list(backtest_results.values()))

        assert len(comparison_df) > 0
        assert 'Strategy' in comparison_df.columns
        assert 'Win Rate' in comparison_df.columns

        # === Step 4: 종합 리포트 생성 ===
        mock_monitor = MagicMock()
        mock_monitor.get_performance_summary.return_value = {
            "total_signals": 500,
            "avg_consensus_time": 85.0,
            "avg_mtf_time": 55.0,
            "avg_gate_time": 35.0,
            "avg_confidence": 0.78,
            "gate_pass_rates": {"liquidity": 0.82, "news": 0.73, "regime": 0.88},
            "regime_distribution": {"TRENDING_UP": 250, "SIDEWAYS": 180, "VOLATILE": 70},
            "grade_distribution": {"STRONG_BUY": 120, "BUY": 240, "HOLD": 140},
            "risk_distribution": {"LOW": 250, "MEDIUM": 180, "HIGH": 70}
        }

        report_generator = PerformanceReportGenerator(
            db_session=session,
            output_dir=temp_output_dir,
            report_format="html"
        )

        report = report_generator.generate_report(
            lookback_days=60,
            phase1_monitor=mock_monitor,
            weight_manager=weight_manager
        )

        # 최종 검증
        assert report is not None
        assert len(report.strategies) > 0
        assert len(report.weight_adjustments) > 0
        assert report.system_snapshot.total_signals_processed == 500

        # 리포트 저장 (3가지 포맷)
        json_path = report_generator.save_report(report, format='json')
        md_path = report_generator.save_report(report, format='markdown')
        html_path = report_generator.save_report(report, format='html')

        assert Path(json_path).exists()
        assert Path(md_path).exists()
        assert Path(html_path).exists()

    def test_regime_change_adaptation(
        self,
        initialize_performance_data,
        sample_strategies
    ):
        """
        시나리오 4: 레짐 변화에 대한 적응형 가중치 조정
        """
        session = initialize_performance_data

        weight_manager = AdaptiveWeightManager(
            strategies=sample_strategies,
            lookback_days=60
        )

        # 레짐 1: TRENDING_UP
        weights_trending = weight_manager.calculate_regime_adjusted_weights(
            session,
            market_regime='TRENDING_UP'
        )

        # 리밸런싱
        weight_manager.rebalance(session, 'TRENDING_UP', force=True)

        # 레짐 2: VOLATILE
        weights_volatile = weight_manager.calculate_regime_adjusted_weights(
            session,
            market_regime='VOLATILE'
        )

        # 리밸런싱
        weight_manager.rebalance(session, 'VOLATILE', force=True)

        # 검증: 레짐에 따라 가중치가 달라야 함
        assert weights_trending != weights_volatile

        # 조정 기록 확인
        history = weight_manager.get_adjustment_history()
        assert len(history) >= 2
        assert history[0]['regime'] == 'TRENDING_UP'
        assert history[1]['regime'] == 'VOLATILE'

    def test_performance_degradation_alert(
        self,
        initialize_performance_data,
        temp_output_dir
    ):
        """
        시나리오 5: 성능 저하 감지 및 알림
        """
        session = initialize_performance_data

        # 저성능 전략 추가
        poor_strategy = SignalPerformance(
            strategy_name='poor_performer',
            lookback_days=60,
            precision=0.25,  # 낮은 정밀도
            recall=0.20,
            f1_score=0.225,
            win_rate=25.0,
            sharpe_ratio=0.3,
            total_signals=50,
            successful_signals=12
        )
        session.add(poor_strategy)
        session.commit()

        # 리포트 생성
        mock_monitor = MagicMock()
        mock_monitor.get_performance_summary.return_value = {
            "total_signals": 100,
            "avg_consensus_time": 100.0,
            "avg_mtf_time": 60.0,
            "avg_gate_time": 40.0,
            "avg_confidence": 0.60,
            "gate_pass_rates": {"liquidity": 0.5, "news": 0.4, "regime": 0.6},
            "regime_distribution": {"VOLATILE": 80, "SIDEWAYS": 20},
            "grade_distribution": {"HOLD": 80, "BUY": 20},
            "risk_distribution": {"HIGH": 50, "CRITICAL": 30, "MEDIUM": 20}
        }

        report_generator = PerformanceReportGenerator(
            db_session=session,
            output_dir=temp_output_dir,
            report_format="json"
        )

        report = report_generator.generate_report(
            lookback_days=60,
            phase1_monitor=mock_monitor
        )

        # 알림 검증
        assert len(report.alerts) > 0

        # 저성능 전략 알림이 있어야 함
        perf_alerts = [a for a in report.alerts if 'poor_performer' in str(a) or '성능' in a.get('title', '')]
        assert len(perf_alerts) > 0

        # 고위험 신호 알림이 있어야 함
        risk_alerts = [a for a in report.alerts if a.get('level') == 'CRITICAL']
        assert len(risk_alerts) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
