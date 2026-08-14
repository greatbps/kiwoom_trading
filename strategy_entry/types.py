"""strategy_entry/types.py — WI-25 §4/§5 공통 Interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

VALID_STRATEGY_SEQS = tuple(range(32, 40))  # 32~39

# §21 Fail-Closed 판정 단계
STAGE_MISSING_FIELD = 'ENTRY_REJECTED_MISSING_FIELD'
STAGE_UNKNOWN_STRATEGY = 'ENTRY_REJECTED_UNKNOWN_STRATEGY'
STAGE_ADAPTER_ERROR = 'ENTRY_REJECTED_ADAPTER_ERROR'
STAGE_STRATEGY_DISABLED = 'STRATEGY_DISABLED'
STAGE_RANK_BLOCKED = 'RANK_BLOCKED'
STAGE_GATE_BLOCKED = 'GATE_BLOCKED'
STAGE_RISK_BLOCKED = 'RISK_BLOCKED'
STAGE_ALLOWED = 'ALLOWED'

REQUIRED_FIELDS = ('strategy_seq', 'symbol', 'signal_timestamp', 'signal_price', 'signal_id')


@dataclass
class StrategyEntryRequest:
    """§4/§5 — Strategy Signal 1건을 Entry Adapter에 넣기 위한 요청 객체.
    Strategy Identity(§5) 보존을 위한 필드는 전부 Optional이 아니라 기본값
    None으로 두되, Adapter가 __post_init__이 아니라 evaluate() 시점에
    Fail-Closed 검증한다(구성 자체는 항상 허용해 테스트에서 '누락된 요청'도
    쉽게 만들 수 있게 함)."""
    strategy_seq: Optional[int]
    strategy_name: Optional[str]
    symbol: Optional[str]
    signal_timestamp: Optional[str]
    signal_price: Optional[float]
    candidate_id: Optional[str] = None
    signal_id: Optional[str] = None
    signal_reason: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def missing_fields(self) -> list[str]:
        missing = []
        for f in REQUIRED_FIELDS:
            v = getattr(self, f)
            if v is None or (isinstance(v, str) and not v.strip()):
                missing.append(f)
        return missing


@dataclass
class StrategyEntryDecision:
    """§4 — Entry Adapter의 최종 판정. Strategy Identity(§5) 필드를 전부
    보존해 최종 Decision까지 strategy_seq 등이 사라지지 않게 한다."""
    allowed: bool
    strategy_seq: Optional[int]
    strategy_name: Optional[str]
    symbol: Optional[str]
    decision_stage: str
    reason: str
    candidate_id: Optional[str] = None
    signal_id: Optional[str] = None
    signal_timestamp: Optional[str] = None
    signal_price: Optional[float] = None
    signal_reason: Optional[str] = None
    enable_status: Optional[str] = None
    ranking_result: Optional[dict] = None
    gate_result: Optional[dict] = None
    risk_result: Optional[dict] = None


@dataclass
class OrderCandidate:
    """§13 — 실제 주문 직전 단계. execute_buy()에는 절대 전달하지 않는다
    (Shadow 검증 전용, §22)."""
    strategy_seq: int
    symbol: str
    side: str
    quantity: int
    price: float
    signal_id: Optional[str]
    decision_id: str
