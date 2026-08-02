# ENTRY_FLOW — Swing 진입 파이프라인 전수 분석

Iteration 9 Phase 1 · 2026-08-02 · 읽기 전용 분석

---

## 1. 현행 (Pullback)

```
[15:35 cron]  swing_runner.py
      │
      ├─ load_universe()          data/swing_universe.json
      ├─ get_market_regime()      레짐 게이트
      ├─ fetch_daily(code)        일봉 lookback
      │
      ▼
   SignalEngine(df, config)        analyzers/swing/signal_engine.py
      │
      ├─ PatternManager(window=5, min_swing_pct=0.02)
      │     ├ CupHandleDetector      weight 1.2
      │     ├ PullbackDetector       weight 1.0
      │     └ BoxBreakoutDetector    weight 0.9
      │
      ├─ zigzag.get_pivots(df, n=20)
      ├─ run_all(df, pivots)  →  [PatternResult, ...]
      │
      ▼
   _score()   final_score = meta['score'] × PATTERN_WEIGHT
              size        = score_from_size(final_score)
              trigger     = meta['trigger']
      │
      ▼
   _select()  trigger=True AND final_score >= 5.0 중 최고점수 1개
      │
      ▼
   swing_runner  min_score 게이트 → 섹터 한도 → 쿨다운 → Top-3
      │
      ▼
   data/swing_positions.json   (entry / stop / target / size)
      │
[09:00 cron]  swing_executor.py  →  시장가 매수
```

### 점수 → 사이즈

| final_score | size |
|---|---|
| ≥ 8 | 1.0 |
| ≥ 6 | 0.7 |
| ≥ 5 | 0.5 |
| < 5 | 진입 없음 |

---

## 2. 백테스트 (CHoCH)

```
backtest/adapter.py  SMCAdapter.get_signal(df, i)
      │
      ├─ 확정봉 window = df.iloc[i-60 : i]      (i 봉은 미확정)
      ├─ _find_pivots(w, lb=3)
      ├─ _is_bearish_structure()      LH + LL 확인
      ├─ Bullish CHoCH                종가가 last_LH 상향 돌파
      ├─ 몸통 비중 >= 50%             꼬리 돌파 차단
      ├─ require_volume               vol >= 20봉평균 × 1.5
      ├─ atr_pct 2 ~ 8%
      └─ require_ma50_trend           close > MA50 AND MA50 우상향
      │
      ▼
   'BUY' | None
```

---

## 3. 두 규칙의 차이

| 축 | Live (Pullback) | Backtest (CHoCH) |
|---|---|---|
| 판정 근거 | ZigZag 피벗 패턴 3종 | 시장구조 전환 (LH/LL → 상향 돌파) |
| 출력 | 연속 점수 (가중 합) | 이진 (통과/미통과) |
| 임계 | final_score ≥ 5.0 | 필터 전부 통과 |
| 거래량 | 패턴 내부 조건 | 명시 필터 (RVOL ≥ 1.5) |
| 변동성 | 없음 | ATR% 2~8% 대역 |
| 추세 | 없음 | MA50 우상향 + 종가 > MA50 |
| 2년 신호 | 2,201건 | 117건 |

**같은 날 같은 종목에 둘 다 신호: 3건 (CHoCH 의 2.6%)**

---

## 4. 교체 후 (CHoCH)

호출부는 그대로 두고 **신호 생성만** 바꾼다.

```
swing_runner._entry_engine(df, config)
      │
      ├─ 'pullback' (기본값)  →  SignalEngine
      └─ 'choch'              →  ChochSignalEngine
                                    └→ backtest.adapter.SMCAdapter (그대로 호출)
                                    └→ BEST_CONFIG / BEST_ADAPTER_KWARGS (그대로)
```

`ChochSignalEngine` 은 `SignalEngine` 과 **같은 키**를 돌려준다.
`swing_runner` 의 게이트·사이징·Top-3·저장 경로는 손대지 않았다.

### 점수 처리

⚠️ CHoCH 판정은 이진이다. 호환을 위해 `final_score = 6.0` 고정
(→ `size` 0.7)을 주되, **예측력이 있다고 주장하지 않는다.**

- Phase 0.1 — ScoreEngine 순위가 Random 대비 43.3 백분위 (효과 없음)
- Phase 1 Iter1 — 연속 스코어 Spearman rho −0.169 (p=0.131),
  trend 축 −0.267 로 유의하게 **음**

동점 정렬은 임의로 갈리며, 그것이 정직한 상태다.

### 손절 산출

`stop = 최근 10봉 저점` (진입가 이상이면 entry × 0.95 로 대체).
`exit_logic` 이 `max_stop_pct = 5.0` 으로 캡한다.
이 값이 `structure_stop_price` 로 흘러간다 — Iteration 6-1 에서
정상화한 경로를 그대로 탄다.
