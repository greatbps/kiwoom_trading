# Phase 3 개선사항 완료

## 개요

Phase 3에서는 다음 두 가지 핵심 기능을 구현했습니다:

1. **EMA + Volume Breakdown 매도 신호 구현** (5단계)
2. **실제 Kiwoom 주문 API 통합** (매수/매도/정정/취소)

## 1. EMA + Volume Breakdown 매도 신호

### 구현 위치
- `analyzers/technical_analyzer.py`: `detect_ema_breakdown()` 메서드
- `core/order_executor.py`: `check_exit_signals()` 5단계 로직

### 감지 로직

```python
def detect_ema_breakdown(df: pd.DataFrame, ema_period: int = 20):
    """
    EMA + Volume Breakdown 감지

    추세 전환 조건:
    1. 가격이 EMA 아래로 이탈
    2. 거래량 급증 (평균 대비 1.5배 이상)
    3. 연속 2개 캔들 하락
    """
```

### 판정 기준

| 조건 | 신뢰도 | 매도 판단 |
|------|--------|-----------|
| EMA 하향 돌파 + 거래량 급증 + 연속 하락 | **HIGH** | 즉시 전량 매도 |
| EMA 하향 돌파 + (거래량 급증 OR 연속 하락) | **MEDIUM** | 손실 상태면 매도 |
| EMA 대비 -2% 이상 이탈 + 거래량 급증 | **MEDIUM** | 손실 상태면 매도 |

### 6단계 매도 전략 (최종 완성)

```python
# 1단계: Hard Stop (-3%) → 전량 매도
# 2단계: 1차 익절 (+4%) → 40% 매도
# 3단계: 2차 익절 (+6%) → 40% 매도 + Trailing 활성화
# 4단계: ATR Trailing Stop → 나머지 20%
# 5단계: EMA + Volume Breakdown → 추세 전환 감지 매도 ✅ 신규 추가
# 6단계: Time Filter (15:00) → 강제 청산
```

### 5단계 매도 로직 (OrderExecutor)

```python
if chart_data and len(chart_data) > 0:
    from analyzers.technical_analyzer import TechnicalAnalyzer
    analyzer = TechnicalAnalyzer()

    df = analyzer.prepare_dataframe(chart_data)
    breakdown = analyzer.detect_ema_breakdown(df, ema_period=20)

    if breakdown['breakdown_detected']:
        confidence = breakdown['confidence']

        # HIGH 신뢰도면 즉시 매도
        if confidence == 'HIGH':
            return position.remaining_quantity, f"5단계: EMA Breakdown (HIGH) - {reason}"

        # MEDIUM 신뢰도 + 손실 상태면 매도
        elif confidence == 'MEDIUM' and profit_rate < 0:
            return position.remaining_quantity, f"5단계: EMA Breakdown (MEDIUM) - {reason}"
```

### 테스트 결과

```bash
python test/test_phase3_improvements.py --mode ema
```

**결과:**
```
📊 삼성전자 (005930)
  ✓ 차트 데이터: 600일
  [EMA Breakdown 분석 결과]
    Breakdown 감지: ❌ NO
    신뢰도: NONE
    사유: 정상 (Breakdown 없음)
```

모든 종목에서 정상적으로 Breakdown 감지 기능이 작동하며, 현재는 추세 전환 신호가 없는 상태입니다.

## 2. 실제 Kiwoom 주문 API 통합

### 구현된 API 메서드

#### kiwoom_api.py

| 메서드 | API ID | 기능 | 파라미터 |
|--------|--------|------|----------|
| `order_buy()` | kt10000 | 매수 주문 | stock_code, quantity, price, trade_type |
| `order_sell()` | kt10001 | 매도 주문 | stock_code, quantity, price, trade_type |
| `order_modify()` | kt10002 | 정정 주문 | orig_ord_no, stock_code, quantity, price |
| `order_cancel()` | kt10003 | 취소 주문 | orig_ord_no, stock_code, quantity |

