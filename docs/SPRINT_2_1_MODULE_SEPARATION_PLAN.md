# Sprint 2.1: main_auto_trading.py 모듈 분리 계획

**Sprint**: 2.1 - main_auto_trading.py 분리
**대상 파일**: `main_auto_trading.py` (2,767 lines)
**목표**: 8개 모듈로 분리하여 유지보수성 향상

---

## 📊 현재 상태 분석

### 파일 구조
- **총 라인 수**: 2,767 lines
- **주요 클래스**: `IntegratedTradingSystem` (2,340+ lines)
- **독립 함수**: 4개
- **메서드 수**: 28개 (IntegratedTradingSystem 내)

### 문제점
1. ❌ **단일 책임 원칙 위반** (SRP): 한 클래스가 너무 많은 역할 수행
2. ❌ **높은 결합도**: WebSocket, 거래, 모니터링, DB 등이 하나로 묶임
3. ❌ **테스트 어려움**: 거대한 클래스는 단위 테스트 불가능
4. ❌ **재사용성 낮음**: 특정 기능만 사용하기 어려움
5. ❌ **코드 가독성 저하**: 2,700+ 라인 파일 탐색 어려움

---

## 🎯 분리 계획

### 분리 기준
- **단일 책임 원칙 (SRP)**: 각 모듈은 하나의 책임만
- **높은 응집도**: 관련 기능끼리 그룹화
- **낮은 결합도**: 모듈 간 의존성 최소화
- **재사용 가능성**: 독립적으로 사용 가능

### 8개 모듈 구조

```
trading/
├── __init__.py
├── websocket_client.py        # 1. WebSocket 연결 관리
├── account_manager.py          # 2. 계좌 관리
├── condition_scanner.py        # 3. 조건검색 및 필터링
├── market_monitor.py           # 4. 시장 모니터링
├── signal_detector.py          # 5. 매매 신호 감지
├── order_executor.py           # 6. 주문 실행
├── position_tracker.py         # 7. 포지션 추적
└── trading_orchestrator.py     # 8. 전체 시스템 조율

main_auto_trading.py            # 진입점 (간소화)
```

---

## 📦 모듈별 상세 설계

### 1. `trading/websocket_client.py` (WebSocket 연결 관리)

**책임**: Kiwoom WebSocket 연결 및 메시지 송수신

**클래스**: `KiwoomWebSocketClient`

**메서드**:
- `connect()`: WebSocket 연결
- `disconnect()`: WebSocket 해제
- `send_message(trnm, data)`: 메시지 전송
- `receive_message(timeout)`: 메시지 수신
- `login()`: WebSocket 로그인

**이동할 코드** (main_auto_trading.py):
- `__init__`: WebSocket 관련 초기화
- `connect()`
- `send_message()`
- `receive_message()`
- `login()`

**라인 수**: ~200 lines

**의존성**:
- `websockets`
- `asyncio`
- `exceptions` (에러 처리)

---

### 2. `trading/account_manager.py` (계좌 관리)

**책임**: 계좌 잔고, 보유 종목, 주문 가능 금액 관리

**클래스**: `AccountManager`

**메서드**:
- `initialize()`: 계좌 정보 초기화
- `update_balance()`: 잔고 업데이트
- `get_available_cash()`: 주문 가능 금액 조회
- `get_holdings()`: 보유 종목 조회
- `has_holding(stock_code)`: 특정 종목 보유 여부

**속성**:
- `balance`: 예수금
- `available_cash`: 주문 가능 금액
- `holdings`: 보유 종목 dict
- `total_invested`: 총 투자 금액

**이동할 코드** (main_auto_trading.py):
- `initialize_account()`
- `update_account_balance()`
- 계좌 관련 속성들

**라인 수**: ~250 lines

**의존성**:
- `kiwoom_api.KiwoomAPI`
- `trading/websocket_client.py`

---

### 3. `trading/condition_scanner.py` (조건검색 및 필터링)

**책임**: 조건검색 실행 및 VWAP 필터링

**클래스**: `ConditionScanner`

**메서드**:
- `get_condition_list()`: 조건식 목록 조회
- `search_condition(seq, name)`: 조건검색 실행
- `run_filtering()`: 1차 + 2차 필터링
- `validate_stock(stock_code)`: VWAP 사전 검증
- `rescan()`: 조건검색 재실행

