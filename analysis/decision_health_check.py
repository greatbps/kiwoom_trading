"""
Decision Ledger Health Check

장 종료 후 자동 실행 — Decision Ledger 운영 안정성 확인.

실행:
    python3 -m analysis.decision_health_check          # 오늘
    python3 -m analysis.decision_health_check --date 2026-07-01
    python3 -m analysis.decision_health_check --json   # JSON 출력
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

logger = logging.getLogger(__name__)


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── Metric Collectors ───────────────────────────────────────────

def _daily_counts(cur, target_date: date) -> Dict[str, int]:
    d = str(target_date)
    cur.execute(
        "SELECT COUNT(*) FROM research.candidates WHERE observed_at::date=%s", (d,))
    candidates = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM research.decision_ledger WHERE decided_at::date=%s", (d,))
    decisions = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM research.event_store WHERE occurred_at::date=%s", (d,))
    events = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM research.future_return_events WHERE recorded_at::date=%s", (d,))
    returns = cur.fetchone()[0]

    return {
        'candidates': candidates,
        'decisions': decisions,
        'events': events,
        'future_returns': returns,
    }


def _orphan_checks(cur, target_date: date) -> Dict[str, int]:
    """
    5가지 Orphan 검사. 모두 0이어야 정상.
    """
    d = str(target_date)

    # 1. Candidate without Decision
    cur.execute("""
        SELECT COUNT(*) FROM research.candidates c
        WHERE c.observed_at::date = %s
          AND NOT EXISTS (
              SELECT 1 FROM research.decision_ledger d
              WHERE d.candidate_id = c.candidate_id
          )
    """, (d,))
    cand_no_decision = cur.fetchone()[0]

    # 2. Decision without Event
    cur.execute("""
        SELECT COUNT(*) FROM research.decision_ledger d
        WHERE d.decided_at::date = %s
          AND NOT EXISTS (
              SELECT 1 FROM research.event_store e
              WHERE e.entity_id = d.decision_id
                AND e.entity_type = 'decision'
          )
    """, (d,))
    decision_no_event = cur.fetchone()[0]

    # 3. Event (candidate entity) without Candidate
    cur.execute("""
        SELECT COUNT(*) FROM research.event_store e
        WHERE e.occurred_at::date = %s
          AND e.entity_type = 'candidate'
          AND NOT EXISTS (
              SELECT 1 FROM research.candidates c
              WHERE c.candidate_id = e.entity_id
          )
    """, (d,))
    event_no_candidate = cur.fetchone()[0]

    # 4. Future Return without Decision
    cur.execute("""
        SELECT COUNT(*) FROM research.future_return_events fr
        WHERE fr.recorded_at::date = %s
          AND NOT EXISTS (
              SELECT 1 FROM research.decision_ledger d
              WHERE d.decision_id = fr.decision_id
          )
    """, (d,))
    return_no_decision = cur.fetchone()[0]

    # 5. Duplicate Trace IDs
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT trace_id FROM research.candidates
            WHERE observed_at::date = %s
              AND trace_id IS NOT NULL
            GROUP BY trace_id
            HAVING COUNT(*) > 1
        ) t
    """, (d,))
    duplicate_trace = cur.fetchone()[0]

    return {
        'candidate_without_decision': cand_no_decision,
        'decision_without_event':     decision_no_event,
        'event_without_candidate':    event_no_candidate,
        'return_without_decision':    return_no_decision,
        'duplicate_trace':            duplicate_trace,
    }


def _lifecycle_broken(cur, target_date: date) -> int:
    """유효하지 않은 lifecycle_status 조합 탐지."""
    d = str(target_date)
    cur.execute("""
        SELECT COUNT(*) FROM research.decision_ledger
        WHERE decided_at::date = %s
          AND lifecycle_status NOT IN (
              'FROZEN','EXECUTED','EXECUTION_FAILED','OUTCOME_PENDING',
              'OUTCOME_RECORDED','AUDIT_COMPLETED','KNOWLEDGE_EXTRACTED'
          )
    """, (d,))
    return cur.fetchone()[0]


def _missing_trace_ids(cur, target_date: date) -> int:
    d = str(target_date)
    cur.execute(
        "SELECT COUNT(*) FROM research.candidates "
        "WHERE observed_at::date=%s AND trace_id IS NULL", (d,))
    return cur.fetchone()[0]


