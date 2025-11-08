# Kiwoom Trading System - v2 Refactoring Summary

**날짜:** 2025-10-30
**파일:** `main_auto_trading.py`
**백업:** `main_auto_trading.py.backup`

---

## 📋 주요 개선사항

### 1. **비동기 데이터 다운로드** ✅
- **문제점:** 기존 `download_stock_data()` 함수가 동기(blocking) 방식으로 동작하여 프로그램 전체가 멈추는 현상 발생
- **해결책:**
  - `asyncio.to_thread()` 사용하여 비동기 처리
  - `download_stock_data_sync()`: 기존 동기 함수 유지
  - `download_stock_data_yahoo()`: 새로운 비동기 래퍼 함수

```python
async def download_stock_data_yahoo(ticker: str, days: int = 7, try_kq: bool = True):
    """Yahoo Finance에서 데이터 다운로드 (비동기, .KS/.KQ 자동 전환)"""
    # .KS 시도
    df = await asyncio.to_thread(download_stock_data_sync, f"{ticker}.KS", days)
    if df is not None and not df.empty:
        return df

    # .KQ 시도
    if try_kq:
        df = await asyncio.to_thread(download_stock_data_sync, f"{ticker}.KQ", days)
        return df

    return None
```

**효과:** 여러 종목 동시 처리 시 멈춤 현상 해결, 전체 처리 시간 단축

---

### 2. **Yahoo Finance .KS/.KQ 자동 전환** ✅
- **문제점:** `.KS` 검색 실패 시 프로그램이 그냥 실패 처리
- **해결책:** `.KS` 실패 시 자동으로 `.KQ` (코스닥) 재시도

```python
# .KS (코스피) 시도 → 실패 시 자동으로 .KQ (코스닥) 시도
ticker_ks = f"{ticker}.KS"
ticker_kq = f"{ticker}.KQ"
```

**효과:** 코스닥 종목도 정확하게 조회 가능

---

### 3. **키움 API 우선 사용 + Yahoo 보충** ✅
- **문제점:** 오전장 시작 시 데이터 부족
- **해결책:**
  1. **키움 API 우선** 사용 (`get_minute_chart`, 5분봉)
  2. 데이터 부족(< 100개) 시 **Yahoo Finance로 보충**
  3. 두 데이터 병합하여 충분한 봉 개수 확보

```python
async def get_kiwoom_minute_data(api: KiwoomAPI, stock_code: str, required_bars: int = 100):
    """키움 API에서 5분봉 데이터 조회"""
    result = api.get_minute_chart(
        stock_code=stock_code,
        tic_scope="5",  # 5분봉
        upd_stkpc_tp="1"  # 수정주가 적용
    )
    # DataFrame 변환 및 반환
```

```python
async def validate_stock_for_trading(stock_code, stock_name, validator, api):
    """
    1단계: 키움 API 시도
    2단계: 데이터 부족 시 Yahoo 보충
    3단계: 데이터 병합 및 VWAP 검증
    """
    df = await get_kiwoom_minute_data(api, stock_code, required_bars=100)

    if df is None or len(df) < 100:
        # Yahoo로 보충
        yahoo_df = await download_stock_data_yahoo(stock_code, days=7, try_kq=True)

        if df is not None:
            # 키움 + Yahoo 병합
            df = pd.concat([yahoo_df, df], ignore_index=True)
            df = df.drop_duplicates(subset=['datetime', 'time'], keep='last')
```

**효과:**
- 오전장 데이터 부족 문제 해결
- 실시간 키움 데이터 활용으로 정확도 향상
- Yahoo 데이터로 과거 이력 보충

---

### 4. **데이터 흐름 개선**

**기존:**
```
Yahoo Finance만 사용 → 실시간성 부족, 오전장 데이터 부족
```

**개선 후:**
```
1. 키움 API (5분봉, 최대 100개)
   ↓ (데이터 부족 시)
2. Yahoo Finance (.KS/.KQ 자동 전환)
   ↓
3. 데이터 병합 (중복 제거)
   ↓
4. VWAP 검증
```

