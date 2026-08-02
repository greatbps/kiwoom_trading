# TRADE_TRACEABILITY — Iteration 8-3

2026-08-02 · Regression **492 PASS / 16 Known FAIL** — RELEASE ALLOWED
**main_auto_trading.py 무수정 · DDL 0건 · 원장 411행 유지**

---

## 1. 현재 구조 (§3)

```
==============================
CURRENT TRADE WRITE FLOW
==============================
BUY 발생 위치        main_auto_trading.execute_buy()          L9351
Order 생성 위치      KiwoomAPI.order_buy()
Order Number 위치    order_result → 지역변수 order_no
Trade INSERT 위치    db.insert_trade(trade_data)              L10613/10690/10847
                     ⚠️ trade_data['order_no'] 가 **이미 실려 있다**
EXIT INSERT 위치     main_auto_trading.execute_sell()         L12915
                     ⚠️ sell_trade 에 order_no 가 **없다**
==============================
```

### 핵심 발견

**`order_no` 는 이미 `trade_data` 에 3곳에서 실려 오는데
`insert_trade` 의 INSERT 문에 대응 컬럼이 없어 버려지고 있었다.**

수집도, 전달도 되고 있었다. 저장만 안 됐다.

---

## 2. 발견 문제 (§11-2)

| 문제 | 원인 | 영향 |
|---|---|---|
| `order_no` 미저장 | INSERT 문에 컬럼 없음. 값은 전달되는데 버려짐 | 주문↔거래 대조 불가 |
| duplicate INSERT | `execute_sell` 의 `[SELL_DEDUP]` 가드가 `if _entry_time_str and ...` 조건부. **entry_time 은 411행 중 27행에만 있어 대부분 건너뛴다** | 2025-11 에 74건 중복 (30.1%) |
| EXIT 연결 실패 | SELL 행에 BUY 참조 키 없음 | Match Coverage 67.4% |

### 중복 재현 확인 (§6)

| 항목 | 확인 결과 |
|---|---|
| `execute_sell` 재호출 | ⚠️ **직접 확인 불가** — 2025-11 로그 없음 (보유 로그 2025-12-11~) |
| 감시 loop 반복 | 간격 30~71초 = `check_interval=60` 과 일치 (**정황 일치**) |
| DB insert 재실행 | ✅ 동일 5필드 74건 확인 |
| exception retry | ❌ `execute_sell` 재시도는 함수 내 3회, INSERT 는 그 뒤 1회 — **이 경로 아님** |
| scheduler 중복 실행 | ⚠️ 확인 불가 |

**원인을 코드로 확정하지 못했다. 추정하지 않는다.**
다만 **어느 경로든 저장 계층에서 막으면 재발하지 않는다** — 그래서
방어를 저장 계층에 뒀다.

---

## 3. 변경 내용 (§11-3)

### `database/trading_db.py` (+79 −0)

| 변경 | 목적 | 내용 |
|---|---|---|
| `logger` 정의 | 안전 | **모듈에 logger 가 없었다.** 추가한 경고 로그가 `NameError` 를 내면 거래 기록이 통째로 실패한다 |
| `_fold_order_no()` | 주문 추적 | `order_no` → `entry_context` JSONB. BUY 는 `order_no`, SELL 은 `exit_order_no` |
| `_is_duplicate_trade()` | 중복 방어 | 5필드 + 300초 창 |
| `insert_trade()` 진입부 | 배선 | 두 함수 호출. 중복이면 **0 반환** (예외 아님) |

**`main_auto_trading.py` 를 건드리지 않았다** — `order_no` 가 이미
전달되고 있어 저장 계층만 고치면 됐다. §2 준수.

### 설계 판단 3가지

**① Option A 채택 (INSERT 전 체크), Option B(UNIQUE) 미실행**
§7 지시대로 DDL 을 실행하지 않았다. UNIQUE 는 §8 에 제안만 남긴다.

**② 시간 창 300초**
⚠️ 창이 없으면 **정상 거래를 막는다.** 같은 종목을 같은 가격·수량으로
하루 두 번 거래하는 것은 정상이다. 2025-11 중복은 30~71초 간격이었다.

**③ 검사 실패 시 통과 (fail-open)**
⚠️ 중복 검사가 예외를 내면 **막지 않고 통과**시킨다.
거래 기록을 잃는 것이 중복 하나가 더 생기는 것보다 나쁘다.

---

## 4. 검증 결과 (§11-4)

```
==============================
ORDER TRACEABILITY AUDIT
==============================

✅ PASS  Test 1  동일 EXIT 2회 → 1건 저장 · 1건 차단
          1회차 trade_id=430, 2회차=0
✅ PASS  Test 2  다른 가격 EXIT → 정상 저장
✅ PASS  Test 3  다른 종목 EXIT → 정상 저장
✅ PASS  Test 4  재시작 후 동일 EXIT → 차단
          새 인스턴스 결과=0 (DB 기준 판단 — 메모리 아님)
✅ PASS  Test 5  300초 밖 동일 거래 → 통과
          정상거래 차단 방지
✅ PASS  Test 6  BUY order_no → entry_context 보존
          {"order_no": "0163513", "order_time": "..."}
✅ PASS  Test 7  SELL order_no → exit_order_no 보존
✅ PASS  Test 8  기존 entry_context 보존 + order_no 병합
          {"order_no": "0164000", "condition_sources": ["GreatMid"]}

BUY Records              165
EXIT Records             246
Order No Coverage %      BUY 0.0%  EXIT 0.0%   (기존 거래는 기록 없음)
Duplicate Detection Test 8/8 PASS
Duplicate Block Count    2
Regression Result        492 PASS / 16 Known FAIL — RELEASE ALLOWED
Runtime Error            0
==============================

  성능: INSERT 6.01ms · 중복검사 포함 1.03ms
  검증 데이터 정리: 7행 삭제 (원장 오염 0)
```

