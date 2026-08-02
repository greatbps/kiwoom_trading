# Production Audit v2.1 — Runtime Reachability & Execution Map

> 작성일: 2026-07-28 | 수행 09:58~10:15 KST
> 주 산출물: **`docs/RUNTIME_EXECUTION_MAP.md`**
> 회귀 테스트: `tests/unit/test_runtime_map_v21_20260728.py` (25건)
> 이월: `docs/PRODUCTION_AUDIT_V2_1_FOLLOWUP.md`

---

## 1. 감사 요약

| 등급 | 개수 | 항목 |
|---|---|---|
| **Critical** | **0** | 신규 없음 |
| **High** | **1** | V2.1-H01 **v2.0 모듈 분류 오류** (감사 산출물 자체의 오류) |
| **Medium** | **2** | V2.1-M01 FVG 플래그 항상 False, V2.1-M02 RSS 증가 추세(관찰 중) |
| **Low** | **2** | LOADED-ONLY 개념 부재로 인한 혼동, FD 증가(13→24) |

**실거래 코드 변경 없음.** 이번 작업은 순수 감사이며, 수정한 것은 v2.0 보고서의 잘못된
분류와 그것을 고정하는 테스트뿐이다.

### V2.1-H01 [High] v2.0 보고서의 모듈 분류가 틀렸다

- **원인**: v2.0의 import closure 계산이 두 가지를 빠뜨렸다.
  1. **상대 import** — `analyzers/smc/__init__.py`의 `from .smc_signals import ...`
  2. **패키지 `__init__` 전파** — `trading/__init__.py`가 하위 12개를 eager import
- **결과**: 실제로 로드되는 모듈을 "휴면"으로 오판했다.

| 모듈 | v2.0 판정 | **v2.1 정정** |
|---|---|---|
| `analyzers.smc.smc_signals` | NOT-LOADED | 🟢 **ACTIVE** (SMC 진입 신호 본체) |
| `trading.order_executor` | NOT-LOADED | 🟠 **LOADED-ONLY** |
| `trading.position_tracker` | NOT-LOADED | 🟠 **LOADED-ONLY** |
| `trading.trend_exit_engine` | NOT-LOADED | 🟠 **LOADED-ONLY** |
| 전체 집계 | 418개 중 286 미도달 | **501개 중 330 미로드** |

- **영향**: 감사 보고서를 신뢰해 "이 파일은 안 쓰이니 지워도 된다"고 판단했다면
  **실사용 모듈(SMC)을 건드릴 뻔했다.** 실거래에 직접 영향은 없었으나
  감사 산출물의 신뢰도 문제로 High로 분류한다.
- **발견 경위**: v2.1에서 작성한 회귀 테스트가 실패하며 잡아냈다(테스트를 맞추려
  기준을 바꾸지 않고 원인을 추적한 결과).
- **조치**: `RUNTIME_EXECUTION_MAP.md`에 정정 사항과 집계 방법 주의사항 명시,
  회귀 테스트로 고정.

---

## 2. Strategy Reachability 결과

판정 근거는 **실행 로그 + 코드 존재 + 설정값** 3가지다. 추측으로 판정한 항목은 없다.

| 대상 | 판정 | 근거 |
|---|---|---|
| **CHoCH 탐지** | 🟢 **Reachable** | `smc_decision_*.log`에 `[CHOCH]` **548회** (TEST 더미 제외) |
| **Liquidity Sweep** | 🟢 **Reachable** | `[SWEEP]` **382회** |
| **NO_SIG 경로** | 🟢 **Reachable** | `[NO_SIG]` **8,954회** — 지배적 경로 |
| **Entry Prefilter** | 🟢 **Reachable** | `[PREFILTER_BLOCK]` 24회 |
| **SMC Reject** | 🟢 **Reachable** | `[REJECT]` 14회 |
| **Displacement 필터** | 🟢 **Reachable** | `[DISP_BLOCK]` 124회 / 13일 |
| **Trend Breakout** | 🟢 **Reachable** | `[TREND_SIG]` 96회 / 14일 |
| **FVG 플래그** | 🔴 **ALWAYS FALSE** | `main_auto_trading.py:6377`이 `details.get('fvg')`를 읽지만 **`analyzers/smc/` 전체에 fvg 구현 0건** → 키가 설정되는 곳이 없어 항상 `None`→`False` |
| **`FVG_MISSING` reason code** | 🔴 **Unreachable** | 위 결과로 이 코드는 발행될 수 없다 (`services/decision_service.py`에 정의만 존재) |
| **RAE 진입** | ⚫ **Config-disabled** | `rae.enabled = False`. 코드는 존재(`analyzers/smc/rae_detector.py`, LOADED)하나 실행 안 됨 |
| **C_GRADE_FALLBACK** | ⚪ **Runtime Unknown** | 구현 존재(`main:7316`), 30일간 로그 0건. CHoCH가 548회 발화하므로 경로 자체는 살아있고 조건만 미충족으로 보이나 **단정할 근거 없음** |
| **Cup&Handle** | ⚪ **Runtime Unknown** | `analyzers/patterns/cup_handle.py` LOADED. 패턴 레이어는 `score_enabled=False`라 로그 전용 |
| **BOS** | ⚪ **Runtime Unknown** | `analyzers/smc/smc_structure.py`에 구현 존재, 전용 로그 태그가 없어 실행 여부 판별 불가 |
| **Order Block** | ⚪ **Runtime Unknown** | 구현 9곳 존재. `[C_GRADE_FALLBACK]` 로그에 "CHoCH+OB" 문구가 있으나 해당 로그 0건 |

