-- Migration 006: HTS 조건검색 Strategy Dataset 테이블 4개 추가
-- 목적: WI-9~11이 [STRATEGY_MONITOR]/[EVIDENCE_ATTR] 로그로만 남기던 seq 32-39
--       Candidate -> Router -> Monitor -> Signal 흐름을 research 스키마
--       테이블로 구조화 저장한다 (로그 파싱 없이 SQL로 집계 가능하게).
-- 배경: WI-12에서 과거 시점 조건검색 재현이 불가능하다고 확정됐다(HTS/OpenAPI
--       어느 세대에도 historical search API 없음). 그래서 2026-08-11부터
--       쌓이는 Live 데이터가 유일한 소스이고, 이걸 놓치지 않고 구조화 저장하는
--       것이 WI-13의 목적이다.
-- 범위: research.candidates/decision_ledger(Signal Orchestrator/execute_buy
--       Gate 전용, 완전히 다른 파이프라인 단계)는 건드리지 않는다. 새 테이블만
--       추가한다.
-- 설계: research.future_return_events와 동일하게 signal_outcomes도 고정 컬럼
--       대신 horizon별 Event 모델을 쓴다(DATA_CONTRACT.md "전역 설계 결정"
--       원칙과 일관성 유지, 신규 horizon 추가 시 DDL 불필요).

BEGIN;

-- ============================================================
-- 1. CONDITION CANDIDATES
-- ============================================================
-- HTS 조건검색 seq 32-39가 실제로 찾은 종목. 한 종목이 여러 seq에 동시
-- 매칭되면 seq별로 별도 행을 만든다(Router가 이미 seq마다 개별 호출되는
-- 구조와 1:1 대응). condition_sources에 당시 동시매칭된 전체 seq 목록을
-- 같이 저장해 소스 유실 없이 재구성 가능하게 한다.

CREATE TABLE IF NOT EXISTS research.condition_candidates (
    candidate_id       UUID         PRIMARY KEY DEFAULT research.gen_uuid_v7(),

    observed_at         TIMESTAMPTZ  NOT NULL,
    stock_code          VARCHAR(10)  NOT NULL,
    stock_name          VARCHAR(50),
    market              VARCHAR(10),

    condition_seq       SMALLINT     NOT NULL CHECK (condition_seq BETWEEN 32 AND 39),
    condition_name      VARCHAR(50)  NOT NULL,
    -- 같은 관측 시점에 동시 매칭된 전체 seq 목록(denormalized, source 유실 방지)
    condition_sources   JSONB        NOT NULL DEFAULT '[]',

    source              VARCHAR(50)  NOT NULL DEFAULT 'kiwoom_hts_condition_search',
    trace_id            VARCHAR(25),

    schema_version       SMALLINT    NOT NULL DEFAULT 1,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by            VARCHAR(50) NOT NULL DEFAULT 'strategy_monitor_router'
);

CREATE INDEX IF NOT EXISTS idx_cc_stock_observed
    ON research.condition_candidates (stock_code, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_cc_condition_seq
    ON research.condition_candidates (condition_seq);
CREATE INDEX IF NOT EXISTS idx_cc_observed_at
    ON research.condition_candidates (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_cc_trace_id
    ON research.condition_candidates (trace_id);

COMMENT ON TABLE research.condition_candidates IS
    'HTS 조건검색 seq 32-39 Candidate 원본 이벤트. research.candidates(Signal '
    'Orchestrator ACCEPT)와는 다른 파이프라인 단계 — 혼동 금지. WI-13.';
COMMENT ON COLUMN research.condition_candidates.condition_sources IS
    '같은 관측 시점에 동시 매칭된 전체 seq 목록(예: [32,33,37]). 이 행 자체는 '
    'condition_seq 하나에 대응하지만, 멀티소스 attribution을 재구성하려면 이 '
    '컬럼을 참조한다.';

-- ============================================================
-- 2. STRATEGY MONITOR EVENTS
-- ============================================================
-- Router가 실제로 호출한 Monitor의 결과(analyzers/strategy_monitors/*).

CREATE TABLE IF NOT EXISTS research.strategy_monitor_events (
    event_id            UUID         PRIMARY KEY DEFAULT research.gen_uuid_v7(),
    candidate_id         UUID        NOT NULL
                             REFERENCES research.condition_candidates(candidate_id),

    observed_at          TIMESTAMPTZ NOT NULL,   -- candidates.observed_at 복사(조인 없이 시간 질의)
    stock_code            VARCHAR(10) NOT NULL,   -- candidates.stock_code 복사
    condition_seq         SMALLINT    NOT NULL,

    monitor_name          VARCHAR(50) NOT NULL,   -- 'MomentumMonitor' 등
    monitor_version        VARCHAR(20) NOT NULL DEFAULT 'v1',
    monitor_status         VARCHAR(20) NOT NULL
                               CHECK (monitor_status IN
                                   ('SIGNAL', 'NO_SIGNAL', 'ERROR', 'NOT_IMPLEMENTED')),
    monitor_state          VARCHAR(40),            -- 세부 상태(MOMENTUM_CONFIRMING 등)
    monitor_result         JSONB,                  -- reasons/data_quality 등 원본 결과
    monitor_error          TEXT,

    schema_version          SMALLINT   NOT NULL DEFAULT 1,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by                VARCHAR(50) NOT NULL DEFAULT 'strategy_monitor_router'
);

CREATE INDEX IF NOT EXISTS idx_sme_candidate_id
    ON research.strategy_monitor_events (candidate_id);
CREATE INDEX IF NOT EXISTS idx_sme_condition_seq
    ON research.strategy_monitor_events (condition_seq);
CREATE INDEX IF NOT EXISTS idx_sme_monitor_status
    ON research.strategy_monitor_events (monitor_status);
CREATE INDEX IF NOT EXISTS idx_sme_observed_at
    ON research.strategy_monitor_events (observed_at DESC);

COMMENT ON TABLE research.strategy_monitor_events IS
    'Strategy Monitor Router 호출 결과. monitor_status=NOT_IMPLEMENTED는 seq '
    '34/35/39(EOD/Trend/ITS) — 다른 Monitor로 fallback하지 않았다는 증거로 '
    '고정한다. WI-13.';

-- ============================================================
-- 3. STRATEGY SIGNALS
-- ============================================================
-- monitor_status='SIGNAL'인 경우만 생성.

CREATE TABLE IF NOT EXISTS research.strategy_signals (
    signal_id             UUID        PRIMARY KEY DEFAULT research.gen_uuid_v7(),
    candidate_id           UUID       NOT NULL
                               REFERENCES research.condition_candidates(candidate_id),
    monitor_event_id        UUID      NOT NULL
                               REFERENCES research.strategy_monitor_events(event_id),

    observed_at              TIMESTAMPTZ NOT NULL,
    stock_code                VARCHAR(10) NOT NULL,
    condition_seq              SMALLINT   NOT NULL,
    strategy_name               VARCHAR(50) NOT NULL,

    signal_type                  VARCHAR(40),   -- 신호 시점 monitor_state
    signal_score                  NUMERIC(12,4),
    entry_price_reference          NUMERIC(12,2) CHECK (entry_price_reference > 0),
    signal_reason                   TEXT,

    schema_version                  SMALLINT   NOT NULL DEFAULT 1,
    created_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by                        VARCHAR(50) NOT NULL DEFAULT 'strategy_monitor_router'
);

CREATE INDEX IF NOT EXISTS idx_ss_candidate_id
    ON research.strategy_signals (candidate_id);
CREATE INDEX IF NOT EXISTS idx_ss_condition_seq
    ON research.strategy_signals (condition_seq);
CREATE INDEX IF NOT EXISTS idx_ss_observed_at
    ON research.strategy_signals (observed_at DESC);

COMMENT ON TABLE research.strategy_signals IS
    'Strategy Monitor가 signal=True를 반환한 경우만 저장. 이 자체가 매매 '
    '판단이 아니라 관측 기록이다(execute_buy와 무관). WI-13.';

-- ============================================================
-- 4. SIGNAL OUTCOMES (Event 모델 — future_return_events와 동일 설계)
-- ============================================================

CREATE TABLE IF NOT EXISTS research.signal_outcomes (
    outcome_event_id       UUID        PRIMARY KEY DEFAULT research.gen_uuid_v7(),
    signal_id                UUID      NOT NULL
                                 REFERENCES research.strategy_signals(signal_id),
    stock_code                 VARCHAR(10) NOT NULL,

    horizon_label                VARCHAR(10) NOT NULL,  -- '+1D','+2D','+3D','+5D'
    reference_price                NUMERIC(12,2) NOT NULL CHECK (reference_price > 0),
    price_at_horizon                 NUMERIC(12,2),
    return_pct                         NUMERIC(8,4),
    mfe_pct                              NUMERIC(8,4),  -- Maximum Favorable Excursion
    mae_pct                                NUMERIC(8,4),-- Maximum Adverse Excursion

    schema_version                          SMALLINT   NOT NULL DEFAULT 1,
    recorded_at                              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by                                VARCHAR(50) NOT NULL DEFAULT 'condition_signal_outcome_collector',

    UNIQUE (signal_id, horizon_label)
);

CREATE INDEX IF NOT EXISTS idx_so_signal_id
    ON research.signal_outcomes (signal_id);
CREATE INDEX IF NOT EXISTS idx_so_horizon_label
    ON research.signal_outcomes (horizon_label);

COMMENT ON TABLE research.signal_outcomes IS
    '비동기 사후 가격경로 이벤트(Event 모델, future_return_events와 동일 '
    '설계 — 신규 horizon 추가 시 DDL 불필요). WI-13.';

-- 기록 후 불변 (future_return_events와 동일 패턴)
CREATE OR REPLACE FUNCTION research.prevent_signal_outcome_update()
RETURNS TRIGGER LANGUAGE plpgsql AS
$func$
BEGIN
    RAISE EXCEPTION USING MESSAGE = FORMAT(
        'signal_outcomes are immutable: signal_id=%s horizon=%s',
        OLD.signal_id, OLD.horizon_label
    );
END;
$func$;

CREATE TRIGGER signal_outcomes_immutability
    BEFORE UPDATE ON research.signal_outcomes
    FOR EACH ROW EXECUTE FUNCTION research.prevent_signal_outcome_update();

CREATE OR REPLACE RULE no_delete_signal_outcomes AS
    ON DELETE TO research.signal_outcomes DO INSTEAD NOTHING;

COMMIT;
