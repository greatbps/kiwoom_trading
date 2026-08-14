"""
tests/unit/test_condition_dataset_repository.py — WI-13 Condition Dataset Repository

research.condition_candidates / strategy_monitor_events / strategy_signals에
대한 저장 검증. tests/test_research_schema.py와 동일 안전장치
(utils.database_guard로 테스트 DB가 아니면 스킵)를 사용한다.

실행:
    python3 -m pytest tests/unit/test_condition_dataset_repository.py -v
"""

import os
import sys
import unittest
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import psycopg2

from utils.database_guard import DestructiveOperationBlocked, assert_destructive_allowed
from repositories.condition_dataset_repository import ConditionDatasetRepository

TEST_STOCK = 'ZZTEST99'  # 실거래 종목코드와 겹치지 않는 테스트 전용 마커


def get_conn():
    db_name = os.getenv('POSTGRES_DB', 'trading_system')
    try:
        assert_destructive_allowed(db_name, operation='DELETE condition_dataset test rows')
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

    def _put_conn(self, conn):
        pass


class ConditionDatasetRepositoryTest(unittest.TestCase):

    def setUp(self):
        self.conn = get_conn()
        self.repo = ConditionDatasetRepository(_StubDB(self.conn))
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.conn.close()

    def _cleanup(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                DELETE FROM research.strategy_signals
                WHERE stock_code = %s
            """, (TEST_STOCK,))
            cur.execute("""
                DELETE FROM research.strategy_monitor_events
                WHERE stock_code = %s
            """, (TEST_STOCK,))
            cur.execute("""
                DELETE FROM research.condition_candidates
                WHERE stock_code = %s
            """, (TEST_STOCK,))
        self.conn.commit()

    # ── 1. 정상 INSERT (3개 메서드) ──────────────────────────────

    def test_record_candidate_signal_success(self):
        cid = self.repo.record_candidate(
            observed_at=datetime.now(), stock_code=TEST_STOCK,
            condition_seq=32, condition_name='Momentum',
            condition_sources=[32], stock_name='테스트종목',
        )
        self.assertIsNotNone(cid)

        meid = self.repo.record_monitor_event(
            candidate_id=cid, observed_at=datetime.now(), stock_code=TEST_STOCK,
            condition_seq=32, monitor_name='MomentumMonitor',
            monitor_status='SIGNAL', monitor_state='MOMENTUM_CONFIRMING',
            monitor_result={'reasons': ['price_velocity=50%']},
        )
        self.assertIsNotNone(meid)

        sid = self.repo.record_signal(
            candidate_id=cid, monitor_event_id=meid, observed_at=datetime.now(),
            stock_code=TEST_STOCK, condition_seq=32, strategy_name='Momentum',
            signal_type='MOMENTUM_CONFIRMING', entry_price_reference=10000.0,
            signal_reason='test',
        )
        self.assertIsNotNone(sid)

        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM research.strategy_signals WHERE signal_id = %s::uuid", (sid,))
            self.assertEqual(cur.fetchone()[0], 1)

    # ── 2. Multi-seq 후보 — 각 seq 독립 저장, source 유실 없음 ──

    def test_multi_condition_candidate_independent_rows(self):
        seqs = [32, 33, 37]
        cids = []
        for seq in seqs:
            cid = self.repo.record_candidate(
                observed_at=datetime.now(), stock_code=TEST_STOCK,
                condition_seq=seq, condition_name=f'seq{seq}',
                condition_sources=seqs,
            )
            self.assertIsNotNone(cid)
            cids.append(cid)

        self.assertEqual(len(set(cids)), 3, "seq마다 독립된 candidate_id여야 한다")

        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT condition_seq, condition_sources FROM research.condition_candidates
                WHERE stock_code = %s ORDER BY condition_seq
            """, (TEST_STOCK,))
            rows = cur.fetchall()
        self.assertEqual([r[0] for r in rows], [32, 33, 37])
        for _, sources in rows:
            self.assertEqual(sorted(sources), [32, 33, 37],
                              "각 행의 condition_sources가 동시매칭 전체 seq를 보존해야 한다")

    # ── 3. NO_SIGNAL도 candidate/monitor_event로 저장됨 ──────────

    def test_no_signal_candidate_still_persisted(self):
        cid = self.repo.record_candidate(
            observed_at=datetime.now(), stock_code=TEST_STOCK,
            condition_seq=37, condition_name='Squeeze Momentum Pro',
            condition_sources=[37],
        )
        meid = self.repo.record_monitor_event(
            candidate_id=cid, observed_at=datetime.now(), stock_code=TEST_STOCK,
            condition_seq=37, monitor_name='SqueezeMonitor',
            monitor_status='NO_SIGNAL', monitor_state='SQUEEZE_FAILED',
            monitor_result={'reasons': ['squeeze_on=False']},
        )
        self.assertIsNotNone(meid)

        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT monitor_status FROM research.strategy_monitor_events
                WHERE event_id = %s::uuid
            """, (meid,))
            self.assertEqual(cur.fetchone()[0], 'NO_SIGNAL')
            cur.execute("SELECT COUNT(*) FROM research.strategy_signals WHERE candidate_id = %s::uuid", (cid,))
            self.assertEqual(cur.fetchone()[0], 0, "NO_SIGNAL은 signal 행을 만들지 않는다")

    # ── 4. Monitor 예외 -> ERROR로 기록, 예외가 올라가지 않음 ────

    def test_monitor_error_recorded_without_raising(self):
        cid = self.repo.record_candidate(
            observed_at=datetime.now(), stock_code=TEST_STOCK,
            condition_seq=36, condition_name='VWAP', condition_sources=[36],
        )
        try:
            meid = self.repo.record_monitor_event(
                candidate_id=cid, observed_at=datetime.now(), stock_code=TEST_STOCK,
                condition_seq=36, monitor_name='VWAPMonitor',
                monitor_status='ERROR', monitor_state=None,
                monitor_result=None, monitor_error='ValueError: insufficient data',
            )
        except Exception as e:
            self.fail(f"record_monitor_event이 예외를 올렸다(금지): {e}")
        self.assertIsNotNone(meid)

        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT monitor_status, monitor_error FROM research.strategy_monitor_events
                WHERE event_id = %s::uuid
            """, (meid,))
            status, err = cur.fetchone()
            self.assertEqual(status, 'ERROR')
            self.assertIn('insufficient data', err)

    # ── 5. NOT_IMPLEMENTED seq — signal 행 생성 안 됨(fallback 없음) ──

    def test_not_implemented_seq_no_signal_row(self):
        cid = self.repo.record_candidate(
            observed_at=datetime.now(), stock_code=TEST_STOCK,
            condition_seq=34, condition_name='EOD', condition_sources=[34],
        )
        meid = self.repo.record_monitor_event(
            candidate_id=cid, observed_at=datetime.now(), stock_code=TEST_STOCK,
            condition_seq=34, monitor_name='EODMonitor',
            monitor_status='NOT_IMPLEMENTED', monitor_state='NOT_IMPLEMENTED',
            monitor_result={'data_quality': 'NO_CODE'},
        )
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT monitor_status FROM research.strategy_monitor_events
                WHERE event_id = %s::uuid
            """, (meid,))
            self.assertEqual(cur.fetchone()[0], 'NOT_IMPLEMENTED')
            cur.execute("SELECT COUNT(*) FROM research.strategy_signals WHERE candidate_id = %s::uuid", (cid,))
            self.assertEqual(cur.fetchone()[0], 0)

    # ── 6. 실패 시 예외 없이 None 반환 (repository 계약) ──────────

    def test_record_candidate_invalid_seq_returns_none_not_raises(self):
        # condition_seq CHECK 제약(32~39) 위반 -> INSERT 실패 -> None 반환, 예외 無
        try:
            cid = self.repo.record_candidate(
                observed_at=datetime.now(), stock_code=TEST_STOCK,
                condition_seq=99, condition_name='INVALID', condition_sources=[99],
            )
        except Exception as e:
            self.fail(f"record_candidate이 예외를 올렸다(금지): {e}")
        self.assertIsNone(cid)


if __name__ == '__main__':
    unittest.main()
