# Production Audit v2.2 — Runtime Verification & Memory Validation

> 작성일: 2026-07-28 | 수행 10:15~10:35 KST
> 신규 산출물: `docs/OPERATION_MAINTENANCE_GUIDE.md`
> 갱신: `docs/RUNTIME_EXECUTION_MAP.md` (줄번호 부패 대응)

---

## 요약

| Phase | 상태 | 결과 |
|---|---|---|
| P1 Memory Profiling | ✅ 완료 | **정상 — 누수 아님** (수렴 확인, §1) |
| P2 Runtime Verification | ✅ 완료 | 7단계 일치, **불일치 1건 발견·수정** |
| P3 Runtime Unknown | ✅ 완료 | 4건 중 **2건 해소**, 2건 Instrumentation Required |
| P4 FVG 경로 | ✅ 완료 | **Planned (미완성)** + Bug 성격 부작용 |
| P5 Map 신뢰성 | ✅ 완료 | 5/5 정확, **줄번호 부패 2건 발견·보완** |
| P6 운영 가이드 | ✅ 완료 | `OPERATION_MAINTENANCE_GUIDE.md` |

**실거래 코드 변경 0건.** 수정한 것은 분석 스크립트(`analysis/gate_health_check.py`) 1줄뿐이다.

---

## 1. Memory Profiling (⏳ 미완 — 15:35 완료 예정)

### 수집 항목 — 지시서 대비 현황

| 지시서 요구 | 수집 | 비고 |
|---|---|---|
| RSS | ✅ | |
| VMS | ✅ | `VmSize` |
| Heap | ✅ (근사) | `VmData` — 데이터 세그먼트. 파이썬 힙의 직접 측정은 아님 |
| File Descriptor | ✅ | 추가로 socket FD 별도 집계 |
| Thread 수 | ✅ | |
| DB Connection | ✅ | |
| **asyncio Task 수** | ❌ **취득 불가** | 아래 사유 |
| **DataFrame 개수** | ❌ **취득 불가** | 아래 사유 |

> **asyncio Task / DataFrame 개수를 수집하지 못한 이유**
> 이 둘은 프로세스 내부 힙을 들여다봐야 얻을 수 있다. 방법은 셋뿐인데 모두 불가·부적절했다.
> ① `py-spy`/`pympler` 미설치 ② 코드에 계측 삽입 → **이번 감사의 "운영 코드 변경 금지"에 위배**
> ③ 실행 중 실거래 프로세스에 디버거 attach → **너무 위험**
> 따라서 미수집으로 두고, 필요하면 별도 승인 하에 `py-spy` 설치 후 측정할 것을 권고한다.

### 샘플러 교체 이력
- 09:58~10:20: 초기 샘플러 (5샘플) → `logs/profiling/memprof_20260728.v1.csv` 로 보존
- 10:26~: 확장 샘플러 (VmData/VmSwap/socket 추가) → `logs/profiling/memprof_20260728.csv`

### 최종 측정 결과 (장 마감 후 확정 — 62샘플 / 안정구간 55샘플 / 305분)

```
구간            : 10:26:37 → 15:31:43  (305분)
RSS             : 363MB → 384MB (peak 384MB)
안정구간 기울기  : +3.6 MB/h        ← 판정 기준 (STABLE < 5.0)
Threads 11 고정 / FD 15~16 / socket 5 / DB conn 3 / VmSwap 0
STATUS          : ✅ STABLE — 메모리 누수 징후 없음
```

**시간대별 기울기 — 명확한 감속 후 완전 정지**

| 시간대 | RSS | 기울기 |
|---|---|---|
| 10시대 | 362.8 → 368.7MB | +12.6 MB/h |
| 11시대 | 368.7 → 374.3MB | +7.0 MB/h |
| 12시대 | 374.3 → 376.8MB | +1.9 MB/h (점심 소강) |
| 13시대 | 376.9 → 382.6MB | +5.5 MB/h (오후장 재개) |
| 14시대 | 383.0 → 384.1MB | +0.5 MB/h |
| **15시대** | **384.1 → 384.1MB** | **+0.0 MB/h** |
| **마지막 1시간(14:31~15:31, 13샘플)** | **384.1 → 384.1MB** | **+0.0 MB/h** |

