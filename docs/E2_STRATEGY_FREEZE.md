# v1.4.x Strategy Freeze & E2 실거래 검증 (2026-07-20 ~)

## 배경

이번 주 System Audit(`docs/SYSTEM_AUDIT_2026-07-20.md`)로 EC_HALT/Regime DB기록/SCORE_ENGINE 입력/
daily_scan 미실행 등 구조적 결함을 발견·수정했고, System Health Check(읽기전용 자동감시)를 구축했다.
시스템이 "설계대로 동작하는가"에 대한 검증은 대부분 끝났으므로, 이제부터는 "전략 자체가 성과를
내는가"를 실거래 데이터로 검증하는 단계(E2)로 전환한다.

## Strategy Freeze — 변경 금지 항목

E2 검증(최소 100건 실거래 표본 확보)이 끝날 때까지 아래는 **변경 금지**:

- **Entry**: SMC/CHoCH/BOS/OB/Liquidity Sweep/EMA/RSI/ATR/Volume 조건, 모든 Entry Filter
- **Exit**: Trailing/ATR/Stop Loss/Profit Lock/Time Exit/Partial Exit
- **Regime**: Score 계산/Threshold/TREND_UP·NEUTRAL·RISK_OFF 판정
- **Risk**: Position Size/Daily Loss/Drawdown/EC_HALT
- **SCORE_ENGINE**: Weight/Cutoff/Ranking
- **Candidate Pipeline**: PRE_CANDIDATE/SIGNAL_PIPELINE/ACCEPT 로직

## 허용되는 수정 (분류 A/B/C)

| 분류 | 내용 | 예시 |
|---|---|---|
| **A. Bug Fix** | None 전달/DB 기록 오류/Logger 오류/Scheduler 오류/Exception 처리/Crash/Memory Leak/Race Condition | 이번 주 고친 EC_HALT/Regime파싱/SCORE_ENGINE 입력 버그 |
| **B. Observability** | 로그/대시보드/통계/리포트/Health Check 추가 | System Health Check, E2 Trade Analytics |
| **C. Infrastructure** | 위 A/B를 지원하는 배치/스케줄러/DB 조회 도구 | daily_scan 크론 재등록 |

**어떤 수정이든 매매 결과(어떤 종목을 언제 얼마에 사고 파는지)를 바꾸면 안 된다.** A/B/C 어디에도
속하지 않거나 애매하면 반드시 사용자에게 먼저 확인한다.

## ⚠️ 확인 필요 — 판단 보류 중인 항목 (2026-07-20 일부 해소)

`market_regime`/`position_size_mult`는 2026-07-20 "E2 기간 허용 작업" 지시로 A등급 확정,
`execute_buy()`/`insert_trade()`에 기록 코드 추가 완료(아래 변경 이력 참조).

`choch_grade`/`gate_reason`/`score`는 **여전히 미해결**: 코드 추적 결과 `choch_grade`는
`check_entry_signal()` 내에서 `execute_buy()` 호출이 끝난 *이후*에 계산되어 `self.positions[stock_code]`에
patch되는 구조라, `execute_buy()` 내부에서 실행되는 DB 기록 시점에는 아직 실제 값을 알 수 없다.
안전하게 채우려면 `execute_buy()`의 8개 호출부에 새 파라미터를 추가로 threading 해야 하는데,
이는 Entry 로직 호출 경로를 건드리는 작업이라 **임의로 진행하지 않음 — 별도 승인 필요.**
`gate_reason`/`score`는 전용 컬럼이 없고 기존 `entry_type`/`entry_reason`/`total_score` 필드로
사실상 대체되어 있어 신규 필드 추가 실익이 낮다고 판단, 보류.

## 실거래 검증 목표

| 단계 | 표본 |
|---|---|
| 1차 검증 | 30건 |
| 중간 검증 | 50건 |
| 최종 검증 | 100건 |

2026-07-20 기준 누적 29건(exit_time 확정 기준) — 1차 검증 문턱 코앞.

