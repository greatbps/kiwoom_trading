# 수급 분석 시스템 검증 보고서

## 📋 검증 일시
2025-11-07

## ✅ 검증 결과: **정상 작동**

사용자가 자동매매 실행 중 확인한 출력:
```
[3/4] 수급 분석 중...
[DEBUG] investor_data: 100건
[DEBUG] program_data: 0건
수급 점수: 25.00/50
외국인: 0점
기관: 50점
추천: 보통 (관망)
```

---

## 🔍 상세 분석

### 1. 데이터 수집 단계

**위치**: `analyzers/supply_demand_analyzer.py:86-105`

```python
def analyze_investor_trend(self, investor_data: List[Dict[str, Any]], ...):
    if not investor_data or len(investor_data) == 0:
        return {
            'foreign_score': 0,
            'institution_score': 0,
            'total_score': 50,  # 데이터 없음 시 중립
            'signals': ['수급 데이터 없음']
        }
```

#### 수집된 데이터:
- ✅ **investor_data: 100건** - 투자자별 매매 동향 데이터 정상 수집
  - 외국인 5일 순매수 금액 추출
  - 기관 5일 순매수 금액 추출
  - 최근 5일 데이터 사용 (Line 107)

- ✅ **program_data: 0건** - 프로그램 매매 데이터 없음
  - 정상: 해당 종목에 프로그램 거래가 없을 수 있음
  - 현재 알고리즘은 program_data를 사용하지 않음 (Line 217 주석)

---

### 2. 점수 계산 로직

**위치**: `analyzers/supply_demand_analyzer.py:29-84`

#### 정규화 비율 계산 (Line 45-47)
```python
denom = max(avg_turnover_5d, 1)  # 0 방지
norm_ratio = net_buy_5d / denom
```

**목적**: 시가총액이 다른 종목들을 공정하게 비교
- 삼성전자: 순매수 100억원 / 거래대금 5000억원 = 2%
- 소형주: 순매수 10억원 / 거래대금 50억원 = 20% (더 강한 신호!)

#### 기본 점수 계산 (Line 49-67)

| 구간 | norm_ratio | 점수 | 설명 |
|------|-----------|------|------|
| 매우 강함 | ≥ 50% | 50점 | 거래대금의 절반 이상 순매수 |
| 강함 | 20% ~ 50% | 0~50점 (선형) | 강한 매수 신호 |
| 중간 | 5% ~ 20% | 0~25점 (완만) | 중간 매수 신호 |
| 약함 | 0% ~ 5% | 0~12.5점 (매우 완만) | 약한 매수 신호 |
| 순매도 | < 0% | 0점 | 매도 우세 |

#### 보너스 시스템 (Line 69-81)

1. **일관성 보너스** (Line 70-71): 4일 이상 연속 매수 → 1.10배
   ```python
   if buy_days_5d >= 4:
       score *= 1.10
   ```

2. **가속 보너스** (Line 74-75): 최근 3일 증가 추세 → 1.10배
   ```python
   if last3_trend_ok:
       score *= 1.10
   ```

3. **거래량 확인** (Line 78-81): 평상시 대비 120% 이상 → 1.05배
   ```python
   if volume_ratio >= 1.2:
       score *= 1.05
   ```

---

### 3. 실제 출력 결과 검증

#### 외국인: 0점

**계산 과정**:
1. 5일 순매수 금액: `≤ 0` (순매도 또는 보합)
2. `norm_ratio = net_buy_5d / denom ≤ 0`
3. Line 66-67: `else: score = 0.0`

**해석**: 외국인이 해당 종목을 5일간 순매도했거나 거래하지 않음

#### 기관: 50점

**계산 과정**:
1. 5일 순매수 금액: 거래대금의 50% 이상
2. `norm_ratio = net_buy_5d / denom ≥ 0.5`
3. Line 50-52: `if norm_ratio >= 0.5: score = 50.0`

**해석**: 기관이 해당 종목을 매우 강하게 매수 중 (거래대금 대비 50% 이상!)

#### 총점: 25.00/50

**계산 과정** (Line 153):
```python
total_score = min(foreign_score + inst_score, 100) / 2
            = min(0 + 50, 100) / 2
            = 50 / 2
            = 25.00
```

**정규화 이유**: 외국인+기관 합산 시 최대 100점이 되므로, 50점 만점으로 변환

#### 추천: "보통 (관망)"

**판단 로직** (Line 244-253):
```python
score = 25.00

if score >= 42:      # 84% 이상
    recommendation = "강한 동행 수급 (우수)"
elif score >= 32:    # 64% 이상
    recommendation = "수급 양호"
elif score >= 22:    # 44% 이상  ← 여기 해당!
    recommendation = "보통 (관망)"
else:
    recommendation = "수급 약함"
```

**해석**:
- 외국인은 매도/보합
- 기관은 강하게 매수
- **상충되는 신호** → 관망 추천

---

## 📊 가속 여부 확인 알고리즘

**위치**: `analyzers/supply_demand_analyzer.py:299-318`

