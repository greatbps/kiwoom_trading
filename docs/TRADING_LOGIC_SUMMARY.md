# VWAP + 트레일링 스탑 매매 로직 정리

## 📋 전체 매매 흐름

```
[조건검색 & AI 통합 분석]
    ↓
[사전 매수 검증]
    ↓
[진입 시그널 감지] → VWAP 상향 돌파 + 필터
    ↓
[매수 실행]
    ↓
[포지션 관리] → 고가 추적 + 트레일링 스탑
    ↓
[청산 실행] → 6단계 매도 전략
```

---

## 🔍 1단계: 관심종목 선정

### 현재 로직
```
조건검색(HTS) → VWAP 검증 → AI 통합 분석 → watchlist.json 저장
```

- **조건검색**: 모멘텀/돌파 전략으로 1차 후보 추출 (`main_condition_filter.py`)
- **VWAP 재검증**: `core/vwap_validator.py`가 5분봉 VWAP 백테스트로 노이즈 제거
- **AI 통합 분석**: 뉴스·기술·수급·기본 분석 점수를 `analysis_engine.py`가 가중 합산
- **사전 매수 검증**: `PreTradeValidator`가 최근 5일 시뮬 결과를 확인해 승인/거부 (docs/pre_trade_validation_guide.md)

### 운영상의 주의
- 조건검색이 실패하면 watchlist가 비어 있으므로 자동 매수 루프 전에 파일 업데이트 여부를 확인한다.
- AI 분석 가중치는 `config/analysis_weights.json`에서 백테스트 결과에 따라 자동 조정되며, 백업은 `config/backups/`에 저장된다.

---

## 🎯 2단계: 진입 (매수)

### 현재 로직
```python
# 1분 루프
for stock in watchlist:
    entry_check = entry_timing_analyzer.analyze_entry_timing(stock)

    if entry_check.can_enter and pre_trade_validator.allow(stock):
        risk_ok, reason = risk_manager.can_open_position(...)
        if risk_ok:
            execute_buy(...)
```

### 진입 조건 상세

| 구분 | 조건 | 상태 |
|------|------|------|
| **필수** | 5분봉 VWAP 상향 돌파 | ✅ |
| **필터1** | MA20 상단 유지 (추세 필터) | ✅ |
| **필터2** | 거래량 20분 평균 대비 ≥ 1.2배 | ✅ |
| **필터3** | 시간 필터 (09:30~14:59) | ✅ |
| **필터4** | 일봉 20일선 위 (상위 추세) | ✅ |
| **필터5** | 변동성 ≤ 5% (ATR 기반) | ✅ |
| **필터6** | 사전 매수 검증 통과 (승률/PF 기준) | ✅ |

### 실무 팁
- 변동성 한도는 하드 스탑(-3%) 및 분할 익절 구조와 조합되므로 5% 상단을 넘기지 않는다.
- 사전 검증 실패, 시간 필터 미통과, 또는 RiskManager 경고가 발생하면 주문 시도를 중단하고 3분간 후보에서 제외한다.

---

## 💰 3단계: 포지션 관리

### 현재 로직
```python
position_plan = risk_manager.calculate_position_size(...)  # 자본 2% 리스크, 포지션 30% 제한

if entry_signal == "STRONG_BUY":
    entry_ratio = 1.0
else:
    entry_ratio = 0.7

execute_buy(quantity=position_plan.quantity * entry_ratio)

# 추가 진입 (조건부)
if profit_rate > 0.5 and still_above_vwap:
    execute_buy(quantity=position_plan.remaining_qty * stage2_ratio)
```

### 현행 파라미터
- 거래당 리스크 2%, 종목당 최대 30% (`RiskManager`)
- STRONG_BUY → 100% 진입, BUY → 70% 진입 (`TRADING_SIGNAL_LOGIC.md`)
- 변동성 상위 종목(ATR > 5%)은 stage2/3 진입을 비활성화하여 과도한 노출을 막는다.
- 사전 검증이 최소 조건만 충족했을 경우 entry_ratio를 0.5로 낮추고 RiskManager 경고 플래그를 세팅한다.

---

## 📤 4단계: 청산 (매도)

### 6단계 매도 우선순위

```python
HARD_STOP_RATE = -0.03
PARTIAL_TP1_RATE = 0.04
PARTIAL_TP2_RATE = 0.06
TRAILING_ATR_MULTIPLIER = 2
FORCE_CLOSE_TIME = "15:00"

# 1. Hard Stop (-3%) → 전량 시장가 손절
# 2. 1차 익절 (+4%) → 보유 물량 40% 청산
# 3. 2차 익절 (+6%) → 추가 40% 청산 + 트레일링 활성화
# 4. ATR 트레일링 (고가 - ATR×2) → 잔량 20% 청산
# 5. EMA + Volume Breakdown → 추세 붕괴 시 잔량 청산
# 6. 시간 기반 (15:00 이후) → 전량 청산
```

