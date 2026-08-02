# RUNTIME EXECUTION MAP

> 작성일: 2026-07-28 | Production Audit v2.1
> **목적: "어느 파일이 실제로 실행되는가"를 5분 안에 확인한다.**
> 검증 방법: ① import closure(지연 import 포함) ② 코드 호출 추적 ③ 당일 실행 로그 태그 — 3중 대조
> 기준 프로세스: PID 3199837 (2026-07-28 09:58 기동)
>
> ⚠️ **줄번호는 신뢰하지 말 것 (v2.2 검증에서 확인)**
> 이 문서의 절대 줄번호는 코드가 바뀌면 즉시 어긋난다. 실제로 v2.1 작성 후 반나절 만에
> `execute_buy`(9139→9124)와 부분청산 트레일링 가드(12231→12242) 2건이 밀렸다.
> **함수명·코드 조각으로 `grep` 하는 방식을 우선 사용하라.** 이 문서도 가능한 곳은
> 줄번호 대신 grep 앵커로 표기했다.

---

## 0. 먼저 읽을 것 — 분류 정의

이 프로젝트는 **같은 기능의 구현이 3~4개씩 공존**한다. 이름만 보고 파일을 고치면
아무 효과가 없을 수 있다. 아래 4단계로 구분한다.

| 분류 | 의미 | 수정 시 효과 |
|---|---|---|
| 🟢 **ACTIVE-RUNTIME** | 실행됨. 당일 로그에 증거 있음 | **즉시 실거래 영향** |
| 🟡 **ACTIVE-PATH** | 실행 경로에 있으나 당일 조건 미충족으로 미실행 (예: 거래 0건이라 execute_buy 미호출) | **조건 충족 시 실거래 영향** |
| 🟠 **LOADED-ONLY** | import는 되나 함수/클래스가 호출·인스턴스화된 적 없음 | **효과 없음** |
| ⚪ **NOT-LOADED** | import조차 안 됨 | **효과 없음** |

**규모**: 전체 **501개 모듈 중 로드 171개 / 미로드 330개**.

> ⚠️ **집계 방법 주의 (v2.0 보고서 정정)**
> import closure를 계산할 때 다음 두 가지를 반드시 반영해야 한다. 빠뜨리면 실제로
> 로드되는 모듈을 "휴면"으로 오판한다 — v2.0 보고서가 이 오류를 범했다.
> 1. **상대 import**: `analyzers/smc/__init__.py`는 `from .smc_signals import ...`로
>    하위를 끌어온다. 상대 import를 건너뛰면 SMC 전체가 미로드로 잘못 집계된다.
> 2. **패키지 `__init__` 전파**: `trading/__init__.py`는 하위 12개 모듈을 eager import한다.
>    따라서 `trading.exit_logic_optimized` 하나만 import해도 `order_executor`,
>    `position_tracker`, `trend_exit_engine`이 **전부 메모리에 로드**된다.
>    (단, 로드될 뿐 인스턴스화되지는 않는다 → LOADED-ONLY)

---

## 1. 매수 파이프라인 (Entry)

```
[스캔/후보]  daily_scan(cron 08:30)  →  watchlist
     ↓
[Score]      ScoreEngine.rank()
     ↓
[루프]       check_entry_signal()          ← 진입 총괄 (main_auto_trading.py:5411)
     ↓
[Risk Gate]  _check_global_risk_gates()
     ↓
[Regime]     RegimeAnalyzer.evaluate()
     ↓
[SMC]        SMCStrategy.check_entry_signal()
     ↓
[신호큐]     _emit_signal() → _flush_pending_signals()
     ↓
[집행]       execute_buy()                  ← EQ 필터 6종 + 사이징 + 게이트
     ↓
[주문]       KiwoomAPI.order_buy()
     ↓
[DB]         TradingDatabase.insert_trade() + DecisionService.record_acceptance()
     ↓
[포지션]     self.positions[code] 생성 + _save_positions_state()
```

