# Production Audit Fix v1.1 Report

> 작성일: 2026-07-28 | 대상: `docs/PIPELINE_AUDIT_REPORT_v1.md` 발견 버그
> 수행 시각: 09:16~09:25 KST (장중, 단 아래 §5 배포 상태 참고)
> 회귀 테스트: `tests/unit/test_bug_audit_v1_20260728.py` (22건)
> 이월 항목: `docs/PIPELINE_AUDIT_FOLLOWUP.md`

---

## 1. 수정 요약

| BUG | 등급 | 수정 여부 | 영향 |
|---|---|---|---|
| **BUG-01** Entry Quality YAML 설정 무시 | HIGH | ✅ 수정 | 설정 반영 정확성 복구. **매매 동작 변화 없음**(현재 YAML 값 == 기존 하드코딩 기본값, §5에서 수치로 검증) |
| **BUG-02** Partial Exit trailing stop 하락 가능 | MEDIUM | ✅ 수정 | 스탑 역행 차단. 스탑을 **느슨하게 만들지 않는 방향**이므로 리스크 완화 아님 |
| **BUG-03** candidate=0 시 알람 침묵 | HIGH | ✅ 수정 | 무거래/파이프라인 장애가 이제 WARNING/ERROR로 구분 보고됨 |
| **BUG-04** Gate Funnel 지표 출처 혼합 | MEDIUM | ✅ 수정 | 퍼널이 `research.decision_ledger` 단일 출처로 통일, 숫자 역전 해소 |

---

## 2. 변경 파일

### 2-1. `main_auto_trading.py` (BUG-01)

- **change**: `execute_buy()` 내 Entry Quality 설정 조회 5건을 ConfigLoader 전체 경로(방식 A)로 통일.
  ```python
  # before — eq_config는 일반 dict라 점표기 키가 항상 miss → 기본값 폴백
  eq_config = self.config.get('entry_quality', {})
  rvol_enabled   = eq_config.get('rvol_filter.enabled', True)
  rvol_threshold = eq_config.get('rvol_filter.threshold', 1.7)
  ema9_enabled   = eq_config.get('ema9_pullback.enabled', True)
  vwap_enabled   = eq_config.get('vwap_distance.enabled', True)
  vwap_max       = eq_config.get('vwap_distance.max_pct', 1.8) / 100

  # after
  rvol_enabled   = self.config.get('entry_quality.rvol_filter.enabled', True)
  rvol_threshold = self.config.get('entry_quality.rvol_filter.threshold', 1.7)
  ema9_enabled   = self.config.get('entry_quality.ema9_pullback.enabled', True)
  vwap_enabled   = self.config.get('entry_quality.vwap_distance.enabled', True)
  vwap_max       = self.config.get('entry_quality.vwap_distance.max_pct', 1.8) / 100
  ```
- **reason**: `ConfigLoader.get()`은 점 경로를 지원하지만 그 **반환값(일반 dict)** 은 지원하지 않는다.
  YAML이 중첩 구조라 5개 설정 전부 YAML을 무시하고 하드코딩 기본값으로 동작하고 있었다.
  CLAUDE.md의 "코드 수정 < YAML 파라미터 조정" 원칙이 이 경로에서 무효 상태였다.
- **risk**: **없음(무동작 변경)**. 현재 YAML 값 5개 전부가 기존 기본값과 동일함을 실행으로 확인(§5).
  `eq_config` 변수는 완전히 제거되어 혼합 사용(방식 A/B 혼용) 여지도 없앴다.
- **검증**: 점표기-on-dict 스캐너 재실행 결과 `0건` (수정 전 5건).

### 2-2. `main_auto_trading.py` (BUG-02)

- **change**: 부분청산 `stage >= 2` 경로에 monotonic 가드 추가.
  ```python
  # before
  position['trailing_stop_price'] = position['highest_price'] * (1 - ratio / 100)

  # after
  _pe_calc_stop = position['highest_price'] * (1 - ratio / 100)
  _pe_prev_stop = position.get('trailing_stop_price') or 0
  position['trailing_stop_price'] = max(_pe_prev_stop, _pe_calc_stop)
  ```
- **reason**: `trading/exit_logic_optimized.py:1089`는 `max(prev, calc)`로 단조성을 보장하는데
  이 경로만 가드가 없어, ATR 기반으로 이미 높게 래칫된 스탑이 부분청산 시점에 하향 이동할 수 있었다
  (예: 9,960 → 9,900). 이후 ATR이 확대되면 낮아진 값이 그대로 굳어 이익 반납으로 이어진다.
- **risk**: **낮음**. 스탑을 **올리기만** 하고 내리지 않는 변경이라 손절이 느슨해지지 않는다.
  상향 래칫은 그대로 동작함을 테스트로 확인(`test_bug02_higher_calculated_stop_still_ratchets_up`).