#### 매매 구분 (trade_type)

| 코드 | 설명 | 용도 |
|------|------|------|
| 0 | 보통(지정가) | 기본 주문 |
| 3 | 시장가 | 즉시 체결 |
| 5 | 조건부지정가 | - |
| 6 | 최유리지정가 | 빠른 체결 |
| 7 | 최우선지정가 | - |
| 10 | 보통(IOC) | 즉시 체결 또는 취소 |
| 13 | 시장가(IOC) | - |

### OrderExecutor 통합

#### 매수 주문 (execute_buy)

```python
# 실제 Kiwoom API 호출
api_result = self.api.order_buy(
    stock_code=stock_code,
    quantity=quantity,
    price=int(price),
    trade_type="0"  # 지정가
)

# API 응답 확인
if api_result.get('return_code') != 0:
    return OrderResult(success=False, ...)

# 주문번호 추출
order_no = api_result.get('ord_no')

# 포지션 생성 및 관리
position = Position(...)
self.position_manager.add_position(position)
```

#### 매도 주문 (execute_sell)

```python
# 손익 계산
realized_pnl = (price - position.avg_price) * quantity
pnl_rate = (price - position.avg_price) / position.avg_price * 100

# 실제 Kiwoom API 호출
api_result = self.api.order_sell(
    stock_code=stock_code,
    quantity=quantity,
    price=int(price),
    trade_type="0"  # 지정가
)

# 거래 기록
self.risk_manager.record_trade(
    stock_code=stock_code,
    trade_type='SELL',
    quantity=quantity,
    price=price,
    realized_pnl=realized_pnl
)
```

### API 응답 형식

#### 매수 주문 응답
```json
{
    "ord_no": "00024",
    "return_code": 0,
    "return_msg": "정상적으로 처리되었습니다"
}
```

#### 매도 주문 응답
```json
{
    "ord_no": "0000138",
    "dmst_stex_tp": "KRX",
    "return_code": 0,
    "return_msg": "매도주문이 완료되었습니다."
}
```

#### 취소 주문 응답
```json
{
    "ord_no": "0000141",
    "base_orig_ord_no": "0000139",
    "cncl_qty": "000000000001",
    "return_code": 0,
    "return_msg": "매수취소 주문입력이 완료되었습니다"
}
```

### 안전 장치

1. **API 응답 검증**
   ```python
   if api_result.get('return_code') != 0:
       return OrderResult(success=False, message=f"주문 실패: {api_result.get('return_msg')}")
   ```

2. **가격 정수 변환**
   ```python
   price=int(price)  # float → int 변환
   ```

3. **지정가 주문 기본값**
   - 시장가 대신 지정가(trade_type="0") 사용
   - 갑작스러운 가격 변동 방지

4. **토큰 자동 확인**
   ```python
   if not self.access_token:
       self.get_access_token()
   ```

## 테스트

### 1. EMA Breakdown 감지 테스트

```bash
python test/test_phase3_improvements.py --mode ema
```

**특징:**
- 3개 주요 종목 분석
- 실시간 차트 데이터 사용
- Breakdown 신뢰도 분류 (HIGH/MEDIUM/NONE)

### 2. 주문 API Dry Run 테스트

```bash
python test/test_phase3_improvements.py --mode order
```

**주의사항:**
- ⚠️ **실제 주문 API 호출**
- 모의투자 계좌 사용 권장
- 실전 계좌 사용시 극소량(1주)만 테스트
- 사용자 확인 프롬프트 포함

**테스트 순서:**
1. 토큰 발급
2. 현재가 조회
3. 매수 주문 (1주, 지정가) - 사용자 확인 필요
4. 주문 취소 - 사용자 확인 필요

### 3. 통합 테스트

```bash
python test/test_phase3_improvements.py --mode integration
```

