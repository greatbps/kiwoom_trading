# Iteration 8 — Swing Entry Parity Validation

문서 버전: v1.0
생성: 2026-08-02
Regression: 466 PASS / 16 Known FAIL — RELEASE ALLOWED
**운영 코드 무수정 (분석 전용)**

---

## 결론

**Entry Parity 는 달성하지 못했다. 그리고 Live 진입 규칙을 채택해서는
안 된다는 근거가 나왔다.**

Live 의 `SWING:pullback` 진입을 백테스트에서 그대로 돌린 결과,
**PF 1.126 · 승률 32.9% · Train 구간 PF 0.966(손실)** 이다.
백테스트의 CHoCH 진입(PF 1.639 · 승률 40.7%)에 모든 구간에서 뒤진다.

---

## 1. Live Swing Entry Pipeline 분석

```
[15:35 cron] swing_runner.py
   └→ SignalEngine (analyzers/swing/signal_engine.py)
        └→ PatternManager
             ├ CupHandleDetector   가중치 1.2
             ├ PullbackDetector    가중치 1.0
             └ BoxBreakoutDetector 가중치 0.9
        └→ final_score = raw_score × weight
        └→ trigger=True AND final_score >= 5.0 인 최고점수 1개 선택
        └→ score_from_size():  >=8 → 100% / >=6 → 70% / >=5 → 50%
   └→ 레짐 게이트 · 섹터 한도 · 쿨다운 · Top-3 상한
   └→ data/swing_positions.json

[09:00 cron] swing_executor.py → 시장가 매수
```

### Backtest 진입과의 차이

| | Live (Pullback) | Backtest (CHoCH) |
|---|---|---|
| 구현 | `analyzers/swing/signal_engine.py` | `backtest/adapter.py` |
| 판정 | 패턴 3종 점수화 (cup_handle · pullback · box_breakout) | Bullish CHoCH + 거래량 + ATR + MA50 |
| 임계 | final_score ≥ 5.0 | 필터 전부 통과 |
| 구조 근거 | ZigZag 피벗 기반 패턴 | 시장구조 전환(CHoCH) |

**공통점이 없다.** 이름만 Swing 이다.

---

## 2. 동일 조건 백테스트

청산·리스크·포트폴리오를 **동일**하게 두고 진입만 바꿨다.
청산은 Iteration 7-2 에서 채택 후보로 정한 Case3
(SL −5% · min_hold 3봉 · max_hold 60봉 · 오버나이트 허용).

⚠️ **재구현하지 않았다.** `SignalEngine` 을 그대로 호출했다.
다시 구현하면 Live 와 백테스트가 아니라 구현 두 개를 비교하게 된다.

### 신호 생성

```
B. CHoCH      117건 / 78종목
A. Pullback  2201건 / 76종목   (CHoCH 대비 18.8배)
두 규칙이 같은 날 같은 종목에 신호:  3건 (CHoCH 의 2.6%)
```

**겹치는 신호가 3건뿐이다.** 두 규칙은 사실상 다른 종목을 다른 날에 산다.

### 상한 없음

| 케이스 | 후보 | 거래 | 승률 | PF | 기대값 | 수익 | MDD | 보유 | 연속손실 |
|---|---|---|---|---|---|---|---|---|---|
| A. Pullback | 2201 | 753 | 33.3% | 1.148 | 0.480% | +72.28% | **−81.31%** | 9.2봉 | 16 |
| B. CHoCH | 117 | 80 | 43.8% | **1.884** | 2.442% | +39.08% | **−11.27%** | 8.8봉 | 9 |

⚠️ Pullback 의 MDD −81.31% 는 상한 없이 753거래를 동시에 안은 결과다.
운영에는 Top-3 상한이 있으므로 이 숫자를 그대로 읽으면 안 된다.

### Top-3 (Live 상한)

| 케이스 | 후보 | 거래 | 승률 | PF | 기대값 | 수익 | MDD | 보유 | 연속손실 |
|---|---|---|---|---|---|---|---|---|---|
| **A. Pullback** | 2201 | 140 | **32.9%** | **1.126** | 0.415% | +11.63% | −12.92% | 8.8봉 | 12 |
| **B. CHoCH** | 117 | 59 | **40.7%** | **1.639** | 1.884% | +22.23% | −11.48% | 9.3봉 | 9 |

### 구간 안정성 (Top-3)

| 진입 규칙 | Train PF | Validation PF | Test PF |
|---|---|---|---|
| A. Pullback (Live) | **0.966** | 1.239 | 1.066 |
| B. CHoCH (Backtest) | 1.531 | 1.339 | **1.894** |

