# Source of Truth 전수 감사

> 생성일: 2026-07-27 | 운영 안정성 감사 v3.0 — Audit 1
> 원칙: 이 문서는 진단 결과다. 코드 수정은 별도로 승인된 3건(§7 표에 ✅ 수정완료 표기)만 반영됐다.

## 1. RiskManager (`core/risk_manager.py`)

- **Source of Truth**: `data/risk_log.json` (파일). `consecutive_losses`/`cooldown_until`/`daily_trades`/`daily_realized_pnl` 전부 이 파일 하나.
- 중복 저장: 없음(이 개념 자체는 단일 소스).
- ✅ **수정 완료(오늘 세션 초반)**: `load()`가 `cooldown_until` 만료 여부와 무관하게 `consecutive_losses`를 무기한 복원하던 버그. 만료 시 0/None으로 리셋하도록 수정, 회귀테스트(`tests/unit/test_risk_manager.py`) 추가.
- 파일 기반 보조 장치 `data/cooldown.lock` — 프로세스 간 공유용 별도 파일. `risk_log.json`과 별개 파일이지만 같은 개념(연속손실 쿨다운)을 다른 형태로 한 번 더 기록 — 엄밀히는 "이중 저장"이나, 의도된 설계(멀티프로세스 동기화용)로 보임.

## 2. DrawdownEngine (`core/drawdown_engine.py`)

- **기존 Source of Truth**: 없음(메모리만). **재시작 시 DANGER/HALT 상태가 NORMAL로 초기화되는 실질적 버그였음.**
- ✅ **수정 완료(오늘)**: `data/drawdown_state.json`에 영속화 추가(`trading/equity_controller.py`의 원자적 쓰기 패턴 재사용). 오늘 날짜와 일치할 때만 복원, 다른 날짜면 기본값 — `EquityController`가 이미 갖고 있던 안전장치와 동일한 사상.
- 회귀테스트: 기존 `tests/unit/test_drawdown_engine.py`에 재시작 복원 케이스 추가.

## 3. EquityController (`trading/equity_controller.py`)

- **Source of Truth**: `data/equity_state.json` (peak만 영속화, DD%는 매번 재계산).
- 4단계 복구(Primary→Backup→DB재구성→fail-closed) 이미 구현되어 있고, 2026-06-10 "peak 오염" 사고 이후 `_account_data_reliable` 가드가 두 호출부(main_auto_trading.py:2693, 11754)에 모두 적용되어 있음을 확인. **문제 없음 — 참고 모범사례로 활용.**

## 4. Position 상태

- **주 Source of Truth**: 브로커(Kiwoom `get_account_info`) — `initialize_account()`가 매 프로세스 시작 시 브로커 보유 종목 기준으로 `self.positions`를 구성. 로컬 JSON(`positions_strategy.json` 등)은 메타데이터(전략태그/트레일링가 등)만 병합 — 이 경로는 정상.
- **결함 발견**: `_restore_positions_state()`(별도 경로)가 `data/positions_state.json`에서 브로커 대조 없이 항목을 복원 가능 — 실제로 매도된 종목이 stale 파일에 남아있으면 "유령 포지션"으로 되살아날 수 있음.
- `_check_orphan_positions()`는 기존에 "브로커에 있는데 엔진엔 없음"(orphan) 방향만 감지, 반대 방향(엔진엔 있는데 브로커엔 없음=phantom)은 감지 못했음.
- ✅ **수정 완료(오늘)**: `_check_orphan_positions()`에 `phantom_codes` 양방향 확인 추가 — 발견 시 `[PHANTOM_POSITION]` critical 로그+콘솔 경고만(자동매도 등 행동 없음, 관측 우선).
- `_restore_positions_state()` 자체의 브로커 대조 로직 보강은 이번 승인 범위 밖(더 큰 변경) — §7에 후속 과제로 기록.

## 5. 일일 손실 한도 (Daily Loss Limit) — 3중 독립 구현 확인

| 구현체 | 변수 | Config 키 | 영속화 |
|---|---|---|---|
| `core/risk_manager.py` | `daily_realized_pnl`, `HARD_MAX_DAILY_LOSS_PCT` | `risk_management.daily_max_loss_pct` | `risk_log.json` |
| `analyzers/signal_orchestrator.py:176-211` | 매개변수로 받은 `daily_pnl`/`current_cash` | `risk_control.max_daily_loss_pct` (기본 3.0) | 없음(호출 시점 값) |
| `main_auto_trading.py` 자체 | `self._daily_pnl_pct`, `self._daily_loss_halted` | `_dr_cfg`(risk_control.daily_risk) | **없음, `daily_routine()`마다 리셋** |

세 곳 모두 서로 다른 threshold/변수를 독립적으로 관리 — 값이 어긋나도 서로 알 방법이 없다. `main_auto_trading.py` 자체 트래커는 ✅ 오늘 수정한 재시작 가드(§Audit3)로 "장중 재시작 시 근거없이 리셋"되는 문제는 해결됐지만, **3개 구현이 통합되지 않은 구조적 중복 자체는 이번 승인 범위 밖** — 후속 과제.

## 6. Candidate 개념 — 3개 구조

- `research.candidates`(Postgres) — Decision Ledger 계열의 공식 기록.
- `signal_orchestrator.recent_accepts`/`recent_accepts_first`(메모리) — diagnostic_mode 등에서 사용.
- `signal_orchestrator.pre_candidates`(메모리, `{symbol: (datetime, price, date)}`) — L3 통과 시점 스냅샷, 위 두 개와 별개.
- `data/watchlist.json`은 후보라기보다 모니터링 대상 스냅샷(외부 도구 전용, 라이브 프로세스가 재읽지 않음) — 관련은 있으나 동일 개념은 아님.
- 문제라기보다 "레이어가 다른 캐시"에 가까움 — 통합 필요성은 낮음, 다만 문서화 부재 상태였다는 점만 기록.

## 7. Dead Code

| 대상 | 확인 |
|---|---|
| `core/market_monitor.py`의 `MarketMonitor` 클래스 전체 | `main_auto_trading.py` 어디서도 import/인스턴스화 안 됨 — 완전 대체됨(watchlist.json 재로딩 기능도 포함하고 있었으나 미사용). **삭제 후보, 이번엔 삭제하지 않음(범위 밖)**. |

## 최종 요약 표

| 항목 | 상태 | 조치 |
|---|---|---|
| RiskManager 쿨다운 무기한복원 | 버그 | ✅ 수정완료(오늘 세션 초반) |
| DrawdownEngine 재시작 상태소실 | 버그 | ✅ 수정완료 |
| Position 유령포지션(엔진→브로커 방향 미감지) | 버그 | ✅ 수정완료(관측만) |
| `_restore_positions_state()` 브로커 미대조 | 잠재 버그 | 후속 과제(범위 밖) |
| 일일손실한도 3중 독립구현 | 구조적 중복 | 후속 과제(범위 밖, 통합은 리팩토링 성격) |
| Candidate 3중 구조 | 설계상 레이어 분리 | 문서화만, 조치 불필요 판단 |
| `MarketMonitor` dead code | 죽은 코드 | 삭제 후보로 기록, 미삭제 |
