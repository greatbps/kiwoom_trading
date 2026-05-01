"""
ZigZag 변곡점 추출기

smc_utils.find_swing_points()를 일봉 패턴 인식용으로 래핑.
재발명 없이 기존 로직 그대로 사용, volume 필드만 부가.
"""

from __future__ import annotations

import logging
from typing import Optional
import pandas as pd

from .base import Pivot

logger = logging.getLogger(__name__)


class ZigZag:
    """
    일봉 기준 ZigZag 변곡점 추출기.

    기존 SMC의 find_swing_points()를 래핑하여
    패턴 인식에 필요한 Pivot 리스트로 변환한다.

    Args:
        window:         좌우 비교 봉 수 (일봉은 5 권장, 분봉은 3)
        min_swing_pct:  최소 스윙 크기 비율 (일봉 2%, 분봉 0.5%)
    """

    def __init__(self, window: int = 5, min_swing_pct: float = 0.02):
        self.window = window
        self.min_swing_pct = min_swing_pct

    def get_pivots(self, df: pd.DataFrame, n: int = 10) -> list[Pivot]:
        """
        최근 n개 변곡점을 시간순(과거→최신)으로 반환.

        Args:
            df:  일봉 OHLCV DataFrame
            n:   반환할 최대 변곡점 수 (패턴별 MIN_PIVOTS 이상이면 충분)

        Returns:
            Pivot 리스트 (시간순, 과거 → 최신)
            데이터 부족 또는 오류 시 빈 리스트
        """
        try:
            from analyzers.smc.smc_utils import find_swing_points
        except ImportError:
            logger.error("[ZIGZAG] smc_utils import 실패")
            return []

        if df is None or len(df) < self.window * 2 + 2:
            return []

        df_clean = df.copy()
        df_clean.columns = [c.lower() for c in df_clean.columns]

        swing_points = find_swing_points(
            df_clean,
            lookback=self.window,
            min_swing_size_pct=self.min_swing_pct,
        )

        if not swing_points:
            return []

        # 최근 n개만 사용 (과거→최신 순 유지)
        recent = swing_points[-n:] if len(swing_points) > n else swing_points

        pivots: list[Pivot] = []
        for sp in recent:
            vol = 0.0
            if 'volume' in df_clean.columns and 0 <= sp.index < len(df_clean):
                vol = float(df_clean['volume'].iloc[sp.index])
            pivots.append(Pivot(
                idx=sp.index,
                price=sp.price,
                kind=sp.type,       # 'high' | 'low'
                timestamp=sp.timestamp,
                volume=vol,
            ))

        return pivots

    def get_highs(self, pivots: list[Pivot]) -> list[Pivot]:
        """고점만 필터링."""
        return [p for p in pivots if p.is_high]

    def get_lows(self, pivots: list[Pivot]) -> list[Pivot]:
        """저점만 필터링."""
        return [p for p in pivots if p.is_low]

    def validate_alternating(self, pivots: list[Pivot]) -> bool:
        """
        변곡점이 고점/저점 교대로 배치되어 있는지 확인.
        find_swing_points 정상 동작 시 항상 True.
        """
        for i in range(1, len(pivots)):
            if pivots[i].kind == pivots[i - 1].kind:
                return False
        return True