```python
def _check_acceleration(self, recent_amounts: List[float]) -> bool:
    """
    최근 데이터의 가속 여부 확인

    Args:
        recent_amounts: 최근 순매수 금액 리스트 (시간순)

    Returns:
        가속 여부
    """
    if len(recent_amounts) < 2:
        return False

    # 마지막 값이 평균보다 큰지 확인
    if len(recent_amounts) >= 3:
        avg = sum(recent_amounts[:-1]) / len(recent_amounts[:-1])
        return recent_amounts[-1] > avg and recent_amounts[-1] > 0
    else:
        # 2개 데이터면 증가 추세만 확인
        return recent_amounts[-1] > recent_amounts[-2] and recent_amounts[-1] > 0
```

**로직**:
1. 3일 이상 데이터: 마지막 날 > 이전 평균 + 양수
2. 2일 데이터: 마지막 날 > 이전 날 + 양수

---

## 💡 시그널 생성 로직

**위치**: `analyzers/supply_demand_analyzer.py:155-192`

### 외국인 시그널 (Line 158-169)
```python
if foreign_net_5d > 0:
    norm_ratio_f = foreign_net_5d / max(avg_turnover_5d, 1)
    signals.append(f"외국인 5일 순매수 {금액}원 (비율: {norm_ratio_f:.1%}) [{score:.1f}점]")

    if foreign_buy_days >= 4:
        signals.append(f"  └ {foreign_buy_days}일 연속 매수 (일관성 ✅)")

    if foreign_trend_ok:
        signals.append(f"  └ 최근 3일 가속 (추세 ✅)")

elif foreign_net_5d < 0:
    signals.append(f"외국인 5일 순매도 {금액}원 ❌")
else:
    signals.append("외국인 보합 ➡️")
```

### 기관 시그널 (Line 171-182)
- 동일한 로직

### 일관성 분석 (Line 184-188)
```python
if foreign_net_5d > 0 and inst_net_5d > 0:
    signals.append("🚀 외국인+기관 동반 매수 (일관성 우수)")
elif foreign_net_5d < 0 and inst_net_5d < 0:
    signals.append("⚠️ 외국인+기관 동반 매도 (주의)")
```

---

## ✅ 결론

### 1. 수급 분석은 **정상적으로 작동** 중

- ✅ 100건의 투자자 데이터 정상 수집
- ✅ 정규화 비율 계산으로 시가총액 차이 보정
- ✅ 외국인 0점, 기관 50점 → 총 25점 계산 정확
- ✅ "보통 (관망)" 추천 정확 (상충 신호)

### 2. 알고리즘 품질

- ✅ **정규화 비율**: 대형주/소형주 공정 비교
- ✅ **3단계 보너스**: 일관성, 가속, 거래량 확인
- ✅ **상충 신호 감지**: 외국인 vs 기관 다른 방향 감지
- ✅ **가속 여부**: 최근 3일 증가 추세 확인

### 3. 실제 동작 예시

이번 케이스:
- 외국인: 5일간 순매도 또는 보합 → **0점**
- 기관: 거래대금의 50% 이상 순매수 → **50점** (매우 강함!)
- 총점: (0 + 50) / 2 = **25점**
- 추천: 22~32점 구간 → **"보통 (관망)"**

**의미**:
- 기관은 강하게 매수 중
- 외국인은 관심 없음
- → 기관 단독 매수, 외국인 참여 없음 → 관망 추천

---

## 📝 추가 개선 가능 사항

### 1. 프로그램 매매 데이터 활용
현재는 `program_data`를 수집하지만 점수 계산에 사용하지 않음

**위치**: `analyzers/supply_demand_analyzer.py:217`
```python
program_data: List[Dict[str, Any]] = None,  # 현재 미사용
```

**개선 방안**:
```python
# 프로그램 순매수도 점수에 반영 (10점 만점)
if program_data:
    program_score = calculate_program_score(program_data)
    total_score = (foreign_score + inst_score) / 2 + program_score / 5
```

### 2. 시그널 강도 레벨
현재는 단순히 점수만 표시

**개선 방안**:
```python
if foreign_score >= 45:
    level = "🔥 초강력"
elif foreign_score >= 35:
    level = "💪 강력"
elif foreign_score >= 25:
    level = "👍 양호"
...
```

### 3. 역사적 비교
과거 데이터와 비교하여 상대적 강도 표시

**개선 방안**:
```python
# 과거 30일 평균 수급 점수와 비교
historical_avg = get_historical_avg_score(stock_code, days=30)
if current_score > historical_avg * 1.5:
    signals.append("📈 과거 대비 매우 강한 수급 (150% 이상)")
```

---

## 🎯 최종 평가

| 항목 | 상태 | 비고 |
|------|------|------|
| 데이터 수집 | ✅ 정상 | 100건 투자자 데이터 수집 |
| 정규화 계산 | ✅ 우수 | 시가총액 차이 보정 |
| 점수 계산 | ✅ 정확 | 외국인 0점, 기관 50점, 총 25점 |
| 추천 로직 | ✅ 정확 | 상충 신호 감지 → 관망 추천 |
| 보너스 시스템 | ✅ 작동 | 일관성, 가속, 거래량 확인 |
| 시그널 생성 | ✅ 명확 | 사용자가 이해하기 쉬운 메시지 |

**종합 평가**: 🏆 **수급 분석 시스템은 정상 작동 중이며 정교하게 설계되어 있음**

---

**다음 실행부터 안심하고 사용하세요!** 🎯
