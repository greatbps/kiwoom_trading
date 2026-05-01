"""
trading/online_stats.py — EMA 기반 온라인 통계 + 실시간 사이징 조절

역할:
  거래 결과를 EMA로 누적 → 실시간 expectancy 계산 → position size 조절.
  WFO(오프라인)와 달리 매 거래 후 즉시 반영되는 온라인 러닝.

설계 원칙:
  "빠르게 배우는 게 아니라 천천히 틀리지 않게 적응하는 것"
  - alpha=0.05 (느린 EMA → 급격한 변화 방지)
  - min_trades=30 (최소 샘플 전까지 중립)
  - 클리핑: mult ∈ [0.6, 1.3] (폭주 방지)
  - 일일 1회 저장 (data/online_stats.json 지속)

통합:
  execute_sell() → online_stats.update(pnl_pct)   (기록)
  execute_buy()  → online_stats.get_size_mult()    (적용)

파라미터 의미:
  alpha:      EMA 반영속도 (0.05=느림, 0.10=빠름)
  effect_scale: expectancy → mult 변환 스케일
    e=+0.5, scale=0.3 → mult=1.15 (15% 증가)
    e=-0.3, scale=0.3 → mult=0.91 (9% 감소)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).parent.parent / 'data' / 'online_stats.json'


class OnlineStats:
    """EMA 기반 실시간 거래 통계 추적 + 사이징 배율 산출."""

    def __init__(
        self,
        alpha:        float = 0.05,    # EMA 반영 속도 (낮을수록 안정)
        min_trades:   int   = 30,      # 이 수 미만이면 중립 (mult=1.0)
        effect_scale: float = 0.30,    # expectancy → mult 스케일
        mult_min:     float = 0.60,    # 절대 하한 (폭주 방지)
        mult_max:     float = 1.30,    # 절대 상한 (과도한 증폭 방지)
    ):
        self._alpha        = alpha
        self._min_trades   = min_trades
        self._effect_scale = effect_scale
        self._mult_min     = mult_min
        self._mult_max     = mult_max

        # EMA 상태
        self._win_rate: float = 0.50
        self._avg_win:  float = 1.00
        self._avg_loss: float = 1.00
        self._n: int          = 0

        self._load()

    # ── 상태 I/O ──────────────────────────────────────────────────────

    def _load(self):
        """세션 시작 시 저장된 상태 복원."""
        if not _STATE_PATH.exists():
            return
        try:
            d = json.loads(_STATE_PATH.read_text(encoding='utf-8'))
            self._win_rate = float(d.get('win_rate', 0.50))
            self._avg_win  = float(d.get('avg_win',  1.00))
            self._avg_loss = float(d.get('avg_loss', 1.00))
            self._n        = int(d.get('n', 0))
            logger.info(f"[OL_STATS] 상태 복원: n={self._n} wr={self._win_rate:.2f} "
                        f"E={self.expectancy():.3f}")
        except Exception as e:
            logger.warning(f"[OL_STATS] 로드 실패: {e}")

    def save(self):
        """현재 상태를 파일에 저장 (일 1회 호출 권장)."""
        try:
            _STATE_PATH.parent.mkdir(exist_ok=True)
            _STATE_PATH.write_text(
                json.dumps({
                    'win_rate':   round(self._win_rate, 6),
                    'avg_win':    round(self._avg_win,  6),
                    'avg_loss':   round(self._avg_loss, 6),
                    'n':          self._n,
                    'expectancy': round(self.expectancy(), 6),
                    'updated_at': datetime.now().isoformat(),
                }, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except Exception as e:
            logger.warning(f"[OL_STATS] 저장 실패: {e}")

    # ── Public API ────────────────────────────────────────────────────

    def update(self, pnl_pct: float):
        """
        거래 결과 EMA 업데이트. execute_sell() 후 호출.

        Args:
            pnl_pct: 실현손익률 % (양수=수익, 음수=손실)
        """
        a = self._alpha
        self._n += 1

        if pnl_pct > 0:
            self._win_rate = (1 - a) * self._win_rate + a * 1.0
            self._avg_win  = (1 - a) * self._avg_win  + a * abs(pnl_pct)
        else:
            self._win_rate = (1 - a) * self._win_rate
            # avg_loss는 abs로 저장 (양수)
            self._avg_loss = (1 - a) * self._avg_loss + a * abs(pnl_pct)

        # 최솟값 보호 (EMA가 0에 수렴하는 것 방지)
        self._avg_win  = max(self._avg_win,  0.01)
        self._avg_loss = max(self._avg_loss, 0.01)

        if self._n % 10 == 0:
            logger.info(f"[OL_STATS] n={self._n} wr={self._win_rate:.2f} "
                        f"aw={self._avg_win:.2f} al={self._avg_loss:.2f} "
                        f"E={self.expectancy():.3f}")

    def expectancy(self) -> float:
        """현재 EMA 기반 expectancy (R 단위)."""
        return self._win_rate * self._avg_win - (1.0 - self._win_rate) * self._avg_loss

    def get_size_mult(self) -> Tuple[float, str]:
        """
        expectancy → position_size multiplier.

        min_trades 미만이면 중립(1.0) 반환.

        효과:
          E=+0.5 → ×1.15  (성과 좋을 때 소폭 증가)
          E= 0.0 → ×1.00  (중립)
          E=-0.3 → ×0.91  (성과 나쁠 때 소폭 감소)
        """
        if self._n < self._min_trades:
            return 1.0, f'OL_COLD: n={self._n}<{self._min_trades}'

        e    = self.expectancy()
        mult = 1.0 + e * self._effect_scale
        mult = round(max(self._mult_min, min(self._mult_max, mult)), 3)
        tag  = 'OL_UP' if mult > 1.0 else ('OL_DOWN' if mult < 1.0 else 'OL_NEUT')
        return mult, f'{tag}: n={self._n} E={e:.3f} ×{mult}'

    def get_status(self) -> dict:
        return {
            'n':          self._n,
            'win_rate':   round(self._win_rate, 4),
            'avg_win':    round(self._avg_win,  4),
            'avg_loss':   round(self._avg_loss, 4),
            'expectancy': round(self.expectancy(), 4),
            'ready':      self._n >= self._min_trades,
        }

    @property
    def n_trades(self) -> int:
        return self._n
