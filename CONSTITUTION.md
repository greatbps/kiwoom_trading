# Trading OS — Constitution v1.0

> **"모든 주장은 증거를 가져야 하고, 모든 자신감은 검증되어야 한다."**

이 문장은 Rule Engine에도, Scientist AI에도, Knowledge Base에도, Digital Twin에도 동일하게 적용된다. 이 일관성이 Trading OS의 핵심이다.

> **"데이터가 AI를 이끌고, AI가 전략을 제안하며, 사람은 방향을 결정한다."**
>
> 이 순서를 바꾸지 않는 것이 이 헌법의 전부다.

> **"Trading OS는 모델 중심(Model-Centric)이 아니라 계약 중심(Contract-Centric)이다."**
>
> AI는 교체 가능하다. Rule Engine도 교체 가능하다. 뉴스 공급자도 교체 가능하다.  
> 그러나 Contract와 Constitution은 유지된다.  
> 플랫폼의 안정성은 특정 기술이 아니라 계약과 증거(Evidence) 위에 선다.

> **"운영 데이터가 설계보다 우선한다."**
>
> 아무리 좋은 설계도 운영 데이터 앞에서는 가설일 뿐이다.  
> 데이터가 설계의 한계를 보여주면, 설계를 바꾼다. 데이터를 외면하지 않는다.  
> 새로운 기능 10개보다 아키텍처 변경 0회가 더 큰 성과다.

*제정: 2026-06-27*

---

## 전문 (Preamble)

이 문서는 구현 지침이 아니다.

이 문서는 앞으로 이 시스템에 내려질 모든 설계 결정의 **판단 기준**이다.

기능은 추가되고 삭제된다. 코드는 리팩토링된다. AI 모델은 교체된다.  
그러나 이 헌법에 담긴 원칙은 바뀌지 않는다.

어떤 새로운 기능이 제안되었을 때, 이 헌법의 7개 조항에 비추어  
하나라도 위반된다면 그 기능은 구현하지 않는다.

---

## Article 1. Execution is Sacred

**실주문은 언제나 결정론적 Rule Engine만 수행한다.**

- LLM은 직접 주문하지 않는다.
- AI 장애가 발생해도 Rule Engine은 독립적으로 동작해야 한다.
- AI의 `recommended_bias: LONG`은 다음 세션의 컨텍스트일 뿐,  
  현재 진행 중인 주문에 개입하지 않는다.

**왜:** AI는 확률적이다. 주문은 결정론적이어야 한다.  
이 둘이 동일한 코드 경로 안에 있는 순간, 시스템의 신뢰성은 무너진다.

---

## Article 2. Every Claim Needs Evidence

**모든 주장에는 증거가 있어야 한다.**

- Knowledge Base의 모든 항목에는 `evidence`가 연결된다.
- Hypothesis는 반드시 Experiment를 거친다.
- Strategy는 Evidence Gate(P6)를 통과해야 한다.
- `evidence_level = 'anecdotal'` 항목은 의사결정에 사용할 수 없다.

**왜:** "이 전략이 잘 작동한다"는 말은 증거 없이는 믿음일 뿐이다.  
이 시스템은 믿음이 아니라 데이터 위에서 움직인다.

---

## Article 3. History Cannot Be Rewritten

**과거는 수정하지 않는다.**

- `decision_log`는 Append Only. 수정·삭제 없음.
- `strategy_versions`는 Immutable. 기존 버전을 덮어쓰지 않는다.
- `knowledge_base`는 `is_active = FALSE`로만 비활성화한다. 삭제하지 않는다.
- `research_notebook`은 원본을 보존하고 Revision으로 관리한다.

**왜:** 분석의 신뢰성은 과거 데이터의 무결성에서 온다.  
"그때 우리가 무엇을 봤고, 무엇을 결정했는가"를 언제든 재현할 수 있어야 한다.  
이것이 없으면 학습이 아니라 망각이다.

---

## Article 4. Unknown is a Valid State

**"모른다"는 것은 실패가 아니다.**

- 데이터 부족, 신뢰도 부족, 증거 부족이면 결론을 내리지 않는다.
- 이런 상태의 가설은 `research_queue`에 등록되어 데이터가 쌓일 때까지 기다린다.
- 표본이 충분하지 않은 지식은 `evidence_level = 'anecdotal'`로 표시된다.

**왜:** 불확실한 상태에서 억지로 결론을 만드는 것이  
잘못된 전략 변경의 가장 큰 원인이었다.  
"아직 모른다"를 선언할 수 있는 시스템만이 장기적으로 학습한다.

---

## Article 5. AI is a Researcher, Not a Trader

**Scientist AI는 끝까지 주문 권한을 갖지 않는다.**

권한은 단계적으로 승격되지만, 마지막 단계에서도 AI가 하는 일은  
*제안(Suggest)* 이지 *배포(Deploy)* 가 아니다.

