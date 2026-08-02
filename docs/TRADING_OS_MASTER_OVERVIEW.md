# Trading OS — 시스템 총괄 개요

> **대상 독자**: 운영자, 총괄 매니저, 신규 개발자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `main_auto_trading.py`, `config/strategy_hybrid.yaml`, `CLAUDE.md`

---

## 1. 시스템 목표

키움증권 API를 통해 국내 주식 시장에서 **스윙 트레이딩**을 자동화하는 시스템.

핵심 원칙:
- **스윙 전략 전용** — 인트라데이/단타 없음. 평균 보유 1~5영업일.
- **SMC(Smart Money Concept) 기반** — CHoCH(Character of Change), 유동성 스윕, Order Block 중심.
- **안정성 우선** — 속도보다 재현성, 검증 가능성, 최소 변경.
- **실계좌 운용 중** (`dry_run: false`).

---

## 2. 현재 지원 시장/전략

| 항목 | 현재 상태 |
|------|-----------|
| 시장 | 국내 주식 (KOSPI/KOSDAQ) |
| 전략 | 스윙 SMC 단일 전략 (`entry_mode: smc`) |
| 자본 관리 | 종목당 10~100% 유동 비중 (등급·구조 기반) |
| 롱온리 | 매수 포지션만 (숏 없음) |
| 브로커 | 키움증권 API (REST + WebSocket) |
| DB | PostgreSQL (`trading_system`) |

---

## 3. 전체 아키텍처 개요

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

---

## 4. 하루 운영 흐름 요약

| 시점 | 이벤트 | 담당 |
|------|--------|------|
| 전날 15:35 | 스윙 후보 선정 + 주문큐 생성 | `swing_runner.py` |
| 장전 | AI Gate 점수 확인 | `strategy_hybrid.yaml` |
| 09:00 | 스윙 매수 실행 | `swing_executor.py` |
| 09:00~15:30 | 장중 신호 평가 + 진입/청산 | `main_auto_trading.py` |
| 15:30 | 장 마감 + 리포트 | `analysis/daily_trade_report.py` |
| 15:55 | Health Check | cron |
| 매 30분 | Returns Collector | cron |

---

## 5. 구현 완료 범위

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

### 미구현 (Planned)

- 멀티 전략 동시 운영 포트폴리오 OS
- 실시간 자금 배분 최적화
- 해외 주식 연동

---

## 6. 리스크 제어 철학

Trading OS의 리스크 제어는 **4개 레이어**로 구성됩니다.

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

**핵심 설계 원칙**: 손실을 막는 것보다 나쁜 포지션을 빠르게 털어내는 것이 우선.

---

## 7. 주요 설정 파일

| 파일 | 역할 |
|------|------|
| `config/strategy_hybrid.yaml` | 전략 파라미터 전체 (코드 수정보다 YAML 먼저) |
| `config/strategy_swing.yaml` | 스윙 전략 전용 파라미터 |
| `data/risk_log.json` | 연패 기록 (Loss Streak Guard 상태) |
| `logs/swing_orders_YYYYMMDD.json` | 전날 생성한 스윙 주문큐 |

---

## 8. 긴급 정지 절차

```bash
# 자동매매 즉시 중단
kill $(pgrep -f "main_auto_trading.py")

# watchdog 중단 (자동재시작 방지)
kill $(pgrep -f "watchdog.py")

# 포지션 확인
python3 check_account_pnl.py

# (수동) HTS에서 직접 청산
```

---

## 9. 운영 상태 확인

```bash
# 시스템 전체 상태
python3 -m analysis.os_status

# 수용 기준 (47개 항목)
python3 -m analysis.acceptance_test

# 당일 리포트
python3 -m analysis.daily_trade_report

# 주간 리뷰
python3 -m analysis.ops_weekly_review
```

---

## 10. 향후 확장 포인트

> **주의**: 아래는 미래 설계 방향이며 현재 미구현입니다.

```
[현재] 단일 SMC 스윙 전략 OS
  ↓ E2 (30건+) 달성 후
[다음] RAE 2nd Wave 활성화 (현재 disabled)
  ↓ ML Filter AUC≥0.60 달성 후
[그 다음] ML Filter 실전 전환
  ↓ 멀티전략 데이터 충분 시
[미래] 포트폴리오 OS (멀티전략 자금배분)
```

**Constitution 원칙**: "Architecture Changes Without Evidence" 금지.  
증거(E2 이상)가 쌓인 후에만 다음 단계로 진입.
