# 작업지시서 — Iteration 8

> 자동 생성. Iteration 7-2 결과에 근거한다.

문서 버전: v1.0
생성: 2026-08-02
선행: Iteration 7-2 (Exit Parity) — **PASS** (Mapping 90.5%)
Regression 기준: 466 PASS / 16 Known FAIL

---

## 1. 이번 목표 달성 여부

| 기준 | 목표 | 결과 |
|---|---|---|
| Exit Mapping | ≥90% | **90.5%** ✅ |
| Win Rate | ≥40% | 43.8% ✅ |
| Profit Factor | ≥1.5 | 1.884 ✅ |
| MDD | ≤15% | −11.27% ✅ |
| Average Holding | ≥5일 | 8.8봉 ✅ |
| Regression | PASS | 466/16 ✅ |

**전 항목 충족.** 다만 아래 두 가지가 남아 있다.

---

## 2. 미해결 — 다음으로 넘긴다

### 2-1. Iteration 7-1 운영 검증이 아직 끝나지 않았다

`[POSITION_SCHEMA]` 계측은 붙였으나 **표본 0건**이다.
10거래일 또는 SWING 신규 10건이 쌓여야 판정할 수 있다.

```bash
python3 -m phase1.schema_monitor --days 10
```

**이게 PASS 되기 전에는 Exit 규칙 변경을 운영에 반영하면 안 된다.**
손절값 전달이 정상인지 확인되지 않은 상태에서 청산 규칙을 바꾸면
어느 쪽이 원인인지 구분할 수 없게 된다.

### 2-2. 실거래 95.8%가 여전히 미검증

백테스트는 CHoCH 후보 기준이다. 실거래의 95.8%를 만든
조건검색식 `VWAP+AI` 전략은 **한 번도 백테스트된 적이 없다.**
Exit Parity 를 맞춘 것은 나머지 3%(스윙) 쪽이다.

---

## 3. Iteration 8 — 두 갈래, 사용자 선택 필요

### 안 ㉠ — Exit 규칙 운영 반영 (Case 3 채택)

**전제**: Iteration 7-1 이 PASS 된 뒤에만 착수.

변경 대상:
```
config/strategy_hybrid.yaml
  - SWING 포지션의 시간 청산(15:00/15:10) 제외
  - SWING 포지션의 오버나이트 차단 제외
  - MAX_HOLD 를 봉 단위로 정의
```

⚠️ **위험**: 오버나이트 허용은 갭 리스크를 계좌에 그대로 얹는다.
백테스트 MDD −11.27% 는 일봉 기준이고, 실거래 갭은 더 클 수 있다.
Iteration 6 에서 확인했듯 삼성SDI 는 하루 만에 −9.9% 까지 갔다.

산출: `exit_policy_change.md` · Regression · 동일조건 재백테스트

### 안 ㉡ — VWAP+AI 전략 백테스트 착수

실거래의 95.8%를 검증 가능하게 만든다.

필요한 것:
- 분봉 OHLCV 데이터 확보 경로 (**현재 없음**)
- VWAP 상향 돌파 신호 재현
- 장중 청산 규칙 구현 (TIME_EXIT 38.4% · EARLY_FAILURE 19.0%)
- CLAUDE.md 절대원칙("인트라데이 없음") 개정 여부 결정

⚠️ Phase 0~1 의 일봉 백테스트 자산 대부분이 재사용 불가다.
별도 Phase 로 분리하는 것이 맞다.

---

## 4. 즉시 조치 권고 (Iteration 과 무관)

### 4-1. 운영 원장에 TEST 데이터 4건
`TEST01` · `TEST99`. 분석 때마다 걸러야 하고 집계에 섞이면 조용히 틀린다.

### 4-2. 청산 49.6%에 대응 매수 기록 없음
Iteration 3·4·7-2 에서 반복 지적됐고 개선되지 않았다.
진입가·보유기간 분석이 그만큼 불완전하다.

### 4-3. 청산 행의 `strategy_name` 미기록
SWING/INTRADAY 구분이 부정확하다. Iteration 7-2 의 OVERNIGHT_BLOCK
분석에서 NAVER·삼성전자가 INTRADAY 로 잘못 분류됐다 —
Iteration 4 에서 Swing Runner 매수로 확인된 종목인데도.

### 4-4. EC_HALT drawdown 이상치 (Iter2 보고, 미해결)
`dd=-93.7%` · `dd=-54.4%` 기록이 수천 건.

---

## 5. 절차 (고정)

```
Baseline Freeze  →  측정  →  원인 규명  →  최소 수정
     →  Regression  →  동일조건 재백테스트  →  KPI 검증  →  다음
```

각 Iteration 종료 시 다음 작업지시서를 자동 생성한다.

---

## 6. 남은 전제

- 일봉 근사. Live 의 분 단위 청산을 재현할 수 없다.
- MA5_EXIT 7건 · OVERNIGHT_BLOCK 8건은 표본이 얇아 규칙 판정 불가.
- Train/Validation/Test 구간 모두 파라미터 선택에 이미 사용된 데이터다.
- yfinance 데이터 변동성이 실제보다 크다 (삼성전자 연율 56%).
