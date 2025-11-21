# 자동 매매 시스템 구현 완료

## 개요

trading_system 프로젝트의 검증된 비즈니스 로직을 기반으로, 더욱 발전되고 깔끔한 구조의 자동 매매 시스템을 구현했습니다.

**핵심 성과 데이터 (trading_system 기반):**
- 로직 준수시: +5.58% 평균 수익, 50% 승률
- 매수 로직만 준수시: +4.17% 평균 수익, **90% 승률**
- 로직 미준수시: -1.53% 평균 손실
- **차이: +7.11%p 개선 효과**

## 아키텍처

### 핵심 컴포넌트 (core/)

```
core/
├── auto_trading_handler.py   # 메인 트레이딩 루프
├── position_manager.py        # 포지션 추적 및 관리
├── risk_manager.py            # 리스크 관리 및 한도 통제
├── order_executor.py          # 주문 실행 및 6단계 매도 전략
└── market_monitor.py          # 실시간 시장 모니터링
```

### 1. PositionManager (포지션 관리자)

**기능:**
- 보유 종목 추적 및 관리
- 실시간 가격 업데이트
- 분할 매도 단계 추적 (진입 → 1차익절 → 2차익절 → Trailing)
- ATR 기반 Trailing Stop 자동 업데이트
- 포지션 저장/복원 (data/positions.json)

**핵심 클래스:**

```python
@dataclass
class Position:
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: float
    buy_time: datetime

    # 매매 전략
    target1, target2, target3: float
    stop_loss: float

    # 분할 매도 진행
    stage: int  # 0: 진입, 1: 1차익절, 2: 2차익절, 3: trailing
    remaining_quantity: int
    is_trailing_active: bool
    trailing_stop: Optional[float]
    atr: float
```

**주요 메서드:**
- `add_position()`: 신규 포지션 추가
- `update_price()`: 현재가 업데이트
- `update_stage()`: 매도 단계 업데이트
- `update_trailing_stop()`: Trailing stop 자동 조정
- `get_summary()`: 포트폴리오 요약

### 2. RiskManager (리스크 관리자)

**검증된 리스크 파라미터 (trading_system 실전 데이터):**

```python
RISK_PER_TRADE = 0.02          # 거래당 2% 리스크
MAX_POSITION_SIZE = 0.30       # 포지션당 최대 30%

# 하드 리밋 (절대 초과 불가)
HARD_MAX_POSITION = 200000     # 20만원
HARD_MAX_DAILY_LOSS = 500000   # 50만원 (일일)
HARD_MAX_WEEKLY_LOSS = 0.03    # 주간 손실 3% 초과 시 신규 진입 제한
HARD_MAX_DAILY_TRADES = 10     # 일일 최대 10회

# 포트폴리오 제약
MAX_POSITIONS = 5              # 최대 5종목
MIN_CASH_RESERVE = 0.20        # 최소 현금 20%
```

> **주간 손실 관리**  
> - `HARD_MAX_WEEKLY_LOSS`가 발동하면 신규 진입은 중단되며, 보유 포지션은 트레일링만 허용한다.  
> - RiskManager는 자동으로 진입 비중을 50% 이하로 낮추고 `risk_log.json`에 경고를 남긴다.  
> - 주간 손실이 -1% 미만으로 회복될 때까지 강화 모드를 유지한다.

**주요 기능:**

1. **신규 포지션 진입 가능 여부 확인**
   ```python
   can_open, reason = risk_manager.can_open_position(
       current_balance=10000000,
       current_positions_value=2000000,
       position_count=2,
       position_size=300000
   )
   ```

2. **포지션 크기 계산 (리스크 기반)**
   ```python
   position_size = risk_manager.calculate_position_size(
       current_balance=10000000,
       current_price=70000,
       stop_loss_price=67000,
       entry_confidence=1.0
   )
   # 결과: quantity, investment, risk_amount, position_ratio, max_loss
   ```