> **Runtime Unknown이 많은 이유**: 이 항목들은 전용 로그 태그가 없어 정적 분석만으로는
> 실행 여부를 확정할 수 없다. 확정하려면 해당 분기에 로그를 추가해야 하는데,
> 그것은 이번 감사의 "변경 금지" 범위와 충돌하므로 FOLLOWUP으로 넘긴다.

---

## 3. Runtime Execution Map

→ **`docs/RUNTIME_EXECUTION_MAP.md`** (본 감사의 주 산출물)

포함 내용:
- 매수 파이프라인 13단계 / 청산 파이프라인 8단계의 실행 파일·함수·호출 위치·상태
- 기능별 실사용 vs 휴면 대조표 (9개 기능군)
- cron 실행 흐름 타임라인
- 실행 중 프로세스 실측치
- **수정 전 30초 체크리스트** (운영자가 잘못된 파일을 고치는 것을 막는 3개 명령어)

---

## 4. Dead Module 분류

**전체 501개 모듈**

| 분류 | 개수 | 설명 |
|---|---|---|
| 🟢 ACTIVE (RUNTIME/PATH) | **171** | import closure에 포함 = 실제 로드됨 |
| 🟠 LOADED-ONLY | 171 중 4건 확인 | `trading.order_executor`, `trading.position_tracker`, `trading.trend_exit_engine`, `brokers.kiwoom_broker` — 로드되나 인스턴스화 없음 |
| ⚪ NOT-LOADED | **330** | import조차 안 됨 |

**NOT-LOADED 330개의 성격** (전량이 "삭제 대상"은 아님):
- 상당수는 **수동 실행용 분석 스크립트** (예: `analysis/trading_health_audit.py`) — 정상
- 일부는 **중복 구현** — 혼동 위험 (아래)
- 일부는 **archive/backup 성격**

**혼동 위험이 큰 중복 구현** (RUNTIME_EXECUTION_MAP §3 전문 참조)

| 기능 | 실사용 | 휴면 개수 |
|---|---|---|
| 주문 실행 | `kiwoom_api.py` | 3 |
| 손절/청산 | `trading/exit_logic_optimized.py` | 6 (동명 파일 2개 포함) |
| 포지션 관리 | `main_auto_trading.self.positions` | 3 |
| 스케줄링 | cron | 1 |

**삭제하지 않았다** (지시서 준수). 대신 회귀 테스트로 "휴면 상태 유지"를 고정했다.

---

## 5. Memory 결과 — ⚠️ 측정 진행 중 (미완)

정적 분석이 아닌 **실측**을 위해 5분 간격 샘플러를 가동했다.
- 스크립트: 백그라운드 실행, 15:35 종료
- 출력: `logs/profiling/memprof_20260728.csv`
- 수집 항목: RSS / VSZ / Threads / FD / DB Connections / CPU / uptime

**현재까지 3개 샘플 (기동 후 13분)**

| 시각 | RSS | Threads | FD | DB conn |
|---|---|---|---|---|
| 10:00:50 | 362 MB | 11 | 13 | 2 |
| 10:05:50 | 366 MB | 11 | 13 | 2 |
| 10:10:50 | 369 MB | 11 | **24** | 3 |

**현 시점 판단: 누수 여부 판정 불가.**
- RSS +7MB/13분(≈32MB/h)이나, 기동 직후 캐시 워밍 구간이라 **정상 범위일 가능성이 높다**
- FD 13→24 증가는 관찰 필요 (WebSocket 재연결/DB 커넥션 풀 확장 가능성)
- Threads 11 고정 — `main_auto_trading.py`의 `threading` 직접 사용은 **0건**이므로
  전부 라이브러리(requests/websockets/psycopg2) 소유

