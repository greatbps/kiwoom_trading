# Production Audit v2.0 — 운영 안정성 심층 감사 보고서

> 작성일: 2026-07-28 | 수행 시각: 09:37~09:50 KST (장중, 배포 상태는 §7 참고)
> 회귀 테스트: `tests/unit/test_audit_v2_20260728.py` (12건)
> 이월 항목: `docs/PRODUCTION_AUDIT_V2_FOLLOWUP.md`

---

## 1. 감사 요약

| 등급 | 개수 | 항목 |
|---|---|---|
| **Critical** | **1** | V2-CRIT01 주문 API 타임아웃 재시도 → 중복 주문 위험 |
| **High** | **3** | V2-SF01/02 상태파일 무음 실패, V2-DC01 실거래 모듈 다중 사본 |
| **Medium** | **3** | V2-SCH01 크론 동시실행, V2-SCH02 gate_health 이중 실행, V2-LOG01 로그 태그 충돌(이월) |
| **Low** | **2** | ScoreEngine 로그 중복(이월), 크론 표기 불일치 |

**수정 완료: Critical 1건 + High 2건.** 나머지는 FOLLOWUP에 기록.

### Phase별 수행 결과

| Phase | 상태 | 핵심 결과 |
|---|---|---|
| P1 Silent Failure | ✅ 완료 | 467건 전수 분류 → 진성 CRITICAL 2건 수정 |
| P2 Reachability | ⚠️ 부분 | 모듈 단위 도달성만 수행. **전략 조건문 단위 분석은 미수행**(§6) |
| P3 API Recovery | ✅ 완료 | **중복 주문 위험 발견·수정** |
| P4 Race Condition | ✅ 완료 | 실경로 race **없음**으로 결론(근거 §2) |
| P5 Memory/Resource | ⚠️ 부분 | 정적 확인만. 장기 실행 프로파일링 미수행 |
| P6 Scheduler | ✅ 완료 | 16:05 3중 실행 확인(무해), gate_health 이중 실행 발견 |
| P7 Logging | ✅ 완료 | 무음 실패 2건 로깅 추가 |
| P8 Dead Code | ✅ 완료 | 286/418 미도달, 실거래 도메인 다중 사본 발견 |
| P9 Regression Test | ✅ 완료 | 12건 신규, 전체 415 PASS |

---

## 2. 발견 사항

### V2-CRIT01 [CRITICAL] 주문 API 타임아웃 재시도 → 중복 주문 ✅ 수정

- **위치**: `kiwoom_api.py:853`(order_buy), `kiwoom_api.py:955`(order_sell)
- **원인**:
  ```python
  @retry_on_error(max_retries=1, delay=1.0,
                  exceptions=(TradingConnectionError, TradingTimeoutError))
  ```
  주문 POST는 `timeout=15`로 전송된다(`kiwoom_api.py:914`). 15초 내 응답이 없으면
  `requests.exceptions.Timeout` → `_handle_request_error()`가 `TradingTimeoutError`로 변환
  (`kiwoom_api.py:153-157`) → 데코레이터가 이를 잡아 **주문을 1회 재전송**한다.
- **영향**: **HTTP 타임아웃은 "주문 실패"가 아니라 "응답만 유실"일 수 있다.** 주문이 이미
  키움에 접수된 상태에서 재시도하면 동일 종목이 **2회 체결**되어 의도의 2배 노출이 생긴다.
  실계좌 자금에 직접 영향.
- **하류 방어 부재 확인**: `DUPLICATE_BLOCK`(`main_auto_trading.py:9735`)은 `self.positions`를
  검사하는데, 이 값은 주문 **성공 이후**에 갱신된다. 즉 단일 `order_buy()` 호출 내부의
  재시도 중복은 전혀 막지 못한다. `TradeReconciliation`은 사후 동기화라 예방 장치가 아니다.
- **재현**: `tests/unit/test_audit_v2_20260728.py::test_crit01_order_apis_do_not_retry_on_timeout`
- **수정**: `exceptions`에서 `TradingTimeoutError` 제거, `TradingConnectionError`만 유지.
  연결 오류는 연결 자체가 수립되지 않은 경우라 주문 미전송이 거의 확실해 재시도가 안전하다.
  **트레이드오프**: 타임아웃 시 진입 1건을 놓칠 수 있으나, 중복 주문보다 손실이 작다(운영자 승인).
- **범위 제한**: 조회 계열(`get_access_token`/`get_stock_price`/`get_balance`)은 멱등이라
  타임아웃 재시도 유지. `order_cancel`도 유지 — 중복 취소는 노출을 만들지 않고, 오히려
  취소 실패가 원하지 않는 주문을 살려두어 더 위험하다.

