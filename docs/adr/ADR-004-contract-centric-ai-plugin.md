# ADR-004: Trading OS는 모델 중심이 아니라 계약 중심이다

**Status:** Accepted (2026-06-27)  
**Data Contract:** scientist_predictions CONTRACT v1.0

---

## Context

AI 트레이딩 시스템의 가장 큰 위험 중 하나는 특정 AI 모델에 종속되는 것이다. "Claude 없이는 작동하지 않는 시스템"이 되는 순간, 모델 비용 상승, 서비스 중단, 성능 저하에 취약해진다.

## Decision

**Trading OS의 Architecture와 Constitution은 어떤 AI 모델도 알지 못한다.**

Architecture는 Contract만 안다. AI는 그 Contract를 만족하는 한 교체 가능한 Component다.

```
현재: Claude Haiku (MIE), Claude Sonnet (main)
미래: GPT-N, Gemini, 오픈소스, 사내 모델 — 어떤 것이든 가능
조건: scientist_predictions Contract 만족
```

이것은 Windows의 Driver Model과 같다:
- Windows는 프린터 내부 구현을 모른다
- Printer Driver Interface만 안다
- Trading OS는 AI 내부 구현을 모른다
- Research Driver Interface(scientist_predictions)만 안다

## Rationale

**1. AI 모델은 빠르게 진화한다**  
Claude 4.x → 5.x → 6.x, GPT-4 → 5 → N. 모델이 바뀔 때마다 시스템을 재설계하면 아키텍처가 모델의 수명에 종속된다.

**2. 계약이 있으면 비교가 가능하다**  
RE-001(Claude Haiku)과 RE-002(가상의 GPT) 모두 동일한 `scientist_predictions` 계약을 만족하면, 두 Research Environment의 Calibration을 비교할 수 있다. 모델 중심이면 이 비교가 불가능하다.

**3. Governance는 모델을 평가하지 않는다**  
Governance는 hypothesis와 experiment 결과를 평가한다. 어떤 모델이 그것을 만들었는지는 관심 없다. Contract를 통해 생성된 산출물만 평가한다.

## Alternatives Considered

**Claude Native Integration**  
Claude API를 직접 호출하는 코드를 시스템 전반에 삽입.  
*Rejected*: Claude 서비스 장애 = 시스템 전체 장애. 모델 교체 시 대규모 코드 변경 필요.

**AI Abstraction Layer**  
모든 AI 호출을 추상화 레이어로 감싸서 모델 교체를 쉽게.  
*Partially Accepted*: `analysis/market_intelligence.py`는 모델 교체를 CLAUDE_MODEL 상수 하나로 처리. 하지만 더 중요한 것은 코드 추상화가 아니라 *출력 데이터의 계약*. 아무리 추상화해도 출력 데이터 형식이 다르면 의미 없다.

## Consequences

- `research_environment` 테이블이 "어떤 모델이 어떤 계약으로 동작했는가"의 유일한 기록
- 새 AI 모델 도입 = 새 RE 생성 + 기존 Contract 만족 확인
- 모델 성능 비교 = RE별 Calibration 비교 (동일 계약 기반이므로 공정한 비교)
- Constitution과 Architecture 문서에 특정 모델명이 등장하지 않는다 (CLAUDE.md 제외)
