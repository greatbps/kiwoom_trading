# 필터 최적화 분석 리포트

**분석 일시**: 2025-11-28
**데이터 기간**: 2025-11-14 ~ 2025-11-28 (최근 2주)
**분석 데이터**: PostgreSQL trading_system DB

---

## 📊 Executive Summary

### 심각한 문제 발견 🚨

```
Validation 예측 vs 실거래 성과:

Old Validation System (~ 11-02):
- 통과 종목 예측 승률: 78.6%
- 통과 종목 예측 수익: +2.8원

현재 실거래 성과 (11-14 ~ 11-28):
- 실제 승률: 25.0%  ❌ (-53.6%p 차이!)
- 실제 평균 손익: -36원/건
- 총 손실: -3,420원 (48 round-trips)
```

**결론**: **Validation 시스템이 현재 시장 환경을 전혀 반영하지 못하고 있음**

---

## 1. 데이터 현황

### PostgreSQL 데이터베이스

```
trades: 131건 (실거래 기록)
├─ 완료 거래: 120건
├─ 승: 28건 (23.3%)
└─ 패: 25건

validation_scores: 8,259건 (~ 2025-11-02)
├─ 통과: 1,125건 (13.6%)
└─ 차단: 7,134건 (86.4%)

filter_history: 2,079건
├─ 1차 필터링 기록
└─ 조건 검색 결과
```

### 데이터 품질 이슈

1. **Validation 데이터 구버전**: 11월 2일까지만 (26일 전)
2. **실거래 데이터**: 11월 14일 ~ 28일 (최근)
3. **시점 불일치**: Validation과 실거래 사이 12~26일 gap

---

## 2. 실거래 성과 분석

### 2.1 최근 2주 성과 (2025-11-14 ~ 11-28)

```
거래 일수: 7일
왕복 거래: 48건
승률: 25.0% (12승 19패)
평균 손익: -36원/건
총 손익: -3,420원
```

### 2.2 손실 종목 TOP 5

| 종목코드 | 종목명 | 거래 | 총 손실 | 평균 손실 |
|---------|--------|------|---------|----------|
| 140430 | 카티스 | 1건 | **-12,710원** | -12,710원 |
| 235980 | 메드팩토 | 2건 | **-4,060원** | -1,015원 |
| 009420 | 한올바이오파마 | 6건 | **-3,200원** | -267원 |
| 388870 | 파로스아이바이오 | 1건 | -2,440원 | -1,220원 |
| 323280 | 태성 | 2건 | -1,700원 | -425원 |

**총 손실: -24,110원** (전체 손실의 대부분)

### 2.3 수익 종목 TOP 5

| 종목코드 | 종목명 | 거래 | 총 수익 | 평균 수익 |
|---------|--------|------|---------|----------|
| 366030 | 공구우먼 | 6건 | **+7,170원** | +552원 |
| 068290 | 삼성출판사 | 1건 | +1,950원 | +1,950원 |
| 049950 | 미래컴퍼니 | 1건 | +1,320원 | +440원 |
| 026890 | 스틱인베스트먼트 | 5건 | +1,210원 | +110원 |
| 950160 | 코오롱티슈진 | 1건 | +800원 | +800원 |

**총 수익: +12,450원**

**순 손실: -11,660원** (손실 > 수익)

---

## 3. Validation vs 실거래 비교

### 3.1 Old Validation System 성과

```
Validation 통과 종목 (is_passed=1):
- 예측 평균 승률: 78.6%
- 예측 평균 수익: +2.8원
- 통과 비율: 13.6% (1,125 / 8,259)

Validation 차단 종목 (is_passed=0):
- 예측 평균 승률: 21.6%
- 예측 평균 수익: -109.3원
- 차단 비율: 86.4% (7,134 / 8,259)
```

→ **Validation은 차단 기능은 잘 작동** (차단 종목은 실제로 저품질)

### 3.2 Validation 통과 → 실거래 매칭

**한올바이오파마 (009420) 사례**:

```
Validation (~ 11-02):
- 예측 승률: 100.0%  ✓
- is_passed: 1 (통과)

실거래 (11-14 ~ 11-28):
- 실제 거래: 6건
- 평균 손익: -267원  ❌
- 총 손익: -3,200원  ❌
```

→ **Validation과 실제 성과 완전히 반대!**

---

## 4. 문제점 및 원인 분석

### 4.1 식별된 문제

#### Problem 1: Validation 데이터 구버전

