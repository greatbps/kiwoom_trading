"""Iteration 7-3 — D+2 정산 예수금 기반 총자산 계산 검증.

main_auto_trading 을 import 하면 실거래 초기화가 딸려오므로
소스에서 `_settled_cash` 만 AST 로 떼어내 독립 실행한다.
"""
import ast
import logging
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, 'main_auto_trading.py')


def _extract(func_name):
    with open(SRC, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            mod = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(mod)
            ns = {'datetime': __import__('datetime').datetime,
                  'logger': logging.getLogger('test_settled_cash')}
            exec(compile(mod, SRC, 'exec'), ns)
            return ns[func_name]
    raise AssertionError(f'{func_name} 를 찾지 못했다')


settled_cash = _extract('_settled_cash')


class _Obj:
    """self 대역. _deposit_obs_date 만 쓰인다."""


def call(balance_info, pymn, obs_date='RESET'):
    o = _Obj()
    if obs_date != 'RESET':
        o._deposit_obs_date = obs_date
    return settled_cash(o, balance_info, pymn)


# ── 정상 경로 ────────────────────────────────────────────────────────────
def test_uses_d2_entra_when_present():
    b = {'entr': '000000005060450', 'pymn_alow_amt': '000000000542250',
         'd2_entra': '000000005060450'}
    assert call(b, '000000000542250') == 5060450.0


def test_sell_pending_case_20260526():
    """매도 미수령 구간 — 출금가능금 기준이면 542,250, D+2 기준이면 5,060,450."""
    b = {'entr': '000000005060450', 'pymn_alow_amt': '000000000542250',
         'd2_entra': '000000005060450'}
    settled = call(b, '000000000542250')
    assert settled + 0 == 5060450.0          # 평가액 0 (전량 매도)
    assert settled - 542250.0 == 4518200.0   # 브로커 실측 오차와 일치


def test_buy_pending_case_20260624():
    """매수 미차감 구간 — 출금가능금 기준이면 +2,065,358 과대."""
    b = {'entr': '000000004768719', 'pymn_alow_amt': '000000004768719',
         'd2_entra': '000000002683409'}
    settled = call(b, '000000004768719')
    assert settled + 1860000.0 == 4543409.0
    assert 4768719.0 + 1860000.0 - 4543409.0 == 2085310.0


def test_zero_account_is_allowed():
    """빈 계좌(0원)는 정상값이다. 폴백으로 새지 않는다."""
    b = {'pymn_alow_amt': '000000000000000', 'd2_entra': '000000000000000'}
    assert call(b, '000000000000000') == 0.0


# ── 폴백 (조용한 폴백 금지) ──────────────────────────────────────────────
@pytest.mark.parametrize('raw', [None, '', 'abc', '   '])
def test_fallback_when_d2_unusable(raw, caplog):
    b = {'pymn_alow_amt': '000000001857799'}
    if raw is not None:
        b['d2_entra'] = raw
    with caplog.at_level(logging.WARNING, logger='test_settled_cash'):
        assert call(b, '000000001857799') == 1857799.0
    assert any('[EQUITY_D2]' in r.message or '[EQUITY_D2]' in r.getMessage()
               for r in caplog.records), 'WARN 로그 없이 조용히 폴백했다'


def test_fallback_when_d2_zero_but_cash_positive(caplog):
    """d2_entra=0 인데 출금가능금이 양수면 모순 → 폴백해야 한다."""
    b = {'pymn_alow_amt': '000000001857799', 'd2_entra': '000000000000000'}
    with caplog.at_level(logging.WARNING, logger='test_settled_cash'):
        assert call(b, '000000001857799') == 1857799.0
    assert any('[EQUITY_D2]' in r.getMessage() for r in caplog.records)


# ── 관측 로그 ────────────────────────────────────────────────────────────
def test_observation_log_emitted_once_per_day(caplog):
    b = {'entr': '1', 'pymn_alow_amt': '1', 'd1_entra': '1', 'd2_entra': '1'}
    with caplog.at_level(logging.INFO, logger='test_settled_cash'):
        o = _Obj()
        settled_cash(o, b, '1')
        settled_cash(o, b, '1')
    obs = [r for r in caplog.records if 'entr=' in r.getMessage()]
    assert len(obs) == 1, f'하루 1회여야 하는데 {len(obs)}회'


# ── 운영 코드 배선 확인 ──────────────────────────────────────────────────
def test_total_assets_uses_settled_cash():
    src = open(SRC, encoding='utf-8').read()
    assert src.count('self.total_assets = self.settled_cash + self.positions_value') == 2
    assert 'self.total_assets = self.withdrawable_cash + self.positions_value' not in src
    assert src.count('self.settled_cash = self._settled_cash(') == 2


def test_withdrawable_cash_still_recorded():
    """출금가능금은 스냅샷 기록용으로 남아 있어야 한다 (기존 시계열 비교 가능)."""
    src = open(SRC, encoding='utf-8').read()
    assert 'deposit=int(self.withdrawable_cash)' in src
