# 금요일 Verdict → 다음 주 액션 결정표
> `ops_weekly_review.py` 출력 결과를 받아 다음 주 행동을 확정하는 단일 기준표.
> "이 표에 없는 재량 판단은 허용하지 않는다."

---

## 1단계 — A 먼저 확인 (거래수)

| A 결과 | 의미 | 다음 주 행동 |
|---|---|---|
| **FAIL** | 누적 30건 미달 | **B~E 무시. 정책 유지. 거래 축적만.** B가 ✗여도 전략 수정 금지 — 운영 이슈로 기록만. |
| **PASS** | 30건 이상 | 2단계 진행 |

> A=FAIL이면 표 읽기 끝. 다음 주 액션은 "현 정책 유지, 거래 축적" 한 가지뿐.

---

## 2단계 — B 확인 (KPI 8개)

A=PASS인 경우에만 읽는다.

| B 결과 | 의미 | 다음 주 행동 |
|---|---|---|
| **FAIL** (`✗` 있음) | 운영 KPI 이상 | **전략 심사 중단. 해당 ✗ 항목 원인 파악 후 수정.** E2 심사 보류. |
| **부분** (`?` 있음) | 수동 항목 미확인 | 수동 항목 3개(`주문 실패율·Scientist·Approval`) 확인 후 재판정. 확인 전 전략 수정 금지. |
| **PASS** | 8개 모두 ✓ | 3단계 진행 |

> B=FAIL이면 "문제가 전략에 있는가, 운영에 있는가"를 먼저 구분한다.
> KPI ✗ → 운영 문제. C~E → 전략 문제. 이 순서가 뒤바뀌면 잘못된 수정이 생긴다.

---

## 3단계 — C/D/E + Scientist 조합 (A=PASS, B=PASS/부분 확인 후)

### C: 효과 일관성

| C 결과 | 의미 | 다음 주 행동 |
|---|---|---|
| **N/A** | 구간별 샘플 3건 미만 | 기록만. 판단 보류. |
| **일관됨** | AI Score 높을수록 성과 좋음 | 현 파라미터 유지. |
| **불일치** | Score 구간과 성과 역전 | PROMOTE_TO_E2 후보. 다음 주 Scientist로 구체화. |

### D: 영향 범위

| D 결과 | 의미 | 다음 주 행동 |
|---|---|---|
| **SWING만** | 정상 | 현 구조 유지. |
| **EXPLORATION 동반 하락** | 파라미터 과적합 의심 | Scientist → BLOCKED_BY_OPS. 수정 금지. |
| **N/A** | 이번 주 거래 없음 | 기록만. |

### E: 반복성 (Scientist verdict)

| Scientist | 이번 주 관찰 | 다음 주 행동 |
|---|---|---|
| **KEEP_WATCH** | 아직 결론 없음 | 같은 관찰 항목 유지. note에 추적 기간/조건 명시. |
| **PROMOTE_TO_E2** | 반복 패턴 + 데이터 근거 확보 | OPERATIONS_LOG E2 후보란에 등록. 다음 리뷰에서 C 항목 집중 확인. |
| **DROP_NOISE** | 일회성/재현 없음 | strategy_proposals.jsonl에서 해당 항목 status=REJECTED 처리. |
| **BLOCKED_BY_OPS** | 운영 이슈 선행 | 운영 이슈 해소 전까지 해당 아이디어 동결. B ✗ 해소 후 KEEP_WATCH로 전환. |

---

## 최종 조합 요약표

A=PASS + B=PASS 상황에서 C/D/E 조합별 결론:

| C | D | E (Scientist) | 다음 주 결론 | 액션 |
|---|---|---|---|---|
| N/A | SWING만 | KEEP_WATCH | **E2 대기** | 표본 축적. 정책 유지. |
| N/A | SWING만 | PROMOTE_TO_E2 | **E2 후보 등록** | OPERATIONS_LOG E2 후보란 추가. |
| 일관됨 | SWING만 | KEEP_WATCH | **E2 심사 진입** | E2_REVIEW_CHECKLIST.md 작성. |
| 일관됨 | SWING만 | PROMOTE_TO_E2 | **E2 심사 진입** | E2_REVIEW_CHECKLIST.md 작성 + 해당 항목 포함. |
| 불일치 | SWING만 | KEEP_WATCH | **관찰 강화** | 해당 Score 구간 ENTRY_SNAPSHOT 로그 집중 확인. |
| 불일치 | SWING만 | PROMOTE_TO_E2 | **E2 후보 등록** | 불일치 원인 + 데이터 근거 함께 등록. |
| any | EXPL 동반 하락 | any | **수정 금지** | Scientist → BLOCKED_BY_OPS. 다음 주 D 재확인. |
| N/A | N/A | DROP_NOISE | **정상 유지** | 해당 proposal REJECTED 처리. 다음 주 신규 관찰. |

---

## 빠른 판단 흐름 (체크리스트)

```
□ A=FAIL? → 끝. 정책 유지.
□ B=FAIL? → 끝. 운영 KPI 수정.
□ B=부분? → 수동 3개 확인 후 재판정.
□ D=EXPL 동반 하락? → 끝. 수정 금지.
□ C=불일치 + E=PROMOTE_TO_E2? → E2 후보 등록.
□ C=일관됨 + B=PASS? → E2_REVIEW_CHECKLIST.md 작성.
□ 나머지 → 정책 유지 + 관찰 항목 KEEP_WATCH 유지.
```

---

## Scientist note 작성 기준

| 분류 | 좋은 예 | 덜 좋은 예 |
|---|---|---|
| KEEP_WATCH | `NO_TRADE_DAY 차단 종목의 사후 alpha 2주 더 수집` | `조금 더 보자` |
| PROMOTE_TO_E2 | `Score≥70 구간 3주 연속 승률 80%+, E2 심사 후보` | `좋아 보임` |
| DROP_NOISE | `장중 15시 이후 바이오 단발성 반등 3회 관찰, 재현성 없음` | `별로임` |
| BLOCKED_BY_OPS | `swing_executor 안정화 전까지 종가 스윙 아이디어 보류` | `나중에` |

> 기준: **관찰 대상 + 이유 + 기간/조건** 세 가지가 note에 들어가야 한다.

---

## 정책 변경 허용 조건 (요약)

```
전략 수정 허용 ← E2_REVIEW_CHECKLIST A~E 전항목 PASS
                + 금요일 verdict = "E2 심사 진입"
                + PM(사용자) 명시적 승인

전략 수정 금지 ← 위 조건 미충족 시 항상
```

> E2 심사 진입 ≠ 수정 승인. 심사 진입 후 CHECKLIST 5항목(A거래수/B-KPI/C효과/D범위/E반복)
> 모두 PASS여야 수정 후보 1개씩 검토 가능.
