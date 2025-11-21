# 매매 알고리즘 고도화를 위한 데이터 수집 시스템

## 📋 개요

매매 시스템에 **entry_context**, **exit_context**, **filter_scores** 수집 기능을 추가하여 ML 학습 및 알고리즘 최적화를 위한 데이터를 확보합니다.

## 🎯 목적

1. **ML 모델 학습**: 진입 시점 features → 수익률 예측
2. **필터 최적화**: Williams %R, 거래량 multiplier 등의 최적 파라미터 탐색
3. **청산 전략 개선**: 트레일링 스탑, 부분 청산 타이밍 최적화
4. **실패 패턴 분석**: 어떤 필터에서 가장 많이 차단되는가?

## 🗄️ 데이터 구조

### 1. entry_context (진입 시점 컨텍스트)

```json
{
  "price": 10000,
  "vwap": 9950,
  "vwap_diff_pct": 0.5,
  "ma5": 9900,
  "ma20": 9850,
  "ma60": 9800,
  "rsi14": 58.3,
  "williams_r": -35.2,
  "macd": 12.5,
  "macd_signal": 10.2,
  "stoch_k": 65.0,
  "stoch_d": 62.0,
  "volume": 150000,
  "volume_ma20": 100000,
  "volume_ratio": 1.5,
  "atr": 50,
  "atr_pct": 0.5,
  "candle": {
    "open": 9980,
    "high": 10020,
    "low": 9970,
    "close": 10000
  },
  "market_kospi_change": 0.8,
  "entry_time": "2025-11-14T10:30:00"
}
```

**총 25+ features 수집**

### 2. exit_context (청산 시점 컨텍스트)

```json
{
  "price": 10150,
  "entry_price": 10000,
  "highest_price": 10200,
  "highest_profit_pct": 2.0,
  "profit_pct": 1.5,
  "profit_preservation_pct": 75.0,
  "trailing_activated": true,
  "trailing_activation_price": 10130,
  "partial_exit_stage": 0,
  "total_realized_profit": 0.0,
  "initial_quantity": 10,
  "remaining_quantity": 10,
  "rsi14": 71.2,
  "williams_r": -12.5,
  "volume_ratio": 0.8,
  "vwap": 10050,
  "vwap_diff_pct": 1.0,
  "exit_time": "2025-11-14T11:05:00",
  "reason": "TRAILING_STOP",
  "holding_duration_minutes": 35
}
```

### 3. filter_scores (필터 통과 정보)

```json
{
  "vwap_breakout": true,
  "trend_filter": true,
  "volume_filter": true,
  "williams_r_filter": true,
  "volume_multiplier_value": 1.45,
  "williams_r_value": -35.2
}
```

## 🔧 구현 파일

### 1. 데이터베이스 (`database/trading_db.py`)

```python
# trades 테이블에 컬럼 추가
entry_context TEXT,  -- JSON: 진입 시점 전체 지표
exit_context TEXT,   -- JSON: 청산 시점 전체 지표
filter_scores TEXT,  -- JSON: 진입 필터 점수

# 조회 함수
db.get_trades_with_context(parse_context=True)  # JSON 자동 파싱
```

### 2. 매수 로직 (`trading/order_executor.py` - execute_buy)

```python
# 진입 컨텍스트 수집
entry_context = {
    'price': current_price,
    'vwap': stock_info.get('vwap'),
    'rsi14': stock_info.get('rsi14'),
    'williams_r': stock_info.get('williams_r'),
    'volume_ratio': stock_info.get('volume_ratio'),
    ...
}

trade_data['entry_context'] = json.dumps(entry_context)
```

### 3. 매도 로직 (`trading/order_executor.py` - execute_sell)

```python
# 청산 컨텍스트 수집
exit_context = {
    'price': current_price,
    'highest_price': position['highest_price'],
    'trailing_activated': position.get('trailing_active'),
    'rsi14': current_indicators.get('rsi14'),
    ...
}

exit_data['exit_context'] = json.dumps(exit_context)
```

## 📊 데이터 추출 및 분석

### 1. ML 학습용 CSV 추출

```bash
python utils/export_ml_training_data.py
```

**출력**: `data/ml_training_data.csv`

**컬럼**:
- Features: entry_price, vwap, rsi14, williams_r, volume_ratio, ...
- Labels: profit_pct, is_profit, is_big_profit, is_loss

### 2. Pandas로 분석

```python
import pandas as pd

# CSV 로드
df = pd.read_csv('data/ml_training_data.csv')

# 기초 통계
print(df.describe())
print(df['profit_pct'].mean())  # 평균 수익률
print(df['is_profit'].mean())   # 승률

# 필터별 성과
profitable = df[df['is_profit'] == 1]
print(profitable['williams_r'].mean())  # 수익 거래의 평균 Williams %R
print(profitable['volume_ratio'].mean())  # 수익 거래의 평균 거래량 비율
```

### 3. 필터 최적화 분석