| Level | 역할 | 주문 권한 |
|-------|------|---------|
| L0 | 패턴 설명 | 없음 |
| L1 | 가설 초안 | 없음 |
| L2 | 연구 큐 등록 | 없음 |
| L3 | 실험 요청 | 없음 |
| L4 | PM에게 제안 | 없음 |

**왜:** AI는 오늘보다 더 뛰어난 버전으로 언제든 교체될 수 있다.  
어떤 AI 버전에도 주문 권한을 주지 않는 것이  
시스템이 특정 모델에 종속되지 않는 유일한 방법이다.

---

## Article 6. Evolution Must Be Measured

**전략 진화는 반드시 정량적으로 검증되어야 한다.**

모든 Experiment는 시작 전에 **성공 지표를 사전 선언**한다.  
사후에 지표를 바꾸는 행위는 실험을 무효로 만든다.

검증 가능한 지표의 예:
- 승률(Win Rate)
- 기대값(Expectancy)
- 최대 낙폭(Max Drawdown)
- 샤프 비율(Sharpe Ratio)
- 수익 팩터(Profit Factor)
- 과최적화 여부 (walk-forward ≥ backtest × 0.7)

**왜:** "좋아 보인다"는 이유로 Production이 바뀐 적이 너무 많았다.  
이 헌법이 존재하는 이유 중 하나가 그 패턴을 막는 것이다.

---

## Article 7. Everything is an Experiment Until Proven Otherwise

**Production은 검증된 실험의 결과물이지, 실험의 무덤이 아니다.**

이 원칙이 적용되는 대상:

- 새로운 전략 파라미터 → Experiment
- 새로운 AI 모델(GPT → Claude) → Experiment
- 새로운 뉴스 소스 추가 → Experiment
- 새로운 feature (RVOL 대신 다른 지표) → Experiment
- 새로운 Prompt 버전 → Experiment
- 새로운 Data Source → Experiment

단 하나의 예외도 없다. "이번만 바로 운영에 넣자"는 순간 이 조항이 위반된다.

**왜:** 검증되지 않은 것이 Production에 들어가는 통로가 하나라도 열리면, Governance Layer 전체가 무력화된다. 통로 자체를 구조적으로 막는 것이 이 조항의 목적이다.

---

## Article 8. Separation of Concerns

**세 엔진은 서로의 역할을 침범하지 않는다.**

```
Execution Engine  → 실행만 한다. AI의 의견을 실시간으로 묻지 않는다.
Research Engine   → 연구만 한다. 실계좌 주문을 내지 않는다.
Governance Engine → 승인만 한다. 전략 실험을 직접 수행하지 않는다.
```

이 경계가 무너지는 신호:

- "AI가 오늘 KOSPI를 보고 직접 진입 결정을 내렸다" → Article 1 위반
- "증거 없이 직관으로 YAML 값을 바꿨다" → Article 2, 6 위반
- "이전 실험 기록을 지웠다" → Article 3 위반
- "표본 10건으로 새 전략을 채택했다" → Article 4, 6 위반

**왜:** 경계가 무너지는 것은 항상 조금씩, 예외처럼 시작된다.  
헌법은 그 예외들에 이름을 붙이기 위해 존재한다.

---

## 핵심 철학 — The Three Orders

```
데이터  →  AI  →  사람
 이끌고  제안하며  결정한다
```

**첫 번째 질서:** 데이터 없이 AI가 추측하지 않는다.  
**두 번째 질서:** AI가 사람을 우회하지 않는다.  
**세 번째 질서:** 사람이 데이터 없이 감으로 전략을 바꾸지 않는다.

이 세 가지가 균형을 이룰 때 이 시스템은 올바르게 동작한다.  
하나라도 무너지면 전체가 흔들린다.

---

## 설계 변경 판단 기준

새로운 기능이나 변경을 검토할 때 이 질문에 답하라.

```
1. 이 변경이 Execution Engine에 AI를 개입시키는가?         → Article 1 위반
2. 이 변경은 증거 없이 결론을 만드는가?                   → Article 2 위반
3. 이 변경이 기존 기록을 수정하거나 삭제하는가?            → Article 3 위반
4. 표본이 충분하지 않은 상태에서 결론을 강제하는가?        → Article 4 위반
5. AI에게 배포 권한을 직접 또는 간접으로 부여하는가?       → Article 5 위반
6. 성공 지표를 사전 선언하지 않고 변경을 적용하는가?       → Article 6 위반
7. 검증 없이 Production에 직접 반영된 변경이 있는가?       → Article 7 위반
8. 세 엔진 중 하나가 다른 엔진의 역할을 수행하는가?        → Article 8 위반
```

하나라도 "예"라면 구현을 중단하고 다시 설계한다.

---

## No Architecture Changes Without Evidence

**ARCHITECTURE.md는 다음 세 가지 조건 중 하나를 만족할 때만 수정한다.**

```
1. 운영 데이터가 기존 설계의 한계를 보여주었다.
   (측정 가능한 지표로 근거를 제시해야 한다.)

2. Constitution과 충돌하는 구조적 문제가 발견되었다.
   (어떤 Article과 충돌하는지 명시해야 한다.)

3. Data Contract를 유지하면서 해결할 수 없는 구조적 문제가 발생했다.
   (어떤 Contract와 충돌하는지, 왜 Contract 수정만으로 해결되지 않는지 설명해야 한다.)
```