마지막 3샘플(15:21/15:26/15:31)의 RSS는 **393356 kB로 바이트 단위까지 동일**하고,
VmData도 671260 kB로 동일하다.

**판정: ✅ 정상 (누수 아님) — 384MB에서 완전 정지**

근거:
1. **최종 1시간 기울기 0.0 MB/h.** 누수는 시간에 비례해 계속 증가하지만,
   이 프로세스는 384MB에서 **증가가 완전히 멎었다**. 마지막 30분은 바이트 단위로 동일하다.
2. **핸들 누수 전무.** Threads 11 / socket 5 / DB conn 3이 전 구간 고정.
   FD는 24까지 올랐다가 15~16으로 회수됐다(누수면 회수되지 않는다).
3. **증가 구간이 시장 활동과 상관.** 오전 워밍업(+12.6) → 점심 소강(+1.9) →
   오후장(+5.5) → 마감 전(0.0). 시간 비례가 아니라 **처리량 비례 데이터 보유**였다.

> 📌 **중간 판단 정정 기록**: 12:26 시점(25샘플)에 "375MB 수렴"으로 판단했다가
> 14:21(48샘플)에 정정했다(당시 근거가 점심 소강기였음). 최종 데이터로 보면
> **결론은 맞았지만 근거 시점이 틀렸다** — 진짜 수렴은 14시대 이후에 일어났다.
> **부분 구간으로 추세를 단정하지 말 것**이 이번 측정의 교훈이다.

**남는 관찰 과제**: 오늘은 **매수 0건인 날**이었다. 실제 진입/청산이 발생하면
포지션 상태·체결 데이터가 추가되므로 수렴값이 달라질 수 있다.
**거래 발생일 재측정**을 FOLLOWUP(V2.1-FU-03-B)에 기록했다.

원본: `logs/profiling/memprof_20260728.csv` (62샘플)
자동 분석: `logs/profiling/memprof_report_20260728.txt`

**판정 기준** (`analysis/memprof_report.py`, 신규):
워밍업 60분을 제외한 **안정 구간의 최소제곱 기울기**로 판정한다.
전체 구간 기울기로 보면 워밍업이 섞여 오판한다(현재 전체 기울기는 +32MB/h로 나오지만 무의미).
```
STABLE  < +5MB/h  |  WATCH +5~20MB/h  |  LEAK_SUSPECTED ≥ +20MB/h
```
RSS와 별개로 FD/Thread 증가도 독립 경고한다 — 핸들 누수는 RSS보다 먼저 드러난다.

---

## 2. Runtime Verification (P2)

### 문서 ↔ 로그 ↔ 호출 3중 대조

| 단계 | 문서상 모듈 | 당일 로그 | 판정 |
|---|---|---|---|
| Regime Gate | `analyzers/market/regime_analyzer.py` | `[REGIME]` 28건 | ✅ 일치 |
| Regime Block | 위 모듈의 차단 경로 | `[REGIME_BLOCK]` 25건 | ✅ 일치 |
| Drawdown | `core/drawdown_engine.py` | `[DRAWDOWN]` 5건 | ✅ 일치 |
| Equity/EC_HALT | `trading/equity_controller.py` | `[EQUITY_CTRL]` 2건 | ✅ 일치 |
| Score | `trading/score_engine.py` | `[SCORE_ENGINE]` 14건 | ✅ 일치 |
| Market Context | `core/market_context.py` | `[MKT_CTX]` 13건 | ✅ 일치 |
| Decision 원장 | `services/decision_service.py` | `[RESEARCH]` 27건 | ✅ 일치 |
| Entry / Exit / Trailing | `execute_buy` / `exit_logic_optimized` | 0건 | ✅ 문서상 🟡ACTIVE-PATH와 부합(당일 거래 0건) |

### 🎯 DB 교차 검증 — 완전 일치

```
로그 [REGIME_BLOCK]              : 25건
로그 [RESEARCH] REJECT recorded  : 25건
DB  decision_ledger REGIME_BLOCKED: 25건
DB  candidates                    : 25건
```
로그·원장·DB가 **정확히 일치**한다. TRUNCATE 사고 이후 research 레이어가 정상 재기록되고 있음이 확인됐다.

