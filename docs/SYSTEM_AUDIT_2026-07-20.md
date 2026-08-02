# System Audit — 2026-07-20

전체 데이터 흐름(수집→일봉스캔→SCORE→후보→게이트→진입→청산→DB→대시보드→스케줄러→예외처리) 전수 감리.
버그 수정이 아니라 잠재 결함 식별이 목적. 신규 기능/전략/파라미터 변경 없음.

## 최종 요약 표

| 구분 | 상태 | 심각도 | 설명 | 수정 필요 |
|---|---|---|---|---|
| Data Flow(수집) | FAIL | Major | `relative_strength_filter.py` 조회실패 시 (0,0,0) 반환 — 실제 0%수익률과 구분불가, RS≥70 게이트 왜곡 가능 | O |
| Daily Scan | FAIL | **Critical** | `daily_scan.py`가 7주째 크론 미등록으로 미실행. SMC 시그널 점수(+2, 원래 최대배점)가 조용히 완전히 죽어있었음 | O |
| SCORE_ENGINE | FIXED | Critical(해결) | watchlist 전종목에 OHLCV 대신 None 전달 → 거래량/MA50 항상 0점. 07-19 수정·검증 완료 | 완료 |
| Candidate Pipeline | WARN | Major | 로그 중복기록으로 PRE_CANDIDATE/REGIME_BLOCK 등 원시 카운트 약 2배 부풀려짐(집계 로직 자체 왜곡 아님, 관측 지표만 영향) | O |
| Watchlist Lifecycle | PASS | - | 재시작시 미복원은 의도된 설계(당일 신선 스캔), 유지/삭제 로직 이상 없음. 다만 30종목 초과시 절단이 점수순 아닌 임의순(현재 비활성 상태) | X(모니터링) |
| Global Gate | WARN | Critical(해결)+Minor | EC_HALT(해결)/Regime(해결) 정상. Afternoon Cutoff는 현재 time_window 설정상 구조적으로 발동 불가(중복 게이트). DD_HALT/DAILY_LOSS 등은 수주간 0%로 미검증 상태 | 일부 |
| Entry | WARN | Major | 매수 실패시 에러핸들러가 예외를 삼켜 DB/결정원장에 실패기록 안 남음(자금 영향 없음, 관측성 문제) | O |
| Exit | FAIL | **Major~Critical** | 매도 시 실제 브로커 주문 전에 DB/결정원장에 먼저 "매도완료" 기록 — 주문실패시 가짜 손익 기록 남고 일부는 재시도시 중복기록도 가능 | O |
| DB 기록 | WARN | Critical | `risk_manager.py`/`order_executor.py`에서 TradeDB insert 실패가 완전 무음 처리(`except: pass`) — 거래기록 통째로 유실 가능 | O |
| Dashboard | FIXED | Major(해결) | 주간손익 stale-week 표시, 장기보유종목 가짜 SL/TP 표시, api_server 토큰고착 반복 — 전부 이번주 수정·완화 완료 | 완료 |
| Scheduler | PASS | - | 크론 15개 전부 정상, api_server 일일재시작 3일 연속 정상작동 확인 | X |
| Exception 처리 | FAIL | Critical | `equity_controller.py` 상태파일 손상시 peak=0 → 드로다운 서킷브레이커 조용히 무력화(비원자적 저장이라 실제 손상 가능경로 있음) 등 10건 | O |

---

## 1. 데이터 수집 (Data Flow)

**PASS 항목**: OHLCV 캐시(`relative_strength_filter.py`, `volatility_regime.py`, `liquidity_shift_detector.py`) 전부 빈 결과를 캐싱하지 않음 — 과거 "OHLCV 빈 캐시 버그"류 재발 없음.

