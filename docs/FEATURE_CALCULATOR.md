# Feature Calculator 가이드

## 📊 개요

`FeatureCalculator`는 백테스트 및 실시간 거래를 위한 종목별 Feature를 계산하는 클래스입니다.

## 🎯 계산하는 Features (8개)

| Feature | 설명 | 계산 방법 |
|---------|------|-----------|
| `vwap_backtest_winrate` | VWAP 백테스트 승률 | PreTradeValidator 결과 사용 |
| `vwap_avg_profit` | VWAP 평균 수익률 (%) | PreTradeValidator 결과 사용 |
| `current_vwap_distance` | 현재가-VWAP 괴리율 (%) | (현재가 - VWAP) / VWAP × 100 |
| `volume_z_score` | 거래량 Z-score | (현재거래량 - 20일평균) / 20일표준편차 |
| `recent_return_5d` | 최근 5일 수익률 (%) | (현재가 - 5일전가) / 5일전가 × 100 |
| `market_volatility` | 시장 변동성 (%) | KOSPI ATR (14일) |
| `sector_strength` | 업종 강도 | 업종 지수 수익률 (TODO) |
| `price_momentum` | 가격 모멘텀 (%) | (현재가 - 20일이동평균) / 20일이동평균 × 100 |

## 📝 사용 방법

### 1. 기본 사용

```python
from utils.feature_calculator import FeatureCalculator
from core.kiwoom_rest_client import KiwoomRESTClient

# API 클라이언트 초기화
async with KiwoomRESTClient(app_key, app_secret) as api_client:
    # FeatureCalculator 생성
    calculator = FeatureCalculator(api_client)

    # 종목별 Feature 계산
    features = await calculator.calculate_all_features(
        stock_code="005930",  # 삼성전자
        vwap_stats={'win_rate': 65.0, 'avg_profit_pct': 2.3}
    )

    print(features)
    # {
    #   'vwap_backtest_winrate': 0.65,
    #   'vwap_avg_profit': 2.3,
    #   'current_vwap_distance': -0.5,
    #   'volume_z_score': 1.8,
    #   'recent_return_5d': -1.2,
    #   'market_volatility': 15.3,
    #   'sector_strength': 0.8,
    #   'price_momentum': 1.2
    # }
```

### 2. 백테스트 통합과 함께 사용

```python
from utils.backtest_integration import convert_vwap_results_to_backtest_input

# VWAP 검증 통과 종목
validated_stocks = pipeline.validated_stocks

# Feature Calculator로 실제 데이터 계산
calculator = FeatureCalculator(api_client)

candidates = await convert_vwap_results_to_backtest_input(
    validated_stocks,
    feature_calculator=calculator  # ✨ 실제 Feature 계산
)

# 백테스트 실행
runner = BacktestRunner()
results = await runner.run_backtest(candidates, ...)
```

### 3. 현재가 및 VWAP 직접 제공

```python
# 이미 현재가와 VWAP를 알고 있는 경우
features = await calculator.calculate_all_features(
    stock_code="005930",
    vwap_stats={'win_rate': 65.0, 'avg_profit_pct': 2.3},
    current_price=72000,  # 현재가 직접 제공
    current_vwap=71500    # VWAP 직접 제공
)
```

## 🔧 주요 메서드

### `calculate_all_features()`

모든 Feature를 한번에 계산합니다.

**Parameters:**
- `stock_code` (str): 종목코드
- `vwap_stats` (Dict, optional): VWAP 백테스트 통계
- `current_price` (float, optional): 현재가 (None이면 API 조회)
- `current_vwap` (float, optional): 현재 VWAP (None이면 계산)

**Returns:**
- `Dict[str, float]`: 8개 Feature 딕셔너리

### 내부 메서드 (직접 호출 가능)

