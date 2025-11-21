# 최적화된 청산 로직 통합 가이드

## 📋 개요

데이터 분석 기반으로 최적화된 청산 로직을 기존 `main_auto_trading.py`에 통합하는 방법입니다.

**목표**: 손익비 0.27 → 1.2+ 개선

## 🔧 통합 방법

### 1단계: Config 파일 교체

```bash
# 기존 config 백업
cp config/strategy_config.yaml config/strategy_config.yaml.backup

# 새 config 적용
cp config/strategy_config_optimized.yaml config/strategy_config.yaml
```

### 2단계: main_auto_trading.py 수정

#### A. Import 추가 (파일 상단)

```python
# Line ~15 근처에 추가
from trading.exit_logic_optimized import OptimizedExitLogic
```

#### B. __init__() 메서드에서 OptimizedExitLogic 초기화

```python
# Line ~800 근처 (AutoTradingSystem.__init__)

def __init__(self, config_path: str = "config/strategy_config.yaml"):
    # 기존 코드...

    self.config = StrategyConfig(config_path)

    # 최적화된 청산 로직 초기화 (NEW)
    self.exit_logic = OptimizedExitLogic(self.config.config)

    # 나머지 기존 코드...
```

#### C. check_exit_signal() 메서드 교체

```python
# Line 2096-2265 전체 교체

def check_exit_signal(self, stock_code: str, kiwoom_df: pd.DataFrame = None):
    """매도 신호 체크 (최적화 버전)"""
    try:
        console.print(f"[dim]🔍 {stock_code}: 매도 신호 체크 시작[/dim]")

        position = self.positions.get(stock_code)
        if not position:
            console.print(f"[yellow]⚠️  {stock_code}: 포지션 정보 없음[/yellow]")
            return

        # 기본값 설정
        position.setdefault('entry_price', position.get('avg_price', 0))
        position.setdefault('highest_price', position['entry_price'])
        position.setdefault('trailing_active', False)
        position.setdefault('trailing_stop_price', None)
        position.setdefault('partial_exit_stage', 0)

        # 1순위: 키움 API 데이터 사용
        if kiwoom_df is not None and len(kiwoom_df) >= 50:
            console.print(f"[dim]  ✓ {stock_code}: 키움 데이터 사용 ({len(kiwoom_df)}봉)[/dim]")
            df = kiwoom_df.copy()
        else:
            # 2순위: Yahoo Finance
            market = None
            if stock_code in self.validated_stocks:
                market = self.validated_stocks[stock_code].get('market')

            if not market:
                market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'

            ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
            ticker = f"{stock_code}{ticker_suffix}"

            console.print(f"[dim]  📊 {stock_code}: Yahoo 데이터 조회 중 ({ticker})...[/dim]")
            df = download_stock_data_sync(ticker, days=1)

            if df is None or len(df) < 50:
                console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족[/yellow]")
                return

        # VWAP 및 지표 계산
        vwap_config = self.config.get_section('vwap')
        df = self.analyzer.calculate_vwap(df,
                                           use_rolling=vwap_config.get('use_rolling', True),
                                           rolling_window=vwap_config.get('rolling_window', 20))
        df = self.analyzer.calculate_atr(df)

        signal_config = self.config.get_signal_generation_config()
        df = self.analyzer.generate_signals(df, **signal_config)

        # 현재가 추출
        current_price = df['close'].iloc[-1]

        # 음수 가격 검증
        if current_price <= 0:
            console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price}[/red]")
            return

        # 수익률 계산 (로깅용)
        entry_price = position.get('entry_price', 0)
        if entry_price > 0:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            console.print(f"[dim]  💰 {stock_code}: 현재가 {current_price:,.0f}원, "
                         f"진입가 {entry_price:,.0f}원, 수익률 {profit_pct:+.2f}%[/dim]")

        # ========================================
        # 🚀 최적화된 청산 로직 호출
        # ========================================
        should_exit, exit_reason, additional_info = self.exit_logic.check_exit_signal(
            position=position,
            current_price=current_price,
            df=df
        )

        # 부분 청산 처리
        if additional_info and additional_info.get('partial_exit'):
            stage = additional_info['stage']
            exit_ratio = additional_info['exit_ratio']
            profit_pct = additional_info['profit_pct']

            console.print(f"[yellow]📊 부분 청산 {stage}차 발동 (+{profit_pct:.2f}%)[/yellow]")

            self.execute_partial_sell(
                stock_code=stock_code,
                price=current_price,
                profit_pct=profit_pct,
                exit_ratio=exit_ratio,
                stage=stage
            )
            return

        # 전량 매도 처리
        if should_exit:
            console.print(f"[yellow]🔔 매도 신호: {exit_reason}[/yellow]")

            # Emergency Hard Stop 시 플래그 전달
            use_market_order = additional_info and additional_info.get('use_market_order', False)

            profit_pct = additional_info.get('profit_pct', 0) if additional_info else 0

            self.execute_sell(
                stock_code=stock_code,
                current_price=current_price,
                profit_pct=profit_pct,
                reason=exit_reason,
                use_market_order=use_market_order
            )

    except Exception as e:
        console.print(f"[red]❌ {stock_code} 매도 신호 체크 실패: {e}[/red]")
        import traceback
        console.print(f"[red]{traceback.format_exc()}[/red]")
```

#### D. execute_sell() 메서드에 시장가 옵션 추가

