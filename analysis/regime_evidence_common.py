"""
analysis/regime_evidence_common.py

Regime Gate Evidence Level(E0~E3) 자동 판정 — 일일/주간 리포트가 공유하는 단일 판정 로직.
정책/threshold 변경과 무관한 순수 읽기 전용 분류 함수.

기준 (reports/regime_validation_plan.md와 동일 임계값):
    E0: 데이터/표본 없음 (이론적 설계만)
    E1: 사후 시뮬레이션(regime_block_simulator) 표본 < 30건
    E2: 사후 시뮬레이션 표본 >= 30건, 또는 독립 데이터소스 재검증 완료 — 실거래 표본은 아직 부족
    E3: 실거래(PASS→체결) 50건 이상 AND REGIME_BLOCK 실사례 100건 이상 누적
"""
from __future__ import annotations

from typing import Any, Dict


def classify_evidence_level(realized_trade_n: int, regime_block_n: int, simulated_block_n: int) -> Dict[str, Any]:
    """
    Args:
        realized_trade_n: 실제 체결(PASS→Entry→Exit) 누적 건수
        regime_block_n: research.decision_ledger 기준 REGIME_BLOCKED 누적 건수
        simulated_block_n: regime_block_simulator 사후 시뮬레이션 누적 표본 수

    Returns:
        {'level': 'E0'|'E1'|'E2'|'E3', 'reason': str, 'criteria': str}
    """
    criteria = (
        "E3: 실거래 50건+ AND REGIME_BLOCK 100건+ | "
        "E2: 시뮬레이션 30건+ 또는 독립소스 재검증 완료 | "
        "E1: 시뮬레이션 <30건 | E0: 표본 없음"
    )

    if realized_trade_n >= 50 and regime_block_n >= 100:
        return {'level': 'E3', 'reason': f'실거래 {realized_trade_n}건 + REGIME_BLOCK {regime_block_n}건 누적', 'criteria': criteria}
    if simulated_block_n >= 30:
        return {'level': 'E2', 'reason': f'사후 시뮬레이션 {simulated_block_n}건 (실거래 {realized_trade_n}건은 아직 부족)', 'criteria': criteria}
    if simulated_block_n > 0:
        return {'level': 'E1', 'reason': f'사후 시뮬레이션 {simulated_block_n}건 (30건 미만)', 'criteria': criteria}
    return {'level': 'E0', 'reason': '표본 없음', 'criteria': criteria}
