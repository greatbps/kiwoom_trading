# 키움 자동매매 시스템 - 핵심 로직 요약

## 시스템 개요

한국 주식시장(KOSPI/KOSDAQ)에서 자동으로 거래하는 시스템입니다.
- **실거래 계좌**: 약 374,989원
- **목표 수익**: 일일 2% (약 7,500원)
- **전략**: 하이브리드 전략 (트레일링 스톱 + 부분청산 + 시간대별 필터링)
- **운영 기간**: 2025년 12월 1일~현재

## 주간 성과 (12/1~12/5)

### 수익률 현황
- **주간 손익**: -1,590원 (-0.42%)
- **총 거래**: 12건 (매수/매도 쌍 기준)
- **승률**: 16.7% (2승 10패)
- **평균 보유 시간**:
  - 승리: 124분
  - 패배: 9분

### 핵심 인사이트
- **최적 거래 시간대**: 14시 (승률 100%, 1/1)
- **최악 거래 시간대**: 09~11시 (승률 9.1%, 1/11)
- **평균 승리 수익**: +1.21%
- **평균 패배 손실**: -2.03%

## 파일 구조 및 역할

### 1. main_auto_trading.py (186KB)
**역할**: 메인 오케스트레이터 - 전체 거래 프로세스 총괄

**핵심 기능**:
- Kiwoom API 연동 (OCXManager 통합)
- 실시간 시세/체결 데이터 수신
- 포지션 관리 및 주문 실행
- 리스크 관리 통합

**주요 클래스**:
```python
class AutoTrader:
    - initialize_account()  # 계좌 초기화
    - run_trading_loop()    # 메인 거래 루프
    - handle_real_data()    # 실시간 데이터 처리
    - execute_entry()       # 진입 신호 실행
    - check_exit_signals()  # 청산 신호 체크
```

**설정 통합**:
```python
self.config = ConfigLoader('config/strategy_hybrid.yaml')
self.risk_manager = RiskManager(
    initial_balance=self.current_cash,
    config=self.config.config  # 🔧 Config 연동
)
```

### 2. signal_orchestrator.py (26KB)
**역할**: 다층 필터링 시스템 - 진입 신호 검증

**6단계 필터링 시스템** (L0~L6):
```python
L0: 리스크 관리 필터
    - 일일/주간 손실 한도 체크
    - 최대 포지션 수 체크
    - 쿨다운 상태 체크

L1: 시간 필터
    - 장 초반 30분 회피 (09:00~09:30)
    - 장 마감 21분 회피 (14:59~15:20)
    - 시간대별 가중치 적용:
      * 09:30~12:00: 0.5x (승률 9.1%)
      * 14:00~14:59: 1.5x (승률 100%)

L2: 기술적 필터
    - 추세 필터 (EMA5 > EMA20)
    - 거래량 필터 (평균 대비 1.2배 이상)
    - 변동성 필터 (ATR 0.5%~4.5%)

L3: 재진입 방지
    - 동일 종목 20분 쿨다운

L4: VWAP 필터
    - VWAP 대비 가격 위치 확인

L5: 포지션 사이즈 계산
    - ATR 기반 동적 사이즈 조정
    - 리스크당 1% 제한

L6: 최종 승인
    - 모든 필터 통과 확인
```

**시간대별 가중치 (ML 기반)**:
```python
if 9.5 <= hour < 12:
    signal_strength *= 0.5  # Morning penalty
elif 14 <= hour < 15:
    signal_strength *= 1.5  # Afternoon bonus
```

### 3. exit_logic_optimized.py (14KB)
**역할**: 청산 로직 - 수익 보호 및 손실 제한

**다층 청산 전략**:

#### A. 부분 청산 (Partial Exit)
```python
# 📊 실제 평균 승리 수익 +1.21% 기반 최적화
Tier 1: +0.7% (평균의 60%) → 40% 청산
Tier 2: +1.1% (평균의 90%) → 40% 청산
잔여 20% → 트레일링 스톱으로 추가 수익 추구
```

#### B. 트레일링 스톱
```python
# 📊 실제 데이터 기반 타이트한 설정
활성화: +0.5% (평균 승리의 40%)
거리: 최고가 대비 -0.8%
최소 보장 수익: +0.3%
```

#### C. 초기 실패 컷 (Early Failure Cut)
```python
# 📊 실제 평균 손실 -2.03% 기반
시간 윈도우: 30분 (평균 승리 보유 124분 고려)
손절 기준: -1.6% (평균 손실의 80%)
목적: 초단타 손실 방지 (1~2분 보유 → 모두 실패)
```

