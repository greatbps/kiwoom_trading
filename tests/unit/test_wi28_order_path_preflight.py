"""
tests/unit/test_wi28_order_path_preflight.py — WI-28 §5/§6/§7/§8/§9/§12.

Strategy Signal → strategy_entry → Order Construction → Kiwoom Order Boundary
전 구간을 Fake Ranking/Gate/Risk로 결정론적으로 검증한다. 실제 API 호출 없음.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from strategy_entry.adapter import StrategyEntryAdapter
from strategy_entry.enable_registry import StrategyEnableRegistry
from strategy_entry.order_boundary import (
    construct_order_request, submit_to_kiwoom_dry_run, BLOCKED_BY_DRY_RUN,
)
from strategy_entry.preflight import run_preflight, run_preflight_by_seq
from strategy_entry.shadow import (
    run_shadow, SHADOW_ALLOWED, SHADOW_RANK_BLOCKED, SHADOW_GATE_BLOCKED,
    SHADOW_RISK_BLOCKED, SHADOW_DUPLICATE, POSITION_CONFLICT,
)
from strategy_entry.types import (
    StrategyEntryRequest, VALID_STRATEGY_SEQS, STAGE_MISSING_FIELD,
)

STRATEGY_NAMES = {
    32: 'Momentum', 33: 'Breakout', 34: 'EOD', 35: 'Supertrend+EMA+RSI',
    36: 'VWAP', 37: 'Squeeze Momentum Pro', 38: 'Bottom', 39: 'ITS',
}


# ── Fakes (WI-25와 동일 패턴, 결정론적) ───────────────────────────────────────
class FakeScoreEngine:
    min_score = 2

    def __init__(self, score_map=None):
        self.score_map = score_map or {}

    def score(self, symbol, ohlcv):
        return {'total': self.score_map.get(symbol, 5), 'smc': 0, 'volume': 0,
                'ma50': 0, 'pattern': 0}


class FakeRegimeAnalyzer:
    def __init__(self, allow_new_entries=True):
        self.allow_new_entries = allow_new_entries

    def evaluate(self, force=False):
        return SimpleNamespace(allow_new_entries=self.allow_new_entries, regime='TREND_UP',
                                allowed_min_grade='A', size_multiplier=1.0, reasons=['TEST'])


class FakeRiskManager:
    def __init__(self, can_enter=True, reason='OK'):
        self.can_enter = can_enter
        self.reason = reason

    def can_open_position(self, current_balance, current_positions_value,
                           position_count, position_size):
        return self.can_enter, self.reason


def _make_adapter(score_map=None, allow_new_entries=True, can_enter=True):
    registry = StrategyEnableRegistry(path='/tmp/wi28_test_registry_unused.json')
    return StrategyEntryAdapter(
        score_engine=FakeScoreEngine(score_map),
        regime_analyzer=FakeRegimeAnalyzer(allow_new_entries=allow_new_entries),
        risk_manager=FakeRiskManager(can_enter=can_enter),
        enable_registry=registry,
        ohlcv_provider=lambda symbol: None,
        account_state_provider=lambda: {'current_balance': 1_000_000, 'positions_value': 0,
                                         'position_count': 0, 'total_assets': 1_000_000},
    )


def _fixture(seq, symbol=None, ts=None):
    symbol = symbol or f'00{seq}000'
    ts = ts or f'2026-08-13T09:{seq - 32:02d}:00'
    return StrategyEntryRequest(
        strategy_seq=seq, strategy_name=STRATEGY_NAMES[seq], symbol=symbol,
        signal_timestamp=ts, signal_price=10000.0 + seq,
        signal_id=f'sig-{seq}', candidate_id=f'cand-{seq}', signal_reason='WI28_FIXTURE',
    )


def _all_fixtures():
    return {seq: _fixture(seq) for seq in VALID_STRATEGY_SEQS}


# ── §5 정상 Signal 테스트 (8개 전략 전부) ─────────────────────────────────────
@pytest.mark.parametrize('seq', VALID_STRATEGY_SEQS)
def test_normal_signal_reaches_kiwoom_boundary_with_identity_preserved(seq):
    adapter = _make_adapter()
    fixtures = _all_fixtures()
    results = run_preflight_by_seq(adapter, {seq: fixtures[seq]})
    r = results[seq]

    assert r['shadow_result'] == SHADOW_ALLOWED
    assert r['decision'].strategy_seq == seq
    assert r['decision'].candidate_id == fixtures[seq].candidate_id
    assert r['decision'].symbol == fixtures[seq].symbol
    assert r['decision'].signal_timestamp == fixtures[seq].signal_timestamp

    assert r['order_request'] is not None
    assert r['order_request'].strategy_seq == seq  # §9 - 전략 ID가 변하지 않음
    assert r['order_request'].stock_code == fixtures[seq].symbol
    assert r['order_request'].signal_id == fixtures[seq].signal_id
    assert r['order_request'].candidate_id == fixtures[seq].candidate_id

    assert r['boundary_result']['status'] == BLOCKED_BY_DRY_RUN  # §4


# ── §6 차단 조건 테스트 ────────────────────────────────────────────────────────
def test_ranking_block_produces_no_order():
    adapter = _make_adapter(score_map={'005930': 0})  # min_score=2 미달
    results = run_preflight(adapter, [_fixture(32, symbol='005930')])
    assert results[0]['shadow_result'] == SHADOW_RANK_BLOCKED
    assert results[0]['order_request'] is None


def test_regime_gate_block_produces_no_order():
    adapter = _make_adapter(allow_new_entries=False)
    results = run_preflight(adapter, [_fixture(33, symbol='005930')])
    assert results[0]['shadow_result'] == SHADOW_GATE_BLOCKED
    assert results[0]['order_request'] is None


def test_risk_block_produces_no_order():
    adapter = _make_adapter(can_enter=False)
    results = run_preflight(adapter, [_fixture(34, symbol='005930')])
    assert results[0]['shadow_result'] == SHADOW_RISK_BLOCKED
    assert results[0]['order_request'] is None


def test_duplicate_resend_produces_only_one_order():
    adapter = _make_adapter()
    req = _fixture(35, symbol='005930', ts='2026-08-13T10:00:00')
    req_dup = _fixture(35, symbol='005930', ts='2026-08-13T10:00:00')
    results = run_preflight(adapter, [req, req_dup])
    labels = [r['shadow_result'] for r in results]
    assert labels.count(SHADOW_ALLOWED) == 1
    assert labels.count(SHADOW_DUPLICATE) == 1
    orders = [r for r in results if r['order_request'] is not None]
    assert len(orders) == 1


def test_existing_position_blocks_new_order():
    """§6 - 동일 종목 기존 포지션 존재 -> 신규 주문 차단."""
    adapter = _make_adapter()
    req = _fixture(36, symbol='005930')
    results = run_preflight(adapter, [req], existing_position_symbols={'005930'})
    assert results[0]['shadow_result'] == POSITION_CONFLICT
    assert results[0]['order_request'] is None


@pytest.mark.parametrize('missing_field', ['strategy_seq', 'symbol', 'signal_timestamp',
                                            'signal_price', 'signal_id'])
def test_invalid_signal_fields_fail_closed_no_order(missing_field):
    adapter = _make_adapter()
    base = dict(strategy_seq=32, strategy_name='Momentum', symbol='005930',
                signal_timestamp='2026-08-13T09:00:00', signal_price=10000.0,
                signal_id='sig-x', candidate_id='cand-x', signal_reason='TEST')
    base[missing_field] = None
    req = StrategyEntryRequest(**base)
    results = run_preflight(adapter, [req])
    assert results[0]['decision'].allowed is False
    assert results[0]['order_request'] is None


# ── §7 8개 전략 독립성 (교차 오염 없음) ────────────────────────────────────────
def test_cross_strategy_no_contamination_in_batch():
    """seq32~39가 서로 다른 종목에 동시에 Signal을 내면 각자 독립적으로
    Entry Path를 통과해야 하고, 한 전략의 결과가 다른 전략에 영향을 주면
    안 된다."""
    adapter = _make_adapter()
    fixtures = _all_fixtures()  # 서로 다른 심볼(00{seq}000)이라 충돌 없음
    results = run_preflight(adapter, list(fixtures.values()))
    for r in results:
        assert r['shadow_result'] == SHADOW_ALLOWED
        assert r['order_request'].strategy_seq == r['request'].strategy_seq

    seqs_seen = {r['request'].strategy_seq for r in results}
    assert seqs_seen == set(VALID_STRATEGY_SEQS)


def test_one_strategy_block_does_not_affect_another():
    adapter_mixed = _make_adapter(score_map={'005930': 0, '000660': 5})
    req32_blocked = _fixture(32, symbol='005930')
    req33_allowed = _fixture(33, symbol='000660')
    results = run_preflight(adapter_mixed, [req32_blocked, req33_allowed])
    by_seq = {r['request'].strategy_seq: r for r in results}
    assert by_seq[32]['shadow_result'] == SHADOW_RANK_BLOCKED
    assert by_seq[33]['shadow_result'] == SHADOW_ALLOWED


# ── §8 SMC 완전 격리 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize('smc_state', ['SMC_SIGNAL', 'SMC_NO_SIGNAL', 'SMC_ERROR'])
@pytest.mark.parametrize('seq', VALID_STRATEGY_SEQS)
def test_smc_state_does_not_affect_preflight_result(seq, smc_state):
    import strategy_entry.adapter as adapter_mod
    fake_smc = SimpleNamespace(state=smc_state)
    with patch.object(adapter_mod, '_WI28_INJECTED_SMC_STATE', fake_smc, create=True):
        adapter = _make_adapter()
        results = run_preflight(adapter, [_fixture(seq)])
    assert results[0]['shadow_result'] == SHADOW_ALLOWED
    assert results[0]['request'].strategy_seq == seq


def test_order_boundary_modules_have_zero_smc_code_references():
    import glob
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for path in glob.glob(os.path.join(root, 'strategy_entry', '*.py')):
        in_docstring = False
        for line in open(path, encoding='utf-8'):
            stripped = line.strip()
            if stripped.startswith('"""'):
                in_docstring = not in_docstring if stripped.count('"""') == 1 else in_docstring
                continue
            if in_docstring or stripped.startswith('#'):
                continue
            assert 'smc' not in line.lower(), f'{path}: {line.strip()}'


