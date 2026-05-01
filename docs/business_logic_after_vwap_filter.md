# 2차 필터링 이후 자동매매 전체 비즈니스 로직

> **기준일**: 2026-05-01  
> **진입점**: `main_auto_trading.py`의 `check_entry_signal()` (L.4973)  
> **2차 필터링 정의**: VWAP 기반 백테스트 검증 (`PreTradeValidatorV2`)까지 통과한 종목에 대해 실행되는 로직

---

## 전체 흐름 요약

```
[2차 필터 통과 종목]
        │
        ▼
 ① check_entry_signal()          ← 진입 판단 (Gate 1~2 + 모드별 분기)
        │
        ├─ [OB Pullback 대기 중] ─→ OB Zone 진입 체크 후 _emit_signal()
        │
        ├─ [entry_mode = smc]  ─→ Signal Orchestrator (L0~L6) + SMC 신호
        │
        └─ [entry_mode = squeeze_only / squeeze_with_orderbook 등]
                │
                ▼
 ② execute_buy()                  ← 포지션 사이징 + 주문 실행
        │
        ▼
 ③ 모니터링 루프                   ← MFE/MAE 추적, 등급 승격, 피라미딩
        │
        ▼
 ④ check_exit_signal()            ← 청산 신호 판단 (8단계 우선순위)
        │
        ▼
 ⑤ execute_sell()                 ← 주문 실행 + DB 저장
        │
        ▼
 ⑥ 포스트-거래 처리               ← 쿨다운, Market Sensor, Conservative Mode 등
```

---

## 1단계: check_entry_signal() — 진입 판단

**파일**: `main_auto_trading.py:4973`

```python
async def check_entry_signal(self, stock_code: str, kiwoom_df: pd.DataFrame = None):
    """매수 신호 체크 (SignalOrchestrator 사용 - L0~L6 통합)"""
```

### 1-1. Gate 1 — Global Risk Gate (전역 리스크)

`_check_global_risk_gates(stock_code, stock_name)` 호출.  
물리적 시간 차단(10:00 이전 / 14:59 이후), Kill Switch, 일일 손실 한도, Market Sensor, DD 체크를 담당.  
실패 시 즉시 `return`.

```python
_gate_ok, _gate_reason = self._check_global_risk_gates(stock_code, stock_name)
if not _gate_ok:
    logger.debug(f"[GLOBAL_GATE] {stock_code}: {_gate_reason}")
    return
```

### 1-2. Gate 2 — Per-stock State Gate (종목 단위 상태)

`TradeStateManager.can_enter()` 호출.  
당일 손절 이력, 재진입 금지, 당일 매매 이력 등 종목별 상태를 확인.

```python
can_enter, reason = self.state_manager.can_enter(
    stock_code=stock_code,
    strategy_tag=strategy_tag,
    check_stoploss=True,
    check_invalidated=True,
    check_traded=True
)
if not can_enter:
    return
```

### 1-3. 데이터 준비

키움 5분봉 데이터(`kiwoom_df`) 우선 사용, 부족 시 Yahoo Finance fallback.  
최소 50봉 필요. 음수/0 가격 행 제거 후 VWAP, ATR, 신호 계산.

**ATR 변동성 필터**: ATR이 현재가의 5% 초과 시 진입 차단 (`[ATR_BLOCK]`).

```python
if atr_pct > 5.0:
    logger.debug(f"[ATR_BLOCK] {stock_code}: ATR {atr_pct:.2f}%")
    return
```

### 1-4. OB Pullback 대기 처리 (별도 경로)

`smc_pending` 딕셔너리에 등록된 종목은 일반 신호 체크를 건너뛰고 OB Zone 진입만 모니터링.

| 조건 | 결과 |
|------|------|
| 경과 > `timeout_minutes`(25분) | `[OB_TIMEOUT]` — 대기 취소 |
| 현재가 > CHoCH 고점 × 1.005 | `[OB_INVALID]` — 무효화 |
| OB Zone 진입 + 반응 캔들 확인 | `[OB_ENTRY]` — `_emit_signal()` 호출 |

```python
in_ob_zone = ob_entry_floor <= current_price <= ob_high * (1 + tolerance)
if in_ob_zone and reaction_ok:
    self._emit_signal(stock_code, ..., strategy='SMC_OB')
```

### 1-5. 진입 모드별 분기

YAML `squeeze_momentum.entry_mode` 값에 따라 분기:

| 모드 | 설명 |
|------|------|
| `smc` | Signal Orchestrator L0~L6 + SMC CHoCH 신호 |
| `squeeze_only` | Squeeze Momentum 필터만 사용 |
| `squeeze_with_orderbook` | Squeeze + 호가창 통합 |
| `hybrid` | SMC + Squeeze 병렬 |

---

## 1-6. Signal Orchestrator (L0~L6) — SMC 모드 전용

**파일**: `analyzers/signal_orchestrator.py`

6개 레이어가 순차적으로 실행되며, 하나라도 REJECT 시 진입 불가.

#### L0: 시스템 필터
- 진입 시간: 10:00~14:59 (평일만)
- 일일 손실: -3.0% 이상

#### L1: 장세 필터
- `VolatilityRegimeDetector` 기반 실현 변동성(RV) 체제 판별
- 신뢰도 0.0~1.0 반환

#### L3: Multi-Timeframe Consensus V2
- 1분봉 + 5분봉 + 15분봉 + 1시간봉 동시 추세 확인
- EMA, RSI, MACD 일치도 검증

#### L4: 수급/오더플로우
- 기관 Z-score ≥ 1.0, 외인 Z-score ≥ 1.0
- 주문 불균형 ≥ 0.2

#### L5: Squeeze Momentum V2
- VWAP 위 필수
- 거래량 ≥ 20봉 평균 × 0.8
- Squeeze 상태(ON/OFF) 확인
- Tier 결정: TIER_1(1.0) / TIER_2(0.7) / TIER_3(0.5)

#### L6: Pre-Trade Validator V2
- 승률 ≥ 40%, 평균 수익 ≥ 0.3%, 손익비 ≥ 1.15
- Fallback Stage: 샘플 부족 시 size 축소 (Stage1: ×0.5, Stage2: ×0.3, Stage3: 차단)

#### Confidence 결합 + Multi-Alpha Engine

L3~L6 결과를 `ConfidenceAggregator`로 통합하여 최종 신뢰도 계산.  
이후 8개 알파(VWAP, Volume, OBV, Institutional Flow, News, Momentum, Mean Reversion, Volatility) 점수가  
`aggregate_score > 0.8`을 충족해야 `ACCEPT`.

---

## 1-7. SMC 진입 신호 — check_entry_signal() in smc_signals.py

**파일**: `analyzers/smc/smc_signals.py`

Signal Orchestrator 통과 후 SMC 모드에서 실행되는 핵심 진입 판단.

### Step 1: 구조 분석 + CHoCH 탐지

`SMCStructureAnalyzer.analyze_structure()` → `detect_choch()` 호출.

- **Bullish CHoCH**: 최근 LL 이후 현재가 > 이전 HL 돌파
- **Bearish CHoCH**: 최근 HH 이후 현재가 < 이전 LH 이탈
- CHoCH 없으면 BOS(Break of Structure) 체크 후 REJECT

### Step 2: 유동성 스윕 탐지

CHoCH 발생 직전 구간에서 `detect_liquidity_sweep()` 실행.

```python
liquidity_sweep = detect_liquidity_sweep(
    df, swing_points,
    lookback=20,
    sweep_threshold_pct=0.1,
    end_idx=choch.index - 1   # CHoCH 직전까지
)
```

| 타입 | 정의 |
|------|------|
| Tier 1 | 저점 완전 돌파 후 회복 (진짜 Sweep) |
| Tier 2 | Equal Level 터치 (유사 Sweep) |

### Step 3: 프리필터 (4조건 중 2개 이상)

```python
def check_entry_prefilter(df, df_htf, choch, liquidity_sweep):
    # 조건 1: HTF 추세 생존 (30분봉 HH/HL)
    # 조건 2: 유동성 청산 확인
    # 조건 3: 되돌림 캔들 (broken level 근처)
    # 조건 4: 거래량 확인 (vol ≥ 20봉 평균)  ← F3 필터
    if conditions_met >= 2:
        return PASS
```

### Step 4: Displacement 필터

CHoCH 확정봉의 실제 변위를 검증하여 "약한 CHoCH(아슬아슬한 돌파)" 제거.  
3개 조건 중 2개 이상 충족 필요:

| 조건 | 기준 |
|------|------|
| 캔들 range | ≥ ATR × 1.2 |
| body ratio | ≥ 0.5 (몸통 비율) |
| 거래량 | ≥ 평균거래량 × 1.5 |

### Step 5: CHoCH 등급 평가 (100점 만점)

```python
def evaluate_choch_grade(df, choch, structure, liquidity_sweep, order_block, htf_trend_alive):
    score = 0
    if htf_trend_alive:          score += 15  # HTF 구조 일치
    if liquidity_sweep(방향일치): score += 25  # Sweep (방향 불일치 시 +10)
    if order_block(range≥0.5%):  score += 20  # OB 품질 (range<0.5% 시 +10)
    if sqz_on:                   score += 15  # 변동성 수축 상태
    if current_price > vwap:     score += 15  # VWAP 위 위치
```

| 등급 | 점수 | 기본 비중 | 비고 |
|------|------|----------|------|
| A | ≥80 | 100% | HTF + Sweep + OB + VWAP 복합 충족 |
| B | 50~79 | 50% | 부분 충족 |
| C | <50 | 12% | Sweep Fallback + OB 있을 때만 허용 |

**B급 추가 제한**:
- `grade_b_cutoff: "11:30"` — 11:30 이후 B급 차단 (`[HTF_B_BLOCK]`)
- `htf_b_block: true` — HTF 추세 없는 B급 차단

**F4 필터 (CHoCH 임계값 강화)**: A급 기준 70→80점, B급 기준 40→50점.

### Step 6: EMA9 눌림 필터 (선택적)

CHoCH 발생 후 추격 진입 방지. 5개 게이트:

| 게이트 | 조건 |
|--------|------|
| 0 | CHoCH 신선도: 최대 6봉 이내 |
| 1 | 추격 차단: gap > ATR × 0.8 → REJECT |
| 2 | EMA9 미도달: 현재가 > EMA9 × 1.005 → 대기 |
| 3 | 얕은 눌림 차단: depth / impulse_range < 0.3 → REJECT |
| 4 | 거래량 이중 조건: 얕은 눌림 + 거래량 과다 → REJECT |

---

## 2단계: execute_buy() — 포지션 사이징 및 주문

**파일**: `main_auto_trading.py:7723`

```python
def execute_buy(self, stock_code, stock_name, price, df,
                position_size_mult=1.0, entry_confidence=1.0,
                entry_reason=None, ...):
```

### 2-1. 진입 전 Gate 체크 (순서대로)

| 번호 | Gate | 처리 |
|------|------|------|
| 1 | **시간대 가중치** (`_get_time_weight`) | weight=0.0 → return, <1.0 → size 축소 |
| 2 | **Midday Boost** (12:00~12:30) | confidence≥0.55 → size×1.2 (`[MIDDAY_BOOST]`) |
| 3 | **금지 종목** (`stock_ban_list`) | 3회 연속 손실 종목 → return |
| 4 | **연패 쿨다운** (`_trade_cooldown`) | edge≥0.20 또는 confidence≥0.85 → bypass 허용 |
| 5 | **Market Context** | NO_TRADE_DAY → size×0.3, ATR 모드별 배율 조정 |
| 6 | **Entry Quality 필터** (EQ-1~4) | RVOL≥1.7, EMA9 눌림, VWAP 거리≤1.8%, S/D 필터 |
| 7 | **포지션 위치 필터** | 돌파점 이격≤2.0%, EMA20 이격≤3.0% |
| 8 | **레짐 차단** | CHOP/REVERSAL → REJECT |
| 9 | **보수 모드** (`conservative_mode`) | max_positions=1, size×0.5 |
| 10 | **기존 포지션 중복** | `stock_code in self.positions` → return |
| 11 | **일일 거래 한도** | `daily_trade_count ≥ max_trades_per_stock_per_day` |
| 12 | **Drift Detector** | EMERGENCY_STOP → 차단, REDUCE_SIZE → size 감산 |
| 13 | **ML 진입 필터** | `shadow_mode=false` + prob<0.40 → 차단 (rollout_pct 확률) |

### 2-2. 포지션 사이징 계산

`RiskManager.calculate_position_size()` 기반으로 기본 수량 계산 후 단계적 배율 적용.

