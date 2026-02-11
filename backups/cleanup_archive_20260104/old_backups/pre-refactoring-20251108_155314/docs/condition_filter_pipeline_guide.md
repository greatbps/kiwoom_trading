# 키움 조건식 → VWAP 필터링 파이프라인 가이드

## 📋 개요

키움증권 조건식으로 1차 필터링한 종목을 VWAP 전략으로 2차 검증하여 최종 투자 종목을 선정하는 자동화 시스템입니다.

---

## 🔄 전체 플로우

```
1️⃣  키움 조건식 검색 (1차 필터링)
    ↓
   50~100개 종목
    ↓
2️⃣  VWAP 사전 검증 (2차 필터링)
    ↓
   5~20개 종목
    ↓
3️⃣  백테스트 시뮬레이션
    ↓
   최종 투자 대상 선정
    ↓
4️⃣  리포트 생성
```

---

## 🚀 실행 방법

### 기본 실행

```bash
source venv/bin/activate
python main_condition_filter.py
```

### 조건식 선택 커스터마이징

`main_condition_filter.py` 파일의 `CONDITION_INDICES` 변수를 수정하여 사용할 조건식을 선택할 수 있습니다:

```python
# 기본 설정 (Momentum, Breakout, EOD 전략)
CONDITION_INDICES = [17, 18, 19]

# GreatBPS 전용 조건식 사용
CONDITION_INDICES = [6, 24, 26]  # GREAT_1016, 신고가(GreatBPS), 키움데이(GreatBPS)

# 특정 조건식 하나만 사용
CONDITION_INDICES = [17]  # Momentum 전략만
```

조건식 인덱스는 실행 시 출력되는 "조건검색식 목록" 테이블에서 확인할 수 있습니다.

### 단계별 과정

**1단계: 키움 API 로그인**
- 자동으로 로그인 창 표시
- 로그인 완료 대기

**2단계: 조건식 검색**
- 설정된 6개 조건식으로 종목 검색
- 중복 제거 후 1차 후보 종목 리스트 생성

**3단계: VWAP 2차 검증**
- 각 종목의 최근 5일 데이터 다운로드
- VWAP 전략 시뮬레이션 실행
- 검증 기준:
  ```
  - 최소 거래: 2회 이상
  - 최소 승률: 50% 이상
  - 최소 평균 수익률: +0.5% 이상
  - 최소 Profit Factor: 1.2 이상
  ```

**4단계: 시뮬레이션**
- 검증 통과 종목에 대해 7일 백테스트
- 실제 성과 확인

**5단계: 리포트**
- 최종 선정 종목 리스트
- 성과 통계
- 추천 순위

---

## 📊 출력 예시

### 조건식 검색 결과

```
═══ 1단계: 조건식 검색 (1차 필터링) ═══

조건식 목록
┏━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ 번호 ┃ 조건식 이름         ┃ 검색 여부 ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│  0 │ 거래량급증          │ ✅      │
│  1 │ 20일신고가돌파      │ ✅      │
│  2 │ RSI과매도           │ ✅      │
│  3 │ MACD골든크로스      │ ✅      │
│  4 │ 이평선정배열        │ ✅      │
│  5 │ 볼린저밴드하단      │ ✅      │
└────┴────────────────────┴─────────┘

  ✅ 거래량급증: 23개 종목
  ✅ 20일신고가돌파: 15개 종목
  ✅ RSI과매도: 18개 종목
  ✅ MACD골든크로스: 27개 종목
  ✅ 이평선정배열: 31개 종목
  ✅ 볼린저밴드하단: 12개 종목

총 68개 종목 발견 (중복 제거)
```

### VWAP 2차 검증 결과

