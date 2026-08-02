# Known Issues Registry

> v1.0 | 2026-07-28 | Manifest: `docs/BASELINE_MANIFEST.md` v1.0.0
> 여기 등재된 항목은 **기준선에 포함된 기존 문제**다. 새로 생긴 문제는 여기 없으므로
> `./check_baseline.sh`가 실패하면 그건 **신규 회귀**로 간주하고 되돌린다.

| ID | 등급 | 항목 | 운영 영향 | 상태 |
|---|---|---|---|---|
| KI-01 | Low | pytest Known FAIL 16건 (테스트 낡음) | **없음** | 미해결 |
| KI-02 | Medium | FVG 플래그 항상 False (미구현) | 없음(분석 왜곡) | 보류 |
| KI-03 | Medium | Runtime Unknown 2건 (BOS/Order Block) | 없음 | 계측 필요 |
| KI-04 | Medium | 실거래 모듈 다중 사본 | 없음(오수정 위험) | 보류(삭제 금지) |
| KI-05 | Low | 거래 발생일 메모리 미측정 | 없음 | 관찰 |
| KI-06 | Low | C_GRADE_FALLBACK 문서-실제 불일치 | 없음 | 확인 필요 |
| KI-07 | Low | gate_health_check 이중 실행 | 없음 | 보류 |
| KI-08 | Low | Silent Failure WARNING 193건 미판정 | 미상 | 보류 |

---

## KI-01 [Low] pytest Known FAIL 16건 — **테스트 낡음, 코드 정상**

**대상**
- `tests/unit/test_swing_holding_manager.py` — 8건
  (`test_case2_two_consecutive_days_exit`, `test_case4_ma20_below_exits_immediately`,
   `test_case5_add_bullish_and_rising`, `test_case8_drawdown_exit`,
   `test_case9_max_hold_days_exit`, `test_case10_trail_condition`,
   `test_add_lot_size_first`, `test_add_lot_size_second`)
- `tests/unit/test_swing_runner_logic.py` — 8건
  (`test_a5_held_sector_blocks_same_new`, `test_b1_exposure_cap_blocks_new_entry`,
   `test_d1_exit_generates_sell_order`, `test_d2_add_generates_add_order`,
   `test_d3_hold_generates_no_order`, `test_g2_exposure_at_max_returns_empty`,
   `test_g4_trail_action_in_process_hold`, `test_h3_main_with_upgrade`)

**원인 — 코드가 아니라 테스트가 낡았다 (확인 완료)**

```python
# 실제 구현 (analyzers/swing/holding_manager.py:62)
def evaluate(self, pos, df_daily, score_drift=0.0) -> tuple[str, str]:
    return 'HOLD', 'HOLD'

# 실사용 (swing_runner.py:400) — 정상 언패킹 ✅
action, exit_reason = holding_mgr.evaluate(pos, df, score_drift=score_drift)

# 테스트 (test_swing_holding_manager.py:122) — 구 시그니처 기대 ❌
result = mgr.evaluate(pos, df)
assert result == 'EXIT'      # AssertionError: ('EXIT','MA5_EXIT') == 'EXIT'
```

`evaluate()`가 2-tuple을 반환하도록 바뀐 시점에 **실사용 코드는 갱신됐지만 테스트는 방치**됐다.
`test_swing_runner_logic.py` 쪽은 mock의 반환 arity가 낡아 `swing_runner`의 언패킹이 실패한다.

**운영 영향: 없음.** 실거래 경로(`swing_runner.py`)는 정상 동작한다.

**해결 방법**: 16개 테스트의 단언을 2-tuple 언패킹으로 갱신. 운영 코드는 건드리지 않는다.
해결 시 `PYTEST_MIN_PASS`를 456으로, `PYTEST_MAX_FAIL`을 0으로 올리고 Manifest PATCH 버전 증가.

---

## KI-02 [Medium] FVG 플래그가 항상 False — 탐지기 미구현

- **위치**: `main_auto_trading.py` → `grep -n "fvg_detected"`
- **내용**: `bool(details.get('fvg'))`를 Decision Ledger에 기록하는데,
  `analyzers/smc/` 전체에 `fvg`를 설정하는 구현이 **0건**. 항상 `None` → `False`.
- **판정**: **Planned (미완성)**. git 이력에 구현·삭제 흔적 없음.
  Ledger 스키마·`FVG_MISSING` reason code·스냅샷 키만 먼저 설계되고 탐지기 미작성.
- **부작용**: "FVG 없음"과 "FVG 측정 안 함"이 뭉개져 향후 원장 분석을 왜곡한다.
  `FVG_MISSING` reason code는 **영원히 발행되지 않는다**(unreachable).
- **운영 영향**: 없음(게이트 조건으로 쓰이지 않음).
- **선택지**: ① 탐지기 구현(전략 변경 → 승인 필요) ② 기록·코드 제거 ③ 현상 유지
- **회귀 감지**: `test_fvg_detection_is_not_implemented_so_flag_is_always_false`
  (FVG 구현 추가 시 실패하며 이 항목 갱신을 요구)

---

## KI-03 [Medium] Runtime Unknown 2건 — 관측 수단 부재

| 대상 | 상태 |
|---|---|
| **BOS** | `analyzers/smc/smc_structure.py`에 `logger.` 호출 **0건**. 실행 여부를 알 방법이 전무 |
| **Order Block** | `smc_signals.py`에서 파라미터로 전달받아 분기. 전용 로그 태그 없음 |