#### D. 시간 기반 청산
```python
15:00 청산:
    if allow_overnight_final_confirm == True:
        pass  # EOD Manager 승인 종목은 제외
    else:
        전량 청산  # 익일 보유 미승인 종목

15:05 강제 청산:
    모든 포지션 전량 청산 (API 지연 고려)
```

#### E. 하드 스톱
```python
# 📊 실제 거래 데이터 기반 강화
Hard Stop: -2.5% (기존 -3.0%에서 강화)
Technical Stop: -1.5%
```

### 4. signal_detector.py (18KB)
**역할**: 기술적 신호 탐지 - 진입 기회 식별

**탐지 신호**:
```python
1. 브레이크아웃 신호
   - 저항선 돌파 + 거래량 증가

2. 추세 신호
   - EMA 정배열 (5 > 20)
   - 상승 추세 지속

3. 거래량 신호
   - Z-score 기반 비정상 거래량 감지
   - 평균 대비 1.2배 이상

4. VWAP 신호
   - VWAP 상단 돌파
   - 매수 강도 확인
```

### 5. risk_manager.py (22KB)
**역할**: 포트폴리오 리스크 관리

**핵심 기능**:

#### A. 포지션 제한
```python
max_positions: 3개 (동시 보유)
hard_max_position: 300,000원
max_position_size_pct: 30% (계좌 대비)
min_cash_reserve_pct: 20% (최소 현금 보유)
```

#### B. 일일/주간 한도
```python
max_trades_per_day: 15건 (📊 실제 데이터 기반: 10→15)
daily_max_loss_pct: 2.0%
weekly_max_loss_pct: 5.0%
daily_target_profit_pct: 2.0% (목표 수익)
```

#### C. 연속 손실 보호
```python
max_consecutive_losses: 3건
→ 3연패 시 자동 쿨다운 20분
```

#### D. 날짜 롤오버 (🔧 CRITICAL FIX)
```python
# 매일 00:00 자동 리셋
current_date = date.today().isoformat()
if current_date != self.today:
    self.today = current_date
    self.daily_trades = []
    self.daily_realized_pnl = 0.0

    # 매주 월요일 자동 리셋
    if current_week_start != self.week_start:
        self.week_start = current_week_start
        self.weekly_trades = []
        self.weekly_realized_pnl = 0.0
```

**Config 통합** (🔧 REFACTOR):
```python
def __init__(self, initial_balance, storage_path, config=None):
    if config:
        risk_mgmt = config.get('risk_management', {})
        self.MAX_POSITIONS = risk_mgmt.get('max_positions', 5)
        self.HARD_MAX_DAILY_TRADES = risk_mgmt.get('max_trades_per_day', 10)
        # ... 모든 파라미터 config 기반으로 로드
    else:
        # 하드코딩 값 사용 (하위 호환성)
```

### 6. strategy_hybrid.yaml (6.5KB)
**역할**: 전략 설정 파일 - 모든 파라미터의 단일 소스

**주요 섹션**:

#### A. 트레일링 스톱 설정
```yaml
trailing:
  activation_pct: 1.5        # 트레일링 활성화 +1.5%
  ratio: 1.0                 # 트레일링 비율 -1.0%
  stop_loss_pct: 3.0         # 기본 손절 -3.0%

  use_profit_tier: true      # 큰 수익 시 강화
  profit_tier_threshold: 6.0 # +6% 도달 시
  profit_tier_ratio: 0.5     # 0.5%로 강화

  use_atr_based: true        # ATR 기반 트레일링
  atr_multiplier: 1.3        # ATR×1.3
```

#### B. 시간 필터 (🔧 FIX + 📊 ML)
```yaml
time_filter:
  use_time_filter: true
  avoid_early_minutes: 30    # 09:30까지 회피
  avoid_late_minutes: 21     # 14:59까지만 진입

  time_weight:
    morning_penalty: 0.5     # 09:30~12:00 신호 50% 감소 (승률 9.1%)
    afternoon_bonus: 1.5     # 14:00~14:59 신호 50% 증가 (승률 100%)
```

