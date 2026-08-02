# ADR-006: AI Agents Are Organized by Time Cycle

**날짜**: 2026-06-29  
**상태**: Accepted  
**관련 Constitution 조항**: Article 8 (Separation of Concerns)

---

## Context

Trading OS의 4개 AI Agent(Observer / Analyst / Scientist / Governance)는  
처음에 **역할(Role)** 기준으로 분리되었다.

이 분리는 맞다. 그러나 충분하지 않다.

Trading OS는 시간(Time) 을 중심으로 설계된 시스템이다.  
장 시작 전 / 장중 / 장 종료 후 / 배포 전 — 이 사이클이 하루의 구조다.

AI도 이 사이클에 맞춰야 한다.  
그렇지 않으면 AI가 "기능 모듈"로 인식되고, 운영 조직으로 성장하지 못한다.

---

## Decision

**각 AI Agent의 실행 시점(Time Window)을 Architecture에 명시한다.**

```
Before Market (07:32)       → Observer AI
During Market (09:00~15:30) → Analyst AI
After Market  (16:40, 22:00)→ Scientist AI
Before Deploy (on-demand)   → Governance AI
```

아울러:

- 각 AI의 **KPI를 Architecture 레벨에서 정의**한다.
- **Memory Manager**를 5번째 컴포넌트로 추가한다 (Phase 5 구현 예정).

---

## Alternatives Considered

### A. 역할(Role) 기준 유지
*거부 이유*: 역할만으로는 "언제 동작하는가"가 불명확하다.  
Observer가 장중에 돌아도 설계상 막을 방법이 없다.  
Time Layer가 없으면 순서 보장이 계약이 아니라 관례가 된다.

### B. Event-driven (결정 발생 시 즉시 트리거)
*거부 이유*: 실시간 트리거는 데이터 누적 없이 반응한다.  
Scientist AI는 최소 N건의 데이터가 있어야 의미있는 패턴을 찾을 수 있다.  
배치(Batch) + 스케줄이 현 단계에서 더 적합하다.

### C. KPI 없이 기능만 정의
*거부 이유*: Constitution Article 6 — "모든 진화는 측정 가능해야 한다."  
AI Agent도 예외가 없다. KPI 없는 Agent는 개선할 수 없다.

---

## Consequences

**긍정적:**
- 하루 운영 흐름과 AI 실행 시점이 정확히 일치
- 각 Agent의 품질을 독립적으로 측정 가능
- Memory Manager로 장기 운영 리스크(Evidence Inflation) 구조적 대응

**주의:**
- Analyst AI "During Market" 기능은 아직 미구현 (Phase 3c-1 예정)
- 현재 session_review.py(16:37)는 Analyst EOD + Scientist 중간 역할
  → Phase 3c 진행 시 역할 분리 필요
- Memory Manager는 KB ≥ 1,000건 이후 구현 (현재는 과설계)

---

## KPI Summary

| Agent | KPI | 목표 |
|-------|-----|------|
| Observer | Coverage | 필수 지표 100% 수집 |
| Observer | Freshness | 데이터 age ≤ 1거래일 |
| Observer | Latency | 브리핑 발송 ≤ 5분 |
| Analyst | Explain Accuracy | ≥ 70% (n ≥ 30) |
| Analyst | Decision Consistency | regime별 설명 일관성 |
| Scientist | Calibration Error | ≤ 15%p 평균 |
| Scientist | Unknown Declaration Rate | 15~30% |
| Scientist | Prediction Accuracy | 평가일 기준 |
| Governance | False Approval Rate | ≤ 10% |
| Governance | Review Time | ≤ 7일 |

---

## Relationship to Existing ADRs

- ADR-001: AI는 실주문하지 않는다 — 유효. Time Cycle과 충돌 없음.
- ADR-005: Governance는 독립 엔진 — 유효. "Before Deployment"로 시간 명시.
- ADR-006 (이 문서): Time Cycle + KPI 추가.

---

*ADR-006 — 2026-06-29*
