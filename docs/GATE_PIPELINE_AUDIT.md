# Gate Pipeline 감사

> 생성일: 2026-07-27 | 운영 안정성 감사 v3.0 — Audit 2
> 조사 방법: `check_entry_signal()` → `execute_buy()` 전체 흐름을 코드 추적. 결론만이 아니라 각 게이트가 `research.decision_ledger`에 실제로 기록되는지 여부를 전수 확인했다.

## 전체 흐름도

```
check_entry_signal(stock_code, kiwoom_df)
│
├─① _check_global_risk_gates()                                    ✅ DB-logged (GLOBAL_GATE_BLOCKED/MARKET_SENSOR_BLOCKED)
│     [TIME_PHYSICAL]                                              — 의도적 DB 제외(물리적시간, 노이즈 방지 목적)
│     [KILL_SWITCH]/[ORPHAN_HALT]/[DAILY_LOSS_LIMIT]/[MAX_TRADES]/
│     [DD_HALT]/[DD_HALT_EXCEPTION]/[EC_HALT]/[EC_HALT_EXCEPTION]   ✅ feature_snapshot.gate_reason에 하위태그 보존
│     [MS_BLOCK]                                                   ✅ MARKET_SENSOR_BLOCKED
│
├─② state_manager.can_enter() (STOCK_GATE)                         ✅ DB-logged
│
├─[Gate 1] EARLY_WINDOW_BLOCK (09:00-09:30)                        ❌ 로그 전용, DB 미기록
├─[Gate 2] Market Regime Gate                                      ✅ DB-logged (오늘 regime 원시지표까지 보강 완료)
├─[Gate 3] SMC afternoon cutoff                                    ❌ 로그 전용, DB 미기록 (EARLY_WINDOW와 동일 유형)
│
├─ DATA_INSUFFICIENT (데이터 부족)                                  ✅ DB-logged
│
├─ entry_mode 분기 (현재 config: entry_mode="smc")
│   ├─ squeeze_only / squeeze_with_orderbook / ma_cross / squeeze_2tf  ❌ Decision Ledger 완전 미연결 (현재 비활성, YAML 한 줄로 활성화 가능한 살아있는 코드)
│   ├─ "smc" ★ 활성 경로                                            ✅ DB-logged (SMC_NO_SIG 계열)
│   ├─ legacy_only / hybrid (Signal Orchestrator 경유)               ❌ Decision Ledger 미연결 (현재 비활성)
│
└─ execute_buy()
      ├─ 조기 return 약 25곳(TIME_WEIGHT/BAN_LIST/TRADE_CD/EQ-1~6/
      │   DB_HARD_STOP/COOLDOWN×2/CAN_ENTER_BLOCK/ML_BLOCK/EQ_BLOCK 등)  ❌❌ **최대 사각지대** — candidates row는 존재하나 decision_ledger에 PASS/REJECT 어느쪽도 안 남는 "고아 레코드"
      ├─ Kiwoom 주문 API 실패/예외                                    ❌ 실제 주문시도가 있었는데 흔적 없음
      └─ 주문 성공 → record_acceptance()                              ✅ DB-logged (PASS)
```

## 핵심 발견 1 — `execute_buy()` 조기 return이 Decision Ledger를 고아로 만든다

`services/decision_service.py`의 `begin_evaluation()`은 **즉시** `research.candidates`에 행을 만든다. SMC가 PASS하면 그 컨텍스트가 `self._pending_decision_ctx[stock_code]`에 저장됐다가 `execute_buy()` 시작 시 `.pop()`으로 한 번만 꺼내 쓴다. 이 시점부터 `record_acceptance()`(주문 성공 후) 사이에 있는 **약 25개 조기 return** 중 하나라도 걸리면:

- `research.candidates` 행은 이미 존재
- `research.decision_ledger` 행은 **영원히 생기지 않음** (PASS도 REJECT도 아님)

대부분 `logger.debug`/`logger.info`만 남기거나, 별도 테이블(`insert_blocked_trade`, `_record_blocked_entry`, `signal_rejections`)에 흔적을 남긴다 — decision_ledger 하나만 보고 "왜 거래가 없었는지"를 재구성하려는 사람에게는 이 25곳 전부 사각지대다.

**왜 지금 고치지 않는가**: 각 지점에 `record_rejection(_ds_pass_ctx, '<TAG>', ...)`을 추가하는 것 자체는 기계적이지만, `execute_buy()`는 이 시스템에서 **가장 민감한 실주문 실행 함수**이고 25곳을 건드리는 건 diff 크기와 검증 부담이 크다. 이번 감사에서는 문제를 확정하는 데 집중하고, 실제 수정은 별도 승인 하에 진행하는 것으로 사용자와 합의했다.

## 핵심 발견 2 — Signal Orchestrator는 별도 세계

- `analyzers/signal_orchestrator.py`의 L0-L6 게이트(`evaluate_signal()`)는 `public.signal_events` 테이블에 기록한다 — `research.decision_ledger`와 스키마도 키도 다르다.
- 게다가 **현재 활성 경로(`entry_mode: smc`)에서는 `evaluate_signal()` 자체가 호출되지 않는다.** SMC 경로에서 Orchestrator는 `set_smc_hints()`(스코어링 힌트 제공)와 `recent_accepts`(진단모드 워치리스트 강제편입) 용도로만 쓰인다 — 실제 게이트 역할을 안 한다.
- 결론: 지금 시스템에서 "왜 거래가 없었는가"의 유일한 신뢰 가능한 소스는 `research.decision_ledger` + `logs/auto_trading_*.log`뿐이고, Signal Orchestrator 로그(`logs/signal_orchestrator.log`)는 후보발굴 통계용으로는 유용하지만 게이트 판단 근거로는 이미 SMC 경로에서 배제되어 있다.

## 핵심 발견 3 — GLOBAL_GATE는 문제 없음 (대조군)

`_check_global_risk_gates()`의 모든 `return False` 경로는 `[TAG]` 프리픽스가 있고, 전부 `feature_snapshot.gate_reason`에 원문 그대로 보존된다(`decision_reason_code`는 `GLOBAL_GATE_BLOCKED` 하나로 뭉치지만 JSONB 안에 원인이 살아있음). **이 부분은 설계대로 정상 동작 — 위 두 발견과 대조되는 "잘 된 사례".**

## 요약

| 게이트 | DB 기록 | 심각도 |
|---|---|---|
| GLOBAL_GATE 전체 | ✅ | - |
| STOCK_GATE | ✅ | - |
| Regime Gate | ✅(오늘 보강) | - |
| SMC 활성경로 | ✅ | - |
| EARLY_WINDOW_BLOCK | ❌ | 낮음(로그로 대체 가능) |
| SMC afternoon cutoff | ❌ | 낮음(로그로 대체 가능) |
| 대체 전략경로 4개+Orchestrator | ❌ | 중간(현재 비활성이라 당장 영향 없음, 활성화 시 즉시 사각지대) |
| **execute_buy() 조기 return 25곳** | ❌ | **높음(실제 SMC PASS 후보의 최종 운명을 알 수 없음)** |
| Kiwoom 주문 실패/예외 | ❌ | 중간(실제 주문시도 이력 소실) |

**권고**: `execute_buy()` 개선은 별도 작업지시서로 분리해 진행할 것을 권고한다(범위가 크고 검증 부담이 높아 이번 감사 승인 범위 밖).
