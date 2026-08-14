"""
core/risk_layer.py — Risk Layer 순수 함수 (Iteration 25)

Volatility Size Reduction / ATR Adaptive Stop 공식을 운영 코드와
`phase1/risk_layer_v25.py` 연구 모듈이 **동일하게 import해서** 쓴다.
공식을 두 곳에 따로 구현하지 않음으로써 Replay-운영 Parity를 사후 비교가
아니라 구조적으로 보장한다.

상수는 Iteration 24(`phase1/risk_management_v24/risk_management_v24.json`)에서
확정된 값을 그대로 동결한다 — 재튜닝하지 않는다.
"""
from __future__ import annotations

from typing import Optional

# Iteration 24 확정값 — 재튜닝 금지
ATR_MULT = 2.0
SIZE_REDUCE = 0.7
VOLATILITY20_THRESHOLD = 4.9941  # Train p80 (risk_management_v24.json)


def volatility_size_mult(volatility20: Optional[float], enabled: bool) -> float:
    """Volatility Size Reduction 배율.

    enabled=False 또는 volatility20 없음 → 1.0(무변경).
    volatility20 > VOLATILITY20_THRESHOLD → SIZE_REDUCE.
    """
    if not enabled or volatility20 is None:
        return 1.0
    if volatility20 > VOLATILITY20_THRESHOLD:
        return SIZE_REDUCE
    return 1.0


def atr_stop_price(entry_price: float, atr_pct: Optional[float], enabled: bool) -> Optional[float]:
    """ATR Adaptive Stop 가격 = entry_price - ATR_MULT * (entry_price * atr_pct / 100).

    enabled=False, entry_price<=0, atr_pct 없음/0 이하 → None
    (호출자는 None이면 기존 폴백 로직을 그대로 쓴다).
    """
    if not enabled or entry_price is None or entry_price <= 0:
        return None
    if atr_pct is None or atr_pct <= 0:
        return None
    atr_abs = entry_price * atr_pct / 100
    return entry_price - ATR_MULT * atr_abs