- **전 경로 점검**: 실행 경로의 모든 `trailing_stop_price` 대입 지점을 확인했다.
  `main_auto_trading.py`(수정 완료) / `trading/exit_logic_optimized.py`(이미 가드 있음).
  `trading/trend_exit_engine.py:360`은 가드가 없으나 **실행 경로에 없어**(크론 미등록·미실행·미임포트)
  이번 범위에서 제외하고 FOLLOWUP FU-01에 기록했다.

### 2-3. `analysis/gate_health_check.py` (BUG-03)

- **change**:
  - `collect_liveness()` 신규 — 생존신호(트레이딩 로그 존재/regime 평가 횟수/daily_scan 산출물/하트비트) 수집
  - `classify_zero_candidate()` 신규 — candidate=0의 원인을 `ERROR`(파이프라인 미실행) / `WARNING`(전략 필터 거절)로 분류
  - `build_report()`에 `errors` 리스트와 `zero_candidate_severity` 추가, ERROR가 WARNING보다 우선하도록 `system_status` 판정 변경
- **reason**: 기존 즉시경고가 전부 `candidates > 0` 을 전제로 해서, **candidate가 0인
  가장 의심스러운 상황에서 어떤 알람도 울리지 않았다.** 실제로 2026-07-27 주문 0건인데
  Gate Health가 `NORMAL`로 보고되었고, 26일 무거래가 한 번도 경보되지 않았다.
- **risk**: **없음**. 읽기 전용 분석 스크립트로 실거래 로직에 관여하지 않는다.
- **오탐 방지**: 과거 날짜 조회 시 `daily_patterns.json`이 매일 덮어써져 항상 `scanner_ran=False`가
  되는 문제를 발견해, `scanner_ran`을 **오늘 검사일 때만 판정**(과거는 `None`=판정 불가)하도록 보정했다.
  실측 확인: 07-26(일, 프로세스 미실행) → `ERROR`, 07-27(활동 있었음) → `WARNING`, 당일 → `WARNING`.

### 2-4. `analysis/gate_health_check.py` (BUG-04)

- **change**:
  - `_decision_total()` / `_entry_stage_count()` / `_reason_breakdown()` 신규 (DB 단일 출처 집계)
  - `entry_signal_checked`를 **로그 태그 산술 → `research.decision_ledger` 집계**로 전환
    ```python
    # before
    entry_signal_checked = max(0, logs['regime_evaluated'] - logs['regime_block']
                                - logs['afternoon_cutoff_block'] - logs['early_window_block'])
    # after
    entry_signal_checked = _entry_stage_count(cur, target_date)
    ```
  - `_ENTRY_STAGE_REASONS` 상수 — migration 005의 execute_buy 단계 reason_code 9종 기준
  - 출력에서 DB 출처(`[Funnel — source: DB only]`)와 로그 출처(`[Reference — source: LOG,
    different denominator]`)를 분리하고, 로그 값에는 `pct()`를 적용하지 않음
- **reason**: DB 카운트(candidate 1건=1행)와 로그 카운트(스캔 사이클마다 종목별 반복)의
  분모가 달라 `REGIME_BLOCK 707 vs Candidate 293 (241%)` 같은 역전이 발생했다.
  퍼널 정합성 검증 자체가 구조적으로 불가능한 상태였다.
- **risk**: **없음**. 읽기 전용 분석 스크립트. DB 스키마 변경 없음, 쿼리 추가만.

### 2-5. `tests/unit/test_bug_audit_v1_20260728.py`

- **change**: 감사 시점 7건(4 PASS + 3 xfail) → **22건 전부 PASS**로 재작성.
  xfail 마커 전량 제거(수정 완료로 더 이상 실패하지 않음).
- **reason**: 지시서 §3 필수 항목 커버 — BUG-01 YAML→Runtime 적용, BUG-02 monotonic,
  BUG-03 candidate zero alarm, BUG-04 funnel count consistency.
- **risk**: 없음(테스트 전용).

---

## 3. Regression 결과

```
BEFORE (수정 전)
  PASS   : 385
  FAIL   : 16
  XFAIL  : 3

AFTER (수정 후)
  PASS   : 403
  FAIL   : 16
  XFAIL  : 0

NEW FAIL : 0
```

**Pre-existing Failure (16건, 이번 작업과 무관)**
- `tests/unit/test_swing_holding_manager.py` — 8건
- `tests/unit/test_swing_runner_logic.py` — 8건
- 원인: `holding_mgr.evaluate()` 반환값 언패킹 불일치(`ValueError: too many values to unpack`).
  감사 이전부터 존재했으며 `git stash`로 수정 전 코드에서도 동일하게 실패함을 확인했다.

