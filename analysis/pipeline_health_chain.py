"""
Pipeline Health Chain (2026-08-17)

Data Collection → Candidate → Signal → Decision → Intent → Submit → Fill →
Trade → Future Return 전체를 하나의 Reconciliation Chain으로 연결해,
거래일 종료 후 "어디에서 몇 건이 사라졌는가?"를 자동으로 확인한다.

이 모듈은 실제 스키마를 그대로 반영한다 (§1 원칙 — 추측 매핑 금지).
research.candidates / research.decision_ledger / research.future_return_events /
research.event_store(event_type='PersistenceFailure') / public.trades 를
실제로 조사한 결과, 다음이 확정됐다:

  - DATA:      research 스키마에 별도 저장되지 않는다. candidate_id/trace_id가
               생기기 전 단계라 이 체인에서 계측 불가능한 진짜 시작점이다.
               억지로 카운트를 만들지 않고 N/A로 고정한다 (§3).
  - SIGNAL:    execute_buy/decision_ledger 파이프라인에는 Candidate와 Decision
               사이에 별도로 영속화되는 "Signal" 테이블이 없다. Signal Orchestrator
               ACCEPT 자체가 candidates row 생성이다(= CANDIDATE 단계와 합쳐짐).
               *** research.condition_candidates/strategy_signals는 HTS
               조건검색 Shadow 파이프라인(WI-13)으로 완전히 별개다. 이전에
               StrategySignalCreated↔check_entry_signal을 잘못 연결했던 실수를
               반복하지 않기 위해, 이 체인에서는 그 테이블들을 절대 사용하지 않는다. ***
  - INTENT:    decision_ledger.decision='PASS' 그 자체가 "주문 의도"다. 별도
               persist 없음 — DECISION 단계의 PASS 서브셋과 항상 1:1.
  - SUBMIT/FILL: mark_executed()/mark_execution_failed() 둘 다 동일한
               _update_lifecycle() 헬퍼를 쓰고 FROZEN→EXECUTED 혹은
               →EXECUTION_FAILED 로 "한 번에" 전이한다. 실제 코드에
               Submit(주문 접수)과 Fill(체결)을 구분하는 별도 상태/테이블이
               없다 — 두 개념을 하나의 SUBMIT/FILL 단계로 합친다.
               *** 실사용 코드 조사 결과 mark_execution_failed()는
               repositories/decision_repository.py에 구현만 되어 있고
               main_auto_trading.py/services/decision_service.py 어디에서도
               호출되지 않는다(grep 확인, 2026-08-17). 즉 주문 실패 시
               EXECUTION_FAILED로 전이될 경로가 현재 운영 코드에 없다 —
               PASS 결정이 EXECUTED로도 EXECUTION_FAILED로도 전이하지 못하고
               FROZEN에 멈춰 있으면 그게 바로 이 체인이 잡아야 하는
               unexplained drop이다. ***
  - TRADE:     public.trades(trade_id SERIAL)는 research 스키마 밖에 있다.
               main_auto_trading.py가 self.db.insert_trade() 실패 시 예외를
               삼키고 trade_id=None으로 두고도 decision_service.record_order()를
               그대로 호출한다(services/decision_service.py record_order():
               trade_id or 0) — 즉 trades INSERT가 실패해도 decision_ledger는
               EXECUTED로 기록되고 execution_result.trade_id=0 이라는 sentinel만
               남는다. main_auto_trading.py의 주문 흐름 자체는 건드리지 않고
               (고위험 변경 금지, CLAUDE.md §17), 이미 남아있는 이 sentinel을
               읽기 전용으로 탐지해 TRADE 단계 실패로 분류한다.
  - FUTURE_RETURN: research.future_return_events, decision_id 기준. 비동기로
               채워지므로(+30m,+EOD,...) 미수집=실패가 아니라 PENDING일 수
               있다 — Eligible=0이면 N/A, PersistenceFailure 이벤트가 있으면만
               FAIL, 그 외 미수집은 WARNING(§9 "명시적 skip"에 준함).

실행:
    python3 -m analysis.pipeline_health_chain              # 오늘
    python3 -m analysis.pipeline_health_chain --date 2026-08-14
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))


# ─── 실제 Stage → Source 매핑 (§16 출력용, 코드 확인 완료) ─────────────────
STAGE_SOURCE_MAP: Dict[str, Dict[str, str]] = {
    'DATA': {
        'source': 'N/A — research 스키마에 미저장 (실시간 시세 폴링, candidate_id 생성 이전)',
        'event_type': 'N/A',
        'identifier': 'N/A',
    },
    'CANDIDATE': {
        'source': 'research.candidates',
        'event_type': 'CandidateCreated (성공) / PersistenceFailure{failure_type=CANDIDATE_INSERT} (실패)',
        'identifier': 'candidate_id, trace_id',
    },
    'SIGNAL': {
        'source': 'N/A — 이 체인(execute_buy/decision_ledger)에는 별도 Signal 테이블 없음. '
                  'CANDIDATE 생성 자체가 Signal Orchestrator ACCEPT다. '
                  '(research.condition_candidates/strategy_signals는 무관한 별개 파이프라인 — 사용 안 함)',
        'event_type': 'N/A',
        'identifier': 'N/A (candidate_id와 동일 취급)',
    },
    'DECISION': {
        'source': 'research.decision_ledger',
        'event_type': 'DecisionFrozen (성공) / PersistenceFailure{failure_type=DECISION_INSERT} (실패)',
        'identifier': 'decision_id (FK candidate_id), trace_id, decision IN (PASS,REJECT,SHADOW_PASS,SHADOW_REJECT)',
    },
    'INTENT': {
        'source': 'N/A — decision_ledger.decision=\'PASS\' 그 자체 (별도 persist 없음)',
        'event_type': 'N/A',
        'identifier': 'decision_id (DECISION 단계와 동일)',
    },
    'SUBMIT_FILL': {
        'source': 'research.decision_ledger.lifecycle_status (FROZEN→EXECUTED / →EXECUTION_FAILED)',
        'event_type': 'DecisionExecuted / DecisionExecutionFailed / '
                       'PersistenceFailure{failure_type=LIFECYCLE_EXECUTED}',
        'identifier': 'decision_id, execution_result JSONB{trade_id, order_no, executed_price}',
    },
    'TRADE': {
        'source': 'public.trades (trade_id SERIAL) — research 스키마 밖',
        'event_type': '(event_store 이벤트 없음, execution_result.trade_id sentinel로만 판별)',
        'identifier': "trade_id (decision_ledger.execution_result->>'trade_id'로 참조, FK 아님)",
    },
    'FUTURE_RETURN': {
        'source': 'research.future_return_events',
        'event_type': 'FutureReturnPartial (성공) / PersistenceFailure{failure_type=FUTURE_RETURN_INSERT} (실패)',
        'identifier': 'decision_id, horizon_label',
    },
}


@dataclass
class StageResult:
    stage: str
    input: Optional[int] = None
    output: Optional[int] = None
    reject: Optional[int] = None
    fail: Optional[int] = None
    drop: Optional[int] = None
    status: str = 'N/A'
    note: str = ''

    def as_row(self) -> Dict[str, Any]:
        return {
            'stage': self.stage, 'input': self.input, 'output': self.output,
            'reject': self.reject, 'fail': self.fail, 'drop': self.drop,
            'status': self.status, 'note': self.note,
        }


# ─── §9 Health Status 순수 판정 함수 (DB 의존 없음 — 단독 단위 테스트 가능) ──

def reconcile(
    stage: str,
    input_: Optional[int],
    output_: int,
    reject_: int = 0,
    fail_: int = 0,
) -> StageResult:
    """
    Input = Output + Reject + Fail + Unexplained(Drop) 이 되도록 drop을 계산하고
    §9 규칙(PASS/WARNING/FAIL/N/A)에 따라 status를 판정한다.

    input_이 None이면 이 stage의 입력 자체를 관측할 수 없는 경우다(§3, 예: CANDIDATE
    단계의 진짜 "시도 수"는 DATA가 미계측이라 알 수 없음) — 이 경우 drop은 계산하지
    않고(N/A), output/fail만으로 자체 완결적인 판정을 내린다.
    """
    if input_ is None:
        # 자체 완결 판정: 이 stage 자체가 실패했는지만 본다 (입력 대비 손실은 못 잼).
        if output_ == 0 and fail_ == 0:
            return StageResult(stage, None, output_, reject_, fail_, 0, 'N/A', '해당 기간 입력 없음')
        status = 'FAIL' if fail_ > 0 else 'PASS'
        return StageResult(stage, None, output_, reject_, fail_, 0, status,
                            '' if status == 'PASS' else f'PersistenceFailure {fail_}건')

    if input_ == 0:
        return StageResult(stage, 0, output_, reject_, fail_, 0, 'N/A', '해당 기간 입력 없음')

    drop = input_ - output_ - reject_ - fail_
    if drop < 0:
        # 이론상 발생하면 집계 쿼리 자체가 잘못됐다는 신호 — 감춰서는 안 된다.
        return StageResult(stage, input_, output_, reject_, fail_, drop, 'FAIL',
                            f'집계 불일치(drop<0) — reconciliation 쿼리 자체 점검 필요')

    if drop > 0:
        return StageResult(stage, input_, output_, reject_, fail_, drop, 'FAIL',
                            f'SILENT_DROP {drop}건 — Reject/Fail로 설명 안 됨')
    if fail_ > 0:
        return StageResult(stage, input_, output_, reject_, fail_, 0, 'FAIL',
                            f'PersistenceFailure {fail_}건')
    if reject_ > 0:
        return StageResult(stage, input_, output_, reject_, fail_, 0, 'WARNING',
                            f'명시적 Reject {reject_}건 (정상)')
    return StageResult(stage, input_, output_, reject_, fail_, 0, 'PASS', '')


# ─── DB 조회 (실제 psycopg2 커넥션 — 운영 실행 시에만 사용, 테스트는 mock cursor) ──

def _persistence_failure_by_type(cur, target_date: date, failure_type: str) -> int:
    cur.execute(
        """
        SELECT COUNT(*) FROM research.event_store
        WHERE event_type = 'PersistenceFailure'
          AND payload->>'failure_type' = %s
          AND occurred_at::date = %s
        """,
        (failure_type, str(target_date)),
    )
    return int(cur.fetchone()[0])


def compute_chain(cur, target_date: date) -> Dict[str, Any]:
    """
    각 Stage를 실제 쿼리로 채운다. 읽기 전용(SELECT/COUNT만) — 매매 판단 경로와
    완전히 분리되어 있고, 어떤 INSERT/UPDATE도 하지 않는다.
    """
    stages: List[StageResult] = []

    # CANDIDATE
    cur.execute(
        "SELECT COUNT(*) FROM research.candidates WHERE observed_at::date = %s",
        (str(target_date),),
    )
    n_candidates = int(cur.fetchone()[0])
    f_candidate = _persistence_failure_by_type(cur, target_date, 'CANDIDATE_INSERT')
    stages.append(reconcile('CANDIDATE', None, n_candidates, 0, f_candidate))

    # SIGNAL — N/A (설계상 미존재, §16 매핑 참조)
    stages.append(StageResult('SIGNAL', None, None, None, None, None, 'N/A',
                               'CANDIDATE 단계와 동일 이벤트 — 별도 persist 없음'))

    # DECISION (Input = CANDIDATE Output)
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE decision = 'PASS')   AS n_pass,
            COUNT(*) FILTER (WHERE decision = 'REJECT') AS n_reject,
            COUNT(*)                                    AS n_total
        FROM research.decision_ledger
        WHERE decided_at::date = %s
        """,
        (str(target_date),),
    )
    n_pass, n_reject, n_decisions = (int(x) for x in cur.fetchone())
    f_decision = _persistence_failure_by_type(cur, target_date, 'DECISION_INSERT')
    decision_result = reconcile('DECISION', n_candidates, n_decisions, 0, f_decision)
    # Reject는 이 stage 자체의 손실이 아니라 다음 stage(INTENT)로 못 넘어가는 정상
    # 갈림길이다 — reconcile()의 reject 인자로는 안 넣고, note에만 부기한다.
    decision_result.note = (decision_result.note + f' | PASS={n_pass} REJECT={n_reject}').strip(' |')
    stages.append(decision_result)

    # INTENT — DECISION.PASS와 1:1 (별도 persist 없음, 항상 자명하게 reconcile됨)
    intent_result = reconcile('INTENT', n_pass, n_pass, 0, 0)
    intent_result.note = 'decision=PASS 서브셋 (별도 persist 없음)'
    stages.append(intent_result)

    # SUBMIT_FILL (Input = PASS decisions)
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE lifecycle_status = 'EXECUTED')         AS n_executed,
            COUNT(*) FILTER (WHERE lifecycle_status = 'EXECUTION_FAILED') AS n_exec_failed
        FROM research.decision_ledger
        WHERE decided_at::date = %s AND decision = 'PASS'
        """,
        (str(target_date),),
    )
    n_executed, n_exec_failed = (int(x) for x in cur.fetchone())
    f_lifecycle_executed = _persistence_failure_by_type(cur, target_date, 'LIFECYCLE_EXECUTED')
    submit_fill_result = reconcile('SUBMIT_FILL', n_pass, n_executed, n_exec_failed, f_lifecycle_executed)
    stages.append(submit_fill_result)

    # TRADE (Input = EXECUTED decisions; trade_id=0 sentinel = public.trades INSERT 실패)
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE COALESCE((execution_result->>'trade_id')::bigint, 0) > 0) AS n_trade_ok,
            COUNT(*) FILTER (WHERE COALESCE((execution_result->>'trade_id')::bigint, 0) = 0) AS n_trade_missing
        FROM research.decision_ledger
        WHERE decided_at::date = %s AND decision = 'PASS' AND lifecycle_status = 'EXECUTED'
        """,
        (str(target_date),),
    )
    n_trade_ok, n_trade_missing = (int(x) for x in cur.fetchone())
    trade_result = reconcile('TRADE', n_executed, n_trade_ok, 0, n_trade_missing)
    if n_trade_missing > 0:
        trade_result.note = (
            f"public.trades INSERT 누락 감지(execution_result.trade_id=0 sentinel) {n_trade_missing}건"
        )
    stages.append(trade_result)

    # FUTURE_RETURN (Eligible = 그날 전체 decision, PASS/REJECT 모두)
    cur.execute(
        """
        SELECT COUNT(DISTINCT d.decision_id)
        FROM research.decision_ledger d
        JOIN research.future_return_events f
          ON f.decision_id = d.decision_id AND f.horizon_label = '+EOD'
        WHERE d.decided_at::date = %s
        """,
        (str(target_date),),
    )
    n_return_done = int(cur.fetchone()[0])
    f_future_return = _persistence_failure_by_type(cur, target_date, 'FUTURE_RETURN_INSERT')
    fr_result = reconcile('FUTURE_RETURN', n_decisions, n_return_done, 0, f_future_return)
    if fr_result.status == 'FAIL' and f_future_return == 0 and fr_result.drop and fr_result.drop > 0:
        # PersistenceFailure 없이 단순 비동기 미수집일 수 있다 — FAIL이 아니라 WARNING(PENDING).
        fr_result.status = 'WARNING'
        fr_result.note = f'PENDING (비동기 미수집) {fr_result.drop}건 — PersistenceFailure 이벤트 없음'
    stages.append(fr_result)

    persistence_failures_total = f_candidate + f_decision + f_lifecycle_executed + f_future_return
    unexplained_total = sum(
        (s.drop or 0) for s in stages if s.status == 'FAIL' and (s.drop or 0) > 0
    )

    if any(s.status == 'FAIL' for s in stages):
        pipeline_status = 'FAIL'
    elif any(s.status == 'WARNING' for s in stages):
        pipeline_status = 'WARNING'
    else:
        pipeline_status = 'PASS'

    return {
        'target_date': str(target_date),
        'stages': [s.as_row() for s in stages],
        'persistence_failures_total': persistence_failures_total,
        'unexplained_drops_total': unexplained_total,
        'pipeline_status': pipeline_status,
    }


