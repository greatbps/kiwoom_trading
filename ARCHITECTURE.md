# Trading OS — Architecture Principles v1.0

> A self-evolving quantitative trading platform that separates  
> **execution, intelligence, research, and governance.**
>
> → 이 문서는 *어떻게(How)* 를 다룬다.  
> → *왜(Why)* 는 **[CONSTITUTION.md](./CONSTITUTION.md)** 를 참조한다.  
> → *무엇을(Contract)* 은 **[DATA_CONTRACT.md](./DATA_CONTRACT.md)** 를 참조한다.

---

## Three Engines

```
┌──────────────────────────────────┐
│      GOVERNANCE ENGINE           │
│  PM Approval → Strategy Version  │
│  Evidence Gate → Deployment      │
└──────────────────┬───────────────┘
                   │ promotes
┌──────────────────▼───────────────┐
│       RESEARCH ENGINE            │
│  MIE · Session · Decision Log    │
│  Hypothesis · Experiment         │
│  Scientist AI · Research Queue   │
│  Research Environment (RE)       │
└──────────────────┬───────────────┘
                   │ informs (next session only)
┌──────────────────▼───────────────┐
│      EXECUTION ENGINE            │
│  Rule Engine — deterministic     │
│  Real orders only                │
└──────────────────────────────────┘
```

AI is a core member of the **Research Engine** only.  
It never directly controls the Execution or Governance Engines.

---

## The Four Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Governance Layer   — Strategy Version, PM Approval, Audit      │
├─────────────────────────────────────────────────────────────────┤
│  Research Layer     — Hypothesis, Experiment, Scientist AI,     │
│                       Research Notebook, Research Queue,        │
│                       Research Environment (Meta Governance)    │
├─────────────────────────────────────────────────────────────────┤
│  Intelligence Layer — Market Context (MIE), Trading Session,    │
│                       Decision Log, Knowledge Base              │
├─────────────────────────────────────────────────────────────────┤
│  Execution Layer    — Rule Engine (deterministic, real orders)  │
└─────────────────────────────────────────────────────────────────┘
```

These four layers never merge. Execution never consults AI at order time.  
AI never touches the Execution layer directly.

---

## Principle 1 — Execution is Deterministic

**실시간 주문은 항상 Rule Engine이 담당한다.**

```
AI role:  Observe → Summarize → Context → Risk Assessment → Sector Selection
AI never: Calculate order size at runtime
AI never: Trigger buy/sell
AI never: Override stop-loss
```

- `main_auto_trading.py` is the only process that issues real orders.
- AI output feeds the *next session*, never the *current order*.
- Even if AI produces a `recommended_bias: LONG`, the Rule Engine may still SKIP based on its own filters.

**Why this matters:** A latency spike, API timeout, or hallucination in the AI layer must never cause an unintended trade.

---

## Principle 2 — Every Decision Must Be Reproducible

**"왜"뿐 아니라 "무엇을 봤는가"도 재현되어야 한다.**

Every BUY/SELL/SKIP in `decision_log` must capture the full causal chain:

```
decision_log
├── signals          (RSI, RVOL, CHoCH grade, EMA cross)
├── market_context   → market_context.id  (regime, risk_score)
├── session_id       → trading_sessions.id
├── strategy_version (v2.0.0)
├── model_version    (claude-haiku-4-5-20251001)
├── prompt_version   (mie-v1.0, review-v1.0)
└── confidence
```

Future requirement: When Claude 6 replaces Claude 5, decisions made under each model must be distinguishable — so we can isolate whether performance changed due to the *model* or the *market regime*.

---

## Principle 3 — AI Never Changes Production

**AI는 Observe → Suggest → Experiment → Report까지만 한다. Deploy는 사람이 한다.**

```
AI output pipeline:
  Observe  → market_context, session data
  Suggest  → hypothesis (status: draft)
  Experiment → experiment (status: backtesting → paper_trading)
  Report   → knowledge_base, research_notebook

