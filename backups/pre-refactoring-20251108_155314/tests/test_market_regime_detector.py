#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/tests/test_market_regime_detector.py

시장 체제 감지기 시스템 테스트
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
import numpy as np

from analyzers.market_regime_detector import MarketRegimeDetector, MarketRegime, MarketState
from risk_management.position_sizing import PositionSizingRecommendation

@pytest.fixture
def mock_config():
    return {
        'position_sizing': {
            'default_risk_per_trade_ratio': 0.01,
            'max_account_risk_ratio': 0.05,
            'regime_adjustment_factors': {
                'BULL_TREND': 1.2,
                'BEAR_TREND': 0.7,
                'SIDEWAYS': 0.9,
                'HIGH_VOLATILITY': 0.6,
                'LOW_VOLATILITY': 1.1
            },
            'min_position_ratio': 0.005,
            'max_position_ratio': 0.20
        }
    }

@pytest.fixture
def market_regime_detector(mock_config):
    detector = MarketRegimeDetector(mock_config)
    # LLM 제거됨 - Mock 제거
    return detector

@pytest.fixture
def sample_market_data():
    # Generate sample market data for 60 days
    data = []
    base_price = 10000.0
    for i in range(60):
        change_rate = np.random.uniform(-0.01, 0.01) # +/- 1%
        volume = np.random.randint(500000, 2000000)
        data.append({
            'date': (datetime.now() - timedelta(days=59 - i)).isoformat(),
            'close': base_price * (1 + change_rate),
            'change_rate': change_rate,
            'volume': volume
        })
        base_price *= (1 + change_rate)
    return data

@pytest.fixture
def sample_individual_stocks():
    return [
        {'symbol': 'A', 'change_rate': 0.02},
        {'symbol': 'B', 'change_rate': -0.01},
        {'symbol': 'C', 'change_rate': 0.03},
        {'symbol': 'D', 'change_rate': -0.02},
        {'symbol': 'E', 'change_rate': 0.005},
    ]

@pytest.mark.asyncio
async def test_detect_current_regime(market_regime_detector, sample_market_data, sample_individual_stocks):
    regime = await market_regime_detector.detect_current_regime(
        market_data=sample_market_data,
        individual_stocks=sample_individual_stocks
    )
    
    assert isinstance(regime, MarketRegime)
    assert regime.regime_type in ['BULL_TREND', 'BEAR_TREND', 'SIDEWAYS', 'HIGH_VOLATILITY', 'LOW_VOLATILITY']
    assert 0 <= regime.confidence <= 100
    assert regime.start_date is not None
    assert len(regime.key_indicators) > 0

@pytest.mark.asyncio
async def test_get_regime_based_recommendations_position_sizing_integration(market_regime_detector, mock_config):
    # Mock a specific regime for testing
    current_regime = MarketRegime(
        regime_type='BULL_TREND',
        sub_regime='STRONG',
        confidence=85.0,
        start_date=datetime.now(),
        duration_days=10,
        expected_duration=30,
        key_indicators=['MA_CROSS'],
        recommended_strategies=['momentum'],
        risk_factors=[],
        market_characteristics={'volatility': 'MEDIUM'},
        transition_probability={}
    )

    # Mock portfolio context with necessary details for position sizing
    portfolio_context = {
        'account_balance': 100000.0,
        'current_stock_price': 100.0,
        'stop_loss_price': 95.0,
        'signal_strength': 0.9,
        'risk_tolerance_level': 'HIGH'
    }

    # Temporarily set the current_regime for the detector for this test
    market_regime_detector.current_regime = current_regime

    recommendations = await market_regime_detector.get_regime_based_recommendations(
        current_regime=current_regime,
        portfolio_context=portfolio_context
    )

    assert 'position_sizing' in recommendations
    position_sizing_advice = recommendations['position_sizing']
    assert isinstance(position_sizing_advice, PositionSizingRecommendation)
    assert position_sizing_advice.recommended_size_ratio > 0
    assert position_sizing_advice.max_shares > 0
    assert position_sizing_advice.adjusted_by_regime is True
    assert position_sizing_advice.confidence > 0

    # Verify that the position sizing was adjusted by regime (BULL_TREND factor is 1.2)
    # Calculate expected risk per trade without regime adjustment
    default_risk_per_trade = portfolio_context['account_balance'] * mock_config['position_sizing']['default_risk_per_trade_ratio']
    # Calculate expected risk per trade with regime adjustment
    expected_risk_per_trade_with_regime = default_risk_per_trade * mock_config['position_sizing']['regime_adjustment_factors']['BULL_TREND']
    # Further adjust by signal strength and risk tolerance
    signal_adjustment_factor = 0.5 + (portfolio_context['signal_strength'] * 0.5)
    expected_risk_per_trade_with_regime *= signal_adjustment_factor
    expected_risk_per_trade_with_regime *= 1.2 # HIGH risk tolerance factor

    # Allow for some floating point deviation
    assert position_sizing_advice.risk_per_trade_usd == pytest.approx(expected_risk_per_trade_with_regime, rel=1e-2)

@pytest.mark.asyncio
async def test_analyze_market_state(market_regime_detector, sample_market_data):
    market_state = await market_regime_detector._analyze_market_state(sample_market_data)
    assert isinstance(market_state, MarketState)
    assert market_state.volatility_level in ['VERY_LOW', 'LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH']
    assert -100 <= market_state.trend_strength <= 100

@pytest.mark.asyncio
async def test_ai_regime_classification_call(market_regime_detector, sample_market_data):
    # Ensure _ai_regime_classification is called and returns a valid structure
    market_state = await market_regime_detector._analyze_market_state(sample_market_data)
    volatility_regime = await market_regime_detector._analyze_volatility_regime(sample_market_data)
    trend_regime = await market_regime_detector._analyze_trend_regime(sample_market_data)
    volume_regime = await market_regime_detector._analyze_volume_regime(sample_market_data)
    breadth_analysis = await market_regime_detector._analyze_market_breadth([])

    ai_result = await market_regime_detector._ai_regime_classification(
        market_state, volatility_regime, trend_regime, volume_regime, breadth_analysis
    )
    
    # LLM 제거됨 - AI 테스트 비활성화
    assert isinstance(ai_result, dict)