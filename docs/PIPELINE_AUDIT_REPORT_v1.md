# AUTOMATED TRADING PIPELINE AUDIT REPORT

> Production Reliability Audit v1.0 | 2026-07-28 | 읽기 전용 감사
> 수행 시각: 08:43~08:55 KST (장 시작 전). **코드 변경 없음 — 발견 및 재현까지만 수행.**
> 회귀 테스트: `tests/unit/test_bug_audit_v1_20260728.py`

---

## 0. 감사 범위에 대한 정직한 고지

지시서는 Phase 1~10 전수 감사를 요구했으나, 이번 세션에서 **실제로 근거를 확보한 범위**는
사용자가 지정한 우선순위(① 데이터 전달 ② Gate 차단 ③ DB Logging ④ Risk ⑤ Execution
⑥ Strategy Logic ⑦ Code Quality) 중 **①~⑤ 및 ⑦의 일부**다.

- **완료**: 데이터 전달(Score Engine 전 경로), Gate 숫자 정합성, DB Logging 정합성,
  Risk 상태 영속성, Trailing Stop 단조성, Config 키 정합성(기계적 전수), 지정 회귀 대상 8건
- **미완**: Phase 4 전략 로직 조건문 전수(SMC/CHoCH/BOS/Sweep/OB/Cup&Handle 도달불가 코드
  분석), Phase 10 메모리 누수/API 재연결 시나리오 검증
- 미완 항목은 §6에 후속 과제로 명시했다. "검사했으나 이상 없음"과 "검사하지 않음"을
  섞지 않기 위해 구분해 기록한다.

---

## 1. 발견 요약

| 등급 | 개수 | 항목 |
|---|---|---|
| **Critical** | 0 | — |
| **High** | 2 | BUG-01 Entry Quality 설정 무력화, BUG-03 게이트 알람 Silent Failure |
| **Medium** | 2 | BUG-02 Trailing Stop 단조성 위반, BUG-04 Gate Funnel 숫자 정합성 붕괴 |
| **Low** | 2 | BUG-05 `[REGIME_BLOCK]` 태그 재사용, BUG-06 ScoreEngine 로그 중복 |
| **Info** | 1 | ScoreEngine 선별 기능의 구조적 무력화 (버그 아님, 설계 결과) |

### 지정 회귀 대상 검증 결과

| 회귀 대상 | 결과 | 근거 |
|---|---|---|
| Score Engine OHLCV None 전달 | ✅ 수정 확인 | `[SCORE_INPUT] ohlcv_loaded=N missing=0` 7일 연속 |
| EC_HALT peak equity 오류 | ✅ 정상 | `_account_data_reliable` 가드 2개 호출부 + `_do_update_peak` 단조증가 + EOD 게이트 |
| Regime Block DB logging 누락 | ⚠️ 부분 | route=ALL 기록됨 / route=RAE 미기록 → BUG-05 |
| Trailing Stop monotonic 보장 | ❌ 위반 | exit_logic은 보장, 부분청산 경로 미보장 → BUG-02 |
| daily reset 재시작 처리 | ✅ 수정 확인 | `utils/daily_reset_marker.py`, `[DAILY_RESET_SKIPPED]` 로그 |
| Drawdown state persistence | ✅ 수정 확인 | `data/drawdown_state.json` 영속화 |
| E2 Evidence Level 조건 | ⏸️ 판정 불가 | research 테이블 TRUNCATE로 근거 데이터 소실 (별건) |
| RISK_OFF Gate 정상 작동 | ✅ 정상 | 07-27 REGIME_BLOCK 92건 정상 차단, size_mult=0.0 |

---

## 2. Issues

### BUG-01 [HIGH] Entry Quality 설정이 YAML을 무시하고 하드코딩 기본값으로 동작

**위치**: `main_auto_trading.py:9305, 9306, 9331, 9358, 9359` (`execute_buy()` 내)

