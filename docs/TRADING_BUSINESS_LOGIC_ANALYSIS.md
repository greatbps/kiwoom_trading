# 매매 비즈니스 로직 분석 (trading_system 참고)

## 📋 문서 목적
trading_system 프로젝트의 매매 비즈니스 로직을 분석하여 kiwoom_trading 프로젝트에 적용할 핵심 개념을 정리

**분석 대상**: `/home/greatbps/projects/trading_system`
**분석 일시**: 2025-10-24
**참고 문서**:
- trading_logic_analysis_summary.md
- 고도화된_매도_전략_가이드.md
- trading/executor.py
- trading/risk_manager.py

---

## 🎯 핵심 발견사항

### 1. 로직 준수 효과 입증 ⭐

**실제 성과 데이터 (3개월, 100건 거래):**
- **로직 완전 준수**: 평균 수익률 +5.58%, 승률 50%
- **로직 미준수**: 평균 수익률 -1.53%, 승률 50%
- **성과 차이**: 로직 준수 시 **+7.11%p 수익률 개선**

**매수 로직만 준수:**
- 승률: **90%** (매우 높음)
- 평균 수익률: +4.17%
- 보유기간: 25.4일

**결론**: 체계적 로직 준수가 실제 수익으로 입증됨

---

## 💰 매수 로직 (Entry Logic)

### 현재 적용 중인 기준

```python
# 1. 가격 필터
PRICE_MIN = 5000        # 최소 5천원
PRICE_MAX = 500000      # 최대 50만원

# 2. 2차 필터링 (다중 지표 검증)
- 거래량 필터: 최소 거래량 기준
- 시가총액 필터: 일정 규모 이상
- 기술적 지표: RSI, MACD, 이동평균 등
- AI 모델 스코어: 종합 점수 60점 이상

# 3. 시장 상황 반영
- 변동성 지수 확인
- 시장 추세 확인 (상승장/하락장/횡보장)
```

### 매수 로직 개선 방향

**단기 (1개월):**
- 가격 밴드 종목별 세분화
- 거래량 필터 강화 (최소 기준 상향)
- 시장 상황 동적 반영 (VIX 등)

**중기 (3개월):**
- AI 모델 정확도 향상
- 섹터별 특성 반영
- 다중 지표 가중치 최적화

---

## 🎯 매도 로직 (Exit Logic)

### 고도화된 6단계 매도 전략

#### 1단계: 하드 스탑 (-3%)
```python
if current_price <= entry_price * 0.97:
    # 전량 즉시 손절 (시장가)
    sell_all(market_order=True)
```

#### 2단계: 1차 부분익절 (+4%)
```python
if current_price >= entry_price * 1.04:
    # 보유량의 40% 매도 (지정가)
    sell_partial(quantity=position * 0.4, limit_price=current_price)
```

#### 3단계: 2차 부분익절 (+6%)
```python
if current_price >= entry_price * 1.06:
    # 보유량의 40% 추가 매도
    sell_partial(quantity=position * 0.4, limit_price=current_price)
    # ATR 트레일링 스탑 활성화 (잔여 20%)
    activate_trailing_stop(multiplier=1.5)
```

#### 4단계: ATR 트레일링 스탑
```python
trailing_line = highest_price - (ATR * 1.5)
if current_price <= trailing_line:
    # 잔여량 전량 매도
    sell_all(market_order=True)
```

#### 5단계: EMA + 볼륨 브레이크다운
```python
if profit_pct >= 6.0 and \
   current_price < ema_3min_5 and \
   volume > avg_volume * 1.2:
    # 추세 이탈 감지 → 잔여량 매도
    sell_all(market_order=True)
```

#### 6단계: 시간 필터
```python
if current_time >= "15:00":
    # 장 마감 30분 전 → 모든 포지션 청산
    close_all_positions()
```

### 매도 전략 핵심 개념

**부분 익절 (Scale-out):**
- 1차 목표(+4%): 40% 매도 → 리스크 감소
- 2차 목표(+6%): 40% 매도 → 수익 확정
- 잔여 20%: 트레일링으로 추가 수익 추구

**ATR 기반 트레일링 스탑:**
- 변동성 고려한 동적 손절선
- 트렌드 연장 시 수익 극대화
- 급락 시 빠른 청산

**볼륨/VWAP 필터:**
- 가짜 브레이크다운 방지
- 거래량 동반 여부 확인
- 추세 전환 신호 검증

---

## 🛡️ 리스크 관리 시스템

### 1. 포지션 사이징

```python
class PositionSizing:
    # 계좌 대비 리스크
    RISK_PER_TRADE = 0.02        # 거래당 2%
    MAX_POSITION_SIZE = 0.30      # 최대 30%

    # 하드 리미트 (절대 제한)
    HARD_MAX_POSITION = 200000    # 20만원
    HARD_MAX_DAILY_LOSS = 500000  # 50만원

    def calculate_position_size(self, account_balance, stop_loss_pct):
        # 리스크 금액
        risk_amount = account_balance * RISK_PER_TRADE

        # 주당 리스크
        risk_per_share = entry_price * (stop_loss_pct / 100)

        # 수량 계산
        quantity = int(risk_amount / risk_per_share)

        # 최대 한도 적용
        max_quantity = int(account_balance * MAX_POSITION_SIZE / entry_price)

        return min(quantity, max_quantity)
```

