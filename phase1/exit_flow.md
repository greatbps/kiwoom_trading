# Exit 호출 경로 Audit — Swing 포지션

Phase 1 Iteration 5 / Experiment 1
생성: 2026-08-02 · **읽기 전용 분석, 운영 코드 무수정**

---

## 1. 전체 흐름

```
[15:35 cron]  swing_runner.py
                 └→ 종목 선정 + stop_price / target_price 계산
                 └→ data/swing_positions.json 기록

[09:00 cron]  swing_executor.py           ← 하루 한 번
                 └→ 시장가 매수
                 └→ data/swing_positions.json 갱신 (entry_price, quantity)
                 └→ PostgreSQL trades INSERT (stop_price 포함)

[main_auto_trading.py — 08:45 기동, 15:30 종료]
   monitor_and_trade()  루프
      ├ 5분마다 : _attach_swing_risk_positions()      (main:3708)
      │             └→ swing_positions.json → self.positions 에 편입
      │                strategy_horizon='SWING', risk_only=True
      │                ⚠️ structure_stop_price 를 넣지 않는다
      │
      └ 60초마다: check_all_stocks()                   (main:4034)
                    all_stocks = watchlist(≤30) | positions
                    └→ check_exit_signal(code, df)     (main:4607)
                         └→ OptimizedExitLogic.check_exit_signal()  (main:8282)
                              └→ execute_sell() / execute_partial_sell()
                                   └→ KiwoomAPI.order_sell()
```

---

## 2. Stop Loss 평가 주기

| 항목 | 값 | 근거 |
|---|---|---|
| 청산 판정 주기 | **60초** | `check_interval = 60` (main:3637) |
| 스윙 포지션 편입 주기 | **300초** | `(current_time - last_swing_attach).seconds >= 300` (main:3708) |
| main 가동 구간 | 08:45 ~ 15:30 | watchdog cron 8:45/9:00/9:15, `shutdown_time` 15:30 (main:3712) |
| swing_executor 실행 | **1일 1회 09:00** | `0 9 * * 1-5` |
| **스윙 청산 전용 cron** | **없음** | crontab 에 항목 없음 |

장중 감시는 존재한다 — 60초 주기이고 보유 종목은 항상 `all_stocks` 에
포함된다 (`all_stocks = _wl_capped | set(self.positions.keys())`, main:4131).

**따라서 "감시가 없다" 가 아니라 "감시는 도는데 손절값이 전달되지 않는다" 가
정확한 진단이다.**

---

## 3. 실패 경로 — 확인된 것

### 3-1. `structure_stop_price` 유실 (주요 원인)

`_attach_swing_risk_positions()` 가 만드는 포지션 딕셔너리 (main:8667~8684):

```python
self.positions[code] = {
    'stock_code', 'stock_name', 'name', 'quantity', 'avg_price',
    'entry_price', 'current_price', 'highest_price', 'entry_time',
    'entry_date', 'trailing_active', 'trailing_stop_price',   # ← None
    'partial_exit_stage', 'strategy_horizon', 'strategy',
    'risk_only', 'allow_overnight',
}
```

**`structure_stop_price` 키가 없다.** `swing_positions.json` 과 DB 에는
`stop_price` 가 기록돼 있는데 편입 과정에서 옮겨지지 않는다.

그 결과 `exit_logic_optimized.py:807`:

```python
structure_stop_price = position.get('structure_stop_price')   # → None
...
if structure_stop_price and struct_stop_config.get('enabled', True):
    ...  # 구조 손절 — 진입 안 함
else:
    if _is_swing:
        _swing_hard_stop = self.config.get('risk_control.swing_hard_stop_pct', 12.0)
        if profit_pct <= -_swing_hard_stop:      # ← -12% 여야 청산
            return True, '[SWING_HARD_STOP] ...'
        elif profit_pct <= -max_stop_pct:        # -5%
            logger.warning('[SWING_NO_STRUCTURE_STOP] ... -12% 까지 허용')
            # ⚠️ 청산하지 않는다. 경고만 남긴다.
```

