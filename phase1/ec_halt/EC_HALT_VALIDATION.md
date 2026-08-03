# EC_HALT_VALIDATION

Iteration 7-1 · 최종 판정

```
==============================
EC_HALT VALIDATION
==============================
Peak              4,467,501   (data/equity_state.json, 2026-07-12~)
Equity            3,371,599   (최신 EC_HALT 로그, 2026-07-31)
Drawdown          -24.5%
Threshold         -18%
Trigger Count     28,527  (29 영업일)
False Trigger     0
Restart Test      PASS
Persistence       PASS
Regression        495 PASS / 16 Known FAIL
Runtime Error     0
==============================
```

## 판정: **FAIL**

작업지시서 §성공기준의 3택 중 **FAIL (Drawdown 계산 버그)** 에 해당한다.

**단, 버그의 위치가 공식이 아니라 입력값이다.**

| 세부 | 판정 |
|---|---|
| dd 공식 `(equity-peak)/peak` | PASS |
| peak 단조성 / EOD-only / 저장 / 복원 | PASS |
| peak 폴백 오염 가드 | PASS (2026-07-12 수정 완료) |
| False Trigger | PASS (0건) |
| 재시작 / 영속성 | PASS |
| 회귀 / 런타임 에러 | PASS |
| **equity 입력값 (매도 미결제 대금 누락)** | **FAIL** |

## FAIL 근거 (실측 1건으로 충분)

```
2026-05-22
  매도 체결      4,526,860원   (trades)
  잔고 로그      "잔고 업데이트: 542,250원 (총자산: 542,250원)"
  EC_HALT dd     -90.0%
2026-06-19
  EC_HALT equity 4,773,205원   ← 자금은 소멸하지 않았다
```

`total_assets = pymn_alow_amt + positions_value` 이므로
매도 체결 후 결제(D+2) 전에는 매도대금이 어느 항에도 없다.
EC_HALT 는 이 허수 낙폭으로 진입을 차단했다.

## Threshold 재검토 결과: 불필요

-18% → -30% 로 완화해도 차단 일수 17/29일로 **변화 없음**.
임계값은 이번 HALT 의 원인이 아니다. (`EC_HALT_AUDIT.md` §4)

## 이번 이터레이션에서 변경한 것

**없음.** 전 과정 읽기 전용.
임계값·전략·진입/청산 조건·Position Size·Feature Flag·상태파일 모두 무변경.

## 산출물

| 파일 | 내용 |
|---|---|
| `EC_HALT_AUDIT.md` | 종합 감사 + threshold 민감도 |
| `EC_HALT_CALL_FLOW.md` | 호출 경로 · Fail Closed 지점 |
| `PEAK_TIMELINE.md` | peak 변화 이력 + 2026-06-10 오염 사건 |
| `DRAWDOWN_VALIDATION.md` | dd 공식/입력값 검증 |
| `EC_HALT_VALIDATION.md` | 최종 판정 (이 문서) |
| `ec_halt_validation.json` | 기계 판독용 검증 결과 |
