"""
Volatility Alpha - 변동성 기반 전략

ATR, Bollinger Band Width, Historical Volatility를 결합한 변동성 신호 생성
"""

import numpy as np
import pandas as pd
from .base_alpha import BaseAlpha, AlphaOutput


class VolatilityAlpha(BaseAlpha):
    """
    Volatility 기반 알파

    Logic:
    1. ATR (Average True Range): 변동성 크기 측정
    2. BB Width: Bollinger Band 폭 (변동성 확대/축소)
    3. Historical Volatility: 과거 변동성 비교

    Strategy:
    - 변동성 축소 → 확대 전환: 큰 움직임 예상 (Breakout 준비)
    - 변동성 확대: 트렌드 지속 가능성
    - 변동성 과도: 조정 가능성

    Score Range:
    - +3.0: 변동성 확대 + 상승 (Breakout)
    - +1.5: 보통 변동성 증가
    - 0.0: 변동성 안정
    - -1.5 ~ -3.0: 변동성 축소 (Consolidation)

    Confidence Range:
    - 1.0: 모든 지표 일치
    - 0.5: 일부 지표 일치
    - 0.0: 지표 불일치
    """

    def __init__(self, weight: float = 0.6, atr_period: int = 14,
                 bb_period: int = 20, bb_std: float = 2.0,
                 hv_period: int = 20):
        """
        Args:
            weight: 알파 가중치 (기본 0.6 - 보조 전략)
            atr_period: ATR 계산 기간 (기본 14)
            bb_period: BB Width 계산 기간 (기본 20)
            bb_std: BB 표준편차 배수 (기본 2.0)
            hv_period: Historical Volatility 계산 기간 (기본 20)
        """
        super().__init__("Volatility", weight)
        self.atr_period = atr_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.hv_period = hv_period

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        Volatility 알파 계산

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

        if df is None or len(df) < max(self.atr_period, self.bb_period, self.hv_period) + 10:
            return AlphaOutput(
                name="Volatility",
                score=0.0,
                confidence=0.0,
                reason="데이터 부족"
            )

        try:
            # 1. ATR 계산
            atr = self._calculate_atr(df)
            atr_score, atr_conf = self._calculate_atr_score(df, atr)

            # 2. Bollinger Band Width 계산
            bb_width, bb_width_pct = self._calculate_bb_width(df)
            bbw_score, bbw_conf = self._calculate_bbw_score(bb_width_pct)

            # 3. Historical Volatility 계산
            hv = self._calculate_historical_volatility(df)
            hv_score, hv_conf = self._calculate_hv_score(df, hv)

            # 최종 점수 합산 (가중 평균)
            total_score = (atr_score * 0.4) + (bbw_score * 0.3) + (hv_score * 0.3)
            total_score = np.clip(total_score, -3.0, 3.0)

            # 최종 신뢰도
            total_confidence = self._calculate_confidence(atr_score, bbw_score, hv_score,
                                                         atr_conf, bbw_conf, hv_conf)

            # 이유 설명
            if total_score > 0.5:
                direction = "확대 (Breakout 준비)"
            elif total_score < -0.5:
                direction = "축소 (Consolidation)"
            else:
                direction = "안정"

            reason = (
                f"변동성 {direction} (ATR={atr:.0f}, "
                f"BBW={bb_width_pct:.1f}%, HV={hv:.2f})"
            )

            return AlphaOutput(
                name="Volatility",
                score=total_score,
                confidence=total_confidence,
                reason=reason,
                metadata={
                    "atr": atr,
                    "bb_width": bb_width,
                    "bb_width_pct": bb_width_pct,
                    "historical_volatility": hv,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="Volatility",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )

    def _calculate_atr(self, df: pd.DataFrame) -> float:
        """
        ATR (Average True Range) 계산

        True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        ATR = True Range의 이동평균
        """
        high = df["high"]
        low = df["low"]
        close = df["close"]

        # True Range 계산
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # ATR = TR의 이동평균
        atr = true_range.rolling(window=self.atr_period).mean()

        return atr.iloc[-1]

    def _calculate_atr_score(self, df: pd.DataFrame, atr: float) -> tuple:
        """
        ATR을 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - ATR이 평균 대비 높으면 변동성 확대 → score > 0
            - ATR이 평균 대비 낮으면 변동성 축소 → score < 0
        """
        # ATR의 이동평균 (40일)
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = true_range.rolling(window=self.atr_period).mean()

        atr_mean = atr_series.rolling(window=40).mean().iloc[-1]
        atr_std = atr_series.rolling(window=40).std().iloc[-1]

        if atr_std == 0:
            return 0.0, 0.1

        # ATR Z-Score
        atr_z = (atr - atr_mean) / atr_std

        if atr_z > 2:
            # 변동성 매우 높음 (과도, 조정 가능성)
            score = -min((atr_z - 2) / 2, 1.0)  # z=4 → -1.0점
            confidence = 0.3
        elif atr_z > 1:
            # 변동성 증가 중 (Breakout 가능)
            score = (atr_z - 1) / 2  # z=2 → 0.5점
            confidence = 0.3
        elif atr_z > 0:
            # 변동성 보통
            score = atr_z / 2  # z=1 → 0.5점
            confidence = 0.2
        elif atr_z > -1:
            # 변동성 약간 감소
            score = atr_z / 2  # z=-1 → -0.5점
            confidence = 0.2
        else:
            # 변동성 매우 낮음 (Consolidation, Breakout 준비)
            score = max(atr_z / 2, -1.5)  # z=-3 → -1.5점
            confidence = 0.3

        return score, confidence

    def _calculate_bb_width(self, df: pd.DataFrame) -> tuple:
        """
        Bollinger Band Width 계산

        BB Width = (Upper Band - Lower Band) / Middle Band

        Returns:
            (bb_width, bb_width_percentile)
        """
        close = df["close"]

        # Bollinger Bands
        middle = close.rolling(window=self.bb_period).mean()
        std = close.rolling(window=self.bb_period).std()

        upper = middle + (std * self.bb_std)
        lower = middle - (std * self.bb_std)

        # BB Width (절대값)
        bb_width = (upper - lower).iloc[-1]

        # BB Width의 백분위 (최근 60일 대비)
        bb_width_series = upper - lower
        bb_width_pct = (bb_width_series.iloc[-60:] < bb_width).sum() / 60 * 100

        return bb_width, bb_width_pct

    def _calculate_bbw_score(self, bb_width_pct: float) -> tuple:
        """
        BB Width 백분위를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - bb_width_pct > 80%: 변동성 확대 → score > 0
            - bb_width_pct < 20%: 변동성 축소 (Squeeze) → score < 0
        """
        if bb_width_pct > 80:
            # 변동성 높음 (상위 20%)
            score = (bb_width_pct - 80) / 40  # 90% → 0.25점, 100% → 0.5점
            confidence = 0.3
        elif bb_width_pct > 60:
            # 변동성 증가 중
            score = (bb_width_pct - 60) / 80  # 70% → 0.125점
            confidence = 0.2
        elif bb_width_pct < 20:
            # 변동성 낮음 (Squeeze, Breakout 준비)
            score = -(20 - bb_width_pct) / 40  # 10% → -0.25점, 0% → -0.5점
            confidence = 0.3
        elif bb_width_pct < 40:
            # 변동성 감소 중
            score = -(40 - bb_width_pct) / 80  # 30% → -0.125점
            confidence = 0.2
        else:
            # 변동성 보통
            score = 0.0
            confidence = 0.1

        return score, confidence

    def _calculate_historical_volatility(self, df: pd.DataFrame) -> float:
        """
        Historical Volatility 계산 (연환산)

        HV = std(log returns) × sqrt(252)
        """
        close = df["close"]

        # 로그 수익률
        log_returns = np.log(close / close.shift(1))

        # 표준편차 (최근 hv_period)
        std = log_returns.rolling(window=self.hv_period).std().iloc[-1]

        # 연환산 (하루 252 거래일 기준, 분봉이므로 추가 조정 필요)
        # 간단하게 일간 변환: 5분봉 기준 78개 bar = 1일
        hv_annual = std * np.sqrt(252 * 78)

        return hv_annual

    def _calculate_hv_score(self, df: pd.DataFrame, hv: float) -> tuple:
        """
        Historical Volatility를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - HV가 평균 대비 높으면 변동성 확대 → score > 0
        """
        close = df["close"]
        log_returns = np.log(close / close.shift(1))

        # HV 시계열
        hv_series = log_returns.rolling(window=self.hv_period).std() * np.sqrt(252 * 78)

        # HV 평균 및 표준편차 (최근 60일)
        hv_mean = hv_series.iloc[-60:].mean()
        hv_std = hv_series.iloc[-60:].std()

        if hv_std == 0:
            return 0.0, 0.1

        # HV Z-Score
        hv_z = (hv - hv_mean) / hv_std

        if hv_z > 2:
            # 변동성 매우 높음 (과도)
            score = -min((hv_z - 2) / 2, 1.0)
            confidence = 0.3
        elif hv_z > 1:
            # 변동성 증가
            score = (hv_z - 1) / 2
            confidence = 0.3
        elif hv_z > 0:
            # 변동성 보통
            score = hv_z / 2
            confidence = 0.2
        elif hv_z > -1:
            # 변동성 감소
            score = hv_z / 2
            confidence = 0.2
        else:
            # 변동성 매우 낮음
            score = max(hv_z / 2, -1.5)
            confidence = 0.3

        return score, confidence

    def _calculate_confidence(self, atr_score: float, bbw_score: float, hv_score: float,
                            atr_conf: float, bbw_conf: float, hv_conf: float) -> float:
        """
        전체 신뢰도 계산 (모든 지표가 같은 방향이면 높음)

        Returns:
            confidence (0 ~ 1)
        """
        # 모든 지표의 방향 확인
        signs = [np.sign(atr_score), np.sign(bbw_score), np.sign(hv_score)]

        # 같은 방향인 지표 개수
        if signs.count(signs[0]) == 3 and signs[0] != 0:
            # 모두 일치
            base_confidence = 0.9
        elif signs.count(signs[0]) == 2 or signs.count(signs[1]) == 2:
            # 2개 일치
            base_confidence = 0.6
        else:
            # 불일치
            base_confidence = 0.3

        # 개별 신뢰도 평균과 결합
        avg_conf = (atr_conf + bbw_conf + hv_conf) / 3
        total_confidence = (base_confidence + avg_conf) / 2

        return min(total_confidence, 1.0)
