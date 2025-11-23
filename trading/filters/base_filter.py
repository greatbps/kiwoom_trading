"""
BaseFilter - Confidence 반환 인터페이스

기존 Pass/Fail 대신 0.0~1.0 신호 강도(confidence) 반환
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple
import pandas as pd


class FilterResult:
    """필터 결과 (Pass/Fail + Confidence)"""

    def __init__(self, passed: bool, confidence: float = 0.0, reason: str = ""):
        self.passed = passed          # True/False (하위 호환성)
        self.confidence = confidence  # 0.0 ~ 1.0 (신호 강도)
        self.reason = reason          # 설명

    def __bool__(self):
        """하위 호환성: if filter_result: 구문 지원"""
        return self.passed

    def __repr__(self):
        return f"FilterResult(passed={self.passed}, conf={self.confidence:.2f}, reason='{self.reason}')"


class BaseFilter(ABC):
    """
    L3-L6 필터 베이스 클래스

    기존: Pass/Fail만 반환
    개선: Pass/Fail + Confidence(0~1) 반환
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def check(self, symbol: str, df: pd.DataFrame, **kwargs) -> FilterResult:
        """
        필터 체크 (구현 필수)

        Returns:
            FilterResult(passed, confidence, reason)
        """
        pass

    def __call__(self, symbol: str, df: pd.DataFrame, **kwargs) -> FilterResult:
        """편의 메서드"""
        return self.check(symbol, df, **kwargs)
