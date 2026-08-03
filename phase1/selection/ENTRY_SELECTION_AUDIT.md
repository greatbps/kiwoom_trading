# ENTRY_SELECTION_AUDIT

Iteration 8-1 Phase 1 · 현재 Selection Pipeline 분석

## 1. 파이프라인

```
swing_runner
  ↓  SignalEngine (pullback)          진입 신호
  ↓  ScoreEngine.score()              후보 점수
  ↓  sorted(reverse=True)             랭킹
  ↓  max_slots = 3                    슬롯 상한
  ↓  다음 거래일 시가 진입
```

## 2. 후보 → 진입 Funnel (2025-08-01 ~ 2026-07-31, 112종목)

| 항목 | 값 |
|---|---|
| 기간 | 243 거래일 |
| 평가한 봉 | 20,322 |
| Signal 발생 | 792 |
| Signal 발생 종목 | 111 / 112 |
| 후보가 2개 이상이던 날 | 131일 |
| 후보 > 빈 슬롯 (binding) | 126일 (51.9%) |
| **실제 진입** | **81** |
| **폐기** | **646 (81.6%)** |
| 폐기 사유 | **전부 슬롯 부족** (`max_slots=3`) |

Funnel 합계 확인: 진입 81 + 폐기 646 = 727.
나머지 65 는 이미 보유 중이거나 진입 예약 상태라 후보에서 제외된 건이다
(`portfolio.run()` ③ 의 `s not in open_pos and s not in pending`).

## 3. 현재 ScoreEngine 구성 (`trading/score_engine.py`)

| Feature | 산식 | 가중 | 적용 여부 |
|---|---|---|---|
| `smc` | `symbol in daily_smc_symbols` → 2 | **+2 / 0** | ✅ (백테스트에선 **후보 전원 +2 — 변별력 0**) |
| `volume` | 당일 거래량 ≥ 20일 평균 × 1.5 | **+1 / 0** | ✅ |
| `ma50` | 종가 > MA50 **and** MA50 상승 | **+1 / 0** | ✅ |
| `pattern` | `pattern_weights.json` 가중 | 가변 | ❌ **OFF** (`score_pattern=False`) |
| RVOL | — | — | ❌ 없음 |
| Trend | — | — | ❌ 없음 (ma50 이진값만) |
| Momentum | — | — | ❌ 없음 |
| Risk | — | — | ❌ 없음 |

**Threshold**: `MIN_SCORE = 2`, `MAX_SELECTED = 5` (운영 슬롯은 `swing.max_positions = 3`)
**Ranking**: `total` 내림차순, 파이썬 안정정렬

## 4. 핵심 문제 — 점수가 사실상 3단계뿐이다

`smc` 가 후보 전원에게 동일하게 +2 이므로, 실제로 갈리는 것은
`volume`(0/1) 과 `ma50`(0/1) 둘뿐이다. 가능한 점수는 **2 · 3 · 4** 세 개다.

실측 분포 (후보 2개 이상인 131일):

```
4.0 점 :   39건  ( 5.1%)
3.0 점 :  697건  (90.9%)
2.0 점 :   30건  ( 3.9%)
```

**후보의 91%가 3.0 점으로 동일하다.**

```
최고점 동점일 : 101 / 131일  (77.1%)
```

**"골라야 하는 날"의 77%에서 1등이 여러 명이다.**
동점은 `sorted` 안정정렬 때문에 **종목코드 순서**로 갈린다.

즉 현재 시스템은 절반이 넘는 날(126/243)에 후보를 버리면서,
그 선택의 77%를 **종목코드 오름차순**으로 하고 있다.
이건 선별이 아니다.

## 5. 이 감사에서 나온 질문

동점을 종목코드로 깨는 대신 **의미 있는 지표로 깨면 성과가 오르는가?**
→ Phase 2 (SELECTION_BACKTEST.md)
