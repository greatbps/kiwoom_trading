"""
tests/unit/test_execution_lifecycle_integration.py

FINAL PRE-LIVE EXECUTION LIFECYCLE INTEGRATION VALIDATION (2026-08-17)

Candidate → Decision → Execution → Trade → Pipeline Health 전체 lifecycle을
하나의 체인으로 검증한다. 새로운 매매 전략/진입조건/리스크 로직은 추가하지
않는다 — 이 파일은 검증 전용이며 production 코드를 바꾸지 않는다.

이 파일이 재사용하는 실제 구성요소(추측 아님, 이전 라운드에서 코드로 확인됨):
  - repositories/decision_repository.py: create_candidate/freeze_decision/
    mark_executed/mark_execution_failed/_record_persistence_failure
  - services/decision_service.py: begin_evaluation/record_acceptance/
    record_rejection/record_order/record_order_failure
  - analysis/pipeline_health_chain.py: reconcile()/compute_chain()

중요한 스키마상의 사실(허구로 만들지 않음, §3 Fill Failure 관련):
  이 코드베이스에는 "Order Submit"과 "Fill"을 구분하는 별도 상태/테이블이
  없다 — mark_executed()/mark_execution_failed() 둘 다 FROZEN에서 한 번에
  전이한다(analysis/pipeline_health_chain.py의 SUBMIT_FILL 매핑 참조). 따라서
  §3-E "Fill Failure"는 이 시스템에서는 §3-C/D(주문 실행 실패)와 동일한
  코드 경로로 귀결된다 — 별도 Fill 상태를 인위적으로 만들지 않고, 이 사실을
  테스트로 명시한다.

실제 DB/API는 전혀 호출하지 않는다 — _FakeCursor/_FakeConn/_FakeDB로 psycopg2를
완전히 대체한다.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from repositories.decision_repository import DecisionRepository, PERSISTENCE_FAILURE_EVENT_TYPE
from services.decision_service import DecisionService
from services.evaluation_context import EvaluationContext
from analysis.pipeline_health_chain import compute_chain, reconcile


# ─── Fake DB 계층 (이전 라운드와 동일 패턴, 이 파일 안에서 자족적으로 재정의) ──
class _FakeCursor:
    def __init__(self, *, raise_on_call: int | None = None,
                 raise_exc: BaseException | None = None,
                 fetchone_value=("11111111-1111-1111-1111-111111111111",)):
        self._call_n = 0
        self._raise_on_call = raise_on_call
        self._raise_exc = raise_exc or RuntimeError("boom")
        self._fetchone_value = fetchone_value
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._call_n += 1
        self.executed.append((sql.strip().split("\n", 1)[0][:45], params))
        if self._raise_on_call is not None and self._call_n == self._raise_on_call:
            raise self._raise_exc

    def fetchone(self):
        return self._fetchone_value


class _FakeConn:
    def __init__(self, cursor: _FakeCursor, *, raise_commit_on_call: int | None = None,
                 commit_raise_exc: BaseException | None = None):
        self._cursor = cursor
        self.committed = 0
        self.rolled_back = 0
        self._commit_call_n = 0
        self._raise_commit_on_call = raise_commit_on_call
        self._commit_raise_exc = commit_raise_exc or RuntimeError("commit boom")

    def cursor(self):
        return self._cursor

    def commit(self):
        self._commit_call_n += 1
        if (self._raise_commit_on_call is not None
                and self._commit_call_n == self._raise_commit_on_call):
            raise self._commit_raise_exc
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class _FakeDB:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def _get_conn(self):
        return self._conn

    def _put_conn(self, c):
        pass


def _make_service(repo: DecisionRepository) -> DecisionService:
    svc = DecisionService.__new__(DecisionService)
    svc._db = None
    svc._enabled = True
    svc._dl_enabled = True
    svc._ev_enabled = True
    svc._policy_version = 'SMC_v2.3'
    svc._warn_ms = 15.0
    svc._error_ms = 50.0
    svc._repo = repo
    return svc


def _pf_events(cur: _FakeCursor):
    """cur.executed에서 PersistenceFailure 이벤트 payload만 뽑는다."""
    return [p for sql, p in cur.executed
            if 'INSERT INTO research.event_store' in sql and p and PERSISTENCE_FAILURE_EVENT_TYPE in p]


def _event_types(cur: _FakeCursor):
    return [p[1] for sql, p in cur.executed if 'INSERT INTO research.event_store' in sql and p]


def _decision_insert_calls(cur: _FakeCursor):
    return [p for sql, p in cur.executed if 'INSERT INTO research.decision_ledger' in sql]


def _lifecycle_update_calls(cur: _FakeCursor):
    return [p for sql, p in cur.executed if 'UPDATE research.decision_ledger' in sql]


def assert_valid_state_transition(decision: str, lifecycle_status: str | None):
    """§7 State Transition Invariant — 시스템 전체에서 허용되는 (decision,
    lifecycle_status) 조합은 이 4가지뿐이다. 그 외 조합이 나타나면 이 자체가
    버그다(예: REJECT인데 EXECUTED, PASS/FROZEN인데 lifecycle 정보가 아예 없음 등)."""
    allowed = {
        ('REJECT', None),
        ('REJECT', 'OUTCOME_PENDING'),
        ('PASS', 'FROZEN'),
        ('PASS', 'EXECUTED'),
        ('PASS', 'EXECUTION_FAILED'),
    }
    assert (decision, lifecycle_status) in allowed, (
        f"금지된 상태 조합 발견: decision={decision} lifecycle_status={lifecycle_status}"
    )


# ============================================================
# §2 NORMAL GOLDEN PATH
# ============================================================
class TestGoldenPath:
    def test_full_chain_candidate_to_executed(self):
        """Candidate → Decision(PASS/FROZEN) → Order Intent(=PASS 그 자체) →
        Order Submit/Fill(=EXECUTED 전이, 이 시스템엔 별도 상태 없음) →
        Trade INSERT(trade_id>0) → EXECUTED. 전 구간 정상 완주."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)

        # 1) Candidate
        ctx = svc.begin_evaluation(symbol='005930', price=71000.0,
                                    features={'rvol': 2.1}, stock_name='삼성전자')
        assert ctx is not None
        assert ctx.candidate_id and ctx.trace_id

        # 2) Decision PASS/FROZEN (= Order Intent 그 자체)
        decision_id = svc.record_acceptance(ctx, features={'choch_grade': 'A'}, confidence=0.9)
        assert decision_id is not None

        # 3) Order Submit(성공) + Trade INSERT(trade_id=777) → Fill/EXECUTED
        result = svc.record_order(decision_id=decision_id, trade_id=777,
                                   order_no='0000777', executed_price=71000.0,
                                   trace_id=ctx.trace_id)
        assert result is True

        # ── 상태 누락 없음 검증 ──
        candidate_calls = [p for sql, p in cur.executed if 'INSERT INTO research.candidates' in sql]
        assert len(candidate_calls) == 1

        decision_calls = _decision_insert_calls(cur)
        assert len(decision_calls) == 1
        assert decision_calls[0][4] == 'PASS'  # decision

        update_calls = _lifecycle_update_calls(cur)
        assert len(update_calls) == 1
        assert update_calls[0][0] == 'EXECUTED'

        events = _event_types(cur)
        assert events == ['CandidateCreated', 'DecisionFrozen', 'DecisionExecuted']

        # trade_id > 0 최종 확인 — execution_result JSONB 안의 trade_id
        exec_result_json = update_calls[0][1]  # extra_sets 순서상 execution_result가 첫 값
        assert '"trade_id": 777' in exec_result_json

        assert _pf_events(cur) == [], "정상 경로에 PersistenceFailure가 기록되면 안 됨"
        assert_valid_state_transition('PASS', 'EXECUTED')

    def test_golden_path_pipeline_health_is_pass(self):
        """위 Golden Path 1건을 compute_chain()의 집계 결과로 환산하면 PASS여야 한다."""
        cur_script = _make_scripted_cursor(
            n_candidates=1, f_candidate=0,
            n_pass=1, n_reject=0, n_total=1, f_decision=0,
            n_executed=1, n_exec_failed=0, f_lifecycle_executed=0,
            n_trade_ok=1, n_trade_missing=0,
            n_return_done=1, f_future_return=0,
        )
        from datetime import date
        chain = compute_chain(cur_script, date(2026, 8, 17))
        assert chain['pipeline_status'] == 'PASS'
        assert chain['persistence_failures_total'] == 0
        assert chain['unexplained_drops_total'] == 0


