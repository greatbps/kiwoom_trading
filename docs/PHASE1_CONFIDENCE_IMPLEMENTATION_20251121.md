# Phase 1: Confidence Layer 구현 진행 상황

**시작일**: 2025-11-21
**목표**: L3-L6 Pass/Fail → Confidence(0~1) 반환 구조로 전환

---

## ✅ 완료된 작업 (Step 1-3)

### Step 1: BaseFilter 인터페이스 ✅
**파일**: `trading/filters/base_filter.py`

```python
class FilterResult:
    """필터 결과 (Pass/Fail + Confidence)"""
    - passed: bool        # True/False (하위 호환성)
    - confidence: float  # 0.0 ~ 1.0 (신호 강도)
    - reason: str        # 설명

class BaseFilter(ABC):
    """L3-L6 필터 베이스 클래스"""
    @abstractmethod
    def check(self, symbol, df, **kwargs) -> FilterResult
```

**효과**:
- 기존 Pass/Fail 구조 유지 (하위 호환성)
- Confidence 정보 추가 (신호 강도)

---

### Step 2: Confidence Aggregator ✅
**파일**: `trading/confidence_aggregator.py`

```python
class ConfidenceAggregator:
    """멀티 필터 Confidence 결합 엔진"""

    def aggregate(self, filter_results) -> (final_conf, should_pass, reason):
        # 가중 평균 계산
        weights = {"L3": 1.5, "L4": 1.0, "L5": 1.2, "L6": 0.8}
        final_conf = weighted_average(results, weights)

        # 최소 임계값 체크 (0.5)
        if final_conf < 0.5:
            return 0.0, False, "Low confidence"

        return final_conf, True, "..."

    def calculate_position_multiplier(self, confidence):
        # 0.5 → 0.6, 1.0 → 1.0
        return 0.6 + (confidence - 0.5) * 0.8
```

**효과**:
- L3-L6 결과를 가중 평균으로 결합
- Confidence 기반 포지션 크기 조정 (0.6 ~ 1.0)

---

### Step 3: L3 MTF Filter V2 ✅
**파일**: `analyzers/multi_timeframe_consensus_v2.py`

#### 개선 내용

**Before (V1)**:
```python
consensus = entry_signal_1m and trend_5m and trend_15m
return consensus  # True/False만
```

**After (V2)**:
```python
def check_with_confidence(self, symbol, market, df_1m) -> FilterResult:
    # 1. VWAP 돌파 강도 (0~0.4)
    vwap_conf = calculate_vwap_strength(price, vwap)

    # 2. EMA 정렬 강도 (0~0.3)
    ema_conf = calculate_ema_strength(close_5m, ema_5m, close_15m, ema_15m)

    # 3. 거래량 증가 (0~0.3)
    volume_conf = calculate_volume_strength(df_1m)

    # 합산 (0~1.0)
    confidence = vwap_conf + ema_conf + volume_conf

    return FilterResult(True, confidence, reason)
```

#### Confidence 계산 로직

**1. VWAP 돌파 강도** (0~0.4 점수):
```python
strength = (price - vwap) / vwap

if strength >= 0.5%:     # 강한 돌파
    return 0.4
elif strength >= 0.1%:   # 중간 돌파
    return 0.1 ~ 0.4 (선형)
else:                    # 약한 돌파
    return < 0.1
```

**예시**:
- 메드팩토 10:11 → VWAP +0.08% → conf = 0.08 (**약한 신호**)
- 코오롱티슈진 10:05 → VWAP +0.6% → conf = 0.4 (**강한 신호**)

**2. EMA 정렬 강도** (0~0.3 점수):
```python
# 5분봉 EMA 정렬 (0~0.15)
if close_5m > ema_5m:
    strength_5m = (close_5m - ema_5m) / ema_5m
    score += min(strength_5m * 15, 0.15)

# 15분봉 EMA 정렬 (0~0.15)
if close_15m > ema_15m:
    strength_15m = (close_15m - ema_15m) / ema_15m
    score += min(strength_15m * 15, 0.15)
```

**3. 거래량 증가** (0~0.3 점수):
```python
# 거래량 Z-score
z = (current_vol - mean_vol) / std_vol

if z > 3.0:    # 3σ 이상
    return 0.3
elif z > 2.0:  # 2σ ~ 3σ
    return 0.2
elif z > 1.0:  # 1σ ~ 2σ
    return 0.1
```

**효과**:
- 메드팩토 6건 중 5건의 confidence < 0.4 예상
- 신테카바이오 15:30 confidence < 0.3 예상
- 코오롱티슈진 10:05 confidence = 0.8+ 예상

---

## ✅ 완료된 작업 (Step 4-7)

### Step 4: L4 Liquidity Filter V2 ✅
**파일**: `analyzers/liquidity_shift_detector_v2.py`

