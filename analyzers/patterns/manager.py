"""
패턴 탐지 매니저

패턴 = 플러그인 구조:
  - register()로 탐지기 추가
  - run_all()로 전체 실행
  - 새 패턴 추가 = 클래스 하나 register만 하면 끝
"""

from __future__ import annotations

import logging
from typing import Optional
import pandas as pd

from .base import Pivot, PatternResult, PatternDetector
from .zigzag import ZigZag

logger = logging.getLogger(__name__)


class PatternManager:
    """
    패턴 탐지기 레지스트리 + 실행 오케스트레이터.

    사용 예시:
        manager = PatternManager()
        manager.register(DoubleBottomDetector(), BullFlagDetector())

        pivots = manager.zigzag.get_pivots(df_daily)
        results = manager.run_all(df_daily, pivots)

        best = results[0] if results else None

    ScoreEngine 연동:
        pattern = manager.best(df_daily, pivots)
        score['pattern'] = score_from_pattern(pattern)
    """

    def __init__(
        self,
        window: int = 5,
        min_swing_pct: float = 0.02,
    ):
        self._detectors: list[PatternDetector] = []
        self.zigzag = ZigZag(window=window, min_swing_pct=min_swing_pct)

    # ── 탐지기 등록 ──────────────────────────────────────────────────────

    def register(self, *detectors: PatternDetector) -> None:
        """패턴 탐지기 등록 (여러 개 한 번에 가능)."""
        self._detectors.extend(detectors)
        logger.debug(f"[PATTERN_MGR] 등록: {[d.name for d in detectors]}")

    @property
    def registered(self) -> list[str]:
        """현재 등록된 패턴 이름 목록."""
        return [d.name for d in self._detectors]

    # ── 실행 ─────────────────────────────────────────────────────────────

    def run_all(
        self,
        df: pd.DataFrame,
        pivots: list[Pivot],
    ) -> list[PatternResult]:
        """
        등록된 모든 탐지기 실행 → confidence 내림차순 정렬 반환.

        Args:
            df:      일봉 OHLCV DataFrame
            pivots:  ZigZag.get_pivots() 결과 (시간순)

        Returns:
            감지된 패턴 결과 리스트 (없으면 빈 리스트)
        """
        results: list[PatternResult] = []

        for det in self._detectors:
            if len(pivots) < det.MIN_PIVOTS:
                logger.debug(
                    f"[PATTERN_MGR] {det.name} SKIP — "
                    f"pivots {len(pivots)} < MIN_PIVOTS {det.MIN_PIVOTS}"
                )
                continue

            try:
                result = det.detect(df, pivots)
                if result is not None:
                    results.append(result)
                    logger.info(
                        f"[PATTERN] {result.pattern} | "
                        f"conf={result.confidence:.2f} | "
                        f"entry={result.entry:.0f} stop={result.stop:.0f} "
                        f"target={result.target:.0f} RR={result.rr}"
                    )
            except Exception as e:
                logger.warning(f"[PATTERN_MGR] {det.name} 오류: {e}", exc_info=True)

        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def best(
        self,
        df: pd.DataFrame,
        pivots: Optional[list[Pivot]] = None,
        n_pivots: int = 10,
    ) -> Optional[PatternResult]:
        """
        가장 confidence 높은 패턴 하나 반환.

        pivots를 생략하면 내부에서 ZigZag 자동 실행.
        """
        if pivots is None:
            pivots = self.zigzag.get_pivots(df, n=n_pivots)

        results = self.run_all(df, pivots)
        return results[0] if results else None

    def score_from_pattern(self, result: Optional[PatternResult]) -> int:
        """
        PatternResult → ScoreEngine에서 사용할 정수 점수.

        phase별 적용 비율:
            breakout  → 100%  (conf 0.85+ → 2점, 0.70+ → 1점)
            confirmed →  90%  (이미 돌파, 약간 늦음)
            forming   →  70%  (아직 미확인 — 점수 조심스럽게)

        forming 상태는 score 0 반환 (진입 대기 종목 참고용으로만 사용).
        실제 진입 시 phase가 breakout/confirmed로 바뀌면 점수 반영.
        """
        if result is None:
            return 0

        conf = result.confidence

        if result.phase == "forming":
            return 0    # 아직 돌파 전 — 점수 미반영 (모니터링만)

        if result.phase == "confirmed":
            conf *= 0.90

        # breakout or confirmed (조정 후)
        if conf >= 0.85:
            return 2
        if conf >= 0.70:
            return 1
        return 0
