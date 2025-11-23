"""
Base Alpha Class - 모든 알파의 기본 클래스

모든 알파는 BaseAlpha를 상속하여 compute() 메서드를 구현해야 합니다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class AlphaOutput:
    """
    알파 계산 결과

    Attributes:
        name: 알파 이름 (예: "VWAP", "VOLUME_SPIKE")
        score: 방향 및 강도 (-3.0 ~ +3.0)
                +3.0 = 강한 매수 신호
                0.0 = 중립
                -3.0 = 강한 매도 신호
        confidence: 신뢰도 (0.0 ~ 1.0)
                    1.0 = 매우 확실
                    0.5 = 보통
                    0.0 = 불확실
        reason: 설명 (디버깅 및 로깅용)
        metadata: 추가 정보 (선택)
    """
    name: str
    score: float
    confidence: float
    reason: str = ""
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        """유효성 검사"""
        if not -3.0 <= self.score <= 3.0:
            raise ValueError(f"Score must be in [-3.0, 3.0], got {self.score}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be in [0.0, 1.0], got {self.confidence}")


class BaseAlpha(ABC):
    """
    모든 알파의 기본 추상 클래스

    새로운 알파를 추가하려면:
    1. BaseAlpha를 상속
    2. compute() 메서드 구현
    3. AlphaOutput(name, score, confidence, reason) 반환

    Example:
        class MyAlpha(BaseAlpha):
            def __init__(self, weight=1.0):
                super().__init__("MY_ALPHA", weight)

            def compute(self, symbol, state):
                # 알파 로직 구현
                score = ...
                confidence = ...
                return AlphaOutput("MY_ALPHA", score, confidence, "이유")
    """

    def __init__(self, name: str, weight: float = 1.0):
        """
        Args:
            name: 알파 이름
            weight: 가중치 (기본 1.0)
                    높을수록 최종 aggregate score에 더 큰 영향
        """
        self.name = name
        self.weight = weight

    @abstractmethod
    def compute(self, symbol: str, state: Dict[str, Any]) -> AlphaOutput:
        """
        알파 계산 (서브클래스에서 반드시 구현)

        Args:
            symbol: 종목코드 (예: "005930")
            state: 계산에 필요한 데이터
                {
                    "df": OHLCV DataFrame (1분봉),
                    "df_5m": OHLCV DataFrame (5분봉),
                    "ai_analysis": AI 종합분석 결과,
                    "institutional_flow": 수급 데이터,
                    ...
                }

        Returns:
            AlphaOutput: 계산 결과

        Raises:
            Exception: 계산 실패 시 (Engine이 자동으로 처리)
        """
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name}, weight={self.weight})"
