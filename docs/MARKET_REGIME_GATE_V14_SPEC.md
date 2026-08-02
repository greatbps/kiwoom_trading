# Market Regime Gate — v1.4 설계 명세

> **문서 목적**: v1.4 Market Regime Gate의 도입 배경, 설계, 구현, 검증 방법 정의  
> **관련 파일**: `analyzers/market/regime_analyzer.py`, `config/strategy_hybrid.yaml`  
> **작성일**: 2026-07-05 | ruleset_version v1.4

---

## 1. 도입 배경

### 1.1 근본 문제

분석 결과(113건, `ENTRY_SIGNAL_QUALITY_REPORT.md`):

| 기간 | 건수 | WR | PF | 시장 환경 |
|------|------|----|----|---------|
| 2025-11 | 6건 | 66.7% | 3.674 | KOSPI 강세 |
| 2026-01 SMC_A | 4건 | **100%** | — | 강세 지속 |
| 2026-02 SMC_A | 11건 | **9%** | 0.051 | KOSPI 하락 |
| 2026-03~ | 11건 | 0% | 0 | 하락/횡보 |

**동일한 A급 CHoCH 신호가 시장 환경만 달라진 상황에서 WR 100% → 9%로 전락했다.**

### 1.2 이전 구조의 한계

```
[기존 흐름]
후보 종목
→ CHoCH 감지
→ A/B/C 등급 평가   ← 종목 레벨만 본다
→ 사이즈 결정
→ execute_buy()
```

**문제**: 시장 전체가 하락장/리스크오프일 때도 종목별 신호가 발생하면 그대로 진입한다.

### 1.3 목표 구조 (v1.4)

```
[v1.4 흐름]
1. 시장 레짐 판단 (TREND_UP / NEUTRAL / RISK_OFF)
   ↓
2. RISK_OFF → 신규 진입 전면 차단
   NEUTRAL  → A급만 허용, 사이즈 × 0.7
   TREND_UP → 정상 진입
   ↓
3. 종목별 CHoCH/SMC 평가 (기존 로직)
   ↓
4. 레짐 사이즈 배율 × 기존 사이징 결과
   ↓
5. execute_buy()
```

---

## 2. 레짐 상태 정의

### 2.1 TREND_UP (강세장)

**조건**: score >= 3 (기본값)

**의미**: KOSPI/KOSDAQ EMA 위 + 기울기 상승 + breadth 강세 + 급락 없음

**정책**:
- `allow_new_entries = True`
- `allowed_min_grade = "A"`
- `size_multiplier = 1.0`
- `allow_rae = True` (단, RAE 자체가 disabled이면 실거래 없음)

---

### 2.2 NEUTRAL (중립/횡보)

**조건**: -2 <= score <= +2 (기본값)

**의미**: 명확한 강세도 아니고 완전 리스크오프도 아닌 혼조장

**정책**:
- `allow_new_entries = True`
- `allowed_min_grade = "A"` (B급은 v1.3.2에서 이미 차단됨)
- `size_multiplier = 0.7`
- `allow_rae = False`

---

### 2.3 RISK_OFF (하락/리스크오프)

**조건**: score <= -3 (기본값)

**의미**: KOSPI/KOSDAQ EMA 아래 + 기울기 하락 + breadth 약세 + 급락 발생

**정책**:
- `allow_new_entries = False` (신규 진입 전면 차단)
- `size_multiplier = 0.0`
- `allow_rae = False`
- **기존 포지션 관리는 정상 수행** (청산 로직은 별도)

---

## 3. 입력 피처 정의

### 3.1 지수 추세 피처 (KOSPI + KOSDAQ 각각)

| 피처 | 계산 방법 | 점수 |
|------|-----------|------|
| 종가 > EMA20 | 당일 종가 vs EMA(20일봉) | +1 / -1 |
| EMA20 기울기 | 오늘 EMA vs 전일 EMA | +1 / -1 |
| 일간 수익률 급락 | KOSPI<-1.5% or KOSDAQ<-2.0% | -1 |

**지수 프록시**:
- KOSPI: KODEX 200 (`069500`)
- KOSDAQ: KODEX 코스닥150 (`229200`)

**데이터 소스**: `kiwoom_api.get_daily_chart()` (일봉)

### 3.2 Score 계산 예시

| 상황 | 항목 | 점수 |
|------|------|------|
| KOSPI > EMA20 | +1 | |
| KOSDAQ < EMA20 | -1 | |
| KOSPI EMA20 slope up | +1 | |
| KOSDAQ EMA20 slope down | -1 | |
| KOSPI 일간 -0.8% (>-1.5%) | 0 | |
| **합계** | | **0 → NEUTRAL** |

---

## 4. 판정 로직

### 4.1 코드 구조

```python
# analyzers/market/regime_analyzer.py

class RegimeAnalyzer:
    def evaluate(self) -> RegimeDecision:
        # 1. 캐시 확인 (60분 유효)
        # 2. KOSPI/KOSDAQ 일봉 → EMA 피처 계산
        # 3. score 합산
        # 4. threshold 비교 → TREND_UP / NEUTRAL / RISK_OFF
        # 5. 정책 적용 → RegimeDecision 반환
```