def _recording_latency(cur, target_date: date) -> Dict[str, Optional[float]]:
    """
    Recording Latency = recorded_at - decided_at (DB 기록 지연).
    거래 흐름에 추가되는 실제 레이턴시.
    """
    d = str(target_date)
    cur.execute("""
        SELECT
            PERCENTILE_CONT(0.50) WITHIN GROUP (
                ORDER BY EXTRACT(epoch FROM (recorded_at - decided_at)) * 1000
            ) AS p50_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(epoch FROM (recorded_at - decided_at)) * 1000
            ) AS p95_ms,
            PERCENTILE_CONT(0.99) WITHIN GROUP (
                ORDER BY EXTRACT(epoch FROM (recorded_at - decided_at)) * 1000
            ) AS p99_ms,
            MAX(EXTRACT(epoch FROM (recorded_at - decided_at)) * 1000) AS max_ms,
            COUNT(*) AS sample_count
        FROM research.decision_ledger
        WHERE decided_at::date = %s
          AND recorded_at IS NOT NULL
          AND decided_at IS NOT NULL
    """, (d,))
    row = cur.fetchone()
    if row is None or row[4] == 0:
        return {'p50_ms': None, 'p95_ms': None, 'p99_ms': None,
                'max_ms': None, 'sample_count': 0}
    return {
        'p50_ms':       round(float(row[0]), 2) if row[0] else None,
        'p95_ms':       round(float(row[1]), 2) if row[1] else None,
        'p99_ms':       round(float(row[2]), 2) if row[2] else None,
        'max_ms':       round(float(row[3]), 2) if row[3] else None,
        'sample_count': int(row[4]),
    }


def _reject_reason_breakdown(cur, target_date: date) -> List[Dict]:
    """거절 사유별 집계."""
    d = str(target_date)
    cur.execute("""
        SELECT decision_reason_code, COUNT(*) AS cnt
        FROM research.decision_ledger
        WHERE decided_at::date = %s
          AND decision = 'REJECT'
        GROUP BY decision_reason_code
        ORDER BY cnt DESC
    """, (d,))
    return [{'reason': r[0], 'count': r[1]} for r in cur.fetchall()]


def _insert_failure_rate(cur, target_date: date) -> Dict[str, Any]:
    """
    INSERT 실패율 = CandidateCreated event가 누락된 Candidate 비율.

    Candidate 행은 존재하지만 event_store에 CandidateCreated가 없으면
    INSERT가 실패했거나 이벤트 기록이 롤백된 것이다.

    target: 0.0% (허용 실패 없음)
    """
    d = str(target_date)
    cur.execute("""
        SELECT
            COUNT(c.candidate_id)                                     AS total,
            COUNT(e.entity_id)                                        AS with_event,
            COUNT(c.candidate_id) - COUNT(e.entity_id)                AS missing
        FROM research.candidates c
        LEFT JOIN research.event_store e
            ON e.entity_id = c.candidate_id
           AND e.entity_type = 'candidate'
           AND e.event_type  = 'CandidateCreated'
        WHERE c.observed_at::date = %s
    """, (d,))
    row = cur.fetchone()
    total, with_event, missing = int(row[0]), int(row[1]), int(row[2])
    rate = round(missing / total * 100, 2) if total > 0 else 0.0
    return {'total': total, 'with_event': with_event, 'missing': missing, 'rate_pct': rate}