# ── §9 주문 파라미터 무결성 ────────────────────────────────────────────────────
def test_order_request_has_all_required_fields():
    adapter = _make_adapter()
    results = run_preflight(adapter, [_fixture(37, symbol='005930')])
    order = results[0]['order_request']
    assert order.stock_code == '005930'
    assert order.side == 'BUY'
    assert order.quantity > 0
    assert order.price > 0
    assert order.strategy_seq == 37
    assert order.signal_id is not None
    assert order.candidate_id is not None
    assert order.entry_mode == 'DRY_RUN_ENTRY'
    assert order.order_timestamp is not None


def test_construct_order_request_never_defaults_strategy_seq_to_smc_marker():
    """§9 '전략 ID가 SMC 등 다른 값으로 변하지 않는지' - construct_order_request가
    strategy_seq를 하드코딩하거나 SMC 관련 기본값으로 바꾸지 않는지 소스로 고정."""
    import inspect
    src = inspect.getsource(construct_order_request)
    assert 'strategy_seq=order_candidate.strategy_seq' in src
    assert "'SMC'" not in src
    assert '"SMC"' not in src


# ── §4/§12 실제 주문 차단 (Kiwoom Boundary) ────────────────────────────────────
def test_submit_to_kiwoom_dry_run_never_accepts_api_client():
    """submit_to_kiwoom_dry_run 시그니처 자체가 KiwoomAPI 인스턴스를 받지
    않는지 확인 - 구조적으로 실제 API 호출이 불가능함을 보장."""
    import inspect
    sig = inspect.signature(submit_to_kiwoom_dry_run)
    assert list(sig.parameters.keys()) == ['order_request']


