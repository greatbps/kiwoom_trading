# CHOCH_ENTRY_REPORT — Entry Parity Migration

Iteration 9 · 2026-08-02
Regression: 466 PASS / 16 Known FAIL — RELEASE ALLOWED

---

## 완료 조건 대비

| 항목 | 목표 | 결과 | 판정 |
|---|---|---|---|
| Live Entry = Backtest Entry | 100% | 100% | ✅ |
| Entry Signal Match | ≥95% | **100.0%** | ✅ |
| Trade Match | ≥95% | 100.0% | ✅ |
| Position Schema 유지 | 100% | 무수정 | ✅ |
| Stop 전달률 | 100% | 무수정 | ✅ |
| Exit Mapping | ≥90.5% | 무수정 | ✅ |
| PF | ≥1.5 | **1.639** (Top-3) | ✅ |
| Win Rate | ≥40% | **40.7%** | ✅ |
| MDD | ≤15% | **−11.48%** | ✅ |
| Regression | PASS | 466/16 | ✅ |
| **Paper Trading** | PASS | **미착수** | ⏸ |

**Paper Trading 을 제외한 전 항목 충족.**

---

## Phase 2 — 구현

`analyzers/swing/choch_engine.py` 신규.

```python
def _adapter():
    from backtest.adapter import SMCAdapter
    from backtest.daily_scan import BEST_ADAPTER_KWARGS, BEST_CONFIG
    return SMCAdapter(BEST_CONFIG, **BEST_ADAPTER_KWARGS)
```

**중복 구현 없음.** 판정도 파라미터도 백테스트 것을 그대로 쓴다.
그래서 Signal Match 가 100.0% 가 나왔다 — 재구현했다면 이 숫자는
나오지 않았을 것이고, 그게 Pullback/CHoCH 가 갈라진 방식이었다.

### Feature Flag

```python
swing_runner._entry_engine(df, config)
   기본값 'pullback'  (현행 유지)
   'choch' 를 명시해야 교체
   그 외 값은 ValueError  ← 오타로 조용히 다른 엔진이 도는 것이 가장 위험
```

환경변수 `SWING_ENTRY_ENGINE` 또는 config `swing.entry_engine`.

### [SWING_ENTRY] 로그

```
[SWING_ENTRY] symbol=005930 engine=choch signal_type=choch score=6.0
              structure_condition=confirmed entry_price=71500.0
              stop=68200.0 trigger=True
```

---

## Phase 6 — 독립 신호 조사: **6개 전부 기각**

CHoCH 를 완화하지 않고 거래를 늘릴 수 있는지 조사했다.
공통 청산 Case3 · Top-3 · 기각 기준 PF≥1.5 · WR≥40% · MDD≥−15%.

| 신호 | 신호수 | 거래 | 승률 | PF | MDD | CHoCH 중복 | 증분거래 | 판정 |
|---|---|---|---|---|---|---|---|---|
| BOS | 1,330 | 160 | 35.0% | 1.218 | −19.06% | 5.1% | +100 | ❌ PF·WR·MDD |
| Liquidity Sweep | 1,612 | 127 | 35.4% | 1.038 | −15.91% | 0.0% | +89 | ❌ PF·WR·MDD |
| Order Block | 1,789 | 153 | 33.3% | 1.091 | −26.68% | 0.1% | +115 | ❌ PF·WR·MDD |
| False Breakout | 1,015 | 146 | 39.0% | **1.520** | −8.89% | 0.9% | +81 | ❌ WR (39.0<40) |
| Spring (Wyckoff) | 3,325 | 135 | 36.3% | 1.227 | −17.42% | 0.1% | +78 | ❌ PF·WR·MDD |
| Volatility Compression | 528 | 71 | 32.4% | 1.032 | −13.73% | 0.4% | +23 | ❌ PF·WR |

참고 — **CHoCH 단독: 59거래 · 승률 40.7% · PF 1.639 · MDD −11.48%**

### 읽는 법

6개 모두 CHoCH 와 **거의 겹치지 않는다** (중복 0.0~5.1%). 진짜 독립
신호다. 그런데 **전부 CHoCH 보다 성능이 나쁘다.**

가장 근접한 False Breakout 도 승률 39.0% 로 기준(40%)에 0.99%p 미달이고
PF 1.520 은 CHoCH 의 1.639 에 못 미친다. 거래는 +81건 늘지만 품질이
떨어진다.

⚠️ 설령 통과한 것이 있어도 바로 붙이면 안 된다. 같은 데이터에서 6개를
재면 우연히 하나는 좋아 보인다(다중비교). 통과분은 별도 out-of-sample
검증 대상으로만 올려야 한다. **이번에는 통과가 0개라 이 문제가
발생하지 않았다.**

---

## 거래 빈도에 대한 결론

CHoCH 로 교체하면 **2년 59거래 (Top-3 기준) — 월 2.5건**이다.
Pullback 의 140거래에서 절반 이하로 줄어든다.

늘리는 방법은 셋뿐이고, 셋 다 막혀 있다.

1. **CHoCH 완화** → Phase 0.2~0.3 에서 전수 기각 (과최적화 확인)
2. **독립 신호 추가** → Phase 6 에서 6개 전수 기각
3. **유니버스 확대** → 미시도. 현재 78종목 하드코딩

**3번이 유일하게 남은 경로다.**

---

## 승인 기준 (§12) 대비

| 조건 | 상태 |
|---|---|
| Entry Parity ≥95% | ✅ 100.0% |
| Paper Trading PF≥1.5 · WR≥40% · MDD≤15% | ⏸ **미착수** |
| Position Schema · stop · Exit Mapping 회귀 없음 | ✅ 무수정 |
| Funnel 정량 분석 | ✅ ENTRY_FUNNEL.md |
| 거래량 확대는 CHoCH 약화가 아닌 독립 신호로 | ✅ 6개 조사, 전부 기각 |
| 장마감 후 재시작 + dry-run 로그 확인 | ⏸ 미실시 |

**Live 반영 불가.** Paper Trading 이 남았다.

---

## 변경 파일

```
analyzers/swing/choch_engine.py          신규
swing_runner.py                          +36 (feature flag + [SWING_ENTRY] 로그)
tests/unit/test_choch_entry_engine.py    신규 8건

수정 금지 대상 전부 무수정:
  exit_logic_optimized.py · risk_manager.py · main_auto_trading.py
  POSITION_SCHEMA · _normalize_position · structure_stop_price
```

## 전제

- 신호 규칙만 검증했다. 유니버스·레짐 게이트·섹터 한도·쿨다운은
  재현하지 않았고, 실제 Live 거래는 59건보다 적다.
- Train/Validation/Test 구간 모두 파라미터 선택에 이미 사용된 데이터다.
- 청산은 일봉 스윙 근사다.
- yfinance 데이터 변동성이 실제보다 크다.
- **실거래의 95.8%인 VWAP+AI 전략은 여전히 이 논의 밖에 있다.**
