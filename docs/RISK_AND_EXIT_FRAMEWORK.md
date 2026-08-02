# 리스크 & 청산 프레임워크

> **대상 독자**: 운영자, 개발자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `trading/exit_logic_optimized.py`, `core/drawdown_engine.py`, `config/strategy_hybrid.yaml`

---

## 1. 청산 우선순위 표

아래 순서대로 체크. 먼저 조건을 만족한 규칙이 발동.

| 우선순위 | 규칙 | 기준값 | 로그 태그 |
|----------|------|--------|-----------|
| 1 | **Hard Stop** | -2.0% (09:20 이전 유예) | `[HARD_STOP]` |
| 2 | **LCL v2.1** (Loss Cut Limiter) | 진입 초기 손실 기준 | `[LCL_EARLY_CUT]` |
| 3 | **Early Failure Structure** | 점수 ≥ 3 (5개 신호) | `[EF_TRIGGER]` |
| 4 | **Swing Hard Stop** | -12% (구조손절 미설정 fallback) | `[SWING_HARD_STOP]` |
| 5 | **TP1** (부분 익절) | +2R / 25% 청산 | `[TP1]` |
| 6 | **TP2** (부분 익절) | +4R / 25% 청산 | `[TP2]` |
| 7 | **ATR Trailing** | activation 2.0%, distance 0.8% | `[TRAILING_STOP]` |
| 8 | **Time Exit** | 최대 보유 봉수 초과 | `[TIME_EXIT]` |
| 9 | **Overnight Exit** | B급 이하 14:50 강제청산 | `[OVERNIGHT_EXIT]` |
| 10 | **DrawdownEngine HALT** | 계좌 -5% → 진입 차단 | `[DD_HALT]` |

---

## 2. 규칙별 상세

### 2.1 Hard Stop

```yaml
risk_control:
  hard_stop_pct: 2.0   # -2.0% 손실 시 강제 청산
```

특수 케이스:
- **09:20 이전 유예**: 시초가 동시호가 왜곡 / 기관 프로그램 노이즈로 09:00~09:20은 Hard Stop 발동 유예
- **Hard Stop Relax**: 특정 조건에서 -2.5%로 완화 (설정 있음, 확인 필요)
- **Swing Hard Stop**: -12% (구조손절 미설정 시 최후 안전망)

"왜 -2.0%인가": ML 분석 결과, 실제 손실 분포에서 -2.5%, -3.09% 등 큰 손실 방지를 위해 -2.5→-2.0으로 조정 (2025-12-16).

### 2.2 LCL v2.1 (Loss Cut Limiter)

진입 후 초기 구간(5~30분)에서 손실이 기준치 초과 시 조기 청산.

```yaml
risk_control:
  early_failure:
    enabled: true
    window_minutes: 30       # 진입 후 30분 이내
    loss_cut_pct: -1.6       # -1.6% 이상 손실 시 발동
```

- 발동 이유: 진입 방향이 즉각 틀릴 경우 손실을 최소화
- "왜 LCL이 Hard Stop보다 앞단에 있는가": LCL은 -1.6%에서 발동, Hard Stop은 -2.0%. LCL이 먼저 발동해 -2.0%까지 손실이 커지는 것을 방지.

### 2.3 Early Failure Structure

진입 후 5~15분 내 구조 붕괴 신호 점수화 → 임계값 초과 시 조기 청산.

```yaml
risk_control:
  early_failure_structure:
    enabled: true
    threshold: 3           # 이 이상이면 조기 청산
```

| 신호 | 점수 | 설명 |
|------|------|------|
| A: direction_fail | 2 | 진입 방향과 반대로 가격 이동 |
| B: atr_decay | 1 | ATR 감소 (momentum 소진) |
| C: volume_dry | 1 | 거래량 급감 |
| D: MFE 부족 (≤ ATR×0.25) | 1 | 진입 후 최대 이익이 너무 작음 |
| E: N봉 추종 실패 | 1 | N봉 동안 진입 방향으로 이동 없음 |

**EF Subtype 자동 분류**:
- `ef_no_demand`: Signal D(MFE 부족) 발동 → 처음부터 수급 없었음 (쿨다운 45분, override 불가)
- `ef_no_follow`: Signal D 미발동 + 추종 실패 → 타이밍은 맞았으나 지속 실패 (쿨다운 20분, override 허용)

**Reclaim Bonus**: Reclaim 감지 시 EF threshold +1 (더 관대하게 보유)

### 2.4 TP1 / TP2 (부분 익절)

```python
# exit_logic_optimized.py
# TP1: 2R 달성 시 25% 청산 + trailing floor 활성화
# TP2: 4R 달성 시 추가 25% 청산
# 잔여 50%: trailing stop으로 관리
```

R = entry price 기준 1R (단위 손익).

### 2.5 ATR Trailing Stop

