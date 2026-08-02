# 파일 & 모듈 맵 — 코드 탐색용 인덱스

> **대상 독자**: 개발자, 후속 작업자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)

---

## 핵심 실행 파일

| 파일 | 역할 | 주요 클래스/함수 | 상태 |
|------|------|-----------------|------|
| `main_auto_trading.py` | 장중 메인 오케스트레이션 (~5000줄) | `check_entry_signal()`, `execute_buy()`, `execute_sell()`, `check_exit_conditions()` | Implemented |
| `swing_runner.py` | 스윙 후보 선정 크론 (15:35) | `scan_new_signals()`, `find_upgrade_candidate()` | Implemented |
| `swing_executor.py` | 스윙 매수 실행 크론 (09:00) | — | Implemented |
| `watchdog.py` | 프로세스 감시/재시작 | — | Implemented |
| `api_server.py` | 대시보드 백엔드 (port 8765) | — | Implemented |
| `check_account_pnl.py` | 계좌 손익 확인 | — | Implemented |

---

## 전략 / 신호 파일

| 파일 | 역할 | 주요 클래스/함수 | 상태 |
|------|------|-----------------|------|
| `analyzers/smc/smc_signals.py` | SMC 진입 신호 엔진 | `SMCStrategy`, `evaluate_choch_grade()`, `check_entry_prefilter()`, `check_entry_signal()` | Implemented |
| `analyzers/smc/smc_structure.py` | BOS/CHoCH 구조 분석 | `SMCStructureAnalyzer`, `detect_choch()` | Implemented |
| `analyzers/smc/smc_utils.py` | 스윙포인트/스윕 유틸 | `SwingPoint`, `LiquiditySweep`, `find_swing_points()`, `detect_liquidity_sweep()` | Implemented |
| `analyzers/smc/rae_detector.py` | RAE 상태 머신 | `RAECandidate`, `RAEDetector`, `register()`, `check()`, `expire()` | Implemented (disabled) |
| `analyzers/smc/trend_expansion_detector.py` | TED 5조건 검증 | `TrendExpansionDetector`, `evaluate()` | Implemented (RAE 종속) |
| `analyzers/signal_orchestrator.py` | L0~L6 필터 파이프라인 | `evaluate_signal()` | Implemented |
| `analyzers/swing/signal_engine.py` | 스윙 신호 점수화 | `SignalEngine` | Implemented |
| `analyzers/swing/state_machine.py` | 스윙 포지션 상태관리 | `SwingStateManager`, `SwingPosition`, `SwingState` | Implemented |
| `analyzers/swing/holding_manager.py` | 보유 포지션 홀딩 판단 | `HoldingManager` | Implemented |
| `analyzers/multi_timeframe_consensus.py` | HTF 30분봉 추세 확인 | — | Implemented |
| `analyzers/indicators.py` | 기술적 지표 계산 | VWAP, EMA, ATR, RVOL 등 | Implemented |

---

## 리스크 / 청산 파일

| 파일 | 역할 | 주요 클래스/함수 | 상태 |
|------|------|-----------------|------|
| `trading/exit_logic_optimized.py` | 청산 조건 평가 엔진 | `OptimizedExitLogic`, `check_exit_signal()`, `check_early_failure_structure()` | Implemented |
| `core/drawdown_engine.py` | 계좌 DD 레벨 관리 | `DrawdownEngine`, `update()`, `get_level()`, `is_halted()`, `get_size_mult()` | Implemented |
| `core/risk_manager.py` | 포지션 사이즈 계산 | `RiskManager` | Implemented |
| `core/position_sizing.py` | 최종 사이즈 배율 함수 | `compute_final_size()` | Implemented (v1.3) |
| `core/edt_sizer.py` | EDT Kelly Sizer | `EDTSizer`, `check_session_guards()` | Implemented |
| `metrics/reentry_metrics.py` | 재진입 쿨다운 + EF분류 | `ReentryMetrics`, `categorize_exit_reason()`, `check_cooldown_override()`, `check_override_abuse()` | Implemented |

---

## 데이터 / DB 파일

| 파일 | 역할 | 주요 클래스/함수 | 상태 |
|------|------|-----------------|------|
| `database/trading_db.py` | PostgreSQL 접근 레이어 | `TradingDatabase`, `insert_trade()`, `update_swing_mfe_mae()` | Implemented |
| `database/decision_trace.py` | 의사결정 추적 DB | `DecisionTrace` | Implemented |
| `core/trade_capture.py` | 체결 후 DB 저장 | — | Implemented |
| `core/trade_db.py` | 거래 DB 래퍼 | — | Implemented |

---

## 분석 / 오프라인 도구 (핵심)

