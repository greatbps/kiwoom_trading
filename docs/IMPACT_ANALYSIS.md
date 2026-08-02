# IMPACT_ANALYSIS.md — Critical Bug Fix v2.1 영향 범위 분석

수정 전 절차: 각 변수/함수의 참조(읽기/쓰기) 위치 전수 조사 → 동일 데이터를 사용하는
정책/DB기록/로그/대시보드/분석 코드까지 영향범위 문서화 → 회귀 테스트.

---

## 1. `drawdown_engine.can_enter()` / `equity_ctrl.can_enter()`

**수정 대상**: `main_auto_trading.py:5370-5397` `_check_global_risk_gates()`

**참조 위치 전수조사**:
- `can_enter()` 호출부: `_check_global_risk_gates()`(5382,5393, **수정함**), `drawdown_engine.can_enter(strategy="rs"/"def")`(6694,6753, **수정 안 함** — 근거는 아래)
- `_check_global_risk_gates()` 호출부(영향받는 곳): 5428(check_entry_signal), 8995(_flush_pending_signals), 11912(갭업 재진입) — 3곳 전부 `(bool, str)` 튜플을 그대로 언패킹해 진입 여부 판단, 반환 형식 변경 없어 호출부 수정 불필요

**영향받는 정책/DB/로그**: 없음 — 반환값이 (False, reason) 형태인 것은 기존과 동일, `reason` 문자열에 `_EXCEPTION` 태그만 추가됐을 뿐 DB 스키마/대시보드 파싱 로직에 영향 없음(전부 자유텍스트 로그용).

**추가 확인 — 두 번째 call site(RS/DEF 전략별 DD halt)는 왜 수정하지 않았나**: 라인 6694/6753은
try/except가 전혀 없이 직접 호출됨. `can_enter()`가 순수 인메모리 연산(파일/DB I/O 없음)이라
예외 발생 가능성이 극히 낮고, 만에 하나 예외가 나도 이 코드는 execute_buy 밖의 스캔루프 내부라
그 상위 stock 단위 `except Exception: error_logger.error(...); continue` 가드가 이미 존재 —
예외 시 해당 종목 처리를 건너뛸 뿐 "무음 허용"으로 흐르지 않음. Fail-Open 클래스가 아니므로
이번 작업 범위(같은 결함 유형 수정)에서 제외.

**equity_ctrl.can_enter() NaN 방어**: `trading/equity_controller.py:170-176`에 `equity != equity`
NaN 체크 추가 — `get_drawdown_mult()`/`get_size_mult()`(포지션 사이징 함수, 6703/6762/9454)는
NaN 입력 시 5-tier 분기가 우연히 EC_SURVIVAL(최저사이즈)로 떨어져 안전하게 동작함을 확인,
**수정 안 함**(Position Size 로직 변경 금지 원칙과 무관하게 이미 안전).

**회귀 테스트**: 정상 허용/정상 차단/DB오류 시뮬(Exception)/JSON손상 시뮬(Exception)/None/NaN/
peak=0 — 7개 시나리오 전부 `main_auto_trading.py:_check_global_risk_gates()`를 stub 인스턴스로
직접 호출해 실측 검증(위 CBF-1 작업 로그 참조). 정상 시나리오 결과 100% 동일 확인.

---

## 2. EQ-4/5/6 진입필터 + SMC Displacement 필터

**수정 대상**: `main_auto_trading.py:9266-9423`(execute_buy 내 3개 필터), `analyzers/smc/smc_signals.py:1255-1294`

**참조 위치**: 이 4개 필터는 전부 `execute_buy()`/`check_entry_signal()` 내부에 국한된 지역
로직 — 필터 통과/차단 결과가 DB나 다른 함수로 전파되는 유일한 경로는 각 필터의 `return`문이
`execute_buy()`/`check_entry_signal()`을 조기 종료시키는 것뿐. 외부에서 이 필터들의 중간
계산값을 직접 참조하는 코드 없음(grep 확인: `_sd_score`/`_bo_dist`/`_e20_dist`/
`details['displacement']`는 전부 각 함수 지역 변수 또는 SMC 함수의 반환 dict 내부에서만 소비).

**영향받는 정책/DB/로그**: `details['displacement']`는 `check_entry_signal()`의 반환값 3번째
요소(dict)에 담겨 호출부(main_auto_trading.py의 SMC 진입 분기)로 전달되지만, 기존에도
`{'passed': False, ...}` 형태였고 이번 수정도 동일 스키마(`{'passed': False, 'reasons': [...]}`)
유지 — 하위 소비 코드(로그 출력용 `details.get('displacement')`)에 영향 없음.

**회귀 테스트**: `tests/unit/test_smc_e2e.py` + `test_smc_replay.py` + `test_smc_synthetic.py`
82개 전체 재실행 → **82 passed, 0 failed** (기존 골든 스냅샷 전부 동일 유지 확인). 추가로
displacement 필터 6개 시나리오(정상/Exception강제/컬럼누락/NaN/None/빈DataFrame) 직접 검증.

---

