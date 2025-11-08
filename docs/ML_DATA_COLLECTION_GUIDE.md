# ML 학습용 데이터 수집 가이드

## 개요

이 문서는 키움 REST API를 사용하여 ML 학습용 주식 데이터를 수집하는 방법을 설명합니다.

## 주요 기능

### 1. 키움 REST API 클라이언트 (`core/kiwoom_rest_client.py`)

- **OAuth 인증 관리**: 자동 토큰 발급 및 갱신
- **분봉차트 데이터 조회**: 1분~60분 분봉 데이터 수집
- **Rate Limit 처리**: 초당 요청 수 제한 및 자동 대기
- **에러 핸들링**: 재시도 로직 및 상세 에러 처리
- **연속조회**: 대량 데이터 자동 수집

#### 주요 API 에러 코드

| 코드 | 설명 | 대응 방법 |
|------|------|----------|
| 1687 | 재귀 호출 제한 | 요청 간격 조정 |
| 1700 | 요청 개수 초과 (Rate Limit) | 30초 대기 후 재시도 |
| 8005 | 토큰 만료 | 자동 토큰 갱신 |
| 1902 | 종목 정보 없음 | 종목코드 확인 |

### 2. ML 데이터 수집기 (`core/ml_data_collector.py`)

- **다중 종목 병렬 수집**: 여러 종목을 동시에 효율적으로 수집
- **데이터 정규화**: 수집한 데이터를 ML 학습에 적합한 형태로 변환
- **진행상황 모니터링**: 실시간 수집 현황 추적
- **메타데이터 저장**: 데이터 출처 및 수집 정보 기록

## 설치 및 설정

### 1. 필수 라이브러리 설치

```bash
cd kiwoom_trading
source venv/bin/activate
pip install tenacity aiohttp python-dotenv pandas numpy openpyxl
```

### 2. 키움 API 키 발급

