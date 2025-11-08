#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regime Gate 단위 테스트
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from signal_processing.regime_gate import RegimeGate


class TestRegimeGate:
    """RegimeGate 테스트 클래스"""

    @pytest.fixture
    def regime_gate(self):
        """기본 RegimeGate 인스턴스"""
        return RegimeGate(adx_threshold=25, volatility_threshold=30)

    def test_detect_trending_up(self, regime_gate):
        """TRENDING_UP 레짐 감지 테스트"""
        market_data = {
            'adx': 30,
            'sma_short': 2500,
            'sma_long': 2400,
            'volatility': 20
        }
        regime = regime_gate.detect_regime(market_data)
        assert regime == 'TRENDING_UP'

    def test_detect_trending_down(self, regime_gate):
        """TRENDING_DOWN 레짐 감지 테스트"""
        market_data = {
            'adx': 30,
            'sma_short': 2400,
            'sma_long': 2500,
            'volatility': 20
        }
        regime = regime_gate.detect_regime(market_data)
        assert regime == 'TRENDING_DOWN'

    def test_detect_sideways(self, regime_gate):
        """SIDEWAYS 레짐 감지 테스트"""
        market_data = {
            'adx': 20,  # 25 미만
            'sma_short': 2450,
            'sma_long': 2450,
            'volatility': 15
        }
        regime = regime_gate.detect_regime(market_data)
        assert regime == 'SIDEWAYS'

    def test_detect_volatile(self, regime_gate):
        """VOLATILE 레짐 감지 테스트 (최우선)"""
        market_data = {
            'adx': 35,
            'sma_short': 2500,
            'sma_long': 2400,
            'volatility': 35  # 30 초과
        }
        regime = regime_gate.detect_regime(market_data)
        assert regime == 'VOLATILE'

    def test_volatile_overrides_trending(self, regime_gate):
        """VOLATILE 레짐이 TRENDING보다 우선순위 높음"""
        market_data = {
            'adx': 40,  # 강한 추세
            'sma_short': 2600,
            'sma_long': 2400,
            'volatility': 40  # 높은 변동성
        }
        regime = regime_gate.detect_regime(market_data)
        # 추세장이지만 변동성이 높으면 VOLATILE
        assert regime == 'VOLATILE'

    def test_missing_data_defaults_to_sideways(self, regime_gate):
        """데이터 누락 시 SIDEWAYS 기본값"""
        market_data = {
            'adx': 30
            # sma_short, sma_long 누락
        }
        regime = regime_gate.detect_regime(market_data)
        assert regime == 'SIDEWAYS'

    def test_evaluate_strategy_for_trending_up(self, regime_gate):
        """TRENDING_UP에서 전략 적합도 평가"""
        # breakout은 TRENDING_UP에 최적 (1.0)
        suitability = regime_gate.evaluate_strategy_for_regime('breakout', 'TRENDING_UP')
        assert suitability == 1.0

        # mean_reversion은 TRENDING_UP에 부적합 (0.2)
        suitability = regime_gate.evaluate_strategy_for_regime('mean_reversion', 'TRENDING_UP')
        assert suitability == 0.2

    def test_evaluate_strategy_for_volatile(self, regime_gate):
        """VOLATILE에서 전략 적합도 평가"""
        # 모든 전략이 VOLATILE에서 낮은 적합도
        suitability_squeeze = regime_gate.evaluate_strategy_for_regime('squeeze_momentum', 'VOLATILE')
        assert suitability_squeeze == 0.1

        suitability_breakout = regime_gate.evaluate_strategy_for_regime('breakout', 'VOLATILE')
        assert suitability_breakout == 0.15

    def test_evaluate_unknown_strategy_uses_default(self, regime_gate):
        """알 수 없는 전략은 default 값 사용"""
        suitability = regime_gate.evaluate_strategy_for_regime('unknown_strategy', 'TRENDING_UP')
        assert suitability == 0.5  # default for TRENDING_UP

    def test_evaluate_unknown_regime_low_score(self, regime_gate):
        """알 수 없는 레짐은 낮은 점수 반환"""
        suitability = regime_gate.evaluate_strategy_for_regime('squeeze_momentum', 'UNKNOWN_REGIME')
        assert suitability == 0.1

    def test_custom_thresholds(self):
        """커스텀 임계값 테스트"""
        custom_gate = RegimeGate(adx_threshold=30, volatility_threshold=40)

        market_data = {
            'adx': 28,  # 30 미만
            'sma_short': 2500,
            'sma_long': 2400,
            'volatility': 25
        }
        regime = custom_gate.detect_regime(market_data)
        assert regime == 'SIDEWAYS'  # ADX < 30

    def test_strategy_suitability_map_completeness(self, regime_gate):
        """전략 적합도 맵 완전성 검증"""
        regimes = ['TRENDING_UP', 'TRENDING_DOWN', 'SIDEWAYS', 'VOLATILE']
        strategies = ['squeeze_momentum', 'breakout', 'momentum', 'mean_reversion']

        for regime in regimes:
            for strategy in strategies:
                suitability = regime_gate.evaluate_strategy_for_regime(strategy, regime)
                # 모든 조합에서 유효한 값 반환
                assert 0.0 <= suitability <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