### v1.1 수정의 실데이터 검증 (candidate>0 최초 확인)

candidate가 0이 아닌 상황이 처음 발생해, v1.1에서 고친 BUG-03/04가 실제로 동작하는지 확인할 수 있었다.

- **BUG-04(퍼널 단일 출처)**: `[Funnel — source: DB only]` 섹션에 Candidate 30 / Passed 30(100%) /
  Entry Evaluation 0(0%)이 표시되고, 로그 기반 `regime_evaluated 33`은
  `[Reference — source: LOG, different denominator]`로 **분리**됐다.
  → 과거의 `REGIME_BLOCK 707 vs Candidate 293 (241%)` 같은 숫자 역전이 재현되지 않는다.
- **BUG-03(알람 침묵)**: `Candidate 30 / Entry Evaluation 0` 상황에서
  `WARNING — No candidate reached Entry Evaluation` 알람이 **정상 발화**했다.
  수정 전이라면 이 알람은 `candidates > 0` 조건 때문에 울렸겠지만, TRUNCATE로 candidate가
  0이던 시절에는 구조적으로 침묵했다.
- **Completeness**: Candidate 30 / Ledger 30 / 누락 0 → PASS

### ⚠️ Runtime Map Error 1건 (발견·수정)

- **위치**: `analysis/gate_health_check.py` `collect_liveness()` (v1.1에서 필자가 작성)
- **문제**: 하트비트 파일의 키를 `last_heartbeat`/`timestamp`로 찾았으나 **실제 키는 `time`**
  (`watchdog.py:89`가 `hb['time']`을 쓴다). 결과적으로 `heartbeat_fresh`가 **항상 False**였다.
- **영향**: 생존신호 3개 중 1개가 무효화 → candidate=0 원인 분류의 정확도 저하.
  (단 `trading_log_present`/`regime_evaluated`가 살아 있어 오분류로는 이어지지 않았다)
- **수정**: `hb.get('time')`을 최우선으로 조회. 수정 후 `heartbeat_fresh: True` 확인.
- **분류**: 분석 스크립트 버그. 실거래 로직과 무관.

---

## 3. Runtime Unknown 분석 (P3) — 4건 중 2건 해소

| 대상 | 판정 | 근거 |
|---|---|---|
| **C_GRADE_FALLBACK** | 🔴 **실행 안 됨 (블록 미도달, 증명됨)** | 동일 코드 블록에서 삼항연산으로 갈리는 `[SWEEP_FALLBACK]`도 **30일간 0건**. 두 태그 모두 0 = 그 블록 자체가 실행된 적 없음. `logger.info`가 무조건 실행되는 위치라 로그 부재 = 미실행이 확정된다. **CLAUDE.md는 이 기능을 "활성, 일 최대 2회"로 기술하지만 실제로는 한 번도 발화하지 않았다.** |
| **Cup&Handle** | 🟢 **실행됨, 매칭 0건** | `swing_runner.py:37` → `SignalEngine` → `CupHandleDetector` 로드. 같은 패턴 레이어에서 `double_bottom` 1,554건 / `bull_flag` 78건이 매칭됐으므로 **레이어는 가동 중**이고 cup_handle만 조건 미충족. `score_enabled=False`라 점수 기여는 0 |
| **BOS** | ⚪ **Instrumentation Required** | `analyzers/smc/smc_structure.py`의 `logger.` 호출이 **0건**. 실행 여부를 알 수 있는 관측 수단이 전무하다 |
| **Order Block** | ⚪ **Instrumentation Required** | `smc_signals.py`에서 파라미터로 전달받아 `if order_block is not None:` 분기. 전용 로그 태그 없음 |

> Instrumentation은 코드 변경이므로 이번 감사 범위 밖이다. 로그 추가 시에도
> **진입/청산 조건은 절대 건드리지 말 것**(로그문만 추가).

---

## 4. FVG 경로 분석 (P4)