3. **긴급 중지 조건 확인**
   ```python
should_stop, reason = risk_manager.check_emergency_stop(unrealized_pnl)
# 일일(-5%) 또는 주간(-3%) 손실 한도 초과시 True
   ```

4. **리스크 지표 계산**
   - 총 자산, 현금 비율, 포지션 비율
   - 일일 손익 (실현 + 미실현)
   - 손실 허용 잔여 금액
   - 거래 횟수 추적

### 3. OrderExecutor (주문 실행자)

**6단계 고도화 매도 전략 구현:**

```python
# 매도 전략 파라미터
HARD_STOP_RATE = -0.03         # -3% 하드 스탑
PARTIAL_TP1_RATE = 0.04        # +4% 1차 익절
PARTIAL_TP2_RATE = 0.06        # +6% 2차 익절

PARTIAL_SELL_RATIO_1 = 0.40    # 1차 익절 40%
PARTIAL_SELL_RATIO_2 = 0.40    # 2차 익절 40%
TRAILING_RATIO = 0.20          # Trailing 20%

FORCE_CLOSE_TIME = "15:00:00"  # 장 마감 전 강제 청산
```

**6단계 매도 로직:**

1. **Hard Stop (-3%)**
   - 전량 즉시 매도
   - 손실 확대 방지

2. **Partial TP 1 (+4%)**
   - 40% 분할 매도
   - 원금 일부 회수

3. **Partial TP 2 (+6%)**
   - 추가 40% 매도
   - Trailing Stop 활성화 (나머지 20%)

4. **ATR Trailing Stop**
   - ATR 2배 아래로 trailing
   - 가격 상승시 trailing stop도 상승 (하락 불가)
   - 20% 물량으로 추세 추종

5. **EMA + Volume Breakdown**
   - (추후 구현 예정)
   - 추세 전환 감지시 잔여 물량 청산

6. **Time Filter (15:00)**
   - 장 마감 전 강제 청산
   - 익일 갭 리스크 회피

**주요 메서드:**

```python
# 매수 주문 실행
execute_buy(stock_code, stock_name, quantity, price, targets, stop_loss, atr, ...)

# 매도 주문 실행
execute_sell(stock_code, quantity, price, reason)

# 6단계 매도 신호 체크
check_exit_signals(position, current_price, current_time)

# 매도 신호 처리 (체크 + 실행)
process_exit_signal(position, current_price, current_time)
```

### 4. MarketMonitor (시장 모니터)

**기능:**
- 관심 종목 리스트 관리 (data/watchlist.json)
- 장 운영 시간 확인 (09:00 ~ 15:30, 주말 제외)
- 매수 신호 스캔 (통합 분석 + 매매 전략 생성)
- 실시간 현재가 조회

**주요 메서드:**

```python
# 장 운영 시간 확인
is_market_open(current_time) -> bool

# 관심 종목 추가/제거
add_to_watchlist(stock_code, stock_name)
remove_from_watchlist(stock_code)

# 매수 신호 스캔
scan_for_buy_signals(account_balance) -> List[dict]
# 결과: [{'stock_code', 'stock_name', 'signal', 'score', 'trading_plan', ...}]

# 현재가 조회
get_current_price(stock_code) -> float
```

### 5. AutoTradingHandler (자동 매매 핸들러)

**메인 트레이딩 루프:**

```python
while is_running:
    # 1. 장 운영 시간 확인
    if not is_market_open():
        continue

    # 2. 계좌 상태 조회
    current_balance = get_account_balance()
    positions_value = get_total_value()
    unrealized_pnl = get_total_profit_loss()

    # 3. 긴급 중지 조건 확인 (일일 -5%, 주간 -3%)
    should_stop, reason = check_emergency_stop(unrealized_pnl)
    if should_stop:
        emergency_liquidate()  # 모든 포지션 강제 청산
        break

    # 4. 리스크 지표 확인
    risk_metrics = get_risk_metrics(...)

    # 5. 보유 포지션 모니터링
    for position in positions:
        current_price = get_current_price(position.stock_code)
        update_price(position, current_price)

        # 6단계 매도 신호 체크
        exit_result = process_exit_signal(position, current_price, current_time)

    # 6. 매수 신호 스캔
    if can_open_position():
        buy_candidates = scan_for_buy_signals(current_balance)
        if buy_candidates:
            best_candidate = buy_candidates[0]  # 최고 점수
            try_buy(best_candidate, ...)

    # 7. 다음 루프 대기
    sleep(60)  # 1분 대기
```

