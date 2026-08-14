"""seq 36 — VWAP. WI-10 Phase 1: `analyzers/entry_timing_analyzer.py`의
`EntryTimingAnalyzer.calculate_vwap()`(rolling/누적 VWAP, 무수정)을 그대로 재사용한다.
기존 "2차 VWAP검증"(main_auto_trading.py:3233-3265, `PreTradeValidator`)은 전체 후보
공통 적용이라 seq36 전용 판단이 아니었다 — 이 Monitor가 그 빈자리를 채운다."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from analyzers.entry_timing_analyzer import EntryTimingAnalyzer

from .base import StrategyMonitor, StrategyMonitorResult

MIN_BARS = 21  # rolling_window 기본값(20) + 1


class VWAPMonitor(StrategyMonitor):
    seq = 36
    strategy_name = 'VWAP'

    def __init__(self):
        self._analyzer = EntryTimingAnalyzer()

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        if df is None or len(df) < MIN_BARS:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{MIN_BARS}봉) — calculate_vwap rolling_window 미달')
        try:
            d = self._analyzer.calculate_vwap(df, use_rolling=True, rolling_window=20)
            close = float(d['close'].iloc[-1])
            vwap = float(d['vwap'].iloc[-1])
            prev_close = float(d['close'].iloc[-2])
            prev_vwap = float(d['vwap'].iloc[-2])
        except Exception as e:
            return self._error_result(symbol, e)

        deviation_pct = (close - vwap) / vwap * 100 if vwap else 0.0
        reasons = [f'deviation={deviation_pct:.2f}%']
        was_below = prev_close < prev_vwap
        now_above = close >= vwap

        if was_below and now_above:
            state = 'VWAP_RECLAIM'
        elif now_above and deviation_pct >= 0.5:
            state = 'VWAP_CONFIRMED'
        elif now_above:
            state = 'VWAP_CANDIDATE'
        elif deviation_pct <= -2:
            state = 'VWAP_FAILED'
        else:
            state = 'VWAP_WEAKENING'

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=(state in ('VWAP_RECLAIM', 'VWAP_CONFIRMED')),
            reasons=reasons,
        )
