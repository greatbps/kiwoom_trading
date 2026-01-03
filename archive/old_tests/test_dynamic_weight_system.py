#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/tests/test_dynamic_weight_system.py

동적 가중치 시스템 종합 테스트
"""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzers.market_condition_analyzer import MarketConditionAnalyzer, VolatilityRegime, TradingTimeRegime
from analyzers.performance_tracker import PerformanceTracker, PredictionOutcome
from analyzers.weight_adjuster import WeightAdjuster
from analyzers.enhanced_consensus_engine import EnhancedConsensusEngine, create_consensus_engine


class TestMarketConditionAnalyzer(unittest.IsolatedAsyncioTestCase):
    """MarketConditionAnalyzer 테스트"""
    
    async def asyncSetUp(self):
        self.config = Mock()
        self.data_collector = Mock()
        self.analyzer = MarketConditionAnalyzer(self.config, self.data_collector)
    
    async def test_analyze_current_condition(self):
        """현재 시장 상황 분석 테스트"""
        condition = await self.analyzer.analyze_current_condition()
        
        # 기본 검증
        self.assertIsNotNone(condition)
        self.assertIsInstance(condition.volatility_regime, VolatilityRegime)
        self.assertIsInstance(condition.trading_time_regime, TradingTimeRegime)
        self.assertGreaterEqual(condition.volatility_percentile, 0)
        self.assertLessEqual(condition.volatility_percentile, 100)
        
        # 가중치 조정 팩터 검증
        self.assertGreater(condition.technical_weight_factor, 0)
        self.assertGreater(condition.sentiment_weight_factor, 0)
        self.assertGreater(condition.momentum_weight_factor, 0)
        self.assertGreater(condition.volume_weight_factor, 0)
    
    def test_trading_time_regime_determination(self):
        """시간대별 거래 특성 결정 테스트"""
        # 오전 개장 시간
        morning_time = datetime.now().replace(hour=9, minute=15)
        regime = self.analyzer._determine_trading_time_regime(morning_time)
        self.assertEqual(regime, TradingTimeRegime.OPENING_RUSH)
        
        # 점심 시간
        lunch_time = datetime.now().replace(hour=12, minute=30)
        regime = self.analyzer._determine_trading_time_regime(lunch_time)
        self.assertEqual(regime, TradingTimeRegime.LUNCH_QUIET)
        
        # 마감 시간
        closing_time = datetime.now().replace(hour=15, minute=0)
        regime = self.analyzer._determine_trading_time_regime(closing_time)
        self.assertEqual(regime, TradingTimeRegime.CLOSING_RUSH)


class TestPerformanceTracker(unittest.IsolatedAsyncioTestCase):
    """PerformanceTracker 테스트"""
    
    async def asyncSetUp(self):
        self.config = Mock()
        self.db_manager = Mock()
        self.tracker = PerformanceTracker(self.config, self.db_manager)
    
    async def test_record_prediction(self):
        """예측 기록 테스트"""
        prediction_id = await self.tracker.record_prediction(
            analyzer_name="technical",
            symbol="005930",
            prediction_score=75.5,
            confidence=0.8,
            expected_direction="up",
            strategy="momentum"
        )
        
        self.assertIsNotNone(prediction_id)
        self.assertTrue(len(prediction_id) > 0)
        self.assertEqual(len(self.tracker.prediction_records), 1)
        
        # 기록된 내용 검증
        record = self.tracker.prediction_records[0]
        self.assertEqual(record.analyzer_name, "technical")
        self.assertEqual(record.symbol, "005930")
        self.assertEqual(record.prediction_score, 75.5)
        self.assertEqual(record.confidence, 0.8)
        self.assertEqual(record.expected_direction, "up")
    
    def test_weight_adjustment_factor_calculation(self):
        """가중치 조정 팩터 계산 테스트"""
        # 높은 정확도
        factor = self.tracker._calculate_weight_adjustment_factor(0.8, 0.85, 30)
        self.assertGreater(factor, 1.0)  # 가중치 증가
        self.assertLessEqual(factor, 1.5)  # 최대값 제한
        
        # 낮은 정확도
        factor = self.tracker._calculate_weight_adjustment_factor(0.2, 0.15, 30)
        self.assertLess(factor, 1.0)  # 가중치 감소
        self.assertGreaterEqual(factor, 0.5)  # 최소값 제한
        
        # 최소 예측 횟수 미달
        factor = self.tracker._calculate_weight_adjustment_factor(0.9, 0.95, 10)
        self.assertEqual(factor, 1.0)  # 기본값 유지
    
    def test_get_weight_adjustments(self):
        """가중치 조정 팩터 조회 테스트"""
        adjustments = self.tracker.get_weight_adjustments()
        
        # 모든 분석기에 대한 조정 팩터 존재
        expected_analyzers = ['technical', 'sentiment', 'supply_demand', 
                            'chart_pattern', 'fundamental', 'mtf', 'multi_llm']
        
        for analyzer in expected_analyzers:
            self.assertIn(analyzer, adjustments)
            self.assertIsInstance(adjustments[analyzer], float)


class TestWeightAdjuster(unittest.IsolatedAsyncioTestCase):
    """WeightAdjuster 테스트"""
    
    async def asyncSetUp(self):
        self.config = Mock()
        
        # Mock 구성 요소들
        self.market_analyzer = Mock()
        self.market_analyzer.analyze_current_condition = AsyncMock()
        
        self.performance_tracker = Mock()
        self.performance_tracker.get_weight_adjustments = Mock(return_value={
            'technical': 1.2,
            'sentiment': 0.8,
            'supply_demand': 1.0,
            'chart_pattern': 0.9,
            'fundamental': 1.0,
            'mtf': 1.1,
            'multi_llm': 1.0
        })
        
        self.adjuster = WeightAdjuster(
            self.config, 
            self.market_analyzer, 
            self.performance_tracker
        )
    
    async def test_get_dynamic_weights(self):
        """동적 가중치 계산 테스트"""
        # Mock 시장 상황 생성
        from analyzers.market_condition_analyzer import MarketCondition, VolatilityRegime, TradingTimeRegime, MarketStatus
        
        mock_condition = MarketCondition(
            volatility_regime=VolatilityRegime.HIGH,
            trading_time_regime=TradingTimeRegime.OPENING_RUSH,
            market_status=MarketStatus.OPEN,
            volatility_percentile=85.0,
            vix_equivalent=35.0,
            intraday_momentum=0.3,
            sector_rotation_active=True,
            technical_weight_factor=1.2,
            sentiment_weight_factor=0.8,
            momentum_weight_factor=1.1,
            volume_weight_factor=1.2
        )
        
        self.market_analyzer.analyze_current_condition.return_value = mock_condition
        
        # 동적 가중치 계산
        dynamic_weights = await self.adjuster.get_dynamic_weights("momentum", multi_llm_enabled=False)
        
        # 기본 검증
        self.assertIsNotNone(dynamic_weights)
        self.assertIsNotNone(dynamic_weights.base_weights)
        self.assertIsNotNone(dynamic_weights.adjusted_weights)
        self.assertGreater(dynamic_weights.confidence_score, 0)
        self.assertLessEqual(dynamic_weights.confidence_score, 1)
        
        # 가중치 합계 검증 (1.0에 가까워야 함)
        total_weight = sum(dynamic_weights.adjusted_weights.values())
        self.assertAlmostEqual(total_weight, 1.0, places=3)
        
        # 각 가중치가 유효 범위 내에 있는지 확인
        for weight in dynamic_weights.adjusted_weights.values():
            self.assertGreaterEqual(weight, 0.03)  # 최소 3%
            self.assertLessEqual(weight, 0.50)     # 최대 50%
    
    def test_weight_normalization(self):
        """가중치 정규화 테스트"""
        # 비정상적인 가중치
        abnormal_weights = {
            'technical': 0.8,
            'sentiment': 0.6,
            'supply_demand': 0.4,
            'chart_pattern': 0.2,
            'fundamental': 0.1,
            'mtf': 0.3
        }
        
        normalized = self.adjuster._normalize_and_validate_weights(abnormal_weights)
        
        # 합계가 1.0인지 확인
        total = sum(normalized.values())
        self.assertAlmostEqual(total, 1.0, places=4)
        
        # 모든 가중치가 유효 범위 내인지 확인
        for weight in normalized.values():
            self.assertGreaterEqual(weight, self.adjuster.min_weight_threshold)
            self.assertLessEqual(weight, self.adjuster.max_weight_threshold)


class TestEnhancedConsensusEngine(unittest.IsolatedAsyncioTestCase):
    """EnhancedConsensusEngine 테스트"""
    
    async def asyncSetUp(self):
        self.config = Mock()
        self.data_collector = Mock()
        self.database_manager = Mock()
        
        # 동적 기능 비활성화로 시작
        self.engine = EnhancedConsensusEngine(
            config=self.config,
            data_collector=self.data_collector,
            database_manager=self.database_manager,
            enable_dynamic_weights=False
        )
    
    def test_initialization_without_dynamic_features(self):
        """동적 기능 없이 초기화 테스트"""
        self.assertIsNotNone(self.engine)
        self.assertEqual(self.engine.enable_dynamic_weights, False)
        self.assertEqual(self.engine.dynamic_weight_usage_count, 0)
        self.assertEqual(self.engine.fallback_usage_count, 0)
    
    def test_compatibility_with_original_engine(self):
        """기존 ConsensusEngine과 호환성 테스트"""
        # 기존과 동일한 가중치 반환 확인
        weights1 = self.engine._get_strategy_weights("momentum", False)
        weights2 = self.engine.get_strategy_weights("momentum", False)
        
        self.assertEqual(weights1, weights2)
        
        # 기본 전략 가중치 확인
        momentum_weights = self.engine.get_strategy_weights("momentum")
        expected_sum = sum(momentum_weights.values())
        self.assertAlmostEqual(expected_sum, 1.0, places=4)
    
    async def test_enhanced_features_disabled(self):
        """향상된 기능이 비활성화된 상태에서 동작 테스트"""
        # Mock 분석 결과
        analysis_results = {
            'technical': {'technical_score': 75},
            'sentiment': {'overall_score': 60},
            'supply_demand': {'overall_score': 70},
            'chart_pattern': {'overall_score': 65},
            'fundamental': {'overall_score': 55},
            'mtf': {'mtf_score': 80}
        }
        
        # 동기 방식 호출 (기존과 동일)
        score, details = self.engine.synthesize(analysis_results, "momentum")
        
        self.assertIsInstance(score, float)
        self.assertGreater(score, 0)
        self.assertLess(score, 100)
        self.assertIsInstance(details, dict)
        self.assertEqual(self.engine.fallback_usage_count, 1)
    
    def test_factory_function(self):
        """팩토리 함수 테스트"""
        # 기존 엔진 생성
        original_engine = create_consensus_engine(self.config, enhanced=False)
        self.assertEqual(type(original_engine).__name__, "ConsensusEngine")
        
        # 향상된 엔진 생성
        enhanced_engine = create_consensus_engine(self.config, enhanced=True)
        self.assertEqual(type(enhanced_engine).__name__, "EnhancedConsensusEngine")
    
    def test_system_statistics(self):
        """시스템 통계 테스트"""
        stats = self.engine.get_system_statistics()
        
        expected_keys = [
            'dynamic_weight_enabled', 'dynamic_usage_count', 'fallback_usage_count',
            'total_synthesize_calls', 'dynamic_usage_rate', 'system_components_ready'
        ]
        
        for key in expected_keys:
            self.assertIn(key, stats)
        
        self.assertEqual(stats['dynamic_weight_enabled'], False)
        self.assertEqual(stats['system_components_ready'], False)


class TestIntegrationScenarios(unittest.IsolatedAsyncioTestCase):
    """통합 시나리오 테스트"""
    
    async def test_gradual_rollout_scenario(self):
        """점진적 배포 시나리오 테스트"""
        config = Mock()
        
        # 1단계: 기존 시스템
        original = create_consensus_engine(config, enhanced=False)
        self.assertEqual(type(original).__name__, "ConsensusEngine")
        
        # 2단계: 향상된 엔진 + 동적 기능 비활성
        enhanced_disabled = EnhancedConsensusEngine(config, enable_dynamic_weights=False)
        self.assertFalse(enhanced_disabled.enable_dynamic_weights)
        
        # 3단계: 동적 기능 활성화 시도 (구성 요소 없으면 실패해야 함)
        success = enhanced_disabled.enable_dynamic_features(True)
        self.assertFalse(success)  # 구성 요소가 준비되지 않아 실패
    
    async def test_error_handling_and_fallback(self):
        """에러 처리 및 폴백 테스트"""
        config = Mock()
        engine = EnhancedConsensusEngine(config, enable_dynamic_weights=True)
        
        # 동적 시스템이 준비되지 않은 상태에서 synthesize 호출
        analysis_results = {
            'technical': {'technical_score': 75},
            'sentiment': {'overall_score': 60},
        }
        
        score, details = engine.synthesize(analysis_results, "momentum")
        
        # 자동으로 기존 시스템으로 폴백되어야 함
        self.assertIsInstance(score, float)
        self.assertGreater(engine.fallback_usage_count, 0)
        self.assertEqual(engine.dynamic_weight_usage_count, 0)


def run_all_tests():
    """모든 테스트 실행"""
    # 테스트 스위트 생성
    test_classes = [
        TestMarketConditionAnalyzer,
        TestPerformanceTracker,
        TestWeightAdjuster,
        TestEnhancedConsensusEngine,
        TestIntegrationScenarios
    ]
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # 테스트 실행
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    print("Dynamic Weight System Test Starting...")
    
    success = run_all_tests()
    
    if success:
        print("All tests passed!")
    else:
        print("Some tests failed")
    
    exit(0 if success else 1)