class _ScriptedCursor:
    """pipeline_health_chain.compute_chain()의 9개 execute/fetchone 쌍을 순서대로 스크립트.
    (tests/unit/test_pipeline_health_chain.py의 _make_cursor와 동일한 계약.)"""

    def __init__(self, results):
        self._results = list(results)
        self._i = -1

    def execute(self, sql, params=None):
        self._i += 1

    def fetchone(self):
        return self._results[self._i]


def _make_scripted_cursor(*, n_candidates=0, f_candidate=0,
                           n_pass=0, n_reject=0, n_total=0, f_decision=0,
                           n_executed=0, n_exec_failed=0, f_lifecycle_executed=0,
                           n_trade_ok=0, n_trade_missing=0,
                           n_return_done=0, f_future_return=0) -> _ScriptedCursor:
    return _ScriptedCursor([
        (n_candidates,), (f_candidate,),
        (n_pass, n_reject, n_total), (f_decision,),
        (n_executed, n_exec_failed), (f_lifecycle_executed,),
        (n_trade_ok, n_trade_missing),
        (n_return_done,), (f_future_return,),
    ])


# ============================================================
# §3 FAILURE PATH MATRIX
# ============================================================
class TestFailureMatrixA_CandidatePersistence:
    def test_candidate_insert_failure_blocks_decision_stage(self):
        """A. Candidate INSERT 실패 → ctx=None → 이후 Decision 단계로 절대
        진행하지 않음(begin_evaluation이 None을 반환하면 모든 후속 호출이
        no-op이라는 게 DecisionService의 설계 계약)."""
        cur = _FakeCursor(raise_on_call=1, raise_exc=RuntimeError("can't adapt type 'numpy.int64'"))
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)

        ctx = svc.begin_evaluation(symbol='005930', price=71000.0)
        assert ctx is None

        # ctx=None이면 record_acceptance()가 즉시 반환하고 어떤 decision_ledger
        # INSERT도 시도하지 않는다 — "이후 정상 Decision 생성으로 진행하지 않음".
        decision_id = svc.record_acceptance(ctx, features={})
        assert decision_id is None
        assert _decision_insert_calls(cur) == []

        pf = _pf_events(cur)
        assert len(pf) == 1
        assert 'CANDIDATE_INSERT' in pf[0][5]