- **해결 방법**: 각 분기에 `logger.debug`/`[TAG]` 한 줄 추가 → 판정 가능.
- **주의**: 로그 추가 시에도 **진입/청산 조건은 절대 건드리지 말 것**(로그문만).
- **운영 영향**: 없음. 관측성 문제일 뿐.

---

## KI-04 [Medium] 실거래 모듈 다중 사본 — 오수정 위험

전체 501개 모듈 중 **330개 미로드**. 특히 같은 역할 구현이 3~6개 공존한다.

| 기능 | 🟢 실사용 | 휴면 |
|---|---|---|
| 주문 실행 | `kiwoom_api.py` | `core/order_executor.py`, `trading/order_executor.py`(LOADED-ONLY), `brokers/kiwoom_broker.py`(LOADED-ONLY) |
| 손절/청산 | `trading/exit_logic_optimized.py` | `core/stop_loss_manager.py`, `core/auto_stop_loss_system.py`, `trading/stop_loss_executor.py`, `trading/trend_exit_engine.py`(LOADED-ONLY), `gpt_share/…`, `docs/share/…` |
| 포지션 | `main_auto_trading.self.positions` | `core/position_manager.py`, `trading/position_tracker.py`(LOADED-ONLY), `core/portfolio_manager.py` |
| 스케줄링 | cron | `core/scheduler.py` |

- **위험**: `core/auto_stop_loss_system.py`처럼 **이름이 그럴싸한 휴면 모듈**을 고치고
  "손절을 개선했다"고 오인. 청산 로직은 **동명 파일이 2개 더 있다**.
- **삭제 금지** 지시로 미조치. 대신 `test_runtime_map_v21_20260728.py`가 휴면 상태를 고정한다.
- **권장 후속**: 휴면 모듈 상단에 `# UNUSED — not in execution path` 마커 추가 또는
  `deprecated/` 이동. 별도 승인 필요.
- **주의**: 330개 중 상당수는 **수동 실행용 분석 스크립트**로 정상적인 미로드다. 일괄 삭제 금지.

---

## KI-05 [Low] 거래 발생일 메모리 미측정

- 2026-07-28 측정(62샘플)은 **매수 0건인 날**이었다. 안정구간 +3.6 MB/h, 384MB 수렴.
- 실제 진입/청산이 발생하면 포지션 상태·체결 데이터가 추가되어 수렴값이 달라질 수 있다.
- **조치**: 거래 발생일에 `python3 -m analysis.memprof_report` 재실행 →
  `BASELINE_MANIFEST.md`의 `MEMORY_BASELINE_RSS_MB` 갱신(MINOR 버전 증가).
- **누수 판단 조건**: 안정구간 +20 MB/h 이상 지속, 또는 FD/Thread가 회수 없이 증가.

---

## KI-06 [Low] C_GRADE_FALLBACK — 문서와 실제 불일치

- `CLAUDE.md`는 "C_GRADE_FALLBACK 활성, 일 최대 2회" / "Sweep Fallback 활성, 일 최대 3회"로 기술.
- **실제로는 30일간 한 번도 발화하지 않았다.** 같은 코드 블록에서 삼항으로 갈리는
  `[SWEEP_FALLBACK]`도 0건이므로 **블록 자체가 미도달**임이 증명된다
  (`logger.info`가 무조건 실행되는 위치라 로그 부재 = 미실행 확정).
- **판단 필요**: ① 의도대로 조건이 엄격한 것인가 ② 상류 게이트가 막고 있는가 ③ 문서가 틀린 것인가
- **운영 영향**: 없음(기능이 안 도는 것일 뿐 오동작 아님). 다만 문서 신뢰도 문제.

---

## KI-07 [Low] gate_health_check 이중 실행

- 15:58 자체 크론 + 16:05 `operations_daily_summary`가 `run_gate_health_check()` 직접 호출.
- 같은 검사가 하루 2회 돌고, 그 사이 데이터가 바뀌면 두 리포트가 불일치할 수 있다.
- **권장**: 15:58 크론 제거 또는 `operations_daily_summary`가 15:58 산출물을 읽도록 변경.

---

## KI-08 [Low] Silent Failure WARNING 193건 미판정

- v2.0에서 프로젝트 전체 Silent Failure를 **467건** 전수 분류:
  SAFE 200 / **WARNING 193** / CRITICAL후보 74.
- CRITICAL 후보는 수동 검토 후 진성 2건만 수정(`positions_strategy.json` 저장/로드).
- **WARNING 193건은 개별 위험도 판정이 남아 있다.**
- 재검사: `python3 /tmp/.../silent.py` 또는 동등한 AST 스캔 재작성.

---

## 등재/해소 규칙

**등재**: 감사·운영 중 발견됐으나 즉시 수정하지 않기로 한 항목. 반드시 아래를 적는다.
① 운영 영향 유무 ② 미수정 사유 ③ 해결 방법 ④ 관련 회귀 테스트

**해소**: 문제를 실제로 고치면
① 이 문서에서 제거(또는 해소 표기)
② `BASELINE_MANIFEST.md` 기준값 갱신 + 버전 증가
③ `./check_baseline.sh` 통과 확인
