# 운영 시뮬레이션 (Audit 4)

> 생성일: 2026-07-27 | 운영 안정성 감사 v3.0
> 방법: 실 라이브 프로세스에 신호를 주입하는 대신, 합성 데이터 기반 unit 테스트(`tests/unit/test_regime_scenarios.py`, 6건 전부 통과)로 시장 시나리오를 검증하고, 재시작/API실패/DB실패/장시작/장종료는 코드 추적 기반으로 서술한다(사용자 승인 범위).

## 시장 시나리오 (합성 데이터 검증 완료)

| 시나리오 | 검증 결과 | 비고 |
|---|---|---|
| 상승장 | TREND_UP 정상 판정 | `test_uptrend_scenario_is_trend_up` |
| 횡보장(혼조) | NEUTRAL, score=0 | KOSPI/KOSDAQ가 반대방향일 때만 0 가능 — 아래 구조적 발견 참조 |
| RISK_OFF(지속하락) | RISK_OFF 정상 판정 | `test_downtrend_scenario_is_risk_off` |
| 급락장(단일일 -10%) | RISK_OFF + day_drop 사유 포함 | `test_crash_scenario_triggers_day_drop_penalty` |
| 급등장(단일일 +10%) | TREND_UP, 단 "급등 보너스" 피처는 없음(비대칭 설계 확인) | `test_melt_up_scenario_has_no_symmetric_day_gain_bonus` |

**구조적 발견(부산물)**: EMA 산식(`ewm(span=20).mean()`)상 "above_EMA20 AND slope_down" 조합은 단일 스텝에서 수학적으로 불가능하다(new_ema는 항상 price와 prev_ema의 가중평균이므로, slope_down이 성립하려면 price<prev_ema가 전제되고 이는 곧 price<=new_ema를 강제한다). 따라서 지수 하나의 (above/below + slope) 조합은 항상 정확히 -2점 또는 +2점만 가능하며 0점은 없다 — **NEUTRAL 레짐은 KOSPI와 KOSDAQ가 서로 다른 방향일 때만 나온다**는 것이 산식으로 증명됨(`test_single_index_score_is_always_plus_or_minus_two_never_zero`). 이건 버그가 아니라 EMA 지표 자체의 수학적 성질이지만, "왜 NEUTRAL이 드물게 나오는지" 설명에 유용한 근거라 기록해둔다.

## 장 시작 (코드 추적)

`run()` → `daily_routine()`. 08:50까지 대기(`wait_until_time`) 후 시스템 초기화(WebSocket 연결/로그인/토큰검증) → 09:00 실시간 모니터링 진입. ✅ 오늘 수정한 재시작 가드 덕에, 정상적인 "새로운 하루"의 첫 실행에서는 리셋 블록이 정확히 한 번 실행됨(마커 없음 → True).

## 장 종료 (코드 추적)

`overnight_close`/`handle_eod()` 계열이 14:50~15:30 사이 B급 이하 강제청산, EquityController의 `update_peak_eod()` 등을 처리(기존 로직, 이번에 수정하지 않음). 재시작 안전장치로 `main_auto_trading.py:3657-3669`에 "14:50 이후 재시작 시 즉시 overnight_close 강제 실행" 코드가 이미 있음을 확인 — 장마감 근처 재시작에 대한 방어는 기존에 잘 되어 있음.

## 재시작 (코드 추적 + 오늘 수정 반영)

| 재시작 시점 | 이전 동작 | 현재 동작 |
|---|---|---|
| 장중(08:50~15:30) | 일일 리셋 블록 전체 재실행 → DrawdownEngine/일일카운터 소실 | ✅ 마커로 감지, 리셋 생략 + DrawdownEngine 상태파일에서 복원 |
| 14:50 이후 | overnight_close 강제 실행(기존에 이미 안전) | 변경 없음(이미 안전) |
| 08:50 이전(예: 새벽) | 정상적으로 대기 후 첫 리셋 실행 | 변경 없음(정상 케이스) |

## API 실패 (코드 추적)

- Regime Gate: `_score_index()`가 `except Exception`으로 감싸져 있어 API 실패 시 해당 지수는 `score=0, reasons=[]`로 처리되고, 양쪽 다 실패하면 `_compute()`가 `fallback_on_error`(기본 NEUTRAL) 반환 — **fail-safe(진입 차단 방향은 아니지만 최소한 크래시하지 않음)**.
- Kiwoom 주문 API: `execute_buy()`의 주문 실패/예외 처리는 `console.print`만 하고 조용히 종료 — decision_ledger에 안 남는 문제는 `GATE_PIPELINE_AUDIT.md`에 이미 기록.
- Decision Service 전반: `record_rejection`/`record_acceptance` 호출부가 전부 `try/except: pass`로 감싸져 있어, Postgres/decision_service 자체가 실패해도 **매매 로직 자체는 멈추지 않음**(관측성만 소실, 이 자체가 안전설계이자 동시에 "조용히 실패해서 못 알아챌 위험"의 양면).

## DB 실패 (코드 추적)

- 위와 동일하게 `decision_service` 호출부의 `try/except: pass` 패턴 — DB가 죽어도 실거래 로직은 계속 진행(매매 안전 우선, 관측성 희생).
- `EquityController._recover_peak_from_db()`는 DB 연결 실패 시 `None` 반환 → 4단계 복구 체인의 다음 단계(fail-closed, peak=0 + Health Check 요구)로 자연스럽게 넘어감 — 이 부분은 이미 잘 설계되어 있음.
- `RiskManager`/`DrawdownEngine`은 애초에 DB가 아니라 로컬 파일 기반이라 DB 장애의 영향을 받지 않음(장점).

## 결론

시장 시나리오(상승/횡보/RISK_OFF/급락/급등) 판정 로직은 합성 데이터로 검증한 결과 설계대로 정상 동작. 재시작 안정성은 이번 감사에서 발견한 2건(DrawdownEngine, daily_routine 재시작판별)을 수정해 개선됨. API/DB 실패는 "매매 안전 > 관측성"이라는 일관된 설계 철학(fail-open이 아니라 조용히 계속 진행)으로 되어 있으며, 이 자체가 트레이드오프이지 버그는 아니다 — 다만 GATE_PIPELINE_AUDIT.md의 execute_buy() 사각지대와 결합하면 "실패했는데 아무도 모른다"는 상황이 될 수 있어 그쪽이 우선순위 높은 후속 과제다.
