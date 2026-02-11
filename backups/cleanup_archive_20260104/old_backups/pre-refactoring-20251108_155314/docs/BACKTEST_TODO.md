# Backtest Integration TODO

## ✅ 완료된 작업

1. **백테스트 시스템 구현** (`backtest_with_ranker.py`)
   - 시뮬레이션 로직 (익절/손절/보유기간)
   - entry_features 수집 (8개 피처)
   - JSON 결과 저장 (Ranker 학습 데이터 포맷)
   - **실제 키움 API 데이터 지원** (use_real_data=True)
   - Mock 데이터 폴백 (데이터 부족 시 자동)

2. **메뉴 통합** (`main_menu.py` 옵션 [5])
   - 백테스트 실행 인터페이스
   - 파라미터 입력 (보유기간, 익절, 손절)
   - 결과 출력 및 저장

3. **키움 REST API 확장** (`core/kiwoom_rest_client.py`)
   - ✅ `get_daily_chart()`: 일봉 차트 조회
   - ✅ `get_historical_data_for_backtest()`: 백테스트용 과거 데이터 조회
   - 일봉/분봉 자동 선택 및 기간 필터링

4. **조건검색 → 백테스트 통합**
   - ✅ `utils/backtest_integration.py`: VWAP 결과 → 백테스트 입력 변환
   - ✅ `run_condition_and_backtest.py`: 통합 실행 스크립트
   - 조건검색 → VWAP 필터 → 백테스트 → 결과 저장 전체 파이프라인

## 🚧 실전 적용을 위해 필요한 작업

### 1. 실제 조건검색 결과 연동 (우선순위: 높음)

**현재 상태:**
```python
# 하드코딩된 샘플 데이터
candidates = pd.DataFrame({
    'code': ['005930', '000660', ...],
    'name': ['삼성전자', 'SK하이닉스', ...],
    ...
})
```

**필요한 변경:**
```python
# 실제 조건검색 + VWAP 필터 결과 사용
from main_condition_filter import run_condition_search
from vwap_filter import VWAPFilter  # 경로는 실제 구현 확인 필요

# 조건검색 실행
search_results = await run_condition_search()

# VWAP 필터 적용
vwap_filter = VWAPFilter()
candidates = vwap_filter.filter(search_results)
```

**작업 내용:**
- `main_condition_filter.py`에서 결과를 DataFrame으로 반환하도록 수정
- VWAP 필터 모듈 확인 및 인터페이스 통일
- `backtest_with_ranker.py`의 `candidates` 포맷과 맞추기

### 2. 실제 과거 데이터 사용 (우선순위: 높음)

**현재 상태:**
```python
# _simulate_trade() 메서드에서 랜덤 워크 사용
returns = np.random.normal(
    entry_features['vwap_avg_profit'] / 100,
    0.02,
    holding_period
)
```

**필요한 변경:**
```python
# 키움 API에서 실제 과거 차트 데이터 가져오기
from core.kiwoom_rest_client import KiwoomRestClient

async def _simulate_trade(self, stock_code, entry_date, ...):
    # 과거 차트 데이터 요청
    chart_data = await self.api_client.get_chart_data(
        stock_code=stock_code,
        start_date=entry_date,
        end_date=entry_date + timedelta(days=holding_period),
        interval='D'  # 일봉
    )

    # 실제 가격으로 익절/손절 체크
    for day_data in chart_data:
        current_price = day_data['close']
        profit_pct = (current_price - entry_price) / entry_price * 100

        if profit_pct >= take_profit_pct:
            return {'exit_reason': 'take_profit', ...}
        elif profit_pct <= stop_loss_pct:
            return {'exit_reason': 'stop_loss', ...}
```

**작업 내용:**
- `KiwoomRestClient`에 과거 차트 데이터 조회 메서드 추가
- 일봉/분봉 선택 가능하도록 구현
- Rate limit 고려 (API 호출 제한)

### 3. 대량 백테스트 실행 (우선순위: 중간)

**목표:** 100+ 거래 샘플 수집 (Ranker 학습용)

**구현 방법:**
```python
# 과거 30-60일 데이터로 백테스트
for date in date_range(start_date='2024-10-01', end_date='2024-11-01'):
    # 해당 날짜의 조건검색 + VWAP 필터 재현
    candidates = get_candidates_on_date(date)

    # 백테스트 실행
    results = await runner.run_backtest(candidates, ...)

    # 결과 누적
    all_results.append(results)
```

**주의사항:**
- 조건검색 결과를 과거로 복원하려면 해당 날짜의 시장 데이터 필요
- 혹은 최근 N일간 매일 조건검색 결과를 기록해두기

### 4. Feature 실시간 계산 로직 (우선순위: 중간)

**현재:** `_extract_entry_features()`에서 DataFrame 컬럼 읽기

**필요:** 실시간 API에서 feature 계산

```python
def calculate_entry_features(self, stock_code: str) -> Dict[str, float]:
    # VWAP 백테스트 통계 (과거 데이터 기반)
    vwap_stats = self.vwap_analyzer.get_backtest_stats(stock_code)

    # 현재 VWAP 괴리율 (실시간 계산)
    current_price = await self.api.get_current_price(stock_code)
    vwap = await self.api.get_vwap(stock_code)
    vwap_distance = (current_price - vwap) / vwap * 100

    # 거래량 Z-score (20일 평균 대비)
    volume_data = await self.api.get_volume_history(stock_code, days=20)
    volume_z = calculate_z_score(volume_data['current'], volume_data['history'])

    # ... 나머지 features

    return {
        'vwap_backtest_winrate': vwap_stats['winrate'],
        'current_vwap_distance': vwap_distance,
        'volume_z_score': volume_z,
        ...
    }
```

**작업 내용:**
- 각 feature별 실시간 계산 로직 구현
- API 호출 최소화 (캐싱)
- `main_auto_trading.py`에서 사용 가능하도록 인터페이스 제공

### 5. 백테스트 결과 분석 도구 (우선순위: 낮음)

**추가 기능:**
- 백테스트 결과 비교 (여러 파라미터 조합)
- 성과 지표 시각화 (Sharpe ratio, MDD 등)
- Feature별 수익률 상관관계 분석

## 📝 다음 단계

1. **실제 조건검색 결과 연동** (1차 목표)
   - `main_condition_filter.py` 리팩토링
   - VWAP 필터 인터페이스 확인

2. **과거 데이터 API 구현** (1차 목표)
   - `KiwoomRestClient.get_chart_data()` 메서드 추가
   - `_simulate_trade()` 로직 교체

3. **대량 백테스트 수집** (2차 목표)
   - 30-60일 데이터로 100+ 샘플 확보
   - Ranker 재학습

4. **실전 파이프라인 통합** (최종 목표)
   - `main_auto_trading.py`에 Ranker 적용
   - 조건검색 → VWAP → **Ranker** → 모니터링 → 매매

---

**참고:**
- 현재 백테스트는 **프로토타입 완료** 단계
- 실전 적용을 위해 위 4개 작업 필요
- 우선순위: 실제 데이터 연동 > 대량 수집 > 분석 도구
