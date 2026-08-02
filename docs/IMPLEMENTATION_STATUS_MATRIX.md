# 구현 상태 매트릭스

> **대상 독자**: 운영자, 개발자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)

상태 정의:
- **Implemented** : 코드로 동작 중, 실거래 적용
- **Partial** : 일부 구현됐으나 제한/조건부 활성
- **Disabled** : 구현됐으나 비활성화
- **Shadow** : 동작하나 실거래 영향 없음 (로그만)
- **Planned** : 미구현

---

## A. 진입 (Entry)

| 영역 | 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|------|
| Entry | CHoCH 탐지 | **Implemented** | `smc_structure.py` | 현재 유일 진입 트리거 |
| Entry | 유동성 스윕 탐지 | **Implemented** | `smc_utils.py` | Sweep Fallback 허용 (B급 일 3회) |
| Entry | CHoCH 등급 A/B/C | **Implemented** | `smc_signals.py` | A≥B>C 순 우선 |
| Entry | Order Block (OB) | **Implemented** | `smc_signals.py` | OB pullback 대기 |
| Entry | Reclaim 확인 | **Implemented** | `smc_signals.py` | 5봉 내 확인 |
| Entry | Prefilter 2-of-3 | **Implemented** | `smc_signals.py` | HTF+Sweep+Reclaim 중 2개 |
| Entry | HTF 30분봉 추세 | **Implemented** | `multi_timeframe_consensus.py` | Prefilter 조건 1 |
| Entry | Displacement Filter | **Implemented** | `smc_signals.py` | 확정봉 검증 |
| Entry | RAE (2nd wave 진입) | **Disabled** | `rae_detector.py` | `rae.enabled: false`, 20건 대기 |
| Entry | TED (RAE soft gate) | **Disabled** | `trend_expansion_detector.py` | RAE 비활성으로 미적용 |
| Entry | EXPLORATION Mode | **Partial** | `main_auto_trading.py` | 활성, bypass, max 2/day |
| Entry | Stage B Trend Extension | **Disabled** | — | 데이터 부족 |
| Entry | MA Cross 전략 | **Disabled** | `ma_cross_strategy.py` | entry_mode: smc로 비활성 |
| Entry | Squeeze 2TF 전략 | **Disabled** | `squeeze_momentum.py` | entry_mode: smc로 비활성 |

---

## B. 필터 (Filter)

| 영역 | 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|------|
| Filter | Signal Orchestrator L0~L6 | **Implemented** | `signal_orchestrator.py` | 전 종목 공통 |
| Filter | G3 품질 게이트 (soft penalty) | **Implemented** | `signal_orchestrator.py` | v1.3: hard block→soft |
| Filter | Stage A 게이트 | **Implemented** | `signal_orchestrator.py` | bdh > 15% 차단 |
| Filter | c_late_v2 (10:30~13:00) | **Implemented** | `signal_orchestrator.py` | 늦은 진입 필터 |
| Filter | c_late_block (13:00 이후) | **Implemented** | `signal_orchestrator.py` | 강한 차단 |
| Filter | AI Gate (ai_score) | **Implemented** | `swing_runner.py` | min_score: 20 |
| Filter | Market Context NO_TRADE_DAY | **Implemented** | `core/market_context.py` | 전국장 차단 |
| Filter | EDT 필터 | **Implemented** | `smc_signals.py` | 조기 하락 추세 차단 |
| Filter | ML Filter | **Shadow** | `config/strategy_hybrid.yaml` | shadow_mode: true |
| Filter | EQ ML Filter | **Shadow** | `config/strategy_hybrid.yaml` | shadow_mode: true |

---

## C. 포지션 사이징 (Sizing)

| 영역 | 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|------|
| Sizing | 등급 기반 base size | **Implemented** | `main_auto_trading.py` | A≈100%, B≈40%, C≈12% |
| Sizing | DrawdownEngine 배율 | **Implemented** | `core/drawdown_engine.py` | NORMAL→CAUTION→DANGER→HALT |
| Sizing | Session Guard 배율 | **Implemented** | `core/edt_sizer.py` | 4종 가드 |
| Sizing | Conservative Mode 배율 | **Partial** | `config/strategy_hybrid.yaml` | 조건부 자동 활성 |
| Sizing | Loss Streak Guard 배율 | **Partial** | `metrics/reentry_metrics.py` | 조건부 자동 활성 |
| Sizing | G3 soft penalty (×0.6) | **Implemented** | `core/position_sizing.py` | v1.3 신규 |
| Sizing | RAE route 배율 (×0.7) | **Implemented** | `core/position_sizing.py` | RAE disabled이나 로직 존재 |
| Sizing | TED 실패 배율 (×0.8) | **Implemented** | `core/position_sizing.py` | RAE disabled이나 로직 존재 |

---

## D. 청산 (Exit)

