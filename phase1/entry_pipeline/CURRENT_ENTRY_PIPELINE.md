# CURRENT_ENTRY_PIPELINE

Iteration 6-3 · 2026-08-03 · **읽기 전용 감사 · 코드 변경 0건**
Regression 495 PASS / 16 Known FAIL — RELEASE ALLOWED

---

## 결론: **FAIL** — 백테스트로 진행하지 않는다

11개 완료 기준 중 **9개 충족 · 2개 미달**.

미달 항목:
1. **Entry Rule 식별** — 진입 경로 7개는 확정했으나 신호 엔진 4종의
   내부 조건 미확인
2. **로그-DB-코드 일치 62.5%** (5/8)

---

## 1. 가장 중요한 확인 — `condition_name` 은 상수다

```
main_auto_trading.py:10614   'condition_name': 'VWAP+AI',
main_auto_trading.py:10848   'condition_name': 'VWAP+AI',
```

**증거** `trade_id=419` — `condition_name='VWAP+AI'` 인데
`entry_reason='EXPLORATION:12:10 돌파(6640→6690 +0.75%) RVOL=6.6x'`.

**`main_auto_trading` 경로로 나가는 모든 매수에 이 상수가 붙는다.**
조건검색식 이름도, 전략 이름도 아니다.

### 파급

Iteration 3 이후 "실거래의 95.8% 가 VWAP+AI" 를 전제로 작업해 왔다.
그 95.8% 는 **이 상수를 센 값**이다.

실제 전략 분포는 `entry_reason` 기준이어야 한다 (Iteration 6-2):

| 형식 | 건 | 기간 |
|---|---|---|
| VWAP 상향 돌파 (order_executor, 미호출 모듈) | 96 | 2025-11-28 ~ 2026-01-16 |
| SMC LONG | 39 | 2026-01-22 ~ 2026-02-25 |
| EXPLORATION | 10 | 2026-04-08 ~ 2026-06-09 |
| OTHER | 9 | 2026-01-20 ~ 2026-04-30 |
| SWING | 5 | 2026-05-12 ~ 2026-07-01 |
| EXPERIMENT | 5 | 2026-03-25 ~ 2026-03-26 |

---

## 2. BUY 생성 위치

| 항목 | 값 |
|---|---|
| 함수 | `main_auto_trading.execute_buy()` |
| 위치 | L9351 |
| 호출 | **7곳** — `check_entry_signal()` 6 + `_flush_pending_signals()` 1 |

별도 경로: `swing_executor.py` (독립 프로세스, `insert_swing_buy()`)

---

## 3. Call Graph

`ENTRY_CALL_GRAPH.md` 참조. 중간 함수 생략 없음.

```
monitor_and_trade() → check_all_stocks() → check_entry_signal()
   → [엔진 4종] → execute_buy() → risk_manager → order_buy()
   → insert_trade() → trading_db(_fold_order_no + _is_duplicate_trade)
   → PostgreSQL
```

---

## 4. Entry Rule — ❌ 부분

진입 경로 7개와 신호 엔진 4종을 식별했으나
**엔진 내부의 Indicator · 변수 · Threshold 는 확인하지 않았다.**

| 엔진 | 파일 |
|---|---|
| Squeeze + 호가창 | `analyzers/squeeze_with_orderbook.py` |
| MA Cross | `analyzers/ma_cross_strategy.py` |
| Two-Timeframe Squeeze | `analyzers/squeeze_momentum_lazybear.py` |
| SMC | `analyzers/smc/smc_signals.py` |

**확인 불가 — 엔진별 개별 추적 필요.**

---

## 5. Risk Filter — ✅

`execute_buy()` 내 조기 return 37경로, reason_code 8종.

---

## 6. Position Size — ✅

`core/risk_manager.py:301`
```python
risk_amount         = current_balance * self.RISK_PER_TRADE
risk_per_share      = abs(current_price - stop_loss_price)
risk_based_quantity = int(risk_amount / risk_per_share)
```

⚠️ `stop_loss_price` 산출에 ATR 이 쓰이는지는 **확인 불가**
(이번 범위에서 추적하지 않았다).

---

## 7. Trade Insert — ✅

```
execute_buy()  L10604 / L10889
   → trade_data { stock_code, stock_name, trade_type, trade_time,
                  price, quantity, amount, order_no,
                  condition_name='VWAP+AI'(상수), strategy_config='hybrid',
                  entry_reason, entry_context(조건검색출처+order_no), … }
   → db.insert_trade()
   → _fold_order_no()      order_no → entry_context
   → _is_duplicate_trade()  5필드 + 300초
   → PostgreSQL trades
```

---

## 8. 로그 대조 — ❌ 62.5%

`PIPELINE_PARITY_REPORT.md` 참조. 8건 중 5건 확인.

3건(2026-04-14)이 로그 보유 기간 안인데도 어느 파일에도 없다.
**이유 확인 불가.**

---

## 9. 다음 — 원인 분석 Iteration 필요

§판단 기준대로 FAIL 이므로 백테스트로 넘어가지 않는다.

### 권고 순서

1. **어느 전략을 백테스트할지 먼저 정한다.**
   "VWAP+AI 95.8%" 가 상수를 센 값이었으므로, 대상 선정을 다시 해야
   한다. `entry_reason` 기준으로는 SMC LONG 39건이 가장 많고,
   최근 6개월은 EXPLORATION 10건 · SWING 5건이다.
2. **선정된 전략의 엔진 내부 조건을 추적한다.**
3. 그 다음 백테스트.

⚠️ **어느 전략도 단독 표본이 100건을 넘지 않는다.**
가장 많은 SMC LONG 도 39건이고 2026-02-25 에 끊겼다.
백테스트 대상을 정하기 전에 **"지금 무엇이 돌고 있는가" 를
운영 로그로 확인하는 것이 먼저**일 수 있다.
