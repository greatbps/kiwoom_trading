"""
박스 돌파 (Box Breakout) 탐지기

Bulkowski 통계: 성공률 55%

구조:
    일정 기간(기본 30봉) 동안 고점/저점이 좁은 범위를 유지
    → 저항선 3회 이상 터치
    → 종가 기준 저항선 1% 이상 돌파

핵심:
    박스 상단의 반복 저항 = 매도 물량 집중
    돌파 = 그 물량을 흡수한 강한 수요 신호
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import numpy as np

from .base import PatternDetector, PatternResult, Pivot

logger = logging.getLogger(__name__)


class BoxBreakoutDetector(PatternDetector):
    """박스 돌파 탐지기."""

    NAME = "box_breakout"
    BASE_CONFIDENCE: float = 0.55
    MIN_PIVOTS: int = 3

    RANGE_MAX_PCT: float = 0.08
    LOOKBACK_BARS: int = 30
    RESISTANCE_TOUCH_MIN: int = 3
    RESISTANCE_TOL_PCT: float = 0.01
    BREAKOUT_CONFIRM_PCT: float = 0.01

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

        if len(df) < self.LOOKBACK_BARS + 5:
            return None

        lookback_df = df.iloc[-(self.LOOKBACK_BARS + 1):-1]  # 전봉까지
        recent_pivots = [p for p in pivots if p.idx >= len(df) - self.LOOKBACK_BARS - 1]

        range_valid, avg_price, box_high, box_low = self.is_range(lookback_df)
        if not range_valid:
            return None

        touch_valid, resistance_level = self.multiple_resistance_touch(
            recent_pivots, lookback_df
        )

        # 점수 계산 (최대 5점)
        score = 3  # is_range 통과
        if touch_valid:
            score += 2

        if score < 3:
            return None

        # 돌파 판정
        current_close = float(df['close'].iloc[-1])
        breakout_line = resistance_level * (1 + self.BREAKOUT_CONFIRM_PCT)
        trigger = current_close > breakout_line

        if trigger:
            phase = "breakout"
            entry = current_close
        else:
            phase = "forming"
            entry = breakout_line

        # 손절: 박스 하단
        stop = box_low * 0.997

        # 목표: 박스 높이만큼 상승
        box_height = box_high - box_low
        target = resistance_level + box_height

        avg_vol = self._avg_volume(df)
        current_vol = float(df['volume'].iloc[-1]) if 'volume' in df.columns else 0.0
        vol_mult = self._vol_multiplier(current_vol, avg_vol)

        conf = self._calc_confidence(score, touch_valid, vol_mult, phase)

        result = PatternResult(
            pattern=self.NAME,
            confidence=min(conf, 0.97),
            entry=round(entry, 0),
            stop=round(stop, 0),
            target=round(target, 0),
            timeframe='daily',
            phase=phase,
            pivots_used=recent_pivots[-3:] if len(recent_pivots) >= 3 else recent_pivots,
            meta={
                'box_high': round(box_high, 0),
                'box_low': round(box_low, 0),
                'resistance_level': round(resistance_level, 0),
                'box_range_pct': round((box_high - box_low) / avg_price * 100, 1),
                'touch_count': self._count_touches(recent_pivots, resistance_level),
                'score': score,
                'trigger': trigger,
            },
        )

        logger.debug(
            f"[BOX] {phase} | conf={conf:.2f} | score={score} | "
            f"box_high={box_high:.0f} box_low={box_low:.0f} | touch={touch_valid}"
        )
        return result

    def is_range(
        self,
        df: pd.DataFrame,
        lookback: int = 30,
    ) -> tuple[bool, float, float, float]:
        """
        고-저 범위 / 평균가 < 8% → 박스 구간.

        Returns:
            (valid, avg_price, box_high, box_low)
        """
        if len(df) == 0:
            return False, 0.0, 0.0, 0.0

        box_high = float(df['high'].max())
        box_low = float(df['low'].min())
        avg_price = float(df['close'].mean())

        if avg_price <= 0:
            return False, 0.0, 0.0, 0.0

        range_pct = (box_high - box_low) / avg_price
        if range_pct > self.RANGE_MAX_PCT:
            logger.debug(f"[BOX] SKIP 범위 과대: {range_pct:.1%}")
            return False, avg_price, box_high, box_low

        return True, avg_price, box_high, box_low

    def multiple_resistance_touch(
        self,
        pivots: list[Pivot],
        lookback_df: pd.DataFrame,
    ) -> tuple[bool, float]:
        """
        박스 상단 저항선 3회 이상 터치 확인.

        고점 피벗들의 클러스터 기반으로 저항선 추정.

        Returns:
            (valid, resistance_level)
        """
        highs = [p for p in pivots if p.is_high]

        if not highs:
            # 피벗이 없으면 lookback_df 고점 기반으로 대체
            box_high = float(lookback_df['high'].max()) if len(lookback_df) > 0 else 0.0
            return False, box_high

        # 고점 중 최고가 = 저항선 기준
        resistance = max(h.price for h in highs)

        touch_count = self._count_touches(highs, resistance)
        if touch_count < self.RESISTANCE_TOUCH_MIN:
            logger.debug(f"[BOX] SKIP 저항 터치 부족: {touch_count}회")
            return False, resistance

        return True, resistance

    def _count_touches(self, pivots: list[Pivot], resistance: float) -> int:
        """저항선 ±1% 이내에 들어온 피벗 수."""
        if resistance <= 0:
            return 0
        count = 0
        for p in pivots:
            if p.is_high:
                diff = abs(p.price - resistance) / resistance
                if diff <= self.RESISTANCE_TOL_PCT:
                    count += 1
        return count

    def _calc_confidence(
        self,
        score: int,
        touch_valid: bool,
        vol_mult: float,
        phase: str,
    ) -> float:
        score_ratio = score / 5.0
        conf = self.BASE_CONFIDENCE * score_ratio

        if touch_valid:
            conf *= 1.10

        conf *= vol_mult

        if phase == "breakout":
            pass
        elif phase == "confirmed":
            conf *= 0.95
        elif phase == "forming":
            conf *= 0.70

        return conf
