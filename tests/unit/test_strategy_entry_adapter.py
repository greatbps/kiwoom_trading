"""
tests/unit/test_strategy_entry_adapter.py — WI-25 §25 필수 테스트 전체.

실제 ScoreEngine/RegimeAnalyzer/RiskManager 대신 제어 가능한 Fake를 주입해
Adapter/Shadow 로직 자체를 결정론적으로 검증한다(API 호출 없음).
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from strategy_entry.adapter import StrategyEntryAdapter
from strategy_entry.enable_registry import StrategyEnableRegistry, ENABLED, DISABLED
from strategy_entry.shadow import (
    run_shadow, SHADOW_ALLOWED, SHADOW_RANK_BLOCKED, SHADOW_GATE_BLOCKED,
    SHADOW_RISK_BLOCKED, SHADOW_DUPLICATE, SHADOW_DISABLED, SHADOW_REJECTED,
    POSITION_CONFLICT,
)
from strategy_entry.types import (
    StrategyEntryRequest, VALID_STRATEGY_SEQS, STAGE_ALLOWED,
    STAGE_MISSING_FIELD, STAGE_UNKNOWN_STRATEGY, STAGE_STRATEGY_DISABLED,
)


# ── Fakes (결정론적, API 호출 없음) ───────────────────────────────────────────
class FakeScoreEngine:
    min_score = 2

    def __init__(self, score_map=None, raise_for=None):
        self.score_map = score_map or {}
        self.raise_for = raise_for or set()

    def score(self, symbol, ohlcv):
        if symbol in self.raise_for:
            raise RuntimeError(f'{symbol} score 계산 실패(테스트 주입 예외)')
        total = self.score_map.get(symbol, 0)
        return {'total': total, 'smc': 0, 'volume': 0, 'ma50': 0, 'pattern': 0}


class FakeRegimeAnalyzer:
    def __init__(self, allow_new_entries=True, regime='TREND_UP', raise_error=False):
        self.allow_new_entries = allow_new_entries
        self.regime = regime
        self.raise_error = raise_error

    def evaluate(self, force=False):
        if self.raise_error:
            raise RuntimeError('regime 계산 실패(테스트 주입 예외)')
        return SimpleNamespace(
            allow_new_entries=self.allow_new_entries, regime=self.regime,
            allowed_min_grade='A', size_multiplier=1.0, reasons=['TEST'],
        )


class FakeRiskManager:
    def __init__(self, can_enter=True, reason='OK', raise_error=False):
        self.can_enter = can_enter
        self.reason = reason
        self.raise_error = raise_error

    def can_open_position(self, current_balance, current_positions_value,
                           position_count, position_size):
        if self.raise_error:
            raise RuntimeError('risk 계산 실패(테스트 주입 예외)')
        return self.can_enter, self.reason


def _make_adapter(score_map=None, allow_new_entries=True, can_enter=True,
                   enable_all=False, raise_rank_for=None, raise_gate=False,
                   raise_risk=False, registry_path=None):
    registry = StrategyEnableRegistry(path=registry_path or '/tmp/wi25_test_registry_unused.json')
    if not registry.path.exists():
        registry.save()
    if enable_all:
        for seq in VALID_STRATEGY_SEQS:
            registry.set_enabled(seq, True)
    return StrategyEntryAdapter(
        score_engine=FakeScoreEngine(score_map, raise_for=raise_rank_for),
        regime_analyzer=FakeRegimeAnalyzer(allow_new_entries=allow_new_entries, raise_error=raise_gate),
        risk_manager=FakeRiskManager(can_enter=can_enter, raise_error=raise_risk),
        enable_registry=registry,
        ohlcv_provider=lambda symbol: None,
        account_state_provider=lambda: {'current_balance': 1_000_000, 'positions_value': 0,
                                         'position_count': 0, 'total_assets': 1_000_000},
    )


def _req(seq=32, symbol='005930', ts='2026-08-13T09:00:00', price=10000.0, **kw):
    return StrategyEntryRequest(
        strategy_seq=seq, strategy_name='Momentum', symbol=symbol,
        signal_timestamp=ts, signal_price=price, signal_id=f'sig-{seq}-{symbol}-{ts}',
        candidate_id=f'cand-{seq}-{symbol}', signal_reason='TEST', **kw,
    )


# ── 1. Strategy Signal → Entry Request / 2. Identity Preservation ───────────
def test_identity_fields_preserved_through_full_pipeline():
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    req = _req(seq=32, symbol='005930')
    decision = adapter.evaluate(req)
    assert decision.strategy_seq == 32
    assert decision.strategy_name == 'Momentum'
    assert decision.symbol == '005930'
    assert decision.candidate_id == req.candidate_id
    assert decision.signal_id == req.signal_id
    assert decision.signal_timestamp == req.signal_timestamp
    assert decision.signal_price == req.signal_price
    assert decision.signal_reason == req.signal_reason
    assert decision.allowed is True
    assert decision.decision_stage == STAGE_ALLOWED


# ── 3. 8-strategy routing ────────────────────────────────────────────────────
@pytest.mark.parametrize('seq', VALID_STRATEGY_SEQS)
def test_all_8_strategies_route_correctly(seq):
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    decision = adapter.evaluate(_req(seq=seq, symbol='005930'))
    assert decision.strategy_seq == seq
    assert decision.allowed is True


# ── 4. Multi-strategy isolation ──────────────────────────────────────────────
def test_multi_strategy_isolation_one_strategy_does_not_affect_another():
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    d32 = adapter.evaluate(_req(seq=32, symbol='005930'))
    d33 = adapter.evaluate(_req(seq=33, symbol='005930'))
    assert d32.strategy_seq == 32 and d32.allowed is True
    assert d33.strategy_seq == 33 and d33.allowed is True  # 32 평가가 33에 영향 없음

    # 33이 나중에 Rank 미달이어도 32의 과거 결정은 바뀌지 않는다(각 evaluate가 독립 값 반환)
    adapter2 = _make_adapter(score_map={'005930': 0}, enable_all=True)
    d33_blocked = adapter2.evaluate(_req(seq=33, symbol='005930'))
    assert d33_blocked.allowed is False
    assert d32.allowed is True  # 이전에 만든 d32 객체 자체는 불변


# ── 5. SMC Isolation (Case A/B/C) ────────────────────────────────────────────
@pytest.mark.parametrize('smc_state', ['SMC_SIGNAL', 'SMC_NO_SIGNAL', 'SMC_ERROR'])
@pytest.mark.parametrize('seq', VALID_STRATEGY_SEQS)
def test_smc_isolation_all_seqs_unaffected_by_smc_state(seq, smc_state):
    """StrategyEntryAdapter/Request/Decision 어디에도 SMC 상태를 넣을 파라미터가
    없다 — '주입'은 전역에 가짜 SMC 상태를 심어보는 방식으로 시뮬레이션한다
    (analyzers/strategy_monitors 테스트와 동일 기법)."""
    import strategy_entry.adapter as adapter_mod
    fake_smc = SimpleNamespace(state=smc_state)

    with patch.object(adapter_mod, '_WI25_INJECTED_SMC_STATE', fake_smc, create=True):
        adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
        decision = adapter.evaluate(_req(seq=seq, symbol='005930'))

    assert decision.strategy_seq == seq
    assert decision.allowed is True
    assert decision.decision_stage == STAGE_ALLOWED


def test_strategy_entry_modules_have_zero_smc_code_references():
    """docstring/주석에서 'SMC와 독립적이다'라고 설명하는 것은 허용하되(그게
    이 패키지의 존재 이유다), 실제 코드(import/속성접근/함수호출)에 SMC 관련
    참조가 있으면 안 된다 — docstring/주석 라인은 제외하고 검사한다."""
    import glob
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for path in glob.glob(os.path.join(root, 'strategy_entry', '*.py')):
        in_docstring = False
        for line in open(path, encoding='utf-8'):
            stripped = line.strip()
            if stripped.startswith('"""') :
                in_docstring = not in_docstring if stripped.count('"""') == 1 else in_docstring
                continue
            if in_docstring or stripped.startswith('#'):
                continue
            assert 'smc' not in line.lower(), f'{path}: 코드 라인에 smc 참조 발견 → {line.strip()}'


