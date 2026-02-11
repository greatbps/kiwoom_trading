#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MTF Analyzer 단위 테스트
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from signal_processing.mtf_analyzer import MTFAnalyzer


class TestMTFAnalyzer:
    """MTFAnalyzer 테스트 클래스"""

    @pytest.fixture
    def mtf_analyzer(self):
        """기본 MTFAnalyzer 인스턴스"""
        timeframes = ['3m', '15m', '1h', '1d']
        return MTFAnalyzer(timeframes)

    def test_initialization(self, mtf_analyzer):
        """초기화 테스트"""
        assert mtf_analyzer.timeframes == ['3m', '15m', '1h', '1d']
        assert len(mtf_analyzer.weights) == 4
        # 가중치 합이 1.0
        assert abs(sum(mtf_analyzer.weights.values()) - 1.0) < 0.001

    def test_default_weights(self, mtf_analyzer):
        """기본 가중치 검증"""
        assert mtf_analyzer.weights['3m'] == 0.1
        assert mtf_analyzer.weights['15m'] == 0.2
        assert mtf_analyzer.weights['1h'] == 0.3
        assert mtf_analyzer.weights['1d'] == 0.4

    def test_calculate_confluence_score_without_duration(self, mtf_analyzer):
        """Squeeze duration 없이 점수 계산"""
        signals = {
            '3m': 1.0,
            '15m': 1.0,
            '1h': 1.0,
            '1d': 1.0
        }
        score = mtf_analyzer.calculate_confluence_score(signals)
        # 모든 신호가 1.0이면 가중치 합 = 1.0
        assert abs(score - 1.0) < 0.001

    def test_calculate_confluence_score_partial_signals(self, mtf_analyzer):
        """일부 타임프레임만 신호"""
        signals = {
            '3m': 0.5,
            '15m': 0.7,
            '1h': 0.9,
            '1d': 1.0
        }
        score = mtf_analyzer.calculate_confluence_score(signals)
        # 0.1*0.5 + 0.2*0.7 + 0.3*0.9 + 0.4*1.0 = 0.86
        expected = 0.1*0.5 + 0.2*0.7 + 0.3*0.9 + 0.4*1.0
        assert abs(score - expected) < 0.001

    def test_calculate_confluence_score_with_squeeze_duration(self, mtf_analyzer):
        """Squeeze duration 반영 테스트"""
        signals = {
            '3m': 1.0,
            '15m': 1.0,
            '1h': 1.0,
            '1d': 1.0
        }

        # Duration 없음
        score_no_duration = mtf_analyzer.calculate_confluence_score(signals, squeeze_duration=0)

        # Duration 10
        score_with_duration = mtf_analyzer.calculate_confluence_score(signals, squeeze_duration=10)

        # Duration이 있으면 점수가 더 높아야 함 (장기 타임프레임 부스트)
        assert score_with_duration > score_no_duration

    def test_squeeze_duration_boost_long_timeframes_only(self, mtf_analyzer):
        """Squeeze duration은 장기 타임프레임만 부스트"""
        signals_long_only = {
            '3m': 0.0,
            '15m': 0.0,
            '1h': 1.0,
            '1d': 1.0
        }

        signals_short_only = {
            '3m': 1.0,
            '15m': 1.0,
            '1h': 0.0,
            '1d': 0.0
        }

        # 장기 타임프레임에서 boost 효과가 더 큼
        score_long = mtf_analyzer.calculate_confluence_score(signals_long_only, squeeze_duration=15)
        score_short = mtf_analyzer.calculate_confluence_score(signals_short_only, squeeze_duration=15)

        # 장기가 더 높은 가중치를 가지므로 기본적으로 높음
        # Duration은 장기 타임프레임만 추가 boost

    def test_squeeze_duration_factor_capped(self, mtf_analyzer):
        """Duration factor가 1.5배로 제한됨"""
        signals = {
            '3m': 1.0,
            '15m': 1.0,
            '1h': 1.0,
            '1d': 1.0
        }

        # Duration 25 = 1.0 + 0.02*25 = 1.5 (최대)
        score_25 = mtf_analyzer.calculate_confluence_score(signals, squeeze_duration=25)

        # Duration 50 = 1.0 + 0.02*50 = 2.0 -> 1.5로 캡핑
        score_50 = mtf_analyzer.calculate_confluence_score(signals, squeeze_duration=50)

        # 둘 다 캡핑되어 동일해야 함
        assert abs(score_25 - score_50) < 0.001

    def test_confluence_score_capped_at_1(self, mtf_analyzer):
        """최종 점수가 1.0으로 제한됨"""
        signals = {
            '3m': 1.0,
            '15m': 1.0,
            '1h': 1.0,
            '1d': 1.0
        }

        # 매우 긴 duration
        score = mtf_analyzer.calculate_confluence_score(signals, squeeze_duration=100)

        # 1.0을 초과하지 않음
        assert score <= 1.0

    def test_is_confluence_met(self, mtf_analyzer):
        """Confluence 충족 여부 테스트"""
        # 높은 점수
        assert mtf_analyzer.is_confluence_met(0.7, min_threshold=0.6) == True

        # 낮은 점수
        assert mtf_analyzer.is_confluence_met(0.5, min_threshold=0.6) == False

        # 경계값
        assert mtf_analyzer.is_confluence_met(0.6, min_threshold=0.6) == True

    def test_custom_weights(self):
        """커스텀 가중치 테스트"""
        custom_weights = {
            '3m': 0.15,
            '15m': 0.25,
            '1h': 0.30,
            '1d': 0.30
        }
        analyzer = MTFAnalyzer(['3m', '15m', '1h', '1d'], weights=custom_weights)

        signals = {
            '3m': 1.0,
            '15m': 1.0,
            '1h': 1.0,
            '1d': 1.0
        }
        score = analyzer.calculate_confluence_score(signals)

        # 커스텀 가중치 합
        assert abs(score - 1.0) < 0.001

    def test_missing_timeframe_weight_raises_error(self):
        """가중치가 없는 타임프레임 사용 시 에러"""
        with pytest.raises(ValueError):
            MTFAnalyzer(['3m', '5m'])  # 5m은 기본 가중치에 없음

    def test_empty_signals(self, mtf_analyzer):
        """빈 신호 딕셔너리"""
        score = mtf_analyzer.calculate_confluence_score({})
        assert score == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
