"""
L4: Liquidity Shift Detector V2 - Confidence 반환

기존: detect_shift() → (bool, strength, reason)
개선: check_with_confidence() → FilterResult(passed, confidence, reason)
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.liquidity_shift_detector import LiquidityShiftDetector
from trading.filters.base_filter import FilterResult
from rich.console import Console

console = Console()


class LiquidityShiftDetectorV2(LiquidityShiftDetector):
    """
    L4 Liquidity Shift Detector V2 - Confidence 기반

    기존 detect_shift()는 유지 (하위 호환성)
    새로운 check_with_confidence()는 FilterResult 반환
    """

    def __init__(self, api=None, **kwargs):
        super().__init__(api=api, **kwargs)

        # Confidence 가중치 조정
        self.inst_weight = 0.4       # 기관 순매수 (40%)
        self.foreign_weight = 0.3    # 외국인 순매수 (30%)
        self.order_weight = 0.3      # 호가 불균형 (30%)

    def calculate_inst_confidence(self, inst_z: float) -> float:
        """
        기관 순매수 Z-score → Confidence

        Returns:
            0.0 ~ 0.4 점수
        """
        if inst_z <= 0:
            return 0.0

        # Z-score를 0~0.4 범위로 변환
        # 1σ = 0.13, 2σ = 0.27, 3σ+ = 0.4
        conf = min(inst_z / 3.0, 1.0) * self.inst_weight
        return float(conf)

    def calculate_foreign_confidence(self, foreign_z: float) -> float:
        """
        외국인 순매수 Z-score → Confidence

        Returns:
            0.0 ~ 0.3 점수
        """
        if foreign_z <= 0:
            return 0.0

        # Z-score를 0~0.3 범위로 변환
        conf = min(foreign_z / 3.0, 1.0) * self.foreign_weight
        return float(conf)

    def calculate_order_imbalance_confidence(self, order_imbalance: float) -> float:
        """
        호가 불균형 → Confidence

        Args:
            order_imbalance: -1.0 ~ +1.0

        Returns:
            0.0 ~ 0.3 점수
        """
        if order_imbalance <= 0:
            return 0.0

        # Order Imbalance를 0~0.3 범위로 변환
        # 0.2 이상이면 최대 점수
        if order_imbalance >= self.order_imbalance_threshold * 2:  # 0.4+
            return self.order_weight
        else:
            conf = (order_imbalance / (self.order_imbalance_threshold * 2)) * self.order_weight
            return float(conf)

    def check_with_confidence(
        self,
        stock_code: str,
        investor_data: pd.DataFrame = None,
        order_book: Dict = None
    ) -> FilterResult:
        """
        L4 Liquidity Shift + Confidence 계산

        Args:
            stock_code: 종목코드
            investor_data: 투자자 동향 데이터 (옵션)
            order_book: 호가 데이터 (옵션)

        Returns:
            FilterResult(passed, confidence, reason)
        """
        # 기존 detect_shift() 호출
        shift_detected, strength, reason = self.detect_shift(stock_code)

        if not shift_detected:
            # Shift 감지 못하면 Fail
            return FilterResult(False, 0.0, f"L4 수급 전환 없음: {reason}")

        # Confidence 세분화 계산
        try:
            # 1. 기관/외국인 순매수 Z-score
            inst_z, foreign_z = self.calculate_institutional_z_score(
                stock_code, investor_data
            )

            # 2. 호가 불균형
            order_imbalance = self.calculate_order_imbalance(
                stock_code, order_book
            )

            # 3. Confidence 계산
            inst_conf = self.calculate_inst_confidence(inst_z)
            foreign_conf = self.calculate_foreign_confidence(foreign_z)
            order_conf = self.calculate_order_imbalance_confidence(order_imbalance)

            # 합산 (0~1.0)
            confidence = inst_conf + foreign_conf + order_conf
            confidence = min(confidence, 1.0)

            # 상세 정보 추가
            detailed_reason = (
                f"{reason} | "
                f"Conf={confidence:.2f} "
                f"(기관:{inst_conf:.2f} 외인:{foreign_conf:.2f} 호가:{order_conf:.2f})"
            )

            return FilterResult(True, confidence, detailed_reason)

        except Exception as e:
            console.print(f"[dim]⚠️  L4 Confidence 계산 실패: {e}[/dim]")
            # 에러 시 기존 strength 사용
            return FilterResult(shift_detected, strength, f"{reason} | Conf={strength:.2f}")


if __name__ == "__main__":
    """테스트 코드"""
    print("=" * 80)
    print("🧪 Liquidity Shift Detector V2 (Confidence) 테스트")
    print("=" * 80)

    # API 없이 테스트 (기본값 반환)
    detector = LiquidityShiftDetectorV2(api=None)

    test_stocks = ["005930", "035420"]

    for stock_code in test_stocks:
        print(f"\n종목: {stock_code}")

        # V1 (기존)
        detected, strength, reason = detector.detect_shift(stock_code)
        print(f"  V1 (기존): {detected} - {reason}")
        print(f"           Strength = {strength:.2f}")

        # V2 (Confidence)
        result = detector.check_with_confidence(stock_code)
        print(f"  V2 (Conf): {result.passed} - {result.reason}")
        print(f"           Confidence = {result.confidence:.2f}")