**이동할 코드** (main_auto_trading.py):
- `get_condition_list()`
- `search_condition()`
- `run_condition_filtering()`
- `rescan_and_add_stocks()`
- `validate_stock_for_trading()` (독립 함수)

**라인 수**: ~600 lines

**의존성**:
- `trading/websocket_client.py`
- `analyzers/pre_trade_validator.py`
- `kiwoom_api.KiwoomAPI`

---

### 4. `trading/market_monitor.py` (시장 모니터링)

**책임**: 시장 시간 체크, 실시간 가격 갱신

**클래스**: `MarketMonitor`

**메서드**:
- `is_market_open()`: 장 운영 시간 체크
- `wait_until_market_open()`: 장 시작까지 대기
- `update_prices(stock_codes)`: 가격 업데이트
- `check_all_stocks()`: 모든 종목 상태 체크

**이동할 코드** (main_auto_trading.py):
- `is_market_open()`
- `wait_until_time()`
- `check_all_stocks()` (일부)

**라인 수**: ~300 lines

**의존성**:
- `datetime`
- `kiwoom_api.KiwoomAPI`

---

### 5. `trading/signal_detector.py` (매매 신호 감지)

**책임**: VWAP 매수/매도 신호 감지

**클래스**: `SignalDetector`

**메서드**:
- `check_entry_signal(stock_code, df)`: 매수 신호 체크
- `check_exit_signal(stock_code, df)`: 매도 신호 체크
- `calculate_vwap(df)`: VWAP 계산
- `detect_crossover(df)`: 크로스오버 감지

**이동할 코드** (main_auto_trading.py):
- `check_entry_signal()`
- `check_exit_signal()`
- VWAP 계산 로직

**라인 수**: ~400 lines

**의존성**:
- `pandas`
- `analyzers/entry_timing_analyzer.py`

---

### 6. `trading/order_executor.py` (주문 실행)

**책임**: 매수/매도 주문 실행 및 리스크 관리

**클래스**: `OrderExecutor`

**메서드**:
- `execute_buy(stock_code, price, quantity)`: 매수 실행
- `execute_sell(stock_code, price, quantity)`: 매도 실행
- `execute_partial_sell(...)`: 부분 청산
- `calculate_position_size(...)`: 포지션 크기 계산
- `check_risk_limits(...)`: 리스크 한도 체크

**이동할 코드** (main_auto_trading.py):
- `execute_buy()`
- `execute_sell()`
- `execute_partial_sell()`
- 리스크 관리 로직

**라인 수**: ~450 lines

**의존성**:
- `kiwoom_api.KiwoomAPI`
- `trading/account_manager.py`
- `database/trading_db.py`
- `exceptions` (InsufficientFundsError 등)

---

### 7. `trading/position_tracker.py` (포지션 추적)

**책임**: 보유 포지션 상태 추적 및 관리

**클래스**: `PositionTracker`

**메서드**:
- `add_position(stock_code, ...)`: 포지션 추가
- `update_position(stock_code, ...)`: 포지션 업데이트
- `remove_position(stock_code)`: 포지션 제거
- `get_position(stock_code)`: 포지션 조회
- `get_all_positions()`: 전체 포지션 조회
- `calculate_profit(stock_code, current_price)`: 수익률 계산

**속성**:
- `positions`: dict[stock_code -> Position]
- `Position`: dataclass (entry_price, quantity, entry_time 등)

**이동할 코드** (main_auto_trading.py):
- `holdings` 관리 로직
- 수익률 계산 로직
- 포지션 상태 업데이트 로직

**라인 수**: ~250 lines

**의존성**:
- `dataclasses`
- `datetime`

---

### 8. `trading/trading_orchestrator.py` (전체 시스템 조율)

**책임**: 모든 모듈 통합 및 전체 거래 플로우 관리

**클래스**: `TradingOrchestrator`

**메서드**:
- `initialize()`: 시스템 초기화
- `run_daily_routine()`: 일일 루틴
- `monitor_and_trade()`: 실시간 모니터링 및 거래
- `shutdown()`: 시스템 종료

**이동할 코드** (main_auto_trading.py):
- `daily_routine()`
- `monitor_and_trade()`
- `run()`
- `shutdown()`

**라인 수**: ~350 lines

**의존성**:
- `trading/websocket_client.py`
- `trading/account_manager.py`
- `trading/condition_scanner.py`
- `trading/market_monitor.py`
- `trading/signal_detector.py`
- `trading/order_executor.py`
- `trading/position_tracker.py`

