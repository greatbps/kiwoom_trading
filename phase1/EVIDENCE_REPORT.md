# Evidence Report — CHoCH Paper Trading Validation

Iteration 7-1 (재개) · 2026-08-02
Regression: 474 PASS / 16 Known FAIL — RELEASE ALLOWED
**운영 코드 무수정 · 실주문 0건**

---

## 결론 먼저

**두 코드 경로가 같은 답을 낸다는 것은 증명했다 (Match 6종 100%).
그러나 §13 승인 기준은 충족하지 못했다 — 전진(out-of-sample) 증거가 없다.**

그리고 검증 과정에서 **더 중요한 사실**이 나왔다.

> **최근 20거래일(2026-07-01 ~ 07-29) CHoCH 최종 신호 0건.**

작업지시서가 요구한 "20거래일 Paper Trading" 을 CHoCH 로 수행하면
**신호가 0건이라 아무것도 검증되지 않는다.**

---

## 1. Match 지표 (전 구간 재생 검증, 482거래일)

| 항목 | 기준 | 결과 | 불일치 | 판정 |
|---|---|---|---|---|
| Signal Match | 100% | **100.0%** | 0건 | ✅ |
| Trade Match | 100% | **100.0%** | 0건 | ✅ |
| Entry Match | 100% | **100.0%** | 0건 | ✅ |
| Stop Match | 100% | **100.0%** | 0건 | ✅ |
| Exit Match | 100% | **100.0%** | 0건 | ✅ |
| Position Match | 100% | **100.0%** | 0건 | ✅ |
| Regression | PASS | 474/16 | — | ✅ |
| Runtime Error | 0 | 0 | — | ✅ |
| CSV 저장 | PASS | 4종 생성 | — | ✅ |

```
Trading Days   482      Signal Count  117      Trade Count  116
신호 있던 날    91 / 482일 (18.9%)
Mismatch List  0건
```

⚠️ **분모는 신호 117건이다.** 처음에 일별 %의 산술평균을 냈더니 18.9%로
나왔는데, 신호 없는 날 81%가 0%로 잡힌 탓이었다. 지표를 고쳤다.

---

## 2. Funnel 통계 (482거래일)

| 단계 | 합계 | 일평균 | 최대 | 직전 대비 |
|---|---|---|---|---|
| Candidates | 36,554 | 75.84 | 76 | — |
| CHoCH | 684 | 1.42 | 9 | 1.9% |
| RVOL | 281 | 0.58 | 6 | **−58.9%** |
| ATR | 266 | 0.55 | 6 | −5.3% |
| MA50 | **117** | **0.24** | 4 | **−56.0%** |
| Trades (Top-3) | 116 | 0.24 | 3 | −0.9% |

**병목: 거래량 필터(−58.9%) > MA50(−56.0%).**

---

## 3. 평균 거래 빈도 — 승인의 실질적 장애물

```
Average Signal Freq   0.24 건/거래일
                    = 4.9 건 / 20거래일
                    = 월 약 5건 (전 구간 평균)

최근 20거래일 실측     0건
```

전 구간 평균으로는 20거래일에 4.9건이지만, **가장 최근 20거래일은 0건**이다.
신호가 91일(18.9%)에만 몰려 있고 편차가 크다.

| 기준 | 필요 | 예상 소요 |
|---|---|---|
| 20거래일 | — | 1개월 (신호 0~5건) |
| 30 signal | 30건 | **약 6개월** (0.24건/일 기준) |

**§4 "20거래일" 과 §13 "Match 100%" 를 동시에 만족시키려면,
그 20거래일 안에 신호가 나와야 한다. 최근 실적으로는 기대할 수 없다.**

---

## 4. 평균 Scan Time

| 항목 | 값 |
|---|---|
| Average | **0.166s** |
| Max | 0.309s |
| 95 Percentile | 0.188s |

76종목 1일치 스캔 기준. Live Scan 에 미치는 영향은 **일 0.17초 수준**이고,
`swing_runner` 는 하루 1회(15:35) 실행이므로 실질 부담이 없다.

⚠️ 증가율은 산출하지 않았다. 비교 대상(Pullback 엔진 스캔 시간)을
같은 조건에서 재지 않았기 때문이다. 추정하지 않는다.

---

## 5. Mismatch 분석