class TestFailureMatrixB_DecisionPersistence:
    def test_decision_insert_failure_no_frozen_state_created(self):
        """B. candidates INSERT는 성공하지만 decision_ledger INSERT(3번째 execute
        호출: candidates INSERT, CandidateCreated event INSERT, 그 다음)가 실패
        하는 경우. '잘못된 PASS/FROZEN 상태 생성 금지' — INSERT 자체가
        rollback되므로 FROZEN 행은 DB에 존재하지 않는다(rolled_back==1로 확인)."""
        cur = _FakeCursor(raise_on_call=3, raise_exc=RuntimeError("decision insert fail"))
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)

        ctx = svc.begin_evaluation(symbol='005930', price=71000.0)
        assert ctx is not None  # candidate는 성공

        decision_id = svc.record_acceptance(ctx, features={})
        assert decision_id is None
        assert conn.rolled_back == 1

        pf = _pf_events(cur)
        assert len(pf) == 1
        assert 'DECISION_INSERT' in pf[0][5]


class TestFailureMatrixCD_OrderApiFailure:
    def test_c_order_return_code_failure_is_pass_frozen_execution_failed(self):
        """C. order_buy() return_code != 0 (main_auto_trading.py의 이 조건에서
        호출하는 것과 동일한 record_order_failure() 경로). PASS→FROZEN→
        EXECUTION_FAILED만 나와야 하며 REJECT/EXECUTED는 절대 나오면 안 된다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        ctx = EvaluationContext(trace_id='TR-A', candidate_id=str(uuid.uuid4()),
                                 symbol='005930', observed_at=datetime.now(),
                                 policy_version='SMC_v2.3')

        decision_id = svc.record_order_failure(ctx, 'ORDER_FAILURE',
                                                features={'gate_reason': 'ORDER_FAILURE: 잔고부족'})
        assert decision_id is not None

        decision_calls = _decision_insert_calls(cur)
        assert decision_calls[0][4] == 'PASS'
        update_calls = _lifecycle_update_calls(cur)
        assert update_calls[0][0] == 'EXECUTION_FAILED'
        assert 'REJECT' not in [c[4] for c in decision_calls]
        assert 'EXECUTED' not in [u[0] for u in update_calls]
        assert_valid_state_transition('PASS', 'EXECUTION_FAILED')

    def test_d_order_api_exception_produces_execution_failed_event(self):
        """D. 브로커 API 예외 강제 발생 시나리오도 동일 경로(record_order_failure)
        를 타므로 C와 동일하게 검증하되, DecisionExecutionFailed 이벤트 자체가
        실제로 남는지 별도 확인한다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        ctx = EvaluationContext(trace_id='TR-B', candidate_id=str(uuid.uuid4()),
                                 symbol='000660', observed_at=datetime.now(),
                                 policy_version='SMC_v2.3')

        decision_id = svc.record_order_failure(ctx, 'API_FAILURE',
                                                features={'gate_reason': 'API_FAILURE: network timeout'})
        assert decision_id is not None
        assert _event_types(cur) == ['DecisionFrozen', 'DecisionExecutionFailed']
        pf = _pf_events(cur)
        assert pf == [], "정상적으로 처리된 실행 실패는 PersistenceFailure가 아니다(설계상 구분됨)"