## 자동 리포트 인프라

| 도구 | 주기 | 내용 |
|---|---|---|
| `analysis/e2_trade_analytics.py` | 매일 16:07 | KPI(거래수/승률/PF/Expectancy/평균손익/손익비/평균보유시간/최대연속손실/최대DD), Regime별 성과, Entry 품질(현재 데이터부족), Exit 품질(MFE/MAE/기브백/Trailing·TimeExit비율), Risk 분석(포지션사이징), RISK_OFF 차단/후보 현황 |
| `analysis/ops_weekly_review.py` | 매주 금 16:10 | E0/E1/E2+ 증거등급 자동판정, KPI 8개 체크, Scientist 분류(수동 입력), OPERATIONS_LOG 행 생성 |
| `analysis/system_health_check.py` | 매일 08:55/16:05 | daily_scan/SCORE_ENGINE/Candidate Pipeline/Global Gate/Exit동기화/Equity Controller/Scheduler/Exception/Log-DB일치/로깅중복 |

모두 기존 `trades`/`research.decision_ledger`/`research.candidates` 테이블만 조회 — **신규 DB
스키마 없음.**

## 변경 관리 원칙

E2 기간 중 모든 코드 수정은 A/B/C 중 하나로 분류하고 이 문서 하단에 이력을 남긴다. 전략 변경은
E2 종료 전까지 금지.

## E2 종료 조건 (전부 충족 시에만 전략 변경 검토 시작)

1. 최소 100건의 실거래 표본 확보
2. System Health Check Critical FAIL 0건 유지
3. EC_HALT/Scheduler/SCORE_ENGINE 등 구조적 버그 재발 0건
4. Exit DB와 브로커 체결 불일치 0건 (`system_health_check.py`의 exit_sync 항목으로 상시 감시)
5. KPI가 안정적으로 산출되고 주간 리뷰가 누락 없이 생성됨

이 단계의 목표는 "좋은 성과"가 아니라 "신뢰할 수 있는 데이터"다.

## E2 종료 후 검토 예정 — 백테스트/실거래 코드 동일성 확보 (2026-07-20)

`backtest/daily_scan.py`의 실제 `BEST_CONFIG`/`BEST_ADAPTER_KWARGS`로 2년치(78종목) 백테스트를
돌려본 결과(총 79건, 승률 48.1%, RR 0.28, MDD -0.9%, 수익률 +11.7%), 다음 구조적 문제를 확인:

- `backtest/adapter.py`의 `SMCAdapter`는 실거래 `analyzers/smc/smc_signals.py`를 그대로 쓰지
  않고 **별도로 재구현한 병렬 코드** — 백테스트 전략 ≠ 실거래 전략.
- 청산도 백테스트는 고정 TP 5%/SL 3%, 실거래는 `analyzers/swing/holding_manager.py`의
  MA5/MA20/Trailing/Time Exit 복합 로직 — 완전히 다른 청산 구조.
- 표본 79건(종목당 평균 1건, 월평균 3.3건)은 승률/RR/PF 분산이 매우 커서 통계적 결론 불가.
  "0/78 종목 통과"는 전략 실패가 아니라 검정 자체가 성립하지 않는 상태.
- RR 0.28처럼 비정상적으로 낮은 값은 전략 품질보다 **백테스트 엔진이 실거래 Exit를 재현하지
  못해서 발생하는 왜곡**일 가능성이 큼.

**결론(사용자 확정)**: 이 백테스트 결과 자체로 Entry/Exit 수정을 판단하지 않는다. 이 프레임워크는
현재 "실거래 전략의 성과를 검증하는 도구"로 쓰기엔 구조적 동일성이 부족하다는 것 자체가
유의미한 결론이며, E2 Strategy Freeze를 유지하는 근거가 된다.

**E2 종료 후 우선 추진 과제(전략 변경 아님, 검증 환경 개선)**:
1. `analyzers/smc/smc_signals.py`를 라이브/백테스트가 공유하는 구조로 리팩터링
   (`backtest/adapter.py`의 별도 재구현 제거)
