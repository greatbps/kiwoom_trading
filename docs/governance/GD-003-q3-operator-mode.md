# GD-003: 2026 Q3 — 개발자 모드에서 운영자 모드로 전환

**Date:** 2026-06-28  
**Type:** Phase Transition Decision  
**Status:** Accepted

---

## Decision

2026년 3분기(7월~9월) 동안 다음 두 가지를 목표로 한다.

**목표 1: ARCHITECTURE.md 수정 0회**  
운영 데이터가 설계의 한계를 보여주지 않는 한, 아키텍처를 건드리지 않는다.

**목표 2: Phase A 완주**  
새 기능 추가 없이 Daily/Weekly 운영 루프만 반복한다.

```
허용                              금지
────────────────────────────────  ────────────────────────────────
MIE 일일 실행                     새로운 AI Agent
Session Review 일일 실행          새로운 LLM 통합
Weekly KPI 확인                   새로운 Dashboard
OS Health Report 확인             새로운 Strategy
Acceptance Test 주기 실행         새로운 Prompt Framework
버그 수정                         새로운 DB 테이블
운영 로그 관찰                    Architecture 변경
```

---

## Phase Roadmap (운영자 시각)

| Phase | 기간 | 목표 | 성공 기준 |
|-------|------|------|---------|
| A | 2026 Q3 | 데이터 축적 | 아키텍처 변경 0회, 루프 90일 완주 |
| B | 2026 Q4 | Scientist 검증 | L0→L1 승격 여부 결정 |
| C | 2027 Q1 | Experiment | Hypothesis→Deploy 1사이클 완료 |
| 4 | 2027+ | Digital Twin | Phase C 성공 후 |

---

## 6개월 후 진짜 KPI

지금은 `32/32 PASS`가 가장 눈에 띈다.  
6개월 후에는 이 숫자들이 더 중요하다.

| KPI | 목표 | 의미 |
|-----|------|------|
| Scientist Calibration Error | ≤ 10%p | AI가 자기 자신을 얼마나 정확히 아는가 |
| Knowledge Validity | ≥ 80% | 지식이 여전히 유효한가 |
| Hypothesis Success Rate | 측정 시작 | 좋은 가설을 만드는가 |
| Experiment Reproducibility | ≥ 90% | 재현 가능한 실험인가 |
| **Architecture Changes** | **= 0** | **아키텍처가 운영을 견디고 있는가** |

마지막이 가장 중요하다. Architecture Changes = 0이면 설계가 현실을 견뎠다는 증거다.

---

## Constitution Applied

**"운영 데이터가 설계보다 우선한다." (Constitution 전문, 4번째 철학)**

이 Phase 전환 자체가 그 철학의 첫 번째 적용이다.  
설계가 완성되었으므로 이제 데이터가 말하게 한다.

---

## Governance Rule (이 결정에서 파생)

**"새로운 것을 만들고 싶은 충동은 Phase A 동안 research_notebook에 기록한다."**

좋은 아이디어가 생기면 구현하지 않는다. `research_notebook`에 제목과 근거만 남긴다.  
Phase B 이후, 데이터가 그 아이디어를 지지하면 그때 가설로 격상시킨다.

---

## OS Status at Decision Time

```
python3 -m analysis.os_status  (2026-06-28)

Execution    🟢 Running
Acceptance   32/32 PASS
Arch Changes 0  (Q3 목표: 0) 🟢
BUY ≥ 100    🔴 0/100         ← Phase A 동안 채운다
Predictions  🔴 1/50          ← Phase B 동안 채운다
```

🔴가 많은 것이 문제가 아니다. 이것이 지금의 정직한 상태다.  
Phase A가 끝날 때 이 숫자들이 어떻게 바뀌었는지가 진짜 평가다.
