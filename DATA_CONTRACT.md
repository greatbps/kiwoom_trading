# Trading OS — Data Contracts v2.0

> CONSTITUTION(Why) → ARCHITECTURE(How) → **DATA_CONTRACT(Contract)** → CLAUDE.md(What)

**v2.0 변경 사항 (2026-06-30)**
- `candidate` 계약 추가 — 평가 파이프라인 진입 전 단계
- `decision_ledger` 계약 추가 — 불변 의사결정 원장 (v1 `decision_log`와 별개, 연구 전용)
- `future_returns` 계약 추가 — 비동기 사후 성과 추적
- `hypothesis` 계약 개정 — version 및 hypothesis_group_id 추가
- Event Bus 테이블 갱신

이 문서는 Trading OS의 객체 간 인터페이스를 정의한다.  
새로운 AI 모델, 새로운 연구 모듈, 새로운 데이터 소스가 추가될 때  
**이 계약을 만족하면 시스템에 연결될 수 있다.**

계약을 만족하지 못하면 연결되지 않는다.

---

## 전역 설계 결정 (Phase 2 Final Freeze — 2026-06-30)

| 항목 | 결정 | 이유 |
|------|------|------|
| **ID 전략** | UUIDv7 (시간순 정렬 가능) | Event Replay / 분산 환경 / 백테스트 Import 시 PK 충돌 방지 |
| **Snapshot 철학** | feature_snapshot에 계산 가능한 값도 전부 저장 | 3개월 후 계산식 변경 시 재현 불가 방지. "당시 현실"을 저장 |
| **Event Time** | 4개 timestamp 필수 (observed_at / decided_at / executed_at / recorded_at) | Decision Latency 계산 가능 |
| **Enum 금지** | reason_dictionary 테이블로 대체 | 신규 Reason 추가 시 DDL 변경 불필요 |
| **future_returns** | 고정 컬럼 아닌 Event 모델 (future_return_events) | +10D, +20D 등 horizon 추가 시 DDL 불필요 |
| **event_store** | 모든 Research 이벤트 중앙 로그 | Replay / Timeline / Debug / AI Learning 기반 |
| **연구 재현성 우선** | 성능 < Research Reproducibility | 6개월 후 의사결정 완벽 재현 가능 여부가 설계 기준 |

---

## 계약 표기 형식

```
CONTRACT: <object_name>
VERSION : <version>

REQUIRED  — 반드시 존재해야 하는 필드 + 타입 + 유효 범위
OPTIONAL  — 있으면 활용되는 필드
INVARIANT — INSERT/UPDATE 후에도 항상 참이어야 하는 조건
IMMUTABLE — 생성 이후 절대 변경 불가 (Article 3 적용)
EVENTS    — 이 객체가 생성/변경될 때 발행하는 이벤트
```

---

## CONTRACT: market_context

```
VERSION: 1.0
SOURCE : analysis/market_intelligence.py (MIE)
TABLE  : market_context
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| context_date | DATE | UNIQUE, ≤ TODAY |
| market_regime | VARCHAR | {'Risk-On', 'Risk-Off', 'Neutral', 'Crisis'} |
| volatility_level | VARCHAR | {'Low', 'Medium', 'High', 'Extreme'} |
| risk_score | SMALLINT | 0 ~ 100 |
| opportunity_score | SMALLINT | 0 ~ 100 |
| recommended_bias | VARCHAR | {'LONG', 'SHORT', 'NEUTRAL', 'AVOID'} |

**OPTIONAL**
`sector_rotation[], major_events[], caution_factors[], watch_sectors[]`  
`kospi_change_pct, kosdaq_change_pct, usdkrw, vix, sp500f_change_pct`  
`briefing_text, raw_data, model_used, generation_time_ms`

**INVARIANT**
- UNIQUE(context_date): 하루에 하나
- context_json JSONB에 required 필드 전부 포함
- context_date에 대응하는 trading_sessions 레코드가 자동 생성됨

**EVENTS**
```
MarketContextCreated  {context_id, date, regime, risk_score, opportunity_score}
```

---

## CONTRACT: trading_session

```
VERSION: 1.0
SOURCE : analysis/market_intelligence.py (MIE가 오픈, session_review.py가 마감)
TABLE  : trading_sessions
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| session_date | DATE | UNIQUE |
| status | VARCHAR | {'open', 'closed', 'reviewed'} |

**OPTIONAL**
`market_context_id, regime, volatility_level`  
`buy_count, sell_count, daily_pnl_pct, daily_pnl_krw`  
`review_text, review_tags[]`

**INVARIANT**
- status 전이: open → closed → reviewed (역방향 불가)
- market_context_id는 동일 날짜의 market_context를 참조
- status='reviewed' 이면 review_text NOT NULL

**EVENTS**
```
SessionOpened   {session_id, date, regime}
SessionReviewed {session_id, date, daily_pnl_pct, review_tags}
```

---

## CONTRACT: decision_log

```
VERSION: 1.0
SOURCE : main_auto_trading.py → _log_decision()
TABLE  : decision_log
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| decision_time | TIMESTAMP | NOT NULL |
| stock_code | VARCHAR | NOT NULL |
| decision_type | VARCHAR | {'BUY', 'SELL', 'SKIP'} |
| signals | JSONB | NOT NULL, ≥ 1 key |
| strategy_version | VARCHAR | NOT NULL |
| model_version | VARCHAR | NOT NULL (P2 재현성) |

**OPTIONAL**
`trade_id, stock_name, filter_results, gate_results`  
`decision_reason, confidence, market_context_id, session_id`  
`prompt_version, research_environment_id`

**INVARIANT**
- Append-Only: UPDATE 금지 (Article 3)
- market_context_id → 당일 market_context.id
- session_id → 당일 trading_sessions.id
- BUY이면 trade_id를 가능한 한 빨리 채움

**IMMUTABLE**
생성 이후 모든 필드

**EVENTS**
```
BuyDecisionLogged   {decision_id, stock_code, confidence, session_id}
SellDecisionLogged  {decision_id, trade_id, reason}
```

---

## CONTRACT: hypothesis

```
VERSION: 2.0
SOURCE : Scientist AI or PM manual
TABLE  : hypotheses
CHANGED: version, hypothesis_group_id 추가 (v2.0)
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| title | TEXT | NOT NULL |
| description | TEXT | NOT NULL |
| status | VARCHAR | state machine (아래 참조) |
| research_environment_id | INTEGER | 활성 RE 참조 필수 |
| version | SMALLINT | ≥ 1, 동일 group 내 단조증가 |
| hypothesis_group_id | VARCHAR | 'HYP-YYYYMMDD-NNN' 형식 |

**OPTIONAL**
`rationale, source_data JSONB, proposed_change JSONB`  
`tags[], parent_hypothesis_id, knowledge_base_id`  
`target_feature, expected_effect, expected_direction`  
`priority_score NUMERIC` (Research Director Rule Engine이 계산)

**INVARIANT**
- 상태 머신: draft → pending_review → queued → backtesting → walk_forward  
  → paper_trading → awaiting_pm → approved | rejected → deployed | archived
- 역방향 전이 불가
- 동일 hypothesis_group_id 내 version은 단조증가 (gap 없음)
- 신규 version 생성 시: 이전 version status → 'superseded' (삭제 아님)
- knowledge_base_id: 이 가설과 관련된 기존 지식 연결
- Scientist AI L0는 hypothesis INSERT 권한 없음 (L1부터)

**IMMUTABLE** (생성 후)
`hypothesis_group_id`, `version`, `title`, `description`, `target_feature`, `expected_effect`

**EVENTS**
```
HypothesisCreated    {hypo_id, group_id, version, title, scientist_level, re_id}
HypothesisSuperseded {old_hypo_id, new_hypo_id, group_id, version}
HypothesisApproved   {hypo_id, experiment_id}
HypothesisRejected   {hypo_id, reason}
```

---

## CONTRACT: knowledge_base

```
VERSION: 1.0
SOURCE : Scientist AI, session_review.py, 또는 PM manual
TABLE  : knowledge_base
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| category | VARCHAR | NOT NULL |
| finding_type | VARCHAR | {'positive', 'negative', 'conditional'} |
| title | TEXT | NOT NULL |
| evidence | JSONB | NOT NULL, 최소 {'n': int} 포함 |
| evidence_level | VARCHAR | {'anecdotal', 'preliminary', 'confirmed', 'strong'} |

**OPTIONAL**
`description, conditions JSONB, tags[]`  
`source_hypothesis_ids[], source_experiment_ids[]`  
`confidence, valid_until, confidence_decay_months`  
`market_regime_scope[], supersedes_id, research_environment_id`  
`p_value, sample_sessions, sample_trades, avg_pnl_pct`

**INVARIANT**
- 삭제 불가: `is_active = FALSE`로만 비활성화 (Article 3)
- evidence_level 기준:
  - anecdotal: n < 20
  - preliminary: 20 ≤ n < 50
  - confirmed: 50 ≤ n < 200
  - strong: n ≥ 200
- 상충되는 새 발견 → supersedes_id로 계보 연결 + 구 항목 is_active=FALSE
- evidence_level='anecdotal' 항목은 Scientist AI 의사결정에 사용 불가

**EVENTS**
```
KnowledgeCreated    {kb_id, category, evidence_level, finding_type}
KnowledgeSuperseded {old_kb_id, new_kb_id}
```

---

## CONTRACT: scientist_predictions

```
VERSION: 1.0
SOURCE : Scientist AI (L1 이상)
TABLE  : scientist_predictions
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| prediction_text | TEXT | NOT NULL, ≤ 500 chars |
| confidence_pct | SMALLINT | 0 ~ 100 |
| target_metric | VARCHAR | NOT NULL |
| evaluation_date | DATE | ≥ created_at + 30 days |
| research_environment_id | INTEGER | 활성 RE 참조 필수 |

**OPTIONAL**
`hypothesis_id, notebook_id, session_id`  
`evaluation_lookback_days (default 90)`

**INVARIANT**
- DB 트리거로 prediction_text, confidence_pct, target_metric 불변 (Article 3)
- evaluation_date ≥ NOW() + 30 days (최소 30일 horizon)
- 결과 필드(outcome_*, evaluated_at)만 evaluation_date 이후 채움
- status 전이: pending → evaluated | expired

**IMMUTABLE** (DB 트리거 강제)
`prediction_text`, `confidence_pct`, `target_metric`

**EVENTS**
```
PredictionCreated   {pred_id, confidence_pct, eval_date, scientist_level}
PredictionEvaluated {pred_id, outcome_correct, calibration_error}
```

---

---

## CONTRACT: candidate

```
VERSION: 1.0
SOURCE : signal_orchestrator.py → SignalOrchestrator (ACCEPT 시점)
TABLE  : research.candidates
```

후보(Candidate)는 평가 파이프라인에 진입한 종목을 나타낸다.  
Decision이 만들어지기 전 단계이며, 하나의 Candidate는 정확히 하나의 Decision을 생성한다.

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| candidate_id | BIGSERIAL | PK |
| session_id | INTEGER | FK → trading_sessions.id |
| market_context_id | INTEGER | FK → market_context.id |
| stock_code | VARCHAR(10) | NOT NULL |
| observed_at | TIMESTAMPTZ | NOT NULL |
| price | NUMERIC | > 0 |
| lifecycle_status | VARCHAR | {'CREATED', 'EVALUATED', 'ARCHIVED'} |

**OPTIONAL**
`stock_name, sector, market (KOSPI/KOSDAQ)`  
`rs_score NUMERIC` (상대강도 0~100)  
`rvol NUMERIC` (상대거래량 배율)  
`atr_pct NUMERIC` (ATR %)  
`regime VARCHAR` (시장 레짐 — 진입 시점 스냅샷)  
`ema_gap_pct NUMERIC` (EMA 이격률)  
`orchestrator_score NUMERIC`  
`orchestrator_accept_reason TEXT`

**INVARIANT**
- observed_at은 생성 후 불변
- lifecycle_status 전이: CREATED → EVALUATED → ARCHIVED (역방향 불가)
- 동일 (session_id, stock_code)에 대해 복수 Candidate 허용 (재평가 추적 목적)

**EVENTS**
```
CandidateCreated   {candidate_id, stock_code, session_id, observed_at}
CandidateEvaluated {candidate_id, decision_id}
```

---

## CONTRACT: decision_ledger

```
VERSION: 1.0
SOURCE : main_auto_trading.py → execute_buy() 진입·거절 시점
TABLE  : research.decision_ledger
```

Decision Ledger는 모든 의사결정의 불변 원장이다.  
FROZEN 이후 어떤 필드도 수정할 수 없다.  
수정이 필요하면 별도 Audit 또는 Knowledge 레코드로 기록한다.

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| decision_id | BIGSERIAL | PK |
| candidate_id | BIGINT | FK → research.candidates.candidate_id |
| decided_at | TIMESTAMPTZ | NOT NULL |
| policy_version | VARCHAR | NOT NULL, e.g. 'SMC_v2.3' |
| decision | VARCHAR | {'PASS', 'REJECT', 'SHADOW_PASS', 'SHADOW_REJECT'} |
| decision_reason_code | VARCHAR | 열거값 (아래 참조) |
| lifecycle_status | VARCHAR | state machine (아래 참조) |

**OPTIONAL**
`market_context_id, session_id`  
`feature_snapshot JSONB` — 결정 시점 모든 feature 스냅샷  
`decision_verdict TEXT` — LLM이 생성하는 자연어 판결문 (배치, 15:30)  
`confidence NUMERIC` (0.0 ~ 1.0)  
`expected_rr NUMERIC` (기대 Risk/Reward)  
`risk_score NUMERIC` (0.0 ~ 1.0)  
`shadow_results JSONB` — Shadow Policy 비교 결과  
`execution_result JSONB` — {'trade_id': int, 'order_no': str, 'executed_price': numeric}  
`decision_auditor_result JSONB` — Decision Auditor 평가  
`outcome_auditor_result JSONB` — Outcome Auditor 평가

**decision_reason_code 열거값**
```
PASS                  — 모든 조건 충족, 진입
CHOCH_MISSING         — 구조 전환(CHoCH) 미확인
SWEEP_MISSING         — 유동성 스윕 미탐지
FVG_MISSING           — Fair Value Gap 없음
VOLUME_INSUFFICIENT   — 거래량 기준 미달
CONFIDENCE_LOW        — 신뢰도 임계값 미달
RISK_SCORE_HIGH       — 리스크 점수 초과
GLOBAL_GATE_BLOCKED   — 시스템 전역 차단 (Kill Switch / Daily Loss 등)
STOCK_GATE_BLOCKED    — 종목 단위 차단 (손절 이력 / 쿨다운 등)
MARKET_SENSOR_BLOCKED — Market Sensor 차단 (EF 누적)
DATA_INSUFFICIENT     — 데이터 부족
POLICY_MISMATCH       — 정책 버전 조건 불충족
OTHER                 — 분류 불가 (비율 > 5% 시 신규 코드 분류 검토)
```

**lifecycle_status 상태 머신**
```
CREATED
  ↓ (즉시, 동일 트랜잭션 내)
FROZEN  ← 이 이후 decision·feature_snapshot·policy_version 불변
  ↓
  ├─ (decision=PASS) → EXECUTED | EXECUTION_FAILED
  └─ (decision=REJECT/SHADOW_*) → OUTCOME_PENDING
         ↓ (비동기 job — +30m/EOD/+3D/+5D 모두 채워지면)
     OUTCOME_RECORDED
         ↓ (Dual Auditor 평가 완료)
     AUDIT_COMPLETED
         ↓ (Knowledge 추출 후)
     KNOWLEDGE_EXTRACTED
```

**IMMUTABLE** (lifecycle_status = FROZEN 이후 DB 트리거로 강제)
`decision`, `decision_reason_code`, `feature_snapshot`, `policy_version`,  
`confidence`, `expected_rr`, `risk_score`, `decided_at`, `candidate_id`

**INVARIANT**
- INSERT 권한만 허용: `REVOKE UPDATE, DELETE ON research.decision_ledger FROM trading_app`
- FROZEN 전이는 INSERT와 동일 트랜잭션 내에서 처리 (CREATED 상태가 외부에 노출되지 않음)
- decision=PASS 이면 execution_result.trade_id를 가능한 한 빨리 채움
- decision_reason_code ≠ 'OTHER' 비율 ≥ 95% 유지 (INVARIANT 위반 시 로그 경고)
- feature_snapshot에 최소 포함: `choch_grade, sweep_detected, rvol, atr_pct, regime`

**EVENTS**
```
DecisionCreated   {decision_id, candidate_id, stock_code, decision, policy_version}
DecisionFrozen    {decision_id, stock_code, decision, reason_code}
DecisionExecuted  {decision_id, trade_id, executed_price}
OutcomeRecorded   {decision_id, return_eod, return_5d}
AuditCompleted    {decision_id, decision_quality, outcome_quality}
```

---

## CONTRACT: future_return_events

```
VERSION: 1.0
SOURCE : cron job — scripts/returns_collector.py
TABLE  : research.future_return_events
DESIGN : Event 모델 (고정 컬럼 아님) — 신규 horizon 추가 시 DDL 불필요
```

Decision 1건당 horizon별 수익률 이벤트를 별도 행으로 저장한다.  
고정 컬럼(`return_30m`, `return_eod`, ...) 대신 Event 모델을 사용하여  
`+10D`, `+20D`, `+60D` 등 신규 horizon이 추가돼도 스키마 변경이 없다.

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| event_id | UUID | PK, UUIDv7 |
| decision_id | UUID | FK → research.decision_ledger.decision_id |
| stock_code | VARCHAR(10) | NOT NULL |
| horizon_label | VARCHAR(10) | '+30m' \| '+EOD' \| '+1D' \| '+3D' \| '+5D' \| '+10D' \| '+20D' 등 |
| decision_price | NUMERIC | NOT NULL, > 0 (결정 시점 가격, 재현성용 복사) |
| return_pct | NUMERIC | NOT NULL, (price_at_horizon - decision_price) / decision_price × 100 |

**OPTIONAL**
`horizon_minutes INTEGER` — NULL = EOD (장마감 기준)  
`price_at_horizon NUMERIC`

**INVARIANT**
- UNIQUE (decision_id, horizon_label): horizon당 하나의 이벤트
- 기록 후 수정·삭제 불가 (Rule로 강제)
- decision=PASS인 경우 기록 선택사항

**outcome_label 계산** (VIEW `research.decision_outcomes`에서 +5D 기준 자동 산출)
```
return_5d ≤ -3%  → EXCELLENT_REJECT
return_5d ≤  0%  → GOOD_REJECT
return_5d <  3%  → POOR_REJECT
return_5d ≥  3%  → OPPORTUNITY_LOSS
return_5d ≥  7%  → LARGE_OPPORTUNITY_LOSS
(PASS 결정)      → NULL
(미집계)         → PENDING
```

**EVENTS**
```
FutureReturnPartial  {decision_id, horizon_label, return_pct}
FutureReturnComplete {decision_id, return_5d, outcome_label}  — +5D 채워질 때
```

---

## CONTRACT: reason_dictionary

```
VERSION: 1.0
SOURCE : DBA / PM (수동 관리), INSERT only
TABLE  : research.reason_dictionary
DESIGN : DB ENUM 대신 Dictionary 테이블 — 신규 Reason 추가 시 DDL 불필요
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| reason_code | VARCHAR(50) | PK |
| category | VARCHAR(30) | {'execution', 'technical', 'scoring', 'risk', 'system', 'data', 'policy', 'misc'} |
| description | TEXT | NOT NULL |

**OPTIONAL**
`is_active BOOLEAN` (기본 TRUE — 비활성화 시 FALSE, 삭제 금지)

**초기 reason_code 목록** (13개)
```
PASS / CHOCH_MISSING / SWEEP_MISSING / FVG_MISSING / VOLUME_INSUFFICIENT /
CONFIDENCE_LOW / RISK_SCORE_HIGH / GLOBAL_GATE_BLOCKED / STOCK_GATE_BLOCKED /
MARKET_SENSOR_BLOCKED / DATA_INSUFFICIENT / POLICY_MISMATCH / OTHER
```

**INVARIANT**
- reason_code PK: 한 번 등록된 코드 변경 불가 (is_active=FALSE로 비활성화)
- `OTHER` 비율 > 5% 시 신규 코드 추가 검토 (CLAUDE.md 체크리스트 항목)
- reason_code 삭제 금지 (과거 decision_ledger의 FK 무결성)

---

## CONTRACT: event_store

```
VERSION: 1.0
SOURCE : 모든 Research Layer 컴포넌트
TABLE  : research.event_store
DESIGN : Append-Only. Research Layer 전체 이벤트의 중앙 로그.
```

Replay / Timeline / Debug / AI Learning의 기반이 되는 단일 이벤트 로그.  
이 테이블만 있으면 Research Layer의 모든 상태 변화를 재현할 수 있다.

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| event_id | UUID | PK, UUIDv7 |
| occurred_at | TIMESTAMPTZ | NOT NULL |
| event_type | VARCHAR(60) | NOT NULL (아래 표준 이벤트 목록 참조) |
| entity_type | VARCHAR(30) | {'candidate', 'decision', 'hypothesis', 'trade', 'audit', 'knowledge'} |
| entity_id | UUID | NOT NULL |
| source | VARCHAR(60) | NOT NULL (발행 모듈명) |
| payload | JSONB | NOT NULL DEFAULT '{}' |

**표준 event_type 목록**
```
CandidateCreated / CandidateEvaluated
DecisionFrozen / DecisionExecuted / DecisionExecutionFailed
OutcomePartialRecorded / OutcomeCompleteRecorded
AuditDecisionCompleted / AuditOutcomeCompleted
KnowledgeExtracted
HypothesisCreated / HypothesisSuperseded / HypothesisApproved / HypothesisRejected
```

**INVARIANT**
- Append-Only: UPDATE, DELETE 금지 (Rule로 강제)
- entity_id는 반드시 해당 entity의 실제 PK (UUID)
- occurred_at은 이벤트가 실제 발생한 시각 (recorded_at과 다를 수 있음)

---

## CONTRACT: research_environment

```
VERSION: 1.0
SOURCE : PM manual (자동 생성 불가)
TABLE  : research_environment
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| re_no | VARCHAR | UNIQUE, 'RE-NNN' 형식 |
| llm_primary | VARCHAR | 실제 모델 ID |
| scientist_level | SMALLINT | 0 ~ 4 |
| evidence_rules | JSONB | NOT NULL |

**INVARIANT**
- 한 번에 활성 RE는 하나뿐 (`status='active'` 1건)
- scientist_level은 PM만 변경 가능 (코드 경로 없음)
- RE 교체 시 이전 RE는 `status='retired'` (삭제 불가)
- scorecard는 월 1회 갱신, 최소 30건 predictions 누적 후

---

## CONTRACT: condition_candidate

```
VERSION: 1.0
SOURCE : main_auto_trading.py → Strategy Monitor Router 호출 지점 (WI-9/WI-13)
TABLE  : research.condition_candidates
```

