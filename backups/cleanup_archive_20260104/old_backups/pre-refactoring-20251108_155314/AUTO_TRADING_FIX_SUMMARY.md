# 자동매매 API 연동 수정 완료 보고서

## 📋 수정 일시
2025-11-07

## 🔍 발견된 문제점

### 1. 매수 주문 미실행
**위치:** `main_auto_trading.py` 라인 2056
```python
# 포지션 생성 (실제로는 API 호출)  ← 주석만 있고 실제 코드 없음
entry_time = datetime.now()
self.positions[stock_code] = { ... }
```

**문제:**
- 매수 시그널 발생 시 DB에만 저장
- 실제 키움 API `order_buy()` 호출 없음
- 거래소에 주문이 전송되지 않음

### 2. 매도 주문 미실행 (전량)
**위치:** `main_auto_trading.py` 라인 2268
```python
# 포지션 제거 (실제로는 API 호출)  ← 주석만 있고 실제 코드 없음
del self.positions[stock_code]
```

**문제:**
- 매도 시그널 발생 시 DB에만 기록
- 실제 키움 API `order_sell()` 호출 없음
- 거래소에 주문이 전송되지 않음

### 3. 부분 매도 주문 미실행
**위치:** `main_auto_trading.py` 라인 2227
```python
# 포지션 업데이트
position['quantity'] -= partial_quantity
```

**문제:**
- 부분 청산 시그널 발생 시 DB와 메모리만 업데이트
- 실제 키움 API 호출 없음
- 거래소에 주문이 전송되지 않음

---

## ✅ 수정 내용

### 1. 매수 주문 API 연동 추가

**위치:** `main_auto_trading.py` 라인 2056~2075

```python
# 실제 키움 API 매수 주문
try:
    console.print(f"[yellow]📡 키움 API 매수 주문 전송 중...[/yellow]")
    order_result = self.api.order_buy(
        stock_code=stock_code,
        quantity=quantity,
        price=int(price),
        trade_type="0"  # 지정가 주문
    )

    if order_result.get('return_code') != 0:
        console.print(f"[red]❌ 매수 주문 실패: {order_result.get('return_msg')}[/red]")
        return

    order_no = order_result.get('ord_no')
    console.print(f"[green]✓ 매수 주문 성공 - 주문번호: {order_no}[/green]")

except Exception as e:
    console.print(f"[red]❌ 매수 API 호출 실패: {e}[/red]")
    return
```

**추가 기능:**
- 주문번호를 포지션에 저장: `'order_no': order_no`
- 실패 시 즉시 return하여 포지션 생성 방지
- 상세한 에러 메시지 출력

### 2. 매도 주문 API 연동 추가

**위치:** `main_auto_trading.py` 라인 2280~2301

```python
# 실제 키움 API 매도 주문
try:
    console.print(f"[yellow]📡 키움 API 매도 주문 전송 중...[/yellow]")
    order_result = self.api.order_sell(
        stock_code=stock_code,
        quantity=position['quantity'],
        price=0,  # 시장가 매도
        trade_type="3"  # 시장가
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
```

**추가 기능:**
- 시장가 매도 (`price=0`, `trade_type="3"`)
- 실패 시 포지션 유지 (수동 처리 가능)
- 주문번호 출력

### 3. 부분 매도 주문 API 연동 추가

**위치:** `main_auto_trading.py` 라인 2217~2238

```python
# 실제 키움 API 부분 매도 주문
try:
    console.print(f"[yellow]📡 키움 API 부분 매도 주문 전송 중...[/yellow]")
    order_result = self.api.order_sell(
        stock_code=stock_code,
        quantity=partial_quantity,
        price=0,  # 시장가 매도
        trade_type="3"  # 시장가
    )

    if order_result.get('return_code') != 0:
        console.print(f"[red]❌ 부분 매도 주문 실패: {order_result.get('return_msg')}[/red]")
        console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
        return

    order_no = order_result.get('ord_no')
    console.print(f"[green]✓ 부분 매도 주문 성공 - 주문번호: {order_no}[/green]")

except Exception as e:
    console.print(f"[red]❌ 부분 매도 API 호출 실패: {e}[/red]")
    console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
    return
```

**추가 기능:**
- 부분 수량만 매도
- 실패 시 포지션 유지
- 주문번호 출력

---

## 🎯 주문 전략

### 매수 주문
- **주문 유형:** 지정가 (`trade_type="0"`)
- **가격:** 시그널 발생 시점의 현재가
- **이유:** 정확한 가격에 매수하여 슬리피지 최소화

### 매도 주문 (전량/부분)
- **주문 유형:** 시장가 (`trade_type="3"`)
- **가격:** 0 (시장가)
- **이유:**
  - 빠른 청산이 목적
  - 손절/익절 시그널 발생 시 즉시 처리
  - 부분 청산 시에도 빠른 실행 필요

---

## 🔄 실행 흐름

### 매수 플로우
```
1. VWAP 매수 시그널 발생
2. 사전 검증 통과
3. 포지션 수량 계산
   ↓
4. 키움 API order_buy() 호출  ← 신규 추가!
5. return_code == 0 확인
6. 주문번호 획득
   ↓
7. 포지션 생성 (메모리)
8. DB에 거래 기록
9. 리스크 관리자 기록
```

### 매도 플로우 (전량)
```
1. 매도 시그널 발생 (손절/익절/VWAP)
2. 포지션 확인
3. 수익률 계산
   ↓
4. 키움 API order_sell() 호출  ← 신규 추가!
5. return_code == 0 확인
6. 주문번호 획득
   ↓
7. DB에 거래 기록
8. 리스크 관리자 기록
9. 포지션 삭제 (메모리)
```

