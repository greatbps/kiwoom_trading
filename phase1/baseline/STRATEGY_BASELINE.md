# STRATEGY_BASELINE

Iteration 8-0 · 2026-08-03 · 전략 무수정

## 1. 조건 (전 전략 100% 동일)

| 항목 | 값 |
|---|---|
| 데이터 | `phase1/minute_cache/5m` — Iteration 6 데이터셋 **그대로**, 신규 수집 0 |
| 종목 | 112 |
| 기간 | 2025-08-01 ~ 2026-07-31 (243 거래일) |
| 봉 | 5분봉 → **일봉 리샘플** (스윙 전략이라 일봉 기준. 집계만, 데이터 수정 없음) |
| 워밍업 | 60봉 |
| 청산 | `phase0.portfolio.EXIT_PROFILE` (BacktestEngine 공용) |
| 비용 | commission 0.0035 왕복 |
| 슬롯 | 3 (`config swing.max_positions`) |
| 사이즈 | `INITIAL_CAPITAL / SLOT_DIVISOR` |
| Look-ahead | 매 봉 `df.iloc[:i+1]` 로 잘라 넘김 |

**전략별로 다른 것은 진입 신호뿐이다.**

## 2. 전략

| # | 이름 | 호출한 엔진 | 상태 |
|---|---|---|---|
| A | Live Swing (Pullback) | `swing_runner.SignalEngine` **원본** | 운영 기본값 |
| B | CHoCH | `analyzers.swing.choch_engine.ChochSignalEngine` **원본** | feature flag 뒤 |
| C | Exploration (core rules only) | YAML 수치 규칙만 재현 | **비활성 · Reference** |

재구현하지 않고 운영 클래스를 그대로 호출했다. 재구현하면 비교 대상이
운영 전략이 아니게 된다.

### C 는 왜 Reference 인가

운영 `exploration` 은 `enabled: false` 이고, 진입 조건에 오프라인에서
재현 불가능한 게이트가 섞여 있다.

- `SMC_NO_SIG` (그날 SMC 가 신호를 안 냈어야 함)
- Orchestrator ACCEPT (L0~L6 파이프라인 결과)
- NOT `NO_TRADE_DAY` (MarketContext 실시간 판정)
- `confirm_entry` 1봉 확인 대기 (실시간 체결 흐름)

여기서 적용한 것은 YAML 수치 규칙(`min_rvol` 1.2 · `breakout_window` 10 ·
`volume_mult` 1.5 · `max_3bar_rise` 0.04)뿐이다.
**결과는 거래수의 상한이며 운영 exploration 의 성과가 아니다.**

## 3. 결과

```
==============================
STRATEGY BASELINE BACKTEST
==============================
Strategy                   Trades   Win%     PF    Exp%    MDD%  Hold   Mon  Parity   Score
C Exploration (core only)      65  43.08   1.87  2.6557  -11.65  5.43  5.62   30.81   73.41
A Live Swing (Pullback)        81  41.98   1.56  1.7232   -8.29  4.86  7.00   10.23   62.08
B CHoCH                        41  31.71   1.06  0.2166  -14.98  4.49  3.54   51.25   22.78
==============================
Runtime Error   0
Regression      507 PASS / 16 Known FAIL
```

### 전체 지표

| 지표 | A Pullback | B CHoCH | C Expl(core) |
|---|---|---|---|
| 총 거래수 | 81 | 41 | 65 |
| 월 거래수 | 7.00 | 3.54 | 5.62 |
| 주 거래수 | 1.67 | 0.84 | 1.34 |
| 평균 일 거래수 | 0.333 | 0.169 | 0.267 |
| 승률 | 41.98% | 31.71% | 43.08% |
| PF | 1.56 | 1.06 | 1.87 |
| 평균 수익 | +7.03% | +6.68% | +8.30% |
| 평균 손실 | −2.11% | −2.78% | −1.62% |
| Expectancy | +1.72% | +0.22% | +2.66% |
| 평균 R (sl 5% 기준) | 0.345 | 0.043 | 0.531 |
| MDD | −8.29% | −14.98% | −11.65% |
| 평균 보유 | 4.86일 | 4.49일 | 5.43일 |
| Profit | 22,010,846 | 8,205,204 | 25,377,349 |
| Loss | 14,140,199 | 7,715,377 | 13,553,933 |
| Recovery Factor | 0.949 | 0.033 | 1.016 |

(평균 수익/손실·Profit/Loss 는 `strategy_baseline.json` 원본값)

## 4. Funnel

```
                bar      signal   filter통과   신호일    trade   슬롯부족폐기
A Pullback   20,322        792        792       792       81         646
B CHoCH      20,322         80         80        80       41          23
C Expl(core) 20,322      1,676        211       211       65         130
```

`filtered` 는 각 전략 자체 필터에서 걸러진 수다.
A·B 는 `min_score_to_enter` 5.0 을 신호 생성 단계에서 이미 만족해 추가 탈락 0.

## 5. Entry Parity — 두 가지를 구분해야 한다

| 전략 | **엔진 Parity** (백테스트 진입로직 == 운영 진입로직) | Funnel 전환율 (신호일→체결) |
|---|---|---|
| A | **100%** — `SignalEngine` 원본 호출 | 10.23% |
| B | **100%** — `ChochSignalEngine` 원본 호출 | 51.25% |
| C | **부분** — 재현 불가 게이트 있음 (§2) | 30.81% |

KPI 의 "Entry Parity 100%" 는 **엔진 Parity** 기준으로 A·B 달성.
Funnel 전환율이 낮은 것은 진입 로직 불일치가 아니라 **슬롯 3개 상한** 때문이다.

**A 는 792 신호일 중 646 개를 슬롯이 없어 버렸다.**
A 의 병목은 신호 부족이 아니라 슬롯이다. 이건 8-1 에서 다뤄야 할 지점이다.
