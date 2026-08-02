"""
tests/unit/test_audit_v2_20260728.py

Production Audit v2.0 (2026-07-28) 에서 수정한 항목의 회귀 테스트.

커버 대상:
  V2-CRIT01 [CRITICAL] 주문 API의 타임아웃 재시도 → 중복 주문 위험
  V2-SF01/02 [CRITICAL] positions_strategy.json 저장/로드 무음 실패
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE))


# ═════════════════════════════════════════════════════════════════════════════
# V2-CRIT01 [CRITICAL] 주문 API 타임아웃 재시도 = 중복 주문 위험
#
# HTTP 타임아웃은 "주문 실패"가 아니라 "응답만 유실"일 수 있다.
# 주문이 이미 접수된 상태에서 재시도하면 동일 종목이 2회 체결된다.
# 하류 방어 없음(DUPLICATE_BLOCK은 API 호출 이전 단계 검사).
# ═════════════════════════════════════════════════════════════════════════════

_ORDER_FUNCS = ('order_buy', 'order_sell')
# 조회 계열은 멱등이라 타임아웃 재시도가 안전 — 그대로 유지되어야 한다
_READONLY_FUNCS = ('get_access_token', 'get_stock_price', 'get_balance')


def _retry_exceptions_for(func_name: str) -> set:
    """kiwoom_api.py에서 해당 함수에 붙은 @retry_on_error의 exceptions 튜플을 파싱."""
    src = (BASE / 'kiwoom_api.py').read_text(encoding='utf-8', errors='ignore')
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != func_name:
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            name = getattr(fn, 'id', None) or getattr(fn, 'attr', None)
            if name != 'retry_on_error':
                continue
            for kw in dec.keywords:
                if kw.arg == 'exceptions':
                    return {
                        getattr(e, 'id', None) or getattr(e, 'attr', None)
                        for e in getattr(kw.value, 'elts', [])
                    }
            return set()   # retry_on_error는 있으나 exceptions 미지정
    return None            # 데코레이터 자체가 없음


@pytest.mark.parametrize('func_name', _ORDER_FUNCS)
def test_crit01_order_apis_do_not_retry_on_timeout(func_name):
    """주문 API는 타임아웃에 재시도하면 안 된다(중복 주문 방지)."""
    excs = _retry_exceptions_for(func_name)
    if excs is None:
        pytest.skip(f'{func_name}에 retry_on_error 데코레이터 없음 — 재시도 위험 자체가 없음')
    assert 'TradingTimeoutError' not in excs, (
        f'{func_name}이 타임아웃에 재시도한다 — 주문이 이미 접수됐을 수 있어 중복 체결 위험'
    )


@pytest.mark.parametrize('func_name', _ORDER_FUNCS)
def test_crit01_connection_error_retry_is_preserved(func_name):
    """
    연결 실패(요청 미전송이 거의 확실)에 대한 재시도는 유지되어야 한다.
    과잉 수정으로 모든 재시도를 없애면 진입 누락이 늘어난다.
    """
    excs = _retry_exceptions_for(func_name)
    if excs is None:
        pytest.skip(f'{func_name}에 retry_on_error 데코레이터 없음')
    assert 'TradingConnectionError' in excs, (
        f'{func_name}의 연결오류 재시도까지 제거됨 — 의도한 수정 범위를 넘어섬'
    )


@pytest.mark.parametrize('func_name', _READONLY_FUNCS)
def test_crit01_readonly_apis_keep_timeout_retry(func_name):
    """조회 계열은 멱등이므로 타임아웃 재시도를 유지해야 한다(회귀 방지)."""
    excs = _retry_exceptions_for(func_name)
    if excs is None:
        pytest.skip(f'{func_name}에 retry_on_error 데코레이터 없음')
    assert 'TradingTimeoutError' in excs, (
        f'{func_name}(조회 계열)의 타임아웃 재시도가 사라짐 — 불필요하게 취약해짐'
    )


def test_crit01_timeout_is_actually_raised_as_trading_timeout_error():
    """
    전제 검증: requests Timeout이 TradingTimeoutError로 변환되는 경로가 살아있어야
    위 테스트들이 의미를 갖는다.
    """
    src = (BASE / 'kiwoom_api.py').read_text(encoding='utf-8', errors='ignore')
    assert 'requests.exceptions.Timeout' in src
    assert 'raise TradingTimeoutError' in src


# ═════════════════════════════════════════════════════════════════════════════
# V2-SF01 / V2-SF02 [CRITICAL] positions_strategy.json 무음 실패
#
# 이 파일은 재시작 시 청산 전략 복원에 쓰인다. 저장/로드 실패를 모르면
# 재시작 후 다른 청산 로직이 적용될 수 있는데 기존엔 except: pass 였다.
# ═════════════════════════════════════════════════════════════════════════════

def _main_src() -> str:
    return (BASE / 'main_auto_trading.py').read_text(encoding='utf-8', errors='ignore')


def test_sf01_positions_strategy_save_failure_is_logged():
    """저장 실패가 로그로 드러나야 한다."""
    src = _main_src()
    assert 'POS_STRATEGY_SAVE_FAIL' in src, 'positions_strategy.json 저장 실패 로그가 없음'


def test_sf02_positions_strategy_load_failure_is_logged():
    """로드 실패가 로그로 드러나야 한다."""
    src = _main_src()
    assert 'POS_STRATEGY_LOAD_FAIL' in src, 'positions_strategy.json 로드 실패 로그가 없음'


def test_sf_positions_strategy_handlers_are_not_bare_pass():
    """
    회귀 방지: positions_strategy.json을 다루는 try 블록의 except가
    단순 pass로 되돌아가지 않았는지 AST로 확인한다.
    """
    src = _main_src()
    tree = ast.parse(src)
    lines = src.splitlines()

    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        try_src = '\n'.join(lines[node.lineno - 1: node.body[-1].end_lineno])
        if 'positions_strategy.json' not in try_src:
            continue
        checked += 1
        for h in node.handlers:
            is_bare_pass = len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
            assert not is_bare_pass, (
                f'positions_strategy.json 처리 except가 무음 pass로 회귀 (L{h.lineno})'
            )

    assert checked >= 2, f'positions_strategy.json try 블록을 {checked}개만 찾음 (기대 2개 이상)'


# ═════════════════════════════════════════════════════════════════════════════
# 구조적 회귀 방지 — 감사에서 확인한 아키텍처 전제
# ═════════════════════════════════════════════════════════════════════════════

def test_update_account_balance_has_no_await_points():
    """
    [P4 근거 고정] update_account_balance()는 내부에 await가 없어 asyncio 단일
    이벤트루프에서 원자적으로 실행된다 — 그래서 create_task로 동시 호출돼도
    Lost Update가 발생하지 않는다.

    만약 이 함수에 await가 추가되면 그 전제가 깨져 race condition이 생기므로,
    그 시점에 이 테스트가 실패해 알려준다.
    """
    tree = ast.parse(_main_src())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'update_account_balance':
            awaits = [
                n for n in ast.walk(node)
                if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
            ]
            assert not awaits, (
                f'update_account_balance()에 await({[a.lineno for a in awaits]})가 추가됨 — '
                f'더 이상 원자적이지 않아 self.current_cash/total_assets에 '
                f'Lost Update가 발생할 수 있다. 동시 실행 가드를 추가할 것.'
            )
            return
    pytest.fail('update_account_balance()를 찾지 못함')