```
═══ 2단계: VWAP 사전 검증 (2차 필터링) ═══

검증 기준:
  - 최소 거래: 2회
  - 최소 승률: 50%
  - 최소 평균 수익률: +0.5%
  - 최소 Profit Factor: 1.2

  ✅ 삼성전자: 승률 66.7%, 수익 +1.2%
  ✅ LG화학: 승률 100.0%, 수익 +5.6%
  ✅ 카카오: 승률 75.0%, 수익 +2.1%
  ✅ 현대차: 승률 60.0%, 수익 +0.8%
  ✅ NAVER: 승률 55.6%, 수익 +1.5%

✅ 검증 통과: 5개
❌ 검증 실패: 63개

2차 검증 통과 종목
┏━━━━━━━━━━┳━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━━━━━━┓
┃ 종목명    ┃ 코드    ┃ 거래수 ┃ 승률  ┃ 평균수익률  ┃
┡━━━━━━━━━━╇━━━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━━━━━━┩
│ LG화학    │ 051910 │  3회  │ 100% │   +5.60%   │
│ 카카오    │ 035720 │  4회  │  75% │   +2.10%   │
│ NAVER     │ 035420 │  9회  │ 55.6% │   +1.50%   │
│ 삼성전자  │ 005930 │  3회  │ 66.7% │   +1.20%   │
│ 현대차    │ 005380 │  5회  │  60% │   +0.80%   │
└──────────┴────────┴──────┴──────┴────────────┘
```

### 최종 리포트

```
╔══════════════════════════════════════════════════════╗
║                 최종 리포트                         ║
╚══════════════════════════════════════════════════════╝

1️⃣  조건식 검색 결과

  📋 거래량급증: 23개 종목
  📋 20일신고가돌파: 15개 종목
  📋 RSI과매도: 18개 종목
  📋 MACD골든크로스: 27개 종목
  📋 이평선정배열: 31개 종목
  📋 볼린저밴드하단: 12개 종목

2️⃣  VWAP 2차 검증 결과

  ✅ 통과: 5개 종목

3️⃣  시뮬레이션 최종 결과

최종 선정 종목 성과
┏━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━┳━━━━━━┓
┃ 종목명    ┃ 거래  ┃ 평균수익률  ┃ 승률  ┃
┡━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━╇━━━━━━┩
│ LG화학    │  3회 │    +5.59%  │ 100% │
│ 카카오    │  4회 │    +2.15%  │  75% │
│ NAVER     │  9회 │    +1.48%  │  56% │
│ 삼성전자  │  3회 │    +1.18%  │  67% │
│ 현대차    │  5회 │    +0.82%  │  60% │
└──────────┴──────┴────────────┴──────┘

전체 평균:
  - 총 거래: 24회
  - 평균 수익률: +2.24%
  - 평균 승률: 71.6%
```

---

## ⚙️ 설정 커스터마이징

### 조건식 선택

```python
# main_condition_filter.py에서 수정

# 모든 조건식 사용
stock_codes = pipeline.search_conditions([0, 1, 2, 3, 4, 5, 6, 7])

# 특정 조건식만 사용
stock_codes = pipeline.search_conditions([0, 3, 5])  # 3개만
```

### 검증 기준 조정

```python
# 파이프라인 초기화 시 수정
validator = PreTradeValidator(
    config=self.config,
    lookback_days=7,       # 검증 기간 늘림
    min_trades=3,          # 최소 거래 3회로 상향
    min_win_rate=60.0,     # 승률 60% 이상
    min_avg_profit=1.0,    # 수익률 1% 이상
    min_profit_factor=1.5  # PF 1.5 이상
)
```

### 전략 변경

```python
# 다른 전략 사용
pipeline = ConditionFilterPipeline(
    config_path="config/strategy_aggressive.yaml"  # 공격적 전략
)
```

---

## 💡 활용 시나리오

### 시나리오 1: 보수적 선별 (소수 정예)

```python
# 엄격한 기준 적용
validator = PreTradeValidator(
    min_trades=5,          # 충분한 샘플
    min_win_rate=70.0,     # 높은 승률
    min_avg_profit=1.5,    # 높은 수익률
    min_profit_factor=2.0  # 높은 PF
)
```

**결과 예상:**
- 68개 → 1~3개 종목
- 승률 80%+
- 안정적 수익

### 시나리오 2: 공격적 선별 (기회 포착)

