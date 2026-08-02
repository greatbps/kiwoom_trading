# Critical Bug Fix v2.1 — Fail-Open 제거 및 안정성 강화 (2026-07-20)

Business Logic Audit v2.0에서 발견된 Critical 5건에 대한 구현 결함 수정. 전략(Entry/Exit/
Score/Regime/Position Size) 변경 없이 예외처리만 Fail Closed로 전환.

## 산출물 표

| 작업 | 결과 | 전략 영향 | 회귀 테스트 | 비고 |
|---|---|---|---|---|
| DD_HALT / EC_HALT | PASS | None | PASS | `_check_global_risk_gates()` fail-open→Fail Closed. equity_controller.py NaN 방어 추가 발견·수정 |
| EQ-4/5/6 + SMC Filter | PASS | None | PASS | 4개 필터 전부 Fail Closed. SMC 관련 82개 기존 테스트 전체 통과 |
| same_day_entry | PASS | None | PASS | 경로만 수정(trailing.same_day_entry). 하위 소비로직 자체가 없어 영향 0% |
| stock_ban_list / orphan_halt | PASS | None | PASS | 일일리셋 + self-healing 추가. 오탐(진짜 고아 유지) 방지 확인 |
| Logger Handler | PASS | None | PASS | 재시작 후 실측: 중복 0건(수정 전 12.5%) |

부록: `docs/FAIL_OPEN_AUDIT.md`, `docs/IMPACT_ANALYSIS.md`

## 작업별 요약

### 작업 1 — DD_HALT/EC_HALT Fail-Open 제거
`main_auto_trading.py:5370-5397`의 `except Exception: pass`를 `logger.exception()` +
`return False, "[DD_HALT_EXCEPTION]"/"​[EC_HALT_EXCEPTION]"`으로 전환. 추가로
`trading/equity_controller.py:can_enter()`에서 NaN 입력이 예외 없이 통과되는 별도 버그를
발견·수정(equity!=equity 체크 추가). peak≤0("no_peak"→허용)은 기존부터의 설계된 동작이라
그대로 유지, 별도 논의 필요 항목으로 문서에 기록만 함.
**검증**: 정상허용/정상차단/DB오류(Exception)/JSON손상(Exception)/None/NaN — 7개 시나리오
실측, `_check_global_risk_gates()`를 stub 인스턴스로 직접 호출해 확인.

### 작업 2 — EQ-4/5/6 + SMC Displacement Fail-Open 제거
`main_auto_trading.py`의 EQ-4(수급급락)/EQ-5(추격진입)/EQ-6(레짐) 3개 필터와
`analyzers/smc/smc_signals.py`의 displacement(가짜 CHoCH 방지) 필터, 총 4곳을
`FILTER_EXCEPTION` 로그 + BLOCK(진입 차단)으로 전환.
**검증**: 정상/None/NaN/컬럼누락/빈DataFrame/Exception — 6개 시나리오 실측(실제 fixture
데이터 + mock 조합). SMC 관련 기존 테스트 82개(test_smc_e2e/replay/synthetic) 전체 재실행,
전부 통과 확인.

### 작업 3 — same_day_entry 설정 경로 수정
`trading/exit_logic_optimized.py`가 `risk_control.same_day_entry`(YAML에 없음)를 읽던 것을
`trailing.same_day_entry`(실제 위치)로 수정. **중요 발견**: 이 값을 실제로 소비하는 로직이
live 파일에 전혀 없음(정의만 되고 사용되는 곳이 없음, `gpt_share/` 사본에만 존재) — 경로를
고쳐도 현재는 실거래 Exit 판단에 어떤 영향도 없음(0% 영향, "전략영향 없음" 요건을 가장
확실하게 충족). 실제 강화손절 기능을 살리려면 별도의 "기능 추가" 작업지시가 필요.
**검증**: ON/OFF/실제운영YAML 3개 시나리오로 값 반영 확인.

### 작업 4 — stock_ban_list/_orphan_halt 복구
`daily_routine()`의 일일 리셋 블록에 두 상태 모두 추가(정상적인 하루 경계 해제).
`_check_orphan_positions()`의 "정상" 판정 2개 분기(브로커 보유 0개 / 고아 없음)에
self-healing 로직 추가 — 프로세스 재시작을 기다리지 않고 다음 정기 점검에서 즉시 해제.
**검증**: 정상종료(일일리셋)/비정상종료(재시작 후 클린상태)/중복등록(set dedup)/self-healing
(고아 해소 시 즉시해제, 여전히 고아면 halt 유지하는 오탐방지 포함) — 6개 시나리오 실측.

