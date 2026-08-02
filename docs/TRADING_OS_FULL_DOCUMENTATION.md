# Trading OS — 전체 시스템 문서

> **ruleset_version**: v1.3.1-final  
> **최종 갱신**: 2026-07-05  
> **이 파일은 아래 13개 문서의 통합본입니다.**

---

## 목차

1. [시스템 총괄 개요](#1-시스템-총괄-개요)
2. [아키텍처 상세](#2-아키텍처-상세)
3. [스윙 SMC 전략 명세서](#3-스윙-smc-전략-명세서)
4. [SMC 진입 엔진 상세](#4-smc-진입-엔진-상세)
5. [RAE & TED 명세](#5-rae--ted-명세)
6. [리스크 & 청산 프레임워크](#6-리스크--청산-프레임워크)
7. [Cooldown & 재진입 매트릭스](#7-cooldown--재진입-매트릭스)
8. [런타임 흐름 & 일일 운영](#8-런타임-흐름--일일-운영)
9. [파일 & 모듈 맵](#9-파일--모듈-맵)
10. [Trade Tag 스키마](#10-trade-tag-스키마)
11. [리포팅 & 검증 체계](#11-리포팅--검증-체계)
12. [로그 & 알림 체계](#12-로그--알림-체계)
13. [구현 상태 매트릭스](#13-구현-상태-매트릭스)

---

# 1. 시스템 총괄 개요

> **대상 독자**: 운영자, 총괄 매니저, 신규 개발자

## 1.1 시스템 목표

키움증권 API를 통해 국내 주식 시장에서 **스윙 트레이딩**을 자동화하는 시스템.

핵심 원칙:
- **스윙 전략 전용** — 인트라데이/단타 없음. 평균 보유 1~5영업일.
- **SMC(Smart Money Concept) 기반** — CHoCH(Character of Change), 유동성 스윕, Order Block 중심.
- **안정성 우선** — 속도보다 재현성, 검증 가능성, 최소 변경.
- **실계좌 운용 중** (`dry_run: false`).

## 1.2 현재 지원 시장/전략

| 항목 | 현재 상태 |
|------|-----------|
| 시장 | 국내 주식 (KOSPI/KOSDAQ) |
| 전략 | 스윙 SMC 단일 전략 (`entry_mode: smc`) |
| 자본 관리 | 종목당 10~100% 유동 비중 (등급·구조 기반) |
| 롱온리 | 매수 포지션만 (숏 없음) |
| 브로커 | 키움증권 API (REST + WebSocket) |
| DB | PostgreSQL (`trading_system`) |

## 1.3 전체 아키텍처 개요

```
[장 마감] → swing_runner.py (15:35)
              ↓
            후보 선정 (score ≥ 5 + trigger)
              ↓
            logs/swing_orders_YYYYMMDD.json
              ↓
[장 시작] → swing_executor.py (09:00)
              ↓
            후보 매수 + 기존 포지션 관리
              ↓
[장중] ←→ main_auto_trading.py (상시)
              ├── Signal Orchestrator   L0~L6 필터 파이프라인
              ├── SMC Strategy          CHoCH → Sweep → OB → Entry
              ├── DrawdownEngine        DD 레벨 관리 (NORMAL/CAUTION/DANGER/HALT)
              ├── ExitLogic             Hard Stop / LCL / Trailing / TP / Time
              ├── RAEDetector           2nd wave 진입 (현재 disabled)
              └── ReentryMetrics        재진입 쿨다운 + EF 감시
              ↓
[장 후] → AI Research Layer (자동 분석)
              ├── Observer AI (07:32)   전날 장 분석
              ├── Analyst AI (15:30)    당일 세션 분석
              ├── Scientist AI (금 16:40) 주간 가설 검증
              └── Governance AI (온디맨드) 설계 변경 심의
```

## 1.4 하루 운영 흐름 요약

| 시점 | 이벤트 | 담당 |
|------|--------|------|
| 전날 15:35 | 스윙 후보 선정 + 주문큐 생성 | `swing_runner.py` |
| 장전 | AI Gate 점수 확인 | `strategy_hybrid.yaml` |
| 09:00 | 스윙 매수 실행 | `swing_executor.py` |
| 09:00~15:30 | 장중 신호 평가 + 진입/청산 | `main_auto_trading.py` |
| 15:30 | 장 마감 + 리포트 | `analysis/daily_trade_report.py` |
| 15:55 | Health Check | cron |
| 매 30분 | Returns Collector | cron |

## 1.5 구현 완료 범위

### 완전 구현 (Implemented)

| 분야 | 내용 |
|------|------|
| 전략 엔진 | SMC CHoCH + 유동성 스윕 + OB + Reclaim |
| 진입 등급 | A/B/C급 + 등급별 포지션 사이즈 |
| 리스크 엔진 | Hard Stop / LCL v2.1 / Early Failure / DrawdownEngine |
| 청산 엔진 | TP1/TP2 + ATR Trailing + Time Exit + Overnight |
| 재진입 제어 | 쿨다운 시스템 + EF Subtype 분류 + Override 남용 방지 |
| 포지션 사이징 | 등급×G3×DD×Session×Conservative 복합 배율 |
| Signal Orchestrator | L0~L6 독립 필터 파이프라인 |
| AI Research Layer | 4개 AI Agent (Observer/Analyst/Scientist/Governance) |
| 거래 DB | PostgreSQL `trades` 테이블 + `entry_features` JSONB |
| 스윙 전략 | `swing_runner.py` + `swing_executor.py` 크론 등록 |
| 보고 체계 | 일간/주간 리포트 + Scientist Scorecard |

### 부분 구현/Shadow (Partial / Shadow)

| 분야 | 상태 | 비고 |
|------|------|------|
| RAE (2nd wave 진입) | `rae.enabled: false` | 운영 데이터 20건 대기 중 |
| TED (Trend Expansion) | 구현 완료, RAE와 연동 | RAE 비활성으로 사실상 미적용 |
| ML Filter | `shadow_mode: true` | 판단 로그만, 진입 차단 안 함 |
| EQ ML Filter | `shadow_mode: true` | 50건 + AUC≥0.60 달성 후 활성화 |
| Stage B Trend Extension | Disabled | 데이터 부족 |
| EXPLORATION Mode | 활성 (bypass), max 2/day | 실험적 탐색 진입 |

## 1.6 리스크 제어 철학

```
레이어 1 — 진입 필터        (나쁜 진입 차단)
  G3, Stage A, c_late_v2, AI Gate, Orchestrator L0~L6

레이어 2 — 포지션 사이징    (진입해도 작게)
  DD 레벨 × 등급 × Conservative × Session Guard

레이어 3 — 청산 규칙        (손실 빠르게 차단)
  Hard Stop → LCL → Early Failure → Trailing → Time Exit

레이어 4 — 계좌 전체 제어   (시스템 이상 시 전면 차단)
  DrawdownEngine HALT, Emergency Stop
```

## 1.7 긴급 정지 절차

```bash
kill $(pgrep -f "main_auto_trading.py")
kill $(pgrep -f "watchdog.py")
kill $(pgrep -f "api_server.py")
python3 check_account_pnl.py
# 이후 HTS에서 수동 청산
```

---

# 2. 아키텍처 상세

> **대상 독자**: 개발자, 후속 작업자

## 2.1 레이어 분해

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

## 2.2 레이어별 책임과 파일 목록

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
| `core/regime_detector.py` | 시장 레짐 감지 |
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
| `trading/exit_logic_optimized.py` | 청산 조건 평가 |
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

## 2.3 모듈 간 호출 흐름

### 장중 진입 흐름

```
main_auto_trading.py
  └── check_entry_signal(stock_code, df)
        ├── [시간 필터] 09:00~15:30
        ├── [Market Context] NO_TRADE_DAY 체크
        ├── signal_orchestrator.evaluate_signal()  → ACCEPT/REJECT
        ├── smc_strategy.check_entry_signal()
        │     ├── analyze_structure() / detect_choch() / detect_liquidity_sweep()
        │     ├── check_entry_prefilter()   ← 2-of-3 필터
        │     ├── displacement_filter
        │     └── evaluate_choch_grade()   ← A/B/C 등급
        ├── [DrawdownEngine] HALT 시 차단
        └── execute_buy()
              ├── compute_final_size()
              ├── Kiwoom 주문 발송
              └── DB INSERT (entry_features JSONB)
```

### 장중 청산 흐름

```
main_auto_trading.py (polling loop)
  └── check_exit_conditions(stock_code, position, df)
        └── exit_logic.check_exit_signal()
              ├── 1. Hard Stop (-2.0%)
              ├── 2. LCL v2.1
              ├── 3. Early Failure Structure
              ├── 4. TP1 / TP2
              ├── 5. ATR Trailing
              └── 6. Time Exit / Overnight Exit
        └── execute_sell()
              ├── Kiwoom 매도 주문
              ├── DB UPDATE (exit_reason, profit_rate)
              └── reentry_metrics.update_cooldown()
```

## 2.4 상태 저장 위치

| 상태 | 저장 위치 | 휘발 여부 |
|------|-----------|-----------|
| 포지션 정보 | `self.positions` (dict in memory) | 재시작 시 소멸 |
| 거래 기록 | PostgreSQL `trades` 테이블 | 영속 |
| 쿨다운 상태 | `stock_cooldown` (dict in memory) | 재시작 시 소멸 |
| RAE 상태 | `rae_detector._candidates` (memory) | 재시작 시 소멸 |
| 연패 기록 | `data/risk_log.json` | 영속 |
| DD 레벨 | `DrawdownEngine._daily_pnl` (memory) | 재시작 시 소멸 |
| 스윙 주문큐 | `logs/swing_orders_YYYYMMDD.json` | 파일 영속 |
| AI 분석 결과 | PostgreSQL `decision_log`, `research_notebook` | 영속 |

---

# 3. 스윙 SMC 전략 명세서

> **대상 독자**: 운영자, 개발자  
> **관련 파일**: `analyzers/smc/smc_signals.py`, `config/strategy_hybrid.yaml`

## 3.1 전략 목적

**Smart Money Concept(SMC)** 기반 스윙 전략. 기관/세력의 유동성 수집 패턴(Liquidity Sweep → CHoCH → OB)을 포착해 1~5영업일 보유 후 익절.

핵심 전제:
- 세력은 진입 전 저점 쓸어내기(Sweep) 후 방향 전환(CHoCH)
- CHoCH 이후 Order Block(이전 음봉 고점)을 지지선으로 재진입
- HTF(30분봉) 추세와 일치할 때만 진입 (방향 필터)

## 3.2 전략 파라미터 (strategy_hybrid.yaml 기준)

| 항목 | 값 |
|------|-----|
| entry_mode | `smc` |
| min_choch_grade | `B` 이상 |
| require_liquidity_sweep | true (기본값) |
| prefilter_min_conditions | 2-of-3 |
| Swing Hard Stop | -12% (구조손절 미설정 시 fallback) |
| dry_run | false (실거래 중) |

## 3.3 후보 선정 로직 (swing_runner.py — 15:35)

1. 유니버스 스캔: 설정된 종목 풀 일봉 수집 (lookback 120일)
2. 신호 점수화: 패턴 + 추세 + 거래량 종합 점수
3. 필터링: `score ≥ 5 AND trigger = True`
4. AI Gate: ai_score < 20 제외
5. 섹터 중복 방지: 같은 섹터 max 1종목
6. Top-3 선정 후 `logs/swing_orders_YYYYMMDD.json` 저장

## 3.4 장중 진입 파이프라인

```
후보 종목 수신
  ↓ 시간 필터 (09:00~15:30)
  ↓ Market Context 게이트
  ↓ Signal Orchestrator L0~L6
  ↓ SMC 전략 (CHoCH → 등급 평가 → OB pending)
  ↓ DrawdownEngine 체크
  ↓ execute_buy()
```

## 3.5 CHoCH 등급 (A/B/C)

| 등급 | 조건 | 포지션 배율 |
|------|------|-------------|
| **A** | HTF 추세 일치 + Sweep + 강한 OB + 변동성 수축 | ~100% |
| **B** | Sweep 없거나 OB 약함 | ~40~50% |
| **C** | 횡보 내 CHoCH, 변동성 미확장 | ~12% |

## 3.6 포지션 사이징

```
final_size = base × route_mult × g3_mult × dd_mult × session_mult × conservative_mult × ted_mult
```

| 배율 | 값 |
|------|-----|
| A급 base | ~1.0 |
| B급 base | ~0.5 |
| C급 base | ~0.12 |
| RAE route_mult | × 0.7 |
| G3 soft penalty | × 0.6 |
| DD CAUTION | × 0.7 |
| DD DANGER | × 0.4 |
| DD HALT | × 0.0 |
| Conservative Mode | × 0.5 |

## 3.7 G3 / Stage A / c_late_v2 필터

**G3 (v1.3: soft penalty)**

| 조건 | v1.3 현재 |
|------|-----------|
| bdh < 3% (HIGH_PROX) | size × 0.6 (soft penalty) |
| Phase1 09:00~10:00 | delay ≤ 3분 허용 |
| Phase2 10:00~10:30 | delay ≤ 2분 허용 |

"왜 G3가 늦은 진입 방지용인가": bdh가 낮으면 스윕 미완성 구간으로 오인할 수 있는 구조 진입처럼 보이지만 실제론 추격.

**Stage A**: bdh > 15% → 비정상 변동성 차단.

**c_late_v2**: 10:30~13:00 지연 진입 시 품질 추가 필터링.

## 3.8 현재 활성/비활성 기능

| 기능 | 상태 |
|------|------|
| SMC CHoCH 진입 | ✅ 활성 |
| Sweep 탐지 | ✅ 활성 |
| CHoCH 등급 A/B/C | ✅ 활성 |
| G3 soft penalty | ✅ 활성 (v1.3) |
| EXPLORATION Mode | ✅ 활성 (max 2/day) |
| RAE | ❌ disabled |
| ML Filter | 🔵 shadow_mode |
| EQ ML Filter | 🔵 shadow_mode |
| Stage B Trend Extension | ❌ disabled |

---

# 4. SMC 진입 엔진 상세

> **대상 독자**: 개발자  
> **관련 파일**: `analyzers/smc/smc_signals.py`, `smc_structure.py`, `smc_utils.py`

## 4.1 CHoCH를 핵심으로 쓰는 이유

| 항목 | BOS | CHoCH |
|------|-----|-------|
| 의미 | 추세 지속 (같은 방향 고점 돌파) | 추세 전환 (반대 방향 고점 돌파) |
| 진입 적합성 | 추격성 (이미 상승 중) | 전환 초기 (리스크/리워드 유리) |

CHoCH는 세력이 유동성 수집을 완료한 직후 나타나므로 진입 시점이 추세 전환 초기. BOS는 이미 알려진 추세를 쫓는 것.

## 4.2 유동성 스윕 탐지 방식

`detect_liquidity_sweep()` — `analyzers/smc/smc_utils.py`

1. 최근 N봉의 저점 중 swing_low 식별
2. 캔들이 swing_low 아래로 인트라 저가 이탈
3. 해당 캔들이 swing_low 위로 종가 마감 (꼬리)
4. 방향: 상승 CHoCH라면 저점 스윕

세력은 저점 아래 스탑들을 청산시켜 유동성 확보 후 상승 전환. 이 패턴 포착 = 세력 진입 신호.

## 4.3 OB 품질 점수

| 조건 | 판단 |
|------|------|
| 고저 범위 ≥ 0.5% | 강한 OB → A급 가산 |
| 고저 범위 0.2~0.5% | 중간 OB |
| 고저 범위 < 0.2% | 약한 OB → B급 강등 |

OB 수준(고점)이 진입 이후 구조 지지선. OB 이탈 시 Early Failure 또는 Hard Stop 발동.

## 4.4 Reclaim 조건

CHoCH 이후 broken level로 되돌아온 후 반등 확인.

```yaml
reclaim_lookback: 5        # CHoCH 후 5봉 이내
reclaim_tolerance_pct: 0.3 # broken level의 ±0.3% 허용
```

Reclaim = 세력이 broken level을 지지선으로 사용한다는 확인. 가짜 CHoCH 필터링.

## 4.5 Prefilter 2-of-3 구조

| # | 조건 | 파라미터 |
|---|------|----------|
| 1 | HTF 추세 방향 일치 | `prefilter_require_htf_trend: true` |
| 2 | 유동성 스윕 존재 | `prefilter_require_liquidity_sweep: true` |
| 3 | Reclaim 확인 | `prefilter_require_reclaim: true` |

3개 중 2개 이상 충족 시 통과. 모든 조건 강제 시 진입 기회 급감.

## 4.6 Displacement Filter

CHoCH 확정봉이 진짜 displacement인지 검증:
- 이전 N봉 대비 충분히 큰 range
- 거래량 평균 이상
- 방향성이 명확한 캔들 (몸통 > 꼬리)

## 4.7 BAD vs GOOD Late Entry

### BAD Late Entry (차단)
- bdh ≥ 15% → Stage A 비정상 변동성
- +5% 이상 급등 후 추격
- Volume peak 이후 (momentum 소진)

### GOOD Late Entry (RAE 활성화 시 허용 예정)
- Pullback 구조 (되돌림 후 지지)
- Reclaim 발생 + VWAP/EMA20 지지
- RVOL 재확장 (momentum 복원)

> **현재**: RAE disabled이므로 GOOD Late Entry도 차단됨.

## 4.8 신호가 execute_buy()까지 연결되는 흐름

```python
# main_auto_trading.py 요약

def check_entry_signal(stock_code, df):
    if not _is_trading_hours(): return
    if market_context.is_no_trade_day(): return
    if signal_orchestrator.evaluate_signal() != 'ACCEPT': return
    smc_signal, reason, details = smc_strategy.check_entry_signal()
    if not smc_signal:
        # OB pending 등록 (pullback 대기)
        return
    if drawdown_engine.is_halted(): return
    final_size = compute_final_size(base_size, ...)
    execute_buy(stock_code, final_size, reason, details)

def execute_buy(stock_code, size, reason, details):
    order_id = kiwoom.place_buy_order(...)
    db.insert_trade(entry_features=_pending_signal_meta)  # 즉시 INSERT
```

**중요**: `Signal Orchestrator ACCEPT ≠ SMC 진입`. 두 조건 모두 충족해야 `execute_buy()` 도달.

---

# 5. RAE & TED 명세

> **관련 파일**: `analyzers/smc/rae_detector.py`, `analyzers/smc/trend_expansion_detector.py`

## 5.1 설계 철학

| 레이어 | 역할 |
|--------|------|
| SMC (CHoCH) | **진입 조건** — 방향성 확인 |
| TED | **수익 필터** — 추세 확장 검증 |
| RAE | **수익 확장 엔진** — 2nd wave 포착 |

v1.3 = "초입 시스템 + 재가속 수익 엔진 + 구조 기반 확장 필터가 결합된 2-stage SMC trading OS"

## 5.2 RAE 상태 머신

```
IMPULSE → PULLBACK → REACCEL → ENTRY
                            ↓
                      EXPIRED (구조붕괴 / 타임아웃)
```

| 상태 | 설명 | 전이 조건 |
|------|------|-----------|
| `IMPULSE` | CHoCH 이후 첫 impulse 진행 중 | 현재가 < impulse_high - 0.5R → PULLBACK |
| `PULLBACK` | 0.5R 이상 되돌림 진행 | 가격 반등 + 거래량 재확장 → REACCEL |
| `REACCEL` | 재가속 시작 | 진입 조건 채점 → score ≥ 7 → ENTRY |
| `EXPIRED` | 타임아웃(60분) / 구조 붕괴 | 종료 |

### REACCEL → ENTRY 진입 조건 채점

| 조건 | 점수 |
|------|------|
| SMC 구조 유지 (가격 > choch_level) | +2 |
| pullback 깊이 0.5R ~ 1.5R | +2 |
| VWAP 또는 EMA20 지지 | +2 |
| 거래량 재확장 (≥ pullback avg × 1.2) | +2 |
| 가격 반등 중 (최근 3봉 상승) | +1 |
| **합계** | **최대 9, 7점 이상 시 ENTRY** |

### 리셋 조건 및 로그 태그

| 사유 | 태그 |
|------|------|
| 장 시작 전 일일 리셋 | `[RAE_RESET_DAILY]` |
| 구조 붕괴 (broken_level 이탈) | `[RAE_RESET_INVALIDATED]` |
| 180분 이상 stale | `[RAE_RESET_STALE]` |
| 청산 후 RAE 상태 정리 | `[RAE_RESET_AFTER_EXIT]` |

## 5.3 TED (Trend Expansion Detector) 조건

RAE 진입 직전 soft gate. 실패 시 차단 X, size × 0.8.

| 조건 | 판단 기준 |
|------|-----------|
| ATR 재확장 | 현재 ATR ≥ 최근 10봉 최저 ATR × 1.05 |
| RVOL 1.2~3.0 | RVOL 범위 내 + 전봉 대비 유지 |
| HH/HL 유지 | 최근 6봉 중 절반 이상 고점·저점 상승 |
| VWAP 위 유지 | 현재가 ≥ VWAP × (1 - 0.2%) |
| EMA20 slope ≥ 0 | EMA20이 3봉 전 대비 상승 또는 횡보 |

min_pass_count = 3 (5개 중 3개 이상)

## 5.4 Primary vs RAE 진입 차이

| 항목 | Primary | RAE |
|------|---------|-----|
| 진입 시점 | CHoCH 발생 → OB pullback 대기 → 즉시 진입 | CHoCH → impulse → pullback → 재가속 |
| size_mult | 등급·구조 기반 | Primary size × 0.7 |
| TED 적용 | 미적용 | 적용 (soft) |
| 로그 태그 | `[SMC_PRIMARY]` | `[SMC_RAE]`, `[REACCEL_ENTRY]` |
| 현재 상태 | ✅ 활성 | ❌ disabled |

## 5.5 RAE auto_enable_guard (go-live 조건, 7개 모두 충족 필요)

| # | 조건 | 기준값 |
|---|------|--------|
| 1 | RAE 거래수 | ≥ 20건 |
| 2 | RAE 단독 승률 | ≥ 35% |
| 3 | RAE 단독 PF | ≥ 1.10 |
| 4 | RAE avg R | ≥ 0.25 |
| 5 | 전체 PF 유지 | 악화 ≤ 5% |
| 6 | 전체 MDD | baseline 대비 +15% 이내 |
| 7 | RAE LCL/EF 비율 | < 60% |

---

# 6. 리스크 & 청산 프레임워크

> **관련 파일**: `trading/exit_logic_optimized.py`, `core/drawdown_engine.py`

## 6.1 청산 우선순위 표

| 우선순위 | 규칙 | 기준값 | 로그 태그 |
|----------|------|--------|-----------|
| 1 | **Hard Stop** | -2.0% (09:20 이전 유예) | `[HARD_STOP]` |
| 2 | **LCL v2.1** | -1.6%, 진입 후 30분 이내 | `[LCL_EARLY_CUT]` |
| 3 | **Early Failure Structure** | 점수 ≥ 3 (5개 신호) | `[EF_TRIGGER]` |
| 4 | **Swing Hard Stop** | -12% (fallback) | `[SWING_HARD_STOP]` |
| 5 | **TP1** (부분 익절) | +2R / 25% 청산 | `[TP1]` |
| 6 | **TP2** (부분 익절) | +4R / 25% 청산 | `[TP2]` |
| 7 | **ATR Trailing** | activation 1.5%, distance 0.8% | `[TRAILING_STOP]` |
| 8 | **Time Exit** | 최대 보유 봉수 초과 | `[TIME_EXIT]` |
| 9 | **Overnight Exit** | B급 이하 14:50 강제청산 | `[OVERNIGHT_EXIT]` |

## 6.2 Early Failure Structure 신호

| 신호 | 점수 | 설명 |
|------|------|------|
| A: direction_fail | 2 | 진입 방향과 반대로 가격 이동 |
| B: atr_decay | 1 | ATR 감소 (momentum 소진) |
| C: volume_dry | 1 | 거래량 급감 |
| D: MFE 부족 (≤ ATR×0.25) | 1 | 진입 후 최대 이익이 너무 작음 |
| E: N봉 추종 실패 | 1 | N봉 동안 진입 방향 이동 없음 |

**EF Subtype**:
- `ef_no_demand`: Signal D 발동 → 수급 없었음 (쿨다운 45분, override 불가)
- `ef_no_follow`: Signal D 미발동 + 추종 실패 (쿨다운 20분, override 허용)

## 6.3 DrawdownEngine 상태

```
NORMAL  : PnL > -1.5%   → size × 1.0
CAUTION : PnL ≤ -1.5%   → size × 0.7  [DD_CAUTION]
DANGER  : PnL ≤ -3.0%   → size × 0.4  [DD_DANGER]
HALT    : PnL ≤ -5.0%   → 진입 차단    [DD_HALT]
```

## 6.4 청산 사유 매트릭스

| exit_reason | 의미 | 쿨다운 |
|-------------|------|--------|
| `take_profit` | TP1/TP2/trailing 익절 | 0분 |
| `trailing_stop` | ATR trailing 발동 | 30분 |
| `time_exit` | 시간 초과 강제 청산 | 20분 |
| `hard_stop` | Hard Stop 발동 | 60분 |
| `stop_loss` | 일반 손절 | 45분 |
| `lcl_early_cut` | LCL 조기 손절 | 45분 |
| `ef_no_demand` | EF: MFE 부족 | 45분, override 불가 |
| `ef_no_follow` | EF: 추종 실패 | 20분, override 허용 |
| `early_failure` | EF: 미분류 | 60분, override 불가 |
| `overnight_exit` | Overnight 강제 청산 | 0분 |

---

# 7. Cooldown & 재진입 매트릭스

> **관련 파일**: `metrics/reentry_metrics.py`, `main_auto_trading.py`

## 7.1 Primary↔RAE 충돌 방지 규칙 (v1.3 신규)

| 규칙 | 조건 | 효과 | 태그 |
|------|------|------|------|
| **Rule A** | 동일 종목 포지션 보유 중 | RAE 진입 금지 | `[RAE_BLOCK] RULE_A` |
| **Rule B** | Primary LCL/EF/Hard Stop 후 | RAE 15분 대기 | `[RAE_BLOCK] RULE_B` |
| **Rule C** | RAE 실패 후 | 당일 재RAE 금지 | `[RAE_BLOCK] RULE_C` |
| **Rule D** | 수익 청산 (take_profit 등) | 위 규칙 예외 | — |
| **Rule E** | RAE cooldown override | TED valid + score ≥ 70 모두 필요 | — |

## 7.2 청산 사유 × 재진입 매트릭스

| 청산 경로 | 청산 사유 | Primary 재진입 | RAE 재진입 |
|-----------|----------|----------------|------------|
| PRIMARY | TAKE_PROFIT | ✅ 기존 cooldown | ✅ 기존 cooldown |
| PRIMARY | LCL_EARLY_CUT | ✅ (45분) | ⛔ 15분 대기 (Rule B) |
| PRIMARY | EARLY_FAILURE | ✅ (60분) | ⛔ 15분 대기 (Rule B) |
| PRIMARY | HARD_STOP | ✅ (60분) | ⛔ 15분 대기 (Rule B) |
| RAE | TAKE_PROFIT | ✅ 기존 cooldown | ✅ 기존 cooldown |
| RAE | LCL_EARLY_CUT | ✅ 기존 cooldown | ⛔ 당일 금지 (Rule C) |
| RAE | EARLY_FAILURE | ✅ 기존 cooldown | ⛔ 당일 금지 (Rule C) |

## 7.3 Override 허용 매트릭스

| override 트리거 | Primary 재진입 | RAE 재진입 |
|----------------|----------------|------------|
| Squeeze (BB≤15% + vol×2.5) | ✅ | ✅ (+ TED valid 필요) |
| Momentum (ROC≥2.5% + RSI≥65) | ✅ | ✅ (+ RAE score≥70 필요) |
| ef_no_demand 청산 후 | ❌ | ❌ |
| early_failure 청산 후 | ❌ | ❌ |
| hard_stop 청산 후 | ❌ | ❌ |

## 7.4 일일 상태 리셋

```
장 시작 전 (daily_reset):
  smc_pending.clear()
  rae_detector._candidates.clear()   → [RAE_RESET_DAILY]
  _primary_fail_ts.clear()
  _rae_daily_failed.clear()
```

---

# 8. 런타임 흐름 & 일일 운영

> **대상 독자**: 운영자

## 8.1 하루 타임라인

```
[전날 15:35] swing_runner.py — 후보 선정 + 주문큐 생성
[전날 15:55] health_check 크론
[09:00]      swing_executor.py — 스윙 매수 실행
[09:00~15:30] main_auto_trading.py — 장중 운영
[15:30]      Analyst AI — 당일 세션 분석
[15:30~]     일간 리포트 자동 생성
[매 30분]    returns_collector 크론
[금 16:40]   Scientist AI — 주간 가설 검증
```

## 8.2 swing_runner.py가 하는 일

1. 유니버스 스캔 (lookback 120일 일봉)
2. 보유 포지션 홀딩 판단 (HOLD / TRAIL / SELL / ADD)
3. 신호 점수화 (score ≥ 5 AND trigger = True)
4. AI Gate 필터 (ai_score < 20 제외)
5. 섹터 중복 방지 (동일 섹터 max 1)
6. Top-3 선정 → `logs/swing_orders_YYYYMMDD.json` 저장
7. 보유 종목 MFE/MAE DB 갱신

## 8.3 장전 운영자 확인

```bash
# 주문큐 확인
cat logs/swing_orders_$(date +%Y-%m-%d).json | python3 -m json.tool

# 연패 상태
python3 -c "import json; d=json.load(open('data/risk_log.json')); print('연패:', d['consecutive_losses'])"

# 시스템 상태
python3 -m analysis.os_status

# 프로세스 확인
pgrep -f main_auto_trading.py && echo "실행중" || echo "중단됨"
```

## 8.4 장중 메인 루프 흐름

```
메인 루프 초기화
  └── 주문큐 로딩 / 계좌 잔고 조회 / DrawdownEngine 초기화 / RAE 일일 리셋

for stock_code in watchlist:
    check_entry_signal(stock_code, df)    ← 진입 평가
    check_exit_conditions(stock_code)     ← 청산 평가
    check_rae_candidates(stock_code, df)  ← RAE 추적 (현재 disabled)
```

## 8.5 장애/재시작 시 복구

| 상태 | 자동 복구 | 수동 확인 필요 |
|------|-----------|----------------|
| 보유 포지션 | ✅ PostgreSQL 재로딩 가능 | 금액/수량 대조 |
| 쿨다운 상태 | ❌ 재시작 시 초기화 | 쿨다운 리셋됨 인지 |
| DD 레벨 | ❌ 재시작 시 초기화 | 당일 손익 수동 확인 |
| 연패 기록 | ✅ `data/risk_log.json` 유지 | — |
| 스윙 주문큐 | ✅ 파일 유지 | — |

재시작 절차:

```bash
python3 check_account_pnl.py   # 포지션 확인
python3 main_auto_trading.py & # 재시작
python3 watchdog.py &          # watchdog 재시작
```

## 8.6 일일 정상 동작 확인 기준

| 항목 | 정상 상태 |
|------|-----------|
| main_auto_trading.py | 실행 중 |
| auto_trading_YYYYMMDD.log | 당일 날짜로 갱신됨 |
| DrawdownEngine | NORMAL 또는 CAUTION |
| 연패 수 | 3 미만 권장 |

---

# 9. 파일 & 모듈 맵

> **대상 독자**: 개발자 (코드 탐색용 인덱스)

## 9.1 핵심 실행 파일

| 파일 | 역할 | 주요 함수 | 상태 |
|------|------|-----------|------|
| `main_auto_trading.py` | 장중 메인 오케스트레이션 (~5000줄) | `check_entry_signal()`, `execute_buy()`, `execute_sell()` | Implemented |
| `swing_runner.py` | 스윙 후보 선정 크론 (15:35) | `scan_new_signals()` | Implemented |
| `swing_executor.py` | 스윙 매수 실행 크론 (09:00) | — | Implemented |
| `watchdog.py` | 프로세스 감시/재시작 | — | Implemented |
| `api_server.py` | 대시보드 백엔드 (port 8765) | — | Implemented |

## 9.2 전략 / 신호 파일

| 파일 | 역할 | 주요 클래스/함수 | 상태 |
|------|------|-----------------|------|
| `analyzers/smc/smc_signals.py` | SMC 진입 신호 엔진 | `SMCStrategy`, `evaluate_choch_grade()`, `check_entry_prefilter()` | Implemented |
| `analyzers/smc/smc_structure.py` | BOS/CHoCH 구조 분석 | `SMCStructureAnalyzer` | Implemented |
| `analyzers/smc/smc_utils.py` | 스윙포인트/스윕 유틸 | `detect_liquidity_sweep()`, `find_swing_points()` | Implemented |
| `analyzers/smc/rae_detector.py` | RAE 상태 머신 | `RAEDetector`, `register()`, `check()` | Implemented (disabled) |
| `analyzers/smc/trend_expansion_detector.py` | TED 5조건 검증 | `TrendExpansionDetector` | Implemented (RAE 종속) |
| `analyzers/signal_orchestrator.py` | L0~L6 필터 파이프라인 | `evaluate_signal()` | Implemented |
| `analyzers/swing/signal_engine.py` | 스윙 신호 점수화 | `SignalEngine` | Implemented |
| `analyzers/swing/state_machine.py` | 스윙 포지션 상태관리 | `SwingStateManager`, `SwingPosition` | Implemented |
| `analyzers/multi_timeframe_consensus.py` | HTF 30분봉 추세 확인 | — | Implemented |

## 9.3 리스크 / 청산 파일

| 파일 | 역할 | 주요 클래스/함수 | 상태 |
|------|------|-----------------|------|
| `trading/exit_logic_optimized.py` | 청산 조건 평가 엔진 | `OptimizedExitLogic`, `check_exit_signal()`, `check_early_failure_structure()` | Implemented |
| `core/drawdown_engine.py` | 계좌 DD 레벨 관리 | `DrawdownEngine`, `update()`, `is_halted()`, `get_size_mult()` | Implemented |
| `core/position_sizing.py` | 최종 사이즈 배율 함수 | `compute_final_size()` | Implemented (v1.3) |
| `core/edt_sizer.py` | EDT Kelly Sizer | `EDTSizer`, `check_session_guards()` | Implemented |
| `metrics/reentry_metrics.py` | 재진입 쿨다운 + EF 분류 | `ReentryMetrics`, `categorize_exit_reason()`, `check_cooldown_override()` | Implemented |

## 9.4 분석 / 오프라인 도구 (핵심)

| 파일 | 역할 | 실행 방법 |
|------|------|-----------|
| `analysis/daily_trade_report.py` | 일간 거래 리포트 | `python3 -m analysis.daily_trade_report` |
| `analysis/ops_weekly_review.py` | 주간 심사 (A~E 판정) | `python3 -m analysis.ops_weekly_review` |
| `analysis/acceptance_test.py` | 수용 기준 47개 검증 | `python3 -m analysis.acceptance_test` |
| `analysis/e2e_integration_test.py` | E2E 통합 테스트 | `python3 -m analysis.e2e_integration_test` |
| `analysis/os_status.py` | 운영 상태 요약 | `python3 -m analysis.os_status` |
| `analysis/cooldown_optimizer.py` | 쿨다운 최적화 | `python3 -m analysis.cooldown_optimizer [--days 7]` |
| `analysis/ef_sensitivity_analyzer.py` | EF 민감도 분석 | `python3 -m analysis.ef_sensitivity_analyzer [--days 7]` |
| `backtests/rae_validation_runner.py` | RAE 성과 검증 | `python3 -m backtests.rae_validation_runner [--days 60]` |

## 9.5 AI Research Layer

| 파일 | 역할 | 실행 시점 |
|------|------|-----------|
| `analysis/analyst_ai.py` | 당일 세션 분석 | 15:30 크론 |
| `analysis/scientist_ai.py` | 주간 가설 검증 | 금 16:40, 22:00 |
| `analysis/governance_ai.py` | 설계 변경 심의 | 온디맨드 |
| `analysis/strategy_ai.py` | 전략 성과 평가 + evidence level | 온디맨드 |
| `analysis/approve_proposal.py` | E2 미만 변경 차단 | 온디맨드 |

## 9.6 수정 시 필수 확인 파일

| 수정 대상 | 함께 확인할 파일 |
|-----------|-----------------|
| 진입 조건 | `smc_signals.py`, `signal_orchestrator.py`, `strategy_hybrid.yaml` |
| 청산 조건 | `exit_logic_optimized.py`, `strategy_hybrid.yaml` |
| 포지션 사이즈 | `position_sizing.py`, `edt_sizer.py`, `risk_manager.py` |
| 재진입 규칙 | `reentry_metrics.py`, `strategy_hybrid.yaml` |
| DB 스키마 | `database/trading_db.py`, `core/trade_capture.py` |
| RAE/TED | `rae_detector.py`, `trend_expansion_detector.py`, `main_auto_trading.py` |

---

# 10. Trade Tag 스키마

> **저장 위치**: `trades.entry_features` JSONB 컬럼

## 10.1 필드 목록

| 필드 | 타입 | 값 예시 | 설명 |
|------|------|---------|------|
| `entry_route` | str | `PRIMARY` / `RAE` | 진입 경로 |
| `entry_setup` | str | `SMC_OB` / `SMC_RECLAIM` / `SMC_RAE` | 구체적 진입 패턴 |
| `smc_grade` | str | `A` / `B` / `C` | CHoCH 등급 |
| `entry_time_bucket` | str | `OPEN` / `OPEN_LATE` / `MID` / `LATE` | 진입 시간대 |
| `g3_penalty_applied` | bool | `true` / `false` | G3 soft penalty 적용 여부 |
| `g3_penalty_mult` | float | `0.6` | G3 penalty 배율 (1.0 = 미적용) |
| `ted_valid` | bool\|null | `true` / `false` / `null` | TED 검증 결과 (RAE만) |
| `ted_score` | int\|null | `3` / `null` | TED 통과 조건 수 (0~5) |
| `rae_score` | int\|null | `7` / `null` | RAE 진입 점수 (0~9) |
| `reentry_flag` | bool | `false` | 재진입 여부 |
| `cooldown_override_used` | bool | `false` | cooldown override 사용 여부 |
| `size_components` | dict | (아래 참조) | 사이즈 계산 컴포넌트 분해 |
| `conf` | float | `0.650` | 진입 신뢰도 |
| `size_mult` | float | `0.35` | 최종 적용 사이즈 배율 |

### size_components 구조

```json
{
  "base":          0.5,
  "route_mult":    0.7,
  "g3_mult":       0.6,
  "dd_mult":       1.0,
  "ted_mult":      1.0,
  "reclaim_bonus": 1
}
```

## 10.2 시간대 (entry_time_bucket) 정의

| 버킷 | 시간 범위 | 특성 |
|------|-----------|------|
| `OPEN` | 09:00 ~ 10:00 | 장 초반, G3 Phase1 |
| `OPEN_LATE` | 10:00 ~ 10:30 | G3 Phase2 |
| `MID` | 10:30 ~ 13:00 | c_late_v2 적용 구간 |
| `LATE` | 13:00 이후 | c_late_block 적용 |

## 10.3 로그 태그 일람 (진입/RAE)

| 태그 | 위치 | 의미 |
|------|------|------|
| `[SMC_PRIMARY]` | main | Primary OB 진입 확정 |
| `[SMC_RAE]` | main | RAE 진입 확정 |
| `[REACCEL_ENTRY]` | main | REACCEL 상태 진입 감지 |
| `[G3_SOFT_PENALTY]` | orchestrator | HIGH_PROX → size 축소 적용 |
| `[TED_BLOCK]` | main | TED 실패 → soft penalty (차단 아님) |
| `[RAE_REGISTER]` | rae_detector | RAE 후보 등록 |
| `[RAE_PULLBACK]` | rae_detector | IMPULSE→PULLBACK 전환 |
| `[RAE_REACCEL]` | rae_detector | PULLBACK→REACCEL 전환 |
| `[RAE_SIG]` | rae_detector | RAE 진입 신호 확정 |
| `[RAE_BLOCK]` | main | 충돌 방지 규칙으로 RAE 차단 |

## 10.4 SELL 시점 BUY 태그 참조 SQL

```sql
SELECT
    b.entry_features->>'entry_route'  AS route,
    b.entry_features->>'smc_grade'    AS grade,
    s.exit_category,
    s.profit_rate
FROM trades b
JOIN trades s ON b.stock_code = s.stock_code
             AND b.entry_time  = s.entry_time
             AND s.trade_type  = 'SELL'
WHERE b.trade_type = 'BUY'
ORDER BY b.trade_time DESC;
```

---

# 11. 리포팅 & 검증 체계

> **관련 파일**: `analysis/`, `backtests/`

## 11.1 리포트 종류

| 리포트 | 실행 | 주기 |
|--------|------|------|
| 일간 거래 리포트 | `python3 -m analysis.daily_trade_report` | 매일 |
| 스윙 성과 리포트 | `python3 -m analysis.swing_report` | 매일 |
| 주간 심사 | `python3 -m analysis.ops_weekly_review` | 금요일 |
| 수용 기준 47개 | `python3 -m analysis.acceptance_test` | 필요 시 |
| E2E 통합 테스트 | `python3 -m analysis.e2e_integration_test` | 필요 시 |
| RAE 성과 검증 | `python3 -m backtests.rae_validation_runner --days 60` | RAE 활성화 전 |
| 쿨다운 최적화 | `python3 -m analysis.cooldown_optimizer --days 7` | 오프라인 |
| EF 민감도 분석 | `python3 -m analysis.ef_sensitivity_analyzer --days 7` | 오프라인 |

## 11.2 성과 지표 정의

| 지표 | 정의 |
|------|------|
| **PF (Profit Factor)** | `sum(win_pnl) / sum(loss_pnl)` |
| **avg R** | `mean(pnl / risk_amount)` |
| **MDD** | Peak-to-trough 최대 손실 |
| **MAE** | `(entry_price - trough) / entry_price` |
| **MFE** | `(peak - entry_price) / entry_price` |
| **WR** | `win_count / total_count` |

avg R 기준: ≥ 0.3 = 적정, ≥ 0.5 = 우수.

## 11.3 Evidence Level (증거 등급)

| 레벨 | 기준 | 허용 행동 |
|------|------|-----------|
| **E0** | < 10건 | 데이터 부족, 변경 금지 |
| **E1** | 10~29건 | 최소 변경만 (현재 수준) |
| **E2** | 30건 + KPI 충족 | E2_REVIEW_CHECKLIST 5항목 완성 후 심사 |
| **E3** | E2 + 반복 재현 | 구조 변경 승인 가능 |

**변경 승인 흐름**: `아이디어 → NB → Evidence → Hypothesis → Experiment → Approval → Implementation`

## 11.4 주간 심사 자동 판정 (A~E)

| 항목 | 내용 | 자동/수동 |
|------|------|-----------|
| A | 수익성 (PF ≥ 1.2, avg R ≥ 0.3, MDD ≤ 15%) | 자동 |
| B | 전략 구조 (EF/LCL 비율 < 50%) | 자동 |
| C | Scientist 판정 | **수동** |
| D | 증거 수준 (E0~E3) | 자동 |
| E | 다음 주 액션 힌트 | 자동 |

---

# 12. 로그 & 알림 체계

## 12.1 로그 파일 목록

| 파일 | 내용 | 갱신 주기 |
|------|------|-----------|
| `logs/auto_trading_YYYYMMDD.log` | 메인 거래 이벤트 전체 | 장중 실시간 |
| `logs/auto_trading_errors.log` | 에러 전용 | 발생 시 |
| `logs/signal_orchestrator.log` | L0~L6 Orchestrator 평가 결과 | 장중 실시간 |
| `logs/smc_decision_YYYYMMDD.log` | CHoCH 감지 기록 | 장중 실시간 |
| `logs/sweep_attempt_YYYYMMDD.log` | 유동성 스윕 탐지 기록 | 장중 실시간 |
| `logs/reentry_report_YYYY-MM-DD.json` | 재진입 / EF 통계 | 장 마감 후 |
| `data/risk_log.json` | 연패 기록 | 거래 시 |
| `logs/swing_orders_YYYYMMDD.json` | 스윙 주문큐 | 15:35 생성 |

## 12.2 핵심 로그 태그 목록

### 진입 관련

| 태그 | 의미 |
|------|------|
| `[SMC_PRIMARY]` | Primary CHoCH 진입 확정 |
| `[SMC_RAE]` | RAE 진입 확정 |
| `[G3_SOFT_PENALTY]` | HIGH_PROX → size × 0.6 |
| `[TED_BLOCK]` | TED 실패 → soft penalty |
| `[EXPLORATION_KILLED]` | EXPLORATION 승률 미달 자동 비활성화 |

### 청산 관련

| 태그 | 의미 |
|------|------|
| `[HARD_STOP]` | Hard Stop 발동 |
| `[LCL_EARLY_CUT]` | LCL 조기 손절 |
| `[EF_TRIGGER]` | Early Failure Structure 발동 |
| `[TP1]` / `[TP2]` | 부분 익절 |
| `[TRAILING_STOP]` | ATR Trailing Stop 발동 |
| `[TIME_EXIT]` | Time Exit 강제 청산 |
| `[OVERNIGHT_EXIT]` | Overnight 강제 청산 |

### 리스크 엔진

| 태그 | 의미 |
|------|------|
| `[DD_CAUTION]` | DrawdownEngine CAUTION |
| `[DD_DANGER]` | DrawdownEngine DANGER |
| `[DD_HALT]` | DrawdownEngine HALT — 진입 차단 |
| `[DD_STRATEGY_HALT]` | 특정 전략 당일 차단 |
| `[LSG_BLOCK]` | Loss Streak Guard 차단 |

## 12.3 운영 중 반드시 봐야 하는 로그 TOP 10

```bash
TODAY=$(date +%Y%m%d)

# 1. DrawdownEngine HALT (진입 전면 차단)
grep "DD_HALT" logs/auto_trading_${TODAY}.log

# 2. Hard Stop 발동 (큰 손실)
grep "HARD_STOP" logs/auto_trading_${TODAY}.log

# 3. Early Failure 발동
grep "EF_TRIGGER\|ef_no_demand" logs/auto_trading_${TODAY}.log

# 4. LCL 발동
grep "LCL_EARLY_CUT" logs/auto_trading_${TODAY}.log

# 5. 매수/매도 완료
grep "매수완료\|매도완료" logs/auto_trading_${TODAY}.log

# 6. Orchestrator 차단
grep "REJECT\|BLOCK" logs/signal_orchestrator.log | tail -20

# 7. API 에러
grep "ERROR" logs/auto_trading_errors.log | tail -20

# 8. G3 Soft Penalty
grep "G3_SOFT_PENALTY" logs/signal_orchestrator.log

# 9. Loss Streak Guard
grep "LSG_BLOCK" logs/auto_trading_${TODAY}.log

# 10. 연패 상태
python3 -c "import json; d=json.load(open('data/risk_log.json')); print('연패:', d['consecutive_losses'])"
```

## 12.4 로그 태그 작성 규칙

새 로그 추가 시 `[TAG_NAME]` 형식 필수.

```python
logger.info(f"[TAG_NAME] {stock_code}: 메시지 key=val")
```

---

# 13. 구현 상태 매트릭스

> 상태: **Implemented** / **Partial** / **Disabled** / **Shadow** / **Planned**

## 13.1 진입 (Entry)

| 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|
| CHoCH 탐지 | **Implemented** | `smc_structure.py` | 현재 유일 진입 트리거 |
| 유동성 스윕 탐지 | **Implemented** | `smc_utils.py` | Fallback 일 3회 허용 |
| CHoCH 등급 A/B/C | **Implemented** | `smc_signals.py` | — |
| Order Block (OB) | **Implemented** | `smc_signals.py` | OB pullback 대기 |
| Reclaim 확인 | **Implemented** | `smc_signals.py` | 5봉 이내 |
| Prefilter 2-of-3 | **Implemented** | `smc_signals.py` | HTF+Sweep+Reclaim |
| HTF 30분봉 추세 | **Implemented** | `multi_timeframe_consensus.py` | — |
| Displacement Filter | **Implemented** | `smc_signals.py` | 확정봉 검증 |
| RAE (2nd wave) | **Disabled** | `rae_detector.py` | `rae.enabled: false` |
| TED (RAE soft gate) | **Disabled** | `trend_expansion_detector.py` | RAE 종속 |
| EXPLORATION Mode | **Partial** | `main_auto_trading.py` | 활성, max 2/day |
| Stage B Trend Extension | **Disabled** | — | 데이터 부족 |

## 13.2 필터 (Filter)

| 기능 | 상태 | 파일 | 비고 |
|------|------|------|------|
| Signal Orchestrator L0~L6 | **Implemented** | `signal_orchestrator.py` | — |
| G3 품질 게이트 (soft penalty) | **Implemented** | `signal_orchestrator.py` | v1.3 soft 전환 |
| Stage A 게이트 | **Implemented** | `signal_orchestrator.py` | bdh > 15% 차단 |
| c_late_v2 / c_late_block | **Implemented** | `signal_orchestrator.py` | — |
| AI Gate (ai_score) | **Implemented** | `swing_runner.py` | min_score: 20 |
| ML Filter | **Shadow** | `strategy_hybrid.yaml` | shadow_mode: true |
| EQ ML Filter | **Shadow** | `strategy_hybrid.yaml` | shadow_mode: true |

## 13.3 포지션 사이징 (Sizing)

| 기능 | 상태 | 파일 |
|------|------|------|
| 등급 기반 base size | **Implemented** | `main_auto_trading.py` |
| DrawdownEngine 배율 | **Implemented** | `core/drawdown_engine.py` |
| Session Guard 배율 | **Implemented** | `core/edt_sizer.py` |
| Conservative Mode 배율 | **Partial** | 조건부 자동 활성 |
| Loss Streak Guard 배율 | **Partial** | 조건부 자동 활성 |
| G3 soft penalty (×0.6) | **Implemented** | `core/position_sizing.py` |
| RAE route 배율 (×0.7) | **Implemented** | `core/position_sizing.py` |

## 13.4 청산 (Exit)

| 기능 | 상태 | 파일 |
|------|------|------|
| Hard Stop (-2.0%) | **Implemented** | `exit_logic_optimized.py` |
| LCL v2.1 (-1.6%, 30분 이내) | **Implemented** | `exit_logic_optimized.py` |
| Early Failure Structure | **Implemented** | `exit_logic_optimized.py` |
| EF Subtype 분류 | **Implemented** | `exit_logic_optimized.py` |
| TP1 (2R/25%) / TP2 (4R/25%) | **Implemented** | `exit_logic_optimized.py` |
| ATR Trailing Stop | **Implemented** | `exit_logic_optimized.py` |
| Time Exit | **Implemented** | `exit_logic_optimized.py` |
| Overnight Exit (14:50) | **Implemented** | `exit_logic_optimized.py` |
| Swing Hard Stop (-12%) | **Implemented** | `exit_logic_optimized.py` |

## 13.5 재진입 / 쿨다운 (Reentry)

| 기능 | 상태 | 파일 |
|------|------|------|
| exit_reason별 쿨다운 | **Implemented** | `metrics/reentry_metrics.py` |
| Override (Squeeze/Momentum) | **Implemented** | `metrics/reentry_metrics.py` |
| Override 남용 방지 (R2) | **Implemented** | `metrics/reentry_metrics.py` |
| EF Subtype 쿨다운 차등 | **Implemented** | `metrics/reentry_metrics.py` |
| Primary↔RAE 충돌 방지 | **Implemented** | `main_auto_trading.py` |
| Subtype Ratio Drift 감시 (R1) | **Implemented** | `metrics/reentry_metrics.py` |

## 13.6 AI / ML Layer

| 기능 | 상태 | 파일 |
|------|------|------|
| Observer / Analyst / Scientist / Governance AI | **Implemented** | `analysis/` |
| Strategy AI + approve_proposal | **Implemented** | `analysis/` |
| ML Filter (실전 차단) | **Shadow** | `strategy_hybrid.yaml` |
| EQ ML Filter | **Shadow** | `strategy_hybrid.yaml` |

## 13.7 운영 / 인프라

| 기능 | 상태 | 파일 |
|------|------|------|
| 스윙 후보 선정 크론 (15:35) | **Implemented** | `swing_runner.py` |
| 스윙 매수 실행 크론 (09:00) | **Implemented** | `swing_executor.py` |
| Returns Collector 크론 (30분) | **Implemented** | `analysis/returns_collector.py` |
| Watchdog (자동 재시작) | **Implemented** | `watchdog.py` |
| Dashboard API (port 8765) | **Implemented** | `api_server.py` |
| E2E 통합 테스트 (47/47 PASS) | **Implemented** | `analysis/acceptance_test.py` |
| 주간 심사 파이프라인 (A~E) | **Implemented** | `analysis/ops_weekly_review.py` |

## 13.8 핵심 미완성 사항

| 우선순위 | 기능 | 완료 조건 |
|----------|------|-----------|
| 1 | **RAE 활성화** | Shadow 20건 + 7조건 `RAE_GO_LIVE_READY` 판정 |
| 2 | **ML Filter 실전 전환** | 50건 이상 + AUC ≥ 0.60 |
| 3 | **E2 달성** | 30건 + KPI 충족 + `E2_REVIEW_CHECKLIST` 5항목 |
