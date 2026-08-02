# Regime 관련 데이터소스 일관성 매트릭스

> 생성일: 2026-07-27
> 목적: 07-13 Kiwoom/Yahoo 값 차이의 근본 배경 — 코드베이스 전체가 실제로 KOSPI/KOSDAQ 지수를 어떤 소스로 읽는지 전수 확인 (grep 기반, read-only)

## 1. v1.4 Regime Gate (실거래 게이트, 오늘 분석 대상)

| Module | Function | Source | Ticker | Cache | Update Time |
|---|---|---|---|---|---|
| `analyzers/market/regime_analyzer.py` | `RegimeAnalyzer._compute()` → `_score_index()` | **Kiwoom API** (`self.api.get_daily_chart`) | `069500`(KOSPI, KODEX200), `229200`(KOSDAQ, KODEX코스닥150) — **ETF 가격**, 지수 자체 아님 | `cache_minutes: 60` (장중 1시간마다 갱신) | 장중 실시간 호출 시점 |
| `analyzers/market/regime_analyzer.py` | `RegimeAnalyzer.evaluate_for_datetime()` (백테스트 전용 메서드, 현재 실거래 미사용) | yfinance | `^KS11`, `^KQ11` — **지수 원본** | 없음(1회성 호출) | N/A |

**실거래에 실제로 영향을 주는 건 첫 번째 행뿐입니다** — `evaluate()`가 매매 루프에서 호출되는 유일한 경로이고, ETF 가격을 씁니다.

## 2. 사후 검증/백테스트 스크립트 (오늘 Task1~4에서 사용)

| Module | Function | Source | Ticker | Cache | Update Time |
|---|---|---|---|---|---|
| `tools/backtest/label_market_regime_for_trades.py` | `HistoricalRegimeCalculator` | yfinance | `^KS11`, `^KQ11` (지수) | 없음(배치) | 실행 시점 1회 로드 |
| `tools/backtest/regime_extended_analysis.py` | `load_index_daily` | yfinance | `^KS11`, `^KQ11` (지수) | 없음 | 실행 시점 |
| `analysis/regime_block_simulator.py` (cron 매일 16:02) | - | yfinance | 미확인(그리드 매칭상 `^KS11/^KQ11` 계열로 추정) | 없음 | 매일 EOD |

## 3. Regime Gate와 무관한 그 외 시장 레짐/컨텍스트 모듈 (참고 — v1.4 게이트와 별개 시스템)

| Module | 용도 | Source | Ticker |
|---|---|---|---|
| `core/market_context.py`, `gpt_share/market_context.py` | Overnight/Market Context 판단 | Kiwoom | `069500`, `229200` |
| `core/regime_detector.py` | (별도 레거시 레짐 감지기로 추정) | Kiwoom | `229200` |
| `swing_runner.py` | 스윙 2단 레짐 판단 | yfinance | `^KS11` |
| `analyzers/volatility_regime.py`, `analyzers/relative_strength_filter.py` | 변동성/RS 필터 | yfinance | `^KS11`/`^KQ11` |
| `analysis/market_intelligence.py` | 모닝 브리핑 | yfinance | `^KS11`/`^KQ11` |
| `analysis/defensive_short_backtest.py` | 숏전략 백테스트 | yfinance | `069500.KS`/`229200.KQ` (Yahoo상 ETF, 지수 아님 — 세 번째 변형) |
| `api_server.py` | 대시보드 ADX 근사치 | yfinance | `^KS11` |

## 4. 핵심 발견

1. **실거래 v1.4 Regime Gate는 Kiwoom ETF 가격만 사용**하고, 오늘 검증에 쓴 백테스트 파이프라인은 **Yahoo 지수 원본만 사용** — 애초에 서로 다른 두 상품(ETF vs 지수)을 비교하는 구조. 07-13 사례처럼 "실거래 스냅샷과 사후 재계산이 다르다"는 현상은 Kiwoom-vs-Yahoo 소스 차이가 아니라 **같은 Kiwoom 소스 내에서 장중 스냅샷 시점(당일 미완성 봉) vs 사후 재계산(당일 종가 확정) 차이**임을 Task 1에서 직접 수치로 확인함(양쪽 소스 사후 재계산은 -6으로 일치).
2. 코드베이스 전체적으로 KOSPI/KOSDAQ 데이터소스가 최소 3가지 변형(Kiwoom ETF 코드, Yahoo 지수, Yahoo ETF 티커)으로 파편화되어 있음 — v1.4 게이트 자체의 정합성 문제는 아니지만, 향후 다른 모듈과 교차검증할 때 반드시 어떤 소스인지 먼저 확인 필요.
3. `RegimeAnalyzer.evaluate_for_datetime()`(yfinance 버전)이 클래스 내에 이미 존재하므로, 향후 실거래-백테스트 정합성을 높이려면 "백테스트도 Kiwoom ETF를 쓰게 통일" 또는 "실거래도 지수 원본을 쓰게 통일" 둘 중 하나를 검토할 수 있음 — 단, 이는 코드/정책 변경 사안이라 이번 read-only 분석 범위 밖이며 별도 승인 필요.