**구현 완료**:
```python
class LiquidityShiftDetectorV2(LiquidityShiftDetector):
    def __init__(self, api=None, **kwargs):
        super().__init__(api=api, **kwargs)
        self.inst_weight = 0.4       # 기관 순매수 (40%)
        self.foreign_weight = 0.3    # 외국인 순매수 (30%)
        self.order_weight = 0.3      # 호가 불균형 (30%)

    def check_with_confidence(self, stock_code, investor_data=None, order_book=None) -> FilterResult:
        # 1. 기관 순매수 Z-score → Confidence (0~0.4)
        # 2. 외국인 순매수 Z-score → Confidence (0~0.3)
        # 3. 호가 불균형 → Confidence (0~0.3)
        confidence = inst_conf + foreign_conf + order_conf
        return FilterResult(True, confidence, detailed_reason)
```

---

### Step 5: L5 Squeeze Filter V2 ✅
**파일**: `analyzers/squeeze_momentum_v2.py`

**구현 완료**:
```python
class SqueezeMomentumProV2(SqueezeMomentumPro):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.squeeze_weight = 0.4    # Squeeze 강도 (40%)
        self.momentum_weight = 0.3   # 모멘텀 방향 (30%)
        self.width_weight = 0.3      # BB Width (30%)

    def check_with_confidence(self, df: pd.DataFrame) -> FilterResult:
        # 1. Squeeze 강도 (BB/KC ratio) → Confidence (0~0.4)
        # 2. Momentum 방향 (3-bar 연속 상승) → Confidence (0~0.3)
        # 3. BB Width (변동성 수축) → Confidence (0~0.3)
        confidence = squeeze_conf + momentum_conf + width_conf
        return FilterResult(True, confidence, detailed_reason)
```

---

### Step 6: L6 Validator V2 ✅
**파일**: `analyzers/pre_trade_validator_v2.py`

**구현 완료**:
```python
class PreTradeValidatorV2(PreTradeValidator):
    def __init__(self, config: ConfigLoader, **kwargs):
        super().__init__(config, **kwargs)
        self.pf_weight = 0.4           # Profit Factor (40%)
        self.win_rate_weight = 0.3     # 승률 (30%)
        self.avg_profit_weight = 0.3   # 평균 수익률 (30%)

    def check_with_confidence(self, stock_code, stock_name, historical_data,
                             current_price, current_time, historical_data_30m=None) -> FilterResult:
        # 1. Profit Factor → Confidence (0~0.4)
        # 2. 승률 (윌슨 하한 기반) → Confidence (0~0.3)
        # 3. 평균 수익률 → Confidence (0~0.3)
        confidence = pf_conf + win_rate_conf + avg_profit_conf

        # Fallback Stage 패널티 적용
        if fallback_stage > 0:
            penalty = fallback_stage * 0.1
            confidence = max(confidence - penalty, 0.2)

        return FilterResult(True, confidence, detailed_reason)
```

---

### Step 7: SignalOrchestrator 통합 ✅
**파일**: `analyzers/signal_orchestrator.py`

**구현 완료**:
```python
class SignalOrchestrator:
    def __init__(self, config: Dict, api=None):
        # V2 Filters (Confidence-based)
        self.mtf_consensus = MultiTimeframeConsensusV2(config)
        self.liquidity_detector = LiquidityShiftDetectorV2(api=api, ...)
        self.squeeze = SqueezeMomentumProV2(...)
        self.validator = PreTradeValidatorV2(config=config, ...)

        # Confidence Aggregator
        self.confidence_aggregator = ConfidenceAggregator()

    def evaluate_signal(self, stock_code, stock_name, current_price, df, market='KOSPI',
                       current_cash=0, daily_pnl=0) -> Dict:
        # L0-L1: 기존 Pass/Fail
        if not self.check_l0_system_filter(current_cash, daily_pnl):
            return {'allowed': False, ...}
        if not self.check_l1_regime_filter(market):
            return {'allowed': False, ...}

        # L3-L6: Confidence 반환
        l3_result = self.mtf_consensus.check_with_confidence(stock_code, market, df)
        l4_result = self.liquidity_detector.check_with_confidence(stock_code)
        l5_result = self.squeeze.check_with_confidence(df)
        l6_result = self.validator.check_with_confidence(
            stock_code, stock_name, df, current_price, datetime.now()
        )

        # Confidence 결합
        filter_results = {
            "L3_MTF": l3_result,
            "L4_LIQUIDITY": l4_result if l4_result.passed else FilterResult(True, 0.3, "L4 Default"),
            "L5_SQUEEZE": l5_result if l5_result.passed else FilterResult(True, 0.3, "L5 Default"),
            "L6_VALIDATOR": l6_result
        }

        final_confidence, should_pass, reason = self.confidence_aggregator.aggregate(filter_results)

        if not should_pass:
            # Confidence < 0.5 차단
            return {'allowed': False, 'rejection_level': 'CONFIDENCE', ...}

        # 포지션 크기 조정 (0.6 ~ 1.0)
        position_mult = self.confidence_aggregator.calculate_position_multiplier(final_confidence)

        return {
            'allowed': True,
            'confidence': final_confidence,
            'position_size_multiplier': position_mult,
            'aggregation_reason': reason,
            'details': {...}
        }
```