### V2-SF01/SF02 [High] positions_strategy.json 무음 실패 ✅ 수정

- **위치**: `main_auto_trading.py` 저장부(execute_buy 내), 로드부(`initialize_account` 내)
- **원인**: 양쪽 모두 `except Exception: pass`
- **영향**: 이 파일은 **재시작 시 청산 전략 복원**에 쓰인다(`main_auto_trading.py:2319` 주석).
  저장이 실패하면 재시작 후 해당 포지션이 `swing_positions.json` 폴백으로 판정되어
  **다른 청산 로직**이 적용될 수 있는데, 실패 사실이 로그 어디에도 남지 않았다.
- **수정**: `logger.error()` 추가. **제어 흐름은 그대로**(raise하지 않음) — 진입은 이미
  완료된 시점이라 예외를 올리면 오히려 상태가 더 꼬인다. 최소 변경 원칙 적용.
- **참고**: 같은 계열인 `_save_positions_state()`는 이미 atomic write + `.bak` 백업 +
  `logger.warning`까지 갖추고 있어 문제없음을 확인했다.

### V2-DC01 [High] 실거래 모듈의 다중 사본 존재 (수정 안 함 — 삭제 금지)

실제 실행 경로는 **단 하나**인데, 같은 역할의 미사용 구현이 다수 공존한다.

| 역할 | **실제 사용** | 휴면 사본 |
|---|---|---|
| 주문 실행 | `main_auto_trading.py` → `self.api.order_buy/sell` | `core/order_executor.py`, `trading/order_executor.py`, `brokers/kiwoom_broker.py` |
| 청산/손절 | `trading/exit_logic_optimized.OptimizedExitLogic` | `core/stop_loss_manager.py`, `core/auto_stop_loss_system.py`, `trading/stop_loss_executor.py`, `trading/trend_exit_engine.py` |
| 포지션 관리 | `main_auto_trading.self.positions` | `core/position_manager.py`, `trading/position_tracker.py`, `core/portfolio_manager.py` |
| 스케줄링 | cron | `core/scheduler.py` |

- **영향**: 운영자가 `core/auto_stop_loss_system.py`를 수정하고 "손절을 고쳤다"고 믿을 위험.
  실제로 이번 감사에서도 `gpt_share/`, `docs/share/`에 `exit_logic_optimized.py` 사본이
  추가로 존재함을 확인했다(BUG-02 수정 시 실사용 파일 식별에 시간 소요).
- **수정하지 않음**: 지시서 "삭제는 금지". FOLLOWUP에 기록.

### V2-SCH01/02 [Medium] 스케줄러 (수정 안 함)

- **16:05에 3개 작업 동시 실행**: `live_lcl_validation.py`, `operations_daily_summary`,
  `system_health_check --mode post`. → **무해 판정**: 셋 다 DB 쓰기 0건(읽기 전용)이고
  상호 의존이 없다.
- **gate_health_check 이중 실행**: 15:58 자체 크론 + 16:05 `operations_daily_summary`가
  `run_gate_health_check()`를 직접 import 호출. 같은 검사가 하루 2번 돌며 두 결과가
  다를 수 있다(그 사이 데이터 변동 시). 기능 장애는 아니나 혼선 소지.

---

## 3. Phase 4 (Race Condition) — 왜 "없음"으로 결론했는가

오탐을 피하기 위해 근거를 남긴다.

- `main_auto_trading.py`의 `threading` 사용: **0건** → 멀티스레드 경쟁 없음
- 동시 실행 가능 지점은 `asyncio.create_task()` 3곳:
  `rescan_and_add_stocks()`(L4003), `update_account_balance()`(L10910 매수 후, L13151 매도 후)
- 처음엔 `update_account_balance()`가 `self.current_cash`/`total_assets`를 쓰므로
  **Lost Update 후보**로 의심했으나, AST로 확인한 결과 **함수 내부에 `await`가 0개**다.
  asyncio 단일 이벤트루프에서 await 없는 코루틴은 **원자적으로 실행**되어 인터리빙이 불가능하다.
  → race 성립 안 함.
- 이 전제가 깨지는 것을 감지하려고 회귀 테스트를 남겼다:
  `test_update_account_balance_has_no_await_points` — 누군가 이 함수에 `await`를 추가하면
  즉시 실패하며 "동시 실행 가드를 추가하라"고 알린다.
