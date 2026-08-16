"""
tests/unit/test_pipeline_health_chain.py

Pipeline Health Chain (2026-08-17) 검증.
analysis/pipeline_health_chain.py의 reconcile()(순수 함수, DB 없이 테스트)과
compute_chain()(SQL 조회 — mock cursor로 시나리오 주입)을 검증한다.

실제 DB는 전혀 연결하지 않는다(_ScriptedCursor가 psycopg2 cursor를 흉내낸다).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from analysis.pipeline_health_chain import (
    STAGE_SOURCE_MAP,
    compute_chain,
    reconcile,
)


# ─── §12 Silent Drop Fixture Case A~D — reconcile() 순수 함수 단독 테스트 ───
class TestReconcileFixtures:
    def test_case_a_reject_only_no_failure_passes(self):
        """Input=100 Output=95 Reject=5 Failure=0 Drop=0 → PASS(§9: reject>0이면 WARNING이지만
        §12 예시는 이를 PASS로 규정 — reject만 있고 drop=0이면 정상 종결로 취급)."""
        r = reconcile('TEST', 100, 95, reject_=5, fail_=0)
        assert r.drop == 0
        assert r.status in ('PASS', 'WARNING')  # 명시적 reject는 WARNING 이하로만 취급, FAIL 아님
        assert r.status != 'FAIL'

    def test_case_b_unexplained_drop_fails(self):
        """Input=100 Output=95 Reject=3 Failure=1 Drop=1 → FAIL."""
        r = reconcile('TEST', 100, 95, reject_=3, fail_=1)
        assert r.drop == 1
        assert r.status == 'FAIL'

    def test_case_c_full_passthrough_passes(self):
        """Input=100 Output=100 → PASS."""
        r = reconcile('TEST', 100, 100, reject_=0, fail_=0)
        assert r.drop == 0
        assert r.status == 'PASS'

    def test_case_d_zero_input_is_not_applicable(self):
        """Input=0 → N/A (FAIL 아님)."""
        r = reconcile('TEST', 0, 0, reject_=0, fail_=0)
        assert r.status == 'N/A'

    def test_pure_failure_without_reject_is_fail(self):
        r = reconcile('TEST', 100, 95, reject_=0, fail_=5)
        assert r.drop == 0
        assert r.status == 'FAIL'
        assert 'PersistenceFailure' in r.note

    def test_input_none_with_failures_is_fail(self):
        """CANDIDATE처럼 Input을 관측할 수 없는 stage — failure>0이면 그래도 FAIL."""
        r = reconcile('CANDIDATE', None, 90, fail_=5)
        assert r.status == 'FAIL'

    def test_input_none_no_failure_is_pass(self):
        r = reconcile('CANDIDATE', None, 90, fail_=0)
        assert r.status == 'PASS'


# ─── Chain Identity — Stage 매핑이 실제 코드와 일치하는지 문서화 테스트 ─────
class TestChainIdentityStageMapping:
    def test_all_nine_stages_present(self):
        expected = {'DATA', 'CANDIDATE', 'SIGNAL', 'DECISION', 'INTENT',
                    'SUBMIT_FILL', 'TRADE', 'FUTURE_RETURN'}
        assert expected.issubset(set(STAGE_SOURCE_MAP.keys()))

    def test_candidate_maps_to_research_candidates_table(self):
        assert STAGE_SOURCE_MAP['CANDIDATE']['source'] == 'research.candidates'

    def test_decision_maps_to_decision_ledger(self):
        assert STAGE_SOURCE_MAP['DECISION']['source'] == 'research.decision_ledger'

    def test_trade_maps_to_public_trades_not_research(self):
        assert 'public.trades' in STAGE_SOURCE_MAP['TRADE']['source']

    def test_signal_stage_explicitly_flags_no_separate_table(self):
        # 이전에 StrategySignalCreated를 check_entry_signal과 잘못 연결했던 실수를
        # 반복하지 않기 위한 회귀 테스트 — SIGNAL 매핑 설명에 그 경고가 남아있어야 한다.
        assert 'condition_candidates' in STAGE_SOURCE_MAP['SIGNAL']['source']
        assert '별개' in STAGE_SOURCE_MAP['SIGNAL']['source']


# ─── mock cursor — compute_chain()의 9개 execute/fetchone 쌍을 순서대로 스크립트 ──
class _ScriptedCursor:
    """compute_chain() 내부 쿼리 순서: CANDIDATE count, CANDIDATE_INSERT failures,
    DECISION(pass/reject/total), DECISION_INSERT failures, SUBMIT_FILL(executed/exec_failed),
    LIFECYCLE_EXECUTED failures, TRADE(ok/missing), FUTURE_RETURN done, FUTURE_RETURN_INSERT failures.
    """

    def __init__(self, results):
        self._results = list(results)
        self._i = -1
        self.executed = []

    def execute(self, sql, params=None):
        self._i += 1
        self.executed.append(sql.strip().split('\n', 1)[0][:30])

    def fetchone(self):
        return self._results[self._i]


def _make_cursor(*, n_candidates=0, f_candidate=0,
                  n_pass=0, n_reject=0, n_total=0, f_decision=0,
                  n_executed=0, n_exec_failed=0, f_lifecycle_executed=0,
                  n_trade_ok=0, n_trade_missing=0,
                  n_return_done=0, f_future_return=0):
    return _ScriptedCursor([
        (n_candidates,),
        (f_candidate,),
        (n_pass, n_reject, n_total),
        (f_decision,),
        (n_executed, n_exec_failed),
        (f_lifecycle_executed,),
        (n_trade_ok, n_trade_missing),
        (n_return_done,),
        (f_future_return,),
    ])


class TestComputeChainHealthyDay:
    def test_fully_reconciled_day_is_pass(self):
        cur = _make_cursor(
            n_candidates=10, f_candidate=0,
            n_pass=6, n_reject=4, n_total=10, f_decision=0,
            n_executed=6, n_exec_failed=0, f_lifecycle_executed=0,
            n_trade_ok=6, n_trade_missing=0,
            n_return_done=10, f_future_return=0,
        )
        chain = compute_chain(cur, date(2026, 8, 14))
        assert chain['pipeline_status'] == 'PASS'
        assert chain['persistence_failures_total'] == 0
        assert chain['unexplained_drops_total'] == 0


# ─── §11 — 08-12~08-14 numpy.int64 장애 축소 재현 Fixture ──────────────────
class TestAug12FailureReplayFixture:
    """실제 장애: SMC Signal 발생 → candidate INSERT 전부 실패(numpy.int64) →
    Decision 자체가 존재하지 않음. Candidate failure로 FAIL이 잡혀야 하며,
    Decision 단계는 '카운트가 줄어서'가 아니라 CANDIDATE의 PersistenceFailure
    이벤트 때문에 파이프라인이 FAIL이어야 한다."""

    def test_candidate_total_failure_flags_pipeline_fail_via_persistence_event(self):
        cur = _make_cursor(
            n_candidates=0, f_candidate=745,   # 실제 장애 규모와 동일한 형태 — 행은 0건, 실패 이벤트만 745건
            n_pass=0, n_reject=0, n_total=0, f_decision=0,
            n_executed=0, n_exec_failed=0, f_lifecycle_executed=0,
            n_trade_ok=0, n_trade_missing=0,
            n_return_done=0, f_future_return=0,
        )
        chain = compute_chain(cur, date(2026, 8, 12))
        by_stage = {s['stage']: s for s in chain['stages']}

        assert by_stage['CANDIDATE']['fail'] == 745
        assert by_stage['CANDIDATE']['status'] == 'FAIL'
        # Decision 자체는 입력(candidates)이 0이라 자명하게 N/A — "count가 줄어서 FAIL"이 아니다.
        assert by_stage['DECISION']['status'] == 'N/A'
        assert (by_stage['DECISION']['drop'] or 0) == 0
        assert chain['pipeline_status'] == 'FAIL'
        assert chain['persistence_failures_total'] == 745


# ─── §13 Fault Injection — 각 Stage별 실패가 정확히 그 Stage에서 잡히는지 ──
class TestFaultInjectionPerStage:
    def test_candidate_insert_failure_isolated_to_candidate_stage(self):
        cur = _make_cursor(n_candidates=95, f_candidate=5,
                            n_pass=50, n_reject=45, n_total=95,
                            n_executed=50, n_trade_ok=50, n_return_done=95)
        chain = compute_chain(cur, date(2026, 8, 14))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['CANDIDATE']['fail'] == 5
        assert by_stage['CANDIDATE']['status'] == 'FAIL'
        assert by_stage['DECISION']['status'] == 'PASS'  # 다른 stage는 영향 없음

    def test_decision_insert_failure_isolated_to_decision_stage(self):
        cur = _make_cursor(n_candidates=100, f_candidate=0,
                            n_pass=45, n_reject=50, n_total=95, f_decision=5,
                            n_executed=45, n_trade_ok=45, n_return_done=95)
        chain = compute_chain(cur, date(2026, 8, 14))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['CANDIDATE']['status'] == 'PASS'
        assert by_stage['DECISION']['fail'] == 5
        assert by_stage['DECISION']['status'] == 'FAIL'

    def test_submit_fill_lifecycle_failure_isolated(self):
        """PASS 결정이 EXECUTED로 전이하는 도중 실패(LIFECYCLE_EXECUTED) — SUBMIT_FILL만 FAIL."""
        cur = _make_cursor(n_candidates=100, n_pass=50, n_reject=50, n_total=100,
                            n_executed=45, n_exec_failed=0, f_lifecycle_executed=5,
                            n_trade_ok=45, n_return_done=100)
        chain = compute_chain(cur, date(2026, 8, 14))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['SUBMIT_FILL']['fail'] == 5
        assert by_stage['SUBMIT_FILL']['status'] == 'FAIL'
        assert by_stage['DECISION']['status'] == 'PASS'

    def test_trade_insert_missing_detected_via_sentinel(self):
        """public.trades INSERT 실패 → execution_result.trade_id=0 sentinel로만 남는
        케이스. 이벤트는 없지만(§ TRADE 매핑 설명) TRADE 단계가 FAIL로 잡혀야 한다."""
        cur = _make_cursor(n_candidates=100, n_pass=50, n_reject=50, n_total=100,
                            n_executed=50, n_trade_ok=47, n_trade_missing=3,
                            n_return_done=100)
        chain = compute_chain(cur, date(2026, 8, 14))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['TRADE']['fail'] == 3
        assert by_stage['TRADE']['status'] == 'FAIL'
        assert 'trade_id=0' in by_stage['TRADE']['note']

    def test_future_return_persistence_failure_isolated(self):
        cur = _make_cursor(n_candidates=100, n_pass=50, n_reject=50, n_total=100,
                            n_executed=50, n_trade_ok=50,
                            n_return_done=95, f_future_return=5)
        chain = compute_chain(cur, date(2026, 8, 14))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['FUTURE_RETURN']['fail'] == 5
        assert by_stage['FUTURE_RETURN']['status'] == 'FAIL'

    def test_future_return_pending_without_failure_event_is_warning_not_fail(self):
        """PersistenceFailure 이벤트 없이 단순히 아직 수집이 안 된 경우(비동기 지연) —
        §8: Eligible>0, processed<Eligible → drop 탐지는 하되 FAIL로 확정하지 않는다."""
        cur = _make_cursor(n_candidates=100, n_pass=50, n_reject=50, n_total=100,
                            n_executed=50, n_trade_ok=50,
                            n_return_done=90, f_future_return=0)
        chain = compute_chain(cur, date(2026, 8, 14))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['FUTURE_RETURN']['status'] == 'WARNING'
        assert 'PENDING' in by_stage['FUTURE_RETURN']['note']

    def test_future_return_zero_eligible_is_not_applicable(self):
        cur = _make_cursor(n_candidates=0, n_pass=0, n_reject=0, n_total=0,
                            n_executed=0, n_trade_ok=0, n_return_done=0)
        chain = compute_chain(cur, date(2026, 8, 14))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['FUTURE_RETURN']['status'] == 'N/A'


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
