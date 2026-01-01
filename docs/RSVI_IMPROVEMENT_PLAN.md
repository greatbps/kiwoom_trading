# RSVI (Relative Volume Strength Index) 개선 계획

**작성일**: 2025-11-28
**목적**: 거래량 필터 고도화 → 승률 향상
**기반**: ChatGPT 분석 결과

---

## 📊 현재 문제점 (ChatGPT 진단 결과)

### 1. 거래량 필터의 한계

**현재 구현**:
```
L6 Pre-Trade Validator:
- 명시적인 거래량 하드컷 없음
- VWAP 위/아래만 체크
- "거래량이 얼마나 강한가"를 판단 안 함
```

**문제**:
1. **1비트 필터**: "있다/없다" 수준만 체크
2. **상대적 강도 무시**: 평소 대비 얼마나 이례적인지 판단 불가
3. **숨은 급등 신호 놓침**: 거래량 폭발 초기 구간 미포착
4. **잘못된 진입 허용**: 약한 거래량에도 진입 가능

### 2. 현재 성과와의 연관성

```
최근 2주 성과 (11-14 ~ 11-28):
- 승률: 25.0% (매우 낮음)
- 주요 손실: 대손실 5개 종목 (-24,110원)

가설:
→ 거래량 뒷받침 없는 신호에 진입
→ 급격한 추세 약화 → 손실
```

---

## 🎯 개선 방향

### 핵심 아이디어

```
"거래량이 있냐 없냐"
    ↓
"평소 대비 얼마나 이례적으로 강한가"
```

**RSVI (Relative Volume Strength Index)** 도입:
- `vol_z20`: 거래량 Z-score (표준편차 기반)
- `vroc10`: 10캔들 대비 거래량 변화율

---

## 📈 RSVI 지표 정의

### 1. Volume Z-Score (vol_z20)

```python
vol_ma20 = df['volume'].rolling(20).mean()
vol_std20 = df['volume'].rolling(20).std()

vol_z20 = (current_volume - vol_ma20) / (vol_std20 + 1e-9)
```

**의미**:
```
vol_z20 >= 2.0  → 평균 + 2σ (매우 강함, 상위 2.5%)
vol_z20 >= 1.5  → 평균 + 1.5σ (강함, 상위 7%)
vol_z20 >= 1.0  → 평균 + 1σ (양호, 상위 16%)
vol_z20 >= 0.0  → 평균 이상
vol_z20 < 0.0   → 평균 이하 (약함)
```

### 2. Volume Rate of Change (vroc10)

```python
vroc10 = (current_volume / volume_10_candles_ago) - 1.0
```

**의미**:
```
vroc10 >= 3.0  → 4배 증가 (급등 초기)
vroc10 >= 2.0  → 3배 증가 (강한 가속)
vroc10 >= 1.0  → 2배 증가 (가속)
vroc10 >= 0.0  → 증가 중
vroc10 < 0.0   → 감소 중
```

### 3. RSVI Score 계산

```python
rsvi_score = 0.0

# Z-score 기반 (60%)
if vol_z20 >= 2.0:
    rsvi_score += 0.6
elif vol_z20 >= 1.0:
    rsvi_score += 0.4
elif vol_z20 >= 0.0:
    rsvi_score += 0.2

# VROC 기반 (40%)
if vroc10 >= 2.0:
    rsvi_score += 0.4
elif vroc10 >= 1.0:
    rsvi_score += 0.3
elif vroc10 >= 0.0:
    rsvi_score += 0.1

# 0.0 ~ 1.0 범위
rsvi_score = min(rsvi_score, 1.0)
```

**범위**: 0.0 ~ 1.0
- **0.8~1.0**: 매우 강함 (ideal entry)
- **0.6~0.8**: 강함 (good entry)
- **0.4~0.6**: 보통 (acceptable)
- **0.2~0.4**: 약함 (risky)
- **0.0~0.2**: 매우 약함 (avoid)

---

## 🚀 3단계 실행 계획

### Phase 1: L6 거래량 필터 개편 (우선순위 1)

**목표**: 하드컷 → RSVI 기반 Confidence

#### 1.1 RSVI 지표 추가

**파일**: `analyzers/volume_indicators.py` (신규)