**FAIL (Major)**: `analyzers/relative_strength_filter.py::calculate_return()` — 데이터 조회 실패/부족 시 `(0.0, 0.0, 0.0)`을 반환하는데, 이게 "실제로 시장과 동일하게 0% 움직인 종목"과 구분이 안 됨. `filter_candidates()`의 RS≥70 게이트(신호 오케스트레이터 L2, `signal_orchestrator.py:84`)에 그대로 들어가서, 조회 실패한 종목이 우연히 게이트를 통과하거나 반대로 정상 종목이 억울하게 걸릴 수 있음.

## 2. Daily Scan (신규 발견, Critical)

`backtest/daily_scan.py`가 **크론탭에 전혀 등록돼 있지 않고**, `logs/daily_scan.log` 기준 **2026-07-01 이후 실행 기록이 없음**(에러/트레이스백도 없이 그냥 안 돎). 증거:
- `data/daily_watchlist.json` — `scan_date: "2026-05-13"` (68일 정체)
- `data/daily_patterns.json` — mtime 2026-07-01 (19일 정체)

`load_daily_watchlist()`/`load_daily_patterns()`는 날짜 체크 후 빈 값(`[]`/`{}`)을 정상 반환하므로 "오래된 데이터를 진짜인 것처럼 서빙"하는 버그는 아님 — 대신 **SCORE_ENGINE의 SMC 배점(+2, 설계상 가장 중요한 시그널)이 몇 주째 완전히 비활성 상태였는데 그걸 알려주는 로그가 하나도 없었음**. 07-19에 고친 OHLCV 버그와 합치면, 지금까지 SCORE_ENGINE은 volume(+1)/ma50(+1) 두 컴포넌트만으로 최대 2점 — score≥2 컷오프를 겨우 걸치거나 못 넘는 상태가 구조적으로 계속됐을 가능성.

## 3. SCORE_ENGINE — 해결됨

07-19 수정 완료(별도 기록 참조). watchlist 실제 OHLCV 전달, `[SCORE_INPUT]`/`[SCORE_DETAIL]` 로그 추가, 실제 데이터로 MA50=1 등 정상 스코어링 재현 검증 완료.

## 4. Candidate Pipeline

**WARN (Major, 신규)**: `analyzers/signal_orchestrator.py`의 `signal_logger`가 `propagate=False`를 설정 안 해서, 자체 파일(`signal_orchestrator.log`)과 루트(→ `auto_trading_YYYYMMDD.log`) 양쪽에 중복 기록됨. 별도로 `main_auto_trading.py`의 여러 지점에서 `console.print()`와 `logger.info()`가 같은 내용을 각각 출력하는데, 실거래 프로세스가 stdout을 동일 로그파일에 그대로 append하는 구조라 결과적으로 같은 파일 안에서도 중복이 발생. **이번 감리를 포함해 최근 대화에서 인용한 REGIME_BLOCK/PRE_CANDIDATE 등 "로그 grep count" 수치는 대략 2배 부풀려져 있었을 가능성이 높음.** DB 기반 집계(research.candidates/decision_ledger COUNT)와 `regime_block_simulator.py`의 심볼별 dedup 로직은 이 문제의 영향을 받지 않음 — 그동안 내린 "RISK_OFF가 맞다/EC_HALT=0이다" 등 핵심 결론은 전부 DB 기준이라 유효함.

**PRE_TO_CAND 태그**: 최초 승격시에만 찍혀야 하는데 매 ACCEPT마다 찍혀서(가드 없음) 퍼널 지표로 쓰기엔 부정확(Minor, 데이터 손상 아님).

**5거래일(07-13~07-17) CANDIDATE_ACCEPT=0 관련**: 이번 대화에서 실제 KOSPI/KOSDAQ 데이터로 여러 차례 직접 검증됨(07-16 -6~-8% 급락 등 실제 시장 데이터로 확인) — **버그 아니라 수정된 레짐 게이트가 정확히 작동한 결과.**

## 5. Watchlist Lifecycle — PASS

