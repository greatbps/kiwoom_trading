# State Management 감사

> 생성일: 2026-07-27 | 운영 안정성 감사 v3.0 — Audit 3

## 대상별 확인

| 상태 | 재시작 시 정상 복원 | 오래된 값 자동 제거 | 날짜 변경 처리 | Stale 위험 | Race Condition |
|---|---|---|---|---|---|
| RiskManager 쿨다운 | ✅(오늘 수정) | ✅ | ✅ | 해소됨 | 낮음(단일 프로세스) |
| DrawdownEngine | ✅(오늘 수정) | ✅ | ✅ | 해소됨 | 낮음 |
| Position(브로커기준) | ✅ | N/A | N/A | `_restore_positions_state()` 경로에 한해 존재(§SOURCE_OF_TRUTH_AUDIT) | 낮음 |
| Regime cache | N/A(60분 캐시, 파일없음) | ✅(캐시 자연만료) | N/A | 낮음 | 낮음 |
| Candidate cache(`self.watchlist` 등) | ❌ 복원 안 함(매일 새로 구성이 설계) | ✅ | N/A | 설계상 의도됨, 문제 아님 | 낮음 |
| Scheduler state(`daily_routine`) | ❌ **버그였음** | - | ❌ **버그였음** | ✅ 오늘 수정 | 낮음 |

## 핵심 발견 — `daily_routine()` 재시작 판별 부재 (수정 완료)

`main_auto_trading.py`의 `run()`은 `while self.running: await self.daily_routine()`로 하루 종일 도는 단일 함수 호출 구조다. `daily_routine()` 자체는 **순수 시계 기반**(`datetime.now()`가 08:50을 지났으면 바로 시작)이라 "오늘 이미 이 함수가 실행된 적 있는지"를 판별할 방법이 전혀 없었다.

**실제 발생 가능한 시나리오**: 장중 11시에 프로세스가 크래시/재시작(watchdog 등)되면, 새 프로세스의 `daily_routine()`이 다시 호출되어 다음을 포함한 전체 일일 리셋 블록(main_auto_trading.py 13297행~)이 재실행된다:
- `market_context.reset()`
- `self._daily_buy_count/_daily_pnl_pct/_daily_loss_halted` (이미 오늘 발생한 손실 기록이 지워짐)
- `stock_ban_list.clear()` (오늘 금지목록 리셋)
- `self.drawdown_engine.reset_daily()` (DrawdownEngine의 DANGER/HALT 상태가 지워짐 — Audit 1과 직결)
- 그 외 10여 개의 `_daily_*_count` 카운터 전부

즉 "오늘 이미 손실 한도에 걸렸다"는 사실 자체가 재시작 한 번으로 사라질 수 있었다.

**✅ 수정 완료**: `utils/daily_reset_marker.py`(신규, 단위테스트 가능하게 분리) — 마커 파일(`data/daily_routine_marker.txt`)에 오늘 날짜가 이미 기록되어 있으면 리셋 블록 전체를 건너뛰고 `[DAILY_RESET_SKIPPED]` 로그만 남긴 뒤 그대로 진행(WebSocket 연결 등 이후 흐름은 완전히 동일). 회귀테스트 `tests/unit/test_daily_routine_guard.py` 5건 추가.

## 그 외 확인 사항

- **Candidate/Watchlist 미복원은 버그가 아니라 설계**: `data/watchlist.json`은 write-only(외부 도구 전용)이고, 매일 조건검색+StockGravity로 새로 구성하는 게 원래 의도. `data/daily_watchlist.json`(스윙용, `backtest/daily_scan.py` 산출)은 `daily_routine()`마다 재로드되므로 재시작에 안전.
- **`_cycle_count`/`_entry_scan_cycles`(LOOP_LAG 완화용) 재시작 시 0으로 리셋** — 영향은 "재시작 직후 한두 사이클 더/덜 스캔"뿐, 안전 문제 없음. 조치 불필요로 판단.
- **Race Condition**: 이 프로세스는 asyncio 단일 이벤트루프 기반이라 상태 변수 자체의 동시성 경쟁은 낮음. 다만 `data/cooldown.lock` 같은 파일 기반 상태는 여러 프로세스(watchdog이 좀비로 오판해 중복 기동하는 경우 등)가 동시에 쓸 가능성이 이론적으로 있음 — 오늘 발견된 로그 유실 사고(§LOGGING_AUDIT)와 유사한 클래스의 위험으로, 파일 잠금(`fcntl` 등) 없이 단순 읽기-쓰기만 하고 있음. 지금까지 실제 사고 사례는 없었으나 잠재 위험으로 기록.

## 요약

| 항목 | 상태 |
|---|---|
| RiskManager/DrawdownEngine 재시작 복원 | ✅ 수정 완료 |
| daily_routine() 재시작 판별 | ✅ 수정 완료 |
| Position phantom 감지 | ✅ 수정 완료(관측만) |
| Watchlist/Candidate 미복원 | 설계 의도, 조치 불필요 |
| 파일 기반 상태(cooldown.lock 등) 동시쓰기 방어 | 잠재 위험, 후속 과제로 기록(범위 밖) |
