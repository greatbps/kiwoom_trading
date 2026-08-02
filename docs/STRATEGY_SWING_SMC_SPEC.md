# 스윙 SMC 전략 명세서

> **대상 독자**: 운영자, 개발자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `analyzers/smc/smc_signals.py`, `config/strategy_hybrid.yaml`, `main_auto_trading.py`

---

## 1. 전략 목적

**Smart Money Concept(SMC)** 기반 스윙 전략. 기관/세력의 유동성 수집 패턴(Liquidity Sweep → CHoCH → OB)을 포착해 1~5영업일 보유 후 익절.

핵심 전제:
- 세력은 진입 전 저점 쓸어내기(Sweep) 후 방향 전환(CHoCH)
- CHoCH 이후 Order Block(이전 음봉 고점)을 지지선으로 재진입
- HTF(30분봉) 추세와 일치할 때만 진입 (방향 필터)

---

## 2. 전략 파라미터 (strategy_hybrid.yaml 기준)

| 항목 | 값 |
|------|-----|
| entry_mode | `smc` |
| min_choch_grade | `B` 이상 |
| require_liquidity_sweep | true (기본값, Sweep Fallback 제외) |
| prefilter_min_conditions | 2-of-3 |
| 최대 보유 봉수 (time_exit) | 설정값 × max_bars_mult |
| Swing Hard Stop | -12% (구조손절 미설정 시 fallback) |
| dry_run | false (실거래 중) |

---

## 3. 후보 선정 로직 (swing_runner.py — 15:35 실행)

1. **유니버스 스캔**: 설정된 종목 풀에서 일봉 데이터 수집 (lookback 120일)
2. **신호 점수화**: `analyzers/swing/signal_engine.py` — 패턴 + 추세 + 거래량 종합 점수
3. **필터링**: `score ≥ 5 AND trigger = True`
4. **Top-3 선정**: 점수 순 + 섹터 중복 방지 (같은 섹터 max 1개)
5. **주문큐 생성**: `logs/swing_orders_YYYYMMDD.json`

> AI Gate (`swing.ai_gate.min_score: 20`): AI 점수 20 미만이면 후보 제외.

---

## 4. 장중 진입 파이프라인

```
후보 종목 수신
  ↓
시간 필터 (09:00~15:30, SMC 12:30 컷오프)
  ↓
Market Context 게이트 (NO_TRADE_DAY)
  ↓
Signal Orchestrator L0~L6
  ├── G3 품질 게이트 (HIGH_PROX → soft penalty × 0.6)
  ├── Stage A (bdh > 15% 차단)
  └── c_late_v2 (늦은 진입 필터)
  ↓
SMC 전략 파이프라인
  ├── 구조 분석 (BOS/CHoCH 탐지)
  ├── 유동성 스윕 탐지
  ├── Prefilter 2-of-3
  ├── Displacement Filter (확정봉 검증)
  └── CHoCH 등급 평가 (A/B/C)
  ↓
DrawdownEngine 체크 (HALT 시 차단)
  ↓
execute_buy()
```

---

## 5. SMC 구조 세부

### 5.1 유동성 스윕 (Liquidity Sweep)

세력이 저점(지지선) 아래로 일시적 돌파 후 상승 전환하는 패턴.

- `detect_liquidity_sweep()` — `analyzers/smc/smc_utils.py`
- Sweep 방향 = CHoCH 방향과 일치해야 함
- Sweep 없으면 B급 강등 (Sweep Fallback 옵션으로 일 최대 3회 허용)

### 5.2 CHoCH (Character of Change)

트렌드 전환 신호. 이전 스윙 고점(BOS)을 상향 돌파 → 상승 CHoCH.

**BOS와의 차이**:
- BOS(Break of Structure): 추세 지속 신호 (같은 방향)
- CHoCH: 추세 전환 신호 (반대 방향으로의 전환)
- SMC에서 CHoCH를 BOS보다 우선하는 이유: 세력의 "방향 전환" 선언이기 때문. BOS는 이미 알려진 추세의 연장이지만, CHoCH는 기존 유동성을 수집한 후 새 방향을 잡는 시점.

### 5.3 CHoCH 등급 (A/B/C)

| 등급 | 조건 | 포지션 배율 | 비고 |
|------|------|-------------|------|
| **A** | HTF 추세 일치 + Sweep + 강한 OB + 변동성 수축 | ~100% | 최고 신뢰도 |
| **B** | Sweep 없거나 OB 약함 | ~40~50% | 일반적 신뢰도 |
| **C** | 횡보 내 CHoCH, 변동성 미확장 | ~12% | 낮은 신뢰도 |

등급 평가는 `evaluate_choch_grade()` — `analyzers/smc/smc_signals.py`.

### 5.4 Order Block (OB)

CHoCH 직전 마지막 하락 캔들(음봉)의 고점 = Order Block.
이 수준이 진입 지지선 역할.

OB 강도 평가 기준:
- 고저 범위 ≥ 0.5% → 강한 OB (+A급 가산)
- 고저 범위 0.2~0.5% → 중간 OB

### 5.5 Reclaim (되돌림 확인)

CHoCH 이후 N봉 내 broken level로의 되돌림 후 반등 확인.
- `reclaim_lookback`: 5봉 내
- `reclaim_tolerance_pct`: 0.3% 허용 오차

### 5.6 Prefilter 2-of-3

진입 전 3가지 조건 중 2개 이상 충족 필요:
1. **HTF Trend** — 30분봉 추세 방향 일치
2. **Liquidity Sweep** — 유동성 스윕 존재
3. **Reclaim** — broken level 되돌림 확인

"왜 2-of-3인가": 완벽한 A급 셋업은 드물지만 최소 2개 조건 충족 시 신뢰도 충분. 모든 조건 강제 시 진입 기회 과도 감소.

---

## 6. HTF / EDT / RVOL / G3 / Stage A / c_late_v2 필터

### HTF (Higher Timeframe) 필터

30분봉 기준 추세 방향 확인. `multi_timeframe_consensus.py`.

### EDT (Early Downtrend) 필터

`check_early_downtrend()` — `analyzers/smc/smc_signals.py`.
진입 초기 하락 추세 감지 시 진입 차단.

### RVOL (Relative Volume)

현재 거래량 / 최근 N일 평균 거래량.
TED에서 1.2~3.0 범위 기준 사용.

### G3 품질 게이트 (v1.3: soft penalty로 전환)

delay=0 CHoCH 진입 시 품질 검증.

| 조건 | v1.2 (이전) | v1.3 (현재) |
|------|-------------|-------------|
| bdh < 3% (HIGH_PROX) | 하드 차단 | size × 0.6 (soft penalty) |
| Phase1 (09:00~10:00) | delay ≤ 3분 허용 | 동일 |
| Phase2 (10:00~10:30) | delay ≤ 2분 허용 | 동일 |

"왜 G3가 늦은 진입 방지용인가": bdh(당일 고저폭 대비 현재가 위치)가 낮으면 이미 저점 근방에 있는 상태. 이때 매수는 추격이 아니라 구조 진입처럼 보이지만 실제론 스윕 미완성 구간.

로그: `[G3_SOFT_PENALTY]` — signal_orchestrator.py

### Stage A 품질 게이트

G3 통과 후 delay=0 잔여 이상 거래 추가 차단.
- bdh > 15% → 비정상 변동성 → 차단

### c_late_v2 (10:30~13:00 지연 진입 필터)

"왜 LCL이 Hard Stop보다 앞단에 있는가":
LCL은 진입 초기(5~30분) 손실을 조기에 차단해 Hard Stop(-2%)까지 손실이 키워지는 것을 방지. 손실 발생 속도가 빠를수록 LCL이 먼저 발동.

---

## 7. 포지션 사이징

### 기본 공식 (v1.3 단일화)

```
final_size = base × route_mult × g3_mult × dd_mult × session_mult × conservative_mult × ted_mult
final_size = clamp(final_size, 0, hard_max)
```

| 배율 | 값 |
|------|-----|
| A급 base | ~1.0 |
| B급 base | ~0.5 |
| C급 base | ~0.12 |
| RAE route_mult | × 0.7 |
| G3 soft penalty | × 0.6 |
| DD CAUTION | × 0.7 |
| DD DANGER | × 0.4 |
| DD HALT | × 0.0 |
| Conservative Mode | × 0.5 |
| TED 실패 (RAE) | × 0.8 |

구현 파일: `core/position_sizing.py` (`compute_final_size()`)

---

## 8. 청산 규칙 (요약)

상세는 `docs/RISK_AND_EXIT_FRAMEWORK.md` 참조.

| 우선순위 | 규칙 | 기준 |
|----------|------|------|
| 1 | Hard Stop | -2.0% (릴랙스 옵션 있음) |
| 2 | LCL v2.1 | 진입 초기 손실 기준 |
| 3 | Early Failure | 점수 기반 조기 청산 |
| 4 | TP1 | +2R / 25% 부분 익절 |
| 5 | TP2 | +4R / 25% 부분 익절 |
| 6 | ATR Trailing | activation 2.0%, distance 0.8% |
| 7 | Time Exit | 최대 보유 봉수 초과 |
| 8 | Overnight Exit | B급 이하 14:50 강제청산 |

---

## 9. 재진입 / 쿨다운

상세는 `docs/COOLDOWN_AND_REENTRY_MATRIX.md` 참조.

---

## 10. EXPLORATION Mode

비정형 탐색 진입. bypass_time_filter=true, max 2/day.

- AI Gate 점수 미달이어도 10% 확률로 탐색 허용
- conf_threshold: 0.55 이상
- 최근 3봉 급등(4%) / 시가+10% 초과 추격 차단
- 승률 30% 미만 시 `[EXPLORATION_KILLED]` 자동 비활성화

---

## 11. ruleset_version 변경 이력

| 버전 | 날짜 | 주요 변경 |
|------|------|-----------|
| v1.0 | 2026-04-초 | SMC 초기 구현 |
| v1.1 | 2026-07-02 | AI Gate(min=50)+EXPLORATION T+1, Baseline 운영 시작 |
| v1.2 | 2026-06-초 | CHoCH 등급 시스템, Prefilter 2-of-3, Early Failure v2 |
| v1.3 | 2026-07-05 | RAE+TED 구현, G3 soft penalty, 포지션 사이징 단일화 |

---

## 12. 현재 활성/비활성 기능

| 기능 | 상태 | 파일 |
|------|------|------|
| SMC CHoCH 진입 | ✅ 활성 | `smc_signals.py` |
| Sweep 탐지 | ✅ 활성 | `smc_utils.py` |
| Prefilter 2-of-3 | ✅ 활성 | `smc_signals.py` |
| CHoCH 등급 A/B/C | ✅ 활성 | `smc_signals.py` |
| G3 soft penalty | ✅ 활성 (v1.3) | `signal_orchestrator.py` |
| Stage A 게이트 | ✅ 활성 | `signal_orchestrator.py` |
| EXPLORATION Mode | ✅ 활성 | `main_auto_trading.py` |
| AI Gate | ✅ 활성 (min=20) | `swing_runner.py` |
| RAE | ❌ disabled | `rae_detector.py` |
| TED | ❌ disabled (RAE 종속) | `trend_expansion_detector.py` |
| ML Filter | 🔵 shadow_mode | `config/strategy_hybrid.yaml` |
| EQ ML Filter | 🔵 shadow_mode | `config/strategy_hybrid.yaml` |
| Stage B Trend Extension | ❌ disabled | 데이터 부족 |
| Conservative Mode | 조건부 활성 | Hard Stop 횟수 기반 자동 |
| Loss Streak Guard | 조건부 활성 | 연패 N회 기반 자동 |
