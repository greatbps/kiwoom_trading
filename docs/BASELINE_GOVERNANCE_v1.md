# Baseline Governance v1.0

> 발효일: 2026-07-28 | Manifest: `docs/BASELINE_MANIFEST.md` v1.0.0
> 적용 범위: `kiwoom_trading` 자동매매 시스템의 모든 코드 변경

**이 문서부터 운영 방식이 "감사 중심"에서 "기준선 유지 중심"으로 바뀐다.**
앞으로 모든 변경은 Release Gate를 통과해야 운영에 반영할 수 있다.

---

## 1. 현재 시스템 상태

### 완료된 것 (Production Audit v1.0 ~ v2.2)

| 항목 | 결과 |
|---|---|
| Critical 버그 | **0건** (v2.0의 주문 중복 위험 제거 완료) |
| 중복 주문 위험 | **제거** — 주문 API의 타임아웃 재시도 차단 |
| 설정 반영 신뢰성 | **확보** — Entry Quality YAML이 실제로 반영됨 |
| 데이터 정합성 | **확보** — 로그=원장=DB 3중 일치 (340/340/340) |
| 메모리 안정성 | **검증** — 384MB 수렴, 마지막 1시간 +0.0 MB/h |
| Runtime Execution Map | **구축** — 501개 모듈 4단계 분류 |
| 운영 기준선 | **고정** — Manifest v1.0.0 |
| 회귀 테스트 체계 | **확립** — 440 PASS, 실행경로 구조까지 테스트로 고정 |

### 알려진 문제

`docs/KNOWN_ISSUES.md` 8건(KI-01~08). **전부 운영 영향 없음.**
가장 큰 것은 Known FAIL 16건인데, 확인 결과 **코드가 아니라 테스트가 낡은 것**이다
(`evaluate()`가 2-tuple 반환으로 바뀌었는데 테스트만 구 시그니처 기대. 실사용
`swing_runner.py:400`은 정상 언패킹).

---

## 2. Baseline 정의

**기준선의 Single Source of Truth는 `docs/BASELINE_MANIFEST.md`다.**

`check_baseline.sh`는 기준값을 하드코딩하지 않고 Manifest의 `BASELINE:BEGIN~END` 블록을
**직접 파싱**한다. 따라서 기준을 바꾸려면 Manifest를 고쳐야 하고, 고치면 버전을 올려야 한다.
이 구조가 "슬쩍 기준을 낮춰 통과시키는 것"을 막는다.

```
PASS            : 440          Known FAIL : 16 (KI-01)
NEW FAIL        : 0            Critical   : 0      High : 0
Memory          : STABLE (+3.6 MB/h, 384MB 수렴)
Research        : OK
Runtime Map     : PASS (25 tests)
Operation Guide : PASS (6/6 anchors)
```

### Architecture Freeze

| 항목 | 고정값 |
|---|---|
| 주문 실행 경로 | `kiwoom_api.py` `order_buy`/`order_sell` **단일** |
| 청산 실행 경로 | `trading/exit_logic_optimized.py` **단일** |
| 로드 모듈 / 전체 | **171 / 501** |
| 동시성 모델 | asyncio 단일 이벤트 루프 |
| watchdog | 08:45 / 09:00 / 09:15 |

이 값이 바뀌면 `tests/unit/test_runtime_map_v21_20260728.py`가 실패한다.

---

## 3. Release Gate

### 실행

```bash
./check_baseline.sh
```

| 종료코드 | 의미 |
|---|---|
| **0** | ✅ RELEASE ALLOWED — 운영 반영 가능 |
| **1** | ❌ RELEASE BLOCKED — 되돌리거나 Manifest 갱신 |
| 2 | Manifest 없음/형식 오류 — 검증 불가 |

### 검증 5항목

