"""
VWAP Alpha - 기존 VWAP 전략을 알파로 변환

Volume Weighted Average Price 돌파/이탈 전략
"""

import numpy as np
import pandas as pd
from .base_alpha import BaseAlpha, AlphaOutput


class VWAPAlpha(BaseAlpha):
    """
    VWAP (Volume Weighted Average Price) 기반 알파

    Logic:
    1. VWAP 돌파 강도: (현재가 - VWAP) / VWAP
    2. EMA 정렬: 5분봉 EMA(5) > EMA(15) > EMA(60)
    3. 거래량 증가: Z-score > 2.0

    Score Range:
    - +3.0: 강한 돌파 (>1% + EMA 정렬 + 거래량 급증)
    - +1.5: 보통 돌파 (0.5% + EMA 정렬)
    - 0.0: VWAP 근처
    - -1.5 ~ -3.0: 이탈

    Confidence Range:
    - 1.0: 모든 조건 충족 (VWAP 1%+ 돌파 + EMA 3단 정렬 + 거래량 z>3)
    - 0.5: 일부 조건 충족
    - 0.0: 조건 미달
    """

    def __init__(self, weight: float = 2.0, volume_lookback: int = 40):
        """
        Args:
            weight: 알파 가중치 (기본 2.0 - 기존 검증된 전략)
            volume_lookback: 거래량 평균 계산 기간 (기본 40 bars)
        """
        super().__init__("VWAP", weight)
        self.volume_lookback = volume_lookback

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        """
        VWAP 알파 계산

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
        df = state.get("df")
        if df is None or len(df) < self.volume_lookback:
            return AlphaOutput(
                name="VWAP",
                score=0.0,
                confidence=0.0,
                reason="데이터 부족"
            )

        try:
            # 현재가
            current_price = df["close"].iloc[-1]

            # 1. VWAP 계산 및 돌파 강도
            vwap = self._calculate_vwap(df)
            vwap_diff = (current_price - vwap) / vwap  # 비율
            vwap_score = np.clip(vwap_diff * 300, -1.5, 1.5)  # 0.5% 돌파 = 1.5점

            # VWAP 신뢰도 (돌파/이탈이 클수록 높음)
            vwap_conf = min(abs(vwap_diff) * 200, 0.3)  # 최대 0.3

            # 2. EMA 정렬 (5분봉 우선, 없으면 1분봉)
            df_for_ema = state.get("df_5m", df)
            ema_score, ema_conf = self._calculate_ema_alignment(df_for_ema)

            # 3. 거래량 증가
            volume_z = self._calculate_volume_z(df)
            volume_score, volume_conf = self._calculate_volume_score(volume_z)

            # 최종 점수 합산
            total_score = vwap_score + ema_score + volume_score
            total_score = np.clip(total_score, -3.0, 3.0)

            # 최종 신뢰도 합산
            total_confidence = min(vwap_conf + ema_conf + volume_conf, 1.0)

            # 이유 설명
            direction = "돌파" if vwap_diff > 0 else "이탈"
            ema_status = "정렬" if ema_score > 0 else "역배열"
            reason = (
                f"VWAP {direction} {abs(vwap_diff)*100:.2f}%, "
                f"EMA {ema_status}, "
                f"Vol Z={volume_z:.1f}"
            )

            return AlphaOutput(
                name="VWAP",
                score=total_score,
                confidence=total_confidence,
                reason=reason,
                metadata={
                    "vwap": vwap,
                    "price": current_price,
                    "vwap_diff_pct": vwap_diff * 100,
                    "volume_z": volume_z,
                }
            )

        except Exception as e:
            return AlphaOutput(
                name="VWAP",
                score=0.0,
                confidence=0.0,
                reason=f"계산 오류: {str(e)}"
            )

    def _calculate_vwap(self, df: pd.DataFrame) -> float:
        """
        VWAP (Volume Weighted Average Price) 계산

        VWAP = Σ(typical_price × volume) / Σ(volume)
        typical_price = (high + low + close) / 3
        """
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        vwap = (typical_price * df["volume"]).sum() / df["volume"].sum()
        return vwap

    def _calculate_ema_alignment(self, df: pd.DataFrame) -> tuple:
        """
        EMA 정렬 확인

        Returns:
            (score, confidence)
            - 3단 정렬 (5 > 15 > 60): score=1.0, conf=0.4
            - 2단 정렬 (5 > 15): score=0.5, conf=0.2
            - 역배열: score=0.0, conf=0.0
        """
        if len(df) < 60:
            return 0.0, 0.0

        ema_5 = df["close"].ewm(span=5).mean().iloc[-1]
        ema_15 = df["close"].ewm(span=15).mean().iloc[-1]
        ema_60 = df["close"].ewm(span=60).mean().iloc[-1]

        if ema_5 > ema_15 > ema_60:
            return 1.0, 0.4  # 완벽한 정렬
        elif ema_5 > ema_15:
            return 0.5, 0.2  # 부분 정렬
        else:
            return 0.0, 0.0  # 역배열

    def _calculate_volume_z(self, df: pd.DataFrame) -> float:
        """
        거래량 Z-score 계산

        Z = (현재 거래량 - 평균) / 표준편차
        """
        vol = df["volume"]
        mean = vol.rolling(self.volume_lookback).mean().iloc[-1]
        std = vol.rolling(self.volume_lookback).std().iloc[-1]
        current = vol.iloc[-1]

        if std == 0:
            return 0.0

        z = (current - mean) / std
        return z

    def _calculate_volume_score(self, volume_z: float) -> tuple:
        """
        거래량 Z-score를 점수와 신뢰도로 변환

        Returns:
            (score, confidence)
            - z > 3.0: score=0.5, conf=0.3
            - z = 2.0: score=0.25, conf=0.15
            - z < 2.0: score=0.0, conf=0.0
        """
        if volume_z > 2.0:
            # 거래량 급증
            score = min((volume_z - 2.0) / 4.0, 0.5)  # z=6 → 0.5점
            confidence = min((volume_z - 2.0) / 4.0, 0.3)  # z=6 → 0.3
            return score, confidence
        else:
            return 0.0, 0.0
