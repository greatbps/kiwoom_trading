# Kiwoom Trading — DB ERD & 데이터 품질 감사 보고서
**작성일**: 2026-05-09  
**DB**: PostgreSQL `trading_system`  
**검사 대상**: 46개 테이블 전수

---

## 1. 테이블 전체 현황 (행 수 기준)

| 분류 | 테이블 | 행 수 | 마지막 기록 | 상태 |
|------|--------|------:|------------|------|
| **핵심 거래** | `trades` | 396 | 2026-05-08 | ✅ 활성 |
| | `trade_signals` | 21 | 2026-05-08 | ⚠️ 일부 |
| | `ml_dataset` | 107 | 2026-04-06 | ⚠️ 일부 |
| | `ml_decisions` | 3 | 2026-04-14 | ⚠️ 미사용 |
| **스윙 전용** | `swing_features` | 0 | — | 🆕 P0 신설 |
| **피드백 루프** | `buy_failures` | 0 | — | ❌ 미수집 |
| | `signal_rejections` | 0 | — | ❌ 미수집 |
| **계좌 현황** | `account_snapshot` | 36 | 2026-05-08 | ✅ 활성 |
| | `daily_capital_snapshot` | 14 | 2026-05-06 | ⚠️ 소액계좌 |
| **모니터링** | `log_trade_events` | 11,522 | 불명 | ✅ 활성 |
| | `monitoring_stocks` | 42 | — | ✅ 활성 |
| **구시스템 잔존** | `filter_history` | 2,079 | 2025-11-02 | 🔴 구버전 |
| | `validation_scores` | 8,259 | 2025-11-02 | 🔴 구버전 |
| | `strategy_execution_results` | 22 | 2025-10-21 | 🔴 구버전 |
| | `filtered_candidates` | 13 | 2025-12월 | 🔴 구버전 |
| **비활성 (0건)** | `trade_executions`, `trade_history`, `trade_reconciliation`, `trade_review`, `backtest_results`, `analysis_results`, `signal_performance`, `filter_feature_snapshot`, `blocked_trades`, `system_logs` | 0 | — | 🔴 미사용 |

---

## 2. 핵심 테이블 스키마 및 ERD

### 2.1 테이블 관계도

```
trades (396)
  ├─ entry_signal_id ──→ trade_signals (21)  [FK: 0/396 채워짐 ❌]
  ├─ exit_signal_id  ──→ trade_signals (21)  [FK: 6/396 채워짐 ⚠️]
  │
  ├──[참조됨] ml_dataset.trade_id  (107)     [107/107 연결 ✅]
  └──[참조됨] ml_decisions.trade_id (3)      [3/3 연결 ✅]

swing_features (0)
  └─ trade_id ──→ trades                     [P0 신설, 아직 데이터 없음]

trade_signals (21)
  └─ signal_id ──→ ml_dataset.signal_id      [8/107 연결 ⚠️]

account_snapshot (36)
  └─ (독립, FK 없음)

daily_capital_snapshot (14)
  └─ (독립, FK 없음)
```

### 2.2 trades 테이블 (45 컬럼)

| 컬럼그룹 | 주요 컬럼 | NULL 비율 | 비고 |
|---------|----------|----------:|------|
| **식별** | trade_id, stock_code, trade_type, trade_time | 0% | ✅ 정상 |
| **거래** | price, quantity, amount | 0% | ✅ 정상 |
| **전략** | strategy_name | 6% | ⚠️ 최근 22건 NULL |
| **청산** | exit_reason, profit_rate | 38% | → BUY 레코드 (정상) |
| **연결** | entry_signal_id | **100%** | ❌ 전혀 채워지지 않음 |
| | exit_signal_id | 98% | ⚠️ 6건만 채워짐 |
| **스윙 전용** (P0 신설) | swing_pattern, swing_score | **100%** | → P0 이후 신규 BUY부터 수집 |
| | mfe_pct, mae_pct | **100%** | → P0 이후 신규 BUY부터 수집 |
| | peak_price, trough_price | **100%** | → P0 이후 swing_runner 실행시 갱신 |
| | market_regime | **100%** | → P0 이후 수집 |
| | stop_price, target_price | **100%** | → P0 이후 수집 |

**BUY 분류 (158건)**:
| strategy_name | 건수 | 기간 |
|--------------|-----:|------|
| UNKNOWN | 139 | 2025-11-24 ~ 2026-02-25 |
| EXPLORATION | 8 | 2026-03-25 ~ 2026-04-08 |
| TREND | 2 | 2026-04-01 ~ 2026-04-06 |
| NULL | 9 | 2026-04-14 ~ 2026-04-30 |