```
기본수량 (risk_per_trade=2%, structure_stop 기반)
    × 신뢰도 배율
    × 주간 손실 조정 (주간 -3% 초과 시 ×0.5)
    × Loss Streak Guard 배율 (연패 5회 이상 시 ×0.5)
    × 시간대 가중치 (_time_weight)
    × Midday Boost (12:00~12:30 + conf≥0.55 시 ×1.2)
    × Market Context (NO_TRADE_DAY: ×0.3)
    × EC Drawdown 배율 (5단계, ×0.3~1.5)
    × Kelly Criterion (초반 20거래 미만: 고정 0.6)
    × Conservative Mode (Hard Stop 발동 시 ×0.5)
    × Drift Detector (REDUCE_SIZE 시 ×0.5~1.0)
    → 최종 수량 (최소 1주 보장)
```

### 2-3. 주문 실행

호가단위 조정(`_adjust_price_to_tick`) 후 KIS API 지정가 주문.

```python
order_result = self.api.order_buy(
    stock_code=stock_code,
    quantity=quantity,
    price=buy_price,
    trade_type="0"   # 지정가
)
```

### 2-4. 포지션 생성

```python
self.positions[stock_code] = {
    'stock_name': stock_name,
    'entry_price': price,
    'entry_time': entry_time,
    'quantity': quantity,
    'highest_price': price,      # MFE 추적용
    'trailing_active': False,
    'choch_grade': choch_grade,  # A / B / C
    'structure_stop_price': structure_stop_price,
    'atr_at_entry': df['atr'].iloc[-1],
    'partial_exit_stage': 0,     # 0: 미진행, 1: TP1 완료, 2: TP2 완료
    'r_tp1_price': ...,          # 1.5R 익절 목표가
    'r_tp2_price': ...,          # 3.0R 익절 목표가
    'r_pct': ...,                # R 크기 (%)
    'mfe_pct': 0.0,
    'mae_pct': 0.0,
    'allow_overnight': ...,      # 진입 시 판단
    'score_supply_demand': ...,
    'entry_confidence': entry_confidence,
    ...
}
```

### 2-5. DB 저장

- `trades` 테이블에 BUY 레코드 저장
- `decision_trace.record_entry_signal()` — 진입 신호 ID 생성
- `RiskManager.record_trade()` — 리스크 관리 기록

---

## 3단계: 모니터링 루프 — 포지션 보유 중

메인 이벤트 루프에서 매 틱(키움 실시간 체결/호가)마다 실행.

### 3-1. MFE/MAE 실시간 업데이트

```python
if current_price > position['highest_price']:
    position['highest_price'] = current_price
    position['mfe_pct'] = (current_price - entry_price) / entry_price * 100

# MAE: 진입 이후 최대 역방향 움직임
if current_price < position.get('trough_price', entry_price):
    position['trough_price'] = current_price
    position['mae_pct'] = (entry_price - current_price) / entry_price * 100
```

### 3-2. 등급 승격 — `_maybe_upgrade_grade()`

**파일**: `main_auto_trading.py:9069`

조건: 수익률 ≥ 3.0% **AND** 수급점수 ≥ 60점

```python
if profit_pct >= upg_profit and sd_score >= upg_sd:
    # 과열 필터 3단계 통과 시 승격
    position['choch_grade'] = 'A'
    position['allow_overnight'] = True   # overnight 자동 승인
    position['grade_upgraded'] = True
```

**과열 필터** (승격 차단 조건):
- EMA5 단독 이격 ≥ 5% → 차단 (`[UPGRADE_BLOCK]`)
- EMA20 이격 ≥ 5% AND EMA5 이격 ≥ 3% 동시 → 차단

### 3-3. 피라미딩 — `_maybe_pyramid_add()`

**파일**: `main_auto_trading.py:9153`

2단계 피라미딩 (`pyramiding.enabled: true`):

| 단계 | 수익 조건 | 추가 비중 | 추가 조건 |
|------|----------|----------|----------|
| STEP 1 | A급 ≥0.5% / 기타 ≥1.5% | 초기수량 × 15% | 현재가 > EMA20 + 강한 캔들 |
| STEP 2 | ≥3.0% | 초기수량 × 20% | 현재가 > EMA20 + ATR 확장 + 수급 ≥40 |

**레짐별 제한**:

| 레짐 | 처리 |
|------|------|
| TREND | STEP1 + STEP2 전체 허용 |
| CHOP | STEP1만 허용 (RVOL≥1.5 + VWAP 위 조건 추가) |
| REVERSAL | 전면 차단 |

