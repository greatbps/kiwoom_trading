"""seq 33 — Breakout. WI-10 Phase 1: `analyzers/trend/trend_breakout.py`의
`TrendBreakoutStrategy.check_entry()`가 완전구현돼 있고 실제 라이브에서도 호출된다
(main_auto_trading.py:5630, 단 트리거는 regime=='TREND'이지 seq33 조건매칭이 아니다).
그 클래스를 무수정으로 재사용한다 — 단 이 Monitor 호출은 라이브 BUY와 무관한 별도
관측 경로다(execute_buy 미접촉).

⚠️ `check_entry()`는 설계상 5분봉을 기대하지만(docstring), 이번 WI-10은 신규 API
호출 없이 Evidence 단계에 이미 있는 일봉 30봉을 재사용하기로 확인했다(사용자 승인) —
일봉으로 호출한 결과는 실제 라이브 5분봉 판정의 근사치일 뿐이다.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from analyzers.trend.trend_breakout import TrendBreakoutStrategy

from .base import StrategyMonitor, StrategyMonitorResult

MIN_BARS = 21  # breakout_lookback 기본값(20) + 1


class BreakoutMonitor(StrategyMonitor):
    seq = 33
    strategy_name = 'Breakout'

    def __init__(self, config: dict):
        # TrendBreakoutStrategy(config)는 config.get("trend", {})를 읽는다(무수정 재사용).
        self._strategy = TrendBreakoutStrategy(config)

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        if df is None or len(df) < MIN_BARS:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{MIN_BARS}봉) — TrendBreakoutStrategy.check_entry 요구량 미달')
        try:
            signal, reason, details = self._strategy.check_entry(df, debug=False)
        except Exception as e:
            return self._error_result(symbol, e)

        is_pullback = bool(details.get('is_pullback')) if isinstance(details, dict) else False
        if signal:
            state = 'BREAKOUT_CONFIRMED'
        elif is_pullback:
            state = 'BREAKOUT_RETEST'
        elif 'ext' in str(reason).lower() or '이격' in str(reason):
            state = 'BREAKOUT_WEAKENING'
        else:
            state = 'BREAKOUT_CANDIDATE'

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=bool(signal), reasons=[str(reason)],
        )