| # | 항목 | 실패 시 의미 |
|---|---|---|
| ① | Runtime Execution Map (25 tests) | 실행 경로 변경됨 → Map 갱신 + MAJOR 증가 |
| ② | Operation Guide 앵커 (6/6) | 가이드 바로가기가 깨짐 → §3 갱신 |
| ③ | 전체 회귀 (PASS≥440, FAIL≤16) | **NEW FAIL 발생** → 즉시 되돌림 |
| ④ | 데이터 무결성 (가드 0건 + Research OK) | 파괴적 SQL 또는 데이터 유실 |
| ⑤ | Memory (안정구간 < +5.0 MB/h) | 누수 의심 |

> **동작 검증 완료**: 기준을 일부러 위반시킨 음성 테스트에서
> `RELEASE BLOCKED` + 종료코드 1이 정상 반환되는 것을 확인했다(2026-07-28).
> 게이트가 실제로 차단한다.

---

## 4. 운영 규칙

### 4-1. 변경 반영 순서 — **직접 운영 반영 금지**

```
Feature (개발)
   ↓
Regression (pytest)
   ↓
Baseline (./check_baseline.sh)
   ↓
Deploy (재시작)
```

**Gate를 건너뛴 운영 반영은 금지한다.** 이유: 이 시스템은 실계좌를 운용하며,
과거 사고가 전부 "검증 없이 반영"에서 나왔다(TRUNCATE로 인한 원장 유실,
주문 타임아웃 재시도로 인한 중복 체결 위험, 26일 무거래를 알람이 놓친 사건).

### 4-2. 시각별 반영 규칙

| 시각 | 파일 수정 | 재시작 |
|---|---|---|
| ~08:44 | 안전 | 08:45 watchdog이 자동 반영 |
| **08:45~09:15** | **위험** — 크래시 시 watchdog이 미검증 코드로 재시작 | 신중히 |
| 09:15~15:30 | 안전(자동 반영 없음) | **장중 재시작은 사전 승인 필수** |
| 15:30~ | 안전 | 자유 |

> watchdog은 **정상 프로세스를 재시작하지 않는다**(하트비트가 신선하면 통과).
> 따라서 **09:15 이후 수정은 당일 자동 반영되지 않는다.**

### 4-3. 장중 재시작 사전 점검 (필수 3종)

```bash
grep -E "주문 성공" logs/auto_trading_$(date +%Y%m%d).log | tail -3   # 미체결 주문 0건
python3 -c "import json;d=json.load(open('data/positions_state.json'));print(len({k:v for k,v in d.items() if k!='_meta'}))"
cat /tmp/kiwoom_heartbeat.json
```
셋 다 안전하지 않으면 재시작하지 않는다.

### 4-4. 절대 변경 금지 (승인 없이)

진입 조건 / 청산 조건 / Score 계산 / MIN_SCORE / SMC·CHoCH·BOS /
Regime Gate / Drawdown / LCL / Position Sizing / Risk 정책 / YAML 기본값

### 4-5. 수정 전 필수 확인 — 휴면 코드 함정

이 프로젝트는 **같은 기능의 구현이 3~6개 공존**한다.
`core/auto_stop_loss_system.py`처럼 이름이 그럴싸한 파일이 **한 번도 실행되지 않는다.**

수정 전 반드시 `docs/RUNTIME_EXECUTION_MAP.md §3 대조표`를 확인하거나:
```bash
grep -rn "import <모듈>" main_auto_trading.py     # 로드되는가
grep -rn "<클래스>(" main_auto_trading.py          # 호출되는가
grep "\[<태그>\]" logs/auto_trading_$(date +%Y%m%d).log   # 실행 흔적
```

---

## 5. 변경 절차

### 5-1. 일반 변경 (버그 수정, 로깅 개선)

1. `./check_baseline.sh` — **변경 전** 기준선 확인
2. 최소 범위로 수정 (리팩토링 금지)
3. `python3 -m py_compile <수정파일>`
4. `./check_baseline.sh` — **변경 후** 통과 확인
5. 재시작 필요 여부 판단 (§4-2) → 필요 시 승인받고 재시작
6. 재시작 후 검증 (`docs/OPERATION_MAINTENANCE_GUIDE.md §2-⑥`)