HTS 조건검색 seq 32-39가 실제로 찾은 종목의 원본 이벤트. `candidate`
계약(Signal Orchestrator ACCEPT 시점)과는 **다른 파이프라인 단계**이며 서로
독립이다 — 혼동 금지.

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| candidate_id | UUID | PK |
| observed_at | TIMESTAMPTZ | NOT NULL |
| stock_code | VARCHAR(10) | NOT NULL |
| condition_seq | SMALLINT | 32 ~ 39 |
| condition_name | VARCHAR(50) | NOT NULL |
| condition_sources | JSONB | NOT NULL, 동시 매칭된 전체 seq 목록 |

**OPTIONAL**
`stock_name, market, source, trace_id`

**INVARIANT**
- 한 종목이 여러 seq에 동시 매칭되면 seq별로 별도 행 (병합 금지 — source 유실 방지)
- condition_sources는 이 행이 속한 관측 시점에 동시 매칭된 전체 seq 스냅샷

**EVENTS**
```
ConditionCandidateCreated {candidate_id, stock_code, condition_seq, observed_at}
```

---

## CONTRACT: strategy_monitor_event

```
VERSION: 1.0
SOURCE : main_auto_trading.py → Strategy Monitor Router 호출 지점 (WI-13)
TABLE  : research.strategy_monitor_events
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| event_id | UUID | PK |
| candidate_id | UUID | FK → research.condition_candidates |
| monitor_name | VARCHAR(50) | NOT NULL |
| monitor_status | VARCHAR(20) | {'SIGNAL','NO_SIGNAL','ERROR','NOT_IMPLEMENTED'} |

**OPTIONAL**
`monitor_version, monitor_state, monitor_result JSONB, monitor_error`

**INVARIANT**
- monitor_status='NOT_IMPLEMENTED'는 seq 34/35/39 전용 — 다른 Monitor로
  fallback 금지(강제할 필드는 없으나 §Router 계약상 monitor_name이 해당 seq의
  공식 Monitor 클래스명과 정확히 일치해야 함)
- monitor_status='ERROR'여도 이 INSERT 자체는 실패하면 안 됨(트레이딩 흐름과 무관)

**EVENTS**
```
StrategyMonitorEvaluated {event_id, candidate_id, monitor_name, monitor_status}
```

---

## CONTRACT: strategy_signal

```
VERSION: 1.0
SOURCE : main_auto_trading.py → Strategy Monitor Router 호출 지점 (WI-13)
TABLE  : research.strategy_signals
```

`strategy_monitor_event.monitor_status = 'SIGNAL'`인 경우만 생성. 매매 판단이
아니라 관측 기록 — execute_buy/Ranking/Gate와 무관.

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| signal_id | UUID | PK |
| candidate_id | UUID | FK → research.condition_candidates |
| monitor_event_id | UUID | FK → research.strategy_monitor_events |
| strategy_name | VARCHAR(50) | NOT NULL |

**OPTIONAL**
`signal_type, signal_score, entry_price_reference, signal_reason`

**EVENTS**
```
StrategySignalCreated {signal_id, candidate_id, strategy_name, condition_seq}
```

---

## CONTRACT: signal_outcome

```
VERSION: 1.0
SOURCE : analysis/condition_signal_outcome_collector.py (cron, WI-13)
TABLE  : research.signal_outcomes
DESIGN : Event 모델 (future_return_events와 동일 원칙) — 신규 horizon 추가 시 DDL 불필요
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| outcome_event_id | UUID | PK |
| signal_id | UUID | FK → research.strategy_signals |
| horizon_label | VARCHAR(10) | '+1D' \| '+2D' \| '+3D' \| '+5D' |
| reference_price | NUMERIC | > 0 |

**OPTIONAL**
`price_at_horizon, return_pct, mfe_pct, mae_pct`

**INVARIANT**
- UNIQUE (signal_id, horizon_label)
- 기록 후 수정·삭제 불가 (DB 트리거 + RULE)

**EVENTS**
```
SignalOutcomeRecorded {signal_id, horizon_label, return_pct}
```

