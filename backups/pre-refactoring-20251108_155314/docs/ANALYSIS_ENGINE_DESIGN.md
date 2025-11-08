# 🧠 Analysis Engine Design - 분석 엔진 설계

## 📋 목차
1. [개요](#개요)
2. [전체 구조](#전체-구조)
3. [개별 엔진 상세 설계](#개별-엔진-상세-설계)
4. [점수 산출 및 가중치](#점수-산출-및-가중치)
5. [통합 분석 로직](#통합-분석-로직)
6. [구현 우선순위](#구현-우선순위)

---

## 개요

### 🎯 목적
종목을 다각도로 분석하여 **투자 매력도 점수**를 산출하고, 최종적으로 **매수/매도 신호**를 생성

### 📊 최종 산출물
- **종합 점수**: 0~100점
- **투자 등급**: S, A, B, C, D (5단계)
- **매매 신호**: STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
- **신뢰도**: 0.0~1.0

---

## 전체 구조

### 🏗️ 분석 엔진 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    Analysis Engine Manager                   │
│                      (analysis_engine.py)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
                ┌──────────┴──────────┐
                │  개별 분석 엔진 실행  │
                └──────────┬──────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼──────┐  ┌────────▼────────┐  ┌─────▼──────┐
│ 1. 뉴스 분석  │  │ 2. 기술적 분석   │  │ 3. 수급 분석│
│  (30%)       │  │    (40%)        │  │   (20%)    │
└──────┬───────┘  └────────┬────────┘  └─────┬──────┘
       │                   │                  │
┌──────▼───────┐  ┌────────▼────────┐  ┌─────▼──────┐
│ 4. 기본 분석  │  │ 5. 차트 패턴    │  │ 6. 시장상황 │
│   (10%)      │  │     (포함)      │  │  (보정)    │
└──────────────┘  └─────────────────┘  └────────────┘
                           │
                ┌──────────▼──────────┐
                │   점수 통합 및 계산   │
                │  - 가중 평균 계산    │
                │  - 시장 상황 보정    │
                │  - 신뢰도 계산       │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │     최종 산출물      │
                │  - 종합점수 (0-100) │
                │  - 투자등급 (S-D)   │
                │  - 매매신호         │
                │  - 추천사유         │
                └─────────────────────┘
```

---

## 개별 엔진 상세 설계

### 1️⃣ 뉴스 분석 엔진 (News & Sentiment Analysis)
**가중치: 30%**

#### 📂 파일 구조
```
analyzers/
├── news_analyzer.py           # 뉴스 수집 및 전처리
└── sentiment_analyzer.py      # AI 기반 감성 분석 (Gemini)
```

#### 🔍 분석 항목
| 항목 | 가중치 | 설명 |
|------|--------|------|
| 감성 점수 | 40% | AI가 분석한 뉴스 감성 (-100 ~ +100) |
| 영향도 | 30% | 주가에 미치는 영향 (HIGH/MEDIUM/LOW) |
| 뉴스 빈도 | 20% | 최근 뉴스 발생 빈도 (관심도) |
| 신뢰도 | 10% | AI 분석 신뢰도 |

#### 📊 점수 계산 로직
```python
news_score = (
    sentiment_score * 0.4 +      # -100~100 → 0~100 변환 필요
    impact_score * 0.3 +          # HIGH=100, MEDIUM=60, LOW=30
    frequency_score * 0.2 +       # 뉴스 개수 기반
    confidence * 100 * 0.1        # 0.0~1.0 → 0~100
)
```

#### 🎯 입력/출력
**입력:**
- 종목코드, 종목명
- 검색 기간 (기본: 최근 3일)

**출력:**
```python
{
    "sentiment": "POSITIVE",           # VERY_POSITIVE ~ VERY_NEGATIVE
    "sentiment_score": 65,             # -100 ~ +100
    "confidence": 0.85,                # 0.0 ~ 1.0
    "impact": "MEDIUM",                # HIGH/MEDIUM/LOW
    "news_count": 12,                  # 뉴스 개수
    "positive_factors": [...],         # 긍정 요인 리스트
    "negative_factors": [...],         # 부정 요인 리스트
    "summary": "...",                  # 요약
    "final_score": 72.5                # 0 ~ 100
}
```

---

### 2️⃣ 기술적 분석 엔진 (Technical Analysis)
**가중치: 40%** (가장 높음)

#### 📂 파일 구조
```
analyzers/
├── technical_analyzer.py      # 기술적 지표 계산
├── technical_indicators.py    # 지표 계산 함수들
├── chart_pattern_analyzer.py  # 차트 패턴 인식
└── price_action_analyzer.py   # 가격 행동 분석
```

#### 🔍 분석 항목
| 카테고리 | 항목 | 가중치 | 설명 |
|----------|------|--------|------|
| **추세** | 이동평균선 | 15% | MA5, MA20, MA60 배열 |
| | 추세 강도 | 10% | ADX, 추세선 각도 |
| **모멘텀** | RSI | 10% | 과매수/과매도 |
| | MACD | 10% | 골든크로스/데드크로스 |
| | Stochastic | 5% | %K, %D 교차 |
| **변동성** | Bollinger Bands | 10% | 밴드 돌파, 폭 |
| | ATR | 5% | 변동성 수준 |
| **거래량** | Volume | 10% | 거래량 증가 패턴 |
| | OBV | 5% | 누적 거래량 추세 |
| **패턴** | 캔들 패턴 | 10% | 망치형, 역망치형 등 |
| | 차트 패턴 | 10% | 삼각수렴, 헤드앤숄더 등 |

#### 📊 점수 계산 로직
```python
technical_score = (
    trend_score * 0.25 +          # 추세 분석
    momentum_score * 0.25 +        # 모멘텀 지표
    volatility_score * 0.15 +      # 변동성 분석
    volume_score * 0.15 +          # 거래량 분석
    pattern_score * 0.20           # 패턴 인식
)
```

#### 🎯 입력/출력
**입력:**
- 종목코드
- 분석 기간 (일봉: 120일, 분봉: 최근 5일)

**출력:**
```python
{
    "trend": {
        "direction": "UP",             # UP/DOWN/SIDEWAYS
        "strength": 75,                # 0 ~ 100
        "ma_alignment": "BULLISH",     # 정배열/역배열
        "score": 80
    },
    "momentum": {
        "rsi": 65,                     # 0 ~ 100
        "macd": "GOLDEN_CROSS",        # 신호
        "stochastic": 70,
        "score": 75
    },
    "volatility": {
        "atr": 2500,
        "bb_position": "MIDDLE",       # UPPER/MIDDLE/LOWER
        "score": 60
    },
    "volume": {
        "volume_ratio": 1.8,           # 평균 대비
        "obv_trend": "UP",
        "score": 85
    },
    "patterns": {
        "candle_patterns": ["HAMMER"],
        "chart_patterns": ["TRIANGLE"],
        "score": 70
    },
    "final_score": 74.5                # 0 ~ 100
}
```

---

### 3️⃣ 수급 분석 엔진 (Supply & Demand Analysis)
**가중치: 20%**

#### 📂 파일 구조
```
analyzers/
├── supply_demand_analyzer.py  # 수급 분석 메인
└── volume_analyzer.py          # 거래량 상세 분석
```

#### 🔍 분석 항목
| 항목 | 가중치 | 설명 |
|------|--------|------|
| 기관 매매 | 35% | 기관 순매수/순매도 |
| 외국인 매매 | 35% | 외국인 순매수/순매도 |
| 개인 매매 | 10% | 개인 매매 동향 |
| 프로그램 매매 | 10% | 프로그램 순매수 |
| 체결강도 | 10% | 매수/매도 체결 강도 |

#### 📊 점수 계산 로직
```python
supply_demand_score = (
    institution_score * 0.35 +     # 기관 순매수 비중
    foreign_score * 0.35 +         # 외국인 순매수 비중
    individual_score * 0.10 +      # 개인 매매
    program_score * 0.10 +         # 프로그램 매매
    strength_score * 0.10          # 체결강도
)
```

#### 🎯 입력/출력
**입력:**
- 종목코드
- 분석 기간 (기본: 최근 10일)

**출력:**
```python
{
    "institution": {
        "net_buy": 5000000,            # 순매수량 (주)
        "net_buy_amount": 25000000000, # 순매수금액 (원)
        "trend": "BUYING",             # BUYING/SELLING/NEUTRAL
        "score": 85
    },
    "foreign": {
        "net_buy": 3000000,
        "net_buy_amount": 15000000000,
        "trend": "BUYING",
        "score": 80
    },
    "individual": {
        "trend": "SELLING",
        "score": 40
    },
    "program": {
        "net_buy": 1000000,
        "trend": "BUYING",
        "score": 70
    },
    "strength": {
        "buy_strength": 125.5,         # 100 기준
        "sell_strength": 98.2,
        "score": 75
    },
    "final_score": 78.0                # 0 ~ 100
}
```

---

### 4️⃣ 기본 분석 엔진 (Fundamental Analysis)
**가중치: 10%**

#### 📂 파일 구조
```
analyzers/
└── fundamental_analyzer.py     # 기본 분석
```

#### 🔍 분석 항목
| 항목 | 가중치 | 설명 |
|------|--------|------|
| PER | 25% | 주가수익비율 |
| PBR | 25% | 주가순자산비율 |
| ROE | 20% | 자기자본이익률 |
| 부채비율 | 15% | 재무 안정성 |
| 영업이익률 | 15% | 수익성 |

#### 📊 점수 계산 로직
```python
fundamental_score = (
    per_score * 0.25 +             # 낮을수록 좋음
    pbr_score * 0.25 +             # 낮을수록 좋음
    roe_score * 0.20 +             # 높을수록 좋음
    debt_ratio_score * 0.15 +      # 적정 수준
    profit_margin_score * 0.15     # 높을수록 좋음
)
```

#### 🎯 입력/출력
**입력:**
- 종목코드

**출력:**
```python
{
    "valuation": {
        "per": 12.5,                   # 배
        "pbr": 1.2,
        "sector_per_avg": 15.0,        # 업종 평균
        "score": 75
    },
    "profitability": {
        "roe": 15.2,                   # %
        "operating_margin": 12.5,
        "score": 80
    },
    "stability": {
        "debt_ratio": 85.0,            # %
        "score": 70
    },
    "final_score": 75.0                # 0 ~ 100
}
```

---

### 5️⃣ 시장 상황 분석 엔진 (Market Regime Analysis)
**역할: 보정 계수 (0.8 ~ 1.2)**

#### 📂 파일 구조
```
analyzers/
└── market_regime_detector.py   # 시장 상황 감지
```

#### 🔍 분석 항목
| 항목 | 설명 |
|------|------|
| 시장 추세 | KOSPI/KOSDAQ 추세 (강세/약세/횡보) |
| 시장 변동성 | VIX, 시장 변동성 지수 |
| 섹터 강도 | 해당 섹터의 상대 강도 |
| 투자 심리 | 공포/탐욕 지수 |

#### 📊 보정 계수 계산
```python
if market_regime == "BULL_TREND":
    correction_factor = 1.1        # +10% 보너스
elif market_regime == "BEAR_TREND":
    correction_factor = 0.9        # -10% 페널티
elif market_regime == "HIGH_VOLATILITY":
    correction_factor = 0.85       # -15% 페널티
else:  # SIDEWAYS, LOW_VOLATILITY
    correction_factor = 1.0        # 보정 없음

final_score = base_score * correction_factor
```

#### 🎯 출력
```python
{
    "regime": "BULL_TREND",           # 시장 상황
    "confidence": 0.85,
    "correction_factor": 1.1,
    "reason": "KOSPI 상승 추세, 섹터 강세"
}
```

---

## 점수 산출 및 가중치

### 📊 최종 점수 계산 공식

```python
# 1단계: 개별 엔진 점수 (0~100)
news_score = 72.5
technical_score = 74.5
supply_demand_score = 78.0
fundamental_score = 75.0

# 2단계: 가중 평균 계산
base_score = (
    news_score * 0.30 +           # 30%
    technical_score * 0.40 +      # 40%
    supply_demand_score * 0.20 +  # 20%
    fundamental_score * 0.10      # 10%
)
# base_score = 74.65

# 3단계: 시장 상황 보정
market_correction = 1.1  # 강세장
final_score = base_score * market_correction
# final_score = 82.12 (최대 100으로 제한)

# 4단계: 신뢰도 계산
confidence = min(
    news_confidence,
    technical_confidence,
    supply_demand_confidence
) * 0.7 + 0.3  # 최소 30% 신뢰도 보장
```

### 🏆 투자 등급 분류

| 점수 범위 | 등급 | 매매 신호 | 설명 |
|-----------|------|-----------|------|
| 90 ~ 100 | S | STRONG_BUY | 매우 강력한 매수 추천 |
| 80 ~ 89 | A | BUY | 매수 추천 |
| 70 ~ 79 | B | HOLD / WEAK_BUY | 보유 또는 약한 매수 |
| 60 ~ 69 | C | HOLD / WEAK_SELL | 보유 또는 약한 매도 |
| 0 ~ 59 | D | SELL / STRONG_SELL | 매도 추천 |

---

## 통합 분석 로직

### 🔄 전체 프로세스

```python
class AnalysisEngine:
    """통합 분석 엔진"""

    def analyze_stock(self, stock_code: str, stock_name: str):
        """종목 종합 분석"""

        # 1단계: 개별 엔진 실행 (병렬 처리 가능)
        news_result = self.news_analyzer.analyze(stock_code, stock_name)
        technical_result = self.technical_analyzer.analyze(stock_code)
        supply_demand_result = self.supply_demand_analyzer.analyze(stock_code)
        fundamental_result = self.fundamental_analyzer.analyze(stock_code)

        # 2단계: 시장 상황 분석
        market_regime = self.market_regime_detector.detect()

        # 3단계: 점수 통합
        base_score = self._calculate_weighted_score(
            news_result['final_score'],
            technical_result['final_score'],
            supply_demand_result['final_score'],
            fundamental_result['final_score']
        )

        # 4단계: 시장 상황 보정
        final_score = base_score * market_regime['correction_factor']
        final_score = min(final_score, 100)  # 최대 100점

        # 5단계: 신뢰도 계산
        confidence = self._calculate_confidence([
            news_result.get('confidence', 0.8),
            technical_result.get('confidence', 0.9),
            supply_demand_result.get('confidence', 0.85)
        ])

        # 6단계: 등급 및 신호 결정
        grade = self._determine_grade(final_score)
        signal = self._determine_signal(final_score, confidence)

        # 7단계: 추천 사유 생성
        reasons = self._generate_reasons(
            news_result, technical_result,
            supply_demand_result, fundamental_result
        )

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "final_score": round(final_score, 2),
            "grade": grade,
            "signal": signal,
            "confidence": round(confidence, 2),
            "market_regime": market_regime['regime'],
            "details": {
                "news": news_result,
                "technical": technical_result,
                "supply_demand": supply_demand_result,
                "fundamental": fundamental_result
            },
            "reasons": reasons,
            "analyzed_at": datetime.now().isoformat()
        }
```

---

## 구현 우선순위

### 📅 Phase 1: 핵심 엔진 (Week 1-2)
1. ✅ **뉴스 분석 엔진** (이미 테스트 완료)
   - `analyzers/news_analyzer.py`
   - `analyzers/sentiment_analyzer.py`

2. 🔄 **기술적 분석 엔진**
   - `analyzers/technical_analyzer.py`
   - `analyzers/technical_indicators.py`
   - 기본 지표: MA, RSI, MACD, Bollinger Bands

3. 🔄 **통합 분석 엔진**
   - `analyzers/analysis_engine.py`
   - 점수 통합 로직

### 📅 Phase 2: 확장 엔진 (Week 3-4)
4. ⏳ **수급 분석 엔진**
   - `analyzers/supply_demand_analyzer.py`
   - `analyzers/volume_analyzer.py`

5. ⏳ **차트 패턴 분석**
   - `analyzers/chart_pattern_analyzer.py`

6. ⏳ **기본 분석 엔진**
   - `analyzers/fundamental_analyzer.py`

### 📅 Phase 3: 고도화 (Week 5+)
7. ⏳ **시장 상황 분석**
   - `analyzers/market_regime_detector.py`

8. ⏳ **AI 최적화**
   - 가중치 자동 조정
   - 백테스팅 기반 학습

---

## 🎯 다음 단계

1. **뉴스 분석 엔진 모듈화** (test → analyzers 이동)
2. **기술적 분석 엔진 구현 시작**
3. **키움 API 차트 데이터 수집기 구현**

---

## 📚 참고 자료

- trading_system 프로젝트의 analyzers 구조
- TA-Lib 기술적 지표 라이브러리
- Gemini AI API 문서
