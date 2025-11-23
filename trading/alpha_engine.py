"""
Simons-Style Alpha Engine - 멀티 알파 결합 엔진

여러 독립적인 알파 신호를 가중 평균하여 최종 aggregate score 계산
"""

from typing import List, Dict, Any
from trading.alphas.base_alpha import BaseAlpha, AlphaOutput


class SimonsStyleAlphaEngine:
    """
    멀티-알파 엔진

    여러 알파를 결합하여 최종 aggregate score를 계산합니다.
    각 알파는 weight × confidence × score로 기여합니다.

    Example:
        engine = SimonsStyleAlphaEngine(
            alphas=[
                VWAPAlpha(weight=2.0),
                VolumeSpikeAlpha(weight=1.5),
                OBVTrendAlpha(weight=1.2),
            ]
        )

        result = engine.compute(symbol, state)
        print(result["aggregate_score"])  # -3 ~ +3
    """

    def __init__(self, alphas: List[BaseAlpha]):
        """
        Args:
            alphas: BaseAlpha 인스턴스 리스트
        """
        self.alphas = alphas

    def compute(self, symbol: str, state: Dict[str, Any]) -> Dict:
        """
        모든 알파 계산 및 가중 평균

        Args:
            symbol: 종목코드
            state: 알파 계산에 필요한 데이터
                {
                    "df": OHLCV DataFrame,
                    "ai_analysis": AI 종합분석,
                    "institutional_flow": 수급 데이터,
                    ...
                }

        Returns:
            {
                "aggregate_score": float (-3 ~ +3),
                "alphas": [AlphaOutput, ...],
                "weighted_scores": {
                    "VWAP": {"score": 2.0, "confidence": 0.8, ...},
                    ...
                },
                "total_weight": float
            }
        """
        alpha_outputs = []

        # 각 알파 계산
        for alpha in self.alphas:
            try:
                output = alpha.compute(symbol, state)
                alpha_outputs.append(output)
            except Exception as e:
                print(f"❌ {alpha.name} 계산 실패: {e}")
                # 실패 시 중립 신호로 대체
                alpha_outputs.append(AlphaOutput(
                    name=alpha.name,
                    score=0.0,
                    confidence=0.0,
                    reason=f"오류: {str(e)}"
                ))

        # 가중 평균 계산
        total_weighted_score = 0.0
        total_weight = 0.0
        weighted_scores = {}

        for alpha, output in zip(self.alphas, alpha_outputs):
            # Weighted contribution = weight × confidence × score
            weighted = alpha.weight * output.confidence * output.score
            total_weighted_score += weighted

            # Total weight = Σ(weight × confidence)
            total_weight += alpha.weight * output.confidence

            # 각 알파별 기여도 저장
            weighted_scores[output.name] = {
                "score": output.score,
                "confidence": output.confidence,
                "weight": alpha.weight,
                "weighted_contribution": weighted,
                "reason": output.reason,
            }

        # Aggregate score 계산
        if total_weight > 0:
            aggregate_score = total_weighted_score / total_weight
        else:
            # 모든 알파의 confidence가 0인 경우
            aggregate_score = 0.0

        return {
            "aggregate_score": aggregate_score,
            "alphas": alpha_outputs,
            "weighted_scores": weighted_scores,
            "total_weight": total_weight,
        }

    def print_breakdown(self, result: Dict):
        """
        알파 breakdown을 보기 좋게 출력 (디버깅용)

        Args:
            result: compute() 결과
        """
        print("\n" + "="*70)
        print("📊 Multi-Alpha Breakdown")
        print("="*70)

        print(f"\n🎯 Aggregate Score: {result['aggregate_score']:+.3f}")
        print(f"   Total Weight: {result['total_weight']:.2f}\n")

        print(f"{'Alpha':20s} {'Score':>8s} {'Conf':>6s} {'Weight':>7s} {'Contrib':>9s} {'Reason'}")
        print("-" * 70)

        for alpha_name, details in result["weighted_scores"].items():
            score = details["score"]
            conf = details["confidence"]
            weight = details["weight"]
            contrib = details["weighted_contribution"]
            reason = details.get("reason", "")[:30]  # 30자로 제한

            print(
                f"{alpha_name:20s} "
                f"{score:+7.2f} "
                f"{conf:5.2f} "
                f"{weight:6.1f} "
                f"{contrib:+8.3f} "
                f"{reason}"
            )

        print("="*70 + "\n")

    def add_alpha(self, alpha: BaseAlpha):
        """
        알파 추가 (동적)

        Args:
            alpha: BaseAlpha 인스턴스
        """
        self.alphas.append(alpha)

    def remove_alpha(self, alpha_name: str):
        """
        알파 제거 (동적)

        Args:
            alpha_name: 제거할 알파 이름
        """
        self.alphas = [a for a in self.alphas if a.name != alpha_name]

    def get_alpha_names(self) -> List[str]:
        """
        현재 등록된 알파 목록 반환

        Returns:
            알파 이름 리스트
        """
        return [alpha.name for alpha in self.alphas]

    def __repr__(self):
        alpha_names = ", ".join(self.get_alpha_names())
        return f"SimonsStyleAlphaEngine(alphas=[{alpha_names}])"
