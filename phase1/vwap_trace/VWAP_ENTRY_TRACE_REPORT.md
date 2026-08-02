# VWAP_ENTRY_TRACE_REPORT

Iteration 6-2 · 2026-08-03 · **분석 전용 · 코드 변경 0건**
Regression 495 PASS / 16 Known FAIL — RELEASE ALLOWED

---

## 결론: **NOT READY**

작업지시서 §12 의 세 결론 중 **3번**이다.

> **VWAP+AI 진입 경로가 현재 운영 코드에 존재하지 않는다.**

`"VWAP 상향 돌파 (종합점수: X)"` 문자열을 만드는 곳은
`trading/order_executor.py:251` **한 곳뿐**인데, 이 모듈은
`main_auto_trading.py` 에서 **한 번도 호출되지 않는다** (grep 0건).

`docs/RUNTIME_EXECUTION_MAP.md` 도 `order_executor` 를 LOADED-ONLY
(로드되나 미호출)로 분류하고 있다 — 이번 추적이 그것을 확인했다.

---

## 1. entry_reason 형식별 시점 — 결정적 증거

| 형식 | 건 | 최초 | 최종 |
|---|---|---|---|
| **VWAP 상향 돌파** (order_executor) | **96** | **2025-11-28** | **2026-01-16** |
| SMC LONG | 39 | 2026-01-22 | 2026-02-25 |
| EXPLORATION | 10 | 2026-04-08 | 2026-06-09 |
| OTHER | 9 | 2026-01-20 | 2026-04-30 |
| SWING | 5 | 2026-05-12 | 2026-07-01 |
| EXPERIMENT | 5 | 2026-03-25 | 2026-03-26 |
| (null) | 1 | 2025-11-24 | 2025-11-24 |

**VWAP+AI 진입은 2026-01-16 에 끊겼다.** 그 이후 매수는 전부
다른 형식이다. 즉 **VWAP+AI 는 현재 돌고 있는 전략이 아니다.**

⚠️ 앞선 Iteration 들에서 "실거래의 95.8% 가 VWAP+AI" 라고 보고했다.
그 수치는 `condition_name` 컬럼 기준이었고, `condition_name='VWAP+AI'`
는 order_executor 가 상수로 박아 넣은 값이다.

**`condition_name` 은 조건검색식 이름이 아니라 그 모듈이 남긴
라벨이었다.** 2026-01-16 이후 매수에도 이 라벨이 붙어 있는지
확인이 필요하다 — 아래 §5.

---

## 2. 조건검색 (§3-1) — ✅ 추적 완료

| 항목 | 내용 | 근거 |
|---|---|---|
| 수신 위치 | `filter_stocks_with_vwap()` | `main_auto_trading.py:3040` 루프 |
| 조건식 인덱스 | `[17,18,19,20,21,22]` | `config/strategy_hybrid.yaml:1275` |
| 이름 | 런타임에 키움이 내려줌 (`condition_list[idx][1]`) | 코드·설정에 없음 |
| 종목 추가 | `all_stocks.update(stocks)` | `main:3069` |
| 중복 제거 | `set` 자료형 | 〃 |
| 출처 추적 | `_cond_sources[code].append(name)` | Iteration 8-1 추가 |
| 만료 조건 | 재실행 시 `condition_search` 소스 종목만 초기화 | `main:3004` |
| 후보 유지 | 다음 재실행까지 (주기 미측정) | — |

---

## 3. VWAP (§3-2) — ✅ 계산식 확인

`analyzers/entry_timing_analyzer.py:58` `calculate_vwap()`

```python
df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
df['pv'] = df['typical_price'] * df['volume']

if use_rolling:                                   # ← 기본값 True
    df['rolling_pv']     = df['pv'].rolling(window=rolling_window).sum()
    df['rolling_volume'] = df['volume'].rolling(window=rolling_window).sum()
    df['vwap'] = df['rolling_pv'] / df['rolling_volume']
else:
    # 일자별 누적 (groupby date → cumsum)
    df['vwap'] = df['cumulative_pv'] / df['cumulative_volume']
```

| 항목 | 값 | 근거 |
|---|---|---|
| 입력 | 5분봉 OHLCV | `main:154` "주식 데이터 다운로드 (5분봉)" |
| 모드 | **20봉 rolling** | `config` 에 최상위 `vwap:` 섹션 **없음** → 기본값 `use_rolling=True, rolling_window=20` |
| 초기화 | 없음 (rolling 이므로) | — |
| Tick 사용 | **없음** | 분봉만 |

### ⚠️ 이름과 실제가 다르다

**"VWAP" 이라는 이름은 보통 일중 누적(session VWAP)을 뜻하는데,
실제로는 20봉(=100분) 이동평균이다.**

`config/strategy_hybrid.yaml` 에 `vwap:` 섹션이 없어
`get_section('vwap')` 이 빈 값을 돌려주고, 코드 기본값이 그대로 쓰인다.

백테스트를 만들 때 이걸 일중 누적으로 구현하면 **다른 전략**이 된다.

---

## 4. VWAP 돌파 (§3-3) — ⚠️ 여러 정의가 공존

코드에서 발견된 돌파 판정이 **4종류**다.

