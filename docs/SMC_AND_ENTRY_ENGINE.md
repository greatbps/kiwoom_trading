# SMC 진입 엔진 상세 문서

> **대상 독자**: 개발자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `analyzers/smc/smc_signals.py`, `analyzers/smc/smc_structure.py`, `analyzers/smc/smc_utils.py`

---

## 1. CHoCH를 핵심으로 쓰는 이유

**BOS(Break of Structure)** vs **CHoCH(Character of Change)**:

| 항목 | BOS | CHoCH |
|------|-----|-------|
| 의미 | 추세 지속 (같은 방향 고점 돌파) | 추세 전환 (반대 방향 고점 돌파) |
| 신호 | 추세 가속 예상 | 방향 전환 예상 |
| 진입 적합성 | 추격성 (이미 상승 중) | 전환 초기 (리스크/리워드 유리) |

**왜 CHoCH가 BOS보다 우선인가**:
- CHoCH는 세력이 유동성 수집을 완료한 직후 나타남
- 진입 시점이 추세 전환 초기 → 리스크/리워드 비율 최적
- BOS는 이미 알려진 추세를 쫓는 것 → 뒤늦은 참여

---

## 2. 유동성 스윕 탐지 방식

```python
# analyzers/smc/smc_utils.py
detect_liquidity_sweep(df, direction='up')
```

**탐지 로직**:
1. 최근 N봉의 저점 중 swing_low 식별
2. 캔들이 swing_low 아래로 인트라 저가 이탈
3. 해당 캔들이 swing_low 위로 종가 마감 (꼬리)
4. 방향: 상승 CHoCH라면 저점 스윕, 하락 CHoCH라면 고점 스윕

**왜 스윕이 중요한가**:
세력은 진입 전 유동성(스탑 물량)을 수집. 저점 아래 스탑들을 청산시켜 유동성 확보 후 상승 전환. 이 패턴이 포착되면 세력 진입 신호.

---

## 3. OB 품질 점수

Order Block = CHoCH 직전 마지막 하락 캔들.

| 조건 | 판단 |
|------|------|
| 고저 범위 ≥ 0.5% | 강한 OB → A급 가산 |
| 고저 범위 0.2~0.5% | 중간 OB |
| 고저 범위 < 0.2% | 약한 OB → B급 강등 |

OB 수준(고점)이 진입 이후 구조 지지선 역할.
OB 이탈 시 → Early Failure 또는 Hard Stop 발동.

---

## 4. Reclaim 조건

CHoCH 이후 **broken level**(직전 스윙 고점)으로 되돌아온 후 반등.

```yaml
prefilter_require_reclaim: true
reclaim_lookback: 5        # CHoCH 후 5봉 이내
reclaim_tolerance_pct: 0.3 # broken level의 ±0.3% 허용
```

Reclaim의 의미: 세력이 broken level을 지지선으로 사용한다는 확인. 빠르게 이탈하는 가짜 CHoCH 필터링.

---

## 5. Prefilter 2-of-3 구조

```python
# analyzers/smc/smc_signals.py
check_entry_prefilter(choch, df, htf_trend_alive)
```

3개 조건 중 2개 이상 충족 시 통과:

| # | 조건 | 파라미터 | 담당 함수 |
|---|------|----------|-----------|
| 1 | HTF 추세 방향 일치 | `prefilter_require_htf_trend: true` | `multi_timeframe_consensus.py` |
| 2 | 유동성 스윕 존재 | `prefilter_require_liquidity_sweep: true` | `smc_utils.detect_liquidity_sweep()` |
| 3 | Reclaim 확인 | `prefilter_require_reclaim: true` | 내부 로직 |

**왜 2-of-3인가**: 완벽한 셋업(3-of-3)은 드물어 진입 기회가 급감. 1-of-3은 노이즈 증가. 2-of-3이 신뢰도와 기회 균형 최적점.

---

## 6. CHoCH 등급 평가 상세

```python
# analyzers/smc/smc_signals.py
evaluate_choch_grade(choch, df_1m, df_30m, liquidity_sweep, ob, htf_trend_alive)
```

### 등급 결정 로직

