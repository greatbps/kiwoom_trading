# Production Audit v2.1 — Follow-up Backlog

> 작성일: 2026-07-28 | 본 감사: `docs/PRODUCTION_AUDIT_V2_1_REPORT.md`
> 원칙: 별도 승인 없이 수정하지 않는다.
> 이전 이월분: `docs/PIPELINE_AUDIT_FOLLOWUP.md`, `docs/PRODUCTION_AUDIT_V2_FOLLOWUP.md` (전부 유효)

---

## V2.1-FU-01 [Medium] FVG 플래그가 항상 False — 구현 부재

- **위치**: `main_auto_trading.py:6377` `'fvg_detected': bool(details.get('fvg'))`
- **사실**: `analyzers/smc/` 전체에 `fvg`를 설정하는 구현이 **0건**. 따라서 이 키는
  절대 채워지지 않고 `fvg_detected`는 항상 `False`로 Decision Ledger에 기록된다.
- **파생**: `services/decision_service.py`의 `FVG_MISSING` reason code는
  **발행될 수 없다**(unreachable). `research.reason_dictionary`에는 등록되어 있다.
- **영향**: 매매 판단에 영향 없음(이 플래그를 게이트 조건으로 쓰지 않음).
  다만 Decision Ledger 분석 시 "FVG는 항상 없었다"는 잘못된 결론을 유도할 수 있다.
- **선택지**: ① FVG 탐지를 구현한다(전략 변경 → 승인 필요) ②
  `fvg_detected` 기록과 `FVG_MISSING` 코드를 제거해 혼동을 없앤다 ③ 현상 유지 + 문서화
- **회귀 테스트 있음**: `test_fvg_detection_is_not_implemented_so_flag_is_always_false`
  — FVG 구현이 추가되면 실패하며 이 항목 갱신을 알린다.

---

## V2.1-FU-02 [Medium] Reachability "Runtime Unknown" 4건 — 로그 부재로 판정 불가

전용 로그 태그가 없어 실행 여부를 확정할 수 없는 분기들이다.

| 대상 | 구현 위치 | 상태 |
|---|---|---|
| BOS | `analyzers/smc/smc_structure.py` | 구현 존재, 전용 태그 없음 |
| Order Block | `analyzers/smc/` (9곳) | 구현 존재, 전용 태그 없음 |
| C_GRADE_FALLBACK | `main_auto_trading.py:7316` | 태그 있으나 30일간 0건 |
| Cup&Handle | `analyzers/patterns/cup_handle.py` | LOADED, 패턴레이어 `score_enabled=False` |

- **판정 불가 사유**: 정적 분석만으로는 조건 충족 여부를 알 수 없고, 로그가 없어
  런타임 증거도 없다.
- **권장**: 각 분기에 `logger.debug`/`[TAG]` 한 줄씩 추가하면 판정 가능해진다.
  **단 이는 코드 변경이므로 이번 감사 범위 밖**(감사는 변경 금지).
- **주의**: 로그 추가 시에도 진입/청산 조건은 절대 건드리지 말 것.

---

## V2.1-FU-03-B [관찰] 거래 발생일 메모리 재측정 필요 (2026-07-28 추가)

2026-07-28 측정(48샘플)은 **매수 0건인 날**의 값이다. 안정구간 기울기 **+4.2 MB/h**로
STABLE 판정이지만 WATCH 임계(5.0)에 근접한다. 증가율이 시장 활동과 상관관계를 보였으므로
(점심 소강 +0.5MB/30분 vs 오후장 +3.4MB/30분), **실제 진입/청산이 발생하는 날에는 더 높을 수 있다.**

- **조치**: 거래가 발생한 날 `python3 -m analysis.memprof_report`를 다시 돌려
  `docs/OPERATIONAL_BASELINE.md` ⑤항 기준값을 갱신할 것.
- **누수로 판단할 조건**: 안정구간 +20MB/h 이상 지속, 또는 FD/Thread가 회수되지 않고 증가.
- 현재까지 핸들(Threads 11 / FD 15 / socket 5 / DB 3)은 전 구간 고정이라 핸들 누수는 배제됐다.

---

## V2.1-FU-03 [Low→관찰] 메모리/FD 증가 추세

- 기동 후 13분 구간에서 RSS 362→369MB(≈32MB/h), **FD 13→24**, DB conn 2→3
- 기동 직후 캐시 워밍 구간이라 정상일 가능성이 높으나 확정 불가
- **판정 시점**: 15:35 샘플러 종료 후. 분석 명령은 본 보고서 §5 참조
- **누수 판정 기준(제안)**: 13:00~15:30 구간에서도 시간당 20MB 이상 단조 증가 지속 시 누수 의심
- **FD 증가**가 계속되면 WebSocket 재연결 시 소켓 미해제 가능성 조사 필요
- 샘플러는 `logs/profiling/memprof_YYYYMMDD.csv`에 계속 기록된다(15:35 자동 종료)

---

## V2.1-FU-04 [Low] LOADED-ONLY 모듈의 존재가 문서화되어 있지 않았다

`trading/__init__.py`가 하위 12개 모듈을 eager import하기 때문에,
`trading.exit_logic_optimized` 하나만 써도 `order_executor`/`position_tracker`/
`trend_exit_engine`이 전부 메모리에 올라온다.

- **영향**: ① 메모리 사용량(미사용 모듈 로드) ② "import되어 있으니 쓰이는 것"이라는 오해
- **권장**: `trading/__init__.py`의 eager import를 lazy로 바꾸거나 최소화.
  **단 import 부작용에 의존하는 코드가 있을 수 있어 신중한 검증 필요** — 승인 대상.

---

## V2.1-FU-05 [Low] Silent Failure 2차 감사 — 신규 발견 없음

지시서 Phase 6에 따라 이번엔 수정하지 않고 기록만 하기로 했으나,
**v2.0에서 이미 467건을 전수 분류했고 이번 감사에서 신규 패턴은 발견되지 않았다.**

v2.0 미처리분(그대로 유효):
- WARNING 193건 개별 위험도 판정 미완
- `_get_today_market_context_id()` / `_get_today_session_id()`의 에러-부재 혼동
  (`docs/PRODUCTION_AUDIT_V2_FOLLOWUP.md` V2-FU-04)

---

## V2.1-FU-06 [운영 절차] 09:15 이후 코드 변경은 자동 반영되지 않는다

감사 중 확인된 운영상 중요 사실이다.

- watchdog 크론은 **08:45 / 09:00 / 09:15 3회뿐**이고, 하트비트가 신선하면 재시작하지 않는다.
- 따라서 **09:15 이후 파일을 수정해도 당일 실거래에는 반영되지 않는다**
  (이미 메모리에 로드된 코드로 계속 동작).
- 반영하려면 **수동 재시작**이 필요하며, 이는 운영자 승인 사항이다.
- 반대로, 09:15 이후 파일 수정은 **의도치 않게 반영될 위험도 없다**(안전 창).

이 사실을 `docs/RUNTIME_EXECUTION_MAP.md §4`에도 기록했다.