| 영역 | 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|------|
| Exit | Hard Stop (-2.0%) | **Implemented** | `exit_logic_optimized.py` | 09:20 이전 유예 |
| Exit | LCL v2.1 (진입 초기 손절) | **Implemented** | `exit_logic_optimized.py` | -1.6%, 30분 이내 |
| Exit | Early Failure Structure | **Implemented** | `exit_logic_optimized.py` | 5신호 점수 ≥ 3 |
| Exit | EF Subtype 분류 | **Implemented** | `exit_logic_optimized.py` | ef_no_demand/ef_no_follow |
| Exit | TP1 (2R/25%) | **Implemented** | `exit_logic_optimized.py` | 부분 익절 |
| Exit | TP2 (4R/25%) | **Implemented** | `exit_logic_optimized.py` | 추가 부분 익절 |
| Exit | ATR Trailing Stop | **Implemented** | `exit_logic_optimized.py` | activation 1.5%, dist 0.8% |
| Exit | Time Exit | **Implemented** | `exit_logic_optimized.py` | 최대 보유 봉수 |
| Exit | Overnight Exit | **Implemented** | `exit_logic_optimized.py` | B급 이하 14:50 |
| Exit | Swing Hard Stop (-12%) | **Implemented** | `exit_logic_optimized.py` | 구조손절 미설정 fallback |

---

## E. 재진입 / 쿨다운 (Reentry)

| 영역 | 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|------|
| Reentry | exit_reason별 쿨다운 | **Implemented** | `metrics/reentry_metrics.py` | 20~60분 |
| Reentry | Override (Squeeze/Momentum) | **Implemented** | `metrics/reentry_metrics.py` | 조건부 허용 |
| Reentry | Override 남용 방지 | **Implemented** | `metrics/reentry_metrics.py` | R2 규칙 |
| Reentry | EF Subtype 쿨다운 차등 | **Implemented** | `metrics/reentry_metrics.py` | ef_no_demand 45m, ef_no_follow 20m |
| Reentry | Primary↔RAE 충돌 방지 | **Implemented** | `main_auto_trading.py` | Rule A/B/C/D (RAE disabled) |
| Reentry | Subtype Ratio Drift 감시 | **Implemented** | `metrics/reentry_metrics.py` | R1 규칙 |

---

## F. AI / ML Layer

| 영역 | 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|------|
| AI | Observer AI (07:32) | **Implemented** | `analysis/analyst_ai.py` | 전날 장 분석 |
| AI | Analyst AI (15:30) | **Implemented** | `analysis/analyst_ai.py` | 당일 세션 분석 |
| AI | Scientist AI (금 16:40) | **Implemented** | `analysis/scientist_ai.py` | 주간 가설 검증 |
| AI | Governance AI (온디맨드) | **Implemented** | `analysis/governance_ai.py` | 설계 변경 심의 |
| AI | Strategy AI | **Implemented** | `analysis/strategy_ai.py` | 전략 판정 + evidence level |
| AI | Approve Proposal (E2 gate) | **Implemented** | `analysis/approve_proposal.py` | E2 미만 변경 차단 |
| ML | ML Filter (실전 차단) | **Shadow** | `config/strategy_hybrid.yaml` | shadow_mode: true |
| ML | EQ ML Filter | **Shadow** | `config/strategy_hybrid.yaml` | shadow_mode: true |
| ML | ML Pipeline | **Partial** | `analysis/ml_pipeline.py` | 데이터 수집 중 |

---

## G. 운영 / 인프라

| 영역 | 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|------|
| Ops | 스윙 후보 선정 크론 | **Implemented** | `swing_runner.py` | 15:35 |
| Ops | 스윙 매수 실행 크론 | **Implemented** | `swing_executor.py` | 09:00 |
| Ops | Returns Collector 크론 | **Implemented** | `analysis/returns_collector.py` | 30분 주기 |
| Ops | Health Check 크론 | **Implemented** | — | 15:55 |
| Ops | Watchdog (자동 재시작) | **Implemented** | `watchdog.py` | — |
| Ops | Dashboard API | **Implemented** | `api_server.py` | port 8765 |
| Ops | Decision Trace | **Implemented** | `database/decision_trace.py` | trace_id 연결 |
| Ops | E2E 통합 테스트 (47 PASS) | **Implemented** | `analysis/acceptance_test.py` | 2026-06-29 47/47 |
| Ops | 주간 심사 파이프라인 | **Implemented** | `analysis/ops_weekly_review.py` | A~E 자동판정 |

---

## H. 증거 등급 현황

| 지표 | 현재값 | 목표 |
|------|--------|------|
| Evidence Level | E1 (29건 수준, 확인 필요) | E2 = 30건 + KPI |
| RAE 거래 수 | 0건 (disabled) | 20건 (Go-Live 조건) |
| ML Filter AUC | 확인 필요 (shadow) | ≥ 0.60 (실전 전환 조건) |
| 수용 기준 | 47/47 PASS | 유지 |

---

## 요약: 핵심 미완성 사항

| 우선순위 | 기능 | 완료 조건 |
|----------|------|-----------|
| 1 | **RAE 활성화** | 20건 이상 Shadow 거래 + 7조건 판정 |
| 2 | **ML Filter 실전 전환** | 50건 이상 + AUC ≥ 0.60 |
| 3 | **E2 달성** | 30건 + KPI 충족 + E2_REVIEW_CHECKLIST |
