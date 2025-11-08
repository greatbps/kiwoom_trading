#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Performance Report Generator 단위 테스트
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from monitoring.performance_report_generator import (
    PerformanceReportGenerator,
    StrategyPerformanceSnapshot,
    SystemPerformanceSnapshot,
    PerformanceReport
)


@pytest.fixture
def mock_db_session():
    """Mock DB 세션"""
    session = MagicMock()
    return session


@pytest.fixture
def temp_output_dir():
    """임시 출력 디렉토리"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def report_generator(mock_db_session, temp_output_dir):
    """PerformanceReportGenerator 픽스처"""
    return PerformanceReportGenerator(
        db_session=mock_db_session,
        output_dir=temp_output_dir,
        report_format="json"
    )


@pytest.fixture
def sample_strategies():
    """샘플 전략 성능 데이터"""
    return [
        StrategyPerformanceSnapshot(
            strategy_name="squeeze_momentum",
            precision=0.75,
            recall=0.70,
            f1_score=0.725,
            win_rate=75.0,
            sharpe_ratio=1.5,
            total_signals=100,
            successful_signals=75,
            lookback_days=7,
            timestamp=datetime.now().isoformat()
        ),
        StrategyPerformanceSnapshot(
            strategy_name="breakout",
            precision=0.65,
            recall=0.60,
            f1_score=0.625,
            win_rate=65.0,
            sharpe_ratio=1.2,
            total_signals=80,
            successful_signals=52,
            lookback_days=7,
            timestamp=datetime.now().isoformat()
        ),
        StrategyPerformanceSnapshot(
            strategy_name="momentum",
            precision=0.45,
            recall=0.40,
            f1_score=0.425,
            win_rate=45.0,
            sharpe_ratio=0.8,
            total_signals=50,
            successful_signals=22,
            lookback_days=7,
            timestamp=datetime.now().isoformat()
        )
    ]


@pytest.fixture
def sample_system_snapshot():
    """샘플 시스템 스냅샷"""
    return SystemPerformanceSnapshot(
        timestamp=datetime.now().isoformat(),
        total_signals_processed=230,
        avg_processing_time_ms=250.5,
        gate_pass_rates={
            "liquidity": 0.85,
            "news": 0.72,
            "regime": 0.90
        },
        regime_distribution={
            "TRENDING_UP": 120,
            "SIDEWAYS": 80,
            "VOLATILE": 30
        },
        grade_distribution={
            "STRONG_BUY": 50,
            "BUY": 80,
            "HOLD": 100
        },
        risk_distribution={
            "LOW": 100,
            "MEDIUM": 90,
            "HIGH": 40
        },
        avg_consensus_score=2.5,
        avg_mtf_score=0.75,
        avg_signal_confidence=0.80
    )


class TestStrategyPerformanceSnapshot:
    """StrategyPerformanceSnapshot 테스트"""

    def test_create_snapshot(self):
        """스냅샷 생성 테스트"""
        snapshot = StrategyPerformanceSnapshot(
            strategy_name="test_strategy",
            precision=0.8,
            recall=0.75,
            f1_score=0.775,
            win_rate=80.0,
            sharpe_ratio=1.5,
            total_signals=100,
            successful_signals=80,
            lookback_days=7,
            timestamp=datetime.now().isoformat()
        )

        assert snapshot.strategy_name == "test_strategy"
        assert snapshot.precision == 0.8
        assert snapshot.f1_score == 0.775


class TestSystemPerformanceSnapshot:
    """SystemPerformanceSnapshot 테스트"""

    def test_create_snapshot(self, sample_system_snapshot):
        """시스템 스냅샷 생성 테스트"""
        assert sample_system_snapshot.total_signals_processed == 230
        assert sample_system_snapshot.avg_processing_time_ms == 250.5
        assert len(sample_system_snapshot.gate_pass_rates) == 3


class TestPerformanceReportGenerator:
    """PerformanceReportGenerator 테스트"""

    def test_initialization(self, report_generator, temp_output_dir):
        """초기화 테스트"""
        assert report_generator.db_session is not None
        assert report_generator.output_dir == Path(temp_output_dir)
        assert report_generator.report_format == "json"
        assert report_generator.output_dir.exists()

    def test_collect_strategy_performance(self, report_generator, mock_db_session):
        """전략 성능 수집 테스트"""
        # Mock DB query
        mock_perf = MagicMock()
        mock_perf.strategy_name = "squeeze_momentum"
        mock_perf.precision = 0.75
        mock_perf.recall = 0.70
        mock_perf.f1_score = 0.725
        mock_perf.win_rate = 75.0
        mock_perf.sharpe_ratio = 1.5
        mock_perf.total_signals = 100
        mock_perf.successful_signals = 75
        mock_perf.lookback_days = 7

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [mock_perf]
        mock_db_session.query.return_value = mock_query

        snapshots = report_generator.collect_strategy_performance(lookback_days=7)

        assert len(snapshots) == 1
        assert snapshots[0].strategy_name == "squeeze_momentum"
        assert snapshots[0].precision == 0.75

    def test_collect_system_performance(self, report_generator):
        """시스템 성능 수집 테스트"""
        # Mock phase1_monitor
        mock_monitor = MagicMock()
        mock_monitor.get_performance_summary.return_value = {
            "total_signals": 100,
            "avg_consensus_time": 80.0,
            "avg_mtf_time": 60.0,
            "avg_gate_time": 40.0,
            "avg_confidence": 0.75,
            "gate_pass_rates": {"liquidity": 0.8, "news": 0.7},
            "regime_distribution": {"TRENDING_UP": 60, "SIDEWAYS": 40},
            "grade_distribution": {"STRONG_BUY": 30, "BUY": 50, "HOLD": 20},
            "risk_distribution": {"LOW": 50, "MEDIUM": 40, "HIGH": 10}
        }

        snapshot = report_generator.collect_system_performance(mock_monitor)

        assert snapshot.total_signals_processed == 100
        assert snapshot.avg_processing_time_ms == 180.0
        assert snapshot.avg_signal_confidence == 0.75

    def test_collect_system_performance_no_data(self, report_generator):
        """데이터 없는 경우 시스템 성능 수집 테스트"""
        mock_monitor = MagicMock()
        mock_monitor.get_performance_summary.return_value = {"status": "no_data"}

        snapshot = report_generator.collect_system_performance(mock_monitor)

        assert snapshot.total_signals_processed == 0
        assert snapshot.avg_processing_time_ms == 0.0

    def test_analyze_key_findings(self, report_generator, sample_strategies, sample_system_snapshot):
        """주요 발견사항 분석 테스트"""
        findings = report_generator.analyze_key_findings(sample_strategies, sample_system_snapshot)

        assert len(findings) > 0
        assert any("squeeze_momentum" in f for f in findings)  # 최고 성능 전략
        assert any("momentum" in f for f in findings)  # 저조한 전략

    def test_generate_recommendations(self, report_generator, sample_strategies, sample_system_snapshot):
        """권장사항 생성 테스트"""
        findings = report_generator.analyze_key_findings(sample_strategies, sample_system_snapshot)
        recommendations = report_generator.generate_recommendations(
            sample_strategies, sample_system_snapshot, findings
        )

        assert len(recommendations) > 0
        assert any("가중치" in r for r in recommendations)

    def test_generate_alerts_low_performance(self, report_generator):
        """저성능 알림 생성 테스트"""
        strategies = [
            StrategyPerformanceSnapshot(
                strategy_name="poor_strategy",
                precision=0.25,
                recall=0.20,
                f1_score=0.225,
                win_rate=25.0,
                sharpe_ratio=0.3,
                total_signals=50,
                successful_signals=12,
                lookback_days=7,
                timestamp=datetime.now().isoformat()
            )
        ]

        system_snapshot = SystemPerformanceSnapshot(
            timestamp=datetime.now().isoformat(),
            total_signals_processed=50,
            avg_processing_time_ms=200.0,
            gate_pass_rates={},
            regime_distribution={},
            grade_distribution={},
            risk_distribution={},
            avg_consensus_score=0.0,
            avg_mtf_score=0.0,
            avg_signal_confidence=0.0
        )

        alerts = report_generator._generate_alerts(strategies, system_snapshot)

        assert len(alerts) > 0
        assert any(alert['level'] == 'ERROR' for alert in alerts)

    def test_generate_alerts_high_processing_time(self, report_generator):
        """높은 처리 시간 알림 생성 테스트"""
        strategies = []
        system_snapshot = SystemPerformanceSnapshot(
            timestamp=datetime.now().isoformat(),
            total_signals_processed=100,
            avg_processing_time_ms=1500.0,  # 임계값 초과
            gate_pass_rates={},
            regime_distribution={},
            grade_distribution={},
            risk_distribution={},
            avg_consensus_score=0.0,
            avg_mtf_score=0.0,
            avg_signal_confidence=0.0
        )

        alerts = report_generator._generate_alerts(strategies, system_snapshot)

        assert len(alerts) > 0
        assert any("처리 시간" in alert['message'] for alert in alerts)

    def test_generate_alerts_critical_risk(self, report_generator):
        """고위험 신호 알림 생성 테스트"""
        strategies = []
        system_snapshot = SystemPerformanceSnapshot(
            timestamp=datetime.now().isoformat(),
            total_signals_processed=100,
            avg_processing_time_ms=200.0,
            gate_pass_rates={},
            regime_distribution={},
            grade_distribution={},
            risk_distribution={"CRITICAL": 10, "HIGH": 20, "MEDIUM": 30, "LOW": 40},
            avg_consensus_score=0.0,
            avg_mtf_score=0.0,
            avg_signal_confidence=0.0
        )

        alerts = report_generator._generate_alerts(strategies, system_snapshot)

        assert len(alerts) > 0
        assert any(alert['level'] == 'CRITICAL' for alert in alerts)

    def test_generate_report(self, report_generator, sample_strategies, sample_system_snapshot):
        """리포트 생성 테스트"""
        with patch.object(report_generator, 'collect_strategy_performance', return_value=sample_strategies):
            with patch.object(report_generator, 'collect_system_performance', return_value=sample_system_snapshot):
                report = report_generator.generate_report(lookback_days=7)

                assert report.report_id.startswith("PERF_")
                assert len(report.strategies) == 3
                assert report.system_snapshot.total_signals_processed == 230
                assert len(report.key_findings) > 0
                assert len(report.recommendations) > 0

    def test_save_report_json(self, report_generator, sample_strategies, sample_system_snapshot, temp_output_dir):
        """JSON 리포트 저장 테스트"""
        report = PerformanceReport(
            report_id="TEST_REPORT_001",
            generation_time=datetime.now().isoformat(),
            report_period_start=(datetime.now() - timedelta(days=7)).isoformat(),
            report_period_end=datetime.now().isoformat(),
            strategies=sample_strategies,
            system_snapshot=sample_system_snapshot,
            weight_adjustments=[],
            key_findings=["Finding 1", "Finding 2"],
            recommendations=["Recommendation 1"],
            alerts=[]
        )

        filepath = report_generator.save_report(report, format="json")

        assert Path(filepath).exists()
        with open(filepath, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            assert loaded['report_id'] == "TEST_REPORT_001"
            assert len(loaded['strategies']) == 3

    def test_save_report_markdown(self, report_generator, sample_strategies, sample_system_snapshot, temp_output_dir):
        """Markdown 리포트 저장 테스트"""
        report = PerformanceReport(
            report_id="TEST_REPORT_002",
            generation_time=datetime.now().isoformat(),
            report_period_start=(datetime.now() - timedelta(days=7)).isoformat(),
            report_period_end=datetime.now().isoformat(),
            strategies=sample_strategies,
            system_snapshot=sample_system_snapshot,
            weight_adjustments=[],
            key_findings=["Finding 1"],
            recommendations=["Recommendation 1"],
            alerts=[{"level": "WARNING", "title": "Test Alert", "message": "Test message"}]
        )

        filepath = report_generator.save_report(report, format="markdown")

        assert Path(filepath).exists()
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            assert "# Performance Report" in content
            assert "squeeze_momentum" in content
            assert "⚠️" in content  # Warning emoji

    def test_save_report_html(self, report_generator, sample_strategies, sample_system_snapshot, temp_output_dir):
        """HTML 리포트 저장 테스트"""
        report = PerformanceReport(
            report_id="TEST_REPORT_003",
            generation_time=datetime.now().isoformat(),
            report_period_start=(datetime.now() - timedelta(days=7)).isoformat(),
            report_period_end=datetime.now().isoformat(),
            strategies=sample_strategies,
            system_snapshot=sample_system_snapshot,
            weight_adjustments=[],
            key_findings=["Finding 1"],
            recommendations=["Recommendation 1"],
            alerts=[{"level": "ERROR", "title": "Test Error", "message": "Error message"}]
        )

        filepath = report_generator.save_report(report, format="html")

        assert Path(filepath).exists()
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            assert "<!DOCTYPE html>" in content
            assert "<table>" in content
            assert "squeeze_momentum" in content

    def test_save_report_unsupported_format(self, report_generator, sample_strategies, sample_system_snapshot):
        """지원하지 않는 포맷 테스트"""
        report = PerformanceReport(
            report_id="TEST_REPORT_004",
            generation_time=datetime.now().isoformat(),
            report_period_start=(datetime.now() - timedelta(days=7)).isoformat(),
            report_period_end=datetime.now().isoformat(),
            strategies=sample_strategies,
            system_snapshot=sample_system_snapshot,
            weight_adjustments=[],
            key_findings=[],
            recommendations=[],
            alerts=[]
        )

        with pytest.raises(ValueError, match="Unsupported format"):
            report_generator.save_report(report, format="xml")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
