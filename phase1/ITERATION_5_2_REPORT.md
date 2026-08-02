# Iteration 5-2 — Order Traceability 완성 및 거래 귀속성 확보

2026-08-02 · Regression **492 PASS / 16 Known FAIL** — RELEASE ALLOWED
**main_auto_trading.py 무수정 · DDL 0건 · 원장 411행 유지**

---

## 결론 먼저 — 목표 하나가 제약과 충돌한다

| §3 목표 | 결과 | 판정 |
|---|---|---|
| 신규 BUY order_no 저장 100% | 구조 완비 | ✅ |
| **신규 EXIT order_no 저장 ≥95%** | **구조상 불가** | ❌ |
| 중복 EXIT 방어 100% | 8/8 PASS | ✅ |
| 기존 전략 영향 0 | 무수정 | ✅ |
| DB Migration 없음 | 없음 | ✅ |
| Regression PASS | 492/16 | ✅ |

**EXIT order_no 는 §5 를 지키는 한 달성할 수 없다.**

```
main_auto_trading.py:12871   order_no = order_result.get('ord_no')   ← 값은 있다
main_auto_trading.py:12880   sell_trade = { ... }                    ← 안 넣는다
                             9줄 차이, 같은 스코프
```

저장 계층은 준비됐다(Test 7 통과). **보내주는 쪽이 없다.**
고치려면 `sell_trade` 에 한 줄을 넣어야 하는데 §5 가
`main_auto_trading.py` 수정을 금지한다.

```python
'order_no': order_no,   # sell_trade 딕셔너리에 추가 — 이게 전부다
```

**임의로 넣지 않았다. 승인이 필요하다.**

---

## 1. Baseline 대비

| 항목 | §2 제시 | 현재 |
|---|---|---|
| BUY Records | 165 | 165 |
| EXIT Records | 246 | 246 |
| 실질 EXIT | 172 | 172 |
| Matched Position | 116 | 116 |
| Match Coverage | 67.4% | 67.4% |

**과거 데이터는 변하지 않았다.** 이번 작업은 신규 거래에만 효과가 있다.

---

## 2. 작업 범위 (§4)

### 4.1 저장 계층 — `database/trading_db.py` (+79)

| 함수 | 역할 |
|---|---|
| `_fold_order_no()` | `order_no` → `entry_context` JSONB. BUY 는 `order_no`, SELL 은 `exit_order_no` |
| `_is_duplicate_trade()` | 5필드 + 300초 창 |
| `insert_trade()` 진입부 | 두 함수 호출. 중복이면 **0 반환** |
| `logger` 정의 | **모듈에 없었다.** 경고 로그가 `NameError` 를 내면 거래 기록이 통째로 실패한다 |

**신규 컬럼 0건. 기존 `entry_context` JSONB 활용.**
`setdefault` 로 병합해 Iteration 8-1 의 조건검색 출처를 덮지 않는다.

### 4.2 중복 방어 — 시간 창 300초

§4.2 가 지정한 5필드 + 300초. **시간 제한 없는 UNIQUE 를 쓰지 않았다.**

⚠️ 창이 없으면 **정상 거래를 막는다.** 같은 종목을 같은 가격·수량으로
하루 두 번 거래하는 것은 정상이다. 2025-11 중복은 30~71초 간격이었다.

### 4.3 fail-open

중복 검사가 예외를 내면 **막지 않고 통과**시킨다.
거래 기록을 잃는 것이 중복 하나가 더 생기는 것보다 나쁘다.

---

## 3. 테스트 결과 (§6·§7)

```
==============================
ORDER TRACEABILITY AUDIT
==============================

✅ PASS  Test 1  동일 EXIT 2회 → 1건 저장 · 1건 차단
          1회차 trade_id=437, 2회차=0
✅ PASS  Test 2  다른 가격 EXIT → 정상 저장
          trade_id=438
✅ PASS  Test 3  다른 종목 EXIT → 정상 저장
          trade_id=439
✅ PASS  Test 4  재시작 후 동일 EXIT → 차단
          새 인스턴스 결과=0 (DB 기준 판단 — 메모리 의존 없음)
✅ PASS  Test 5  300초 밖 동일 거래 → 통과
          trade_id=440 (정상거래 차단 방지)
✅ PASS  Test 6  BUY order_no → entry_context 보존
          {"order_no": "0163513", "order_time": "..."}
✅ PASS  Test 7  SELL order_no → exit_order_no 보존
          {"exit_order_no": "0163999", "order_time": "..."}
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

  검증 데이터 정리: 7행 삭제 (원장 411행 유지, 오염 0)
```

### Test 7 이 통과하는데 커버리지가 0%인 이유

Test 7 은 **저장 계층에 `order_no` 를 직접 넣어** 검증한다.
실제 `execute_sell` 은 그 값을 보내지 않는다.
**그릇은 준비됐고 담을 사람이 없다.**