**신규 테스트 22건 상세**
| 대상 | 건수 | 핵심 검증 |
|---|---|---|
| BUG-01 | 7 | ConfigLoader 계약 고정, 점표기-on-dict 잔존 0건, YAML threshold 1.8/3.5 반영, enabled=False 반영, **E2E: threshold 99.0→차단 / 0.1→통과** |
| BUG-02 | 4 | 지시서 명시 케이스(prev=100, calc=95 → 100), 단조성, 상향 래칫 정상, 소스 가드 존재 |
| BUG-03 | 7 | 침묵 해소, 파이프라인 정상→WARNING, 미실행→ERROR, 정지→ERROR, 프로브 부재 처리, 정상일 오탐 없음, 분류기 단위 |
| BUG-04 | 4 | reason_code 커버리지+FK 유효성, 로그 산술 제거 확인, DB 필드 노출, 퍼널 역전 불가 |

---

## 4. 운영 영향 평가

| 항목 | 판정 | 근거 |
|---|---|---|
| **매매 판단 변경** | **NO** | BUG-01은 수치로 무동작 검증(아래). BUG-02는 스탑을 올리기만 함. BUG-03/04는 읽기 전용 분석 스크립트 |
| **전략 변경** | **NO** | 진입/청산 조건, Score 계산식, MIN_SCORE, Regime 기준, RISK_OFF/LCL/Drawdown 정책, SMC 조건 전부 불변 |
| **위험 감소** | **YES** | ① 트레일링 스탑 이익 반납 경로 차단 ② 파이프라인 장애가 ERROR로 즉시 드러남 |
| **설정 반영 정확성** | **개선** | EQ-1/2/3의 5개 YAML 설정이 이제 실제로 반영됨 (기존: 전부 무시) |
| **모니터링 신뢰성** | **개선** | candidate=0 침묵 해소 + 퍼널 단일 출처화로 숫자 역전 제거 |

### ⚠️ `git diff` 해석 주의

이 저장소의 작업트리에는 **여러 이전 세션의 커밋되지 않은 변경**이 누적되어 있다
(브랜치 HEAD는 `6a260488` Trading OS v1.0 시점). 따라서 `git diff main_auto_trading.py`는
1,289 insertions를 보여주지만 **그 대부분은 이번 작업과 무관한 기존 변경**이다
(regime gate, choch_grade threading, daily_reset_marker 등).

이번 작업의 변경만 격리 확인한 결과:

| 파일 | 수정 시각 | 변경 내용 |
|---|---|---|
| `analysis/gate_health_check.py` | ~09:05 | BUG-03/04 |
| `main_auto_trading.py` | 09:16:50 | BUG-01(5줄) + BUG-02(가드 3줄) |
| `tests/unit/test_bug_audit_v1_20260728.py` | 09:19:34 | 회귀 테스트 |

`main_auto_trading.py`에서 이번에 바꾼 것은 위 두 지점뿐이며,
진입/청산 조건·threshold·score·regime 로직은 **한 줄도 건드리지 않았다.**

### BUG-01 무동작 변경 실증

```
key                              수정전(기본값)   수정후(YAML)   동일?
rvol_filter.enabled                      True           True    YES
rvol_filter.threshold                     1.7            1.7    YES
ema9_pullback.enabled                    True           True    YES
vwap_distance.enabled                    True           True    YES
vwap_distance.max_pct                     1.8            1.8    YES
=> 매매 동작 변화: 없음 (모든 값 동일)
```
즉 이번 수정은 **"지금 동작을 바꾸는" 것이 아니라 "앞으로 YAML을 바꿨을 때 반영되게" 하는 것**이다.

---

## 5. 배포 상태 — ⚠️ 확인 필요

**현재 실행 중인 프로세스는 수정 전 코드로 동작 중이다.**

- 수정 시각: 09:16~09:25 (장중)
- 실행 중 프로세스: PID 3196098, 08:45 기동 — **파일 수정은 이미 로드된 프로세스에 영향 없음**
- watchdog 크론(08:45/09:00/09:15)은 모두 통과했고, 세 번 모두 "정상 동작 중"으로 재시작하지 않았다.
  **오늘 남은 자동 재시작 스케줄 없음** → 의도치 않은 반영 위험 없음
- `analysis/gate_health_check.py`(BUG-03/04)는 별도 크론 스크립트라 **다음 실행(15:58)부터 즉시 반영**

**따라서 BUG-01/02(main_auto_trading.py)는 장 마감 후 수동 재시작 시점에 반영된다.**
재시작 여부/시점은 운영자 승인 사항이다.

---

## 6. 완료 조건 체크

- [x] BUG-01 수정
- [x] BUG-02 수정
- [x] BUG-03 수정
- [x] BUG-04 수정
- [x] Regression Test 추가 (22건, 전부 PASS)
- [x] pytest 실행 완료 (Before/After 기록, NEW FAIL 0)
- [x] 전략 로직 변경 없음 확인 (§4)
- [x] PIPELINE_AUDIT_FIX_REPORT_v1.1 작성 (본 문서)
- [x] 추가 발견 사항은 수정하지 않고 `docs/PIPELINE_AUDIT_FOLLOWUP.md`에 기록 (FU-01~FU-05)
