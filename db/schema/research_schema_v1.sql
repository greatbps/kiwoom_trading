-- ============================================================
-- Trading Research OS — Research Schema DDL v1.0
-- ============================================================
-- 날짜    : 2026-06-30
-- 승인    : GD-007 (research 스키마 예외 허용)
-- 계약    : DATA_CONTRACT.md v2.0
-- 설계 원칙: Research Reproducibility > Performance Optimization
--
-- 전제 조건:
--   PostgreSQL 13+
--   pgcrypto 확장 (gen_random_bytes 사용)
--
-- 실행 순서:
--   1. pgcrypto 확인
--   2. research 스키마
--   3. UUIDv7 함수
--   4. reason_dictionary (+ 시드 데이터)
--   5. candidates
--   6. decision_ledger (+ immutability trigger)
--   7. future_return_events (+ outcome view)
--   8. event_store
--   9. Permissions 주석 (환경별 조정 필요)
-- ============================================================

-- 0. 전제 확인
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- 1. SCHEMA
-- ============================================================

CREATE SCHEMA IF NOT EXISTS research;

COMMENT ON SCHEMA research IS
    'Trading Research OS — Decision Intelligence Platform. '
    'GD-007 (2026-06-30) 승인. 운영 스키마(public)와 완전 격리.';

-- ============================================================
-- 2. UUID v7 GENERATOR
-- ============================================================
-- 시간순 정렬 가능한 UUID.
-- 형식: [48비트 ms timestamp][4비트 version=7][12비트 random]
--       [2비트 variant=10][62비트 random]
-- 이점: Event Replay, 백테스트 Import, 분산 환경에서 PK 충돌 없음.

CREATE OR REPLACE FUNCTION research.gen_uuid_v7()
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    ms  BIGINT;
    b   BYTEA;
BEGIN
    ms := floor(extract(epoch from clock_timestamp()) * 1000)::BIGINT;
    b  := decode(lpad(to_hex(ms), 12, '0'), 'hex') || gen_random_bytes(10);
    b  := set_byte(b, 6, (get_byte(b, 6) & x'0f'::int) | x'70'::int);  -- version = 7
    b  := set_byte(b, 8, (get_byte(b, 8) & x'3f'::int) | x'80'::int);  -- variant = 10xx
    RETURN encode(b, 'hex')::uuid;
END;
$$;

COMMENT ON FUNCTION research.gen_uuid_v7() IS
    'UUIDv7: 48비트 ms timestamp prefix로 시간순 정렬 보장. pgcrypto 필요.';

-- ============================================================
-- 3. REASON DICTIONARY
-- ============================================================
-- DB ENUM 대신 Dictionary 테이블.
-- 신규 Reason Code 추가 = INSERT 한 줄, DDL 변경 없음.

CREATE TABLE IF NOT EXISTS research.reason_dictionary (
    reason_code    VARCHAR(50)  PRIMARY KEY,
    category       VARCHAR(30)  NOT NULL
                       CHECK (category IN (
                           'execution', 'technical', 'scoring',
                           'risk', 'system', 'data', 'policy', 'misc'
                       )),
    description    TEXT         NOT NULL,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    schema_version SMALLINT     NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by     VARCHAR(50)  NOT NULL DEFAULT 'system'
);

COMMENT ON TABLE research.reason_dictionary IS
    'Decision Reason Code 사전. ENUM 대신 사용 — 코드 추가 시 DDL 불필요. '
    'is_active=FALSE로 비활성화 (삭제 금지 — FK 무결성).';

-- 시드: 초기 13개 reason code
INSERT INTO research.reason_dictionary
    (reason_code, category, description, created_by)
VALUES
    ('PASS',                  'execution', '모든 조건 충족, 진입',                            'init'),
    ('CHOCH_MISSING',         'technical', '구조 전환(CHoCH) 미확인',                        'init'),
    ('SWEEP_MISSING',         'technical', '유동성 스윕 미탐지',                              'init'),
    ('FVG_MISSING',           'technical', 'Fair Value Gap 없음',                             'init'),
    ('VOLUME_INSUFFICIENT',   'technical', '거래량 기준 미달',                                'init'),
    ('CONFIDENCE_LOW',        'scoring',   '신뢰도 임계값 미달',                              'init'),
    ('RISK_SCORE_HIGH',       'risk',      '리스크 점수 초과',                                'init'),
    ('GLOBAL_GATE_BLOCKED',   'system',    '시스템 전역 차단 (Kill Switch / Daily Loss 등)', 'init'),
    ('STOCK_GATE_BLOCKED',    'system',    '종목 단위 차단 (손절 이력 / 쿨다운 등)',         'init'),
    ('MARKET_SENSOR_BLOCKED', 'system',    'Market Sensor 차단 (EF 누적)',                    'init'),
    ('DATA_INSUFFICIENT',     'data',      '데이터 부족 (봉 수 / 데이터 품질 미달)',          'init'),
    ('POLICY_MISMATCH',       'policy',    '정책 버전 조건 불충족',                           'init'),
    ('OTHER',                 'misc',      '분류 불가 — 비율 >5% 시 신규 코드 추가 검토',   'init')
