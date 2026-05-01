# Kiwoom Trading — Claude Code 지침

## 프로젝트 개요

키움증권 API 기반 SMC(Smart Money Concept) 자동매매 시스템.
실계좌 운용 중 — 코드 변경 시 항상 신중하게 접근할 것.

---

## 핵심 실행 파일

- **`main_auto_trading.py`** — 메인 트레이딩 루프 (~5000+ 라인). 모든 매수/매도 로직의 진입점.
- **`config/strategy_hybrid.yaml`** — 전략 파라미터 전체. 코드 수정 없이 숫자 조정 가능.
- **`watchdog.py`** — 프로세스 감시 및 자동 재시작.

---

## 아키텍처

```
main_auto_trading.py
├── Signal Orchestrator (L0~L6 독립 파이프라인)
│   └── logs/signal_orchestrator.log
├── SMC Strategy (CHoCH → Sweep → OB → Entry)
│   ├── analyzers/smc/smc_signals.py      ← CHoCH 등급, OB, 신호 생성
│   ├── analyzers/smc/smc_structure.py    ← BOS/CHoCH 탐지
│   └── analyzers/smc/smc_utils.py        ← 스윙포인트, Sweep 탐지
├── core/risk_manager.py                  ← 포지션 사이즈, 연패 카운트
├── metrics/reentry_metrics.py            ← 재진입 쿨다운, Market Sensor, Conservative Mode
├── trading/exit_logic_optimized.py       ← Hard Stop, Trailing, Partial Exit
└── core/market_context.py               ← 당일 시장 상태 판단 (NO_TRADE_DAY)
```

### 두 파이프라인은 독립 AND 구조
Signal Orchestrator ACCEPT ≠ SMC 진입. 둘 다 독립적으로 조건 충족해야 매수.

---

## SMC 진입 흐름

```
check_entry_signal() [main_auto_trading.py ~line 4010]
  → 시간 필터 (10:30~12:30)
  → Market Context / Market Sensor gate
  → smc_strategy.check_entry_signal() [smc_signals.py]
      → analyze_structure() → detect_choch()
      → detect_liquidity_sweep()
      → check_entry_prefilter() [min 2/4 조건]
      → displacement_filter
      → evaluate_choch_grade() [A/B/C]
          A: 80점+  →  100% size
          B: 50점+  →  40% size
          C: 50점-  →  C_FALLBACK (12% size, OB+reclaim 필수)
      → signal=True → execute_buy()
```

---

## 포지션 Size 계층

| 케이스 | 최종 size |
|--------|----------|
| A급 + Sweep | ~100% |
| B급 + Sweep | ~40% |
| B급 Fallback (no sweep) | ~20% |
| C급 C_FALLBACK | ~12% |

Conservative Mode 활성화 시: 위 값 × 0.5
Loss Streak Guard 활성화 시: 추가 × LSG_mult

---

## 주요 설정 파라미터 (strategy_hybrid.yaml)

```yaml
smc:
  swing_lookback: 20         # 스윙포인트 탐지 범위 (크면 포인트 적음)
  sweep_lookback: 20         # Sweep 탐색 범위
  smc_afternoon_cutoff: 12:30  # 이후 신규 진입 차단
  sweep_fallback_enabled: true
  max_fallback_per_day: 3
  grade_c_fallback_size_mult: 0.6
  max_c_fallback_per_day: 2
  c_fallback_cooldown_min: 15

  choch_grade:
    min_grade: B             # C급 단독 차단 (OB없으면)
    grade_b_cutoff: "11:30"  # B급 11:30 이후 차단
    htf_b_block: true        # HTF 없는 B급 추가 차단

risk_control:
  conservative_mode:
    trading_halt_threshold: 2  # Hard Stop 2회 → 당일 종료

  loss_streak_guard:
    threshold: 3             # 연패 3회 → LSG 발동
    auto_reset_days: 3
```

---

## 로그 파일 위치

| 파일 | 내용 |
|------|------|
| `logs/signal_orchestrator.log` | Orchestrator ACCEPT/REJECT |
| `logs/smc_decision_YYYYMMDD.log` | CHoCH 감지 기록 |
| `logs/sweep_attempt_YYYYMMDD.log` | Sweep 탐지 상세 (디버그용) |
| `logs/auto_trading_YYYYMMDD.log` | 메인 루프 전체 로그 |
| `logs/auto_trading_errors.log` | 에러 전용 |
| `logs/reentry_report_YYYY-MM-DD.json` | 당일 재진입/쿨다운 리포트 |
| `data/risk_log.json` | consecutive_losses, 일일 거래 기록 |

### 중요 로그 태그

