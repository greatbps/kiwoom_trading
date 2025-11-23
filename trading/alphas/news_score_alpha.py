"""
News Score Alpha - AI 종합분석 뉴스 점수 재활용

기존 AI 종합분석의 뉴스 점수 (0-100)를 알파 신호로 변환
"""

import numpy as np
from .base_alpha import BaseAlpha, AlphaOutput


class NewsScoreAlpha(BaseAlpha):
    """
    뉴스 점수 알파

    Logic:
    1. 기존 AI 종합분석의 score_news (0-100) 활용
    2. 50 = 중립, 100 = 매우 긍정, 0 = 매우 부정
    3. 0-100 범위를 -3 ~ +3으로 선형 변환

    Score Range:
    - +3.0: score_news = 100 (매우 긍정적 뉴스)
    - +1.5: score_news = 75
    - 0.0: score_news = 50 (중립)
    - -1.5: score_news = 25
    - -3.0: score_news = 0 (매우 부정적 뉴스)

    Confidence Range:
    - 1.0: score_news = 0 or 100 (극단적, 확실)
    - 0.5: score_news = 25 or 75
    - 0.0: score_news = 50 (중립, 불확실)

    Note:
    - 08:50에 1회만 계산되므로 가중치 낮음 (0.8)
    - 장중 뉴스 변화는 반영 안 됨
    """

    def __init__(self, weight: float = 0.8):
        """
        Args:
            weight: 알파 가중치 (기본 0.8 - 1회성 데이터이므로 낮음)
        """
        super().__init__("NEWS", weight)

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        News Score 알파 계산

        Args:
            symbol: 종목코드
            state: {
                "ai_analysis": {
                    "scores": {
                        "news": int (0-100)
                    }
                },
                ...
            }

        Returns:
            AlphaOutput with score and confidence
        """
        analysis = state.get("ai_analysis", None)

        if analysis is None:
            return AlphaOutput(
                name="NEWS",
                score=0.0,
                confidence=0.0,
                reason="AI 분석 없음"
            )

        try:
            # 1. 뉴스 점수 추출
            scores = analysis.get("scores", {})
            news_score = scores.get("news", 50)  # 기본값 50 (중립)

            # 유효성 검사
            if not 0 <= news_score <= 100:
                return AlphaOutput(
                    name="NEWS",
                    score=0.0,
                    confidence=0.0,
                    reason=f"유효하지 않은 뉴스 점수: {news_score}"
                )

            # 2. 0-100 → -3 ~ +3 변환
            # 공식: score = ((news_score - 50) / 50) * 3
            score = ((news_score - 50) / 50) * 3.0
            score = np.clip(score, -3.0, 3.0)

            # 3. Confidence 계산
            # 극단적일수록 (0 또는 100에 가까울수록) 신뢰도 높음
            # 50에서 멀어질수록 confidence 증가
            confidence = abs(score) / 3.0  # 0.0 ~ 1.0

            # 4. 이유 설명
            if news_score >= 75:
                sentiment = "매우 긍정"
            elif news_score >= 60:
                sentiment = "긍정"
            elif news_score >= 40:
                sentiment = "중립"
            elif news_score >= 25:
                sentiment = "부정"
            else:
                sentiment = "매우 부정"

            reason = f"뉴스 {sentiment} (점수 {news_score}/100)"

            return AlphaOutput(
                name="NEWS",
                score=score,
                confidence=confidence,
                reason=reason,
                metadata={
                    "raw_score": news_score,
                    "sentiment": sentiment,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="NEWS",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )
