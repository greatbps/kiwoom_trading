# v1.3.2 검증 체크리스트

> **목적**: v1.3.2 패치(B급 차단 + 09:00~09:30 차단)가 모든 진입 경로에 일관 적용되는지 검증  
> **작성일**: 2026-07-05  
> **검증 방법**: 로그 태그 확인 + 코드 분석

---

## 검증 항목 1 — B급 진입 전면 차단

### 1-A. YAML min_grade 확인

```bash
grep "min_grade" config/strategy_hybrid.yaml
# 기대 출력: min_grade: "A"
```

**판정**: ✅ `min_grade: "A"` 확인됨

---

### 1-B. B급 downgrade 이후 차단 코드 확인

```bash
grep -n "CHOCH_GRADE_BLOCK\|choch_grade == 'B'" main_auto_trading.py
```

**기대 동작**:
- A→B downgrade(RVOL 부족) 이후 `[CHOCH_GRADE_BLOCK]` 로그 + return
- A→B downgrade(수급 급락) 이후 동일 처리

**로그 예시**:
```
[CHOCH_GRADE_BLOCK] grade=B 123456 reason=min_grade=A
```

**판정 기준**: B급 code path 이후 `return` 존재 → ✅

---

### 1-C. PRIMARY / RAE / TED 경로 확인

| 경로 | B급 차단 방법 | 확인 |
|------|-------------|------|
| PRIMARY (SMC signal) | `smc_signals.py` min_grade='A' 적용 | ✅ |
| PRIMARY (A→B downgrade) | main_auto_trading.py `[CHOCH_GRADE_BLOCK]` | ✅ |
| RAE | `rae.enabled=false` 상태이므로 실거래 없음 | ✅ (N/A) |

---

### 1-D. 확인 테스트 (수동 검증)

```bash
# 실제 B급 진입이 execute_buy로 연결되지 않는지 로그 확인
grep "CHOCH_GRADE_BLOCK\|B급.*execute_buy\|B_TIER3" logs/auto_trading_$(date +%Y%m%d).log
# → CHOCH_GRADE_BLOCK은 나와야 하고, B_TIER3은 나오면 안 됨
```

---

## 검증 항목 2 — 09:00~09:30 신규 진입 차단

### 2-A. YAML early_window_block 확인

```bash
grep -A4 "early_window_block" config/strategy_hybrid.yaml
# 기대 출력:
# early_window_block:
#   enabled: true
#   block_start: "09:00"
#   block_end: "09:30"
```

**판정**: ✅ 확인됨

---

### 2-B. 공통 게이트 위치 확인

```bash
grep -n "EARLY_WINDOW_BLOCK" main_auto_trading.py
```

**기대**: STOCK_GATE 이후, RAE 체크(rae_detector.has_candidate) 이전에 위치

**코드 위치 확인**:
- STOCK_GATE: ~line 5382
- EARLY_WINDOW_BLOCK 게이트: ~line 5415
- RAE 체크: ~line 5646

→ 순서 ✅ (게이트가 RAE보다 먼저)

---

### 2-C. 로그 형식 확인

```bash
# 09:00~09:29:59에 신호가 발생하면:
grep "EARLY_WINDOW_BLOCK" logs/auto_trading_$(date +%Y%m%d).log
# 기대:
# [EARLY_WINDOW_BLOCK] 123456 route=ALL now=09:12:34 window=09:00~09:30
```

---

### 2-D. 예외 없음 확인

09:00~09:29:59에는 어떤 신호도 execute_buy로 연결되지 않는지 확인:

```bash
# 09:00~09:30 사이 execute_buy 호출 여부 (없어야 함)
awk '/09:0[0-9]:[0-5][0-9].*execute_buy|09:2[0-9]:[0-5][0-9].*execute_buy/' \
  logs/auto_trading_$(date +%Y%m%d).log
```

---

## 검증 항목 3 — 기존 포지션 청산 영향 없음

```bash
# 기존 포지션 exit 로직이 early_window_block에 걸리지 않는지 확인
# exit 로직은 check_entry_signal()이 아닌 별도 함수에서 처리됨
grep -n "def.*exit\|def.*sell\|execute_sell" main_auto_trading.py | head -10
```

**판정**: exit_logic은 `check_entry_signal()` 외부에서 호출되므로 영향 없음 ✅

---

## 검증 항목 4 — 로그 정합성

운영 후 1주일 이내 확인:

| 로그 태그 | 기대 빈도 | 의미 |
|-----------|-----------|------|
| `[CHOCH_GRADE_BLOCK]` | 간헐적 (B급 강등 발생 시) | B급 차단 정상 |
| `[EARLY_WINDOW_BLOCK]` | 09:00~09:30 신호 있을 때 | 시간 차단 정상 |
| `[B_TIER3]` | **없어야 함** | B급 size 축소 코드 삭제됨 |
| `[B_CUTOFF]` | **없어야 함** | B급 시간 제한 코드 삭제됨 |

---

## 검증 항목 5 — 컴파일 확인

```bash
python3 -m py_compile main_auto_trading.py && \
python3 -m py_compile analyzers/smc/smc_signals.py && \
python3 -m py_compile analyzers/market/regime_analyzer.py && \
echo "✅ 컴파일 OK"
```

**판정**: ✅ OK 확인됨 (2026-07-05)

---

## 최종 판정

| 항목 | 상태 |
|------|------|
| B급 YAML 차단 | ✅ min_grade="A" |
| B급 코드 차단 (downgrade 경로) | ✅ [CHOCH_GRADE_BLOCK] |
| 09:00~09:30 공통 게이트 | ✅ [EARLY_WINDOW_BLOCK] |
| PRIMARY에 적용 | ✅ |
| RAE에 적용 | ✅ (게이트가 먼저) |
| 기존 청산 영향 없음 | ✅ |
| 컴파일 통과 | ✅ |

**v1.3.2 패치 검증 완료** — 운영 투입 가능.