```python
def attach_rsvi_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    RSVI 지표 추가

    Args:
        df: OHLCV 데이터프레임

    Returns:
        vol_z20, vroc10이 추가된 데이터프레임
    """
    # Volume 이동평균/표준편차
    df['vol_ma20'] = df['volume'].rolling(20, min_periods=1).mean()
    df['vol_std20'] = df['volume'].rolling(20, min_periods=1).std()

    # Z-score
    df['vol_z20'] = (df['volume'] - df['vol_ma20']) / (df['vol_std20'] + 1e-9)

    # Volume ROC
    df['vroc10'] = df['volume'] / (df['volume'].shift(10) + 1e-9) - 1.0

    return df

def calculate_rsvi_score(vol_z20: float, vroc10: float) -> float:
    """RSVI 점수 계산 (0.0 ~ 1.0)"""
    score = 0.0

    # Z-score (60%)
    if vol_z20 >= 2.0:
        score += 0.6
    elif vol_z20 >= 1.0:
        score += 0.4
    elif vol_z20 >= 0.0:
        score += 0.2
    else:
        score -= 0.1  # 페널티

    # VROC (40%)
    if vroc10 >= 2.0:
        score += 0.4
    elif vroc10 >= 1.0:
        score += 0.3
    elif vroc10 >= 0.0:
        score += 0.1
    else:
        score -= 0.05  # 페널티

    return max(0.0, min(1.0, score))
```

#### 1.2 L6 Validator 수정

**파일**: `analyzers/pre_trade_validator_v2.py`

**수정 위치**: `check_with_confidence()` 메서드

```python
def check_with_confidence(self, stock_code, stock_name, historical_data,
                         current_price, current_time, historical_data_30m=None):
    """L6 검증 + RSVI 반영"""

    # 1. RSVI 지표 추가 (없으면)
    if 'vol_z20' not in historical_data.columns:
        from analyzers.volume_indicators import attach_rsvi_indicators
        historical_data = attach_rsvi_indicators(historical_data)

    latest = historical_data.iloc[-1]
    vol_z20 = latest['vol_z20']
    vroc10 = latest['vroc10']

    # 2. 최소 하드컷 (완전 거래량 부실 방지)
    if vol_z20 < -1.0 and vroc10 < -0.5:
        return FilterResult(False, 0.0, "RSVI: 거래량 매우 약함 (vol_z={:.2f}, vroc={:.2f})".format(vol_z20, vroc10))

    # 3. RSVI 점수 계산
    from analyzers.volume_indicators import calculate_rsvi_score
    rsvi_score = calculate_rsvi_score(vol_z20, vroc10)

    # 4. 기존 백테스트 검증
    allowed, reason, stats = self.validate_trade(...)

    if not allowed:
        return FilterResult(False, 0.0, f"L6 검증 실패: {reason}")

    # 5. 기존 백테스트 Confidence
    backtest_conf = self._calculate_backtest_confidence(stats)

    # 6. RSVI와 결합 (RSVI 비중 70%)
    final_confidence = (0.3 * backtest_conf) + (0.7 * rsvi_score)

    # 7. 최종 임계값 체크
    if final_confidence < 0.4:
        return FilterResult(
            False,
            final_confidence,
            f"L6: Confidence 부족 ({final_confidence:.2f} < 0.4) | RSVI={rsvi_score:.2f}, BT={backtest_conf:.2f}"
        )

    reason = f"L6 통과 | Conf={final_confidence:.2f} (RSVI:{rsvi_score:.2f} BT:{backtest_conf:.2f})"
    return FilterResult(True, final_confidence, reason)
```

**예상 효과**:
- 약한 거래량 신호 차단: 승률 향상
- 강한 거래량 신호 우대: 포지션 크기 증가

---

### Phase 2: Multi-Alpha에 Volume Strength 추가 (우선순위 2)

**목표**: RSVI를 독립 Alpha로 승격

#### 2.1 Alpha Volume Strength 구현

**파일**: `trading/alphas/alpha_volume_strength.py` (신규)

```python
"""Alpha: Volume Strength (RSVI 기반)"""

def alpha_volume_strength(df: pd.DataFrame) -> float:
    """
    거래량 상대 강도 Alpha

    Args:
        df: RSVI 지표가 추가된 데이터프레임

    Returns:
        -1.0 ~ +1.0 점수
    """
    if 'vol_z20' not in df.columns:
        from analyzers.volume_indicators import attach_rsvi_indicators
        df = attach_rsvi_indicators(df)

    latest = df.iloc[-1]
    vol_z20 = latest['vol_z20']
    vroc10 = latest['vroc10']

    score = 0.0

    # Z-score 기반
    if vol_z20 >= 2.5:
        score += 0.6
    elif vol_z20 >= 1.5:
        score += 0.4
    elif vol_z20 >= 0.5:
        score += 0.2
    elif vol_z20 >= 0.0:
        score += 0.1
    else:
        score -= 0.2  # 페널티

    # VROC 기반
    if vroc10 >= 3.0:
        score += 0.4
    elif vroc10 >= 1.5:
        score += 0.3
    elif vroc10 >= 0.5:
        score += 0.1
    else:
        score -= 0.1  # 페널티

    return max(-1.0, min(1.0, score))
```