#### C. 리스크 관리 (📊 DATA)
```yaml
risk_management:
  initial_capital: 10000000
  daily_target_profit_pct: 2.0  # 일일 목표 2%
  daily_max_loss_pct: 2.0
  max_drawdown_pct: 10.0
  max_trades_per_day: 15        # 📊 10→15 (실제 데이터 기반)
  max_consecutive_losses: 3
  max_positions: 3              # 🔧 CRITICAL FIX (누락 항목 추가)
  position_risk_pct: 1.0
  min_rr_ratio: 1.5

  min_cash_reserve_pct: 20
  max_position_size_pct: 30
  hard_max_position: 300000
```

#### D. 초기 실패 컷 (📊 DATA)
```yaml
risk_control:
  early_failure:
    enabled: true
    window_minutes: 30       # 📊 ML: 30분 (평균 승리 보유 124분)
    loss_cut_pct: -1.6       # 📊 평균 손실 -2.03%의 80%
```

#### E. 부분 청산 (📊 DATA)
```yaml
partial_exit:
  enabled: true
  tiers:
    - profit_pct: 0.7        # 📊 평균 수익 +1.21%의 60%
      exit_ratio: 0.4
    - profit_pct: 1.1        # 📊 평균 수익 +1.21%의 90%
      exit_ratio: 0.4
```

#### F. 트레일링 스톱 (📊 DATA)
```yaml
trailing_stop:
  activation_profit_pct: 0.5 # 📊 평균 수익의 40%
  distance_pct: 0.8          # 📊 타이트하게
  min_lock_profit_pct: 0.3   # 📊 최소 수익 보장
```

### 7. trade_pattern_learner.py (7.2KB)
**역할**: 머신러닝 패턴 학습 - 실제 거래 데이터 분석

**학습 프로세스**:
```python
1. 거래 데이터 로드
   - risk_log.json에서 매수/매도 쌍 추출
   - 보유 시간, 수익률, 시간대 분석

2. 패턴 학습
   - 시간대별 승률 계산
   - 보유 시간 통계 (승리 vs 패배)
   - 포지션 사이즈 상관관계

3. 인사이트 도출
   - 최적 거래 시간대
   - 최악 거래 시간대
   - 최소 권장 보유 시간

4. 모델 저장
   - ai/models/trade_patterns.pkl에 저장
```

**학습 결과 (12/1~12/5)**:
```python
{
    'time_win_rate': {
        9: 0.14,   # 09시: 14.3% (1/7)
        10: 0.0,   # 10시: 0% (0/1)
        11: 0.0,   # 11시: 0% (0/3)
        14: 1.0    # 14시: 100% (1/1)
    },
    'hold_time_stats': {
        'win_avg': 124.01,    # 승리 평균 보유: 124분
        'win_std': 101.97,
        'loss_avg': 8.95,     # 패배 평균 보유: 9분
        'loss_std': 16.67
    },
    'insights': {
        'best_hour': 14,
        'worst_hour': 11,
        'min_hold_recommended': 37.16  # 최소 37분 보유 권장
    }
}
```

**Config 자동 적용**:
```python
# 학습된 패턴이 strategy_hybrid.yaml에 자동 반영됨
time_weight:
  morning_penalty: 0.5     # 09~11시 저승률 대응
  afternoon_bonus: 1.5     # 14시 고승률 활용

early_failure:
  window_minutes: 30       # 초단타 방지 (패배 평균 9분)
```

## 시스템 아키텍처 플로우

```
1. 시장 데이터 수신
   └─> signal_detector.py: 기술적 신호 탐지
       └─> signal_orchestrator.py: 6단계 필터링
           └─> risk_manager.py: 포지션 허가 확인
               └─> main_auto_trading.py: 주문 실행
                   └─> 포지션 모니터링 시작

2. 포지션 모니터링
   └─> exit_logic_optimized.py: 청산 조건 체크
       ├─> 부분 청산 (Tier 1, 2)
       ├─> 트레일링 스톱
       ├─> 초기 실패 컷
       ├─> 시간 기반 청산
       └─> 하드 스톱
           └─> main_auto_trading.py: 청산 주문 실행

3. 거래 종료
   └─> risk_manager.py: 거래 기록 저장
       └─> trade_pattern_learner.py: 패턴 학습 (일일 종료 시)
           └─> strategy_hybrid.yaml: 자동 최적화
```

## 핵심 최적화 내역

### 🔧 Phase 1 최적화 (2025-12-06 적용)

#### 1. Early Failure Cut 완화 (치명적 버그 수정)
**문제**:
- 코드 하드코딩: 15분, -0.6% (노이즈에 즉시 손절)
- 설정 파일 무시: 30분, -1.6%
- 결과: 패배 거래 평균 보유 9분 (노이즈에 털림)

