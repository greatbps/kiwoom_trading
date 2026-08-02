# 운영 기준선 (Operational Baseline) v1.0

> 고정일: 2026-07-28 | Production Audit v2.0~v2.2 완료 시점
> 관련: `RUNTIME_EXECUTION_MAP.md`, `OPERATION_MAINTENANCE_GUIDE.md`, `PRODUCTION_AUDIT_V2_2_REPORT.md`

이 문서는 **"지금 이 상태가 정상이다"** 를 못 박는다.
앞으로 기능 추가·전략 개선을 하기 전에 **아래 5개 기준을 모두 통과**해야 하고,
작업 후에도 다시 통과해야 한다. 통과하지 못하면 작업을 되돌린다.

---

## 기준선 5개 항목

### ① Runtime Execution Map 최신 상태

**기준**: `docs/RUNTIME_EXECUTION_MAP.md`가 실제 실행 경로와 일치

**검증**
```bash
python3 -m pytest tests/unit/test_runtime_map_v21_20260728.py -q --no-cov
```
**기대**: 25 passed

이 테스트가 실패하면 둘 중 하나다 — 휴면 모듈을 실행 경로에 넣었거나, 실사용 모듈을 뺐다.
어느 쪽이든 Map 갱신 없이 넘어가면 안 된다.

**고정 시점 값**: 전체 501개 모듈 / 로드 171 / 미로드 330

---

### ② 운영 가이드 최신 상태

**기준**: `docs/OPERATION_MAINTENANCE_GUIDE.md`의 §3 "작업별 바로가기" 표가 실제와 일치

**검증** (수동 — 표의 grep 앵커가 여전히 잡히는지)
```bash
grep -n "max(prev_stop, calc_stop)"   trading/exit_logic_optimized.py
grep -n "_pe_prev_stop, _pe_calc_stop" main_auto_trading.py
grep -n "def order_buy\|def order_sell" kiwoom_api.py
grep -n "def check_exit_signal"        trading/exit_logic_optimized.py
grep -n "def can_enter"                core/drawdown_engine.py
```
**기대**: 5개 모두 결과 있음

---

### ③ 전체 회귀 테스트

**기준**: PASS ≥ 440, **NEW FAIL = 0**

**검증**
```bash
python3 -m pytest tests/unit/ tests/simulation/ --no-cov -q 2>&1 | tail -3
```
**기대**: `16 failed, 440 passed`

**Pre-existing Failure 16건** (이 16건은 기준선에 포함된 기존 실패다 — 늘어나면 안 된다)
- `tests/unit/test_swing_holding_manager.py` 8건
- `tests/unit/test_swing_runner_logic.py` 8건
- 원인: `holding_mgr.evaluate()` 반환값 언패킹 불일치 (`ValueError: too many values to unpack`)
- 감사 이전부터 존재. **별도 과제로 남아 있다**(FOLLOWUP 참조)

> 주의: `tests/` 전체를 돌리면 `tests/phase1/`, `tests/phase3/` 등에서 collection 에러가 난다
> (`signal_processing` 모듈 부재 — 레거시 테스트). 기준선은 `tests/unit/ tests/simulation/` 기준이다.

---

### ④ 신규 Critical / High 버그 0건

**기준**: 미해결 Critical·High 없음

**현재 상태 (2026-07-28 고정 시점)**

| 감사 | Critical | High | 상태 |
|---|---|---|---|
| Pipeline Audit v1.0 → Fix v1.1 | 0 | 2 | ✅ 전부 수정 |
| Production Audit v2.0 | 1 | 3 | ✅ Critical 1 + High 2 수정, 1건은 삭제금지로 보고 |
| Production Audit v2.1 | 0 | 1 | ✅ 수정(감사 산출물 오류) |
| Production Audit v2.2 | 0 | 0 | — |

**수정 완료된 주요 항목**
- `V2-CRIT01` 주문 API 타임아웃 재시도 → 중복 주문 위험 제거 (`kiwoom_api.py`)
- `BUG-01` Entry Quality YAML 설정 무시 (`main_auto_trading.py`)
- `BUG-02` 부분청산 trailing stop monotonic 가드
- `BUG-03/04` gate_health_check 알람 침묵 + 퍼널 출처 혼합
- `V2-SF01/02` positions_strategy.json 무음 실패

**미해결(승인 대기)** — `docs/PIPELINE_AUDIT_FOLLOWUP.md`,
`docs/PRODUCTION_AUDIT_V2_FOLLOWUP.md`, `docs/PRODUCTION_AUDIT_V2_1_FOLLOWUP.md`
전부 Medium 이하이며 실거래 판단에 영향 없음.

---

### ⑤ Memory Profiling 정상

**기준**: 안정 구간(워밍업 60분 제외) 기울기 < +5 MB/h, FD·Thread 누수 없음

**검증**
```bash
python3 -m analysis.memprof_report
```
**기대**: `STATUS : ✅ STABLE`

