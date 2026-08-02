# 전략 룰셋 요약 — v1.4 기준

> **문서 목적**: 현재 스윙 전략의 진입/청산/리스크 정책을 v1.4 기준으로 정리  
> **기준일**: 2026-07-05  
> **ruleset_version**: v1.4

---

## ⚠️ 절대 원칙 (변경 불가)

1. **스윙(Swing)만 한다** — 인트라데이/단타 전략 없음
2. **DB는 PostgreSQL만** — SQLite 절대 금지
3. **실계좌 운용 중** — dry_run: false

---

## 1. 진입 정책 (Entry Policy)

### 1.1 레짐 게이트 (v1.4 신규)

**위치**: 모든 신규 진입의 최상위 게이트

| 레짐 | 조건 | 신규 진입 | 사이즈 | RAE |
|------|------|-----------|--------|-----|
| **TREND_UP** | score ≥ +3 | ✅ 허용 | ×1.0 | ✅ |
| **NEUTRAL** | -2~+2 | ✅ 허용 | ×0.7 | ❌ |
| **RISK_OFF** | score ≤ -3 | ❌ 전면 차단 | ×0.0 | ❌ |

로그: `[REGIME]`, `[REGIME_BLOCK]`, `[REGIME_SIZE_ADJUST]`

---

### 1.2 Early Window Block (v1.3.2 신규)

**09:00~09:29:59 신규 진입 전면 금지** — 예외 없음

- 근거: ALPHA=0%, EF=75% (구조적 실패 구간)
- 적용: PRIMARY / RAE / TED / Trend Breakout 모두
- 로그: `[EARLY_WINDOW_BLOCK]`

---

### 1.3 CHoCH 등급 요건 (v1.3.2 업데이트)

**A급만 실전 진입 허용** (v1.3: B급도 허용 → v1.3.2: A급 이상만)

| 등급 | 기준 | 진입 | 사이즈 |
|------|------|------|--------|
| **A+** | score≥80 + RVOL≥1.8 + Sweep + TREND | ✅ 최우선 | 1.0R |
| **A** | score≥80 | ✅ | 0.5R |
| **A-** | score≥80 but Sweep없음 or RVOL부족 | ✅ | 0.3R |
| **B** | 50~79점 | ❌ **차단** | — |
| **C** | <50점 | ❌ 차단 | — |

로그: `[CHOCH_GRADE_BLOCK] grade=B {symbol} reason=min_grade=A`

A→B 강등 경로 (RVOL 부족, 수급 급락): 동일하게 차단

---

### 1.4 진입 시간대

| 구간 | 정책 |
|------|------|
| 09:00~09:29 | ❌ 전면 차단 (v1.3.2) |
| 09:30~13:00 | ✅ A급 허용 |
| 13:00~15:20 | ⚠️ smc_afternoon_cutoff 이후 제한 |

---

### 1.5 공통 진입 게이트 순서

```
STOCK_GATE         (종목별 상태 — 손절 이력, 당일 매매 등)
   ↓
EARLY_WINDOW_BLOCK (09:00~09:30 시간 차단)
   ↓
REGIME_GATE        (RISK_OFF → 차단, NEUTRAL → 사이즈 축소)
   ↓
RAE_BLOCK          (RULE_A/B/C/D — RAE 규칙 검사)
   ↓
PRIMARY_SMC        (CHoCH/Sweep/OB 분석)
   ↓
CHOCH_GRADE_BLOCK  (B급 downgrade 이후 차단)
   ↓
REGIME_SIZE_ADJUST (레짐 size_mult 적용)
   ↓
execute_buy()
```

---

## 2. 사이징 체계 (Sizing)

최종 포지션 사이즈 계산 순서:

```
base_size (Kelly 기반)
 × CHoCH 등급 배율 (A+=1.0, A=0.5, A-=0.3)
 × A+ bypass_kelly (A+는 Kelly 우회, size=1.0R)
 × DrawdownEngine 배율 (NORMAL×1.0 / CAUTION×0.7 / DANGER×0.4 / HALT×0.0)
 × Session Guard (LSG, Conservative Mode 등)
 × G3 Soft Penalty (bdh<3% → ×0.6)
 × Regime Size Multiplier (TREND_UP×1.0 / NEUTRAL×0.7 / RISK_OFF×0.0)
```

---

## 3. 청산 정책 (Exit Policy)

**변경 없음** — v1.4에서 청산 로직은 수정하지 않음.

| 청산 유형 | 방법 | WR | 비고 |
|-----------|------|----|----|
| **ATR Trailing** | 수익 확정 | 81.8% | 유일한 수익 엔진 — 절대 수정 금지 |
| **EF** | 조기 실패 감지 | — | 평균 손실 -1.3% (Hard Stop 대비 절감) |
| **Hard Stop** | -2.0% 고정 | 2.6% | 최후 안전망 — 수정 금지 |
| **LCL v2.1** | 청산가 기반 손절 | — | 유지 |
| **Time Exit** | 당일/overnight 기준 | — | 유지 |

---

## 4. 리스크 관리 (Risk Management)

### DrawdownEngine

| 레벨 | 조건 | 배율 |
|------|------|------|
| NORMAL | DD < 경고 기준 | ×1.0 |
| CAUTION | 경고 기준 초과 | ×0.7 |
| DANGER | 위험 기준 초과 | ×0.4 |
| HALT | 한도 초과 | ×0.0 (진입 금지) |

### AI Gate (Orchestrator)

최소 점수: 50점 이상 (기본값)

### 일일 한도

- `max_trades_per_day`: 3회 (기본값)
- `daily_loss_limit`: 활성화 (설정 참조)

---

## 5. 비활성 기능 (v1.4 현재)

| 기능 | 상태 | 활성화 조건 |
|------|------|-------------|
| **RAE** | `enabled=false` | Primary PF ≥ 1.0 달성 후 |
| **ML Filter** | Shadow 모드 | 50건 + AUC≥0.60 후 |
| **Breadth Gate** | 설정만 (미연결) | 데이터 소스 확보 후 |
| **B급 진입** | 차단 | 레짐 게이트 안정화 후 재검토 가능 |

---

## 6. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v1.3.1-final | 2026-07-02 | AI Gate min=50, EXPLORATION T+1 |
| v1.3.2 | 2026-07-05 | B급 차단, 09:00~09:30 차단 |
| v1.4 | 2026-07-05 | Market Regime Gate 도입 |

---

## 7. 교정 목표

v1.4 이후 30건 이상 거래 시 아래 지표 확인:

| 지표 | 현재 (113건) | 목표 |
|------|-------------|------|
| WR | 23.9% | ≥ 30% |
| PF | 0.291 | ≥ 0.80 |
| avg_pnl | -0.769% | ≥ -0.3% |
| ALPHA율 | 16.8% | ≥ 22% |
| RISK_OFF 진입 | 미측정 | 0% (레짐 차단) |

목표 미달 시 → `MARKET_REGIME_GATE_V14_SPEC.md` 참조 후 임계값 재조정.
