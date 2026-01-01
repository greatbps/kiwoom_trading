# 핵심 거래 로직 파일 구조

## 📁 메인 실행 파일

### `main_auto_trading.py`
**역할**: 자동매매 메인 오케스트레이터

**핵심 로직**:
- `monitor_and_trade()`: 메인 거래 루프 (1분마다 실행)
- `check_entry_signals()`: 진입 신호 체크
- `check_exit_conditions()`: 청산 조건 체크
- `execute_buy()`: 매수 실행
- `execute_sell()`: 매도 실행 (전량)
- `execute_partial_sell()`: 부분 청산 실행

**주요 플로우**:
```
1. 관심종목 업데이트 (5분마다)
2. 보유 포지션 청산 체크
3. 신규 진입 신호 탐지
4. 리스크 검증 후 매수/매도 실행
```

---

## 🎯 신호 감지 및 필터링

### `trading/signal_detector.py`
**역할**: VWAP 기반 매수 신호 감지

**핵심 로직**:
- `detect_signal()`: 메인 신호 감지
- VWAP 상향 돌파 감지
- 기술적 지표 계산 (RSI, EMA, ATR, Volume)
- 추세 필터, 거래량 필터 적용

**신호 조건**:
```python
# 매수 신호
- 종가 > VWAP (상향 돌파)
- EMA5 > EMA20 (상승 추세)
- 거래량 > 20일 평균 * 1.5 (거래량 급증)
- 변동성 필터 (0.8% < ATR < 4.0%)
```

### `trading/confidence_aggregator.py`
**역할**: 멀티 레이어 필터 신뢰도 결합

**핵심 로직**:
- `aggregate()`: L3-L6 레이어 신뢰도 가중 평균
- `calculate_position_multiplier()`: 신뢰도 기반 포지션 크기 조정

**신뢰도 계산**:
```python
# 레이어별 가중치
L3_MTF: 1.5         # Multi-Timeframe (가장 중요)
L4_LIQUIDITY: 1.0   # 유동성
L5_SQUEEZE: 1.2     # Squeeze 모멘텀
L6_VALIDATOR: 0.8   # 최종 검증

# MIN_CONFIDENCE = 0.5 (50% 미만은 진입 차단)
```

---

## 🚪 청산 로직

### `trading/exit_logic_optimized.py`
**역할**: 데이터 기반 청산 우선순위 관리

**청산 우선순위** (위에서 아래 순서):
```
0순위: Early Failure Cut
  - 30분 이내 -1.6% 손실 → 즉시 시장가 청산
  - ✅ min_hold_time 체크 제거 (2025-12-16 수정)

1순위: Hard Stop
  - -2.0% 손실 → 전량 시장가 청산
  - ✅ -2.5% → -2.0% 강화 (2025-12-16 수정)

2-3순위: 부분 청산
  - +1.5% 수익 → 30% 청산
  - +2.5% 수익 → 30% 청산
  - 나머지 40% → 트레일링 스탑

4순위: 트레일링 스탑
  - +2.5% 수익 도달 시 활성화
  - 최고가 대비 -0.8% 하락 시 청산
  - 최소 +0.3% 수익 보장

5순위: VWAP/EMA 약화 신호
  - 다중 조건 충족 시 청산 (VWAP↓ + EMA3↓ + RSI<45)
  - +2.0% 이상 수익 구간에서는 무시

6순위: 시간 기반 청산
  - 15:00 이후 전량 청산
  - 익일 보유 승인 종목 제외
```

**핵심 메서드**:
- `check_exit_signal()`: 청산 신호 체크
- `_check_vwap_exit()`: VWAP 기반 청산
- `_safe_get_price()`: 안전한 가격 추출 (바이너리 버그 방지)

---

## 🛡️ 리스크 관리

### `trading/risk_manager.py`
**역할**: 포지션 크기 및 리스크 한도 관리

**핵심 로직**:
- `calculate_position_size()`: 포지션 크기 계산
- `check_risk_limits()`: 일일/주간 손실 한도 체크
- `update_trade()`: 거래 기록 및 연속 손실 추적

**리스크 한도**:
```yaml
# 일일 한도
daily_max_loss_pct: 2.0%        # 일일 최대 손실
daily_target_profit_pct: 2.0%   # 일일 목표 수익
max_trades_per_day: 15          # 일일 최대 거래 횟수

# 주간 한도
max_weekly_loss_pct: 5.0%       # 주간 최대 손실

# 연속 손실 대응
max_consecutive_losses: 3       # 3연속 손실 시 당일 중지

# 포지션 제한
max_positions: 3                # 동시 보유 최대 3종목
position_risk_pct: 1.0%         # 종목당 리스크 1%
max_position_size_pct: 30%      # 종목당 최대 30%
```

---

## 📊 멀티 레이어 필터

### `trading/filters/base_filter.py`
**역할**: 필터 기본 클래스

**FilterResult 구조**:
```python
@dataclass
class FilterResult:
    passed: bool        # 통과 여부
    confidence: float   # 신뢰도 (0.0-1.0)
    reason: str        # 사유
```

### 레이어별 필터 (L0-L6)

#### L0: `trading/signal_orchestrator.py`
- 일일/주간 손실 한도 체크
- 최우선 차단 필터

#### L1: `trading/filters/volume_filter.py`
- 거래량 급증 검증 (1.5배 이상)
- ✅ 1.2배 → 1.5배 강화 (2025-12-15)

#### L2: `trading/filters/volatility_filter.py`
- ATR 기반 변동성 필터
- 0.8% < ATR < 4.0%
- ✅ 0.5-4.5% → 0.8-4.0% 강화 (2025-12-15)