**시나리오:**
1. 가상 포지션 설정 (100주 @ 95,000원)
2. EMA Breakdown 감지
3. 매도 판단 로직 실행
4. 권장 매도 수량/가격 출력

## Phase 3 vs Phase 2 비교

| 항목 | Phase 2 | Phase 3 |
|------|---------|---------|
| **5단계 매도** | TODO (미구현) | ✅ EMA + Volume Breakdown |
| **주문 실행** | 시뮬레이션 (print) | ✅ 실제 Kiwoom API |
| **매수 주문** | 가상 | ✅ order_buy() (kt10000) |
| **매도 주문** | 가상 | ✅ order_sell() (kt10001) |
| **주문 정정** | 없음 | ✅ order_modify() (kt10002) |
| **주문 취소** | 없음 | ✅ order_cancel() (kt10003) |
| **Breakdown 감지** | 없음 | ✅ EMA(20) + Volume + 연속 하락 |
| **신뢰도 분류** | 없음 | ✅ HIGH / MEDIUM / NONE |

## 실전 운영 가이드

### 1. 모의투자 먼저 테스트

```python
# kiwoom_api.py 수정
BASE_URL = "https://mockapi.kiwoom.com"  # 모의투자
```

### 2. 실전 전환 전 체크리스트

- [ ] .env 파일 실전 계좌 설정
- [ ] 모의투자에서 최소 1주일 이상 테스트
- [ ] 일일 손실 한도 적절한지 확인 (HARD_MAX_DAILY_LOSS)
- [ ] 포지션 크기 한도 적절한지 확인 (HARD_MAX_POSITION)
- [ ] EMA Breakdown 신뢰도 임계값 조정 필요 여부 확인

### 3. 실전 운영 모니터링

```python
# 자동 매매 실행
from core.auto_trading_handler import AutoTradingHandler

handler = AutoTradingHandler(
    account_no="12345678-01",
    initial_balance=10000000
)
handler.start()
```

**모니터링 항목:**
- 일일 거래 횟수
- 일일 실현 손익
- EMA Breakdown 발생 빈도
- 주문 체결률
- API 오류 발생 여부

## 향후 개선 과제

### 완료 ✅
- [x] EMA + Volume Breakdown 매도 신호
- [x] 실제 Kiwoom 주문 API 통합
- [x] 주문 정정/취소 기능
- [x] Phase 3 테스트 작성

### 예정 (나중에)
- [ ] 알림 시스템 (Slack, Telegram)
- [ ] 웹 대시보드
- [ ] 백테스팅 시스템 (Phase 4)
- [ ] Breakdown 신뢰도 최적화
- [ ] 시장가 vs 지정가 전략 개선
- [ ] 주문 체결 확인 로직

## 파일 목록

### 신규 생성
- `test/test_phase3_improvements.py` - Phase 3 전용 테스트
- `docs/PHASE3_IMPROVEMENTS.md` - 이 문서

### 수정된 파일
- `kiwoom_api.py` - 주문 API 4개 메서드 추가
- `analyzers/technical_analyzer.py` - `detect_ema_breakdown()` 메서드 추가
- `core/order_executor.py` - 실제 API 호출 + 5단계 로직 통합

## 실행 명령어

```bash
# 가상환경 활성화
source venv/bin/activate

# EMA Breakdown 감지 테스트
python test/test_phase3_improvements.py --mode ema

# 통합 테스트 (매도 판단 로직)
python test/test_phase3_improvements.py --mode integration

# 주문 API 테스트 (주의: 실제 주문)
python test/test_phase3_improvements.py --mode order

# 전체 자동 매매 시스템
python test/test_auto_trading.py --mode full
```

## 업데이트 이력

- 2025-10-24: Phase 3 개선사항 완료
  - EMA + Volume Breakdown 매도 신호 구현
  - Kiwoom 주문 API 4종 통합 (매수/매도/정정/취소)
  - 6단계 매도 전략 완성
  - 통합 테스트 성공