```python
# 현재가 조회
price = await calculator._get_current_price("005930")

# 최근 차트 데이터 조회 (30일)
chart_data = await calculator._get_recent_chart_data("005930", days=30)

# VWAP 계산
vwap = calculator._calculate_vwap_from_chart(chart_data)

# 거래량 Z-score 계산
z_score = calculator._calculate_volume_z_score(chart_data)

# 최근 수익률 계산
return_5d = calculator._calculate_recent_return(chart_data, days=5)

# 가격 모멘텀 계산
momentum = calculator._calculate_price_momentum(chart_data)

# ATR 계산
atr = calculator._calculate_atr(chart_data, period=14)
```

## ⚙️ 설정 및 캐싱

### 시장 변동성 캐싱

```python
calculator = FeatureCalculator(api_client)

# 시장 변동성은 1시간 캐시됨
calculator.cache_ttl = 3600  # 초 단위 (기본값: 1시간)
```

캐시를 사용하여 API 호출을 최소화합니다.

## ⚠️ 주의사항

### 1. 키움 API 응답 필드명

현재 코드는 추정된 필드명을 사용합니다:

```python
# 일봉 데이터 필드명 (확인 필요)
close = data.get('stk_close_prc', data.get('close', 0))
high = data.get('stk_high_prc', data.get('high', 0))
low = data.get('stk_low_prc', data.get('low', 0))
volume = data.get('volume', data.get('stk_trd_qty', 0))
```

**TODO:** 실제 키움 API 응답으로 필드명 확인 및 업데이트 필요

### 2. 에러 처리

Feature 계산 실패 시 자동으로 기본값 반환:

```python
{
    'vwap_backtest_winrate': 0.5,
    'vwap_avg_profit': 0.0,
    'current_vwap_distance': 0.0,
    'volume_z_score': 0.0,
    'recent_return_5d': 0.0,
    'market_volatility': 15.0,
    'sector_strength': 0.5,
    'price_momentum': 0.0,
}
```

### 3. API Rate Limit

종목별로 차트 데이터를 조회하므로 Rate Limit 고려 필요:

```python
# 여러 종목 처리 시 배치 처리 권장
for stock in stocks:
    features = await calculator.calculate_all_features(stock['code'])
    await asyncio.sleep(0.2)  # Rate Limit 방지
```

## 🚀 향후 개선 사항

### 1. KOSPI 변동성 실제 계산

**현재:**
```python
async def _calculate_market_volatility(self) -> float:
    return 15.0  # 기본값
```

**목표:**
```python
async def _calculate_market_volatility(self) -> float:
    # KOSPI 지수 데이터 조회
    kospi_data = await self._get_recent_chart_data("0001", days=20)
    # ATR 계산
    volatility = self._calculate_atr(kospi_data, period=14)
    return volatility
```

### 2. 업종 강도 계산

**현재:**
```python
async def _calculate_sector_strength(self, stock_code: str) -> float:
    return 0.5  # 기본값
```

**목표:**
```python
async def _calculate_sector_strength(self, stock_code: str) -> float:
    # 종목의 업종 확인
    sector = await self._get_sector(stock_code)
    # 업종 지수 조회
    sector_data = await self._get_sector_index(sector)
    # 수익률 계산
    return self._calculate_recent_return(sector_data, days=5)
```

### 3. Feature 캐싱 확장

```python
# 종목별 Feature 캐시 (TTL: 5분)
self.feature_cache = {}
self.feature_cache_ttl = 300
```

### 4. 배치 처리 최적화

```python
async def calculate_features_batch(
    self,
    stock_codes: List[str],
    batch_size: int = 10
) -> Dict[str, Dict[str, float]]:
    """여러 종목 Feature 동시 계산 (Rate Limit 고려)"""
    # 구현 예정
```

## 📚 관련 파일

- `utils/feature_calculator.py` - FeatureCalculator 구현
- `utils/backtest_integration.py` - 백테스트 통합
- `core/kiwoom_rest_client.py` - API 클라이언트
- `backtest_with_ranker.py` - 백테스트 실행기

## 🧪 테스트

```bash
# FeatureCalculator 단독 테스트
python utils/feature_calculator.py

# 백테스트 통합 테스트
python run_condition_and_backtest.py
# → Feature를 실제 API 데이터로 계산? (y/n): y
```

---

**마지막 업데이트:** 2025-11-02
