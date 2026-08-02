# Baseline Manifest

> **이 문서가 기준선의 Single Source of Truth다.**
> `./check_baseline.sh`가 아래 `BASELINE:BEGIN~END` 블록을 **직접 파싱해서** 검증한다.
> 따라서 기준값을 바꾸려면 반드시 이 파일을 수정해야 하고, 수정하면 `MANIFEST_VERSION`을 올려야 한다.

---

## 기계 판독 블록 (check_baseline.sh가 읽는다 — 형식 변경 금지)

<!-- BASELINE:BEGIN -->
```ini
MANIFEST_VERSION=1.0.0
FROZEN_AT=2026-07-28
FROZEN_BY="Production Audit v1.0~v2.2"

# ── pytest 기준 (tests/unit/ + tests/simulation/) ──
PYTEST_MIN_PASS=440
PYTEST_MAX_FAIL=16

# ── Runtime Execution Map ──
RUNTIME_MAP_TEST_FILE=tests/unit/test_runtime_map_v21_20260728.py
RUNTIME_MAP_EXPECT_PASS=25

# ── Operation Guide 앵커 ──
GUIDE_ANCHORS_EXPECT=6

# ── Memory ──
MEMORY_MAX_STABLE_SLOPE_MB_H=5.0
MEMORY_WARMUP_EXCLUDE_MIN=60
MEMORY_BASELINE_RSS_MB=384

# ── 버그 허용치 ──
CRITICAL_MAX=0
HIGH_MAX=0
```
<!-- BASELINE:END -->

---

## 1. 시스템 기준 (Frozen Artifacts)

| 산출물 | 버전/기준 | 파일 |
|---|---|---|
| Runtime Execution Map | v2.1 (2026-07-28) | `docs/RUNTIME_EXECUTION_MAP.md` |
| Operation Maintenance Guide | v1.0 (2026-07-28) | `docs/OPERATION_MAINTENANCE_GUIDE.md` |
| Operational Baseline | v1.0 (2026-07-28) | `docs/OPERATIONAL_BASELINE.md` |
| Known Issues Registry | v1.0 (2026-07-28) | `docs/KNOWN_ISSUES.md` |
| Governance | v1.0 (2026-07-28) | `docs/BASELINE_GOVERNANCE_v1.md` |
| Release Gate 스크립트 | — | `./check_baseline.sh` |

### 코드 기준 (Architecture Freeze)

| 항목 | 고정값 |
|---|---|
| 전체 모듈 수 | 501 |
| 로드되는 모듈 (ACTIVE) | **171** |
| 미로드 모듈 | 330 |
| 주문 실행 경로 | `kiwoom_api.py` `order_buy` / `order_sell` **단일** |
| 청산 실행 경로 | `trading/exit_logic_optimized.py` **단일** |
| 동시성 모델 | asyncio 단일 이벤트 루프 (`threading` 직접 사용 0건) |
| cron 작업 수 | 22 |
| watchdog 실행 | 08:45 / 09:00 / 09:15 (3회) |

> 위 값이 바뀌면 `tests/unit/test_runtime_map_v21_20260728.py`가 실패한다.
> 의도된 변경이면 Map을 갱신하고 `MANIFEST_VERSION`을 올린다.

---

## 2. pytest 기준

| 항목 | 값 |
|---|---|
| 대상 | `tests/unit/` + `tests/simulation/` |
| **최소 PASS** | **440** |
| **최대 FAIL** | **16** (Known FAIL, 아래) |
| NEW FAIL 허용 | **0** |

> `tests/` 전체를 돌리면 `tests/phase1/`, `tests/phase3/`, `tests/trading/`에서
> collection 에러가 난다(`signal_processing` 등 부재 모듈). 기준선은
> `tests/unit/ tests/simulation/` 범위로 정의한다.

### Known FAIL 16건 (기준선에 포함된 기존 실패)

전부 **테스트 낡음(test drift)** 이며 **운영 영향 없음**. 상세는 `docs/KNOWN_ISSUES.md` KI-01.

`tests/unit/test_swing_holding_manager.py` — 8건
`tests/unit/test_swing_runner_logic.py` — 8건

---

## 3. Memory Baseline

| 항목 | 값 |
|---|---|
| 판정 기준 | 안정구간 기울기 **< +5.0 MB/h** |
| 워밍업 제외 | 기동 후 **60분** |
| 고정 시점 실측 | **+3.6 MB/h** (62샘플 / 305분) |
| RSS 수렴값 | **384 MB** |
| 마지막 1시간 기울기 | **+0.0 MB/h** |
| Threads / FD / socket / DB conn | 11 / 15~16 / 5 / 3 (전 구간 고정) |
| 측정 도구 | `python3 -m analysis.memprof_report` |

> ⚠️ 이 값은 **매수 0건인 날**의 측정치다. 거래 발생일 재측정 필요(KI-05).

---

## 4. Research Integrity 기준

| 항목 | 기준 |
|---|---|
| `analysis/check_research_integrity` | `STATUS : OK` |
| `analysis/check_destructive_tests` | 위반 **0건** |
| Decision Ledger 정합성 | 로그 = 원장 = DB **3중 일치** |
| 고정 시점 실측 (2026-07-28) | candidates 340 = decision_ledger 340 = `[REGIME_BLOCK]` 340 |

---

## 5. 현재 기준 요약

```
PASS            : 440
Known FAIL      : 16
NEW FAIL        : 0
Critical        : 0
High            : 0
Memory          : STABLE (+3.6 MB/h, 384MB 수렴)
Research        : OK
Runtime Map     : PASS (25 tests)
Operation Guide : PASS (6/6 anchors)
```

**최종 검증**: 2026-07-28 15:40, `./check_baseline.sh` 종료코드 0

---

## 6. Manifest 버전 관리 규칙

`MANIFEST_VERSION`은 `MAJOR.MINOR.PATCH`.

| 변경 유형 | 올릴 자리 | 예 |
|---|---|---|
| 아키텍처 변경 (주문/청산 경로, 동시성 모델, ACTIVE 모듈 구성) | **MAJOR** | 1.0.0 → 2.0.0 |
| 기준값 변경 (PASS 수, Memory 임계, 앵커 수) | **MINOR** | 1.0.0 → 1.1.0 |
| Known FAIL 해소, 문서 오타·보강 | **PATCH** | 1.0.0 → 1.0.1 |

**버전을 올릴 때 반드시 함께 할 일**
1. 이 문서의 `BASELINE:BEGIN~END` 블록 갱신
2. 아래 변경 이력에 한 줄 추가
3. `./check_baseline.sh` 실행해 통과 확인
4. 영향받는 문서 갱신 (`RUNTIME_EXECUTION_MAP.md` / `OPERATION_MAINTENANCE_GUIDE.md` / `KNOWN_ISSUES.md`)

---

## 7. 변경 이력

| 버전 | 일자 | 변경 | 근거 |
|---|---|---|---|
| **1.0.0** | 2026-07-28 | 최초 고정 | Production Audit v1.0~v2.2 완료. Critical 0 / Memory STABLE / Research OK 확인 |