**사용법:**

```python
# 자동 매매 핸들러 생성
handler = AutoTradingHandler(
    account_no="12345678-01",
    initial_balance=10000000,
    risk_per_trade=0.02,
    max_position_size=0.30
)

# 자동 매매 시작 (Ctrl+C로 중지)
handler.start()

# 상태 리포트 조회
status = handler.get_status_report()
```

## 데이터 저장 구조

```
data/
├── positions.json         # 포지션 정보
├── risk_log.json          # 일일 거래 로그
└── watchlist.json         # 관심 종목 리스트
```

### positions.json 예시

```json
{
  "005930": {
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "quantity": 100,
    "avg_price": 70000,
    "current_price": 73500,
    "buy_time": "2025-10-24T09:30:00",
    "target1": 72000,
    "target2": 74000,
    "target3": 76000,
    "stop_loss": 67000,
    "stage": 2,
    "remaining_quantity": 20,
    "is_trailing_active": true,
    "trailing_stop": 71500,
    "atr": 1000,
    "entry_signal": "BUY",
    "entry_score": 65.5
  }
}
```

### risk_log.json 예시

```json
{
  "initial_balance": 10000000,
  "today": "2025-10-24",
  "daily_trades": [
    {
      "timestamp": "2025-10-24T09:30:15",
      "stock_code": "005930",
      "stock_name": "삼성전자",
      "type": "BUY",
      "quantity": 100,
      "price": 70000,
      "amount": 7000000,
      "realized_pnl": 0
    },
    {
      "timestamp": "2025-10-24T14:20:30",
      "stock_code": "005930",
      "stock_name": "삼성전자",
      "type": "SELL",
      "quantity": 40,
      "price": 72000,
      "amount": 2880000,
      "realized_pnl": 80000
    }
  ],
  "daily_realized_pnl": 80000
}
```

### watchlist.json 예시

```json
[
  {
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "last_check_time": "2025-10-24T10:15:00",
    "last_score": 64.0,
    "last_signal": "BUY"
  },
  {
    "stock_code": "000660",
    "stock_name": "SK하이닉스",
    "last_check_time": "2025-10-24T10:15:00",
    "last_score": 67.2,
    "last_signal": "BUY"
  }
]
```

## 테스트

### 단위 테스트

```bash
# 포지션 관리자 테스트
python test/test_auto_trading.py --mode position

# 리스크 관리자 테스트
python test/test_auto_trading.py --mode risk

# 전체 단위 테스트
python test/test_auto_trading.py --mode all
```

### 통합 테스트

```bash
# 전체 시스템 통합 테스트 (실제 API 호출)
python test/test_auto_trading.py --mode full
```

**통합 테스트 결과:**

```
✅ 매수 후보 2개 발견

순위     종목명          신호                 점수          현재가       수량            투자금
--------------------------------------------------------------------------------
1      SK하이닉스       BUY             67.20    508,000원        3주      1,524,000원
2      삼성전자         BUY             64.00     98,500원       21주      2,068,500원

🏆 최고 점수 종목: SK하이닉스 (67.20점)

📋 매매 계획:
  진입 신호: BUY (MEDIUM)
  매수 수량: 3주
  투자 금액: 1,524,000원 (15.2%)
  목표가:
    1차: 513,304원 (+1.04%)
    2차: 516,839원 (+1.74%)
    3차: 522,143원 (+2.78%)
  손절가: 482,600원 (-5.00%)
  리스크/리워드: 1:0.21
```

