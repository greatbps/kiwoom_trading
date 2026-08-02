# Phase 1 Iteration 6 — Swing Risk Execution 정상화

문서 버전: v1.0
생성: 2026-08-02
Regression: **446 PASS / 16 Known FAIL** — RELEASE ALLOWED (기존 440 + 신규 6)

---

## Phase 1 — Stop 전달 경로 수정 ✅

`main_auto_trading.py` `_attach_swing_risk_positions()` 에
`structure_stop_price` 전달을 추가했다.

```python
_stop = sp_entry.get('stop_price') or sp_entry.get('stop')
...
# 진입가보다 높은 손절가는 잘못된 값 — 넣으면 즉시 청산된다
if _stop is not None and _stop >= entry_price:
    logger.warning(...)
    _stop = None

self.positions[code] = {
    ...
    'structure_stop_price': _stop,
    ...
}
```

부가 변경 2건 (같은 함수 내, 최소 범위):

1. **진입가 이상 손절가 방어.** 잘못된 값이 들어오면 편입 즉시 청산된다.
   손절값 전달을 고치면서 이 방어를 빼면 더 큰 사고가 난다.
2. **로그에 손절값 출력.** 이 결함이 몇 달간 드러나지 않은 이유가
   관측 부재였다 — "어태치했다" 만 찍고 "무엇을" 어태치했는지 안 찍었다.

```
[SWING_RISK_ATTACH] 006400 삼성SDI entry=684,000 qty=8 stop=670,658(-1.95%)
[SWING_RISK_ATTACH] 006400 삼성SDI entry=684,000 qty=8 stop=없음 → SWING fallback(-12%)
```

### 회귀 테스트 신규 6건

`tests/unit/test_swing_stop_attach.py` — 키 하나가 빠지는 것만으로
재발하고, 재발해도 **예외가 나지 않는다.** 그래서 못박았다.

| 테스트 | 지키는 것 |
|---|---|
| `test_attach_passes_structure_stop_price` | 키 존재 |
| `test_attach_keeps_swing_markers` | `_is_swing` 판정용 두 키 유지 |
| `test_exit_logic_reads_structure_stop_price` | 반대편 키 이름 고정 |
| `test_swing_fallback_still_exists` | 손절값 없는 포지션의 최후 방어 유지 |
| `test_invalid_stop_is_rejected` | 진입가 이상 손절 방어 |
| `test_attach_logs_stop_value` | 관측 가능성 |

---

## Phase 2 — strategy_horizon 유실 조사 ⚠️ 부분 규명

### 확정된 사실

`[HARD_STOP] -5.0% (-6.95%)` 문자열의 생산자는 코드베이스 전체에서
**단 한 곳** — `exit_logic_optimized.py:872`, **비-SWING `else` 분기**다.

```python
_is_swing = (position.get('strategy_horizon','') == 'SWING'
             or position.get('strategy','') == 'swing')
```

따라서 청산 시점에 `_is_swing == False` 였다. 두 키가 **둘 다** 없었다.

### 청산 주체 특정

```
2026-05-13 09:00:09  [SELL_COMPLETE] 006400 ... reason=09:00 [HARD_STOP] -5.0% (-6.95%)
2026-05-13 09:00:10  [SWING_RISK_ATTACH] 006400 ... → hard stop 어태치
2026-05-13 08:50:13  [ORPHAN_CHECK] 정상 — 브로커=['006400'] 엔진=['006400']
```

**청산(09:00:09)이 어태치(09:00:10)보다 1초 빠르다.** 그리고 08:50에
이미 엔진이 보유 중이었다. 즉 이 포지션은 `_attach_swing_risk_positions()`
가 만든 것이 **아니다.**

### `self.positions` 에 쓰는 경로 전수

| 위치 | 함수 | `strategy_horizon` | `structure_stop_price` |
|---|---|---|---|
| L2070 | `_restore_positions_state` | ✅ 기본값 SWING 보장 | ❌ |
| L2406 | `initialize_account` (기존 갱신) | ❌ (`strategy` 만) | ❌ |
| L2435 | `initialize_account` (신규 생성) | ❌ (`strategy` 만) | ❌ |
| L8668 | `_attach_swing_risk_positions` | ✅ | ✅ **이번에 추가** |

`_restore_positions_state` 는 **당일 진입분만** 복원한다
(`if not entry_date_str.startswith(today): continue`). 삼성SDI 는
05-12 진입이므로 05-13 기동 시 복원 대상이 아니다.

→ **`initialize_account` (L2406/L2435) 경로로 들어왔을 가능성이 높다.**

### 미규명

L2435 는 `'strategy': _strategy_tag` 를 넣고,
`_strategy_tag = _pos_strategy.get(code) or ('swing' if code in _swing_codes else 'smc')`
이다. 현재 `positions_strategy.json` 에는 `'006400': 'swing'` 이 있으므로
`_is_swing` 이 True 여야 한다.

**2026-05-13 당시 그 파일에 006400 이 없었을 가능성**이 있으나
과거 파일 상태를 확인할 수 없어 **확증하지 못했다. 추정으로 단정하지
않는다.**

### ⚠️ 이번 수정이 그 경로를 덮지 못한다

가장 큰 손실을 낸 삼성SDI 는 `_attach_swing_risk_positions()` 가 아니라
`initialize_account` 경로로 들어왔다. **이번 Phase 1 수정은 그 경로를
고치지 않았다.** L2406 / L2435 에도 `structure_stop_price` 가 없다.