**해결**:
```python
# exit_logic_optimized.py:38-39
self.early_failure_window = 30   # 15→30분 (노이즈 견디기)
self.early_failure_loss = -1.6   # -0.6→-1.6% (평균 손실 -2.03%의 80%)
```

**근거**:
- -0.6%는 코스닥 정상 변동폭 (노이즈)
- 평균 패배 손실 -2.03%의 80% = -1.6%가 적절
- -2.0%까지 늦추면 손절과 겹쳐서 의미 없음

**기대 효과**: 승률 16.7% → 30%+ 개선

#### 2. 오전장 진입 차단 (구조적 리스크 회피)
**문제**: 09~10시 승률 9.1% (1승 10패)

**해결**: 이미 구현됨
```python
# analyzers/signal_orchestrator.py:171-172
entry_start = time(10, 0, 0)  # 10시 이후 매수
entry_end = time(14, 59, 0)   # 14:59까지
```

**근거**:
- 오전장은 변동성 극대 (구조적 리스크)
- confidence 모델 아직 미완성
- 완전 차단이 조건부 허용보다 안전

**기대 효과**: 저품질 거래 11건 제거 → 전체 승률 대폭 개선

### 🔄 Phase 2 예정 (데이터 20~30건 후)

#### 3. 부분 청산 재설계 (보류)
**현재 유지**:
```yaml
partial_exit:
  tiers:
    - profit_pct: 0.7   # 평균 수익 +1.21%의 60%
    - profit_pct: 1.1   # 평균 수익 +1.21%의 90%
```

**Phase 1 최적화 후 데이터 재분석 필요**:
- 후보 A: 1.5% / 2.5%
- 후보 B: 2.0% / 4.0%

**보류 이유**:
- 샘플 12건으로 최적화 시 과적합
- Early Failure + 오전장 차단으로 데이터 분포 변경 예상
- 새로운 데이터 기반 재설계 필요

---

### 📝 Phase 3 보완 사항 (2025-12-06 적용)

#### 4. 3연패 시 당일 거래 중지 정책
**문제**: 3연패 후 20분 쿨다운 → 시장 상태 불일치 시 반복 손실

**해결**:
```yaml
# config/strategy_hybrid.yaml
risk_management:
  max_consecutive_losses: 3
  on_consecutive_loss_action: "halt_day"  # 당일 중지
  loss_size_reduction: 0.5                # reduce_size 선택 시 50% 축소
```

```python
# core/risk_manager.py:380-391
if self.CONSECUTIVE_LOSS_ACTION == 'halt_day':
    # 당일 거래 중지 (장 마감까지)
    self.cooldown_until = datetime.now().replace(hour=15, minute=30).isoformat()
elif self.CONSECUTIVE_LOSS_ACTION == 'reduce_size':
    # 포지션 사이즈 50% 축소
    self.position_size_multiplier = 0.5
```

**근거**:
- 3연패 = 시장 상태가 전략과 불일치
- 20분 쉬고 재진입 → 또 손실 반복
- 당일 중지 또는 포지션 축소가 안전

**기대 효과**: 연속 손실 폭주 방지, 계좌 보호

#### 5. 최소 보유 시간 강제 (30분)
**문제**: 패배 거래 평균 보유 9분 (초단타 손절)

**해결**:
```yaml
# config/strategy_hybrid.yaml
risk_control:
  min_hold_time:
    enabled: true
    minutes: 30
```

```python
# trading/exit_logic_optimized.py:136-139
below_min_hold = False
if self.min_hold_enabled and elapsed_minutes < self.min_hold_minutes:
    below_min_hold = True

# Early Failure는 최소 보유 시간 이후에만 작동
if self.early_failure_enabled and not below_min_hold:
    ...
```

**근거**:
- 패배 평균 9분 vs 승리 평균 124분
- 30분 미만 거래는 대부분 노이즈 손절
- 하드 스톱 (-2.5%)은 항상 작동 (안전장치)

**기대 효과**: 초단타 손절 방지, 추세 추종 기회 확보

---

### (이전) 시간대별 가중치 (ML 기반)
**문제**: 장 초반 거래 승률 9.1% vs 오후 100%

**해결**:
```yaml
time_weight:
  morning_penalty: 0.5   # 아침 신호 강도 50% 감소
  afternoon_bonus: 1.5   # 오후 신호 강도 50% 증가
```

**참고**: L0 필터에서 10시 이전 완전 차단으로 대체됨