# ─── 텍스트 렌더링 (operations_daily_summary.py가 그대로 재사용) ────────────

def render_chain_report(chain: Dict[str, Any]) -> str:
    lines = []
    lines.append('=' * 60)
    lines.append('PIPELINE HEALTH CHAIN')
    lines.append(f"Date: {chain['target_date']}")
    lines.append('=' * 60)
    lines.append('')
    lines.append(f"{'Stage':<14}{'Input':>8}{'Output':>8}{'Reject':>8}{'Fail':>6}{'Drop':>6}  Status")
    for s in chain['stages']:
        def _f(v):
            return '-' if v is None else str(v)
        lines.append(
            f"{s['stage']:<14}{_f(s['input']):>8}{_f(s['output']):>8}"
            f"{_f(s['reject']):>8}{_f(s['fail']):>6}{_f(s['drop']):>6}  {s['status']}"
            + (f"  ({s['note']})" if s['note'] else '')
        )
    lines.append('-' * 60)
    lines.append(f"Persistence Failures : {chain['persistence_failures_total']}")
    lines.append(f"Unexplained Drops    : {chain['unexplained_drops_total']}")
    lines.append(f"Pipeline Status      : {chain['pipeline_status']}")
    lines.append('=' * 60)
    return '\n'.join(lines)


def run_chain_check(target_date: Optional[date] = None) -> Dict[str, Any]:
    """DB에 직접 연결해 compute_chain() 결과 dict만 반환한다.
    operations_daily_summary.py의 _collect_decision_health()와 동일한 패턴."""
    from database.trading_db import TradingDB
    target_date = target_date or date.today()
    db = TradingDB()
    conn = db._get_conn()
    try:
        with conn.cursor() as cur:
            return compute_chain(cur, target_date)
    finally:
        db._put_conn(conn)


def run(target_date: Optional[date] = None) -> str:
    chain = run_chain_check(target_date)
    text = render_chain_report(chain)
    print(text)
    return text


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pipeline Health Chain')
    parser.add_argument('--date', default=None, help='날짜 (YYYY-MM-DD). 기본: 오늘')
    args = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else date.today()
    run(target)
