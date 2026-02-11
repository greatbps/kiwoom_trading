# MA Cross 복기 시스템 사용 가이드

## 📋 개요

MA Cross 전략의 모든 거래를 자동으로 분석하고 복기하는 시스템입니다.

### 주요 기능

1. **자동 복기 데이터 수집**
   - 진입 시점: MA Cross 타이밍, 캔들 타입, 가격 위치, 시간대, 거래량 등
   - 청산 시점: 청산 유형, 손익률, MAE (최대 역행폭), 골든크로스 유지 시간

2. **거래내역 비교 (키움 API vs DB)**
   - 주기적으로 키움 API에서 실제 거래내역 조회
   - 시스템 DB와 비교하여 누락/불일치 감지
   - trade_alerts.log에 자동 기록

3. **데이터 기반 전략 개선**
   - 캔들 타입별 성과 분석
   - 진입 지연별 성과 분석
   - 실패 패턴 자동 감지
   - 개선 권장사항 제시

---

## 🗄️ 데이터베이스 구조

### 1. `trade_review` 테이블
MA Cross 복기 데이터 저장

```sql
-- 주요 필드
trade_id                    -- 거래 ID (YYYYMMDD_HHMMSS_SYMBOL)
symbol                      -- 종목 코드
entry_time, exit_time       -- 진입/청산 시각
entry_price, exit_price     -- 진입/청산 가격
pnl_pct                     -- 손익률 (%)
exit_type                   -- 'dead_cross' | 'hard_stop'

-- MA Cross 분석
ma_cross_timing             -- 'immediate' | 'delayed'
ma_cross_delay_bars         -- Cross 후 진입까지 봉 수
golden_cross_duration_bars  -- 골든크로스 유지 봉 수
max_adverse_excursion_pct   -- MAE (최대 역행폭)

-- 진입 구조
entry_candle_type           -- 'strong_bull' | 'weak_bull' | 'doji' | 'bear'
price_location              -- 'breakout' | 'box_top' | 'box_middle' | 'box_bottom'
time_slot                   -- '09:00_09:30' | '09:30_10:30' | ...
volume_ratio                -- 거래량 배율

-- 실패 패턴 플래그
late_entry                  -- 늦은 진입 (2봉 이상 지연)
no_volume                   -- 거래량 부족
near_resistance             -- 저항선 근처
chasing_entry               -- 추격 매수
sudden_drop_before_dead     -- 데드크로스 전 급락

result                      -- 'profit' | 'loss' | 'breakeven'
```

### 2. `trade_reconciliation` 테이블
거래내역 비교 결과 저장

```sql
trade_date                  -- 비교 날짜
db_trade_count              -- DB 거래 수
api_trade_count             -- API 거래 수
is_matched                  -- 일치 여부
missing_trades              -- 누락된 거래 (JSON)
extra_trades                -- 불일치 거래 (JSON)
discrepancy_detail          -- 상세 정보
```

---

## 🚀 사용법

### 1. 거래내역 비교 (주기적 실행 권장)

```bash
# 오늘 거래내역 비교
python scripts/reconcile_trades.py

# 특정 날짜 비교
python scripts/reconcile_trades.py 20260113

# 최근 7일 비교
python scripts/reconcile_trades.py --days 7
```

**출력 예시:**
```
✅ 거래내역 일치 (총 5건)

또는

❌ 거래내역 불일치 감지!
  - 누락된 거래: 3건
    • 인팩 SELL 2주 @ 11,760원
    • 인팩 SELL 2주 @ 11,830원
    • 인팩 SELL 2주 @ 11,830원
```

### 2. 복기 분석 리포트

```bash
# 최근 30일 분석
python scripts/analyze_trades.py

# 최근 7일 분석
python scripts/analyze_trades.py --days 7

# 특정 종목 분석
python scripts/analyze_trades.py --symbol 005930
```

**출력 예시:**
```
📊 전체 통계
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지표                         값
───────────────────────────────
총 거래 수                    42
승리                     28건
패배                     14건
승률                    66.7%
평균 손익                +1.23%
평균 승리                +2.45%
평균 손실                -1.58%
손익비 (R:R)              1.55
Hard Stop 비율        5/42 (11.9%)
평균 MAE                 -0.87%

📈 진입 캔들 타입별 성과
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
캔들 타입    거래 수    평균 손익
───────────────────────────────
장대양봉        15      -0.52%
짧은 양봉        20      +1.85%
도지/윗꼬리       5      +0.32%
음봉             2      -1.20%

⏱️  Cross 후 진입 지연별 성과
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지연 봉 수    거래 수    평균 손익
───────────────────────────────
즉시            28      +1.72%
1봉 후          10      +0.45%
2봉 후           4      -1.03%

💡 데이터 기반 권장사항:

  ❌ '장대양봉' 진입의 평균 손익: -0.52%
     → 이 캔들 타입에서의 진입을 피하세요

  ⏱️  즉시 진입이 2.8% 더 우수
     → Cross 후 2봉 이상 지연된 진입은 피하세요
```

