"""
점수 기반 종목 선별 엔진

점수 체계:
  +2    일봉 SMC BUY 신호 (daily_scan.py 결과)
  +1    거래량 스파이크 (20봉 평균 × 1.5 이상)
  +1    MA50 위 + 우상향
  +0~?  패턴 점수: confidence × pattern_weight (검증 통과 후 자동 활성)

패턴 점수 활성 조건 (자동):
  data/pattern_weights.json 존재 AND
  config.pattern_recognition.score_enabled = true

선별 기준:
  score >= 2 → 후보
  상위 MAX_SELECTED개만 진입 허용
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from analyzers.patterns.base import PatternResult

logger = logging.getLogger(__name__)

MAX_SELECTED  = 5
MIN_SCORE     = 2
_WEIGHTS_PATH = Path(__file__).parent.parent / 'data' / 'pattern_weights.json'
_WEIGHTS_TTL  = 3600   # 1시간마다 재로드


class ScoreEngine:
    """
    Args:
        daily_smc_symbols:   daily_scan.py 가 오늘 발행한 BUY 종목 set
        max_selected:        최대 선택 종목 수
        min_score:           최소 점수 기준
        score_pattern:       패턴 점수 ON/OFF (config.pattern_recognition.score_enabled)
    """

    def __init__(
        self,
        daily_smc_symbols: set   = None,
        max_selected:      int   = MAX_SELECTED,
        min_score:         int   = MIN_SCORE,
        score_pattern:     bool  = False,
    ):
        self.daily_smc_symbols = daily_smc_symbols or set()
        self.max_selected      = max_selected
        self.min_score         = min_score
        self.score_pattern     = score_pattern

        self._weights:    dict  = {}     # {phase_key: float}
        self._weights_ts: float = 0.0
        self._load_weights()

    # ── 가중치 로드 ───────────────────────────────────────────────────────────

    def _load_weights(self) -> None:
        """pattern_weights.json 로드 (TTL 기반 캐시)."""
        now = datetime.now().timestamp()
        if now - self._weights_ts < _WEIGHTS_TTL and self._weights:
            return

        if not _WEIGHTS_PATH.exists():
            self._weights = {}
            return

        try:
            data = json.loads(_WEIGHTS_PATH.read_text(encoding='utf-8'))
            self._weights    = data.get('weights', {})
            self._weights_ts = now
            logger.debug(f'[SCORE_ENGINE] 패턴 가중치 로드: {list(self._weights.keys())}')
        except Exception as e:
            logger.warning(f'[SCORE_ENGINE] 가중치 로드 실패: {e}')
            self._weights = {}

    def reload_weights(self) -> None:
        """강제 재로드 (daily_routine 호출용)."""
        self._weights_ts = 0.0
        self._load_weights()

    # ── 점수 계산 ─────────────────────────────────────────────────────────────

    def score(
        self,
        symbol:         str,
        ohlcv:          Optional[pd.DataFrame]  = None,
        pattern_result: Optional["PatternResult"] = None,
        pattern_dict:   Optional[dict]          = None,
    ) -> dict:
        """
        단일 종목 점수.

        Args:
            symbol:         종목 코드
            ohlcv:          일봉 DataFrame (close/volume 컬럼)
            pattern_result: PatternResult 객체 (직접 전달 시)
            pattern_dict:   daily_patterns.json 의 raw dict (객체 없을 때)

        Returns:
            {'total': float, 'smc': int, 'volume': int, 'ma50': int, 'pattern': float}
        """
        smc     = self._score_smc(symbol)
        volume  = self._score_volume(ohlcv)
        ma50    = self._score_ma50(ohlcv)
        pattern = self._score_pattern(pattern_result, pattern_dict)
        total   = smc + volume + ma50 + pattern

        return {
            'total':   round(total, 3),
            'smc':     smc,
            'volume':  volume,
            'ma50':    ma50,
            'pattern': round(pattern, 3),
        }

    def _score_pattern(
        self,
        result:      Optional["PatternResult"],
        pat_dict:    Optional[dict],
    ) -> float:
        """
        패턴 점수.

        score_enabled=False  → 0 (로그만)
        weights 없음         → 0 (검증 데이터 부족)
        weights 있음         → confidence × weight (실전 기대값 기반)
        """
        if not self.score_pattern:
            return 0.0

        self._load_weights()
        if not self._weights:
            return 0.0

        # PatternResult 또는 raw dict 중 하나로 데이터 추출
        if result is not None:
            phase_key  = f"{result.pattern}({result.phase})"
            confidence = float(result.confidence)
        elif pat_dict is not None:
            phase_key  = f"{pat_dict.get('pattern', '')}({pat_dict.get('phase', '')})"
            confidence = float(pat_dict.get('confidence', 0))
        else:
            return 0.0

        raw = self._weights.get(phase_key, 0.0)
        # weights는 float(구버전) 또는 dict(신버전) 둘 다 지원
        weight = raw.get('weight', 0.0) if isinstance(raw, dict) else float(raw)
        if weight <= 0:
            return 0.0

        score = min(confidence * weight, 1.0)   # 상한 1.0
        logger.debug(
            f'[SCORE_PAT] {phase_key} conf={confidence:.2f} '
            f'weight={weight:.4f} → {score:.4f}'
        )
        return score

    def _score_smc(self, symbol: str) -> int:
        return 2 if symbol in self.daily_smc_symbols else 0

    def _score_volume(self, ohlcv: Optional[pd.DataFrame]) -> int:
        if ohlcv is None or 'volume' not in ohlcv.columns or len(ohlcv) < 21:
            return 0
        vol_now = float(ohlcv['volume'].iloc[-1])
        vol_avg = float(ohlcv['volume'].iloc[-21:-1].mean())
        return 1 if vol_avg > 0 and vol_now >= vol_avg * 1.5 else 0

    def _score_ma50(self, ohlcv: Optional[pd.DataFrame]) -> int:
        if ohlcv is None or 'close' not in ohlcv.columns or len(ohlcv) < 55:
            return 0
        close = ohlcv['close']
        ma50  = close.rolling(50).mean()
        now   = float(ma50.iloc[-1])
        prev  = float(ma50.iloc[-11])
        if np.isnan(now) or np.isnan(prev):
            return 0
        return 1 if close.iloc[-1] > now and now > prev else 0

    # ── 랭킹 + 선별 ──────────────────────────────────────────────────────────

    def rank(
        self,
        candidates: dict,
        patterns:   Optional[dict] = None,
    ) -> list[tuple[str, dict]]:
        """
        후보 종목 전체 점수 계산 + 정렬.

        Args:
            candidates: {symbol: ohlcv_df or None}
            patterns:   {symbol: pattern_dict}  (daily_patterns.json 내용)

        Returns:
            [(symbol, score_dict), ...] 점수 내림차순
        """
        scored = []
        for sym, ohlcv in candidates.items():
            pat_dict = (patterns or {}).get(sym)
            s = self.score(sym, ohlcv, pattern_dict=pat_dict)
            scored.append((sym, s))
        scored.sort(key=lambda x: x[1]['total'], reverse=True)
        return scored

    def select(self, ranked: list[tuple[str, dict]]) -> list[str]:
        passed   = [sym for sym, s in ranked if s['total'] >= self.min_score]
        selected = passed[:self.max_selected]
        top5     = ranked[:5]
        logger.info(
            f'[SCORE_ENGINE] raw={len(ranked)}  '
            f'score>={self.min_score}: {len(passed)}  '
            f'selected: {len(selected)}'
        )
        logger.info(f'[SCORE_ENGINE] top5: {[(s, d["total"]) for s, d in top5]}')
        logger.info(f'[SCORE_ENGINE] final: {selected}')
        return selected

    def log_summary(self, ranked: list[tuple[str, dict]]) -> str:
        lines = [
            '[FINAL_SELECTION]',
            f'raw: {len(ranked)}',
            f'selected: {len(self.select(ranked))}',
            f'pattern_score: {"ON" if self.score_pattern else "OFF (LOG_ONLY)"}',
            'top:',
        ]
        for sym, s in ranked[:5]:
            lines.append(
                f'  {sym}  total={s["total"]}  '
                f'(smc={s["smc"]} vol={s["volume"]} ma50={s["ma50"]} pat={s["pattern"]})'
            )
        return '\n'.join(lines)


# ── 돌파형 진입 체크 ─────────────────────────────────────────────────────────

def check_breakout_entry(
    current_price: float,
    today_high:    float,
    tolerance:     float = 0.001,
) -> bool:
    return current_price > today_high * (1 + tolerance)