작업지시서가 `_attach_swing_risk_positions()` 만 지목했으므로 범위를
넘겨 수정하지 않았다. **다음 Iteration 최우선 항목으로 올린다.**

---

## Phase 3 — 백테스트 비교 ✅

`sl_pct` 만 바꾸고 나머지 청산 조건은 전부 고정.

### 전체 구간 (2024-08-01 ~ 2026-07-30, 482거래일)

| 케이스 | 거래 | 승률 | PF | 기대값 | 수익 | MDD | 평균보유 | 연속손실 |
|---|---|---|---|---|---|---|---|---|
| **Before −12%** (유실 상태) | 80 | 56.2% | 1.606 | 2.394% | +38.30% | **−17.65%** | 13.1봉 | 6 |
| **After −5%** (구조손절 캡) | 81 | 43.2% | **1.813** | 2.246% | +36.38% | **−11.27%** | 8.6봉 | 10 |
| 참고 −3% | 83 | 36.1% | 1.876 | 1.943% | +32.26% | −9.86% | 6.0봉 | 9 |
| 참고 −2% | 84 | 29.8% | 1.985 | 1.821% | +30.60% | −9.41% | 4.5봉 | 9 |

### Before → After 변화

```
거래수        80 →     81    +1
승률       56.2% →  43.2%   -13.0pp
PF        1.606 →  1.813   +0.207
기대값     2.394% → 2.246%   -0.148%p
총수익     38.30% → 36.38%   -1.92%p
MDD      -17.65% → -11.27%  +6.38%p
평균보유    13.1봉 →  8.6봉   -4.5봉
최대연속손실   6 →     10      +4
```

### Walk Forward

| 구간 | Before PF | After PF | Before MDD | After MDD |
|---|---|---|---|---|
| Train | **0.985** | **1.504** | −17.65% | −11.27% |
| Validation | 1.816 | 1.689 | −11.89% | −8.37% |
| Test | 1.289 | **1.693** | −10.18% | −6.58% |

**Train 구간에서 Before 는 PF 0.985 — 손실이다.** After 는 1.504.
Validation 만 Before 가 앞선다(1.816 vs 1.689).

### 해석 — 공짜가 아니다

손절을 조이면 **승률이 13pp 떨어지고 최대 연속손실이 6→10 으로 늘어난다.**
총수익도 1.92%p 줄어든다. 대신 MDD 가 6.38%p 개선되고 PF 가 오른다.

**"손실만 줄고 나머지는 그대로" 가 아니다. 거래 성격이 바뀐다.**
운영자가 −12% 시절의 승률(56%)에 익숙하다면 43%는 체감상 훨씬 나쁘게
느껴질 수 있다. 이건 정상 동작이다.

---

## Phase 4 — Exit KPI

| KPI | 목표 | 결과 | 판정 |
|---|---|---|---|
| Stop price 전달률 | 100% | 100% (attach 경로) | ✅ |
| `structure_stop_price` 존재율 | 100% | 100% (attach 경로) | ✅ |
| 손절 미실행 | 0건 | **미검증** — 실거래 발생 필요 | ⏸ |
| Exit mapping | ≥90% | **21.9%** | ❌ |
| Regression | 440/16 | 446/16 | ✅ |

**Exit mapping 21.9%** 는 이번 수정으로 개선되지 않는다. Live 청산의
78%(189건)가 `EARLY_FAILURE` · `OVERNIGHT_BLOCK` · `MA5_EXIT` ·
`DRAWDOWN_STOP` 인데 백테스트에 대응 규칙이 없다. 이건 **백테스트에
규칙을 추가하는 작업**이지 Exit 실행 수정이 아니다.

---

## Phase 5 — Entry 개선 (미착수)

완료 조건 미충족으로 착수하지 않았다.

| 조건 | 상태 |
|---|---|
| Exit 정상화 PASS | ⚠️ 부분 — `initialize_account` 경로 미수정 |
| Swing Backtest ↔ Live 전략 동일화 | ❌ 미착수 (진입 규칙이 CHoCH vs pullback) |
| 30건 이상 검증 데이터 | ❌ 현재 5건 |

필수 로그 추가(`candidate_first_time` · `entry_signal_time` ·
`entry_price_vs_signal_price`)도 착수하지 않았다 — Entry 단계 진입
조건이 충족되지 않았고, 운영 코드 추가 수정은 별도 승인 사항이다.

---

## 변경 파일

```
main_auto_trading.py                     +21 -8   (_attach_swing_risk_positions 만)
tests/unit/test_swing_stop_attach.py     신규 6 테스트
phase1/stop_backtest_compare.py          신규
phase1/stop_backtest_compare.{csv,json}  신규
```

`trading/exit_logic_optimized.py` · `core/risk_manager.py` ·
`trading/score_engine.py` 무수정.

---

## 실거래 반영 전 확인 사항

1. **`initialize_account` 경로 미수정** — 가장 큰 손실을 낸 경로다.
   이것을 고치지 않으면 재시작 후 편입되는 스윙 포지션은 여전히
   설계 손절이 집행되지 않는다.
2. **손절이 강해진다** — −12% → −1.4~5.0%. 조기 청산이 늘고 승률이
   13pp 떨어진다. 백테스트로 확인했으나 실거래 성격 변화는 크다.
3. **장중 반영 금지** — CLAUDE.md 절차대로 장마감 후 재시작이 필요하다.
   09:15 이후 수정은 당일 미반영이다.
4. **dry-run 선행 권장** — 편입 로그에 `stop=...` 가 실제로 찍히는지
   확인 후 실거래 적용.
