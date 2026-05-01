# Kiwoom Auto Trading System — 전체 파이프라인 및 매매 로직

> 작성 목적: GPT 외부 리뷰용 시스템 설명 문서  
> 작성 기준: 실제 코드 기준 (main_auto_trading.py, smc_signals.py, exit_logic_optimized.py, strategy_hybrid.yaml)

---

## 현재 상황 (문제 인식)

- 실거래 기준 최근 연속 손실 7회 이상 누적
- 거래 자체가 거의 발생하지 않음 (SMC prefilter 차단 99% 이상)
- 발생한 1건은 구조 손절 (-1.18%)
- 리뷰 목적: 파이프라인 구조적 문제점 식별 및 개선 방향 도출

---

## A. 파이프라인 전체

---

### 1. 시스템 초기화 및 종목 선정

**실행 흐름**
```
08:45   Watchdog cron → 전날 프로세스 kill → 새 프로세스 시작
09:00~  daily_routine() 실행
        ├─ WebSocket 연결 (키움 API)
        ├─ 토큰 발급/갱신 (REST)
        ├─ 조건검색식 목록 조회 (17~23번 조건)
        └─ run_condition_filtering() → 감시 종목 확정
10:30~  monitor_and_trade() → 메인 루프 (5분 주기)
15:30   장 마감, 일일 리포트 출력
```

**종목 유니버스 구성 방식**
- 키움 HTS 조건검색식 (17~22번: 모멘텀, 23번: Bottom Pullback) 통과 종목
- 코스피/코스닥 혼합, 거래대금 필터는 조건식 내장
- 이후 VWAP 필터링, RS(Relative Strength) 스코어링 → 최종 감시 종목 확정 (보통 70~100개)
- 실시간 재필터링: 5분마다 조건검색 재실행 → 신규 종목 자동 추가

---

### 2. 시장 상태 판단 (Market Sensor)

**지수/변동성 기반 판단: 없음**
- 코스피/코스닥 지수, VIX 등 외부 시장 상태 지표를 직접 체크하는 로직 없음
- 개별 종목의 SMC 구조 + EF(Early Failure) 발생 패턴으로 간접 추론

**EF 기반 Market Sensor** (`metrics/reentry_metrics.py`)
```
EF(Early Failure) 발동 이력 누적
  → 오전 EF 2회 이상: 오후 진입 차단 (AFTERNOON_BLOCKED)
  → no_follow 타입 EF 3회 이상: 전면 차단 (RISK_OFF_DAY)
```

**현재 시장 상태 판단 한계**
- 장 전체 추세/분위기는 반영 안 됨
- EF 기반이라 "손실이 발생해야만" 감지됨 (사후 감지 구조)

---

### 3. 종목 필터링 게이트 (진입 전 순서대로 적용)

```
[Gate 0] 시간 필터
  - 10:30 이전: 모든 진입 차단
  - SMC 모드: 12:30 이후 차단

[Gate 1] 쿨다운 / 밴 리스트
  - 최근 손절 종목: exit_reason별 쿨다운 (EF=60분, Hard Stop=60분, trailing=30분)
  - early_failure 타입: 해당 종목 절대 재진입 금지

[Gate 2] Market Sensor
  - AFTERNOON_BLOCKED / RISK_OFF_DAY 상태 시 모든 진입 차단

[Gate 3] Conservative Mode (Hard Stop 발동 이후)
  - max_positions: 3 → 1
  - Hard Stop 2회 이상: 당일 거래 전면 종료 (TRADING_HALT)

[Gate 4] Loss Streak Guard (연속 손실 5회 이상)
  - 포지션 사이즈 50% 축소 (Conservative와 중첩 시 25%)
  - EF 민감도 상향 (score threshold 3→2)
```

---

### 4. HTF (Higher Timeframe) 판단

**타임프레임**: 30분봉 (5분봉 OHLCV를 30분으로 리샘플)

**추세 정의 방식 (HH/HL 패턴)**
```python
# 스윙 포인트 탐지 후 패턴 확인
is_uptrend   = (HH 패턴) AND (HL 패턴)   # 상승 추세
is_downtrend = (LH 패턴) AND (LL 패턴)   # 하락 추세
# neutral/ranging → 통과 안 함 (2026-02-10 강화)
```

**HTF 판단 결과 활용**
- Prefilter 조건 1번 (3조건 중 하나)
- CHoCH 등급 평가 점수 +25점 항목
- **2026-02-26 신규**: HTF ❌ + B급 CHoCH → 진입 금지

---

### 5. SMC 구조 탐지

**사용 타임프레임**: 5분봉 (Kiwoom REST API)

**탐지 항목**

| 구조 요소 | 탐지 방법 |
|-----------|-----------|
| Swing Point | lookback=5봉 기준 고점/저점 |
| BOS (Break of Structure) | 스윙 고점 돌파 (추세 지속) |
| CHoCH (Change of Character) | 추세 반전 방향 구조선 돌파 |
| Liquidity Sweep | 직전 스윙 저점/고점 이탈 후 복귀 |
| Order Block (OB) | CHoCH 직전 마지막 반대 방향 캔들 |

