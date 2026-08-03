# ACCOUNT_EQUITY_AUDIT

Iteration 7-2 · 2026-08-03 · 조회 전용 · 운영 무변경

## 판정: **PASS**

True Equity 계산식을 확정할 수 있는 근거를 확보했다.
→ Iteration 7-3 (Equity 계산 수정) 진행 가능.

단, 후보 C 를 최종 고정하기 전에 **미결제 잔액이 있는 날 조회 1회**가 필요하다 (§5).

## 1. KPI

| 항목 | 목표 | 결과 |
|---|---|---|
| API 응답 분석 | 100% | 3 TR / 104 필드 덤프 (kt00001·kt00004·ka01690) |
| Equity 구성 필드 식별 | 100% | 금액 필드 74개 분류 |
| D+2 관련 필드 확인 | 100% | **존재** — `d2_entra` 외 6개 |
| 현재 계산식 검증 | PASS | 미결제 없는 날 브로커 값과 **오차 0원** |
| True Equity 후보 정의 | PASS | 8개 후보 (A/B/C/C'/D/E/F/F') |
| Runtime Error | 0 | 0 |
| Regression | PASS | 495 PASS / 16 Known FAIL |

## 2. 현재 계산식 검증 (Phase 3)

```
운영:  total_assets = pymn_alow_amt + positions_value      (main:2410, 2825)
실측:  1,857,799 + 1,827,000 = 3,684,799
브로커: kt00004.aset_evlt_amt = 3,684,799                   차이 0원
```

**미결제 잔액이 없는 날에는 현재 계산식이 정확하다.**
운영 로그·`account_snapshot`(오늘 total 3,809,899)과의 차이는
스냅샷이 08:50 전일종가 기준(180주 × 10,845)이고 실측은 현재가(10,150)이기 때문이다.

## 3. D+2 결제 분석 (Phase 4)

`account_snapshot` 150건 + `trades` 122건 대조 → 금액·부호가 모두 맞는 **확정 6건**.

**매도·매수 모두 체결 당일에는 `pymn_alow_amt` 에 반영되지 않는다.**

| 사건 | 현재 계산식 | 실제 | 오차 |
|---|---|---|---|
| 2026-05-22 매도 4,526,860 직후 | 542,250 | ≈5,060,540 | **-4.52M 과소** |
| 2026-06-24 매수 2,121,000 미결제 | 6,628,719 | ≈4,543,409 | **+2.09M 과대** |

Iteration 7-1 에서 "확인 불가" 로 남겼던 **6,628,719 의 원인이 확정됐다**:
매수대금 미차감 + 매수종목 평가액 가산 = 이중계상.
그리고 그 값이 그날 신고점(peak)이 됐다.

상세: `D2_SETTLEMENT_ANALYSIS.md`

## 4. True Equity 후보와 권고 (Phase 5·6)

오늘은 미결제가 0 이라 **후보 8개가 전부 같은 값**이다. 동률이지 판별이 아니다.
결제 사건 실측을 근거로 권고한다.

**권고: `True Equity = d2_entra(D+2 추정예수금) + 주식평가액`**

이유: 매도 미수령·매수 미차감을 **한 필드로 양방향 동시 해결**하고,
결제일(T+N)을 코드가 직접 계산하지 않는다.

- B(`entr`) / F(`aset_evlt_amt`) — 매수 미결제 과대계상이 남을 수 있음
- D(당일매도체결금 가산) — 매도측만 보정, T+N 변경에 취약
- E(`d1_entra`) — 결제 1일분만 반영

상세: `TRUE_EQUITY_ANALYSIS.md`

## 5. 확정 전 필요한 측정 1회 (미실시)

오늘 `entr == d2_entra` 라 아래는 **확인 불가**다.
다음 체결이 있는 날 `python3 -m phase1.account_equity_audit` 재실행이면 끝난다 (조회 전용).

- `d2_entra` 가 매도 미결제분을 포함하는가 / 매수 미결제분을 차감하는가
- `aset_evlt_amt` 가 `entr` 기반인가 `d2_entra` 기반인가
- C(`d2_entra`) vs C'(`d2_pymn_alow_amt`) 중 무엇인가

## 6. 수정 영향도 (Phase 7 — 분석만, 수정하지 않음)

`self.total_assets` 를 읽는 지점 전수.

| 모듈 | 사용처 | 영향 |
|---|---|---|
| `trading/equity_controller.py` | `can_enter` / `update_peak` / `update_peak_eod` | **직접 — EC_HALT, peak** |
| `main_auto_trading.py:5558` | 진입 게이트 EC_HALT | 직접 |
| `main_auto_trading.py:9804` | `get_drawdown_mult` → 포지션 사이즈 배수 | **직접 — 사이징** |
| `main_auto_trading.py:10229/10265~10310` | 포트폴리오·섹터 익스포저 한도 | 직접 |
| `main_auto_trading.py:12113` | `account_value` (일일 정산) | 직접 |
| `core/risk_manager.py` (9곳) | 손실 한도·포지션 한도 | 직접 |
| `core/drawdown_engine.py` | DD 레벨 (NORMAL/CAUTION/DANGER/HALT) | 직접 |
| `main_auto_trading.py:2431/2872` | `account_snapshot` INSERT | 기록값 변경 |
| `main_auto_trading.py:1089/1102` | `daily_capital_snapshot` | 기록값 변경 |
| `trading/account_manager.py` (9곳) | 계좌 상태 | 간접 |
| `api_server.py` (17곳) | 대시보드 | 표시값 변경 |
| `core/menu_handlers.py` (3곳) | CLI 메뉴 | 표시값 변경 |
| `data/equity_state.json` | peak 영속 | **재산출 필요** |
| `data/risk_log.json` | `initial_balance` | 검토 필요 |

⚠️ 재시작 시 `equity_state.json` 의 peak 은 **옛 정의로 계산된 값**이다.
계산식을 바꾸면 peak 도 같은 정의로 재산출해야 하며,
그러지 않으면 정의가 다른 두 수로 dd 를 계산하게 된다.

⚠️ `get_drawdown_mult` 가 포지션 사이즈에 직접 들어가므로
이 변경은 CLAUDE.md 기준 **고위험**이다. 장 마감 후 반영 + dry-run 확인이 필요하다.

## 7. 부수 발견 (이번 범위 밖, 수정하지 않음)

1. `kiwoom_api.get_account_evaluation()` (kt00004) 은 `qry_tp` 를 안 보내
   항상 `rc=2 (필수입력 파라미터=qry_tp)` 로 실패한다. 호출부가 있는지는 미조사.
2. 감사 스크립트 초판의 마스킹이 4자 이하 값(한글 이름)을 그대로 통과시켰다.
   발견 즉시 수정하고 산출물을 재생성했다. 현재 산출물에 실명 없음.

## 8. 출력

```
==============================
ACCOUNT EQUITY AUDIT
==============================
API Fields             74
Current Formula        출금가능금 + 평가금액 = 3,684,799
True Equity Candidates 6
Best Candidate         A  출금가능금(pymn_alow_amt) + 평가금액
Difference             +0
D+2 Analysis           D+2필드 7개 / 실측사건 122건
Regression             495 PASS / 16 Known FAIL
Runtime Error          0
==============================
```

⚠️ `Best Candidate` 는 **오늘 값이 전부 동률이라 첫 후보가 뽑힌 것**이다.
실제 권고는 §4 의 Case C 다. 스크립트 출력을 그대로 결론으로 읽으면 안 된다.
