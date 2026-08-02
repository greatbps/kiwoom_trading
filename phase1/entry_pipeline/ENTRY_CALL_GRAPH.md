# ENTRY_CALL_GRAPH — 현재 운영 BUY 호출 관계

Iteration 6-3 · 2026-08-03 · **코드 확인만. 추정 0건.**

```
main_auto_trading.py
      │
      ▼
monitor_and_trade()                          L3601
      │  60초 주기 (check_interval = 60, L3637)
      ▼
check_all_stocks()                           L4074
      │  all_stocks = watchlist(≤30) | positions   L4131
      │
      ├──────────────────────────┬───────────────────────┐
      ▼                          ▼                       ▼
check_entry_signal()        check_entry_signal()   _flush_pending_signals()
  (L4778, async)              (L5413, async)          (L4801)
      │                                                  │
      │  L5567 함수 본체                                  │  L9291 함수 본체
      │                                                  │
      ├─ ① squeeze_orderbook_strategy.check_entry_signal()  L6130
      ├─ ② ma_cross_strategy.check_entry_signal()           L6233
      ├─ ③ two_tf_strategy.check_entry_signal()             L6308
      ├─ ④ smc_strategy.check_entry_signal()                L6457
      │                                                     │
      ▼                                                     ▼
   execute_buy() 호출 6곳                            execute_buy() 호출 1곳
      L6747  entry_reason=entry_reason                   L9340
      L6877  "SQZ:HH:MM bbw=… vol=…"                     sig['entry_reason']
      L6940  "RS:HH:MM …"
      L6999  "DEFENSIVE:HH:MM …"
      L7295  "EXPLORATION:HH:MM …"
      L7359  "EXPERIMENT:HH:MM"
      │
      └──────────────────┬──────────────────────────────────┘
                         ▼
              execute_buy()                    L9351
                         │
                         ├─ Risk Filter  조기 return 37경로
                         │     reason_code 8종 (Decision Ledger)
                         │
                         ├─ Position Size
                         │     RiskManager.calculate_position_size()
                         │     core/risk_manager.py:301
                         │     qty = int((balance × RISK_PER_TRADE)
                         │               / abs(entry - stop))
                         │
                         ├─ KiwoomAPI.order_buy()
                         │     order_no = order_result.get('ord_no')
                         │
                         ▼
              db.insert_trade(trade_data)      L10604 / L10889
                         │  condition_name = 'VWAP+AI'  (상수)
                         │  entry_context   = _condition_attribution()  (Iter8-1)
                         │                  + order_no                  (Iter8-3)
                         ▼
              database/trading_db.py  insert_trade()
                         │  _fold_order_no()      order_no → entry_context
                         │  _is_duplicate_trade() 5필드 + 300초
                         ▼
                    PostgreSQL  trades
```

## 별도 경로 — swing_executor (독립 프로세스)

```
[15:35 cron] swing_runner.py  →  data/swing_positions.json
[09:00 cron] swing_executor.py
                  │
                  ├─ KiwoomAPI.order_buy()
                  ▼
             db.insert_swing_buy()
                  │  condition_name = 'SWING'
                  ▼
             PostgreSQL  trades
```

**main_auto_trading 과 swing_executor 는 별개 프로세스이고
`insert_trade` / `insert_swing_buy` 로 서로 다른 함수를 쓴다.**
