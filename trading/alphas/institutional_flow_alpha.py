"""
Institutional Flow Alpha - 기관/외인 수급 분석

기관 및 외국인 투자자의 순매수/순매도 흐름을 분석
"""

import numpy as np
from .base_alpha import BaseAlpha, AlphaOutput


class InstitutionalFlowAlpha(BaseAlpha):
    """
    기관/외인 수급 알파

    Logic:
    1. 기관 순매수 금액 + 외인 순매수 금액 계산
    2. 총 거래대금 대비 비율 계산
    3. 비율 > 5% → 강한 수급 → BUY
       비율 < -5% → 약한 수급 → SELL

    Score Range:
    - +3.0: 순매수 비율 10%+ (매우 강한 수급)
    - +1.5: 순매수 비율 5-10%
    - 0.0: 비율 < 1% (중립)
    - -1.5 ~ -3.0: 순매도 비율 (약한 수급)

    Confidence Range:
    - 1.0: 비율 10%+ (매우 확실)
    - 0.5: 비율 5-10%
    - 0.0: 비율 < 1%
    """

    def __init__(self, weight: float = 1.0):
        """
        Args:
            weight: 알파 가중치 (기본 1.0)
        """
        super().__init__("INST_FLOW", weight)

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        Institutional Flow 알파 계산

        Args:
            symbol: 종목코드
            state: {
                "institutional_flow": {
                    "inst_net_buy": int,      # 기관 순매수 (원)
                    "foreign_net_buy": int,   # 외인 순매수 (원)
                    "total_traded_value": int # 총 거래대금 (원)
                },
                ...
            }

        Returns:
            AlphaOutput with score and confidence
        """
        flow = state.get("institutional_flow", None)

        if flow is None:
            return AlphaOutput(
                name="INST_FLOW",
                score=0.0,
                confidence=0.0,
                reason="수급 데이터 없음"
            )

        try:
            # 데이터 추출
            inst_buy = flow.get("inst_net_buy", 0)
            foreign_buy = flow.get("foreign_net_buy", 0)
            total_value = flow.get("total_traded_value", 0)

            if total_value == 0:
                return AlphaOutput(
                    name="INST_FLOW",
                    score=0.0,
                    confidence=0.0,
                    reason="거래대금 0"
                )

            # 1. 순매수 비율 계산
            net_buy = inst_buy + foreign_buy
            ratio = net_buy / total_value

            # 2. Score 계산
            # ratio 10% = ±3.0점
            score = np.clip(ratio * 30, -3.0, 3.0)

            # 3. Confidence 계산
            # ratio 10% = 1.0 confidence
            confidence = np.clip(abs(ratio) * 10, 0.0, 1.0)

            # 4. 이유 설명
            direction = "순매수" if net_buy > 0 else "순매도"
            ratio_pct = abs(ratio) * 100
            reason = (
                f"기관+외인 {direction} "
                f"(비율 {ratio_pct:.1f}%, "
                f"금액 {abs(net_buy)/1e8:.1f}억)"
            )

            return AlphaOutput(
                name="INST_FLOW",
                score=score,
                confidence=confidence,
                reason=reason,
                metadata={
                    "inst_net_buy": inst_buy,
                    "foreign_net_buy": foreign_buy,
                    "total_net_buy": net_buy,
                    "ratio": ratio,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="INST_FLOW",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )
