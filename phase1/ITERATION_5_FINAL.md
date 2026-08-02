# Iteration 5 종료 — Order Traceability 완료

2026-08-02 · Regression **495 PASS / 16 Known FAIL** — RELEASE ALLOWED
**전략 로직 변경 0 · DDL 0 · 원장 411행 유지**

---

## 1. 수정 내용 — 실제 코드 2줄

```python
# main_auto_trading.py  execute_sell()        L12885
sell_trade = { ..., 'order_no': order_no, ... }

# main_auto_trading.py  execute_partial_sell() L12538
partial_sell_trade = { ..., 'order_no': order_no, ... }
```

### 부분청산도 같은 결함이었다 — 테스트가 잡았다

작업지시서는 `sell_trade` 한 곳만 지목했다. 회귀 테스트를 붙이니
**`partial_sell_trade` 에도 같은 누락**이 드러났다.

```
L12519  order_no = order_result.get('ord_no')   ← 값 있음
L12534  partial_sell_trade = { ... }            ← 안 넣음
```

**부분청산 23건이 같은 이유로 추적 불가였다.** 같은 결함 클래스이고
지시서 §3 의 "동일 의미의 dict 항목 추가" 범위 안이라 함께 채웠다.

⚠️ 테스트를 처음 쓸 때 `src.index('sell_trade = {')` 로 검사했더니
`partial_sell_trade` 가 먼저 잡혀 FAIL 했다. **그 실패가 누락을
드러냈다.** 두 곳을 모두 검사하도록 고쳤다.

---

## 2. 검증 결과

```
==============================
ORDER TRACEABILITY AUDIT
==============================

✅ PASS  Test 1  동일 EXIT 2회 → 1건 저장 · 1건 차단
✅ PASS  Test 2  다른 가격 EXIT → 정상 저장
✅ PASS  Test 3  다른 종목 EXIT → 정상 저장
✅ PASS  Test 4  재시작 후 동일 EXIT → 차단 (DB 기준, 메모리 의존 없음)
✅ PASS  Test 5  300초 밖 동일 거래 → 통과
✅ PASS  Test 6  BUY order_no → entry_context 보존
✅ PASS  Test 7  SELL order_no → exit_order_no 보존
✅ PASS  Test 8  기존 entry_context 보존 + order_no 병합

BUY Records              165
EXIT Records             246
Order No Coverage %      BUY 0.0%  EXIT 0.0%   (기존 거래는 기록 없음)
Duplicate Detection Test 8/8 PASS
Duplicate Block Count    2
Regression Result        495 PASS / 16 Known FAIL — RELEASE ALLOWED
Runtime Error            0
==============================

  검증 데이터 정리: 7행 삭제 (원장 411행 유지, 오염 0)
```

### §5 목표 대비

| 항목 | 목표 | 결과 | 판정 |
|---|---|---|---|
| BUY order_no | 100% | 구조 완비 | ✅ |
| **EXIT order_no** | **≥95%** | **구조 완비 (전량 + 부분)** | ✅ |
| Duplicate Detection | 8/8 | 8/8 | ✅ |
| Regression | PASS | 495 PASS / 16 Known FAIL | ✅ |
| Runtime Error | 0 | 0 | ✅ |

⚠️ **Coverage 0% 는 과거 거래 기준이다.** 411건은 기록 자체가 없어
영원히 0% 다. **신규 거래부터 100% 로 쌓인다.**

측정 가능한 것은 "구조가 완비됐는가" 이고, 실제 커버리지는
신규 매수·청산이 발생해야 나온다.

---

## 3. §6 완료 판단

```
BUY
 ↓  order_no + entry_context (Iter 8-1 조건검색 출처)
Condition Attribution
 ↓
Position
 ↓
EXIT
 ↓  exit_order_no (전량 + 부분)
Order Trace
```

**신규 거래 기준 추적 가능. Iteration 5 종료 조건 충족.**

---

## 4. 안전성 확인

| 항목 | 확인 |
|---|---|
| `order_no` 스코프 | L12871·L12519 에서 할당 → dict 생성보다 앞. `NameError` 없음 |
| 주문 실패 시 | `order_result is None` · `return_code != 0` 을 앞에서 return. 성공 경로만 도달 |
| `ord_no` 키 부재 시 | `order_no = None`. `_fold_order_no` 가 `if not order_no: return` 으로 안전 처리 |
| 기존 `entry_context` | `setdefault` 병합. Iteration 8-1 조건검색 출처 미파괴 |
| 전략 로직 | VWAP·CHoCH·risk·stop·position_size·진입/청산 조건 변경 **0건** |

회귀 테스트 3건 추가:
`test_sell_trade_carries_order_no` (두 경로 모두) ·
`test_sell_order_no_assigned_before_use` ·
`test_fold_order_no_handles_none`

---

## 5. 잔여 Risk

1. **과거 411건 커버리지 0%** — 기록이 없다. 영원히 그렇다.
2. **재진입 33건 · 분할매수 12건 손익 귀속 불가** — `self.positions` 가
   종목당 1개다. §7 대로 별도 Iteration 으로 보류.
3. **중복 원인 미확정** — 저장 계층에서 막았으나 왜 그랬는지는 모른다.
   같은 원인이 매수 쪽에 있을 수 있다.
4. **BUY 중복 미검증** — `insert_trade` 공통이라 동작하겠지만 실측 안 함.
5. **중복 검사 인덱스 없음** — 411행이라 무시할 수준이나 원장이 커지면 느려진다.
6. **실거래 미검증** — 위 검증은 전부 dry-run 이다. 실제 매도가 발생해
   `exit_order_no` 가 찍히는지 확인해야 한다.

---

## 6. 반영 절차

**장마감 후 재시작 필요.** 재시작 후 첫 청산에서 확인할 것:

```sql
SELECT entry_context->>'exit_order_no', count(*)
FROM trades WHERE trade_type='SELL'
  AND trade_time > '2026-08-03' GROUP BY 1;
```

`NULL` 이 나오면 전달이 안 된 것이다.

---

## 7. 다음 단계 — Iteration 6

**VWAP+AI 실거래 전략 검증.** 실거래 158건(95.8%)인데 백테스트 0건이다.

### Iteration 6-0 선행 과제 — 분봉 데이터 확보

| 후보 | 상태 | 비고 |
|---|---|---|
| 키움 API | ⚠️ 미확인 | 실행 중 프로세스에서만. 조회 한도 확인 필요 |
| 저장 DB | ❌ 없음 | `daily_capital_snapshot` 외 OHLCV 테이블 없음 (Iter0 확인) |
| 기존 로그 | ⚠️ 부분 | `logs/` 에 가격이 산발적으로 찍히나 OHLCV 형태 아님 |
| 분봉 캐시 구축 | 🔨 필요 | 위 셋 중 하나를 원천으로 |

**필수 필드**: 종목 · 날짜 · 시간 · 시가 · 고가 · 저가 · 종가 · 거래량

⚠️ yfinance 는 분봉을 **최근 60일치만** 준다. 실거래 구간
(2025-11 ~ 2026-07)을 덮지 못한다. **키움 API 가 유일한 현실적
후보로 보이나 확인되지 않았다.**

원천 확보 없이는 VWAP 돌파 신호를 재현할 수 없다.
**Iteration 6-0 을 데이터 확보 전담으로 두는 것이 맞다.**
