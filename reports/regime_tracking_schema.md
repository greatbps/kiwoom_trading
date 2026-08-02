# Regime Gate Tracking — 데이터 스키마 문서

> 생성일: 2026-07-27

## 왜 신규 DB 테이블(`research.regime_block_tracking`) 대신 이 구조인가

작업지시서는 신규 Postgres 테이블을 요구했으나, 구현 전 조사 결과:
1. `analysis/regime_block_simulator.py`가 이미 REGIME_BLOCK 종목의 사후 성과를 실제 Exit Logic 리플레이로 추적하고 있어 신규 테이블을 만들면 같은 목적의 시스템이 두 개 생긴다.
2. `research` 스키마 신규 테이블은 `CLAUDE.md` §21(GD-007, 2026-06-30)에 따라 DATA_CONTRACT.md 사전 확정 + PM 명시적 승인이 필요한 절차 대상이다.
3. 사용자 승인 하에 신규 테이블 대신 **기존 스크립트+CSV 확장** + **기존 `research.decision_ledger`(이미 존재하는 테이블, JSONB 컬럼이라 스키마 변경 없이 키만 추가 가능)** 조합으로 동일 목적을 달성했다.

## 1. `research.decision_ledger.feature_snapshot` 신규 키 (스키마 변경 없음, JSONB 내 키 추가)

DDL 변경 없음 — `feature_snapshot`은 이미 `JSONB` 컬럼이고, `services/decision_service.py`의 `_build_snapshot()`이 스칼라 값이면 임의의 새 키를 그대로 통과시킨다(코드 미수정으로 확인됨).

| 키 | 타입 | 설명 | 기록 위치 |
|---|---|---|---|
| `regime` | str | TREND_UP/NEUTRAL/RISK_OFF | `main_auto_trading.py` REGIME_BLOCK/PASS 양쪽 |
| `regime_score` | int | Regime 총점 | 상동 |
| `regime_state` | str | `regime`과 동일값(작업지시서 컬럼명 그대로 병기) | 상동 |
| `regime_reasons` | str | `\|`로 join된 reason 목록 | 상동 |
| `size_multiplier` | float | 당시 사이즈 배율 | 상동 |
| `kospi_close` / `kospi_ema20` / `kospi_ema20_prev` | float | KOSPI(Kiwoom ETF 069500) 원시 지표 | `analyzers/market/regime_analyzer.py`의 `RegimeDecision.index_metrics`에서 전달 |
| `kosdaq_close` / `kosdaq_ema20` / `kosdaq_ema20_prev` | float | KOSDAQ(Kiwoom ETF 229200) 원시 지표 | 상동 |

REGIME_BLOCK 건은 `decision_reason_code='REGIME_BLOCKED'`, PASS 건은 `decision='PASS'`로 조회 구분.

## 2. `analysis/data/regime_block_detail.csv` (신규, 종목별 상세)

| 컬럼 | 설명 |
|---|---|
| `trade_date` | 차단일 |
| `code` / `stock_name` | 종목코드/명(watchlist.json 조회, 과거 종목은 코드로 대체될 수 있음) |
| `block_time` / `block_tag` | 차단 시각 / REGIME_BLOCK or AFTERNOON_CUTOFF_BLOCK |
| `regime` / `regime_score` | 차단 당시 레짐/점수 (로그 파싱) |
| `entry_price` | 차단 시점 가격(가상 진입가) |
| `ret_1d`/`ret_3d`/`ret_5d`/`ret_10d`/`ret_20d` | 고정 거래일 후 수익률(%) — yfinance 일봉 기준 |
| `mfe_pct` / `mae_pct` | 이후 20거래일 내 최고/최저 수익률(%) |
| `exit_logic_result` | 기존 Exit Logic 리플레이 결과(WIN/LOSS/FLAT) — **주 지표, 고정일 수익률은 참고 지표** |
| `exit_logic_return_pct` | 리플레이 실현 손익(%) |

기존 `analysis/data/regime_block_history.csv`(일별 집계, 이미 존재)는 그대로 유지되며 이 파일과 역할이 다르다: history.csv=일별 요약, detail.csv=종목별 상세.

## 3. 왜 고정 N일 수익률과 Exit Logic 리플레이를 둘 다 쓰는가

- Exit Logic 리플레이(기존, 그대로 유지): "실제 운영 중인 청산 전략을 그대로 적용했다면"의 진짜 반사실적 시뮬레이션 — 가장 신뢰도 높은 주 지표.
- 고정 N일 수익률(신규): 작업지시서가 명시적으로 요구한 지표이자, Exit Logic 리플레이가 실패(데이터 부족 등)했을 때의 대체/교차검증 지표.