| 단계 | 실행 파일 | 실행 함수 | 호출 위치 | 상태 | 근거 |
|---|---|---|---|---|---|
| 스캔 | `backtest/daily_scan.py` | `scan_today()` | cron 08:30 | 🟢 | `logs/daily_scan.log` 당일 08:30 |
| Score | `trading/score_engine.py` | `ScoreEngine.rank()` | `main:13656` | 🟢 | `[SCORE_ENGINE]`×14, `[SCORE_INPUT]`×2 |
| 진입 총괄 | `main_auto_trading.py` | `check_entry_signal()` | 모니터링 루프 | 🟢 | `[MKT_CTX]`×11 |
| Risk Gate | `main_auto_trading.py` | `_check_global_risk_gates()` | `main:5450` | 🟢 | 루프 내 매 사이클 |
| ├ 연패/한도 | `core/risk_manager.py` | `RiskManager.can_open_position()` | `execute_buy` 내 | 🟡 | 진입 시도 시에만 |
| ├ 드로우다운 | `core/drawdown_engine.py` | `DrawdownEngine.can_enter()` | gate 내 | 🟢 | `[DRAWDOWN]`×5 |
| └ 자산곡선 | `trading/equity_controller.py` | `EquityController` | gate 내 | 🟢 | `[EQUITY_CTRL]`×2 |
| Regime Gate | `analyzers/market/regime_analyzer.py` | `RegimeAnalyzer.evaluate()` | `main:5540` | 🟢 | `[REGIME]`×8, `[REGIME_BLOCK]`×5 |
| SMC 신호 | `analyzers/smc/smc_signals.py` | `SMCStrategy.check_entry_signal()` | `main:6301` | 🟡 | 오늘 CHoCH 0건 |
| Orchestrator | `analyzers/signal_orchestrator.py` | `check_l0~l6` | 루프 내 | 🟢 | `logs/signal_orchestrator.log` |
| **집행** | **`main_auto_trading.py`** | **`execute_buy()`** | `_flush_pending_signals` | 🟡 | 오늘 진입 0건 |
| **주문** | **`kiwoom_api.py`** | **`KiwoomAPI.order_buy()`** | `main` 내 `self.api.order_buy(` | 🟡 | 오늘 주문 0건 |
| DB 기록 | `database/trading_db.py` | `insert_trade()` | `main:10377` | 🟡 | 진입 시 |
| Decision 원장 | `services/decision_service.py` → `repositories/decision_repository.py` | `record_acceptance/rejection` | `execute_buy`, `main:5568` | 🟢 | `[RESEARCH]`×7 |
| 포지션 저장 | `main_auto_trading.py` | `_save_positions_state()` (L1955) | 진입/청산 후 | 🟡 | 포지션 0건 |

### ⚠️ 매수 경로의 휴면 구현 (수정해도 효과 없음)

| 파일 | 상태 | 설명 |
|---|---|---|
| `brokers/kiwoom_broker.py` | 🟠 LOADED-ONLY | `brokers/__init__.py:31`에서 import되어 **모듈은 로드**되지만, `get_broker()`는 `KIS_DOMESTIC`/`KIS_OVERSEAS`만 호출(`main:876,884`)하고 **`BrokerType.KIWOOM`은 한 번도 호출되지 않음** → `KiwoomBroker()` 인스턴스가 생성된 적 없다 |
| `trading/order_executor.py` | 🟠 LOADED-ONLY | `trading/__init__.py:11`이 eager import. `OrderExecutor()` 인스턴스화는 진입점 어디에도 없음 |
| `trading/position_tracker.py` | 🟠 LOADED-ONLY | `trading/__init__.py:8`이 eager import. `PositionTracker()` 미인스턴스화 |
| `core/order_executor.py` | ⚪ NOT-LOADED | 주문 실행기. import조차 안 됨 |
| `core/position_manager.py` | ⚪ NOT-LOADED | 포지션 관리 |
| `core/portfolio_manager.py` | ⚪ NOT-LOADED | 포트폴리오 |

> **v2.0 보고서 정정 (중요)**
> v2.0은 `trading/order_executor.py`, `trading/position_tracker.py`,
> `trading/trend_exit_engine.py`, `analyzers/smc/*`를 "NOT-LOADED(휴면)"로 분류했으나
> **오류였다**. 상대 import와 패키지 `__init__` 전파를 집계에서 빠뜨린 탓이다.
> 정확히는 앞의 셋은 **LOADED-ONLY**(로드되나 미호출), `analyzers/smc/*`는 **ACTIVE**다.
> 이 오류는 v2.1에서 작성한 회귀 테스트
> (`tests/unit/test_runtime_map_v21_20260728.py`)가 잡아냈다.

