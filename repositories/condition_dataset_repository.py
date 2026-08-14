"""
repositories/condition_dataset_repository.py — Condition Candidate Dataset (WI-13)

HTS 조건검색 seq 32-39 -> Strategy Monitor Router -> Signal 흐름을
research 스키마 테이블(condition_candidates/strategy_monitor_events/
strategy_signals)에 구조화 저장한다.

repositories/decision_repository.py와 동일한 설계 원칙:
  - 모든 public 메서드는 실패해도 예외를 올리지 않는다 (트레이딩 흐름 보호)
  - research.event_store에 대응 이벤트를 자동 기록한다
  - candidate_id 등은 INSERT 실패 시 None을 반환한다

research.candidates/decision_ledger(Signal Orchestrator ACCEPT / execute_buy
Gate 전용)와는 다른 파이프라인 단계다 — 절대 혼동하지 않는다.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConditionDatasetRepository:
    """research 스키마 전용 Repository. TradingDB 인스턴스를 DI로 수령."""

    def __init__(self, db) -> None:
        """
        Args:
            db: database.trading_db.TradingDB 인스턴스
                (_get_conn / _put_conn 메서드 보유)
        """
        self._db = db

    # ──────────────────────────────────────────────────────────────
    # 1. Condition Candidate
    # ──────────────────────────────────────────────────────────────

    def record_candidate(
        self,
        observed_at: datetime,
        stock_code: str,
        condition_seq: int,
        condition_name: str,
        condition_sources: list,
        stock_name: Optional[str] = None,
        market: Optional[str] = None,
        trace_id: Optional[str] = None,
        source: str = 'kiwoom_hts_condition_search',
    ) -> Optional[str]:
        """
        condition_candidates INSERT + ConditionCandidateCreated 이벤트 기록.

        Returns:
            candidate_id: str | None (실패 시 None — 트레이딩 흐름에 영향 없음)
        """
        conn = self._db._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research.condition_candidates (
                        observed_at, stock_code, stock_name, market,
                        condition_seq, condition_name, condition_sources,
                        source, trace_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                    ) RETURNING candidate_id::text
                """, (
                    observed_at, stock_code, stock_name, market,
                    condition_seq, condition_name,
                    json.dumps(condition_sources, default=str),
                    source, trace_id,
                ))
                candidate_id = cur.fetchone()[0]

            self._emit_event(
                conn=conn,
                event_type='ConditionCandidateCreated',
                entity_type='condition_candidate',
                entity_id=candidate_id,
                source=source,
                occurred_at=observed_at,
                payload={
                    'stock_code': stock_code,
                    'condition_seq': condition_seq,
                    'condition_name': condition_name,
                },
            )
            conn.commit()
            return candidate_id

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                f"[condition_dataset] record_candidate failed "
                f"stock={stock_code} seq={condition_seq}: {exc}"
            )
            return None
        finally:
            self._db._put_conn(conn)

    # ──────────────────────────────────────────────────────────────
    # 2. Strategy Monitor Event
    # ──────────────────────────────────────────────────────────────

    def record_monitor_event(
        self,
        candidate_id: str,
        observed_at: datetime,
        stock_code: str,
        condition_seq: int,
        monitor_name: str,
        monitor_status: str,
        monitor_state: Optional[str] = None,
        monitor_result: Optional[Dict[str, Any]] = None,
        monitor_error: Optional[str] = None,
        monitor_version: str = 'v1',
    ) -> Optional[str]:
        """
        strategy_monitor_events INSERT + StrategyMonitorEvaluated 이벤트 기록.

        monitor_status는 호출부(main_auto_trading.py)에서 아래 규칙으로 계산:
            data_quality == 'NO_CODE'  -> NOT_IMPLEMENTED
            data_quality == 'ERROR'    -> ERROR
            signal is True             -> SIGNAL
            signal is False            -> NO_SIGNAL

        Returns:
            event_id: str | None
        """
        conn = self._db._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research.strategy_monitor_events (
                        candidate_id, observed_at, stock_code, condition_seq,
                        monitor_name, monitor_version, monitor_status,
                        monitor_state, monitor_result, monitor_error
                    ) VALUES (
                        %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                    ) RETURNING event_id::text
                """, (
                    candidate_id, observed_at, stock_code, condition_seq,
                    monitor_name, monitor_version, monitor_status,
                    monitor_state,
                    json.dumps(monitor_result, default=str) if monitor_result is not None else None,
                    monitor_error,
                ))
                event_id = cur.fetchone()[0]

            self._emit_event(
                conn=conn,
                event_type='StrategyMonitorEvaluated',
                entity_type='strategy_monitor_event',
                entity_id=event_id,
                source=monitor_name,
                occurred_at=observed_at,
                payload={
                    'candidate_id': candidate_id,
                    'condition_seq': condition_seq,
                    'monitor_status': monitor_status,
                },
            )
            conn.commit()
            return event_id

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                f"[condition_dataset] record_monitor_event failed "
                f"stock={stock_code} seq={condition_seq} monitor={monitor_name}: {exc}"
            )
            return None
        finally:
            self._db._put_conn(conn)

    # ──────────────────────────────────────────────────────────────
    # 3. Strategy Signal
    # ──────────────────────────────────────────────────────────────

    def record_signal(
        self,
        candidate_id: str,
        monitor_event_id: str,
        observed_at: datetime,
        stock_code: str,
        condition_seq: int,
        strategy_name: str,
        signal_type: Optional[str] = None,
        signal_score: Optional[float] = None,
        entry_price_reference: Optional[float] = None,
        signal_reason: Optional[str] = None,
    ) -> Optional[str]:
        """
        strategy_signals INSERT + StrategySignalCreated 이벤트 기록.
        monitor_status='SIGNAL'인 경우에만 호출부에서 호출한다.

        Returns:
            signal_id: str | None
        """
        conn = self._db._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research.strategy_signals (
                        candidate_id, monitor_event_id, observed_at, stock_code,
                        condition_seq, strategy_name, signal_type, signal_score,
                        entry_price_reference, signal_reason
                    ) VALUES (
                        %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING signal_id::text
                """, (
                    candidate_id, monitor_event_id, observed_at, stock_code,
                    condition_seq, strategy_name, signal_type, signal_score,
                    entry_price_reference, signal_reason,
                ))
                signal_id = cur.fetchone()[0]

            self._emit_event(
                conn=conn,
                event_type='StrategySignalCreated',
                entity_type='strategy_signal',
                entity_id=signal_id,
                source=strategy_name,
                occurred_at=observed_at,
                payload={
                    'candidate_id': candidate_id,
                    'condition_seq': condition_seq,
                    'strategy_name': strategy_name,
                },
            )
            conn.commit()
            return signal_id

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                f"[condition_dataset] record_signal failed "
                f"stock={stock_code} seq={condition_seq} strategy={strategy_name}: {exc}"
            )
            return None
        finally:
            self._db._put_conn(conn)

    # ──────────────────────────────────────────────────────────────
    # event_store 발행 (decision_repository._emit_event와 동일 패턴)
    # ──────────────────────────────────────────────────────────────

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
        research.event_store INSERT. conn은 호출부 트랜잭션과 공유 —
        commit은 호출부에서. 이 메서드가 실패하면 호출부 트랜잭션이 롤백된다
        (의도적, decision_repository._emit_event와 동일 원칙).
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