**고정 시점 측정값 — 장 마감 후 확정 (2026-07-28, PID 3199837, 62샘플 / 305분)**

| 지표 | 값 |
|---|---|
| RSS 수렴값 | **384 MB** |
| **안정구간 기울기** | **+3.6 MB/h** (STABLE 기준 <5.0) |
| **마지막 1시간 기울기** | **+0.0 MB/h** (완전 정지) |
| Threads | **11 고정** |
| File Descriptors | **15~16** (일시 24 상승 후 회수) |
| Sockets / DB Connections | **5 / 3 고정** |
| VmData(힙 근사) | 634 → 656 MB (마지막 30분 동일값) |
| VmSwap | 0 |

**판정: ✅ 정상 (누수 아님) — 384MB에서 완전 정지**

시간대별 기울기가 **+12.6 → +7.0 → +1.9 → +5.5 → +0.5 → +0.0 MB/h**로 감속했고,
마지막 3샘플(15:21/15:26/15:31)은 RSS·VmData가 **바이트 단위까지 동일**하다.
누수라면 시간에 비례해 계속 증가해야 하는데 완전히 멎었다.

핸들도 전 구간 고정(Threads 11 / socket 5 / DB conn 3)이고, FD는 24까지 올랐다가
15~16으로 **회수**됐다 — 핸들 누수는 RSS 누수보다 먼저 드러나는데 전무하다.

> ⚠️ 이 값은 **매수 0건인 날**의 측정치다. 실제 진입/청산이 발생하면 포지션 상태·체결
> 데이터가 추가되므로 수렴값이 달라질 수 있다. **거래 발생일 재측정** 필요(FOLLOWUP V2.1-FU-03-B).
>
> 📌 측정 교훈: 12:26 시점(25샘플)에 "375MB 수렴"으로 판단했다가 정정했다.
> 근거로 삼은 평탄 구간이 **점심 소강기**였고 오후장에 다시 올랐다.
> **부분 구간으로 추세를 단정하지 말 것.** 진짜 수렴은 14시대 이후에 일어났다.

> ⚠️ 미수집 항목: **asyncio Task 수 / DataFrame 개수**.
> py-spy·pympler 미설치이고, 코드 계측은 "변경 금지" 위배, 실거래 프로세스 디버거 attach는
> 위험하다고 판단해 수집하지 않았다. 필요 시 별도 승인 하에 `py-spy` 설치 후 측정할 것.

---

## 운영 환경 기준값 (참고)

| 항목 | 값 |
|---|---|
| 실행 프로세스 | `./venv/bin/python main_auto_trading.py` (단일) |
| 동시성 모델 | asyncio 단일 이벤트 루프 (`threading` 직접 사용 0건) |
| watchdog 크론 | 08:45 / 09:00 / 09:15 **3회뿐** — 정상 프로세스는 재시작하지 않음 |
| 코드 반영 | **09:15 이후 수정은 당일 자동 반영 안 됨** (수동 재시작 필요) |
| 크론 작업 수 | 22개 |
| DB | PostgreSQL `trading_system` (SQLite 금지) |
| Decision Ledger | 로그·원장·DB 3중 일치 확인 (07-28: 25/25/25) |

---

## 기준선 재고정이 필요한 경우

아래 상황에서는 이 문서를 갱신해야 한다.

1. 모듈 추가·삭제로 `test_runtime_map_v21_20260728.py`가 실패할 때
2. Pre-existing Failure 16건 중 일부를 실제로 고쳤을 때 (PASS 기준선 상향)
3. 메모리 수렴값이 크게 바뀌었을 때 (예: 500MB 초과)
4. watchdog 크론 스케줄을 변경했을 때
5. 신규 Critical/High 버그를 수정했을 때

---

## 부록 — 기준선 일괄 점검

**`./check_baseline.sh`** (프로젝트 루트, 실행 가능)

```bash
./check_baseline.sh          # 종료코드 0 = 통과 / 1 = 위반
```

5개 항목을 한 번에 점검하고 하나라도 실패하면 종료코드 1을 반환한다.
기능 추가·전략 개선 **전후 양쪽**에서 실행할 것.

### 고정 시점 실행 결과 (2026-07-28 15:40, 장 마감 후 최종)

```
① Runtime Execution Map    ✅ 25 passed
② 운영 가이드 앵커          ✅ 6/6 유효
③ 전체 회귀                ✅ PASS 440 / FAIL 16
④ 데이터 무결성            ✅ 파괴적 SQL 가드 위반 0건 / research STATUS : OK
⑤ Memory Profiling         ✅ STABLE

RESULT : ✅ 기준선 통과
```

> `research STATUS : OK`는 의미가 있다. 2026-07-27 TRUNCATE 사고 직후에는 WARNING이었고,
> 이날 처음으로 OK로 회복됐다(candidates/decision_ledger/event_store 정상 축적 재개).
