"""
tests/unit/test_pipeline_health_persistence_failure.py

Pipeline Health / Silent Failure Detection 회귀 테스트 (2026-08-17).

배경: 2026-08-11 이후 사흘간 research.candidates가 0건이었던 이유는
create_candidate()의 numpy.int64 INSERT 실패가 logger.warning() 텍스트로만
남고 어떤 카운터/집계에도 잡히지 않았기 때문이다(analysis/decision_health_check.py의
_insert_failure_rate()는 candidates 행이 "존재하는데 이벤트가 없는" 부분실패만
잡고, 행 자체가 하나도 안 생긴 완전실패는 분모가 0이 되어 0%로 위장됐다).

이번 수정: repositories/decision_repository.py의 create_candidate()/freeze_decision()
except 블록에서 research.event_store에 event_type='PersistenceFailure' 구조화
이벤트를 남기고, analysis/decision_health_check.py가 이를 SQL COUNT로 집계해
operations_daily_summary.py의 CRITICAL 판정까지 연결한다.

안전 원칙: 이 파일은 실제 DB에 전혀 연결하지 않는다 — MagicMock으로 psycopg2
connection/cursor를 흉내낸다(이 프로젝트에는 별도 테스트 DB가 없다 — 이미
tests/test_decision_service.py의 database_guard가 이 상태를 확인해 모든 실DB
테스트를 스킵 중이다. 이 파일은 그 제약 자체를 우회하는 게 아니라 애초에 DB가
필요 없게 만들어서 항상 실행 가능하다).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from repositories.decision_repository import DecisionRepository, PERSISTENCE_FAILURE_EVENT_TYPE


# ─── Fake DB/conn/cursor — 실제 DB 없이 psycopg2 인터페이스만 흉내 ──────────
class _FakeCursor:
    """execute() 호출 순번에 따라 지정된 예외를 던지거나 fetchone()을 반환한다."""

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
        self.put_back = 0

    def _get_conn(self):
        return self._conn

    def _put_conn(self, c):
        self.put_back += 1


# ─── A/B/C/E — create_candidate() 실패 시 PersistenceFailure 이벤트 기록 ────
class TestCreateCandidatePersistenceFailure:
    def test_A_normal_candidate_creates_no_failure_event(self):
        """정상 Candidate 생성 → failure event 0건."""
        cur = _FakeCursor()  # 아무것도 raise 안 함
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))

        candidate_id, trace_id = repo.create_candidate(
            stock_code="005930", observed_at=datetime.now(), price=71000.0,
        )
        assert candidate_id is not None
        failure_events = [e for e in cur.executed if "PersistenceFailure" in str(e)]
        # event_store INSERT 자체는 항상 실행되지만, event_type 파라미터를 봐야 한다.
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert pf_calls == [], f"정상 흐름인데 PersistenceFailure 이벤트가 기록됨: {pf_calls}"
        assert conn.rolled_back == 0

    def test_B_insert_failure_records_one_persistence_failure_event(self):
        """create_candidate()의 INSERT(1번째 execute)를 실패시키면 failure event가 남아야 한다."""
        cur = _FakeCursor(raise_on_call=1, raise_exc=RuntimeError("can't adapt type 'numpy.int64'"))
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))

        candidate_id, trace_id = repo.create_candidate(
            stock_code="005930", observed_at=datetime.now(), price=71000.0,
        )
        assert candidate_id is None, "실패했는데 candidate_id가 반환됨"
        assert conn.rolled_back == 1
        # 실패 후: rollback + 새 미니 트랜잭션(_record_persistence_failure)에서 INSERT + commit
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert len(pf_calls) == 1, f"PersistenceFailure 이벤트가 정확히 1건 기록돼야 함: {pf_calls}"
        assert conn.committed == 1, "실패 이벤트 자체는 별도로 commit돼야 함"

    def test_C_multiple_failures_count_accurately(self):
        """실패를 3번 연속 발생시키면 이벤트도 3번 남아야 한다(누적/중복 없이 정확히)."""
        n_failures = 0
        for _ in range(3):
            cur = _FakeCursor(raise_on_call=1, raise_exc=RuntimeError("fail"))
            conn = _FakeConn(cur)
            repo = DecisionRepository(_FakeDB(conn))
            repo.create_candidate(stock_code="005930", observed_at=datetime.now(), price=1.0)
            pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
            n_failures += len(pf_calls)
        assert n_failures == 3

    def test_E_unknown_exception_type_still_recorded(self):
        """psycopg2 관련 예외가 아닌 임의의 Exception도 동일하게 집계돼야 한다."""
        class WeirdError(Exception):
            pass

        cur = _FakeCursor(raise_on_call=1, raise_exc=WeirdError("unexpected"))
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        repo.create_candidate(stock_code="005930", observed_at=datetime.now(), price=1.0)
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert len(pf_calls) == 1

    def test_F_numpy_int64_style_failure_detected(self):
        """이번 실제 장애(numpy.int64)와 동일한 에러 메시지로 재현 — failure_type=CANDIDATE_INSERT로 잡혀야 한다."""
        cur = _FakeCursor(raise_on_call=1, raise_exc=Exception("can't adapt type 'numpy.int64'"))
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))
        repo.create_candidate(stock_code="000660", observed_at=datetime.now(), price=1.0)
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert len(pf_calls) == 1
        payload_json = pf_calls[0][1][5]  # _emit_event의 INSERT 파라미터 순서상 payload 위치
        assert "CANDIDATE_INSERT" in payload_json
        assert "numpy.int64" in payload_json

    def test_meta_failure_never_propagates(self):
        """관측 계측 자체(_record_persistence_failure)가 실패해도 create_candidate()는
        여전히 (None, trace_id)를 조용히 반환해야 한다 — 트레이딩 흐름 보호가 최우선(§3)."""
        cur = _FakeCursor(raise_on_call=1, raise_exc=RuntimeError("insert fail"))
        conn = _FakeConn(cur)
        # commit()도 실패하게 만들어서 _record_persistence_failure 내부가 또 예외를 만나게 한다.
        conn.commit = MagicMock(side_effect=RuntimeError("commit also broken"))
        repo = DecisionRepository(_FakeDB(conn))

        # 예외가 밖으로 전파되면 이 호출 자체가 실패한다 — 그러면 테스트가 FAIL.
        candidate_id, trace_id = repo.create_candidate(
            stock_code="005930", observed_at=datetime.now(), price=1.0,
        )
        assert candidate_id is None
        assert trace_id  # trace_id는 항상 채워짐


# ─── COMMIT 실패 Fault Injection (INSERT는 성공, commit()에서만 실패) ────────
class TestCommitFailureFaultInjection:
    """§8 — INSERT 실패 / COMMIT 실패 / 예기치 못한 예외 3종을 각각 별도로
    증명해야 한다. 위 B/E/F는 execute()(INSERT) 단계 실패이고, 이 클래스는
    INSERT 자체는 전부 성공한 뒤 conn.commit()에서만 실패하는 케이스를
    별도로 검증한다 — try 블록의 except가 두 실패 지점을 동일하게 잡아야
    한다는 것을 증명한다."""

    def test_create_candidate_commit_failure_detected(self):
        """candidates INSERT + CandidateCreated event INSERT 둘 다 성공하지만
        conn.commit()이 실패하는 경우 — 여전히 candidate_id=None + failure event 1건."""
        cur = _FakeCursor()  # execute()는 전부 정상 (raise_on_call=None)
        # 1번째 commit() 호출 = 실제 트랜잭션 commit → 실패.
        # _record_persistence_failure()가 여는 새 트랜잭션의 commit(2번째 호출)은 성공해야 한다.
        conn = _FakeConn(cur, raise_commit_on_call=1,
                          commit_raise_exc=RuntimeError("could not serialize access"))
        repo = DecisionRepository(_FakeDB(conn))

        candidate_id, trace_id = repo.create_candidate(
            stock_code="005930", observed_at=datetime.now(), price=71000.0,
        )
        assert candidate_id is None, "commit 실패인데 candidate_id가 반환됨"
        assert conn.rolled_back == 1
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert len(pf_calls) == 1, f"COMMIT 실패가 failure event로 안 잡힘: {pf_calls}"
        payload_json = pf_calls[0][1][5]
        assert "CANDIDATE_INSERT" in payload_json

    def test_freeze_decision_commit_failure_detected(self):
        """freeze_decision()도 동일하게 commit 실패를 DECISION_INSERT failure로 잡아야 한다."""
        cur = _FakeCursor()
        conn = _FakeConn(cur, raise_commit_on_call=1,
                          commit_raise_exc=RuntimeError("connection reset"))
        repo = DecisionRepository(_FakeDB(conn))

        decision_id = repo.freeze_decision(
            candidate_id=str(uuid.uuid4()), stock_code="005930", decision="PASS",
            decision_reason_code="TEST", policy_version="v1", feature_snapshot={},
            observed_at=datetime.now(), trace_id="TR-TEST-COMMIT",
        )
        assert decision_id is None
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert len(pf_calls) == 1
        payload_json = pf_calls[0][1][5]
        assert "DECISION_INSERT" in payload_json


# ─── D — Candidate 성공 + Decision 실패 ────────────────────────────────────
class TestFreezeDecisionPersistenceFailure:
    def test_D_candidate_success_decision_failure_detected(self):
        """freeze_decision() INSERT 실패도 별도 failure_type=DECISION_INSERT로 잡혀야 한다."""
        cur = _FakeCursor(raise_on_call=1, raise_exc=RuntimeError("decision insert fail"))
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))

        decision_id = repo.freeze_decision(
            candidate_id=str(uuid.uuid4()), stock_code="005930", decision="PASS",
            decision_reason_code="TEST", policy_version="v1", feature_snapshot={},
            observed_at=datetime.now(), trace_id="TR-TEST-1",
        )
        assert decision_id is None
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert len(pf_calls) == 1
        payload_json = pf_calls[0][1][5]
        assert "DECISION_INSERT" in payload_json

    def test_G_explicit_reject_is_not_misclassified_as_failure(self):
        """정상적인 REJECT 결정(예외 없이 성공적으로 기록됨)은 persistence failure가
        아니다 — decision='REJECT'라는 비즈니스 의미와 '기록 자체의 실패'를 혼동하면 안 된다."""
        cur = _FakeCursor()  # 아무 예외도 안 던짐 — REJECT도 정상 INSERT
        conn = _FakeConn(cur)
        repo = DecisionRepository(_FakeDB(conn))

        decision_id = repo.freeze_decision(
            candidate_id=str(uuid.uuid4()), stock_code="005930", decision="REJECT",
            decision_reason_code="GLOBAL_GATE", policy_version="v1", feature_snapshot={},
            observed_at=datetime.now(), trace_id="TR-TEST-2",
        )
        assert decision_id is not None, "정상 REJECT 기록 자체가 실패하면 안 됨"
        pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
        assert pf_calls == [], "정상 REJECT를 persistence failure로 오인함"


# ─── H — Silent Drop Invariant: Attempt = Success + Failure (Candidate 레벨) ──
class TestSilentDropInvariant:
    """Candidate 영속화 레벨에서의 invariant: 시도한 create_candidate() 호출 수는
    반드시 (CandidateCreated 이벤트 수) + (PersistenceFailure 이벤트 수)와 같아야
    한다. 이 레벨에는 'Explicit Reject' 개념이 없다 — Reject는 한 단계 위
    Decision 레벨(decision_ledger.decision IN ('PASS','REJECT'))의 개념이고,
    Reject이려면 애초에 Candidate 자체는 이미 성공적으로 persist된 상태여야
    한다(freeze_decision은 candidate_id가 있어야 호출 가능). 그래서 이 레벨의
    invariant는 Attempt = Success + Failure 두 항으로 충분하다."""

    def test_invariant_holds_for_mixed_success_and_failure_batch(self):
        attempts = 5
        n_fail_calls = {2, 4}  # 5번 중 2번은 실패하도록
        success = 0
        failure = 0
        for i in range(1, attempts + 1):
            if i in n_fail_calls:
                cur = _FakeCursor(raise_on_call=1, raise_exc=RuntimeError("fail"))
            else:
                cur = _FakeCursor()
            conn = _FakeConn(cur)
            repo = DecisionRepository(_FakeDB(conn))
            candidate_id, _ = repo.create_candidate(
                stock_code="005930", observed_at=datetime.now(), price=1.0,
            )
            if candidate_id is not None:
                success += 1
            pf_calls = [e for e in cur.executed if e[1] and PERSISTENCE_FAILURE_EVENT_TYPE in e[1]]
            failure += len(pf_calls)

        assert success == 3
        assert failure == 2
        assert success + failure == attempts, (
            f"SILENT_DROP 발생: attempts={attempts} success={success} failure={failure} "
            f"— {attempts - success - failure}건이 어디에도 설명되지 않음"
        )


def _silent_drop_status(attempts: int, success: int, explicit_reject: int, failure: int) -> str:
    """Decision 레벨 invariant 체커: Attempt = Success + Explicit Reject + Failure.
    세 항으로 attempts를 설명하지 못하면 FAIL — operations_daily_summary가
    실제로 쓰는 함수는 아니고, Silent Drop 개념 자체를 검증하기 위한 테스트
    전용 헬퍼다(§11 Test H)."""
    unexplained = attempts - (success + explicit_reject + failure)
    return 'FAIL' if unexplained != 0 else 'PASS'


class TestSilentDropFixtureDetectsFailure:
    """Test H — 지금까지의 TestSilentDropInvariant는 '정상적으로 계측된' 경우에만
    invariant가 성립함을 보였다. 이 테스트는 반대로, 계측이 실제로 새는 픽스처를
    주고 그 상태가 반드시 FAIL로 잡히는지 증명한다(사용자 예시:
    Attempts=100, Success=60, Reject=35 → 5건 SILENT_DROP → FAIL)."""

    def test_silent_drop_fixture_flags_fail(self):
        # persistence_failure 카운터가 완전히 죽어서(=이번 장애의 실제 형태) 5건이
        # success/reject/failure 어디에도 잡히지 않은 상황을 재현한다.
        status = _silent_drop_status(attempts=100, success=60, explicit_reject=35, failure=0)
        assert status == 'FAIL'

    def test_fully_reconciled_batch_passes(self):
        # 위와 동일한 100건이지만 failure=5로 정상 계측되면 PASS여야 한다(대조군).
        status = _silent_drop_status(attempts=100, success=60, explicit_reject=35, failure=5)
        assert status == 'PASS'

    def test_zero_attempts_trivially_passes(self):
        status = _silent_drop_status(attempts=0, success=0, explicit_reject=0, failure=0)
        assert status == 'PASS'


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
