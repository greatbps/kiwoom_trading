-- Migration 003: strategy_type 컬럼 추가
-- 목적: 모든 전략(SMC_INTRADAY / SWING / CLOSE_BREAKOUT / ...)을 동일 Research Layer로 통합
-- 기존 레코드는 SMC_INTRADAY로 backfill (Phase 4A 구현 당시 경로가 SMC뿐이었음)

BEGIN;

ALTER TABLE research.candidates
    ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(30) NOT NULL DEFAULT 'SMC_INTRADAY';

ALTER TABLE research.decision_ledger
    ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(30) NOT NULL DEFAULT 'SMC_INTRADAY';

-- 기존 레코드 백필 (이미 DEFAULT로 처리되나 명시적으로 남김)
UPDATE research.candidates    SET strategy_type = 'SMC_INTRADAY' WHERE strategy_type = 'SMC_INTRADAY';
UPDATE research.decision_ledger SET strategy_type = 'SMC_INTRADAY' WHERE strategy_type = 'SMC_INTRADAY';

COMMENT ON COLUMN research.candidates.strategy_type    IS 'SMC_INTRADAY / SWING / CLOSE_BREAKOUT 등 전략 구분';
COMMENT ON COLUMN research.decision_ledger.strategy_type IS 'SMC_INTRADAY / SWING / CLOSE_BREAKOUT 등 전략 구분';

COMMIT;
