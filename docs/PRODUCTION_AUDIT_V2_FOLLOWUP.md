# Production Audit v2.0 — Follow-up Backlog

> 작성일: 2026-07-28 | 본 감사: `docs/PRODUCTION_AUDIT_V2_REPORT.md`
> 원칙: 여기 항목은 별도 승인 없이 수정하지 않는다.
> (v1.1 이월분은 `docs/PIPELINE_AUDIT_FOLLOWUP.md` 참조 — FU-01~FU-05는 여전히 유효)

---

## V2-FU-01 [High] 실거래 모듈의 다중 사본 (삭제 금지 지시로 미조치)

같은 역할의 구현이 3~4중으로 존재하고 그중 하나만 실제 동작한다. 전체 418개 모듈 중
**286개가 자동 실행 진입점(cron + main + watchdog)에서 도달 불가**하다.

| 역할 | 실제 사용 | 휴면 사본 |
|---|---|---|
| 주문 실행 | `self.api.order_buy/sell` | `core/order_executor.py`, `trading/order_executor.py`, `brokers/kiwoom_broker.py` |
| 청산/손절 | `trading/exit_logic_optimized.py` | `core/stop_loss_manager.py`, `core/auto_stop_loss_system.py`, `trading/stop_loss_executor.py`, `trading/trend_exit_engine.py` |
| 포지션 관리 | `main_auto_trading.self.positions` | `core/position_manager.py`, `trading/position_tracker.py`, `core/portfolio_manager.py` |
| 스케줄링 | cron | `core/scheduler.py` |
| 웹소켓 | main 내장 | `core/websocket/websocket_manager.py`, `trading/websocket_client.py` |
| 계좌 | main 내장 | `trading/account_manager.py`, `core/auto_balance_monitor.py` |

추가로 `gpt_share/exit_logic_optimized.py`, `docs/share/exit_logic_optimized.py`,
`docs/share/main_auto_trading.py` 같은 **문서/공유용 사본**도 존재한다.

- **위험**: 운영자·AI가 휴면 사본을 수정하고 "고쳤다"고 오인. 이번 감사에서도 실사용 파일
  식별에 시간이 소요됐다.
- **주의**: 286개 중 상당수는 **수동 실행용 분석 스크립트**(예: `analysis/trading_health_audit.py`)로
  정상적인 미도달이다. 일괄 삭제는 위험하며, 도메인별 선별이 필요하다.
- **권장 후속**: ① 휴면 모듈 상단에 `# UNUSED — not in execution path` 마커 주석 추가(삭제 대신),
  또는 ② `deprecated/` 디렉터리로 이동. 어느 쪽이든 별도 승인 필요.

---

## V2-FU-02 [Medium] gate_health_check 이중 실행

- 15:58 자체 크론 + 16:05 `operations_daily_summary`가 `run_gate_health_check()` 직접 호출
- 같은 검사가 하루 2번 돌고, 그 사이 데이터가 바뀌면 두 리포트가 불일치할 수 있다.
- **권장**: 크론에서 15:58 항목을 제거하고 `operations_daily_summary` 결과만 쓰거나,
  반대로 `operations_daily_summary`가 15:58 산출물을 읽도록 변경.

---

## V2-FU-03 [Medium] 16:05 3중 동시 실행

`live_lcl_validation.py` / `operations_daily_summary` / `system_health_check --mode post`

- **현재는 무해**(셋 다 DB 읽기 전용, 상호 의존 없음)로 판정했으나, 향후 어느 하나가
  DB 쓰기를 추가하면 경쟁이 생긴다.
- **권장**: 1~2분 간격으로 분산하거나, 순차 실행 래퍼로 묶기.

---

## V2-FU-04 [Low] `_get_today_market_context_id()` / `_get_today_session_id()`의 에러-부재 혼동

- **위치**: `main_auto_trading.py:11513`, `:11522`
- `except Exception: return None` — DB 오류와 "해당 행 없음"을 구분하지 못한다.
- **영향 제한적**: 반환값은 `decision_log`의 FK 컬럼 채우기에만 쓰이고, 실패해도
  바깥 try가 로깅한다. 매매 판단에 영향 없음.
- **권장**: 오류 시 `logger.debug` 한 줄 추가로 구분 가능하게.

---

## V2-FU-05 [미수행] Strategy Reachability — 조건문 단위 분석

- 이번 감사는 **모듈 단위 도달성**만 수행했다(어떤 파일이 실행 경로에 있는가).
- 지시서가 요구한 **조건문 단위 분석**(SMC/CHoCH/BOS/Liquidity Sweep/Order Block/
  Pullback/Cup Handle/Breakout에서 "항상 False인 조건", "도달 불가능한 분기")은
  **수행하지 못했다.**
- 이 작업은 각 전략 함수의 제어흐름 그래프 분석 + 실제 입력 분포 확인이 필요해
  단독 세션 규모다.
- **권장**: 별도 세션에서 전략 파일 1~2개씩 나눠 진행.

---

## V2-FU-06 [미수행] Memory / Resource 프로파일링

- 정적 확인만 수행했다(스레드 0개, DB 커넥션 풀 사용).
- **미수행**: 장기 실행 시 메모리 증가 추이, DataFrame 누적, File Handle/Timer 누수,
  커넥션·커서 누수 실측.
- **권장**: 운영 프로세스에 `tracemalloc` 또는 주기적 `RSS` 로깅을 붙여
  1주일 추이를 관찰한 뒤 판단. (현재 PID 3196098의 RSS는 약 315MB)

---

## V2-FU-07 [Low] v1.1 이월분 (여전히 유효)

`docs/PIPELINE_AUDIT_FOLLOWUP.md`의 아래 항목은 이번에도 조치하지 않았다.

- **FU-01** `trading/trend_exit_engine.py:360` trailing stop monotonic 가드 없음 (휴면 코드)
- **FU-02** ScoreEngine이 하락장에서 구조적으로 선별 0건 (정책 판단 사항)
- **FU-03** `[REGIME_BLOCK]` 태그가 종결/비종결 두 이벤트에 재사용
- **FU-04** ScoreEngine `log_summary()` 로그 3줄 중복
- **FU-05** `except Exception: pass` 113건 개별 위험도 판정
  → **본 v2.0 감사에서 467건으로 전수 재집계**하고 SAFE 200 / WARNING 193 / CRITICAL후보 74로
    분류했다. 실경로 수동 검토 결과 진성 CRITICAL은 2건이었고 모두 수정했다.
    나머지 WARNING 193건은 여전히 개별 판정이 필요하다.