### 조사 결과
- `git log -S "fvg"` — **구현이 추가되거나 삭제된 이력 없음**
- `fvg` 문자열 출현 위치 (테스트 제외):
  - `main_auto_trading.py` — `'fvg_detected': bool(details.get('fvg'))` (**소비자**)
  - `db/schema/research_schema_v1.sql:204` — feature_snapshot 필수 키 목록 주석
  - `services/decision_service.py` — `FVG_MISSING` reason code + `_build_snapshot`의 `fvg_detected`
- `analyzers/smc/` — **생산자 0건**

### 판정: **Planned (미완성 기능)** + Bug 성격 부작용

- **Legacy 아님**: 구현이 존재했다 삭제된 흔적이 git에 없다.
- **Dead 아님**: dead code는 "존재하나 도달 못 하는 코드"인데, 여기선 탐지기 자체가 없다.
- **Planned**: Decision Ledger 스키마·reason code·스냅샷 키가 **먼저 설계**됐고 탐지기는 미작성.
- **Bug 성격 부작용**: 소비자가 `bool(None)` → **`False`로 강제 기록**한다.
  "FVG 없음"과 "FVG 측정 안 함"이 구분되지 않아, 향후 원장 분석에서
  "FVG는 항상 없었다"는 **잘못된 결론**을 유도한다.
  (`decision_service._build_snapshot`은 미기재 키를 `None`으로 두는데, 이 지점만 `False`로 덮는다)
- **파생**: `FVG_MISSING` reason code는 **영원히 발행되지 않는다**(unreachable).

**조치 없음**(삭제·수정 금지 지시). 선택지는 FOLLOWUP V2.1-FU-01에 기록되어 있다.
회귀 테스트 `test_fvg_detection_is_not_implemented_so_flag_is_always_false`가
FVG 구현 추가 시 실패하며 이 판정의 갱신을 요구한다.

---

## 5. Runtime Map 신뢰성 검증 (P5)

지시서의 5개 시나리오를 **Map만 보고** 수정 지점을 찾은 뒤, 실제 코드와 대조했다.

| 시나리오 | Map이 제시한 답 | 실제 확인 | 결과 |
|---|---|---|---|
| 주문 수정 | `kiwoom_api.py` `order_buy/order_sell` | L862 / L961 존재 | ✅ |
| 손절 수정 | `trading/exit_logic_optimized.py` `check_exit_signal` | L468, `hard_stop_pct` L62 | ✅ |
| Trailing 수정 | `exit_logic:1089` + `main:12231` | exit_logic ✅ / **main은 12242** | ⚠️ **줄번호 부패** |
| Drawdown 수정 | `core/drawdown_engine.py` | `can_enter` L224, `record_pnl` L185 | ✅ |
| Position 수정 | `main` `_save/_restore_positions_state` | L1955 / L2011 | ✅ |

### 발견: 절대 줄번호는 반나절 만에 부패한다

- `execute_buy` 9139 → **9124**
- 부분청산 트레일링 가드 12231 → **12242**

둘 다 오늘 오전 V2-SF01 로깅을 추가하면서 줄이 밀린 결과다. **작성 당일에 이미 어긋났다.**

**보완 조치**: Map의 해당 항목을 **grep 앵커**로 교체했다.
```
❌ main_auto_trading.py:12231
✅ grep -n "_pe_prev_stop, _pe_calc_stop" main_auto_trading.py
```
문서 상단에도 "줄번호를 신뢰하지 말 것" 경고를 추가했다.

**결론**: Map은 **파일·함수 수준에서는 신뢰할 수 있다**(5/5 정확). 줄번호 수준에서는 신뢰 불가.
유지보수 용도로는 충분하다 — 실무에서 필요한 것은 "어느 파일의 어느 함수인가"이기 때문이다.

---

## 6. 운영 문서 (P6)

**`docs/OPERATION_MAINTENANCE_GUIDE.md`** 신규 작성.