### 검증 데이터 격리

`ZZTEST` 접두 + `finally` 삭제. 실행 후 `trades` 411행 유지, `ZZ%` 0행.
원장에 `TEST01`/`TEST99` 가 남아 있는 전례(Iteration 4 발견)를
반복하지 않았다.

---

## 4. Regression 검증 (§8)

```
492 PASS / 16 FAIL — RELEASE ALLOWED
```

**16건 전부 기존 Known FAIL 이다. 신규 FAIL 0건.**

```
test_swing_holding_manager.py     8건
test_swing_runner_logic.py        8건
```

메모리 기록대로 테스트가 낡은 것이고 운영 영향은 없다.

이번 Iteration 에서 회귀 테스트 8건을 추가해 484 → 492 로 늘었다.

---

## 5. 성능

| 항목 | 값 |
|---|---|
| 중복 검사 | INSERT 당 SELECT 1회 추가 |
| memory | 변화 없음 (상태 미보유) |
| DB query | +1 / INSERT |

⚠️ **INSERT 7.41ms → 1.20ms 를 개선으로 읽으면 안 된다.**
첫 호출에 커넥션 풀 워밍업이 섞였다. 실제 추가 비용은 인덱스 조회
1회이고, **부하 조건에서 재측정하지 않았다.**

⚠️ 중복 검사 조합
`(stock_code, trade_type, price, quantity, exit_reason, trade_time)`
의 **인덱스가 없다.** 411행이라 무시할 수준이지만 원장이 커지면 느려진다.

---

## 6. §9 재평가 — PASS 인가 FAIL 인가

```
BUY  →  POSITION  →  EXIT   추적 가능한가
```

| 구간 | 상태 |
|---|---|
| BUY → 기록 | ✅ `order_no` + 조건검색 출처 저장 |
| POSITION | ⚠️ `self.positions` 가 종목당 1개 — 재진입 33건·분할매수 12건 분리 불가 (§5 가 구조 변경 금지) |
| EXIT → 기록 | ❌ `order_no` 미전달 |
| 중복 방어 | ✅ |

**부분 PASS 다.**

§9 는 PASS 면 VWAP+AI 백테스트로, FAIL 이면 position_id 검토로
가라고 한다. 현재는 그 중간이다.

**다만 EXIT order_no 는 1줄이고, position_id 는 고위험 구조 변경이다.**
난이도가 전혀 다르므로 같은 FAIL 로 묶으면 안 된다.

---

## 7. 승인 요청 — 3가지

| # | 내용 | 범위 | 위험 |
|---|---|---|---|
| **A** | `sell_trade['order_no'] = order_no` 1줄 | `main_auto_trading.py` L12880 부근 | **낮음** — dict 키 추가, 기존 값 미변경 |
| B | 중복 검사용 부분 인덱스 | DDL | 낮음 |
| C | `position_id` + `self.positions` 구조 변경 | 다수 파일 | **높음** — 별도 Iteration |

**A 를 승인하면 §3 의 EXIT ≥95% 가 즉시 충족된다.**
A 없이는 이번 Iteration 의 목표 하나가 영구 미달로 남는다.

---

## 8. 잔여 Risk

1. **EXIT 주문번호 미수집** — 위 A.
2. **중복 원인 미확정** — 저장 계층에서 막았으므로 재발은 방지되지만
   **왜 그랬는지는 여전히 모른다.** 같은 원인이 매수 쪽에 있을 수 있다.
3. **BUY 중복 미검증** — `insert_trade` 공통이라 동작하겠지만 실측 안 함.
4. **중복 검사 인덱스 없음.**
5. **과거 411건 커버리지 0%** — 기록이 없다. 영원히 그렇다.
6. **재진입·분할매수 45건 손익 귀속 불가** — 조건식별 PF 를 내면
   이 45건이 어느 진입에 붙는지 알 수 없다.

---

## 9. 다음 단계 (§10)

작업지시서는 **Iteration 6 VWAP+AI 백테스트**로 가라고 한다.
방향에 동의한다 — 실거래 95.8% 를 차지하는 전략이 아직 한 번도
검증된 적이 없다.

다만 선행 과제가 하나 있다.

**분봉 OHLCV 데이터 확보 경로가 없다.** VWAP 상향 돌파는 장중
신호이고, 현재 백테스트 인프라는 일봉이다. Phase 0~1 의 일봉 자산
대부분이 재사용 불가다.

- `pykrx` 이 환경에서 무응답 (Iteration 7-2 확인)
- yfinance 는 분봉을 최근 60일치만 준다
- 키움 API 는 실행 중 프로세스에서만

**데이터 원천 확보가 Iteration 6 의 첫 관문이다.**