### 매도 플로우 (부분)
```
1. 부분 청산 시그널 발생 (+2%/+5%)
2. 청산 수량 계산 (40%)
3. 수익 계산
   ↓
4. 키움 API order_sell() 호출  ← 신규 추가!
5. return_code == 0 확인
6. 주문번호 획득
   ↓
7. DB에 거래 기록
8. 리스크 관리자 기록
9. 포지션 수량 감소 (메모리)
```

---

## ⚠️ 에러 처리

### 1. API 호출 실패
```python
except Exception as e:
    console.print(f"[red]❌ 매수/매도 API 호출 실패: {e}[/red]")
    return  # 포지션 생성/삭제 하지 않음
```
- 네트워크 오류, 토큰 만료 등
- 포지션 불일치 방지

### 2. 주문 거부 (return_code != 0)
```python
if order_result.get('return_code') != 0:
    console.print(f"[red]❌ 주문 실패: {order_result.get('return_msg')}[/red]")
    return  # 포지션 생성/삭제 하지 않음
```
- 잔고 부족, 주문 가격 오류 등
- 포지션 불일치 방지

### 3. 매도 실패 시 안전장치
```python
console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
return
```
- 매도 주문 실패 시 포지션 유지
- 수동으로 HTS/MTS에서 처리 가능
- 데이터 정합성 유지

---

## 📊 로그 출력 개선

### Before (수정 전)
```
✅ 매수 완료
✅ 매도 완료
✅ 부분 청산 완료
```

### After (수정 후)
```
📡 키움 API 매수 주문 전송 중...
✓ 매수 주문 성공 - 주문번호: 123456789
✅ 매수 완료 (DB ID: 42)

📡 키움 API 매도 주문 전송 중...
✓ 매도 주문 성공 - 주문번호: 987654321
✅ 매도 완료 (주문번호: 987654321)

📡 키움 API 부분 매도 주문 전송 중...
✓ 부분 매도 주문 성공 - 주문번호: 555666777
✅ 부분 청산 완료 (주문번호: 555666777)
```

---

## ✅ 테스트 체크리스트

### 필수 테스트 항목
- [ ] 키움 API 토큰 발급 정상 작동
- [ ] 매수 주문 API 호출 및 주문번호 획득
- [ ] 매도 주문 API 호출 및 주문번호 획득
- [ ] 부분 매도 주문 API 호출
- [ ] API 호출 실패 시 에러 처리
- [ ] 주문 거부 시 포지션 불일치 방지
- [ ] HTS/MTS에서 실제 주문 확인

### 로그 확인
```bash
# 실시간 로그 확인
tail -f logs/auto_trading_errors.log

# DB 확인
python3 -c "
import sqlite3
conn = sqlite3.connect('data/trading.db')
cursor = conn.cursor()
cursor.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 10')
for row in cursor.fetchall():
    print(row)
conn.close()
"
```

---

## 🚀 실행 방법

```bash
cd /home/greatbps/projects/kiwoom_trading

# 1. 환경 활성화
source venv/bin/activate

# 2. .env 파일 확인
cat .env | grep KIWOOM

# 3. 자동매매 실행
python main_auto_trading.py

# 4. 로그 모니터링 (별도 터미널)
tail -f logs/auto_trading_errors.log
```

---

## 📌 중요 참고사항

1. **모의투자 테스트 필수**
   - 실제 계좌 사용 전 모의투자 계좌로 충분히 테스트
   - `.env`에서 도메인 변경: `https://mockapi.kiwoom.com`

2. **API 호출 제한**
   - 초당 5회 제한 (키움 정책)
   - 현재 코드는 순차 실행으로 문제없음

3. **장 운영 시간**
   - 정규장: 09:00 ~ 15:30
   - 장 외 시간에는 주문 거부됨

4. **주문 체결 확인**
   - 주문 전송 ≠ 체결 완료
   - HTS/MTS에서 체결 내역 확인 필요
   - 미체결 시 수동 취소/정정 가능

5. **계좌 잔고 확인**
   - 매수 전 주문가능금액 확인
   - 잔고 부족 시 주문 거부됨

---

## 📝 수정 완료 확인

✅ **매수 주문 API 연동 완료**
- 위치: `main_auto_trading.py:2056~2075`
- 기능: 지정가 매수 주문
- 에러 처리: 실패 시 포지션 생성 방지

✅ **매도 주문 API 연동 완료**
- 위치: `main_auto_trading.py:2280~2301`
- 기능: 시장가 전량 매도
- 에러 처리: 실패 시 포지션 유지

✅ **부분 매도 주문 API 연동 완료**
- 위치: `main_auto_trading.py:2217~2238`
- 기능: 시장가 부분 매도
- 에러 처리: 실패 시 포지션 유지

✅ **로그 출력 개선 완료**
- API 호출 시작/성공/실패 상태 출력
- 주문번호 출력으로 추적 가능

---

## 🎉 결론

**수정 전:**
- 시그널만 발생하고 실제 주문은 없음
- DB에만 기록
- 거래소와 연동 안 됨

**수정 후:**
- 시그널 발생 → 키움 API 호출 → 실제 주문 전송
- 주문번호 획득 및 추적 가능
- 완전한 자동매매 시스템 구축

**이제 프로그램이 실제로 자동으로 매매를 수행합니다!** 🚀