**문제**:
```python
eq_config = self.config.get('entry_quality', {})     # → 일반 dict 반환
rvol_enabled   = eq_config.get('rvol_filter.enabled', True)      # 점표기 키
rvol_threshold = eq_config.get('rvol_filter.threshold', 1.7)
ema9_enabled   = eq_config.get('ema9_pullback.enabled', True)
vwap_enabled   = eq_config.get('vwap_distance.enabled', True)
vwap_max       = eq_config.get('vwap_distance.max_pct', 1.8)
```
`ConfigLoader.get()`은 점 경로를 지원하지만(`utils/config_loader.py:44` `key.split('.')`),
그 **반환값은 일반 `dict`**다. 일반 dict의 `.get('a.b')`는 `"a.b"`라는 리터럴 키를 찾으므로
중첩 구조에서는 **항상 miss → 하드코딩 기본값 폴백**한다.

YAML은 중첩 구조다:
```yaml
entry_quality:
  rvol_filter: {enabled: true, threshold: 1.7}
  ema9_pullback: {enabled: true}
  vwap_distance: {enabled: true, max_pct: 1.8}
```

**영향**:
- EQ-1(RVOL)/EQ-2(EMA9)/EQ-3(VWAP) **3개 진입 품질 게이트의 5개 설정이 YAML과 무관하게 동작**
- **현재 실질 동작 차이는 없다** — YAML 값이 우연히 하드코딩 기본값과 전부 일치하기 때문
- 그러나 CLAUDE.md의 핵심 운영 원칙인 **"코드 수정 < YAML 파라미터 조정"이 이 경로에서 완전히 무효**.
  운영자가 `rvol_filter.threshold`를 1.5로 낮추거나 `ema9_pullback.enabled: false`로 꺼도
  **아무 경고 없이 무시**된다. 튜닝했다고 믿는 상태에서 실제로는 안 바뀐 채 매매가 계속된다.
- 전형적인 Silent Failure — 로그에도 "설정 못 읽음" 흔적이 남지 않는다.

**재현**:
```
python3 -m pytest tests/unit/test_bug_audit_v1_20260728.py::test_bug01_repro_dotted_key_on_plain_dict_always_misses -v
```
직접 확인:
```python
cfg = ConfigLoader('config/strategy_hybrid.yaml')
eq = cfg.get('entry_quality', {})
eq.get('rvol_filter.threshold', 1.7)          # → 1.7 (기본값, YAML 미도달)
cfg.get('entry_quality.rvol_filter.threshold') # → 1.7 (YAML 실제 도달)
```

**수정(제안, 미적용)**: 5줄을 ConfigLoader 전체 경로 조회로 변경.
```python
rvol_enabled   = self.config.get('entry_quality.rvol_filter.enabled', True)
rvol_threshold = self.config.get('entry_quality.rvol_filter.threshold', 1.7)
ema9_enabled   = self.config.get('entry_quality.ema9_pullback.enabled', True)
vwap_enabled   = self.config.get('entry_quality.vwap_distance.enabled', True)
vwap_max       = self.config.get('entry_quality.vwap_distance.max_pct', 1.8)
```
현재 YAML 값 == 기본값이므로 **이 수정만으로는 매매 동작이 바뀌지 않는다**(무동작 변경 수정).

**테스트**: `test_bug01_entry_quality_settings_honor_yaml` (현재 xfail, 수정 시 XPASS)

---

### BUG-02 [MEDIUM] Trailing Stop 단조성(monotonic) 위반 — 부분청산 경로

**위치**: `main_auto_trading.py:12223` (`stage >= 2` 부분청산 블록)

**문제**: `trading/exit_logic_optimized.py:1086-1089`는 단조성을 명시적으로 보장한다.
```python
prev_stop = position.get('trailing_stop_price') or 0
trailing_stop_price = max(prev_stop, calc_stop)      # ✅ 역행 불가
```
그러나 부분청산 경로는 **가드 없이 raw 대입**한다.
```python
position['trailing_stop_price'] = position['highest_price'] * (1 - ratio / 100)   # ❌
```

**영향**:
- ATR 기반으로 이미 높게 래칫된 스탑이 부분청산(TP2) 시 **하향 이동**할 수 있다.
  예: highest=10,000 / ATR스탑=9,960 → 부분청산 후 10,000×0.99 = **9,900 (60원 하락)**
