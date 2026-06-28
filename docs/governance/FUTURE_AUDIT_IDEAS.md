# Future Audit Ideas — 보류 중인 감사 체계 아이디어

> 이 파일은 "좋은 아이디어이지만 아직 Evidence가 없는 것"을 보관한다.  
> Evidence가 충분해지면 Governance Decision으로 격상한다.  
> **구현 금지. 기록만 한다.**

---

## IDEA-001: Audit Findings Lifecycle

**제안일:** 2026-06-28  
**제안 근거:** GD-004 논의 중 식별

### 배경

현재 감사 구조:
```
Audit → Evidence → Report (종료)
```

감사 표준(ISO 19011, SOC2, ISO 27001)의 구조:
```
Audit → Finding → Lifecycle (OPEN → FIXED → VERIFIED → CLOSED)
```

Trading OS 내 다른 객체들은 모두 Lifecycle을 가진다.

| 객체 | Lifecycle |
|------|---------|
| Hypothesis | draft → queued → experiment → approved / rejected |
| Knowledge | active → is_active=FALSE (retired) |
| Scientist Prediction | pending → evaluated / expired |
| **Audit Finding** | (없음) — 현재 비대칭 |

### 제안 스키마

```sql
-- 미래 구현 시 참고용 (현재 생성 금지)
CREATE TABLE audit_findings (
    finding_id      VARCHAR PRIMARY KEY,  -- 예: 'A-001', 'CRIT-001'
    audit_date      DATE NOT NULL,
    severity        VARCHAR NOT NULL,     -- CRITICAL / IMPORTANT / ADVISORY
    category        VARCHAR,             -- Execution / Research / DB / Docs 등
    evidence_level  VARCHAR,             -- L1_Runtime / L2_Code / L3_Document
    description     TEXT NOT NULL,
    root_cause      TEXT,
    impact          TEXT,
    recommendation  TEXT,
    status          VARCHAR DEFAULT 'OPEN',  -- OPEN / FIXED / ACCEPTED / REJECTED
    verified_at     DATE,
    verified_by     VARCHAR,
    notes           TEXT
);
```

### PDCA 연결

```
Plan   → AUDIT_PROMPT_v1.0 실행
Do     → Finding 기록 (audit_findings INSERT)
Check  → 다음 감사에서 OPEN → FIXED 확인
Act    → 반복 Finding 패턴 → AUDIT_PROMPT 개선 또는 Architecture 조정
```

### 도입 조건 (GD-004 명시)

```
□ 감사 실행 횟수 ≥ 3~5회
□ 동일 유형 Finding 반복 등장
□ 수동 추적 한계 도달
```

**현재 상태:** 감사 1회 — 조건 미달, 구현 보류.

---

*이 파일에 새 아이디어 추가 시: 제안일, 제안 근거, 도입 조건을 반드시 기록한다.*
