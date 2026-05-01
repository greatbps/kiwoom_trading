"""
컵 앤 핸들 (Cup & Handle) 탐지기

Bulkowski 통계: 성공률 61%

구조:
    좌측 고점 → 컵 저점 → 우측 고점 (U자형 컵)
              → 핸들 (우측 고점 아래 소폭 조정)
              → 핸들 상단 돌파

거래량 패턴:
    컵 형성 중 거래량 감소, 핸들 중 컵 평균 대비 70% 미만
    돌파 시 거래량 급증 = 신뢰 높음
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import numpy as np

from .base import PatternDetector, PatternResult, Pivot

logger = logging.getLogger(__name__)


class CupHandleDetector(PatternDetector):
    """컵 앤 핸들 탐지기."""

    NAME = "cup_handle"
    BASE_CONFIDENCE: float = 0.61
    MIN_PIVOTS: int = 4

    CUP_DEPTH_MIN: float = 0.15
    CUP_DEPTH_MAX: float = 0.50
    CUP_SYMMETRY_TOL: float = 0.05
    HANDLE_MAX_RETRACE: float = 0.15
    HANDLE_MIN_BARS: int = 5
    HANDLE_MAX_BARS: int = 30
    VOLUME_DRY_RATIO: float = 0.70

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

        highs = [p for p in pivots if p.is_high]
        lows = [p for p in pivots if p.is_low]

        if len(highs) < 2 or not lows:
            return None

        # 컵 우측 고점 = 가장 최근 고점
        cup_right = highs[-1]

        # 컵 저점 = 우측 고점 직전 저점
        trough_candidates = [l for l in lows if l.idx < cup_right.idx]
        if not trough_candidates:
            return None
        cup_trough = trough_candidates[-1]

        # 컵 좌측 고점 = 저점 이전 고점
        left_candidates = [h for h in highs if h.idx < cup_trough.idx]
        if not left_candidates:
            return None
        cup_left = left_candidates[-1]

        # 컵 형태 검증
        if not self.is_cup_shape(cup_left, cup_trough, cup_right):
            return None

        # 핸들 검증
        handle_valid, handle_bars, handle_high = self.is_handle_consolidation(
            df, cup_right.idx
        )
        if not handle_valid:
            return None

        # 거래량 수축
        vol_dry = self.volume_dry_up(df, cup_right.idx)

        # 점수 계산 (최대 7점)
        score = 3  # 컵 형태 확인
        score += 2  # 핸들 확인 (이미 valid)
        if vol_dry:
            score += 2

        # 손절: 컵 저점
        stop = cup_trough.price * 0.997

        # 목표가: 컵 높이만큼 핸들 상단에서 상승
        cup_height = cup_right.price - cup_trough.price
        target = handle_high + cup_height

        # 돌파 판정
        current_close = float(df['close'].iloc[-1])
        prev_close = float(df['close'].iloc[-2]) if len(df) >= 2 else handle_high * 0.99
        breakout_line = handle_high * 1.001

        if prev_close < breakout_line and current_close > breakout_line:
            phase = "breakout"
            entry = current_close
        elif current_close > breakout_line and prev_close >= breakout_line:
            phase = "confirmed"
            entry = current_close
        else:
            phase = "forming"
            entry = breakout_line

        trigger = phase in ("breakout", "confirmed")

        avg_vol = self._avg_volume(df)
        current_vol = float(df['volume'].iloc[-1]) if 'volume' in df.columns else 0.0
        vol_mult = self._vol_multiplier(current_vol, avg_vol)

        conf = self._calc_confidence(score, vol_mult, phase)

        depth_pct = (cup_right.price - cup_trough.price) / cup_right.price
        symmetry_pct = abs(cup_left.price - cup_right.price) / cup_right.price

        result = PatternResult(
            pattern=self.NAME,
            confidence=min(conf, 0.97),
            entry=round(entry, 0),
            stop=round(stop, 0),
            target=round(target, 0),
            timeframe='daily',
            phase=phase,
            pivots_used=[cup_left, cup_trough, cup_right],
            meta={
                'cup_left': round(cup_left.price, 0),
                'cup_trough': round(cup_trough.price, 0),
                'cup_right': round(cup_right.price, 0),
                'cup_depth_pct': round(depth_pct * 100, 1),
                'symmetry_pct': round(symmetry_pct * 100, 1),
                'handle_high': round(handle_high, 0),
                'handle_bars': handle_bars,
                'volume_dry': vol_dry,
                'score': score,
                'trigger': trigger,
            },
        )

        logger.debug(
            f"[CUP] {phase} | conf={conf:.2f} | score={score} | "
            f"depth={depth_pct:.1%} sym={symmetry_pct:.1%} | vol_dry={vol_dry}"
        )
        return result

    def is_cup_shape(
        self,
        cup_left: Pivot,
        cup_trough: Pivot,
        cup_right: Pivot,
    ) -> bool:
        """U자형: 좌/우 고점이 비슷한 레벨, 깊이 15~50%."""
        symmetry = abs(cup_left.price - cup_right.price) / cup_right.price
        if symmetry > self.CUP_SYMMETRY_TOL:
            logger.debug(f"[CUP] SKIP 대칭 불량: {symmetry:.1%}")
            return False

        ref_price = max(cup_left.price, cup_right.price)
        depth = (ref_price - cup_trough.price) / ref_price
        if depth < self.CUP_DEPTH_MIN or depth > self.CUP_DEPTH_MAX:
            logger.debug(f"[CUP] SKIP 깊이 범위 초과: {depth:.1%}")
            return False

        # 저점이 고점보다 낮아야 함 (당연하지만 명시적으로)
        if cup_trough.price >= cup_right.price:
            return False

        return True

    def is_handle_consolidation(
        self,
        df: pd.DataFrame,
        cup_right_idx: int,
    ) -> tuple[bool, int, float]:
        """
        컵 우측 고점 이후 핸들 구간 검증.

        Returns:
            (valid, bar_count, handle_high)
        """
        bars_since = len(df) - 1 - cup_right_idx
        if bars_since < self.HANDLE_MIN_BARS or bars_since > self.HANDLE_MAX_BARS:
            return False, bars_since, 0.0

        handle_df = df.iloc[cup_right_idx:]
        if len(handle_df) < 2:
            return False, 0, 0.0

        cup_right_price = float(df['close'].iloc[cup_right_idx])
        handle_high = float(handle_df['high'].max())
        handle_low = float(handle_df['low'].min())

        # 핸들은 컵 우측 고점 아래에서 형성 (15% 이내 조정)
        retrace = (cup_right_price - handle_low) / cup_right_price
        if retrace > self.HANDLE_MAX_RETRACE:
            logger.debug(f"[CUP] SKIP 핸들 되돌림 과대: {retrace:.1%}")
            return False, bars_since, 0.0

        return True, bars_since, handle_high

    def volume_dry_up(self, df: pd.DataFrame, start_idx: int) -> bool:
        """핸들 구간 거래량 < 컵 평균 거래량의 70%."""
        if 'volume' not in df.columns:
            return True

        cup_df = df.iloc[:start_idx]
        handle_df = df.iloc[start_idx:]

        if len(cup_df) == 0 or len(handle_df) == 0:
            return True

        cup_avg = float(cup_df['volume'].mean())
        handle_avg = float(handle_df['volume'].mean())

        if cup_avg <= 0:
            return True

        return handle_avg < cup_avg * self.VOLUME_DRY_RATIO

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