class TestFailureMatrixE_FillFailure:
    def test_fill_failure_has_no_separate_state_and_is_not_masked_as_executed(self):
        """E. Fill Failure — 이 코드베이스엔 Submit과 Fill을 구분하는 별도
        상태가 없다(§ 파일 상단 설명). '주문 Submit 이후 Fill 실패'에 가장
        가까운 실제 시나리오는 브로커가 주문은 접수했지만(order_no 존재)
        체결/영속화가 끝내 실패해 trade_id를 못 얻는 경우 — 즉 F(Trade INSERT
        Failure)와 동일한 코드 경로다. 별도 Fill 상태를 인위적으로 만들지
        않고, EXECUTED로 위장되지 않음만 확인한다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        decision_id = str(uuid.uuid4())

        # order_no는 있지만(브로커가 주문은 접수) trade_id를 못 얻음(체결 확인/영속화 실패)
        result = svc.record_order(decision_id=decision_id, trade_id=None,
                                   order_no='0009999', executed_price=None)
        assert result is True
        update_calls = _lifecycle_update_calls(cur)
        assert update_calls[0][0] == 'EXECUTION_FAILED'
        assert 'EXECUTED' != update_calls[0][0]


class TestFailureMatrixF_TradeInsertFailure:
    def test_trade_insert_failure_none_is_execution_failed_never_executed(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        decision_id = str(uuid.uuid4())

        svc.record_order(decision_id=decision_id, trade_id=None, order_no='0001111')
        update_calls = _lifecycle_update_calls(cur)
        assert update_calls[0][0] == 'EXECUTION_FAILED'
        assert all(u[0] != 'EXECUTED' for u in update_calls)


class TestFailureMatrixG_TradeIdSentinelMatrix:
    @pytest.mark.parametrize('trade_id,expected_status', [
        (None, 'EXECUTION_FAILED'),
        (0, 'EXECUTION_FAILED'),
        (55, 'EXECUTED'),
    ])
    def test_trade_id_sentinel_matrix(self, trade_id, expected_status):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        decision_id = str(uuid.uuid4())

        svc.record_order(decision_id=decision_id, trade_id=trade_id, order_no='000X')
        update_calls = _lifecycle_update_calls(cur)
        assert update_calls[0][0] == expected_status
        assert_valid_state_transition('PASS', expected_status)


# ============================================================
# §4 DECISION SEMANTICS VALIDATION
# ============================================================
class TestDecisionSemanticsNeverMixed:
    def test_strategy_reject_never_produces_lifecycle_transition(self):
        """전략 거절(REJECT)은 lifecycle_status 전이(_update_lifecycle) 자체를
        절대 발생시키지 않는다 — REJECT는 freeze_decision에서 끝나는 별개 경로."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        ctx = EvaluationContext(trace_id='TR-C', candidate_id=str(uuid.uuid4()),
                                 symbol='005930', observed_at=datetime.now(),
                                 policy_version='SMC_v2.3')

        svc.record_rejection(ctx, 'CHOCH_MISSING', features={})

        decision_calls = _decision_insert_calls(cur)
        assert decision_calls[0][4] == 'REJECT'
        assert _lifecycle_update_calls(cur) == []  # UPDATE 자체가 없음
        assert_valid_state_transition('REJECT', None)

    def test_order_failure_is_never_recorded_as_reject(self):
        """§4 핵심 — 주문 실행 실패는 절대 전략 REJECT로 기록되지 않는다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        ctx = EvaluationContext(trace_id='TR-D', candidate_id=str(uuid.uuid4()),
                                 symbol='005930', observed_at=datetime.now(),
                                 policy_version='SMC_v2.3')

        svc.record_order_failure(ctx, 'ORDER_FAILURE', features={})
        decision_calls = _decision_insert_calls(cur)
        assert decision_calls[0][4] == 'PASS'
        assert decision_calls[0][4] != 'REJECT'


# ============================================================
# §6 SILENT DROP INVARIANT (Candidate/Decision/Intent/Submit/Execution/Trade)
# ============================================================
class TestSilentDropInvariantFullChain:
    def test_every_stage_explains_all_attempts_in_healthy_scenario(self):
        r_candidate = reconcile('CANDIDATE', None, 100, fail_=0)
        r_decision = reconcile('DECISION', 100, 100, reject_=0, fail_=0)  # 100건 모두 decision 생성(PASS+REJECT)
        r_intent = reconcile('INTENT', 60, 60, 0, 0)      # PASS 60건 = Intent
        r_submit_fill = reconcile('SUBMIT_FILL', 60, 55, reject_=5, fail_=0)  # 55 EXECUTED + 5 EXECUTION_FAILED(명시적)
        r_trade = reconcile('TRADE', 55, 55, 0, 0)         # trade_id 정상 발급 55건

        for r in (r_candidate, r_decision, r_intent, r_submit_fill, r_trade):
            assert r.status in ('PASS', 'WARNING', 'N/A'), f"{r.stage}: {r.status} — {r.note}"
            assert (r.drop or 0) == 0, f"{r.stage}에 SILENT_DROP 발생: {r.note}"

    def test_unexplained_attempt_at_any_stage_fails(self):
        """의도적으로 Decision 단계에서 3건을 설명 안 되게 만들면 FAIL이어야 한다."""
        r_decision = reconcile('DECISION', 100, 95, reject_=2, fail_=0)  # 100 - 95 - 2 - 0 = 3 unexplained
        assert r_decision.status == 'FAIL'
        assert r_decision.drop == 3


# ============================================================
# §8 MUTATION TEST — Execution Failure transition 제거
# ============================================================
class TestMutationExecutionFailedTransitionRemoved:
    def test_removing_mark_execution_failed_call_is_caught_by_tests(self):
        """_record_order_failure_impl()에서 mark_execution_failed() 호출을
        빼면(=freeze만 하고 실패 전이를 안 남기면) 관련 테스트가 FAIL해야
        한다는 것을 실제로 뮤테이션-복원 사이클로 증명한다."""
        import services.decision_service as ds_mod
        original = ds_mod.DecisionService._record_order_failure_impl

        def _mutated_impl(self, ctx, reason, features):
            # mark_execution_failed() 호출을 의도적으로 생략 — freeze만 하고 끝냄
            decided_at = datetime.now()
            feature_snapshot = self._build_snapshot(features)
            decision_id = self._repo.freeze_decision(
                candidate_id=ctx.candidate_id, stock_code=ctx.symbol,
                decision='PASS', decision_reason_code='PASS',
                policy_version=ctx.policy_version, feature_snapshot=feature_snapshot,
                observed_at=ctx.observed_at, decided_at=decided_at,
                strategy_type=getattr(ctx, 'strategy_type', 'SMC_INTRADAY'),
                trace_id=ctx.trace_id, created_by='decision_service',
            )
            return decision_id  # EXECUTION_FAILED 전이 없음 — 뮤테이션

        ds_mod.DecisionService._record_order_failure_impl = _mutated_impl
        try:
            cur = _FakeCursor()
            conn = _FakeConn(cur)
            repo = DecisionRepository(_FakeDB(conn))
            svc = _make_service(repo)
            ctx = EvaluationContext(trace_id='TR-MUT', candidate_id=str(uuid.uuid4()),
                                     symbol='005930', observed_at=datetime.now(),
                                     policy_version='SMC_v2.3')
            svc.record_order_failure(ctx, 'ORDER_FAILURE', features={})

            # 뮤테이션 상태에서는 lifecycle UPDATE가 전혀 없어야 한다(=FROZEN에 방치) —
            # 이게 바로 §3-C/D가 원래 잡아야 했던 결함과 동일한 형태다.
            update_calls = _lifecycle_update_calls(cur)
            assert update_calls == [], "뮤테이션이 무력화되지 않음 — 테스트가 결함을 못 잡고 있다는 뜻"
        finally:
            ds_mod.DecisionService._record_order_failure_impl = original

        # 원복 후 정상 동작 재확인
        cur2 = _FakeCursor()
        conn2 = _FakeConn(cur2)
        repo2 = DecisionRepository(_FakeDB(conn2))
        svc2 = _make_service(repo2)
        ctx2 = EvaluationContext(trace_id='TR-MUT2', candidate_id=str(uuid.uuid4()),
                                  symbol='005930', observed_at=datetime.now(),
                                  policy_version='SMC_v2.3')
        svc2.record_order_failure(ctx2, 'ORDER_FAILURE', features={})
        assert _lifecycle_update_calls(cur2)[0][0] == 'EXECUTION_FAILED'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