---

## 🔄 자동화 설정

### Cron으로 주기적 실행 (권장)

```bash
# crontab 편집
crontab -e

# 매일 15:30에 거래내역 비교
30 15 * * * cd /home/greatbps/projects/kiwoom_trading && python scripts/reconcile_trades.py >> logs/reconcile.log 2>&1

# 매주 일요일 18:00에 주간 분석 리포트
0 18 * * 0 cd /home/greatbps/projects/kiwoom_trading && python scripts/analyze_trades.py --days 7 >> logs/weekly_report.log 2>&1
```

---

## 📊 분석 쿼리 예시

### 1. Hard Stop 비율 확인
```sql
SELECT
    COUNT(*) FILTER (WHERE exit_type = 'hard_stop')::float / COUNT(*) * 100 AS hard_stop_pct
FROM trade_review
WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days';
```

### 2. 장대양봉 진입 성과
```sql
SELECT
    COUNT(*) AS trades,
    ROUND(AVG(pnl_pct), 2) AS avg_pnl,
    COUNT(*) FILTER (WHERE result = 'profit') AS wins
FROM trade_review
WHERE entry_candle_type = 'strong_bull'
  AND trade_date >= CURRENT_DATE - INTERVAL '30 days';
```

### 3. 늦은 진입의 위험성
```sql
SELECT
    ma_cross_delay_bars,
    COUNT(*) AS trades,
    ROUND(AVG(pnl_pct), 2) AS avg_pnl
FROM trade_review
WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY ma_cross_delay_bars
ORDER BY ma_cross_delay_bars;
```

### 4. "들어가면 안 되는 골든" 자동 추출
```sql
SELECT *
FROM trade_review
WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
  AND (late_entry::int
     + no_volume::int
     + chasing_entry::int
     + near_resistance::int) >= 2
ORDER BY pnl_pct;
```

---

## 🔧 문제 해결

### Q1: 복기 데이터가 없다고 나옵니다

**A:** 아직 거래가 기록되지 않았거나, 자동매매 코드에 복기 로직이 통합되지 않았습니다.

해결 방법:
1. `main_auto_trading.py`에서 `TradeReviewAnalyzer` 사용 확인
2. 진입/청산 시점에 `save_trade_review()` 호출 확인

### Q2: 거래내역 비교에서 계속 불일치가 발생합니다

**A:** 키움 API 응답 필드명이 실제와 다를 수 있습니다.

해결 방법:
1. `swing_trader_pipeline/app/services/kiwoom.py`의 `get_daily_trade_history()` 확인
2. 실제 API 응답 구조에 맞게 필드명 수정
3. API ID가 `ka10030`이 맞는지 키움 API 문서 확인

### Q3: PostgreSQL 연결 오류

**A:** 환경 변수 설정 확인

```bash
# .env 파일 확인
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=trading_system
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
```

---

## 📈 전략 개선 워크플로우

1. **데이터 수집 (자동)**
   - 거래 시마다 복기 데이터 자동 저장
   - 매일 API 거래내역 비교

2. **주간 분석 (수동)**
   ```bash
   python scripts/analyze_trades.py --days 7
   ```

3. **패턴 발견**
   - "장대양봉 진입은 손실이 많다"
   - "즉시 진입이 지연 진입보다 우수하다"
   - "특정 시간대의 승률이 낮다"

4. **전략 수정**
   - `config/strategy_hybrid.yaml` 수정
   - 또는 코드에 필터 추가

5. **재분석**
   - 다시 30일 운영 후 분석
   - 개선 여부 확인

---

## 🎯 다음 단계

- [ ] 자동매매 코드에 복기 로직 통합
- [ ] 웹 대시보드 구축 (Grafana/Streamlit)
- [ ] 실시간 복기 알림 (Slack/Discord)
- [ ] ML 모델 학습용 데이터 Export

---

**📧 문의:** trade_review_system.py 코드 참고
