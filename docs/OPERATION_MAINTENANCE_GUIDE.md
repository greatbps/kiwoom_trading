# 운영 유지보수 가이드

> 작성일: 2026-07-28 | Production Audit v2.2
> 대상: 이 시스템의 코드를 수정하려는 운영자 / AI 어시스턴트
> 관련: `docs/RUNTIME_EXECUTION_MAP.md`(실행 경로 지도), `CLAUDE.md`(프로젝트 원칙)

이 문서의 목적은 하나다.
**"고쳤는데 아무 효과가 없었다" 와 "의도치 않게 실거래가 바뀌었다" 를 둘 다 막는 것.**

---

## 0. 이 프로젝트의 3가지 함정

수정 전에 이것만은 알고 시작해야 한다.

1. **같은 기능의 구현이 3~6개 공존한다.**
   손절 로직은 `trading/exit_logic_optimized.py` 하나만 동작하고, 나머지 6개는 휴면이다.
   그중 2개는 **파일명까지 동일**하다(`gpt_share/`, `docs/share/`).
   `core/auto_stop_loss_system.py`는 이름과 달리 **한 번도 실행되지 않는다.**

2. **09:15 이후 코드를 고쳐도 당일 반영되지 않는다.**
   watchdog 크론은 08:45/09:00/09:15 3회뿐이고, 프로세스가 정상이면 재시작하지 않는다.
   이미 메모리에 로드된 코드로 계속 돈다. → **반영하려면 수동 재시작 필요.**
   (역으로, 09:15 이후 파일 수정은 의도치 않게 반영될 위험도 없다 — 안전 창)

3. **줄번호는 반나절이면 어긋난다.** 문서의 `파일:줄번호`를 믿지 말고 `grep`으로 찾아라.

---

## 1. 수정 전 체크리스트

### ① 현재 실행 PID 확인
```bash
ps aux | grep main_auto_trading | grep -v grep
cat /tmp/kiwoom_heartbeat.json          # {"pid":..., "stage":..., "time":...}
```
`stage`가 `monitoring`이면 장중 감시 중이다.

### ② 지금이 안전한 시각인가
```bash
date '+%H:%M'
```
| 시각 | 파일 수정 | 재시작 |
|---|---|---|
| ~08:44 | 안전 | 08:45 watchdog이 자동 반영 |
| 08:45~09:15 | **위험** — 크래시 시 watchdog이 미검증 코드로 재시작 | 신중히 |
| 09:15~15:30 | 안전(자동 반영 없음) | **장중 재시작은 승인 필요** |
| 15:30~ | 안전 | 자유 |

### ③ 고치려는 파일이 실제로 실행되는가 — **가장 중요**
```bash
# (1) 로드되는가
grep -rn "import <모듈>" main_auto_trading.py api_server.py swing_runner.py \
        swing_executor.py watchdog.py

# (2) 호출/인스턴스화되는가  ← 로드만 되고 안 쓰이는 모듈이 많다
grep -rn "<클래스명>(\|\.<함수명>(" main_auto_trading.py

# (3) 오늘 실행 흔적이 있는가
grep "\[<로그태그>\]" logs/auto_trading_$(date +%Y%m%d).log
```
셋 다 비면 **휴면 코드**다. 고쳐도 실거래는 변하지 않는다.
빠른 확인은 `docs/RUNTIME_EXECUTION_MAP.md §3 대조표`.

### ④ ACTIVE / LEGACY 판정
| 분류 | 의미 | 수정 효과 |
|---|---|---|
| 🟢 ACTIVE-RUNTIME | 오늘 로그에 증거 있음 | 즉시 영향 |
| 🟡 ACTIVE-PATH | 경로에 있으나 조건 미충족(예: 거래 0건이면 execute_buy 미실행) | 조건 충족 시 영향 |
| 🟠 LOADED-ONLY | import는 되나 호출 안 됨 | **효과 없음** |
| ⚪ NOT-LOADED | import조차 안 됨 | **효과 없음** |

