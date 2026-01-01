# TradeStateManager 통합 가이드

**작성일**: 2025-12-23
**목적**: TradeStateManager를 main_auto_trading.py에 통합

---

## 📋 통합 목표

### 해결할 문제
1. ✅ 같은 종목 당일 중복 진입
2. ✅ 손절 후 재매수
3. ✅ Bottom 무효화 후 재진입
4. ✅ 성과 데이터 왜곡
5. ✅ Pending 진입 관리 (즉시 매수 리스크 제거)

---

## 🔧 통합 방법

### 1단계: Import 및 초기화

#### `main_auto_trading.py` 수정

```python
# 파일 상단 import 추가
from trading.trade_state_manager import (
    TradeStateManager,
    TradeAction,
    InvalidationReason
)

# IntegratedTradingSystem.__init__() 수정
class IntegratedTradingSystem:
    def __init__(self, condition_indices, live_mode=False, skip_wait=False):
        # ... 기존 코드 ...

        # ✅ TradeStateManager 초기화 추가
        self.state_manager = TradeStateManager()

        console.print("[green]✓ TradeStateManager 초기화 완료[/green]")
```

---

### 2단계: 진입 전 체크

#### 조건검색 후 진입 체크 수정

```python
async def check_entry_signal(self, stock_code: str, kiwoom_df=None):
    """매수 진입 체크"""

    # 기본 정보 가져오기
    stock_info = self.validated_stocks.get(stock_code, {})
    stock_name = stock_info.get('name', stock_code)
    strategy_tag = stock_info.get('strategy', 'momentum')

    # ✅ 1. TradeStateManager 체크 추가
    can_enter, reason = self.state_manager.can_enter(
        stock_code=stock_code,
        strategy_tag=strategy_tag,
        check_stoploss=True,       # 손절 종목 체크
        check_invalidated=True,    # 무효화 신호 체크
        check_traded=True          # 당일 거래 체크
    )

    if not can_enter:
        console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {reason}[/yellow]")
        return

    # ✅ 2. Pending 진입 체크 (Momentum 전략만)
    if strategy_tag.startswith("momentum"):
        # 이미 Pending 대기 중인지 체크
        if self.state_manager.is_pending(stock_code):
            # Pending 확인 업데이트
            ready, pending_reason = await self._check_pending_conditions(stock_code, kiwoom_df)
            if not ready:
                console.print(f"[dim]{stock_name} ({stock_code}): {pending_reason}[/dim]")
                return
            # Pending 확정 → 진입
            self.state_manager.remove_pending(stock_code, "진입 확정")
        else:
            # 새 신호 → Pending 등록
            current_price = self._get_current_price(stock_code, kiwoom_df)
            self.state_manager.add_pending_entry(
                stock_code=stock_code,
                stock_name=stock_name,
                strategy_tag=strategy_tag,
                signal_price=current_price,
                required_confirmations=2  # 2캔들 확인 필요
            )
            console.print(f"[yellow]⏳ Pending 등록: {stock_name} - 확인 대기[/yellow]")
            return  # 진입하지 않고 리턴

    # ✅ 3. Bottom 전략은 기존대로 즉시 진입 (Pullback 이미 확인됨)
    # ... 기존 매수 로직 ...

    # ✅ 4. 매수 실행 후 기록
    if buy_success:
        self.state_manager.mark_traded(
            stock_code=stock_code,
            stock_name=stock_name,
            action=TradeAction.BUY,
            price=buy_price,
            quantity=quantity,
            strategy_tag=strategy_tag,
            reason="진입 조건 충족"
        )
```

---

### 3단계: Pending 확인 로직

#### 새 메서드 추가

```python
async def _check_pending_conditions(
    self,
    stock_code: str,
    kiwoom_df
) -> Tuple[bool, str]:
    """
    Pending 진입 조건 확인

    조건:
    1. 가격 유지: 신호가 대비 -1% 이내
    2. 거래량 유지: 직전 5봉 평균 이상
    3. VWAP 상단 유지

    Returns:
        (ready, reason)
    """
    pending = self.state_manager.get_pending_info(stock_code)
    if not pending:
        return False, "Pending 정보 없음"

    # 현재 데이터 가져오기
    if kiwoom_df is None or len(kiwoom_df) == 0:
        kiwoom_df = await self.get_stock_data(stock_code)

    if kiwoom_df is None or len(kiwoom_df) == 0:
        return False, "데이터 없음"

    current_price = kiwoom_df['close'].iloc[-1]
    current_vwap = kiwoom_df['vwap'].iloc[-1] if 'vwap' in kiwoom_df.columns else 0
    current_volume = kiwoom_df['volume'].iloc[-1]

    # 직전 5봉 평균 거래량
    avg_volume_5 = kiwoom_df['volume'].iloc[-6:-1].mean() if len(kiwoom_df) >= 6 else 0

    # 조건 체크
    conditions_met = {}

    # 1. 가격 유지 (-1% 이내)
    price_change_pct = ((current_price - pending.signal_price) / pending.signal_price) * 100
    conditions_met['price_maintained'] = price_change_pct >= -1.0

    # 2. 거래량 유지
    conditions_met['volume_confirmed'] = current_volume >= avg_volume_5 if avg_volume_5 > 0 else False

    # 3. VWAP 상단 유지
    conditions_met['vwap_above'] = current_price > current_vwap if current_vwap > 0 else False

    # Pending 확인 업데이트
    return self.state_manager.update_pending_confirmation(
        stock_code=stock_code,
        conditions_met=conditions_met
    )
```