1. [키움 OpenAPI](https://openapi.kiwoom.com) 접속
2. 회원가입 및 로그인
3. API 신청
4. APP KEY 및 SECRET KEY 발급

### 3. 환경변수 설정

`.env` 파일에 다음 내용 추가:

```env
# 키움 API
KIWOOM_APP_KEY=your_app_key_here
KIWOOM_APP_SECRET=your_app_secret_here
```

## 사용 방법

### 기본 사용 예제

```python
import asyncio
from core.ml_data_collector import MLDataCollector

async def main():
    # 수집 대상 종목
    stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
    ]

    async with MLDataCollector(
        app_key="your_app_key",
        app_secret="your_app_secret",
        is_mock=True,  # 모의투자 모드
        data_dir="./data/ml_training",
        max_concurrent_tasks=2
    ) as collector:
        # 종목 추가
        collector.add_stocks_from_list(
            stock_list=stocks,
            minute_interval=5,  # 5분봉
            max_pages=50
        )

        # 데이터 수집
        stats = await collector.collect_all()

        print(f"수집 완료: {stats['total_data_points']:,}건")

asyncio.run(main())
```

### 예제 스크립트 실행

```bash
cd kiwoom_trading
source venv/bin/activate
python examples/collect_ml_data.py
```

## 데이터 구조

### 디렉토리 구조

```
data/ml_training/
├── raw/                    # 원본 데이터 (CSV)
│   ├── 005930_5min.csv
│   ├── 000660_5min.csv
│   └── ...
├── processed/              # 전처리된 데이터
│   └── ...
└── metadata/               # 메타데이터 (JSON)
    ├── 005930.json
    ├── 000660.json
    └── ...
```

### CSV 데이터 형식

| 컬럼명 | 설명 | 타입 |
|--------|------|------|
| datetime | 체결시간 | datetime |
| open | 시가 | float |
| high | 고가 | float |
| low | 저가 | float |
| close | 종가 | float |
| volume | 거래량 | int |
| change | 전일대비 | int |
| change_sign | 전일대비 기호 | str |

### 메타데이터 형식

```json
{
  "stock_code": "005930",
  "stock_name": "삼성전자",
  "minute_interval": 5,
  "data_count": 1500,
  "date_range": {
    "start": "2025-01-01T09:00:00",
    "end": "2025-01-15T15:30:00"
  },
  "collected_at": "2025-01-15T16:00:00",
  "columns": ["datetime", "open", "high", "low", "close", "volume"]
}
```

## API 호출 제한 및 최적화

### Rate Limit

- **기본 설정**: 초당 3회 (보수적)
- **조정 가능**: `max_requests_per_second` 파라미터
- **자동 대기**: Rate Limit 초과 시 30초 대기 후 재시도

### 최적화 팁

1. **동시 작업 수 조정**
   ```python
   collector = MLDataCollector(
       max_concurrent_tasks=2  # 동시 2개 종목
   )
   ```

2. **페이지 제한 설정**
   ```python
   collector.add_stock(
       stock_code="005930",
       max_pages=50  # 최대 50페이지
   )
   ```

3. **시간대 분산**
   - 장 마감 후 데이터 수집 권장
   - 대량 수집 시 야간 시간 활용

## 에러 처리

### 자동 재시도

- **네트워크 에러**: 최대 3회 재시도 (지수 백오프)
- **Rate Limit**: 30초 대기 후 1회 재시도
- **토큰 만료**: 자동 갱신 후 재시도

### 수동 에러 처리

```python
try:
    stats = await collector.collect_all()
except KiwoomAPIError as e:
    print(f"API 에러: {e.error_code} - {e.message}")
except Exception as e:
    print(f"예상치 못한 에러: {e}")
```

## 수집된 데이터 활용

### 데이터 로드

```python
# 수집된 데이터 로드
df = collector.load_collected_data("005930", minute_interval=5)

# 기본 통계
print(df.describe())

# 시계열 플롯
import matplotlib.pyplot as plt
df.plot(x='datetime', y='close')
plt.show()
```

### ML 학습 데이터 준비

```python
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

# 데이터 로드
df = pd.read_csv('data/ml_training/raw/005930_5min.csv')
df['datetime'] = pd.to_datetime(df['datetime'])

# 기술적 지표 추가 (예: 이동평균)
df['ma5'] = df['close'].rolling(window=5).mean()
df['ma20'] = df['close'].rolling(window=20).mean()

# 정규화
scaler = MinMaxScaler()
scaled_data = scaler.fit_transform(df[['open', 'high', 'low', 'close', 'volume']])

# 학습/검증 분할
train_size = int(len(df) * 0.8)
train_data = scaled_data[:train_size]
test_data = scaled_data[train_size:]
```

## 문제 해결

### 토큰 발급 실패

**증상**: `8001` 에러 (App Key/Secret Key 검증 실패)

**해결방법**:
1. `.env` 파일의 키 확인
2. 키움 OpenAPI 사이트에서 키 재발급
3. 실전/모의투자 모드 일치 확인

### 데이터가 수집되지 않음

**증상**: `1902` 에러 (종목 정보 없음)

**해결방법**:
1. 종목코드 형식 확인 (6자리 숫자)
2. 거래소 코드 확인 (KRX, NXT, SOR)
3. 상장폐지 종목 제외

### Rate Limit 초과

**증상**: `1700` 에러 (요청 개수 초과)

**해결방법**:
1. `max_requests_per_second` 값 감소
2. `max_concurrent_tasks` 값 감소
3. 수집 종목 수 감소 또는 분할 수집

## 참고 자료

- [키움 REST API 문서](docs/키움api/키움 REST API 문서.xlsx)
- [키움 OpenAPI 사이트](https://openapi.kiwoom.com)
- [API 에러 코드 전체 목록](docs/키움api/키움 REST API 문서.xlsx - 오류코드 시트)

## 주의사항

1. **API 사용량 관리**: 과도한 요청은 계정 제한을 유발할 수 있습니다
2. **데이터 저장 공간**: 대량 수집 시 충분한 디스크 공간 확보
3. **개인정보 보호**: API 키는 절대 공개 저장소에 업로드하지 마세요
4. **모의투자 모드**: 테스트는 항상 모의투자 모드(`is_mock=True`)에서 진행

## 라이선스

이 코드는 프로젝트 라이선스를 따릅니다.
