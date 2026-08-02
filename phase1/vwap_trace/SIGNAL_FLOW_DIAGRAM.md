# SIGNAL_FLOW_DIAGRAM — VWAP+AI

Iteration 6-2 · 코드 근거 기재. **미확인 구간은 ❌ 로 표시.**

```
┌─ ① 조건검색 ─────────────────────────────────────────────────────┐
│ 함수  filter_stocks_with_vwap()                                  │
│ 파일  main_auto_trading.py:3040                                  │
│ 입력  self.condition_indices [17~22] (config:1275)               │
│ 출력  all_stocks: set[str]                                       │
│       _cond_sources: dict[code → list[조건식명]]  (Iter8-1)      │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ② 후보 등록 ────────────────────────────────────────────────────┐
│ 함수  (같은 루프 내)                                             │
│ 파일  main_auto_trading.py:3223 / 3241                           │
│ 입력  all_stocks + L2 RS 필터 결과                               │
│ 출력  self.validated_stocks[code] = {name, market, rs_rating,    │
│         stats, data, analysis, strategy, condition_sources, …}   │
│       self.watchlist.add(code)                                   │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ③ VWAP 계산 ────────────────────────────────────────────────────┐
│ 함수  EntryTimingAnalyzer.calculate_vwap()                       │
│ 파일  analyzers/entry_timing_analyzer.py:58                      │
│ 입력  df(5분봉 OHLCV), use_rolling=True, rolling_window=20       │
│       ⚠️ config 에 vwap: 섹션 없음 → 코드 기본값이 그대로 쓰임    │
│ 출력  df['vwap']  = rolling_sum(pv,20) / rolling_sum(volume,20)  │
│       pv = ((H+L+C)/3) × V                                       │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ④ AI Score ─────────────────────────────────── ❌ 미확인 ───────┐
│ 함수  ???                                                        │
│ 파일  ???                                                        │
│ 입력  ???                                                        │
│ 출력  analysis['total_score']                                    │
│       → order_executor.py:251 이 읽는다                          │
│       → entry_timing_analyzer 에 total_score 정의 0건            │
│       → 실거래 89건(53.9%)이 0.0 = .get(...,0) 기본값            │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ⑤ VWAP 돌파 판정 ─────────────────────────────── ❌ 미확인 ─────┐
│ 코드에 4종 공존 — 어느 것이 쓰였는지 특정 불가                    │
│   main:8617          close_prev < vwap_prev and high >= vwap_now │
│   main:8708          close >= vwap_val                           │
│   main:11308         current_price > vwap_val                    │
│   analyzer:227       (recent['close'] > recent['vwap']).all()    │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ⑥ Risk Filter ──────────────────────────────────────────────────┐
│ 함수  execute_buy() 내 조기 return 37경로                        │
│ 파일  main_auto_trading.py:9351~                                 │
│ 입력  position 상태 · 계좌 · 쿨다운 · 레짐 · 한도                │
│ 출력  reason_code 8종 (Decision Ledger 기록)                     │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ⑦ Position Sizing ──────────────────────────────────────────────┐
│ 함수  RiskManager.calculate_position_size()                      │
│ 파일  core/risk_manager.py:301                                   │
│ 입력  current_balance, current_price, stop_loss_price            │
│ 출력  qty = int((balance × RISK_PER_TRADE) / |entry - stop|)     │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ⑧ BUY ────────────────────────────────── ⚠️ 경로 불일치 ────────┐
│ DB 기록을 만든 함수  OrderExecutor.execute_buy()                 │
│ 파일                 trading/order_executor.py:251               │
│ 상태                 **main_auto_trading 에서 호출 0건**          │
│                      RUNTIME_EXECUTION_MAP 도 LOADED-ONLY 분류    │
│                                                                  │
│ 현재 실사용          main_auto_trading.execute_buy()  L9351       │
│ entry_reason 형식    "HH:MM 5분봉 …" / "SQZ:…" / "RS:…" /        │
│                      "EXPLORATION:…" / "A+:…"                     │
│                      → "VWAP 상향 돌파" 를 만들지 않는다          │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ⑨ trade 기록 ───────────────────────────────────────────────────┐
│ 함수  TradingDatabase.insert_trade()                             │
│ 파일  database/trading_db.py:600                                 │
│ 저장  condition_name · entry_reason · entry_context(order_no ·   │
│       condition_sources) — Iter8-1/8-3 추가                      │
└──────────────────────────────────────────────────────────────────┘
```