| 위치 | 판정 | 성격 |
|---|---|---|
| `main:8617` | `close_prev < vwap_prev and high >= vwap_now` | 전봉 아래 → 당봉 고가 돌파 |
| `main:8708` | `close >= vwap_val` | 종가 기준 단순 비교 |
| `main:11308` | `current_price > vwap_val` | **현재가** 기준 |
| `entry_timing_analyzer:227` | `(recent['close'] > recent['vwap']).all()` | N봉 **연속** 유지 |

**어느 것이 VWAP+AI 진입을 만들었는지 특정하지 못했다.**
진입을 만든 `order_executor` 가 미호출이라 역추적이 끊긴다.

⚠️ 작업지시서가 "추정 금지" 를 명시했으므로 **하나를 골라 적지 않는다.**

---

## 5. AI Score (§3-4) — ❌ 산출식 미확인

`order_executor.py:251` 이 `analysis.get('total_score', 0)` 을 읽는다.

```python
'entry_reason': f"VWAP 상향 돌파 (종합점수: {analysis.get('total_score', 0):.1f})",
```

**`analysis` 를 만드는 곳을 특정하지 못했다.**
`entry_timing_analyzer.py` 에 `total_score` 정의가 **0건**이다.

### 실거래 89건(53.9%)이 `종합점수: 0.0` 이었던 이유

`analysis.get('total_score', 0)` 의 **기본값 0** 이다.
`analysis` 에 `total_score` 키가 없으면 0.0 이 찍힌다.

**즉 점수가 0 이어서 0.0 이 아니라, 점수 자체가 없어서 0.0 이다.**

⚠️ 이는 Iteration 8-1 에서 "점수가 게이트로 작동하지 않는다" 고
보고한 것의 원인이다. 게이트가 느슨한 게 아니라 **값이 없었다.**

---

## 6. 진입 필터 (§3-5) — ✅

`execute_buy()` 내 조기 return 경로. Iteration P0 에서 37개 확인,
Decision Ledger reason_code 8종으로 분류돼 있다.

`COOLDOWN_ACTIVE` · `RISK_BLOCKED` · `DUPLICATE_POSITION` ·
`MAX_POSITIONS` · `INSUFFICIENT_CAPITAL` · `ENTRY_QUALITY_BLOCKED` ·
`API_FAILURE` · `ORDER_FAILURE`

---

## 7. Position Size (§3-6) — ✅

`core/risk_manager.py:301`

```python
risk_amount        = current_balance * self.RISK_PER_TRADE
risk_per_share     = abs(current_price - stop_loss_price)
risk_based_quantity = int(risk_amount / risk_per_share)
```

---

## 8. 승인 기준 대비

| 항목 | 목표 | 결과 | 판정 |
|---|---|---|---|
| Entry Pipeline 추적 | 100% | 조건검색·VWAP·필터·사이징 ✅ / 돌파판정·AI Score ❌ | ⚠️ 부분 |
| VWAP 계산식 확인 | PASS | 20봉 rolling 확정 | ✅ |
| AI Score 확인 | PASS | **산출식 없음 확인** | ⚠️ |
| Entry Rule 문서화 | PASS | 빈칸 2개 (돌파·Score) | ⚠️ |
| Backtest Mapping | PASS | `BACKTEST_MAPPING.md` | ✅ |
| Parity Risk 분석 | PASS | `PARITY_RISK_REPORT.md` | ✅ |
| **추정 사용** | 0건 | **0건** | ✅ |
| Runtime Error | 0 | 0 | ✅ |
| Regression | PASS | 495 PASS / 16 Known FAIL | ✅ |

---

## 9. 다음 단계 — 결정이 필요하다

§13 은 "Parity Ready 일 때만 Baseline 백테스트" 라고 못박았다.
**NOT READY 이므로 백테스트로 넘어가지 않는다.**

### 확인이 필요한 것

**앞선 Iteration 들의 "실거래 95.8% = VWAP+AI" 전제가 흔들린다.**

- `condition_name='VWAP+AI'` 는 order_executor 가 박은 상수 라벨
- 그 모듈은 현재 미호출
- VWAP 형식 entry_reason 은 2026-01-16 에 끊김

**그렇다면 2026-01-16 이후 매수 62건은 어느 전략인가?**
`SMC LONG` 39 · `EXPLORATION` 10 · `SWING` 5 · `EXPERIMENT` 5 ·
`OTHER` 9 로 나뉜다.

### 세 갈래

| 안 | 내용 | 비고 |
|---|---|---|
| **㉠** | **현재 실제로 도는 전략을 다시 식별** | 8개월 전 끊긴 전략을 백테스트하는 것보다 우선 |
| ㉡ | order_executor 를 복원해 VWAP+AI 를 되살린 뒤 백테스트 | 전략 변경이다. 별도 승인 |
| ㉢ | 과거 96건(2025-11~2026-01)만 대상으로 백테스트 | 표본 96건, 현재와 무관 |

**㉠ 을 권한다.** "실거래의 95.8%" 라는 근거로 VWAP+AI 를 쫓아왔는데,
그 근거가 컬럼 라벨이었고 실제 진입 경로는 8개월 전 끊겼다.
**지금 무엇이 도는지 먼저 확인하는 것이 맞다.**
