# ENTRY_RULE_REPORT — 현재 운영 전략 정의

Iteration 6-3 · **확인된 것만 기재. 미확인은 명시.**

## 현재 실제로 BUY 를 만드는 경로 7개

| # | 위치 | entry_reason 형식 | 신호 엔진 | 상태 |
|---|---|---|---|---|
| 1 | `main:6747` | 상위에서 받은 `entry_reason` | ①~④ 중 하나 | ✅ |
| 2 | `main:6877` | `SQZ:HH:MM bbw=… vol=…` | `squeeze_orderbook_strategy` | ✅ |
| 3 | `main:6940` | `RS:HH:MM …` | RS 전략 | ✅ |
| 4 | `main:6999` | `DEFENSIVE:HH:MM …` | Defensive | ✅ |
| 5 | `main:7295` | `EXPLORATION:HH:MM …` | Exploration | ✅ |
| 6 | `main:7359` | `EXPERIMENT:HH:MM` | Experiment | ✅ |
| 7 | `main:9340` | `sig['entry_reason']` | `_emit_signal` → pending | ✅ |

### 신호 엔진 4종 (check_entry_signal 내부)

| 엔진 | 호출 | 파일 |
|---|---|---|
| ① Squeeze + 호가창 | `main:6130` | `analyzers/squeeze_with_orderbook.py` |
| ② MA Cross | `main:6233` | `analyzers/ma_cross_strategy.py` |
| ③ Two-Timeframe Squeeze | `main:6308` | `analyzers/squeeze_momentum_lazybear.py` |
| ④ SMC | `main:6457` | `analyzers/smc/smc_signals.py` |

## Entry 조건

**각 엔진의 내부 조건은 이번 Iteration 범위에서 확인하지 않았다.**
작업지시서 §Phase 2 는 "어떤 조건으로 BUY 발생 · Indicator · 변수 ·
Threshold" 를 요구하는데, 엔진이 4개이고 각각 별도 모듈이다.

**확인 불가 — 엔진별 개별 추적이 필요하다.**

## Exit 조건

`trading/exit_logic_optimized.py` `check_exit_signal()`
— Iteration 7-2 에서 청산 규칙 11종 분류 완료.

## Filter

`execute_buy()` 내 조기 return 37경로. reason_code 8종:
`COOLDOWN_ACTIVE` · `RISK_BLOCKED` · `DUPLICATE_POSITION` ·
`MAX_POSITIONS` · `INSUFFICIENT_CAPITAL` · `ENTRY_QUALITY_BLOCKED` ·
`API_FAILURE` · `ORDER_FAILURE`

## Risk

`core/risk_manager.py` — `can_open_position()` · 일일손실 한도 ·
연속손실 · 섹터/포트폴리오 한도.

## Position Size

`core/risk_manager.py:301`
```python
risk_amount         = current_balance * self.RISK_PER_TRADE
risk_per_share      = abs(current_price - stop_loss_price)
risk_based_quantity = int(risk_amount / risk_per_share)
```
- Stop Loss 사용 ✅ (`stop_loss_price`)
- Risk Amount 사용 ✅
- ATR 사용 — `stop_loss_price` 산출에 ATR 이 쓰이는지는 **확인 불가**
  (이번 범위에서 `stop_loss_price` 생성 경로를 추적하지 않았다)
