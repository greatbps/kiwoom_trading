# RSVI Phase 1 실거래 적용 가이드

**작성일**: 2025-11-30
**버전**: Phase 1 (L6 Filter Enhancement)
**상태**: ✅ 검증 완료 - 실거래 적용 준비 완료

---

## ✅ 최종 점검 결과

### 1. 코드 검증 (완료)

| 항목 | 상태 | 비고 |
|------|------|------|
| **문법 검증** | ✅ 통과 | py_compile 검증 완료 |
| **RSVI 모듈** | ✅ 정상 | `analyzers/volume_indicators.py` |
| **L6 Filter 통합** | ✅ 정상 | `analyzers/pre_trade_validator_v2.py` |
| **SignalOrchestrator 연동** | ✅ 정상 | Line 510 호출 확인 |
| **main_auto_trading 통합** | ✅ 정상 | Line 2486 evaluate_signal 호출 |
| **통합 테스트** | ✅ 통과 | 모든 시나리오 정상 작동 |

### 2. 데이터 흐름 확인 (완료)

```
main_auto_trading.py (Line 2486)
    ↓
SignalOrchestrator.evaluate_signal() (Line 414)
    ↓
PreTradeValidatorV2.check_with_confidence() (Line 510)
    ↓
attach_rsvi_indicators() → RSVI 지표 계산
    ↓
calculate_rsvi_score() → RSVI 점수 (0.0-1.0)
    ↓
최종 confidence = 0.3 * backtest + 0.7 * rsvi
    ↓
Threshold 0.4 체크 → 진입 허용/차단
```

### 3. 통합 테스트 결과 (완료)

**테스트 케이스**:
- ✅ RSVI 지표 계산 테스트 → 정상
- ✅ RSVI 하드컷 테스트 → 정상
- ✅ PreTradeValidatorV2 통합 → 정상
- ✅ 시나리오 테스트 (3가지) → 모두 통과

---

## 🚀 실거래 적용 방법

### Step 1: 현재 프로세스 종료

```bash
# 실행 중인 자동매매 프로세스 확인
ps aux | grep main_auto_trading.py

# 프로세스 종료 (방법 1: pkill)
pkill -f "main_auto_trading.py"

# 프로세스 종료 (방법 2: kill)
kill -9 <PID>  # 위에서 확인한 PID 입력
```

**확인**:
```bash
# 프로세스가 종료되었는지 확인
ps aux | grep main_auto_trading.py
# → 아무것도 안 나오면 정상 종료
```

### Step 2: 프로그램 재시작

```bash
# 방법 1: run.sh 사용 (권장)
./run.sh

# 방법 2: 직접 실행
python3 main_auto_trading.py
```

**자동 적용**:
- 재시작 시 RSVI Phase 1이 자동으로 적용됩니다
- 별도 설정 변경 불필요

### Step 3: 로그 확인 (실시간)

```bash
# 실시간 로그 모니터링
tail -f logs/trading_*.log

# RSVI 관련 로그만 확인
tail -f logs/trading_*.log | grep "RSVI\|L6"
```

---

## 📊 모니터링 방법

### 1. RSVI 작동 확인

#### 1-1. RSVI 하드컷 발동 확인

```bash
# RSVI 하드컷으로 차단된 건 확인
grep "L6 RSVI 하드컷" logs/trading_*.log

# 예시 출력:
# 2025-11-30 10:15:23 | ❌ REJECT | 009420 한올바이오파마 @15000원 | L6 RSVI 하드컷: 거래량 매우 약함 | vol_z20=-1.50, vroc10=-0.80
```

#### 1-2. L6+RSVI 통과 건 확인

```bash
# L6+RSVI 통과한 진입 신호 확인
grep "L6+RSVI 통과" logs/trading_*.log

# 예시 출력:
# 2025-11-30 10:20:15 | ✅ ACCEPT | 005930 삼성전자 @70000원 | L6+RSVI 통과 | Conf=0.78 (BT:0.60 RSVI:0.85)
# └ RSVI: vol_z20=+2.10, vroc10=+3.50
# └ 백테스트 15회, 승률(윌슨하한) 55.2%, PF 1.45, 평균 +0.8%
```

#### 1-3. Confidence 분포 확인

```bash
# L6+RSVI Confidence 분포
grep "L6+RSVI" logs/trading_*.log | grep -oP "Conf=\d+\.\d+" | cut -d= -f2 | sort -n | uniq -c

# 예시 출력:
#   2 0.35
#   5 0.42
#  12 0.55
#  20 0.68
#  15 0.82
#   8 0.95
```

### 2. 성과 지표 모니터링