def _persistence_failure_count(cur, target_date: date) -> Dict[str, Any]:
    """
    [Pipeline Health, 2026-08-17] research.event_store에 명시적으로 기록된
    PersistenceFailure 이벤트 집계(repositories/decision_repository.py
    _record_persistence_failure()). 로그 문자열(grep "create_candidate failed")이
    아니라 구조화된 이벤트를 센다 — 메시지 형식이 바뀌어도 안 놓친다.

    _insert_failure_rate()와의 차이: 그건 "candidates 행은 존재하는데 이벤트가
    없는" 부분 실패만 잡는다(분모=candidates 행 수라서 행 자체가 하나도 안
    만들어진 완전 실패는 분모가 0이 되어 0%로 위장됨 — 2026-08-11 이후 사흘간
    실제로 이렇게 놓쳤다). 이 함수는 행 존재 여부와 무관하게 실패 시도 자체를
    센다.
    """
    d = str(target_date)
    cur.execute("""
        SELECT payload->>'failure_type' AS failure_type, COUNT(*) AS n
        FROM research.event_store
        WHERE event_type = 'PersistenceFailure'
          AND occurred_at::date = %s
        GROUP BY payload->>'failure_type'
    """, (d,))
    by_type: Dict[str, int] = {row[0] or 'UNKNOWN_PERSISTENCE': int(row[1]) for row in cur.fetchall()}
    return {
        'total':                     sum(by_type.values()),
        'candidate_insert_failures': by_type.get('CANDIDATE_INSERT', 0),
        'decision_insert_failures':  by_type.get('DECISION_INSERT', 0),
        'other_failures':            sum(v for k, v in by_type.items()
                                          if k not in ('CANDIDATE_INSERT', 'DECISION_INSERT')),
        'by_type':                   by_type,
    }


def _pool_usage(cur) -> Dict[str, Any]:
    """
    PostgreSQL 서버 전체 커넥션 사용률.
    실제 앱 풀 크기가 아닌 서버 레벨 사용률로 이상 징후를 감지한다.
    300건/일은 사실상 부하가 없으므로 확인 정도로만 사용.
    target: < 80%
    """
    cur.execute("""
        SELECT
            COUNT(*)                                                 AS active_conn,
            (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_conn
        FROM pg_stat_activity
        WHERE datname = current_database()
    """)
    row = cur.fetchone()
    active, max_conn = int(row[0]), int(row[1])
    usage_pct = round(active / max_conn * 100, 1) if max_conn > 0 else 0.0
    return {'active_connections': active, 'max_connections': max_conn, 'usage_pct': usage_pct}


def _trace_gap_check(cur, target_date: date) -> List[Dict]:
    """
    Lifecycle 단계 누락 Trace 탐지.
    Stage 순서: CANDIDATE → DECISION → ORDER(EXECUTED) → FILL(OUTCOME_PENDING) → EXIT(OUTCOME_RECORDED+)

    디버깅 시 가장 먼저 확인하는 항목.
    운영 중 Stage가 추가될수록 탐지 범위가 자동 확장된다.
    """
    d = str(target_date)
    gaps: List[Dict] = []

    # Gap 1: Candidate 생성 후 30분이 지났으나 Decision 없음
    cur.execute("""
        SELECT c.trace_id, c.stock_code, c.observed_at, 'CANDIDATE_NO_DECISION' AS gap_type
        FROM research.candidates c
        WHERE c.observed_at::date = %s
          AND c.lifecycle_status = 'CREATED'
          AND c.observed_at < NOW() - INTERVAL '30 minutes'
          AND NOT EXISTS (
              SELECT 1 FROM research.decision_ledger d
              WHERE d.candidate_id = c.candidate_id
          )
        ORDER BY c.observed_at DESC
        LIMIT 20
    """, (d,))
    for r in cur.fetchall():
        gaps.append({
            'gap_type': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
        })

    # Gap 2: PASS Decision이 8시간 이상 FROZEN에서 진행 안 됨 (주문 연결 누락)
    cur.execute("""
        SELECT d.trace_id, d.stock_code, d.decided_at, 'PASS_DECISION_UNEXECUTED' AS gap_type
        FROM research.decision_ledger d
        WHERE d.decided_at::date = %s
          AND d.decision = 'PASS'
          AND d.lifecycle_status = 'FROZEN'
          AND d.decided_at < NOW() - INTERVAL '8 hours'
        ORDER BY d.decided_at DESC
        LIMIT 20
    """, (d,))
    for r in cur.fetchall():
        gaps.append({
            'gap_type': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
        })

    # Gap 3: EXECUTED (주문 체결) 이후 24시간 지나도 OUTCOME_PENDING 미진입
    cur.execute("""
        SELECT d.trace_id, d.stock_code, d.executed_at, 'EXECUTED_NO_OUTCOME' AS gap_type
        FROM research.decision_ledger d
        WHERE d.decided_at::date = %s
          AND d.lifecycle_status = 'EXECUTED'
          AND d.executed_at IS NOT NULL
          AND d.executed_at < NOW() - INTERVAL '24 hours'
        ORDER BY d.executed_at DESC
        LIMIT 20
    """, (d,))
    for r in cur.fetchall():
        gaps.append({
            'gap_type': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
        })

    # Gap 4: OUTCOME_RECORDED 이후 7일 지나도 AUDIT 없음
    cur.execute("""
        SELECT d.trace_id, d.stock_code, d.decided_at, 'OUTCOME_NO_AUDIT' AS gap_type
        FROM research.decision_ledger d
        WHERE d.lifecycle_status = 'OUTCOME_RECORDED'
          AND d.decided_at < NOW() - INTERVAL '7 days'
        ORDER BY d.decided_at DESC
        LIMIT 20
    """)
    for r in cur.fetchall():
        gaps.append({
            'gap_type': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
        })

    return gaps