Human pipeline:
  Review experiment results
  PM Approval
  → strategy_versions (status: approved → deployed)
```

No code path exists where AI can set `strategy_versions.status = 'deployed'` directly.  
Deployment is a manual, audited step. Always.

---

## Principle 4 — Knowledge Never Dies

**지식은 증거(Evidence)를 가져야 하고, 죽지 않는다.**

```sql
knowledge_base
├── evidence JSONB         -- {sessions:63, trades:241, avg_pnl:-0.7}
├── p_value  DECIMAL       -- statistical significance
├── evidence_level         -- anecdotal / preliminary / confirmed / strong
├── supersedes_id          -- KB-012를 대체하면 이 FK로 계보 추적
└── is_active BOOLEAN      -- False = retired, never deleted
```

Rules:
- `is_active = FALSE` means retired, not deleted. History is preserved.
- Every KB entry must have `source_hypothesis_ids` or `source_experiment_ids`.
- `evidence_level = 'anecdotal'` (n < 20) entries cannot be acted upon by Scientist AI.
- When new evidence contradicts an existing KB entry, a new entry is created with `supersedes_id` pointing to the old one. The old entry becomes `is_active = FALSE`.

---

## Principle 5 — Research is Never Finished

**AI도 "아직 모른다"를 인정한다.**

When a hypothesis is promising but data is insufficient, it enters the **Research Queue**:

```
Hypothesis (H-041)
  ↓  "RVOL 2.0+ 전략이 Risk-On에서 유효한가?"
  ↓  현재 거래 수: 12건 (필요: 50건)
  ↓
Research Queue (status: waiting)
  ↓  [자동 모니터링]
  ↓  trades 누적...
  ↓  trades = 50건 달성
  ↓
Research Queue (status: data_sufficient)
  ↓  → Scientist AI 자동 재평가 트리거
  ↓
Experiment (backtest → walk_forward → paper_trading)
  ↓
Knowledge Base (evidence_level: confirmed)
```

An "I don't know yet" state is a valid and first-class research state.

---

## Principle 6 — Every Improvement Must Be Measurable

**"좋아 보인다"는 이유로는 절대 Production에 반영하지 않는다.**

Every hypothesis must declare its success metric *before* the experiment runs:

| Metric | Example threshold |
|--------|------------------|
| Win rate | +3%p vs baseline |
| Expectancy (EV) | > 0 in walk-forward |
| Max drawdown | ≤ baseline |
| Sharpe ratio | Δ ≥ +0.1 |
| Trade frequency | within ±20% of baseline |
| Overfit check | walk-forward ≥ 70% of backtest |

Pre-registration of metrics is mandatory. Post-hoc metric selection invalidates the experiment.

---

## Meta Governance — Research Environment

연구 과정 자체를 버전 관리한다.

```
research_environment (RE-001)
├── llm_primary      : claude-haiku-4-5-20251001
├── llm_secondary    : claude-sonnet-4-6
├── scientist_level  : 0  (현재 승인 레벨)
├── mie_version      : MIE v1.0
├── prompt_version   : mie-v1.0
├── news_sources     : []
└── evidence_rules   : {"min_p_value":0.05, "min_trades":50}
```

When the LLM model is upgraded (e.g., RE-001 → RE-002 with Claude Sonnet), all hypotheses, notebooks, and knowledge entries created under each RE remain traceable. Performance differences can be attributed to **environment change** vs **market change**.

### Scientist AI — Level System

AI는 끝까지 승인자가 아니라 연구자다.

| Level | 이름 | 권한 |
|-------|------|------|
| L0 | Observer | 패턴 설명 생성 |
| L1 | Analyst | Hypothesis 초안 작성 |
| L2 | Researcher | Research Queue 등록 |
| L3 | Scientist | Experiment 요청 |
| L4 | Lead Scientist | PM에게 승격 제안 |

**L0 → L1 승격 기준** (단일 기준 아님 — 5개 동시 충족)

| 지표 | 기준 | 축 |
|------|------|----|
| Explain Accuracy | ≥ 70% (n ≥ 30) | Competence |
| Calibration Error | ≤ 15%p 평균 | Reliability |
| Unknown Declaration Rate | 15~30% 범위 내 | Reliability |
| Overconfidence Index | ≤ 10%p | Reliability |
| Evidence High KB | ≥ 3건 생성 기여 | Competence |

Accuracy만으로 승격 불가 — Calibration이 나쁜 Scientist는 Accuracy가 높아도 위험하다.

**Scorecard 2축**
- **Competence** (능력): Explain Accuracy, Hypothesis 채택률, Evidence Quality, Pattern Precision
- **Reliability** (신뢰성): Calibration, Unknown Rate, Overconfidence, Retraction Rate

Unknown Declaration Rate는 KPI가 아닌 **균형 지표**: 1% = 과신, 92% = 무연구. 적정 범위 15~30%.

Level 승격은 PM이 `research_environment.scientist_level`을 수동으로 올리는 것으로 확정된다.  
어떤 자동화 코드도 `scientist_level`을 직접 수정할 수 없다.

---

## Digital Twin — Parallel Research Cluster (Phase 4)

```
Production  ─────  v2.0 (Rule Engine, real orders)
                │
                ├── Twin A: RVOL threshold 2.0 → 2.2
                ├── Twin B: Risk-Off Filter added
                ├── Twin C: Scientist L3 proposal
                └── Twin D: New sector rotation logic