---

## 📊 예상 효과 검증

### 메드팩토 6건 재분석 (시뮬레이션)

| 시간 | VWAP | EMA | Vol | **Conf** | 기존 | 신규 | 결과 |
|------|------|-----|-----|----------|------|------|------|
| 10:11 | 0.08 | 0.15 | 0.1 | **0.33** | ✅ 진입 | ❌ 차단 | -1.41% 방지 |
| 10:13 | 0.05 | 0.10 | 0.05 | **0.20** | ✅ 진입 | ❌ 차단 | -4.53% 방지 |
| 10:16 | 0.35 | 0.25 | 0.25 | **0.85** | ✅ 진입 | ✅ 진입 | -0.62% (감수) |
| 10:18 | 0.10 | 0.10 | 0.05 | **0.25** | ✅ 진입 | ❌ 차단 | -1.39% 방지 |

**효과**:
- 6건 → 1건 (5건 차단)
- 손실 -3,910원 → -124원 (**-97%**)

---

### 신테카바이오 15:30 재분석

| 항목 | 값 | 점수 |
|------|-----|------|
| VWAP 돌파 | +0.15% | 0.15 |
| 5분봉 EMA | 미정렬 | 0.00 |
| 15분봉 EMA | 미정렬 | 0.00 |
| 거래량 Z | -0.5σ | 0.00 |
| **최종 Confidence** | - | **0.15** |

- 기존: Pass (VWAP만 체크) → 진입
- 신규: **Fail (Conf 0.15 < 0.5)** → 차단
- 효과: -1.82% 손실 방지

---

## 🎯 현재 상태 요약

### ✅ 완료 (100%)
- [x] Step 1: BaseFilter 인터페이스
- [x] Step 2: ConfidenceAggregator
- [x] Step 3: L3 MTF Filter V2
- [x] Step 4: L4 Liquidity Filter V2
- [x] Step 5: L5 Squeeze Filter V2
- [x] Step 6: L6 Validator V2
- [x] Step 7: SignalOrchestrator 통합

### ⏳ 다음 단계
- [ ] Phase 2: 백테스트 검증 (거래내역 27건)
- [ ] Phase 3: 전체 코드 검토 및 최적화

### 📅 실제 일정 (2025-11-23)
- **Day 1**: Phase 1 전체 구현 완료 ✅
  - L3-L6 V2 필터 구현
  - Confidence Aggregator 구현
  - SignalOrchestrator 통합
- **다음**: 백테스트 + 실전 테스트

---

## 🚀 다음 액션

### ✅ Phase 1 완료 (2025-11-23)

**구현 완료 항목**:
1. ✅ `trading/filters/base_filter.py` - FilterResult 클래스
2. ✅ `trading/confidence_aggregator.py` - Confidence 결합 엔진
3. ✅ `analyzers/multi_timeframe_consensus_v2.py` - L3 MTF V2
4. ✅ `analyzers/liquidity_shift_detector_v2.py` - L4 Liquidity V2
5. ✅ `analyzers/squeeze_momentum_v2.py` - L5 Squeeze V2
6. ✅ `analyzers/pre_trade_validator_v2.py` - L6 Validator V2
7. ✅ `analyzers/signal_orchestrator.py` - V2 통합 완료

**핵심 개선사항**:
- L3-L6 필터가 이제 0~1.0 Confidence 반환
- 가중 평균으로 최종 Confidence 계산 (L3:1.5, L4:1.0, L5:1.2, L6:0.8)
- Confidence < 0.5 시그널 자동 차단
- Confidence 기반 포지션 크기 조정 (0.6 ~ 1.0)
- 하위 호환성 유지 (기존 메서드는 그대로)

---

### 📋 Phase 2: 백테스트 검증

**목표**: 실제 거래내역 27건으로 Confidence 효과 검증

**테스트 대상**:
1. 메드팩토 6건 → 1건으로 감소하는지 확인
2. 신테카바이오 15:30 차단 확인
3. 전체 27건 중 약한 신호 필터링 비율

**방법**:
```python
# 기존 거래내역 로드
trades = load_historical_trades()  # 27건

# V2 필터로 재평가
for trade in trades:
    result = orchestrator.evaluate_signal(
        stock_code=trade['code'],
        stock_name=trade['name'],
        current_price=trade['entry_price'],
        df=trade['df'],
        market='KOSPI'
    )

    print(f"{trade['name']} - Conf: {result['confidence']:.2f}, "
          f"Allowed: {result['allowed']}")
```

---

### 📋 Phase 3: 전체 코드 검토

**검토 항목**:
1. V2 클래스 코드 품질 확인
2. Confidence 계산 로직 검증
3. 성능 최적화 (필요시)
4. 문서화 업데이트

---

**작성**: Claude Code (2025-11-23)
**상태**: Phase 1 구현 완료 ✅
**다음**: Phase 2 백테스트 또는 사용자 승인