def _impossible_transitions(cur) -> List[Dict]:
    """
    의미상 절대 발생해서는 안 되는 상태 조합 탐지.

    Trace Gap (단계 누락) 과의 차이:
      - Trace Gap  : 있어야 할 단계가 없는 것
      - Impossible : 있어서는 안 될 상태가 존재하는 것

    원인: 중복 실행, 버그, Race Condition, 수동 DB 조작 중 하나.
    발생 즉시 조사 필요.
    """
    impossibles: List[Dict] = []

    # I-1: REJECT 결정이 EXECUTED 상태 — 거절된 결정은 절대 실행될 수 없다
    cur.execute("""
        SELECT trace_id, stock_code, decided_at,
               'REJECT_EXECUTED' AS violation
        FROM research.decision_ledger
        WHERE decision = 'REJECT'
          AND lifecycle_status = 'EXECUTED'
        ORDER BY decided_at DESC LIMIT 20
    """)
    for r in cur.fetchall():
        impossibles.append({
            'violation': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
            'description': 'REJECT decision reached EXECUTED status',
        })

    # I-2: PASS 결정이 OUTCOME_PENDING 인데 executed_at 없음
    #      (EXECUTED를 건너뛰고 OUTCOME으로 이동 — 주문 연결 누락)
    cur.execute("""
        SELECT trace_id, stock_code, decided_at,
               'PASS_OUTCOME_WITHOUT_EXECUTION' AS violation
        FROM research.decision_ledger
        WHERE decision = 'PASS'
          AND lifecycle_status IN ('OUTCOME_PENDING', 'OUTCOME_RECORDED')
          AND executed_at IS NULL
        ORDER BY decided_at DESC LIMIT 20
    """)
    for r in cur.fetchall():
        impossibles.append({
            'violation': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
            'description': 'PASS reached OUTCOME without execution timestamp',
        })

    # I-3: AUDIT_COMPLETED 인데 auditor 결과 없음
    #      (Audit 완료 처리됐지만 실제 결과가 기록 안 됨)
    cur.execute("""
        SELECT trace_id, stock_code, decided_at,
               'AUDIT_COMPLETED_NO_RESULT' AS violation
        FROM research.decision_ledger
        WHERE lifecycle_status = 'AUDIT_COMPLETED'
          AND decision_auditor_result IS NULL
        ORDER BY decided_at DESC LIMIT 20
    """)
    for r in cur.fetchall():
        impossibles.append({
            'violation': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
            'description': 'AUDIT_COMPLETED but decision_auditor_result is NULL',
        })

    # I-4: OUTCOME_RECORDED 인데 future_return_events 전혀 없음
    #      (수익률 기록 없이 Outcome 완료 처리 — 데이터 누락)
    #      ※ D+5 이상 지난 기록만 검사 — returns_collector가 D+1/D+5 시점에 채우므로
    #        당일/익일 청산 건에서 false positive 방지
    cur.execute("""
        SELECT d.trace_id, d.stock_code, d.decided_at,
               'OUTCOME_RECORDED_NO_RETURNS' AS violation
        FROM research.decision_ledger d
        WHERE d.lifecycle_status = 'OUTCOME_RECORDED'
          AND d.exited_at < NOW() - INTERVAL '7 days'
          AND NOT EXISTS (
              SELECT 1 FROM research.future_return_events fr
              WHERE fr.decision_id = d.decision_id
          )
        ORDER BY d.decided_at DESC LIMIT 20
    """)
    for r in cur.fetchall():
        impossibles.append({
            'violation': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
            'description': 'OUTCOME_RECORDED but no future_return_events exist',
        })

    # I-5: EXECUTED 인데 executed_at 없음 — 타임스탬프 일관성
    cur.execute("""
        SELECT trace_id, stock_code, decided_at,
               'EXECUTED_NO_TIMESTAMP' AS violation
        FROM research.decision_ledger
        WHERE lifecycle_status = 'EXECUTED'
          AND executed_at IS NULL
        ORDER BY decided_at DESC LIMIT 20
    """)
    for r in cur.fetchall():
        impossibles.append({
            'violation': r[3], 'trace_id': r[0],
            'stock_code': r[1], 'ts': str(r[2]),
            'description': 'lifecycle=EXECUTED but executed_at is NULL',
        })

    return impossibles


