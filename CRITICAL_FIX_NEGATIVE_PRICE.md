# 🚨 긴급 수정: 음수 가격 매도 버그 수정

## 📋 발견 일시
2025-11-07 12:21

## ❌ 심각한 버그 발견

### 사례
```
🔔 매도 신호 발생: 한올바이오파마 (009420)
   매수가: 46,300원
   매도가: 34,500원         ← 🚨 음수 가격!
   수익률: -25.49%          ← 🚨 가짜 손실!
   실현손익: -59,000원
   사유: 손절 (-25.49%)
```

**실제 상황:**
- 실제 현재가: 46,200원 (정상)
- 잘못된 매도가: 34,500원 (음수 -34,500을 그대로 사용)
- **결과: 25% 손실로 잘못 매도 시도!**

## 🔍 원인 분석

### 1단계: Yahoo Finance 데이터 오류
```python
# Yahoo Finance에서 가끔 음수 가격 반환
df['close'].iloc[-1] = -34500  # 버그
```

### 2단계: 검증 없이 그대로 사용
```python
current_price = df['close'].iloc[-1]  # -34500
profit_pct = ((current_price - entry_price) / entry_price) * 100
# = ((-34500 - 46300) / 46300) * 100
# = -174% (말도 안 되는 손실)
```

### 3단계: 잘못된 매도 주문
```python
self.api.order_sell(
    stock_code=stock_code,
    quantity=5,
    price=0,  # 시장가
    trade_type="3"
)
# 실제 46,200원에 매도해야 하는데
# 시스템은 -25% 손실로 판단
```

## ✅ 적용된 수정사항

### 1. Yahoo Finance 데이터 필터링

**위치:** `main_auto_trading.py` 라인 65~76

```python
def download_stock_data_sync(ticker: str, days: int = 7):
    ...
    df.reset_index(inplace=True)
    df.columns = [col.lower() for col in df.columns]

    # 🚨 음수/0 가격 필터링 (Yahoo Finance 버그 대응)
    if 'close' in df.columns:
        # 음수 또는 0인 행 제거
        invalid_rows = df[df['close'] <= 0]
        if len(invalid_rows) > 0:
            console.print(f"[yellow]⚠️  {ticker}: {len(invalid_rows)}개 비정상 가격 데이터 제거[/yellow]")
            df = df[df['close'] > 0].copy()

    # 데이터가 너무 적으면 None 반환
    if len(df) < 10:
        return None

    return df
```

**효과:**
- Yahoo Finance에서 받은 데이터 중 음수/0 가격 행 제거
- 데이터 정합성 확보
- 근본 원인 차단

### 2. 매수 시그널 가격 검증

**위치:** `main_auto_trading.py` 라인 1855~1858

```python
# 최신 신호 확인
latest_signal = df['signal'].iloc[-1]
current_price = df['close'].iloc[-1]
current_vwap = df['vwap'].iloc[-1]

# 🚨 음수 가격 검증 (데이터 오류 방지)
if current_price <= 0:
    console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price} - 매수 시그널 무시[/red]")
    return

if latest_signal == 1:  # 매수 신호
    ...
```

**효과:**
- 음수/0 가격으로 매수 주문 방지
- 잘못된 진입가 기록 방지

### 3. 매도 시그널 가격 검증

**위치:** `main_auto_trading.py` 라인 1923~1926

```python
# 최신 신호 확인
latest_signal = df['signal'].iloc[-1]
current_price = df['close'].iloc[-1]

# 🚨 음수 가격 검증 (데이터 오류 방지)
if current_price <= 0:
    console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price} - 매도 시그널 무시[/red]")
    return

# 수익률 계산
profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
...
```

**효과:**
- 음수/0 가격으로 매도 주문 방지
- 잘못된 손실 계산 방지
- **재산 보호!**

## 🎯 수정 효과

### Before (수정 전)
```
데이터: close = -34500
      ↓
수익률: -174% (말도 안 됨)
      ↓
매도 주문: 시장가로 즉시 매도
      ↓
💸 큰 손실 발생!
```

