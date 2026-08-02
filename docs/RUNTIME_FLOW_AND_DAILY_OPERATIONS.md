# 런타임 흐름 & 일일 운영 절차

> **대상 독자**: 운영자 (비개발자도 읽을 수 있게 작성)  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `swing_runner.py`, `swing_executor.py`, `main_auto_trading.py`, `watchdog.py`

---

## 1. 하루 타임라인

```
[전날 15:35] swing_runner.py 크론 실행
    └── 일봉 데이터 수집 + 후보 선정 + 주문큐 생성

[전날 15:55] health_check 크론
    └── 시스템 상태 확인 + AI Observer (07:32)

[장전] 운영자 확인
    └── 주문큐 확인 (logs/swing_orders_YYYYMMDD.json)
    └── 전날 Scientist 리포트 (금요일 이후)

[09:00] swing_executor.py 크론 실행
    └── 주문큐 기반 스윙 매수 실행

[09:00~15:30] main_auto_trading.py 상시 실행
    └── 장중 신호 평가 / 진입 / 보유 / 청산

[15:30] Analyst AI 자동 실행
    └── 당일 세션 분석 + decision_log 저장

[15:30~] 일간 리포트 자동 생성
    └── python3 -m analysis.daily_trade_report

[매 30분] returns_collector 크론
    └── 보유 포지션 수익률 DB 갱신

[금 16:40, 22:00] Scientist AI 자동 실행
    └── 주간 가설 검증 + research_notebook 업데이트
```

---

## 2. 장 마감 후 — swing_runner.py가 하는 일

**목적**: 다음날 장중 매수할 후보 종목을 미리 선정.

```python
# 실행
python3 swing_runner.py
# 또는 크론에서 15:35 자동 실행
```

**처리 순서**:
1. **유니버스 스캔**: 설정된 종목 풀 전체 일봉 수집 (최근 120일)
2. **보유 포지션 상태 확인**: 기존 스윙 포지션 홀딩 판단 (MA5 기준)
   - HOLD: 계속 보유
   - TRAIL: 트레일링 스탑 조정
   - SELL: 다음날 매도
   - ADD_MOMENTUM / ADD_VALUE: 분할 매수
3. **신호 점수화**: 패턴 + 추세 + 거래량 종합 점수 (score ≥ 5 AND trigger = True)
4. **AI Gate 필터**: ai_score < 20 종목 제외
5. **섹터 중복 방지**: 동일 섹터 최대 1종목
6. **Top-3 후보 선정**: 점수 순 정렬
7. **주문큐 저장**: `logs/swing_orders_YYYYMMDD.json`

**MFE/MAE 갱신**: 보유 포지션 당일 고점/저점으로 MFE/MAE 업데이트 → DB 반영.

---

## 3. 장 시작 전 준비

### 운영자 확인 항목

```bash
# 1. 주문큐 확인
cat logs/swing_orders_$(date +%Y-%m-%d).json | python3 -m json.tool

# 2. 리스크 상태
python3 -c "
import json
d = json.load(open('data/risk_log.json'))
print('연패:', d['consecutive_losses'])
"

# 3. 시스템 전체 상태
python3 -m analysis.os_status

# 4. main_auto_trading.py 실행 중인지 확인
pgrep -f main_auto_trading.py && echo "실행중" || echo "중단됨"
```

---

## 4. 장중 메인 루프 (main_auto_trading.py)

### 4.1 종목 후보 로딩

```
메인 루프 시작
  └── swing_orders 주문큐 로딩
  └── 계좌 잔고 / 보유 종목 조회
  └── DrawdownEngine 초기화 (당일 PnL 갱신)
  └── RAE 상태 일일 리셋 (→ [RAE_RESET_DAILY])
```

### 4.2 실시간 신호 평가 (polling, 매 N초)

각 종목에 대해 순환:

```
for stock_code in watchlist:
    check_entry_signal(stock_code, df)
    check_exit_conditions(stock_code, position, df)
    check_rae_candidates(stock_code, df)  # RAE enabled 시
```

### 4.3 진입 평가 흐름

```
[시간 필터]     09:00~15:30 (SMC 12:30 컷오프)
    ↓
[Market Context]  NO_TRADE_DAY? → 전종목 패스
    ↓
[Signal Orchestrator]  L0~L6 필터 → REJECT면 패스
    ↓
[DrawdownEngine]   HALT 상태? → 진입 차단
    ↓
[SMC 전략]     CHoCH 감지 → 등급 평가 → OB pending
    ↓
[OB Pullback]  가격이 OB 수준으로 되돌아올 때
    ↓
[execute_buy()] Kiwoom 주문 발송 + DB 즉시 INSERT
```

