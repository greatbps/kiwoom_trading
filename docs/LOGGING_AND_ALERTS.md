# 로그 & 알림 체계

> **대상 독자**: 운영자, 개발자  
> **최종 갱신 기준**: ruleset_version v1.3 (2026-07-05)  
> **관련 파일**: `logs/`, `main_auto_trading.py`, `analyzers/signal_orchestrator.py`

---

## 1. 로그 파일 목록

| 파일 | 내용 | 갱신 주기 |
|------|------|-----------|
| `logs/auto_trading_YYYYMMDD.log` | 메인 거래 이벤트 전체 | 장중 실시간 |
| `logs/auto_trading_errors.log` | 에러 전용 | 발생 시 |
| `logs/signal_orchestrator.log` | L0~L6 Orchestrator 평가 결과 | 장중 실시간 |
| `logs/smc_decision_YYYYMMDD.log` | CHoCH 감지 기록 | 장중 실시간 |
| `logs/sweep_attempt_YYYYMMDD.log` | 유동성 스윕 탐지 기록 | 장중 실시간 |
| `logs/reentry_report_YYYY-MM-DD.json` | 재진입 / EF 통계 | 장 마감 후 |
| `data/risk_log.json` | 연패 기록 (Loss Streak Guard 상태) | 거래 시 |
| `logs/swing_orders_YYYYMMDD.json` | 스윙 주문큐 | 15:35 생성 |
| `reports/rae_validation_summary_YYYYMMDD.md` | RAE 검증 리포트 | 온디맨드 |

---

## 2. 핵심 로그 태그 목록

### 진입 관련

| 태그 | 의미 | 파일 |
|------|------|------|
| `[SMC_PRIMARY]` | Primary CHoCH 진입 확정 | `main_auto_trading.py` |
| `[SMC_RAE]` | RAE 진입 확정 | `main_auto_trading.py` |
| `[REACCEL_ENTRY]` | RAE REACCEL 상태 진입 감지 | `main_auto_trading.py` |
| `[G3_SOFT_PENALTY]` | HIGH_PROX → size × 0.6 적용 | `signal_orchestrator.py` |
| `[TED_BLOCK]` | TED 실패 → soft penalty (차단 아님) | `main_auto_trading.py` |
| `[EXPLORATION_LOW_CONF]` | EXPLORATION 신뢰도 미달 | `main_auto_trading.py` |
| `[EXPLORATION_CHASE]` | EXPLORATION 급등 추격 차단 | `main_auto_trading.py` |
| `[EXPLORATION_GAP_BLOCK]` | EXPLORATION 갭 추격 차단 | `main_auto_trading.py` |
| `[EXPLORATION_KILLED]` | EXPLORATION 승률 30% 미만 자동 비활성화 | `main_auto_trading.py` |

### RAE 상태 관련

| 태그 | 의미 | 파일 |
|------|------|------|
| `[RAE_REGISTER]` | RAE 후보 등록 | `rae_detector.py` |
| `[RAE_PULLBACK]` | IMPULSE → PULLBACK 상태 전이 | `rae_detector.py` |
| `[RAE_REACCEL]` | PULLBACK → REACCEL 상태 전이 | `rae_detector.py` |
| `[RAE_SIG]` | RAE 진입 신호 확정 | `rae_detector.py` |
| `[RAE_BLOCK]` | 충돌 방지 규칙으로 RAE 차단 | `main_auto_trading.py` |
| `[RAE_RESET_DAILY]` | 일일 리셋 | `main_auto_trading.py` |
| `[RAE_RESET_STALE]` | 180분 초과 stale 리셋 | `rae_detector.py` |
| `[RAE_RESET_INVALIDATED]` | CHoCH 구조 붕괴 리셋 | `rae_detector.py` |
| `[RAE_RESET_AFTER_EXIT]` | 청산 후 RAE 상태 정리 | `main_auto_trading.py` |

### 청산 관련

| 태그 | 의미 | 파일 |
|------|------|------|
| `[HARD_STOP]` | Hard Stop 발동 | `exit_logic_optimized.py` |
| `[SWING_HARD_STOP]` | Swing Hard Stop (-12%) | `exit_logic_optimized.py` |
| `[LCL_EARLY_CUT]` | LCL 조기 손절 | `exit_logic_optimized.py` |
| `[EF_TRIGGER]` | Early Failure Structure 발동 | `exit_logic_optimized.py` |
| `[EF_SUBTYPE]` | EF Subtype 분류 결과 | `exit_logic_optimized.py` |
| `[TP1]` | TP1 부분 익절 | `exit_logic_optimized.py` |
| `[TP2]` | TP2 추가 부분 익절 | `exit_logic_optimized.py` |
| `[TRAILING_STOP]` | ATR Trailing Stop 발동 | `exit_logic_optimized.py` |
| `[TIME_EXIT]` | Time Exit 강제 청산 | `exit_logic_optimized.py` |
| `[OVERNIGHT_EXIT]` | Overnight 강제 청산 | `exit_logic_optimized.py` |
| `[SWING_HARD_STOP]` | 스윙 최대 손절 fallback | `exit_logic_optimized.py` |

