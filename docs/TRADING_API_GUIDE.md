# 키움 REST API 매매 가이드

키움증권 REST API를 활용한 완전한 매매 시스템 구현 가이드입니다.

## 📋 목차

1. [주요 기능](#주요-기능)
2. [API 구조](#api-구조)
3. [설치 및 설정](#설치-및-설정)
4. [기본 사용법](#기본-사용법)
5. [고급 기능](#고급-기능)
6. [실전 매매 예제](#실전-매매-예제)

---

## 🚀 주요 기능

### 1. API 기능 (kiwoom_api.py)

#### 인증
- ✅ `get_access_token()` - 접근 토큰 발급 (au10001)

#### 계좌 조회
- ✅ `get_balance()` - 예수금 상세현황 (kt00001)
- ✅ `get_account_info()` - 일별잔고수익률 (ka01690)
- ✅ `get_account_evaluation()` - 계좌평가현황 (kt00004)

#### 주문 조회
- ✅ `get_unexecuted_orders()` - 미체결 주문 조회 (ka10075)
- ✅ `get_executed_orders()` - 체결 주문 조회 (ka10076)

#### 시세 조회
- ✅ `get_stock_info()` - 주식 기본정보 (ka10001)
- ✅ `get_stock_quote()` - 주식 호가 (ka10004)
- ✅ `get_execution_info()` - 체결정보 (ka10003)
- ✅ `get_daily_chart()` - 일봉 차트 (ka10081)
- ✅ `get_minute_chart()` - 분봉 차트 (ka10080)
- ✅ `get_foreign_investor_trend()` - 외국인 매매 동향 (ka10008)
- ✅ `get_investor_trend()` - 투자자별 매매 동향 (ka10059)

#### 주문 실행
- ✅ `order_buy()` - 매수 주문 (kt10000)
- ✅ `order_sell()` - 매도 주문 (kt10001)
- ✅ `order_modify()` - 정정 주문 (kt10002)
- ✅ `order_cancel()` - 취소 주문 (kt10003)

### 2. 트레이딩 매니저 (trading_manager.py)

#### 포지션 관리
- ✅ `update_positions()` - 보유 포지션 업데이트
- ✅ `update_unexecuted_orders()` - 미체결 주문 업데이트
- ✅ `get_available_cash()` - 주문 가능 현금 조회

#### 자동 매매
- ✅ `buy()` - 자동 수량 계산 매수
- ✅ `sell()` - 보유 종목 매도
- ✅ `cancel_order()` - 주문 취소
- ✅ `cancel_all_orders()` - 미체결 일괄 취소

#### 리스크 관리
- ✅ `check_stop_loss()` - 손절 대상 체크
- ✅ `check_take_profit()` - 익절 대상 체크
- ✅ `execute_stop_loss()` - 손절 자동 실행
- ✅ `execute_take_profit()` - 익절 자동 실행

---

## 📦 API 구조

### 엑셀 문서 구조

`docs/키움api/키움 REST API 문서.xlsx`에는 다음과 같은 시트들이 포함되어 있습니다:

1. **API 리스트** - 전체 API 목록 (200+ APIs)
2. **인증 API** - OAuth 토큰 발급/폐기
3. **국내주식 API** - 종목정보, 시세, 차트, 순위 등
4. **계좌 API** - 잔고, 평가, 주문체결 조회
5. **주문 API** - 매수/매도/정정/취소

### 주요 엔드포인트

```
인증: /oauth2/token
계좌: /api/dostk/acnt
종목정보: /api/dostk/stkinfo
시세: /api/dostk/mrkcond
차트: /api/dostk/chart
주문: /api/dostk/ordr
```

---

## ⚙️ 설치 및 설정

### 1. 환경 설정

`.env` 파일에 다음 정보를 설정하세요:

```bash
# 키움 API 인증 정보
KIWOOM_APP_KEY=your_app_key_here
KIWOOM_APP_SECRET=your_app_secret_here
KIWOOM_ACCOUNT_NUMBER=your_account_number
KIWOOM_USER_ID=your_user_id
```

### 2. 의존성 설치

```bash
cd kiwoom_trading
source venv/bin/activate
pip install -r requirements.txt
```

---

## 📚 기본 사용법

### 1. API 초기화 및 인증

```python
from kiwoom_api import KiwoomAPI

# API 초기화 (.env에서 자동 로드)
api = KiwoomAPI()

# 접근 토큰 발급
token = api.get_access_token()
print(f"토큰: {token}")
```

### 2. 계좌 조회

```python
# 예수금 조회
balance = api.get_balance()
print(f"주문가능금액: {balance}")

# 보유 종목 조회
positions = api.get_account_info()
for stock in positions.get('day_bal_rt', []):
    print(f"{stock['stk_nm']}: {stock['rmnd_qty']}주")

# 계좌 평가현황
evaluation = api.get_account_evaluation()
print(f"총평가금액: {evaluation.get('tot_evlt_amt')}")
```

### 3. 시세 조회

```python
stock_code = "005930"  # 삼성전자

# 기본정보
info = api.get_stock_info(stock_code)
print(f"현재가: {info.get('cur_prc')}")

# 호가
quote = api.get_stock_quote(stock_code)
print(f"매수호가: {quote.get('buy_hoga_1')}")

# 체결 틱
execution = api.get_execution_info(stock_code)
for tick in execution.get('stk_cntr_info', [])[:5]:
    print(f"{tick['cntr_time']}: {tick['cntr_prc']}")
```

### 4. 주문 실행

```python
# 매수 주문
result = api.order_buy(
    stock_code="005930",
    quantity=10,
    price=70000,
    trade_type="0"  # 보통 지정가
)
order_no = result.get('ord_no')

# 매도 주문
result = api.order_sell(
    stock_code="005930",
    quantity=10,
    price=75000,
    trade_type="0"
)

# 시장가 주문
result = api.order_buy(
    stock_code="005930",
    quantity=10,
    price=0,  # 가격 0
    trade_type="3"  # 시장가
)

# 주문 취소
api.order_cancel(
    orig_ord_no=order_no,
    stock_code="005930",
    quantity=0  # 전량 취소
)
```

### 5. 미체결/체결 조회

```python
# 미체결 조회
unexecuted = api.get_unexecuted_orders()
for order in unexecuted.get('ord_noexe', []):
    print(f"{order['stk_nm']}: {order['noexe_qty']}주 미체결")

# 체결 조회
executed = api.get_executed_orders()
for order in executed.get('ord_cntr', []):
    print(f"{order['stk_nm']}: {order['cntr_qty']}주 @ {order['cntr_uv']}")
```

---

## 🎯 고급 기능

### 트레이딩 매니저 사용

```python
from kiwoom_api import KiwoomAPI
from trading_manager import TradingManager

# 초기화
api = KiwoomAPI()
manager = TradingManager(
    api=api,
    max_stocks=10,  # 최대 보유 종목 수
    max_position_ratio=0.1  # 종목당 최대 10% 투자
)

# 포지션 업데이트
manager.update_positions()
manager.update_unexecuted_orders()

# 계좌 현황 출력
manager.print_summary()
```

### 자동 수량 계산 매수

```python
# 가용 현금의 10%로 자동 매수
order_no = manager.buy(
    stock_code="005930",
    price=70000,
    invest_ratio=0.1  # 10% 투자
)

# 시장가 자동 매수 (현재가 자동 조회)
order_no = manager.buy(
    stock_code="005930",
    price=0,  # 시장가
    invest_ratio=0.05,  # 5% 투자
    trade_type="3"
)
```

### 보유 종목 매도

```python
# 일부 매도
manager.sell(
    stock_code="005930",
    quantity=5,
    price=75000
)

# 전량 매도
manager.sell(
    stock_code="005930",
    price=0,  # 시장가
    trade_type="3"
)
```

### 손절/익절 자동 실행

```python
# 손절 체크 (-3%)
stop_loss_stocks = manager.check_stop_loss(-0.03)
print(f"손절 대상: {stop_loss_stocks}")

# 손절 자동 실행
manager.execute_stop_loss(-0.03)

# 익절 체크 (+5%)
take_profit_stocks = manager.check_take_profit(0.05)

# 익절 자동 실행
manager.execute_take_profit(0.05)
```

---

## 💼 실전 매매 예제

### 예제 1: 기본 테스트

```bash
# API 기능 테스트
python test_trading_api.py
```

### 예제 2: 대화형 매매 메뉴

```bash
# 예제 선택 메뉴 실행
python example_trading.py
```

### 예제 3: 자동 모니터링

```python
import time
from datetime import datetime
from kiwoom_api import KiwoomAPI
from trading_manager import TradingManager

# 초기화
api = KiwoomAPI()
manager = TradingManager(api=api)

# 설정
STOP_LOSS = -0.03  # -3% 손절
TAKE_PROFIT = 0.05  # +5% 익절
CHECK_INTERVAL = 60  # 60초마다

print("자동 모니터링 시작...")

try:
    while True:
        now = datetime.now().strftime('%H:%M:%S')
        print(f"\n[{now}] 체크 중...")

        # 포지션 업데이트
        manager.update_positions()

        # 손절/익절 실행
        manager.execute_stop_loss(STOP_LOSS)
        manager.execute_take_profit(TAKE_PROFIT)

        time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print("\n모니터링 종료")

api.close()
```

### 예제 4: 여러 종목 일괄 매수

```python
from kiwoom_api import KiwoomAPI
from trading_manager import TradingManager
import time

api = KiwoomAPI()
manager = TradingManager(api=api, max_position_ratio=0.05)

# 매수 종목 리스트
buy_list = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
]

for stock_code in buy_list:
    # 현재가 조회
    quote = api.get_stock_quote(stock_code)

    if quote.get('return_code') == 0:
        # 호가 데이터 추출
        hoga_data = quote.get('stk_hoga') or quote.get('output') or quote.get('data')
        if hoga_data:
            buy_price = float(hoga_data.get('buy_hoga_1', 0))

            # 매수 주문 (5% 투자)
            manager.buy(
                stock_code=stock_code,
                price=buy_price,
                invest_ratio=0.05
            )

    time.sleep(0.5)  # API 호출 간격

api.close()
```

---

## 📊 주문 타입

### trade_type 값

```python
# 지정가 주문
"0"  # 보통 지정가
"5"  # 조건부지정가
"6"  # 최유리지정가
"7"  # 최우선지정가

# 시장가 주문
"3"  # 시장가

# IOC (즉시체결 또는 취소)
"10"  # 보통(IOC)
"13"  # 시장가(IOC)
"16"  # 최유리(IOC)

# FOK (전량체결 또는 취소)
"20"  # 보통(FOK)
"23"  # 시장가(FOK)
"26"  # 최유리(FOK)
```

---

## ⚠️ 주의사항

### 1. API 호출 제한
- 초당 최대 5회 호출 권장
- 연속 호출시 0.2~0.5초 간격 유지

```python
import time

for stock in stock_list:
    api.order_buy(...)
    time.sleep(0.5)  # API 호출 간격
```

### 2. 토큰 만료
- 접근 토큰은 24시간 유효
- 자동으로 재발급되지만, 장기 실행시 주의

### 3. 장 운영 시간
- 정규장: 09:00 ~ 15:30
- 시간외: 08:30 ~ 09:00, 15:40 ~ 16:00

### 4. 모의투자
- 테스트는 모의투자 계좌 사용 권장
- 도메인: `https://mockapi.kiwoom.com`

---

## 🔍 트러블슈팅

### 토큰 발급 실패
```
✗ 토큰 발급 실패: [401] Unauthorized
```
→ .env 파일의 APP_KEY, APP_SECRET 확인

### 계좌번호 오류
```
✗ 계좌번호가 설정되지 않았습니다
```
→ .env 파일의 KIWOOM_ACCOUNT_NUMBER 확인

### API 응답 오류
```
return_code: -1
return_msg: 잘못된 요청입니다
```
→ API 문서에서 필수 파라미터 확인

---

## 📖 참고 자료

1. **키움 REST API 문서**: `docs/키움api/키움 REST API 문서.xlsx`
2. **키움증권 개발자센터**: https://openapi.kiwoom.com
3. **API 문서 (웹)**: 키움증권 홈페이지 > Open API

---

## 📝 라이선스

이 코드는 교육 및 개인 투자 목적으로만 사용하세요.
실제 투자에 따른 손실은 사용자 책임입니다.

---

## 💡 추가 개선 사항

### 향후 구현 예정
- [ ] 실시간 시세 WebSocket 연동
- [ ] 조건검색 자동 매매
- [ ] 백테스팅 시스템 연동
- [ ] 알림 시스템 (텔레그램/이메일)
- [ ] 매매 로그 데이터베이스 저장
- [ ] 포트폴리오 리밸런싱