```

모든 Twin은 동일한 실시간 시장 피드를 받는다. 실계좌 주문은 없다.  
Twin 성과가 `evidence_rules`를 통과하면 → Experiment → Governance 승격.  
Production은 Twin 결과를 보기 전까지 절대 변경되지 않는다.

---

## Phase Roadmap

| Phase | Name | Status | Key Output |
|-------|------|--------|-----------|
| 2 | Strategy Freeze | ✅ Complete | v2_weak_trend locked |
| 3a | Market Intelligence | ✅ Complete | MIE + Session + Context |
| 3b | Decision Traceability | ✅ Complete | decision_log + session linkage |
| 3c | Scientist AI | ⏳ Next | Pattern Discovery, Research Queue |
| 4.0 | Digital Twin | 🔮 Future | Shadow engine for all experiments |

### Phase 3c Success Criterion (Scientist AI)

The Scientist AI's **first KPI is not "generate new strategies."**  
It is:

> **"정확하게 설명하는 능력"**  
> Explain *when* existing strategies work and *when* they fail.

If it can reliably answer:
- "Risk-On에서 왜 이 전략이 잘 작동했는가?"
- "Risk-Off에서 왜 성과가 나빴는가?"
- "어떤 변수 조합에서 일관되게 수익이 났는가?"

...then it has earned the right to *propose* new hypotheses.

### Phase 4 — Digital Twin (concept only)

```
Production Rule Engine
        │
        ├──────────────────┐
        ▼                  ▼
Real Trading         Digital Twin
(v2.0.0 only)        H-041 applied
                     H-052 applied
                     H-061 applied
                     No real orders.
                     Continuously running.
```

Digital Twin receives the same real-time market feed but issues zero real orders.  
Every hypothesis lives in the Twin until proven. Production never sees an unproven idea.

---

## Object Model

```
market_context ──────────────── trading_sessions
      │                               │
      │                               │
      ▼                               ▼
  decision_log ◄─────── trades ◄─── Rule Engine
      │
      ▼
  knowledge_base ◄──── research_notebook ◄──── research_queue
      │                      │
      ▼                      ▼
  hypotheses ◄────────── experiments
      │
      ▼
  strategy_versions