### 리스크 엔진 관련

| 태그 | 의미 | 파일 |
|------|------|------|
| `[DD_CAUTION]` | DrawdownEngine CAUTION 레벨 진입 | `drawdown_engine.py` |
| `[DD_DANGER]` | DrawdownEngine DANGER 레벨 진입 | `drawdown_engine.py` |
| `[DD_HALT]` | DrawdownEngine HALT — 진입 차단 | `drawdown_engine.py` |
| `[DD_STRATEGY_HALT]` | 특정 전략만 당일 차단 | `drawdown_engine.py` |
| `[LSG_BLOCK]` | Loss Streak Guard 차단 | `main_auto_trading.py` |
| `[C_GRADE_FALLBACK]` | C급 CHoCH fallback 진입 | `main_auto_trading.py` |

---

## 3. 에러 / 경고 / 정보 구분

| 레벨 | 사용 시점 |
|------|-----------|
| `ERROR` | 주문 실패, DB 저장 실패, API 오류 등 시스템 오류 |
| `WARNING` | 데이터 부족, 비정상 값 감지, 쿨다운 우회 남용 경고 |
| `INFO` | 정상적인 거래 이벤트, 상태 전이, 게이트 통과/차단 |
| `DEBUG` | 내부 계산값, 세부 조건 체크 결과 |

---

## 4. 텔레그램 알림

`energy_watchdog` 프로젝트 전용 알림 (별도 프로젝트):
- token: `8252382230:AAEPiPmgvoe73_Z1matB7GTNvqhyNKTPpGM`
- chat_id: `19196452` (OC_GreatBPS_bot → greatBPS)

**kiwoom_trading 메인 시스템**: 텔레그램 알림 별도 확인 필요 (`확인 필요`).

---

## 5. 운영 중 반드시 봐야 하는 로그 TOP 10

```bash
# 1. DrawdownEngine HALT (진입 전면 차단 상태)
grep "DD_HALT" logs/auto_trading_$(date +%Y%m%d).log

# 2. Hard Stop 발동 (큰 손실 이벤트)
grep "HARD_STOP\|hard_stop" logs/auto_trading_$(date +%Y%m%d).log

# 3. Early Failure 발동 (패턴 붕괴)
grep "EF_TRIGGER\|EF_SUBTYPE\|ef_no_demand" logs/auto_trading_$(date +%Y%m%d).log

# 4. LCL 발동 (조기 손절 다발)
grep "LCL_EARLY_CUT" logs/auto_trading_$(date +%Y%m%d).log

# 5. 매수/매도 완료 (실제 거래 확인)
grep "매수완료\|매도완료" logs/auto_trading_$(date +%Y%m%d).log

# 6. Orchestrator 차단 (진입 기회 소멸)
grep "REJECT\|BLOCK\|차단" logs/signal_orchestrator.log | tail -20

# 7. API 에러 (주문 실패 가능성)
grep "ERROR" logs/auto_trading_errors.log | tail -20

# 8. G3 Soft Penalty (늦은 진입 size 축소)
grep "G3_SOFT_PENALTY" logs/signal_orchestrator.log

# 9. Loss Streak Guard 활성
grep "LSG_BLOCK\|Loss Streak" logs/auto_trading_$(date +%Y%m%d).log

# 10. 연패 상태
python3 -c "import json; d=json.load(open('data/risk_log.json')); print('연패:', d['consecutive_losses'])"
```

---

## 6. 일일 운영 로그 체크 루틴

```bash
#!/bin/bash
TODAY=$(date +%Y%m%d)
LOG="logs/auto_trading_${TODAY}.log"

echo "=== 당일 거래 현황 ==="
grep "매수완료\|매도완료" $LOG | wc -l
echo "건"

echo "=== 리스크 이벤트 ==="
grep "DD_HALT\|HARD_STOP\|LSG_BLOCK" $LOG | tail -5

echo "=== EF 발동 ==="
grep "EF_TRIGGER\|ef_no_demand" $LOG | wc -l
echo "건"

echo "=== 에러 ==="
tail -20 logs/auto_trading_errors.log
```

---

## 7. 로그 태그 작성 규칙

새 로그 추가 시 반드시 `[TAG_NAME]` 형식 사용. 이유:

- grep으로 빠른 검색 가능
- 패턴 분석 도구에서 자동 집계 가능
- 운영자가 태그만으로 상황 파악 가능

**형식**: `logger.info(f"[TAG_NAME] {stock_code}: 상세 메시지 key=val")`
