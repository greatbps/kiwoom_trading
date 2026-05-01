"""
Rolling Fitness Tracker — 종목별 MFE/MAE 이력 기반 적합성 평가.

Fitness Score = avg_mfe / avg_mae (최근 N거래 롤링)
  ≥ threshold_active  → 정상 진입 허용
  ≥ threshold_limited → 제한적 허용 (포지션 사이징은 별도)
  < threshold_limited → 진입 차단

min_trades 거래 이전 → warm-up (기본 허용)
"""
from collections import deque
from typing import Optional


class RollingFitnessTracker:
    """
    종목별 롤링 Fitness Score 관리.

    Args:
        window:            롤링 윈도우 거래 수 (기본 20)
        min_trades:        최소 집계 거래 수 — 이하면 warm-up 허용 (기본 5)
        threshold_active:  이 이상 → ACTIVE (기본 1.8)
        threshold_limited: 이 이상 → LIMITED (기본 1.3)
                           이 미만 → EXCLUDED
    """

    def __init__(
        self,
        window:             int   = 20,
        min_trades:         int   = 5,
        threshold_active:   float = 1.8,
        threshold_limited:  float = 1.3,
    ):
        self.window             = window
        self.min_trades         = min_trades
        self.threshold_active   = threshold_active
        self.threshold_limited  = threshold_limited
        # symbol → deque of (mfe_pct, mae_pct) tuples
        self._history: dict[str, deque] = {}

    def update(self, symbol: str, mfe_pct: float, mae_pct: float) -> None:
        """
        거래 완료 후 MFE/MAE를 기록.

        Args:
            mfe_pct: Max Favorable Excursion % (양수, 예: 5.2)
            mae_pct: Max Adverse Excursion % (양수로 변환해서 전달, 또는 음수도 abs 처리)
        """
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self.window)
        mae_abs = abs(mae_pct)
        self._history[symbol].append((float(mfe_pct), float(mae_abs)))

    def get_score(self, symbol: str) -> Optional[float]:
        """
        현재 Fitness Score (avg_mfe / avg_mae).
        min_trades 미만이거나 avg_mae == 0 이면 None 반환.
        """
        hist = self._history.get(symbol)
        if not hist or len(hist) < self.min_trades:
            return None
        avg_mfe = sum(m[0] for m in hist) / len(hist)
        avg_mae = sum(m[1] for m in hist) / len(hist)
        if avg_mae == 0:
            return None
        return avg_mfe / avg_mae

    def get_status(self, symbol: str) -> str:
        """
        'ACTIVE' | 'LIMITED' | 'EXCLUDED' | 'WARMUP'
        """
        score = self.get_score(symbol)
        if score is None:
            return 'WARMUP'
        if score >= self.threshold_active:
            return 'ACTIVE'
        if score >= self.threshold_limited:
            return 'LIMITED'
        return 'EXCLUDED'

    def is_qualified(self, symbol: str) -> bool:
        """
        True  → 진입 허용 (WARMUP / ACTIVE / LIMITED)
        False → 진입 차단 (EXCLUDED)
        """
        return self.get_status(symbol) != 'EXCLUDED'

    def trade_count(self, symbol: str) -> int:
        """현재 누적 거래 수 (최대 window)."""
        hist = self._history.get(symbol)
        return len(hist) if hist else 0

    def summary(self) -> dict:
        """전체 종목 fitness 요약 dict."""
        out = {}
        for sym in self._history:
            score = self.get_score(sym)
            out[sym] = {
                'score':  round(score, 3) if score is not None else None,
                'status': self.get_status(sym),
                'trades': self.trade_count(sym),
            }
        return out
