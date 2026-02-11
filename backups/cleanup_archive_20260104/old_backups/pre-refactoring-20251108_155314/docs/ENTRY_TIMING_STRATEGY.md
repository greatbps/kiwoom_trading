# 진입 타이밍 전략 (Entry Timing)

## 🎯 문제점

```
현재 로직:
  통합 분석 70점 이상 → 즉시 매수 ❌

문제:
  - 고점에서 매수할 위험
  - 단기 조정 직전 매수 가능
  - 진입 타이밍 최적화 부재
```

## ✅ 개선된 로직

```
2단계 필터링:
  1차: 통합 분석 70점 이상 (종목 선별)
  2차: 실시간 진입 타이밍 확인 (매수 시점)

1차 통과 → 감시 대상 등록 📋
2차 통과 → 실제 매수 실행 💰
```

---

## 📊 1차 필터: 종목 선별 (기존)

```python
# 조건검색 + 통합 분석
if final_score >= 70:
    # 감시 대상 등록
    watchlist.add({
        'stock_code': '005070',
        'stock_name': '코스모신소재',
        'score': 76.73,
        'added_time': '2025-10-25 09:30:00'
    })

    → 즉시 매수 ❌
    → 감시 시작 ✅
```

---

## 🎯 2차 필터: 진입 타이밍 (신규)

### 실시간 모니터링 (1분봉)

```python
# 감시 대상을 1분마다 체크
for stock in watchlist:
    # 현재가 + 1분봉 데이터 조회
    current_data = get_realtime_data(stock)

    # 진입 타이밍 지표 계산
    entry_signals = check_entry_timing(current_data)

    if entry_signals.all_green():
        → 매수 실행! 🟢
```

---

## 📈 진입 타이밍 지표 (5가지)

### 1️⃣ VWAP (Volume Weighted Average Price)

```python
# VWAP 계산
VWAP = Σ(가격 × 거래량) / Σ거래량

# 진입 조건
if 현재가 > VWAP:
    → ✅ "VWAP 상단 (매수 우위)"
else:
    → ❌ "VWAP 하단 (관망)"

목적: 평균 매수가보다 높은 가격에 매수 중 = 매수세 우위
```

**예시:**
```
VWAP: 10,000원
현재가: 10,150원 (+1.5%)
→ ✅ 매수세 우위, 진입 가능
```

---

### 2️⃣ 단기 이동평균선 (5분/20분 골든크로스)

```python
# 5분봉 / 20분봉 MA
MA5 = 최근 5분봉 평균
MA20 = 최근 20분봉 평균

# 진입 조건
if MA5 > MA20 and 현재가 > MA5:
    → ✅ "단기 상승 추세"
else:
    → ❌ "추세 약함"

목적: 단기 상승 모멘텀 확인
```

**예시:**
```
MA5:  10,100원
MA20: 10,000원
현재가: 10,150원
→ ✅ 골든크로스 + 현재가 > MA5
```

---

### 3️⃣ RSI (Relative Strength Index)

```python
# RSI 계산 (14봉 기준)
RSI = 100 - (100 / (1 + RS))
RS = 평균 상승폭 / 평균 하락폭

# 진입 조건
if 30 < RSI < 70:
    → ✅ "적정 구간 (과매수/과매도 아님)"
elif RSI <= 30:
    → ⚠️ "과매도 (반등 대기)"
elif RSI >= 70:
    → ❌ "과매수 (진입 보류)"

목적: 과매수 구간 진입 방지
```

**예시:**
```
RSI: 55
→ ✅ 적정 구간 (30~70)
→ 과매수 아님, 진입 가능
```

---

### 4️⃣ 거래량 급증 (Volume Surge)

