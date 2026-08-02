"""
DecisionService 검증 테스트

커버리지:
  1. 전체 rejection lifecycle (begin → reject)
  2. ctx=None 안전성 (no-op 보장)
  3. enabled=false 시 전체 비활성
  4. Feature Flag 조합 (decision_logging=false)
  5. Recording Latency P50/P95/P99 (목표: P95 ≤ 20ms)
  6. Health Check — orphan=0, 지표 정상
  7. EvaluationContext 직렬화

실행:
    python3 tests/test_decision_service.py
    또는 pytest tests/test_decision_service.py -v
"""

import json
import os
import sys
import time
import unittest
from datetime import date, datetime

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


class _StubDB:
    def __init__(self, conn):
        self._conn = conn
    def _get_conn(self):
        return self._conn
    def _put_conn(self, c):
        pass


def _cleanup(cur):
    cur.execute("""
        TRUNCATE research.event_store,
                 research.future_return_events,
                 research.decision_ledger,
                 research.candidates
        RESTART IDENTITY CASCADE
    """)


def _make_service(conn, enabled=True, decision_logging=True):
    """DecisionService를 테스트용 config로 생성."""
    from unittest.mock import patch
    config = {
        'research': {
            'enabled': enabled,
            'decision_logging': decision_logging,
            'event_logging': True,
            'policy_version': 'SMC_v2.3',
            'latency_warn_ms': 15,
            'latency_error_ms': 50,
        }
    }
    with patch('services.decision_service._load_config', return_value=config):
        from services.decision_service import DecisionService
        svc = DecisionService(_StubDB(conn))
    return svc


SAMPLE_FEATURES = {
    'choch_detected': False,
    'choch_grade':    'C',
    'sweep_detected': True,
    'sweep_type':     'buyside',
    'fvg_detected':   False,
    'rvol':           2.1,
    'atr_pct':        1.3,
    'regime':         'Risk-On',
    'rs_score':       88.5,
    'risk_score':     0.35,
    'expected_rr':    2.2,
    'confidence':     0.40,
}


