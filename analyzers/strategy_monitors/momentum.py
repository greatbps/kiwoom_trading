"""seq 32 — Momentum. WI-10 Phase 1: 단일 진입점이 없고 지표가 여러 파일에 흩어져
있다(DEFINED-분산). `analyzers/indicators.py`의 기존 계산함수(무수정 재사용)를 그대로
가져와 상태만 조합한다 — 새 지표 계산식은 추가하지 않는다.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from analyzers.indicators import calculate_price_velocity, calculate_volume_momentum

from .base import StrategyMonitor, StrategyMonitorResult

MIN_BARS = 10  # calculate_price_velocity(period=5)+여유, calculate_volume_momentum(period=10)


class MomentumMonitor(StrategyMonitor):
    seq = 32
    strategy_name = 'Momentum'

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        if df is None or len(df) < MIN_BARS:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{MIN_BARS}봉) — calculate_price_velocity/'
                        'calculate_volume_momentum 최소 요구량 미달')
        try:
            d = calculate_price_velocity(df, period=5)
            d = calculate_volume_momentum(d, period=10)
            velocity = float(d['price_velocity'].iloc[-1])
            vol_mom = float(d['volume_momentum'].iloc[-1])
        except Exception as e:
            return self._error_result(symbol, e)

        reasons = [f'price_velocity={velocity:.2f}%', f'volume_momentum={vol_mom:.2f}']
        if velocity > 0 and vol_mom > 0:
            state = 'MOMENTUM_CONFIRMING'
        elif velocity > 0 or vol_mom > 0:
            state = 'CANDIDATE'
        elif velocity < -2:
            state = 'MOMENTUM_FAILED'
        else:
            state = 'MOMENTUM_WEAKENING'

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=(state == 'MOMENTUM_CONFIRMING'), reasons=reasons,
        )
