#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/tests/test_position_sizing.py

AdaptivePositionSizing 시스템 테스트
"""

import pytest
from datetime import datetime

from risk_management.position_sizing import AdaptivePositionSizing, PositionSizingRecommendation

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
def position_sizer(mock_config):
    return AdaptivePositionSizing(mock_config)

@pytest.mark.asyncio
async def test_position_sizing_bull_trend(position_sizer):
    account_balance = 100000.0
    stock_price = 100.0
    stop_loss_price = 95.0
    signal_strength = 0.8
    regime_type = 'BULL_TREND'
    regime_confidence = 80.0
    
    recommendation = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength
    )
    
    assert isinstance(recommendation, PositionSizingRecommendation)
    assert recommendation.recommended_size_ratio > 0.01 # Bull trend factor should increase it
    assert recommendation.max_shares > 0
    assert recommendation.adjusted_by_regime is True
    assert recommendation.confidence > 0

@pytest.mark.asyncio
async def test_position_sizing_bear_trend(position_sizer):
    account_balance = 100000.0
    stock_price = 100.0
    stop_loss_price = 98.0 # Tighter stop loss for bear market
    signal_strength = 0.6
    regime_type = 'BEAR_TREND'
    regime_confidence = 60.0
    
    recommendation = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength
    )
    
    assert isinstance(recommendation, PositionSizingRecommendation)
    # With the new logic, bear market sizing should be much smaller.
    # The expected ratio should be around default_risk_per_trade_ratio * 5 * regime_factor (0.01 * 5 * 0.7 = 0.035)
    # It should be less than 0.05 (default_risk_per_trade_ratio * 5) and greater than min_position_ratio
    assert recommendation.recommended_size_ratio < 0.04 # Adjusted assertion
    assert recommendation.recommended_size_ratio > position_sizer.min_position_ratio
    assert recommendation.max_shares >= 0
    assert recommendation.adjusted_by_regime is True
    assert recommendation.confidence > 0

@pytest.mark.asyncio
async def test_position_sizing_sideways_trend(position_sizer):
    account_balance = 100000.0
    stock_price = 50.0
    stop_loss_price = 48.0
    signal_strength = 0.7
    regime_type = 'SIDEWAYS'
    regime_confidence = 50.0
    
    recommendation = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength
    )
    
    assert isinstance(recommendation, PositionSizingRecommendation)
    assert 0.005 <= recommendation.recommended_size_ratio <= 0.20 # Should be within bounds
    assert recommendation.max_shares > 0
    assert recommendation.adjusted_by_regime is True
    assert recommendation.confidence > 0

@pytest.mark.asyncio
async def test_position_sizing_invalid_stop_loss(position_sizer):
    account_balance = 100000.0
    stock_price = 100.0
    stop_loss_price = 100.0 # Invalid: stop loss at or above entry
    signal_strength = 0.8
    regime_type = 'BULL_TREND'
    regime_confidence = 80.0
    
    recommendation = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength
    )
    
    assert isinstance(recommendation, PositionSizingRecommendation)
    assert recommendation.max_shares == 0 # Should recommend 0 shares due to invalid stop loss
    assert recommendation.recommended_size_ratio == 0.0 # Should be 0.0 when max_shares is 0
    assert "손절가가 매수가보다 높거나 같아 포지션 사이징 불가." in recommendation.reason # Check for specific warning message

@pytest.mark.asyncio
async def test_position_sizing_zero_balance(position_sizer):
    account_balance = 0.0
    stock_price = 100.0
    stop_loss_price = 95.0
    signal_strength = 0.8
    regime_type = 'BULL_TREND'
    regime_confidence = 80.0
    
    recommendation = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength
    )
    
    assert isinstance(recommendation, PositionSizingRecommendation)
    assert recommendation.max_shares == 0
    assert recommendation.recommended_size_ratio == 0.0
    assert "계좌 잔고 또는 주식 가격이 유효하지 않아 포지션 사이징 불가." in recommendation.reason # Check for specific warning message

@pytest.mark.asyncio
async def test_position_sizing_min_max_ratios(position_sizer):
    # Test with very high risk to hit max_position_ratio
    account_balance = 100000.0
    stock_price = 10.0
    stop_loss_price = 9.9 # Very tight stop loss, high leverage
    signal_strength = 1.0
    regime_type = 'BULL_TREND'
    regime_confidence = 80.0
    
    recommendation = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength,
        risk_tolerance_level="HIGH"
    )
    
    assert isinstance(recommendation, PositionSizingRecommendation)
    assert recommendation.recommended_size_ratio <= position_sizer.max_position_ratio
    assert recommendation.recommended_size_ratio >= position_sizer.min_position_ratio

    # Test with very low risk to hit min_position_ratio (should result in 0 shares/ratio)
    account_balance = 100000.0
    stock_price = 1000.0
    stop_loss_price = 1.0 # Extremely wide stop loss, very low risk per share
    signal_strength = 0.1
    regime_type = 'BEAR_TREND'
    regime_confidence = 60.0
    
    recommendation = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength,
        risk_tolerance_level="LOW"
    )
    
    assert isinstance(recommendation, PositionSizingRecommendation)
    assert recommendation.recommended_size_ratio == 0.0 # Should be 0.0 when max_shares is 0
    assert recommendation.max_shares == 0

@pytest.mark.asyncio
async def test_position_sizing_risk_tolerance(position_sizer):
    account_balance = 100000.0
    stock_price = 100.0
    stop_loss_price = 90.0
    signal_strength = 0.7
    regime_type = 'SIDEWAYS'
    regime_confidence = 50.0

    # LOW risk tolerance
    rec_low = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength,
        risk_tolerance_level="LOW"
    )

    # MEDIUM risk tolerance
    rec_medium = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength,
        risk_tolerance_level="MEDIUM"
    )

    # HIGH risk tolerance
    rec_high = await position_sizer.get_adaptive_position_sizing(
        regime_type=regime_type,
        regime_confidence=regime_confidence,
        account_balance=account_balance,
        stock_price=stock_price,
        stop_loss_price=stop_loss_price,
        signal_strength=signal_strength,
        risk_tolerance_level="HIGH"
    )

    assert rec_low.recommended_size_ratio < rec_medium.recommended_size_ratio
    assert rec_medium.recommended_size_ratio < rec_high.recommended_size_ratio