**Architecture 변경이 허용되지 않는 이유들:**

- "아이디어가 좋아 보여서"
- "최신 AI 모델이 나와서"
- "더 멋진 설계를 봐서"
- "일정이 촉박해서"

Architecture가 안정적일수록 위에 올라오는 모든 것이 안정적이다.  
Architecture를 자주 바꾸는 프로젝트는 아무것도 쌓이지 않는다.

---

## Governance Decision Records

주요 거버넌스 결정은 `docs/governance/`에 기록된다.

| GD | 결정 | Constitution | 날짜 |
|----|------|-------------|------|
| GD-001 | Platform v2.8.0 Release Deferred | Article 6, 7 | 2026-06-27 |
| GD-002 | Release는 Acceptance Test 결과를 Evidence로 제출 | Article 6, 7 | 2026-06-28 |
| GD-003 | Q3 운영자 모드 전환 — Architecture 변경 0회 목표 | 전문 4번째 철학 | 2026-06-28 |
| GD-004 | AUDIT_PROMPT_v1.0 승인 및 동결 — Phase A 동안 프롬프트 수정 금지 | Article 2, 7 | 2026-06-28 |
| GD-005 | AI Agent 설계를 Time-Cycle 기준으로 재정의 — ADR-006 채택 | Article 8 | 2026-06-29 |

새 거버넌스 결정이 내려질 때마다 이 목록에 추가한다.

**GD-001의 의미:**  
이것은 Constitution이 처음으로 실제 결정을 통제한 사례다.  
PM이 "v2.8.0을 달자"고 했을 때, Constitution이 "운영 데이터가 없으므로 아직 아니다"고 판단했다.  
이 순간부터 헌법은 문서가 아니라 프로젝트를 통제하는 규칙이 되었다.

---

## Operating Cadence

Trading OS의 운영 주기.

```
Daily
  ├── MIE (07:32)         — market_context 생성, 브리핑 발송
  ├── Trading (09:00~)    — Execution Engine 운영
  └── Session Review (16:37) — 하루 연구 단위 마감

Weekly
  ├── Strategy Report     — 거래 성과 요약
  └── OS Health Report (토 08:47) — 플랫폼 건강도 점검

Monthly
  ├── Scientist Scorecard — Calibration, Unknown Rate 평가
  ├── Calibration Review  — scientist_predictions 결과 점검
  └── Governance Review   — Hypothesis 진행 상황 점검

Quarterly
  ├── Architecture Review — "No Architecture Changes Without Evidence" 기준 확인
  └── Platform Release Decision — v2.8.0 릴리스 조건 5개 달성 여부 판단
```

이 리듬이 정착될 때, Trading OS는 "자동매매 프로그램"에서  
"지속적으로 검증되고 개선되는 연구 운영체제"가 된다.

---

## 장기 운영 리스크 (Auditor 관점)

이것은 경고가 아니라 관찰이다. 이 리스크들은 Phase A가 끝날 때쯤 현실이 될 가능성이 높다.

**Risk 1: 성공이 가장 큰 적이다**

실패는 Constitution을 강화한다. 성공은 Constitution을 시험한다.  
"승률 72%, 수익률 +18%" 이후 "이번만 예외로"라는 유혹이 올 것이다.  
GD-001("아직 아니다")이 존재한 것처럼, 그 유혹이 올 때도 Constitution이 같은 답을 해야 한다.

**Risk 2: Evidence Inflation (증거 인플레이션)**

Knowledge가 늘기만 하면 그것도 부채가 된다.  
분기마다 확인해야 할 KPI: **신규 지식 vs 폐기 지식의 비율**  
`valid_until`이 경과한 KB 항목을 방치하는 것은 지식의 유효성을 거짓으로 유지하는 것이다.  
`os_status.py`에서 만료 항목을 확인하고, 분기 리뷰에서 폐기 처리한다.

**Risk 3: Constitution의 실적이 없으면 Constitution은 장식이 된다**

지금은 "Article가 존재한다, Acceptance Test가 존재한다"까지다.  
장기적으로는 "Article 3가 12회의 위반 시도를 차단했다"는 통계가 있어야 한다.  
`system_events` 테이블이 이것을 기록할 수 있다 — Phase B 이후 구현 대상.

---

## 이 헌법의 개정 조건

헌법은 쉽게 바뀌지 않는다.

개정 조건:
- 실거래 데이터 기반의 명확한 근거 존재
- 기존 원칙과의 충돌 가능성 사전 분석
- Phase 진입 조건 달성 이후

개정 불가 조건:
- "일단 해보고 나중에 원칙을 맞추자"
- "이번만 예외로"
- 표본 부족 상태에서의 긴급 결정

---

*Constitution v1.0 — 2026-06-27*  
*이 문서는 Trading OS가 존재하는 한 유효하다.*
