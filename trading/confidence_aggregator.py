"""
Confidence Aggregator - 멀티 필터 신뢰도 결합

L3-L6 각 레이어의 confidence를 가중 평균으로 결합
"""

from typing import List, Dict, Tuple
import numpy as np
from trading.filters.base_filter import FilterResult


class ConfidenceAggregator:
    """
    멀티 필터 Confidence 결합 엔진

    각 필터의 confidence(0~1)를 가중 평균으로 결합하여
    최종 신호 강도를 계산
    """

    def __init__(self, weights: Dict[str, float] = None):
        """
        Args:
            weights: 레이어별 가중치 {"L3": 1.5, "L4": 1.0, ...}
        """
        self.weights = weights or {
            "L3_MTF": 1.5,         # Multi-Timeframe (가장 중요)
            "L4_LIQUIDITY": 1.0,   # 유동성
            "L5_SQUEEZE": 1.2,     # Squeeze 모멘텀
            "L6_VALIDATOR": 0.8,   # 최종 검증
        }

    def aggregate(
        self,
        filter_results: Dict[str, FilterResult]
    ) -> Tuple[float, bool, str]:
        """
        필터 결과 결합

        Args:
            filter_results: {"L3_MTF": FilterResult(...), ...}

        Returns:
            (final_confidence, should_pass, reason)
        """
        if not filter_results:
            return 0.0, False, "No filter results"

        # 가중 평균 계산
        numerator = 0.0
        denominator = 0.0
        reasons = []

        for layer_name, result in filter_results.items():
            weight = self.weights.get(layer_name, 1.0)

            # Pass/Fail 체크
            if not result.passed:
                return 0.0, False, f"{layer_name} failed: {result.reason}"

            # 가중치 * confidence
            numerator += weight * result.confidence
            denominator += weight

            reasons.append(f"{layer_name}:{result.confidence:.2f}")

        # 최종 confidence
        final_confidence = numerator / denominator if denominator > 0 else 0.0

        # 최소 임계값 체크 (0.5)
        MIN_CONFIDENCE = 0.5

        if final_confidence < MIN_CONFIDENCE:
            return final_confidence, False, f"Low confidence ({final_confidence:.2f} < {MIN_CONFIDENCE})"

        reason = f"Aggregated conf={final_confidence:.2f} [{', '.join(reasons)}]"
        return final_confidence, True, reason

    def calculate_position_multiplier(self, confidence: float) -> float:
        """
        Confidence 기반 포지션 크기 조정

        Args:
            confidence: 0.0 ~ 1.0

        Returns:
            position_multiplier: 0.6 ~ 1.0
        """
        # 선형 스케일링: 0.5 → 0.6, 1.0 → 1.0
        if confidence < 0.5:
            return 0.6

        # 0.5 ~ 1.0 → 0.6 ~ 1.0
        return 0.6 + (confidence - 0.5) * 0.8


# 전역 인스턴스 (기본 가중치)
default_aggregator = ConfidenceAggregator()


def aggregate_confidence(
    filter_results: Dict[str, FilterResult]
) -> Tuple[float, bool, str]:
    """
    편의 함수: 기본 aggregator 사용

    Example:
        results = {
            "L3_MTF": FilterResult(True, 0.8, "Strong VWAP"),
            "L4_LIQUIDITY": FilterResult(True, 0.6, "OK"),
            "L5_SQUEEZE": FilterResult(True, 0.7, "Momentum+"),
            "L6_VALIDATOR": FilterResult(True, 0.5, "Pass"),
        }

        conf, passed, reason = aggregate_confidence(results)
        # conf = 0.68, passed = True
    """
    return default_aggregator.aggregate(filter_results)
