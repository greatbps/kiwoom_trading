"""
Research Schema Validation — Phase 3.5

4가지 검증:
  1. DDL Validation      — 테이블/인덱스/트리거 존재 확인
  2. Smoke Test          — 전체 Lifecycle (Candidate→Decision→Audit→Knowledge)
  3. Immutable Test      — 핵심 필드 변경 차단, DELETE 차단
  4. Replay Test         — event_store만으로 Timeline 복원

실행:
    cd /home/greatbps/projects/kiwoom_trading
    python3 -m pytest tests/test_research_schema.py -v

또는 직접:
    python3 tests/test_research_schema.py
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

from utils.database_guard import DestructiveOperationBlocked, assert_destructive_allowed


def get_conn():
    """🔧 2026-07-27 [P0 후속검증]: 이 모듈의 _cleanup()은 research 스키마 전체를
    TRUNCATE한다. 실거래 DB를 가리키면 실거래 Decision Ledger/Candidate/Event
    히스토리가 통째로 삭제된다 — 실제로 이 테스트가 원인으로 확인된 데이터 유실
    사고 있음. database_guard가 테스트 DB가 아니라고 판단하면 즉시 스킵한다."""
    db_name = os.getenv('POSTGRES_DB', 'trading_system')
    try:
        assert_destructive_allowed(db_name, operation='TRUNCATE research schema')
    except DestructiveOperationBlocked as e:
        raise unittest.SkipTest(str(e))
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=db_name,
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── Minimal TradingDB stub for DecisionRepository ───────────────

class _StubDB:
    """DecisionRepository DI용 최소 stub — 실제 TradingDB 연결."""

    def __init__(self, conn):
        self._conn = conn

    def _get_conn(self):
        return self._conn

    def _put_conn(self, conn):
        pass  # 단일 연결 유지


# ─── Test Fixture ─────────────────────────────────────────────────

FIXED_TRACE = 'TR-TEST-000001'  # 테스트 전용 고정 Trace ID


def _cleanup(cur):
    """
    테스트 데이터 삭제.
    - DELETE RULE이 decision_ledger / future_return_events를 차단하므로 TRUNCATE 사용
    - TRUNCATE는 Rules를 우회함 (PostgreSQL 설계)
    - CASCADE로 FK 의존 순서를 자동 처리
    """
    cur.execute("""
        TRUNCATE research.event_store,
                 research.future_return_events,
                 research.decision_ledger,
                 research.candidates
        RESTART IDENTITY CASCADE
    """)


# ─── Test Suite ───────────────────────────────────────────────────

class TestDDLValidation(unittest.TestCase):
    """검증 1: DDL 구조물 존재 확인."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.cur = cls.conn.cursor()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _exists(self, query, params=()):
        self.cur.execute(query, params)
        return self.cur.fetchone()[0]

    def test_schema_exists(self):
        ok = self._exists(
            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name='research')"
        )
        self.assertTrue(ok, "research schema missing")

    def test_tables_exist(self):
        for tbl in ['candidates', 'decision_ledger', 'future_return_events',
                    'event_store', 'reason_dictionary']:
            ok = self._exists(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='research' AND table_name=%s)", (tbl,)
            )
            self.assertTrue(ok, f"Table research.{tbl} missing")

    def test_view_exists(self):
        ok = self._exists(
            "SELECT EXISTS(SELECT 1 FROM information_schema.views "
            "WHERE table_schema='research' AND table_name='decision_outcomes')"
        )
        self.assertTrue(ok, "View research.decision_outcomes missing")

    def test_uuid_v7_function(self):
        self.cur.execute("SELECT research.gen_uuid_v7()")
        uid = self.cur.fetchone()[0]
        self.assertIsNotNone(uid)
        parts = str(uid).split('-')
        self.assertEqual(len(parts), 5, f"UUID format wrong: {uid}")
        self.assertEqual(parts[2][0], '7', f"Not UUIDv7 (version != 7): {uid}")

    def test_uuid_v7_is_time_sortable(self):
        """1ms 간격을 두면 반드시 오름차순 — 동일 ms 내는 랜덤 비트로 결정."""
        self.cur.execute("SELECT research.gen_uuid_v7()")
        u1 = self.cur.fetchone()[0]
        self.cur.execute("SELECT pg_sleep(0.01), research.gen_uuid_v7()")
        _, u2 = self.cur.fetchone()
        self.assertLess(str(u1), str(u2), f"UUIDv7 not time-sortable: {u1} >= {u2}")

    def test_reason_dictionary_seeded(self):
        self.cur.execute("SELECT count(*) FROM research.reason_dictionary")
        cnt = self.cur.fetchone()[0]
        self.assertEqual(cnt, 13, f"Expected 13 reason codes, got {cnt}")

    def test_trigger_exists(self):
        self.cur.execute(
            "SELECT count(*) FROM information_schema.triggers "
            "WHERE trigger_schema='research' AND trigger_name='decision_ledger_immutability'"
        )
        self.assertGreater(self.cur.fetchone()[0], 0, "Immutability trigger missing")

    def test_indexes_exist(self):
        key_indexes = ['idx_dl_decided_at', 'idx_dl_decision', 'idx_dl_feature_snapshot',
                       'idx_dl_trace_id', 'idx_event_store_occurred_at']
        for idx in key_indexes:
            self.cur.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='research' AND indexname=%s)",
                (idx,)
            )
            self.assertTrue(self.cur.fetchone()[0], f"Index {idx} missing")