**0건.** 분석할 대상이 없다.

이 결과는 `ChochSignalEngine` 이 `backtest.adapter.SMCAdapter` 를
**그대로 호출**하기 때문이다. 재구현했다면 100%가 나오지 않았을 것이다.

---

## 6. §13 승인 기준 대비

| 항목 | 기준 | 결과 | 판정 |
|---|---|---|---|
| Signal Match | 100% | 100.0% | ✅ |
| Trade Match | 100% | 100.0% | ✅ |
| Entry Match | 100% | 100.0% | ✅ |
| Stop Match | 100% | 100.0% | ✅ |
| Exit Match | 100% | 100.0% | ✅ |
| Position Match | 100% | 100.0% | ✅ |
| Regression | PASS | 474 PASS / 16 FAIL | ✅ |
| Runtime Error | 0 | 0 | ✅ |
| CSV 저장 | PASS | 4종 | ✅ |
| **20거래일 Paper Trading** | **완료** | **미완료** | ⏸ |

**9/10 충족. 마지막 항목이 시간을 요구한다.**

⚠️ **위 Match 지표는 재생(replay) 검증이다.** 캐시된 과거 데이터로
두 코드 경로가 같은 답을 내는지 확인한 것이고, **전진 증거가 아니다.**
`--daily` 를 20거래일 누적해야 §4 요건을 충족한다.

---

## 7. 발견된 문제 (수정하지 않고 보고)

작업지시서 §"중요 원칙" 에 따라 **수정하지 않았다.**

### 7-1. CHoCH 신호 빈도가 검증 요건을 충족하지 못한다

최근 20거래일 0건. 30 signal 기준은 약 6개월 소요.
Paper Trading 기간을 늘리거나, 기준을 바꾸거나, 신호 빈도를
올리는 것 중 하나가 필요하다.

**단, 빈도를 올리는 세 경로 중 둘은 이미 막혀 있다:**
- CHoCH 완화 → Phase 0.2~0.3 전수 기각 (과최적화 확인)
- 독립 신호 추가 → Iteration 9 Phase 6 에서 6종 전수 기각
- **유니버스 확대 → 미시도. 유일하게 남은 경로.**

### 7-2. 내 판정 로직에 결함이 있었다 (수정 완료)

첫 실행에서 `Mismatch 0건 → PASS` 로 찍었다. 신호가 0건이면 비교
대상이 없어서 0인 것이지 일치를 확인한 게 아니다.
`INSUFFICIENT` 판정을 추가했다. Iteration 7-1 에서 같은 함정을 이미
지적했는데 같은 실수를 했다.

---

## 8. 제출물

| 항목 | 위치 |
|---|---|
| 최종 코드 | `phase1/paper_choch.py` |
| 크론 스크립트 | `phase1/paper_choch/run_daily.sh` (**미등록**) |
| CSV 4종 | `phase1/paper_choch/paper_{signal_log,funnel,diff,summary}.csv` |
| Regression | 474 PASS / 16 Known FAIL |
| 터미널 출력 | 아래 §9 |

### 변경 파일 목록

```
phase1/paper_choch.py              신규 (검증 도구)
phase1/paper_choch/run_daily.sh    신규 (크론용, 미등록)
phase1/EVIDENCE_REPORT.md          신규

운영 코드 무수정:
  main_auto_trading.py · risk_manager.py · exit_logic_optimized.py
  POSITION_SCHEMA · structure_stop_price · _normalize_position()
  choch_engine.py · BEST_CONFIG · BEST_ADAPTER_KWARGS
```

**실주문 0건.** `paper_choch.py` 는 `KiwoomAPI` 를 import 하지 않는다 —
주문을 호출할 경로 자체가 없다.

---

## 9. 다음 단계

크론은 **등록하지 않았다.** 운영 크론 변경은 별도 승인 사항으로 본다.

```bash
# 등록 시
40 15 * * 1-5 /home/greatbps/projects/kiwoom_trading/phase1/paper_choch/run_daily.sh
# 누적 리포트
python3 -m phase1.paper_choch --final
```

다만 §7-1 때문에, 크론을 지금 걸어도 20거래일 뒤 신호 0건으로
끝날 가능성이 높다. **유니버스 확대를 먼저 다루는 편이 낫다고 본다.**
