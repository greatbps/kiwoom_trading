"""
Momentum Alpha - 모멘텀 기반 추세 추종 전략

RSI, MACD, Rate of Change를 결합한 모멘텀 신호 생성
"""

import numpy as np
import pandas as pd
from .base_alpha import BaseAlpha, AlphaOutput


class MomentumAlpha(BaseAlpha):
    """
    Momentum 기반 알파

    Logic:
    1. RSI (Relative Strength Index): 30 미만 과매도, 70 초과 과매수
    2. MACD (Moving Average Convergence Divergence): MACD > Signal 상승 신호
    3. ROC (Rate of Change): 가격 변화율

    Score Range:
    - +3.0: 강한 상승 모멘텀 (RSI 70+, MACD 상승, ROC 5%+)
    - +1.5: 보통 상승 모멘텀
    - 0.0: 중립
    - -1.5 ~ -3.0: 하락 모멘텀

    Confidence Range:
    - 1.0: 모든 지표 일치 (RSI/MACD/ROC 동일 방향)
    - 0.5: 일부 지표 일치
    - 0.0: 지표 불일치
    """

    def __init__(self, weight: float = 1.0, rsi_period: int = 14, macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9, roc_period: int = 10):
        """
        Args:
            weight: 알파 가중치 (기본 1.0)
            rsi_period: RSI 계산 기간 (기본 14)
            macd_fast: MACD fast EMA (기본 12)
            macd_slow: MACD slow EMA (기본 26)
            macd_signal: MACD signal line (기본 9)
            roc_period: ROC 계산 기간 (기본 10)
        """
        super().__init__("Momentum", weight)
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.roc_period = roc_period

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        Momentum 알파 계산

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
        # 5분봉 우선 사용 (모멘텀은 더 긴 시간 프레임에서 유효)
        df = state.get("df_5m", state.get("df"))

        if df is None or len(df) < max(self.macd_slow, self.rsi_period, self.roc_period) + 10:
            return AlphaOutput(
                name="Momentum",
                score=0.0,
                confidence=0.0,
                reason="데이터 부족"
            )

        try:
            # 1. RSI 계산
            rsi = self._calculate_rsi(df)
            rsi_score, rsi_conf = self._calculate_rsi_score(rsi)

            # 2. MACD 계산
            macd, signal, histogram = self._calculate_macd(df)
            macd_score, macd_conf = self._calculate_macd_score(macd, signal, histogram)

            # 3. ROC 계산
            roc = self._calculate_roc(df)
            roc_score, roc_conf = self._calculate_roc_score(roc)

            # 최종 점수 합산 (가중 평균)
            total_score = (rsi_score * 0.3) + (macd_score * 0.4) + (roc_score * 0.3)
            total_score = np.clip(total_score, -3.0, 3.0)

            # 최종 신뢰도 (모든 지표가 같은 방향이면 높음)
            total_confidence = self._calculate_confidence(rsi_score, macd_score, roc_score, rsi_conf, macd_conf, roc_conf)

            # 이유 설명
            direction = "상승" if total_score > 0 else "하락" if total_score < 0 else "중립"
            reason = (
                f"모멘텀 {direction} (RSI={rsi:.1f}, "
                f"MACD={'상승' if macd > signal else '하락'}, "
                f"ROC={roc:.1f}%)"
            )

            return AlphaOutput(
                name="Momentum",
                score=total_score,
                confidence=total_confidence,
                reason=reason,
                metadata={
                    "rsi": rsi,
                    "macd": macd,
                    "macd_signal": signal,
                    "macd_histogram": histogram,
                    "roc": roc,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="Momentum",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )

    def _calculate_rsi(self, df: pd.DataFrame) -> float:
        """
        RSI (Relative Strength Index) 계산

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        close = df["close"]
        delta = close.diff()

        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return rsi.iloc[-1]

    def _calculate_rsi_score(self, rsi: float) -> tuple:
        """
        RSI를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - RSI > 70: 과매수 → score=+1.0 (단기 상승 가능)
            - RSI 50-70: 상승 → score=+0.5
            - RSI 30-50: 중립 → score=0.0
            - RSI < 30: 과매도 → score=-1.0 (반등 대기)
        """
        if rsi > 70:
            # 과매수 (상승 모멘텀 강함)
            score = min((rsi - 70) / 30, 1.0)  # RSI=100 → 1.0점
            confidence = 0.3
        elif rsi > 50:
            # 상승 중
            score = (rsi - 50) / 40  # RSI=70 → 0.5점
            confidence = 0.2
        elif rsi > 30:
            # 중립
            score = 0.0
            confidence = 0.1
        else:
            # 과매도 (하락 모멘텀 또는 반등 대기)
            score = -((30 - rsi) / 30)  # RSI=0 → -1.0점
            confidence = 0.3

        return score, confidence

    def _calculate_macd(self, df: pd.DataFrame) -> tuple:
        """
        MACD (Moving Average Convergence Divergence) 계산

        Returns:
            (macd, signal, histogram)
        """
        close = df["close"]

        # EMA 계산
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()

        # MACD = Fast EMA - Slow EMA
        macd = ema_fast - ema_slow

        # Signal Line = MACD의 EMA
        signal = macd.ewm(span=self.macd_signal, adjust=False).mean()

        # Histogram = MACD - Signal
        histogram = macd - signal

        return macd.iloc[-1], signal.iloc[-1], histogram.iloc[-1]

    def _calculate_macd_score(self, macd: float, signal: float, histogram: float) -> tuple:
        """
        MACD를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - MACD > Signal (histogram > 0): 상승 신호
            - MACD < Signal (histogram < 0): 하락 신호
        """
        # Histogram의 크기가 클수록 강한 신호
        # 정규화를 위해 최근 가격 대비 비율 사용
        close = histogram  # 단순화

        if histogram > 0:
            # 상승 신호
            score = min(abs(histogram) / 100, 1.5)  # 최대 1.5점
            confidence = min(abs(histogram) / 200, 0.4)  # 최대 0.4
        else:
            # 하락 신호
            score = -min(abs(histogram) / 100, 1.5)
            confidence = min(abs(histogram) / 200, 0.4)

        return score, confidence

    def _calculate_roc(self, df: pd.DataFrame) -> float:
        """
        ROC (Rate of Change) 계산

        ROC = ((현재가 - N일전 가격) / N일전 가격) * 100
        """
        close = df["close"]
        roc = ((close.iloc[-1] - close.iloc[-self.roc_period]) / close.iloc[-self.roc_period]) * 100

        return roc

    def _calculate_roc_score(self, roc: float) -> tuple:
        """
        ROC를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - ROC > 5%: 강한 상승 → score=+1.5
            - ROC > 2%: 상승 → score=+0.5
            - ROC < -5%: 강한 하락 → score=-1.5
        """
        if roc > 5:
            score = min(roc / 5, 1.5)  # ROC=7.5% → 1.5점
            confidence = 0.3
        elif roc > 2:
            score = (roc - 2) / 6  # ROC=5% → 0.5점
            confidence = 0.2
        elif roc > -2:
            score = 0.0
            confidence = 0.1
        elif roc > -5:
            score = (roc + 2) / 6  # ROC=-5% → -0.5점
            confidence = 0.2
        else:
            score = max(roc / 5, -1.5)  # ROC=-7.5% → -1.5점
            confidence = 0.3

        return score, confidence

    def _calculate_confidence(self, rsi_score: float, macd_score: float, roc_score: float,
                            rsi_conf: float, macd_conf: float, roc_conf: float) -> float:
        """
        전체 신뢰도 계산 (모든 지표가 같은 방향이면 높음)

        Returns:
            confidence (0 ~ 1)
        """
        # 모든 지표의 방향 확인
        signs = [np.sign(rsi_score), np.sign(macd_score), np.sign(roc_score)]

        # 같은 방향인 지표 개수
        if signs.count(signs[0]) == 3 and signs[0] != 0:
            # 모두 일치 (상승 또는 하락)
            base_confidence = 0.9
        elif signs.count(signs[0]) == 2 or signs.count(signs[1]) == 2:
            # 2개 일치
            base_confidence = 0.6
        else:
            # 불일치
            base_confidence = 0.3

        # 개별 신뢰도 평균과 결합
        avg_conf = (rsi_conf + macd_conf + roc_conf) / 3
        total_confidence = (base_confidence + avg_conf) / 2

        return min(total_confidence, 1.0)
