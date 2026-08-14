"""strategy_entry/preflight.py — WI-28 Order Path Pre-Flight 통합 실행기.

Strategy Signal → strategy_entry(Ranking/Gate/Risk) → Position/Duplicate →
Order Construction → Kiwoom Order Boundary(Dry-Run) 전 구간을 한 번에
실행하고 단계별 결과를 반환한다. 실제 execute_buy()/kiwoom_api.order_buy()는
어디에서도 호출하지 않는다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_entry.adapter import StrategyEntryAdapter
from strategy_entry.order_boundary import construct_order_request, submit_to_kiwoom_dry_run
from strategy_entry.shadow import run_shadow, SHADOW_ALLOWED
from strategy_entry.types import StrategyEntryRequest


def run_preflight(
    adapter: StrategyEntryAdapter,
    requests: List[StrategyEntryRequest],
    existing_position_symbols: Optional[set] = None,
) -> List[dict]:
    """§3 전 구간을 실행한다. 반환값은 run_shadow()의 결과 목록에
    order_request/boundary_result가 추가된 형태다."""
    results = run_shadow(adapter, requests, existing_position_symbols=existing_position_symbols)

    for r in results:
        r['order_request'] = None
        r['boundary_result'] = None
        if r['shadow_result'] == SHADOW_ALLOWED and r['order_candidate'] is not None:
            order_req = construct_order_request(r['order_candidate'], r['request'])
            r['order_request'] = order_req
            r['boundary_result'] = submit_to_kiwoom_dry_run(order_req)

    return results


def run_preflight_by_seq(
    adapter: StrategyEntryAdapter,
    fixtures: Dict[int, StrategyEntryRequest],
    existing_position_symbols: Optional[set] = None,
) -> Dict[int, dict]:
    """8개 전략 fixture(seq -> request) 1건씩을 Preflight 실행하고 seq를
    key로 하는 dict로 반환한다(보고서 테이블 구성용)."""
    results = run_preflight(adapter, list(fixtures.values()),
                             existing_position_symbols=existing_position_symbols)
    return {r['request'].strategy_seq: r for r in results}
