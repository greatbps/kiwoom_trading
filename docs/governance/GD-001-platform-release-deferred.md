# GD-001: Platform v2.8.0 Release Deferred

**Date:** 2026-06-27  
**Type:** Release Decision  
**Status:** Accepted

---

## Decision

Platform v2.8.0 릴리스를 유보한다.  
현재 버전은 `v2.8.0-beta`로 유지한다.

---

## Reason

운영 증거가 충분하지 않다.

설계(Architecture)와 구현(Implementation)은 완성되었다.  
그러나 이 아키텍처가 실제 운영에서 의도대로 작동하는지는 아직 검증되지 않았다.

---

## Evidence at Decision Time

| 지표 | 요구 기준 | 현재 상태 |
|------|---------|---------|
| decision_log BUY | ≥ 100건 | 수집 중 (기준 미달) |
| scientist_predictions | ≥ 50건 | 0건 (Phase 3c 미시작) |
| Calibration Grade | 산출 가능 | N/A |
| OS Health Report | ≥ 8주 누적 | 0주 |
| Hypothesis → Deployment 사이클 | ≥ 1회 완료 | 0회 |

---

## Constitution Applied

**Article 7 — Everything is an Experiment Until Proven Otherwise**

> "어떤 설계도 운영 데이터로 검증되기 전까지는 가설이다."

Architecture v1.0은 올바른 설계라는 *가설*이다.  
이 가설은 운영 데이터로 검증되어야 한다.  
검증 전에 "완성"을 선언하는 것은 Article 7에 위배된다.

**Article 6 — Every Improvement Must Be Measurable**

릴리스 기준(위의 5개 조건)이 사전에 선언되어 있다.  
조건 미충족 = 릴리스 불가. 사후 기준 변경은 허용되지 않는다.

---

## Trigger

PM이 "v2.8.0 태그를 달자"고 제안.  
Architecture 검토 결과, 운영 증거 없이 정식 릴리스를 선언하는 것은  
이 프로젝트 자체의 헌법 원칙과 충돌함을 확인.  
제안이 Constitution에 의해 유보됨.

---

## Outcome

- CHANGELOG: `v2.8.0` → `v2.8.0-beta`
- ARCHITECTURE.md: Version Policy 섹션 추가
- 릴리스 조건 5개 명문화 (변경 불가, 사전 선언)

---

## Significance

이것은 단순한 버전 번호 변경이 아니다.

이 결정은 **Constitution이 처음으로 실제 결정을 통제한 사례**다.

사람이 "v2.8.0을 달자"고 했을 때,  
Architecture가 "운영 데이터가 없으므로 아직 아니다"고 판단했다.

이 순간부터 헌법은 문서가 아니라 프로젝트를 통제하는 규칙이 되었다.

---

## Next Review

다음 Governance Review: v2.8.0 릴리스 조건 5개 중 달성 현황 점검  
예정 주기: 분기 1회 (Quarterly Governance Review)