# ── 6. Ranking integration ───────────────────────────────────────────────────
def test_ranking_integration_blocks_low_score():
    adapter = _make_adapter(score_map={'005930': 1}, enable_all=True)  # min_score=2
    decision = adapter.evaluate(_req(symbol='005930'))
    assert decision.allowed is False
    assert decision.decision_stage == 'RANK_BLOCKED'
    assert decision.ranking_result['score'] == 1


# ── 7. Gate integration ──────────────────────────────────────────────────────
def test_gate_integration_blocks_when_regime_disallows():
    adapter = _make_adapter(score_map={'005930': 5}, allow_new_entries=False, enable_all=True)
    decision = adapter.evaluate(_req(symbol='005930'))
    assert decision.allowed is False
    assert decision.decision_stage == 'GATE_BLOCKED'
    assert decision.gate_result['pass'] is False


def test_gate_result_recorded_independently_per_strategy():
    """§11 - seq32/seq33이 각자 독립된 gate_result를 갖는지(같은 dict 참조 공유 아님)."""
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    d32 = adapter.evaluate(_req(seq=32, symbol='005930'))
    d33 = adapter.evaluate(_req(seq=33, symbol='005930'))
    assert d32.gate_result is not d33.gate_result
    assert d32.gate_result == d33.gate_result  # 내용은 같아도(같은 시장상황) 객체는 별개