### 청산 파라미터

| 항목 | 값 | 비고 |
|------|-----|------|
| 하드 스탑 | -3% | 일일 손실 한도와 동일 |
| 1차 익절 | +4% (40%) | 원금 회수 목적 |
| 2차 익절 | +6% (40%) | 트레일링 활성화 트리거 |
| 트레일링 스탑 | 최고가 - ATR×2 | 잔량 20% 추세 추종 |
| 강제 청산 | 15:00 이후 | 장 마감 30분 전 |

### 운용 가이드
- ATR 기반 손절선이 -3%보다 넓게 계산되더라도 하드 스탑(-3%)을 우선 적용한다.
- EMA Breakdown은 HIGH 신뢰도 신호에서 즉시 발동하며, MEDIUM 신호는 손실 구간일 때만 실행한다.
- 강제 청산 직전(14:50 이후)에는 신규 진입을 차단하고 보유 종목만 관리한다.

---

## ⚙️ 5단계: 리스크 관리

### RiskManager 핵심 파라미터

```python
RISK_PER_TRADE = 0.02         # 거래당 2%
MAX_POSITION_SIZE = 0.30      # 종목당 최대 30%
MAX_POSITIONS = 5             # 동시 보유 5종목
HARD_MAX_POSITION = 200000    # 단일 포지션 20만원 절대 한도
HARD_MAX_DAILY_LOSS = -0.05   # 일일 손실 -5% 시 자동 중단
HARD_MAX_WEEKLY_LOSS = -0.03  # 주간 손실 -3% 시 포지션 축소
MAX_DAILY_TRADES = 10         # 일일 거래 제한
MIN_CASH_RESERVE = 0.20       # 현금 20% 유지
CONSECUTIVE_LOSS_LIMIT = 3    # 연속 손실 3회면 쿨다운
```

### 강화된 운용 규칙
- **주간 손실 -3%**: 신규 포지션 금지, 보유 종목은 트레일링만 유지 또는 손익 0 근처 청산. 다음 주 RiskManager 리셋까지 entry_ratio를 50%로 제한.
- **연속 손실 3건**: 1거래일 쿨다운 후 `PreTradeValidator` 기준을 강화(승률 ≥ 60%, PF ≥ 1.5).
- **거래 재개 조건**: 일일 손실을 -2% 이내로 회복하고 주간 누적 손실이 -1% 이내가 되면 정상 사이즈로 복귀.
- **데이터 품질**: 실시간 지표 수집 실패 시 해당 종목을 watchlist에서 즉시 제거했고, `risk_log.json`에 장애 원인을 기록한다.

---

## 🔁 사전 매수 검증 & 데이터 품질

- `PreTradeValidator`는 최근 5일 5분봉 백테스트로 승률·평균 수익률·Profit Factor를 확인한다.
- 거래 샘플이 2회 미만일 경우 다음 중 하나를 적용한다.
  - 진입 비중 50% 축소
  - 상위 타임프레임(30분봉)으로 보조 검증 후 조건 충족 시에만 진입
  - RiskManager 주의 플래그 기록 후 재검증 시까지 후보 리스트에서 제외
- 백테스트 피처 중 실시간 값이 아직 준비되지 않은 항목은 문서에 “임시값”으로 표기되어 있으며, 실제 거래 판단에는 반영하지 않는다 (docs/BACKTEST_PROGRESS.md 참고).

---

## 📊 전체 로직 흐름도

```
[조건검색 실행]
    ↓
[VWAP 백테스트 & AI 분석]
    ↓
[사전 검증 통과 종목 watchlist 등록]
    ↓
[실시간 1분 루프]
    ├─ RiskManager 한도 확인
    ├─ EntryTimingAnalyzer 체크
    ├─ PreTradeValidator 재검증
    ├─ 주문 실행 (OrderExecutor)
    └─ 포지션 모니터링 & 6단계 매도 전략
```

---

## 📑 참고 문서
- docs/REALTIME_SIGNAL_LOGIC.md – 실시간 매수/매도 6단계 로직
- docs/TRADING_SIGNAL_LOGIC.md – 점수 기반 시그널 및 포지션 사이징
- docs/pre_trade_validation_guide.md – 사전 검증 기준과 파라미터
- docs/BACKTEST_PROGRESS.md – 백테스트/Ranker 진행 상황 및 데이터 한계
- docs/AUTO_TRADING_SYSTEM.md – 전체 시스템 아키텍처와 모듈 설명
