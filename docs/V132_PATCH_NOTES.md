# v1.3.2 패치 노트

> **배포일**: 2026-07-05  
> **기반 버전**: v1.3.1-final  
> **패치 목적**: 기대값이 낮은 진입 경로 즉시 제거  
> **변경 범위**: config/strategy_hybrid.yaml + main_auto_trading.py  
> **증거 수준**: E1 (분석 113건 기반)

---

## ⚠️ 핵심 요약

> **v1.3.2에서 기대값 낮은 진입(B급, 장초반 진입)을 즉시 제거하고,  
> v1.4에서 Market Regime Gate를 도입해 "거래하면 안 되는 장에서는 아예 신규 스윙 진입을 막는 구조"로 전환한다.**

---

## 1. 패치 1 — B급 CHoCH 진입 전면 차단

### 변경 근거

| 지표 | 값 |
|------|----|
| SMC_B 건수 | 4건 |
| SMC_B WR | **0%** |
| SMC_B PF | **0** |
| SMC_B ALPHA율 | **0%** |
| Hard Stop 비율 | 75% (4건 중 3건) |

B급 4건 전량 손실. 사이즈 0.2로 축소해도 기대값이 음수.

### YAML 변경

```yaml
# config/strategy_hybrid.yaml — smc.choch_grade
min_grade: "A"   # 이전: "B"
```

### 코드 변경

`main_auto_trading.py` — B급 downgrade 경로(RVOL 부족, 수급 부족) 이후 처리:

```python
# 이전: size × 0.2 + 시간 제한
# 변경 후:
if choch_grade == 'B':
    logger.info(f"[CHOCH_GRADE_BLOCK] grade=B {stock_code} reason=min_grade=A")
    return
```

### 영향 범위

- **PRIMARY**: SMC 신호 레벨에서 B급 차단 (min_grade='A')
- **A→B 강등 경로**: downgrade 이후에도 추가 차단
- **RAE**: B급 CHoCH로 등록된 RAE 후보는 기존 `rae_enabled=false`로 이미 실거래 없음
- **로그**: `[CHOCH_GRADE_BLOCK] grade=B {symbol} reason=min_grade=A`

---

## 2. 패치 2 — 09:00~09:30 신규 진입 하드 차단

### 변경 근거

| 시간대 | 건수 | ALPHA율 | EF율 |
|--------|------|---------|------|
| 09:00~10:00 | 8건 | **0%** | **75%** |
| 10:30~13:00 | 65건 | 21.5% | 52.3% |

장초반 30분은 구조적으로 ALPHA=0%. 신호 노이즈, 고점 추격, 미완성 구조 겹침.

### YAML 변경

```yaml
# config/strategy_hybrid.yaml — time_filter
early_window_block:
  enabled: true
  block_start: "09:00"
  block_end: "09:30"
```

### 코드 변경

`main_auto_trading.py` — STOCK_GATE 직후, RAE/PRIMARY 이전 공통 게이트:

```python
# PRIMARY, RAE, TED 모두 이 게이트를 통과해야 함
if _ew_start <= current_time < _ew_end:
    logger.info(f"[EARLY_WINDOW_BLOCK] {stock_code} route=ALL now=...")
    return
```

### 차단 예외

없음. 09:00~09:29:59 신규 진입 전면 금지.

### 영향 범위

- **PRIMARY SMC**: 차단 ✅
- **RAE**: 차단 ✅ (게이트가 RAE 체크 전에 위치)
- **TED 경유 RAE**: 차단 ✅
- **Trend Breakout**: 차단 ✅ (동일 함수 내)
- **기존 포지션 청산**: 영향 없음 (exit 로직은 별도 함수)

---

## 3. 주의사항

### 주의 1 — B급이 완전히 사라지지는 않음

`min_grade='A'`는 smc_signals.py에서 B급 신호를 차단하지만, A→B 강등 경로는 main_auto_trading.py에서 발생한다. 이 경우도 코드 레벨에서 `[CHOCH_GRADE_BLOCK]`으로 차단된다.

### 주의 2 — 진입 건수 감소 예상

B급 4건(3.5%) 제거 + 09:00~09:30 8건(7.1%) 제거 = 약 10~11% 진입 감소 예상. 단, 모두 기대값 음수 구간이므로 WR과 avg_pnl은 개선될 것.

### 주의 3 — RAE 실거래 없음 (기존 상태 유지)

RAE는 `rae.enabled=false`로 인해 실거래 없음. v1.3.2 패치와 무관.

---

## 4. 롤백 방법

```bash
# YAML 롤백 (B급 복원)
# config/strategy_hybrid.yaml — smc.choch_grade
# min_grade: "B"  ← 되돌리기

# YAML 롤백 (early_window_block 비활성화)
# time_filter.early_window_block.enabled: false
```

코드 변경은 YAML `enabled: false` 로 무력화 가능.

---

## 5. 검증 항목

→ `docs/V132_VALIDATION_CHECKLIST.md` 참조