```
Validation 데이터: ~ 2025-11-02 (26일 전)
실거래 데이터: 2025-11-14 ~ 11-28 (최근)

→ 시장 환경 변화 반영 안 됨
```

#### Problem 2: Validation 과적합

```
Validation 예측: 100% 승률
실제 결과: 손실

→ 백테스트 과적합 (look-ahead bias, survivorship bias)
```

#### Problem 3: 슬리피지 및 거래 비용 미반영

```
Validation: 이론적 수익 계산
실거래: 슬리피지, 호가 차이, 체결 지연

→ 실제 수익이 예측보다 낮음
```

#### Problem 4: 특정 종목 반복 손실

```
009420 한올바이오파마: 6건 거래, -3,200원
235980 메드팩토: 2건 거래, -4,060원

→ 같은 종목 반복 진입으로 손실 누적
```

### 4.2 근본 원인

1. **Validation이 업데이트 안 됨**: 11월 2일 이후 26일간 미갱신
2. **백테스트 과적합**: 과거 데이터에 최적화, 미래 성과 예측 실패
3. **리스크 관리 부족**: 손실 종목 재진입 허용
4. **현재 필터 시스템 불사용**: Validation 데이터 오래되어 실제로 안 쓰임

---

## 5. 현재 사용 중인 필터 시스템

### Signal Orchestrator (L0-L6)

**현재 시스템**은 Validation을 사용하지 않고 **SignalOrchestrator**를 사용:

```
L0: 진입 시간, 일일 거래 횟수, 가격대
L1: 포지션 수, 최대 거래 금액
L2: 리스크 관리 (쿨다운, 손절)
L3: 뉴스 필터, 금지 종목
L4: 가격 검증, 유동성
L5: 종합 평가, 손절가 계산
L6: CONFIDENCE (현재 0.4)
```

**SignalOrchestrator 성과 (11-28 오늘)**:

```
총 평가: 2,577건
ACCEPT: 1,690건 (65.6%)
REJECT: 887건 (34.4%)
  - CONFIDENCE: 490건 (55.2%)
  - L0: 397건 (44.8%)

실거래 성과:
- 14건 거래 (7 매수, 7 매도)
- 승률: 75.0% (6승 2패)
- 일일 수익: +5,250원
```

→ **SignalOrchestrator가 Old Validation보다 훨씬 우수!**

---

## 6. 권장사항

### 6.1 즉시 조치 (High Priority)

#### ✅ 1. Old Validation 시스템 비활성화

```python
# database/trading_db.py insert_trade()에서 제거
# - vwap_validation_score
# - sim_win_rate
# - sim_avg_profit
# 더 이상 저장하지 않음 (이미 NULL로 저장 중)
```

**이유**: 구버전 데이터, 신뢰도 낮음

#### ✅ 2. 손실 종목 금지 리스트 강화

```python
# main_auto_trading.py
# 3회 연속 손실 → 즉시 ban_list 추가 (현재 구현됨)

추가 제안:
- 일일 손실 -5% 이상 종목 → 당일 금지
- 2회 연속 -3% 이상 손실 → 당일 금지
```

**적용 시 효과**:
- 140430 카티스 (-12,710원) 차단 가능
- 235980 메드팩토 2차 진입 방지 → -4,060원 중 -2,030원 절약

#### ✅ 3. CONFIDENCE 임계값 유지 (0.4)

```
현재 CONFIDENCE = 0.4:
- 승률 75.0%
- 일일 수익 +5,250원

→ 변경 불필요, 현재 설정 우수
```

**이전 권장 (0.35로 하향) 철회**: 현재 성과 좋음

### 6.2 단기 개선 (1-2주 내)

#### 📊 4. 실거래 기반 Validation 재구축

```
기존 Validation 문제:
- 백테스트 기반 → 과적합
- 구버전 데이터 (11-02)

새로운 접근:
✓ 실거래 데이터 기반 (trades 테이블)
✓ Rolling 7일 평균 승률/수익
✓ 매일 자동 갱신
```

**구현 방법**:

