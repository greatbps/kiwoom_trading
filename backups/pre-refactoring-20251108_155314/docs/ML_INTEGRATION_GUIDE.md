# ML Integration Guide - Candidate Ranker

## 📊 개요

Candidate Ranker는 **룰 기반 파이프라인을 보완**하는 ML 모델입니다.

### 역할
- 조건검색 + VWAP 통과 종목들을 **점수화**
- `buy_probability`와 `predicted_return` 산출
- 상위 K개만 실제 매매 대상으로 선정

### Pipeline 위치
```
조건검색 → VWAP 필터 → [Ranker 점수화] → 모니터링 → 매매
```

---

## 🎯 사용 방법

### 1. 모델 학습

메뉴에서 **[3] ML 모델 학습** 선택:

```bash
./run.sh
# 메뉴에서 3 입력
```

학습 과정:
1. 백테스트 결과 로드 (최근 60일)
2. Feature 추출 (VWAP 지표, 거래량, 모멘텀 등)
3. LightGBM Classifier + Regressor 학습
4. 모델 저장 (`./models/ranker/`)

### 2. 기존 파이프라인에 통합

#### main_auto_trading.py 수정 예시:

```python
from ml.candidate_ranker import CandidateRanker

# 조건검색 결과
candidates = await condition_search()

# VWAP 필터
vwap_passed = vwap_filter(candidates)

# ✨ Ranker 적용
ranker = CandidateRanker()
ranked = ranker.rank_candidates(
    vwap_passed,
    threshold=0.7,  # buy_probability >= 0.7
    top_k=10        # 상위 10개만
)

# 모니터링
for symbol in ranked.itertuples():
    print(f"{symbol.name}: "
          f"buy_prob={symbol.buy_probability:.2f}, "
          f"pred_return={symbol.predicted_return:.2f}%")

    # 실시간 모니터링 시작
    monitor_symbol(symbol.code)
```

---

## 📦 주요 클래스

### CandidateRanker

**초기화:**
```python
from ml.candidate_ranker import CandidateRanker

ranker = CandidateRanker(
    model_dir="./models/ranker",
    min_train_samples=100
)
```

**학습:**
```python
import pandas as pd

# 백테스트 결과 DataFrame
training_data = pd.DataFrame({
    'vwap_backtest_winrate': [...],
    'vwap_avg_profit': [...],
    'current_vwap_distance': [...],
    'volume_z_score': [...],
    'actual_profit_pct': [...]  # Label
})

metrics = ranker.train(training_data)
print(f"AUC: {metrics['classifier']['auc']:.3f}")
```

**예측 (랭킹):**
```python
# 조건검색 + VWAP 통과 종목
candidates = pd.DataFrame({
    'code': ['005930', '000660'],
    'name': ['삼성전자', 'SK하이닉스'],
    'vwap_backtest_winrate': [0.65, 0.72],
    'vwap_avg_profit': [2.3, 3.1],
    # ... 기타 features
})

ranked = ranker.rank_candidates(
    candidates,
    threshold=0.6,  # 60% 이상만
    top_k=5
)

print(ranked[['name', 'buy_probability', 'predicted_return']])
```

---

## 🔍 Features 설명

| Feature | 설명 | 출처 |
|---------|------|------|
| `vwap_backtest_winrate` | VWAP 백테스트 승률 | 백테스트 결과 |
| `vwap_avg_profit` | VWAP 평균 수익률 (%) | 백테스트 결과 |
| `current_vwap_distance` | 현재가-VWAP 괴리율 (%) | 실시간 계산 |
| `volume_z_score` | 거래량 Z-score (20일 평균 대비) | 실시간 계산 |
| `recent_return_5d` | 최근 5일 수익률 (%) | 가격 데이터 |
| `market_volatility` | 시장 변동성 (KOSPI ATR) | 시장 데이터 |
| `sector_strength` | 업종 강도 | 업종 데이터 |
| `price_momentum` | 가격 모멘텀 | 가격 데이터 |

---

## 📈 성능 지표

### Classifier (buy_probability)
- **AUC**: 0.75+ (좋음), 0.65~0.75 (보통), <0.65 (재학습 필요)
- **Accuracy**: 실제 정확도

### Regressor (predicted_return)
- **RMSE**: 예측 수익률 오차
- **MAE**: 평균 절대 오차

---

## 🔧 설정 튜닝

### 임계값 조정

```python
# 보수적 (정밀도 우선)
ranked = ranker.rank_candidates(candidates, threshold=0.8, top_k=5)

# 공격적 (재현율 우선)
ranked = ranker.rank_candidates(candidates, threshold=0.5, top_k=20)
```

### 재학습 주기

- **주간 재학습 권장** (백테스트 데이터 누적)
- Cron 작업 예시:
```bash
# 매주 일요일 오전 2시
0 2 * * 0 cd /path/to/project && python ml_train_menu.py
```

---

## 🚀 다음 단계 (우선순위)

1. ✅ **Candidate Ranker** (완료)
2. **Position Sizer** - 종목별 최적 포지션 크기 예측
3. **Realtime Confirm** - 실시간 진입 시점 확정
4. **Exit Predictor** - 동적 손절/익절 제안
5. **Meta Strategy** - 전략 가중치 자동 조정

---

## 📝 백테스트 결과 포맷

`TrainingDataBuilder`가 요구하는 백테스트 결과 JSON 구조:

```json
{
  "date": "2025-11-01",
  "trades": [
    {
      "symbol": "005930",
      "entry_date": "2025-11-01 09:30:00",
      "exit_date": "2025-11-01 15:20:00",
      "profit_pct": 2.3,
      "entry_features": {
        "vwap_backtest_winrate": 0.68,
        "vwap_avg_profit": 2.1,
        "current_vwap_distance": -0.5,
        "volume_z_score": 1.8,
        "recent_return_5d": -1.2,
        "market_volatility": 15.3,
        "sector_strength": 0.8,
        "price_momentum": 1.2
      }
    }
  ]
}
```

백테스트 시 `entry_features`를 반드시 기록하도록 수정 필요.

---

## ⚠️ 주의사항

1. **Fail-safe**: 모델 로드 실패 시 룰 기반으로 폴백
2. **Explainability**: Feature Importance로 의사결정 추적
3. **Latency**: 실시간 예측은 < 100ms 목표 (LightGBM 충분히 빠름)
4. **Data Quality**: 이상치 감지 및 필터링 필수

---

## 📚 참고

- `ml/candidate_ranker.py` - Ranker 구현
- `ml/training_data_builder.py` - 학습 데이터 생성
- `ml_train_menu.py` - 학습 메뉴
- `examples/integrate_ranker.py` - 통합 예시 (작성 예정)