class TestDecisionServiceRejection(unittest.TestCase):
    """Rejection 전체 lifecycle."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.conn.autocommit = False
        cls.cur = cls.conn.cursor()
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.svc = _make_service(cls.conn)

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.conn.close()

    def test_01_begin_evaluation_returns_context(self):
        ctx = self.svc.begin_evaluation(
            symbol='005930', price=75000.0,
            features=SAMPLE_FEATURES,
            stock_name='삼성전자', sector='Semiconductor',
        )
        self.assertIsNotNone(ctx, "begin_evaluation returned None — Candidate 생성 실패")
        self.assertTrue(ctx.is_valid())
        self.assertTrue(ctx.trace_id.startswith('TR-'))
        self.assertEqual(ctx.symbol, '005930')
        self.assertEqual(ctx.policy_version, 'SMC_v2.3')
        self.__class__.ctx = ctx

    def test_02_candidate_in_db(self):
        self.cur.execute(
            "SELECT lifecycle_status FROM research.candidates "
            "WHERE trace_id=%s", (self.ctx.trace_id,)
        )
        row = self.cur.fetchone()
        self.assertIsNotNone(row, "Candidate not in DB")
        self.assertEqual(row[0], 'CREATED')

    def test_03_record_rejection(self):
        ok = self.svc.record_rejection(
            ctx=self.ctx,
            reason_code='CHOCH_MISSING',
            features=SAMPLE_FEATURES,
        )
        self.assertTrue(ok, "record_rejection returned False")

    def test_04_decision_in_db(self):
        self.cur.execute(
            "SELECT decision, decision_reason_code, lifecycle_status "
            "FROM research.decision_ledger WHERE trace_id=%s",
            (self.ctx.trace_id,)
        )
        row = self.cur.fetchone()
        self.assertIsNotNone(row, "Decision not in DB")
        self.assertEqual(row[0], 'REJECT')
        self.assertEqual(row[1], 'CHOCH_MISSING')
        self.assertEqual(row[2], 'FROZEN')

    def test_05_candidate_updated_to_evaluated(self):
        self.cur.execute(
            "SELECT lifecycle_status FROM research.candidates "
            "WHERE trace_id=%s", (self.ctx.trace_id,)
        )
        self.assertEqual(self.cur.fetchone()[0], 'EVALUATED')

    def test_06_events_recorded(self):
        self.cur.execute(
            "SELECT event_type FROM research.event_store "
            "WHERE payload->>'trace_id'=%s ORDER BY occurred_at",
            (self.ctx.trace_id,)
        )
        events = [r[0] for r in self.cur.fetchall()]
        self.assertIn('CandidateCreated', events)
        self.assertIn('DecisionFrozen', events)

    def test_07_trace_id_consistent(self):
        """trace_id가 candidate → decision → event 전체에 동일하게 전달된다."""
        trace = self.ctx.trace_id

        self.cur.execute(
            "SELECT COUNT(*) FROM research.candidates WHERE trace_id=%s", (trace,))
        self.assertEqual(self.cur.fetchone()[0], 1)

        self.cur.execute(
            "SELECT COUNT(*) FROM research.decision_ledger WHERE trace_id=%s", (trace,))
        self.assertEqual(self.cur.fetchone()[0], 1)

        self.cur.execute(
            "SELECT COUNT(*) FROM research.event_store "
            "WHERE payload->>'trace_id'=%s", (trace,))
        self.assertGreaterEqual(self.cur.fetchone()[0], 2)


class TestDecisionServiceSafety(unittest.TestCase):
    """ctx=None 안전성 + Feature Flag."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.conn.autocommit = False

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_none_ctx_record_rejection_noop(self):
        svc = _make_service(self.conn)
        ok = svc.record_rejection(None, 'CHOCH_MISSING', SAMPLE_FEATURES)
        self.assertFalse(ok, "record_rejection(None) should return False")

    def test_none_ctx_record_acceptance_noop(self):
        svc = _make_service(self.conn)
        ok = svc.record_acceptance(None, SAMPLE_FEATURES)
        self.assertFalse(ok)

    def test_none_ctx_record_order_noop(self):
        svc = _make_service(self.conn)
        ok = svc.record_order(None)
        self.assertFalse(ok)

    def test_none_ctx_record_exit_noop(self):
        svc = _make_service(self.conn)
        ok = svc.record_exit(None)
        self.assertFalse(ok)

    def test_disabled_begin_evaluation_returns_none(self):
        svc = _make_service(self.conn, enabled=False)
        ctx = svc.begin_evaluation('005930', 75000.0)
        self.assertIsNone(ctx, "disabled service must return None")

    def test_decision_logging_false_noop(self):
        svc = _make_service(self.conn, enabled=True, decision_logging=False)
        ctx = svc.begin_evaluation('005930', 75000.0)
        self.assertIsNone(ctx, "decision_logging=false must return None from begin_evaluation")

    def test_reason_normalization(self):
        svc = _make_service(self.conn)
        cases = [
            ('SMC_NO_SIG',       'CHOCH_MISSING'),
            ('GLOBAL_GATE',      'GLOBAL_GATE_BLOCKED'),
            ('STOCK_GATE',       'STOCK_GATE_BLOCKED'),
            ('LOW_VOLUME',       'VOLUME_INSUFFICIENT'),
            ('totally_unknown',  'OTHER'),
            ('CHOCH_MISSING',    'CHOCH_MISSING'),  # already valid
        ]
        for raw, expected in cases:
            got = svc._normalize_reason(raw)
            self.assertEqual(got, expected, f"normalize({raw!r}) = {got!r}, want {expected!r}")


class TestDecisionServiceLatency(unittest.TestCase):
    """
    Recording Latency KPI 검증.
    목표: P95 ≤ 20ms, P99 ≤ 50ms
    """

    N_SAMPLES = 30

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.conn.autocommit = False
        cls.cur = cls.conn.cursor()
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.svc = _make_service(cls.conn)
        cls.latencies_ms: list = []

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.conn.close()

    def test_latency_n_samples(self):
        """N_SAMPLES 회 rejection 기록 후 P50/P95/P99 측정."""
        latencies = []
        for i in range(self.N_SAMPLES):
            t0 = time.perf_counter()
            ctx = self.svc.begin_evaluation(
                symbol=f'00000{i % 10}',
                price=50000.0 + i * 100,
                features=SAMPLE_FEATURES,
                stock_name=f'TestStock{i}',
            )
            if ctx:
                self.svc.record_rejection(ctx, 'CHOCH_MISSING', SAMPLE_FEATURES)
            elapsed = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed)

        latencies.sort()
        n = len(latencies)
        p50 = latencies[int(n * 0.50)]
        p95 = latencies[int(n * 0.95)]
        p99 = latencies[int(n * 0.99)]

        print(f"\n  Latency (n={n}): P50={p50:.1f}ms  P95={p95:.1f}ms  P99={p99:.1f}ms  Max={latencies[-1]:.1f}ms")

        self.assertLessEqual(p95, 20.0,
            f"P95 {p95:.1f}ms > 20ms (KPI 위반) — Trading latency increase 초과")
        self.assertLessEqual(p99, 50.0,
            f"P99 {p99:.1f}ms > 50ms")

    def test_db_recording_latency_percentiles(self):
        """DB timestamps 기반 Recording Latency (recorded_at - decided_at)."""
        self.cur.execute("""
            SELECT
                PERCENTILE_CONT(0.50) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM (recorded_at - decided_at)) * 1000
                ) AS p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM (recorded_at - decided_at)) * 1000
                ) AS p95,
                PERCENTILE_CONT(0.99) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM (recorded_at - decided_at)) * 1000
                ) AS p99,
                COUNT(*) AS n
            FROM research.decision_ledger
            WHERE decided_at >= NOW() - INTERVAL '5 minutes'
        """)
        row = self.cur.fetchone()
        p50, p95, p99, n = row
        if n and n > 0:
            print(f"\n  DB Recording Latency (n={n}): "
                  f"P50={p50:.1f}ms  P95={p95:.1f}ms  P99={p99:.1f}ms")
            self.assertLessEqual(float(p95), 20.0,
                f"DB P95 {p95:.1f}ms > 20ms (KPI 위반)")


