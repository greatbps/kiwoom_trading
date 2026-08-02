"""
포지션 리스크 스키마 회귀 테스트 (Iteration 6-1)

━━━ 무엇을 지키는가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `self.positions` 에 쓰는 경로가 4개다.

      _restore_positions_state()          재시작 복원
      initialize_account()  신규/갱신     브로커 잔고 동기화
      _attach_swing_risk_positions()      swing_executor 편입

  경로마다 넣는 키가 달라서 `structure_stop_price` 가 유실됐고,
  exit_logic 이 구조손절 분기를 못 타고 SWING fallback(-12%)으로 빠졌다.
  설계 손절이 -1.4~-5.3% 인데 -12% 까지 방치됐다.

  실거래 스윙 5건 중 4건이 손절가보다 낮게 청산됐고 초과손실 691,142원 —
  전체 스윙 손실의 63.5% 였다.

  ⚠️ 이 결함은 **예외를 내지 않는다.** 키 하나가 빠질 뿐이고 프로그램은
     정상 동작한다. 그래서 몇 달간 드러나지 않았다. 테스트로만 잡힌다.
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
SRC = open(MAIN, encoding='utf-8').read()
TREE = ast.parse(SRC)


def _fn(name: str):
    # ⚠️ async def 는 AsyncFunctionDef 다. FunctionDef 만 보면 못 찾는다.
    for n in ast.walk(TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == name:
            return n
    pytest.fail(f'{name} 함수를 찾지 못했다')


def _calls_normalize(fn) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == '_normalize_position':
            return True
    return False


# ── 스키마 정의 ──────────────────────────────────────────────────────────
def test_schema_helper_exists():
    fn = _fn('_normalize_position')
    assert fn is not None


def test_schema_declares_required_fields():
    """POSITION_RISK_FIELDS 가 5개 필수 필드를 선언한다."""
    assert 'POSITION_RISK_FIELDS' in SRC
    for f in ('strategy_horizon', 'structure_stop_price', 'entry_price',
              'entry_reason', 'position_type'):
        assert f"'{f}'" in SRC, f'필수 필드 {f} 미선언'


# ── Case 1~3: 세 생성 경로가 모두 스키마를 거친다 ────────────────────────
def test_case3_broker_sync_normalizes():
    """Case 3 — initialize_account (브로커 동기화) 에서 stop 유지."""
    assert _calls_normalize(_fn('initialize_account')), (
        'initialize_account 가 _normalize_position 을 호출하지 않는다. '
        '브로커 잔고 복원 경로에서 손절값이 유실된다 — '
        '실거래 최대 손실(삼성SDI -376,000원)이 이 경로였다.'
    )


def test_case2_restart_restore_normalizes():
    """Case 2 — 재시작 복원에서 stop 유지."""
    assert _calls_normalize(_fn('_restore_positions_state')), (
        '_restore_positions_state 가 _normalize_position 을 호출하지 않는다.'
    )


def test_case1_swing_attach_normalizes():
    """Case 1 — swing_executor 매수분 편입에서 stop 존재."""
    fn = _fn('_attach_swing_risk_positions')
    assert _calls_normalize(fn)
    # 딕셔너리 자체에도 키가 있어야 한다 (헬퍼가 빠져도 1차 방어)
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            t = node.targets[0]
            if isinstance(t, ast.Subscript) and \
                    getattr(t.value, 'attr', None) == 'positions':
                keys = {k.value for k in node.value.keys
                        if isinstance(k, ast.Constant)}
                assert 'structure_stop_price' in keys
                return
    pytest.fail('attach 경로의 positions 대입을 찾지 못했다')


# ── 손절값 원천이 보존되는가 ─────────────────────────────────────────────
def test_swing_state_dict_preserved_not_just_keys():
    """
    ⚠️ `set(json.load(...).keys())` 로 키만 뽑으면 stop_price 가 버려진다.
       그게 손절 유실의 출발점이었다.
    """
    assert '_swing_state' in SRC, 'swing_positions.json 전체 dict 를 보존해야 한다'
    assert "set(_json.loads(_sp.read_text(encoding='utf-8')).keys())" not in SRC, (
        'swing_positions.json 에서 키만 뽑고 있다 — stop_price 가 버려진다.'
    )


# ── 방어 로직 (Phase 3) ──────────────────────────────────────────────────
def test_missing_stop_is_guarded_before_exit_logic():
    """
    SWING 인데 손절값이 없으면 -12% fallback 으로 가기 전에 막아야 한다.
    """
    assert 'SWING_STOP_MISSING' in SRC, (
        'exit_logic 호출 전 SWING 손절 유실 방어가 없다.'
    )
    i = SRC.index('SWING_STOP_MISSING')
    j = SRC.index('self.exit_logic.check_exit_signal')
    assert i < j, '방어가 exit_logic 호출보다 뒤에 있으면 소용없다'


def test_guard_applies_safe_stop_not_quarantine():
    """
    격리가 아니라 안전 손절을 적용해야 한다.

    ⚠️ 포지션을 감시에서 빼면(quarantine) 무방비가 되어 더 위험하다.
    """
    i = SRC.index('SWING_STOP_MISSING')
    block = SRC[i - 2000:i + 1500]
    assert 'max_stop_pct' in block, '안전 손절 기준값을 config 에서 읽지 않는다'
    assert 'stop_recovered' in block


def test_invalid_stop_rejected():
    """진입가 이상인 손절가는 버린다 — 넣으면 즉시 전량청산된다."""
    fn = _fn('_swing_stop_of')
    seg = ast.get_source_segment(SRC, fn) or ''
    assert 'stop >= entry_price' in seg


def test_normalize_does_not_overwrite_existing():
    """
    이미 채워진 값을 덮어쓰면 안 된다 — 제대로 동작하던 경로를 망친다.
    """
    seg = ast.get_source_segment(SRC, _fn('_normalize_position')) or ''
    assert "not pos.get('structure_stop_price')" in seg
    assert "not pos.get('strategy_horizon')" in seg


# ── 런타임 동작 ──────────────────────────────────────────────────────────
#
# ⚠️ 위 테스트들은 전부 소스 텍스트/AST 검사다. "코드가 거기 있다" 는
#    확인하지만 "제대로 동작한다" 는 확인하지 못한다.
#    _normalize_position 은 staticmethod 하나만 참조하는 순수 함수에
#    가까우므로 인스턴스 없이 직접 호출해 검증한다.
#    main_auto_trading 을 import 하면 로깅 핸들러 등 부작용이 생긴다.
#    소스에서 두 함수만 뽑아 독립 실행한다.
def _build_stub():
    import logging as _lg
    ns = {'logger': _lg.getLogger('test_stub')}
    body = []
    for name in ('_swing_stop_of', '_normalize_position'):
        seg = ast.get_source_segment(SRC, _fn(name))
        assert seg, f'{name} 소스를 뽑지 못했다'
        # 클래스 들여쓰기(4칸) 제거 + 데코레이터 제거
        lines = [ln[4:] if ln.startswith('    ') else ln
                 for ln in seg.split('\n')
                 if not ln.strip().startswith('@')]
        body.append('\n'.join(lines))
    exec('\n\n'.join(body), ns)
    stub = type('_Stub', (), {
        '_swing_stop_of': staticmethod(ns['_swing_stop_of']),
        '_normalize_position': ns['_normalize_position'],
    })
    return stub()


_Stub = _build_stub


def test_runtime_swing_gets_stop_from_swing_state():
    pos = {'strategy': 'swing', 'entry_price': 684000.0}
    _Stub()._normalize_position('006400', pos,
                                swing_entry={'stop_price': 670658.0})
    assert pos['structure_stop_price'] == 670658.0
    assert pos['strategy_horizon'] == 'SWING'
    assert pos['position_type'] == 'SWING'


def test_runtime_stop_above_entry_is_rejected():
    """진입가 이상이면 넣지 않는다 — 넣으면 즉시 전량청산된다."""
    pos = {'strategy': 'swing', 'entry_price': 684000.0}
    _Stub()._normalize_position('006400', pos,
                                swing_entry={'stop_price': 700000.0})
    assert not pos.get('structure_stop_price')


def test_runtime_existing_stop_not_overwritten():
    pos = {'strategy': 'swing', 'entry_price': 684000.0,
           'structure_stop_price': 660000.0}
    _Stub()._normalize_position('006400', pos,
                                swing_entry={'stop_price': 670658.0})
    assert pos['structure_stop_price'] == 660000.0


def test_runtime_non_swing_untouched():
    pos = {'strategy': 'smc', 'entry_price': 10000.0}
    _Stub()._normalize_position('005930', pos, swing_entry=None)
    assert pos['strategy_horizon'] == 'INTRADAY'
    assert not pos.get('structure_stop_price')


def test_runtime_missing_swing_entry_is_safe():
    """swing_positions.json 에 없어도 예외 없이 넘어가야 한다."""
    pos = {'strategy': 'swing', 'entry_price': 684000.0}
    _Stub()._normalize_position('006400', pos, swing_entry=None)
    assert pos['strategy_horizon'] == 'SWING'
    assert not pos.get('structure_stop_price')   # 방어로직이 뒤에서 채운다