class TestLifecycleSmoke(unittest.TestCase):
    """검증 2: 전체 Lifecycle Smoke Test."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.conn.autocommit = False
        cls.cur = cls.conn.cursor()
        _cleanup(cls.cur)
        cls.conn.commit()

        from repositories.decision_repository import DecisionRepository
        cls.repo = DecisionRepository(_StubDB(cls.conn))

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.conn.close()

    def test_01_create_candidate(self):
        now = datetime.now()
        cid, trace = self.repo.create_candidate(
            stock_code='005930',
            stock_name='삼성전자',
            observed_at=now,
            price=75000.0,
            sector='Semiconductor',
            market='KOSPI',
            rs_score=88.5,
            rvol=2.1,
            atr_pct=1.2,
            regime='Risk-On',
            trace_id=FIXED_TRACE,
            created_by='test',
        )
        self.assertIsNotNone(cid, "create_candidate returned None")
        self.assertEqual(trace, FIXED_TRACE)
        self.__class__.candidate_id = cid

        self.cur.execute(
            "SELECT stock_code, trace_id, lifecycle_status FROM research.candidates "
            "WHERE candidate_id=%s::uuid", (cid,)
        )
        row = self.cur.fetchone()
        self.assertEqual(row[0], '005930')
        self.assertEqual(row[1], FIXED_TRACE)
        self.assertEqual(row[2], 'CREATED')

    def test_02_freeze_decision_reject(self):
        feature_snap = {
            'choch_grade': 'C', 'choch_detected': False, 'sweep_detected': True,
            'sweep_type': 'buyside', 'sweep_distance_pct': 0.8,
            'fvg_detected': False, 'rvol': 2.1, 'atr_pct': 1.2,
            'regime': 'Risk-On', 'rs_score': 88.5, 'ema_gap_pct': 0.5,
            'htf_trend': 'bullish', 'structure_trend': 'up',
            'risk_score': 0.45, 'expected_rr': 2.1, 'sector': 'Semiconductor',
        }
        did = self.repo.freeze_decision(
            candidate_id=self.__class__.candidate_id,
            stock_code='005930',
            decision='REJECT',
            decision_reason_code='CHOCH_MISSING',
            policy_version='SMC_v2.3',
            feature_snapshot=feature_snap,
            observed_at=datetime.now() - timedelta(minutes=1),
            decided_at=datetime.now(),
            confidence=0.35,
            expected_rr=2.1,
            risk_score=0.45,
            trace_id=FIXED_TRACE,
            created_by='test',
        )
        self.assertIsNotNone(did, "freeze_decision returned None")
        self.__class__.decision_id = did

        self.cur.execute(
            "SELECT decision, decision_reason_code, lifecycle_status, policy_version "
            "FROM research.decision_ledger WHERE decision_id=%s::uuid", (did,)
        )
        row = self.cur.fetchone()
        self.assertEqual(row[0], 'REJECT')
        self.assertEqual(row[1], 'CHOCH_MISSING')
        self.assertEqual(row[2], 'FROZEN')
        self.assertEqual(row[3], 'SMC_v2.3')

        self.cur.execute(
            "SELECT lifecycle_status FROM research.candidates "
            "WHERE candidate_id=%s::uuid", (self.__class__.candidate_id,)
        )
        self.assertEqual(self.cur.fetchone()[0], 'EVALUATED')

    def test_03_mark_outcome_pending(self):
        ok = self.repo.mark_outcome_pending(self.__class__.decision_id, trace_id=FIXED_TRACE)
        self.assertTrue(ok)
        self.cur.execute(
            "SELECT lifecycle_status FROM research.decision_ledger "
            "WHERE decision_id=%s::uuid", (self.__class__.decision_id,)
        )
        self.assertEqual(self.cur.fetchone()[0], 'OUTCOME_PENDING')

    def test_04_record_future_returns(self):
        for horizon, ret in [('+30m', 1.2), ('+EOD', 3.5), ('+1D', 5.1),
                              ('+3D', 7.2), ('+5D', 8.3)]:
            ok = self.repo.record_future_return(
                decision_id=self.__class__.decision_id,
                stock_code='005930',
                horizon_label=horizon,
                decision_price=75000.0,
                return_pct=ret,
                price_at_horizon=75000.0 * (1 + ret / 100),
                trace_id=FIXED_TRACE,
            )
            self.assertTrue(ok, f"record_future_return failed for {horizon}")

        self.cur.execute(
            "SELECT count(*) FROM research.future_return_events "
            "WHERE decision_id=%s::uuid", (self.__class__.decision_id,)
        )
        self.assertEqual(self.cur.fetchone()[0], 5)

    def test_05_complete_outcome(self):
        ok = self.repo.complete_outcome(self.__class__.decision_id, trace_id=FIXED_TRACE)
        self.assertTrue(ok)
        self.cur.execute(
            "SELECT lifecycle_status FROM research.decision_ledger "
            "WHERE decision_id=%s::uuid", (self.__class__.decision_id,)
        )
        self.assertEqual(self.cur.fetchone()[0], 'OUTCOME_RECORDED')

    def test_06_complete_audit(self):
        ok = self.repo.complete_audit(
            decision_id=self.__class__.decision_id,
            decision_quality='RATIONAL',
            outcome_quality='LARGE_OPPORTUNITY_LOSS',
            decision_notes='CHoCH 조건 미충족으로 거절 — 당시 정보 기준 합리적',
            outcome_notes='+5일 +8.3% — 기회 손실 발생',
            decision_verdict='CHoCH 미확인으로 REJECT. 당시 판단은 정책 기준 합리적이나 결과적으로 기회 손실.',
            trace_id=FIXED_TRACE,
        )
        self.assertTrue(ok)
        self.cur.execute(
            "SELECT lifecycle_status, decision_auditor_result->>'quality', "
            "       outcome_auditor_result->>'quality' "
            "FROM research.decision_ledger WHERE decision_id=%s::uuid",
            (self.__class__.decision_id,)
        )
        row = self.cur.fetchone()
        self.assertEqual(row[0], 'AUDIT_COMPLETED')
        self.assertEqual(row[1], 'RATIONAL')
        self.assertEqual(row[2], 'LARGE_OPPORTUNITY_LOSS')

    def test_07_extract_knowledge(self):
        ok = self.repo.extract_knowledge(self.__class__.decision_id, trace_id=FIXED_TRACE)
        self.assertTrue(ok)
        self.cur.execute(
            "SELECT lifecycle_status FROM research.decision_ledger "
            "WHERE decision_id=%s::uuid", (self.__class__.decision_id,)
        )
        self.assertEqual(self.cur.fetchone()[0], 'KNOWLEDGE_EXTRACTED')

    def test_08_outcome_view_label(self):
        self.cur.execute(
            "SELECT outcome_label, return_5d FROM research.decision_outcomes "
            "WHERE decision_id=%s::uuid", (self.__class__.decision_id,)
        )
        row = self.cur.fetchone()
        self.assertIsNotNone(row, "decision_outcomes view returned no row")
        self.assertEqual(row[0], 'LARGE_OPPORTUNITY_LOSS', f"Wrong outcome_label: {row[0]}")
        self.assertAlmostEqual(float(row[1]), 8.3, places=1)

    def test_09_event_count(self):
        self.cur.execute(
            "SELECT event_type FROM research.event_store "
            "WHERE payload->>'trace_id'=%s ORDER BY occurred_at",
            (FIXED_TRACE,)
        )
        events = [r[0] for r in self.cur.fetchall()]
        expected = [
            'CandidateCreated', 'DecisionFrozen', 'OutcomePending',
            'FutureReturnPartial', 'FutureReturnPartial', 'FutureReturnPartial',
            'FutureReturnPartial', 'FutureReturnPartial',
            'OutcomeCompleteRecorded', 'AuditCompleted', 'KnowledgeExtracted',
        ]
        self.assertEqual(events, expected, f"Event sequence mismatch:\n  got: {events}")


class TestImmutability(unittest.TestCase):
    """검증 3: 핵심 필드 변경 차단 + DELETE 차단."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.conn.autocommit = False
        cls.cur = cls.conn.cursor()
        _cleanup(cls.cur)
        cls.conn.commit()

        from repositories.decision_repository import DecisionRepository
        repo = DecisionRepository(_StubDB(cls.conn))
        cid, _ = repo.create_candidate(
            stock_code='000660', observed_at=datetime.now(),
            price=100000.0, trace_id=FIXED_TRACE, created_by='test',
        )
        did = repo.freeze_decision(
            candidate_id=cid, stock_code='000660',
            decision='REJECT', decision_reason_code='SWEEP_MISSING',
            policy_version='SMC_v2.3',
            feature_snapshot={'choch_detected': False, 'sweep_detected': False,
                              'rvol': 1.0, 'atr_pct': 0.9, 'regime': 'Neutral',
                              'rs_score': 55.0, 'risk_score': 0.3, 'expected_rr': 1.5},
            observed_at=datetime.now(), trace_id=FIXED_TRACE, created_by='test',
        )
        cls.decision_id = did

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.conn.close()

    def _try_update(self, sql, params):
        """UPDATE 시도 — EXCEPTION 발생해야 통과."""
        try:
            self.cur.execute(sql, params)
            self.conn.rollback()
            return False  # 차단 안 됨 = 실패
        except Exception:
            self.conn.rollback()
            return True   # 차단 됨 = 성공

    def _try_delete(self, sql, params):
        """DELETE 시도 — Rule이 INSTEAD NOTHING이므로 예외 없지만 행 영향 0이어야 함."""
        try:
            self.cur.execute(sql, params)
            affected = self.cur.rowcount
            self.conn.rollback()
            return affected == 0  # 영향 행 0 = 차단
        except Exception:
            self.conn.rollback()
            return True

    def test_decision_field_immutable(self):
        blocked = self._try_update(
            "UPDATE research.decision_ledger SET decision='PASS' "
            "WHERE decision_id=%s::uuid",
            (self.decision_id,)
        )
        self.assertTrue(blocked, "decision field update was NOT blocked — immutability broken!")

    def test_reason_code_immutable(self):
        blocked = self._try_update(
            "UPDATE research.decision_ledger SET decision_reason_code='PASS' "
            "WHERE decision_id=%s::uuid",
            (self.decision_id,)
        )
        self.assertTrue(blocked, "decision_reason_code update was NOT blocked")

    def test_feature_snapshot_immutable(self):
        blocked = self._try_update(
            "UPDATE research.decision_ledger SET feature_snapshot='{\"hacked\":true}' "
            "WHERE decision_id=%s::uuid",
            (self.decision_id,)
        )
        self.assertTrue(blocked, "feature_snapshot update was NOT blocked")

    def test_policy_version_immutable(self):
        blocked = self._try_update(
            "UPDATE research.decision_ledger SET policy_version='FAKE_v0' "
            "WHERE decision_id=%s::uuid",
            (self.decision_id,)
        )
        self.assertTrue(blocked, "policy_version update was NOT blocked")

    def test_delete_blocked(self):
        blocked = self._try_delete(
            "DELETE FROM research.decision_ledger WHERE decision_id=%s::uuid",
            (self.decision_id,)
        )
        self.assertTrue(blocked, "DELETE was NOT blocked — immutability broken!")

    def test_aa_lifecycle_forward_allowed(self):
        """lifecycle_status 전진 업데이트는 허용되어야 함 (FROZEN → OUTCOME_PENDING)."""
        try:
            self.cur.execute(
                "UPDATE research.decision_ledger SET lifecycle_status='OUTCOME_PENDING' "
                "WHERE decision_id=%s::uuid",
                (self.decision_id,)
            )
            self.conn.commit()
            self.cur.execute(
                "SELECT lifecycle_status FROM research.decision_ledger "
                "WHERE decision_id=%s::uuid", (self.decision_id,)
            )
            self.assertEqual(self.cur.fetchone()[0], 'OUTCOME_PENDING')
        except Exception as e:
            self.fail(f"lifecycle_status forward update (should be allowed) raised: {e}")

    def test_knowledge_extracted_no_revert(self):
        """KNOWLEDGE_EXTRACTED에서 역방향 전이 차단."""
        self.cur.execute(
            "UPDATE research.decision_ledger SET lifecycle_status='KNOWLEDGE_EXTRACTED' "
            "WHERE decision_id=%s::uuid", (self.decision_id,)
        )
        self.conn.commit()
        blocked = self._try_update(
            "UPDATE research.decision_ledger SET lifecycle_status='FROZEN' "
            "WHERE decision_id=%s::uuid",
            (self.decision_id,)
        )
        self.assertTrue(blocked, "Revert from KNOWLEDGE_EXTRACTED was NOT blocked")


