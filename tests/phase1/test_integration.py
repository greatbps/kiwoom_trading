#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 1 통합 테스트
전체 신호 생성 파이프라인 테스트
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from signal_processing.consensus_engine import ConsensusEngine
from signal_processing.mtf_analyzer import MTFAnalyzer
from signal_processing.regime_gate import RegimeGate
from signal_processing.news_gate import NewsGate
from signal_processing.liquidity_gate import LiquidityGate
from signal_processing.quality_risk_evaluator import QualityRiskEvaluator
from database.models import Base, SignalPerformance
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta


class TestPhase1Integration:
    """Phase 1 통합 테스트"""

    @pytest.fixture
    def db_session(self):
        """테스트용 인메모리 DB"""
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()

        # 테스트 전략 성능 데이터
        strategies = [
            SignalPerformance(
                strategy_name='squeeze_momentum',
                lookback_days=60,
                precision=0.75,
                total_signals=100,
                successful_signals=75
            ),
            SignalPerformance(
                strategy_name='breakout',
                lookback_days=60,
                precision=0.70,
                total_signals=80,
                successful_signals=56
            )
        ]

        session.add_all(strategies)
        session.commit()

        yield session
        session.close()

    @pytest.fixture
    def components(self, db_session):
        """테스트용 컴포넌트들"""
        return {
            'consensus_engine': ConsensusEngine(
                ['squeeze_momentum', 'breakout'],
                db_session=db_session
            ),
            'mtf_analyzer': MTFAnalyzer(['3m', '15m', '1h', '1d']),
            'regime_gate': RegimeGate(),
            'news_gate': NewsGate(),
            'liquidity_gate': LiquidityGate(),
            'quality_evaluator': QualityRiskEvaluator()
        }

    def test_full_pipeline_strong_buy_signal(self, components):
        """강한 매수 신호 전체 파이프라인 테스트"""
        # 1. Consensus 계산
        strategy_signals = {
            'squeeze_momentum': 1.0,
            'breakout': 0.9
        }
        consensus_score = components['consensus_engine'].calculate_consensus_score(strategy_signals)
        assert consensus_score > 1.5  # 강한 합의

        # 2. MTF 분석
        mtf_signals = {
            '3m': 0.9,
            '15m': 0.95,
            '1h': 1.0,
            '1d': 1.0
        }
        mtf_score = components['mtf_analyzer'].calculate_confluence_score(
            mtf_signals,
            squeeze_duration=10
        )
        assert mtf_score > 0.8  # 강한 MTF 컨플루언스

        # 3. Regime Gate
        market_data = {
            'adx': 30,
            'sma_short': 2500,
            'sma_long': 2400,
            'volatility': 15
        }
        regime = components['regime_gate'].detect_regime(market_data)
        assert regime == 'TRENDING_UP'

        # 4. Liquidity Gate
        liquidity_data = {
            'price': 50000,
            'atr': 1500,  # 3% ATR
            'avg_trade_value': 5_000_000_000,
            'spread_pct': 0.2,
            'market_cap': 100_000_000_000
        }
        liquidity_passed = components['liquidity_gate'].evaluate('TEST001', liquidity_data)
        assert liquidity_passed == True

        # 5. News Gate
        news_data = [
            {
                'type': 'news',
                'timestamp': datetime.now() - timedelta(hours=2),
                'sentiment': 0.3,
                'content': '긍정적인 실적 발표'
            }
        ]
        news_passed = components['news_gate'].evaluate('TEST001', news_data)
        assert news_passed == True

        # 6. Gates 통과 수
        gates_passed = sum([
            liquidity_passed,
            news_passed,
            components['regime_gate'].evaluate_strategy_for_regime('squeeze_momentum', regime) > 0.5
        ])

        # 7. 품질-리스크 평가
        result = components['quality_evaluator'].evaluate(
            mtf_score=mtf_score,
            consensus_score=consensus_score,
            gates_passed=gates_passed,
            volatility=15,
            liquidity_score=0.9,
            market_regime=regime
        )

        # 검증
        assert result.quality_grade in ['STRONG_BUY', 'BUY']
        assert result.risk_level in ['LOW', 'MEDIUM']
        assert result.base_score > 70

    def test_full_pipeline_sell_signal(self, components):
        """약한 매도 신호 전체 파이프라인 테스트"""
        # 1. Consensus 계산 (약한 신호)
        strategy_signals = {
            'squeeze_momentum': 0.3,
            'breakout': 0.2
        }
        consensus_score = components['consensus_engine'].calculate_consensus_score(strategy_signals)
        assert consensus_score < 1.0

        # 2. MTF 분석 (약한 컨플루언스)
        mtf_signals = {
            '3m': 0.3,
            '15m': 0.4,
            '1h': 0.3,
            '1d': 0.2
        }
        mtf_score = components['mtf_analyzer'].calculate_confluence_score(mtf_signals)
        assert mtf_score < 0.4

        # 3. Regime Gate (VOLATILE)
        market_data = {
            'adx': 35,
            'sma_short': 2400,
            'sma_long': 2500,
            'volatility': 45  # 높은 변동성
        }
        regime = components['regime_gate'].detect_regime(market_data)
        assert regime == 'VOLATILE'

        # 4. Liquidity Gate (실패)
        liquidity_data = {
            'price': 50000,
            'atr': 500,  # 1% ATR (낮음)
            'avg_trade_value': 500_000_000,  # 낮은 거래대금
            'spread_pct': 0.8,  # 높은 스프레드
            'market_cap': 30_000_000_000  # 낮은 시가총액
        }
        liquidity_passed = components['liquidity_gate'].evaluate('TEST002', liquidity_data)
        assert liquidity_passed == False

        # 5. News Gate (부정 뉴스)
        news_data = [
            {
                'type': 'news',
                'timestamp': datetime.now() - timedelta(hours=1),
                'sentiment': -0.6,  # 부정 감성
                'content': '횡령 조사'
            }
        ]
        news_passed = components['news_gate'].evaluate('TEST002', news_data)
        assert news_passed == False

        # 6. Gates 통과 수
        gates_passed = 0  # 모두 실패

        # 7. 품질-리스크 평가
        result = components['quality_evaluator'].evaluate(
            mtf_score=mtf_score,
            consensus_score=consensus_score,
            gates_passed=gates_passed,
            volatility=45,
            liquidity_score=0.2,
            market_regime=regime
        )

        # 검증
        assert result.quality_grade in ['SELL', 'STRONG_SELL']
        assert result.risk_level in ['HIGH', 'CRITICAL']
        assert result.base_score < 40

    def test_regime_based_weight_adjustment(self, components):
        """레짐 기반 가중치 조정 통합 테스트"""
        # TRENDING_UP 레짐
        components['consensus_engine'].update_weights_by_regime('TRENDING_UP')
        trending_weights = components['consensus_engine'].weights.copy()

        # VOLATILE 레짐
        components['consensus_engine'].update_weights_by_regime('VOLATILE')
        volatile_weights = components['consensus_engine'].weights.copy()

        # TRENDING_UP에서 breakout 가중치가 더 높아야 함
        # (breakout은 TRENDING_UP에 적합도 1.0)
        assert trending_weights['breakout'] > volatile_weights['breakout']

        # 가중치 합은 항상 1.0
        assert abs(sum(trending_weights.values()) - 1.0) < 0.001
        assert abs(sum(volatile_weights.values()) - 1.0) < 0.001

    def test_squeeze_duration_impact_on_mtf(self, components):
        """Squeeze duration이 MTF 점수에 미치는 영향 테스트"""
        signals = {
            '3m': 0.7,
            '15m': 0.8,
            '1h': 0.9,
            '1d': 0.9
        }

        # Duration 없음
        score_no_duration = components['mtf_analyzer'].calculate_confluence_score(signals, squeeze_duration=0)

        # Duration 5
        score_duration_5 = components['mtf_analyzer'].calculate_confluence_score(signals, squeeze_duration=5)

        # Duration 15
        score_duration_15 = components['mtf_analyzer'].calculate_confluence_score(signals, squeeze_duration=15)

        # Duration이 길수록 점수가 높아야 함
        assert score_no_duration < score_duration_5 < score_duration_15

    def test_news_gate_safety(self, components):
        """NewsGate 안전성 테스트"""
        # 1. 뉴스 없음 - 기본 FAIL
        result_empty = components['news_gate'].evaluate('TEST003', [], allow_empty=False)
        assert result_empty == False

        # 2. 뉴스 없음 - allow_empty=True
        result_allowed = components['news_gate'].evaluate('TEST003', [], allow_empty=True)
        assert result_allowed == True

        # 3. 공시 쿨다운
        disclosure_data = [
            {
                'type': 'disclosure',
                'timestamp': datetime.now() - timedelta(minutes=30),  # 30분 전
                'sentiment': 0.0,
                'content': '중요 공시'
            }
        ]
        result_disclosure = components['news_gate'].evaluate('TEST004', disclosure_data)
        assert result_disclosure == False  # 60분 쿨다운

    def test_liquidity_gate_market_cap_filter(self, components):
        """LiquidityGate 시가총액 필터 테스트"""
        # 좋은 유동성이지만 시가총액 낮음
        data_low_cap = {
            'price': 50000,
            'atr': 1500,
            'avg_trade_value': 2_000_000_000,
            'spread_pct': 0.3,
            'market_cap': 40_000_000_000  # 400억 (500억 미만)
        }
        result = components['liquidity_gate'].evaluate('TEST005', data_low_cap)
        assert result == False  # 시가총액 부족으로 실패

        # 시가총액 충분
        data_good_cap = data_low_cap.copy()
        data_good_cap['market_cap'] = 60_000_000_000  # 600억
        result = components['liquidity_gate'].evaluate('TEST005', data_good_cap)
        assert result == True

    def test_quality_grade_with_risk_condition(self, components):
        """HIGH 등급에 LOW 리스크 필수 조건 테스트"""
        # 높은 점수 + 낮은 리스크 = STRONG_BUY
        result1 = components['quality_evaluator'].evaluate(
            mtf_score=0.9,
            consensus_score=3.8,
            gates_passed=3,
            volatility=10,
            liquidity_score=0.95,
            market_regime='TRENDING_UP'
        )
        assert result1.quality_grade == 'STRONG_BUY'
        assert result1.risk_level == 'LOW'

        # 높은 점수 + 높은 리스크 = BUY 또는 HOLD (STRONG_BUY 안됨)
        result2 = components['quality_evaluator'].evaluate(
            mtf_score=0.9,
            consensus_score=3.8,
            gates_passed=3,
            volatility=50,  # 높은 변동성
            liquidity_score=0.4,  # 낮은 유동성
            market_regime='VOLATILE'
        )
        assert result2.quality_grade != 'STRONG_BUY'
        assert result2.risk_level in ['HIGH', 'CRITICAL']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