```

**Every arrow is a foreign key. Every object has a timestamp. Nothing is deleted.**

---

## Phase 3c Activation Checklist

Scientist AI(L0)가 실제로 돌기 시작하는 조건. 모두 충족해야 한다.

```
□ decision_log  ≥ 30건 (BUY 결정 + 신호 스냅샷 수집됨)
□ trading_sessions ≥ 20일치 (레짐별 분포 존재)
□ trades (SELL 완료) ≥ 30건 (결과 있는 거래)
□ knowledge_base evidence_level 'anecdotal' 이상 ≥ 3건
□ research_environment RE-001 활성 상태 확인
□ overnight_tracking ≥ 20건 (Calibration 의미 발생 직전)
```

데이터가 쌓이면 Phase 3c 첫 작업:  
`analysis/pattern_discovery.py` — SQL slicing + Claude 해석 → hypothesis 초안 자동 생성

**Scientist AI의 첫 번째 질문 (L0 단계):**
> "Risk-On 세션과 Risk-Off 세션에서 동일한 CHoCH A급 진입의 평균 PnL 차이는?"

이 질문에 데이터로 답할 수 있을 때, L1 승격 논의를 시작한다.

---

## Deprecation Policy

**Knowledge는 남고, Strategy는 은퇴한다.**

이 구분이 장기 운영에서 핵심이다.

### Strategy Version 생명주기

```
draft → pending_review → active
active → deprecated → retired → archived
```

| 상태 | 의미 | 운영 계속? |
|------|------|----------|
| active | 현재 운영 중 | ✓ |
| deprecated | 후계 버전으로 교체 예정, 경고 기간 | ✓ (임시) |
| retired | 운영 중단, 이유 기록됨 | ✗ |
| archived | 장기 보존, 분석 가능 | ✗ |

**Retirement 규칙:**
- `retired_reason`은 반드시 작성한다 — "2032년에 왜 v2.1을 버렸는가"를 설명할 수 있어야 한다
- retired 이후에도 `strategy_versions` 레코드는 삭제하지 않는다 (Article 3)
- retired 전략으로 생성된 모든 `decision_log`와 `trades`는 그대로 보존된다

**Knowledge 비활성화 규칙:**
- `is_active = FALSE`로만 비활성화 (삭제 없음)
- 새 KB가 기존 KB를 대체할 때 `supersedes_id`로 계보 연결
- 대체된 KB는 여전히 과거 결정의 맥락으로 참조 가능하다

---

## ADR (Architecture Decision Records)

주요 설계 결정의 이유는 `docs/adr/`에 기록된다.

| ADR | 제목 | 핵심 거부 대안 |
|-----|------|-------------|
| ADR-001 | AI는 실주문하지 않는다 | LLM Direct Trading |
| ADR-002 | Research Queue — Unknown은 유효한 상태 | 억지 결론 생성 |
| ADR-003 | Decision Log는 Append-Only | 수정 이력 테이블 |
| ADR-004 | 계약 중심(Contract-Centric) AI Plugin | Claude Native Integration |
| ADR-005 | Governance는 독립 엔진 | Research→Production 직통 |

새 결정이 내려질 때마다 ADR을 추가한다. 코드보다 ADR이 먼저다.

---

## What This Is Not

| Common misconception | Reality |
|---------------------|---------|
| "AI-powered trading bot" | AI never issues orders |
| "Backtesting framework" | We test on live paper first |
| "Signal generator" | Signals are Rule Engine output, AI interprets context |
| "Strategy optimizer" | Human PM approves every strategy change |

---

---

## Version Policy

| 버전 | 의미 | 상태 |
|------|------|------|
| Architecture v1.0 | 이 문서 + Constitution + Data Contract 완성 | ✅ 2026-06-27 |
| Platform v2.8.0-beta | 구현 완료, 운영 검증 전 | 현재 |
| Platform v2.8.0 | 운영 5개 조건 충족 후 릴리스 | ⏳ |

*Architecture v1.0 — 2026-06-27*  
*Architecture 문서는 새 Principle 추가 시에만 개정한다. 전술적 변경에는 개정하지 않는다.*
