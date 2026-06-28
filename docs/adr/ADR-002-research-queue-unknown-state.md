# ADR-002: Research Queue — "모른다"는 유효한 상태다

**Status:** Accepted (2026-06-27)  
**Constitution:** Article 4 — Unknown is a Valid State

---

## Context

AI 시스템은 일반적으로 입력이 들어오면 출력을 만든다. "결론 없음"은 실패로 간주된다. 대부분의 Scientist AI 설계는 항상 무언가를 만들어내도록 강제한다.

우리는 이것을 거부했다.

## Decision

**데이터 부족, 신뢰도 부족, 상충되는 결과가 있을 때 Scientist AI는 결론을 내리지 않는다.**

이런 가설은 `research_queue`에 `status='waiting'`으로 등록되어 조건이 충족될 때까지 대기한다.  
조건 충족(예: trades ≥ 50) 시 자동으로 `status='data_sufficient'`로 전환되어 재평가된다.

## Rationale

**1. 표본 부족 결론의 위험성**  
n=8인 데이터로 "Risk-Off RVOL 전략이 유효하지 않다"를 결론으로 내리면, Governance Layer를 통해 잘못된 전략 변경이 일어날 수 있다.

**2. 억지 결론의 패턴**  
우리 프로젝트 초기에 "전략 문제"라고 생각했던 것들 중 상당수가 실제로는 "표본 부족 문제"였다. 억지로 결론을 만드는 것이 반복 수정의 원인이었다.

**3. Research의 본질**  
좋은 연구는 "모른다"를 선언할 수 있을 때 신뢰성이 생긴다. 항상 답을 내놓는 연구자보다 "이건 아직 데이터가 부족합니다"를 말할 수 있는 연구자가 더 신뢰할 수 있다.

## Alternatives Considered

**Low-confidence hypothesis 즉시 생성**  
표본이 부족해도 낮은 confidence로 가설을 만든다.  
*Rejected*: Governance Layer가 confidence=30%인 가설을 어떻게 처리할지 불명확. 낮은 confidence라도 가설이 존재하면 실험을 트리거하게 된다.

**데이터 수집 후 배치 처리**  
일정 기간마다 모든 미결 가설을 재평가.  
*Partially Accepted*: Research Queue의 auto_trigger 메커니즘이 이것의 자동화 버전. 하지만 "언제 재평가할 것인가"를 명시적으로 wait_condition에 저장하는 방식이 더 투명하다.

## Consequences

- `research_queue.wait_condition`에는 최소 기준을 항상 명시해야 한다.
- Research Queue에 오래 머무는 가설은 "약한 가설"이 아니라 "데이터가 아직 부족한 가설"이다.
- OS Health Report에 Research Queue 상태를 포함시켜 관리한다.
- Scientist AI의 Unknown Declaration Rate는 15~30% 범위가 적정하다.