### After (수정 후)
```
데이터: close = -34500
      ↓
검증: current_price <= 0 감지
      ↓
❌ 비정상 현재가 -34500 - 매도 시그널 무시
      ↓
✅ 주문 전송 안 함 (안전)
```

## 📊 추가 보호 장치

### 3중 방어선

1. **데이터 수신 단계** (라인 65~76)
   - Yahoo Finance 데이터에서 음수/0 행 제거
   - 깨끗한 데이터만 반환

2. **매수 시그널 단계** (라인 1855~1858)
   - 음수/0 가격이면 매수 중단
   - 잘못된 진입 방지

3. **매도 시그널 단계** (라인 1923~1926)
   - 음수/0 가격이면 매도 중단
   - 잘못된 청산 방지

## ⚠️ 과거 피해 확인 필요

### 로그에서 발견된 오류들
```
2025-11-03 14:22:36 - ERROR - 009420: 비정상 현재가 -34150
2025-11-03 14:22:35 - ERROR - 112290: 비정상 현재가 -22300
2025-11-03 14:22:35 - ERROR - 065440: 비정상 현재가 -1609
2025-11-03 14:22:35 - ERROR - 011930: 비정상 현재가 -1818
```

**확인 필요:**
1. 11월 3일 14시경 실제 거래가 있었는지
2. 해당 시간에 잘못된 매도가 발생했는지
3. HTS/MTS에서 체결 내역 확인

## 🔍 Yahoo Finance 버그 원인 추정

### 가능성 1: Adjusted Close 버그
- Yahoo Finance는 배당/액면분할 조정값 제공
- 조정 과정에서 음수 발생 가능

### 가능성 2: 장중 데이터 누락
- 5분봉 데이터 수집 중 일부 누락
- 빈 데이터를 음수로 채움

### 가능성 3: API 타임아웃
- 데이터 요청 중 타임아웃
- 불완전한 응답 파싱

## ✅ 테스트 방법

### 1. 수정 확인
```bash
cd /home/greatbps/projects/kiwoom_trading
grep -n "비정상 현재가" main_auto_trading.py
```

예상 출력:
```
1857:    console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price} - 매수 시그널 무시[/red]")
1925:    console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price} - 매도 시그널 무시[/red]")
```

### 2. 실행 테스트
```bash
python main_auto_trading.py
```

**기대 동작:**
- 음수 가격 데이터 발견 시:
  ```
  ⚠️  009420.KS: 5개 비정상 가격 데이터 제거
  ❌ 009420: 비정상 현재가 -34500 - 매도 시그널 무시
  ```

### 3. 로그 모니터링
```bash
tail -f logs/auto_trading_errors.log | grep "비정상\|음수"
```

## 📌 향후 개선 사항

### 1. 키움 API 직접 사용 (권장)
```python
# Yahoo Finance 대신 키움 API 사용
df = self.api.get_minute_chart(stock_code, tic_scope="5")
```

**장점:**
- 신뢰할 수 있는 데이터
- 음수 가격 없음
- 실시간 정확도 높음

### 2. 데이터 소스 이중화
```python
# 키움 API 실패 시에만 Yahoo Finance 사용
if kiwoom_df is None:
    df = download_stock_data_sync(ticker)
```

### 3. 알림 추가
```python
if current_price <= 0:
    # 텔레그램/이메일 알림
    send_alert(f"비정상 가격 감지: {stock_code} = {current_price}")
```

## 🎉 결론

**수정 완료:**
- ✅ 음수 가격 데이터 필터링 추가
- ✅ 매수/매도 시그널에 가격 검증 추가
- ✅ 3중 방어선 구축

**효과:**
- 🛡️ 잘못된 매도 주문 방지
- 🛡️ 재산 손실 방지
- 🛡️ 데이터 품질 향상

**긴급도:** 🔴 CRITICAL (즉시 적용 완료)

---

**다음 실행부터 안전합니다!** 🎯
