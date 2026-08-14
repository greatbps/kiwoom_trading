"""
조건검색 출처 추적 회귀 테스트 (Iteration 8-1)

━━━ 무엇을 지키는가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  실거래 매수의 95.8%가 조건검색식에서 오는데, 원장에는 전부
  `condition_name='VWAP+AI'` 하나로만 찍혔다. 조건식이 6개인데
  **어느 것이 이 종목을 물어왔는지 알 수 없었다.**
  조건식별 승률·PF 분석이 원천적으로 불가능한 상태였다.

  원인: `stock_to_condition_map[code] = idx` 가 덮어쓰기라 한 종목이
  여러 조건식에 걸리면 마지막 것만 남는다.

  ⚠️ 이 결함은 예외를 내지 않는다. 데이터가 조용히 안 쌓일 뿐이다.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

MAIN = os.path.join(ROOT, 'main_auto_trading.py')
SRC = open(MAIN, encoding='utf-8').read()
TREE = ast.parse(SRC)


def _fn(name):
    for n in ast.walk(TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == name:
            return n
    pytest.fail(f'{name} 없음')


# ── 수집 ─────────────────────────────────────────────────────────────────
def test_sources_are_accumulated_not_overwritten():
    """
    ⚠️ `= name` 으로 덮어쓰면 다중 출처가 유실된다.
       setdefault(...).append(...) 로 누적해야 한다.
    """
    assert '_cond_sources.setdefault(stock_code, []).append(name)' in SRC, (
        '조건식 출처를 누적하지 않는다 — 덮어쓰면 마지막 것만 남는다'
    )
    assert SRC.count('_cond_sources.setdefault') >= 2, (
        'Bottom / Momentum 두 분기 모두에서 누적해야 한다'
    )


def test_first_seen_time_recorded():
    """condition_match_time — 언제 걸렸는지."""
    assert '_cond_first_seen.setdefault' in SRC


def test_existing_condition_map_untouched():
    """
    기존 stock_to_condition_map 은 전략 태그 결정에 쓰인다.
    출처 추적을 붙이면서 이걸 바꾸면 전략 배정이 달라진다.

    [2026-08-11] 조건식 참조를 idx(배열위치)→seq(고정 ID)로 바꾸면서 루프 변수명도
    idx→cond_seq로 바뀌었다(값의 의미가 바뀌었으니 이름도 맞춰야 함) — 대입 위치·횟수
    (2곳)는 그대로다.
    """
    assert SRC.count('stock_to_condition_map[stock_code] = cond_seq') == 2


# ── 전달 ─────────────────────────────────────────────────────────────────
def test_validated_stocks_carries_attribution():
    """두 분기 모두 validated_stocks 에 출처를 실어야 한다."""
    for key in ('condition_sources', 'primary_condition',
                'condition_match_time'):
        assert SRC.count(f"'{key}': ") >= 2, f'{key} 가 한 분기에만 있다'


def test_helper_exists_and_fails_closed():
    """
    ⚠️ 기록이 없으면 UNKNOWN 이어야 한다. 추정하면 안 된다.
       과거 거래는 기록 자체가 없으므로 영원히 UNKNOWN 이다.
    """
    seg = ast.get_source_segment(SRC, _fn('_condition_attribution')) or ''
    assert "'UNKNOWN'" in seg, '기록 없을 때 UNKNOWN 으로 떨어지지 않는다'
    assert 'validated_stocks' in seg


def test_trade_record_carries_attribution():
    """매수 기록 2곳 모두 entry_context 에 출처가 실려야 한다."""
    assert SRC.count(
        "'entry_context': self._condition_attribution(stock_code)") == 2


def test_attribution_logged_on_buy():
    """[COND_ATTR] 로그 — 사후 분석이 이 줄에 의존한다."""
    assert '[COND_ATTR]' in SRC
    assert 'condition_sources=' in SRC and 'primary_condition=' in SRC


# ── 전략 로직 불변 ───────────────────────────────────────────────────────
def test_no_strategy_logic_changed():
    """
    이번 작업은 데이터 기록만 바꾼다.
    VWAP · Entry · Exit · Risk · Stop · Sizing 계산식을 건드리면 안 된다.
    """
    fn = _fn('execute_buy')
    seg = ast.get_source_segment(SRC, fn) or ''
    # 출처 기록은 dict 대입과 logger 호출뿐이어야 한다
    assert seg.count('_condition_attribution') == 3, (
        'execute_buy 안에서 출처 헬퍼를 3회(기록2+로그1)만 써야 한다'
    )


# ── 런타임 ───────────────────────────────────────────────────────────────
class _Stub:
    def __init__(self, vs):
        self.validated_stocks = vs


def _attr(vs, code):
    ns = {}
    exec(ast.get_source_segment(SRC, _fn('_condition_attribution'))
         .replace('    def ', 'def ', 1)
         .replace('\n        ', '\n    '), ns)
    return ns['_condition_attribution'](_Stub(vs), code)


def test_runtime_multi_source():
    vs = {'005930': {'condition_sources': ['GreatMid', 'Momentum 전략'],
                     'primary_condition': 'GreatMid',
                     'condition_match_time': '2026-08-02T09:01:00'}}
    a = _attr(vs, '005930')
    assert a['condition_sources'] == ['GreatMid', 'Momentum 전략']
    assert a['primary_condition'] == 'GreatMid'


def test_runtime_unknown_when_no_record():
    """기록이 없으면 UNKNOWN — 추정 금지."""
    a = _attr({}, '999999')
    assert a['condition_sources'] == ['UNKNOWN']
    assert a['primary_condition'] == 'UNKNOWN'
    assert a['condition_match_time'] is None