### 4.4 보유 관리

```
for stock_code in positions:
    # 현재가 갱신
    # exit_logic.check_exit_signal() 체크
    # EF (Early Failure Structure) 체크
    # Trailing Stop 갱신
    # Overnight 시간 체크 (14:50 강제)
```

### 4.5 청산 실행

```
execute_sell(stock_code, reason)
  └── Kiwoom 매도 주문
  └── DB UPDATE (exit_reason, profit_rate, exit_category)
  └── reentry_metrics.update_cooldown()
  └── RAE 상태 정리 (RAE_RESET_AFTER_EXIT)
```

---

## 5. 장 종료 후 리포트

### 자동 생성 리포트

```bash
# 일간 리포트 (자동 또는 수동)
python3 -m analysis.daily_trade_report

# Analyst AI 세션 분석 (15:30 자동)
python3 -m analysis.analyst_ai

# Swing 전략 리포트
python3 -m analysis.swing_report
```

### 주간 리뷰 (금요일)

```bash
python3 -m analysis.ops_weekly_review
```

5가지 자동 판정:
- A: 수익성 (PF, avg R, MDD)
- B: 전략 구조 (EF/LCL 비율)
- C: Scientist 판정 (수동 입력)
- D: 증거 수준 (Evidence Level E0~E3)
- E: 전략 변경 제안

---

## 6. 장애/재시작 시 복구 절차

### 6.1 재시작 후 복구해야 하는 상태

| 상태 | 자동 복구 | 수동 확인 필요 |
|------|-----------|----------------|
| 보유 포지션 | ✅ (PostgreSQL에서 재로딩 가능) | 금액/수량 대조 |
| 쿨다운 상태 | ❌ (재시작 시 초기화) | 쿨다운 리셋됨 인지 |
| DD 레벨 | ❌ (재시작 시 초기화) | 당일 손익 수동 확인 |
| 연패 기록 | ✅ (`data/risk_log.json` 파일 유지) | — |
| RAE 상태 | ❌ (재시작 시 초기화) | — |
| 스윙 주문큐 | ✅ (파일 유지) | — |

### 6.2 재시작 절차

```bash
# 1. 현재 포지션 확인
python3 check_account_pnl.py

# 2. HTS에서 실제 잔고와 DB 대조
# 3. 불일치 시 수동 조정

# 4. 재시작
python3 main_auto_trading.py &

# 5. watchdog도 재시작 (자동 재시작 보장)
python3 watchdog.py &
```

---

## 7. 긴급 상황 대응

### 시스템 이상 시 즉시 정지

```bash
# 모든 자동매매 즉시 중단
kill $(pgrep -f "main_auto_trading.py")
kill $(pgrep -f "watchdog.py")
kill $(pgrep -f "api_server.py")
```

### 수동 청산 (HTS)

자동화 중단 후 보유 포지션은 **키움 HTS에서 직접 수동 청산**.
시스템이 멈춘 상태에서 API 청산 시도 금지.

---

## 8. 운영 핵심 지표 확인

```bash
# 당일 거래 현황
grep "매수완료\|매도완료\|EDT_GUARD\|HALT" logs/auto_trading_$(date +%Y%m%d).log

# DD 레벨
grep "DD_CAUTION\|DD_DANGER\|DD_HALT" logs/auto_trading_$(date +%Y%m%d).log

# EF 발동
grep "EF_TRIGGER\|EF_SUBTYPE\|ef_no_demand\|ef_no_follow" logs/auto_trading_$(date +%Y%m%d).log

# 재진입 리포트
cat logs/reentry_report_$(date +%Y-%m-%d).json | python3 -m json.tool
```

---

## 9. 일일 정상 동작 확인 기준

| 항목 | 정상 상태 |
|------|-----------|
| main_auto_trading.py | 실행 중 (`pgrep` 결과 있음) |
| signal_orchestrator.log | 당일 날짜로 갱신됨 |
| auto_trading_YYYYMMDD.log | 당일 날짜로 로그 쌓임 |
| DrawdownEngine | NORMAL 또는 CAUTION (HALT 아님) |
| swing_orders | 전날 파일 존재 |
| 연패 수 | 3 미만 권장 |