```python
# 완화된 기준
validator = PreTradeValidator(
    min_trades=1,          # 적은 샘플
    min_win_rate=40.0,     # 낮은 승률
    min_avg_profit=0.3,    # 낮은 수익률
    min_profit_factor=1.0  # 낮은 PF
)
```

**결과 예상:**
- 68개 → 15~30개 종목
- 승률 50%+
- 변동성 높음

### 시나리오 3: 균형 (추천)

```python
# 기본 설정 사용
validator = PreTradeValidator(
    min_trades=2,
    min_win_rate=50.0,
    min_avg_profit=0.5,
    min_profit_factor=1.2
)
```

**결과 예상:**
- 68개 → 5~10개 종목
- 승률 60~70%
- 안정성 + 수익성

---

## 🔧 고급 기능

### 1. 특정 업종만 필터링

```python
# 파이프라인에 업종 필터 추가
def filter_by_sector(stock_codes, target_sectors=['IT', 'BIO']):
    filtered = []
    for code in stock_codes:
        sector = kiwoom.get_sector(code)
        if sector in target_sectors:
            filtered.append(code)
    return filtered

# 사용
stock_codes = pipeline.search_conditions([0, 1, 2, 3, 4, 5])
it_bio_stocks = filter_by_sector(stock_codes, ['IT', 'BIO'])
validated = pipeline.second_filter_with_vwap(it_bio_stocks)
```

### 2. 시가총액 필터

```python
def filter_by_market_cap(stock_codes, min_cap=1000억):
    filtered = []
    for code in stock_codes:
        cap = kiwoom.get_market_cap(code)
        if cap >= min_cap:
            filtered.append(code)
    return filtered
```

### 3. 자동 실행 (스케줄러)

```python
import schedule

def daily_scan():
    """매일 장 마감 후 자동 스캔"""
    pipeline = ConditionFilterPipeline()
    # ... 실행 ...

# 매일 오후 3시 40분 실행
schedule.every().day.at("15:40").do(daily_scan)

while True:
    schedule.run_pending()
    time.sleep(60)
```

---

## 📈 성과 분석

### 실제 운영 예상 성과

**1개월 운영 시나리오:**
- 거래일: 20일
- 일평균 검색: 68개 종목
- 2차 검증 통과: 5개/일
- 실제 매수: 3개/일 (수동 선택)

**월간 예상:**
- 총 매수: 60회
- 승률: 65~70%
- 평균 수익률: +1.5~2.0%
- 월 수익: +10~15% (복리 계산)

---

## ⚠️ 주의사항

### 1. 데이터 소스 의존성

- 야후 파이낸스 데이터 사용
- 한국 종목은 `.KS` 접미사 필요
- 데이터 없는 종목은 자동 제외

### 2. API 호출 제한

```python
# 종목이 너무 많으면 시간 소요
# 50개 종목 × 2초 = 100초 (약 2분)

# 병렬 처리로 개선 가능
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=5) as executor:
    results = executor.map(validate_stock, stock_codes)
```

### 3. 시장 상황 고려

- 하락장에서는 검증 통과 종목 감소
- 상승장에서는 검증 통과 종목 증가
- 적응형 검증기 사용 권장

---

## 🎯 결론

### 핵심 장점

✅ **자동화**: 수십 개 종목을 자동으로 검증
✅ **객관성**: 데이터 기반 선정
✅ **효율성**: 시간 절약 (수작업 대비 90% 단축)
✅ **일관성**: 동일한 기준 적용

### 활용 전략

1. **매일 실행**: 장 마감 후 다음 날 종목 선정
2. **포트폴리오 구성**: 상위 5~10종목 분산 투자
3. **지속 모니터링**: 주간 성과 분석 및 기준 조정

### 다음 단계

- [ ] 실시간 모니터링 시스템 연동
- [ ] 자동 매수 시스템 구축
- [ ] 성과 트래킹 대시보드
- [ ] 알림 시스템 (텔레그램, 이메일)

---

**이 파이프라인은 종목 선정의 첫 단계입니다. 최종 투자 결정은 추가 분석 후 신중하게 하세요.**