- 다음 exit_logic 평가 사이클에서 `max(prev, calc)`로 **대개 자동 복구**되므로 영향은 제한적이다.
- 다만 그 사이 ATR이 확대되어 calc가 낮아지면 **래칫 수준이 영구적으로 낮아진 채 고정**된다
  (올바른 값 9,960 대신 9,900이 prev로 굳음). 이익 반납 위험.
- 실거래에서 이 시나리오가 실제 발생했다는 증거는 확보하지 못했다(부분청산 이력 부족).

**재현**: `test_bug02_repro_partial_exit_can_lower_trailing_stop` (PASS = 재현 성공)

**수정(제안, 미적용)**:
```python
_new_stop = position['highest_price'] * (1 - ratio / 100)
position['trailing_stop_price'] = max(position.get('trailing_stop_price') or 0, _new_stop)
```
스탑을 **느슨하게 만들지 않는 방향**의 수정이므로 리스크 정책 완화가 아니다.

**테스트**: `test_bug02_trailing_stop_is_monotonic_across_partial_exit` (현재 xfail)

---

### BUG-03 [HIGH] 게이트 헬스체크 알람이 candidates=0일 때 발화 불가 (Silent Failure)

**위치**: `analysis/gate_health_check.py:168, 172` (`build_report()`)

**문제**:
```python
dead_gate_immediate = candidates > 0 and regime_evaluated == 0
entry_immediate     = candidates > 0 and entry_signal_checked == 0
ec_halt_immediate   = candidates >= 100 and ec_halt_today == candidates
```
`candidates`는 **DB(`research.candidates`)** 에서 온다. 이 테이블이 비면
(TRUNCATE 사고, research layer 미기록 등) `candidates=0` → **세 알람 모두 구조적으로 False**.

**영향**:
- **"거래가 왜 안 되는가"를 감시해야 할 알람이, 정확히 그 상황에서 침묵한다.**
- 실증: 2026-07-27 운영 요약이 `Orders Submitted: 0`인데도 Gate Health `NORMAL` 보고.
  같은 날 `Candidate: 0`(TRUNCATE 여파) 때문에 알람 조건이 전부 False였다.
- 5거래일 연속 경고(`full_history`) 역시 `_recent_trading_days()`가
  `SELECT DISTINCT observed_at::date FROM research.candidates`로 이력을 구성하므로
  테이블이 비면 **history_days=[] → 연속 경고도 전부 무력화**된다.
- 결과적으로 26일 무거래가 이 감시 체계에서 한 번도 경보되지 않았다.

**재현**: `test_bug03_repro_alarms_silent_when_candidates_zero` (PASS = 재현 성공)

**수정(제안, 미적용)**: `candidates == 0` 자체를 **독립 경고 조건으로 승격**.
로그상 활동(`regime_evaluated > 0`)이 있는데 DB candidates=0이면
"Research Layer 기록 누락" 경고를 발생시킨다. 판정 로직만 바꾸는 것으로 매매 영향 없음.

**테스트**: `test_bug03_zero_candidates_with_log_activity_should_warn` (현재 xfail)

---

### BUG-04 [MEDIUM] Gate Funnel 숫자 정합성 붕괴 — DB 출처와 로그 출처 혼합

**위치**: `analysis/gate_health_check.py:64-142, 255-280`

**문제**: 하나의 "퍼널" 리포트에 **분모가 다른 두 종류의 카운트**가 섞여 있다.

| 지표 | 출처 | 카운트 단위 |
|---|---|---|
| `candidates` | DB `research.candidates` | candidate 1건 = 1 |
| `passed_global_gate` | DB `research.decision_ledger` | decision 1건 = 1 |
| `orders_submitted` | DB `trades` | 주문 1건 = 1 |
| `regime_evaluated`, `regime_block` | **로그 파일 라인 카운트** | **스캔 사이클마다 종목별로 반복 발생** |

`pct()`는 모든 값을 `candidates`(DB)로 나눈다:
```python
def pct(n): return f"({n / cand * 100:.0f}%)" if cand > 0 else "(-)"
```

