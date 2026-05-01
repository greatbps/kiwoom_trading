"""
상승 플래그 (Bull Flag) 탐지기

Bulkowski 통계: 성공률 85%

구조:
    L_base → H_pole (강한 상승, Pole)
           → 조정 횡보 구간 (Flag: 폴 대비 50% 이내 되돌림)
           → (현재봉 Flag 상단 돌파 체크)

핵심 원리:
    Pole = 에너지 분출
    Flag = 에너지 재응축
    Breakout = 2차 분출

폴 거래량 > 플래그 거래량 확인으로 가짜 플래그 제거.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import numpy as np

from .base import PatternDetector, PatternResult, Pivot

logger = logging.getLogger(__name__)


class BullFlagDetector(PatternDetector):
    """상승 플래그 탐지기."""

    NAME = "bull_flag"
    BASE_CONFIDENCE: float = 0.85   # Bulkowski 통계 승률
    MIN_PIVOTS: int = 4

    # ── 조건 파라미터 ──────────────────────────────────────────────────────
    POLE_MIN_GAIN_PCT:  float = 0.05   # 폴 최소 상승률 5%
    FLAG_MAX_RETRACE:   float = 0.50   # 플래그 최대 되돌림 (폴 대비 50%)
    FLAG_MAX_WIDTH_PCT: float = 0.05   # 플래그 폭 (좁아야 플래그: 5% 이내)
    FLAG_MIN_BARS:      int   = 3      # 플래그 최소 형성 기간
    FLAG_MAX_BARS:      int   = 20     # 플래그 최대 형성 기간
    BREAKOUT_BUFFER_PCT: float = 0.001  # 돌파 버퍼 0.1%

    @property
    def name(self) -> str:
        return self.NAME

    def detect(
        self,
        df: pd.DataFrame,
        pivots: list[Pivot],
    ) -> Optional[PatternResult]:
        """
        상승 플래그 감지.

        1. 피벗에서 [L_base, H_pole] 폴 구간 추출
        2. H_pole 이후 플래그 구간 확인 (조정 횡보)
        3. 플래그 되돌림 ≤ 폴의 50% 확인
        4. 플래그 폭이 좁은지 확인 (≤ 5%)
        5. 폴 거래량 > 플래그 거래량 확인 (수축)
        6. 현재봉 플래그 상단 돌파 여부 → phase 결정
        7. confidence 동적 계산
        """
        df = self._normalize_df(df)
        if df is None:
            return None

        # ── 폴 구간 추출 ─────────────────────────────────────────────────
        highs = [p for p in pivots if p.is_high]
        lows  = [p for p in pivots if p.is_low]

        if not highs or not lows:
            return None

        # 가장 최근 고점 = 폴 꼭대기
        pole_top = highs[-1]

        # 폴 꼭대기 이전의 저점 = 폴 기저
        base_candidates = [l for l in lows if l.idx < pole_top.idx]
        if not base_candidates:
            return None
        pole_base = base_candidates[-1]  # 폴 꼭대기 직전 저점

        # ── 폴 유효성 검증 ───────────────────────────────────────────────
        pole_height    = pole_top.price - pole_base.price
        pole_gain_pct  = pole_height / pole_base.price

        if pole_gain_pct < self.POLE_MIN_GAIN_PCT:
            logger.debug(f"[FLAG] SKIP 폴 상승 부족: {pole_gain_pct:.1%} < {self.POLE_MIN_GAIN_PCT:.0%}")
            return None

        # ── 플래그 구간 분석 ─────────────────────────────────────────────
        bars_since_pole = (len(df) - 1) - pole_top.idx
        if bars_since_pole < self.FLAG_MIN_BARS:
            logger.debug(f"[FLAG] SKIP 플래그 형성 봉 부족: {bars_since_pole}")
            return None
        if bars_since_pole > self.FLAG_MAX_BARS:
            logger.debug(f"[FLAG] SKIP 플래그 너무 오래됨: {bars_since_pole}봉")
            return None

        flag_df  = df.iloc[pole_top.idx:]
        if len(flag_df) < 2:
            return None

        flag_low  = float(flag_df['low'].min())
        flag_high = float(flag_df['high'].max())

        # 되돌림 비율 (폴 대비)
        retracement = (pole_top.price - flag_low) / pole_height
        if retracement > self.FLAG_MAX_RETRACE:
            logger.debug(f"[FLAG] SKIP 되돌림 과대: {retracement:.1%}")
            return None

        # 플래그 폭 (좁아야 함)
        flag_width_pct = (flag_high - flag_low) / pole_top.price
        if flag_width_pct > self.FLAG_MAX_WIDTH_PCT:
            logger.debug(f"[FLAG] SKIP 플래그 폭 과대: {flag_width_pct:.1%}")
            return None

        # ── 거래량: 플래그 < 폴 (수축 확인) ────────────────────────────
        avg_vol      = self._avg_volume(df)
        current_vol  = float(df['volume'].iloc[-1]) if 'volume' in df.columns else 0.0
        vol_mult     = self._vol_multiplier(current_vol, avg_vol)

        # 폴 구간 평균 거래량
        pole_df     = df.iloc[pole_base.idx: pole_top.idx + 1]
        pole_avg_vol = float(pole_df['volume'].mean()) if 'volume' in pole_df.columns and len(pole_df) > 0 else 0.0
        flag_avg_vol = float(flag_df['volume'].mean()) if 'volume' in flag_df.columns and len(flag_df) > 0 else 0.0

        vol_contraction = (flag_avg_vol < pole_avg_vol) if pole_avg_vol > 0 else True

        # ── 목표가 / 손절 ────────────────────────────────────────────────
        target = pole_top.price + pole_height   # 폴 높이만큼 다시 상승
        stop   = flag_low * 0.997               # 플래그 저점 아래 0.3%

        # ── 돌파 판정 ────────────────────────────────────────────────────
        current_close = float(df['close'].iloc[-1])
        prev_close    = float(df['close'].iloc[-2]) if len(df) >= 2 else flag_high * 0.99
        breakout_line = pole_top.price * (1 + self.BREAKOUT_BUFFER_PCT)

        if prev_close < breakout_line and current_close > breakout_line:
            phase = "breakout"
            entry = current_close
        elif current_close > breakout_line and prev_close >= breakout_line:
            phase = "confirmed"
            entry = current_close
        else:
            phase = "forming"
            entry = breakout_line

        conf = self._calc_confidence(
            retracement, flag_width_pct, vol_mult, vol_contraction, phase
        )

        result = PatternResult(
            pattern      = self.NAME,
            confidence   = min(conf, 0.97),
            entry        = round(entry, 0),
            stop         = round(stop, 0),
            target       = round(target, 0),
            timeframe    = 'daily',
            phase        = phase,
            pivots_used  = [pole_base, pole_top],
            meta         = {
                'pole_base':        round(pole_base.price, 0),
                'pole_top':         round(pole_top.price, 0),
                'pole_height':      round(pole_height, 0),
                'pole_gain_pct':    round(pole_gain_pct * 100, 1),
                'flag_low':         round(flag_low, 0),
                'flag_high':        round(flag_high, 0),
                'retracement_pct':  round(retracement * 100, 1),
                'flag_width_pct':   round(flag_width_pct * 100, 1),
                'bars_since_pole':  bars_since_pole,
                'vol_contraction':  vol_contraction,
                'vol_ratio':        round(current_vol / avg_vol, 2) if avg_vol > 0 else 0,
            },
        )

        logger.debug(
            f"[FLAG] {phase} | conf={conf:.2f} | "
            f"pole_gain={pole_gain_pct:.1%} retrace={retracement:.1%} | "
            f"vol_contract={vol_contraction}"
        )
        return result

    # ── 내부 계산 ─────────────────────────────────────────────────────────

    def _calc_confidence(
        self,
        retracement: float,
        flag_width_pct: float,
        vol_mult: float,
        vol_contraction: bool,
        phase: str,
    ) -> float:
        """
        동적 confidence 계산.

        BASE × 되돌림보정 × 폭보정 × 거래량보정 × 수축보정 × phase보정

        되돌림:
            10% 되돌림 → ×0.97  (강한 플래그)
            50% 되돌림 → ×0.85  (약한 플래그, 허용 한계)

        폭:
            좁을수록 신뢰 높음
            5% 폭 → ×0.90
        """
        conf = self.BASE_CONFIDENCE

        # 되돌림: 적을수록 좋음 (0.1 → ×0.97, 0.5 → ×0.85)
        conf *= (1.0 - retracement * 0.30)

        # 플래그 폭: 좁을수록 좋음
        conf *= (1.0 - flag_width_pct * 2.0)

        # 거래량 보정
        conf *= vol_mult

        # 거래량 수축 보너스 (폴 > 플래그 = 건강한 조정)
        if vol_contraction:
            conf *= 1.05

        # Phase 보정
        if phase == "breakout":
            pass
        elif phase == "confirmed":
            conf *= 0.95
        elif phase == "forming":
            conf *= 0.70

        return conf