---

## 2. 매도 / 청산 파이프라인 (Exit)

```
[모니터링 루프]  check_exit_signal()            (main_auto_trading.py:7821)
     ↓
[청산 판정]      OptimizedExitLogic.check_exit_signal()
     ↓  (하드스탑 / 트레일링 / EF / TP1·TP2 / 오버나이트)
[부분청산]       execute_partial_sell()          (main:12181)
[전량청산]       execute_sell()                  (main:12409)
     ↓
[주문]           KiwoomAPI.order_sell()
     ↓
[DB/원장]        insert_trade() + DecisionService.record_exit()
     ↓
[상태]           positions 제거 + _save_positions_state() + stock_cooldown 등록
```

| 단계 | 실행 파일 | 실행 함수 | 호출 위치 | 상태 |
|---|---|---|---|---|
| 청산 판정 총괄 | `main_auto_trading.py` | `check_exit_signal()` (L7821) | 모니터링 루프 | 🟡 (보유 0건) |
| **청산 로직** | **`trading/exit_logic_optimized.py`** | `OptimizedExitLogic.check_exit_signal()` | `main:8282` | 🟡 |
| 인스턴스 생성 | — | `OptimizedExitLogic(self.config)` | `main:413` | 🟢 (기동 시) |
| **트레일링 스탑** | **`trading/exit_logic_optimized.py`** | monotonic 가드 → `grep -n "max(prev_stop, calc_stop)"` | 청산 판정 내 | 🟡 |
| 부분청산 트레일링 | `main_auto_trading.py` | monotonic 가드(v1.1 BUG-02) → `grep -n "_pe_prev_stop, _pe_calc_stop"` | `execute_partial_sell` | 🟡 |
| 부분 청산 | `main_auto_trading.py` | `execute_partial_sell()` (L12181) | 청산 판정 후 | 🟡 |
| 전량 청산 | `main_auto_trading.py` | `execute_sell()` (L12409) | 청산 판정 후 | 🟡 |
| 매도 주문 | `kiwoom_api.py` | `order_sell()` (L960) | `main:12263,12537,12553` | 🟡 |
| EOD 청산 | `trading/eod_manager.py` | `EODManager` | 14:50 루프 | 🟡 |

### ⚠️ 청산 경로의 휴면 구현 — **가장 위험한 혼동 지점**

| 파일 | 상태 | 이름 때문에 오인하기 쉬움 |
|---|---|---|
| `core/auto_stop_loss_system.py` | ⚪ NOT-LOADED | 이름이 "자동 손절 시스템"이라 실사용으로 오인하기 쉬움 |
| `core/stop_loss_manager.py` | ⚪ NOT-LOADED | 손절 관리자 |
| `trading/stop_loss_executor.py` | ⚪ NOT-LOADED | 손절 집행기 |
| `trading/trend_exit_engine.py` | 🟠 **LOADED-ONLY** | `trading/__init__.py:23`이 eager import → **메모리엔 올라와 있다**. 단 `TrendExitEngine()` 인스턴스화가 없어 실행되지 않음. trailing stop에 monotonic 가드 없음(FU-01) |
| `gpt_share/exit_logic_optimized.py` | ⚪ NOT-LOADED | **실사용 파일과 동명** |
| `docs/share/exit_logic_optimized.py` | ⚪ NOT-LOADED | **실사용 파일과 동명** |

> 🔴 **손절/청산 로직을 수정할 때는 반드시 `trading/exit_logic_optimized.py`인지 확인할 것.**
> 동명 파일이 2개 더 있고, 손절 관련 휴면 모듈이 4개 더 있다.

---

## 3. 기능별 실사용 / 휴면 대조표

