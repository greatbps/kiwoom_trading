-- Migration 005: execute_buy() 실행단계 REJECT reason code 8개 추가
-- 목적: execute_buy() 내부 조기 return(자금부족/중복보유/최대보유수/리스크차단/
--       쿨다운/API실패/주문실패 등)이 Decision Ledger에 REJECT로 기록될 수 있도록
--       research.reason_dictionary에 신규 코드 등록 (FK 제약, 미등록 시 기록 자체가 조용히 실패함)
-- 배경: docs/FINAL_OPERATIONAL_AUDIT_REPORT.md Audit 7 — execute_buy() 25~37곳 Decision Ledger 누락

BEGIN;

INSERT INTO research.reason_dictionary
    (reason_code, category, description, created_by)
VALUES
    ('COOLDOWN_ACTIVE',       'risk',      '연패/재진입 쿨다운 중 (TRADE_CD/COOLDOWN_BLOCK 등)',        'migration_005'),
    ('RISK_BLOCKED',          'risk',      'execute_buy() 내부 리스크 게이트 차단 (BAN_LIST/LSG/DRIFT/DB_HARD_STOP 등)', 'migration_005'),
    ('DUPLICATE_POSITION',    'execution', '동일 종목 이미 보유 중 — 추가 매수 금지',                    'migration_005'),
    ('MAX_POSITIONS',         'execution', '최대 보유 종목 수 / 일일 거래 횟수 한도 초과',               'migration_005'),
    ('INSUFFICIENT_CAPITAL',  'execution', '잔고/현금비율 부족으로 포지션 크기 산출 불가 (0주 등)',       'migration_005'),
    ('ENTRY_QUALITY_BLOCKED', 'technical', 'Entry Quality 필터 차단 (RVOL/EMA9/VWAP/수급/위치/레짐/ML 등)', 'migration_005'),
    ('API_FAILURE',           'system',    '매수 주문 API 호출 자체 실패 (예외 발생)',                   'migration_005'),
    ('ORDER_FAILURE',         'system',    '매수 주문 API 응답 실패 (return_code != 0)',                 'migration_005')
ON CONFLICT (reason_code) DO NOTHING;

COMMIT;
