# TRUE_EQUITY_DEPLOYMENT

Iteration 7-3 · 2026-08-03

## 판정: **FAIL** (KPI 미달)

계산식 수정은 성공했다. 그러나 목표였던 **False EC_HALT 제거 ≥90% 는 달성되지 않았다 (0%)**.
지시서 기준 → 원인 분석 후 Iteration 7 보완.

**원인은 이미 규명됐다** — False Trigger 가 애초에 없었다. §4 참조.

## 1. KPI

| 항목 | 목표 | 결과 | 판정 |
|---|---|---|---|
| True Equity 적용 | PASS | `d2_entra` 기반으로 교체, 2곳 | ✅ |
| Peak 재산출 | PASS | 3정의 산출 완료 (상태파일 미반영, §5) | ⚠️ |
| Drawdown 재계산 | PASS | 67 영업일 재계산 | ✅ |
| **False EC_HALT 제거** | **≥90%** | **0%** (TWR 정의로도 76%) | ❌ |
| 정상 EC_HALT 유지 | 100% | 100% (29/29일 유지) | ✅ |
| Regression | PASS | **507 PASS / 16 Known FAIL** (495→507) | ✅ |
| Runtime Error | 0 | 0 | ✅ |

## 2. Phase 1 — 적용한 변경

`main_auto_trading.py`

```python
def _settled_cash(self, balance_info, pymn_alow_str):
    """D+2 정산 기준 예수금(d2_entra). 실패 시 출금가능금 폴백 + WARN."""
```

- 예수금 항: `pymn_alow_amt` → **`d2_entra`** (2곳: `initialize_account`, 잔고 갱신 루틴)
- `self.total_assets = self.settled_cash + self.positions_value` (2곳)
- **폴백**: `d2_entra` 없음/비수치/0 인데 출금가능금 양수 → `pymn_alow_amt` 사용 +
  `logger.warning("[EQUITY_D2] ...")`. **조용한 폴백 없음** (테스트로 강제)
- **관측 로그**: 하루 1회 `[EQUITY_D2] entr= pymn_alow= d1_entra= d2_entra=`
  → 미결제가 있는 첫 날에 Iteration 7-2A §5 의 미확정 항목이 자동 해소된다
- `withdrawable_cash` 는 스냅샷 기록용으로 **유지** (기존 시계열 비교 가능)

변경하지 않은 것: 전략, 진입/청산 조건, Position Size, EC_HALT 임계값, DDL.

## 3. Phase 5 — Dry Run (실계좌 응답, 2026-08-03)

```
출금가능금(구)   1,857,799        [EQUITY_D2] entr=...1857799 pymn_alow=...1857799
D+2 정산예수금   1,857,799                    d1_entra=...1857799 d2_entra=...1857799
평가액           1,827,000
총자산(구)       3,684,799
총자산(신)       3,684,799
브로커 기준      3,684,529   차이 +270
```

오늘은 미결제가 0이라 구/신이 같다. Runtime Error 0.
차이 270원은 브로커 응답 내부 차이(Iteration 7-2A §4)로 이미 규명됐다.

신규 테스트 `tests/unit/test_settled_cash_equity.py` **12건 PASS**
— 2026-05-26(매도 미수령) / 2026-06-24(매수 미차감) 실측 시나리오 재현,
폴백 시 WARN 강제, 관측 로그 1일 1회, 배선 확인 포함.

## 4. Phase 4 — 왜 0% 인가

| 정의 | EC_HALT 29일 중 해제 |
|---|---|
| OLD (운영 기록) | 1일 (3%) |
| **TRUE (이번 수정)** | **0일 (0%)** |
| TWR (입출금 조정) | 22일 (76%) |

브로커 전체 시계열로 재산출한 진짜 peak 은 **8,344,826**(2026-05-08)이고,
**2026-05-13 이후 dd 가 −18% 위로 올라온 날이 하루도 없다.**

계산 결함(7-1·7-2)은 실재했다. 그러나 그 결함은 낙폭의 **크기**를 틀리게 했지
차단 **여부**를 틀리게 하지 않았다. False Trigger 는 없었다.

⚠️ Iteration 7-1 §4 의 "12일은 차단되지 않았을 것" 은 과대였다.
그 계산은 EC_HALT 기록일의 equity 표본만 써서 peak 이 하한이었다(당시 명시함).
전체 시계열로 정정한다.

## 5. Phase 2 — peak 상태파일은 쓰지 않았다

```
현재 data/equity_state.json  peak 4,467,501  →  dd -24.5%
TRUE 정의                    peak 8,344,826  →  dd -55.8%
```

**TRUE 로 쓰면 EC_HALT 가 지금보다 더 강하게 잠긴다.** 지시서 목적과 반대 방향이라
사용자 판단 없이 반영하지 않았다. 재산출 값과 근거는 `PEAK_REBUILD_REPORT.md` 에 있다.

## 6. 진짜 원인 — equity 는 매매 성과 곡선이 아니다

```
2026-05-06  이체입금      +7,039,648   → peak 8,344,826 의 84%
2026-05-13  원화→외화매수 -2,915,060   → 하루 만에 -39.4% 낙폭
```

kt00015(위탁종합거래내역)로 확인했다. 2026-05-13 의 실현손익은 −376,000 인데
총자산은 2,979,095 줄었다. **차액은 매매 손실이 아니라 환전이다.**
`day_stk_asst` 는 원화 예수금 + 국내주식 평가액이라 외화 자산을 포함하지 않는다.

**입금하면 peak 이 오르고, 환전하면 낙폭이 된다.**
EC_HALT 는 매매 성과를 재려 했는데 계좌 잔고를 재고 있다.

## 7. Iteration 7-4 제안 (승인 대기)

1. **EC_HALT 를 TWR 지수 기준으로 전환.**
   `r_t = (E_t − F_t)/E_{t−1} − 1`, `index_t = index_{t−1}(1+r_t)`
   외부 현금흐름 `F_t` 는 kt00015 에서 자동 수집 가능함을 이번에 확인했다.
2. TWR 확정 후 **peak 을 한 번에 재산출**하고 `equity_state.json` 을 교체.
3. 그래도 현재 dd 는 **−30.7%** 다. −18% 아래다.
   → **EC_HALT 는 계속 켜져 있는 게 맞다.** 시스템은 실제로 매매로 30% 잃었다.
   (Iteration 4: swing 5건 = 실거래 손실의 95.2%)

**즉 Iteration 8(전략 비교 백테스트)로 가는 것이 옳다.**
EC_HALT 는 고장난 브레이크가 아니라 정상 작동 중인 브레이크였다.

## 8. 출력

```
==============================
TRUE EQUITY DEPLOYMENT
==============================
Old Formula              pymn_alow_amt + 평가액
New Formula              d2_entra + 평가액  (폴백 시 WARN)
Peak Before              4,467,501  (equity_state.json, 미변경)
Peak After (TRUE)        8,344,826  (2026-05-08, 상태파일 미반영)
EC_HALT Before           29 영업일 / 28,527건
EC_HALT After            29 영업일 (해제 0일)
False Trigger Removed %  0%   (TWR 정의 적용 시 76%)
Regression               507 PASS / 16 Known FAIL
Runtime Error            0
==============================
```
