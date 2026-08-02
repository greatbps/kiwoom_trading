# GD-005: AI Agent 설계를 Time-Cycle 기준으로 재정의

**날짜**: 2026-06-29  
**Constitution 조항**: Article 8 (Separation of Concerns)  
**관련 ADR**: ADR-006

---

## 결정 내용

AI Agent의 설계 원칙을 **역할(Role) 기준**에서 **시간 사이클(Time Cycle) 기준**으로 재정의한다.

```
Before Market (07:32)        → Observer AI
During Market (09:00~15:30)  → Analyst AI
After Market  (16:40, 22:00) → Scientist AI
Before Deploy (on-demand)    → Governance AI
(Phase 5 이후)               → Memory Manager
```

각 Agent에 KPI를 Architecture 수준에서 명시한다.

---

## 근거

Trading OS는 시간을 중심으로 설계된 시스템이다.  
하루의 운영 흐름(Before / During / After Market)이 이미 존재하는데,  
AI의 역할만 시간과 무관하게 정의하면 구조적 불일치가 발생한다.

Auditor 관점에서:

- "Observer가 언제 실행됐는가?" → Time Layer 없이는 계약으로 강제 불가
- "Scientist의 16:40 실행이 지연됐을 때 무슨 일이 생기는가?" → 정의되지 않음
- "Analyst가 장중에 동작하지 않으면 누가 결정을 설명하는가?" → 공백 발생

Time Cycle을 명시하면 이 공백이 구조로 메워진다.

---

## 영향 범위

**변경된 문서:**
- `ARCHITECTURE.md` — Principle 7, 8 추가 (기존 내용 수정 없음)
- `CONSTITUTION.md` — GD-005 추가 (이 문서)

**코드 변경 없음** — 이 결정은 Architecture 수준의 명시화이며,  
기존 구현(market_intelligence.py, session_review.py)의 실행 시점은 변경하지 않는다.

---

## 이 결정이 막는 것

미래에 누군가 다음을 시도할 때 이 결정이 판단 기준이 된다:

- "Scientist AI를 09:00에도 실행하자" → Time Cycle 위반
- "Observer AI가 장중에도 돌면 어때?" → 역할 혼재
- "Memory Manager는 필요없다" → KB 1,000건 이후 재논의

---

*GD-005 — 2026-06-29*
