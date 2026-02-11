#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전략명 매핑 유틸리티"""

import random
from typing import List

class StrategyMapper:
    """전략명 매핑 클래스"""
    
    def __init__(self):
        self.strategy_names = [
            "MOMENTUM", "BREAKOUT", "RSI_STRATEGY", "SUPERTREND_EMA",
            "VWAP_STRATEGY", "SCALPING_3M", "SMART_MONEY", "AI_ANALYSIS"
        ]
    
    def get_strategy_for_stock(self, symbol: str, name: str, current_price=None) -> str:
        """종목에 적합한 전략 선택"""
        if any(keyword in name for keyword in ["삼성", "SK", "LG"]):
            candidates = ["MOMENTUM", "VWAP_STRATEGY", "AI_ANALYSIS"]
        elif current_price and current_price > 50000:
            candidates = ["MOMENTUM", "VWAP_STRATEGY"]
        else:
            candidates = ["BREAKOUT", "SCALPING_3M", "SMART_MONEY"]
        
        return random.choice(candidates)
    
    def get_all_strategy_names(self) -> List[str]:
        return self.strategy_names.copy()

# 싱글톤
strategy_mapper = StrategyMapper()