class TestEventReplay(unittest.TestCase):
    """검증 4: event_store만으로 Decision Timeline 복원."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.conn.autocommit = False
        cls.cur = cls.conn.cursor()
        _cleanup(cls.cur)
        cls.conn.commit()

        from repositories.decision_repository import DecisionRepository
        repo = DecisionRepository(_StubDB(cls.conn))
        cid, _ = repo.create_candidate(
            stock_code='035720', stock_name='카카오',
            observed_at=datetime.now(), price=55000.0,
            trace_id=FIXED_TRACE, created_by='test',
        )
        did = repo.freeze_decision(
            candidate_id=cid, stock_code='035720',
            decision='REJECT', decision_reason_code='VOLUME_INSUFFICIENT',
            policy_version='SMC_v2.3',
            feature_snapshot={'choch_detected': True, 'sweep_detected': True,
                              'rvol': 0.8, 'atr_pct': 1.5, 'regime': 'Risk-On',
                              'rs_score': 72.0, 'risk_score': 0.35, 'expected_rr': 2.5},
            observed_at=datetime.now(), trace_id=FIXED_TRACE, created_by='test',
        )
        repo.mark_outcome_pending(did, trace_id=FIXED_TRACE)
        repo.record_future_return(did, '035720', '+5D', 55000.0, -2.1, trace_id=FIXED_TRACE)
        repo.complete_outcome(did, trace_id=FIXED_TRACE)
        repo.complete_audit(did, 'RATIONAL', 'GOOD_REJECT', trace_id=FIXED_TRACE)
        cls.decision_id = did

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.conn.close()

    def test_timeline_reconstructed_from_events(self):
        """event_store만 읽어서 Timeline을 복원한다."""
        self.cur.execute("""
            SELECT event_type, entity_type, occurred_at
            FROM research.event_store
            WHERE payload->>'trace_id' = %s
            ORDER BY occurred_at
        """, (FIXED_TRACE,))
        rows = self.cur.fetchall()
        event_types = [r[0] for r in rows]

        EXPECTED_SEQUENCE = [
            'CandidateCreated',
            'DecisionFrozen',
            'OutcomePending',
            'FutureReturnPartial',
            'OutcomeCompleteRecorded',
            'AuditCompleted',
        ]
        self.assertEqual(event_types, EXPECTED_SEQUENCE,
                         f"Timeline mismatch:\n  got: {event_types}")

    def test_entity_ids_consistent(self):
        """event_store의 entity_id가 실제 레코드 ID와 일치한다."""
        self.cur.execute("""
            SELECT entity_type, entity_id::text
            FROM research.event_store
            WHERE payload->>'trace_id' = %s
            ORDER BY occurred_at
        """, (FIXED_TRACE,))
        rows = self.cur.fetchall()

        decision_event_ids = {r[1] for r in rows if r[0] == 'decision'}
        self.assertIn(self.decision_id, decision_event_ids,
                      "decision_id not found in event_store entity_ids")

    def test_outcome_label_from_returns(self):
        self.cur.execute(
            "SELECT outcome_label, return_5d FROM research.decision_outcomes "
            "WHERE decision_id=%s::uuid", (self.decision_id,)
        )
        row = self.cur.fetchone()
        self.assertEqual(row[0], 'GOOD_REJECT', f"Expected GOOD_REJECT (-2.1%), got {row[0]}")


# ─── Research Inspector (보너스) ─────────────────────────────────

def research_inspector(trace_id: str, conn=None) -> None:
    """
    trace_id 하나로 Decision 전체 흐름을 한 화면에 출력.

    Usage:
        python3 tests/test_research_schema.py TR-20260630-000183
    """
    close = False
    if conn is None:
        conn = get_conn()
        close = True
    cur = conn.cursor()

    print(f"\n{'='*60}")
    print(f"  Research Inspector — {trace_id}")
    print(f"{'='*60}")

    cur.execute("""
        SELECT candidate_id, stock_code, stock_name, sector,
               observed_at, price, rs_score, rvol, lifecycle_status
        FROM research.candidates WHERE trace_id=%s ORDER BY observed_at
    """, (trace_id,))
    candidates = cur.fetchall()
    if not candidates:
        print("  [!] No candidates found for this trace_id")
        if close:
            conn.close()
        return

    for c in candidates:
        print(f"\n  CANDIDATE  {c[0]}")
        print(f"    stock    : {c[1]} {c[2] or ''} [{c[3] or '-'}]")
        print(f"    observed : {c[4].strftime('%H:%M:%S') if c[4] else '-'}")
        print(f"    price    : {c[6]:,.0f}  RS={c[6]}  rvol={c[7]}" if c[6] else "")
        print(f"    status   : {c[8]}")

    cur.execute("""
        SELECT d.decision_id, d.stock_code, d.decision, d.decision_reason_code,
               d.policy_version, d.decided_at, d.lifecycle_status,
               d.confidence, d.expected_rr, d.risk_score, d.decision_verdict,
               d.decision_auditor_result->>'quality',
               d.outcome_auditor_result->>'quality'
        FROM research.decision_ledger d WHERE d.trace_id=%s ORDER BY d.decided_at
    """, (trace_id,))
    decisions = cur.fetchall()

    for d in decisions:
        print(f"\n  DECISION   {d[0]}")
        print(f"    stock    : {d[1]}")
        print(f"    decision : {d[2]}  reason={d[3]}")
        print(f"    policy   : {d[4]}")
        print(f"    decided  : {d[5].strftime('%H:%M:%S') if d[5] else '-'}")
        print(f"    status   : {d[6]}")
        if d[7]:
            print(f"    scores   : confidence={d[7]:.2f}  RR={d[8]}  risk={d[9]:.2f}")
        if d[10]:
            print(f"    verdict  : {d[10][:80]}...")
        if d[11]:
            print(f"    audit    : decision={d[11]}  outcome={d[12]}")

        cur.execute("""
            SELECT horizon_label, return_pct FROM research.future_return_events
            WHERE decision_id=%s::uuid ORDER BY recorded_at
        """, (d[0],))
        returns = cur.fetchall()
        if returns:
            ret_str = '  '.join(f"{r[0]}:{r[1]:+.1f}%" for r in returns)
            print(f"    returns  : {ret_str}")

        cur.execute("""
            SELECT outcome_label FROM research.decision_outcomes WHERE decision_id=%s::uuid
        """, (d[0],))
        lbl = cur.fetchone()
        if lbl and lbl[0]:
            print(f"    outcome  : {lbl[0]}")

    print(f"\n  TIMELINE (event_store)")
    cur.execute("""
        SELECT occurred_at, event_type, entity_type, source
        FROM research.event_store
        WHERE payload->>'trace_id'=%s ORDER BY occurred_at
    """, (trace_id,))
    for e in cur.fetchall():
        ts = e[0].strftime('%H:%M:%S.%f')[:12] if e[0] else '-'
        print(f"    {ts}  {e[1]:<35}  [{e[2]}] via {e[3]}")

    print(f"\n{'='*60}\n")
    if close:
        conn.close()


# ─── Entry Point ──────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1].startswith('TR-'):
        research_inspector(sys.argv[1])
    else:
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for cls in [TestDDLValidation, TestLifecycleSmoke, TestImmutability, TestEventReplay]:
            suite.addTests(loader.loadTestsFromTestCase(cls))
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
