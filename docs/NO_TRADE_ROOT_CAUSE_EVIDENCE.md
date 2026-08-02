# 무거래 원인 Evidence 문서 (2026-07-01 ~ 2026-07-27)

> 작성일: 2026-07-27 | 운영 안정성 감사 v3.1 — 작업 3
> 원칙: 이 문서는 "확정 사실"과 "추론/개연성"을 명확히 구분한다.
>       추론은 근거 수준(Confirmed Root Cause / Major Contributing Factor / Open Question)을
>       반드시 명시한다.

## 확정 사실 (Confirmed Facts)

1. **실거래 신규 BUY 0건**: PostgreSQL `trades` 테이블 기준, 2026-07-01 이후 신규 BUY 주문이
   기록된 적이 없다(2026-07-01 BUY 1건/SELL 1건이 마지막). 0177R0(TIGER 반도체TOP10커버드콜)
   매수는 장기보유 목적의 별도 경로이며 자동매매 신호 기반 진입이 아니다.
   ```sql
   SELECT trade_time::date, trade_type, COUNT(*) FROM trades
   WHERE trade_time::date >= '2026-07-01' GROUP BY 1,2 ORDER BY 1,2;
   ```

2. **`[TRADE_CD]` 연패 쿨다운이 같은 기간 거의 매 거래일 발동**:
   ```
   07-01:3  07-03:2  07-06:5  07-08:1  07-09:1  07-10:1  07-11:1  07-13:3
   07-14:3  07-15:6  07-16:3  07-17:5  07-18:1  07-20:6  07-21:2  07-22:2
   07-23:2  07-24:3  07-25:1   →  07-27(수정 후): 0
   ```
   (`logs/auto_trading_YYYYMMDD.log`에서 `[TRADE_CD]` 태그 발생 횟수, `TRADE_CD_BYPASS`는 제외)

3. **원인 메커니즘 확인(코드)**: `data/risk_log.json`의 `consecutive_losses=3`,
   `cooldown_until=2026-07-01T15:30`(만료됨)가 2026-07-01부터 오늘 수정 전까지 그대로
   방치되어 있었다. `core/risk_manager.py:load()`가 `cooldown_until` 만료 여부와 무관하게
   `consecutive_losses`를 무조건 복원하던 버그 때문이다. `_check_loss_streak_guard()`
   (`main_auto_trading.py:8562`)는 **①프로세스 시작 시, ②포지션 청산(SELL) 직후**마다 호출되며,
   `consecutive_losses >= 3`이고 `_trade_cooldown == 0`이면 "신규 진입 2회 쿨다운"을 무조건
   재무장한다(`cooldown_trades` 기본값 2, `main_auto_trading.py:8595-8596`). `_trade_cooldown`은
   재시작 시 0으로 초기화되는 순수 메모리 변수라, 만료된 지 오래된 연패 기록이 **재시작할
   때마다, 그리고 포지션을 하나 청산할 때마다 계속 새로 쿨다운을 걸었다.**

4. **버그 수정 완료**: 2026-07-27 `core/risk_manager.py:load()`에 `cooldown_until` 만료 시
   `consecutive_losses`/`cooldown_until`을 리셋하는 로직 추가. 수정 후 재시작(09:26 KST)부터
   `[TRADE_CD]` 발생 0건 확인.

5. **Candidate(운영 일일요약 기준) 수백 건 vs Entry Evaluation 한 자릿수~십여 건**:
   `logs/operations_daily_summary_cron.log`에 기록된 07-13~07-24 기간 Candidate 수(293~959건,
   0인 날도 있음)에 비해 Entry Evaluation은 6~16건 수준으로 훨씬 적고, 모든 날 Orders
   Submitted는 0이다. 이 단계(Candidate → Entry Evaluation)의 감소는 TRADE_CD와 무관하게
   이미 존재했다.

## 높은 개연성 — Major Contributing Factor (Confirmed Root Cause 아님)

> **TRADE_CD 버그는 이미 적었던 진입 시도(Entry Evaluation, 하루 6~16건)를 추가로 차단한
> 중요한 기여 요인이다.**

근거:
- 발동 빈도(1~6회/일)가 하루 전체 진입시도 수(6~16건) 대비 상당한 비율을 차지한다.
- 발동 메커니즘상(재시작마다 + 매도 시마다 재무장) 26일 내내 사실상 상시 활성 상태에
  가까웠을 것으로 추정된다.
- 무거래 기간(07-01~07-25)과 발동 기간이 정확히 겹친다.

**"Confirmed Root Cause"로 격상하지 않는 이유**:
- TRADE_CD에 걸리지 않았어도 그 진입 시도가 실제 주문으로 이어졌을지는 별도 게이트
  (RVOL/EMA9/VWAP/수급/위치/레짐 필터, `risk_manager.can_open_position()` 등)를 통과해야
  확인 가능하며, 이번 조사에서 "TRADE_CD가 없었다면 실제로 몇 건이 주문까지 갔을지"를
  개별 후보 단위로 재구성하지는 않았다.
- research 스키마 데이터가 과거 특정 시점(아래 참고)에 TRUNCATE되어, 그 시점 이전의
  candidate/decision 상세 기록으로 사후 재구성하기 어렵다.

## 미해결 과제 (Open Question — 별도 추적)

```
Candidate 수백 건
  ↓ (대부분 여기서 사라짐 — 원인 미상)
Entry Evaluation 한 자릿수~십여 건
  ↓ (TRADE_CD 등 execute_buy 내부 게이트)
Orders Submitted 0건
```

Candidate에서 Entry Evaluation으로 넘어가는 단계의 대량 감소 원인(SMC 구조 신호 미발생인지,
Entry Quality 필터인지, 레짐 필터인지)은 이번 조사에서 규명하지 않았다. 이는 기존 프로젝트
`project_20260608_no_trades_fix`(memory)에서 계속 추적 중인 주제이며, 이번 발견(TRADE_CD)을
반영해 갱신이 필요하다.

## 참고 — research 스키마 데이터 유실 (별도 사고, 참고용)

`research.candidates`/`decision_ledger`/`event_store`가 `tests/test_research_schema.py`,
`tests/test_decision_service.py`의 `TRUNCATE`로 인해 최소 2회(2026-07-23경, 2026-07-27
13:50경으로 추정) 전체 삭제된 정황이 있다(`pg_stat_user_tables` 누적 INSERT/DELETE
불일치로 확인). 이 사고는 **거래 자체를 막지는 않았지만**, 위 미해결 과제를 DB 쿼리로
사후 재구성하는 것을 어렵게 만든다. 상세는 `docs/TEST_DB_ISOLATION_PLAN.md`,
`utils/database_guard.py`, `analysis/check_research_integrity.py` 참고.

## 다음 확인 시점

TRADE_CD 수정 반영 이후 첫 실거래일(2026-07-28 예정) 종료 후:
- 신규 BUY 발생 여부
- `[TRADE_CD]` 발생 0건 유지 확인
- Entry Evaluation 대비 실제 주문 도달 비율 변화 확인
