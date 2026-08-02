"""
Iteration 8 — Live Swing Entry 를 백테스트에서 그대로 돌린다

━━━ 재구현하지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `analyzers/swing/signal_engine.SignalEngine` 을 **그대로 호출**한다.
  여기서 다시 구현하면 Live 와 백테스트를 비교하는 게 아니라
  구현 두 개를 비교하게 된다 — Phase 0 에서 같은 함정을 이미 봤다.

  다행히 SignalEngine 은 일봉 OHLCV 를 받는다. 캐시된 데이터를 그대로
  넘기면 된다.

━━━ Live 와 다른 점 (반드시 알고 읽어야 한다) ━━━━━━━━━━━━━━━━━━

  ① swing_runner 는 종목 유니버스를 `data/swing_universe.json` 에서
     읽고, 여기서는 DEFAULT_CANDIDATES 78종목을 쓴다. 진입 **규칙**을
     비교하는 것이지 유니버스를 비교하는 게 아니다.
  ② swing_runner 에는 레짐 게이트 · 섹터 한도 · 쿨다운 · Top-3 상한이
     더 붙는다. 여기서는 **신호 생성 단계까지만** 재현한다.
     따라서 이 백테스트의 신호 수는 Live 상한선이다.
  ③ min_final_score = 5.0 은 SignalEngine.MIN_FINAL_SCORE 를 따른다.

사용법:
    python -m phase1.swing_entry_adapter        # 신호 수만 확인
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from analyzers.swing.signal_engine import SignalEngine

logging.getLogger('analyzers').setLevel(logging.CRITICAL)
logging.getLogger('analyzers.swing.signal_engine').setLevel(logging.CRITICAL)

# SignalEngine 이 패턴을 잡으려면 과거 봉이 필요하다.
# swing_runner 의 DEFAULT_LOOKBACK_DAYS 와 맞춘다.
MIN_BARS = 60
WINDOW = 120


class SwingEntryAdapter:
    """
    Live 스윙 진입 규칙 어댑터.

    Args:
        config:      swing_runner 가 넘기는 config (없으면 빈 dict)
        min_score:   최소 최종점수 (기본 SignalEngine.MIN_FINAL_SCORE)
        patterns:    허용 패턴 (None = 전부).
                     'pullback' 만 주면 Live 실거래에서 관측된
                     SWING:pullback 만 재현한다.
    """

    def __init__(self, config: dict | None = None,
                 min_score: float | None = None,
                 patterns: tuple[str, ...] | None = None):
        self.config = config or {}
        self.min_score = (SignalEngine.MIN_FINAL_SCORE if min_score is None
                          else min_score)
        self.patterns = patterns

    def signal_at(self, df: pd.DataFrame, i: int) -> dict | None:
        """
        i 번째 봉 시점의 신호.

        ⚠️ `df.iloc[:i+1]` 로 잘라서 넘긴다. 전체를 주면 SignalEngine 이
           미래 봉을 보고 패턴을 잡는다.
        """
        if i < MIN_BARS:
            return None
        sub = df.iloc[max(0, i - WINDOW + 1): i + 1]
        if len(sub) < MIN_BARS:
            return None
        try:
            eng = SignalEngine(sub, self.config)
            eng.MIN_FINAL_SCORE = self.min_score
            sig = eng.run()
        except Exception:
            # ⚠️ 예외를 삼키되 신호로 취급하지 않는다 (fail-closed).
            return None
        if not sig:
            return None
        if self.patterns and sig['pattern'] not in self.patterns:
            return None
        return sig

    def scan(self, data: dict[str, pd.DataFrame],
             progress: bool = False) -> tuple[dict[str, set], dict]:
        """
        Returns:
            (signals, detail)
            signals — {symbol: set(Timestamp)}
            detail  — {(symbol, ts): 신호 dict}   스코어·손절 등 부가정보
        """
        out: dict[str, set] = {}
        detail: dict = {}
        for n, (sym, df) in enumerate(data.items(), 1):
            days = set()
            for i in range(len(df)):
                s = self.signal_at(df, i)
                if s:
                    ts = df.index[i]
                    days.add(ts)
                    detail[(sym, ts)] = s
            if days:
                out[sym] = days
            if progress and n % 20 == 0:
                print(f'    스캔 {n}/{len(data)}')
        return out, detail


if __name__ == '__main__':
    from phase0 import data_cache as dc
    data = dc.load_ohlcv()
    days = set(dc.trading_days(data))
    for label, pats in (('전 패턴', None), ('pullback 만', ('pullback',))):
        sig, det = SwingEntryAdapter(patterns=pats).scan(data, progress=True)
        n = sum(1 for v in sig.values() for d in v if d in days)
        print(f'  {label:<14} 신호 {n}건 / {len(sig)}종목')
