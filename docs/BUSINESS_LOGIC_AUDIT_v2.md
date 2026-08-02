# Business Logic Audit v2.0 — 전수 코드 감리 (2026-07-20)

## 배경 및 목적

지난 한 달간 EC_HALT 계산오류/Regime 기록누락/SCORE_ENGINE None전달/daily_scan 미실행/
Exit DB선기록/Health Check 오탐/Logging 중복/`_last_regime` 상태버그/Dead Path/Scheduler
누락 등 다수의 구현 결함이 발견됐다. 대부분 전략 문제가 아니라 구현(Implementation) 문제였다.

이번 감사의 목적은 **전략 개선이 아니라 전체 비즈니스 로직에 잠복한 결함의 체계적 발견/제거**다.
Market Data → Daily Scan → Watchlist → SCORE_ENGINE → Candidate → Global Gate → Entry →
Holding → Exit → Broker → DB → Dashboard 전 구간을 15개 항목으로 나눠 검증했다.

**방법**: 정적 코드 검색이 필요한 5개 항목(Audit-1,2,3,4,5,6 중 일부 결합)은 병렬 Explore
에이전트에 위임, 실행증거(로그/DB/프로세스)가 필요한 나머지는 직접 검증.

---

## 종합 결과표

| Audit | 결과 | 심각도 | 코드 위치 | 영향 범위 | 수정 필요 | 분류 |
|---|---|---|---|---|---|---|
| 1. Data Flow — choch_grade 신구 게이트 충돌 | **FAIL→FIXED** | Critical | main_auto_trading.py:11469(구)/10717/10904 | force_close_overnight/_maybe_upgrade_grade/_maybe_pyramid_add가 오늘자 choch_grade 동결수정 여파로 모든 신규 포지션을 B급으로 오판 | **YES — 당일 수정완료** | A |
| 1. Data Flow — 그 외 (SCORE_ENGINE/insert_trade/None전파) | PASS | - | - | 기존 수정사항 정상 유지 확인 | NO | - |
| 2. None Propagation | PASS | - | - | 전수 검색 결과 SCORE_ENGINE류 anti-pattern 추가 발견 없음 | NO | - |
| 3. Config — `same_day_entry` 잘못된 섹션 경로 | **FAIL** | Critical | trading/exit_logic_optimized.py:84 (risk_control.same_day_entry, 실제 YAML은 trailing.same_day_entry) | 당일진입 강화손절(-1.5%/0.8%) 기능이 완전히 죽어있음, 대체 코드도 없음 | YES | A |
| 3. Config — `vwap` 섹션 YAML 미정의 | FAIL | Major | main_auto_trading.py 5곳 + signal_detector.py + pre_trade_validator.py | VWAP rolling_window(20)/use_rolling(True) 하드코딩, YAML로 조정 불가 | YES | C |
| 3. Config — `risk_control.db_hard_stop` YAML 미정의 | FAIL | Major | main_auto_trading.py:1100 | 실거래 서킷브레이커가 YAML 조정 불가한 하드코딩 값(24h/5회/-5%/2건)으로만 동작 | YES | C |
| 3. Config — dead YAML 키 (`risk_management.*`, `no_move_exit`) | FAIL | Minor | config/strategy_hybrid.yaml | 죽은 설정, 혼란 유발 가능 | YES | C |
| 4/11. Dead Code — EXPERIMENT 브랜치 | PASS(의도적) | - | main_auto_trading.py:7089 | `experiment.enabled:false`로 영구 차단, Hard Stop 사고 이후 의도적 kill-switch — 정상 | NO | - |
| 4/11. Legacy — 5개 모듈 호출처 0건 | 후보 | Minor | trading/reentry_strategy.py 등 5개 | archive/ 이동 후보, 확인 필요 | NEEDS VERIFICATION | C |
| 4/11. Legacy — 루트 .backup/.bak 파일 4개 | 후보 | Minor | main_auto_trading.py.backup* 등 | archive/ 미이동 백업 파일 | NEEDS VERIFICATION | C |
| 5. Silent Failure — `_check_global_risk_gates()` DD/EC_HALT fail-open | **FAIL** | **Critical** | main_auto_trading.py:5367-5384 | 예외 발생 시 DD_HALT/EC_HALT 서킷브레이커가 무음으로 우회되어 `return True,"OK"`로 진행 — 오늘 equity_controller 내부수정과 별개로 호출부 자체가 fail-open | YES | A |
| 5. Silent Failure — EQ-4/5/6 진입필터(SD/EMA20/Regime) 무음우회 | FAIL | Critical | main_auto_trading.py:9266-9416 | 예외 시 수급급락/추격진입/CHOP레짐 차단 필터가 조용히 통과 처리 | YES | A |
| 5. Silent Failure — SMC displacement 필터 무음우회 | FAIL | Critical | analyzers/smc/smc_signals.py:1255-1290 | 가짜 CHoCH 방지 필터가 예외 시 조용히 통과 | YES | A |
| 5. Silent Failure — Major 15건 이상 | FAIL | Major | order_executor/param_tuner/edt_sizer/regime_detector/rae_detector/exit_logic_optimized 등 | 관측성 저하, 직접적 오판단 위험은 낮음 | YES | B/C |
| 5. Silent Failure — Minor 40여건 | PASS(수용) | Minor | console/UI/캐시/텔레그램 경로 | 실거래 영향 없음 | NO | - |
| 6. State Machine — `stock_ban_list` 영구 미해제 | **FAIL** | **High** | main_auto_trading.py:687,9085,12599 | "당일 진입 금지"가 프로세스 재시작 전까지 영구화 — 재시작 없이 장기실행 시 거래가능 종목군 지속 축소 | YES | A |
| 6. State Machine — `_orphan_halt` 영구 미해제 | **FAIL** | **High** | main_auto_trading.py:5351,8648 | orphan 감지 후 True로 설정되고 다시 False로 되돌리는 코드가 전무 — 한 번 발동하면 재시작 전까지 전체 신규진입 영구차단 | YES | A |
| 6. State Machine — Drawdown 이중시스템(drawdown_engine/equity_controller) 개념 중첩 | 확인 | Medium | main_auto_trading.py:5370-5385,9423-9443 | 서로 다른 기준(당일 실현손익 vs 전고점대비)의 두 감산배수가 동시적용, 상호작용 문서화 없음 | 검토필요 | C |
| 6. State Machine — Regime/Holding/Cooldown | PASS | - | - | 전이 로직 정상 확인 | NO | - |
| 7. Scheduler — `cd` 접두어 누락으로 최소 2주간 실제 미실행 (lcl_validation, returns_collector) | **FAIL(과거), 현재 수정됨** | Critical(이력) | crontab(이력) | 07-04~07-17 매일 조용히 실패, 홈디렉토리 엉뚱한 경로에 에러로그만 쌓임, 담당자 인지 못함 | 이미 수정됨(오늘 16:05 최초 검증 대기) | A(완료) |
| 7. Scheduler — 나머지 21개 크론 | PASS | - | - | 등록/실행/로그/결과생성 전부 확인 | NO | - |
| 8. DB Integrity — decision_ledger FROZEN 100% (오늘) | PASS(정상) | - | - | REGIME_BLOCK 100% 반영, decision_health_check 자체 검증 "HEALTHY" 확인 | NO | - |
| 8. DB Integrity — Broker/Trade/Ledger | PASS | - | - | BUY/SELL 건수 일치, Orphan 0건 | NO | - |
| 9. Logging — 루트 로거 이중 핸들러(FileHandler+StreamHandler stderr) | **FAIL** | **Critical(관측성)** | main_auto_trading.py:111-116 | 운영환경에서 stdout/stderr가 동일 로그파일로 리다이렉트되어 전파(propagate)되는 모든 logger.info 호출이 물리적으로 2회씩 기록 — 오늘자 로그 12,653줄 중 1,577줄(약12.5%)이 완전동일 중복 확인. 이번달 내내 관측된 "grep count 부풀림" 현상의 실제 주된 원인 | YES | B |
| 9. Logging — signal_orchestrator 이중출력 | PASS(오늘수정) | - | - | propagate=False 적용 후 재발 0건 확인 | NO | - |
| 10. Race Condition — `positions_strategy.json` 동시쓰기 | **FAIL** | Major | main_auto_trading.py:10445-10451, swing_executor.py:53-57 | 락/원자적쓰기 없는 동일 패턴으로 두 프로세스가 09:00:00 정각에 동시 write 가능 — read-modify-write 경합 시 한쪽 갱신 유실 위험 | YES | B |
| 10. Race Condition — 그 외 | PASS | - | - | 09:00 전후 실행순서 자체는 정상 | NO | - |
| 12. Regression — SCORE_ENGINE/daily_scan/signal_orchestrator | PASS | - | - | 오늘자 로그로 재발 0건 확인 | NO | - |
| 12. Regression — market_regime 실거래 기록 | 검증대기 | - | - | 오늘 거래 0건(레짐 차단)이라 실거래로 미검증, 다음 실거래 시 확인 필요 | 대기 | - |
| 13. Decision Consistency | PASS | - | - | 유일한 비결정성은 EXPLORATION 10% 확률 override — 의도적, 로그로 추적가능 | NO | - |
| 14. Memory & Resource | PASS(스냅샷) | - | - | RSS 390MB/스레드12/FD30/DB커넥션2 — 현재 스냅샷상 이상 없음. 로거 핸들러 중복추가 없음(가드 확인). 장기추세 비교는 별도 관측 필요 | 관찰지속 | - |
| 15. Business Rule — CLAUDE.md 15항 vs 실제 config 불일치 | Minor | Minor | CLAUDE.md 15항(2026-05-07 기준) vs config/strategy_hybrid.yaml | Trend Breakout 등 문서상 "활성"이 현재 config는 비활성 — 문서 자체가 스냅샷 표기라 오판아님, 최신화 필요 | 권고 | C |

