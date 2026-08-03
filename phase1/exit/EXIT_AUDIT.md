# EXIT_AUDIT

Iteration 8-2 Phase 1 · 운영 Exit 감사

## 1. 운영 SWING 청산은 **2층 구조**다

| 층 | 모듈 | 주기 | 담당 |
|---|---|---|---|
| ① 장중 | `trading/exit_logic_optimized.OptimizedExitLogic.check_exit_signal` | 5분봉 루프 | **손절만** |
| ② EOD | `swing_runner` → `analyzers/swing/holding_manager.HoldingManager.evaluate` | 일봉, 15:35 크론 | **익절 + 시간청산** |

이 두 층을 같이 봐야 운영 청산이 재현된다.

## 2. ① 장중 층 — SWING 은 익절 경로가 **하나도 없다**

`check_exit_signal` 은 17개 청산 분기를 가지고 있으나
수익 실현 계열이 전부 `not _is_swing` 게이트 뒤에 있다.

| Exit Type | 위치 | 조건 | SWING 적용 |
|---|---|---|---|
| 구조 손절 | L884 | `close ≤ structure_stop_price` (−5% cap) | ✅ |
| SWING Hard Stop | L895 | `swing_hard_stop_pct` | ✅ |
| Hard Stop | L907 | `max_stop_pct` | ✅ |
| BE Stop | L924 | TP2 후 BE+buffer | ✅ (TP2 도달 시) |
| **Time Stop** | L723 | `not _is_swing` | ❌ |
| **Vol Tight Stop** | L783 | `not _is_swing` | ❌ |
| **MFM** | L809 | `not _is_swing` | ❌ |
| **NO_PROGRESS** | L1019 | `not _is_swing_pos` | ❌ |
| **A_FORCE_EXIT** | L1031 | `not _is_swing_pos` | ❌ |
| **ATR 트레일링** | L1083 | `not _is_swing` | ❌ |
| EOD 15:00 시간청산 | L1159 | — | (SWING 은 다일 보유) |

**우선순위** (docstring 명시): 5분 최소락 → 구조/Hard Stop → TP1후 BE →
R-TP1 부분익절 → R-TP2 → A급 보호 → ATR 트레일링 → EOD 15:00.

### 실측으로 확인

①층만 재생했을 때 (243일, 81진입):

```
청산 14건 · 승률 0.0% · 전부 [STRUCTURE_STOP]
부분익절 0건
평균 보유 24일 (청산이 안 나서 슬롯이 계속 막힘)
```

**SWING 포지션에게 장중 층은 손절 전용이다.**

## 3. ② EOD 층 — 여기에 익절이 있다

`HoldingManager.evaluate(pos, df_daily, score_drift)`

| Exit Type | 조건 | 설정 키 |
|---|---|---|
| `DRAWDOWN_STOP` | `drawdown_pct ≥ drawdown_exit_pct` | `swing.hold.drawdown_exit_pct` (5.0) |
| `TIME_EXIT` | `holding_days ≥ max_hold_days` | `swing.hold.max_hold_days` (30) |
| `MA20_EXIT` | `close < MA20` | `swing.hold.ma20_hard_exit` (true) |
| `REDUCE` | 수익 ≥ 6% + 드로우다운 경고 + MA5 이탈 | `reduce_min_profit_pct` 등 |
| `TRAIL` | 보유 ≥ 7일 + 수익 ≥ 7% | `trail_start_*` |

액션 우선순위: `EXIT > REDUCE > ADD_MOMENTUM > ADD_VALUE > TRAIL`, 종목당 하루 1액션.

## 4. 백테스트 Exit 은 이 둘 중 어느 쪽도 아니다

`phase0.portfolio.EXIT_PROFILE` (Iteration 8-0/8-1 이 쓴 것):

```
swing_mode=True  tp_pct=0.15  sl_pct=-0.05  min_hold_bars=3
trailing_pct=0.08  be_trigger_pct=0.05  max_hold_bars=30  commission=0.0035
```

파일 주석이 이미 밝히고 있다 — **"운영 config 의 청산은 5분봉 기준이라
일봉으로 그대로 옮길 수 없다. 아래 값은 일봉 스윙 근사치"**.

즉 **8-0/8-1 이 보고한 PF 1.56 은 운영이 쓰지 않는 청산으로 얻은 숫자다.**

## 5. 재현성 결함 — 운영 Exit 은 과거 재생이 안 된다

`exit_logic_optimized.py` 는 `datetime.now()` 를 5곳에서 직접 호출한다
(경과 분 계산, EOD 15:00 판정 등). 과거 봉으로 돌리면 **항상 "지금"** 을 본다.

이번 감사는 하네스에서 모듈의 `datetime` 속성만 교체해 시간을 고정했다.
**운영 소스는 수정하지 않았다.** 다만 이 의존성 자체가
"운영 청산을 백테스트로 검증할 수 없게 만드는" 구조적 결함이다.
