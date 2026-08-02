-- Migration 004: price CHECK 완화 (> 0 → >= 0)
-- 이유: global gate REJECT 시점엔 현재가 미조회 상태라 price=0.0 전달
--       candidates.price / decision_ledger.decision_price 모두 적용

BEGIN;

ALTER TABLE research.candidates
    DROP CONSTRAINT IF EXISTS candidates_price_check,
    ADD CONSTRAINT candidates_price_check CHECK (price >= 0);

ALTER TABLE research.decision_ledger
    DROP CONSTRAINT IF EXISTS decision_ledger_decision_price_check,
    ADD CONSTRAINT decision_ledger_decision_price_check CHECK (decision_price >= 0);

COMMIT;