**영향**:
- 사용자가 요구한 `Candidate → Pass → Block → Entry` **숫자 일치 검증이 구조적으로 불가능**.
- 실증: 2026-07-13 리포트 `Candidate: 293`, `REGIME_BLOCK: 707` → **차단이 후보보다 많음(241%)**.
  로그 태그는 사이클마다 재발생하므로 당연한 결과지만, 리포트는 이를 퍼널처럼 표시한다.
- 07-21~07-24는 `Candidate == Passed == REGIME_BLOCK`으로 완전히 동일한 숫자가 나오는데,
  이 역시 우연이 아니라 서로 다른 것을 세고 있다는 신호다.

**수정(제안, 미적용)**: 로그 출처 지표와 DB 출처 지표를 **리포트에서 시각적으로 분리**하고,
로그 기반 값에는 `pct()`를 적용하지 않는다. (표시 로직만 변경, 매매 영향 없음)

---

### BUG-05 [LOW] `[REGIME_BLOCK]` 로그 태그가 의미가 다른 두 이벤트에 재사용됨

**위치**: `main_auto_trading.py:5545` (종결 차단) vs `main_auto_trading.py:5792` (RAE 서브경로 스킵)

**문제**:
- `5545` route=ALL: 후보 평가를 **종료**시키는 차단. Decision Ledger에 REJECT 기록됨 ✅
- `5792` route=RAE: RAE 재진입 분기만 건너뛰는 **비종결** 이벤트. Ledger 미기록.
  그런데 동일한 `[REGIME_BLOCK]` 태그를 사용한다.

`_log_tag_counts()`는 태그 문자열만 보고 세므로, 비종결 이벤트가 종결 차단 카운트에 합산되어
BUG-04의 정합성 붕괴를 가중시킨다.

**수정(제안, 미적용)**: RAE 경로 태그를 `[RAE_REGIME_SKIP]` 등으로 분리.
(route=RAE에 Decision Ledger REJECT를 추가하는 것은 **권장하지 않는다** — 비종결 이벤트를
종결 결정으로 기록하면 이중 계상이 된다.)

---

### BUG-06 [LOW] ScoreEngine 요약 로그 3줄 중복 출력

**위치**: `trading/score_engine.py:229` (`log_summary()`)

**문제**: `log_summary()` 내부에서 `self.select(ranked)`를 재호출하는데, `select()`가
`logger.info` 3줄을 출력한다. 호출부(`main_auto_trading.py:13641`)에서 이미 `select()`를
호출했으므로 매일 동일 로그가 2회씩 남는다.

**실증**: `[SCORE_ENGINE] raw=7  score>=2: 0  selected: 0` 가 모든 날짜에 2회 연속 출력됨.

**수정(제안, 미적용)**: `log_summary()`가 `select()` 결과를 인자로 받거나, 길이만 계산.

---

### INFO ScoreEngine 선별 기능의 구조적 무력화 (버그 아님 — 설계 결과)

**관찰**: 최근 7거래일 연속 `score>=2: 0  selected: 0`.

**분석** (실데이터 검증 완료):
- `smc` (+2): `daily_scan`은 **정상 실행 중**(오늘 08:30 로그 확인)이나 "[오늘 BUY 신호 없음]"
  → `load_daily_watchlist()`가 빈 리스트 반환(`scan_date != today` 체크는 **정상 동작**,
  스테일 데이터 버그 없음) → 항상 0
- `ma50` (+1): 실데이터 검증 결과 **정확한 계산**. 삼성전자 254,000원 vs MA50 303,170원,
  SK하이닉스 1,816,000 vs 2,192,100, 현대차 403,000 vs 553,990 — 전 종목이 실제로 MA50 하회.
  일봉 정렬도 정상(`first=20240206 → last=20260728` 오름차순, 07-15 역순 핫픽스 적용 확인).
- `volume` (+1): 간헐적으로만 1

→ 하락장에서 획득 가능 최대 점수 = 1 < `MIN_SCORE=2`. **선별이 구조적으로 항상 0건**이며,
`main_auto_trading.py:13657-13662`의 폴백(기존 watchlist 유지)에만 의존한다.