| 기능 | 🟢 실사용 (여기를 고쳐야 함) | 휴면 구현 |
|---|---|---|
| **주문 실행** | `kiwoom_api.py` `order_buy/order_sell` | `core/order_executor.py`, `trading/order_executor.py`, `brokers/kiwoom_broker.py`(LOADED-ONLY) |
| **손절/청산** | `trading/exit_logic_optimized.py` | `core/stop_loss_manager.py`, `core/auto_stop_loss_system.py`, `trading/stop_loss_executor.py`, `trading/trend_exit_engine.py`, `gpt_share/…`, `docs/share/…` |
| **트레일링 스탑** | `trading/exit_logic_optimized.py:1089` + `main_auto_trading.py:12231` | `trading/trend_exit_engine.py:360` |
| **포지션 관리** | `main_auto_trading.self.positions` + `_save_positions_state()` | `core/position_manager.py`, `trading/position_tracker.py`, `core/portfolio_manager.py` |
| **레짐 판정** | `analyzers/market/regime_analyzer.py` | `core/regime_detector.py`(일부 함수만 사용), `core/market_monitor.py`, `trading/market_monitor.py` |
| **스케줄링** | **cron** (crontab) | `core/scheduler.py` |
| **웹소켓** | `main_auto_trading.py` 내장 | `core/websocket/websocket_manager.py`, `trading/websocket_client.py` |
| **계좌 조회** | `kiwoom_api.get_balance/get_account_info` | `trading/account_manager.py`, `core/auto_balance_monitor.py` |
| **KIS 중기** | `brokers/korea_invest_broker.py` (🟢 `get_broker(KIS_*)`) | — |

---

## 4. 스케줄 실행 흐름 (cron)

```
08:30  daily_scan            → data/daily_patterns.json, daily_watchlist.json
08:45  watchdog              → 하트비트 날짜 불일치 시 강제 재시작
08:55  system_health_check --mode pre
09:00  watchdog(정상확인) / swing_executor(스윙 매수)
09:15  watchdog(정상확인)                    ← 오늘 마지막 자동 재시작 기회
09~15  */15 code_audit --check    (코드 변경 감시)
09~15  */30 returns_collector
15:35  swing_runner          → 스윙 종목 선정
15:45  returns_collector
15:55  decision_health_check
15:58  gate_health_check     ← v1.1에서 candidate=0 판별 추가
16:02  regime_block_simulator
16:03  code_audit --eod-report
16:05  live_lcl_validation + operations_daily_summary + system_health_check --mode post  ⚠️ 3중 동시
16:07  e2_trade_analytics
16:08  regime_daily_report
16:10  ops_weekly_review        (금요일만)
16:12  regime_evidence_weekly   (금요일만)
```

- **watchdog은 정상 프로세스를 재시작하지 않는다** — 하트비트가 신선하면 "정상 동작 중" 로그만 남긴다.
  따라서 **09:15 이후에는 당일 자동 재시작이 없다**(코드 수정이 자동 반영되지 않음).
- 16:05 3중 동시 실행은 셋 다 **DB 읽기 전용**이라 무해(V2-FU-03).
- `gate_health_check`는 15:58 크론 + 16:05 `operations_daily_summary` 내부 호출로 **하루 2회 실행**(V2-FU-02).

---

## 5. 실행 중 프로세스 실측 (PID 3199837)

| 지표 | 값 | 비고 |
|---|---|---|
| RSS | 362 MB | 기동 158초 시점 |
| Threads | **11** | `main_auto_trading.py`의 `threading` 직접 사용은 **0건** — 전부 라이브러리(requests/websockets/psycopg2 pool) |
| File Descriptors | 13 | |
| DB Connections | 2 | |

> 동시성 구조: **asyncio 단일 이벤트 루프**. `asyncio.create_task()`는 3곳
> (`rescan_and_add_stocks`, `update_account_balance`×2). `update_account_balance()`는
> 내부에 `await`가 없어 원자적으로 실행되므로 Lost Update가 발생하지 않는다
> (회귀 테스트 `test_update_account_balance_has_no_await_points`가 이 전제를 고정).

---

## 6. 수정 전 체크리스트 (운영자용)

코드를 고치기 전에 30초만 투자하면 헛수고를 막을 수 있다.

```bash
# 1) 이 파일이 로드되기는 하는가?
grep -rn "from <모듈경로> import\|import <모듈경로>" main_auto_trading.py api_server.py \
     swing_runner.py swing_executor.py watchdog.py

# 2) 이 클래스/함수가 실제로 호출되는가?
grep -rn "<클래스명>(\|\.<함수명>(" main_auto_trading.py

# 3) 오늘 실행된 흔적이 있는가?
grep "\[<로그태그>\]" logs/auto_trading_$(date +%Y%m%d).log
```

세 개 모두 비어 있으면 **휴면 코드**다. 고쳐도 실거래는 변하지 않는다.