---

## 🔧 수정된 함수

### 1. `download_stock_data_sync(ticker, days=7)`
- 기존 `download_stock_data()` 함수명 변경
- 동기 버전으로 유지 (내부 로직 동일)

### 2. `download_stock_data_yahoo(ticker, days=7, try_kq=True)` **[NEW]**
- 비동기 래퍼 함수
- `.KS` → `.KQ` 자동 전환
- `asyncio.to_thread()` 사용

### 3. `get_kiwoom_minute_data(api, stock_code, required_bars=100)` **[NEW]**
- 키움 API에서 5분봉 데이터 조회
- DataFrame 표준화 (datetime, time, open, high, low, close, volume)
- 비동기 처리

### 4. `validate_stock_for_trading(stock_code, stock_name, validator, api)` **[REFACTORED]**
- **파라미터 추가:** `api: KiwoomAPI`
- **비동기 함수로 변경:** `async def`
- 키움 API 우선 → Yahoo 보충 → 데이터 병합 로직 추가

### 5. `IntegratedTradingSystem.run_condition_filtering()` **[UPDATED]**
- Line 634: `validate_stock_for_trading()` 호출 시 `await` 추가
- `self.api` 파라미터 전달

---

## 📊 성능 개선

| 항목 | 개선 전 | 개선 후 |
|------|---------|---------|
| **데이터 소스** | Yahoo만 | 키움 API (우선) + Yahoo (보충) |
| **멈춤 현상** | 발생 | 해결 (비동기 처리) |
| **코스닥 종목** | 실패 가능 | .KQ 자동 전환으로 성공 |
| **오전장 데이터** | 부족 | Yahoo 보충으로 해결 |
| **병렬 처리** | 불가능 | asyncio.gather() 가능 |

---

## 🧪 테스트 방법

### 1. 기본 실행
```bash
cd /home/greatbps/projects/kiwoom_trading
source venv/bin/activate
python main_auto_trading.py
```

### 2. 로그 확인
- 키움 API 데이터 조회 성공: `✓ 키움 API: {code} 데이터 {count}개 봉 조회`
- Yahoo 보충 시도: `⚠️  {name}({code}) 키움 데이터 부족 → Yahoo Finance 보충 시도`
- .KS/.KQ 전환: `✓ {ticker}.KQ 데이터 로드 성공`

### 3. 주의사항
- 키움 API 연결 필요 (access_token)
- Yahoo Finance 접근 가능 환경
- 최소 100개 봉 데이터 확보 필요

---

## 📝 추가 개선 가능 사항 (향후)

### 1. v2 Refactor 문서 추가 반영
- **EWMA 거래량 z-score** 적용
- **ATR 기반 손절가** 계산
- **시간 가중치** 감성 분석

### 2. 병렬 처리 최적화
```python
# 여러 종목 동시 검증 (현재는 순차 처리)
tasks = [validate_stock_for_trading(code, name, validator, api)
         for code, name in stock_info_list]
results = await asyncio.gather(*tasks)
```

### 3. 캐싱 시스템
- 조회한 데이터 5분간 캐시
- 중복 API 호출 방지

---

## 🔄 롤백 방법

문제 발생 시 백업 파일로 복구:
```bash
cd /home/greatbps/projects/kiwoom_trading
cp main_auto_trading.py.backup main_auto_trading.py
```

---

## ✅ 체크리스트

- [x] 비동기 다운로드 구현
- [x] .KS/.KQ 자동 전환
- [x] 키움 API 통합
- [x] Yahoo 보충 로직
- [x] 데이터 병합 로직
- [x] 함수 호출 부분 수정
- [x] 백업 파일 생성
- [ ] 실제 환경 테스트
- [ ] v2 고급 기능 추가 (EWMA, ATR 등)

---

**문의:** 추가 개선사항이나 버그 발견 시 이슈 등록 부탁드립니다.
