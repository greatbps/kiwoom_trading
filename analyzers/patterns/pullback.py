"""
눌림목 (Pullback) 탐지기

MA5 > MA20 > MA60 정배열 상승추세에서
MA5 부근으로 건강하게 눌린 후 반등하는 패턴.

핵심:
    - 거래량 감소 눌림 = 수급 이탈 없음 = 건강한 조정
    - MA5 터치 후 양봉 = 수급 재개 신호
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import numpy as np

from .base import PatternDetector, PatternResult, Pivot

logger = logging.getLogger(__name__)


class PullbackDetector(PatternDetector):
    """눌림목 탐지기."""

    NAME = "pullback"
    BASE_CONFIDENCE: float = 0.72
    MIN_PIVOTS: int = 3

    MA5_TOL_PCT: float = 0.02
    UPTREND_MA_SLOPE_BARS: int = 5
    VOLUME_DRY_RATIO: float = 0.70
    VOLUME_DRY_LOOKBACK: int = 10
    VOLUME_DRY_BARS: int = 3

    @property
    def name(self) -> str:
        return self.NAME

    def detect(
        self,
        df: pd.DataFrame,
        pivots: list[Pivot],
    ) -> Optional[PatternResult]:
        df = self._normalize_df(df)
        if df is None:
            return None

        if len(df) < 60:
            return None

        ma5 = df['close'].rolling(5).mean()
        ma20 = df['close'].rolling(20).mean()
        ma60 = df['close'].rolling(60).mean()

        if ma5.iloc[-1] is None or pd.isna(ma5.iloc[-1]):
            return None

        uptrend = self.is_uptrend(df, ma5, ma20, ma60)
        near_ma = self.near_ma5(df, ma5)
        vol_dec = self.volume_decrease(df)
        bullish_rev = self.bullish_reversal(df, ma5)

        # 점수 계산 (최대 7점)
        score = 0
        if uptrend:
            score += 3
        if near_ma:
            score += 2
        if vol_dec:
            score += 2

        trigger = bullish_rev and near_ma

        if score < 3:
            return None

        close = float(df['close'].iloc[-1])
        ma5_val = float(ma5.iloc[-1])

        # 손절: MA5 * 0.97 (추세 이탈 기준)
        stop = ma5_val * 0.97

        # 목표: 최근 고점 (최근 20봉 고점)
        recent_high = float(df['high'].iloc[-20:].max())
        target = recent_high

        if trigger:
            phase = "breakout"
            entry = close
        elif near_ma and uptrend:
            phase = "forming"
            entry = close
        else:
            return None

        avg_vol = self._avg_volume(df)
        current_vol = float(df['volume'].iloc[-1]) if 'volume' in df.columns else 0.0
        vol_mult = self._vol_multiplier(current_vol, avg_vol)

        conf = self._calc_confidence(score, vol_mult, phase)

        lows = [p for p in pivots if p.is_low]
        recent_low = lows[-1] if lows else None

        ma5_dist_pct = (close - ma5_val) / ma5_val * 100

        result = PatternResult(
            pattern=self.NAME,
            confidence=min(conf, 0.97),
            entry=round(entry, 0),
            stop=round(stop, 0),
            target=round(target, 0),
            timeframe='daily',
            phase=phase,
            pivots_used=[recent_low] if recent_low else [],
            meta={
                'ma5': round(ma5_val, 0),
                'ma20': round(float(ma20.iloc[-1]), 0),
                'ma60': round(float(ma60.iloc[-1]), 0),
                'ma5_dist_pct': round(ma5_dist_pct, 2),
                'uptrend': uptrend,
                'volume_dry': vol_dec,
                'bullish_reversal': bullish_rev,
                'score': score,
                'trigger': trigger,
            },
        )

        logger.debug(
            f"[PB] {phase} | conf={conf:.2f} | score={score} | "
            f"uptrend={uptrend} near_ma={near_ma} vol_dec={vol_dec} bull={bullish_rev}"
        )
        return result

    def is_uptrend(
        self,
        df: pd.DataFrame,
        ma5: pd.Series,
        ma20: pd.Series,
        ma60: pd.Series,
    ) -> bool:
        """MA5 > MA20 > MA60 정배열 + MA20 기울기 양수."""
        m5 = float(ma5.iloc[-1])
        m20 = float(ma20.iloc[-1])
        m60 = float(ma60.iloc[-1])

        if not (m5 > m20 > m60):
            return False

        # MA20 기울기: 최근 5봉 동안 상승 중
        slope_bars = self.UPTREND_MA_SLOPE_BARS
        if len(ma20) < slope_bars + 1:
            return False

        ma20_slope = float(ma20.iloc[-1]) - float(ma20.iloc[-1 - slope_bars])
        return ma20_slope > 0

    def near_ma5(self, df: pd.DataFrame, ma5: pd.Series) -> bool:
        """현재가가 MA5 기준 ±2% 이내."""
        close = float(df['close'].iloc[-1])
        m5 = float(ma5.iloc[-1])
        if m5 <= 0:
            return False
        dist_pct = abs(close - m5) / m5
        return dist_pct <= self.MA5_TOL_PCT

    def volume_decrease(self, df: pd.DataFrame) -> bool:
        """최근 3봉 거래량 < 직전 10봉 평균의 70% (건강한 눌림 = 수급 이탈 없음)."""
        if 'volume' not in df.columns or len(df) < self.VOLUME_DRY_LOOKBACK + self.VOLUME_DRY_BARS:
            return True

        recent = df['volume'].iloc[-(self.VOLUME_DRY_BARS):]
        prior = df['volume'].iloc[-(self.VOLUME_DRY_LOOKBACK + self.VOLUME_DRY_BARS):-(self.VOLUME_DRY_BARS)]

        if len(prior) == 0:
            return True

        prior_avg = float(prior.mean())
        recent_avg = float(recent.mean())

        if prior_avg <= 0:
            return True

        return recent_avg < prior_avg * self.VOLUME_DRY_RATIO

    def bullish_reversal(self, df: pd.DataFrame, ma5: pd.Series) -> bool:
        """현재봉 양봉 + MA5 부근에서 반등."""
        if len(df) < 2:
            return False
        close = float(df['close'].iloc[-1])
        open_ = float(df['open'].iloc[-1])
        m5 = float(ma5.iloc[-1])

        is_bullish = close > open_
        near = abs(close - m5) / m5 <= self.MA5_TOL_PCT if m5 > 0 else False

        return is_bullish and near

    def _calc_confidence(self, score: int, vol_mult: float, phase: str) -> float:
        score_ratio = score / 7.0
        conf = self.BASE_CONFIDENCE * score_ratio

        conf *= vol_mult

        if phase == "breakout":
            pass
        elif phase == "confirmed":
            conf *= 0.95
        elif phase == "forming":
            conf *= 0.70

        return conf
