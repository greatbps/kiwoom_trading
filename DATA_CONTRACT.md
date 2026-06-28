# Trading OS — Data Contracts v1.0

> CONSTITUTION(Why) → ARCHITECTURE(How) → **DATA_CONTRACT(Contract)** → CLAUDE.md(What)

이 문서는 Trading OS의 객체 간 인터페이스를 정의한다.  
새로운 AI 모델, 새로운 연구 모듈, 새로운 데이터 소스가 추가될 때  
**이 계약을 만족하면 시스템에 연결될 수 있다.**

계약을 만족하지 못하면 연결되지 않는다.

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
VERSION: 1.0
SOURCE : Scientist AI or PM manual
TABLE  : hypotheses
```

**REQUIRED**
| 필드 | 타입 | 유효 범위 |
|------|------|---------|
| title | TEXT | NOT NULL |
| description | TEXT | NOT NULL |
| status | VARCHAR | state machine (아래 참조) |
| research_environment_id | INTEGER | 활성 RE 참조 필수 |

**OPTIONAL**
`rationale, source_data JSONB, proposed_change JSONB`  
`tags[], parent_hypothesis_id, knowledge_base_id`

**INVARIANT**
- 상태 머신: draft → pending_review → queued → backtesting → walk_forward  
  → paper_trading → awaiting_pm → approved | rejected → deployed | archived
- 역방향 전이 불가
- knowledge_base_id: 이 가설과 관련된 기존 지식 연결
- Scientist AI L0는 hypothesis INSERT 권한 없음 (L1부터)

**EVENTS**
```
HypothesisCreated  {hypo_id, title, scientist_level, re_id}
HypothesisApproved {hypo_id, experiment_id}
HypothesisRejected {hypo_id, reason}
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
| PredictionEvaluated | Cron (evaluation_date) | Scorecard 갱신 |
| KnowledgeCreated | Scientist AI / PM | Hypothesis 검토 |

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
*AI 모델, 전략, 데이터 소스가 바뀌어도 이 계약은 유지된다.*
