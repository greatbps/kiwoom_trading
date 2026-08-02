# Trading OS — 아키텍처 상세 문서

> **대상 독자**: 개발자, 후속 작업자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `main_auto_trading.py`, `CLAUDE.md`

---

## 1. 레이어 분해

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 7 — AI / Research Layer                                  │
│  Observer / Analyst / Scientist / Governance AI                 │
├─────────────────────────────────────────────────────────────────┤
│  Layer 6 — Reporting / Validation                               │
│  daily_report / swing_report / rae_validation / acceptance_test │
├─────────────────────────────────────────────────────────────────┤
│  Layer 5 — Risk / Exit / Cooldown                               │
│  DrawdownEngine / ExitLogicOptimized / ReentryMetrics           │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4 — Intraday Signal Engine                               │
│  Signal Orchestrator (L0~L6) + SMC Strategy                     │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3 — Candidate Scan / Strategy                            │
│  swing_runner / SMC prefilter / grade evaluation                │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2 — Execution                                            │
│  execute_buy / execute_sell / order_executor                    │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1 — Data / Broker                                        │
│  Kiwoom REST + WebSocket / PostgreSQL / YAML config             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 레이어별 책임과 파일 목록

### Layer 1 — Data / Broker

| 파일 | 역할 |
|------|------|
| `core/kiwoom_rest_client.py` | Kiwoom REST API 클라이언트 |
| `core/websocket/` | 실시간 WebSocket (체결/잔고) |
| `database/trading_db.py` | PostgreSQL DB 접근 레이어 |
| `config/strategy_hybrid.yaml` | 전략 파라미터 (Single Source of Truth) |
| `config/strategy_swing.yaml` | 스윙 전략 파라미터 |

### Layer 2 — Execution

| 파일 | 역할 |
|------|------|
| `main_auto_trading.py` | `execute_buy()`, `execute_sell()` 구현 (~5000줄) |
| `core/order_executor.py` | 주문 실행 래퍼 |
| `core/trade_capture.py` | 체결 후 DB 저장 |

### Layer 3 — Candidate Scan / Strategy

| 파일 | 역할 |
|------|------|
| `swing_runner.py` | 일봉 기반 후보 선정 (15:35 실행) |
| `swing_executor.py` | 스윙 매수 실행 (09:00 실행) |
| `analyzers/swing/signal_engine.py` | 스윙 신호 점수화 |
| `analyzers/swing/state_machine.py` | 스윙 보유 상태 관리 |
| `analyzers/smc/smc_signals.py` | SMC 진입 신호 (CHoCH + Sweep + OB + Reclaim) |
| `analyzers/smc/smc_structure.py` | 구조 분석 (BOS/CHoCH 탐지) |
| `analyzers/smc/smc_utils.py` | Swing Point / Liquidity Sweep 유틸 |

### Layer 4 — Intraday Signal Engine

| 파일 | 역할 |
|------|------|
| `analyzers/signal_orchestrator.py` | L0~L6 필터 파이프라인 |
| `analyzers/smc/rae_detector.py` | RAE 상태 머신 (현재 disabled) |
| `analyzers/smc/trend_expansion_detector.py` | TED 5조건 검증 (RAE soft gate) |
| `core/market_context.py` | 시장 상태 (NO_TRADE_DAY 등) |
| `core/regime_detector.py` | 시장 레짐 감지 (TREND/RANGING/DEFENSIVE) |
| `analyzers/multi_timeframe_consensus.py` | HTF(30분봉) 추세 확인 |

**Signal Orchestrator L0~L6 파이프라인**:

```
L0 — 기본 필터 (시간/거래량/거래정지)
L1 — 시장 상태 게이트 (NO_TRADE_DAY 차단)
L2 — 신호 품질 필터 (RVOL / 가격 범위)
L3 — G3 품질 게이트 (delay=0 CHoCH, HIGH_PROX → soft penalty)
L4 — Stage A 품질 게이트 (bdh 비정상 차단)
L5 — c_late_v2 (늦은 진입 필터)
L6 — AI Gate (ai_score < min_score 차단)
     → ACCEPT / REJECT
```

### Layer 5 — Risk / Exit / Cooldown

| 파일 | 역할 |
|------|------|
| `core/drawdown_engine.py` | 계좌 DD 레벨 관리 (NORMAL/CAUTION/DANGER/HALT) |
| `trading/exit_logic_optimized.py` | 청산 조건 평가 (Hard Stop / LCL / Trailing / TP / Time) |
| `metrics/reentry_metrics.py` | 쿨다운 + EF subtype 분류 + Override 남용 감시 |
| `core/risk_manager.py` | 포지션 사이즈 계산 (Kelly 기반) |
| `core/position_sizing.py` | 최종 사이즈 배율 함수 (v1.3 신규) |
| `core/edt_sizer.py` | EDT Sizer (Kelly × 섹터가중 × DD캡 × 세션가드) |

**DrawdownEngine 상태**:

```
NORMAL  : PnL > -1.5%   → size × 1.0
CAUTION : PnL ≤ -1.5%   → size × 0.7
DANGER  : PnL ≤ -3.0%   → size × 0.4
HALT    : PnL ≤ -5.0%   → 진입 전면 차단
```

### Layer 6 — Reporting / Validation