재시작시 `data/watchlist.json`에서 미복원 = 의도된 설계(당일 조건검색으로 새로 구성). 유지/삭제 로직 이상 없음. 스윙 경로(`swing_positions.json`)는 완전히 분리돼 있고 정상적으로 재시작 간 복원됨.

**latent (Minor)**: `main_auto_trading.py:4109` `WL_CAP` 초과시 절단이 `set→list` 변환 순서(사실상 임의순)로 이뤄져 점수 높은 종목이 아니라 무작위로 잘릴 수 있음 — 다만 이번 주 관측된 watchlist 크기(최대 38, 상한 30)로는 이 경로가 활성화된 적이 거의 없어 현재는 잠재 위험.

## 6. Global Gate

| 게이트 | 상태 |
|---|---|
| EC_HALT | 해결(07-12), 이후 매일 0건 확인 |
| Market Regime | 해결(07-15 HOTFIX), Decision Ledger 기록 누락도 07-13에 별도 수정 |
| Afternoon Cutoff | **구조적으로 발동 불가** — `time_window=10:30~13:30`이 컷오프(13:30)와 같아서, 이 게이트에 도달하기 전에 이미 시간창 게이트가 막음. 버그는 아니지만 중복/사실상 죽은 코드 |
| DD_HALT / Daily Loss / Kill Switch / Orphan / MS_BLOCK / TIME_PHYSICAL | 수 주간 Gate Health Check에서 계속 0% — **오작동 증거는 없으나 "실제로 한 번도 발동 안 해봐서 작동 여부를 검증 못 한" 상태.** 특히 EC_HALT 선례를 감안하면 이 중 하나가 똑같이 조용히 죽어있을 가능성을 배제 못 함 |

## 7. Entry — WARN (Major)

`execute_buy()`(`main_auto_trading.py:9007~`) 흐름은 정상이나, `exceptions/error_handler.py`의 `sync_wrapper`가 `AuthenticationError` 외 예외를 **재발생 없이 삼켜서** `order_buy()`가 실패시 `None`을 반환. 이후 `.get('return_code')` 호출이 `AttributeError`를 일으키고, 이건 콘솔 출력만 되고 `logger.error`/DB기록 없이 사라짐(단, 데코레이터 자체가 한 번은 `exc_info=True`로 에러를 남기긴 함). **자금 리스크는 없음**(실제 주문이 실패한 것) — 실패한 매수 시도가 분석용 데이터로 안 남는 관측성 문제.

## 8. Exit — FAIL (Major~Critical 경계, 이번 감리 최대 발견)

`execute_sell()`(`main_auto_trading.py:12147~`)에서 **trades DB insert · decision_ledger record_exit이 실제 브로커 매도주문 호출보다 먼저 실행됨**. 이후 실제 주문이 (토큰만료/거부 등으로) 실패하면:
- 포지션은 정상적으로 계속 보유상태 유지됨(자금은 안전)
- 그러나 **DB/결정원장에는 이미 "매도완료"로 가짜 손익·청산사유가 영구 기록됨**
- 일부 경로(`record_exit_signal`, `decision_service.record_exit`)는 dedup 가드가 없어 나중에 재시도 성공시 **중복 기록** 가능

실질적 자금 위험은 없지만 **PnL 분석·Loss Streak Guard·ML 학습 데이터가 오염**될 수 있는 구조적 문제.

## 9. DB 기록 — FAIL (Critical)

- `core/risk_manager.py` `TradeDB().insert(trade)` → `except Exception: pass` (**완전 무음**, 로그조차 없음) — 거래기록이 통째로 조용히 사라질 수 있음
- `trading/order_executor.py` 매수/매도/부분매도 DB insert 실패시 콘솔 출력만
- `core/edt_sizer.py` `record_pnl()` 실패 시 무음 → DrawdownEngine이 최신 손익을 모른 채로 사이징 판단
- `analyzers/market/regime_analyzer.py` 지수별 점수계산 예외시 해당 지수 기여도가 조용히 0/무이유 처리(이미 고친 버그와 동일 패턴, 재발 가능 지점)

