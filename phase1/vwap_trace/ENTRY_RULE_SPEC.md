# ENTRY_RULE_SPEC — VWAP+AI

Iteration 6-2 · **빈칸은 "미확인" 으로 명시. 추정으로 채우지 않는다.**

| 항목 | 내용 | 근거 | 상태 |
|---|---|---|---|
| **조건검색 조건** | 인덱스 `[17,18,19,20,21,22]` 6개. 이름은 키움이 런타임에 내려줌 (`GreatMid` · `Momentum 전략` · `알고리즘추출_1110` · `종가매수_대박주식` 확인, 인덱스↔이름 대응 미확인) | `config:1275`, `main:3040` | ⚠️ 부분 |
| **VWAP 계산** | `typical=(H+L+C)/3`, `pv=typical×V`, `vwap = rolling_sum(pv,20) / rolling_sum(V,20)`. **20봉 rolling** (config 에 `vwap:` 섹션 없음 → 코드 기본값) | `entry_timing_analyzer.py:58` | ✅ |
| **VWAP 돌파 정의** | **미확인.** 코드에 4종 공존 — ① `close_prev<vwap_prev and high>=vwap_now` ② `close>=vwap` ③ `current_price>vwap` ④ N봉 연속 `close>vwap`. 진입을 만든 모듈이 미호출이라 역추적 불가 | `main:8617/8708/11308`, `analyzer:227` | ❌ |
| **AI Score** | **산출식 없음.** `analysis.get('total_score', 0)` 를 읽지만 `analysis` 생성처를 특정 못 함. `entry_timing_analyzer` 에 `total_score` 정의 0건 | `order_executor.py:251` | ❌ |
| **Threshold** | **미확인.** 실거래 89건(53.9%)이 `0.0` — 기본값이 찍힌 것이지 점수가 0 인 게 아니다 | 〃 | ❌ |
| **Entry Timing** | 5분봉 기준. 감시 루프 60초 주기 (`check_interval=60`) | `main:3637` | ✅ |
| **Position Size** | `risk_amount = balance × RISK_PER_TRADE`, `qty = risk_amount / abs(entry-stop)` | `risk_manager.py:301` | ✅ |
| **Risk Filter** | `execute_buy` 내 조기 return 37경로, reason_code 8종 (COOLDOWN_ACTIVE / RISK_BLOCKED / DUPLICATE_POSITION / MAX_POSITIONS / INSUFFICIENT_CAPITAL / ENTRY_QUALITY_BLOCKED / API_FAILURE / ORDER_FAILURE) | Iter P0 | ✅ |
| **BUY 조건** | **미확인.** `order_executor.execute_buy` 가 만들지만 `main` 에서 호출 0건. 현재 코드 경로에 VWAP+AI 진입이 존재하지 않는다 | grep 0건 | ❌ |

**9개 항목 중 5개 확인 · 1개 부분 · 3개 미확인.**