---

## AI Plug-in 계약 (핵심 원칙)

AI 모델이 Claude에서 GPT로, Gemini로, 오픈소스로 바뀌어도  
이 계약들을 만족하면 Trading OS에 연결된다.

```
새 Scientist AI의 최소 요건:
  □ scientist_predictions 계약을 만족하는 레코드를 INSERT할 수 있어야 한다.
  □ research_environment_id를 반드시 참조한다.
  □ confidence_pct를 0~100 정수로 선언한다.
  □ evaluation_date를 최소 30일 후로 설정한다.
  □ prediction_text, confidence_pct, target_metric을 생성 후 수정하지 않는다.
```

AI의 내부 구현(prompt, 모델, 추론 방식)은 계약과 무관하다.  
계약만 지키면 어떤 AI든 Research Layer의 구성원이 될 수 있다.

---

## Event Bus v0 (현재) → v1 (미래)

**현재 (v0):** `system_events` 테이블에 이벤트 로그 기록

```sql
-- 이벤트 발행 예시
INSERT INTO system_events (event_type, source, entity_type, entity_id, payload)
VALUES ('MarketContextCreated', 'mie', 'market_context', 1,
        '{"regime":"Risk-Off","risk_score":62}');

-- 이벤트 구독 예시 (Research Layer)
SELECT * FROM system_events
WHERE event_type = 'TradeClosed'
  AND emitted_at > NOW() - INTERVAL '24 hours';
```

**미래 (v1):** PostgreSQL LISTEN/NOTIFY 또는 외부 메시지 큐로 교체 가능.  
이때 `system_events` 스키마와 event_type 명세는 그대로 유지된다.

이벤트 명세:

| 이벤트 | 발행 주체 | 구독 주체 |
|--------|---------|---------|
| MarketContextCreated | MIE | Session, Decision Log |
| SessionOpened | MIE | Rule Engine (참조만) |
| TradeClosed | Rule Engine | Session Review, Knowledge |
| SessionReviewed | Session Review | Scientist AI |
| HypothesisCreated | Scientist AI | Research Queue |
| HypothesisSuperseded | Scientist AI / Librarian AI | Research Queue |
| PredictionEvaluated | Cron (evaluation_date) | Scorecard 갱신 |
| KnowledgeCreated | Scientist AI / PM | Hypothesis 검토 |
| CandidateCreated | Signal Orchestrator | Decision Ledger |
| CandidateEvaluated | execute_buy gate | Future Returns |
| DecisionFrozen | execute_buy gate | Future Returns (REJECT용) |
| DecisionExecuted | execute_buy (체결) | trades, Future Returns |
| FutureReturnComplete | returns_collector cron | Outcome Auditor |
| AuditCompleted | Dual Auditor | Knowledge Base |

---

## 계약 위반 처리 원칙

1. **INSERT 시점 위반**: DB 제약(CHECK, NOT NULL, UNIQUE)으로 차단
2. **불변 필드 위반**: DB 트리거로 차단 (예: scientist_predictions)
3. **논리적 위반** (valid_until 과거 날짜 등): Application layer에서 검증
4. **계약 미준수 컴포넌트**: Trading OS 연결 불가, 실험 환경에서만 허용

---

## 새 객체 추가 시 체크리스트

```
□ REQUIRED 필드 정의 (타입 + 유효 범위)
□ INVARIANT 정의 (DB 제약 또는 트리거)
□ IMMUTABLE 필드 정의 → 트리거 작성
□ 발행할 EVENTS 정의 → system_events INSERT 코드 추가
□ 이 문서에 CONTRACT 섹션 추가
□ ARCHITECTURE.md의 Object Model 업데이트
```

---

*Data Contract v1.0 — 2026-06-27*  
*Data Contract v2.0 — 2026-06-30 (candidate, decision_ledger, future_returns 추가; hypothesis v2.0)*  
*AI 모델, 전략, 데이터 소스가 바뀌어도 이 계약은 유지된다.*