---

### 4단계: 청산 시 기록

#### 매도 로직 수정

```python
async def execute_sell(
    self,
    stock_code: str,
    quantity: int,
    reason: str,
    is_stoploss: bool = False
):
    """매도 실행"""

    position = self.positions.get(stock_code)
    if not position:
        return False

    stock_name = position['name']
    entry_price = position['entry_price']

    # 매도 실행
    sell_result = await self.kiwoom.sell_order(
        stock_code=stock_code,
        quantity=quantity,
        price=0  # 시장가
    )

    if sell_result['success']:
        sell_price = sell_result['price']

        # ✅ 손절 기록
        if is_stoploss:
            self.state_manager.mark_stoploss(
                stock_code=stock_code,
                stock_name=stock_name,
                entry_price=entry_price,
                exit_price=sell_price,
                reason=reason
            )
        else:
            # ✅ 일반 매도 기록
            action = TradeAction.PARTIAL_SELL if quantity < position['quantity'] else TradeAction.SELL
            self.state_manager.mark_traded(
                stock_code=stock_code,
                stock_name=stock_name,
                action=action,
                price=sell_price,
                quantity=quantity,
                strategy_tag=position.get('strategy_tag', 'unknown'),
                reason=reason
            )

        return True

    return False
```

---

### 5단계: Bottom 무효화 연동

#### `bottom_pullback_manager.py` 수정

```python
class BottomPullbackManager:
    def __init__(self, config: dict, state_manager=None):
        self.config = config
        self.pullback_config = config.get('pullback', {})
        self.signals = {}
        self.current_date = datetime.now().date()

        # ✅ StateManager 연동
        self.state_manager = state_manager

        console.print("[dim]✓ BottomPullbackManager 초기화 완료[/dim]")

    def _invalidate_signal(self, stock_code: str, reason: str):
        """신호 무효화"""
        if stock_code in self.signals:
            signal = self.signals[stock_code]
            signal['state'] = 'INVALIDATED'
            signal['invalidation_reason'] = reason

            # ✅ StateManager에 무효화 기록
            if self.state_manager:
                # 무효화 사유 매핑
                reason_map = {
                    "신호봉 저가 이탈": InvalidationReason.SIGNAL_LOW_BREAK,
                    "시간 초과": InvalidationReason.TIME_EXPIRED,
                    "진입 시간대 이탈": InvalidationReason.TIME_WINDOW_EXIT,
                }

                invalidation_reason = reason_map.get(
                    reason.split('(')[0].strip(),
                    InvalidationReason.MANUAL
                )

                self.state_manager.mark_invalidated(
                    stock_code=stock_code,
                    stock_name=signal['stock_name'],
                    strategy_tag='bottom_pullback',
                    reason=invalidation_reason,
                    signal_price=signal['signal_price'],
                    invalidation_price=signal.get('current_price', 0)
                )

            console.print(
                f"[red]❌ {signal['stock_name']} ({stock_code}): "
                f"신호 무효화 - {reason}[/red]"
            )
```

---

### 6단계: 최고 수익률 추적

#### 실시간 모니터링 루프 수정

```python
async def real_time_monitoring(self):
    """실시간 모니터링"""

    while self.running and self.is_market_open():
        # ... 기존 코드 ...

        # 포지션 체크
        for stock_code, position in list(self.positions.items()):
            # 현재 수익률 계산
            current_profit_pct = self._calculate_profit_pct(position)

            # ✅ 최고 수익률 업데이트
            self.state_manager.update_max_profit(stock_code, current_profit_pct)

            # ... 청산 조건 체크 ...

        # ✅ Pending 진입 만료 정리 (30분)
        self.state_manager.cleanup_expired_pending(timeout_minutes=30)

        await asyncio.sleep(60)
```

---

### 7단계: 일일 리셋 연동

#### `daily_routine()` 수정

