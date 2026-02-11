#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quality Risk Evaluator 단위 테스트
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from signal_processing.quality_risk_evaluator import (
    QualityRiskEvaluator,
    QualityRiskResult,
    evaluate_signal_quality
)


class TestQualityRiskEvaluator:
    """QualityRiskEvaluator 테스트 클래스"""

    @pytest.fixture
    def evaluator(self):
        """기본 Evaluator 인스턴스"""
        return QualityRiskEvaluator()

    def test_calculate_base_score_perfect(self, evaluator):
        """완벽한 신호의 기본 점수"""
        score = evaluator.calculate_base_score(
            mtf_score=1.0,
            consensus_score=4.0,
            gates_passed=3,
            total_gates=3
        )
        # 100점에 가까워야 함
        assert score >= 95
        assert score <= 100

    def test_calculate_base_score_moderate(self, evaluator):
        """중간 신호의 기본 점수"""
        score = evaluator.calculate_base_score(
            mtf_score=0.6,
            consensus_score=2.5,
            gates_passed=2,
            total_gates=3
        )
        # 60-75 범위 예상
        assert 55 <= score <= 80

    def test_calculate_base_score_poor(self, evaluator):
        """약한 신호의 기본 점수"""
        score = evaluator.calculate_base_score(
            mtf_score=0.3,
            consensus_score=1.0,
            gates_passed=1,
            total_gates=3
        )
        # 낮은 점수
        assert score < 50

    def test_determine_risk_level_low(self, evaluator):
        """LOW 리스크 판단"""
        risk = evaluator.determine_risk_level(
            volatility=10,
            liquidity_score=0.9,
            market_regime='TRENDING_UP'
        )
        assert risk == 'LOW'

    def test_determine_risk_level_medium(self, evaluator):
        """MEDIUM 리스크 판단"""
        risk = evaluator.determine_risk_level(
            volatility=25,
            liquidity_score=0.6,
            market_regime='SIDEWAYS'
        )
        assert risk == 'MEDIUM'

    def test_determine_risk_level_high(self, evaluator):
        """HIGH 리스크 판단"""
        risk = evaluator.determine_risk_level(
            volatility=40,
            liquidity_score=0.4,
            market_regime='TRENDING_DOWN'
        )
        assert risk in ['HIGH', 'CRITICAL']

    def test_determine_risk_level_critical(self, evaluator):
        """CRITICAL 리스크 판단"""
        risk = evaluator.determine_risk_level(
            volatility=60,
            liquidity_score=0.2,
            market_regime='VOLATILE'
        )
        assert risk == 'CRITICAL'

    def test_calculate_risk_adjusted_score_low_risk(self, evaluator):
        """LOW 리스크 조정 점수"""
        adjusted = evaluator.calculate_risk_adjusted_score(
            base_score=80.0,
            risk_level='LOW'
        )
        # LOW는 페널티 없음
        assert adjusted == 80.0

    def test_calculate_risk_adjusted_score_medium_risk(self, evaluator):
        """MEDIUM 리스크 조정 점수"""
        adjusted = evaluator.calculate_risk_adjusted_score(
            base_score=80.0,
            risk_level='MEDIUM'
        )
        # 10% 페널티
        assert adjusted == 72.0

    def test_calculate_risk_adjusted_score_high_risk(self, evaluator):
        """HIGH 리스크 조정 점수"""
        adjusted = evaluator.calculate_risk_adjusted_score(
            base_score=80.0,
            risk_level='HIGH'
        )
        # 25% 페널티
        assert adjusted == 60.0

    def test_calculate_risk_adjusted_score_critical_risk(self, evaluator):
        """CRITICAL 리스크 조정 점수"""
        adjusted = evaluator.calculate_risk_adjusted_score(
            base_score=80.0,
            risk_level='CRITICAL'
        )
        # 50% 페널티
        assert adjusted == 40.0

    def test_determine_quality_grade_strong_buy(self, evaluator):
        """STRONG_BUY 등급 판단"""
        grade = evaluator.determine_quality_grade(
            base_score=90.0,
            risk_level='LOW',
            gates_passed=3
        )
        assert grade == 'STRONG_BUY'

    def test_determine_quality_grade_buy(self, evaluator):
        """BUY 등급 판단"""
        grade = evaluator.determine_quality_grade(
            base_score=75.0,
            risk_level='MEDIUM',
            gates_passed=2
        )
        assert grade == 'BUY'

    def test_determine_quality_grade_hold(self, evaluator):
        """HOLD 등급 판단"""
        grade = evaluator.determine_quality_grade(
            base_score=55.0,
            risk_level='MEDIUM',
            gates_passed=2
        )
        assert grade == 'HOLD'

    def test_determine_quality_grade_sell(self, evaluator):
        """SELL 등급 판단"""
        grade = evaluator.determine_quality_grade(
            base_score=35.0,
            risk_level='HIGH',
            gates_passed=1
        )
        assert grade == 'SELL'

    def test_determine_quality_grade_strong_sell(self, evaluator):
        """STRONG_SELL 등급 판단"""
        grade = evaluator.determine_quality_grade(
            base_score=20.0,
            risk_level='CRITICAL',
            gates_passed=0
        )
        assert grade == 'STRONG_SELL'

    def test_high_score_but_high_risk_not_strong_buy(self, evaluator):
        """높은 점수지만 높은 리스크면 STRONG_BUY 안됨"""
        grade = evaluator.determine_quality_grade(
            base_score=90.0,
            risk_level='HIGH',  # 리스크가 HIGH
            gates_passed=3
        )
        assert grade != 'STRONG_BUY'

    def test_evaluate_complete_workflow(self, evaluator):
        """전체 평가 워크플로우 테스트"""
        result = evaluator.evaluate(
            mtf_score=0.85,
            consensus_score=3.5,
            gates_passed=3,
            volatility=15,
            liquidity_score=0.9,
            market_regime='TRENDING_UP'
        )

        assert isinstance(result, QualityRiskResult)
        assert result.quality_grade in ['STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL']
        assert result.risk_level in ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
        assert 0 <= result.base_score <= 100
        assert 0 <= result.risk_adjusted_score <= 100
        assert result.risk_adjusted_score <= result.base_score  # 조정 점수는 항상 낮거나 같음
        assert result.gates_passed == 3

    def test_evaluate_signal_quality_convenience_function(self):
        """편의 함수 테스트"""
        result = evaluate_signal_quality(
            mtf_score=0.7,
            consensus_score=2.8,
            gates_passed=2,
            volatility=25,
            liquidity_score=0.6,
            market_regime='SIDEWAYS'
        )

        assert isinstance(result, QualityRiskResult)
        assert result.quality_grade in ['STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL']

    def test_custom_weights(self):
        """커스텀 가중치 Evaluator"""
        custom_evaluator = QualityRiskEvaluator(
            mtf_weight=0.6,
            consensus_weight=0.2,
            gates_weight=0.2
        )

        score = custom_evaluator.calculate_base_score(
            mtf_score=1.0,
            consensus_score=4.0,
            gates_passed=3
        )

        # 가중치 합이 1.0이므로 점수 유효
        assert 0 <= score <= 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
