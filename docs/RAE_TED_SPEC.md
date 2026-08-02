# RAE & TED Specification — v1.3

> ruleset: v1.3.1-final | 생성: 2026-07-05

---

## 1. 설계 철학

| 레이어 | 역할 |
|--------|------|
| SMC (CHoCH) | **진입 조건** — 방향성 확인 |
| TED | **수익 필터** — 추세 확장 검증 |
| RAE | **수익 확장 엔진** — 2nd wave 포착 |

v1.3 = "초입 시스템 + 재가속 수익 엔진 + 구조 기반 확장 필터가 결합된 2-stage SMC trading OS"

---

## 2. RAE 상태 머신

### 상태 정의

```
IMPULSE → PULLBACK → REACCEL → ENTRY
                            ↓
                      EXPIRED (구조붕괴 / 타임아웃)
```

| 상태 | 설명 | 전이 조건 |
|------|------|-----------|
| `IMPULSE` | CHoCH 이후 첫 impulse 진행 중 | 현재가 < impulse_high - 0.5R → PULLBACK |
| `PULLBACK` | 0.5R 이상 되돌림 진행 | 가격 반등 + 거래량 재확장 → REACCEL |
| `REACCEL` | 재가속 시작 | 진입 조건 5개 채점 → score ≥ 7 → ENTRY |
| `EXPIRED` | 타임아웃(60분) / 구조 붕괴 | 종료 |

### 상태 전이 조건

**IMPULSE → PULLBACK**
- 조건: `current_price < impulse_high - r_unit × pullback_min_r(0.5)`
- 이 시점에서 pullback_low 갱신 시작

**PULLBACK → REACCEL**
- 조건: 가격 반등 `> pullback_low × (1 + 0.003)` AND 거래량 `>= pullback_avg × 1.2`
- pullback_low에서 거래량 수반 반등

**REACCEL → ENTRY (진입 조건 채점)**

| 조건 | 점수 | 설명 |
|------|------|------|
| SMC 구조 유지 (가격 > choch_level) | +2 | 필수 |
| pullback 깊이 0.5R ~ 1.5R | +2 | 너무 얕거나 깊으면 제외 |
| VWAP 또는 EMA20 지지 | +2 | 둘 중 하나 충족 |
| 거래량 재확장 (≥ pullback avg × 1.2) | +2 | momentum 확인 |
| 가격 반등 중 (최근 3봉 상승) | +1 | 방향 확인 |
| **합계** | **최대 9** | **7점 이상 시 ENTRY** |

### 리셋 조건 및 로그 태그

| 사유 | 태그 |
|------|------|
| 장 시작 전 일일 리셋 | `[RAE_RESET_DAILY]` |
| 구조 붕괴 (broken_level 이탈) | `[RAE_RESET_INVALIDATED]` |
| 180분 이상 stale | `[RAE_RESET_STALE]` |
| 청산 후 RAE 상태 정리 | `[RAE_RESET_AFTER_EXIT]` |

---

## 3. TED (Trend Expansion Detector) 조건

RAE 진입 직전 추세 확장 신호를 검증하는 **soft gate** (실패 시 차단 X, size 축소).

| 조건 | 판단 기준 | 점수 |
|------|-----------|------|
| ATR 재확장 | 현재 ATR ≥ 최근 10봉 최저 ATR × 1.05 | +1 |
| RVOL 1.2~3.0 | RVOL 범위 내 + 전봉 대비 유지 | +1 |
| HH/HL 유지 | 최근 6봉 중 절반 이상 고점·저점 상승 | +1 |
| VWAP 위 유지 | 현재가 ≥ VWAP × (1 - 0.2%) | +1 |
| EMA20 slope ≥ 0 | EMA20이 3봉 전 대비 상승 또는 횡보 | +1 |

- **min_pass_count = 3** (5개 중 3개 이상)
- 실패 시: size × 0.8 (소프트 패널티)
- 출력 필드: `strength_score (0~100)`, `rejection_reason`, `ted_passed`

---

## 4. Primary vs RAE 진입 차이

| 항목 | Primary | RAE |
|------|---------|-----|
| 진입 시점 | CHoCH 발생 → OB pullback 대기 → 즉시 진입 | CHoCH 이후 impulse → pullback → 재가속 |
| size_mult | 등급·구조 기반 (A급 ~1.0, B급 ~0.5) | Primary size × 0.7 |
| TED 적용 | 미적용 (초기 비활성) | 적용 (soft) |
| 로그 태그 | `[SMC_PRIMARY]` | `[SMC_RAE]`, `[REACCEL_ENTRY]` |
| DB entry_route | `PRIMARY` | `RAE` |
| cooldown override | 기존 override 규칙 적용 | TED valid + RAE score ≥ 임계값 모두 필요 |

---

## 5. GOOD vs BAD Late Entry

### BAD Late Entry (차단)
- bdh ≥ 10~15% (당일 고저 범위 비정상)
- +5% 이상 급등 이후 추격
- volume peak 이후 (momentum 소진)
- → `c_late_v2` 또는 Stage A로 차단

### GOOD Late Entry (RAE 허용)
- pullback 구조 (되돌림 후 지지)
- reclaim 발생 (broken level 회복)
- VWAP / EMA20 지지 확인
- SMC 구조 유지 (broken level 위)
- RVOL 재확장 (momentum 복원)
- → RAE 경로로 진입 허용

---

## 6. 파라미터 기준값 (config/strategy_hybrid.yaml)

```yaml
rae:
  enabled: false
  timeout_minutes: 60
  pullback_min_r: 0.5
  pullback_max_r: 1.5
  reaccel_bounce_pct: 0.3
  reaccel_vol_ratio: 1.2
  min_entry_score: 7
  rae_size_mult: 0.7

trend_expansion_detector:
  enabled: true
  apply_to_rae_entry: true
  soft_fail_size_mult: 0.8
  min_pass_count: 3
  atr_expansion_ratio: 1.05
  rvol_min: 1.2
  rvol_max: 3.0
  hh_hl_lookback: 6
  vwap_tolerance_pct: 0.2
  ema20_slope_min_pct: 0.0
```