#### 2-1. 일일 승률 확인

```bash
# 오늘 거래 통계 (PostgreSQL)
python3 << 'EOF'
import os
import psycopg2
from datetime import date

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='trading_system',
    user='postgres',
    password='killer99!!'
)

cursor = conn.cursor()
cursor.execute("""
    SELECT
        COUNT(*) / 2 as trades,
        SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) as wins,
        AVG(realized_profit) as avg_profit,
        SUM(realized_profit) as total_profit
    FROM trades
    WHERE DATE(trade_time) = CURRENT_DATE
      AND trade_type = 'SELL'
      AND realized_profit IS NOT NULL;
""")

result = cursor.fetchone()
if result and result[0]:
    trades, wins, avg_profit, total = result
    win_rate = (wins / trades * 100) if trades > 0 else 0
    print(f"📊 오늘 거래 통계 ({date.today()})")
    print(f"  거래: {trades:.0f}건")
    print(f"  승률: {win_rate:.1f}% ({wins:.0f}/{trades:.0f})")
    print(f"  평균 손익: {avg_profit:+,.0f}원")
    print(f"  총 손익: {total:+,.0f}원")
else:
    print("오늘 거래 없음")

conn.close()
EOF
```

#### 2-2. 주간 승률 추이

```bash
# 최근 7일 승률 추이 (PostgreSQL)
python3 << 'EOF'
import os
import psycopg2

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='trading_system',
    user='postgres',
    password='killer99!!'
)

cursor = conn.cursor()
cursor.execute("""
    SELECT
        DATE(trade_time) as date,
        COUNT(*) / 2 as trades,
        SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) as wins,
        AVG(realized_profit) as avg_profit
    FROM trades
    WHERE trade_time >= CURRENT_DATE - INTERVAL '7 days'
      AND trade_type = 'SELL'
      AND realized_profit IS NOT NULL
    GROUP BY DATE(trade_time)
    ORDER BY DATE(trade_time);
""")

print("📈 최근 7일 승률 추이")
print("-" * 60)
for row in cursor.fetchall():
    date, trades, wins, avg = row
    win_rate = (wins / trades * 100) if trades > 0 else 0
    print(f"{date} | {trades:.0f}건 | 승률 {win_rate:5.1f}% ({wins:.0f}/{trades:.0f}) | 평균 {avg:+6.0f}원")

conn.close()
EOF
```

### 3. RSVI 효과 분석

#### 3-1. RSVI 차단 효과

```bash
# RSVI로 차단된 종목이 실제로 어땠을지 확인
# (실거래에서는 직접 확인 어려움, 로그 분석 필요)

grep "RSVI 하드컷" logs/trading_*.log | wc -l
# → RSVI 하드컷 발동 횟수

grep "Confidence 부족" logs/trading_*.log | grep "RSVI" | wc -l
# → RSVI로 인한 Confidence 부족 차단 횟수
```

---

## ⚙️ 파라미터 조정

### 1. RSVI 가중치 조정

**현재 설정**:
- Backtest 30% + RSVI 70%

**조정 방법**:

```python
# analyzers/pre_trade_validator_v2.py:245
# 현재
final_confidence = (0.3 * backtest_conf) + (0.7 * rsvi_score)

# 조정 옵션
final_confidence = (0.4 * backtest_conf) + (0.6 * rsvi_score)  # RSVI 비중 낮춤 (보수적)
final_confidence = (0.2 * backtest_conf) + (0.8 * rsvi_score)  # RSVI 비중 높임 (공격적)
```

**조정 후 재시작 필요**.

### 2. Threshold 조정

**현재 설정**: 0.4

**조정 방법**:

```python
# analyzers/pre_trade_validator_v2.py:249
# 현재
threshold = 0.4

# 조정 옵션
threshold = 0.35  # 더 많은 거래 허용 (승률 다소 하락 가능)
threshold = 0.45  # 더 엄격한 필터링 (승률 개선, 거래 감소)
```

**조정 후 재시작 필요**.

### 3. RSVI 하드컷 기준 조정

**현재 설정**: `vol_z20 < -1.0 AND vroc10 < -0.5`

**조정 방법**:

```python
# analyzers/pre_trade_validator_v2.py:186
# 현재
if vol_z20 < -1.0 and vroc10 < -0.5:

# 조정 옵션 (더 엄격)
if vol_z20 < -0.8 and vroc10 < -0.3:

# 조정 옵션 (더 관대)
if vol_z20 < -1.5 and vroc10 < -0.8:
```

**조정 후 재시작 필요**.

---

## 🔧 문제 해결

### 1. RSVI가 작동하지 않는 것 같아요