**리스크 캡**: `current_qty ≥ initial_qty × 1.5` → 차단

---

## 4단계: check_exit_signal() — 청산 신호 판단

**파일**: `trading/exit_logic_optimized.py:403`

8단계 우선순위로 청산 신호를 판단. 상위 조건이 먼저 체크되며, 발동 시 하위 조건은 실행하지 않음.

```python
def check_exit_signal(self, position, current_price, df):
    """
    우선순위:
      1. 5분 최소 락
      2. 긴급 Hard Stop (갭다운 ≤ -6%)
      3. 구조 손절 / Hard Stop (≤ -5%)
      4. TP1 후 BE 스탑
      5. R-TP1 부분 익절 (1.5R → 50%)
      6. R-TP2 부분 익절 (3R → 잔여 50%) + 트레일링 ON
      7. A급 연장 보호 장치
      8. ATR 트레일링 (3단 tightening)
      9. EOD 15:00 시간 청산
    """
```

### 우선순위 1: 최소 락 5분

```python
_min_hold = self.config.get('risk_control.min_hold_minutes', 5)
if elapsed_minutes < _min_hold:
    return False, f"최소락 ({elapsed_minutes:.1f}/{_min_hold}분)", None
```

노이즈(단기 스파이크)에 의한 즉시 청산 방지.

### 우선순위 2: 긴급 Hard Stop

```python
_emg_pct = self.config.get('risk_control.emergency_stop_pct', 6.0)
if profit_pct <= -_emg_pct:
    # 1봉 유예: 직전봉 저가 이탈 확인 후 발동 (스파이크 vs 실제 붕괴 구분)
    if _candle_confirms_breakdown:
        return True, f"[HARD_STOP_EMERGENCY]", {'use_market_order': True}
```

시장가 주문으로 즉시 청산. 갭다운·수직 낙하 시 구조 손절이 대응 못하는 케이스 방어.

### 우선순위 3: 구조 손절 / Hard Stop

**구조 손절** (structure_stop_price 존재 시):

```python
# max_stop_pct(-5%) cap 적용
# A/A+급 + entry_confidence≥0.7 + TP1 전: 손절 0.5% 완화 ([A_STOP_BUFFER])
if current_price <= structure_stop_price:
    return True, "[STRUCTURE_STOP]", {'use_market_order': True}
```

**% 기반 Hard Stop** (structure_stop 없을 때):

```python
if profit_pct <= -max_stop_pct:   # 기본 -5%
    return True, "[HARD_STOP]", {'use_market_order': True}
```

### 우선순위 4: TP1 이후 BE 스탑

```python
if partial_stage >= 1:
    _be_stop = entry_price * (1 + _be_buffer / 100)  # entry + 0.2%
    if current_price <= _be_stop:
        return True, "[BE_STOP]"
```

TP1(부분 익절) 완료 후 손실 방지를 위한 본전(BE) 손절.  
+0.2% 버퍼: 한국장 호가/슬리피지 방어.

### 우선순위 5: R-기반 부분 익절

```python
# TP1: 1.5R 도달 시 50% 부분 청산
if partial_stage < 1 and current_price >= r_tp1_price:
    return False, "[R_TP1]", {'partial_exit': True, 'stage': 1, 'exit_ratio': 0.5}

# TP2: 3.0R 도달 시 잔여 50% 청산 + 트레일링 ON
if partial_stage == 1 and current_price >= r_tp2_price:
    position['trailing_active'] = True
    return False, "[R_TP2]", {'partial_exit': True, 'stage': 2, 'exit_ratio': 0.5}
```

`return False` = 전량 청산 아님. `execute_sell()` 내부에서 `exit_ratio` 기반 부분 청산 처리.

### 우선순위 6: A급 연장 보호 장치

A급(`eq_grade='A'` AND `choch_grade='A/A+'`) 포지션 전용. 4개 하위 장치:

#### ① STRUCTURE_EXIT — bearish CHoCH 재발 시 전량 청산
```python
from analyzers.smc.smc_structure import SMCStructureAnalyzer
_choch = SMCStructureAnalyzer().detect_choch(df, structure)
if _choch and _choch.direction == 'bearish':
    return True, "[STRUCTURE_EXIT]"
```

#### ② PROFIT_LOCK 티어형 — MFE 달성도에 따른 이익 보호

