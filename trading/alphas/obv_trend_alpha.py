"""
OBV Trend Alpha - On-Balance Volume 추세 분석

OBV (On-Balance Volume)는 가격 변동에 따라 거래량을 누적하여
매수/매도 압력을 측정하는 지표
"""

import numpy as np
import pandas as pd
from .base_alpha import BaseAlpha, AlphaOutput


class OBVTrendAlpha(BaseAlpha):
    """
    On-Balance Volume (OBV) 추세 알파

    Logic:
    1. OBV 계산: 가격 상승일 거래량 +, 하락일 거래량 -
    2. Fast MA (5) vs Slow MA (20) 비교
    3. Fast > Slow → 매수 압력 증가 → BUY
       Fast < Slow → 매도 압력 증가 → SELL

    Score Range:
    - +3.0: Fast가 Slow보다 5%+ 높음 (강한 상승 추세)
    - +1.5: Fast가 Slow보다 2-5% 높음
    - 0.0: 차이 < 1%
    - -1.5 ~ -3.0: Fast가 Slow보다 낮음 (하락 추세)

    Confidence Range:
    - 1.0: 차이 5%+ (매우 확실한 추세)
    - 0.5: 차이 2-5%
    - 0.0: 차이 < 1%
    """

    def __init__(self, weight: float = 1.2, fast: int = 5, slow: int = 20):
        """
        Args:
            weight: 알파 가중치 (기본 1.2)
            fast: Fast MA 기간 (기본 5)
            slow: Slow MA 기간 (기본 20)
        """
        super().__init__("OBV_TREND", weight)
        self.fast = fast
        self.slow = slow

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        OBV Trend 알파 계산

        Args:
            symbol: 종목코드
            state: {"df": OHLCV DataFrame, ...}

        Returns:
            AlphaOutput with score and confidence
        """
        df = state.get("df")
        if df is None or len(df) < self.slow + 5:
            return AlphaOutput(
                name="OBV_TREND",
                score=0.0,
                confidence=0.0,
                reason="데이터 부족"
            )

        try:
            # 1. OBV 계산
            obv = self._calculate_obv(df)

            # 2. Fast/Slow MA
            obv_fast = obv.rolling(self.fast).mean().iloc[-1]
            obv_slow = obv.rolling(self.slow).mean().iloc[-1]

            if pd.isna(obv_fast) or pd.isna(obv_slow):
                return AlphaOutput(
                    name="OBV_TREND",
                    score=0.0,
                    confidence=0.0,
                    reason="OBV MA 계산 실패"
                )

            # 3. 비율 계산
            diff = obv_fast - obv_slow
            norm = abs(obv_slow) + 1e-9  # 0 나누기 방지
            ratio = diff / norm

            # 4. Score 계산
            # ratio 5% 차이 = ±3.0점
            score = np.clip(ratio * 60, -3.0, 3.0)

            # 5. Confidence 계산
            # ratio 5% 차이 = 1.0 confidence
            confidence = np.clip(abs(ratio) * 20, 0.0, 1.0)

            # 6. 이유 설명
            trend = "상승" if ratio > 0 else "하락"
            reason = f"OBV {trend} 추세 (Fast/Slow={ratio*100:+.2f}%)"

            return AlphaOutput(
                name="OBV_TREND",
                score=score,
                confidence=confidence,
                reason=reason,
                metadata={
                    "obv_fast": obv_fast,
                    "obv_slow": obv_slow,
                    "ratio": ratio,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="OBV_TREND",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )

    def _calculate_obv(self, df: pd.DataFrame) -> pd.Series:
        """
        OBV (On-Balance Volume) 계산

        Logic:
        - 가격 상승일: OBV += volume
        - 가격 하락일: OBV -= volume
        - 가격 변동 없음: OBV 유지

        Returns:
            pd.Series: OBV 값
        """
        # 가격 변동 방향
        direction = np.sign(df["close"].diff())

        # 첫 번째 값은 NaN이므로 0으로 처리
        direction.iloc[0] = 0

        # OBV = cumsum(direction * volume)
        obv = (direction * df["volume"]).cumsum()

        return obv