#### 2.2 Multi-Alpha Engine 통합

**파일**: `trading/multi_alpha_engine.py`

**수정**: 가중치 재조정

```python
# 기존 8개 Alpha 가중치 조정
ALPHA_WEIGHTS = {
    'momentum': 0.22,        # 25% → 22%
    'vwap': 0.18,           # 20% → 18%
    'news': 0.13,           # 15% → 13%
    'supply_demand': 0.13,  # 15% → 13%
    'reversal': 0.08,       # 10% → 8%
    'liquidity': 0.06,      # 8% → 6%
    'squeeze': 0.05,        # 5% → 5%
    'ml': 0.02,             # 2% → 2%
    'volume_strength': 0.13 # 0% → 13% (NEW)
}

def compute(self, stock_code, state):
    """Multi-Alpha 계산"""

    # ... 기존 알파 계산 ...

    # Volume Strength Alpha 추가
    from trading.alphas.alpha_volume_strength import alpha_volume_strength
    alpha_vol = alpha_volume_strength(state['df'])

    aggregate_score = (
        alpha_momentum * ALPHA_WEIGHTS['momentum'] +
        alpha_vwap * ALPHA_WEIGHTS['vwap'] +
        alpha_news * ALPHA_WEIGHTS['news'] +
        alpha_supply_demand * ALPHA_WEIGHTS['supply_demand'] +
        alpha_reversal * ALPHA_WEIGHTS['reversal'] +
        alpha_liquidity * ALPHA_WEIGHTS['liquidity'] +
        alpha_squeeze * ALPHA_WEIGHTS['squeeze'] +
        alpha_ml * ALPHA_WEIGHTS['ml'] +
        alpha_vol * ALPHA_WEIGHTS['volume_strength']  # NEW
    )

    return {
        'aggregate_score': aggregate_score,
        'alphas': {
            ...
            'volume_strength': alpha_vol  # NEW
        }
    }
```

**예상 효과**:
- 거래량 약한 신호: aggregate_score 하락 → 진입 차단
- 거래량 강한 신호: aggregate_score 상승 → 진입 허용

---

### Phase 3: 청산/포지션 사이징 반영 (우선순위 3)

**목표**: RSVI를 Exit & Sizing에 활용

#### 3.1 Early Failure Cut 개선

**파일**: `main_auto_trading.py`

**수정**: Early Failure Cut 조건

```python
# 현재
if holding_minutes >= 4 and profit_pct <= -0.66:
    execute_sell(stock_code, current_price, profit_pct, "Early Failure Cut")

# 수정
if holding_minutes >= 4 and profit_pct <= -0.66:
    # RSVI 체크 (거래량이 다시 살아나면 유예)
    df = get_current_minute_data(stock_code)
    if 'vol_z20' not in df.columns:
        from analyzers.volume_indicators import attach_rsvi_indicators
        df = attach_rsvi_indicators(df)

    latest = df.iloc[-1]
    vol_z20 = latest['vol_z20']

    if vol_z20 >= 1.5:
        # 거래량 강하면 한 번 더 기회 (최대 1분 유예)
        console.print(f"[yellow]⚠️  Early Failure 유예 (RSVI 강함: vol_z={vol_z20:.2f})[/yellow]")
    else:
        # 거래량 약하면 즉시 청산
        execute_sell(stock_code, current_price, profit_pct, f"Early Failure Cut (RSVI 약화: {vol_z20:.2f})")
```

**예상 효과**:
- 불필요한 조기 청산 감소
- 거래량 강한 반등 구간 포착

#### 3.2 포지션 사이징 반영

**파일**: `main_auto_trading.py` → `execute_buy()`

**수정**: entry_confidence에 RSVI 반영

```python
# 현재 (고정)
entry_confidence = 1.0

# 수정 (동적)
# SignalOrchestrator 결과에서 RSVI 기반 조정
final_confidence = result['confidence']  # L3-L6 종합
rsvi_score = result.get('rsvi_score', 0.5)  # L6에서 계산된 RSVI

entry_confidence = (0.5 * final_confidence) + (0.5 * rsvi_score)
entry_confidence = max(0.4, min(1.0, entry_confidence))

# 포지션 계산
position_calc = risk_manager.calculate_position_size(
    current_balance=self.current_cash,
    current_price=price,
    stop_loss_price=price * 0.97,
    entry_confidence=entry_confidence  # 동적 조정
)
```

**예상 효과**:
```
RSVI 강함 (0.9):
- entry_confidence = 0.9
- 리스크 1% 풀 적용
- 포지션 크기 100%

RSVI 약함 (0.4):
- entry_confidence = 0.5
- 리스크 0.5% 적용
- 포지션 크기 50%

→ 강한 신호에 집중, 약한 신호는 소극적
```

