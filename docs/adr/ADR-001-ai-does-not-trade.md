# ADR-001: AI는 실주문을 실행하지 않는다

**Status:** Accepted (2026-06-27)  
**Constitution:** Article 1 — Execution is Sacred

---

## Context

자동매매 시스템에 AI를 통합할 때 가장 자연스러운 설계는 AI가 직접 매수/매도 신호를 생성하고 주문을 실행하는 것이다. 많은 AI 트레이딩 프로젝트가 이 방식을 채택한다.

우리는 이것을 거부했다.

## Decision

**AI는 어떤 경우에도 실계좌 주문(execute_buy, execute_sell)을 직접 호출하지 않는다.**

실주문은 `main_auto_trading.py`의 Rule Engine만 실행한다.  
AI의 출력(market_context, briefing, hypothesis)은 *다음 세션의 컨텍스트*가 될 뿐, 현재 진행 중인 주문에 개입하지 않는다.

## Rationale

**1. Latency (지연 위험)**  
LLM API 호출은 1~10초의 불확정 지연이 발생한다. 주문 타이밍이 중요한 상황에서 이 지연은 슬리피지와 기회 손실을 유발한다.

**2. Reproducibility (재현성)**  
LLM은 동일한 입력에 대해 다른 출력을 낼 수 있다. 주문 시점의 결정을 완전히 재현할 수 없다면 원인 분석이 불가능하다. Rule Engine은 동일 입력 → 동일 출력을 보장한다.

**3. Governance (거버넌스)**  
AI가 주문 권한을 가지면 Governance Layer가 무력화된다. 검증되지 않은 판단이 즉시 실계좌에 영향을 미친다.

**4. Failure Isolation (장애 격리)**  
AI API 장애, 네트워크 오류, 환각(hallucination) 발생 시 Rule Engine은 독립적으로 동작해야 한다. AI 없이도 포지션 관리와 손절이 작동해야 한다.

## Alternatives Considered

**LLM Direct Trading**  
AI가 실시간으로 시장 데이터를 받아 주문을 실행.  
*Rejected*: Latency, Reproducibility, Governance 세 가지 이유 모두 치명적.

**AI Signals + Rule Engine Execution**  
AI가 신호를 생성하고 Rule Engine이 실행.  
*Partially Accepted*: 실시간 신호 생성은 허용하지 않지만, AI가 *다음 날의 컨텍스트*를 생성하는 것은 허용. 단, 현재 주문 사이클에 AI 출력이 직접 개입하는 것은 금지.

## Consequences

- Rule Engine은 AI 없이 독립 실행 가능해야 한다.
- AI 장애는 trading 장애가 아니다.
- AI가 "LONG 추천"을 해도 Rule Engine이 자체 필터를 통과하지 못하면 주문하지 않는다.
- decision_log의 모든 BUY/SELL은 Rule Engine이 생성한 것이다.
