# FAIL_OPEN_AUDIT.md — 예외 발생 시 허용(Fail-Open)으로 흐르던 코드 경로 전수 목록

Business Logic Audit v2.0(2026-07-20, Silent Failure Audit)에서 발견한 전수 목록에
Critical Bug Fix v2.1 작업 결과(수정여부)를 반영. main_auto_trading.py + analyzers/ +
trading/ + core/ + services/ + database/ + backtest/ 전체 대상, ~230개 무음/무로그
except 사이트 중 CRITICAL/MAJOR 전체 + MINOR 대표 샘플.

## CRITICAL — 매매 결정에 직접 영향

| # | 위치 | 문제 | 수정여부 | 남은 위험도 |
|---|---|---|---|---|
| 1 | main_auto_trading.py:5370-5385 `_check_global_risk_gates()` | DD_HALT/EC_HALT 예외 시 무음 허용(`except Exception: pass`→`return True,"OK"`) | **✅ 수정완료(CBF-1)** | 없음 — Fail Closed + EXCEPTION 로그 확인 |
| 2 | main_auto_trading.py:9266-9339 execute_buy EQ-4(SD_DROP) | 수급급락 필터 예외 시 무음 통과 | **✅ 수정완료(CBF-2)** | 없음 — FILTER_EXCEPTION 로그 + BLOCK 확인 |
| 3 | main_auto_trading.py:9341-9391 execute_buy EQ-5(POS_FILTER) | 추격진입 필터 예외 시 무음 통과 | **✅ 수정완료(CBF-2)** | 없음 |
| 4 | main_auto_trading.py:9390-9423 execute_buy EQ-6(REGIME) | CHOP/REVERSAL 레짐 차단 필터 예외 시 무음 통과 | **✅ 수정완료(CBF-2)** | 없음 |
| 5 | analyzers/smc/smc_signals.py:1255-1290 displacement 필터 | 가짜 CHoCH 방지 필터 예외 시 무음 통과 | **✅ 수정완료(CBF-2)** | 없음 — 6개 시나리오(정상/None/NaN/컬럼누락/빈DF/Exception) 검증 |

## HIGH — 영구 상태 미해제 (Fail-Open과 인접한 별개 결함군)

| # | 위치 | 문제 | 수정여부 | 남은 위험도 |
|---|---|---|---|---|
| 6 | main_auto_trading.py:687,9089,12620 `stock_ban_list` | "당일" 금지가 프로세스 재시작 전까지 영구화 | **✅ 수정완료(CBF-4)** | 없음 — daily_routine() 매일 리셋 확인 |
| 7 | main_auto_trading.py:5351,8652 `_orphan_halt` | orphan 감지 후 해제 로직 전무 → 영구 신규진입 차단 | **✅ 수정완료(CBF-4)** | 없음 — self-healing(_check_orphan_positions 정상확인 시 즉시해제) + 일일 방어 리셋 |

## MAJOR — 관측성 저하 (직접적 오판단 위험은 낮음, 미수정)

| 위치 | 문제 | 상태 |
|---|---|---|
| core/order_executor.py:177-182,270-275 | execute_buy/sell 실패가 OrderResult에만 담기고 로그 없음 | 미수정 — 호출부가 결과 미확인 시 유실 위험 |
| core/param_tuner.py:264-280 `_check_kill_switch()` | 히스토리 파일 손상 시 kill-switch 조건이 조용히 빠짐 | 미수정 |
| core/edt_sizer.py:159,310,320 | Kelly/DD 연동 실패 시 사이징 로직 무음 저하 | 미수정 |
| core/regime_detector.py:216 | 데이터 조회 실패 시 무로그 None 반환 | 미수정 |
| analyzers/smc/rae_detector.py:210,276 | pullback/VWAP 체크 실패 시 점수 무음 과소평가 | 미수정 |
| trading/exit_logic_optimized.py (15곳: 211,222,233,325,337,355,371,424,597,680,705,766,948,1031,1053) | 청산 점수 컴포넌트별 실패 시 무음으로 0점 처리 | 미수정 |
| core/db_auto_trading_handler.py:2422 | 손절가 도달 배지 계산 실패 시 무음 생략 | 미수정 |
| database/trading_db.py:971 | 종목명 조회 실패 시 `except Exception: continue` | 미수정 |
| database/decision_trace.py:274,426 | 피처추출/ML아웃컴 백필 실패 시 무음 유실 | 미수정 |
| analyzers/signal_orchestrator.py(659,714,748,810,852) + main_auto_trading.py(~15곳) | decision_service/smc_decision_logger 기록 실패 시 무음 — 매매결정 자체는 이미 완료된 후라 실거래 영향 없음, 리서치 데이터만 유실 | 미수정 |
| trading/stop_loss_executor.py:111 | 오늘자 손절기록 로드 실패 시 중복 재실행 가능 | 미수정 |
| trading/eod_manager.py:306,319,344,379 | EOD 보유점수 컴포넌트 계산 실패 시 무음 0점 | 미수정 |

## MINOR — 실거래 영향 없음 (대표 샘플, 총 40여건)

console/UI 표시, 텔레그램 알림, 대시보드 캐시 갱신, 종목명 폴백 등 — 실패해도 매매 결정에
영향 없는 경로. main_auto_trading.py:1516,1690,1698 / core/menu_handlers.py:2250 /
core/trading_system.py:51,117,353,2750 / core/analysis_handlers.py:607,821,942 /
trading/daily_checklist.py:186 / trading/websocket_client.py:205 /
trading/overseas_order_test.py:382 / analyzers/news_sentiment_v2.py:233 등.

## 요약

- Critical Bug Fix v2.1 지시서 범위(작업 1~4)에 해당하는 **5개 Critical + 2개 High, 총 7건 전부 수정 완료** 및 회귀검증.
- MAJOR 등급 관측성 저하 12건은 이번 작업 범위 밖(**미수정**) — 실거래 판단에 직접 영향은 낮으나, 다음 우선순위 후보로 남겨둠.
- MINOR 40여건은 실거래 영향 없어 현행 유지.
