# 리포팅 & 검증 체계

> **대상 독자**: 운영자, 개발자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `analysis/`, `backtests/`

---

## 1. 리포트 종류 및 실행 방법

### 1.1 일간 리포트

```bash
# 당일 거래 요약 (BUY/SELL, PnL, exit_reason 분포)
python3 -m analysis.daily_trade_report

# 스윙 전략 성과
python3 -m analysis.swing_report
```

**포함 내용**: 당일 거래 수, 승률, PF, avg R, exit_reason 분포, DD 최대값.

### 1.2 주간 리포트 (ops_weekly_review)

```bash
python3 -m analysis.ops_weekly_review
```

**자동 판정 항목 (A~E)**:

| 항목 | 기준 | 비고 |
|------|------|------|
| A: 수익성 | PF ≥ 1.2, avg R ≥ 0.3, MDD ≤ 15% | 자동 |
| B: 전략 구조 | EF/LCL 비율 < 50% | 자동 |
| C: Scientist 판정 | 가설 지지/반박/보류 | **수동 입력** (유일한 수동 항목) |
| D: 증거 수준 | Evidence Level E0~E3 | 자동 |
| E: 전략 변경 제안 | 다음 주 액션 힌트 | 자동 |

**다음 주 액션 힌트 예시**:
- HOLD: 현재 전략 유지
- YAML 조정: 특정 파라미터 조정 권고
- 검토 필요: 특이 패턴 발견 시

### 1.3 AI Research Layer 리포트

| 리포트 | 실행 | 출력 |
|--------|------|------|
| Observer AI 분석 | 07:32 자동 | `decision_log` DB 저장 |
| Analyst AI 세션 분석 | 15:30 자동 | `decision_log` + `research_notebook` |
| Scientist 주간 검증 | 금 16:40 자동 | `research_notebook` NB-XXX 생성 |
| Governance AI 심의 | 온디맨드 | `docs/governance/GD-XXX.md` |

### 1.4 검증 도구

```bash
# 수용 기준 47개 항목 (A~F 카테고리)
python3 -m analysis.acceptance_test

# E2E 통합 테스트 (Stage 1~10 + FI 8건)
python3 -m analysis.e2e_integration_test

# RAE 성과 검증
python3 -m backtests.rae_validation_runner --days 60

# 쿨다운 최적화 (오프라인)
python3 -m analysis.cooldown_optimizer --days 7

# EF 민감도 분석 (오프라인)
python3 -m analysis.ef_sensitivity_analyzer --days 7
```

---

## 2. 성과 지표 정의

| 지표 | 정의 | 계산 방법 |
|------|------|-----------|
| **PF (Profit Factor)** | 총 수익 / 총 손실 | `sum(win_pnl) / sum(loss_pnl)` |
| **avg R** | 평균 R 배수 | `mean(pnl / risk_amount)` |
| **MDD (Max Drawdown)** | 최대 누적 손실 | Peak-to-trough |
| **MAE (Max Adverse Excursion)** | 진입 후 최대 역행 | `(entry_price - trough) / entry_price` |
| **MFE (Max Favorable Excursion)** | 진입 후 최대 순방향 이익 | `(peak - entry_price) / entry_price` |
| **WR (Win Rate)** | 수익 거래 비율 | `win_count / total_count` |
| **EF Rate** | Early Failure 비율 | `ef_count / total_count` |
| **LCL Rate** | LCL 발동 비율 | `lcl_count / total_count` |

### R 단위 기준

R = 해당 거래의 최초 리스크 금액 (진입가 - Hard Stop가).

avg R ≥ 0.3 = 손익비 적정, ≥ 0.5 = 우수.

---

## 3. exit_reason 분포 분석

exit_reason별 성과 분석이 전략 개선의 핵심.

```sql
-- exit_reason 분포 쿼리
SELECT
    exit_reason,
    COUNT(*) AS cnt,
    ROUND(AVG(profit_rate)::numeric, 3) AS avg_pnl,
    ROUND(SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*), 2) AS win_rate
FROM trades
WHERE trade_type = 'SELL'
  AND trade_time >= NOW() - INTERVAL '30 days'
GROUP BY exit_reason
ORDER BY cnt DESC;
```

---

## 4. Evidence Level (증거 등급) 시스템

전략 변경 승인 기준. 증거가 충분하지 않으면 변경 불가.

| 레벨 | 기준 | 상태 |
|------|------|------|
| **E0** | 거래 수 < 10건 | 데이터 부족, 변경 금지 |
| **E1** | 10~29건 | 패턴 관찰 가능, 최소 변경만 |
| **E2** | 30건 + KPI 충족 | 심사 시작 가능 (30건 = 승인 아닌 심사 시작) |
| **E3** | E2 조건 + 반복 재현 | 구조 변경 승인 가능 |

**현재 상태**: E1 (29건 수준, 확인 필요)

**E2 심사 체크리스트** (`docs/E2_REVIEW_CHECKLIST.md`):
- A: 거래 수 충족 여부
- B: KPI 충족 여부 (PF, avg R, MDD)
- C: 효과 일관성
- D: 영향 범위
- E: 반복성

---

## 5. 전략 변경 승인 기준

```
아이디어 → Research Notebook → Evidence → Hypothesis → Experiment → Approval → Implementation
```

**변경 불가 조건**:
- E0~E1 상태에서 구조적 변경 시도
- `approve_proposal.py` E2 미만 차단 발동
- `--force` 플래그: 장애/버그/API대응만 허용

**변경 허용 조건**:
- E2 이상 달성 후 `E2_REVIEW_CHECKLIST.md` 5개 항목 완성
- YAML 파라미터 조정은 별도 승인 없이 가능

---

## 6. RAE Shadow → Ready 판정 기준

```bash
python3 -m backtests.rae_validation_runner --days 60
```

**판정 조건 (7개 모두 충족 시 RAE_GO_LIVE_READY)**:

| # | 조건 | 기준값 |
|---|------|--------|
| 1 | RAE 거래수 | ≥ 20건 |
| 2 | RAE 단독 승률 | ≥ 35% |
| 3 | RAE 단독 PF | ≥ 1.10 |
| 4 | RAE avg R | ≥ 0.25 |
| 5 | 전체 PF 유지 | 악화 ≤ 5% |
| 6 | 전체 MDD | baseline 대비 +15% 이내 |
| 7 | RAE LCL/EF 비율 | < 60% |

---

## 7. ML Filter 검증 기준

```yaml
ml_filter:
  shadow_mode: true      # 현재: shadow (로그만)
  threshold: 0.50

eq_ml_filter:
  shadow_mode: true      # 현재: shadow
```

**실전 전환 조건** (현재 미충족):
- labeled 50건 이상
- Shadow AUC ≥ 0.60
- `analysis/eq_shadow_report.py`로 Shadow 비교 후 `eq_promote.py` 실행

---

## 8. 수용 기준 테스트 (acceptance_test.py)

47개 항목, A~F 카테고리:

| 카테고리 | 항목 수 | 내용 |
|----------|---------|------|
| A | 기본 인프라 | DB 연결, 테이블 존재, 로그 경로 |
| B | 전략 설정 | YAML 파라미터 유효성 |
| C | 거래 기록 | trades 테이블 필드 완전성 |
| D | 리스크 엔진 | DrawdownEngine, Exit Logic |
| E | AI Layer | decision_log, research_notebook |
| F | AI Research Layer | 4개 AI Agent 정상 동작 |

**최근 결과**: 47/47 PASS (2026-06-29 기준).
