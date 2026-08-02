# Pipeline Audit — Follow-up Backlog

> 최초 작성: 2026-07-28 (Production Audit Fix v1.1 작업 중)
> 목적: 감사/수정 작업 중 발견되었으나 **해당 작업 범위 밖이라 의도적으로 수정하지 않은** 항목을 기록.
> 원칙: 여기 있는 항목은 별도 승인 없이 수정하지 않는다.

---

## FU-01 [MEDIUM] TrendExitEngine trailing stop에 monotonic 가드 없음

- **위치**: `trading/trend_exit_engine.py:360`
  ```python
  pos.trailing_stop_price = high_price - (atr * pos.trailing_atr_multiplier)
  ```
- **문제**: BUG-02와 동일한 클래스의 결함. `high_price`는 `max_profit_pct` 기반이라 단조증가하지만
  `atr`은 변동하므로, ATR이 확대되면 트레일링 스탑이 **하락**할 수 있다.
- **수정하지 않은 이유**: **실행 경로에 없음.** `main_auto_trading.py` / `api_server.py` /
  `swing_runner.py` / `swing_executor.py` 어디에서도 임포트하지 않으며,
  유일한 사용처인 `trading/dual_account_orchestrator.py`(→ `main_dual_account.py`)는
  크론에 등록되어 있지 않고 프로세스도 실행 중이 아니다. Fix v1.1은 "운영 중 코드의
  안정성 수정"이 범위이므로 휴면 코드는 제외했다.
- **후속 판단 필요**: ① `main_dual_account.py`를 앞으로 쓸 계획이 있는가?
  → 쓸 계획이면 BUG-02와 동일한 `max(prev, calc)` 가드 적용. 없으면 dead code 정리 대상.

---

## FU-02 [정보] ScoreEngine 선별 기능이 현 장세에서 구조적으로 무력

- **관찰**: 최근 7거래일 연속 `score>=2: 0  selected: 0`.
- **분석(검증 완료, 버그 아님)**:
  - `smc(+2)`: `daily_scan`은 정상 실행 중이나 "BUY 신호 없음" → 항상 0
  - `ma50(+1)`: 실데이터 확인 결과 **정확한 계산**. 삼성전자 254,000 vs MA50 303,170 등
    전 종목이 실제로 MA50 하회(하락장). 일봉 정렬도 오름차순 정상(07-15 역순 핫픽스 적용됨).
  - `volume(+1)`: 간헐적으로만 1
  - → 하락장에서 최대 획득 점수 = 1 < `MIN_SCORE=2`
- **결과**: 선별이 항상 0건이며 `main_auto_trading.py:13657-13662`의 폴백(기존 watchlist 유지)에만
  의존한다. ScoreEngine의 "상위 5종목 선별" 기능이 사실상 비활성.
- **수정하지 않은 이유**: `MIN_SCORE` 변경은 **전략 파라미터 변경**에 해당(작업지시서 절대 금지 항목).
  이건 버그가 아니라 정책 판단 사항이다.
- **후속 판단 필요**: 하락장에서 ScoreEngine을 어떻게 동작시킬 것인가(또는 비활성이 의도인가)에 대한
  전략적 결정. 최소한 "선별 0건이 N일 연속"일 때 경고를 띄우는 관측성 추가는 검토 가치 있음.

---

## FU-03 [LOW] `[REGIME_BLOCK]` 로그 태그가 의미가 다른 두 이벤트에 재사용됨

- **위치**: `main_auto_trading.py:5545`(종결 차단) vs `main_auto_trading.py:5792`(RAE 서브경로 스킵)
- **문제**: 전자는 후보 평가를 종료시키는 차단(Decision Ledger 기록됨), 후자는 RAE 재진입 분기만
  건너뛰는 **비종결** 이벤트인데 동일 태그를 쓴다. 로그 기반 집계 시 두 이벤트가 합산된다.
- **수정하지 않은 이유**: BUG-04 수정으로 퍼널 지표가 **DB 단일 출처로 전환**되면서 이 태그 혼선이
  퍼널 숫자를 오염시키던 경로는 해소되었다(로그 값은 참고 섹션으로 분리 표기). 태그명 변경 자체는
  로그 포맷 변경이라 별도 승인 대상.
- **권장 수정**: RAE 경로를 `[RAE_REGIME_SKIP]` 등으로 분리.
  ※ route=RAE에 Decision Ledger REJECT를 추가하는 것은 **권장하지 않음** — 비종결 이벤트를
  종결 결정으로 기록하면 이중 계상이 된다.

---

## FU-04 [LOW] ScoreEngine 요약 로그 3줄 중복 출력

- **위치**: `trading/score_engine.py:229` (`log_summary()`가 내부에서 `self.select()` 재호출)
- **영향**: 관측성 노이즈만. 매일 `[SCORE_ENGINE] raw=... selected: ...` 3줄이 2회씩 기록됨.
- **수정하지 않은 이유**: Fix v1.1 범위(BUG-01~04) 밖.

---

## FU-05 미수행 감사 영역 (Audit v1.0에서 이월)

1. **Phase 4 전략 로직 조건문 전수 감사** — SMC/CHoCH/BOS/Liquidity Sweep/Order Block/
   Cup&Handle의 도달불가 코드·항상 False 조건 분석. **미수행.**
2. **Phase 10 운영 안정성** — 메모리 누수, 객체 누적, Kiwoom API 재연결/중복주문 시나리오. **미수행.**
   - `except Exception: pass` 패턴이 핵심 모듈에 **113건** 존재(main_auto_trading.py 포함).
     개별 위험도 판정이 필요하다. 일부는 의도된 방어이지만, 일부는 실패를 삼켜
     "기능이 망가져도 에러 없이 통과"하는 Silent Failure 경로일 수 있다.
3. **E2 Evidence Level 판정** — research 테이블 데이터 소실로 판정 불가.
   `analysis/check_research_integrity.py`로 데이터 재축적 확인 후 재시도.
4. **BUG-02 실거래 발생 여부 확인** — 부분청산(TP2) 이력이 쌓이면 실제 스탑 하향 사례가
   있었는지 로그로 소급 검증.