### 4.2 RegimeDecision 출력

```python
@dataclass
class RegimeDecision:
    regime: str                  # "TREND_UP" | "NEUTRAL" | "RISK_OFF"
    score: int                   # 총 점수
    allow_new_entries: bool      # 신규 진입 허용 여부
    allowed_min_grade: str       # "A" or None
    size_multiplier: float       # 0.0 ~ 1.0
    allow_rae: bool              # RAE 허용 여부
    reasons: list[str]           # 점수 기여 이유 목록
```

### 4.3 API 오류 처리

```
API 오류 시 → NEUTRAL fallback
(진입 허용, 사이즈 0.7 적용, RAE 차단)
```

NEUTRAL fallback을 선택한 이유: API 오류로 인한 완전 진입 차단(RISK_OFF fallback)은 정상 장에서의 기회 손실이 더 크다. NEUTRAL은 사이즈를 줄이면서 진입은 허용해 중간 경로를 취한다.

---

## 5. 진입 파이프라인 적용

### 5.1 삽입 위치

`main_auto_trading.py` — `check_entry_signal()` 함수 내부:

```
STOCK_GATE         ← 종목 상태 확인
   ↓
EARLY_WINDOW_BLOCK ← 09:00~09:30 차단 (v1.3.2)
   ↓
REGIME_GATE        ← 레짐 차단 (v1.4) ← 여기 삽입
   ↓
RAE check          ← RAE 진입 평가
   ↓
PRIMARY SMC check  ← 종목별 CHoCH 평가
   ↓
B급 downgrade check← A→B 강등 시 차단
   ↓
REGIME_SIZE_ADJUST ← 레짐 size_mult 적용 ← 여기도 적용
   ↓
execute_buy()
```

### 5.2 RAE 특별 처리

RAE 진입 규칙에 `RULE_D_REGIME_*` 추가:

```python
# v1.4 규칙 D: 레짐 게이트 — NEUTRAL/RISK_OFF에서 RAE 차단
if _mrg_decision is not None and not _mrg_decision.allow_rae:
    _rae_blocked_reason = f'RULE_D_REGIME_{_mrg_decision.regime}'
```

---

## 6. 로그 규격

### 레짐 판정 로그

```
[REGIME] {stock_code} regime=TREND_UP score=+4 allow=True size_mult=1.0 allow_rae=True reasons=[KOSPI_above_EMA20, ...]
```

### 진입 차단 로그

```
[REGIME_BLOCK] {stock_code} route=ALL regime=RISK_OFF score=-4
[REGIME_BLOCK] {stock_code} route=RAE reason=RULE_D_REGIME_NEUTRAL
```

### 사이즈 조정 로그

```
[REGIME_SIZE_ADJUST] {stock_code} regime=NEUTRAL mult=0.7 base=0.500 final=0.350
```

---

## 7. YAML 설정

```yaml
market_regime:
  enabled: true
  block_new_entries_on_risk_off: true
  trend_up_size_mult: 1.0
  neutral_size_mult: 0.7
  risk_off_size_mult: 0.0
  allow_rae_in_trend_up: true
  allow_rae_in_neutral: false
  allow_rae_in_risk_off: false

  score_thresholds:
    trend_up_min: 3
    risk_off_max: -3

  index_tickers:
    kospi: "069500"
    kosdaq: "229200"

  ema_period: 20
  cache_minutes: 60
  fallback_on_error: "NEUTRAL"
```

---

## 8. 향후 개선 포인트

### 단기 (데이터 30건+ 이후)

1. **Breadth 피처 추가**: 상승 종목 비율, MA20 위 종목 비율
   - 현재: YAML에 설정만 있고 데이터 소스 미연결
   - 추가 조건: Kiwoom breadth API 또는 별도 데이터 소스 확보 시

2. **Score 임계값 캘리브레이션**: trend_up_min / risk_off_max 조정
   - 기준: 레짐별 성과 분석 30건+ 후

3. **레짐별 성과 기록**: `market_regime` 컬럼을 거래 DB에 추가
   - 목적: 레짐별 WR/PF 분석

### 중기 (E2 달성 이후)

4. **레짐별 A급 커트오프 조정**: TREND_UP에서는 A-도 허용하는 등 차등 적용
5. **레짐 전환 감지**: TREND_UP→NEUTRAL 전환 시 포지션 조기 축소 정책

### 장기 (Phase B 이후)

6. **ML 기반 레짐 확률**: 규칙 기반 대신 확률 기반 (calibration 우선)

---

## 9. 판단 기준 요약표

| 레짐 | score | 신규 진입 | 사이즈 | RAE | 예상 상황 |
|------|-------|-----------|--------|-----|----------|
| TREND_UP | ≥+3 | ✅ | ×1.0 | ✅ | KOSPI 상승, 강세장 |
| NEUTRAL | -2~+2 | ✅ | ×0.7 | ❌ | 횡보, 혼조 |
| RISK_OFF | ≤-3 | ❌ | ×0.0 | ❌ | KOSPI 하락, 급락 |