`config/strategy_hybrid.yaml:359` — `swing_hard_stop_pct: 12.0`

**swing_runner 가 계산한 손절(−1.40% ~ −5.34%)은 집행되지 않는다.
실효 손절선은 −12% 다.** −5% ~ −12% 구간에서는 경고 로그만 남는다.

### 3-2. 매수 당일 미편입 (2차 원인)

5거래 중 2건은 매수 당일 편입 자체가 없었다.

| 거래 | 매수일 어태치 | 최초 어태치 |
|---|---|---|
| 삼성SDI 2026-05-12 | **없음** | 2026-05-13 09:00:10 (**+24시간**) |
| SK하이닉스 2026-06-10 | 09:00:07 | 당일 |
| NAVER 2026-06-15 | 09:00:06 | 당일 |
| 삼성전자 2026-06-23 | 09:00:06 | 당일 |
| 삼성전자 2026-07-01 | **없음** | — |

삼성SDI 는 매수 당일 로그에 종목코드 `006400` 이 **0회** 등장한다.
그날 장중 저가 616,000 (−9.9%) 까지 갔으나 어떤 감시도 없었다.

### 3-3. 런타임 로그로 본 확증

```
[SWING_NO_STRUCTURE_STOP]  0건    (전 로그 기간 2026-01-28 ~ 2026-08-02)
[SWING_RISK_ATTACH]      312건
```

경고 로그가 0건인 것은 −5% ~ −12% 구간 평가에 도달하기 전에
다른 경로(09:00 배치 / 14:50 오버나이트 차단)로 먼저 청산됐기 때문으로
보인다. 실제 청산 사유가 `[HARD_STOP] -5.0%` · `14:50 오버나이트 차단` ·
`MA5_EXIT` · `DRAWDOWN_STOP` 인 것과 일치한다.

⚠️ 다만 `[HARD_STOP] -5.0%` 문자열은 비-SWING 분기(`else`)의 출력이다.
`_is_swing` 이 False 로 평가된 경로가 있다는 뜻이며, 어느 지점에서
`strategy_horizon` 이 소실되는지는 **이번 audit 으로 특정하지 못했다.**
추정하지 않고 미해결로 남긴다.

---

## 4. 휴면 구현 — 혼동 주의

`docs/RUNTIME_EXECUTION_MAP.md` 가 이미 경고한 사항이다.

| 파일 | 상태 |
|---|---|
| `core/auto_stop_loss_system.py` | NOT-LOADED |
| `core/stop_loss_manager.py` | NOT-LOADED |
| `trading/stop_loss_executor.py` | NOT-LOADED |
| `trading/trend_exit_engine.py` | LOADED-ONLY (인스턴스화 없음) |
| `gpt_share/exit_logic_optimized.py` | NOT-LOADED (동명 파일) |
| `docs/share/exit_logic_optimized.py` | NOT-LOADED (동명 파일) |

**실사용은 `trading/exit_logic_optimized.py` 하나다.**

---

## 5. 요약

| 질문 | 답 |
|---|---|
| 장중 감시가 도는가 | **돈다** (60초 주기) |
| 스윙 포지션이 감시 대상인가 | **대상이다** (5분마다 편입) |
| 손절값이 전달되는가 | **전달되지 않는다** (`structure_stop_price` 유실) |
| 실효 손절선 | **−12%** (설계값은 −1.40% ~ −5.34%) |
| 매수 당일 편입되는가 | **5건 중 3건만** |

**결론: Execution 문제가 맞다. 다만 "감시 주기가 느려서" 가 아니라
"손절 가격이 감시자에게 전달되지 않아서" 다.**
따라서 수정 방향은 Scheduler 주기 개선(B안)이 아니라
**편입 시 손절값 전달(A안 변형)** 이다.
