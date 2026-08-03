# DRAWDOWN_VALIDATION

Iteration 7-1 · 실측 검증 (읽기 전용)

## 1. 공식 검증 — PASS

| 항목 | 코드 | 판정 |
|---|---|---|
| dd 공식 | `dd = (equity - self._peak) / self._peak` (equity_controller.py) | ✅ |
| 임계 비교 | `dd <= halt_pct` , `halt_pct = -0.18` | ✅ |
| NaN 처리 | `equity != equity` → Fail Closed | ✅ |
| peak<=0 | `no_peak` 로 통과 (초기 상태) | ✅ |

**로그 400건 재계산 · 불일치 0건.**
`signal_rejections` 의 `dd=…% (equity=… peak=…)` 문자열을 파싱해
`(equity-peak)/peak` 를 다시 계산한 결과 400/400 일치.

**False Trigger 0건.** EC_HALT 28,527건 전부 `dd ≤ -18%` 였다.
임계값보다 얕은 낙폭에서 차단된 사례는 없다.

## 2. equity 입력값 검증 — **FAIL**

dd 공식은 맞지만 **입력 equity 가 틀린 날이 있다.**

```
main:2391  _pymn_alow_str = balance_info.get('pymn_alow_amt') …
main:2392  self.withdrawable_cash = float(_pymn_alow_str)
main:2410  self.total_assets = self.withdrawable_cash + self.positions_value
```

`pymn_alow_amt` = **출금가능금액**. 매도 체결 대금은 결제(D+2) 전까지
출금가능금액에 들어오지 않는다. 매도 직후에는

- 포지션이 사라져 `positions_value` 가 0 으로 떨어지고
- 매도대금은 아직 `pymn_alow_amt` 에 없다

→ **equity 가 매도대금만큼 통째로 사라진다.**

### 실측 (2026-05-22)

| 항목 | 값 | 출처 |
|---|---|---|
| 05-21 EOD equity | 4,988,840 | signal_rejections |
| 05-22 매도 체결 | **4,526,860** (SELL 1건) | `trades` |
| 05-22 잔고 로그 | `잔고 업데이트: 542,250원 (총자산: 542,250원)` | auto_trading_20260522.log |
| 05-22 EC_HALT equity | 542,250 | signal_rejections |
| 05-26 EC_HALT equity | 542,250 | signal_rejections |
| 06-19 EC_HALT equity | **4,773,205** | signal_rejections |

매도대금 4,526,860 이 `positions_value` 에서는 빠지고
`withdrawable_cash` 에는 들어오지 않아 총자산이 542,250 이 됐다.
계좌가 실제로 89% 증발한 것이 아니다 — 결제 전 미수령 대금이
계산에서 누락된 것이다. 06-19 에 4,773,205 로 돌아온 것이 그 증거다.

이 상태로 계산된 dd 는 **-90.0%** 였고, EC_HALT 는 그 값으로 차단했다.

### 같은 패턴이 관측된 날

| 날짜 | EC_HALT equity | 직전 EOD | 직전 매도 |
|---|---|---|---|
| 2026-05-22 | 542,250 | 4,988,840 | 05-22 SELL 4,526,860 |
| 2026-05-26 | 542,250 | (동일 유지) | — |
| 2026-06-25 | 2,683,409 | 6,628,719 | 06-24 SELL 1,884,000 |

2026-06-18 의 1,605,311 은 같은 날 08:50:19 `get_balance` **TimeoutError** 가
로그에 남아 있으나, 그 실패와 1,605,311 이 같은 조회에서 나왔는지는 **확인 불가**.

### 사용 가능한 대체 필드

`kiwoom_api.get_balance()` (kt00001) 이 코드 주석상 노출하는 필드는
`entr`(예수금) · `ord_alow_amt`(주문가능금액) · `pymn_alow_amt`(출금가능금액).
`entr` 은 매수 미결제분이 안 빠져 이중계산이 되고(2026-07-06 수정 이력),
`pymn_alow_amt` 는 매도 미결제분이 안 들어온다.
**D+2 추정예수금 필드가 응답에 있는지는 실계좌 조회 없이 확인 불가.**

## 3. 결론

| 대상 | 판정 |
|---|---|
| dd 공식 | PASS |
| peak 단조성·저장·복원 | PASS |
| peak 폴백 가드 (2026-07-12) | PASS |
| False Trigger | PASS (0건) |
| **equity 입력값** | **FAIL — 매도 미결제 대금 누락** |
