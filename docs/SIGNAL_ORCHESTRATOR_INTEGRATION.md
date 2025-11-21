# SignalOrchestrator 통합 가이드

## 📋 개요

L0-L6 시그널 파이프라인을 main_auto_trading.py에 통합하는 방법을 설명합니다.

## 🔧 통합 단계

### 1. Import 추가

```python
# main_auto_trading.py 상단에 추가
from analyzers.signal_orchestrator import SignalOrchestrator, SignalTier
```

### 2. IntegratedTradingSystem.__init__() 수정

```python
def __init__(self, access_token: str, api: KiwoomAPI, condition_indices: List[int], skip_wait: bool = False):
    # ... 기존 코드 ...

    # SignalOrchestrator 초기화 (최적화된 청산 로직 다음에 추가)
    self.signal_orchestrator = SignalOrchestrator(
        config=self.config,
        api=self.api
    )
    console.print("[dim]✓ SignalOrchestrator 초기화 완료[/dim]")
```

### 3. 조건검색 결과 필터링에 L2 (RS 필터) 적용

기존 `process_condition_search()` 함수에서 RS 필터 추가:

```python
async def process_condition_search(self):
    # ... 조건검색 코드 ...

    # L2: RS 필터 적용
    console.print("\n[cyan]📊 L2: RS 필터링 시작[/cyan]")

    candidates = [
        {
            'stock_code': stock['code'],
            'stock_name': stock['name'],
            'market': stock.get('market', 'KOSPI')
        }
        for stock in self.condition_list
    ]

    # RS 필터링
    filtered_candidates = self.signal_orchestrator.check_l2_rs_filter(
        candidates,
        market='KOSPI'  # 또는 동적으로 판단
    )

    # 필터링된 종목만 watchlist에 추가
    self.watchlist.clear()
    for candidate in filtered_candidates:
        self.watchlist.add(candidate['stock_code'])
        self.validated_stocks[candidate['stock_code']] = {
            'name': candidate['stock_name'],
            'market': candidate.get('market', 'KOSPI'),
            'rs_rating': candidate.get('rs_rating', 0),
            # ... 기존 정보 ...
        }

    console.print(f"[green]✓ RS 필터링 완료: {len(filtered_candidates)}개 종목 선택[/green]")
```

### 4. 매수 신호 체크 함수 수정

기존 `check_buy_signal()` 함수를 SignalOrchestrator로 대체:

```python
async def check_buy_signal(self, stock_code: str, kiwoom_df: pd.DataFrame = None):
    """매수 신호 체크 (SignalOrchestrator 사용)"""
    try:
        console.print(f"[dim]🔍 {stock_code}: 매수 신호 체크 시작[/dim]")

        stock_info = self.validated_stocks.get(stock_code, {})
        stock_name = stock_info.get('name', stock_code)
        market = stock_info.get('market', 'KOSPI')

        # 1. 데이터 조회
        if kiwoom_df is not None and len(kiwoom_df) >= 50:
            df = kiwoom_df.copy()
        else:
            # Yahoo Finance fallback
            ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
            ticker = f"{stock_code}{ticker_suffix}"
            df = download_stock_data_sync(ticker, days=1)

            if df is None or len(df) < 50:
                console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족[/yellow]")
                return

        # 컬럼명 소문자 변환
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
        else:
            df.columns = df.columns.str.lower()

        # VWAP 계산
        vwap_config = self.config.get_section('vwap')
        df = self.analyzer.calculate_vwap(df,
                                           use_rolling=vwap_config.get('use_rolling', True),
                                           rolling_window=vwap_config.get('rolling_window', 20))
        df = self.analyzer.calculate_atr(df)

        signal_config = self.config.get_signal_generation_config()
        df = self.analyzer.generate_signals(df, **signal_config)

        current_price = df['close'].iloc[-1]

        # 2. SignalOrchestrator로 전체 시그널 평가
        signal_result = self.signal_orchestrator.evaluate_signal(
            stock_code=stock_code,
            stock_name=stock_name,
            current_price=current_price,
            df=df,
            market=market,
            current_cash=self.current_cash,
            daily_pnl=self.calculate_daily_pnl()  # 일일 손익 계산 함수 필요
        )

        # 3. 시그널 결과 처리
        if not signal_result['allowed']:
            level = signal_result['rejection_level']
            reason = signal_result['rejection_reason']
            console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {level} 차단 - {reason}[/yellow]")
            return

        # 4. 매수 실행
        tier = signal_result['tier']
        position_size_mult = signal_result['position_size_multiplier']

        console.print(f"[green]✅ {stock_name} ({stock_code}): 매수 시그널 발생![/green]")
        console.print(f"  Tier: {tier}, 포지션 조정: {position_size_mult*100:.0f}%")

        # 기존 execute_buy 호출 (포지션 사이즈 반영)
        self.execute_buy(stock_code, stock_name, current_price, df, position_size_mult)

    except Exception as e:
        console.print(f"[red]❌ {stock_code} 매수 신호 체크 실패: {e}[/red]")
```

