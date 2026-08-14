"""strategy_entry/shadow.py — WI-25 §14/§15/§17/§18 Shadow Execution Runner.

StrategyEntryRequest 목록을 StrategyEntryAdapter로 평가하고, 중복 방지(§17)와
Position Conflict(§18)까지 적용해 최종 Shadow 결과를 만든다. execute_buy()는
어디에서도 호출하지 않는다(§22).
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import List

from strategy_entry.adapter import StrategyEntryAdapter
from strategy_entry.types import (
    StrategyEntryRequest, OrderCandidate, STAGE_ALLOWED,
    STAGE_MISSING_FIELD, STAGE_UNKNOWN_STRATEGY, STAGE_ADAPTER_ERROR,
    STAGE_STRATEGY_DISABLED, STAGE_RANK_BLOCKED, STAGE_GATE_BLOCKED,
    STAGE_RISK_BLOCKED,
)

SHADOW_ALLOWED = 'SHADOW_ALLOWED'
SHADOW_RANK_BLOCKED = 'SHADOW_RANK_BLOCKED'
SHADOW_GATE_BLOCKED = 'SHADOW_GATE_BLOCKED'
SHADOW_RISK_BLOCKED = 'SHADOW_RISK_BLOCKED'
SHADOW_DUPLICATE = 'SHADOW_DUPLICATE'
SHADOW_DISABLED = 'SHADOW_DISABLED'
SHADOW_REJECTED = 'SHADOW_REJECTED'  # 필수필드누락/알수없는전략/adapter예외 통합
POSITION_CONFLICT = 'POSITION_CONFLICT'

_STAGE_TO_SHADOW = {
    STAGE_MISSING_FIELD: SHADOW_REJECTED,
    STAGE_UNKNOWN_STRATEGY: SHADOW_REJECTED,
    STAGE_ADAPTER_ERROR: SHADOW_REJECTED,
    STAGE_STRATEGY_DISABLED: SHADOW_DISABLED,
    STAGE_RANK_BLOCKED: SHADOW_RANK_BLOCKED,
    STAGE_GATE_BLOCKED: SHADOW_GATE_BLOCKED,
    STAGE_RISK_BLOCKED: SHADOW_RISK_BLOCKED,
    STAGE_ALLOWED: SHADOW_ALLOWED,
}


def run_shadow(
    adapter: StrategyEntryAdapter,
    requests: List[StrategyEntryRequest],
    existing_position_symbols: set = None,
) -> List[dict]:
    """§14/§15/§16/§17/§18을 전부 적용한 최종 Shadow 결과 목록을 반환한다.
    한 원소 = {request, decision, order_candidate(Optional), shadow_result}.

    existing_position_symbols(WI-28 §6): 이미 실제 포지션이 있는 종목 집합을
    넘기면, 그 종목을 대상으로 한 신규 Signal은 Order Candidate를 만들지
    않고 POSITION_CONFLICT로 표시한다(기존 포지션 보호 - 신규 매수 중복 방지).
    생략하면(None) 기존 WI-25 동작과 완전히 동일하다."""
    existing_position_symbols = existing_position_symbols or set()
    seen_dedup_keys = set()
    results = []

    for req in requests:
        decision = adapter.evaluate(req, _shadow_bypass_enable_gate=True)
        shadow_result = _STAGE_TO_SHADOW.get(decision.decision_stage, SHADOW_REJECTED)
        order_candidate = None

        if shadow_result == SHADOW_ALLOWED and req.symbol in existing_position_symbols:
            shadow_result = POSITION_CONFLICT  # WI-28 §6 - 기존 보유 종목 신규 진입 차단
        elif shadow_result == SHADOW_ALLOWED:
            dedup_key = (req.strategy_seq, req.symbol, req.signal_timestamp)
            if dedup_key in seen_dedup_keys:
                shadow_result = SHADOW_DUPLICATE  # §17
            else:
                seen_dedup_keys.add(dedup_key)
                order_candidate = OrderCandidate(
                    strategy_seq=req.strategy_seq, symbol=req.symbol, side='BUY',
                    quantity=1,  # 실제 수량 계산은 이번 WI 범위 밖(§13 "생성/검증까지만")
                    price=req.signal_price, signal_id=req.signal_id,
                    decision_id=str(uuid.uuid4()),
                )

        results.append({
            'request': req, 'decision': decision,
            'order_candidate': order_candidate, 'shadow_result': shadow_result,
        })

    _apply_position_conflict(results)  # §18(WI-25) - 같은 배치 내 교차전략 충돌
    return results


def _apply_position_conflict(results: List[dict]) -> None:
    """같은 symbol에 서로 다른 strategy_seq의 OrderCandidate가 동시에
    존재하면 전부 POSITION_CONFLICT로 표시한다. 어느 쪽을 우선할지는 이번
    WI에서 임의 결정하지 않는다(§18)."""
    by_symbol = defaultdict(list)
    for r in results:
        if r['order_candidate'] is not None:
            by_symbol[r['request'].symbol].append(r)

    for symbol, rows in by_symbol.items():
        seqs = {r['request'].strategy_seq for r in rows}
        if len(seqs) > 1:
            for r in rows:
                r['shadow_result'] = POSITION_CONFLICT
