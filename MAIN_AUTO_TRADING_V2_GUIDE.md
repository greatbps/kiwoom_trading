# main_auto_trading_v2.py 사용 가이드

## 📋 개요

`main_auto_trading_v2.py`는 TradingOrchestrator를 사용한 간소화된 자동 매매 시스템입니다.

### 코드 축소 효과

| 항목 | Before (v1) | After (v2) | 개선도 |
|------|-------------|------------|--------|
| 파일 크기 | 2,767 lines | 300 lines | **89% 감소** |
| IntegratedTradingSystem 클래스 | 2,340+ lines | 제거 (TradingOrchestrator 사용) | **100% 모듈화** |
| 주요 로직 | 파일 내 구현 | trading 패키지로 분리 | **완전 분리** |

---

## 🚀 실행 방법

### 1. 자동 실행 모드 (기본)

```bash
python main_auto_trading_v2.py
```

**동작**:
1. 시스템 초기화
2. 계좌 정보 로드
3. 조건검색 + VWAP 필터링 자동 실행
4. 실시간 모니터링 시작
5. Ctrl+C로 종료

### 2. 메뉴 모드

```bash
python main_auto_trading_v2.py --menu
```

**메뉴**:
- [1] 자동 매매 시작
- [2] 조건검색 + VWAP 필터링만 실행
- [3] 현재 계좌 잔고 조회
- [4] 보유 종목 현황 조회
- [0] 종료

---

## 📦 주요 구조

### 초기화 단계

```python
# 1. 설정 로드
config = ConfigManager.load('config/trading_config.yaml')

# 2. API 초기화
api = KiwoomAPI(config)
token = api.get_access_token()

# 3. 의존성 초기화
risk_manager = RiskManager(config)
analyzer = EntryTimingAnalyzer()
validator = PreTradeValidator(config)
db = TradingDatabaseV2('database/trading.db')

# 4. TradingOrchestrator 생성
orchestrator = TradingOrchestrator(
    api=api,
    config=config,
    risk_manager=risk_manager,
    validator=validator,
    analyzer=analyzer,
    db=db
)
```

### 실행 단계

```python
# 1. 계좌 정보 로드
await orchestrator.initialize()

# 2. 조건검색 + VWAP 필터링
await orchestrator.run_condition_filtering("VWAP돌파")

# 3. 실시간 모니터링 시작
await orchestrator.monitor_and_trade()
```

---

## 🔄 기존 코드와의 비교

### Before (main_auto_trading.py - 2,767 lines)

```python
class IntegratedTradingSystem:
    def __init__(self, ...):
        # 수십 개의 속성 초기화
        self.websocket = None
        self.positions = {}
        self.watchlist = set()
        # ... 30+ 속성

    async def connect(self):
        # WebSocket 연결 (100+ lines)

    def check_all_stocks(self):
        # 모니터링 (600+ lines)

    def execute_buy(self):
        # 매수 실행 (150+ lines)

    def execute_sell(self):
        # 매도 실행 (150+ lines)

    # ... 25개의 추가 메서드

# 메인 로직 (100+ lines)
async def main():
    system = IntegratedTradingSystem(...)
    await system.initialize()
    await system.run_condition_filtering()
    await system.monitor_and_trade()
```

**문제점**:
- ❌ 2,767 lines의 거대한 파일
- ❌ 단일 책임 원칙 위반
- ❌ 높은 결합도
- ❌ 테스트 불가능
- ❌ 재사용 불가능

### After (main_auto_trading_v2.py - 300 lines)

```python
# 모든 로직이 trading 패키지로 분리됨
from trading import TradingOrchestrator

# 메인 로직 (간결함)
async def main():
    # 1. 의존성 초기화
    config = ConfigManager.load(...)
    api = KiwoomAPI(config)
    risk_manager = RiskManager(config)
    analyzer = EntryTimingAnalyzer()
    validator = PreTradeValidator(config)
    db = TradingDatabaseV2(...)

    # 2. Orchestrator 생성
    orchestrator = TradingOrchestrator(
        api, config, risk_manager,
        validator, analyzer, db
    )

    # 3. 실행
    await orchestrator.initialize()
    await orchestrator.run_condition_filtering("VWAP돌파")
    await orchestrator.monitor_and_trade()
```

**개선점**:
- ✅ 300 lines (89% 감소)
- ✅ 단일 책임 원칙 준수
- ✅ 낮은 결합도
- ✅ 완전한 모듈화
- ✅ 테스트 가능
- ✅ 가독성 향상

---

## 📊 시스템 플로우