# ─── Report Builder ───────────────────────────────────────────────

def _pass_anomaly(cur, target_date: date) -> Dict[str, Any]:
    """
    오늘 PASS 건수 vs 직전 7일 평균 비교.
    급감(< 30%) 또는 급증(> 300%)이면 anomaly 플래그.

    PASS=0  이 며칠째 이면 버그 또는 조건 과도 강화 신호.
    PASS가 갑자기 3배 이상이면 게이트 비활성화 버그 신호.
    """
    d = str(target_date)

    # 오늘 PASS
    cur.execute("""
        SELECT COUNT(*) FROM research.decision_ledger
        WHERE decision = 'PASS' AND decided_at::date = %s
    """, (d,))
    today_pass = int(cur.fetchone()[0])

    # 직전 7 거래일 (오늘 제외) PASS 평균
    cur.execute("""
        SELECT decided_at::date AS dt, COUNT(*) AS cnt
        FROM research.decision_ledger
        WHERE decision = 'PASS'
          AND decided_at::date < %s
          AND decided_at::date >= %s::date - INTERVAL '14 days'
        GROUP BY dt
        ORDER BY dt DESC
        LIMIT 7
    """, (d, d))
    rows = cur.fetchall()
    history = [int(r[1]) for r in rows]   # 최근 7일 (있는 만큼)
    avg_7d = round(sum(history) / len(history), 1) if history else None

    # 이상 판정
    anomaly = None
    if avg_7d is not None and avg_7d > 0:
        ratio = today_pass / avg_7d
        if ratio < 0.3:
            anomaly = f'SUDDEN_DROP (오늘={today_pass}, 7일평균={avg_7d:.1f}, 비율={ratio:.0%})'
        elif ratio > 3.0:
            anomaly = f'SUDDEN_SPIKE (오늘={today_pass}, 7일평균={avg_7d:.1f}, 비율={ratio:.0%})'
    elif avg_7d == 0 and today_pass > 5:
        anomaly = f'UNEXPECTED_SPIKE (7일평균≈0, 오늘={today_pass})'

    return {
        'today': today_pass,
        'avg_7d': avg_7d,
        'history_days': len(history),
        'anomaly': anomaly,
    }