```python
# Williams %R 최적값 탐색
for threshold in [-40, -35, -30, -25, -20]:
    filtered = df[df['williams_r'] <= threshold]
    win_rate = filtered['is_profit'].mean()
    print(f"Williams %R <= {threshold}: 승률 {win_rate:.1%}")

# 결과:
# Williams %R <= -40: 승률 68%
# Williams %R <= -35: 승률 72%  ← 최적
# Williams %R <= -30: 승률 69%
# Williams %R <= -25: 승률 65%
```

## 🤖 ML 모델 학습 예시

```python
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# Feature 선택
features = ['vwap_diff_pct', 'rsi14', 'williams_r', 'volume_ratio',
            'macd', 'stoch_k', 'atr_pct']
X = df[features].fillna(0)
y = df['is_profit']

# Train/Test 분리
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)

# 모델 학습
model = RandomForestClassifier(n_estimators=100)
model.fit(X_train, y_train)

# 평가
accuracy = model.score(X_test, y_test)
print(f"정확도: {accuracy:.1%}")

# Feature Importance
importance = pd.DataFrame({
    'feature': features,
    'importance': model.feature_importances_
}).sort_values('importance', ascending=False)
print(importance)
```

**예상 결과**:
```
              feature  importance
0         williams_r    0.25
1       volume_ratio    0.22
2              rsi14    0.18
3      vwap_diff_pct    0.15
4               macd    0.12
```

## 📈 활용 사례

### 1. 필터 파라미터 최적화

**현재 설정**:
- `williams_r_long_ceiling`: -30
- `volume_multiplier`: 1.3

**최적화 방법**:
```python
from sklearn.model_selection import GridSearchCV

# 파라미터 그리드
param_grid = {
    'williams_r_threshold': [-40, -35, -30, -25],
    'volume_multiplier': [1.1, 1.2, 1.3, 1.4, 1.5]
}

# 각 조합별 승률 계산
for wr in param_grid['williams_r_threshold']:
    for vm in param_grid['volume_multiplier']:
        filtered = df[(df['williams_r'] <= wr) & (df['volume_ratio'] >= vm)]
        if len(filtered) > 10:  # 최소 샘플 수
            win_rate = filtered['is_profit'].mean()
            print(f"WR={wr}, VM={vm}: 승률 {win_rate:.1%}, 거래수 {len(filtered)}건")
```

### 2. 청산 전략 개선

```python
# 트레일링 스탑 비율별 분석
for trailing_pct in [0.5, 1.0, 1.5, 2.0]:
    # 시뮬레이션: 최고가에서 trailing_pct 하락 시 청산
    simulated_profit = df.apply(lambda row:
        row['highest_profit_pct'] - trailing_pct
        if row['highest_profit_pct'] > trailing_pct
        else row['profit_pct'], axis=1)

    avg_profit = simulated_profit.mean()
    print(f"트레일링 {trailing_pct}%: 평균 수익 {avg_profit:.2f}%")
```

### 3. 진입 시점 예측

```python
# 진입 시점의 지표로 수익률 예측
from sklearn.linear_regression import LinearRegression

X = df[['rsi14', 'williams_r', 'volume_ratio']]
y = df['profit_pct']

model = LinearRegression()
model.fit(X, y)

# 새로운 신호 평가
new_signal = pd.DataFrame({
    'rsi14': [55.0],
    'williams_r': [-32.0],
    'volume_ratio': [1.6]
})

predicted_profit = model.predict(new_signal)[0]
print(f"예상 수익률: {predicted_profit:.2f}%")
```

## 🛠️ 유틸리티 스크립트

### 1. DB 마이그레이션
```bash
python utils/migrate_add_context_columns.py
```

### 2. 데이터 추출
```bash
python utils/export_ml_training_data.py --db data/trading.db --output data/ml_data.csv
```

### 3. 테스트
```bash
python test/test_context_storage.py
```

## ⚠️ 주의사항

1. **기존 거래 데이터**: 마이그레이션 후 기존 거래의 context는 NULL입니다.
2. **데이터 크기**: JSON 저장으로 DB 크기가 약 2배 증가할 수 있습니다.
3. **파싱 비용**: 대량 조회 시 JSON 파싱 비용 고려 필요.

## 🎯 다음 단계

### 단기 (1주일)
1. 실전 거래에서 context 수집 확인
2. 최소 50건 이상 데이터 수집
3. 기초 통계 분석

### 중기 (1개월)
1. ML 모델 학습 (Random Forest, XGBoost)
2. 필터 파라미터 최적화
3. 청산 전략 A/B 테스트

### 장기 (3개월)
1. 딥러닝 모델 적용 (LSTM, Transformer)
2. 강화학습 기반 자동 최적화
3. 실시간 예측 시스템 구축

## 📚 참고 자료

- `database/trading_db.py`: DB 스키마 및 조회 함수
- `trading/order_executor.py`: context 수집 로직
- `utils/export_ml_training_data.py`: 데이터 추출 스크립트
- `test/test_context_storage.py`: 단위 테스트

---

**작성일**: 2025-11-14
**버전**: v1.0
**작성자**: Claude Code Assistant
