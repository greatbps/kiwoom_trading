"""공통 Interface — WI-10 §11 최소 필드 그대로."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import pandas as pd


@dataclass
class StrategyMonitorResult:
    condition_seq: int
    strategy_name: str
    symbol: str
    monitor_state: str
    signal: Optional[bool] = None
    reasons: List[str] = field(default_factory=list)
    timestamp: str = ''
    data_quality: str = 'OK'  # OK | INSUFFICIENT_DATA | NO_CODE | ERROR

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec='seconds')


class StrategyMonitor:
    """모든 Monitor의 공통 부모. 서브클래스는 evaluate()만 구현한다."""

    seq: int = 0
    strategy_name: str = 'UNKNOWN'

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        raise NotImplementedError

    def _not_implemented_result(self, symbol: str, note: str) -> StrategyMonitorResult:
        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state='NOT_IMPLEMENTED', signal=None, reasons=[note],
            data_quality='NO_CODE',
        )

    def _insufficient_data_result(self, symbol: str, note: str) -> StrategyMonitorResult:
        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state='CANDIDATE', signal=None, reasons=[note],
            data_quality='INSUFFICIENT_DATA',
        )

    def _error_result(self, symbol: str, exc: Exception) -> StrategyMonitorResult:
        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state='ERROR', signal=None,
            reasons=[f'{type(exc).__name__}: {exc}'], data_quality='ERROR',
        )
