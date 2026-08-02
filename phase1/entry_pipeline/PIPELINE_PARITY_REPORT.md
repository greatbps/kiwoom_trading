# PIPELINE_PARITY_REPORT — 코드 · DB · 로그 대조

Iteration 6-3 · 2026-08-03

## 항목별 대조

| 항목 | 현재 운영 (코드) | DB | 로그 | 일치 |
|---|---|---|---|---|
| BUY 생성 함수 | `main_auto_trading.execute_buy()` L9351 | — | `[BUY_COMPLETE]` L11048 | ✅ |
| 호출 경로 | `check_entry_signal()` 6곳 + `_flush_pending_signals()` 1곳 | — | — | ✅ |
| **`condition_name`** | **상수 `'VWAP+AI'`** (L10614·L10848) | `VWAP+AI` 158건 | — | ✅ **상수 확인** |
| `entry_reason` | 7경로가 각각 다른 형식 생성 | `SQZ:` · `RS:` · `EXPLORATION:` · `SMC LONG` 등 | 일치 | ✅ |
| Position Size | `risk_manager.py:301` | `quantity` | — | ✅ |
| Trade INSERT | `db.insert_trade()` L10604/L10889 | `trades` | — | ✅ |
| swing 경로 | `swing_executor` → `insert_swing_buy()` | `condition_name='SWING'` 5건 | — | ✅ |

## `condition_name` 은 전략 라벨이 아니다 — 확증

```
main_auto_trading.py:10614   'condition_name': 'VWAP+AI',   ← 하드코딩 상수
main_auto_trading.py:10848   'condition_name': 'VWAP+AI',   ← 하드코딩 상수
```

**증거**: `trade_id=419`

| 필드 | 값 |
|---|---|
| `condition_name` | `VWAP+AI` |
| `entry_reason` | `EXPLORATION:12:10 돌파(6640→6690 +0.75%) RVOL=6.6x` |

**같은 행에서 `condition_name` 은 VWAP+AI 인데 실제 전략은
EXPLORATION 이다.** `main_auto_trading` 경로로 나가는 모든 매수에
이 상수가 붙는다.

⚠️ **앞선 Iteration 들의 "실거래의 95.8% 가 VWAP+AI" 는 이 상수를
센 것이다. 전략 비중이 아니다.**

## Phase 8 — 로그 · DB · 코드 4자 대조

최근 main 경로 BUY 8건 (TEST 제외).

| id | 종목 | 가격 × 수량 | 일시 | 로그 |
|---|---|---|---|---|
| 419 | 396470 | 6,690 × 21 | 2026-06-09 12:10 | ✅ `auto_trading_20260609.log` (수량·가격 일치) |
| 388 | 294630 | 6,030 × 1 | 2026-04-16 11:24 | ✅ `auto_trading_20260416.log` (수량·가격 일치) |
| 384 | 037030 | 8,640 × 1 | 2026-04-15 12:56 | ✅ `auto_trading_restart_20260415_115621.log` |
| 383 | 070300 | 2,915 × 5 | 2026-04-15 12:25 | ✅ `auto_trading_restart_20260415_115621.log` |
| 414 | 458650 | 13,820 × 5 | 2026-05-11 12:49 | ⚠️ `[BUY_COMPLETE]` 는 있으나 구형 `거래 기록` 형식 없음 |
| 378 | 396470 | 7,470 × 4 | 2026-04-14 12:28 | ❌ 전체 로그에 없음 |
| 377 | 082920 | 44,700 × 1 | 2026-04-14 11:03 | ❌ 전체 로그에 없음 |
| 376 | 160980 | 28,600 × 1 | 2026-04-14 10:43 | ❌ 전체 로그에 없음 |

**대조 결과: 5/8 확인 (62.5%). 3건은 로그에 없다.**

### 로그가 없는 이유 — 확인된 것

| 사실 | 근거 |
|---|---|
| `[BUY_COMPLETE]` 태그는 2026-05-11 도입 | 전체 로그에 3건뿐, 최초 등장 2026-05-11 |
| 구형 형식은 `✓ 거래 기록: {name} ({code}) BUY {qty}주 @ {px}원` | `auto_trading_20260416.log` 확인 |
| 로그 롤링 버그가 2026-07-07 에 수정됨 | 메모리 기록 |
| 재시작 로그가 별도 파일로 분리됨 | `auto_trading_restart_20260415_115621.log` |

**2026-04-14 3건이 어느 파일에도 없는 이유는 확인 불가.**
로그 보유 기간(2026-01-28 ~ 2026-08-03) 안에 있는데도 없다.

## 승인 기준 대비

| 항목 | 목표 | 결과 | 판정 |
|---|---|---|---|
| BUY 생성 함수 식별 | 100% | `execute_buy()` L9351 | ✅ |
| Call Graph | 100% | 호출 7곳 + 상위 3단 | ✅ |
| **Entry Rule 식별** | 100% | 경로 7개 식별 / **엔진 4종 내부 조건 미확인** | ❌ |
| Risk Filter 식별 | 100% | 37경로 · reason_code 8종 | ✅ |
| Position Size | 100% | `risk_manager.py:301` | ✅ |
| `entry_reason` 생성 위치 | 100% | 7곳 전부 | ✅ |
| `condition_name` 생성 위치 | 100% | L10614·L10848 상수 | ✅ |
| Trade INSERT 경로 | 100% | L10604/L10889 → `insert_trade()` | ✅ |
| **로그-DB-코드 일치** | 100% | **5/8 (62.5%)** | ❌ |
| 추정 사용 | 0건 | **0건** | ✅ |
| Runtime Error | 0 | 0 | ✅ |
| Regression | PASS | 495 PASS / 16 Known FAIL | ✅ |

## 판정: **FAIL**

두 항목이 100% 에 못 미친다.

1. **Entry Rule 식별 — 진입 경로 7개는 확정했으나 신호 엔진 4종의
   내부 조건(Indicator · 변수 · Threshold)을 확인하지 않았다.**
   백테스트를 만들려면 이것이 필요하다.
2. **로그-DB-코드 일치 62.5%** — 3건이 로그에 없고 이유를 확정하지
   못했다.

§Iteration 종료 후 판단 기준에 따라 **Baseline 백테스트로 진행하지
않는다.** 원인 분석 Iteration 이 필요하다.