**현재 strategy_name=NULL인 9건**: 2026-04-14 이후 SMC 진입 — `decision_trace`의 signal_id 업데이트 실패로 strategy_name이 null 유지됨.

### 2.3 ml_dataset 테이블 (27 컬럼)

| 컬럼 | NULL 비율 | 비고 |
|------|----------:|------|
| trade_id | 0% | ✅ 전체 연결 |
| label_pnl_pct | 0% | ✅ 수익률 라벨 |
| mae_pct | **100%** | ❌ _mfe if _mfe else None 버그 |
| mfe_pct | 96% | ⚠️ 4건만 채워짐 |
| features (JSONB) | 일부 | 9개 피처 키 (rsi, ema9, vwap, ema60, gap_pct, atr_ratio, vol_ratio, market_regime, market_context) |

**source_type 분포**:
- `trade`: 8건 (실제 거래 → 청산 시 결과 라벨링)
- `backfill`: 99건 (`analysis/ml_backfill.py`로 소급 생성)

### 2.4 trade_signals 테이블 (19 컬럼)

| signal_type | 건수 | 비고 |
|-------------|-----:|------|
| entry | 1 | 2026-04-09 1건만 — `record_entry_signal()` 거의 실패 |
| exit | 20 | 2026-04-14 이후 |

**entry_signal이 1건뿐인 원인**: `execute_buy()` 내 `record_entry_signal()` 호출부가 try/except pass로 감싸져 있어 DB 에러 시 무음 실패.

### 2.5 swing_features 테이블 (18 컬럼, P0 신설)

```sql
swing_features
  id            BIGSERIAL PK
  trade_id      → trades.trade_id (ON DELETE SET NULL)
  stock_code    VARCHAR(12)
  entry_date    DATE
  pattern       VARCHAR(50)       -- cup_handle / pullback / box_breakout
  raw_score     FLOAT
  final_score   FLOAT
  phase         VARCHAR(20)
  trigger       BOOLEAN
  confidence    FLOAT
  entry_price   NUMERIC(15,2)
  stop_price    NUMERIC(15,2)
  target_price  NUMERIC(15,2)
  rr_ratio      FLOAT             -- (target-entry)/(entry-stop)
  size          FLOAT             -- allocated_size (0.5/0.7/1.0)
  market_regime VARCHAR(20)
  meta          JSONB
  created_at    TIMESTAMP
```

현재 0건. P0 완료 후 다음 ENTRY 시 자동 수집 시작.

---

## 3. 데이터 저장 시점 및 저장 소스 맵

### 3.1 실시간 저장 (장중 main_auto_trading.py)

| 이벤트 | 저장 테이블 | 저장 함수 | 저장 시점 |
|--------|-----------|----------|----------|
| 매수 완료 | `trades` (BUY) | `db.insert_trade()` | 키움 주문 직후 (P0 fix) |
| 매수 완료 | `trade_signals` (entry) | `decision_trace.record_entry_signal()` | execute_buy 내 — **실패율 높음** |
| 포지션 가격 갱신 | (메모리만) | `position['mfe_pct']` 갱신 | 5분봉 루프마다 |
| 매도 완료 | `trades` (SELL) | `db.insert_trade()` | 키움 매도 직후 |
| 매도 완료 | `trade_signals` (exit) | `decision_trace.record_exit_signal()` | execute_sell 내 |
| 매도 완료 | `ml_dataset` | `decision_trace._insert_ml_dataset()` | record_exit_signal 내 |
| 계좌 스냅샷 | `account_snapshot` | `db.insert_account_snapshot()` | 매도 직후 |
| 일일 자본 | `daily_capital_snapshot` | main_auto_trading.py | 15:35 장 마감 후 |
| 로그 이벤트 | `log_trade_events` | `analysis/log_backfill.py` | **실시간 아님 — 소급 수집** |

### 3.2 스윙 저장 (P0 이후, swing_executor.py + swing_runner.py)

| 이벤트 | 저장 테이블 | 저장 함수 | 저장 시점 |
|--------|-----------|----------|----------|
| 스윙 매수 | `trades` (BUY, strategy_name=swing) | `db.insert_swing_buy()` | 키움 주문 성공 직후 |
| 스윙 매수 | `swing_features` | `db.insert_swing_features()` | 동일 시점 |
| 스윙 매도/부분청산 | `trades` (SELL, strategy_name=swing) | `db.insert_swing_sell()` | 키움 매도 성공 직후 |
| MAE/MFE 일별 갱신 | `trades` (BUY 레코드 UPDATE) | `db.update_swing_mfe_mae()` | 매일 15:35 swing_runner.py 실행 시 |
| 상태 파일 | `data/swing_positions.json` | `SwingStateManager.save()` | swing_runner + swing_executor 실행마다 |

