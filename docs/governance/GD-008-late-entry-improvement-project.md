# GD-008: 늦은 진입 개선 프로젝트 (Late Entry Improvement Project)

**상태**: IN_PROGRESS — Fix C 적용 완료 (2026-07-04)  
**작성일**: 2026-07-04  
**배경 가설**: NB-007, H-002~H-005  
**오프라인 백테스트**: NB-008 (`reports/late_entry_backtest_report_20260704.md`)  
**분석 스크립트**: `analysis/e2_phase1_analysis.py`

---

## 변경 이력

| 날짜 | Fix | 내용 | 상태 |
|---|---|---|---|
| 2026-07-04 | Fix C v1 | 10:30~11:30 단순 시간 차단 | ↩️ 방향 수정 (v2로 대체) |
| 2026-07-04 | Fix C v2 | 10:30 이후 늦은 추격 3-조건 필터 (`late_entry_control.c_late_v2`) | ✅ 적용 |
| - | Fix B-lite | 후보 후 과도 지연 진입 금지 (`candidate_delay_limit`) | ⏳ E2 후 검토 |
| - | Fix A | 절대 수익률 과열 필터 | ❌ 기각 (H-002 역방향) |

### Fix C v2 상세

**3-조건 OR 로직** (10:30 이후에만 적용):

| 조건 | YAML 키 | 기본값 | 설명 |
|---|---|---|---|
| 조건1 추격 | `max_cand_rise_pct` | 1.5% | t0 후보 가격 → 진입가 상승률 초과 시 차단 |
| 조건2 고점근처 | `max_day_high_proximity_pct` | 1.5% | 당일 고가 대비 -1.5% 이내면 차단 |
| 조건3 지연신호 | `max_cand_to_entry_min` | 60분 | 후보→CHoCH 60분 초과 시 차단 |

**구현**: `main_auto_trading.py::_check_late_chase_entry()` — execute_buy() 내 Kiwoom API 호출 직전  
**롤백**: `late_entry_control.c_late_v2.enabled: false`

---

## 원칙

1. **E2(30건 이상) 도달 전 전략 로직 변경 금지** — 현재는 관측만 수행
2. 한 번에 하나의 변경만 적용 후 성과 검증
3. 각 변경마다 Before / After 성과 비교 필수
4. **권고**: 가능하면 50~60건 확보 후 임계값 결정 (승매 표본 부족 시 과적합 위험)

---

## 분석 대상 코호트

```sql
-- instrumented cohort만 사용 (H-003/H-005 분석 시 필수)
WHERE candidate_first_time IS NOT NULL
AND b.entry_time >= '2026-07-04'
```

> Legacy cohort (candidate_first_time IS NULL, 2026-07-03 이전)는
> H-002(후보 과열)만 yfinance 근사 검증 가능. H-003/H-005 혼합 금지.

---

## Phase 1: 원인 분석 (E2 도달 후)

### H-002 — 후보 과열 가설 (우선순위 1)

**목적**: candidate_first_price 시점부터 이미 과열된 종목이 손실로 이어지는가

**분석 항목**:
- `candidate_first_price` 기준 5일 상승률
- `candidate_first_price` 기준 10일 상승률
- 승/패 평균 비교
- 분포 (히스토그램)
- 임계값 후보 (승률 급락 지점)

**산출물**: 분석 리포트 + 임계값 제안

---

### H-003 — SMC 추가 지연 가설 (우선순위 2)

**목적**: 후보→진입(t0→t1) 지연이 손실 원인인지 확인

**분석 항목**:
- `cand_to_entry_min`: 분 단위 지연
- `cand_to_entry_pct`: 가격 지연 (%)

**비교군**: 승매 / 패매 / Early Failure

**산출물**: 평균 + 분포 + 지연 허용 범위 제안

---

### H-005 — 진입 시간대 가설 (우선순위 3)

**목적**: 시간대별 성과 차이가 유의한가

**버킷**:
- 09:00~09:30 (MORNING_OPEN)
- 09:30~10:30 (MORNING_MID)
- 10:30~11:30 (AFTERNOON_EARLY)
- 11:30 이후  (AFTERNOON_LATE)

**산출물**: 버킷별 승률 + 평균손익

---

## Phase 2: 개선안 설계

분석 결과를 바탕으로 아래 3가지 개선안 중 적용 순서 결정.

| 개선안 | 내용 | 검토 조건 |
|---|---|---|
| **A** 후보 과열 필터 | 5일/10일 상승률 임계값으로 후보 제외 | H-002 분포에서 임계값 확정 후 |
| **B** SMC 진입 앞당기기 | CHoCH/OB reclaim 조건 완화, 더 이른 진입 | H-003 지연이 유의할 때만 |
| **C** 시간대 필터 | 10:30 또는 11:00 이후 신규 진입 제한 | H-005 버킷 분석 후 |

---

## Phase 3: 구현 순서

> **절대 여러 개 동시 적용 금지**

```
Step 1: 개선안 A (과열 필터) 적용
        → 20~30건 수집 → Before/After 비교 → 성과 검증
        ↓ 통과 시
Step 2: 개선안 B (SMC 수정) 적용
        → 20~30건 수집 → Before/After 비교
        ↓ 통과 시
Step 3: 개선안 C (시간대 필터) 적용
        → 20~30건 수집 → Before/After 비교
```

---

## Phase 4: 각 단계 검증 비교표

| 항목 | Before | After |
|---|---|---|
| 승률 | | |
| 평균손익 | | |
| Profit Factor | | |
| Early Failure 비율 | | |
| 평균 MAE | | |
| 평균 MFE | | |
| 평균 진입 시각 | | |
| 평균 5일 상승률 (t0 기준) | | |
| 평균 후보→진입 지연 (min) | | |

---

## 최종 목표

늦은 진입 제거:
- 후보: 더 이른 시점에 선정
- SMC: 필요한 확인만 수행
- 추격매수 감소
- Early Failure 감소
- Profit Factor 상승

---

## 참조

- 배경 분석: NB-007 (research_notebook)
- 가설 원문: H-002, H-003, H-004, H-005 (hypotheses 테이블)
- 분석 스크립트: `analysis/e2_phase1_analysis.py`
- 타이밍 인프라: `signal_orchestrator.py::get_candidate_info()`
- 데이터: `trades.candidate_first_time`, `trades.entry_signal_time`