| 파일 | 역할 |
|------|------|
| `analysis/daily_trade_report.py` | 일간 거래 리포트 |
| `analysis/swing_report.py` | 스윙 전략 성과 리포트 |
| `analysis/ops_weekly_review.py` | 주간 심사 (A~E 자동판정) |
| `analysis/acceptance_test.py` | 수용 기준 47개 항목 검증 |
| `backtests/rae_validation_runner.py` | RAE 성과 검증 (S1/S2/S3) |
| `analysis/gate_analysis.py` | 진입 게이트 분석 |
| `analysis/performance_metrics.py` | 성과 지표 계산 |
| `analysis/e2e_integration_test.py` | E2E 통합 테스트 (Stage 1~10) |

### Layer 7 — AI / Research Layer

| 파일 | 역할 | 실행 시점 |
|------|------|----------|
| `analysis/analyst_ai.py` | 당일 세션 분석 + decision_log 저장 | 15:30 |
| `analysis/scientist_ai.py` | 주간 가설 검증 + NB 생성 | 금 16:40, 22:00 |
| `analysis/governance_ai.py` | 설계 변경 심의 (GD 초안 생성) | 온디맨드 |
| `analysis/strategy_ai.py` | 전략 성과 평가 + evidence level 판정 | 온디맨드 |
| `analysis/approve_proposal.py` | E2 미만 변경 차단 | 온디맨드 |

---

## 3. 모듈 간 호출 흐름

### 장중 진입 흐름

```
main_auto_trading.py
  └── check_entry_signal(stock_code, df)
        ├── [시간 필터] 09:00~15:30
        ├── [Market Context] NO_TRADE_DAY 체크
        ├── signal_orchestrator.evaluate_signal()
        │     └── L0~L6 파이프라인 → ACCEPT/REJECT
        ├── smc_strategy.check_entry_signal()
        │     ├── analyze_structure()
        │     ├── detect_choch()
        │     ├── detect_liquidity_sweep()
        │     ├── check_entry_prefilter()     ← 2-of-3 필터
        │     ├── displacement_filter         ← 확정봉 검증
        │     └── evaluate_choch_grade()      ← A/B/C 등급
        ├── [DrawdownEngine] DD 레벨 체크 → HALT 시 차단
        └── execute_buy()
              ├── compute_final_size()        ← 포지션 사이즈
              ├── Kiwoom 주문 발송
              └── DB INSERT (entry_features JSONB)
```

### 장중 청산 흐름

```
main_auto_trading.py (polling loop)
  └── check_exit_conditions(stock_code, position, df)
        ├── exit_logic.check_exit_signal()
        │     ├── 1. Hard Stop (-2.0%)
        │     ├── 2. LCL v2.1 (진입 초기 손절)
        │     ├── 3. Early Failure Structure (점수기반)
        │     ├── 4. TP1 (2R/25%) / TP2 (4R/25%)
        │     ├── 5. ATR Trailing Stop
        │     └── 6. Time Exit (봉수 초과)
        └── execute_sell()
              ├── Kiwoom 매도 주문
              ├── DB UPDATE (exit_reason, profit_rate)
              └── reentry_metrics.update_cooldown()
```

---

## 4. 상태 저장 위치

| 상태 | 저장 위치 | 휘발 여부 |
|------|-----------|-----------|
| 포지션 정보 | `self.positions` (dict in memory) | 재시작 시 소멸 |
| 거래 기록 | PostgreSQL `trades` 테이블 | 영속 |
| 쿨다운 상태 | `stock_cooldown` (dict in memory) | 재시작 시 소멸 |
| RAE 상태 | `rae_detector._candidates` (memory) | 재시작 시 소멸 |
| 연패 기록 | `data/risk_log.json` | 영속 |
| 당일 진입 수 | `daily_entry_count` (memory) | 재시작 시 소멸 |
| DD 레벨 | `DrawdownEngine._daily_pnl` (memory) | 재시작 시 소멸 |
| 스윙 주문큐 | `logs/swing_orders_YYYYMMDD.json` | 파일 영속 |
| AI 분석 결과 | PostgreSQL `decision_log`, `research_notebook` | 영속 |

---

## 5. 설정 우선순위

```
코드 하드코딩 < YAML 파라미터 < DB 저장 override
```

**원칙**: 수치 변경은 항상 YAML 먼저. 코드 수정은 최후 수단.

---

## 6. 재시작 복구 절차

재시작 시 소멸되는 상태:
- `self.positions` — PostgreSQL `trades`에서 미청산 포지션 재로딩 가능
- `stock_cooldown` — 재시작 시 초기화 (쿨다운 리셋됨, 운영상 허용)
- `DrawdownEngine._daily_pnl` — 재시작 시 초기화 (당일 손익 재계산 필요)
- `rae_detector._candidates` — 재시작 시 초기화 (RAE 상태 리셋, 허용)

```bash
# 포지션 확인
python3 check_account_pnl.py

# 운영 상태 확인
python3 -m analysis.os_status
```

---

## 7. 주요 이벤트 로그 경로

| 로그 | 위치 | 내용 |
|------|------|------|
| 메인 | `logs/auto_trading_YYYYMMDD.log` | 전체 거래 이벤트 |
| 에러 | `logs/auto_trading_errors.log` | 에러 전용 |
| Signal Orchestrator | `logs/signal_orchestrator.log` | L0~L6 평가 결과 |
| CHoCH | `logs/smc_decision_YYYYMMDD.log` | CHoCH 감지 기록 |
| Sweep | `logs/sweep_attempt_YYYYMMDD.log` | 유동성 스윕 탐지 |
| 재진입 | `logs/reentry_report_YYYY-MM-DD.json` | EF/쿨다운 통계 |
