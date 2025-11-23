"""
Volume Spike Alpha - 거래량 급등 감지

거래량 Z-score 기반 알파
"""

import numpy as np
import pandas as pd
from .base_alpha import BaseAlpha, AlphaOutput


class VolumeSpikeAlpha(BaseAlpha):
    """
    거래량 급등 감지 알파

    Logic:
    1. 거래량 Z-score 계산
    2. Z > 2.0 → 급등으로 판단
    3. 방향은 최근 수익률 부호 사용
    4. 거래량 급등 + 가격 상승 → 강한 매수
       거래량 급등 + 가격 하락 → 강한 매도

    Score Range:
    - +3.0: 거래량 6배+ 급등 + 가격 상승
    - +1.5: 거래량 3배 급등 + 가격 상승
    - 0.0: 거래량 정상 (Z < 2.0)
    - -1.5 ~ -3.0: 거래량 급등 + 가격 하락

    Confidence Range:
    - 1.0: Z > 4.0 (매우 높은 확신)
    - 0.67: Z = 3.0
    - 0.5: Z = 2.0 (임계값)
    - 0.0: Z < 2.0
    """

    def __init__(self, weight: float = 1.5, lookback: int = 40):
        """
        Args:
            weight: 알파 가중치 (기본 1.5)
            lookback: 거래량 평균 계산 기간 (기본 40 bars)
        """
        super().__init__("VOLUME_SPIKE", weight)
        self.lookback = lookback

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        Volume Spike 알파 계산

        Args:
            symbol: 종목코드
            state: {"df": OHLCV DataFrame, ...}

        Returns:
            AlphaOutput with score and confidence
        """
        df = state.get("df")
        if df is None or len(df) < self.lookback:
            return AlphaOutput(
                name="VOLUME_SPIKE",
                score=0.0,
                confidence=0.0,
                reason="데이터 부족"
            )

        try:
            vol = df["volume"]

            # 1. Z-score 계산
            mean = vol.rolling(self.lookback).mean().iloc[-1]
            std = vol.rolling(self.lookback).std().iloc[-1]
            current = vol.iloc[-1]

            if std == 0 or pd.isna(mean) or pd.isna(std):
                return AlphaOutput(
                    name="VOLUME_SPIKE",
                    score=0.0,
                    confidence=0.0,
                    reason="표준편차 0 또는 NaN"
                )

            z = (current - mean) / std

            # 2. 방향: 최근 수익률
            ret = df["close"].pct_change().iloc[-1]
            if pd.isna(ret):
                ret = 0.0

            direction = np.sign(ret)
            if direction == 0:
                direction = 1.0  # 변화 없으면 양수로 간주

            # 3. Score 계산
            if z > 2.0:
                # Z > 2.0일 때만 의미 있는 신호
                # z=2 → ±1.0, z=4 → ±2.0, z=6 → ±3.0
                score = direction * min(z / 2.0, 3.0)

                # Confidence: z가 클수록 높음
                # z=2 → 0.5, z=4 → 1.0
                confidence = min((z - 2.0) / 2.0 + 0.5, 1.0)
            else:
                # 거래량 정상 → 중립
                score = 0.0
                confidence = 0.0

            # 4. 이유 설명
            if z > 2.0:
                volume_multiple = current / mean if mean > 0 else 0
                price_direction = "상승" if ret > 0 else "하락"
                reason = (
                    f"거래량 {volume_multiple:.1f}배 급등 (Z={z:.1f}), "
                    f"가격 {price_direction} ({ret*100:+.2f}%)"
                )
            else:
                reason = f"거래량 정상 (Z={z:.1f})"

            return AlphaOutput(
                name="VOLUME_SPIKE",
                score=score,
                confidence=confidence,
                reason=reason,
                metadata={
                    "z_score": z,
                    "return": ret,
                    "volume_current": current,
                    "volume_mean": mean,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="VOLUME_SPIKE",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )
