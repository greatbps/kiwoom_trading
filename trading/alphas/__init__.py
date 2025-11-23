"""
Multi-Alpha Engine for Simons-style Trading System

이 모듈은 여러 독립적인 알파 신호를 결합하여 최종 매매 결정을 내립니다.
각 알파는 -3~+3 범위의 score와 0~1 범위의 confidence를 반환합니다.
"""

from .base_alpha import BaseAlpha, AlphaOutput

__all__ = [
    "BaseAlpha",
    "AlphaOutput",
]