| 파일 | 역할 | 실행 방법 | 상태 |
|------|------|-----------|------|
| `analysis/daily_trade_report.py` | 일간 거래 리포트 | `python3 -m analysis.daily_trade_report` | Implemented |
| `analysis/swing_report.py` | 스윙 성과 리포트 | `python3 -m analysis.swing_report` | Implemented |
| `analysis/ops_weekly_review.py` | 주간 심사 (A~E 판정) | `python3 -m analysis.ops_weekly_review` | Implemented |
| `analysis/acceptance_test.py` | 수용 기준 47개 검증 | `python3 -m analysis.acceptance_test` | Implemented |
| `analysis/e2e_integration_test.py` | E2E 통합 테스트 Stage 1~10 | `python3 -m analysis.e2e_integration_test` | Implemented |
| `analysis/os_status.py` | 운영 상태 요약 | `python3 -m analysis.os_status` | Implemented |
| `analysis/os_health_report.py` | 시스템 헬스 리포트 | `python3 -m analysis.os_health_report` | Implemented |
| `analysis/gate_analysis.py` | 진입 게이트 분석 | `python3 -m analysis.gate_analysis` | Implemented |
| `analysis/performance_metrics.py` | 성과 지표 계산 | — | Implemented |
| `analysis/cooldown_optimizer.py` | 쿨다운 최적화 (오프라인) | `python3 -m analysis.cooldown_optimizer [--days 7]` | Implemented |
| `analysis/ef_sensitivity_analyzer.py` | EF 민감도 분석 (오프라인) | `python3 -m analysis.ef_sensitivity_analyzer [--days 7]` | Implemented |
| `analysis/data_quality_check.py` | 데이터 품질 체크 | `python3 -m analysis.data_quality_check` | Implemented |
| `analysis/returns_collector.py` | 수익률 수집 (크론) | 크론 30분 주기 | Implemented |

---

## AI Research Layer

| 파일 | 역할 | 실행 시점 | 상태 |
|------|------|-----------|------|
| `analysis/analyst_ai.py` | 당일 세션 분석 | 15:30 (크론) | Implemented |
| `analysis/scientist_ai.py` | 주간 가설 검증 | 금 16:40, 22:00 | Implemented |
| `analysis/governance_ai.py` | 설계 변경 심의 | 온디맨드 | Implemented |
| `analysis/strategy_ai.py` | 전략 성과 평가 + evidence level | 온디맨드 | Implemented |
| `analysis/approve_proposal.py` | E2 미만 변경 차단 게이트 | 온디맨드 | Implemented |
| `analysis/scientist_scorecard.py` | Scientist 성과 점수 | 온디맨드 | Implemented |

---

## 백테스트 / 검증 도구

| 파일 | 역할 | 상태 |
|------|------|------|
| `backtests/rae_validation_runner.py` | RAE 성과 검증 (S1/S2/S3) | Implemented |
| `analysis/backtest_lcl_v21_final.py` | LCL v2.1 검증 | Implemented |
| `analysis/backtest_early_entry.py` | 조기 진입 백테스트 | Implemented |
| `analysis/backtest_stage_a_final.py` | Stage A 게이트 검증 | Implemented |
| `analysis/shadow_analysis.py` | Shadow 모드 분석 | Implemented |
| `analysis/monte_carlo.py` | 몬테카를로 시뮬레이션 | Implemented |

---

## 설정 파일

| 파일 | 역할 |
|------|------|
| `config/strategy_hybrid.yaml` | 전략 파라미터 전체 (메인) |
| `config/strategy_swing.yaml` | 스윙 전략 전용 |
| `data/risk_log.json` | 연패 기록 (Loss Streak Guard) |

---

## 테스트

| 파일 | 역할 | 상태 |
|------|------|------|
| `tests/test_position_sizing_matrix.py` | 사이징 매트릭스 26개 테스트 | Implemented (26 PASS) |

---

## 수정 시 필수 확인 파일

새 기능 추가/수정 시 반드시 함께 확인:

| 수정 대상 | 함께 확인할 파일 |
|-----------|-----------------|
| 진입 조건 | `smc_signals.py`, `signal_orchestrator.py`, `config/strategy_hybrid.yaml` |
| 청산 조건 | `exit_logic_optimized.py`, `config/strategy_hybrid.yaml` |
| 포지션 사이즈 | `position_sizing.py`, `edt_sizer.py`, `risk_manager.py` |
| 재진입 규칙 | `reentry_metrics.py`, `config/strategy_hybrid.yaml` |
| DB 스키마 | `database/trading_db.py`, `core/trade_capture.py` |
| RAE/TED | `rae_detector.py`, `trend_expansion_detector.py`, `main_auto_trading.py` |
