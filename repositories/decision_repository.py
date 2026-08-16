"""
Decision Repository — Trading Research OS Phase 3

모든 의사결정 라이프사이클을 관리한다.
  Candidate 생성 → Decision Freeze → Execution → Outcome → Audit → Knowledge

설계 원칙:
  - 모든 public 메서드는 research.event_store에 이벤트를 자동 기록
  - 실패 시 예외를 발생시키지 않음 (트레이딩 흐름에 영향 없음)
  - trace_id(TR-YYYYMMDD-NNNNNN)로 전체 흐름을 grep 한 줄로 추적 가능

GD-007 / DATA_CONTRACT v2.0 기준.
"""

import json
import logging
import random
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 2026-08-17 [Pipeline Health] — create_candidate()/freeze_decision()이 내부에서
# 예외를 삼키고 (None, trace_id)로 조용히 실패를 흡수하는 기존 구조(§설계 원칙 2번)는
# 그대로 유지한다(트레이딩 흐름 보호가 목적이므로 변경 대상 아님). 다만 지금까지는
# 그 실패가 logger.warning() 로그 문자열로만 남아서, 완전한 INSERT 실패(행 자체가
# 생성되지 못한 경우)는 어떤 테이블/카운터로도 집계되지 않았다 — 이게 2026-08-11
# 이후 사흘간 Candidate 0건 장애를 몇 주간 아무도 못 알아챈 근본 원인 중 하나였다.
# 로그 문자열 grep이 아니라 research.event_store에 구조화된 이벤트로 남겨서
# analysis/decision_health_check.py가 SQL COUNT로 집계할 수 있게 한다.
PERSISTENCE_FAILURE_EVENT_TYPE = 'PersistenceFailure'


