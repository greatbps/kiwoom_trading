# GD-007: Research Schema Exception — APPROVED

**날짜**: 2026-06-30  
**유형**: Policy Amendment (GD-003 개정)  
**상태**: APPROVED  
**결정자**: PM

---

## 개정 대상

GD-003 (2026-06-28) — Phase A 금지 목록 중 **"새로운 DB 테이블"** 항목

---

## 개정 내용

### 기존 (GD-003)

> Phase A 동안 새로운 DB 테이블 추가 금지 (예외 없음)

### 개정 후 (GD-007)

> Phase A 동안 **운영 스키마(public schema)의** 새로운 DB 테이블 추가 금지 원칙은 유지한다.  
> 단, **`research` 스키마**는 공식 예외 영역으로 선언한다.

---

## 예외 허용 범위

```
허용 (research 스키마)            금지 (public/운영 스키마)
────────────────────────────     ────────────────────────────
research.candidates              trades 확장 (신규 테이블)
research.decision_ledger         signal_rejections 분리 신규
research.future_returns          기능 추가용 임시 테이블
research.hypotheses (개정)       분석용 운영 테이블 추가
research.decision_audits         모든 운영 스키마 스키마 변경
research.knowledge_graph         (컬럼 추가는 별도 ADR 필요)
```

---

## 예외 근거

**1. 성격의 차이**

GD-003의 "새로운 DB 테이블 금지" 목적은 기능 추가 시마다 테이블이 누적되는 것을 방지하기 위함이었다.  
`research` 스키마는 기능 테이블이 아닌 **Trading Research Platform의 Canonical Data Model**이다.  
이는 옵션 기능이 아니라 시스템의 핵심 데이터 계층이다.

**2. 기술부채 방지**

지금 `research` 스키마를 도입하지 않으면:
- 기존 테이블에 임시 JSON 컬럼, 임시 로그 컬럼이 추가되고
- 나중에 `decision_log`, `reject_log`, `audit_log`, `shadow_log`를 다시 분리하는 비용이 발생한다.
- 스키마를 바로잡기 가장 좋은 시점은 지금이다.

**3. 통제 유지**

무제한 허용이 아니다. 아래 Guard Rail이 적용된다.

---

## Guard Rails

### 필수 조건 (research 스키마 신규 테이블 생성 전)

```
□ DATA_CONTRACT.md에 해당 계약 섹션 존재
□ INVARIANT, IMMUTABLE, EVENTS 정의 완료
□ lifecycle_status 또는 상태 머신 정의 (해당 시)
□ PM 명시적 승인
```

### 모든 research 테이블 표준 메타데이터 (REQUIRED)

```sql
schema_version     SMALLINT  NOT NULL DEFAULT 1,
created_by         VARCHAR   NOT NULL,        -- 'system' | 'pm' | 'scientist_ai'
created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
```

### 버전 추적 컬럼 (해당 테이블에 적용)

```sql
-- decision_ledger
decision_schema_version  SMALLINT NOT NULL DEFAULT 1

-- hypotheses
hypothesis_schema_version SMALLINT NOT NULL DEFAULT 1

-- future_returns
returns_schema_version   SMALLINT NOT NULL DEFAULT 1
```

`schema_version`은 해당 테이블의 컬럼 구조가 변경될 때 단조증가한다.  
이를 통해 마이그레이션 없이 구버전 데이터의 해석 방식을 추적할 수 있다.

---

## 변경되지 않는 것

| 항목 | 상태 |
|------|------|
| public 스키마 신규 테이블 금지 | **유지** |
| 새로운 AI Agent 추가 금지 | **유지** |
| 새로운 Strategy 추가 금지 | **유지** |
| Architecture 변경 0회 목표 | **유지** |
| Phase A 완주 목표 (2026 Q3) | **유지** |

---

## Phase 2 착수 체크리스트 (GD-007 승인 이후)

```
✅ Decision Contract 확정 (DATA_CONTRACT.md v2.0)
✅ Hypothesis Contract 확정 (v2.0 — version, group_id 추가)
✅ Event/Lifecycle 정의 완료
✅ decision_reason_code 열거값 확정 (13개)
✅ Immutable 원칙 확정 (DB 트리거 강제)
✅ GD-007 research 스키마 예외 승인  ← 지금
────────────────────────────────────
→ DDL 생성 가능
```

---

## Constitution 적용

**Article 3 (변경 제안 기준)**: "운영 데이터가 설계보다 우선한다."  
현재 `signal_rejections` 테이블의 한계는 운영 데이터가 보여주는 설계 한계다.  
Decision Ledger는 이 한계를 해소하기 위한 최소 필요 변경이다.

**Article 5 (최소 변경 원칙)**: research 스키마를 별도 격리함으로써  
운영 스키마에 대한 영향을 0으로 유지한다.

---

## 다음 단계

1. **Phase 2**: `research` 스키마 + `decision_ledger` DDL 작성
2. **Phase 3**: `main_auto_trading.py` → Decision Ledger INSERT 연결
3. **Phase 3**: Future Returns 비동기 job (`scripts/returns_collector.py`)

---

*GD-007 — 2026-06-30 (PM 결정)*  
*GD-003을 부분 개정함. GD-003의 나머지 조항은 유효.*
