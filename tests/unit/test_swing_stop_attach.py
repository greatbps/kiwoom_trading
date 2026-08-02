"""
SWING_RISK_ATTACH 손절값 전달 회귀 테스트.

━━━ 왜 필요한가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `_attach_swing_risk_positions()` 가 만드는 포지션 딕셔너리에
  `structure_stop_price` 가 빠져 있었다. 그러면
  `exit_logic_optimized.check_exit_signal()` 이 구조손절 분기를 타지
  못하고 SWING fallback(`swing_hard_stop_pct` = -12%)으로 빠진다.

  설계 손절이 -1.4% ~ -5.3% 인데 실효 손절선이 -12% 가 되고,
  그 사이 구간에서는 경고 로그만 남고 청산되지 않는다.

  2026-05 ~ 2026-07 실거래 스윙 5건 중 4건이 손절가보다 낮은 가격에
  청산됐고, 초과손실이 691,142원 — 스윙 총손실의 63.5% 였다.

  키 하나가 빠지는 것만으로 재발하고, 재발해도 예외가 나지 않는다.
  그래서 테스트로 못박는다.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

MAIN = os.path.join(ROOT, 'main_auto_trading.py')
EXIT = os.path.join(ROOT, 'trading', 'exit_logic_optimized.py')


def _attach_fn() -> ast.FunctionDef:
    """`_attach_swing_risk_positions` AST 노드."""
    tree = ast.parse(open(MAIN, encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name == '_attach_swing_risk_positions':
            return node
    pytest.fail('_attach_swing_risk_positions 함수를 찾지 못했다')


def _position_dict_keys(fn: ast.FunctionDef) -> set[str]:
    """함수 안에서 `self.positions[...] = {...}` 로 만드는 딕셔너리의 키."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        tgt = node.targets[0]
        if isinstance(tgt, ast.Subscript) and \
                isinstance(tgt.value, ast.Attribute) and \
                tgt.value.attr == 'positions':
            return {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant)}
    pytest.fail('self.positions[...] = {...} 대입을 찾지 못했다')


def test_attach_passes_structure_stop_price():
    """
    ⚠️ 이 키가 빠지면 설계 손절이 집행되지 않는다.
       예외도 나지 않고 로그도 남지 않는다 — 조용히 -12% 까지 방치된다.
    """
    keys = _position_dict_keys(_attach_fn())
    assert 'structure_stop_price' in keys, (
        'SWING 포지션 편입 시 structure_stop_price 가 빠졌다. '
        'exit_logic 이 구조손절 분기를 타지 못하고 -12% fallback 으로 빠진다.'
    )


def test_attach_keeps_swing_markers():
    """`_is_swing` 판정에 쓰이는 두 키가 유지되어야 한다."""
    keys = _position_dict_keys(_attach_fn())
    assert 'strategy_horizon' in keys
    assert 'strategy' in keys


def test_exit_logic_reads_structure_stop_price():
    """
    반대편도 고정한다 — exit_logic 이 키 이름을 바꾸면 전달해도 소용없다.
    """
    src = open(EXIT, encoding='utf-8').read()
    assert "position.get('structure_stop_price')" in src, (
        'exit_logic_optimized 가 structure_stop_price 를 더 이상 읽지 않는다. '
        'attach 쪽 키 이름도 함께 확인해야 한다.'
    )


def test_swing_fallback_still_exists():
    """
    fallback 자체는 남아 있어야 한다. 손절값이 없는 포지션(수동 매수 등)이
    무방비가 되면 안 된다.
    """
    src = open(EXIT, encoding='utf-8').read()
    assert 'swing_hard_stop_pct' in src


def test_invalid_stop_is_rejected():
    """
    진입가 이상인 손절가는 버려야 한다.

    ⚠️ 그대로 넣으면 편입 즉시 손절 조건이 참이 되어 전량 청산된다.
       손절값 전달을 고치면서 이 방어를 빼면 더 큰 사고가 난다.
    """
    src = open(MAIN, encoding='utf-8').read()
    i = src.index('def _attach_swing_risk_positions')
    body = src[i:i + 4000]
    assert '_stop >= entry_price' in body, (
        '진입가 이상인 손절가를 걸러내는 방어가 없다.'
    )


def test_attach_logs_stop_value():
    """
    손절값을 로그에 남긴다.

    이 결함이 몇 달간 드러나지 않은 이유가 관측 부재였다.
    '어태치했다' 만 찍고 '무엇을' 어태치했는지 안 찍었다.
    """
    src = open(MAIN, encoding='utf-8').read()
    i = src.index('def _attach_swing_risk_positions')
    body = src[i:i + 5000]
    assert 'stop=' in body, 'SWING_RISK_ATTACH 로그에 손절값이 없다'