class DecisionRepository:
    """research 스키마 전용 Repository. TradingDB 인스턴스를 DI로 수령."""

    def __init__(self, db) -> None:
        """
        Args:
            db: database.trading_db.TradingDB 인스턴스
                (_get_conn / _put_conn 메서드 보유)
        """
        self._db = db

    # ──────────────────────────────────────────────────────────────
    # Trace ID
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def generate_trace_id() -> str:
        """TR-YYYYMMDD-NNNNNN 형식 Trace ID 생성.
        grep TR-20260630-000184 한 줄로 Candidate→Decision→Execution 전 과정 추적."""
        date = datetime.now().strftime('%Y%m%d')
        seq = random.randint(100000, 999999)
        return f"TR-{date}-{seq:06d}"

    # ──────────────────────────────────────────────────────────────
    # Lifecycle 1: Candidate 생성
    # ──────────────────────────────────────────────────────────────

    def create_candidate(
        self,
        stock_code: str,
        observed_at: datetime,
        price: float,
        trace_id: Optional[str] = None,
        stock_name: Optional[str] = None,
        session_id: Optional[int] = None,
        market_context_id: Optional[int] = None,
        sector: Optional[str] = None,
        market: Optional[str] = None,
        rs_score: Optional[float] = None,
        rvol: Optional[float] = None,
        atr_pct: Optional[float] = None,
        regime: Optional[str] = None,
        ema_gap_pct: Optional[float] = None,
        orchestrator_score: Optional[float] = None,
        orchestrator_accept_reason: Optional[str] = None,
        strategy_type: str = 'SMC_INTRADAY',
        created_by: str = 'signal_orchestrator',
    ) -> Tuple[Optional[str], str]:
        """
        Candidate 레코드 INSERT + CandidateCreated 이벤트 자동 기록.

        Returns:
            (candidate_id: str | None, trace_id: str)
            실패 시 (None, trace_id) — 트레이딩 흐름에 영향 없음
        """
        if trace_id is None:
            trace_id = self.generate_trace_id()

        conn = self._db._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research.candidates (
                        session_id, market_context_id,
                        stock_code, stock_name, sector, market,
                        observed_at, price,
                        rs_score, rvol, atr_pct, regime, ema_gap_pct,
                        orchestrator_score, orchestrator_accept_reason,
                        strategy_type, trace_id, lifecycle_status, created_by
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CREATED', %s
                    ) RETURNING candidate_id::text
                """, (
                    session_id, market_context_id,
                    stock_code, stock_name, sector, market,
                    observed_at, price,
                    rs_score, rvol, atr_pct, regime, ema_gap_pct,
                    orchestrator_score, orchestrator_accept_reason,
                    strategy_type, trace_id, created_by,
                ))
                candidate_id = cur.fetchone()[0]

            self._emit_event(
                conn=conn,
                event_type='CandidateCreated',
                entity_type='candidate',
                entity_id=candidate_id,
                source=created_by,
                occurred_at=observed_at,
                payload={
                    'stock_code': stock_code,
                    'session_id': session_id,
                    'price': float(price),
                    'trace_id': trace_id,
                },
            )
            conn.commit()
            logger.debug(
                f"[{trace_id}] CandidateCreated "
                f"candidate_id={candidate_id} stock={stock_code} price={price}"
            )
            return candidate_id, trace_id

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(f"[{trace_id}] create_candidate failed stock={stock_code}: {exc}")
            self._record_persistence_failure(
                conn, failure_type='CANDIDATE_INSERT', stage='CANDIDATE_INSERT',
                stock_code=stock_code, trace_id=trace_id, error=exc,
            )
            return None, trace_id
        finally:
            self._db._put_conn(conn)

    # ──────────────────────────────────────────────────────────────
    # Lifecycle 2: Decision Freeze (핵심 — IMMUTABLE 상태로 INSERT)
    # ──────────────────────────────────────────────────────────────

    def freeze_decision(
        self,
        candidate_id: str,
        stock_code: str,
        decision: str,
        decision_reason_code: str,
        policy_version: str,
        feature_snapshot: Dict[str, Any],
        observed_at: datetime,
        decided_at: Optional[datetime] = None,
        confidence: Optional[float] = None,
        expected_rr: Optional[float] = None,
        risk_score: Optional[float] = None,
        strategy_type: str = 'SMC_INTRADAY',
        trace_id: Optional[str] = None,
        created_by: str = 'execute_buy',
    ) -> Optional[str]:
        """
        Decision을 FROZEN 상태로 INSERT + Candidate → EVALUATED 전이.
        DecisionFrozen 이벤트 자동 기록.

        REJECT 결정은 이 메서드 호출 후 mark_outcome_pending()을 이어 호출.
        PASS 결정은 이 메서드 호출 후 mark_executed()를 이어 호출.

        feature_snapshot 필수 키:
            choch_grade, choch_detected, sweep_detected, rvol, atr_pct,
            regime, rs_score, ema_gap_pct, htf_trend, risk_score, expected_rr

        Returns:
            decision_id: str | None — 실패 시 None
        """
        if decided_at is None:
            decided_at = datetime.now()

        conn = self._db._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research.decision_ledger (
                        candidate_id, stock_code,
                        observed_at, decided_at,
                        decision, decision_reason_code,
                        policy_version, feature_snapshot,
                        confidence, expected_rr, risk_score,
                        strategy_type, lifecycle_status, trace_id, created_by
                    ) VALUES (
                        %s::uuid, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, 'FROZEN', %s, %s
                    ) RETURNING decision_id::text
                """, (
                    candidate_id, stock_code,
                    observed_at, decided_at,
                    decision, decision_reason_code,
                    policy_version, json.dumps(feature_snapshot, default=str),
                    confidence, expected_rr, risk_score,
                    strategy_type, trace_id, created_by,
                ))
                decision_id = cur.fetchone()[0]

                cur.execute("""
                    UPDATE research.candidates
                       SET lifecycle_status = 'EVALUATED'
                     WHERE candidate_id = %s::uuid
                """, (candidate_id,))

            self._emit_event(
                conn=conn,
                event_type='DecisionFrozen',
                entity_type='decision',
                entity_id=decision_id,
                source=created_by,
                occurred_at=decided_at,
                payload={
                    'candidate_id': candidate_id,
                    'stock_code': stock_code,
                    'decision': decision,
                    'reason_code': decision_reason_code,
                    'policy_version': policy_version,
                    'trace_id': trace_id,
                },
            )
            conn.commit()
            logger.info(
                f"[{trace_id}] DecisionFrozen "
                f"decision_id={decision_id} {stock_code} {decision} reason={decision_reason_code}"
            )
            return decision_id

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                f"[{trace_id}] freeze_decision failed "
                f"stock={stock_code} decision={decision}: {exc}"
            )
            self._record_persistence_failure(
                conn, failure_type='DECISION_INSERT', stage='DECISION_INSERT',
                stock_code=stock_code, trace_id=trace_id or '', error=exc,
            )
            return None
        finally:
            self._db._put_conn(conn)

    # ──────────────────────────────────────────────────────────────
    # Lifecycle 3: Execution
    # ──────────────────────────────────────────────────────────────

    def mark_executed(
        self,
        decision_id: str,
        trade_id: int,
        order_no: Optional[str] = None,
        executed_price: Optional[float] = None,
        slippage_pct: Optional[float] = None,
        executed_at: Optional[datetime] = None,
        trace_id: Optional[str] = None,
    ) -> bool:
        """FROZEN → EXECUTED + DecisionExecuted 이벤트."""
        if executed_at is None:
            executed_at = datetime.now()
        execution_result = {
            'trade_id': trade_id,
            'order_no': order_no,
            'executed_price': executed_price,
            'slippage_pct': slippage_pct,
        }
        return self._update_lifecycle(
            decision_id=decision_id,
            new_status='EXECUTED',
            extra_sets={
                'execution_result': json.dumps(execution_result),
                'executed_at': executed_at,
            },
            event_type='DecisionExecuted',
            event_payload={
                'trade_id': trade_id,
                'executed_price': executed_price,
                'trace_id': trace_id,
            },
            occurred_at=executed_at,
            source='execute_buy',
        )

    def mark_execution_failed(
        self,
        decision_id: str,
        reason: str,
        trace_id: Optional[str] = None,
    ) -> bool:
        """FROZEN → EXECUTION_FAILED + 이벤트."""
        return self._update_lifecycle(
            decision_id=decision_id,
            new_status='EXECUTION_FAILED',
            extra_sets={},
            event_type='DecisionExecutionFailed',
            event_payload={'reason': reason, 'trace_id': trace_id},
            source='execute_buy',
        )

    def mark_outcome_pending(
        self,
        decision_id: str,
        trace_id: Optional[str] = None,
    ) -> bool:
        """FROZEN → OUTCOME_PENDING (REJECT 결정 직후 자동 전이)."""
        return self._update_lifecycle(
            decision_id=decision_id,
            new_status='OUTCOME_PENDING',
            extra_sets={},
            event_type='OutcomePending',
            event_payload={'trace_id': trace_id},
            source='decision_repository',
        )

    def mark_exited(
        self,
        decision_id: str,
        exit_price: float,
        exit_reason: str,
        pnl_pct: float,
        trace_id: Optional[str] = None,
    ) -> bool:
        """
        EXECUTED → OUTCOME_RECORDED (실제 포지션 청산 시).
        REJECT 경로의 OUTCOME_PENDING→OUTCOME_RECORDED 와 달리,
        PASS 경로는 실제 청산가/PnL을 직접 알 수 있으므로 바로 OUTCOME_RECORDED로 전이.
        """
        exited_at = datetime.now()
        exit_result = {
            'exit_price': exit_price,
            'exit_reason': exit_reason,
            'pnl_pct': pnl_pct,
        }
        return self._update_lifecycle(
            decision_id=decision_id,
            new_status='OUTCOME_RECORDED',
            extra_sets={
                'exit_result': json.dumps(exit_result),
                'exited_at': exited_at,
            },
            event_type='TradeExited',
            event_payload={
                'exit_price': exit_price,
                'exit_reason': exit_reason,
                'pnl_pct': pnl_pct,
                'trace_id': trace_id,
            },
            occurred_at=exited_at,
            source='execute_sell',
        )

    # ──────────────────────────────────────────────────────────────
    # Lifecycle 4: Outcome Recording (returns_collector 완료 후)
    # ──────────────────────────────────────────────────────────────

    def complete_outcome(
        self,
        decision_id: str,
        trace_id: Optional[str] = None,
    ) -> bool:
        """OUTCOME_PENDING → OUTCOME_RECORDED (future_return_events +5D 채워진 후)."""
        return self._update_lifecycle(
            decision_id=decision_id,
            new_status='OUTCOME_RECORDED',
            extra_sets={},
            event_type='OutcomeCompleteRecorded',
            event_payload={'trace_id': trace_id},
            source='returns_collector',
        )

    # ──────────────────────────────────────────────────────────────
    # Lifecycle 5: Dual Audit
    # ──────────────────────────────────────────────────────────────

    def complete_audit(
        self,
        decision_id: str,
        decision_quality: str,
        outcome_quality: str,
        decision_notes: Optional[str] = None,
        outcome_notes: Optional[str] = None,
        decision_verdict: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> bool:
        """
        OUTCOME_RECORDED → AUDIT_COMPLETED + Dual Auditor 결과 저장.

        Args:
            decision_quality: 'RATIONAL' | 'QUESTIONABLE'
            outcome_quality:  'CORRECT' | 'LUCKY' | 'UNLUCKY' | 'WRONG' | 'PENDING'
        """
        extra: Dict[str, Any] = {
            'decision_auditor_result': json.dumps({
                'quality': decision_quality,
                'notes': decision_notes,
            }),
            'outcome_auditor_result': json.dumps({
                'quality': outcome_quality,
                'notes': outcome_notes,
            }),
        }
        if decision_verdict is not None:
            extra['decision_verdict'] = decision_verdict

        return self._update_lifecycle(
            decision_id=decision_id,
            new_status='AUDIT_COMPLETED',
            extra_sets=extra,
            event_type='AuditCompleted',
            event_payload={
                'decision_quality': decision_quality,
                'outcome_quality': outcome_quality,
                'trace_id': trace_id,
            },
            source='auditor_ai',
        )

    # ──────────────────────────────────────────────────────────────
    # Lifecycle 6: Knowledge Extraction
    # ──────────────────────────────────────────────────────────────

    def extract_knowledge(
        self,
        decision_id: str,
        trace_id: Optional[str] = None,
    ) -> bool:
        """AUDIT_COMPLETED → KNOWLEDGE_EXTRACTED (종착점 — 역방향 전이 불가)."""
        return self._update_lifecycle(
            decision_id=decision_id,
            new_status='KNOWLEDGE_EXTRACTED',
            extra_sets={},
            event_type='KnowledgeExtracted',
            event_payload={'trace_id': trace_id},
            source='knowledge_base',
        )

    # ──────────────────────────────────────────────────────────────
    # future_return_events 기록
    # ──────────────────────────────────────────────────────────────

    def record_future_return(
        self,
        decision_id: str,
        stock_code: str,
        horizon_label: str,
        decision_price: float,
        return_pct: float,
        price_at_horizon: Optional[float] = None,
        horizon_minutes: Optional[int] = None,
        trace_id: Optional[str] = None,
        created_by: str = 'returns_collector',
    ) -> bool:
        """
        future_return_events에 수익률 이벤트 INSERT.
        UNIQUE (decision_id, horizon_label) 충돌 시 무시.

        Args:
            horizon_label: '+30m' | '+EOD' | '+1D' | '+3D' | '+5D' | '+10D' ...
        """
        conn = self._db._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research.future_return_events (
                        decision_id, stock_code,
                        horizon_label, horizon_minutes,
                        decision_price, price_at_horizon, return_pct,
                        created_by
                    ) VALUES (
                        %s::uuid, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (decision_id, horizon_label) DO NOTHING
                """, (
                    decision_id, stock_code,
                    horizon_label, horizon_minutes,
                    decision_price, price_at_horizon, return_pct,
                    created_by,
                ))

            self._emit_event(
                conn=conn,
                event_type='FutureReturnPartial',
                entity_type='decision',
                entity_id=decision_id,
                source=created_by,
                payload={
                    'horizon_label': horizon_label,
                    'return_pct': float(return_pct),
                    'decision_price': float(decision_price),
                    'trace_id': trace_id,
                },
            )
            conn.commit()
            logger.debug(
                f"[RETURN] {decision_id} {horizon_label} {return_pct:+.2f}%"
            )
            return True

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                f"[RETURN] record_future_return failed "
                f"decision_id={decision_id} horizon={horizon_label}: {exc}"
            )
            self._record_persistence_failure(
                conn, failure_type='FUTURE_RETURN_INSERT', stage='FUTURE_RETURN_INSERT',
                stock_code=stock_code, trace_id=trace_id or '', error=exc,
            )
            return False
        finally:
            self._db._put_conn(conn)

    # ──────────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ──────────────────────────────────────────────────────────────

    def _update_lifecycle(
        self,
        decision_id: str,
        new_status: str,
        extra_sets: Dict[str, Any],
        event_type: str,
        event_payload: Dict[str, Any],
        source: str,
        occurred_at: Optional[datetime] = None,
    ) -> bool:
        """lifecycle_status 업데이트 + 이벤트 자동 기록. 항상 같은 트랜잭션."""
        if occurred_at is None:
            occurred_at = datetime.now()

        conn = self._db._get_conn()
        try:
            with conn.cursor() as cur:
                # 보호 컬럼(immutability trigger 대상)은 SET에 포함하지 않음
                set_parts = ['lifecycle_status = %s']
                params: list = [new_status]
                for col, val in extra_sets.items():
                    set_parts.append(f"{col} = %s")
                    params.append(val)
                params.append(decision_id)

                cur.execute(
                    f"UPDATE research.decision_ledger "
                    f"SET {', '.join(set_parts)} "
                    f"WHERE decision_id = %s::uuid",
                    params,
                )

            self._emit_event(
                conn=conn,
                event_type=event_type,
                entity_type='decision',
                entity_id=decision_id,
                source=source,
                occurred_at=occurred_at,
                payload=event_payload,
            )
            conn.commit()
            logger.debug(
                f"[DECISION_REPO] {event_type} decision_id={decision_id} → {new_status}"
            )
            return True

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                f"[DECISION_REPO] {event_type} failed decision_id={decision_id}: {exc}"
            )
            # [Pipeline Health Chain, 2026-08-17] mark_executed/mark_execution_failed/
            # mark_outcome_pending/mark_exited/complete_audit/extract_knowledge가 전부
            # 이 헬퍼를 공유한다. new_status로 어느 단계(INTENT→SUBMIT/FILL 등)에서 실패했는지
            # 구분해 기록한다 — CANDIDATE_INSERT/DECISION_INSERT와 동일한 매커니즘.
            self._record_persistence_failure(
                conn, failure_type=f'LIFECYCLE_{new_status}', stage=new_status,
                stock_code='', trace_id=str(decision_id or ''), error=exc,
            )
            return False
        finally:
            self._db._put_conn(conn)

    def _emit_event(
        self,
        conn,
        event_type: str,
        entity_type: str,
        entity_id: str,
        source: str,
        payload: Dict[str, Any],
        occurred_at: Optional[datetime] = None,
    ) -> None:
        """
        research.event_store INSERT.
        conn은 호출부 트랜잭션과 공유 — commit은 호출부에서.
        이 메서드가 실패하면 호출부 트랜잭션 전체가 롤백됨 (의도적).
        """
        if occurred_at is None:
            occurred_at = datetime.now()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO research.event_store (
                    occurred_at, event_type, entity_type, entity_id,
                    source, payload, created_by
                ) VALUES (
                    %s, %s, %s, %s::uuid, %s, %s, %s
                )
            """, (
                occurred_at,
                event_type,
                entity_type,
                entity_id,
                source,
                json.dumps(payload, default=str),
                source,
            ))

    def _record_persistence_failure(
        self,
        conn,
        *,
        failure_type: str,
        stage: str,
        stock_code: str,
        trace_id: str,
        error: BaseException,
    ) -> None:
        """
        [Pipeline Health, 2026-08-17] create_candidate()/freeze_decision()의 except
        블록에서만 호출한다. 원래 트랜잭션은 이미 rollback된 뒤이므로, 같은 conn에
        완전히 새로운 소규모 트랜잭션(INSERT 1건 + commit)만 연다.

        이 메서드 자체는 절대 예외를 밖으로 던지지 않는다 — 관측 계측이 실패해도
        (예: DB 연결 자체가 끊긴 극단적 상황) 원래 실패 흐름(logger.warning + None
        반환)은 그대로 유지되어야 한다(§3 "business logic 변경 없이 보완").

        entity_id는 실패했으니 candidate_id/decision_id가 없다 — 새 UUID를 발급해서
        payload 안에 stock_code/trace_id/error로 역추적 가능하게 남긴다.
        """
        try:
            failure_id = str(uuid.uuid4())
            self._emit_event(
                conn=conn,
                event_type=PERSISTENCE_FAILURE_EVENT_TYPE,
                entity_type='persistence_failure',
                entity_id=failure_id,
                source='decision_repository',
                payload={
                    'failure_type': failure_type,   # CANDIDATE_INSERT / DECISION_INSERT
                    'stage': stage,
                    'stock_code': stock_code,
                    'trace_id': trace_id,
                    'error_type': type(error).__name__,
                    'error': str(error)[:500],
                },
            )
            conn.commit()
        except Exception as meta_exc:
            # 관측 계측 자체의 실패는 별도로만 로그하고 절대 전파하지 않는다.
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                f"[{trace_id}] persistence failure event 기록 자체가 실패함"
                f"(원래 실패는 이미 위에서 로그됨, 트레이딩 흐름 영향 없음): {meta_exc}"
            )
