# ML 학습용 데이터셋 생성 파이프라인 가이드

## 📋 목차

1. [개요](#개요)
2. [파이프라인 구조](#파이프라인-구조)
3. [설치 및 설정](#설치-및-설정)
4. [사용 방법](#사용-방법)
5. [데이터 구조](#데이터-구조)
6. [Universe Tiering](#universe-tiering)
7. [Label 설계](#label-설계)
8. [문제 해결](#문제-해결)

---

## 개요

이 파이프라인은 **키움 REST API**를 사용하여 주식 데이터를 수집하고, ML 학습에 최적화된 형태로 가공합니다.

### 핵심 기능

- ✅ **Universe Management**: Core/Candidate/Exploratory 종목 자동 분류
- ✅ **자동 데이터 수집**: 키움 API 기반 분봉/일봉 데이터 수집
- ✅ **데이터 정제**: 결측치, 이상치, 중복 자동 처리
- ✅ **Label 생성**: n봉 후 수익률 기반 Classification/Regression Label
- ✅ **Feature Engineering**: 기술적 지표 자동 생성
- ✅ **버전 관리**: 데이터셋 해시 및 메타데이터 자동 저장

---

## 파이프라인 구조

```
┌─────────────────────────────────────────────────────────────────┐
│                        ML Dataset Pipeline                       │
└─────────────────────────────────────────────────────────────────┘

   [1] 키움 API        [2] Data         [3] Label      [4] Training
    데이터 수집  →     Cleaner    →    Generator  →   Dataset Builder
      (RAW)          (Processed)      (Labeled)       (Training)

     ↓                   ↓                ↓               ↓
  raw/*.csv      processed/*.parquet  labeled/*.parquet  training/
                                                          ├─ train.parquet
                                                          ├─ val.parquet
                                                          ├─ test.parquet
                                                          └─ metadata.json
```

### 단계별 설명

| 단계 | 모듈 | 입력 | 출력 | 설명 |
|------|------|------|------|------|
| 1 | `ml_data_collector.py` | 종목 리스트 | CSV (OHLCV) | 키움 API에서 분봉 데이터 수집 |
| 2 | `data_cleaner.py` | RAW CSV | Parquet | 결측치/이상치 제거, 정규화 |
| 3 | `label_generator.py` | Processed | Labeled Parquet | n봉 후 수익률 Label 생성 |
| 4 | `training_dataset_builder.py` | Labeled | Train/Val/Test | Feature 추가 및 데이터 분할 |

---

## 설치 및 설정

### 1. 필수 라이브러리 설치

```bash
cd kiwoom_trading
source venv/bin/activate
pip install tenacity aiohttp python-dotenv pandas numpy pyarrow
```

### 2. 키움 API 설정

`.env` 파일에 API 키 추가:

```env
KIWOOM_APP_KEY=your_app_key
KIWOOM_APP_SECRET=your_app_secret
```

### 3. 디렉토리 구조 확인

```bash
mkdir -p data/{raw,processed,labeled,training,universe}
mkdir -p logs
mkdir -p ai/models
```

---

## 사용 방법

### 방법 1: 전체 파이프라인 자동 실행 (추천)

```bash
python examples/build_ml_dataset_pipeline.py
```

이 스크립트는 다음을 자동으로 수행합니다:
1. 데이터 수집 (RAW)
2. 데이터 정제 (Processed)
3. Label 생성 (Labeled)
4. Training Dataset 생성

### 방법 2: 단계별 실행

#### Step 1: RAW 데이터 수집

```python
from core.ml_data_collector import MLDataCollector

stocks = [
    {"code": "005930", "name": "삼성전자"},
    {"code": "000660", "name": "SK하이닉스"},
]

async with MLDataCollector(app_key, app_secret, is_mock=True) as collector:
    collector.add_stocks_from_list(stocks, minute_interval=5, max_pages=50)
    await collector.collect_all()
```

#### Step 2: 데이터 정제

```python
from core.data_cleaner import DataCleaner

cleaner = DataCleaner()
cleaner.batch_clean(symbols=["005930", "000660"], interval="5min")
```

#### Step 3: Label 생성

```python
from core.label_generator import LabelGenerator

label_gen = LabelGenerator()
label_gen.batch_generate_labels(
    symbols=["005930", "000660"],
    interval="5min",
    horizons=[3, 5, 10],
    profit_threshold=2.0,
    loss_threshold=-2.0
)
```

#### Step 4: Training Dataset 생성

```python
from core.training_dataset_builder import TrainingDatasetBuilder

builder = TrainingDatasetBuilder()
metadata = builder.build_training_dataset(
    symbols=["005930", "000660"],
    interval="5min",
    model_name="my_model_v1",
    add_features=True
)
```

---

## 데이터 구조

### RAW 데이터 (CSV)

```csv
datetime,open,high,low,close,volume,change,change_sign
2025-11-01 09:05:00,72800,72900,72700,72850,125000,50,+
2025-11-01 09:10:00,72850,73000,72800,72950,98000,100,+
```

### Processed 데이터 (Parquet)

- 결측치 처리 완료
- 이상치 제거
- OHLC 일관성 검증
- 메타데이터 포함

### Labeled 데이터 (Parquet)

추가 컬럼:
- `return_3bars`, `return_5bars`, `return_10bars`: 수익률 (%)
- `label_5bars_ternary`: -1 (손절), 0 (보합), +1 (익절)
- `label_5bars_binary`: 0 (하락), 1 (상승)

### Training Dataset (Parquet)

- Train/Val/Test 분할
- Feature Engineering 적용 (RSI, MACD, Bollinger Bands 등)
- 메타데이터 JSON 파일 포함

---

## Universe Tiering

### Core Universe

**기준**:
- 평균 거래대금 (60일) ≥ 5억원
- 현재가 ≥ 1,000원
- 데이터 이력 ≥ 250일
- 실거래일수 (60일) ≥ 50일

**용도**: 메인 학습 데이터, 실전 트레이딩

### Candidate Universe

**기준**:
- 평균 거래대금 (60일) ≥ 1억원
- 현재가 ≥ 500원
- 데이터 이력 ≥ 100일

**용도**: 전략 검증, 백테스트

### Exploratory Universe

**기준**:
- 평균 거래대금 (60일) ≥ 1천만원
- 현재가 ≥ 100원
- 데이터 이력 ≥ 60일

**용도**: 소형주 연구, 스트레스 테스트

### Universe 구축 예시

```python
from core.universe_manager import UniverseManager

async with UniverseManager(app_key, app_secret, is_mock=True) as manager:
    await manager.build_universe(max_stocks=100)
    manager.save_universe()

    print(f"Core: {len(manager.core_universe)}개")
    print(f"Candidate: {len(manager.candidate_universe)}개")
    print(f"Exploratory: {len(manager.exploratory_universe)}개")
```

---

## Label 설계

### 1. Classification Labels

#### Binary (2-class)

```python
label = 1 if return > 0 else 0
```

#### Ternary (3-class)

```python
if return >= +2%: label = +1  # 익절
elif return <= -2%: label = -1  # 손절
else: label = 0  # 보합
```

#### Multi-class (5-class)

```python
if return >= +5%: label = +2  # 큰 이익
elif return >= +2%: label = +1  # 작은 이익
elif return <= -5%: label = -2  # 큰 손실
elif return <= -2%: label = -1  # 작은 손실
else: label = 0  # 보합
```

### 2. Regression Labels

```python
target = return (%)  # 그대로 사용
```

### 3. 예측 수평 (Horizon)

- **3봉**: 단기 (15분)
- **5봉**: 중단기 (25분)
- **10봉**: 중기 (50분)
- **15봉**: 장기 (75분)

---

## 문제 해결

### Q1: 데이터 수집이 느려요

**A**: 다음을 조정하세요:
- `max_concurrent_tasks` 감소 (2 → 1)
- `max_requests_per_second` 감소 (5 → 3)
- `max_pages` 감소 (50 → 30)

### Q2: Label 클래스 불균형

**A**: Label Generator 파라미터 조정:
```python
profit_threshold=1.5,  # 2.0 → 1.5 (더 쉬운 익절)
loss_threshold=-1.5    # -2.0 → -1.5 (더 쉬운 손절)
```

### Q3: Feature 생성 시 에러

**A**: `ai/feature_engineer.py` 확인:
- 최소 데이터 길이 부족 (20일 이상 필요)
- 결측치가 너무 많음

### Q4: 메모리 부족

**A**: 종목 수 또는 데이터 기간 감소:
```python
max_pages=20,  # 50 → 20
symbols=symbols[:10]  # 처음 10개만
```

---

## 데이터 버전 관리

### 메타데이터 예시

```json
{
  "version": "20251101_143022",
  "model_name": "lightgbm_v1",
  "symbols": ["005930", "000660"],
  "train": {
    "rows": 15000,
    "hash": "a1b2c3d4e5f6",
    "date_range": {
      "start": "2025-10-01T09:00:00",
      "end": "2025-10-28T15:30:00"
    }
  },
  "features": {
    "total": 87,
    "feature_columns": ["rsi_14", "macd", "bb_upper", ...]
  }
}
```

### 버전 추적

```python
# 데이터셋 해시로 정확한 버전 추적
dataset_hash = builder.dataset_hash(train_df)

# 모델 학습 시 메타데이터에 기록
model_metadata = {
    "dataset_version": "20251101_143022",
    "dataset_hash": dataset_hash,
    "trained_at": datetime.now().isoformat()
}
```

---

## 다음 단계

1. ✅ **데이터셋 생성 완료**
2. ⏭️ **모델 학습**: `ai/ml_model_trainer.py`
3. ⏭️ **모델 평가**: `ai/model_evaluator.py`
4. ⏭️ **실전 적용**: `main_auto_trading.py`와 통합

---

## 참고 자료

- [키움 REST API 문서](../docs/키움api/키움 REST API 문서.xlsx)
- [Feature Engineering 가이드](../ai/feature_engineer.py)
- [ML 모델 학습 가이드](../ai/ml_model_trainer.py)

---

## 라이선스

이 프로젝트는 MIT 라이선스를 따릅니다.