#### L3: `trading/filters/mtf_filter.py`
- Multi-Timeframe 분석
- 3분봉 + 5분봉 추세 일치

#### L4: `trading/filters/liquidity_filter.py`
- 유동성 검증
- 최소 거래대금 체크

#### L5: `trading/filters/squeeze_filter.py`
- TTM Squeeze 모멘텀 감지
- 볼린저 밴드 수축 후 확장 포착

#### L6: `trading/filters/validator_filter.py`
- 최종 검증 필터
- RSI, 추세선 등 종합 체크

---

## ⚙️ 주문 실행

### `trading/order_executor.py`
**역할**: 키움 API 주문 실행 래퍼

**핵심 메서드**:
- `execute_buy_order()`: 매수 주문
- `execute_sell_order()`: 매도 주문
- `get_tick_size()`: 호가 단위 계산

**호가 단위 처리**:
```python
# KRX 호가 단위
1,000원 미만: 1원
1,000-5,000원: 5원
5,000-10,000원: 10원
10,000-50,000원: 50원
50,000원 이상: 100원
```

### `trading/position_tracker.py`
**역할**: 포지션 추적 및 손익 계산

**핵심 로직**:
- `add_position()`: 포지션 추가
- `update_position()`: 포지션 업데이트
- `calculate_unrealized_pnl()`: 미실현 손익 계산

---

## 🔧 설정 및 API

### `config/strategy_hybrid.yaml`
**역할**: 전략 설정 파일 (하이브리드 전략)

**주요 설정 섹션**:
```yaml
trailing:           # 트레일링 스톱 설정
filters:            # 필터 설정 (거래량, 변동성, 추세)
time_filter:        # 시간대 필터 (점심시간 차단 등)
risk_management:    # 리스크 관리 한도
risk_control:       # 손절/익절 설정
partial_exit:       # 부분 청산 설정
trailing_stop:      # 트레일링 스탑 상세
eod_policy:         # 익일 보유 정책
gap_reentry:        # 갭업 재진입 설정
```

**최신 개선 사항 (2025-12-16)**:
- ✅ hard_stop_pct: 2.5 → 2.0
- ✅ midday_penalty: 0.3 → 0.0 (12:00-14:00 완전 차단)
- ✅ trailing activation_profit_pct: 0.5 → 2.5

### `kiwoom_api.py`
**역할**: 키움증권 REST API 클라이언트

**핵심 메서드**:
```python
# 인증
get_access_token()              # OAuth 토큰 획득

# 시세 조회
get_current_price()             # 현재가
get_tick_data()                 # 분봉 데이터

# 주문
place_order()                   # 주문 실행
cancel_order()                  # 주문 취소

# 계좌
get_balance()                   # 잔고 조회
get_positions()                 # 보유 종목
get_executed_orders()           # 체결 내역 조회
```

**API 엔드포인트**:
- `/oauth/token` - 토큰 발급
- `/api/dostk/price` - 시세 조회
- `/api/dostk/order` - 주문
- `/api/dostk/acnt` - 계좌 조회
- `/api/dostk/chart` - 차트 데이터

---

## 🔄 데이터 흐름

```
1. 관심종목 스캔 (5분마다)
   ↓
2. 신호 감지 (signal_detector.py)
   ↓
3. 멀티 레이어 필터링 (L0-L6)
   ↓
4. 신뢰도 결합 (confidence_aggregator.py)
   ↓
5. 리스크 검증 (risk_manager.py)
   ↓
6. 포지션 크기 계산
   ↓
7. 주문 실행 (order_executor.py)
   ↓
8. 포지션 추적 (position_tracker.py)
   ↓
9. 청산 조건 모니터링 (exit_logic_optimized.py)
   ↓
10. 청산 실행 (early failure / hard stop / partial / trailing)
```

---

## 📝 핵심 알고리즘 요약

### 진입 전략
```
VWAP 상향 돌파
+ EMA5 > EMA20 (추세)
+ 거래량 > 평균 * 1.5 (급증)
+ 0.8% < ATR < 4.0% (적정 변동성)
+ 멀티 레이어 필터 통과
+ 신뢰도 ≥ 0.5
+ 시간대 필터 (09:30-12:00, 14:00-14:59만 허용)
→ 매수 진입
```

### 청산 전략
```
손실 우선 차단:
- 30분 이내 -1.6% → Early Failure Cut
- -2.0% 도달 → Hard Stop

수익 단계별 실현:
- +1.5% → 30% 부분 청산
- +2.5% → 30% 부분 청산
- 나머지 40% → 트레일링 스탑 (최고가 대비 -0.8%)

추세 전환 감지:
- VWAP↓ + EMA3↓ + RSI<45 → 청산

시간 기반:
- 15:00 이후 → 전량 청산 (익일 보유 제외)
```

---

## 🐛 최근 버그 수정 (2025-12-16)

### Critical Bug: Early Failure Cut 미작동
**문제**: min_hold_time 체크로 인해 0-30분 구간에서 early_failure가 작동 안 함

**수정**: `trading/exit_logic_optimized.py:147`
```python
# Before (버그)
if self.early_failure_enabled and not below_min_hold:

# After (정상)
if self.early_failure_enabled:  # min_hold 체크 제거
```

**영향**: -1.6% 조기 손절 정상 작동 → 큰 손실 방지

---

## 📚 추가 문서

- `TRADING_READINESS_REPORT.md` - 시스템 준비 상태
- `docs/EXIT_LOGIC_OPTIMIZATION_SUMMARY.md` - 청산 로직 최적화
- `docs/EOD_IMPROVEMENT_PLAN.md` - 익일 보유 개선 계획
- `README.md` - 프로젝트 전체 개요

---

**작성일**: 2025-12-16
**버전**: v2.1 (ML 개선 적용)