```python
# 평균 거래량 대비 현재 거래량
avg_volume = 최근 20분봉 평균 거래량
current_volume = 현재 1분 거래량

volume_ratio = current_volume / avg_volume

# 진입 조건
if 1.2 < volume_ratio < 3.0:
    → ✅ "거래량 적정 증가 (관심 증가)"
elif volume_ratio > 3.0:
    → ⚠️ "거래량 폭증 (단기 급등, 주의)"
else:
    → ❌ "거래량 부족"

목적: 적정 거래량 확인, 과도한 급등 회피
```

**예시:**
```
평균 거래량: 10,000주/분
현재 거래량: 15,000주/분
비율: 1.5배
→ ✅ 적정 증가 (1.2~3.0배)
```

---

### 5️⃣ 호가 스프레드 (Bid-Ask Spread)

```python
# 매수/매도 호가 분석
best_bid = 최우선 매수호가
best_ask = 최우선 매도호가
spread = best_ask - best_bid

bid_volume = 매수 호가 총량 (상위 5호가)
ask_volume = 매도 호가 총량 (상위 5호가)

# 진입 조건
if bid_volume > ask_volume × 1.2:
    → ✅ "매수 우위 (20% 이상 많음)"
else:
    → ❌ "매도 우위 또는 균형"

목적: 매수 대기 물량이 많아 상승 압력 확인
```

**예시:**
```
매수 호가: 120,000주
매도 호가: 90,000주
비율: 1.33배
→ ✅ 매수 우위 (1.2배 이상)
```

---

## 🎯 종합 진입 신호

### 5개 지표 점수화

```python
entry_score = 0

# 1. VWAP
if 현재가 > VWAP:
    entry_score += 20

# 2. 이동평균
if MA5 > MA20 and 현재가 > MA5:
    entry_score += 25

# 3. RSI
if 30 < RSI < 70:
    entry_score += 20

# 4. 거래량
if 1.2 < volume_ratio < 3.0:
    entry_score += 20

# 5. 호가
if bid_volume > ask_volume × 1.2:
    entry_score += 15

# 진입 조건
if entry_score >= 70:
    → 매수 실행! 🟢
elif entry_score >= 50:
    → 관망 (조건부 진입) ⚠️
else:
    → 진입 보류 ❌
```

---

## 🔄 실제 매수 플로우

```
09:00 장 시작
  ↓
09:05 조건검색 실행
  - Momentum 전략: 18개 종목
  ↓
09:10 통합 분석 (1차 필터)
  - 70점 이상: 12개 종목
  - 감시 대상 등록 📋
  ↓
09:11~15:00 실시간 감시 (2차 필터)
  ↓
[1분마다 반복]
  ├─ 코스모신소재
  │   ├─ VWAP: ✅ 20점
  │   ├─ MA: ✅ 25점
  │   ├─ RSI: ✅ 20점
  │   ├─ Volume: ❌ 0점
  │   └─ Spread: ✅ 15점
  │   → 총점: 80점
  │   → 매수 실행! 💰 (09:15)
  │
  ├─ 로보스타
  │   ├─ VWAP: ✅ 20점
  │   ├─ MA: ❌ 0점
  │   ├─ RSI: ✅ 20점
  │   ├─ Volume: ✅ 20점
  │   └─ Spread: ❌ 0점
  │   → 총점: 60점
  │   → 관망 ⏸️ (다음 분봉 대기)
  │
  └─ 대한전선
      → 총점: 40점
      → 진입 보류 ❌
```

---

## 💡 실제 코드 구조