---

## 오늘 즉시 수정 완료 (A등급, 회귀검증 포함)

### choch_grade 신구 게이트 충돌 (오늘 세션 자체 회귀)

**발견 경위**: 오늘 오전 "choch_grade/gate_reason 기록" 작업에서 `should_allow_overnight()`
(현재 `eod_policy.enabled=false`로 동결된 게이트)가 깨어나지 않도록 `positions[code]['choch_grade']`
키를 의도적으로 비워두고 `choch_grade_actual`에만 실제값을 저장했다. 그런데 이 감사에서
**같은 키를 읽는 별개의 LIVE 게이트 2곳**(`force_close_overnight()`의 14:50 오버나이트 차단
로직, `_maybe_upgrade_grade()`의 등급승격 로직)이 있다는 것을 놓쳤다는 게 확인됨 — 이 두 곳은
`pos.get('choch_grade', 'B')`로 직접 읽어서, 모든 신규 포지션이 실제 등급과 무관하게 강제로
B급 취급되고 있었다.

**실제 영향**: A급 CHoCH로 진입한 스윙 포지션이 소폭 손실(-1.5%~-3%) 상태로 14:50을 맞으면,
원래는 정상적으로 오버나이트 보유돼야 하는데 B급으로 오판되어 강제청산됐을 것. 다행히 오늘
해당 시간대(14:50)에 보유 포지션이 0개였어서 실제 피해는 없었음(로그에 OVERNIGHT_CLOSE 발생
이력 없음 확인).