---

## 📊 예상 효과

### 1. 진입 품질 개선

**Before**:
```
거래량 필터 없음 → 약한 신호도 진입
승률: 25.0%
```

**After**:
```
RSVI < 0.4 차단 → 강한 신호만 진입
예상 승률: 35-45%
```

### 2. 손실 감소

**대손실 방지**:
```
Before: 거래량 약한 종목 진입 → -27% 손실
After: RSVI 체크로 사전 차단 → 손실 회피
```

**Early Failure 정확도**:
```
Before: 무조건 4분 -0.66% 컷
After: RSVI 강하면 유예 → 불필요한 컷 감소
```

### 3. 포지션 최적화

```
강한 신호: 포지션 100% (승률 높음)
약한 신호: 포지션 50% (리스크 감소)

→ MDD 감소, 샤프비율 개선
```

---

## 🧪 검증 계획

### 1. 백테스트 (Phase 1 적용 후)

```python
# scripts/backtest_rsvi.py

# 기존 전략 vs RSVI 전략 비교
- 승률
- 평균 수익
- Profit Factor
- MDD
- 샤프비율
```

### 2. 실거래 모니터링 (1주일)

```
지표:
- 일일 승률
- RSVI 분포 (진입 종목)
- Early Failure 유예 효과
```

### 3. 성공 기준

```
승률: 25% → 35% 이상
일일 수익: -36원 → 0원 이상
대손실 발생: 감소
```

---

## 📅 실행 일정

### Week 1 (즉시~12/4)

- [ ] **Phase 1 구현** (우선순위 1)
  - [ ] `analyzers/volume_indicators.py` 생성
  - [ ] `pre_trade_validator_v2.py` 수정
  - [ ] 테스트 및 검증

### Week 2 (12/5~12/11)

- [ ] **Phase 1 실거래 모니터링**
  - [ ] 일일 성과 추적
  - [ ] RSVI 분포 분석
  - [ ] 개선 효과 측정

### Week 3 (12/12~12/18)

- [ ] **Phase 2 구현** (Phase 1 성공 시)
  - [ ] `alpha_volume_strength.py` 생성
  - [ ] Multi-Alpha Engine 통합
  - [ ] 백테스트

### Week 4 (12/19~12/25)

- [ ] **Phase 3 구현** (선택)
  - [ ] Early Failure Cut 개선
  - [ ] 포지션 사이징 동적 조정

---

## 🤖 ChatGPT 요구사항

### 1. 추가 분석 요청

**승리 vs 패배 트레이드 RSVI 분포 비교**:

```
질문:
최근 거래 로그(PostgreSQL trades 테이블)와 분봉 데이터를 기반으로:

1. 승리한 거래의 평균 vol_z20, vroc10
2. 패배한 거래의 평균 vol_z20, vroc10
3. 두 그룹 간 통계적 유의미한 차이가 있는가?

목적:
- RSVI 임계값 최적화 (0.4가 적절한가?)
- RSVI와 승률 간 상관관계 확인
```

### 2. 코드 스니펫 요청

**정확한 함수 시그니처**:

```
요청:
다음 함수들의 정확한 구현 코드:

1. attach_rsvi_indicators(df) → DataFrame
2. calculate_rsvi_score(vol_z20, vroc10) → float
3. alpha_volume_strength(df) → float
4. check_with_confidence() 전체 (RSVI 통합 버전)

조건:
- 현재 프로젝트 구조 (analyzers/, trading/) 준수
- 기존 FilterResult 타입 사용
- 에러 처리 포함
- 로깅 포함
```

### 3. 백테스트 요청

**백테스트 프레임워크**:

```
요청:
scripts/backtest_rsvi.py 전체 코드

기능:
1. PostgreSQL에서 과거 거래 데이터 로드
2. 각 거래 시점의 RSVI 재계산
3. RSVI 임계값별 성능 비교:
   - 0.3, 0.4, 0.5, 0.6
4. 결과 출력:
   - 승률, 평균 수익, PF, MDD
   - RSVI 분포 히스토그램
```

---

## ✅ 다음 액션

### 즉시 실행

1. **ChatGPT에 추가 요청** (위 3가지)
2. **Phase 1 코드 구현** (volume_indicators.py)
3. **테스트 및 검증**

### 사용자 확인 필요

- [ ] Phase 1-3 우선순위 확인
- [ ] 백테스트 여부 결정
- [ ] 실거래 적용 시점 결정

---

**작성자**: Claude Code
**기반**: ChatGPT 분석 결과
**예상 효과**: 승률 25% → 35-45%
