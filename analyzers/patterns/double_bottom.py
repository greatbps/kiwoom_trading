"""
이중 바닥 (Double Bottom / "W" 패턴) 탐지기

Bulkowski 통계: 성공률 88% (하락 추세 후 발생 시)

구조:
    [H_prev] → L1 → H_neck → L2 → (현재봉 돌파 체크)

MIN_PIVOTS = 4 이유:
    피벗이 [H_prev, L1, H_neck, L2] 최소 4개 있어야
    L1이 진짜 골짜기(앞에 고점 있음)임을 보장한다.
    5번째 피벗(GPT 주장)은 불필요 — breakout은 현재 df 봉으로 체크.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import PatternDetector, PatternResult, Pivot

logger = logging.getLogger(__name__)


class DoubleBottomDetector(PatternDetector):
    """이중 바닥 탐지기."""

    NAME = "double_bottom"
    BASE_CONFIDENCE: float = 0.88   # Bulkowski 통계 승률
    MIN_PIVOTS: int = 4

    # ── 조건 파라미터 ──────────────────────────────────────────────────────
    MAX_TROUGH_DIFF_PCT: float = 0.025   # 두 저점 가격 차이 허용 범위 (2.5%)
    MIN_TROUGH_GAP_BARS: int   = 5       # 두 저점 최소 거리 (노이즈 방지)
    MAX_TROUGH_GAP_BARS: int   = 60      # 두 저점 최대 거리 (오래된 패턴 제외)
    BREAKOUT_BUFFER_PCT: float = 0.001   # 넥라인 돌파 버퍼 0.1%

    @property
    def name(self) -> str:
        return self.NAME

    def detect(
        self,
        df: pd.DataFrame,
        pivots: list[Pivot],
    ) -> Optional[PatternResult]:
        """
        이중 바닥 감지.

        1. 피벗에서 [L1, H_neck, L2] 구조 추출
        2. 두 저점 가격 유사성 확인 (2.5% 이내)
        3. 두 저점 간격 확인 (5~60봉)
        4. 현재 봉 기준 돌파 여부 판정 → phase 결정
        5. confidence 동적 계산 (저점 유사도 × 거래량 × phase 보정)
        6. forming 상태도 반환 (조기 진입용)
        """
        df = self._normalize_df(df)
        if df is None:
            return None

        # ── 피벗에서 L1, H_neck, L2 추출 ────────────────────────────────
        lows  = [p for p in pivots if p.is_low]
        highs = [p for p in pivots if p.is_high]

        if len(lows) < 2 or not highs:
            return None

        l2 = lows[-1]   # 최신 저점
        l1 = lows[-2]   # 그 이전 저점

        # l1 ~ l2 사이에 있는 최고점 = 넥라인
        neck_candidates = [h for h in highs if l1.idx < h.idx < l2.idx]
        if not neck_candidates:
            return None
        neck = max(neck_candidates, key=lambda h: h.price)

        # ── 구조 유효성 검증 ─────────────────────────────────────────────

        # 두 저점 간격
        bar_gap = l2.idx - l1.idx
        if bar_gap < self.MIN_TROUGH_GAP_BARS:
            logger.debug(f"[DBT] SKIP gap 너무 짧음: {bar_gap}봉")
            return None
        if bar_gap > self.MAX_TROUGH_GAP_BARS:
            logger.debug(f"[DBT] SKIP gap 너무 김: {bar_gap}봉")
            return None

        # 두 저점 가격 유사성
        avg_trough = (l1.price + l2.price) / 2
        diff_ratio = abs(l1.price - l2.price) / avg_trough
        if diff_ratio > self.MAX_TROUGH_DIFF_PCT:
            logger.debug(f"[DBT] SKIP 저점 차이 과대: {diff_ratio:.2%}")
            return None

        # 저점이 넥라인보다 낮아야 함 (당연한 조건이지만 명시적으로)
        neckline = neck.price
        if l1.price >= neckline or l2.price >= neckline:
            return None

        # ── 돌파 판정 (캔들 기반) ────────────────────────────────────────
        current_close = float(df['close'].iloc[-1])
        prev_close    = float(df['close'].iloc[-2]) if len(df) >= 2 else neckline * 0.99
        breakout_line = neckline * (1 + self.BREAKOUT_BUFFER_PCT)

        # ── 거래량 ───────────────────────────────────────────────────────
        avg_vol     = self._avg_volume(df)
        current_vol = float(df['volume'].iloc[-1]) if 'volume' in df.columns else 0.0
        vol_mult    = self._vol_multiplier(current_vol, avg_vol)

        # ── 목표가 / 손절 (Measured Move) ───────────────────────────────
        trough_low = min(l1.price, l2.price)
        height     = neckline - trough_low
        target     = neckline + height
        stop       = trough_low * 0.997   # 저점 아래 0.3%

        # ── Phase 결정 ───────────────────────────────────────────────────
        if prev_close < breakout_line and current_close > breakout_line:
            # 이번 봉에서 돌파 발생 (가장 이상적인 타이밍)
            phase = "breakout"
            entry = current_close
            conf  = self._calc_confidence(diff_ratio, vol_mult, phase)

        elif current_close > breakout_line and prev_close >= breakout_line:
            # 이미 돌파된 상태 (이전 봉도 위)
            phase = "confirmed"
            entry = current_close
            conf  = self._calc_confidence(diff_ratio, vol_mult, phase)

        else:
            # 아직 돌파 전 (forming) — 조기 감시용으로 반환
            phase = "forming"
            entry = breakout_line          # 돌파 시 예상 진입가
            conf  = self._calc_confidence(diff_ratio, vol_mult, phase)

        result = PatternResult(
            pattern      = self.NAME,
            confidence   = min(conf, 0.97),
            entry        = round(entry, 0),
            stop         = round(stop, 0),
            target       = round(target, 0),
            timeframe    = 'daily',
            phase        = phase,
            pivots_used  = [l1, neck, l2],
            meta         = {
                'neckline':        round(neckline, 0),
                'trough1':         round(l1.price, 0),
                'trough2':         round(l2.price, 0),
                'trough_diff_pct': round(diff_ratio * 100, 2),
                'bar_gap':         bar_gap,
                'height':          round(height, 0),
                'vol_ratio':       round(current_vol / avg_vol, 2) if avg_vol > 0 else 0,
            },
        )

        logger.debug(
            f"[DBT] {phase} | conf={conf:.2f} | "
            f"L1={l1.price:.0f} neck={neckline:.0f} L2={l2.price:.0f} | "
            f"diff={diff_ratio:.2%} gap={bar_gap}봉"
        )
        return result

    # ── 내부 계산 ─────────────────────────────────────────────────────────

    def _calc_confidence(self, diff_ratio: float, vol_mult: float, phase: str) -> float:
        """
        동적 confidence 계산.

        BASE × 저점유사도 × 거래량보정 × phase보정

        저점유사도:
            diff 0.0% → ×1.000  (두 저점 완벽히 같음)
            diff 1.0% → ×0.960
            diff 2.5% → ×0.900  (허용 한계)
        """
        conf = self.BASE_CONFIDENCE

        # 저점 유사도: 차이가 클수록 신뢰 감소
        conf *= (1.0 - diff_ratio * 4.0)

        # 거래량 보정
        conf *= vol_mult

        # Phase 보정
        if phase == "breakout":
            pass                # 이번 봉 돌파 = 가장 이상적, 보정 없음
        elif phase == "confirmed":
            conf *= 0.95        # 약간 늦음
        elif phase == "forming":
            conf *= 0.70        # 아직 미확인

        return conf