```
[C_GRADE_FALLBACK]   C급 fallback 진입
[SWEEP_FALLBACK]     B급 no-sweep 진입
[C_FALLBACK_LIMIT]   C급 일일 한도 초과
[C_FALLBACK_CD]      C급 쿨다운 차단
[C_FALLBACK_RECLAIM] C급 reclaim 없어 차단
[LSG_PASS]           Loss Streak Guard 통과 (고확신)
[LSG_BLOCK]          LSG 차단
[LSG_BOOST]          LSG 탈출 부스트 진입
[LSG_AUTO_RESET]     LSG N일 자동 해제
[TRADING_HALT]       Hard Stop 2회 → 당일 종료
[MKT_CTX]            Market Context 판단
[DISP_BLOCK]         Displacement 필터 차단
[EXPLORATION_TRY]    EXPLORATION 조건 체크 시작
[EXPLORATION_ENTRY]  EXPLORATION 진입 확정
[EXPLORATION_SKIP_RVOL] RVOL < min_rvol → 가짜 돌파 차단
[EXPLORATION_STATS]  EXPLORATION 누적 통계 (count/WR/avg)
[EXPLORATION_KILLED] 승률 30% 미만 → 자동 비활성화
[EXPLORATION_NO_SIG] 돌파/RVOL 조건 미충족
[EXPL_PEND]          1봉 확인 대기 등록 (RVOL < 2.8)
[EXPL_PEND_EARLY]    조기 진입 (RVOL≥4.0 + 가격유지 + VWAP위)
[EXPL_PEND_CONFIRM]  강확인 (가격 -0.2% 이내)
[EXPL_PEND_SOFT]     약확인 (가격 -0.5% 이내 + VWAP↑ + RVOL + HH방향)
[EXPL_PEND_REJECT]   폐기 (가격이탈 or 조건미충족)
[EXPL_PEND_EXPIRED]  폐기 (75s 초과)
[EXPL_SNAP]          진입 직전 스냅샷 (type/rvol/price_vs_bp/vwap_dist/vol_trend)
[EXPLORATION_TIME_BLOCK] 시간 필터 차단
[EXPLORATION_NO_TRADE_BLOCK] NO_TRADE_DAY → 탐색 차단
```

---

## 개발 규칙

### 수정 전 반드시 확인
1. `python3 -m py_compile <파일>` — 문법 오류 체크
2. 실계좌 영향 있는 변경은 YAML 파라미터로 먼저 시도
3. `data/risk_log.json` 직접 수정 시 백업 필수

### 절대 하지 말 것
- `execute_buy()` / `execute_sell()` 직접 호출 코드 추가 (테스트라도)
- `risk_log.json` 의 `consecutive_losses` 임의 증가
- time_filter 비활성화 (`use_time_filter: false`)
- `dry_run: false` → `true` 변경 없이 실거래 로직 테스트

### 새 기능 추가 패턴
- YAML에 설정 키 먼저 추가 → 코드에서 `config.get()` 로 읽기
- 로그 태그 `[TAG_NAME]` 형식으로 통일
- 기능 ON/OFF 플래그 반드시 YAML에 `enabled: true/false` 포함

---

## Trend Breakout 전략 (2026-03-21 추가)

```
SMC Sweep = 0 (강한 상승장) → get_regime() → "TREND" → TrendBreakoutStrategy 자동 발동

check_entry_signal():
  SMC 신호 없음 → regime == "TREND" && auto_enable_on_trend → trend_strategy.check_entry()
    → BREAKOUT: N봉 고점 돌파 + 거래량 1.5x+ + EMA 정배열
    → PULLBACK: EMA20 눌림 + 추세 유지 + 거래량 확인
    → Grade: STRONG(100%) / NORMAL(60%) / WEAK(40%)
    → 일일 최대 2회 (max_per_day)
```

레짐 감지: `market_context.get_regime()` → EMA갭 ≥ 0.5% → "TREND"
로그 태그: `[TREND_SIG]` `[TREND_NO_SIG]` `[TREND_TIME_BLOCK]` `[TREND_REGIME_SKIP]`

## 현재 전략 상태 (2026-03-21 기준)

| 기능 | 상태 |
|------|------|
| SMC 진입 | 활성 (mode=smc) |
| C_GRADE_FALLBACK | 활성 (2026-03-20 추가) |
| Sweep Fallback (B급) | 활성 |
| Conservative Mode | 비활성 (Hard Stop 0회) |
| Loss Streak Guard | 비활성 (연패 0회) |
| Overnight Close | 활성 (B급 14:50 강제청산) |
| Market Context | 활성 |
| Trend Breakout | 활성 (레짐 TREND 감지 시 자동, 일 최대 2회) |

---

## Trend Breakout 모니터링 명령

```bash
# Trend 신호 발생 수 (목표: 0이면 조건 너무 빡셈, 5+ 이면 과다)
grep "TREND_SIG" logs/auto_trading_$(date +%Y%m%d).log | wc -l

# Trend 신호 상세 (등급, 거래량, 이격)
grep "TREND_SIG\|TREND_NO_SIG\|TREND_BLOCK" logs/auto_trading_$(date +%Y%m%d).log | tail -20

# 레짐 판단 결과 (TREND / REVERSAL / NEUTRAL)
grep "TREND_REGIME" logs/auto_trading_$(date +%Y%m%d).log | tail -10

# SMC vs TREND 진입 비율
echo "SMC:"; grep "SMC_SIG" logs/auto_trading_$(date +%Y%m%d).log | wc -l
echo "TREND:"; grep "TREND_SIG" logs/auto_trading_$(date +%Y%m%d).log | wc -l

# Trend 차단 이유 분류
grep "TREND_NO_SIG\|TREND_BLOCK" logs/auto_trading_$(date +%Y%m%d).log | \
  grep -oP 'TREND: [^|]+' | sort | uniq -c | sort -rn | head -10
```

## 자주 쓰는 분석 명령

```bash
# 오늘 거래 현황
grep "C_GRADE_FALLBACK\|SWEEP_FALLBACK\|SMC_SIG\|매수완료\|매도완료" logs/auto_trading_$(date +%Y%m%d).log

# CHoCH 감지 내역
cat logs/smc_decision_$(date +%Y%m%d).log

# Sweep 탐지 상세
cat logs/sweep_attempt_$(date +%Y%m%d).log

# 연패/LSG 상태
python3 -c "import json; d=json.load(open('data/risk_log.json')); print('연패:', d['consecutive_losses'], '/ 오늘:', d['today'])"

# 컴파일 검증
python3 -m py_compile main_auto_trading.py && python3 -m py_compile analyzers/smc/smc_signals.py && echo "OK"
```
