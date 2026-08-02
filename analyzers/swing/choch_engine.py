"""
CHoCH 기반 스윙 진입 엔진 (Entry Parity Migration)

━━━ 왜 만드는가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Live 스윙 진입은 `SignalEngine`(패턴 3종 점수화)이고 백테스트는
  CHoCH 기반이었다. 두 규칙이 같은 날 같은 종목에 신호를 낸 것이
  2년간 **3건**뿐이다 — 이름만 Swing 이고 다른 전략이었다.

  Iteration 8 측정 (Top-3 상한, 공통 청산):

      Pullback(Live)   140거래  승률 32.9%  PF 1.126   Train PF 0.966(손실)
      CHoCH(Backtest)   59거래  승률 40.7%  PF 1.639   세 구간 모두 1.3 이상

  검증된 쪽으로 통일한다. **백테스트를 Live 에 맞추지 않는다.**

━━━ 중복 구현하지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  판정은 `backtest.adapter.SMCAdapter` 를 **그대로 호출**한다.
  여기서 CHoCH 를 다시 구현하면 Live 와 백테스트가 또 갈라진다 —
  그게 애초에 이 문제의 원인이었다.

  필터 파라미터도 `backtest.daily_scan.BEST_CONFIG` /
  `BEST_ADAPTER_KWARGS` 를 그대로 쓴다.

━━━ 점수는 순위 신호가 아니다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ⚠️ CHoCH 판정은 이진(통과/미통과)이다. `SignalEngine` 과 호환을 위해
     `final_score` 를 채우지만, **예측력이 있다고 주장하지 않는다.**

     Phase 0.1: ScoreEngine 순위가 Random 대비 43.3 백분위 (효과 없음)
     Phase 1 Iter1: 연속 스코어 Spearman rho -0.169 (p=0.131),
                    trend 축은 -0.267 로 유의하게 **음**

     따라서 고정값을 준다. 동점 정렬은 임의로 갈리며 그것이 정직하다.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# SignalEngine 과 호환되는 고정 점수.
# score_from_size(): >=8 → 1.0 / >=6 → 0.7 / >=5 → 0.5
# 6.0 → size 0.7. 사이징 체계를 바꾸지 않으려고 중간값을 쓴다.
FIXED_SCORE = 6.0

MIN_BARS = 60           # SMCAdapter window(60) 확보
STOP_LOOKBACK = 10      # 구조 손절용 최근 저점 구간
TARGET_R = 3.0          # 목표가 = 진입 + 3R (손절폭 기준)


def _adapter():
    """
    ⚠️ 백테스트와 **같은 객체**를 쓴다. 파라미터를 여기서 다시 적으면
       언젠가 갈라진다.
    """
    from backtest.adapter import SMCAdapter
    from backtest.daily_scan import BEST_ADAPTER_KWARGS, BEST_CONFIG
    return SMCAdapter(BEST_CONFIG, **BEST_ADAPTER_KWARGS)


class ChochSignalEngine:
    """
    `SignalEngine` 과 동일한 인터페이스.

        eng = ChochSignalEngine(df, config)
        sig = eng.run()      # dict | None

    반환 dict 는 SignalEngine 과 같은 키를 갖는다 — swing_runner 호출부를
    바꾸지 않기 위해서다.
    """

    MIN_FINAL_SCORE: float = 5.0

    def __init__(self, df: pd.DataFrame, config: dict | None = None):
        self._df = df
        self._config = config or {}
        self._ad = _adapter()

    def run(self) -> Optional[dict]:
        df = self._df
        if df is None or len(df) < MIN_BARS:
            return None

        # 마지막 확정봉 기준 판정.
        # ⚠️ get_signal(df, i) 는 i-1 까지만 본다. i = len(df) 를 넘기면
        #    IndexError 가 나므로 마지막 인덱스를 준다.
        i = len(df) - 1
        try:
            if self._ad.get_signal(df, i) != 'BUY':
                return None
        except Exception as e:
            # fail-closed — 판정 못 하면 신호 없음으로 본다
            logger.warning(f'[CHOCH_SIG] 판정 실패 → 신호 없음: {e}')
            return None

        row = df.iloc[i]
        entry = float(row['close'])

        # 구조 손절 — 최근 저점. exit_logic 이 max_stop_pct(-5%)로 캡한다.
        lo = float(df['low'].iloc[max(0, i - STOP_LOOKBACK + 1): i + 1].min())
        stop = lo if 0 < lo < entry else entry * 0.95
        risk = entry - stop
        target = entry + risk * TARGET_R if risk > 0 else entry * 1.15

        return {
            'pattern': 'choch',
            'score': FIXED_SCORE,
            'final_score': FIXED_SCORE,
            'size': 0.7,          # score_from_size(6.0)
            'trigger': True,
            'phase': 'confirmed',
            'entry': round(entry, 2),
            'stop': round(stop, 2),
            'target': round(target, 2),
            'confidence': 0.7,
            'meta': {
                'score': FIXED_SCORE,
                'trigger': True,
                'source': 'backtest.adapter.SMCAdapter',
                'stop_basis': f'최근 {STOP_LOOKBACK}봉 저점',
                'note': '점수는 순위 신호가 아니다 — 예측력 미확인',
            },
        }