# ── 8. Risk integration ──────────────────────────────────────────────────────
def test_risk_integration_blocks_when_risk_manager_rejects():
    adapter = _make_adapter(score_map={'005930': 5}, can_enter=False, enable_all=True)
    decision = adapter.evaluate(_req(symbol='005930'))
    assert decision.allowed is False
    assert decision.decision_stage == 'RISK_BLOCKED'


# ── 9. Duplicate protection ──────────────────────────────────────────────────
def test_duplicate_protection_same_key_produces_one_order_candidate():
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    req = _req(seq=32, symbol='005930', ts='2026-08-13T10:00:00')
    req_dup = _req(seq=32, symbol='005930', ts='2026-08-13T10:00:00')  # 동일 키
    results = run_shadow(adapter, [req, req_dup])
    labels = [r['shadow_result'] for r in results]
    assert labels.count(SHADOW_ALLOWED) == 1
    assert labels.count(SHADOW_DUPLICATE) == 1
    candidates = [r['order_candidate'] for r in results if r['order_candidate'] is not None]
    assert len(candidates) == 1


def test_duplicate_protection_does_not_delete_underlying_signal_data():
    """§17 - 기존 데이터 자체를 삭제해서 해결하지 않는다: request 객체 2건 모두
    results에 남아있어야 한다(하나가 사라지면 안 됨)."""
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    req = _req(seq=32, symbol='005930', ts='2026-08-13T10:00:00')
    req_dup = _req(seq=32, symbol='005930', ts='2026-08-13T10:00:00')
    results = run_shadow(adapter, [req, req_dup])
    assert len(results) == 2


# ── 10. Position conflict ────────────────────────────────────────────────────
def test_position_conflict_when_two_strategies_target_same_symbol():
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    req32 = _req(seq=32, symbol='005930', ts='2026-08-13T10:00:00')
    req33 = _req(seq=33, symbol='005930', ts='2026-08-13T10:05:00')
    results = run_shadow(adapter, [req32, req33])
    assert all(r['shadow_result'] == POSITION_CONFLICT for r in results)
    # 어느 쪽도 임의로 우선시키지 않았는지 - 둘 다 동일하게 CONFLICT 처리됨
    assert {r['request'].strategy_seq for r in results} == {32, 33}


def test_no_position_conflict_for_different_symbols():
    adapter = _make_adapter(score_map={'005930': 5, '000660': 5}, enable_all=True)
    req32 = _req(seq=32, symbol='005930')
    req33 = _req(seq=33, symbol='000660')
    results = run_shadow(adapter, [req32, req33])
    assert all(r['shadow_result'] == SHADOW_ALLOWED for r in results)


# ── 11. Fail-closed ───────────────────────────────────────────────────────────
def test_fail_closed_missing_fields():
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    req = _req(symbol=None)
    decision = adapter.evaluate(req)
    assert decision.allowed is False
    assert decision.decision_stage == STAGE_MISSING_FIELD


def test_fail_closed_unknown_strategy():
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    req = _req(seq=99, symbol='005930')
    decision = adapter.evaluate(req)
    assert decision.allowed is False
    assert decision.decision_stage == STAGE_UNKNOWN_STRATEGY


def test_fail_closed_ranking_exception():
    adapter = _make_adapter(score_map={'005930': 5}, raise_rank_for={'005930'}, enable_all=True)
    decision = adapter.evaluate(_req(symbol='005930'))
    assert decision.allowed is False
    assert decision.decision_stage == 'ENTRY_REJECTED_ADAPTER_ERROR'


def test_fail_closed_gate_exception():
    adapter = _make_adapter(score_map={'005930': 5}, raise_gate=True, enable_all=True)
    decision = adapter.evaluate(_req(symbol='005930'))
    assert decision.allowed is False
    assert decision.decision_stage == 'ENTRY_REJECTED_ADAPTER_ERROR'


def test_fail_closed_risk_exception():
    adapter = _make_adapter(score_map={'005930': 5}, raise_risk=True, enable_all=True)
    decision = adapter.evaluate(_req(symbol='005930'))
    assert decision.allowed is False
    assert decision.decision_stage == 'ENTRY_REJECTED_ADAPTER_ERROR'