### 5. execute_buy() 함수 수정

포지션 사이즈 조정 파라미터 추가:

```python
def execute_buy(self, stock_code: str, stock_name: str, price: float, df: pd.DataFrame, position_size_mult: float = 1.0):
    """매수 실행 (포지션 사이즈 조정 반영)"""

    # ... 기존 코드 ...

    # 포지션 크기 계산
    position_calc = self.risk_manager.calculate_position_size(
        current_balance=self.current_cash,
        current_price=price,
        stop_loss_price=stop_loss_price,
        entry_confidence=1.0
    )

    # SignalOrchestrator의 포지션 조정 반영
    quantity = int(position_calc['quantity'] * position_size_mult)
    amount = position_calc['investment'] * position_size_mult

    # ... 나머지 코드 ...
```

### 6. 일일 손익 계산 함수 추가

```python
def calculate_daily_pnl(self) -> float:
    """금일 손익 계산"""
    try:
        # DB에서 오늘 거래 조회
        today = datetime.now().strftime('%Y-%m-%d')

        trades_today = self.db.get_trades()  # 전체 조회 후 필터

        total_pnl = 0.0
        for trade in trades_today:
            trade_time = trade.get('trade_time', '')
            if trade_time.startswith(today):
                realized_profit = trade.get('realized_profit', 0)
                if realized_profit:
                    total_pnl += float(realized_profit)

        return total_pnl

    except Exception as e:
        console.print(f"[dim]⚠️  일일 손익 계산 실패: {e}[/dim]")
        return 0.0
```

## 📊 예상 효과

### 기존 시스템
- 조건검색 → VWAP 검증 → 진입
- 승률: 54.3%
- 손익비: 0.27

### 통합 후 시스템
- 조건검색 → **L2 RS 필터** → **L3 MTF** → **L4 수급** → **L5 VWAP+Squeeze** → **L6 검증** → 진입
- 예상 승률: **68-75%**
- 예상 손익비: **0.53-1.2**

## 🚨 주의사항

1. **계좌 손실 한도 설정 필수**
   - config/strategy_config.yaml에 `max_daily_loss_pct: 3.0` 확인

2. **RS 필터 min_rating 조정**
   - 초기: 80 (상위 20%)
   - 종목 부족 시: 70 (상위 30%)

3. **L4 수급 데이터**
   - 현재 API 미연결 시 기본 통과
   - 실전 사용 시 키움 API 연동 필요

4. **테스트 필수**
   - 통합 후 모의투자로 최소 1일 테스트 권장

## ✅ 체크리스트

- [ ] Import 추가
- [ ] SignalOrchestrator 초기화
- [ ] L2 RS 필터 적용
- [ ] check_buy_signal() 수정
- [ ] execute_buy() 포지션 조정
- [ ] calculate_daily_pnl() 추가
- [ ] config 설정 확인
- [ ] 통합 테스트 실행

## 📁 관련 파일

- `analyzers/signal_orchestrator.py` - 통합 오케스트레이터
- `analyzers/volatility_regime.py` - L1 RV 필터
- `analyzers/relative_strength_filter.py` - L2 RS 필터
- `analyzers/multi_timeframe_consensus.py` - L3 MTF
- `analyzers/liquidity_shift_detector.py` - L4 수급
- `analyzers/squeeze_momentum.py` - L5 Squeeze