class TestHealthCheck(unittest.TestCase):
    """Health Check — orphan=0, KPI 확인."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_conn()
        cls.conn.autocommit = False
        cls.cur = cls.conn.cursor()
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.svc = _make_service(cls.conn)

        # 정상 데이터 생성
        for i in range(5):
            ctx = cls.svc.begin_evaluation(
                symbol=f'00593{i}', price=70000.0 + i * 1000,
                features=SAMPLE_FEATURES, stock_name=f'TestStock{i}',
            )
            if ctx:
                cls.svc.record_rejection(ctx, 'CHOCH_MISSING', SAMPLE_FEATURES)

    @classmethod
    def tearDownClass(cls):
        _cleanup(cls.cur)
        cls.conn.commit()
        cls.conn.close()

    def test_health_check_runs(self):
        from analysis.decision_health_check import run_health_check
        report = run_health_check(date.today())
        self.assertIn('kpi', report)
        self.assertIn('orphans', report)

    def test_no_orphans(self):
        from analysis.decision_health_check import run_health_check
        report = run_health_check(date.today())
        orphans = report['orphans']
        total = sum(orphans.values())
        self.assertEqual(total, 0,
            f"Orphan records found: {orphans}")

    def test_no_missing_trace_ids(self):
        from analysis.decision_health_check import run_health_check
        report = run_health_check(date.today())
        self.assertEqual(report['missing_trace_ids'], 0)

    def test_no_broken_lifecycle(self):
        from analysis.decision_health_check import run_health_check
        report = run_health_check(date.today())
        self.assertEqual(report['broken_lifecycle'], 0)

    def test_no_impossible_transitions(self):
        from analysis.decision_health_check import run_health_check
        report = run_health_check(date.today())
        impossibles = report['impossible_transitions']
        self.assertEqual(len(impossibles), 0,
            f"Impossible transitions found: {[i['violation'] for i in impossibles]}")

    def test_recording_success_100pct(self):
        from analysis.decision_health_check import run_health_check
        report = run_health_check(date.today())
        pct = report['kpi']['recording_success_pct']
        self.assertEqual(pct, 100.0,
            f"Recording success {pct}% < 100%")


class TestEvaluationContext(unittest.TestCase):
    """EvaluationContext 동작 검증."""

    def test_bool_valid(self):
        from services.evaluation_context import EvaluationContext
        ctx = EvaluationContext(
            trace_id='TR-TEST-000001',
            candidate_id='abc-def',
            symbol='005930',
            observed_at=datetime.now(),
            policy_version='SMC_v2.3',
        )
        self.assertTrue(bool(ctx))

    def test_bool_empty_trace(self):
        from services.evaluation_context import EvaluationContext
        ctx = EvaluationContext(
            trace_id='',
            candidate_id='abc-def',
            symbol='005930',
            observed_at=datetime.now(),
            policy_version='SMC_v2.3',
        )
        self.assertFalse(bool(ctx))

    def test_age_seconds(self):
        from services.evaluation_context import EvaluationContext
        ctx = EvaluationContext(
            trace_id='TR-TEST-000001',
            candidate_id='abc',
            symbol='005930',
            observed_at=datetime.now(),
            policy_version='SMC_v2.3',
        )
        time.sleep(0.01)
        self.assertGreater(ctx.age_seconds(), 0)


if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestDecisionServiceRejection,
        TestDecisionServiceSafety,
        TestDecisionServiceLatency,
        TestHealthCheck,
        TestEvaluationContext,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
