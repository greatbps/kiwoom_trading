"""
패턴 기반 포지션 사이징

역할:
    pattern confidence × 검증된 weight × 진입 신뢰도 → edge
    edge → position_size_mult 보정 배율

통합 방식:
    execute_buy()의 Kelly/confidence 이후 추가 multiplier로 적용.
    score_enabled=False 이면 mult=1.0 반환 (no-op).

설계 원칙:
    - edge < MIN_EDGE → skip (진입 차단)
    - NEUTRAL_EDGE 기준 정규화 → mult=1.0 이 "보통 신호"
    - MAX_MULT=2.0 상한, MIN_MULT=0.5 하한
    - weight=0 (미검증) → mult=1.0 유지 (패널티 없음)

stop 기반 수량 계산 (보조):
    risk_amount = balance × BASE_RISK × edge
    qty = risk_amount / abs(entry - stop)
    → 손절이 멀면 수량 자동 감소, 가까우면 증가
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── 파라미터 ──────────────────────────────────────────────────────────────────
BASE_RISK          = 0.02    # 계좌 기본 리스크 비율 (2%)
MIN_EDGE           = 0.08    # 이 미만이면 진입 차단 (NORMAL/AGGRESSIVE)
DEFENSIVE_MIN_EDGE = 0.12    # DEFENSIVE 전용 최소 edge (높은 기대값만 허용)
NEUTRAL_EDGE       = 0.15    # 이 수준이 mult=1.0 기준점
MIN_MULT      = 0.50    # 최소 배율
MAX_MULT      = 2.00    # 최대 배율 (weight > 1.0 방지와 별개)
MAX_POSITION  = 0.15    # stop-based qty 계산 시 계좌 최대 비중
MIN_POSITION  = 0.01    # stop-based qty 계산 시 계좌 최소 비중

_WEIGHTS_PATH = Path(__file__).parent.parent / 'data' / 'pattern_weights.json'


def _load_weight(phase_key: str) -> float:
    """pattern_weights.json 에서 특정 phase weight 조회."""
    if not _WEIGHTS_PATH.exists():
        return 0.0
    try:
        data  = json.loads(_WEIGHTS_PATH.read_text(encoding='utf-8'))
        entry = data.get('weights', {}).get(phase_key, 0.0)
        if isinstance(entry, dict):
            return float(entry.get('weight', 0.0))
        return float(entry)
    except Exception:
        return 0.0


DEFENSIVE_MIN_CONF = 0.85   # DEFENSIVE 모드에서 허용할 최소 confidence


def compute(
    pat_dict:          dict | None,
    entry_confidence:  float = 1.0,
    balance:           float = 0.0,
    entry:             float = 0.0,
    stop:              float = 0.0,
    market_mode:       str   = "NORMAL",
    min_conf_override: float | None = None,
) -> dict:
    """
    패턴 edge 계산 → 사이징 배율 반환.

    Args:
        pat_dict:         daily_patterns.json 의 {pattern, phase, confidence, ...}
        entry_confidence: SMC/전략 진입 신뢰도 (0~1)
        balance:          현재 예수금 (stop-based qty 계산용)
        entry:            진입가 (stop-based qty 계산용)
        stop:             손절가 (stop-based qty 계산용)
        market_mode:      ATR 3단계 모드 (AGGRESSIVE/NORMAL/DEFENSIVE)

    Returns:
        {
          'mult':     float,   # position_size_mult 에 곱할 배율 (1.0 = 변화 없음)
          'edge':     float,   # confidence × weight × entry_confidence
          'skip':     bool,    # True → 진입 차단
          'qty_hint': int,     # stop 기반 제안 수량 (0 = 계산 불가)
          'reason':   str,
        }
    """
    _none = {'mult': 1.0, 'edge': 0.0, 'skip': False, 'qty_hint': 0, 'reason': ''}

    if pat_dict is None:
        return {**_none, 'reason': 'no_pattern'}

    pattern   = pat_dict.get('pattern', '')
    phase     = pat_dict.get('phase', '')
    conf      = float(pat_dict.get('confidence', 0.0))
    phase_key = f'{pattern}({phase})'

    # DEFENSIVE 모드: confirmed + 고신뢰도만 허용 (저변동성에서 애매한 신호 차단)
    if market_mode == "DEFENSIVE":
        if phase != "confirmed":
            return {**_none, 'reason': f'defensive_non_confirmed:{phase_key}'}
        _min_conf = min_conf_override if min_conf_override is not None else DEFENSIVE_MIN_CONF
        if conf < _min_conf:
            return {**_none, 'reason': f'defensive_low_conf:{conf:.2f}<{_min_conf:.2f}'}

    weight = _load_weight(phase_key)
    if weight <= 0:
        # 미검증 패턴 → 사이징 변경 없이 통과 (패널티 없음)
        return {**_none, 'reason': f'no_weight:{phase_key}'}

    # edge = 패턴 완성도 × 검증 성과 × 진입 신뢰도
    edge = conf * weight * float(entry_confidence)
    edge = round(edge, 4)

    _edge_floor = DEFENSIVE_MIN_EDGE if market_mode == "DEFENSIVE" else MIN_EDGE
    if edge < _edge_floor:
        return {'mult': 1.0, 'edge': edge, 'skip': True,
                'qty_hint': 0, 'reason': f'edge_too_low:{edge:.3f}<{_edge_floor}(mode={market_mode})'}

    # mult 정규화: NEUTRAL_EDGE 기준 1.0
    mult = edge / NEUTRAL_EDGE
    mult = round(max(MIN_MULT, min(MAX_MULT, mult)), 3)

    # stop 기반 제안 수량 (BASE_RISK 고정 — mode 사이징은 position_size_mult에서만 적용)
    qty_hint = 0
    if balance > 0 and entry > 0 and stop > 0 and abs(entry - stop) > 0:
        risk_pct    = max(MIN_POSITION, min(MAX_POSITION, BASE_RISK * edge))
        risk_amount = balance * risk_pct
        qty_hint    = int(risk_amount / abs(entry - stop))

    reason = (f'{phase_key} conf={conf:.2f} w={weight:.4f} '
              f'ec={entry_confidence:.2f} mode={market_mode} → edge={edge:.3f} mult={mult}')

    return {
        'mult':     mult,
        'edge':     edge,
        'skip':     False,
        'qty_hint': qty_hint,
        'reason':   reason,
    }
