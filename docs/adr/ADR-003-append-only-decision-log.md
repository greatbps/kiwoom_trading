# ADR-003: Decision Log는 Append-Only다

**Status:** Accepted (2026-06-27)  
**Constitution:** Article 3 — History Cannot Be Rewritten

---

## Context

매매 결정 기록을 수정하고 싶은 상황이 있을 수 있다. 예를 들어 잘못된 confidence 값이 기록된 경우, 또는 이전 결정의 이유를 명확하게 수정하고 싶은 경우.

## Decision

**`decision_log`는 INSERT만 허용한다. UPDATE와 DELETE는 존재하지 않는다.**

잘못된 데이터가 있어도 수정하지 않는다. 대신 새로운 레코드로 보완 정보를 기록한다.

## Rationale

**1. 분석의 신뢰성**  
"그때 우리가 무엇을 봤고 무엇을 결정했는가"를 나중에 재현할 수 있어야 한다. 기록이 수정 가능하다면 Calibration 분석, Scientist AI 평가, 전략 성과 분석이 모두 신뢰성을 잃는다.

**2. 감사(Audit) 가능성**  
Governance Layer가 "이 전략으로 어떤 결정들이 내려졌는가"를 감사할 때, 기록이 변경될 수 있으면 감사 자체가 무의미하다.

**3. 법적/실질적 책임**  
실계좌 운용에서는 어떤 신호가 어떤 조건에서 매수를 발생시켰는지 원본 기록이 필요하다.

## Alternatives Considered

**소프트 삭제 (is_deleted 컬럼)**  
삭제 대신 플래그 처리.  
*Rejected*: 필터링에 따라 삭제된 것처럼 보일 수 있어 분석을 혼란스럽게 한다.

**수정 이력 테이블**  
원본 + 수정 이력을 따로 관리.  
*Rejected*: "진짜 원본이 무엇인가" 논쟁이 생긴다. Append-Only가 가장 단순하고 명확하다.

## Consequences

- 잘못된 confidence 기록: 그대로 보존, 분석 시 outlier로 처리
- DB 용량은 시간이 지날수록 증가한다 (정상, 허용됨)
- `scientist_predictions`의 prediction_text, confidence_pct도 동일 원칙 적용 (DB 트리거)
- 과거 레코드에 보완 정보 추가가 필요한 경우: outcome_* 컬럼처럼 별도 "결과 필드"로 설계
