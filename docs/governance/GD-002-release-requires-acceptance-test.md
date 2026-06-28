# GD-002: 모든 Release Decision은 Acceptance Test 결과를 근거로 해야 한다

**Date:** 2026-06-28  
**Type:** Process Rule  
**Status:** Accepted

---

## Decision

Platform Release를 결정할 때, Acceptance Test 결과를 반드시 Evidence로 제출해야 한다.

```
Release Decision 흐름:

  Acceptance Test 실행
        ↓
  결과 로그 확인 (logs/acceptance_history.jsonl)
        ↓
  CRITICAL 항목 전부 PASS?
        ↓
  Governance Decision 기록
        ↓
  Release or Hold
```

"Acceptance Test를 건너뛰고 Release를 결정"하는 것은 이 규칙 위반이다.

---

## Rationale

GD-001에서 "증명했으니까 Release"로 증명 책임이 역전되었다.  
이 규칙은 그 역전을 프로세스로 고정한다.

**Before GD-002:**  
"기능이 완성됐으니 Release 하자" → 주관적 판단

**After GD-002:**  
"Acceptance Test PASS + 운영 5개 조건 충족" → 객체적 기준

Release가 "누군가의 확신"이 아니라 "측정 가능한 결과"에 의존하게 된다.

---

## Severity Model

테스트 결과는 세 등급으로 해석한다.

| 등급 | 표기 | 실패 시 |
|------|------|--------|
| CRITICAL | [!] | 즉시 Release Block |
| IMPORTANT | [~] | beta 유지 |
| ADVISORY | [-] | Release 가능, 추적 권고 |

CRITICAL이 모두 PASS이면 IMPORTANT/ADVISORY 실패에도 Release 가능하다.  
단, IMPORTANT 실패가 있으면 Governance Decision에 근거를 남겨야 한다.

---

## Constitution Applied

**Article 6 — Every Improvement Must Be Measurable**

Release 결정 자체도 "측정 가능한 근거"를 가져야 한다.  
Acceptance Test가 그 근거다.

**Article 7 — Everything is an Experiment Until Proven Otherwise**

"기능 완성 = 배포 가능"이라는 가정을 거부한다.  
검증이 완료될 때까지 Release는 실험 상태(beta)다.

---

## Constitution ↔ Acceptance Test 1:1 규칙

새 Constitution Article이 추가되면, 반드시 대응하는 Acceptance Test를 추가한다.

| Article | 검증 테스트 |
|---------|-----------|
| 1 (Execution Deterministic) | C01, C02 |
| 2 (Evidence Required)       | D01, D02, D03 |
| 3 (History Immutable)       | A05, B01, B03 |
| 4 (Unknown is Valid)        | A07 |
| 5 (AI Never Deploys)        | C01 |
| 6 (Measurable)              | D01, D02 |
| 7 (Everything is Experiment)| E01 |
| 8 (Separation of Concerns)  | C01, C02, C04 |

이 매핑이 유지되면 Constitution과 Acceptance Test는 함께 성장한다.

---

## Outcome

- `analysis/acceptance_test.py`: CRITICAL/IMPORTANT/ADVISORY 3등급 분류 추가
- `logs/acceptance_history.jsonl`: 실행 이력 자동 누적
- 릴리스마다 이 이력을 Governance Decision의 Evidence로 첨부

---

## Next Review

다음 Governance Review 시: Acceptance Test 결과 이력 확인 + 신규 테스트 필요 여부 검토.
