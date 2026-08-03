# CURRENT_STRATEGY_MAP — 현재 운영 Pipeline

Iteration 6-4 · 2026-08-03

## 현재 실제로 매수를 만드는 경로는 하나다

```
[15:35 cron]  swing_runner.py                    ← 크론 등록 확인
      │        SignalEngine (pullback 패턴 3종)
      │        Top-3 · 섹터한도 · 쿨다운
      ▼
   data/swing_positions.json   (entry / stop / target / size)
      │
[09:00 cron]  swing_executor.py                  ← 크론 등록 확인
      │        KiwoomAPI.order_buy()
      ▼
   db.insert_swing_buy()   condition_name='SWING'
      ▼
   PostgreSQL trades        최근 BUY 2026-07-01
```

## main_auto_trading 경로 — 신호는 도는데 매수가 안 나간다

```
monitor_and_trade()  60초 주기        ← 프로세스 가동 중 (uptime 2일+)
      ▼
check_all_stocks()
      ▼
check_entry_signal()
      ├ squeeze_orderbook  L6130   실행 여부 확인 불가
      ├ ma_cross           L6233   실행 여부 확인 불가
      ├ two_tf             L6308   실행 여부 확인 불가
      └ smc                L6457   로그 최근 30일 60건 — 호출됨
      ▼
execute_buy()  L9351
      ▼
  ┌─────────────────────────────────────────┐
  │  🛑 EC_HALT                             │
  │  2026-05-13 ~ 07-31  차단 28,527건      │
  │  7월 차단 9,589건 = 100% EC_HALT        │
  └─────────────────────────────────────────┘
      ▼
   (매수 도달 0건, 2026-07-01 이후)
```

## EC_HALT 상태

```
data/equity_state.json    peak = 4,467,501   updated_at = 2026-07-12
data/risk_log.json        initial_balance = 4,563,361
                          consecutive_losses = 3
                          cooldown_until = 2026-07-01T15:30:00

7-29 이후 EC_HALT:  dd = -19.3 ~ -24.5%   임계값 -18%
```

**peak 복구 후에도 실제 낙폭이 임계값을 넘어 정당하게 차단 중이다.**