**판정**: 코드 버그 아님. 다만 ScoreEngine의 "상위 5종목 선별" 기능이 현 장세에서 완전히
비활성이며, 이 사실이 어디에도 경고되지 않는다. **정책 판단이 필요한 사항이므로 수정 제안하지 않음.**

---

## 3. 수정 파일 목록

**이번 감사에서 운영 코드는 한 줄도 변경하지 않았다.** 신규 추가 파일만 존재한다.

| file | change | reason |
|---|---|---|
| `tests/unit/test_bug_audit_v1_20260728.py` | 신규 (7 테스트) | BUG-01/02/03 재현 + 수정 후 기대동작 명세 |
| `docs/PIPELINE_AUDIT_REPORT_v1.md` | 신규 | 본 보고서 |

**수정 대기(승인 필요)**:

| file | 예정 change | 등급 |
|---|---|---|
| `main_auto_trading.py:9305,9306,9331,9358,9359` | eq_config 점표기 → `self.config.get('entry_quality.<key>')` | HIGH |
| `main_auto_trading.py:12223` | trailing stop `max(prev, new)` 가드 추가 | MEDIUM |
| `analysis/gate_health_check.py:168,172` | candidates=0 독립 경고 승격 | HIGH |
| `analysis/gate_health_check.py:255-280` | 로그출처/DB출처 분리 표기 | MEDIUM |
| `main_auto_trading.py:5792` | 로그 태그 `[RAE_REGIME_SKIP]`로 분리 | LOW |
| `trading/score_engine.py:229` | `log_summary()` 중복 select 제거 | LOW |

---

## 4. Regression Test 결과

```
tests/unit/test_bug_audit_v1_20260728.py
  PASS   : 4   (BUG-01/02/03 재현 확인)
  XFAIL  : 3   (수정 후 기대동작 — strict=True, 수정 시 XPASS로 알림)
  FAIL   : 0

전체 스위트 (tests/unit/ + tests/simulation/)
  PASS   : 385
  XFAIL  : 3
  FAIL   : 16  ← 전부 감사 이전부터 존재하던 무관 실패
                (swing_runner holding_mgr.evaluate() 시그니처 불일치, git stash로 확인)
  신규 회귀: 0
```

---

## 5. 운영 영향 평가

이번 감사 작업(코드 변경 없음, 테스트/문서만 추가) 기준:

| 항목 | 판정 |
|---|---|
| **매매 판단 변경** | **NO** — 운영 코드 무변경 |
| **위험 감소** | **NO (아직)** — 발견만 했고 수정 미적용 |
| **전략 변경** | **NO** |

**제안된 수정을 전부 적용할 경우의 예상 영향**:

| 항목 | 판정 | 근거 |
|---|---|---|
| 매매 판단 변경 | **NO** | BUG-01은 현재 YAML 값 == 하드코딩 기본값이라 동작 동일. BUG-02는 스탑을 **더 타이트하게** 유지(느슨해지지 않음). 나머지는 로깅/리포트 전용 |
| 위험 감소 | **YES** | ① YAML 튜닝이 실제로 반영되는 상태 복구 ② 트레일링 스탑 이익 반납 경로 차단 ③ 무거래 상황에서 알람이 실제로 울림 |
| 전략 변경 | **NO** | 진입/청산 조건, threshold, score, regime, position sizing 전부 불변 |

---

## 6. 후속 과제 (이번 감사 미완 영역)

1. **Phase 4 전략 로직 조건문 전수 감사** — SMC/CHoCH/BOS/Liquidity Sweep/Order Block/
   Cup&Handle의 도달불가 코드·항상 False 조건 분석. 미수행.
2. **Phase 10 운영 안정성** — 메모리 누수, 객체 누적, Kiwoom API 재연결/중복주문 시나리오. 미수행.
   (`except Exception: pass` 패턴이 핵심 모듈에 **113건** 존재 — 개별 위험도 판정 필요)
3. **E2 Evidence Level 판정** — research 테이블 데이터 소실로 판정 불가.
   `analysis/check_research_integrity.py`로 데이터 재축적 후 재시도.
4. **BUG-02 실거래 발생 여부 확인** — 부분청산(TP2) 이력이 쌓이면 실제 스탑 하향 사례 검증.