**수정**: `force_close_overnight()`/`_maybe_upgrade_grade()`/`_maybe_pyramid_add()` 3곳에서
`choch_grade`(승격값 우선) → `choch_grade_actual`(진입시 실제값) → 없으면 'B' 순으로 읽도록
수정. `should_allow_overnight()`는 그대로 `choch_grade`만 읽어 동결 유지.

**회귀검증**: py_compile 통과, 4개 시나리오(A급 신규/B급→승격/EXPLORATION무등급/B급 신규)
단위 검증 통과, `should_allow_overnight()` 코드 미변경 확인.

---

## Critical — 즉시 검토 필요 (A/B 등급, 미수정 — 사용자 판단 대기)

1. **`_check_global_risk_gates()`의 DD_HALT/EC_HALT fail-open** (main_auto_trading.py:5367-5384):
   `drawdown_engine.can_enter()`/`equity_ctrl.can_enter()` 호출이 `except Exception: pass`로
   감싸져 있어, 내부에서 예외가 나면 서킷브레이커 체크 자체가 무음으로 스킵되고 함수는
   `return True, "OK"`로 빠져 매매를 허용한다. 오늘 equity_controller.py 내부를 견고하게
   고쳤지만, 이 호출부 래퍼 자체가 같은 계열의 fail-open 문제를 갖고 있었다.
2. **`same_day_entry` 설정 경로 오류**: `trading/exit_logic_optimized.py`가
   `risk_control.same_day_entry`를 읽는데 YAML은 `trailing.same_day_entry`에 정의돼 있어
   당일진입 강화손절 기능이 처음부터 작동한 적이 없음.
3. **EQ-4/5/6 진입필터 무음우회** 및 **SMC displacement 필터 무음우회**: 예외 시 필터가
   막아야 할 진입을 조용히 통과시킴.
4. **`stock_ban_list`/`_orphan_halt` 영구 상태 미해제**: 프로세스 재시작 전까지 각각
   거래가능 종목군 축소/전체 신규진입 영구차단이 누적될 수 있음.
5. **루트 로거 이중 핸들러로 인한 전체 로그 ~12.5% 중복**: 이번 달 내내 여러 로그 집계에서
   본 "카운트가 2배로 부풀려짐" 현상의 실제 시스템 차원 원인.

이 5가지는 모두 **Category A(즉시수정 가능, Bug Fix)** 로 분류되나, 오늘 이미 상당한 분량의
코드를 수정한 상태라 **한 번에 더 밀어붙이기보다 사용자 확인 후 우선순위를 정해 순차 진행**을
권고한다. Race Condition(positions_strategy.json)과 Config 관련 3건은 B/C로 분류, 별도 승인
시 진행.

---

## 변경 이력

| 날짜 | 분류 | 내용 |
|---|---|---|
| 2026-07-20 | A | Business Logic Audit v2.0 — 15개 항목 전수감리, choch_grade 신구게이트 충돌(오늘자 회귀) 발견 즉시수정+회귀검증. 그 외 Critical 5건(gate fail-open/same_day_entry설정오류/EQ필터무음우회/stock_ban_list·_orphan_halt 영구상태/로그이중출력)은 분류만 완료, 수정은 사용자 승인 대기. |