| MFE 달성 | 보호선(floor) |
|----------|-------------|
| ≥ 1.5R | entry + 0.5R |
| ≥ 2.5R | entry + 1.0R |
| ≥ 4.0R | entry + 2.0R |

```python
if current_price < _lock_floor_price:
    return True, f"[PROFIT_LOCK] MFE={_mfe_r:.1f}R → 보호선=+{_floor_pct:.2f}%"
```

#### ③ NO_PROGRESS_EXIT — 1.5R 미달 + N봉 경과
```python
if _mfe_r < 1.5 and _bars_since >= 15:
    return True, "[NO_PROGRESS_EXIT]"
```
힘 없는 거래를 조기에 컷.

#### ④ A_FORCE_EXIT — 최대 보유 봉수 초과
```python
_a_max = time_exit_bars * max_bars_mult   # 기본: 10봉 × 2 = 20봉(100분)
if _bars_since >= _a_max:
    return True, "[A_FORCE_EXIT]"
```

### 우선순위 7: ATR 트레일링 (3단 tightening)

```python
_trailing_on_pct = config.get('risk_control.trailing_activation_pct', 2.0)
if position.get('trailing_active') or profit_pct >= _trailing_on_pct:
    position['trailing_active'] = True

    # 3단 tightening
    if profit_pct >= 8.0:   atr_multiplier = 2.0   # +8%+: 타이트
    elif profit_pct >= 5.0: atr_multiplier = 2.5   # +5~8%: 중간
    else:                   atr_multiplier = 3.0   # +2~5%: 느슨

    trailing_stop_price = highest_price - atr_value * atr_multiplier

    # TP1 이후: BE+0.2% 보장
    if partial_stage >= 1:
        trailing_stop_price = max(trailing_stop_price, entry_price * 1.002)

    if current_price <= trailing_stop_price:
        return True, f"[TRAILING_STOP] ATR×{atr_multiplier}"
```

### 우선순위 8: EOD 시간 기반 청산

```python
if current_time >= time(15, 0):
    return True, f"시간 기반 청산 (15:00, {profit_pct:+.2f}%)"
```

`allow_overnight_final_confirm=True`인 포지션은 시간 청산에서 제외 (오버나이트 보유).

---

## 5단계: execute_sell() — 청산 실행

**파일**: `main_auto_trading.py:10211`

### 5-1. 사전 검증

```python
# ① 장 종료 여부
if not self.is_market_open():
    console.print("[red]❌ 장 종료 시간[/red]")
    return

# ② 점심시간(12:00~14:00) 수익 청산 차단 (손절은 허용)
if MIDDAY_START <= current_time < MIDDAY_END and profit_pct > 0:
    return

# ③ 실제 보유 수량 확인 (API: ka01690, 필드: stk_cd / rmnd_qty)
actual_qty = int(holding.get('rmnd_qty', 0))
if actual_qty != position['quantity']:
    position['quantity'] = actual_qty   # 불일치 시 실제 수량으로 동기화
if actual_qty == 0:
    del self.positions[stock_code]      # 이미 청산됨
    return
```

### 5-2. DB 저장

```python
sell_trade = {
    'trade_type': 'SELL',
    'price': float(price),
    'quantity': int(position['quantity']),
    'exit_reason': reason,
    'realized_profit': float(realized_profit),
    'profit_rate': float(profit_pct),
    'holding_minutes': int(holding_duration // 60),
    'exit_context': {
        'mfe_pct': (highest - entry) / entry * 100,
        'exit_vs_mfe': (price - highest) / entry * 100   # MFE 대비 청산 위치
    }
}
self.db.insert_trade(sell_trade)
# decision_trace.record_exit_signal() — ML 데이터셋 자동 생성
```

### 5-3. 주문 실행

```python
for _sell_attempt in range(2):   # 토큰 만료 시 1회 재시도
    try:
        if use_market_order:
            # 긴급 손절: 시장가
            order_result = self.api.order_sell(stock_code, quantity, price=0, trade_type="3")
        else:
            # 일반 청산: 현재가 -0.5% 지정가
            sell_price = self._adjust_price_to_tick(price * 0.995)
            order_result = self.api.order_sell(stock_code, quantity, sell_price, trade_type="0")
        break
    except Exception as e:
        if _sell_attempt == 0 and '8005' in str(e):   # 토큰 만료
            self.refresh_access_token()
            continue
        return
```

