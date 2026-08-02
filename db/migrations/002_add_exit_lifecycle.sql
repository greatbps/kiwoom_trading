-- Migration 002: PASS Decision Lifecycle 완성 (Order + Exit 컬럼 추가)
-- 적용: psql trading_system -f db/migrations/002_add_exit_lifecycle.sql
-- 영향: research.decision_ledger 에 exit_result, exited_at 컬럼 추가
-- 트리거 수정 없음 — 새 컬럼은 immutability trigger 대상 외

BEGIN;

ALTER TABLE research.decision_ledger
    ADD COLUMN IF NOT EXISTS exit_result JSONB,
    -- {exit_price: float, exit_reason: str, pnl_pct: float}
    ADD COLUMN IF NOT EXISTS exited_at   TIMESTAMPTZ;

-- event_store에 TradeExited 이벤트를 허용 (event_type CHECK 없음 — 자유 문자열)
COMMENT ON COLUMN research.decision_ledger.exit_result IS
    'PASS 결정 청산 정보. {exit_price, exit_reason, pnl_pct}. execute_sell 시 기록.';
COMMENT ON COLUMN research.decision_ledger.exited_at IS
    'PASS 결정 실제 청산 시각. exited_at 존재 = lifecycle OUTCOME_RECORDED 전이 완료.';

COMMIT;