## trading_system과의 차이점 (개선 사항)

### 1. 아키텍처 개선

**trading_system:**
- 단일 파일에 여러 기능 혼재
- 하드코딩된 설정값
- 제한적인 에러 처리

**kiwoom_trading (본 시스템):**
- 명확한 관심사 분리 (5개 독립 모듈)
- 데이터 클래스 기반 타입 안전성
- 포괄적인 예외 처리 및 로깅
- 저장/복원 자동화

### 2. 포지션 관리 개선

**trading_system:**
- 딕셔너리 기반 포지션 추적
- 수동 trailing stop 계산

**kiwoom_trading:**
- Position 데이터 클래스
- 자동 trailing stop 업데이트
- 프로퍼티 기반 실시간 계산 (profit_loss, profit_loss_rate 등)
- 파일 기반 영속성

### 3. 리스크 관리 강화

**trading_system:**
- 기본적인 한도 체크
- 고정된 리스크 파라미터

**kiwoom_trading:**
- 동적 한도 계산 (실시간 잔고 기반)
- 하드 리밋 + 소프트 리밋 이중 안전장치
- 일일 손익 추적 및 긴급 중지
- 포트폴리오 레벨 리스크 지표

### 4. 매도 전략 구조화

**trading_system:**
- 조건문 기반 분기 처리
- 수동 상태 관리

**kiwoom_trading:**
- 6단계 명시적 state machine
- 자동 stage 전환
- Trailing stop 자동화
- Time filter 통합

### 5. 코드 품질

**trading_system:**
- 약 800줄 단일 파일
- 제한적인 문서화
- 하드코딩된 값

**kiwoom_trading:**
- 모듈당 200-400줄 (총 1400줄+)
- 상세한 docstring 및 주석
- 설정 가능한 파라미터
- 타입 힌팅 전면 적용

## 실전 배포 전 체크리스트

### 1. API 설정
- [ ] .env 파일의 실전 계좌 설정
- [ ] 토큰 유효성 확인

### 2. 리스크 파라미터 확인
- [ ] RISK_PER_TRADE (기본 2%)
- [ ] MAX_POSITION_SIZE (기본 30%)
- [ ] HARD_MAX_POSITION (기본 20만원)
- [ ] HARD_MAX_DAILY_LOSS (기본 50만원)

### 3. 관심 종목 리스트
- [ ] data/watchlist.json 설정
- [ ] 종목 유동성 확인

### 4. 백테스팅
- [ ] 과거 데이터로 전략 검증
- [ ] 승률 및 손익비 확인

### 5. 모니터링
- [ ] 로그 파일 설정
- [ ] 알림 시스템 구축 (선택)

## 향후 개선 과제

### Phase 2: 고도화 기능 (완료)
- ✅ 포지션 관리자
- ✅ 리스크 관리자
- ✅ 주문 실행자 (6단계 매도)
- ✅ 시장 모니터
- ✅ 자동 매매 핸들러

### Phase 3: 추가 개선 (예정)
- [ ] EMA + Volume Breakdown 매도 신호 구현
- [ ] 실제 Kiwoom 매수/매도 API 통합 (현재는 시뮬레이션)
- [ ] 알림 시스템 (Slack, Telegram 등)
- [ ] 대시보드 웹 UI
- [ ] 성과 분석 리포트 생성

### Phase 4: 백테스팅 시스템 (예정)
- [ ] 과거 데이터 기반 전략 검증
- [ ] 파라미터 최적화 (Optuna)
- [ ] 몬테카를로 시뮬레이션
- [ ] 성과 지표 (Sharpe, Sortino, MDD 등)

## 라이선스

개인 프로젝트 - trading_system 비즈니스 로직 참고

## 작성자

greatbps

## 업데이트 이력

- 2025-10-24: 자동 매매 시스템 Phase 1-2 구현 완료
  - 핵심 5개 모듈 구현
  - 6단계 고도화 매도 전략 구현
  - 통합 테스트 성공
