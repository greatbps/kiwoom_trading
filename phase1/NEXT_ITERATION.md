# 작업지시서 — Phase 1 Iteration 3

> 자동 생성 (지시서 v1.0 §17). Iteration 2 결과에 근거한다.

문서 버전: v1.0
생성: 2026-08-02
선행: Iteration 2 (Backtest ↔ Live Parity Audit) — **PASS**
Regression 기준: 440 PASS / 16 Known FAIL

---

## 1. 이번 목표 달성 여부

**PASS.** 차이의 97%를 설명했다 (기준: 95% 이상, Unknown 5% 이하).

---

## 2. Root Cause 요약

백테스트 PF 1.813 ↔ 실거래 PF 0.102 의 차이는 실행 오차가 아니다.
**두 시스템이 서로 다른 전략을 돌리고 있다.**

| 원인 | 영향 | 실측 |
|---|---|---|
| 유니버스 불일치 | 40% | 교집합 4종목 / 94종목 (4.3%) |
| 보유기간 불일치 | 30% | 8.6일 vs 197분 (17배) |
| 청산규칙 불일치 | 25% | 공통 청산 사유 **0종** |
| 슬리피지 | 2% | 0.20% 가산해도 PF 1.617 |
| 체결지연 | 0% | 데이터 미기록 |
| Unknown | 3% | — |

---

## 3. 가장 영향이 큰 병목 1개

### 병목: **유니버스 불일치 (영향 40%)**

Live 94종목 중 `DEFAULT_CANDIDATES` 78종목과 겹치는 것이 **4종목**뿐이다.

이것을 1순위로 두는 이유는 영향도가 가장 커서만이 아니다.
**나머지 두 병목(보유기간·청산규칙)의 선행 조건**이기 때문이다.
거래하는 종목이 다르면 보유기간을 맞추든 청산을 맞추든 두 시스템의
숫자는 여전히 비교 불가다. 종목이 먼저 맞아야 나머지 비교가 성립한다.

또한 이 병목만 **운영 코드를 건드리지 않고** 규명할 수 있다.
보유기간·청산규칙 정합은 `main_auto_trading.py` /
`exit_logic_optimized.py` 수정을 요구하므로 동결 해제가 선행돼야 한다.

---

## 4. Iteration 3 작업지시서

### 4-1. 제목

Phase 1 Iteration 3 — Universe Parity 규명

### 4-2. 목적

실거래가 거래하는 94종목이 **어디서 오는지**를 코드 경로로 추적하고,
백테스트 유니버스와 어긋난 지점을 특정한다.

PF 개선이 목표가 아니다. **"운영 시스템이 실제로 무엇을 후보로
삼는가"** 를 문서화하는 것이 목표다.

### 4-3. 절대 조건

수정 금지:

```
main_auto_trading.py
trading/score_engine.py
core/risk_manager.py
trading/exit_logic_optimized.py
backtest/daily_scan.py
backtest/adapter.py
backtest/scanner.py
```

작업은 `phase1/` 내에서만. **읽기 전용 추적**이다.

### 4-4. Baseline Freeze

```bash
./check_baseline.sh      # PASS>=440, FAIL<=16, RELEASE ALLOWED
```

실패 시 즉시 중단.

### 4-5. 작업 항목

**Step 1 — 실거래 종목 출처 추적**

Live 94종목 각각이 어느 경로로 후보가 됐는지 분류한다.

추적 대상:
- 조건검색식 (키움 condition_name)
- `daily_watchlist.json` (daily_scan 산출)
- `swing_runner.py` 선정 결과
- 수동 진입 / 기타

산출: `phase1/universe_origin.csv`
컬럼: `stock_code, name, trades, net_pnl, origin, in_default_candidates`

**Step 2 — 조건검색식 실태**

`trades.condition_name` 분포를 집계한다. 이 컬럼이 채워져 있다면
실거래 후보의 실제 공급원이 조건검색식임을 확인할 수 있다.
비어 있다면 그 사실 자체를 기록한다 — 추정하지 않는다.

