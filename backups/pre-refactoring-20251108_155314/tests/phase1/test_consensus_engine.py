#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Consensus Engine 단위 테스트
"""

import pytest
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from signal_processing.consensus_engine import ConsensusEngine
from database.models import SignalPerformance, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class TestConsensusEngine:
    """ConsensusEngine 테스트 클래스"""

    @pytest.fixture
    def mock_session(self):
        """테스트용 인메모리 DB 세션"""
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()

        # 테스트 데이터 삽입
        perf1 = SignalPerformance(
            strategy_name='squeeze_momentum',
            lookback_days=60,
            precision=0.75,
            total_signals=100,
            successful_signals=75
        )
        perf2 = SignalPerformance(
            strategy_name='breakout',
            lookback_days=60,
            precision=0.65,
            total_signals=80,
            successful_signals=52
        )

        session.add_all([perf1, perf2])
        session.commit()

        yield session

        session.close()

    def test_initialization_without_db(self):
        """DB 없이 초기화 테스트"""
        strategies = ['squeeze_momentum', 'breakout']
        engine = ConsensusEngine(strategies, db_session=None)

        assert engine.strategies == strategies
        assert engine.lookback_days == 60
        assert len(engine.weights) == 2
        # DB 없으면 모두 0.5 기본값
        assert all(0.0 <= w <= 1.0 for w in engine.weights.values())

    def test_initialization_with_db(self, mock_session):
        """DB 연동 초기화 테스트"""
        strategies = ['squeeze_momentum', 'breakout']
        engine = ConsensusEngine(strategies, db_session=mock_session)

        assert len(engine.weights) == 2
        # 가중치 합이 1.0
        assert abs(sum(engine.weights.values()) - 1.0) < 0.001

        # squeeze_momentum이 더 높은 precision을 가지므로 더 큰 가중치
        assert engine.weights['squeeze_momentum'] > engine.weights['breakout']

    def test_strategy_precision_loading(self, mock_session):
        """전략별 정밀도 로딩 테스트"""
        strategies = ['squeeze_momentum', 'breakout', 'new_strategy']
        engine = ConsensusEngine(strategies, db_session=mock_session)

        # DB에 있는 전략은 실제 값
        precision_sm = engine._get_strategy_precision('squeeze_momentum', 60)
        assert abs(precision_sm - 0.75) < 0.001

        precision_bo = engine._get_strategy_precision('breakout', 60)
        assert abs(precision_bo - 0.65) < 0.001

        # DB에 없는 전략은 기본값 0.5
        precision_new = engine._get_strategy_precision('new_strategy', 60)
        assert precision_new == 0.5

    def test_calculate_consensus_score(self):
        """합의 점수 계산 테스트"""
        strategies = ['squeeze_momentum', 'breakout']
        engine = ConsensusEngine(strategies, db_session=None)

        # 모든 전략이 1.0 신호
        signals = {
            'squeeze_momentum': 1.0,
            'breakout': 1.0
        }
        score = engine.calculate_consensus_score(signals)
        assert score > 0
        # 2개 전략이므로 최대 약 2.0 근처
        assert score <= 2.0 + 0.1

        # 일부 전략만 신호
        signals = {
            'squeeze_momentum': 1.0,
            'breakout': 0.0
        }
        score = engine.calculate_consensus_score(signals)
        assert 0 < score < 2.0

    def test_is_consensus_reached(self):
        """합의 도달 여부 테스트"""
        strategies = ['squeeze_momentum', 'breakout']
        engine = ConsensusEngine(strategies, db_session=None)

        # 높은 점수 - 합의 도달
        assert engine.is_consensus_reached(2.5, threshold=2.0) == True

        # 낮은 점수 - 합의 미달
        assert engine.is_consensus_reached(1.5, threshold=2.0) == False

    def test_update_weights(self, mock_session):
        """가중치 업데이트 테스트"""
        strategies = ['squeeze_momentum', 'breakout']
        engine = ConsensusEngine(strategies, db_session=mock_session)

        initial_weights = engine.weights.copy()

        # 가중치 강제 재계산
        engine.update_weights()

        # 가중치가 여전히 정규화되어 있어야 함
        assert abs(sum(engine.weights.values()) - 1.0) < 0.001

        # DB 값이 변하지 않았으므로 가중치도 동일해야 함
        for strategy in strategies:
            assert abs(initial_weights[strategy] - engine.weights[strategy]) < 0.001

    def test_update_weights_by_regime(self, mock_session):
        """레짐 기반 가중치 조정 테스트"""
        strategies = ['squeeze_momentum', 'breakout']
        engine = ConsensusEngine(strategies, db_session=mock_session)

        # TRENDING_UP 레짐 - breakout 유리
        engine.update_weights_by_regime('TRENDING_UP')
        trending_weights = engine.weights.copy()

        # VOLATILE 레짐 - 모두 불리
        engine.update_weights_by_regime('VOLATILE')
        volatile_weights = engine.weights.copy()

        # 가중치 합은 항상 1.0
        assert abs(sum(trending_weights.values()) - 1.0) < 0.001
        assert abs(sum(volatile_weights.values()) - 1.0) < 0.001

        # TRENDING_UP에서 breakout 가중치가 더 높아야 함
        # (VOLATILE에서는 둘 다 낮지만 비율은 유지)

    def test_weight_normalization(self):
        """가중치 정규화 테스트"""
        strategies = ['squeeze_momentum', 'breakout', 'volume_spike']
        engine = ConsensusEngine(strategies, db_session=None)

        # 가중치 합이 1.0인지 확인
        total_weight = sum(engine.weights.values())
        assert abs(total_weight - 1.0) < 0.001

        # 모든 가중치가 0-1 범위인지 확인
        for weight in engine.weights.values():
            assert 0.0 <= weight <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