### ⑤ 수정 전 테스트 기준선 기록
```bash
python3 -m pytest tests/unit/ tests/simulation/ --no-cov -q 2>&1 | tail -3
```
현재 기준선: **PASS 440 / FAIL 16** (FAIL 16은 `test_swing_holding_manager.py` 8건 +
`test_swing_runner_logic.py` 8건 — 감사 이전부터 존재하는 무관 실패)

### ⑥ 변경 금지 항목 확인
`CLAUDE.md`의 절대 금지 + 다음은 **승인 없이 변경 금지**:
진입/청산 조건, Score 계산, MIN_SCORE, SMC/CHoCH/BOS, Regime Gate, Drawdown,
LCL, Position Sizing, Risk 정책, YAML 기본값.

---

## 2. 수정 후 체크리스트

### ① 컴파일
```bash
python3 -m py_compile main_auto_trading.py kiwoom_api.py \
        trading/exit_logic_optimized.py analysis/gate_health_check.py
```

### ② 회귀 테스트 — PASS 감소·NEW FAIL 0
```bash
python3 -m pytest tests/unit/ tests/simulation/ --no-cov -q 2>&1 | tail -3
```
수정 전 기준선과 비교한다. **FAIL이 16을 넘으면 되돌린다.**

특히 아래 테스트는 "실행 경로 구조"를 고정한다. 실패하면 휴면 모듈을 활성화했거나
실사용 모듈을 경로에서 뗀 것이므로 반드시 검토한다.
```bash
python3 -m pytest tests/unit/test_runtime_map_v21_20260728.py -q --no-cov
```

### ③ 주문 모듈 무결성 확인
```bash
# 주문 API의 재시도 정책이 유지되는가 (타임아웃 재시도 = 중복 주문 위험)
python3 -m pytest tests/unit/test_audit_v2_20260728.py -q --no-cov
```

### ④ 재시작 필요 여부 판단
| 수정 대상 | 반영 시점 |
|---|---|
| `main_auto_trading.py`, `kiwoom_api.py`, `trading/`, `core/`, `analyzers/` | **재시작 필요** |
| `analysis/*.py` (크론 스크립트) | 다음 크론 실행 시 자동 |
| `config/strategy_hybrid.yaml` | **재시작 필요** (기동 시 로드) |

### ⑤ 재시작 절차 (장중이면 승인 필수)
```bash
# 1) 사전 안전 점검 — 셋 다 0이어야 안전
grep -E "주문 성공|order_buy" logs/auto_trading_$(date +%Y%m%d).log | tail -3   # 미체결 주문
python3 -c "import json;d=json.load(open('data/positions_state.json'));print(len({k:v for k,v in d.items() if k!='_meta'}),'건')"
cat /tmp/kiwoom_heartbeat.json

# 2) 종료 → PID/하트비트 정리 → 기동
kill -SIGKILL <PID>; sleep 2
rm -f /tmp/kiwoom_trading.pid /tmp/kiwoom_heartbeat.json
nohup ./venv/bin/python main_auto_trading.py >> logs/auto_trading_$(date +%Y%m%d).log 2>&1 </dev/null &
disown
```
> ⚠️ `pkill -f "패턴"`은 **자기 셸까지 죽일 수 있다**(명령줄이 패턴에 매칭됨).
> PID를 직접 지정하거나 `pgrep -f "bash /경로/스크립트\.sh$"`처럼 정확히 좁혀라.

### ⑥ 재시작 후 검증
```bash
LOG=logs/auto_trading_$(date +%Y%m%d).log
grep "DAILY_RESET" $LOG | tail -1        # 재시작이면 [DAILY_RESET_SKIPPED] 여야 정상
grep -E "DecisionService|EquityController|DrawdownEngine|MarketRegimeGate" $LOG | tail -5
grep -iE "traceback|CRITICAL" $LOG | tail -5     # 비어 있어야 정상
cat /tmp/kiwoom_heartbeat.json                    # stage 갱신 확인
```

