"""
EvaluationContext — begin_evaluation() 반환 객체.

모든 downstream 메서드(record_rejection / record_acceptance / record_execution)가
동일한 Context를 전달받아 파라미터 시그니처를 안정적으로 유지한다.
ctx=None 이면 Research Layer가 비활성 또는 실패 상태 → 모든 후속 호출은 no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class EvaluationContext:
    trace_id: str
    candidate_id: str
    symbol: str
    observed_at: datetime
    policy_version: str
    price: float = 0.0
    strategy_type: str = 'SMC_INTRADAY'
    market_snapshot_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    # ── 편의 메서드 ───────────────────────────────────────────────

    def is_valid(self) -> bool:
        return bool(self.trace_id and self.candidate_id and self.symbol)

    def __bool__(self) -> bool:
        return self.is_valid()

    def age_seconds(self) -> float:
        return (datetime.now() - self.observed_at).total_seconds()

    def __repr__(self) -> str:
        return (
            f"EvalCtx(trace={self.trace_id} sym={self.symbol} "
            f"policy={self.policy_version} age={self.age_seconds():.1f}s)"
        )