### 작업 5 — Logger Handler 중복 제거
루트 로거가 FileHandler + StreamHandler(stderr) 두 개를 항상 등록하던 것을,
`sys.stderr.isatty()`로 분기해 리다이렉트된 운영환경(watchdog/cron/nohup)에서는
FileHandler 1개만 등록하도록 수정. 대화형 터미널 실행 시에는 기존처럼 2개 유지(콘솔
가시성 보존).
**검증**: 분기 로직 단위테스트 + **실제 재시작 후 실측** — 재시작 이후 로그 168줄 중
완전동일 연속중복 **0건**(수정 전 오늘자 로그 12,653줄 중 1,577줄=12.5%가 중복이었음).

## 추가 감사 결과

- **Fail-Open Search** (`docs/FAIL_OPEN_AUDIT.md`): 이번 작업 범위 5건(Critical) + 2건(High,
  영구상태) 전부 수정 완료. MAJOR 12건(관측성 저하, order_executor/param_tuner/edt_sizer/
  regime_detector/rae_detector/exit_logic_optimized 등)은 실거래 판단 영향이 낮아 이번 범위
  밖으로 분류, 미수정.
- **Shared Variable Audit + Impact Analysis** (`docs/IMPACT_ANALYSIS.md`): 5개 수정 대상
  전부 참조위치 전수조사 완료. 특히 `drawdown_engine.can_enter(strategy=...)`가
  `_check_global_risk_gates()` 외에 2곳(RS/DEF 전략) 더 있음을 확인 — 이들은 try/except
  자체가 없어(예외 시 상위 종목단위 가드로 안전하게 스킵) 같은 결함 유형이 아니므로 수정 안 함.

## 운영 중 발견/처리한 부수 사항

검증 과정에서 격리 테스트 스크립트가 `main_auto_trading.py`를 import하면서 실제 `.env`
(load_dotenv) 및 오늘자 실제 운영 로그파일에 연결되는 부작용을 확인 — orphan_halt 자동복구
음성/양성 시나리오 테스트 중 실제 텔레그램으로 가짜 "ORPHAN HALT" 알림이 발송됐을 가능성이
있어 사용자에게 즉시 고지함(실제 시스템 이상 아님, 테스트 부작용). 향후 유사 격리 테스트 시
텔레그램/네트워크 호출을 명시적으로 mock 처리하기로 함.

## 회귀 검증 총괄

- `py_compile`: 5개 수정 파일 + 1개 테스트 파일 전체 통과
- 기존 pytest 회귀 스위트: SMC(82) + Signal Injection(12) + Exit Scenario(20) + Exit Logic(30) +
  Position Sizing×2(75) + Decision Service(25) + Trade Decision Audit(89) + Gate Health
  Check(10) + Execute Buy Dry Run(3) = **320개 전체 통과** (수정 과정에서 mock 설정 미비로
  1건 실패 발견 → 실제 원인은 테스트 fixture 누락이었음을 확인 후 fixture 보완, 최종 320
  전체 통과)
- 실거래 프로세스 재시작(2026-07-20 16:02, 장마감 이후 안전한 시점): 정상 기동, 에러/
  트레이스백 0건, 로그 중복 0건 확인

## 성공 기준 충족 여부

1. ✅ 모든 Risk Gate와 Entry Filter가 예외 발생 시 Fail Closed 정책을 따름
2. ✅ 설정 오류(same_day_entry 경로)/영구 상태 유지(ban_list/orphan_halt)/로그 중복 제거
3. ✅ 수정 전후 Entry/Exit/Score/Regime/Position Size 로직 자체는 무변경 — 예외 발생 시의
   "허용→차단" 방향 전환만 있었고, 정상 경로의 결과값은 320개 회귀 테스트로 동일성 확인
4. ✅ 영향 범위 분석(IMPACT_ANALYSIS.md) 및 회귀 테스트 완료, 동일 유형 재발 방지를 위한
   부록 문서(FAIL_OPEN_AUDIT.md) 작성

---

## 변경 이력

| 날짜 | 분류 | 내용 |
|---|---|---|
| 2026-07-20 | A | Critical Bug Fix v2.1 — DD_HALT/EC_HALT/EQ-4·5·6/SMC displacement Fail Closed 전환, same_day_entry 경로수정, stock_ban_list/orphan_halt 복구, 로거 핸들러 중복제거. 320개 회귀테스트 통과, 실거래 재시작 검증 완료. |