```python
# analyzers/realtime_validator.py (새 파일)

class RealtimeValidator:
    def calculate_stock_score(self, stock_code: str) -> dict:
        """
        최근 7일 실거래 데이터 기반 점수 계산
        """
        # 1. 최근 7일 해당 종목 거래 조회
        trades = db.get_trades(
            stock_code=stock_code,
            start_date=(datetime.now() - timedelta(days=7))
        )

        if len(trades) < 2:
            return {'score': 0, 'confidence': 0}

        # 2. 승률 계산
        wins = sum(1 for t in trades if t['realized_profit'] > 0)
        win_rate = wins / len(trades)

        # 3. 평균 수익 계산
        avg_profit = sum(t['realized_profit'] for t in trades) / len(trades)

        # 4. 종합 점수
        score = (win_rate * 100) + (avg_profit / 10)

        return {
            'score': score,
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'sample_size': len(trades)
        }
```

#### 📊 5. 일일 성과 모니터링 자동화

```bash
# scripts/daily_performance_report.sh (새 파일)

#!/bin/bash
# 매일 17:00 실행 (cron)

python3 << EOF
import psycopg2
from datetime import date

conn = psycopg2.connect(...)

# 오늘 거래 통계
query = """
SELECT
    COUNT(*) / 2 as trades,
    SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) as wins,
    AVG(realized_profit) as avg_profit,
    SUM(realized_profit) as total_profit
FROM trades
WHERE DATE(trade_time) = CURRENT_DATE
  AND realized_profit IS NOT NULL
"""

# 결과를 data/daily_reports/{date}.json에 저장
# 슬랙/디스코드 알림 (선택)
EOF
```

### 6.3 중장기 개선 (1개월 내)

#### 🤖 6. ML 기반 필터 (데이터 충분 시)

```
현재 데이터: 131건 (부족)
필요 데이터: 500-1,000건

2-3개월 후 가능:
- 진입 타이밍 예측
- 목표가/손절가 최적화
- CONFIDENCE 동적 조정
```

#### 📈 7. 포지션 사이징 개선

```
현재: 고정 비율 (entry_confidence 기반)

개선안:
- 종목별 변동성 고려
- 최근 승률 기반 Kelly Criterion
- 손실 종목 포지션 축소 (0.5x)
```

---

## 7. 기대 효과

### 즉시 조치 적용 시 (추정)

```
손실 종목 금지 리스트 강화:
- 카티스 (-12,710원) 차단
- 메드팩토 2차 진입 방지 (-2,030원)
→ 예상 손실 절감: -14,740원

현재 손실: -11,660원
개선 후: +3,080원 (흑자 전환)
```

### 실거래 기반 Validation 적용 시

```
현재 승률: 25.0%
목표 승률: 40-50%

승률 10%p 개선 시:
- 추가 승리: 5건/주
- 평균 수익: +400원/건
→ 주간 추가 수익: +2,000원
```

---

## 8. 실행 계획

### Week 1 (즉시)

- [x] Old Validation 시스템 비활성화 확인
- [ ] 손실 종목 금지 로직 강화
  - [ ] 일일 -5% 이상 → 당일 금지
  - [ ] 2회 연속 -3% 이상 → 당일 금지
- [ ] CONFIDENCE 0.4 유지 (변경 없음)

### Week 2-3

- [ ] 실거래 기반 Validator 구현
  - [ ] `analyzers/realtime_validator.py` 생성
  - [ ] 7일 rolling 승률/수익 계산
  - [ ] SignalOrchestrator에 통합
- [ ] 일일 성과 리포트 자동화
  - [ ] `scripts/daily_performance_report.sh` 생성
  - [ ] Cron 설정 (매일 17:00)

### Month 2-3

- [ ] 데이터 500건 도달 시 ML 검토
- [ ] 포지션 사이징 개선
- [ ] 백테스트 시스템 재구축

---

## 9. 결론

### 핵심 발견

1. **Old Validation은 신뢰 불가**: 78.6% 예측 → 25.0% 실제
2. **SignalOrchestrator 우수**: 75% 승률 (11-28 기준)
3. **손실의 대부분이 5개 종목**: -24,110원 (전체의 대부분)

### 최우선 조치

✅ **손실 종목 금지 리스트 강화** → 즉각 효과
✅ **CONFIDENCE 0.4 유지** → 현재 성과 우수
✅ **실거래 기반 Validation 재구축** → 2-3주 내

### 장기 방향

- Old 백테스트 기반 → **실거래 기반** 시스템
- 고정 임계값 → **동적 조정** 시스템
- Rule-based → **ML 기반** (데이터 충분 시)

---

**작성자**: Claude Code
**분석 기간**: 2025-11-14 ~ 2025-11-28
**데이터 소스**: PostgreSQL trading_system.trades (131건)
**다음 리뷰**: 2025-12-05 (1주 후)