**Pullback 은 Train 구간에서 PF 0.966 — 손실이다.** 세 구간 모두 1.3 미만.
CHoCH 는 세 구간 모두 1.3 이상.

---

## 3. Entry Parity 판정

| 기준 | 목표 | A. Pullback | B. CHoCH |
|---|---|---|---|
| Live = Backtest | 동일 | ❌ 겹침 2.6% | — |
| Profit Factor | ≥1.5 | ❌ 1.126 | ✅ 1.639 |
| Win Rate | ≥40% | ❌ 32.9% | ✅ 40.7% |
| MDD | ≤15% | ✅ −12.92% | ✅ −11.48% |

**A. Pullback → FAIL (PF · WR)**
**B. CHoCH → PASS**

### 이것이 뜻하는 것

Entry Parity 를 맞추는 방법은 둘 중 하나다.

**㉠ Backtest 를 Live 에 맞춘다** (백테스트를 Pullback 으로 교체)
→ 검증 대상이 PF 1.126 · Train 손실인 규칙이 된다. **권하지 않는다.**

**㉡ Live 를 Backtest 에 맞춘다** (swing_runner 진입을 CHoCH 로 교체)
→ 검증된 규칙(PF 1.639, 세 구간 모두 1.3 이상)으로 통일된다.
   다만 신호가 2201 → 117 건으로 줄어 **거래 빈도가 18.8배 감소**한다.

---

## 4. 추천

**㉡ 방향을 권한다. 다만 즉시 교체가 아니라 단계적으로.**

이유:
1. Pullback 은 세 구간 모두 PF 1.3 미만이고 Train 은 손실이다.
   운영 성과가 나빴던 것과 방향이 일치한다.
2. CHoCH 는 세 구간 모두 1.3 이상으로 안정적이다.
3. 다만 CHoCH 는 2년 117건 — 스윙 5건이 나온 것과 정합한다.
   교체하면 **거래가 더 줄어든다.** 이건 Phase 0.1~0.3 에서 이미
   확인된 구조적 제약이고, 후보 확대는 그때 전부 실패했다.

### 반드시 짚을 한계

**이 비교는 Live 실거래 5건을 설명하지 못한다.**
Iteration 4 에서 확인했듯 백테스트는 그 5건 중 **0건**의 신호를 냈고,
이번 Pullback 어댑터로 돌려도 유니버스가 달라 같은 거래를 재현하지
못한다. **규칙 비교이지 실거래 재현이 아니다.**

---

## 5. Live 와 다른 점 (읽을 때 반드시 감안)

| 항목 | 이번 백테스트 | Live |
|---|---|---|
| 유니버스 | DEFAULT_CANDIDATES 78 | `data/swing_universe.json` |
| 레짐 게이트 | 없음 | `get_market_regime()` |
| 섹터 한도 | 없음 | 있음 |
| 쿨다운 | 없음 | 있음 |
| 포지션 사이즈 | 슬롯 균등 | `score_from_size()` 50/70/100% |

**따라서 이 백테스트의 신호 수는 Live 상한선이다.** 실제 Live 는
이보다 적게 잡는다.

---

## 6. §4 [SWING_ENTRY] 로그 — 미착수

작업지시서 §"운영 반영 금지: 백테스트 검증 완료 전 Live 변경 금지"
에 따라 **운영 코드를 건드리지 않았다.**

검증 결과가 "Live 진입 규칙을 바꿔야 한다" 로 나왔으므로, 로그 추가는
진입 규칙 결정 이후에 하는 것이 맞다. 지금 붙이면 곧 교체될 규칙을
계측하게 된다.

---

## 산출물

```
phase1/swing_entry_adapter.py   Live SignalEngine 백테스트 어댑터
phase1/entry_parity.py          A/B 비교
phase1/entry_parity.csv         구간 × 상한 × 규칙 KPI
phase1/entry_parity.json
```

## 전제

- `SignalEngine` 을 그대로 호출했으나 유니버스·게이트·사이징은 재현하지
  않았다. 신호 **규칙**만 비교한 것이다.
- Train/Validation/Test 구간 모두 파라미터 선택에 이미 사용된 데이터다.
- 청산은 일봉 스윙 근사다.
- yfinance 데이터 변동성이 실제보다 크다 (삼성전자 연율 56%).
- 실거래 95.8%인 VWAP+AI 전략은 여전히 이 비교 밖에 있다.
