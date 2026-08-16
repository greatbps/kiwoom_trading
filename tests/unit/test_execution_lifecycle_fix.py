"""
tests/unit/test_execution_lifecycle_fix.py

Execution Lifecycle 결함 수정 검증 (2026-08-17, Pipeline Health Chain 후속).

배경 (조사로 확정된 근본원인):
  main_auto_trading.py의 execute_buy()는 브로커 주문(self.api.order_buy())이
  실패하면(return_code!=0 또는 예외) decision_service.record_acceptance()가
  호출되기도 전에 즉시 return한다 — PASS decision_id 자체가 아직 없다. 그래서
  repositories/decision_repository.py의 mark_execution_failed()는 구현만 되어
  있고 실제로는 호출부가 없는 죽은 코드였다(grep으로 확인). 대신 기존
  _finalize_decision()이 이 경우를 decision='REJECT'로 기록했다(Migration 005,
  2026-07-27, Audit 7 — 당시엔 "REJECT로라도 남기자"는 의도적 조치였음). 문제는
  decision_ledger.decision='PASS'가 "SMC가 매수를 승인했다"는 뜻인데, 브로커
  단계 실패를 REJECT로 남기면 "SMC가 애초에 거부했다"는 잘못된 의미가 된다.

  또 하나: public.trades INSERT가 실패해도(trade_id=None) main_auto_trading.py는
  services/decision_service.py의 record_order()를 그대로 호출했고, 거기서
  `trade_id or 0`으로 치환돼 decision_ledger가 EXECUTED + execution_result.
  trade_id=0(SERIAL은 1부터 시작하므로 실존할 수 없는 sentinel)으로 잘못
  기록됐다.

이번 수정 (services/decision_service.py만, 주문/진입 판단 로직 미변경):
  1. DecisionService.record_order_failure() 신규 — PASS로 freeze한 뒤 즉시
     mark_execution_failed()를 호출해 FROZEN→EXECUTION_FAILED로 정확히 기록.
     main_auto_trading.py의 두 실패 분기(ORDER_FAILURE/API_FAILURE)만 이
     경로로 재배선(_finalize_decision → _finalize_decision_execution_failure).
  2. DecisionService.record_order()에 `if not trade_id:` 가드 추가 — trade_id가
     없으면 EXECUTED로 위장하지 않고 mark_execution_failed()로 리다이렉트.
     trade_id가 정상(양의 정수)인 기존 경로는 그대로 보존.

이 파일은 실제 DB에 연결하지 않는다 — repositories/decision_repository.py를
그대로 쓰되 tests/unit/test_pipeline_health_persistence_failure.py와 동일한
_FakeCursor/_FakeConn/_FakeDB로 psycopg2를 흉내낸다. DecisionService는
__new__()로 생성해 config/YAML 의존 없이 필요한 속성만 직접 주입한다
(tests/simulation/test_execute_buy_dry_run.py와 동일한 stub-without-__init__
패턴).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from repositories.decision_repository import DecisionRepository, PERSISTENCE_FAILURE_EVENT_TYPE
from services.decision_service import DecisionService
from services.evaluation_context import EvaluationContext
from analysis.pipeline_health_chain import compute_chain


# ─── Fake DB/conn/cursor — test_pipeline_health_persistence_failure.py와 동일 패턴 ──
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
        self.executed.append((sql.strip().split("\n", 1)[0][:40], params))
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
    """config/YAML 로드 없이 DecisionService를 조립한다."""
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


def _make_ctx(candidate_id: str = None) -> EvaluationContext:
    return EvaluationContext(
        trace_id='TR-20260817-000001',
        candidate_id=candidate_id or str(uuid.uuid4()),
        symbol='005930',
        observed_at=datetime.now(),
        policy_version='SMC_v2.3',
        price=71000.0,
    )


def _executed_params(cur: _FakeCursor):
    return [p for _, p in cur.executed if p is not None]


# ─── A. Execution Failure Lifecycle / FROZEN→EXECUTION_FAILED ─────────────
class TestRecordOrderFailure:
    def test_creates_pass_decision_then_execution_failed(self):
        """record_order_failure()가 decision='PASS'로 freeze한 뒤 즉시
        lifecycle_status=EXECUTION_FAILED로 전이해야 한다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        ctx = _make_ctx()

        decision_id = svc.record_order_failure(ctx, 'ORDER_FAILURE',
                                                features={'gate_reason': 'ORDER_FAILURE: 잔고부족'})

        assert decision_id is not None

        # freeze_decision INSERT: decision 파라미터는 위치 인자 5번째(index 4)
        insert_calls = [p for sql, p in cur.executed if 'INSERT INTO research.decision_ledger' in sql]
        assert len(insert_calls) == 1
        assert insert_calls[0][4] == 'PASS'
        assert insert_calls[0][5] == 'PASS'  # decision_reason_code도 'PASS'

        # _update_lifecycle UPDATE: 첫 파라미터가 new_status='EXECUTION_FAILED'
        update_calls = [p for sql, p in cur.executed if 'UPDATE research.decision_ledger' in sql]
        assert len(update_calls) == 1
        assert update_calls[0][0] == 'EXECUTION_FAILED'

        # event_store에 DecisionExecutionFailed 이벤트가 남았는지
        event_calls = [p for sql, p in cur.executed if 'INSERT INTO research.event_store' in sql]
        event_types = [p[1] for p in event_calls]  # occurred_at, event_type, ...
        assert 'DecisionExecutionFailed' in event_types

    def test_api_failure_reason_recorded_in_event_payload(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        ctx = _make_ctx()

        svc.record_order_failure(ctx, 'API_FAILURE', features={'gate_reason': 'API_FAILURE: timeout'})

        event_calls = [p for sql, p in cur.executed if 'INSERT INTO research.event_store' in sql]
        payload_jsons = [p[5] for p in event_calls]
        assert any('API_FAILURE' in pj for pj in payload_jsons)

    def test_ctx_none_is_noop(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        assert svc.record_order_failure(None, 'ORDER_FAILURE') is None
        assert cur.executed == []

    def test_disabled_service_is_noop(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        svc._dl_enabled = False
        ctx = _make_ctx()
        assert svc.record_order_failure(ctx, 'ORDER_FAILURE') is None
        assert cur.executed == []


# ─── C/D. Trade Persistence Failure / trade_id=0 Protection ───────────────
class TestRecordOrderTradePersistenceGuard:
    def test_trade_id_none_redirects_to_execution_failed(self):
        """public.trades INSERT 실패(trade_id=None) → EXECUTED로 위장하지 않고
        EXECUTION_FAILED로 기록해야 한다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        decision_id = str(uuid.uuid4())

        result = svc.record_order(decision_id=decision_id, trade_id=None,
                                   order_no='0012345', executed_price=71000.0)

        assert result is True
        update_calls = [p for sql, p in cur.executed if 'UPDATE research.decision_ledger' in sql]
        assert len(update_calls) == 1
        assert update_calls[0][0] == 'EXECUTION_FAILED'
        event_calls = [p for sql, p in cur.executed if 'INSERT INTO research.event_store' in sql]
        event_types = [p[1] for p in event_calls]
        assert 'DecisionExecutionFailed' in event_types
        assert 'DecisionExecuted' not in event_types

    def test_trade_id_zero_sentinel_also_redirects(self):
        """trade_id=0(과거 sentinel)도 동일하게 EXECUTION_FAILED로 잡혀야 한다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        decision_id = str(uuid.uuid4())

        svc.record_order(decision_id=decision_id, trade_id=0, order_no='0012345')

        update_calls = [p for sql, p in cur.executed if 'UPDATE research.decision_ledger' in sql]
        assert update_calls[0][0] == 'EXECUTION_FAILED'

    def test_valid_trade_id_still_marks_executed(self):
        """기존 정상 경로 보존 — trade_id가 양의 정수면 그대로 EXECUTED."""
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        decision_id = str(uuid.uuid4())

        result = svc.record_order(decision_id=decision_id, trade_id=42,
                                   order_no='0012345', executed_price=71000.0)

        assert result is True
        update_calls = [p for sql, p in cur.executed if 'UPDATE research.decision_ledger' in sql]
        assert len(update_calls) == 1
        assert update_calls[0][0] == 'EXECUTED'
        event_calls = [p for sql, p in cur.executed if 'INSERT INTO research.event_store' in sql]
        event_types = [p[1] for p in event_calls]
        assert 'DecisionExecuted' in event_types
        assert 'DecisionExecutionFailed' not in event_types

    def test_no_decision_id_is_noop(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        assert svc.record_order(decision_id=None, trade_id=42) is False
        assert cur.executed == []


# ─── Fault Injection — DB COMMIT 실패 (freeze_decision 단계) ───────────────
class TestCommitFailureDuringOrderFailureRecording:
    def test_commit_failure_does_not_crash_caller_and_is_captured_as_persistence_failure(self):
        cur = _FakeCursor()  # execute()는 전부 정상
        conn = _FakeConn(cur, raise_commit_on_call=1,
                          commit_raise_exc=RuntimeError("could not serialize access"))
        repo = DecisionRepository(_FakeDB(conn))
        svc = _make_service(repo)
        ctx = _make_ctx()

        # 예외가 밖으로 전파되면 이 호출 자체가 실패한다 — 그러면 테스트가 FAIL.
        decision_id = svc.record_order_failure(ctx, 'ORDER_FAILURE')

        assert decision_id is None  # freeze_decision commit 실패 → freeze 자체가 실패
        pf_calls = [p for sql, p in cur.executed
                    if 'INSERT INTO research.event_store' in sql and p and PERSISTENCE_FAILURE_EVENT_TYPE in p]
        assert len(pf_calls) == 1, "COMMIT 실패가 기존 PersistenceFailure 메커니즘으로 안 잡힘"
        assert 'CANDIDATE_INSERT' not in pf_calls[0][5]
        assert 'DECISION_INSERT' in pf_calls[0][5]


# ─── Pipeline Health Detection — SUBMIT_FILL 단계가 실제로 잡히는지 ────────
class _ScriptedCursor:
    def __init__(self, results):
        self._results = list(results)
        self._i = -1

    def execute(self, sql, params=None):
        self._i += 1

    def fetchone(self):
        return self._results[self._i]


class TestPipelineHealthDetectsExecutionFailures:
    def test_submit_fill_stage_now_sees_real_execution_failures(self):
        """수정 전에는 EXECUTION_FAILED가 코드상 도달 불가능해 n_exec_failed가 항상
        0으로만 보였다. 이제 실제로 채워질 수 있다 — SUBMIT_FILL이 명시적으로
        설명된 실패(WARNING)로 잡히고, FAIL(설명 안 되는 drop)이 아니어야 한다."""
        cur = _ScriptedCursor([
            (100,), (0,),                      # CANDIDATE: 100건, 실패 0
            (60, 40, 100), (0,),                # DECISION: pass=60 reject=40, 실패 0
            (55, 5), (0,),                      # SUBMIT_FILL: executed=55, exec_failed=5(신규로 실제 관측됨), 실패 0
            (55, 0),                            # TRADE: ok=55
            (100,), (0,),                       # FUTURE_RETURN
        ])
        from datetime import date
        chain = compute_chain(cur, date(2026, 8, 17))
        by_stage = {s['stage']: s for s in chain['stages']}
        assert by_stage['SUBMIT_FILL']['reject'] == 5
        assert by_stage['SUBMIT_FILL']['drop'] == 0
        assert by_stage['SUBMIT_FILL']['status'] == 'WARNING'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