### ⑦ 로그 확인 (장중이면 10분 후 재확인)
```bash
grep -oP '\[[A-Z][A-Z0-9_]{2,28}\]' $LOG | sort | uniq -c | sort -rn | head -15
```
평상시 나와야 하는 태그: `[REGIME]`, `[MKT_CTX]`, `[SCORE_ENGINE]`, `[RESEARCH]`,
`[DRAWDOWN]`, `[EQUITY_CTRL]`

### ⑧ Scheduler 영향 확인
```bash
crontab -l | grep -v "^#" | grep <수정한스크립트>
tail -5 logs/watchdog.log
```

### ⑨ 데이터 무결성 (research 스키마 관련 수정 시)
```bash
python3 -m analysis.check_research_integrity
python3 -m analysis.check_decision_ledger_completeness
python3 -m analysis.check_destructive_tests      # 가드 없는 TRUNCATE/DELETE 검사
```

---

## 3. 자주 하는 작업별 바로가기

| 하고 싶은 것 | 실제 고쳐야 할 곳 |
|---|---|
| 매수 주문 방식 변경 | `kiwoom_api.py` `order_buy()` — ⚠️ 재시도 정책은 중복주문 위험과 직결 |
| 매도/청산 조건 | `trading/exit_logic_optimized.py` `check_exit_signal()` |
| 트레일링 스탑 | `trading/exit_logic_optimized.py` → `grep -n "max(prev_stop, calc_stop)"`<br>부분청산분 → `main_auto_trading.py` → `grep -n "_pe_prev_stop"` |
| 손절(Hard Stop) | `trading/exit_logic_optimized.py` → `grep -n "hard_stop_pct"` |
| 진입 필터(EQ 1~6) | `main_auto_trading.py` `execute_buy()` 내 `[EQ-n]` 주석 블록 |
| Drawdown 정책 | `core/drawdown_engine.py` `can_enter()` / `record_pnl()` |
| 포지션 상태 저장 | `main_auto_trading.py` `_save_positions_state()` / `_restore_positions_state()` |
| Regime 판정 | `analyzers/market/regime_analyzer.py` `RegimeAnalyzer.evaluate()` |
| 게이트 헬스체크 | `analysis/gate_health_check.py` (크론, 재시작 불필요) |

**절대 고치지 말 것(효과 없음)**: `core/order_executor.py`, `trading/order_executor.py`,
`core/stop_loss_manager.py`, `core/auto_stop_loss_system.py`, `trading/stop_loss_executor.py`,
`trading/trend_exit_engine.py`, `core/position_manager.py`, `core/scheduler.py`,
`gpt_share/*`, `docs/share/*`

---

## 4. 사고 시 롤백

```bash
# 1) 즉시 정지
kill $(pgrep -f "main_auto_trading.py")
kill $(pgrep -f "watchdog.py")

# 2) 포지션 확인 후 HTS에서 수동 청산
python3 check_account_pnl.py

# 3) 코드 되돌리기 (수정 전 백업했다면)
cp main_auto_trading.py.bak_<타임스탬프> main_auto_trading.py
python3 -m py_compile main_auto_trading.py
```

---

## 5. 운영 기준선 (Baseline)

새 기능/전략 작업 전에 아래를 모두 만족하는지 확인한다.

- [ ] `docs/RUNTIME_EXECUTION_MAP.md`가 최신 (모듈 추가/삭제 시 갱신)
- [ ] 이 가이드가 최신
- [ ] `pytest tests/unit/ tests/simulation/` — PASS ≥ 440, NEW FAIL 0
- [ ] 신규 Critical/High 버그 0건
- [ ] Memory Profiling 정상 판정 (`python3 -m analysis.memprof_report`)
- [ ] `analysis/check_research_integrity` 이상 없음