2. `analyzers/swing/holding_manager.py`(실거래 Exit)를 백테스트 엔진에서도 그대로 재사용
3. 위 공유 구조가 갖춰진 뒤에만 과거 데이터 재검증 → 그때 비로소 백테스트 결과와 실거래
   결과를 직접 비교 가능해지고, 이후 전략 개선도 근거를 가지고 진행 가능해짐

---

## 변경 이력

| 날짜 | 분류 | 내용 |
|---|---|---|
| 2026-07-20 | B/C | System Health Check v1.0, E2 Trade Analytics, daily_scan/weekly_review 크론 등록 |
| 2026-07-20 | A | E2 기간 허용 작업 4건: (1) Entry 메타데이터(`market_regime`/`position_size_mult`) 기록 — `_last_regime` dead attribute 수정 + `insert_trade()` SQL 컬럼 누락 수정, (2) Exit DB 기록을 브로커 주문 체결 확인 이후로 이동 (`execute_sell()`), (3) `equity_controller` 상태파일 원자적 저장(temp+fsync+rename) + Primary→Backup→DB Recovery 로드 체인, (4) `risk_manager.py` TradeDB insert 무음 except → `logger.exception()` + Health Check WARN 연동. 4건 모두 매매 결정(종목/시각/수량/가격) 변경 없음, py_compile 전체 통과, rollback 트랜잭션 및 격리 시나리오 테스트로 회귀 검증 완료. |
| 2026-07-20 | A | choch_grade 기록 버그 수정 — `execute_buy()`가 항상 존재하지 않는 `kwargs` 변수를 읽어 choch_grade가 영원히 None이었음. `_emit_signal()`→signal dict→`_flush_pending_signals()` 경로로 실제 값 threading. **단, `should_allow_overnight()`의 `require_a_grade` 게이트는 이 버그 때문에 지금까지 사실상 비활성 상태로 운영되어 왔다는 것을 확인** — 그대로 고치면 오버나이트 보유 결정(Risk/Exit 로직)이 바뀌므로, 관측용 값은 `choch_grade_actual`이라는 별도 키에 저장하고 `should_allow_overnight()`에는 계속 None을 전달해 기존 운영 동작을 동결 유지. 게이트 활성화 여부는 별도 승인 필요. |
| 2026-07-20 | A/B | signal_orchestrator.py의 `signal_logger.propagate` 미설정 수정 — REGIME_BLOCK 등 로그가 root logger로 중복 전파되어 grep count가 약 2배 부풀려지던 문제 해소(전용 FileHandler는 그대로 유지). |
| 2026-07-20 | A | System Audit dead code 정리: `regime_detector.reset_cache()`/`signal_detector.calculate_signal_confidence()`/`bottom_pullback_manager.remove_signal()+get_signal_info()` 제거(호출처 0건 확인). `liquidity_shift_detector.py`(v1)는 감사 문서상 "v2로 완전 대체"로 기록돼 있었으나 실제로는 v2가 v1을 **상속**하는 베이스 클래스라 삭제 시도 중 발견하고 즉시 원복 — 감사 문서 정정 완료. |
| 2026-07-20 | A | Business Logic Audit v2.0(15개 항목 전수감리) + Critical Bug Fix v2.1(Fail-Open 제거 5건) — 전문 `docs/BUSINESS_LOGIC_AUDIT_v2.md`/`docs/CRITICAL_BUG_FIX_v2.1.md`/`docs/FAIL_OPEN_AUDIT.md`/`docs/IMPACT_ANALYSIS.md`. DD_HALT/EC_HALT/EQ필터/SMC displacement Fail Closed 전환, same_day_entry 경로수정, stock_ban_list/orphan_halt 복구, 로거핸들러 중복제거(12.5%→0%). 320개 회귀테스트 통과, 실거래 재시작 검증 완료. |