```
main_auto_trading_v2.py
  ↓
TradingOrchestrator.initialize()
  ↓ AccountManager
  ├─ 계좌 잔고 조회
  ├─ 보유 종목 조회
  └─ PositionTracker에 로드

TradingOrchestrator.run_condition_filtering()
  ↓ ConditionScanner
  ├─ 조건식 검색 (키움 API)
  ├─ VWAP 백테스트 필터링
  └─ watchlist 업데이트

TradingOrchestrator.monitor_and_trade()
  ↓ (무한 루프)
  ├─ MarketMonitor.monitor_stocks()
  │   └─ 모든 종목 데이터 수집
  │
  ├─ SignalDetector.check_entry_signal()
  │   └─ 매수 신호 감지
  │       ↓ (신호 있음)
  │       OrderExecutor.execute_buy()
  │           ├─ RiskManager (포지션 크기 계산)
  │           ├─ KiwoomAPI (매수 주문)
  │           ├─ PositionTracker (포지션 추가)
  │           └─ AccountManager (잔고 업데이트)
  │
  └─ SignalDetector.check_exit_signal()
      └─ 매도 신호 감지 (6단계)
          ↓ (신호 있음)
          OrderExecutor.execute_sell()
              ├─ KiwoomAPI (매도 주문)
              ├─ PositionTracker (포지션 제거)
              └─ AccountManager (잔고 업데이트)
```

---

## 🎯 주요 기능

### 1. 자동 조건검색
- 5분마다 조건검색 자동 재실행
- 새로운 종목 자동 추가
- VWAP 백테스트 필터링 적용

### 2. 실시간 모니터링
- 1분마다 모든 종목 체크
- 키움 API + Yahoo Finance fallback
- 데이터 자동 보정

### 3. 자동 매수/매도
- VWAP 기반 매수 신호 감지
- 6단계 청산 로직:
  1. 장 마감 전 강제 청산 (15:00)
  2. Hard Stop (-1.3%)
  3. 부분 청산 (+4% 40%, +6% 40%)
  4. VWAP 하향 돌파
  5. 트레일링 스탑
  6. 시간 필터 (장 초반/말 회피)

### 4. 리스크 관리
- 자동 포지션 크기 계산
- 최대 보유 종목 수 제한
- 최대 포지션 비율 제한

---

## 🔧 설정

`config/trading_config.yaml`에서 모든 설정 관리:

```yaml
# VWAP 검증
vwap_validation:
  lookback_days: 10
  min_trades: 6
  min_win_rate: 40.0
  min_avg_profit_pct: 1.0

# 리스크 관리
risk:
  max_position_size_pct: 10.0
  max_total_exposure_pct: 80.0
  max_positions: 5

# 트레일링 스탑
trailing_stop:
  activation_pct: 1.5
  trailing_ratio: 1.0
  stop_loss_pct: 1.3

# 부분 청산
partial_exit:
  enabled: true
  tiers:
    - profit_pct: 4.0
      exit_ratio: 0.4
    - profit_pct: 6.0
      exit_ratio: 0.4
```

---

## 📝 로그 및 디버깅

### 로그 파일
- `logs/auto_trading_errors.log` - 에러 로그
- `logs/trading.log` - 거래 로그

### 화면 출력
- Rich 라이브러리로 컬러풀한 테이블 표시
- 실시간 모니터링 상태 표시
- 보유 포지션 상세 정보

---

## ⚠️ 주의사항

1. **환경변수 설정 필수**:
   ```
   KIWOOM_APP_KEY=your_app_key
   KIWOOM_APP_SECRET=your_app_secret
   KIWOOM_ACCOUNT_NUMBER=your_account_number
   ```

2. **가상환경 활성화**:
   ```bash
   source venv/bin/activate
   ```

3. **장 운영 시간**:
   - 평일 09:00 ~ 15:30
   - 주말/공휴일 자동 대기

4. **안전 모드**:
   - 첫 실행 시 소액으로 테스트
   - 설정 값 확인 후 실행

---

## 🚀 다음 단계

### 개발자를 위한 확장

```python
# 커스텀 신호 감지기 추가
class MySignalDetector(SignalDetector):
    def check_entry_signal(self, stock_code, stock_name, df):
        # 커스텀 로직
        return super().check_entry_signal(stock_code, stock_name, df)

# Orchestrator에 주입
orchestrator = TradingOrchestrator(...)
orchestrator.signal_detector = MySignalDetector(config, analyzer)
```

### 테스트 실행

```bash
# 단위 테스트
pytest tests/trading/ -v

# 통합 테스트
pytest tests/integration/ -v
```

---

## 📞 문의 및 지원

문제가 발생하면:
1. `logs/auto_trading_errors.log` 확인
2. 설정 파일 검증
3. API 토큰 재발급

**작성일**: 2025-11-09
**버전**: 2.0 (모듈화 버전)