```python
# Line 2526 근처 - execute_sell 함수 시그니처 수정

def execute_sell(
    self,
    stock_code: str,
    current_price: float,
    profit_pct: float,
    reason: str,
    use_market_order: bool = False  # NEW 파라미터
):
    """매도 실행 (시장가 옵션 추가)"""

    position = self.positions.get(stock_code)
    if not position:
        return

    # entry_time 안전 처리
    entry_time = position.get('entry_time') or position.get('entry_date')
    if entry_time:
        holding_duration = (datetime.now() - entry_time).seconds
    else:
        holding_duration = 0

    realized_profit = (current_price - position['entry_price']) * position['quantity']

    console.print()
    console.print("=" * 80, style="red")
    console.print(f"🔔 매도 신호 발생: {position['name']} ({stock_code})", style="bold red")
    console.print(f"   매수가: {position['entry_price']:,.0f}원")
    console.print(f"   매도가: {current_price:,.0f}원")
    console.print(f"   수익률: {profit_pct:+.2f}%")
    console.print(f"   실현손익: {realized_profit:+,.0f}원")
    console.print(f"   사유: {reason}")
    console.print(f"   보유시간: {holding_duration // 60}분")

    # 🔥 시장가 주문 여부 표시
    if use_market_order:
        console.print(f"   [bold red]⚠️  시장가 강제청산 모드[/bold red]")

    # DB에 매도 정보 저장
    trade_id = position.get('trade_id')
    if trade_id:
        sell_trade = {
            'stock_code': stock_code,
            'stock_name': position['name'],
            'trade_type': 'SELL',
            'trade_time': datetime.now().isoformat(),
            'price': float(current_price),
            'quantity': int(position['quantity']),
            'amount': float(current_price * position['quantity']),
            'exit_reason': reason,
            'realized_profit': float(realized_profit),
            'profit_rate': float(profit_pct),
            'holding_duration': int(holding_duration)
        }
        self.db.insert_trade(sell_trade)

    # 키움 API 매도 주문
    try:
        console.print(f"[yellow]📡 키움 API 매도 주문 전송 중...[/yellow]")

        # 🔥 시장가 vs 지정가 선택
        if use_market_order:
            # Hard Stop: 시장가 주문
            order_result = self.api.order_sell(
                stock_code=stock_code,
                quantity=position['quantity'],
                price=0,  # 시장가
                trade_type="3"  # 시장가 (키움: 03)
            )
        else:
            # 일반 청산: 지정가 주문 (현재가로)
            order_result = self.api.order_sell(
                stock_code=stock_code,
                quantity=position['quantity'],
                price=int(current_price),
                trade_type="0"  # 지정가
            )

        if order_result.get('return_code') != 0:
            console.print(f"[red]❌ 매도 주문 실패: {order_result.get('return_msg')}[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            return

        order_no = order_result.get('ord_no')
        console.print(f"[green]✓ 매도 주문 성공 - 주문번호: {order_no}[/green]")

    except Exception as e:
        console.print(f"[red]❌ 매도 API 호출 실패: {e}[/red]")
        console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
        return

    # 리스크 관리자에 거래 기록
    self.risk_manager.record_trade(
        stock_code=stock_code,
        stock_name=position['name'],
        trade_type='SELL',
        quantity=position['quantity'],
        price=current_price,
        realized_pnl=realized_profit
    )

    # 포지션 제거
    del self.positions[stock_code]

    console.print(f"✅ 매도 완료 (주문번호: {order_no})")
    console.print("=" * 80, style="red")
    console.print()
```

### 3단계: 테스트

```bash
# 구문 오류 체크
python3 -m py_compile main_auto_trading.py

# 실행 테스트 (모의투자 계좌로)
python3 main_auto_trading.py
```

## 📊 예상 개선 효과

| 지표 | Before | After (예상) |
|------|--------|-------------|
| 승률 | 54.3% | 50~55% |
| 평균 수익 | +0.56% | +1.2~1.5% |
| 평균 손실 | -2.06% | -1.0~-1.2% |
| 손익비 | 0.27 | 1.0~1.5 |
| 15:00 강제청산 | 71.4% | 30% 이하 |

## 🐛 버그 수정 내역

1. ✅ entry_price 바이너리 데이터 → `_safe_get_price()` 메서드로 안전 추출
2. ✅ 시장가 매도 미작동 → `use_market_order` 플래그 추가
3. ✅ 시간 비교 문자열 버그 → `datetime.time()` 객체로 변경
4. ✅ DataFrame 'signal' 컬럼 미존재 → `if 'signal' in df.columns` 체크
5. ✅ highest_price 메모리 유실 → 포지션 dict에 안전 저장

## ⚠️ 주의사항

1. **반드시 백업 후 작업**
   ```bash
   cp main_auto_trading.py main_auto_trading.py.backup_$(date +%Y%m%d_%H%M%S)
   ```

2. **모의투자로 먼저 검증**
   - 최소 1일 운영 후 실전 적용

3. **Config 설정 확인**
   - `strategy_config_optimized.yaml` 설정 값 확인
   - 필요시 파라미터 조정

## 🚀 다음 단계

1. ✅ Config 교체
2. ✅ 코드 통합
3. ⏳ 테스트 실행
4. ⏳ 모의투자 검증 (1일)
5. ⏳ 실전 적용
6. ⏳ 성과 모니터링

---

**작성일**: 2025-11-15
**버전**: v1.0
**작성자**: Claude Code Assistant