---

## 6단계: 포스트-거래 처리

주문 성공 확인 후 순서대로 실행.

### 6-1. 일일 PnL 누적 및 한도 체크

```python
self._daily_pnl_pct += profit_pct   # 오버나이트 포지션은 include_overnight=false 시 제외

if self._daily_pnl_pct <= _limit:   # 기본 -3.0%
    self._daily_loss_halted = True   # [DAILY_LOSS_LIMIT] → 당일 거래 종료
```

### 6-2. 연속 손실 관리 및 금지 종목 등록

```python
if is_win:
    self.stock_loss_streak[stock_code] = 0   # 리셋
else:
    self.stock_loss_streak[stock_code] += 1

    # 금지 조건 (당일 진입 차단)
    # 1. 단일 대손실: profit_pct <= -5.0%
    # 2. 연속 중손실: streak >= 2 AND profit_pct <= -3.0%
    # 3. 3회 연속 손실
    if should_ban:
        self.stock_ban_list.add(stock_code)
        # data/cooldown.lock 파일 생성 (다음날 0시까지)
```

### 6-3. 차등 쿨다운 설정

`_categorize_exit_reason(reason)` → 카테고리 분류 후 YAML 기반 쿨다운 시간 적용.

```python
self.stock_cooldown[stock_code] = (datetime.now(), is_loss, reason)
```

| exit_reason 카테고리 | 쿨다운 |
|---------------------|--------|
| `ef_no_follow` | 15분 |
| `ef_no_demand` | 30분 |
| `hard_stop` | 60분 |
| `stop_loss` | 30분 |
| `trailing_stop` | 10분 |
| `time_exit` | 20분 |
| `take_profit` | 5분 |
| `default` | 30분 |

### 6-4. Market Sensor 업데이트 (EF 발동 시)

```python
if reason_cat in ('ef_no_follow', 'ef_no_demand'):
    ms_result = self.reentry_metrics.record_ef_event(ef_subtype, ms_config)
```

**Market Sensor 규칙**:
- 오전 EF ≥ 2회 → `[AFTERNOON_BLOCKED]` (12:00+ 신규 진입 차단)
- no_follow ≥ 3회 → `[RISK_OFF_DAY]` (전일 전면 차단)

### 6-5. Conservative Mode 활성화 (Hard Stop 시)

```python
if reason_cat == 'hard_stop':
    cm_result = self.reentry_metrics.record_hard_stop_event(cm_config, ...)
    # Hard Stop 1회: 보수 모드 ON (max_positions=1, size×0.5, cooldown×1.5)
    # Hard Stop 2회: [TRADING_HALT] 당일 거래 종료
```

### 6-6. Loss Streak Guard 상태 업데이트

```python
self._check_loss_streak_guard()
# consecutive_losses >= 5 → LSG 발동
# 효과: size×0.5, EF threshold 강화, Market Sensor no_follow 임계 축소
# 수익 거래 발생 또는 auto_reset_days(3일) 경과 시 자동 해제
```

### 6-7. TradeStateManager 기록

```python
if is_stoploss:
    self.state_manager.mark_stoploss(stock_code, ...)   # 손절 이력 기록
else:
    self.state_manager.mark_traded(stock_code, ...)     # 당일 매매 이력 기록
```

### 6-8. 포지션 제거 및 상태 저장

```python
del self.positions[stock_code]
self._save_positions_state()   # 재시작 복원용 상태 파일 갱신
```

### 6-9. 전략별 성과 기록

```python
# DriftDetector: 전략 태그별 PnL 누적 (전략 붕괴 감지)
# TradeStats: 수익/손실 통계
# SqzPatternStats: Squeeze 패턴 학습 (squeeze 전략 전용)
# RegimeEngine: 레짐별 성과 기록 (DEF/RS 동적 사이징용)
```

---

## 오버나이트 처리 — force_close_overnight()

**파일**: `main_auto_trading.py:9590`  
**실행 시점**: 모니터링 루프에서 14:50 감지 시

```python
async def force_close_overnight():
    # B급 이하 포지션만 강제 청산 (A급은 overnight 허용)
    # config: overnight_close.enabled, exempt_grades=['A'], use_market_order=True
    for stock_code, position in self.positions.items():
        grade = position.get('choch_grade', 'C')
        if grade not in exempt_grades:
            await self.execute_sell(stock_code, price, ..., use_market_order=True)
```