def run_health_check(target_date: Optional[date] = None) -> Dict[str, Any]:
    target_date = target_date or date.today()
    conn = _get_conn()
    try:
        cur = conn.cursor()

        counts        = _daily_counts(cur, target_date)
        orphans       = _orphan_checks(cur, target_date)
        broken        = _lifecycle_broken(cur, target_date)
        missing       = _missing_trace_ids(cur, target_date)
        latency       = _recording_latency(cur, target_date)
        reasons       = _reject_reason_breakdown(cur, target_date)
        insert_fail   = _insert_failure_rate(cur, target_date)
        persist_fail  = _persistence_failure_count(cur, target_date)
        pool          = _pool_usage(cur)
        gaps          = _trace_gap_check(cur, target_date)
        impossibles   = _impossible_transitions(cur)
        pass_anomaly  = _pass_anomaly(cur, target_date)

        total_orphan  = sum(orphans.values())
        latency_ok    = (latency['p95_ms'] or 0) <= 20.0
        integrity_ok  = (total_orphan == 0 and broken == 0 and missing == 0)

        # Recording Success Rate (samples recorded / candidates created)
        recording_success = (
            round(counts['decisions'] / counts['candidates'] * 100, 1)
            if counts['candidates'] > 0 else 100.0
        )

        return {
            'date':                   str(target_date),
            'generated_at':           datetime.now().isoformat(timespec='seconds'),
            'counts':                 counts,
            'orphans':                orphans,
            'broken_lifecycle':       broken,
            'missing_trace_ids':      missing,
            'latency':                latency,
            'reject_reasons':         reasons,
            'insert_failure':         insert_fail,
            'persistence_failure':    persist_fail,
            'pool_usage':             pool,
            'trace_gaps':             gaps,
            'impossible_transitions': impossibles,
            'pass_anomaly':           pass_anomaly,
            'kpi': {
                'recording_success_pct':   recording_success,
                'latency_p95_ms':          latency['p95_ms'],
                'latency_p99_ms':          latency['p99_ms'],
                'total_orphans':           total_orphan,
                'insert_failure_pct':      insert_fail['rate_pct'],
                'persistence_failures':    persist_fail['total'],
                'candidate_persist_failures': persist_fail['candidate_insert_failures'],
                'decision_persist_failures':  persist_fail['decision_insert_failures'],
                'pool_usage_pct':          pool['usage_pct'],
                'total_trace_gaps':        len(gaps),
                'total_impossibles':       len(impossibles),
                'integrity_ok':            (
                    integrity_ok
                    and len(gaps) == 0
                    and len(impossibles) == 0
                    and insert_fail['rate_pct'] == 0.0
                    and persist_fail['total'] == 0
                ),
                'latency_ok':              latency_ok,
                'pass_today':              pass_anomaly['today'],
                'pass_avg_7d':             pass_anomaly['avg_7d'],
                'pass_anomaly':            pass_anomaly['anomaly'],
            },
        }
    finally:
        conn.close()


# ─── Printer ─────────────────────────────────────────────────────