### 3.3 소급/오프라인 저장

| 도구 | 저장 테이블 | 실행 시점 |
|------|-----------|----------|
| `analysis/ml_backfill.py` | `ml_dataset` | 수동 실행 |
| `analysis/log_backfill.py` | `log_trade_events` | 수동 실행 |
| `analysis/missed_alpha.py` | `buy_failures.future_return_*`, `signal_rejections.future_return_*` | 장 마감 후 실행 |
| `scripts/migrate_sqlite_to_postgres.py` | `trades`, `filter_history` 등 | 구 SQLite 마이그레이션 (1회) |

---

## 4. 데이터 품질 이슈 목록

### 🔴 Critical (분석 불가 수준)

| # | 이슈 | 현황 | 원인 | 영향 |
|---|------|------|------|------|
| C1 | `trades.mfe_pct/mae_pct` 전체 NULL | 396/396 (100%) | P0 이전엔 저장 안 함. `_mfe if _mfe else None` falsy 버그도 존재 | Stop Efficiency 분석 불가 |
| C2 | `trades.entry_signal_id` 전체 NULL | 396/396 (100%) | `record_entry_signal()` try/except pass 무음 실패 | 진입 피처와 결과 연결 불가 |
| C3 | `buy_failures` 완전 비어있음 | 0건 | SMC 흐름에서 RVOL/EMA9/VWAP 블록 거의 미발생 | Missed Alpha 분석 불가 |
| C4 | `signal_rejections` 완전 비어있음 | 0건 | GLOBAL_GATE 차단이 거의 없거나 TIME_PHYSICAL 제외 로직 | 필터 오버필터링 분석 불가 |
| C5 | `swing_features` 0건 | 0건 | P0 완료됐으나 아직 신규 스윙 BUY 없음 | 현재 스윙 포지션 2개 trade_id=None |

### 🟡 Warning (부분 불일치)

| # | 이슈 | 현황 | 원인 | 영향 |
|---|------|------|------|------|
| W1 | BUY에 SELL이 2건인 경우 존재 | 070300, 082920 등 | 오버나이트 강제청산 + OVERNIGHT_FAILSAFE 이중 실행 | P&L 집계 왜곡 |
| W2 | 고립 SELL (BUY 없음) | 10건 | 구시스템(2025-11월) 데이터 불일치 | RR/승률 계산 왜곡 |
| W3 | `ml_dataset.mae_pct` 전체 NULL | 107/107 | `_mfe if _mfe else None` falsy 버그 — 0.0%는 None으로 저장 | ML 모델 학습 왜곡 |
| W4 | `trades.strategy_name` NULL | 22/396 (6%) | 최근 9건 SMC 진입 + 구 데이터 139건 UNKNOWN | 전략별 분석 부정확 |
| W5 | `daily_capital_snapshot.capital_end` 소액 | 전체 < 100만원 | 테스트/분할 계좌 추적 중 | 실계좌 자본 추적 안됨 |
| W6 | `trade_signals` entry 1건만 | 1/21 | `record_entry_signal()` 실패율 극히 높음 | 진입 컨텍스트(RSI/VWAP/grade) 손실 |
| W7 | `swing_positions.json` trade_id=None | 2개 포지션 | P0 이전부터 보유 중인 포지션 | P0 MAE/MFE 추적 안 됨 |

### 🟢 정상

| # | 항목 | 현황 |
|---|------|------|
| OK1 | `trades.profit_rate` 정합 | BUY 레코드 제외 시 NULL 정상 |
| OK2 | `ml_dataset.trade_id` FK 연결 | 107/107 (100%) 연결 |
| OK3 | `account_snapshot` | 장 중 매매마다 정상 기록 |
| OK4 | `trades` BUY 즉시 기록 | P0 Fix 이후 정상 (이전엔 execute_buy 후반 실패시 누락) |
| OK5 | P0 신설 테이블 스키마 | 정상 생성, FK/Index 포함 |

---

## 5. P&L 현황 (trades 기준)

