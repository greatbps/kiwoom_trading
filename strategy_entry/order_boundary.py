"""strategy_entry/order_boundary.py — WI-28 §3/§4/§9 Order Construction + Kiwoom Order Boundary.

Order Candidate(§13, WI-25) 다음 단계: 실제 Kiwoom 주문 함수(kiwoom_api.py::
KiwoomAPI.order_buy)가 필요로 하는 형태로 주문 객체를 구성하되, 그 함수를
절대 호출하지 않는 Dry-Run 경계를 둔다. 이 파일의 `submit_to_kiwoom_dry_run()`
이 곧 "Kiwoom Order Boundary"다 — 여기서 항상 멈춘다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from strategy_entry.types import OrderCandidate, StrategyEntryRequest

BLOCKED_BY_DRY_RUN = 'BLOCKED_BY_DRY_RUN'


@dataclass
class KiwoomOrderRequest:
    """§9 — 실제 kiwoom_api.py::order_buy()가 요구하는 필드에 맞춰 구성한
    주문 요청 객체. 실제 API에는 절대 전달하지 않는다.
    """
    stock_code: str
    side: str
    quantity: int
    price: int
    trade_type: str
    strategy_seq: int
    signal_id: Optional[str]
    candidate_id: Optional[str]
    decision_id: str
    entry_mode: str
    order_timestamp: str


def construct_order_request(
    order_candidate: OrderCandidate,
    request: StrategyEntryRequest,
    entry_mode: str = 'DRY_RUN_ENTRY',
) -> KiwoomOrderRequest:
    """Order Candidate(+원본 Request)로부터 Kiwoom 주문 형태의 객체를 만든다.
    strategy_seq 등 Identity(§9 - 전략 ID가 다른 전략의 값으로 변하지
    않는지 확인)는 OrderCandidate/Request에서 그대로 옮겨온다 - 새로 계산하지
    않는다.
    """
    return KiwoomOrderRequest(
        stock_code=order_candidate.symbol,
        side=order_candidate.side,
        quantity=order_candidate.quantity,
        price=int(order_candidate.price) if order_candidate.price else 0,
        trade_type='0',  # 보통가 (kiwoom_api.py::order_buy 기본값과 동일)
        strategy_seq=order_candidate.strategy_seq,
        signal_id=order_candidate.signal_id,
        candidate_id=request.candidate_id,
        decision_id=order_candidate.decision_id,
        entry_mode=entry_mode,
        order_timestamp=datetime.now().isoformat(timespec='seconds'),
    )


def submit_to_kiwoom_dry_run(order_request: KiwoomOrderRequest) -> dict:
    """Kiwoom Order Boundary. 이 함수는 kiwoom_api.py의 order_buy/order_sell/
    send_order 등 실제 주문 함수를 어떤 경로로도 호출하지 않는다 - 함수
    시그니처에 KiwoomAPI 인스턴스조차 받지 않는다(구조적으로 호출이
    불가능하게 설계됨). §4가 요구하는 "실제 주문 직전 Dry-Run 경계"가
    바로 여기다.
    """
    return {
        'status': BLOCKED_BY_DRY_RUN,
        'reason': 'WI-28 Pre-Flight - 실제 주문 API(kiwoom_api.py::order_buy) 호출 금지 경계',
        'order_request': order_request,
    }