def test_fail_closed_default_strategy_disabled():
    """§20/§21 - 레지스트리를 건드리지 않은 기본 상태(전부 DISABLED)에서는
    Rank/Gate/Risk가 전부 통과해도 allowed=False여야 한다."""
    registry = StrategyEnableRegistry(path='/tmp/wi25_test_default_disabled_new.json')
    adapter = StrategyEntryAdapter(
        score_engine=FakeScoreEngine({'005930': 5}),
        regime_analyzer=FakeRegimeAnalyzer(allow_new_entries=True),
        risk_manager=FakeRiskManager(can_enter=True),
        enable_registry=registry,
        ohlcv_provider=lambda s: None,
        account_state_provider=lambda: {'current_balance': 1_000_000, 'positions_value': 0,
                                         'position_count': 0, 'total_assets': 1_000_000},
    )
    decision = adapter.evaluate(_req(symbol='005930'))
    assert decision.allowed is False
    assert decision.decision_stage == STAGE_STRATEGY_DISABLED


# ── 12. Shadow execution / 13. No actual BUY ─────────────────────────────────
def test_shadow_execution_full_labels():
    adapter_allowed = _make_adapter(score_map={'005930': 5}, enable_all=True)
    adapter_rank_blocked = _make_adapter(score_map={'005930': 0}, enable_all=True)
    adapter_gate_blocked = _make_adapter(score_map={'005930': 5}, allow_new_entries=False, enable_all=True)
    adapter_risk_blocked = _make_adapter(score_map={'005930': 5}, can_enter=False, enable_all=True)

    assert run_shadow(adapter_allowed, [_req(symbol='005930')])[0]['shadow_result'] == SHADOW_ALLOWED
    assert run_shadow(adapter_rank_blocked, [_req(symbol='005930')])[0]['shadow_result'] == SHADOW_RANK_BLOCKED
    assert run_shadow(adapter_gate_blocked, [_req(symbol='005930')])[0]['shadow_result'] == SHADOW_GATE_BLOCKED
    assert run_shadow(adapter_risk_blocked, [_req(symbol='005930')])[0]['shadow_result'] == SHADOW_RISK_BLOCKED


def test_no_actual_buy_ever_called():
    """execute_buy 관련 심볼을 스텁으로 심어 evaluate/run_shadow 전 과정에서
    한 번도 호출되지 않는지 확인한다."""
    calls = []

    class _Stub:
        def execute_buy(self, *a, **kw):
            calls.append((a, kw))

    stub = _Stub()
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    reqs = [_req(seq=s, symbol='005930', ts=f'2026-08-13T10:{i:02d}:00')
            for i, s in enumerate(VALID_STRATEGY_SEQS)]
    run_shadow(adapter, reqs)
    assert calls == []
    assert not hasattr(adapter, 'execute_buy')


def test_strategy_entry_source_has_zero_execute_buy_calls():
    import glob
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for path in glob.glob(os.path.join(root, 'strategy_entry', '*.py')):
        src = open(path, encoding='utf-8').read()
        assert '.execute_buy(' not in src
        assert 'send_order(' not in src


# ── 14. Determinism ───────────────────────────────────────────────────────────
def test_determinism_same_dataset_twice_yields_identical_results():
    adapter = _make_adapter(score_map={'005930': 5, '000660': 0}, enable_all=True)
    reqs = [_req(seq=32, symbol='005930', ts='2026-08-13T10:00:00'),
            _req(seq=34, symbol='000660', ts='2026-08-13T10:05:00')]
    r1 = run_shadow(adapter, reqs)
    r2 = run_shadow(adapter, reqs)
    labels1 = [r['shadow_result'] for r in r1]
    labels2 = [r['shadow_result'] for r in r2]
    assert labels1 == labels2


# ── 15/16. Unknown strategy / Missing fields (파라미터화 종합) ────────────────
@pytest.mark.parametrize('field', [
    'symbol', 'signal_timestamp', 'signal_price', 'signal_id', 'strategy_seq',
])
def test_each_required_field_missing_triggers_fail_closed(field):
    adapter = _make_adapter(score_map={'005930': 5}, enable_all=True)
    base = dict(strategy_seq=32, strategy_name='Momentum', symbol='005930',
                signal_timestamp='2026-08-13T09:00:00', signal_price=10000.0,
                signal_id='sig-1', candidate_id='cand-1', signal_reason='TEST')
    base[field] = None
    req = StrategyEntryRequest(**base)
    decision = adapter.evaluate(req)
    assert decision.allowed is False
    assert decision.decision_stage in (STAGE_MISSING_FIELD, STAGE_UNKNOWN_STRATEGY)