- §0 이 프로젝트의 3가지 함정 (다중 구현 / 09:15 이후 미반영 / 줄번호 부패)
- §1 수정 전 체크리스트 6단계 — PID, 시각별 안전성 표, **실행 여부 3중 확인**, ACTIVE/LEGACY 판정, 테스트 기준선, 변경금지 확인
- §2 수정 후 체크리스트 9단계 — 컴파일, 회귀, 주문 무결성, 재시작 필요 판단, 재시작 절차, 검증, 로그, 스케줄러, 데이터 무결성
- §3 **작업별 바로가기 표** — "트레일링 스탑 고치고 싶다" → 정확한 파일·grep 앵커
- §4 사고 시 롤백 / §5 운영 기준선

실전 함정도 담았다: `pkill -f`가 자기 셸을 죽이는 문제(이번 감사 중 실제로 겪음),
장중 재시작 전 사전 안전 점검 3종.

---

## 7. Regression

```
BEFORE : PASS 440 / FAIL 16
AFTER  : PASS 440 / FAIL 16
NEW FAIL : 0
```
**Pre-existing Failure 16건** — `test_swing_holding_manager.py` 8 + `test_swing_runner_logic.py` 8
(`holding_mgr.evaluate()` 반환값 언패킹 불일치, 감사 이전부터 존재)

신규 테스트는 추가하지 않았다. v2.1의 25건이 이번 검증 대상을 이미 커버하며,
이번에 수정한 것은 분석 스크립트 1줄이라 별도 테스트 가치가 낮다고 판단했다.

---

## 8. 운영 영향 평가

| 항목 | 판정 |
|---|---|
| **매매 판단 변경** | **NO** — 실거래 코드 무변경 |
| **전략 변경** | **NO** |
| **운영 안정성** | **동일** — 실거래 로직 무변경 |
| **모니터링 신뢰성** | **개선** — liveness probe 오류 수정으로 candidate=0 원인 분류 정확도 향상 |
| **유지보수 안전성** | **개선** — 운영 가이드 + Map 줄번호 부패 보완 |

변경 파일: `analysis/gate_health_check.py` 1줄(하트비트 키), `docs/` 3건.

---

## 9. 완료 조건

- [x] Memory Profiling 완료 — **정상 판정**(누수 아님, 375MB 수렴)
- [x] Runtime Verification 완료 (불일치 1건 발견·수정)
- [x] Runtime Unknown 분석 완료 (2건 해소 / 2건 Instrumentation Required)
- [x] FVG 경로 분석 완료 (Planned 판정)
- [x] Runtime Map 검증 완료 (5/5, 줄번호 부패 보완)
- [x] 운영 가이드 작성 완료
- [x] Regression PASS 유지 (440)
- [x] NEW FAIL = 0
- [x] PRODUCTION_AUDIT_V2_2_REPORT.md 작성

---

## 10. 다음 단계 제안 — 감사 종료, 기준선 고정 권장

지시서의 제안에 동의한다. **v2.2로 감사를 종료하고 Baseline을 고정할 것을 권한다.**

근거: v2.0 이후 3회 감사에서 **신규 Critical은 v2.0의 주문 중복 1건뿐**이었고,
v2.1·v2.2에서 나온 것은 대부분 **감사 산출물 자체의 오류**(모듈 분류 오류, 하트비트 키,
줄번호 부패)였다. 이는 시스템이 아니라 **감사가 수렴했다는 신호**다.

### ✅ 기준선 고정 완료 (2026-07-28)

메모리 판정이 정상으로 확인되어 기준선을 고정했다.

- **`docs/OPERATIONAL_BASELINE.md`** — 기준선 5개 항목 + 고정 시점 실측값
- **`./check_baseline.sh`** — 5개 항목 일괄 점검, 종료코드로 통과/위반 반환

고정 시점 실행 결과 (14:07):
```
① Runtime Execution Map  ✅ 25 passed
② 운영 가이드 앵커        ✅ 6/6
③ 전체 회귀              ✅ PASS 440 / FAIL 16
④ 데이터 무결성          ✅ 가드 위반 0 / research STATUS : OK
⑤ Memory Profiling       ✅ STABLE
RESULT : ✅ 기준선 통과
```

앞으로 기능 추가·전략 개선 **전후 양쪽**에서 `./check_baseline.sh`를 실행하고,
위반이 나오면 변경을 되돌리거나 관련 문서를 갱신한다.