**판정 시점**: 15:35 샘플 수집 완료 후. 분석 명령:
```bash
python3 - <<'EOF'
import csv
rows=[r for r in csv.DictReader(open('logs/profiling/memprof_20260728.csv')) if r['rss_kb']]
f,l=rows[0],rows[-1]; mins=(int(l['uptime_s'])-int(f['uptime_s']))/60
print(f"{f['ts']}→{l['ts']} ({mins:.0f}분)  RSS {int(f['rss_kb'])/1024:.0f}→{int(l['rss_kb'])/1024:.0f}MB")
print(f"증가율 {(int(l['rss_kb'])-int(f['rss_kb']))/1024/max(mins,1)*60:+.1f} MB/시간")
print(f"FD {f['fds']}→{l['fds']}  Threads {f['threads']}→{l['threads']}  DB {f['db_conn']}→{l['db_conn']}")
EOF
```
**누수 판정 기준(제안)**: 장 후반(13:00~15:30)에도 시간당 20MB 이상 단조 증가가 계속되면 누수 의심.

---

## 6. Scheduler 결과

전체 실행 흐름은 `RUNTIME_EXECUTION_MAP.md §4` 타임라인 참조.

| 확인 항목 | 결과 |
|---|---|
| 중복 실행 | `gate_health_check` **하루 2회** (15:58 크론 + 16:05 `operations_daily_summary` 내부 호출) |
| 시간 충돌 | **16:05에 3개 동시** — 셋 다 DB 읽기 전용이라 **무해** |
| watchdog 경쟁 | **없음.** 하트비트가 신선하면 재시작하지 않음(08:45/09:00/09:15 모두 "정상 동작 중" 확인) |
| Dead Job | 없음 — 등록된 크론 22개 전부 대응 스크립트 존재 |
| 미등록 Job | `core/scheduler.py`가 구현되어 있으나 미사용(cron이 대체) |
| 실행 순서 | 정상 — `daily_scan`(08:30) → watchdog(08:45) → 장중 → EOD 분석(15:35~16:12) |

**중요한 운영 사실**: watchdog 크론은 08:45/09:00/09:15 **3회뿐**이다.
→ **09:15 이후 코드를 수정해도 당일 자동 반영되지 않는다.** 반영하려면 수동 재시작이 필요하다.

---

## 7. Regression

```
BEFORE : PASS 415 / FAIL 16
AFTER  : PASS 440 / FAIL 16
NEW FAIL : 0
신규 테스트 : 25건 (전부 PASS)
```

**Pre-existing Failure 16건** — `test_swing_holding_manager.py` 8 + `test_swing_runner_logic.py` 8.
`holding_mgr.evaluate()` 반환값 언패킹 불일치. 감사 이전부터 존재.

**신규 테스트 구성**
| 대상 | 건수 | 내용 |
|---|---|---|
| ACTIVE 모듈 이탈 감지 | 11 | 실사용 11개 모듈이 실행 경로에서 빠지면 실패 |
| NOT-LOADED 활성화 감지 | 7 | 휴면 모듈이 실행 경로로 편입되면 실패 |
| LOADED-ONLY 인스턴스화 감지 | 4 | `TrendExitEngine()` 등이 생성되기 시작하면 실패 |
| 주문 경로 단일성 | 1 | `BrokerType.KIWOOM` 사용 시작 시 실패 |
| Reachability 고정 | 2 | FVG 구현 추가 시 실패 / SMC 런타임 증거 확인 |

이 테스트들은 **전략 로직을 검증하지 않는다.** 오직 "실행 경로 구조"만 고정한다.

---

## 8. 운영 영향

| 항목 | 판정 |
|---|---|
| **매매 판단 변경** | **NO** — 실거래 코드 무변경 |
| **전략 변경** | **NO** |
| **운영 안정성** | **동일** — 이번엔 버그 수정이 없었다 |
| **Runtime 가시성** | **개선** — 실행 경로가 문서+테스트로 고정됨. 운영자가 휴면 파일을 수정하는 위험 감소 |

---

## 9. 완료 조건 체크

- [x] Strategy Reachability 완료 (로그 근거 기반, Runtime Unknown 항목 명시)
- [x] Runtime Execution Map 작성 (`docs/RUNTIME_EXECUTION_MAP.md`)
- [x] Active/Legacy 분류 완료 (501개, 4단계 분류)
- [~] **Memory Profiling — 측정 중, 15:35 완료 예정** (§5)
- [x] Scheduler Audit 완료
- [x] Import Graph 검증 완료 (**오류 발견·정정**)
- [x] Regression Test 추가 (25건)
- [x] pytest 전체 실행 (440 PASS / NEW FAIL 0)
- [x] PRODUCTION_AUDIT_V2_1_REPORT.md 작성
- [x] RUNTIME_EXECUTION_MAP.md 작성