### 2. 초기 실패 컷 강화
**문제**: 1~2분 보유 거래 전부 실패 (평균 손실 -2.03%)

**해결**:
```yaml
early_failure:
  window_minutes: 30     # 30분 이전
  loss_cut_pct: -1.6     # -1.6% 손실 시 즉시 청산
```

**기대 효과**: 평균 손실 -2.03% → -1.0% 개선

### 3. 부분 청산 최적화
**문제**: 평균 승리 수익 +1.21%인데 청산이 너무 늦음 (+4%, +6%)

**해결**:
```yaml
partial_exit:
  tiers:
    - profit_pct: 0.7    # +0.7%에서 40% 청산 (기존 +4%)
    - profit_pct: 1.1    # +1.1%에서 40% 청산 (기존 +6%)
```

**기대 효과**: 수익 실현율 증가, 손익비 1:1 → 1:1.5 개선

### 4. 트레일링 스톱 타이트화
**문제**: 트레일링 활성화가 너무 늦어서 수익 보호 실패

**해결**:
```yaml
trailing_stop:
  activation_profit_pct: 0.5  # +0.5%에서 활성화 (기존 +2%)
  distance_pct: 0.8           # 0.8% 거리 (타이트)
  min_lock_profit_pct: 0.3    # 최소 +0.3% 보장
```

**기대 효과**: 작은 수익이라도 확실히 확보

### 5. max_positions 버그 수정
**문제**: config 누락으로 기본값 0 → 모든 진입 차단

**해결**:
```yaml
risk_management:
  max_positions: 3  # 명시적 추가
```

**결과**: 12/2 거래 실패 → 12/5 정상 거래 재개

## 주요 버그 수정 이력

### 🔧 CRITICAL FIX #1: max_positions 누락 (12/2)
```yaml
# BEFORE: 없음 → 기본값 0
# AFTER:
max_positions: 3
```

### 🔧 CRITICAL FIX #2: 날짜 롤오버 미작동 (12/3~12/4)
```python
# BEFORE: 날짜 체크 없음
# AFTER:
current_date = date.today().isoformat()
if current_date != self.today:
    self.daily_trades = []
    self.daily_realized_pnl = 0.0
```

### 🔧 CRITICAL FIX #3: EOD 결정 무시 (전체)
```python
# BEFORE: 15:00 무조건 전량 청산
# AFTER:
if position.get('allow_overnight_final_confirm', False):
    return False, None, None  # 익일 보유 승인 종목 제외
```

### 🔧 AttributeError 수정
```python
# BEFORE:
config=self.config._config  # AttributeError

# AFTER:
config=self.config.config  # ✅
```

## GPT 분석 포인트

### 1. 승률 개선 방안
현재 16.7% → 목표 40%+
- 시간대 필터링이 충분한가?
- 신호 강도 threshold 조정 필요?
- 추가 필터 레이어 필요?

### 2. 손익비 개선
현재 1.21% : 2.03% (약 1:1.67, 불리)
- 부분 청산 threshold가 적절한가?
- 트레일링 스톱이 너무 타이트한가?
- 초기 실패 컷 -1.6%가 적절한가?

### 3. 보유 시간 패턴
승리: 평균 124분 vs 패배: 평균 9분
- 30분 윈도우가 적절한가?
- 최소 보유 시간 강제 필요?

### 4. 리스크 관리
- max_positions: 3개가 적절한가?
- 포지션당 리스크 1%가 적절한가?
- 일일 목표 2% 달성 가능성?

### 5. 전략 개선
- ML 모델 정교화 필요?
- 추가 기술 지표 필요?
- 시장 상황별 전략 전환 필요?

## 데이터 기반 근거

모든 최적화는 실제 거래 데이터(12/1~12/5, 12건)를 기반으로 계산되었습니다:

| 메트릭 | 값 | 적용 위치 |
|--------|-----|-----------|
| 평균 승리 수익 | +1.21% | partial_exit tier (0.7%, 1.1%) |
| 평균 패배 손실 | -2.03% | early_failure_cut (-1.6% = 80%) |
| 승리 평균 보유 | 124분 | window_minutes (30분 = 25%) |
| 패배 평균 보유 | 9분 | 초단타 방지 기준 |
| 14시 승률 | 100% | afternoon_bonus (1.5x) |
| 09~11시 승률 | 9.1% | morning_penalty (0.5x) |

---

**작성일**: 2025-12-06
**시스템 버전**: v2.0 (ML 통합)
**다음 목표**: 승률 40%+, 일일 수익 2% 달성
