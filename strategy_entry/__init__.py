"""
strategy_entry — WI-25 Independent Strategy Entry Architecture.

seq32~39 Strategy Signal을 SMC와 완전히 독립적으로 Common Ranking/Gate/Risk까지
평가할 수 있는 Adapter 계층. 실제 execute_buy() 호출은 하지 않는다 — Shadow
검증 전용(§2/§10/§22).
"""
from strategy_entry.types import (
    StrategyEntryRequest,
    StrategyEntryDecision,
    OrderCandidate,
)
from strategy_entry.enable_registry import StrategyEnableRegistry
from strategy_entry.adapter import StrategyEntryAdapter
from strategy_entry.order_boundary import (
    KiwoomOrderRequest, construct_order_request, submit_to_kiwoom_dry_run,
)
from strategy_entry.preflight import run_preflight, run_preflight_by_seq

__all__ = [
    'StrategyEntryRequest',
    'StrategyEntryDecision',
    'OrderCandidate',
    'StrategyEnableRegistry',
    'StrategyEntryAdapter',
    'KiwoomOrderRequest',
    'construct_order_request',
    'submit_to_kiwoom_dry_run',
    'run_preflight',
    'run_preflight_by_seq',
]
