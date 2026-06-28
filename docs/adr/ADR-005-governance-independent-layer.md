# ADR-005: Governance는 독립된 엔진이다

**Status:** Accepted (2026-06-27)  
**Constitution:** Article 8 — Separation of Concerns

---

## Context

연구 결과를 운영 전략에 반영하는 방법은 여러 가지가 있다. 가장 단순한 방법은 Research → Production 직통 경로를 만드는 것이다. 많은 시스템이 이렇게 설계된다.

## Decision

**Research와 Production 사이에 Governance Layer를 두고, 이 계층 없이는 어떤 변경도 Production에 반영되지 않는다.**

```
Research Engine
      ↓ (hypothesis, experiment, evidence)
Governance Engine  ← PM 승인 필수
      ↓ (strategy_versions.status = 'active')
Execution Engine
```

어떤 코드 경로도 Governance를 우회할 수 없다.

## Rationale

**1. Overfitting 방지**  
좋아 보이는 백테스트 결과가 바로 운영에 들어가면 과최적화된 전략이 실계좌를 망친다. Governance는 walk-forward 검증, 표본 크기 확인, Evidence Gate를 통과한 것만 승격시킨다.

**2. LLM 환각 차단**  
Scientist AI가 그럴듯한 가설을 만들었지만 실제로는 잘못된 패턴일 수 있다. Governance는 이것을 Experiment 결과로 검증한 후에만 승인한다.

**3. 감정적 수정 방지**  
"이번 주 손실이 컸으니 전략을 바꿔야 한다"는 충동적 판단이 Production에 직접 들어가는 것을 막는다. 모든 변경은 Experiment → Evidence → 승인 과정을 거친다.

**4. 책임 명확화**  
Governance를 통과한 변경에는 PM 승인이 있다. 이후 결과에 대한 책임 소재가 명확하다.

## Alternatives Considered

**Research → Production 직통**  
Experiment 결과가 기준을 통과하면 자동으로 배포.  
*Rejected*: 자동 배포는 Governance를 코드로 대체하는 것. 코드는 PM 판단을 대신할 수 없고, PM은 코드가 볼 수 없는 맥락(시장 환경, 정책 변화, 포트폴리오 전체)을 볼 수 있다.

**AI-automated Governance**  
AI가 실험 결과를 평가해서 자동 승인.  
*Rejected*: Article 5(AI는 연구자이지 거버너가 아니다)에 직접 위배. AI가 자신의 제안을 스스로 승인하는 구조가 된다.

## Consequences

- 좋은 가설이 있어도 Governance 과정 없이는 전략에 반영되지 않는다
- PM 승인이 병목이 될 수 있다 → 이것은 의도된 마찰(intentional friction)
- strategy_versions의 모든 'active' 상태 레코드에는 승인 근거가 있어야 한다
- Governance 품질 측정: OS Health Report의 "Governance 승인율, 평균 승인 시간"
