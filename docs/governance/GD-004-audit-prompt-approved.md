# GD-004: AUDIT_PROMPT_v1.0 승인 및 동결

**Date:** 2026-06-28  
**Type:** Audit Governance Decision  
**Status:** Accepted

---

## Decision

`docs/governance/AUDIT_PROMPT_v1.0.md`를 Trading OS 공식 감사 기준으로 승인한다.  
이 프롬프트는 **동결(Frozen)** 상태로 유지한다.

---

## Approved Version

```
AUDIT_PROMPT_v1.0 — 2026-06-28
```

핵심 구성:

- 역할 정의: Independent Technical Auditor
- Audit Methodology: L1(Runtime) > L2(Code) > L3(Document) 우선순위
- Evidence Table: Finding마다 Verified By 명시
- 9개 Phase Scope
- 10개 필수 실행 검증 목록 (V-01 ~ V-10)
- Audit Confidence 수치 (Runtime/Code/Document 비율)
- Release Recommendation: APPROVED / KEEP / BLOCKED 중 하나

---

## Freeze Rationale

GD-003(Q3 운영자 모드)에 따라 Phase A 동안 감사 프롬프트를 개선하지 않는다.

개선이 아닌 **반복 실행**이 이 단계의 목표다.

동일한 프롬프트로 반복 감사를 수행함으로써 감사 결과 자체가 데이터가 된다.  
프롬프트를 계속 바꾸면 감사 결과를 비교할 수 없다.

---

## 다음 감사 일정

| 주기 | 조건 |
|------|------|
| 분기 감사 | Architecture Review와 동일 주기 |
| 릴리스 전 | GD-002: Acceptance Test + Audit Report 필수 |
| 사고 발생 시 | Production 이상 → 즉시 감사 |

---

## AUDIT_PROMPT v1.1 논의 조건

아래 세 가지가 모두 충족될 때만 v1.1 논의를 시작한다.

```
□ 감사 실행 횟수 ≥ 3~5회
□ 동일 유형의 Finding이 반복적으로 등장
□ Finding 추적을 수동으로 하기 어려워짐
```

그 전에 등장하는 개선 아이디어는 "좋은 아이디어"일 뿐이다.  
→ `docs/governance/FUTURE_AUDIT_IDEAS.md`에 기록 후 대기.

---

## Constitution 근거

```
Article 7: Everything is an Experiment Until Proven Otherwise
  → AUDIT_PROMPT_v1.0도 운영 데이터가 쌓이기 전까지는 가설이다.
  → 먼저 실행하고, 데이터가 문제를 보여주면 그때 개선한다.

Article 2: Every Claim Needs Evidence
  → "Finding 관리가 필요하다"는 주장도 증거가 있어야 한다.
  → 현재 증거: 감사 1회 실행. 불충분.
```

---

*GD-004 — 2026-06-28*  
*AUDIT_PROMPT_v1.0은 Phase A 종료 전까지 수정하지 않는다.*
