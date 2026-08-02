# E2 심사 체크리스트
> E2 도달 직후 1회, 이후 매주 금요일 반복 사용.  
> `python3 -m analysis.ops_weekly_review` 출력값을 옮겨 적는다.

---

## 심사 기본 정보

```
날짜        :
Ruleset     : v1.1
Proposal ID :
NB 번호     :
Scientist 핵심 제안 (1줄):
```

---

## A. 거래 수 (최소 조건)

```
실거래 누적  : ___ 건
기준         : 30건 이상

결과: □ PASS (≥30)   □ FAIL (<30)
```

A가 FAIL이면 심사 중단. E1 유지.

---

## B. KPI 8개 체크

`python3 -m analysis.ops_daily_check` 최근 7일 결과를 기입한다.

```
1. ENTRY_SNAPSHOT 누락   □ PASS (0건)   □ FAIL
2. AI Gate 오동작        □ PASS (0건)   □ FAIL
3. 주문 실패율           □ PASS (<0.5%) □ FAIL  (___ %)
4. 시스템 예외 거래 실패  □ PASS (0건)   □ FAIL
5. 로그 복원 가능률      □ PASS (100%)  □ FAIL
6. Scientist 제안 성공    □ PASS         □ FAIL  □ 미실행
7. Approval 프로세스     □ PASS         □ FAIL  □ 미실행
8. --force 사용 건수     □ PASS (0건)   □ FAIL  (___ 건)

KPI 결과: 8개 모두 PASS  →  □ 전체 통과   □ ___ 개 실패
```

B에서 1개라도 FAIL이면 E2 보류. KPI 회복 후 재심사.

---

## C. 제안 효과의 일관성

> Scientist 제안 효과를 구간별로 비교한다.  
> `ops_weekly_review` → AI Score 구간 분석 결과를 사용.

```
제안 내용 (예: AI Gate 50→55):

              | 현재 구간 (50~54) | 제안 구간 (55+)
─────────────────────────────────────────────────────
거래 수       |                   |
승률          |                   |
Profit Factor |                   |
평균 수익(%)  |                   |

개선 폭 판정:
  승률 차이  : ___ %p   → □ 의미 있음 (>10%p)  □ 불확실
  PF 차이    : ___       → □ 의미 있음 (>0.3)   □ 불확실
  평균수익 차: ___ %     → □ 의미 있음 (>0.5%)  □ 불확실

착시 여부 확인:
  □ 한두 거래가 결과를 왜곡하는가?  (예: 이상치 1건 제거 시 차이 사라짐)
  □ 특정 1주간에만 집중된 결과인가?

C 결과: □ 일관성 있음   □ 불확실 (보류)   □ 착시 의심 (E2 불인정)
```

---

## D. 영향 범위 확인

```
이 제안이 영향을 미치는 전략:  □ SWING  □ EXPLORATION  □ 전체

SWING 결과  :  승률 ___ %  PF ___  avg ___  %
EXPLORATION :  승률 ___ %  PF ___  avg ___  %

개선 vs 악화:
  □ SWING만 개선됨         → 영향 범위 명확
  □ SWING 개선, EXPL 동일  → 영향 범위 명확
  □ SWING 개선, EXPL 악화  → 보류 필요
  □ 전체 영향 불명확        → E2 불인정

D 결과: □ 영향 범위 명확   □ 보류   □ E2 불인정
```

---

## E. Scientist 제안의 반복성

```
이번 주 제안 방향  :
지난주 제안 방향   :  □ 없음 (첫 제안)  □ 있음 →

동일 방향 여부     :  □ 동일 (E3 후보)  □ 다름  □ 첫 제안

E3 판정 조건 충족  :  □ 2주 연속 동일 방향 → E3 후보 등록
                       □ 미충족 → E2 유지
```

---

## 최종 판정

A~E를 종합한다.

```
A. 거래 수       : □ PASS  □ FAIL
B. KPI 8개       : □ PASS  □ FAIL (___ 개 실패)
C. 효과 일관성   : □ 있음  □ 불확실  □ 착시
D. 영향 범위     : □ 명확  □ 불명확
E. 반복성        : □ E3 후보  □ E2 유지  □ 첫 제안
```

### 판정 결론

| 조건 | 결론 |
|---|---|
| A PASS + B 전체 PASS + C 있음 + D 명확 + E E3 후보 | **E3 확정 → v1.2 승인 검토** |
| A PASS + B 전체 PASS + C 있음 + D 명확 + E 첫/미충족 | **E2 인정, 1주 보류** (다음 주 E 재확인) |
| A PASS + B 일부 FAIL 또는 C/D 불확실 | **E2 보류** (조건 개선 후 재심사) |
| A FAIL 또는 B 전체 FAIL | **E1 유지** (심사 중단) |

```
이번 판정: ___________________________________________
사유      : ___________________________________________
다음 심사 : ___________________________________________
```

---

## 승인/반려 명령 (판정 후 실행)

```bash
# 승인 시
python3 -m analysis.approve_proposal --approve PROP-XXXX \
  --comment "E2 충족: 거래 30건, KPI 8개 PASS, SWING PF 1.4 확인, Scientist 2주 동일 방향"

# 보류 시 (반려 + 사유 기록)
python3 -m analysis.approve_proposal --reject PROP-XXXX \
  --comment "E2 형식 충족했으나 EXPLORATION 성과 확인 필요. 1주 후 재심사."

# 결과 확인
python3 -m analysis.approve_proposal --list
```
