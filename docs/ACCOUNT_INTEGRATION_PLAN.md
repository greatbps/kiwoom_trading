# 실계좌 연동 개선 플랜

## 현재 문제점

### 1. 하드코딩된 초기 잔고
```python
initial_balance = 10000000  # 고정값!
RiskManager(initial_balance=10000000)
```

### 2. 실제 계좌 미연동
- `get_balance()`, `get_account_info()` API 미사용
- 시스템 시작 시 계좌 정보 조회 없음

### 3. 가상 잔고 기반 리스크 관리
- 실제 보유 현금과 무관
- 보유 종목 평가액 미반영

---

## 개선 방안

### Phase 1: 계좌 정보 조회 기능 추가

```python
async def initialize_account(self):
    """시스템 시작 시 계좌 정보 초기화"""
    
    # 1. 계좌 기본 정보 조회
    account_info = self.api.get_account_info()
    
    # 2. 계좌 잔고 조회
    balance_info = self.api.get_balance()
    
    # 3. 정보 출력
    console.print("\n[계좌 정보]")
    console.print(f"  계좌번호: {self.api.account_number}")
    console.print(f"  예수금: {balance_info['cash']:,}원")
    console.print(f"  총평가: {balance_info['total_value']:,}원")
    console.print(f"  보유종목: {len(balance_info['positions'])}개")
    
    # 4. 리스크 관리자 초기화 (실제 잔고 기반)
    self.risk_manager = RiskManager(
        initial_balance=balance_info['cash']
    )
    
    # 5. 보유 포지션 로드
    for pos in balance_info['positions']:
        self.positions[pos['stock_code']] = {
            'quantity': pos['quantity'],
            'avg_price': pos['avg_price'],
            'current_price': pos['current_price'],
            'profit_rate': pos['profit_rate']
        }
```

### Phase 2: 실시간 잔고 업데이트

```python
async def update_account_balance(self):
    """거래 후 실시간 잔고 업데이트"""
    
    balance_info = self.api.get_balance()
    
    # 현금 업데이트
    self.current_cash = balance_info['cash']
    
    # 총 자산 업데이트
    self.total_assets = balance_info['total_value']
    
    # 리스크 관리자에 반영
    self.risk_manager.update_balance(self.current_cash)
```

### Phase 3: 포지션 크기 동적 계산

```python
def calculate_order_size(self, stock_code: str, current_price: float, 
                         stop_loss_price: float):
    """실제 잔고 기반 주문 수량 계산"""
    
    # 1. 현재 잔고 조회
    balance = self.api.get_balance()
    current_cash = balance['cash']
    current_positions_value = balance['positions_value']
    
    # 2. 리스크 관리자로 포지션 크기 계산
    position_calc = self.risk_manager.calculate_position_size(
        current_balance=current_cash,
        current_price=current_price,
        stop_loss_price=stop_loss_price
    )
    
    # 3. 진입 가능 여부 확인
    can_enter, reason = self.risk_manager.can_open_position(
        current_balance=current_cash,
        current_positions_value=current_positions_value,
        position_count=len(self.positions),
        position_size=position_calc['investment']
    )
    
    if not can_enter:
        console.print(f"[yellow]⚠️  진입 불가: {reason}[/yellow]")
        return None
    
    return position_calc['quantity']
```

---

## 수정 파일 목록

### 1. main_auto_trading.py
- [x] `initialize_account()` 메서드 추가
- [ ] `update_account_balance()` 메서드 추가
- [ ] 매수/매도 후 잔고 갱신 로직 추가

### 2. core/risk_manager.py
- [x] 이미 `calculate_position_size()` 구현됨
- [x] 이미 `can_open_position()` 구현됨
- [ ] `update_balance()` 메서드 추가 필요

### 3. kiwoom_api.py
- [x] `get_account_info()` 구현 완료
- [x] `get_balance()` 구현 완료
- [ ] 응답 포맷 표준화 필요

---

## 실행 플로우 (개선 후)

```
[시스템 시작]
1. 토큰 발급 ✓
2. WebSocket 연결 ✓
3. 계좌 정보 조회 ← NEW
   - 예수금: 10,234,567원
   - 보유종목: 2개 (평가액: 1,500,000원)
4. 리스크 관리자 초기화 (실제 잔고)

[조건검색 → 매수 신호]
1. 종목 발견: 005930
2. VWAP 검증 통과
3. 계좌 잔고 조회 ← NEW
4. 포지션 크기 계산 (리스크 2%)
   - 가용 현금: 10,234,567원
   - 리스크: 204,691원
   - 매수 금액: 약 300만원 (30% 한도)
5. 매수 주문
6. 잔고 업데이트 ← NEW

[매도 신호]
1. 트레일링 스탑 도달
2. 매도 주문
3. 잔고 업데이트 ← NEW
4. 리스크 관리자 손익 반영
```

---

## 예상 효과

### Before (현재)
- 가상 잔고 1000만원 고정
- 실제 계좌와 불일치
- 리스크 관리 부정확

### After (개선 후)
- 실시간 계좌 연동
- 정확한 리스크 관리
- 포지션 크기 동적 조정
- 계좌 한도 자동 준수

---

## 다음 단계

1. ✅ 계획 수립 완료
2. ⏳ `initialize_account()` 구현
3. ⏳ `update_account_balance()` 구현
4. ⏳ 통합 테스트
5. ⏳ 월요일 실전 투입

**작성일:** 2025-10-26  
**우선순위:** 🔴 HIGH (월요일 실전 전 필수)