### 5-2. Gate가 BLOCK된 경우

| 원인 | 조치 |
|---|---|
| NEW FAIL 발생 | **변경을 되돌린다.** 기준을 낮추지 않는다 |
| 실행 경로 변경(①) | 의도된 것이면 Map 갱신 + **MAJOR** 증가. 아니면 되돌림 |
| 앵커 깨짐(②) | Guide §3 갱신 + **PATCH** 증가 |
| Memory 이상(⑤) | 누수 조사. 원인 불명이면 되돌림 |

> ⚠️ **기준값을 낮춰서 통과시키는 것은 금지한다.**
> Manifest 수정은 "문제를 해결했거나 기준이 실제로 바뀐" 경우에만 한다.

### 5-3. Manifest 버전 증가 규칙

| 변경 유형 | 자리 |
|---|---|
| 아키텍처 변경 (주문/청산 경로, 동시성, ACTIVE 구성) | **MAJOR** |
| 기준값 변경 (PASS 수, Memory 임계) | **MINOR** |
| Known FAIL 해소, 문서 보강 | **PATCH** |

버전을 올릴 때 함께: ① Manifest 블록 갱신 ② 변경 이력 추가 ③ Gate 통과 확인
④ 영향 문서 갱신(Map / Guide / Known Issues)

### 5-4. Known Issue 해소 시

예: KI-01(테스트 16건)을 고치면
1. 16개 테스트를 2-tuple 언패킹으로 갱신
2. Manifest: `PYTEST_MIN_PASS=456`, `PYTEST_MAX_FAIL=0`, 버전 `1.0.1`
3. `KNOWN_ISSUES.md`에서 KI-01 해소 표기
4. `./check_baseline.sh` 통과 확인

---

## 6. 문서 체계

| 문서 | 역할 | 언제 보나 |
|---|---|---|
| **BASELINE_MANIFEST.md** | 기준선 정의 (SSOT) | 기준값 확인·변경 시 |
| **BASELINE_GOVERNANCE_v1.md** | 운영 규칙 (본 문서) | 변경 절차 확인 시 |
| **KNOWN_ISSUES.md** | 알려진 문제 8건 | Gate 실패 원인 대조 시 |
| **RUNTIME_EXECUTION_MAP.md** | 실행 경로 지도 | **코드 수정 전 필수** |
| **OPERATION_MAINTENANCE_GUIDE.md** | 수정 전후 체크리스트 | 실제 작업 시 |
| **OPERATIONAL_BASELINE.md** | 기준선 실측값 상세 | 근거 확인 시 |
| `check_baseline.sh` | Release Gate | **변경 전후 매번** |

감사 보고서(`PIPELINE_AUDIT_*`, `PRODUCTION_AUDIT_*`)는 이력 자료로 보존한다.

---

## 7. 다음 단계

기준선이 고정됐으므로 이제 **기능 개발·전략 개선을 진행할 수 있다.**
다만 모든 변경은 §5 절차를 따른다.

**우선 권장 작업** (기준선을 더 튼튼하게 만드는 순서)

1. **KI-01 해소** — 테스트 16건 갱신. 운영 코드를 건드리지 않으므로 가장 안전하고,
   해소되면 `FAIL=0`이 되어 이후 NEW FAIL 감지가 훨씬 명확해진다.
2. **KI-05** — 거래 발생일 메모리 재측정으로 기준값 확정
3. **KI-06** — C_GRADE_FALLBACK 문서-실제 불일치 확인 (기능이 의도대로 도는지)
4. **KI-03** — BOS/Order Block 계측 추가 (로그문만)

`FVG`(KI-02)와 `모듈 다중 사본`(KI-04)은 전략·구조 변경이 필요하므로 별도 승인 후.
