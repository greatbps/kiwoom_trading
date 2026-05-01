"""
스윙 신호 엔진

종목별로 등록된 패턴 탐지기를 모두 실행하고,
가중치 적용 최종 점수를 계산하여 최고 점수 신호 하나를 반환.

score_from_size():
    최종 점수 구간별 포지션 비중 결정.
    score >= 8 → 1.0 (풀 사이즈)
    score >= 6 → 0.7
    score >= 5 → 0.5
    미만       → 진입 없음
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from analyzers.patterns.base import Pivot, PatternResult, PatternDetector
from analyzers.patterns.manager import PatternManager
from analyzers.patterns.cup_handle import CupHandleDetector
from analyzers.patterns.pullback import PullbackDetector
from analyzers.patterns.box_breakout import BoxBreakoutDetector

logger = logging.getLogger(__name__)

PATTERN_WEIGHT: dict[str, float] = {
    "cup_handle": 1.2,
    "pullback": 1.0,
    "box_breakout": 0.9,
}


def score_from_size(final_score: float) -> float:
    """최종 점수 → 포지션 비중."""
    if final_score >= 8:
        return 1.0
    if final_score >= 6:
        return 0.7
    if final_score >= 5:
        return 0.5
    return 0.0


def _build_manager() -> PatternManager:
    mgr = PatternManager(window=5, min_swing_pct=0.02)
    mgr.register(
        CupHandleDetector(),
        PullbackDetector(),
        BoxBreakoutDetector(),
    )
    return mgr


class SignalEngine:
    """
    종목별 패턴 탐지 + 점수화 + 최고 신호 선택.

    df는 반드시 일봉 OHLCV (컬럼: open, high, low, close, volume).
    """

    MIN_FINAL_SCORE: float = 5.0

    def __init__(self, df: pd.DataFrame, config: dict):
        self._df = df
        self._config = config
        self._manager = _build_manager()
        self._pivots: list[Pivot] = []

    def run(self) -> Optional[dict]:
        """패턴 탐지 → 점수화 → 최고 신호 반환. 없으면 None."""
        patterns = self._detect_all()
        if not patterns:
            return None
        scored = self._score(patterns)
        return self._select(scored)

    def _detect_all(self) -> list[PatternResult]:
        if len(self._df) < 10:
            return []

        try:
            self._pivots = self._manager.zigzag.get_pivots(self._df, n=20)
        except Exception as e:
            logger.warning(f"[SWING_SIG] 피벗 추출 실패: {e}")
            self._pivots = []

        return self._manager.run_all(self._df, self._pivots)

    def _score(self, results: list[PatternResult]) -> list[dict]:
        scored = []
        for r in results:
            weight = PATTERN_WEIGHT.get(r.pattern, 1.0)
            raw_score = r.meta.get('score', 0) if r.meta else 0
            final_score = raw_score * weight
            size = score_from_size(final_score)
            trigger = r.meta.get('trigger', False) if r.meta else False

            scored.append({
                'pattern': r.pattern,
                'score': raw_score,
                'final_score': round(final_score, 2),
                'size': size,
                'trigger': trigger,
                'phase': r.phase,
                'entry': r.entry,
                'stop': r.stop,
                'target': r.target,
                'confidence': r.confidence,
                'meta': r.meta,
            })

        # 최종 점수 내림차순 정렬
        scored.sort(key=lambda x: x['final_score'], reverse=True)
        return scored

    def _select(self, scored: list[dict]) -> Optional[dict]:
        """trigger=True + final_score >= MIN_FINAL_SCORE 중 최고 점수 1개."""
        for item in scored:
            if item['trigger'] and item['final_score'] >= self.MIN_FINAL_SCORE:
                logger.info(
                    f"[SWING_SIG] 신호 선택: {item['pattern']} | "
                    f"score={item['score']} final={item['final_score']} | "
                    f"size={item['size']} phase={item['phase']} entry={item['entry']}"
                )
                return item

        return None