```
점수 계산:
  HTF 추세 일치 → score += (기준치)
  유동성 스윕 존재 → score += (기준치)
  강한 OB(≥0.5%) → score += (기준치)

A급: HTF + Sweep + (강한 OB 또는 변동성 수축)
B급: 일부 조건 미충족
C급: 횡보 내 CHoCH 또는 변동성 미확장
```

등급별 포지션 배율:

| 등급 | base_size 범위 | 비고 |
|------|----------------|------|
| A + Sweep | ~100% | 최대 진입 |
| B + Sweep | ~40% | 표준 진입 |
| B fallback | ~20% | Sweep 없는 경우 |
| C fallback | ~12% | 보수적 진입 |

---

## 7. Displacement Filter

CHoCH 확정봉이 진짜 displacement인지 검증.

```yaml
smc:
  displacement_filter:
    enabled: true
```

진짜 displacement 조건:
- 확정봉이 이전 N봉 대비 충분히 큰 range
- 거래량이 평균 이상
- 방향성이 명확한 캔들 (몸통 > 꼬리)

---

## 8. Primary Entry vs RAE Entry

| 항목 | Primary Entry | RAE Entry |
|------|--------------|-----------|
| 진입 시점 | CHoCH 후 OB pullback → 즉시 | CHoCH → impulse → pullback → 재가속 |
| 신뢰도 | 전환 초기, 빠른 진입 | 2nd wave, 구조 재확인 후 진입 |
| size_mult | 등급 기반 base size | Primary size × 0.7 |
| 로그 태그 | `[SMC_PRIMARY]` | `[SMC_RAE]`, `[REACCEL_ENTRY]` |
| 현재 상태 | ✅ 활성 | ❌ disabled (rae.enabled: false) |

---

## 9. BAD vs GOOD Late Entry

### BAD Late Entry (차단)

Signal Orchestrator c_late_v2 / Stage A 차단:
- bdh(당일 고저폭) ≥ 15% → Stage A 비정상 변동성
- +5% 이상 급등 후 추격
- Volume peak 이후 (momentum 소진)
- G3 HIGH_PROX: 당일 고저 범위 대비 현재가 너무 밀접

### GOOD Late Entry (RAE로 허용 예정)

현재 RAE disabled이므로 아래 조건의 late entry는 **차단됨**:
- Pullback 구조 (되돌림 후 지지)
- Reclaim 발생 (broken level 회복)
- VWAP / EMA20 지지 확인
- SMC 구조 유지
- RVOL 재확장

> RAE 활성화 시 GOOD Late Entry는 RAE 경로로 허용됨.

---

## 10. 신호가 execute_buy()까지 연결되는 흐름

```python
# main_auto_trading.py

def check_entry_signal(stock_code, df):
    # 1. 시간 필터
    if not _is_trading_hours():
        return

    # 2. Market Context
    if market_context.is_no_trade_day():
        return

    # 3. Signal Orchestrator
    orch_result = signal_orchestrator.evaluate_signal(stock_code, df)
    if orch_result['verdict'] != 'ACCEPT':
        return  # L0~L6 중 하나에서 차단됨

    # 4. SMC Strategy
    smc_signal, reason, details = smc_strategy.check_entry_signal(stock_code, df)
    if not smc_signal:
        # OB pending 등록 (smc_pending에 추가)
        return

    # 5. OB pending → CHoCH 확정 대기
    # smc_pending에 있는 종목은 pullback 대기 중
    # OB level 도달 시 다음 단계

    # 6. DrawdownEngine 체크
    if drawdown_engine.is_halted():
        return

    # 7. 포지션 사이즈 계산
    final_size = compute_final_size(base_size, ...)

    # 8. 실행
    execute_buy(stock_code, final_size, reason, details)

def execute_buy(stock_code, size, reason, details):
    # Kiwoom 주문 발송
    order_id = kiwoom.place_buy_order(...)
    # 즉시 DB INSERT (BUY 기록 누락 방지 — P0 수정 2026-05-08)
    db.insert_trade(entry_features=_pending_signal_meta)
```

**중요**: `Signal Orchestrator ACCEPT ≠ SMC 진입`. 두 조건 모두 충족해야 `execute_buy()` 도달.