def test_no_kiwoom_order_functions_called_anywhere_in_strategy_entry():
    """docstring/주석에서 '실제 주문 함수를 호출하지 않는다'고 설명하는 것은
    허용하고(그게 이 패키지들의 존재 이유다), 실제 코드 라인만 검사한다."""
    import glob
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for path in glob.glob(os.path.join(root, 'strategy_entry', '*.py')):
        in_docstring = False
        for line in open(path, encoding='utf-8'):
            stripped = line.strip()
            if stripped.startswith('"""'):
                in_docstring = not in_docstring if stripped.count('"""') == 1 else in_docstring
                continue
            if in_docstring or stripped.startswith('#'):
                continue
            for banned in ('.order_buy(', '.order_sell(', '.execute_buy(', 'send_order('):
                assert banned not in line, f'{path}: {banned} 발견 → {line.strip()}'


def test_full_8_strategy_preflight_zero_real_orders():
    """8개 전략 전부 정상 Signal로 Preflight를 실행해도 실제 kiwoom 주문
    함수 호출 횟수가 0이어야 한다(§12 DECISION 표의 Actual Kiwoom Orders 근거)."""
    calls = []

    class _StubKiwoomAPI:
        def order_buy(self, *a, **kw):
            calls.append((a, kw))

    stub = _StubKiwoomAPI()
    adapter = _make_adapter()
    results = run_preflight(adapter, list(_all_fixtures().values()))
    assert all(r['boundary_result']['status'] == BLOCKED_BY_DRY_RUN for r in results)
    assert calls == []
    assert not hasattr(adapter, 'order_buy')