## 10. Dashboard — 해결됨

이번 주 발견·수정: 주간손익 stale-week 표시(날짜 검증 추가), 장기보유종목 가짜 SL/TP(별도 표시로 분리), api_server 토큰고착 반복(일일 자동재시작 크론으로 완화).

## 11. Scheduler — PASS

크론 15개 전수 확인, 전부 정상 등록. api_server 일일 재시작(06:00) 3일 연속(07-17~19) 정상 작동 로그로 확인. watchdog(08:45/09:00/09:15) 하트비트 기반 좀비감지 정상.

## 12~13. 예외처리 / None·Null Audit

**Critical**
- `trading/equity_controller.py::_load()` — 상태파일 손상시 `peak=0.0`으로 남는데, 하류 `can_enter()`/`get_drawdown_mult()`가 `peak<=0`을 "no_peak"으로 취급 → **드로다운 서킷브레이커(-18% 정지)가 조용히 완전 비활성화**. `_save()`가 원자적 쓰기(temp+rename)가 아니라서 크래시 시 실제 손상 가능 — EC_HALT 버그의 거울상(그때는 영구정지, 이번엔 정지장치 소멸).
- `core/risk_manager.py:401-402` TradeDB insert `except: pass` (9번 항목과 동일)

**Major**
- `core/edt_sizer.py:150-159` DD엔진 사이즈멀티플라이어 조회 실패시 무음 → 기본값(1.5) 유지, 드로다운 반영 안 됨
- `trading/stop_loss_executor.py` 실행기록 저장 실패해도 메모리 상태는 이미 갱신됨 → 재시작시 중복 손절주문 위험
- `trading/exit_logic_optimized.py` 오버나이트 갭 거래량서지 체크 예외시 무음 → 진짜 갭하락을 노이즈로 오판, 최대 20분 손절 지연

**Minor**: `db_auto_trading_handler.py`(표시전용, 실주문 무관) · `reentry_strategy.py`(재진입후보 목록만 영향) 등 5건 추가.

## 14. 로그 Audit

4번 항목 참조(중복기록). 그 외 "로그만 있고 실제 처리 없음" 패턴은 9/12/13에 통합.

## 15. Dead Code — 확인됨(호출처 없음)

- ~~`analyzers/liquidity_shift_detector.py::LiquidityShiftDetector` (v2로 완전 대체)~~ —
  **2026-07-20 정정: 오판정.** `LiquidityShiftDetectorV2`가 이 클래스를 **상속**하고 있어
  실제로는 살아있는 베이스 클래스임(삭제 시도 중 v2 import 확인하다 발견, 원복 완료).
- `core/regime_detector.py::reset_cache()` — 2026-07-20 제거 완료
- `trading/signal_detector.py::calculate_signal_confidence()` — 2026-07-20 제거 완료
- `trading/bottom_pullback_manager.py::remove_signal()`, `get_signal_info()` — 2026-07-20 제거 완료

---

## 종합 의견

이번 감리로 확인된 패턴: **"실패/예외가 조용히 빈 값·기본값으로 대체되고 로그(있어도 debug급) 외엔 아무 신호가 없다"**는 동일한 결함 유형이 최소 6곳(SCORE_ENGINE·daily_scan·RS필터·equity_controller·risk_manager·exit_logic 갭체크)에서 반복 발견됨 — 개별 버그라기보다 **이 코드베이스 전반의 예외처리 관례** 문제로 보임.

가장 시급한 것은 **Exit 시 DB선기록 문제(8번)**와 **equity_controller 드로다운 무력화 가능성(9/13번)** — 둘 다 "지금 당장 잘못된 결과를 내고 있다"는 증거는 없지만, 조건이 맞으면 조용히 발동할 수 있는 상태. daily_scan.py 미실행(2번)은 이미 확정된 결함이라 우선순위 상위.