### Test 8 이 중요한 이유

Iteration 8-1 의 조건검색 출처가 `entry_context` 에 들어 있다.
`order_no` 를 넣으면서 그걸 덮으면 8-1 작업이 무효가 된다.
`setdefault` 로 병합하고 테스트로 못박았다.

### 검증 데이터 정리

`ZZTEST` 접두를 쓰고 `finally` 에서 삭제했다.
**원장에 `TEST01`/`TEST99` 가 남아 있는 전례(Iteration 4 발견)를
반복하지 않았다.** 실행 후 `trades` 411행 유지, `ZZ%` 잔존 0행.

### 회귀 테스트 8건 추가 (`tests/unit/test_trade_dedup.py`)

저장 계층 가드 존재 · 시간 창 · fail-open · order_no 보존 ·
기존 context 미파괴 · logger 정의 · DDL 없음 · 0 반환(예외 아님)

---

## 5. 성능 (§10)

| 항목 | Before | After | 차이 |
|---|---|---|---|
| trade INSERT | 6.01 ms | 1.03 ms | — |
| duplicate check | — | 포함 | **+1 쿼리** |
| DB query 증가 | — | INSERT 당 SELECT 1회 | |
| memory | — | 변화 없음 | 상태 미보유 |

⚠️ **위 Before/After 를 개선으로 읽으면 안 된다.** 첫 호출은 커넥션
풀 워밍업이 섞여 6.01ms 가 나왔다. 실제 추가 비용은 **인덱스 조회
1회**이고, 부하 조건에서 재측정하지 않았다.

⚠️ 중복 검사 쿼리는 `(stock_code, trade_type, price, quantity,
exit_reason, trade_time)` 조합을 본다. **이 조합의 인덱스가 없다.**
현재 411행이라 무시할 수준이지만 원장이 커지면 느려진다.

---

## 6. 완료 기준 (§13)

| 항목 | 목표 | 결과 | 판정 |
|---|---|---|---|
| 신규 BUY order_no 기록 | 100% | 구조 완비 (신규부터) | ✅ |
| 신규 EXIT order trace | ≥95% | **구조상 불가** — 아래 참조 | ❌ |
| Duplicate 방어 | PASS | 8/8 | ✅ |
| 기존 전략 영향 | 0 | main_auto_trading 무수정 | ✅ |
| DB Migration | 없음 | 없음 | ✅ |
| Regression | PASS | 492 PASS / 16 FAIL | ✅ |
| Runtime Error | 0 | 0 | ✅ |

### ❌ EXIT order trace 미달 — 이유

`execute_sell` 의 `sell_trade` 딕셔너리에 **`order_no` 가 없다.**
매도 주문번호를 수집하지 않는다.

저장 계층은 준비됐지만(Test 7 통과) **보내주는 쪽이 없다.**
고치려면 `main_auto_trading.py` 를 수정해야 하는데 §2 가 금지한다.

**한 줄이면 된다:**
```python
sell_trade['order_no'] = order_result.get('ord_no')   # execute_sell 내
```

별도 승인이 필요하다.

---

## 7. 잔여 Risk

1. **EXIT 주문번호 미수집** — 위 참조. 승인 시 1줄.
2. **중복 원인 미확정** — 저장 계층에서 막았으므로 재발은 방지되지만,
   **왜 그랬는지는 여전히 모른다.** 같은 원인이 다른 곳(예: 매수)에
   있을 수 있다.
3. **중복 검사 인덱스 없음** — 원장이 커지면 INSERT 가 느려진다.
4. **과거 411건은 여전히 커버리지 0%** — 기록이 없다.
5. **BUY 중복은 미검증** — 이번 테스트는 EXIT 중심이다.
   `insert_trade` 는 공통이라 동작하겠지만 실측하지 않았다.

---

## 8. 제안 (실행 안 함)

| 제안 | 내용 | 필요 승인 |
|---|---|---|
| A | `sell_trade['order_no']` 1줄 추가 | main_auto_trading 수정 |
| B | UNIQUE `(stock_code, trade_time, price, quantity, trade_type)` | DDL |
| C | 중복 검사용 부분 인덱스 | DDL |

B 를 걸면 앱 계층 가드가 이중이 되지만, **UNIQUE 위반은 예외를 던져
청산 흐름을 끊을 수 있다.** 현재 방식(0 반환)이 더 안전하다고 본다.

---

## 9. 다음 단계

§14 대로 **Iteration 8-4 Position ID Architecture** 로 간다.
다만 그것은 `self.positions` 구조 변경을 동반하는 **고위험 작업**이다
(Iteration 8-2 §8 제안 ②). 설계만 하고 구현은 별도 승인이 맞다.