**자동 탐지**: 완전 자동 (사람 개입 없음)

**현재 취약점**
- 5분봉 노이즈에 취약 (Noise CHoCH 다수 발생)
- BOS 후 CHoCH 대기 중에도 매 5분마다 재평가 → 과다 신호

---

### 6. 진입 조건 (SMC 모드 전체 흐름)

**Step 1: Prefilter (4조건 중 2개 이상 필수)**
```
조건 1: HTF 추세 일치 (30분봉 HH/HL 또는 LH/LL)
조건 2: Liquidity Sweep 존재 (방향 일치)
조건 3: Reclaim 캔들 (깨진 구조선으로 되돌림)
조건 4: 거래량 > 20봉 평균 (볼륨 확인)

→ 2개 미만: 즉시 차단 (로그: "프리필터 차단 (0/2)")
```

**Step 2: CHoCH 등급 평가 (100점 만점)**
```
HTF 구조 일치     +25점
Liquidity Sweep   +25점 (방향 불일치 시 +10)
Order Block 품질  +20점 (강한 OB), +10점 (약한 OB)
Squeeze 수축      +15점
VWAP 위치         +15점 (현재가 > VWAP)

A급: 80점 이상 → 풀 비중 진입
B급: 50~79점   → 50% 비중 진입
C급: 50점 미만  → 진입 금지
```

**Step 3: 시간 + 등급 조합 필터**
```
B급 CHoCH:
  - 11:30 이후 → 차단 (Mod C)
  - HTF ❌     → 차단 (2026-02-26 신규)
A급 CHoCH:
  - 12:30 이후 → 차단 (Mod A)
```

**Step 4: Signal Orchestrator (별도 L0~L6 필터)**
- L0: 일일 손실 한도 초과 시 REJECT
- L1~L6: 기술적 확인 지표 (VWAP, MA, 거래량 등)
- ACCEPT 여부와 별개로 SMC prefilter 통과 필요

**진입 신호 예시 (실제 로그)**
```
CHoCH[B급](상승전환) + Liquidity Sweep Low + OB(bullish)
구조 손절가: 스윙로우 - ATR × 0.5
```

---

### 7. 리스크 관리 (포지션 사이징)

**손절 계산 방식**
```
구조 기반 손절 (기본):
  구조 손절가 = 스윙로우 - ATR × 0.5
  최대 cap: -3% (초과 시 cap 적용)

Hard Stop (안전망):
  진입가 대비 -2.0% → 즉시 전량 시장가 청산
```

**포지션 사이즈 계산**
```
기본 = (계좌 자산 × position_risk_pct 1%) / (진입가 - 구조손절가)
최대 = 계좌의 40% or 50만원 (hard cap)

조정 배율 중첩:
  B급 CHoCH:          × 0.5
  신뢰도 < 0.8:       × 0.8
  Conservative Mode:  × 0.5
  Loss Streak Guard:  × 0.5
  (중첩 최소: × 0.25까지)
```

---

### 8. 포지션 관리 / 청산

**보유 시간**
```
최소 보유: 30분 (초단타 방지, 이 안에 손절 불가 — Hard Stop 제외)
조건부 연장: HTF 일치 + ATR 확장 + 거래량 이상 중 2개 → 1.5배
```

**Early Failure Structure (EF) — 진입 후 5~15분**
```
Score 누적:
  방향 실패 (진입 반대 방향): +2점
  ATR 감쇠 (< 85%):          +1점
  거래량 고갈 (< 80%):       +1점
  MFE 부족 (< ATR×0.25):    +1점
  추종 실패 (N캔들 역행):    +1점

Score ≥ 3 → 조기 청산 (action: exit_market)

EF 서브타입:
  no_demand: MFE 부족 → 애초에 수급 없는 가짜 신호
  no_follow: MFE 있었으나 추종 실패 → 타이밍은 맞았으나 지속 실패
```

**부분 청산 (Partial Exit)**
```
설정: partial_exit.tiers (YAML)
Stage별 목표 수익률 도달 시 일부 청산
이후 trailing stop 활성화
```

**Trailing Stop**
```
활성화: 수익 +1.5% 이상
거리: 현재가 × 0.8%
최소 수익 잠금: +0.5%

ATR 단계별 강화:
  수익 0~2%: 트레일링 비활성
  수익 2~4%: ATR × 3.0 (느슨)
  수익 4%+:  ATR × 2.0 (타이트)
```

**오버나이트 강제 청산 (2026-02-15)**
```
14:50 체크:
  B급 이하 포지션 → 시장가 전량 청산
  A급 포지션     → 익일 보유 허용
```

---

### 9. 차단 / 중단 로직 요약

| 트리거 | 효과 | 해제 조건 |
|--------|------|-----------|
| EF 오전 2회 | 오후 진입 차단 | 다음날 |
| no_follow 3회 | RISK_OFF_DAY (전면 차단) | 다음날 |
| Hard Stop 1회 | Conservative Mode (size 50%, max 1개) | 다음날 |
| Hard Stop 2회 | TRADING_HALT (당일 종료) | 다음날 |
| 연손 5회 이상 | Loss Streak Guard (size 50%, EF 민감↑) | 수익 거래 발생 |
| B급 + HTF❌ | 해당 신호 차단 | 해당 신호 없어질 때 |
| B급 + 11:30 이후 | 시간 차단 | 다음날 |
| A급 + 12:30 이후 | 시간 차단 | 다음날 |

