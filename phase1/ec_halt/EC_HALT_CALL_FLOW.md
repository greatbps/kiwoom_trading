# EC_HALT_CALL_FLOW

Iteration 7-1 · 2026-08-03 · 코드 확인만

```
signal
  │
  ▼
check_entry_signal()                       main_auto_trading.py:5567
  │
  ▼
[진입 게이트 체인]                          main:5545~5563
  │
  ├─ ④ Drawdown Engine                     main:5547
  │      drawdown_engine.can_enter()
  │      실패 → "[DD_HALT] …"
  │      예외 → "[DD_HALT_EXCEPTION] …"     ← Fail Closed
  │
  ├─ ⑤ Equity Curve HALT                   main:5556
  │      equity_ctrl.can_enter(self.total_assets)
  │      실패 → "[EC_HALT] …"               main:5560
  │      예외 → "[EC_HALT_EXCEPTION] …"     main:5563  ← Fail Closed
  │
  ▼
EquityController.can_enter(equity)         trading/equity_controller.py:168
  │
  ├─ enabled=False        → (True, 'disabled')
  ├─ equity != equity     → (False, 'EC_HALT_EXCEPTION: equity=NaN')  ← Fail Closed
  │                          [CBF-1 2026-07-20] NaN 이 비교식을 전부 False 로
  │                          통과시켜 무음 허용되던 버그 수정
  ├─ peak<=0 or equity<=0 → (True, 'no_peak')
  │
  ├─ halt_pct = cfg['max_dd_halt']  = -0.18
  ├─ dd = (equity - self._peak) / self._peak
  └─ dd <= halt_pct       → (False, 'EC_HALT: dd=…% ≤ -18% (equity=… peak=…)')
  │
  ▼
Entry Block  →  signal_rejections 기록
```

## equity 입력

```
main:2410  self.total_assets = self.withdrawable_cash + self.positions_value
                                     │                        │
                        출금가능금(pymn_alow_amt)      Σ(현재가 × 보유수량)
```

⚠️ `entr` 은 매수 미결제분이 이중계산되어 쓰지 않는다 (main:2409 주석).

## peak 갱신 경로

```
main:2831   equity_ctrl.update_peak(total_assets)        장중
              └─ eod_only_peak=True → 15:20 이전 무시
main:12088  equity_ctrl.update_peak_eod(total_assets)    EOD 확정

두 경로 모두 앞단에 가드:
main:2830   if hasattr(self,'equity_ctrl') and self._account_data_reliable:
```
