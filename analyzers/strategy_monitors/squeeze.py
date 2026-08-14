"""seq 37 — Squeeze Momentum Pro. WI-10 Phase 1: `analyzers/squeeze_momentum.py`의
`SqueezeMomentumPro.check_squeeze()`(존카터식 BB/KC 압축판정, 무수정)를 그대로
재사용한다. `SqueezeWithOrderBook`(실거래 활성 경로, entry_mode=squeeze_only)은
호가창 실시간 데이터가 필요해 이 Monitor(일봉 데이터만 사용)에서는 호출하지 않는다
— 대신 그 클래스가 내부적으로 의존하는 동일한 `check_squeeze()`를 직접 재사용해
같은 판단 로직을 근사한다."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from analyzers.squeeze_momentum import SqueezeMomentumPro

from .base import StrategyMonitor, StrategyMonitorResult


class SqueezeMonitor(StrategyMonitor):
    seq = 37
    strategy_name = 'Squeeze Momentum Pro'

    def __init__(self):
        self._squeeze = SqueezeMomentumPro()

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        try:
            min_bars = max(self._squeeze.bb_period, self._squeeze.kc_period,
                            self._squeeze.momentum_period)
        except AttributeError:
            min_bars = 20
        if df is None or len(df) < min_bars:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{min_bars}봉) — check_squeeze 요구량 미달')
        try:
            squeeze_on, momentum_up, details = self._squeeze.check_squeeze(df)
        except Exception as e:
            return self._error_result(symbol, e)

        reasons = [f"squeeze_on={squeeze_on}", f"momentum_up={momentum_up}",
                   f"momentum_value={details.get('momentum_value', 'N/A')}"]
        if squeeze_on and momentum_up:
            state = 'SQUEEZE_RELEASE'
        elif squeeze_on:
            state = 'SQUEEZE_ACTIVE'
        elif momentum_up:
            state = 'MOMENTUM_CONFIRMING'
        else:
            state = 'SQUEEZE_FAILED'

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=(state == 'SQUEEZE_RELEASE'), reasons=reasons,
        )
