"""
Mean Reversion Alpha - 평균 회귀 전략

Bollinger Bands, Z-Score, Stochastic을 결합한 평균 회귀 신호 생성
"""

import numpy as np
import pandas as pd
from .base_alpha import BaseAlpha, AlphaOutput


class MeanReversionAlpha(BaseAlpha):
    """
    Mean Reversion 기반 알파

    Logic:
    1. Bollinger Bands: 가격이 상단/하단 밴드에서 반등
    2. Z-Score: 가격의 표준편차 거리 (±2σ 이상에서 반전)
    3. Stochastic: %K와 %D의 과매수/과매도 교차

    Score Range:
    - +3.0: 강한 반등 신호 (하단 밴드 터치 + 과매도)
    - +1.5: 보통 반등 신호
    - 0.0: 중립 (평균 근처)
    - -1.5 ~ -3.0: 하락 신호 (상단 밴드 터치 + 과매수)

    Confidence Range:
    - 1.0: 모든 지표 일치 (BB/Z-Score/Stochastic 동일 방향)
    - 0.5: 일부 지표 일치
    - 0.0: 지표 불일치

    Note:
    - 평균 회귀는 레인지 장세에서 유효
    - 트렌드 장세에서는 역행 위험
    """

    def __init__(self, weight: float = 0.8, bb_period: int = 20, bb_std: float = 2.0,
                 zscore_period: int = 20, stoch_k: int = 14, stoch_d: int = 3):
        """
        Args:
            weight: 알파 가중치 (기본 0.8 - 보조 전략)
            bb_period: Bollinger Bands 기간 (기본 20)
            bb_std: Bollinger Bands 표준편차 배수 (기본 2.0)
            zscore_period: Z-Score 계산 기간 (기본 20)
            stoch_k: Stochastic %K 기간 (기본 14)
            stoch_d: Stochastic %D 기간 (기본 3)
        """
        super().__init__("MeanReversion", weight)
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.zscore_period = zscore_period
        self.stoch_k = stoch_k
        self.stoch_d = stoch_d

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        Mean Reversion 알파 계산

        Args:
            symbol: 종목코드
            state: {
                "df": 1분봉 DataFrame,
                "df_5m": 5분봉 DataFrame (optional),
                ...
            }

        Returns:
            AlphaOutput with score (-3 ~ +3) and confidence (0 ~ 1)
        """
        # 5분봉 우선 사용
        df = state.get("df_5m", state.get("df"))

        if df is None or len(df) < max(self.bb_period, self.zscore_period, self.stoch_k) + 10:
            return AlphaOutput(
                name="MeanReversion",
                score=0.0,
                confidence=0.0,
                reason="데이터 부족"
            )

        try:
            current_price = df["close"].iloc[-1]

            # 1. Bollinger Bands 계산
            upper, middle, lower, bb_position = self._calculate_bollinger_bands(df)
            bb_score, bb_conf = self._calculate_bb_score(bb_position, current_price, upper, lower)

            # 2. Z-Score 계산
            zscore = self._calculate_zscore(df)
            zscore_score, zscore_conf = self._calculate_zscore_score(zscore)

            # 3. Stochastic 계산
            stoch_k, stoch_d = self._calculate_stochastic(df)
            stoch_score, stoch_conf = self._calculate_stochastic_score(stoch_k, stoch_d)

            # 최종 점수 합산 (가중 평균)
            total_score = (bb_score * 0.4) + (zscore_score * 0.3) + (stoch_score * 0.3)
            total_score = np.clip(total_score, -3.0, 3.0)

            # 최종 신뢰도 (모든 지표가 같은 방향이면 높음)
            total_confidence = self._calculate_confidence(bb_score, zscore_score, stoch_score,
                                                         bb_conf, zscore_conf, stoch_conf)

            # 이유 설명
            if total_score > 0.5:
                direction = "반등 매수"
            elif total_score < -0.5:
                direction = "반락 매도"
            else:
                direction = "중립"

            reason = (
                f"평균 회귀 {direction} (BB={bb_position:.1f}%, "
                f"Z={zscore:.1f}σ, Stoch={stoch_k:.0f})"
            )

            return AlphaOutput(
                name="MeanReversion",
                score=total_score,
                confidence=total_confidence,
                reason=reason,
                metadata={
                    "bb_upper": upper,
                    "bb_middle": middle,
                    "bb_lower": lower,
                    "bb_position": bb_position,
                    "zscore": zscore,
                    "stoch_k": stoch_k,
                    "stoch_d": stoch_d,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="MeanReversion",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )

    def _calculate_bollinger_bands(self, df: pd.DataFrame) -> tuple:
        """
        Bollinger Bands 계산

        Returns:
            (upper, middle, lower, position)
            position: 현재가의 밴드 내 위치 (0~100%)
        """
        close = df["close"]

        # Middle Band = SMA
        middle = close.rolling(window=self.bb_period).mean()

        # Standard Deviation
        std = close.rolling(window=self.bb_period).std()

        # Upper/Lower Bands
        upper = middle + (std * self.bb_std)
        lower = middle - (std * self.bb_std)

        # 현재가의 밴드 내 위치 (0% = lower, 100% = upper)
        current_price = close.iloc[-1]
        band_width = upper.iloc[-1] - lower.iloc[-1]

        if band_width > 0:
            position = ((current_price - lower.iloc[-1]) / band_width) * 100
        else:
            position = 50  # 밴드가 0이면 중립

        return upper.iloc[-1], middle.iloc[-1], lower.iloc[-1], position

    def _calculate_bb_score(self, position: float, price: float, upper: float, lower: float) -> tuple:
        """
        Bollinger Bands 위치를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - position < 10%: 하단 밴드 터치 → score=+1.5 (매수)
            - position > 90%: 상단 밴드 터치 → score=-1.5 (매도)
            - position 40~60%: 중립 → score=0.0
        """
        if position < 10:
            # 하단 밴드 터치 (과매도, 반등 기대)
            score = 1.5 - (position / 20)  # position=0 → 1.5점
            confidence = 0.4
        elif position > 90:
            # 상단 밴드 터치 (과매수, 반락 기대)
            score = -1.5 + ((100 - position) / 20)  # position=100 → -1.5점
            confidence = 0.4
        elif position < 30:
            # 하단 근처
            score = (30 - position) / 40  # position=20 → 0.25점
            confidence = 0.2
        elif position > 70:
            # 상단 근처
            score = -(position - 70) / 40  # position=80 → -0.25점
            confidence = 0.2
        else:
            # 중립 (밴드 중앙)
            score = 0.0
            confidence = 0.1

        return score, confidence

    def _calculate_zscore(self, df: pd.DataFrame) -> float:
        """
        Z-Score 계산 (가격의 표준편차 거리)

        Z = (현재가 - 평균) / 표준편차
        """
        close = df["close"]
        mean = close.rolling(window=self.zscore_period).mean().iloc[-1]
        std = close.rolling(window=self.zscore_period).std().iloc[-1]
        current = close.iloc[-1]

        if std == 0:
            return 0.0

        zscore = (current - mean) / std
        return zscore

    def _calculate_zscore_score(self, zscore: float) -> tuple:
        """
        Z-Score를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - zscore < -2: 과매도 → score=+1.0 (매수)
            - zscore > +2: 과매수 → score=-1.0 (매도)
        """
        if zscore < -2:
            # 과매도 (평균에서 -2σ 이상 이탈)
            score = min((-zscore - 2) / 2, 1.0)  # z=-4 → 1.0점
            confidence = 0.3
        elif zscore > 2:
            # 과매수 (평균에서 +2σ 이상 이탈)
            score = -min((zscore - 2) / 2, 1.0)  # z=+4 → -1.0점
            confidence = 0.3
        elif zscore < -1:
            # 약한 과매도
            score = (-zscore - 1) / 2  # z=-2 → 0.5점
            confidence = 0.15
        elif zscore > 1:
            # 약한 과매수
            score = -(zscore - 1) / 2  # z=+2 → -0.5점
            confidence = 0.15
        else:
            # 중립
            score = 0.0
            confidence = 0.1

        return score, confidence

    def _calculate_stochastic(self, df: pd.DataFrame) -> tuple:
        """
        Stochastic Oscillator 계산

        %K = ((현재가 - N일 최저가) / (N일 최고가 - N일 최저가)) * 100
        %D = %K의 M일 이동평균

        Returns:
            (stoch_k, stoch_d)
        """
        high = df["high"]
        low = df["low"]
        close = df["close"]

        # 최근 K일간 최고/최저
        lowest_low = low.rolling(window=self.stoch_k).min()
        highest_high = high.rolling(window=self.stoch_k).max()

        # %K 계산
        stoch_k = ((close - lowest_low) / (highest_high - lowest_low)) * 100

        # %D 계산 (K의 이동평균)
        stoch_d = stoch_k.rolling(window=self.stoch_d).mean()

        return stoch_k.iloc[-1], stoch_d.iloc[-1]

    def _calculate_stochastic_score(self, stoch_k: float, stoch_d: float) -> tuple:
        """
        Stochastic을 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - K < 20 and K > D: 과매도에서 반등 → score=+1.0
            - K > 80 and K < D: 과매수에서 반락 → score=-1.0
        """
        # %K와 %D의 교차 확인
        k_cross_up = stoch_k > stoch_d  # K가 D를 상향 돌파
        k_cross_down = stoch_k < stoch_d  # K가 D를 하향 돌파

        if stoch_k < 20:
            # 과매도 구간
            if k_cross_up:
                # 상향 돌파 (매수 신호)
                score = 1.0
                confidence = 0.4
            else:
                score = 0.5
                confidence = 0.2
        elif stoch_k > 80:
            # 과매수 구간
            if k_cross_down:
                # 하향 돌파 (매도 신호)
                score = -1.0
                confidence = 0.4
            else:
                score = -0.5
                confidence = 0.2
        elif stoch_k < 40:
            # 약한 과매도
            score = (40 - stoch_k) / 40  # k=20 → 0.5점
            confidence = 0.15
        elif stoch_k > 60:
            # 약한 과매수
            score = -(stoch_k - 60) / 40  # k=80 → -0.5점
            confidence = 0.15
        else:
            # 중립
            score = 0.0
            confidence = 0.1

        return score, confidence

    def _calculate_confidence(self, bb_score: float, zscore_score: float, stoch_score: float,
                            bb_conf: float, zscore_conf: float, stoch_conf: float) -> float:
        """
        전체 신뢰도 계산 (모든 지표가 같은 방향이면 높음)

        Returns:
            confidence (0 ~ 1)
        """
        # 모든 지표의 방향 확인
        signs = [np.sign(bb_score), np.sign(zscore_score), np.sign(stoch_score)]

        # 같은 방향인 지표 개수
        if signs.count(signs[0]) == 3 and signs[0] != 0:
            # 모두 일치 (반등 또는 반락)
            base_confidence = 0.9
        elif signs.count(signs[0]) == 2 or signs.count(signs[1]) == 2:
            # 2개 일치
            base_confidence = 0.6
        else:
            # 불일치 (평균 회귀 신호 약함)
            base_confidence = 0.3

        # 개별 신뢰도 평균과 결합
        avg_conf = (bb_conf + zscore_conf + stoch_conf) / 3
        total_confidence = (base_confidence + avg_conf) / 2

        return min(total_confidence, 1.0)