| 지표 | 값 |
|------|----|
| 전체 SELL | 238건 |
| 승 | 54건 (22.7%) |
| 패 | 107건 (44.9%) |
| 나머지 | 77건 (pnl=0 또는 NULL) |
| 평균 수익 | +1.355% |
| 평균 손실 | -1.520% |
| RR | 0.89 |
| Expectancy | **-0.868% / 거래** |
| 실현 손익 합계 | -56,560원 |

> **주의**: 이 수치는 `trades` 테이블 기준. 고립 SELL 10건 포함, 중복 SELL 포함.  
> 정제된 분석은 W1/W2 이슈 해결 후 재계산 필요.

---

## 6. 구버전 테이블 (현재 완전히 미사용)

아래 테이블들은 2025년 10~11월 구 시스템에서 생성됐으나 현재 코드에서 Write 없음.
분석 파이프라인에서도 참조 안 됨.

```
filter_history          (2,079건, last: 2025-11-02)
validation_scores       (8,259건, last: 2025-11-02)
strategy_execution_results (22건, last: 2025-10-21)
filtered_candidates     (13건, last: 2025-12월)
filter_stage_results    (2건)
trade_executions        (0건)
trade_history           (0건)
trade_reconciliation    (0건)
blocked_trades          (0건)
signal_performance      (0건)
simulations             (0건)
backtest_results        (0건)
analysis_results        (0건)
```

---

## 7. P0 이후 데이터 흐름 (정상 시나리오)

```
[swing_runner.py 15:35]
  ├─ scan_new_signals()
  │    └─ 후보 발견 → swing_orders_YYYYMMDD.json (market_regime 포함)
  ├─ process_hold_positions()
  │    └─ _update_mfe_mae() → trades UPDATE (peak/trough/mfe/mae)
  └─ SwingStateManager.save()

[swing_executor.py 09:00]
  ├─ ENTRY
  │    ├─ KiwoomAPI.order_buy() 성공
  │    ├─ insert_swing_buy() → trades (BUY) → trade_id 반환
  │    ├─ insert_swing_features() → swing_features (SignalEngine 스냅샷)
  │    └─ SwingPosition.trade_id = trade_id → swing_positions.json 저장
  ├─ EXIT / REDUCE
  │    ├─ KiwoomAPI.order_sell() 성공
  │    └─ insert_swing_sell() → trades (SELL, mfe/mae/peak/trough 포함)
  └─ TRAIL → 주문 없음, 상태만 업데이트

[analysis/missed_alpha.py — 장 마감 후 수동/크론]
  ├─ buy_failures.future_return 채우기 (yfinance)
  └─ signal_rejections.future_return 채우기
```

---

## 8. 즉시 조치 필요 항목 (우선순위)

| 우선순위 | 항목 | 방법 | 예상 효과 |
|---------|------|------|----------|
| **P1** | `ml_dataset.mae_pct` NULL 버그 수정 | `decision_trace.py` L963: `_mfe if _mfe else None` → `mfe_pct if mfe_pct is not None else None` | ML 학습 데이터 복원 |
| **P1** | 현재 swing 포지션 2개 trade_id 수동 등록 | swing_positions.json에 실제 BUY trade_id 수동 입력 | MAE/MFE 추적 즉시 시작 |
| **P2** | `record_entry_signal()` 실패 원인 조사 | decision_trace.py 로그 확인, try/except 세분화 | entry_signal_id / 진입 컨텍스트 복원 |
| **P2** | BUY 1건에 SELL 2건 문제 정리 | OVERNIGHT_FAILSAFE 중복 방지 로직 확인 | P&L 집계 정확도 향상 |
| **P3** | `daily_capital_snapshot` 실계좌 연동 | account_snapshot 기반 재계산 또는 실계좌 설정 | 실자본 추적 |
| **P4** | 구버전 테이블 정리 | 별도 스키마 분리 또는 DROP (데이터 보존 후) | DB 복잡도 감소 |

---

## 9. 관찰 인프라 안정화 체크리스트 (향후 1~2주)

다음 항목을 매일 `analysis/swing_data_quality.py`(미구현)로 자동 검증 권장:

- [ ] 당일 스윙 BUY 발생 시 `swing_features` 동시 기록 확인
- [ ] `trades.peak_price` NULL 여부 (보유 3일+ 경과 BUY 레코드)
- [ ] 동일 stock_code + 동일 날짜 SELL 2건 이상 여부
- [ ] `trades.strategy_name` NULL BUY 건수
- [ ] `swing_positions.json`의 모든 포지션에 `trade_id` 존재 여부
- [ ] `swing_features.market_regime` NULL 건수

---

*이 문서는 2026-05-09 기준 전수 검사 결과임. DB 변경 발생 시 재검사 필요.*