산출: `phase1/condition_usage.csv`

**Step 3 — daily_scan 실행 이력 확인**

`data/daily_watchlist.json` 의 갱신 이력과 실거래 종목의 일치도를
본다. 메모리에 "daily_scan 7주 미실행" 기록이 있으므로, 실제로
실행되고 있는지부터 확인한다.

산출: `phase1/daily_scan_health.json`

**Step 4 — 백테스트 유니버스 재정의 가능성 평가**

Live 94종목으로 백테스트를 돌렸을 때 어떤 숫자가 나오는지 측정한다.
이것이 "백테스트를 실거래에 맞추는" 방향의 타당성을 가른다.

- 94종목 OHLCV 확보 가능 여부 (상장폐지·거래정지 포함)
- OPS 후보 생성 조건 적용 시 신호 수
- 동일 Exit/Risk/Portfolio 로 KPI 산출

산출: `phase1/live_universe_backtest.json`

⚠️ 이 백테스트에는 **강한 생존 편향**이 있다. Live 94종목은 이미
"거래된 종목" 이라 선택 자체가 결과를 안다. 성과 숫자로 전략을
판단하면 안 되고, **신호가 나오기는 하는가**만 본다.

### 4-6. 판정 기준

**PASS**:
- Live 94종목의 출처가 90% 이상 분류됨
- 백테스트 유니버스와 어긋난 지점이 코드 경로로 특정됨

**FAIL**:
- 출처 불명 종목이 10%를 넘음 → 로깅 부족이 근본 원인.
  Iteration 4는 "후보 출처 기록 추가" 가 된다.

### 4-7. Regression 절차

```bash
./check_baseline.sh
```

PASS ≥440, Known FAIL ≤16, RELEASE ALLOWED.

### 4-8. Backtest 절차

```bash
python3 -m phase1.baseline           # Baseline 재현
python3 -m phase1.parity             # Parity 재확인
python3 -m phase1.universe_origin    # Iteration 3 신규
```

### 4-9. 산출물

```
phase1/universe_origin.csv
phase1/condition_usage.csv
phase1/daily_scan_health.json
phase1/live_universe_backtest.json
phase1/ITERATION3_RESULT.md
phase1/NEXT_ITERATION.md   (갱신)
```

---

## 5. 별도 확인 요청 (Iteration 3 범위 밖, 그러나 시급)

### 5-1. EC_HALT drawdown 값 이상

`signal_rejections` 에 `dd=-93.7%` · `dd=-54.4%` 로 기록된 EC_HALT가
있다. equity drawdown 93.7%는 정상 범위가 아니다. 알려진 EC_HALT peak
오염 버그의 잔재일 수 있다. 총 차단 건수가 수천 건이라 실거래 기회에
직접 영향을 준다.

### 5-2. ORPHAN_HALT 7,220건

"고아 포지션 — 수동 확인 후 재시작 필요" 로 7,220건이 차단됐다.
수동 개입이 필요한 상태가 장기간 방치됐을 가능성이 있다.

### 5-3. 전략 정체성 불일치

CLAUDE.md 절대원칙은 "스윙만 한다. 인트라데이/단타 전략 없음" 이지만
실거래의 52%가 당일 왕복, 평균 보유 3.3시간, 장중 청산 62.4%다.
문서와 실제가 다르다. **어느 쪽이 의도인지 결정이 필요하다** —
이 결정 없이는 백테스트를 어느 쪽에 맞출지도 정할 수 없다.

---

## 6. 남은 전제

- Live 표본은 242 청산거래 · 8개월치다. 통계적 결론에는 얇다.
- `trades` 의 메타 컬럼 대부분이 NULL이다 (`market_regime`,
  `confidence`, `r_multiple`, `mfe_pct`, `mae_pct` 전부 0건).
  더 깊은 분해는 로깅 보강 없이는 불가능하다.
- 백테스트 청산은 일봉 스윙 근사다.
- yfinance 데이터 변동성이 실제보다 크다.