```python
async def daily_routine(self):
    """일일 루틴"""

    console.print("=" * 120, style="bold yellow")
    console.print(f"{'📅 일일 자동매매 루틴 시작':^120}", style="bold yellow")
    console.print("=" * 120, style="bold yellow")

    # ✅ TradeStateManager 리셋 (자동)
    # state_manager._check_and_reset_daily()가 자동 호출됨

    # ... 기존 루틴 ...

    # 루틴 종료 전 통계 출력
    self.state_manager.print_summary()
```

---

## 📊 사용 예제

### 예제 1: Momentum 전략 (Pending 진입)

```python
# 조건검색 신호 발생
09:15  조건 17번 신호: 삼성전자
       → can_enter() 체크 ✅
       → is_pending() = False
       → add_pending_entry() 호출
       → "⏳ Pending 등록: 삼성전자 - 확인 대기"

09:16  실시간 모니터링
       → _check_pending_conditions() 호출
       → 가격 유지 ✅, 거래량 OK ✅, VWAP 상단 ✅
       → confirmations = 1/2

09:17  실시간 모니터링
       → _check_pending_conditions() 호출
       → 모든 조건 충족 ✅
       → confirmations = 2/2
       → "✅ Pending 진입 확정"
       → 매수 실행
       → mark_traded() 호출
```

---

### 예제 2: 손절 후 재진입 방지

```python
10:00  삼성전자 손절
       → mark_stoploss() 호출
       → stoploss_today['005930'] = {...}
       → "🛑 손절 기록: 삼성전자"

10:30  조건 18번 신호: 삼성전자 (다시 발생)
       → can_enter() 체크
       → is_stoploss_today('005930') = True
       → return (False, "손절 종목")
       → "⚠️  삼성전자: 손절 종목 (65,000원에서 -2.5%)"
       → 진입 차단 ✅
```

---

### 예제 3: Bottom 무효화 후 재진입 방지

```python
11:00  조건 23번 신호: 오름테라퓨틱
       → Bottom Manager 신호 등록

11:30  신호봉 저가 이탈
       → bottom_manager._invalidate_signal() 호출
       → state_manager.mark_invalidated() 호출
       → "⚠️  신호 무효화: 오름테라퓨틱"

14:00  조건 23번 신호: 오름테라퓨틱 (다시 발생)
       → can_enter() 체크
       → is_invalidated('475830') = True
       → return (False, "무효화된 신호 (신호봉 저가 이탈)")
       → 진입 차단 ✅
```

---

### 예제 4: 일일 진입 제한

```python
# Bottom 전략: 1회 제한
09:30  조건 23번: A사 → 매수 성공
       → mark_traded(..., strategy_tag='bottom_pullback')
       → buy_count = 1

14:00  조건 23번: A사 (다시 발생)
       → can_enter(..., strategy_tag='bottom_pullback')
       → buy_count = 1 >= 1
       → return (False, "Bottom 전략 당일 진입 제한")
       → 진입 차단 ✅

# Momentum 전략: 2회 제한
10:00  조건 17번: B사 → 매수 성공 (1회)
11:00  조건 18번: B사 → 매수 성공 (2회)
13:00  조건 19번: B사 (3번째 신호)
       → can_enter(..., strategy_tag='momentum')
       → buy_count = 2 >= 2
       → return (False, "Momentum 전략 당일 진입 제한")
       → 진입 차단 ✅
```

---

## 🎯 통합 체크리스트

### 필수 수정

- [ ] `main_auto_trading.py` import 추가
- [ ] `__init__()` 에 `state_manager` 초기화
- [ ] `check_entry_signal()` 에 `can_enter()` 체크 추가
- [ ] `_check_pending_conditions()` 메서드 추가
- [ ] `execute_sell()` 에 `mark_traded()` / `mark_stoploss()` 추가
- [ ] `bottom_pullback_manager.py` 에 `state_manager` 연동
- [ ] `real_time_monitoring()` 에 최고 수익률 추적 추가

### 선택 수정

- [ ] 일일 통계 출력 추가
- [ ] Pending 만료 시간 조정 (기본 30분)
- [ ] 전략별 진입 제한 조정

---

## 📈 기대 효과

### Before
- ❌ 손절 후 재매수 → 추가 손실
- ❌ Bottom 무효화 후 재진입 → 리스크
- ❌ 같은 종목 중복 진입 → 과도한 노출
- ❌ 즉시 매수 → 허위 신호 진입

### After
- ✅ 손절 종목 당일 차단
- ✅ 무효화 신호 재진입 방지
- ✅ 전략별 일일 진입 제한
- ✅ Pending 진입으로 신호 검증

---

## 참고 문서

- `trading/trade_state_manager.py` - 핵심 구현
- `TRADING_SYSTEM_OVERVIEW.md` - 시스템 전체 구조
- `BOTTOM_PULLBACK_STRATEGY.md` - Bottom 전략
