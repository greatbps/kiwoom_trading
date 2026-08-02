"""
Position Sizing Engine — v1.3

모든 사이즈 modifier를 한 함수에서 결합한다.
개별 modifier가 main_auto_trading.py 곳곳에 흩어지지 않도록 표준화.

공식:
    final_size = base_size
                 × route_mult      (PRIMARY=1.0 / RAE=0.7)
                 × g3_mult         (soft_penalty=0.6 / normal=1.0)
                 × dd_mult         (NORMAL=1.0 / CAUTION=0.7 / DANGER=0.4 / HALT=0.0)
                 × session_mult    (세션 가드 적용값)
                 × conservative_mult (Conservative mode: 0.5)
                 clamp [0, hard_max]

사용:
    from core.position_sizing import compute_final_size

    final = compute_final_size(
        base_size=0.5,
        route='RAE',
        g3_penalty=True,
        dd_level='CAUTION',
        conservative=False,
        session_mult=1.0,
    )
"""

from __future__ import annotations
from typing import Dict, Literal, Optional


RouteType    = Literal['PRIMARY', 'RAE']
DDLevel      = Literal['NORMAL', 'CAUTION', 'DANGER', 'HALT']

# ── 기본 배율 테이블 ──────────────────────────────────────────────────────────
ROUTE_MULT: Dict[str, float] = {
    'PRIMARY': 1.0,
    'RAE':     0.7,
}

DD_MULT: Dict[str, float] = {
    'NORMAL':  1.0,
    'CAUTION': 0.7,
    'DANGER':  0.4,
    'HALT':    0.0,
}

G3_SOFT_PENALTY_MULT = 0.6
CONSERVATIVE_MULT    = 0.5
HARD_MAX_DEFAULT     = 2.0  # 기본 최대 사이즈 (100% 기준의 200%)


def compute_final_size(
    base_size: float,
    route: RouteType = 'PRIMARY',
    g3_penalty: bool = False,
    g3_penalty_mult: float = G3_SOFT_PENALTY_MULT,
    dd_level: DDLevel = 'NORMAL',
    session_mult: float = 1.0,
    conservative: bool = False,
    conservative_mult: float = CONSERVATIVE_MULT,
    ted_mult: float = 1.0,
    reclaim_bonus_mult: float = 1.0,
    hard_max: float = HARD_MAX_DEFAULT,
) -> Dict:
    """
    최종 포지션 사이즈 계산.

    Args:
        base_size:          기본 사이즈 (CHoCH 등급·구조 기반 초기값)
        route:              'PRIMARY' | 'RAE'
        g3_penalty:         G3 HIGH_PROX soft penalty 적용 여부
        g3_penalty_mult:    G3 penalty 배율 (기본 0.6)
        dd_level:           DrawdownEngine 레벨
        session_mult:       세션 가드 배율 (1.0 = 무적용)
        conservative:       Conservative mode 활성 여부
        conservative_mult:  Conservative 배율 (기본 0.5)
        ted_mult:           TED 실패 시 soft penalty (기본 1.0)
        reclaim_bonus_mult: reclaim 감지 시 bonus (기본 1.0, 현재 사이즈 보호)
        hard_max:           최대 허용 사이즈 (clamp upper bound)

    Returns:
        {
            'final_size': float,
            'components': dict,  # 각 modifier 값 추적
            'clamped':    bool,  # hard_max에 의해 clamped 됐는지
        }
    """
    components: Dict[str, float] = {
        'base':             round(float(base_size), 4),
        'route_mult':       ROUTE_MULT.get(route, 1.0),
        'g3_mult':          float(g3_penalty_mult) if g3_penalty else 1.0,
        'dd_mult':          DD_MULT.get(dd_level, 1.0),
        'session_mult':     round(float(session_mult), 4),
        'conservative_mult': float(conservative_mult) if conservative else 1.0,
        'ted_mult':         round(float(ted_mult), 4),
    }

    result = float(base_size)
    for key, mult in components.items():
        if key == 'base':
            continue
        result *= mult

    clamped = result > hard_max
    result = min(result, hard_max)
    result = max(result, 0.0)

    # NaN/Inf 방어
    if not (0.0 <= result <= hard_max):
        result = 0.0

    return {
        'final_size': round(result, 4),
        'components': components,
        'clamped':    clamped,
        'route':      route,
        'dd_level':   dd_level,
    }