ON CONFLICT (reason_code) DO NOTHING;

-- ============================================================
-- 4. CANDIDATES
-- ============================================================
-- Signal Orchestrator ACCEPT 시점 종목 스냅샷.
-- Decision이 만들어지기 전 단계. Candidate 1건 = Decision 1건.

CREATE TABLE IF NOT EXISTS research.candidates (
    candidate_id           UUID         PRIMARY KEY DEFAULT research.gen_uuid_v7(),
    session_id             INTEGER      REFERENCES trading_sessions(id),
    market_context_id      INTEGER      REFERENCES market_context(id),
    stock_code             VARCHAR(10)  NOT NULL,
    stock_name             VARCHAR(50),
    sector                 VARCHAR(50),
    market                 VARCHAR(10)  CHECK (market IN ('KOSPI', 'KOSDAQ')),

    -- 관찰 시점 스냅샷 (IMMUTABLE)
    observed_at            TIMESTAMPTZ  NOT NULL,
    price                  NUMERIC(12,2) NOT NULL CHECK (price > 0),
    rs_score               NUMERIC(6,2),
    rvol                   NUMERIC(8,4),
    atr_pct                NUMERIC(7,4),
    regime                 VARCHAR(30),
    ema_gap_pct            NUMERIC(7,4),
    orchestrator_score     NUMERIC(7,4),
    orchestrator_accept_reason TEXT,

    -- Lifecycle
    lifecycle_status       VARCHAR(20)  NOT NULL DEFAULT 'CREATED'
                               CHECK (lifecycle_status IN ('CREATED', 'EVALUATED', 'ARCHIVED')),

    -- Trace ID: TR-YYYYMMDD-NNNNNN — 운영 로그와 1:1 매칭, grep으로 전체 흐름 추적
    trace_id               VARCHAR(25),

    -- Versioning Metadata
    schema_version         SMALLINT     NOT NULL DEFAULT 1,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by             VARCHAR(50)  NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_candidates_stock_session
    ON research.candidates (stock_code, session_id);
CREATE INDEX IF NOT EXISTS idx_candidates_observed_at
    ON research.candidates (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_lifecycle
    ON research.candidates (lifecycle_status);

COMMENT ON TABLE research.candidates IS
    'Signal Orchestrator ACCEPT 시점 종목 스냅샷. Decision의 선행 레코드.';
COMMENT ON COLUMN research.candidates.observed_at IS
    '파이프라인 진입 시각 — IMMUTABLE.';

-- observed_at, stock_code, candidate_id 불변 보호
CREATE OR REPLACE FUNCTION research.prevent_candidate_core_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.candidate_id  IS DISTINCT FROM NEW.candidate_id  OR
       OLD.stock_code    IS DISTINCT FROM NEW.stock_code    OR
       OLD.observed_at   IS DISTINCT FROM NEW.observed_at   THEN
        RAISE EXCEPTION
            'candidates: candidate_id, stock_code, observed_at are immutable. candidate_id=%',
            OLD.candidate_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER candidates_core_immutability
    BEFORE UPDATE ON research.candidates
    FOR EACH ROW EXECUTE FUNCTION research.prevent_candidate_core_update();

-- ============================================================
-- 5. DECISION LEDGER
-- ============================================================
-- 모든 의사결정의 불변 원장.
-- FROZEN 상태로 INSERT됨 (CREATED 상태는 애플리케이션 메모리에만 존재).
-- 핵심 필드는 DB 트리거로 수정 차단.

CREATE TABLE IF NOT EXISTS research.decision_ledger (
    decision_id          UUID         PRIMARY KEY DEFAULT research.gen_uuid_v7(),
    candidate_id         UUID         NOT NULL REFERENCES research.candidates(candidate_id),

    -- ── 4개 시간축 (Decision Latency = decided_at - observed_at) ──
    observed_at          TIMESTAMPTZ  NOT NULL,   -- candidates.observed_at 복사 (재현성용)
    decided_at           TIMESTAMPTZ  NOT NULL,   -- execute_buy gate 판단 시각
    executed_at          TIMESTAMPTZ,             -- Kiwoom 주문 접수 시각 (PASS only)
    recorded_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),  -- DB INSERT 시각

    -- ── 의사결정 (FROZEN 이후 불변) ──────────────────────────────
    decision             VARCHAR(20)  NOT NULL
                             CHECK (decision IN ('PASS', 'REJECT', 'SHADOW_PASS', 'SHADOW_REJECT')),
    decision_reason_code VARCHAR(50)  NOT NULL
                             REFERENCES research.reason_dictionary(reason_code),
    policy_version       VARCHAR(50)  NOT NULL,

    -- ── Feature Snapshot (당시 현실 보존, 재계산 금지) ───────────
    -- 필수 키: choch_grade, choch_detected, sweep_detected, sweep_type,
    --          sweep_distance_pct, fvg_detected, rvol, atr_pct, regime,
    --          rs_score, ema_gap_pct, htf_trend, structure_trend,
    --          risk_score, expected_rr, sector, market_context_id
    feature_snapshot     JSONB        NOT NULL,

    -- ── 점수 (feature_snapshot 핵심값 별도 컬럼 — 빠른 집계용) ──
    confidence           NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    expected_rr          NUMERIC(7,3),
    risk_score           NUMERIC(5,4) CHECK (risk_score BETWEEN 0 AND 1),

    -- ── 식별 편의 (Joins 최소화) ─────────────────────────────────
    stock_code           VARCHAR(10)  NOT NULL,   -- candidates.stock_code 복사

    -- ── 결과 (나중에 채워짐, 수정 허용) ─────────────────────────
    execution_result     JSONB,        -- {trade_id, order_no, executed_price, slippage_pct}
    shadow_results       JSONB,        -- [{policy_id, decision, reason}]
    decision_verdict     TEXT,         -- LLM 자연어 판결문 (15:30 배치 생성)

    -- ── Dual Auditor 평가 (나중에 채워짐) ───────────────────────
    decision_auditor_result  JSONB,   -- {quality: RATIONAL|QUESTIONABLE, notes}
    outcome_auditor_result   JSONB,   -- {quality: CORRECT|LUCKY|UNLUCKY|WRONG, notes}

    -- ── Lifecycle State Machine ───────────────────────────────────
    -- PASS  경로: FROZEN → EXECUTED → OUTCOME_RECORDED → AUDIT_COMPLETED → KNOWLEDGE_EXTRACTED
    -- REJECT 경로: FROZEN → OUTCOME_PENDING → OUTCOME_RECORDED → AUDIT_COMPLETED → KNOWLEDGE_EXTRACTED
    lifecycle_status     VARCHAR(30)  NOT NULL DEFAULT 'FROZEN'
                             CHECK (lifecycle_status IN (
                                 'FROZEN',
                                 'EXECUTED',
                                 'EXECUTION_FAILED',
                                 'OUTCOME_PENDING',
                                 'OUTCOME_RECORDED',
                                 'AUDIT_COMPLETED',
                                 'KNOWLEDGE_EXTRACTED'
                             )),

    -- ── Trace ID (운영 로그와 1:1 매칭) ─────────────────────────
    trace_id             VARCHAR(25),   -- TR-YYYYMMDD-NNNNNN

    -- ── Versioning Metadata ───────────────────────────────────────
    decision_schema_version  SMALLINT  NOT NULL DEFAULT 1,
    schema_version           SMALLINT  NOT NULL DEFAULT 1,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by               VARCHAR(50) NOT NULL DEFAULT 'system'
);

-- Indexes (연구 쿼리 최적화)
CREATE INDEX IF NOT EXISTS idx_dl_decided_at
    ON research.decision_ledger (decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_dl_decision
    ON research.decision_ledger (decision);
CREATE INDEX IF NOT EXISTS idx_dl_reason_code
    ON research.decision_ledger (decision_reason_code);
CREATE INDEX IF NOT EXISTS idx_dl_stock_decided
    ON research.decision_ledger (stock_code, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_dl_lifecycle
    ON research.decision_ledger (lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_dl_feature_snapshot
    ON research.decision_ledger USING GIN (feature_snapshot);
CREATE INDEX IF NOT EXISTS idx_dl_policy_version
    ON research.decision_ledger (policy_version);
CREATE INDEX IF NOT EXISTS idx_dl_trace_id
    ON research.decision_ledger (trace_id);
CREATE INDEX IF NOT EXISTS idx_candidates_trace_id
    ON research.candidates (trace_id);

COMMENT ON TABLE research.decision_ledger IS
    'IMMUTABLE 의사결정 원장. FROZEN 이후 핵심 필드 변경 불가 (트리거 강제). '
    'GD-007 / DATA_CONTRACT v2.0 기준.';
COMMENT ON COLUMN research.decision_ledger.feature_snapshot IS
    '결정 시점 모든 feature 스냅샷. 계산 가능한 값도 전부 저장 (재현성 원칙). '
    '3개월 후 계산식이 바뀌어도 당시 값을 재현할 수 있어야 한다.';
COMMENT ON COLUMN research.decision_ledger.observed_at IS
    'candidates.observed_at 복사본. Join 없이 Decision Latency (decided_at - observed_at) 계산 가능.';
COMMENT ON COLUMN research.decision_ledger.decision_schema_version IS
    'feature_snapshot 스키마가 변경될 때 단조증가. 마이그레이션 시 구버전 데이터 해석 기준.';

-- ── Immutability Trigger ─────────────────────────────────────────────
-- 보호 필드 (변경 시 EXCEPTION): decision, decision_reason_code, feature_snapshot,
--     policy_version, confidence, expected_rr, risk_score,
--     decided_at, observed_at, candidate_id, stock_code, decision_schema_version
-- 허용 UPDATE: lifecycle_status, execution_result, executed_at,
--     decision_verdict, shadow_results, decision_auditor_result, outcome_auditor_result

CREATE OR REPLACE FUNCTION research.prevent_decision_core_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.decision                 IS DISTINCT FROM NEW.decision                 OR
       OLD.decision_reason_code     IS DISTINCT FROM NEW.decision_reason_code     OR
       OLD.feature_snapshot         IS DISTINCT FROM NEW.feature_snapshot         OR
       OLD.policy_version           IS DISTINCT FROM NEW.policy_version           OR
       OLD.confidence               IS DISTINCT FROM NEW.confidence               OR
       OLD.expected_rr              IS DISTINCT FROM NEW.expected_rr              OR
       OLD.risk_score               IS DISTINCT FROM NEW.risk_score               OR
       OLD.decided_at               IS DISTINCT FROM NEW.decided_at               OR
       OLD.observed_at              IS DISTINCT FROM NEW.observed_at              OR
       OLD.candidate_id             IS DISTINCT FROM NEW.candidate_id             OR
       OLD.stock_code               IS DISTINCT FROM NEW.stock_code               OR
       OLD.decision_schema_version  IS DISTINCT FROM NEW.decision_schema_version  THEN
        RAISE EXCEPTION
            'decision_ledger: immutable fields cannot be modified after FROZEN. '
            'decision_id=%, user=%',
            OLD.decision_id, current_user;
    END IF;
    -- lifecycle_status 역방향 전이 방지 (KNOWLEDGE_EXTRACTED는 종착점)
    IF OLD.lifecycle_status = 'KNOWLEDGE_EXTRACTED'
       AND NEW.lifecycle_status != 'KNOWLEDGE_EXTRACTED' THEN
        RAISE EXCEPTION
            'decision_ledger: cannot revert lifecycle from KNOWLEDGE_EXTRACTED. decision_id=%',
            OLD.decision_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER decision_ledger_immutability
    BEFORE UPDATE ON research.decision_ledger
    FOR EACH ROW EXECUTE FUNCTION research.prevent_decision_core_update();

-- DELETE 차단 (REVOKE로도 설정, Rule은 이중 방어)
CREATE OR REPLACE RULE no_delete_decision_ledger AS
    ON DELETE TO research.decision_ledger DO INSTEAD NOTHING;

-- ============================================================
-- 6. FUTURE RETURN EVENTS
-- ============================================================
-- Event 모델: 고정 컬럼(return_30m, return_eod, ...) 대신
-- horizon별 행으로 저장 → +10D, +20D 등 추가 시 DDL 불필요.

CREATE TABLE IF NOT EXISTS research.future_return_events (
    event_id         UUID         PRIMARY KEY DEFAULT research.gen_uuid_v7(),
    decision_id      UUID         NOT NULL
                         REFERENCES research.decision_ledger(decision_id),
    stock_code       VARCHAR(10)  NOT NULL,

    -- Horizon 정의
    horizon_label    VARCHAR(10)  NOT NULL,   -- '+30m', '+EOD', '+1D', '+3D', '+5D', '+10D', '+20D'
    horizon_minutes  INTEGER,                 -- NULL = EOD

    -- Return 데이터
    decision_price   NUMERIC(12,2) NOT NULL CHECK (decision_price > 0),
    price_at_horizon NUMERIC(12,2),
    return_pct       NUMERIC(8,4)  NOT NULL,  -- 계산식: (price_at_horizon - decision_price) / decision_price * 100

    -- Metadata
    schema_version   SMALLINT     NOT NULL DEFAULT 1,
    recorded_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by       VARCHAR(50)  NOT NULL DEFAULT 'returns_collector',

    -- 동일 Decision × Horizon 중복 방지
    UNIQUE (decision_id, horizon_label)
);

CREATE INDEX IF NOT EXISTS idx_fre_decision_id
    ON research.future_return_events (decision_id);
CREATE INDEX IF NOT EXISTS idx_fre_horizon_label
    ON research.future_return_events (horizon_label);
CREATE INDEX IF NOT EXISTS idx_fre_recorded_at
    ON research.future_return_events (recorded_at DESC);

COMMENT ON TABLE research.future_return_events IS
    '비동기 사후 수익률 이벤트. Event 모델로 신규 horizon 추가 시 DDL 불필요. '
    '표준 horizon: +30m, +EOD, +1D, +3D, +5D, +10D, +20D, +60D';

-- 기록 후 불변
-- UPDATE: BEFORE TRIGGER (PostgreSQL ON CONFLICT는 UPDATE RULE과 충돌 — Trigger로 대체)
-- DELETE: RULE (DELETE RULE은 ON CONFLICT와 무관)
CREATE OR REPLACE FUNCTION research.prevent_future_return_update()
RETURNS TRIGGER LANGUAGE plpgsql AS
$func$
BEGIN
    RAISE EXCEPTION USING MESSAGE = FORMAT(
        'future_return_events are immutable: decision_id=%s horizon=%s',
        OLD.decision_id, OLD.horizon_label
    );
END;
$func$;

CREATE TRIGGER future_return_events_immutability
    BEFORE UPDATE ON research.future_return_events
    FOR EACH ROW EXECUTE FUNCTION research.prevent_future_return_update();

CREATE OR REPLACE RULE no_delete_future_return_events AS
    ON DELETE TO research.future_return_events DO INSTEAD NOTHING;

-- Outcome Label View (JOIN-free 분석용)
CREATE OR REPLACE VIEW research.decision_outcomes AS
SELECT
    d.decision_id,
    d.candidate_id,
    d.stock_code,
    d.decision,
    d.decision_reason_code,
    d.policy_version,
    d.decided_at,
    d.lifecycle_status,
    d.confidence,
    d.expected_rr,
    d.risk_score,
    fre_30m.return_pct  AS return_30m,
    fre_eod.return_pct  AS return_eod,
    fre_1d.return_pct   AS return_1d,
    fre_3d.return_pct   AS return_3d,
    fre_5d.return_pct   AS return_5d,
    fre_10d.return_pct  AS return_10d,
    CASE
        WHEN d.decision IN ('PASS', 'SHADOW_PASS')  THEN NULL
        WHEN fre_5d.return_pct IS NULL               THEN 'PENDING'
        WHEN fre_5d.return_pct <= -3                 THEN 'EXCELLENT_REJECT'
        WHEN fre_5d.return_pct <= 0                  THEN 'GOOD_REJECT'
        WHEN fre_5d.return_pct < 3                   THEN 'POOR_REJECT'
        WHEN fre_5d.return_pct >= 7                  THEN 'LARGE_OPPORTUNITY_LOSS'
        ELSE                                               'OPPORTUNITY_LOSS'
    END AS outcome_label
FROM research.decision_ledger d
LEFT JOIN research.future_return_events fre_30m
    ON fre_30m.decision_id = d.decision_id AND fre_30m.horizon_label = '+30m'
LEFT JOIN research.future_return_events fre_eod
    ON fre_eod.decision_id = d.decision_id AND fre_eod.horizon_label = '+EOD'
LEFT JOIN research.future_return_events fre_1d
    ON fre_1d.decision_id  = d.decision_id AND fre_1d.horizon_label  = '+1D'
LEFT JOIN research.future_return_events fre_3d
    ON fre_3d.decision_id  = d.decision_id AND fre_3d.horizon_label  = '+3D'
LEFT JOIN research.future_return_events fre_5d
    ON fre_5d.decision_id  = d.decision_id AND fre_5d.horizon_label  = '+5D'
LEFT JOIN research.future_return_events fre_10d
    ON fre_10d.decision_id = d.decision_id AND fre_10d.horizon_label = '+10D';

COMMENT ON VIEW research.decision_outcomes IS
    'outcome_label 자동 계산 뷰. +5D 수익률 기준. horizon 추가 시 뷰만 수정.';

-- ============================================================
-- 7. EVENT STORE
-- ============================================================
-- Research Layer 전체 이벤트 중앙 로그.
-- 이 테이블만으로 Research Layer 상태를 완전히 재현(Replay) 가능해야 한다.

CREATE TABLE IF NOT EXISTS research.event_store (
    event_id      UUID         PRIMARY KEY DEFAULT research.gen_uuid_v7(),
    occurred_at   TIMESTAMPTZ  NOT NULL,
    event_type    VARCHAR(60)  NOT NULL,   -- 'CandidateCreated', 'DecisionFrozen', ...
    entity_type   VARCHAR(30)  NOT NULL,   -- 'candidate', 'decision', 'hypothesis', 'trade', 'audit', 'knowledge'
    entity_id     UUID         NOT NULL,   -- 해당 entity의 실제 PK
    source        VARCHAR(60)  NOT NULL,   -- 발행 모듈: 'signal_orchestrator', 'execute_buy', ...
    payload       JSONB        NOT NULL DEFAULT '{}',
    schema_version SMALLINT    NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by    VARCHAR(50)  NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_event_store_occurred_at
    ON research.event_store (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_store_entity
    ON research.event_store (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_event_store_type
    ON research.event_store (event_type);
CREATE INDEX IF NOT EXISTS idx_event_store_payload
    ON research.event_store USING GIN (payload);

COMMENT ON TABLE research.event_store IS
    'Append-Only 이벤트 로그. Replay / Timeline / Debug / AI Learning의 기반. '
    'entity_id는 반드시 해당 entity의 실제 UUID PK.';

-- Append-Only 강제
CREATE OR REPLACE RULE no_update_event_store AS
    ON UPDATE TO research.event_store DO INSTEAD NOTHING;
CREATE OR REPLACE RULE no_delete_event_store AS
    ON DELETE TO research.event_store DO INSTEAD NOTHING;

-- ============================================================
-- 8. PERMISSIONS (환경별 조정 필요)
-- ============================================================
-- GRANT USAGE ON SCHEMA research TO trading_app;
-- GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA research TO trading_app;
-- -- decision_ledger: 허용 UPDATE 컬럼만 명시
-- GRANT UPDATE (
--     lifecycle_status,
--     execution_result,
--     executed_at,
--     decision_verdict,
--     shadow_results,
--     decision_auditor_result,
--     outcome_auditor_result
-- ) ON research.decision_ledger TO trading_app;
-- -- candidates: lifecycle_status만 UPDATE 허용
-- GRANT UPDATE (lifecycle_status) ON research.candidates TO trading_app;

-- ============================================================
-- 9. MIGRATION NOTE (Phase 3)
-- ============================================================
-- signal_rejections → decision_ledger 마이그레이션
--   파일: scripts/migrate_signal_rejections_to_decision_ledger.py (미구현)
--   파이프라인: old_log → converter → decision_ledger → validator
--
-- 주의사항:
--   - signal_rejections에 feature_snapshot 없음
--     → decision_schema_version=0, feature_snapshot='{"_migrated":true}' 로 표시
--   - candidate 레코드 없음 → 마이그레이션용 placeholder candidate 생성
--   - decision_reason_code 매핑:
--     signal_rejections.rejection_stage → 가장 근접한 reason_code로 변환
--
-- 검증:
--   SELECT count(*) FROM signal_rejections;  -- 원본 건수
--   SELECT count(*) FROM research.decision_ledger WHERE decision_schema_version = 0;  -- 마이그레이션 건수
--   두 값이 동일해야 검증 통과.

-- ============================================================
-- END OF DDL v1.0
-- 다음 단계 (Phase 3):
--   1. database/trading_db.py에 insert_candidate(), insert_decision() 추가
--   2. main_auto_trading.py → execute_buy() 거절 지점에 Decision Ledger INSERT 연결
--   3. scripts/returns_collector.py 구현 (cron: +30m, 15:35, D+3, D+5)
-- ============================================================