```python
class EntryTimingAnalyzer:
    """진입 타이밍 분석기"""

    def analyze_entry_timing(self, stock_code: str) -> dict:
        """
        진입 타이밍 분석

        Returns:
            {
                'can_enter': bool,
                'entry_score': int,
                'signals': {
                    'vwap': {'pass': bool, 'score': int, 'value': float},
                    'ma': {'pass': bool, 'score': int},
                    'rsi': {'pass': bool, 'score': int, 'value': float},
                    'volume': {'pass': bool, 'score': int, 'ratio': float},
                    'spread': {'pass': bool, 'score': int, 'ratio': float}
                },
                'recommendation': str
            }
        """
        # 1. 실시간 데이터 조회
        current_price = self.get_current_price(stock_code)
        minute_data = self.get_minute_chart(stock_code, period=20)  # 20분봉
        orderbook = self.get_orderbook(stock_code)

        # 2. 각 지표 계산
        vwap_signal = self._check_vwap(current_price, minute_data)
        ma_signal = self._check_moving_average(current_price, minute_data)
        rsi_signal = self._check_rsi(minute_data)
        volume_signal = self._check_volume(minute_data)
        spread_signal = self._check_spread(orderbook)

        # 3. 종합 점수
        entry_score = sum([
            vwap_signal['score'],
            ma_signal['score'],
            rsi_signal['score'],
            volume_signal['score'],
            spread_signal['score']
        ])

        # 4. 진입 여부
        can_enter = entry_score >= 70

        return {
            'can_enter': can_enter,
            'entry_score': entry_score,
            'signals': {
                'vwap': vwap_signal,
                'ma': ma_signal,
                'rsi': rsi_signal,
                'volume': volume_signal,
                'spread': spread_signal
            },
            'recommendation': self._get_recommendation(entry_score)
        }
```

---

## 📊 매수 타이밍 시나리오

### 시나리오 1: 이상적인 진입

```
09:15 코스모신소재 (통합점수 76.73)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
현재가: 10,150원

[진입 타이밍 분석]
✅ VWAP: 10,000원 → 현재가 상단 (20점)
✅ MA5 > MA20: 골든크로스 (25점)
✅ RSI: 55 → 적정 구간 (20점)
✅ 거래량: 1.5배 증가 (20점)
✅ 호가: 매수 1.3배 우위 (15점)

총점: 100점 🎯

→ 즉시 매수! 300주 @ 10,150원
```

### 시나리오 2: 진입 대기

```
09:20 로보스타 (통합점수 77.43)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
현재가: 11,200원

[진입 타이밍 분석]
✅ VWAP: 11,000원 → 현재가 상단 (20점)
❌ MA5 < MA20: 데드크로스 (0점)
✅ RSI: 45 → 적정 구간 (20점)
✅ 거래량: 1.8배 증가 (20점)
❌ 호가: 매수/매도 균형 (0점)

총점: 60점 ⚠️

→ 관망 (다음 1분봉 대기)
```

### 시나리오 3: 진입 보류

```
09:25 대한전선 (통합점수 73.15)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
현재가: 9,800원

[진입 타이밍 분석]
❌ VWAP: 10,000원 → 현재가 하단 (0점)
❌ MA5 < MA20: 데드크로스 (0점)
✅ RSI: 40 → 적정 구간 (20점)
❌ 거래량: 0.8배 감소 (0점)
❌ 호가: 매도 우위 (0점)

총점: 20점 ❌

→ 감시 대상 유지, 진입 보류
```

---

## 🎯 핵심 개선 사항

### Before (기존)
```
1차 필터: 통합 분석 70점
  ↓
즉시 매수 → 고점 매수 위험 ⚠️
```

### After (개선)
```
1차 필터: 통합 분석 70점 (종목 선별)
  ↓
감시 대상 등록
  ↓
2차 필터: 진입 타이밍 70점 (매수 시점)
  ↓
최적 타이밍 매수 → 승률 향상 ✅
```

---

## 📋 구현 체크리스트

- [ ] `EntryTimingAnalyzer` 클래스 생성
- [ ] VWAP 계산 로직 구현
- [ ] 단기 이동평균 (5분/20분) 계산
- [ ] RSI 계산 로직 구현
- [ ] 거래량 비율 계산
- [ ] 호가창 분석 (매수/매도 비율)
- [ ] 종합 점수 계산 (100점 만점)
- [ ] 실시간 감시 루프에 통합
- [ ] 백테스팅으로 임계값 최적화

---

**작성일**: 2025-10-25
**최종 수정**: 2025-10-25