### 2. 동적 한도 관리

```python
async def update_dynamic_limits(self):
    # 실시간 잔고 조회
    available_cash = await get_orderable_cash()

    # 동적 계산
    max_position = int(available_cash * MAX_POSITION_SIZE_PCT)
    max_daily_loss = int(available_cash * MAX_DAILY_LOSS_PCT)

    # 하드 리미트 적용 (안전 장치)
    self.max_position_size = min(max_position, HARD_MAX_POSITION)
    self.max_daily_loss = min(max_daily_loss, HARD_MAX_DAILY_LOSS)
```

### 3. 일일 손익 관리

```python
class DailyRiskManager:
    MAX_DAILY_LOSS = 500000  # 50만원

    async def check_daily_loss_limit(self):
        daily_pnl = await calculate_daily_pnl()

        if daily_pnl < -MAX_DAILY_LOSS:
            # 일일 손실 한도 초과
            await emergency_stop_all_trading()
            await send_alert("일일 손실 한도 초과!")
            return False

        return True
```

### 4. 포트폴리오 리스크 평가

```python
class PortfolioRiskAssessment:
    def assess_risk_level(self, positions):
        # 개별 포지션 리스크
        position_risks = {}
        for symbol, position in positions.items():
            risk_level = self._assess_position_risk(symbol, position)
            position_risks[symbol] = risk_level

        # 전체 리스크 레벨
        overall_risk = self._determine_overall_risk(position_risks)

        return {
            'overall_risk': overall_risk,  # LOW/MEDIUM/HIGH/CRITICAL
            'position_risks': position_risks,
            'recommendations': self._generate_recommendations(overall_risk)
        }
```

---

## 🚀 자동 매매 실행 흐름

### 1. 메인 실행 루프

```python
class AutoTradingHandler:
    async def run_main_loop(self):
        while True:
            try:
                # 1. 시장 시간 확인
                if not is_market_open():
                    await asyncio.sleep(60)
                    continue

                # 2. 잔고 및 한도 업데이트
                await update_dynamic_limits()

                # 3. 일일 손실 한도 체크
                if not await check_daily_loss_limit():
                    await stop_trading_for_today()
                    break

                # 4. 모니터링 종목 스캔
                monitoring_stocks = await get_monitoring_stocks()

                # 5. 종목별 분석 및 매매 신호
                for stock in monitoring_stocks:
                    # 분석 실행
                    analysis = await analyze_stock(stock)

                    # 매수 신호 확인
                    if analysis['signal'] == 'BUY':
                        await execute_buy_signal(stock, analysis)

                # 6. 보유 종목 모니터링
                holdings = await get_current_holdings()

                for holding in holdings:
                    # 매도 신호 확인
                    exit_signal = await check_exit_signals(holding)

                    if exit_signal:
                        await execute_sell_signal(holding, exit_signal)

                # 7. 리스크 평가
                await assess_portfolio_risk()

                # 8. 대기
                await asyncio.sleep(MONITORING_INTERVAL)

            except Exception as e:
                logger.error(f"메인 루프 오류: {e}")
                await asyncio.sleep(10)
```

### 2. 매수 실행

```python
async def execute_buy_signal(stock, analysis):
    # 1. 가격 필터 확인
    if not price_filter_check(stock['price']):
        return

    # 2. 2차 필터링
    if not secondary_filter_check(stock, analysis):
        return

    # 3. 포지션 사이징
    position_size = calculate_position_size(
        account_balance=current_balance,
        entry_price=stock['price'],
        stop_loss_pct=3.0
    )

    # 4. 리스크 체크
    if not risk_check_passed(position_size):
        return

    # 5. 실제 주문 실행
    result = await trading_executor.execute_buy_order(
        symbol=stock['symbol'],
        quantity=position_size,
        order_type=OrderType.MARKET
    )

    # 6. 손절/익절 설정
    if result['success']:
        await setup_automatic_stop_loss(
            symbol=stock['symbol'],
            stop_loss_pct=3.0,
            take_profit_pct=6.0
        )
```

### 3. 매도 실행 (고도화 전략)

