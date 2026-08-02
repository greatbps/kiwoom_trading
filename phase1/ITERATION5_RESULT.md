# Phase 1 Iteration 5 — Exit Execution Parity + Risk Control Audit

문서 버전: v1.0
생성: 2026-08-02
Regression: 440 PASS / 16 Known FAIL — RELEASE ALLOWED
**운영 코드 무수정 (읽기 전용 audit)**

---

## 결론 먼저

**Execution 문제가 맞다. 다만 원인이 예상과 다르다.**

작업지시서는 "청산 조건이 실제 실행되는지" 를 묻고, 수정안으로
A(실시간 Monitor 추가) · B(Scheduler 주기 개선) · C(Exit Worker 분리)
를 제시했다. 셋 다 **감시 주기가 느리다**는 가정 위에 있다.

측정 결과 그 가정이 틀렸다.

```
청산 판정 주기        60초        (check_interval = 60)
스윙 포지션 편입 주기   300초       (_attach_swing_risk_positions)
보유 종목 감시 포함    항상        (all_stocks = watchlist | positions)
```

**감시는 돈다. 손절 가격이 감시자에게 전달되지 않는다.**

---

## Experiment 1 — Exit 호출 경로

전문: `phase1/exit_flow.md`

### 근본 원인: `structure_stop_price` 유실

`_attach_swing_risk_positions()` (main:8609) 가 스윙 포지션을
`self.positions` 에 편입할 때 만드는 딕셔너리에 **`structure_stop_price`
키가 없다.** `swing_positions.json` 과 DB 에는 `stop_price` 가 기록돼
있는데 옮겨지지 않는다.

그 결과 `exit_logic_optimized.py:807`:

```python
structure_stop_price = position.get('structure_stop_price')   # → None

if structure_stop_price and ...:      # False → 구조손절 진입 못 함
    ...
else:
    if _is_swing:
        _swing_hard_stop = config.get('risk_control.swing_hard_stop_pct', 12.0)
        if profit_pct <= -_swing_hard_stop:     # -12% 여야 청산
            return True, '[SWING_HARD_STOP] ...'
        elif profit_pct <= -max_stop_pct:       # -5%
            logger.warning('[SWING_NO_STRUCTURE_STOP] ... -12% 까지 허용')
            # ⚠️ 청산하지 않는다. 경고만 남긴다.
```

`config/strategy_hybrid.yaml:359` — `swing_hard_stop_pct: 12.0`

| | 설계값 | 실효값 |
|---|---|---|
| 손절선 | −1.40% ~ −5.34% (swing_runner 산출) | **−12%** |

**−5% ~ −12% 구간에서는 경고 로그만 남고 청산되지 않는다.**

### 2차 원인: 매수 당일 미편입

| 거래 | 매수일 어태치 | 최초 어태치 |
|---|---|---|
| 삼성SDI 2026-05-12 | **없음** | 2026-05-13 09:00:10 (**+24시간**) |
| SK하이닉스 2026-06-10 | 09:00:07 | 당일 |
| NAVER 2026-06-15 | 09:00:06 | 당일 |
| 삼성전자 2026-06-23 | 09:00:06 | 당일 |
| 삼성전자 2026-07-01 | **없음** | — |

삼성SDI 는 매수 당일 로그에 `006400` 이 **0회** 등장한다. 그날 저가
616,000 (−9.9%) 까지 갔으나 감시 자체가 없었다.

---

## Experiment 2 — Swing 5건 손절 지연

| 종목 | 매수일 | 손절가 | 손절 도달일 | 청산일 | 지연 | 청산가 | 초과손실 |
|---|---|---|---|---|---|---|---|
| 삼성SDI | 05-12 | 670,658 | 05-12 | 05-13 | 1일 | 629,000 | 333,264 |
| SK하이닉스 | 06-10 | 2,104,512 | 06-10 | 06-11 | 1일 | 1,975,000 | 129,512 |
| NAVER | 06-15 | 239,493 | 06-17 | 06-17 | 0일 | 242,000 | 0 |
| 삼성전자 | 06-23 | 341,246 | 06-23 | 06-24 | 1일 | 314,000 | 163,476 |
| 삼성전자 | 07-01 | 329,315 | 07-01 | 07-01 | 0일 | 318,500 | 64,890 |

**손절가 이상으로 청산된 건: 1/5. 초과손실 691,142원.**

5건 전부 **매수 당일 또는 그 직후에 손절선을 통과**했다.
지연 0일인 2건도 그날 안에서 −3.3% ~ −4.6% 더 벌어진 뒤 14:50
오버나이트 차단으로 나갔다.

⚠️ 분봉이 없어 "몇 시에 통과했는가" 는 알 수 없다. 지연은 일 단위다.

---

## Experiment 3 — 전체 Live Stop Execution Rate

```
청산 242건 중 손절 계열 39건
  수익률 기록 있는 것      39건
  -5% 보다 깊게 끊긴 것     3건 (7.7%)
  최악                  -10.41%

Stop Execution Rate 92.3%   목표 95% → ❌ FAIL
```

