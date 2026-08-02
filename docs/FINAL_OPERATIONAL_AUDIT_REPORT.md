# 최종 운영 안정성 평가 (Audit 7)

> 생성일: 2026-07-27 | 운영 안정성 감사 v3.0
> 관련 문서: `docs/SOURCE_OF_TRUTH_AUDIT.md`, `docs/GATE_PIPELINE_AUDIT.md`, `docs/STATE_AUDIT.md`, `reports/OPERATION_SIMULATION.md`, `docs/LOGGING_AUDIT.md`

## 평가 항목별 판정

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| 1 | 운영 중 거래가 영구적으로 막힐 가능성 | **PASS(조건부)** | 오늘 3건 수정(RiskManager 쿨다운/DrawdownEngine 재시작소실/daily_routine 재시작판별)으로 "근거 없이 계속 막히는" 케이스는 해소. 단, `execute_buy()`의 25개 조기 return 자체가 잘못된 판단으로 막는 건 아니므로 "영구 차단" 리스크는 아님 — 다만 그 이유를 사후에 알 수 없다는 게 별개 문제(§4) |
| 2 | 숨은 Source of Truth 존재 여부 | **FAIL** | 일일 손실 한도가 3곳(risk_manager/signal_orchestrator/main_auto_trading 자체)에서 독립 계산됨을 확인. 이번엔 수정하지 않음(범위 밖, 통합은 리팩토링 성격의 큰 변경) |
| 3 | 상태 불일치 가능성 | **PASS(수정 후)** | DrawdownEngine 재시작 소실, daily_routine 재시작 판별 부재, Position phantom 미감지 — 3건 모두 오늘 수정+회귀테스트 완료 |
| 4 | 로그 신뢰성 | **PASS(원인 규명+재발방지)** | 오늘 발생한 로그유실 사고의 근본원인(비-append 재시작)을 규명, 재발방지 가이드 문서화. 단, execute_buy() 25곳은 로그도 DB도 아닌 순수 console 출력에 그치는 지점이 다수 있어 "로그만 봐도 알 수 있다"는 완전하진 않음 |
| 5 | Gate 누락 | **FAIL** | EARLY_WINDOW_BLOCK/SMC afternoon cutoff(로그전용), 대체 전략경로 4개+Orchestrator(DB 미연결, 현재 비활성), **execute_buy() 25곳(최대 사각지대)** — 전부 미수정, 문서화만 완료 |
| 6 | Dead Code | **FAIL(경미)** | `core/market_monitor.py`의 `MarketMonitor` 클래스 전체 미사용 확인. 삭제하지 않음(범위 밖, 위험 없는 정리성 작업이라 후속 처리 권고) |
| 7 | Race Condition | **PASS** | asyncio 단일 이벤트루프 구조상 낮음. 파일 기반 상태(`cooldown.lock`)의 동시쓰기 방어 부재는 잠재 위험으로 기록했으나 실제 사고 이력 없음 |
| 8 | Restart 안정성 | **PASS(수정 후)** | 오늘 수정 전에는 장중 재시작이 일일 리셋을 통째로 재실행하는 실질적 결함이 있었음 — 마커 기반 판별로 해소. 14:50 이후 재시작(overnight_close 강제)은 기존부터 안전했음을 확인 |

## 종합 판정: **CONDITIONAL PASS**

오늘 발견/수정한 3건(DrawdownEngine 영속화, daily_routine 재시작판별, Position phantom 감지)은 **실제 운영 중 상태 불일치를 유발할 수 있었던 진짜 버그**였고, 전부 수정+회귀테스트(합계 20건 신규 테스트, 기존 회귀 51~64건과 함께 전체 통과) 완료했다.

다만 이번 감사로 그보다 **범위가 크고 위험도가 높은 구조적 문제 2건**을 확정했다:
1. **`execute_buy()` 내부 ~25개 지점의 Decision Ledger 고아 레코드** — SMC가 이미 승인한 후보의 최종 결과를 사후에 알 수 없음. 실거래 Evidence 축적(오늘 별도 세션에서 구축한 Regime Gate E2→E3 체계)의 신뢰도에 직접 영향.
2. **일일 손실 한도의 3중 독립 구현** — 지금은 셋 다 재시작에 안전해졌지만(간접 효과), 애초에 왜 3곳이 각자 계산하는지에 대한 근본 정리는 안 됨.

이 둘은 "정책 변경"이 아니라 "구조 정리/버그 수정" 범주이므로 향후 승인 시 이번과 동일한 원칙(진입/청산/threshold 불변, 관측성/정합성 개선만)으로 진행 가능하다.

## 동일 유형 재발 방지책

1. **회귀테스트 커버리지 확대**: 오늘 추가한 20건(`test_risk_manager.py`, `test_regime_analyzer.py`, `test_drawdown_engine.py` 보강, `test_daily_routine_guard.py`, `test_regime_scenarios.py`)이 이번에 발견한 버그들의 재발을 자동 탐지한다.
2. **"재시작 안전성"을 신규 상태 추가 시 체크리스트 항목으로**: DrawdownEngine 사례처럼, 새 상태 변수를 추가할 때 "재시작하면 이 값이 어떻게 되는가"를 명시적으로 결정하고 문서화할 것(영속화 필요/불필요 여부 포함).
3. **"조기 return이 Decision Ledger를 스킵하지 않는가"를 신규 게이트 추가 시 체크리스트 항목으로**: `execute_buy()` 사례처럼, `_pending_decision_ctx`류의 1회성 컨텍스트를 다루는 함수에 새 return을 추가할 때는 반드시 `record_rejection`/`record_acceptance` 처리 여부를 확인할 것.
4. **로그 재시작 시 append 모드 강제**: `watchdog.py` 외 경로로 프로세스를 재시작할 일이 있다면 이번 사고 사례를 참고해 반드시 O_APPEND 방식을 쓸 것(`docs/LOGGING_AUDIT.md` 운영가이드 참조).

## 완료 조건 체크

- [x] Source of Truth 감사 완료 (`docs/SOURCE_OF_TRUTH_AUDIT.md`)
- [x] Gate Pipeline 감사 완료 (`docs/GATE_PIPELINE_AUDIT.md`)
- [x] State 감사 완료 (`docs/STATE_AUDIT.md`)
- [x] 운영 시뮬레이션 완료 (`reports/OPERATION_SIMULATION.md`, 합성데이터 대체)
- [x] 회귀 테스트 추가 완료 (20건 신규, 전체 스위트 재확인)
- [x] 로그 감사 완료 (`docs/LOGGING_AUDIT.md`)
- [x] 동일 유형 운영 버그 재발 가능성에 대한 원인 및 대응 방안 문서화 (본 문서 §재발방지책)
- [x] 최종 PASS/FAIL 보고서 제출 (본 문서, CONDITIONAL PASS)