## 3. `same_day_entry` 설정 경로

**수정 대상**: `trading/exit_logic_optimized.py:86` (`self.same_day_entry` 대입식 1줄)

**참조 위치 전수조사**: `self.same_day_entry`/`same_day_enabled`/`same_day_stop_loss_pct`/
`same_day_trailing_ratio` — **`trading/exit_logic_optimized.py` 파일 전체에서 대입 이후
어디에서도 참조되지 않음**(grep 확인, 4개 변수 전부 87~89번 줄에서 정의만 되고 소비처 없음).
즉 이 값들을 실제로 사용해 손절가/트레일링을 조정하는 로직 자체가 **live 파일에 존재하지 않음**
— `gpt_share/exit_logic_optimized.py:745-786,1131-1132`에는 존재하나 별개 사본(공유용 백업본
추정, live 코드 경로 아님).

**영향 범위**: 경로 수정으로 `self.same_day_enabled`가 이제 YAML 값(`True`)을 정확히 반영하게
됐으나, 이를 소비하는 로직이 없어 **실거래 Exit 결정에는 어떤 영향도 없음**(0% 영향 —
작업지시서의 "전략 영향 없음" 요건을 가장 확실하게 충족하는 형태). 향후 이 필드를 실제로
사용하는 로직을 추가하려면(gpt_share 버전 이식 등) 이는 "기능 추가"로 별도 작업지시가 필요.

**회귀 테스트**: ON/OFF/실제운영YAML 3개 시나리오로 `self.same_day_enabled` 값이 YAML을
정확히 반영하는지 확인 (수정 전: 항상 False / 수정 후: YAML대로 True). 하위 Exit 판단 결과에는
애초에 이 값이 관여하지 않으므로 별도 회귀 비교 대상 없음.

---

## 4. `stock_ban_list` / `_orphan_halt`

**수정 대상**: `main_auto_trading.py:13231-13240`(daily_routine 일일리셋 추가),
`main_auto_trading.py:8638-8664`(_check_orphan_positions self-healing 추가)

**참조 위치 전수조사**:
- `stock_ban_list`: 정의(696), 읽기(9106 — 진입후보 필터링), 쓰기(12637 — 연패/대손실 시
  추가), 이번에 리셋 추가(13235). 3개 전부 확인, 다른 파일에서 참조 없음(grep 확인).
- `_orphan_halt`: 읽기(5360 — `_check_global_risk_gates()` 진입 전 최상단 가드), 쓰기(8669 —
  고아 감지 시 True), 이번에 self-healing 2곳(8651,8664) + 일일 방어 리셋(13240) 추가.

**영향받는 정책/DB/로그**: 둘 다 순수 인메모리 상태(DB/파일 미저장) — 대시보드/분석 코드가
직접 참조하는 경로 없음(로그 태그만 신규: `[BAN_LIST_RESET_DAILY]`, `[ORPHAN_HALT_RECOVERED]`,
`[ORPHAN_HALT_RESET_DAILY]` — 기존 `[ORPHAN_HALT]`/`[ORPHAN_CHECK]` 로그와 별도 태그라 기존
로그 기반 집계에 영향 없음).

**회귀 테스트**: 정상종료(일일리셋)/비정상종료(재시작 후 클린상태)/중복등록(set dedup)/
self-healing(고아 0개·정상 시 즉시해제, 여전히 고아 존재 시 halt 유지하는 오탐방지 포함) —
6개 시나리오 전부 stub 인스턴스 직접 호출로 검증.

---

## 5. 루트 로거 핸들러 (Logging)

**수정 대상**: `main_auto_trading.py:109-119`

**참조 위치**: 이 로거 설정은 프로세스당 1회, 모듈 최상단에서만 실행됨. `logger = getLogger('auto_trading')`(main_auto_trading.py 전역 사용)뿐 아니라 `services.decision_service`,
`database.decision_trace` 등 자체 핸들러가 없는 모든 모듈 로거가 root로 propagate — 전부
동일하게 영향받음(중복 해소 대상도 이만큼 넓음).

**영향받는 정책/DB/로그**: 로그 파일 내용(문자열)에는 변화 없음 — 단지 "몇 번 물리적으로
기록되는가"만 바뀜(2회→isatty 여부에 따라 1회 또는 2회). DB/대시보드는 로그 파일을 파싱하지
않으므로 무관. `system_health_check.py`의 로깅중복감지 항목과 `analysis/*` 스크립트들의
grep 기반 카운트 집계가 이번 수정 이후 실제 이벤트 수에 더 가깝게(정상환경 기준 1회) 나오게
됨 — 이는 관측 정확도 개선이지 회귀 위험 아님.

**회귀 테스트**: isatty() 분기 로직 자체를 격리 단위테스트(TTY→2핸들러, 리다이렉트→1핸들러)로
검증. 실제 운영환경(watchdog/nohup 방식 재시작) 기준 "동일 이벤트 1회 출력" 최종 확인은
전체 CBF 수정 완료 후 1회 재시작으로 통합 검증 예정(불필요한 라이브 재시작 반복 방지).