- 기존 감사에서 이미 기록된 파일 기반 상태(`data/cooldown.lock`)의 멀티프로세스 동시쓰기
  위험은 여전히 유효하나 이번 범위 밖(`docs/STATE_AUDIT.md` 참조).

---

## 4. 수정 파일

| 파일 | 변경 이유 | 위험도 |
|---|---|---|
| `kiwoom_api.py` | order_buy/order_sell의 retry exceptions에서 `TradingTimeoutError` 제거 (중복 주문 방지) | **중** — 주문 경로 동작 변경. 타임아웃 시 재시도 없이 실패 처리됨(운영자 승인 완료) |
| `main_auto_trading.py` | positions_strategy.json 저장/로드 실패에 `logger.error` 추가 | **없음** — 로깅만 추가, 제어 흐름 불변 |
| `tests/unit/test_audit_v2_20260728.py` | 회귀 테스트 12건 신규 | 없음 |

---

## 5. 테스트

```
BEFORE : PASS 403 / FAIL 16 / XFAIL 0
AFTER  : PASS 415 / FAIL 16 / XFAIL 0
NEW FAIL : 0
신규 테스트 : 12건 (전부 PASS)
```

**Pre-existing Failure 16건** (이번 작업과 무관, 감사 이전부터 존재):
`tests/unit/test_swing_holding_manager.py` 8건 + `tests/unit/test_swing_runner_logic.py` 8건
— `holding_mgr.evaluate()` 반환값 언패킹 불일치.

**신규 테스트 구성**
| 대상 | 건수 | 검증 내용 |
|---|---|---|
| V2-CRIT01 | 8 | 주문 2종 타임아웃 재시도 없음 / 연결오류 재시도 유지(과잉수정 방지) / 조회 3종 타임아웃 재시도 유지(회귀방지) / Timeout→TradingTimeoutError 변환 경로 존재 |
| V2-SF01/02 | 3 | 저장·로드 실패 로그 존재, AST로 `except: pass` 회귀 차단 |
| P4 전제 고정 | 1 | `update_account_balance()`에 await 추가 시 실패 |

---

## 6. 운영 영향

| 항목 | 판정 |
|---|---|
| **매매 판단 변경** | **NO** — 진입/청산 조건, Score, MIN_SCORE, SMC/CHoCH/BOS, Regime, Drawdown, LCL, Position Sizing 전부 불변 |
| **전략 변경** | **NO** |
| **운영 안정성** | **개선** — 중복 주문(실계좌 자금 직접 영향) 경로 제거, 상태 복원 실패가 로그로 드러남 |
| **모니터링** | **개선** — 무음 실패 2건이 `[POS_STRATEGY_SAVE_FAIL]`/`[POS_STRATEGY_LOAD_FAIL]`로 관측 가능 |

**주문 경로 동작 변화 (유일한 실질 변경)**: 타임아웃 발생 시 이전에는 1회 재시도했으나
이제는 즉시 실패 처리된다. 진입 기회를 놓칠 수 있는 대신 중복 체결 위험이 사라진다.
운영자 승인 하에 적용했다.

---

## 7. 배포 상태 — ⚠️ 확인 필요

**수정분은 아직 실거래에 반영되지 않았다.**

- 실행 중 프로세스: PID 3196098 (08:45 기동) — 파일 수정은 이미 메모리에 로드된 프로세스에
  영향을 주지 않는다.
- watchdog 크론(08:45/09:00/09:15)은 전부 통과했고 세 번 모두 "정상 동작 중"으로
  재시작하지 않았다. **오늘 남은 자동 재시작 스케줄 없음** → 의도치 않은 반영 위험 없음.
- 반영 시점: 장 마감 후 수동 재시작 또는 내일 08:45 watchdog 기동 시.
  **재시작 여부/시점은 운영자 승인 사항.**

---

## 8. 완료 조건 체크

- [x] Silent Failure 전수 감사 완료 (467건 분류)
- [~] Strategy Reachability 분석 — **모듈 단위만 완료, 조건문 단위 미수행**(FOLLOWUP)
- [x] API Recovery 감사 완료
- [x] Race Condition 감사 완료 (근거 §3)
- [~] Memory/Resource 감사 — **정적 확인만, 프로파일링 미수행**(FOLLOWUP)
- [x] Scheduler 감사 완료
- [x] Logging 감사 완료
- [x] Dead Code 감사 완료
- [x] Regression Test 추가 (12건)
- [x] 전체 pytest 실행 (Before/After)
- [x] 운영 영향 평가 완료
- [x] 보고서 작성 완료