def print_report(report: Dict[str, Any]) -> None:
    d = report['date']
    kpi = report['kpi']
    counts = report['counts']
    orphans = report['orphans']
    lat = report['latency']

    def status(ok: bool) -> str:
        return 'OK  ' if ok else 'WARN'

    print(f"\n{'='*60}")
    print(f"  Decision Ledger Health Check — {d}")
    print(f"  Generated: {report['generated_at']}")
    print(f"{'='*60}")

    print(f"\n  DAILY COUNTS")
    print(f"    Candidates      : {counts['candidates']:>6}")
    print(f"    Decisions       : {counts['decisions']:>6}")
    print(f"    Events          : {counts['events']:>6}")
    print(f"    Future Returns  : {counts['future_returns']:>6}")

    rec_pct = kpi['recording_success_pct']
    rec_ok  = rec_pct >= 99.0
    print(f"\n  KPI")
    print(f"    [{status(rec_ok)}] Recording Success     : {rec_pct:.1f}%  (target≥99%)")

    p95 = kpi['latency_p95_ms']
    p99 = kpi['latency_p99_ms']
    lat_ok = kpi['latency_ok']
    p95_str = f"{p95:.1f}ms" if p95 is not None else "N/A"
    p99_str = f"{p99:.1f}ms" if p99 is not None else "N/A"
    print(f"    [{status(lat_ok)}] Latency P95           : {p95_str}  (target≤20ms)")
    print(f"    [    ] Latency P99           : {p99_str}")

    if lat['sample_count'] > 0:
        p50_str = f"{lat['p50_ms']:.1f}ms" if lat['p50_ms'] is not None else "N/A"
        max_str = f"{lat['max_ms']:.1f}ms" if lat['max_ms'] is not None else "N/A"
        print(f"    [    ] Latency P50/Max     : {p50_str} / {max_str}  (n={lat['sample_count']})")

    total_orphan = kpi['total_orphans']
    total_gaps   = kpi['total_trace_gaps']
    total_impos  = kpi['total_impossibles']
    ins_fail     = kpi['insert_failure_pct']
    pool_use     = kpi['pool_usage_pct']
    int_ok       = kpi['integrity_ok']
    print(f"    [{status(int_ok)}] Orphan Records        : {total_orphan}  (target=0)")
    print(f"    [{status(report['broken_lifecycle']==0)}] Broken Lifecycle      : {report['broken_lifecycle']}")
    print(f"    [{status(report['missing_trace_ids']==0)}] Missing Trace IDs    : {report['missing_trace_ids']}")
    print(f"    [{status(total_gaps==0)}] Trace Gaps           : {total_gaps}  (target=0)")
    print(f"    [{status(total_impos==0)}] Impossible Transitions: {total_impos}  (target=0)")
    print(f"    [{status(ins_fail==0.0)}] INSERT Failure Rate  : {ins_fail:.1f}%  (target=0%)")

    persist_total = kpi['persistence_failures']
    print(f"    [{status(persist_total==0)}] Persistence Failures  : {persist_total}  (target=0)"
          f"  (Candidate={kpi['candidate_persist_failures']} Decision={kpi['decision_persist_failures']})")
    print(f"    [{'OK  ' if pool_use < 80 else 'WARN'}] DB Pool Usage        : {pool_use:.0f}%  (warn≥80%)")

    if total_orphan > 0:
        print(f"\n  ORPHAN DETAIL (requires investigation)")
        for k, v in orphans.items():
            if v > 0:
                lbl = k.replace('_', ' ').title()
                print(f"    ⚠️  {lbl:35s} : {v}")

    if total_gaps > 0:
        print(f"\n  TRACE GAPS (lifecycle 단계 누락)")
        by_type: Dict[str, int] = {}
        for g in report['trace_gaps']:
            by_type[g['gap_type']] = by_type.get(g['gap_type'], 0) + 1
        for gap_type, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"    ⚠️  {gap_type:<40} : {cnt}")
        print(f"    → python3 -m analysis.research_inspector --trace <TR-ID>")

    if total_impos > 0:
        print(f"\n  IMPOSSIBLE TRANSITIONS (즉시 조사 필요)")
        by_v: Dict[str, int] = {}
        for i in report['impossible_transitions']:
            by_v[i['violation']] = by_v.get(i['violation'], 0) + 1
        for v, cnt in sorted(by_v.items(), key=lambda x: -x[1]):
            desc = next((i['description'] for i in report['impossible_transitions']
                         if i['violation'] == v), '')
            print(f"    🔴 {v:<40} : {cnt}")
            print(f"         {desc}")
        print(f"    → 원인: 중복실행 / 버그 / Race Condition / 수동 DB 수정")

    if report['reject_reasons']:
        print(f"\n  REJECT REASON BREAKDOWN")
        for r in report['reject_reasons']:
            print(f"    {r['reason']:<35} : {r['count']:>4}")

    # PASS 건수 이상 감지
    pa = report['pass_anomaly']
    avg_str = f"{pa['avg_7d']:.1f}" if pa['avg_7d'] is not None else 'N/A'
    days_str = f" (n={pa['history_days']}일)" if pa['history_days'] > 0 else ' (데이터 없음)'
    pass_ok = pa['anomaly'] is None
    print(f"\n  PASS COUNT MONITOR")
    print(f"    [{status(pass_ok)}] PASS 오늘           : {pa['today']:>4}건"
          f"  (7일평균={avg_str}건{days_str})")
    if pa['anomaly']:
        print(f"    ⚠️  이상 감지: {pa['anomaly']}")
        print(f"         → 로그 확인: grep '\\[SMC_SIG\\]' logs/auto_trading_$(date +%Y%m%d).log")

    overall = int_ok and lat_ok and rec_pct >= 99.0 and total_gaps == 0 and total_impos == 0
    verdict = '✅ HEALTHY' if overall else '⚠️  NEEDS ATTENTION'
    if pa['anomaly']:
        verdict = '⚠️  NEEDS ATTENTION (PASS 건수 이상)'
    print(f"\n  {verdict}")
    print(f"{'='*60}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Decision Ledger Health Check')
    parser.add_argument('--date', default=None,
                        help='날짜 (YYYY-MM-DD). 기본: 오늘')
    parser.add_argument('--json', action='store_true',
                        help='JSON 출력')
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()

    report = run_health_check(target)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