---

## 🔄 의존성 그래프

```
main_auto_trading.py
    ↓
TradingOrchestrator (조율자)
    ↓
    ├─→ WebSocketClient (연결)
    ├─→ AccountManager (계좌)
    │       ├─→ WebSocketClient
    │       └─→ KiwoomAPI
    ├─→ ConditionScanner (조건검색)
    │       ├─→ WebSocketClient
    │       ├─→ KiwoomAPI
    │       └─→ PreTradeValidator
    ├─→ MarketMonitor (모니터링)
    │       └─→ KiwoomAPI
    ├─→ SignalDetector (신호 감지)
    │       └─→ EntryTimingAnalyzer
    ├─→ OrderExecutor (주문 실행)
    │       ├─→ KiwoomAPI
    │       ├─→ AccountManager
    │       └─→ TradingDatabase
    └─→ PositionTracker (포지션 추적)
```

---

## 📝 이동 후 main_auto_trading.py 구조

**Before** (2,767 lines):
```python
# 2,767 lines의 거대한 파일
class IntegratedTradingSystem:
    # 28개 메서드
    # 2,340+ lines
```

**After** (~200 lines):
```python
"""
키움 자동매매 시스템 진입점
"""
from trading.trading_orchestrator import TradingOrchestrator
from kiwoom_api import KiwoomAPI
import asyncio

async def main(skip_wait: bool = False):
    """메인 실행 함수"""
    # API 초기화
    api = KiwoomAPI()
    api.get_access_token()

    # 조율자 생성
    orchestrator = TradingOrchestrator(
        access_token=api.access_token,
        api=api,
        condition_indices=[1, 2, 3, 4, 5, 6],
        skip_wait=skip_wait
    )

    # 시스템 실행
    try:
        await orchestrator.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]시스템 종료 중...[/yellow]")
        await orchestrator.shutdown()

if __name__ == "__main__":
    import sys
    skip_wait = "--skip-wait" in sys.argv
    asyncio.run(main(skip_wait))
```

---

## ✅ 분리 후 기대 효과

### 1. 유지보수성 향상
- 각 모듈 독립적 수정 가능
- 버그 위치 파악 용이
- 코드 리뷰 간소화

### 2. 테스트 용이성
- 모듈별 단위 테스트 가능
- Mock 객체 사용 간편
- 통합 테스트 구조화

### 3. 재사용성 증가
- 특정 모듈만 다른 프로젝트에서 사용 가능
- 예: `SignalDetector`만 백테스트에 사용

### 4. 확장성 개선
- 새로운 거래 전략 추가 용이
- 다른 브로커 API 지원 가능
- 플러그인 아키텍처 가능

### 5. 코드 가독성
- 파일당 200~600 lines (평균 350 lines)
- 명확한 책임 분리
- 직관적인 모듈 이름

---

## 🚀 실행 계획

### Phase 1: 독립 모듈 생성 (순서대로)
1. ✅ `WebSocketClient` (의존성 없음)
2. ✅ `PositionTracker` (의존성 없음)
3. ✅ `AccountManager` (WebSocketClient 의존)
4. ✅ `MarketMonitor` (간단)
5. ✅ `SignalDetector` (간단)
6. ✅ `ConditionScanner` (WebSocketClient 의존)
7. ✅ `OrderExecutor` (AccountManager 의존)
8. ✅ `TradingOrchestrator` (모든 모듈 의존)

### Phase 2: main_auto_trading.py 간소화
- IntegratedTradingSystem 제거
- TradingOrchestrator 사용

### Phase 3: 테스트 작성
- 각 모듈별 단위 테스트
- 통합 테스트

### Phase 4: 문서화
- 각 모듈 사용 예시
- API 문서

---

## 📊 예상 결과

| 항목 | Before | After | 개선 |
|------|--------|-------|------|
| 파일 개수 | 1개 | 9개 | +800% |
| 평균 파일 크기 | 2,767 lines | ~350 lines | -87% |
| 클래스당 메서드 | 28개 | ~5개 | -82% |
| 테스트 가능성 | 낮음 | 높음 | ✅ |
| 재사용성 | 낮음 | 높음 | ✅ |
| 유지보수성 | 낮음 | 높음 | ✅ |

---

**작성자**: Claude Code Assistant
**작성일**: 2025-11-09
**Sprint**: 2.1 - main_auto_trading.py 분리 계획
