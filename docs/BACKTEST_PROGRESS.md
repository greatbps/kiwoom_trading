# 백테스트 통합 진행 상황

## ✅ 완료된 작업 (2025-11-02)

### 1. **백테스트 시스템 구현** (`backtest_with_ranker.py`)
- [x] 시뮬레이션 로직 (익절/손절/보유기간)
- [x] entry_features 수집 (8개 피처)
- [x] JSON 결과 저장 (Ranker 학습 데이터 포맷)
- [x] **실제 키움 API 데이터 지원** (`use_real_data=True`)
- [x] Mock 데이터 폴백 (데이터 부족/에러 시 자동)

### 2. **메뉴 통합** (`main_menu.py` 옵션 [5])
- [x] 백테스트 실행 인터페이스
- [x] 파라미터 입력 (보유기간, 익절, 손절)
- [x] 결과 출력 및 저장

### 3. **키움 REST API 확장** (`core/kiwoom_rest_client.py`)
- [x] `get_daily_chart()`: 일봉 차트 조회 (API ID: ka10081)
- [x] `get_historical_data_for_backtest()`: 백테스트용 과거 데이터 조회
- [x] 일봉/분봉 자동 선택 및 기간 필터링
- [x] Rate limit 처리 및 에러 핸들링

### 4. **조건검색 → 백테스트 통합**
- [x] `utils/backtest_integration.py`: VWAP 결과 → 백테스트 DataFrame 변환
- [x] `run_condition_and_backtest.py`: 통합 실행 스크립트
- [x] 조건검색 → VWAP 필터 → 백테스트 → 결과 저장 전체 파이프라인

---

## 🎯 사용 방법

### 방법 1: 통합 파이프라인 실행 (권장)

```bash
python run_condition_and_backtest.py
```

**플로우:**
1. 조건검색 실행 (Momentum, Breakout, EOD 등)
2. VWAP 2차 필터링
3. 백테스트 (실제 데이터 or Mock 선택 가능)
4. 결과 저장 → `./backtest_results/`

### 방법 2: 메뉴에서 백테스트만 실행

```bash
./run.sh
# 메뉴에서 [5] 백테스트 실행 선택
```

**현재:** 샘플 데이터로 테스트
**향후:** 조건검색 결과 자동 연동 예정

### 방법 3: 프로그램에서 직접 사용

```python
from backtest_with_ranker import BacktestRunner
from core.kiwoom_rest_client import KiwoomRESTClient
import pandas as pd

# API 클라이언트 초기화 (실제 데이터 사용 시)
async with KiwoomRESTClient(app_key, app_secret) as api_client:
    # 백테스트 실행
    runner = BacktestRunner(
        use_real_data=True,  # 실제 데이터 사용
        api_client=api_client
    )

    results = await runner.run_backtest(
        candidates_df,
        holding_period=5,
        take_profit_pct=3.0,
        stop_loss_pct=-2.0
    )

    runner.display_results(results)
```

---

## 🚀 다음 단계

### 우선순위 1: Feature 실시간 계산 로직 구현

**현재 문제:**
`utils/backtest_integration.py`에서 임시값 사용:
```python
entry_price = 10000  # 임시값
volume = 1000000  # 임시값
recent_return_5d = 0.0  # 임시값
```

**필요 작업:**
- 현재가 조회 API 통합
- 거래량 통계 계산 (20일 평균/표준편차)
- 최근 수익률 계산 (5일)
- 시장 변동성 계산 (KOSPI ATR)
- 업종 강도/가격 모멘텀 계산
- 임시 조치: 임시값 기반 백테스트 결과는 `SIMULATED (PLACEHOLDER)` 태그로 저장하고 실거래 파라미터 조정에 사용하지 않는다.

### 우선순위 2: 대량 백테스트 실행 (100+ 샘플 수집)

**목표:** Ranker 실전 학습용 데이터 확보

**방법:**
- 매일 조건검색 결과를 기록
- 30-60일 누적 → 100+ 거래 샘플
- 또는 과거 데이터로 시뮬레이션 (역사적 조건검색 재현)

### 우선순위 3: 키움 API 일봉 응답 필드명 확인

**현재 코드:**
```python
current_price = float(day_data.get('stk_close_prc', day_data.get('close', entry_price)))
```

**필요:**
- 실제 키움 API 일봉 응답 확인
- 필드명 정확히 매핑 (`stk_close_prc`, `stk_high_prc` 등)

### 우선순위 4: 메인 자동매매에 Ranker 통합

**목표:**
```
조건검색 → VWAP 필터 → [Ranker 점수화] → 상위 K개 선정 → 모니터링 → 매매
```

**필요 작업:**
- `main_auto_trading.py` 수정
- Ranker 로드 및 예측
- `threshold=0.7`, `top_k=10` 적용

---

## 📊 현재 상태 요약

| 항목 | 상태 | 비고 |
|------|------|------|
| 백테스트 시뮬레이션 로직 | ✅ 완료 | 익절/손절/보유기간 |
| 실제 데이터 지원 | ✅ 완료 | 키움 API 일봉/분봉 |
| Mock 데이터 폴백 | ✅ 완료 | 자동 전환 |
| 조건검색 연동 | ✅ 완료 | 통합 스크립트 |
| Entry Features 수집 | ⚠️ 임시값 | 실시간 계산 필요, 결과 태그 `SIMULATED` |
| 대량 데이터 수집 | ❌ 미완료 | 100+ 샘플 필요 |
| Ranker 파이프라인 통합 | ❌ 미완료 | main_auto_trading.py |

---

## 📝 관련 파일

- `backtest_with_ranker.py` - 백테스트 실행 엔진
- `core/kiwoom_rest_client.py` - 키움 API 클라이언트
- `utils/backtest_integration.py` - 데이터 변환 유틸
- `run_condition_and_backtest.py` - 통합 실행 스크립트
- `main_menu.py` - 메뉴 옵션 [5]
- `ml/candidate_ranker.py` - Ranker 모델
- `ml/training_data_builder.py` - 학습 데이터 빌더

---

**마지막 업데이트:** 2025-11-02
**작성자:** Claude Code

---

## ⚠️ 리스크 메모
- 임시 피처가 포함된 결과는 주간 손실 한도(-3%) 리포트에서 **참고용**으로만 활용한다. 실거래 파라미터 조정에는 사용하지 않는다.
- `results.json`에 `is_mock` 또는 `SIMULATED` 태그가 존재하면 자동 매매 시스템이 해당 데이터를 무시하도록 `RiskManager`가 포지션 사이즈를 50%로 제한한다.
- 실거래 계정과 동일한 파라미터(하드 스탑 -3%, 4/6% 익절, ATR×2 트레일링)를 적용한 검증 데이터만 성과 비교표에 포함한다.
