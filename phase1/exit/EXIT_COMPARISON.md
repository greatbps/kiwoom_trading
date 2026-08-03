# EXIT_COMPARISON

Iteration 8-2 Phase 5 · 판정

```
==============================
ITERATION 8-2 RESULT
==============================
Best Exit                 D10 — Time Exit 10일
Current PF (백테스트 A)    1.560
Current PF (운영 실제 E)   1.236
Best PF                   2.440
Test PF                   A 1.094 · E 0.871 · D10 1.782
MDD Difference            A -8.29% · E -17.22% · D10 -10.49%
Statistical Result        4조건 중 3개 충족, MDD 에서 탈락 (-13.42% vs -11.73%)
Decision                  **FAIL — 채택 기준 미달**
==============================
```

## 1. 이번 이터레이션의 진짜 결과는 순위표가 아니다

**운영이 실제로 쓰는 청산(E)은 백테스트가 쓰던 청산(A)보다 나쁘다.**

| | 전체 PF | Test PF | MDD | Test MDD | 승률 |
|---|---|---|---|---|---|
| A 백테스트 `EXIT_PROFILE` | 1.560 | 1.094 | −8.29% | −11.73% | 41.98% |
| **E 운영 실제 (2층)** | **1.236** | **0.871** | **−17.22%** | **−21.53%** | **34.58%** |

**Test 구간에서 운영 청산은 PF 0.871 — 손실이다.**
MDD 는 두 배 이상 나쁘다.

Iteration 8-0 이 "Live Swing PF 1.56" 이라고 보고했고, 8-1 이 그 위에서
선별을 비교했다. **그 1.56 은 운영이 쓰지 않는 청산의 숫자였다.**
운영 청산으로 다시 재면 1.236, Test 는 0.871 이다.

이것이 지금까지 "확인 불가" 로 남겨 뒀던 **백테스트↔실거래 격차의 측정된 원인**이다.
(Iteration 4: 실거래 swing 5건이 손실의 95.2%)

## 2. 왜 운영 청산이 나쁜가 — 익절이 없다

`EXIT_AUDIT.md` 참조.

```
장중 층 (exit_logic_optimized) — SWING 은 손절 전용
  구조손절 / SWING Hard Stop / Hard Stop / BE Stop      ✅
  Time Stop / Vol Tight / MFM / NO_PROGRESS /
  A_FORCE_EXIT / ATR 트레일링                            ❌ 전부 `not _is_swing`

EOD 층 (HoldingManager) — 여기에만 익절
  DRAWDOWN_STOP(5%) / TIME_EXIT(30일) / MA20_EXIT / REDUCE / TRAIL
```

실측: 장중 층만 돌리면 **청산 14건 전부 손절, 승률 0%, 부분익절 0건.**
2층 합쳐도 청산의 45%가 구조손절이다.

**손절은 5분봉 해상도로 즉시 집행되고, 익절은 하루 한 번 종가 기준으로만 판정된다.**
비대칭이 그대로 성과에 나온다 — 승률 34.58%, MDD −17.22%.

## 3. 대안 Exit 은 개선 여지를 보여준다

| Exit | Test PF | Test MDD | 4조건 |
|---|---|---|---|
| D10 Time Exit 10일 | **1.782** | −13.42% | 3/4 (MDD) |
| D5 Time Exit 5일 | 1.330 | −11.98% | 3/4 (MDD) |
| D3 Time Exit 3일 | 1.292 | −12.66% | 3/4 (MDD) |
| C ATR Trailing | 2.071 | −16.50% | 3/4 (MDD) |
| B Fixed Target | 0.835 | −16.14% | 0/4 |

**네 개가 Test PF·CI하한·P(PF<1) 세 조건을 동시에 만족한다.**
전부 MDD 하나에서 걸린다. D10 은 1.69%p 차이다 (47거래 — 잡음 범위).

특히 **단순 시간 청산(D3/D5/D10)이 운영 청산(E, Test 0.871)보다 전부 낫다.**
D10 은 Test PF 1.782 로 E 의 두 배다.

## 4. 결론

지시서 기준 **FAIL** — 4조건을 모두 만족하는 Exit 없음 → 다음 단계 Entry Timing Parity.

다만 "현재 Exit 유지" 를 문자 그대로 적용하면 안 된다.
**"현재 Exit"은 A 가 아니라 E 이고, E 는 비교한 7개 중 가장 나쁘다.**

권고 (승인 대기, 이번에 실행하지 않음):

1. **8-3 Entry Timing Parity 를 예정대로 진행한다.** 남은 미측정 격차는
   진입 타이밍뿐이다 (백테스트는 익일 시가, 운영은 장중).
2. **그와 별개로 SWING 익절 비대칭은 별도 안건으로 올린다.**
   장중 층에 SWING 익절이 하나도 없는 것은 성능 문제가 아니라 설계 공백이다.
   손절만 5분봉 해상도인 구조는 승률을 구조적으로 깎는다.
3. **D10(또는 D5) 시간청산은 후보로 남긴다.** 지금 채택하지 않는다 —
   MDD 조건 미달이고 표본이 47~67거래다. 유니버스·기간을 늘려 재검증한다.

## 5. 이번 이터레이션에서 바꾼 것

**운영 코드 0줄.**
Entry Rule · Pullback · CHoCH · ScoreEngine · Selection · Risk Manager ·
Position Size · EC_HALT · `phase0/portfolio.py` 전부 무변경.
`exit_logic_optimized.py` 의 `datetime` 은 **하네스 프로세스 안에서만** 교체했다
(파일 무수정).

Regression 507 PASS / 16 Known FAIL · Runtime Error 0 (장중 예외 0건).