**확인 사항**:

```bash
# 1. 로그에서 RSVI 관련 메시지 확인
grep "RSVI\|L6" logs/trading_*.log | tail -20

# 2. 프로세스가 최신 코드로 실행 중인지 확인
ps aux | grep main_auto_trading.py
# → 시작 시간 확인 (재시작 후 시간과 일치하는지)

# 3. Python 캐시 삭제 후 재시작
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
pkill -f "main_auto_trading.py" && ./run.sh
```

### 2. 모든 신호가 차단돼요

**원인**: RSVI 점수가 너무 낮거나 Threshold가 너무 높음

**해결**:
1. Threshold를 0.35로 낮추기
2. RSVI 가중치를 0.6으로 낮추기 (Backtest 0.4)

### 3. 거래량이 없는 종목에서 오류가 발생해요

**정상 동작**: RSVI는 거래량 데이터가 부족하면 rsvi_score = 0.5 (기본값) 사용

**확인**:
```bash
grep "RSVI 계산 실패" logs/trading_*.log
```

---

## 📋 체크리스트

### 적용 전

- [ ] 현재 프로세스 종료 확인
- [ ] 최신 코드 pull 완료 (git pull)
- [ ] Python 캐시 삭제 (`__pycache__` 제거)
- [ ] 로그 디렉토리 확인 (`logs/` 존재)

### 적용 후 (첫 1시간)

- [ ] 프로세스 정상 시작 확인
- [ ] L6+RSVI 로그 출력 확인
- [ ] RSVI 하드컷 발동 여부 확인
- [ ] 진입 신호 Confidence 분포 확인
- [ ] 에러 로그 없는지 확인

### 적용 후 (첫 1일)

- [ ] 일일 승률 확인 (목표: 25% 이상)
- [ ] RSVI 하드컷 발동 횟수 확인
- [ ] L6+RSVI 차단 건수 확인
- [ ] 총 손익 확인 (목표: 흑자 전환)

### 적용 후 (첫 1주)

- [ ] 주간 승률 추이 확인 (목표: 25% → 35%+)
- [ ] RSVI 효과 분석 (차단 건 중 손실 비율)
- [ ] 파라미터 조정 필요성 검토
- [ ] Phase 2 준비 여부 결정

---

## 📊 기대 효과 (1주일 기준)

| 지표 | 현재 (11-14~28) | 목표 (Phase 1 적용 후) |
|------|-----------------|------------------------|
| **승률** | 8.9% | 25-35% |
| **평균 손익** | -57원/건 | +50원/건 이상 |
| **일일 손익** | -370원 | +500원 이상 |
| **주간 손익** | -5,170원 | +3,000원 이상 (흑자) |
| **대손실 발생** | 1건 (-12,710원) | 0건 (RSVI 하드컷) |

---

## 🎯 성공 기준

### Minimum Success (최소 성공)

- 승률: **8.9% → 20%** (2배 개선)
- 손익: **손실 → 0원** (손실 방지)
- RSVI 하드컷: **대손실 차단** (-5% 이상 0건)

### Target Success (목표 성공)

- 승률: **8.9% → 30%** (3배 개선)
- 손익: **-5,170원 → +2,000원** (흑자 전환)
- RSVI 차단 정확도: **80% 이상** (차단 건 중 손실 비율)

### Excellent Success (우수 성공)

- 승률: **8.9% → 40%** (4배 개선)
- 손익: **-5,170원 → +5,000원** (흑자 안정)
- RSVI 차단 정확도: **90% 이상**

---

## 📝 다음 단계

### 1주일 후

- [ ] 성과 분석 리포트 작성
- [ ] 파라미터 최적화 (필요 시)
- [ ] Phase 2 준비 (Multi-Alpha 통합)

### 2주일 후

- [ ] Phase 2 구현 검토
- [ ] `trading/alphas/alpha_volume_strength.py` 생성
- [ ] Multi-Alpha Engine 가중치 재조정

### 1개월 후

- [ ] Phase 1 성과 최종 평가
- [ ] Phase 2 실거래 적용
- [ ] Phase 3 검토 (Exit & Sizing)

---

**작성자**: Claude Code
**작성일**: 2025-11-30
**버전**: Phase 1 Deployment Guide
**상태**: ✅ 실거래 적용 준비 완료

**긴급 문제 발생 시 롤백 방법**:
```bash
# Git으로 이전 버전 복구
git checkout HEAD~1 -- analyzers/volume_indicators.py
git checkout HEAD~1 -- analyzers/pre_trade_validator_v2.py
pkill -f "main_auto_trading.py" && ./run.sh
```