```yaml
risk_control:
  trailing_stop:
    activation_profit_pct: 1.5  # 수익 1.5% 이상 시 trailing 활성화
    distance_pct: 0.8            # ATR 기반 trailing 거리
    min_lock_profit_pct: 0.5     # 최소 이익 보호
```

> 스윙 전략은 일중 ATR trailing 제외 — 일봉 기준 청산 별도 적용.

### 2.6 Time Exit (시간 청산)

최대 보유 봉수 초과 시 강제 청산.

```yaml
risk_control:
  time_exit:
    bars: 10               # 기본 보유 봉수
    max_bars_mult: 2.0     # 최대 봉수 = bars × max_bars_mult
```

### 2.7 Overnight Exit

```yaml
risk_control:
  overnight_exit:
    enabled: true
    morning_protection_start: "09:00:00"
    morning_protection_end:   "09:30:00"
    use_open_range: true                    # 09:00~09:30 Open Range 기반 스탑
    open_range_multiplier: 1.5
```

B급 이하 포지션: 14:50 강제 청산 (overnight 리스크 제거).

---

## 3. DrawdownEngine

계좌 전체 일중 누적 손익 추적 → 레벨별 대응.

```
NORMAL  : 당일 PnL > -1.5%    → size × 1.0  (정상 진입)
CAUTION : 당일 PnL ≤ -1.5%    → size × 0.7  [DD_CAUTION]
DANGER  : 당일 PnL ≤ -3.0%    → size × 0.4  [DD_DANGER]
HALT    : 당일 PnL ≤ -5.0%    → 진입 차단    [DD_HALT]
```

추가 기능:
- **전략별 halt**: 특정 전략 drawdown -4% → 해당 전략만 당일 차단 (`[DD_STRATEGY_HALT]`)
- **Loss Streak Guard**: 연패 N회 → size × LSG_mult (조건부 활성)

---

## 4. Session Guard (세션 가드)

진입 과열/집중 방지를 위한 세션 레벨 가드.

| 가드 | 조건 | 효과 |
|------|------|------|
| HALT 복구 | HALT 직후 첫 진입 | 사이즈 축소 |
| 섹터 집중 | 동일 섹터 N종목 이상 | 추가 진입 차단 |
| 무거운 장 | 시장 하락 압력 | 사이즈 축소 |
| 승리 과열 | 연속 익절 후 과신 | 사이즈 축소 |

---

## 5. Conservative Mode

```yaml
risk_control:
  conservative_mode:
    enabled: false          # 기본 비활성
    size_mult: 0.5          # 활성화 시 모든 진입 size × 0.5
```

- Hard Stop 누적 또는 Loss Streak Guard 조건 충족 시 자동 활성화
- Soft halt 구현: 전면 차단 대신 size × 0.3으로 고신뢰 신호만 허용

---

## 6. 청산 사유 매트릭스 (exit_reason)

| exit_reason | 의미 | 이후 쿨다운 |
|-------------|------|------------|
| `take_profit` | TP1/TP2/trailing 익절 | 0분 |
| `trailing_stop` | ATR trailing 발동 | 30분 |
| `time_exit` | 시간 초과 강제 청산 | 20분 |
| `hard_stop` | Hard Stop 발동 | 60분 |
| `stop_loss` | 일반 손절 | 45분 |
| `lcl_early_cut` | LCL 조기 손절 | 45분 |
| `ef_no_demand` | EF: MFE 부족 (가짜 신호) | 45분, override 불가 |
| `ef_no_follow` | EF: 추종 실패 (구조 살아있음) | 20분, override 허용 |
| `early_failure` | EF: 미분류 | 60분, override 불가 |
| `overnight_exit` | Overnight 강제 청산 | 0분 (다음날 재진입 허용) |

---

## 7. 규칙 충돌 시 우선순위 로직

```python
# exit_logic_optimized.py — check_exit_signal() 내부 순서

# 1. Hard Stop 체크 (가장 먼저)
if profit_pct <= -hard_stop_pct:
    return (True, 'hard_stop', ...)

# 2. LCL v2.1 체크 (진입 초기)
if elapsed_min < window_minutes and profit_pct <= loss_cut_pct:
    return (True, 'lcl_early_cut', ...)

# 3. Early Failure Structure 체크
ef_result = check_early_failure_structure(...)
if ef_result['triggered']:
    return (True, ef_reason, ...)

# 4. TP1 / TP2 체크
# 5. Trailing Stop 체크
# 6. Time Exit 체크
# 7. Overnight Exit 체크
```

---

## 8. 스윙 전략 특수 청산 규칙

스윙은 일봉 기준으로 보유하므로 일중 ATR trailing 제외.

- **SWING_HOLD 플래그**: 포지션에 `strategy_horizon: 'swing'` 표시된 경우 일중 단기 청산 로직 일부 우회
- **Overnight Exit**: B급 이하 14:50 강제청산 (스윙 포지션 보호)
- **Morning Protection (09:00~09:30)**: 오버나이트 포지션은 Open Range 기반 스탑 적용