**배경**: overnight_close 도입 이유 — B급 이하 전일 오후 진입 종목의 익일 갭다운이 주간 손실의 65%를 차지.

---

## EOD 처리 — handle_eod()

**파일**: `main_auto_trading.py:9706`  
**실행 시점**: `force_close_overnight()` 이후

- `allow_overnight_final_confirm=True` 포지션 최종 점검
- 일일 리포트 생성 (`reentry_metrics.print_report()`)
- `risk_log.json` 업데이트 (consecutive_losses, 일일 거래 기록)
- 다음날 대비 상태 초기화

---

## 주요 설정 파라미터 요약 (strategy_hybrid.yaml)

```yaml
risk_control:
  min_hold_minutes: 5            # 최소 락
  emergency_stop_pct: 6.0        # 긴급 Hard Stop
  hard_stop_pct: 5.0             # % 기반 Hard Stop
  be_stop_buffer_pct: 0.2        # BE 스탑 버퍼
  trailing_activation_pct: 2.0   # 트레일링 발동 수익률
  trailing_tiers:
    base_mult: 3.0               # +2~5%: ATR×3.0
    tier1_profit: 5.0            # 1단 기준
    tier1_mult: 2.5              # +5~8%: ATR×2.5
    tier2_profit: 8.0            # 2단 기준
    tier2_mult: 2.0              # +8%+: ATR×2.0

smc:
  smc_afternoon_cutoff: "12:30"  # SMC 진입 마감
  choch_grade:
    min_grade: B
    grade_b_cutoff: "11:30"      # B급 마감
    htf_b_block: true

overnight_close:
  enabled: true
  exempt_grades: ['A']           # A급만 overnight 허용
  use_market_order: true

re_entry:
  reentry_cooldown:
    by_exit_reason:
      ef_no_follow: 15
      ef_no_demand: 30
      hard_stop: 60
      stop_loss: 30
      trailing_stop: 10

risk_control:
  conservative_mode:
    enabled: true
    trading_halt_threshold: 2    # Hard Stop 2회 → 당일 종료
    max_positions: 1
    position_size_mult: 0.5
  loss_streak_guard:
    enabled: true
    consecutive_loss_threshold: 5
    position_size_mult: 0.5
    auto_reset_days: 3
```

---

## 로그 태그 빠른 참조

| 태그 | 의미 |
|------|------|
| `[GLOBAL_GATE]` | Global Risk Gate 차단 |
| `[STOCK_GATE]` | Per-stock State Gate 차단 |
| `[ATR_BLOCK]` | ATR 5% 초과 차단 |
| `[OB_ENTRY]` | OB Pullback 진입 |
| `[OB_TIMEOUT]` | OB 대기 타임아웃 |
| `[TIME_WEIGHT]` | 시간대 배율 적용 |
| `[MIDDAY_BOOST]` | 12:00~12:30 사이즈 부스트 |
| `[BAN_LIST_BLOCK]` | 3회 손실 금지 종목 차단 |
| `[GRADE_UPGRADE]` | 등급 승격 (B→A) |
| `[PYRAMID_BLOCK]` | 피라미딩 차단 |
| `[HARD_STOP_EMERGENCY]` | 긴급 손절 (-6%) |
| `[STRUCTURE_STOP]` | 구조 손절 |
| `[HARD_STOP]` | % 기반 손절 |
| `[BE_STOP]` | TP1 후 본전 손절 |
| `[R_TP1]` / `[R_TP2]` | R배수 부분 익절 |
| `[STRUCTURE_EXIT]` | A급 bearish CHoCH 청산 |
| `[PROFIT_LOCK]` | A급 이익 보호선 청산 |
| `[NO_PROGRESS_EXIT]` | A급 진전 없음 청산 |
| `[A_FORCE_EXIT]` | A급 강제 시간 청산 |
| `[TRAILING_STOP]` | ATR 트레일링 청산 |
| `[AFTERNOON_BLOCKED]` | Market Sensor 오후 차단 |
| `[RISK_OFF_DAY]` | Market Sensor 전면 차단 |
| `[TRADING_HALT]` | Hard Stop 2회 당일 종료 |
| `[DAILY_LOSS_LIMIT]` | 일일 손실 한도 초과 |