⚠️ 분봉 부재로 "조건 발생 후 N분 내 청산" 은 산출 불가하다.
**"설정 손절폭을 넘겨 끊겼는가" 로 대체 측정**했으므로, 지시서가
정의한 지표와 다른 값이다. 시간 기준 실행률은 여전히 미측정이다.

---

## Experiment 4 — Backtest / Live Exit Rule Mapping

| Backtest 규칙 | Live 대응 | Live 건수 |
|---|---|---|
| SL | HARD_STOP · SWING_HARD_STOP · STRUCTURE_STOP · STOP_LOSS | 39 |
| TP | TAKE_PROFIT · TP1 · TP2 | **0** |
| TRAIL | TRAILING | 4 |
| BE_STOP | BREAK_EVEN | **0** |
| MAX_HOLD | TIME_EXIT | 10 |
| **(대응 없음)** | EARLY_FAILURE · OVERNIGHT_BLOCK · MA5_EXIT · DRAWDOWN_STOP | **189** |

**Mapping 커버리지 53/242 = 21.9%. 목표 100% → FAIL.**

Live 청산의 78%가 백테스트에 대응 규칙이 없다. 특히 **TP 와 BE_STOP 이
실거래에서 0건**이다 — 백테스트는 익절 26건(32%)을 내는데 실거래는
익절로 나간 적이 없다.

---

## 완료 기준 대비

| 기준 | 목표 | 결과 | 판정 |
|---|---|---|---|
| Stop Execution Rate | ≥95% | 92.3% (대체 지표) | ❌ FAIL |
| Swing 손실 확대 제거 | — | 원인 규명 완료, 미수정 | ⏸ 보류 |
| Exit Mapping | 100% | 21.9% | ❌ FAIL |
| Regression | 440/16 | 440/16 | ✅ PASS |

---

## 수정안 비교

지시서의 A/B/C 는 모두 "감시 주기" 를 다루는데, 측정된 원인은 주기가
아니라 **데이터 전달 누락**이다. 따라서 원안을 그대로 채택하지 않고
아래를 제안한다.

### D안 (신규·권장) — 편입 시 손절값 전달

`_attach_swing_risk_positions()` 가 `swing_positions.json` 의
`stop_price` 를 `structure_stop_price` 키로 복사한다.

| 항목 | 평가 |
|---|---|
| 변경 규모 | 딕셔너리 키 1개 추가 (main:8667 블록) |
| 안정성 | 높음 — 기존 `structure_stop_price` 경로를 그대로 탄다 |
| 구현 난이도 | 낮음 |
| 운영 위험 | **중간** — 손절이 −12% → −1.4~5.3% 로 **강해진다**. 조기 청산 증가 가능 |
| 예상 효과 | 5건 기준 초과손실 691,142원 중 대부분 회피 |

⚠️ 위험을 낮게 보지 않는다. 지금까지 −12%까지 버티던 것이 −2%에서
끊기게 되므로 **거래 성격이 바뀐다.** 백테스트로 먼저 확인해야 한다.

### A안 — 실시간 Stop Monitor 추가

| 항목 | 평가 |
|---|---|
| 변경 규모 | 큼 (신규 스레드/프로세스) |
| 안정성 | 낮음 — 중복 청산·경합 위험 |
| 운영 위험 | 높음 |
| 필요성 | **낮음** — 60초 감시가 이미 돈다 |

### B안 — Scheduler 주기 개선

| 항목 | 평가 |
|---|---|
| 효과 | **없음** — 주기가 원인이 아니다 |

60초를 10초로 줄여도 `structure_stop_price` 가 None 이면 −12%까지
청산되지 않는다.

### C안 — Exit Worker 분리

| 항목 | 평가 |
|---|---|
| 변경 규모 | 매우 큼 |
| 안정성 | 낮음 (프로세스 간 상태 동기화 — 지금 문제의 원인이 이것이다) |
| 운영 위험 | 높음 |

swing_executor 와 main 이 이미 분리돼 있고 그 경계에서 손절값이
유실됐다. **분리를 더 늘리는 방향은 같은 실패를 반복한다.**

---

## 미해결 — 추정하지 않고 남긴다

1. **`[HARD_STOP] -5.0%` 문자열이 나온 경로.** 이 문자열은 비-SWING
   분기(`else`)의 출력인데 스윙 포지션에서 관측됐다. 어느 지점에서
   `strategy_horizon` 이 소실되는지 특정하지 못했다.
2. **`[SWING_NO_STRUCTURE_STOP]` 로그 0건.** 경고가 남았어야 할 구간을
   거치지 않고 다른 경로로 먼저 청산된 것으로 보이나 확증하지 못했다.
3. **삼성SDI·삼성전자(07-01) 매수 당일 미편입 원인.** 5분 주기 어태치가
   왜 그날 동작하지 않았는지 로그로 확인되지 않는다.
4. **시간 기준 Stop Execution Rate.** 분봉 부재로 측정 불가.

---

## 전제

- 분봉 데이터가 없다. 모든 시각 분석은 일봉 근사이며 분 단위 지연은
  산출하지 않았다.
- 표본 5건. 평균·승률·PF 를 내지 않았다.
- 백테스트 청산은 일봉 스윙 근사다.