```python
async def check_exit_signals(holding):
    entry_price = holding['avg_price']
    current_price = holding['current_price']
    quantity = holding['quantity']
    profit_pct = (current_price - entry_price) / entry_price * 100

    # 1. 하드 스탑 (-3%)
    if current_price <= entry_price * 0.97:
        return {
            'type': 'HARD_STOP',
            'quantity': quantity,
            'order_type': 'MARKET'
        }

    # 2. 1차 부분익절 (+4%)
    if profit_pct >= 4.0 and not holding.get('partial_exit_1_done'):
        return {
            'type': 'PARTIAL_TP_1',
            'quantity': int(quantity * 0.4),
            'order_type': 'LIMIT'
        }

    # 3. 2차 부분익절 (+6%)
    if profit_pct >= 6.0 and not holding.get('partial_exit_2_done'):
        return {
            'type': 'PARTIAL_TP_2',
            'quantity': int(quantity * 0.4),
            'order_type': 'LIMIT'
        }

    # 4. ATR 트레일링 스탑
    if holding.get('trailing_stop_active'):
        trailing_line = holding['highest_price'] - (holding['atr'] * 1.5)
        if current_price <= trailing_line:
            return {
                'type': 'TRAILING_STOP',
                'quantity': quantity,
                'order_type': 'MARKET'
            }

    # 5. EMA + 볼륨 브레이크다운
    if profit_pct >= 6.0:
        if await check_breakdown_signal(holding):
            return {
                'type': 'BREAKDOWN',
                'quantity': quantity,
                'order_type': 'MARKET'
            }

    # 6. 시간 필터 (15:00 이후)
    if current_time >= "15:00":
        return {
            'type': 'TIME_CLOSE',
            'quantity': quantity,
            'order_type': 'MARKET'
        }

    return None
```

---

## 📊 성능 최적화 기법

### 1. Optuna 자동 최적화

```python
class ExitStrategyOptimizer:
    def optimize_parameters(self, historical_data, n_trials=100):
        def objective(trial):
            # 파라미터 탐색 범위
            params = {
                'hard_stop_loss': trial.suggest_float('hard_stop_loss', 0.95, 0.99),
                'partial_tp_level1': trial.suggest_float('partial_tp_level1', 1.02, 1.06),
                'partial_tp_level2': trial.suggest_float('partial_tp_level2', 1.04, 1.10),
                'atr_multiplier': trial.suggest_float('atr_multiplier', 1.0, 2.5),
                'volume_threshold': trial.suggest_float('volume_threshold', 1.0, 2.0),
            }

            # 백테스트 실행
            result = backtest_with_params(historical_data, params)

            # 목표: 샤프 비율 최대화
            return result['sharpe_ratio']

        # 최적화 실행
        study = optuna.create_study(direction='maximize')
        study.optimize(objective, n_trials=n_trials)

        return study.best_params
```

### 2. 변동성 기반 동적 조정

```python
def adjust_params_by_volatility(base_params, market_volatility):
    # 변동성 높을 때
    if market_volatility > 30:
        # 손절 폭 확대, 목표 수익률 확대
        return {
            **base_params,
            'hard_stop_loss': base_params['hard_stop_loss'] - 0.01,
            'partial_tp_level1': base_params['partial_tp_level1'] + 0.02,
            'atr_multiplier': base_params['atr_multiplier'] * 1.2
        }

    # 변동성 낮을 때
    elif market_volatility < 15:
        # 손절 폭 축소, 목표 수익률 축소
        return {
            **base_params,
            'hard_stop_loss': base_params['hard_stop_loss'] + 0.01,
            'partial_tp_level1': base_params['partial_tp_level1'] - 0.01,
            'atr_multiplier': base_params['atr_multiplier'] * 0.8
        }

    return base_params
```

---

## 🎯 kiwoom_trading 적용 계획

### Phase 1: 기본 구조 (1주)
- [x] 매매 전략 엔진 (trading_strategy.py) - 완료
- [ ] 자동 매매 핸들러 (auto_trading_handler.py)
- [ ] 리스크 관리자 (risk_manager.py)
- [ ] 포지션 관리자 (position_manager.py)

### Phase 2: 고도화 전략 (2주)
- [ ] 6단계 매도 전략 구현
- [ ] ATR 트레일링 스탑
- [ ] 부분 익절 로직
- [ ] 시간/볼륨 필터

### Phase 3: 자동화 (2주)
- [ ] 실시간 모니터링 루프
- [ ] 자동 주문 실행
- [ ] 알림 시스템
- [ ] 비상 정지 메커니즘

### Phase 4: 최적화 (1주)
- [ ] Optuna 통합
- [ ] 백테스팅 시스템
- [ ] 성과 분석 도구

---

## 💡 핵심 교훈

1. **로직 준수의 중요성**: 체계적 로직 따르면 +7%p 수익 개선
2. **부분 익절 효과**: 리스크 감소 + 수익 극대화 동시 달성
3. **동적 리스크 관리**: 실시간 잔고 기반 한도 조정 필수
4. **하드 리미트 중요성**: 절대 제한선으로 대손실 방지
5. **일일 손실 관리**: 일일 한도 초과 시 즉시 중단
6. **자동화 필요성**: 감정 배제, 일관된 실행

---

**작성**: 2025-10-24
**참고 시스템**: trading_system (KIS API 기반)
**적용 대상**: kiwoom_trading (Kiwoom API 기반)