---

## B. CHoCH 로직 상세

### 등급 분류 기준 (100점 만점)

| 항목 | 배점 | 조건 |
|------|------|------|
| HTF 구조 일치 | +25 | 30분봉 HH/HL or LH/LL 패턴 |
| Liquidity Sweep | +25 | 방향 일치 sweep 존재 |
| Order Block 품질 | +20 / +10 | 범위 ≥ 0.5% / 0.2~0.5% |
| Squeeze 수축 | +15 | sqz_on=True or BB < KC |
| VWAP 위치 | +15 | 현재가 > VWAP |

- **A급 (80점 이상)**: 사실상 HTF+Sweep+OB+Squeeze or VWAP 모두 필요
- **B급 (50~79점)**: 일부 조건 미충족 — 현재 50% 비중 진입
- **C급 (50점 미만)**: 진입 금지

### 구조선 정의 방식

```
BOS: 직전 스윙 고점(롱) 또는 저점(숏) 돌파
CHoCH: 추세 반전 — 하락 추세 중 직전 스윙 고점 돌파(bullish CHoCH)
       상승 추세 중 직전 스윙 저점 돌파(bearish CHoCH)
```

### 현재 CHoCH 시스템의 구조적 문제

**1. Prefilter가 너무 넓다**
- 4개 중 2개 통과 → 실질적으로 Sweep+OB만 있으면 HTF ❌ 상태에서도 진입 가능
- 실제 데이터: 오늘 prefilter 통과 건수 = 1건 / 전체 CHoCH 감지 수천 건
- 유일하게 통과한 1건(한전산업 02/25)이 HTF ❌ + B급 → 손절

**2. CHoCH 자체가 노이즈가 많다**
- 5분봉 ranging 시장에서 매 5분 CHoCH 재감지 반복
- "Noise CHoCH" 표현이 로그에 다수 등장
- 실질적 구조 전환인지 노이즈인지 구분 어려움

**3. HTF 판단의 약점**
- 30분봉 HH/HL 패턴만으로 추세 정의 → 횡보 구간에서 무의미
- ranging 구간을 명확히 걸러내는 로직 없음

**4. B급의 역할이 불명확**
- B급 = "열등한 A급"이 아니라 현재 "노이즈 진입의 통로"로 기능
- 2026-02-26: HTF ❌ + B급 차단 적용 → 개선 중

---

## 현재 시스템의 핵심 문제 요약

| 문제 | 세부 내용 |
|------|-----------|
| **진입 신호 부족** | Prefilter 통과율 < 0.1% → 며칠째 0~1건 |
| **HTF 판단 약함** | 30분봉 HH/HL만으로는 ranging 구간 구분 불가 |
| **B급 CHoCH 남용** | HTF ❌ 상태에서도 Sweep+OB로 진입 가능했음 |
| **Market Context 없음** | 지수 추세, 시장 전체 분위기 반영 안 됨 |
| **Loss Streak 누적** | 현재 7연패 → LSG 활성 → 포지션 사이즈 25% |
| **쿨다운 과도** | 손실 후 쿨다운으로 실제 좋은 신호도 차단될 가능성 |

---

## 주요 파라미터 (현재값)

```yaml
# 진입 시간
entry_start:          10:30
smc_afternoon_cutoff: 12:30
grade_b_cutoff:       11:30

# 손절
hard_stop_pct:        2.0%
structure_stop:       스윙로우 - ATR×0.5 (최대 3%)

# 포지션
max_positions:        3 (Conservative: 1)
max_position_size:    40% or 50만원
position_risk_pct:    1.0%

# CHoCH 등급
A급 threshold:        80점
B급 threshold:        50점
B급 비중:             50%

# Prefilter
min_conditions:       2 (4개 중)

# EF
score_threshold:      3 (LSG 활성 시 2)
observe_minutes:      15분

# Conservative Mode
hard_stop_threshold:  2회 → 당일 종료

# Loss Streak Guard
threshold:            5연패
size_mult:            0.5
```

---

## GPT에게 요청할 검토 항목

1. **Prefilter 설계**: 4개 중 2개 조건 기준이 적절한가? HTF를 필수 조건으로 격상해야 하는가?
2. **CHoCH 등급 체계**: B급의 존재 의미와 허용 조건을 어떻게 재설계해야 하는가?
3. **Market Context 추가**: 지수(코스피/코스닥) 추세를 파이프라인에 통합하는 방법
4. **HTF 판단 강화**: HH/HL 외 추가 추세 확인 방법 (MA, 볼린저, 구조 등)
5. **진입 빈도 vs 품질 트레이드오프**: 현재 0~1건/일은 너무 적은가, 적정한가?
6. **Loss Streak Guard 설계**: 연패 후 복구 전략 — 사이즈 축소만으로 충분한가?

