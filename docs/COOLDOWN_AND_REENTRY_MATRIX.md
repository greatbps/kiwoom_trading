# Cooldown & Reentry Matrix — v1.3

> 청산 사유 × 진입 경로 조합별 재진입 규칙 정의

---

## 1. 충돌 방지 규칙 (v1.3 신규, 작업 3)

### 규칙 A — 동일 종목 동시 진입 금지

```
positions[stock_code] 보유 중 → RAE 진입 금지
rae_candidates[stock_code] 진행 중 → Primary 신규 진입 불가 (OB pending 우선)
```

### 규칙 B — Primary 실패 직후 RAE 15분 대기

```
Primary exit_reason ∈ {lcl, early_cut, early_failure, ef_, hard_stop}
→ _primary_fail_ts[stock_code] = now()
→ RAE 진입 차단 (15분간)
```

태그: `[RAE_BLOCK] RULE_B_PRIMARY_FAIL_COOLDOWN(Nm)`

### 규칙 C — RAE 실패 후 당일 재RAE 금지

```
RAE exit_reason ∈ {early_failure, hard_stop, lcl_early_cut}
→ _rae_daily_failed.add(stock_code)
→ 당일 해당 종목 RAE 경로 완전 차단
```

태그: `[RAE_BLOCK] RULE_C_RAE_FAILED_TODAY`

### 규칙 D — 수익 청산은 예외

```
exit_reason ∈ {take_profit, trailing_stop, overnight_exit}
→ _primary_fail_ts 갱신 없음
→ _rae_daily_failed 추가 없음
→ 기존 cooldown 규칙만 적용
```

### 규칙 E — RAE cooldown override 강화 조건

```
기존 squeeze override가 허용돼도 RAE는 아래 2개 모두 필요:
  1. TED valid = true
  2. RAE confidence_score ≥ 승인 임계값 (기본 70)
```

---

## 2. 청산 사유 × 재진입 매트릭스

| 청산 종목 경로 | 청산 사유 | Primary 재진입 | RAE 재진입 |
|---------------|----------|----------------|------------|
| PRIMARY | TAKE_PROFIT | ✅ 기존 cooldown | ✅ 기존 cooldown |
| PRIMARY | TRAILING_STOP | ✅ (30분 cooldown) | ✅ (30분) |
| PRIMARY | LCL_EARLY_CUT | ✅ (45분 cooldown) | ⛔ **15분 대기** (Rule B) |
| PRIMARY | EARLY_FAILURE | ✅ (60분 cooldown) | ⛔ **15분 대기** (Rule B) |
| PRIMARY | HARD_STOP | ✅ (60분 cooldown) | ⛔ **15분 대기** (Rule B) |
| RAE | TAKE_PROFIT | ✅ 기존 cooldown | ✅ 기존 cooldown |
| RAE | TRAILING_STOP | ✅ (30분 cooldown) | ✅ (30분) |
| RAE | LCL_EARLY_CUT | ✅ 기존 cooldown | ⛔ **당일 금지** (Rule C) |
| RAE | EARLY_FAILURE | ✅ 기존 cooldown | ⛔ **당일 금지** (Rule C) |
| RAE | HARD_STOP | ✅ 기존 cooldown | ⛔ **당일 금지** (Rule C) |

---

## 3. 기존 Cooldown 체계 (v1.2 유지)

| exit_reason | 쿨다운 | override 가능? |
|-------------|--------|----------------|
| `ef_no_demand` | 45분 | ❌ 절대 불가 |
| `early_failure` | 60분 | ❌ 절대 불가 |
| `hard_stop` | 60분 | ❌ 절대 불가 |
| `ef_no_follow` | 20분 | ✅ (구조 살아있음) |
| `stop_loss` | 45분 | ✅ (강한 신호 시) |
| `trailing_stop` | 30분 | ✅ |
| `time_exit` | 20분 | ✅ |
| `take_profit` | 0분 | ✅ |

---

## 4. Override 허용 매트릭스

| override 트리거 | Primary 재진입 | RAE 재진입 |
|----------------|----------------|------------|
| Squeeze (BB≤15% + vol×2.5) | ✅ | ✅ (+ TED valid 필요) |
| Momentum (ROC≥2.5% + RSI≥65) | ✅ | ✅ (+ RAE score≥70 필요) |
| ef_no_demand 청산 후 | ❌ | ❌ |
| early_failure 청산 후 | ❌ | ❌ (Rule C로 당일 금지) |
| hard_stop 청산 후 | ❌ | ❌ |

---

## 5. 일일 상태 리셋 시점

```
장 시작 전 (daily_reset 루프):
  self.smc_pending.clear()
  self.rae_detector._candidates.clear()   → [RAE_RESET_DAILY]
  self._primary_fail_ts.clear()
  self._rae_daily_failed.clear()
```
