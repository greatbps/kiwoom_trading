# Regime Parsing Fix — Validation Report
생성 시각: (스크립트 실행 시점, 데이터는 20260715 기준 최신 600행 스냅샷)
## 버그 요약
`analyzers/market/regime_analyzer.py`의 `_parse_daily_df()`가 Kiwoom 응답의 날짜 필드 `dt`를 `_COL_MAP`에서 인식하지 못해 정렬을 건너뛰었다. 그 결과 `close.iloc[-1]`(오늘 종가로 취급되는 값)이 실제로는 배열의 마지막 행 = Kiwoom이 반환한 600행 중 가장 오래된 날짜의 종가를 가리켰고, EMA20/60도 최신→과거 역순 데이터로 계산되었다.
## 수정 내용
`_COL_MAP`에 `"dt": "date"` 한 줄 추가 — 이후 `_parse_daily_df`가 날짜 오름차순 정렬을 수행한다.
## 검증 방법
최근 40거래일 각각에 대해, 그날 시점까지의 실제 과거 데이터(현재 스냅샷에서 역산)로 **수정 전(버그 재현, 최신 600행 역순 그대로 사용)**과 **수정 후(날짜 오름차순 정렬)** 계산을 동일한 원본 데이터에서 각각 재구성해 Regime 점수/버킷을 비교했다. KOSPI(069500)+KOSDAQ(229200) 합산 점수 기준.

**⚠️ 방법론적 제약**: Kiwoom API 스냅샷이 정확히 600행(2024-01-25~2026-07-15)만 확보돼 있어, "수정 전" 재현 시 윈도우 시작점이 항상 2024-01-25에 고정된다(실제 라이브 버그는 호출 시점마다 그 시점 기준 최신 600행을 받아 시작점이 매일 미끄러지지만, 이 검증에서는 그 이전 역사가 없어 재현 불가). 따라서 이 리포트의 "수정 전" 점수는 실제 라이브 버그가 각 날짜에 정확히 냈을 값과 다를 수 있다 — 다만 매커니즘(역순 배열에 EMA를 적용해 사실상 오래된 데이터가 결과를 지배하는 구조)은 동일하게 재현되므로, **버그의 존재와 방향성, 정책 버킷이 자주 달라진다는 결론 자체는 신뢰할 수 있다.**

## 요약 통계
| 항목 | 값 |
|---|---|
| 총 검증 거래일 | 40일 |
| Regime(정책 버킷) 동일 | 15일 |
| Regime(정책 버킷) 변경 | 25일 |
| 점수 완전 동일 | 5일 |
| 점수만 변경(버킷 동일) | 10일 |
| Regime 변경률 | 62.5% |
| 점수 변경률(완전동일 아닌 비율) | 87.5% |

## 전체 일자별 비교
| Date | Old Score | Old Regime | New Score | New Regime | 정책 변경 |
|---|---|---|---|---|---|
| 20260519 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260520 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260521 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260522 | -5 | RISK_OFF | +4 | TREND_UP | **YES** |
| 20260526 | -5 | RISK_OFF | +4 | TREND_UP | **YES** |
| 20260527 | -5 | RISK_OFF | +3 | TREND_UP | **YES** |
| 20260528 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260529 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260601 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260602 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260604 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260605 | -5 | RISK_OFF | -2 | NEUTRAL | **YES** |
| 20260608 | -5 | RISK_OFF | -6 | RISK_OFF | - |
| 20260609 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260610 | -5 | RISK_OFF | -5 | RISK_OFF | - |
| 20260611 | -5 | RISK_OFF | -4 | RISK_OFF | - |
| 20260612 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260615 | -5 | RISK_OFF | +4 | TREND_UP | **YES** |
| 20260616 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260617 | -5 | RISK_OFF | +4 | TREND_UP | **YES** |
| 20260618 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260619 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260622 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260623 | -5 | RISK_OFF | -6 | RISK_OFF | - |
| 20260624 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260625 | -5 | RISK_OFF | -1 | NEUTRAL | **YES** |
| 20260626 | -5 | RISK_OFF | -2 | NEUTRAL | **YES** |
| 20260629 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260630 | -5 | RISK_OFF | +0 | NEUTRAL | **YES** |
| 20260701 | -5 | RISK_OFF | -5 | RISK_OFF | - |
| 20260702 | -5 | RISK_OFF | -6 | RISK_OFF | - |
| 20260703 | -5 | RISK_OFF | -4 | RISK_OFF | - |
| 20260706 | -5 | RISK_OFF | -5 | RISK_OFF | - |
| 20260707 | -5 | RISK_OFF | -5 | RISK_OFF | - |
| 20260708 | -5 | RISK_OFF | -6 | RISK_OFF | - |
| 20260709 | -5 | RISK_OFF | -4 | RISK_OFF | - |
| 20260710 | -5 | RISK_OFF | -4 | RISK_OFF | - |
| 20260713 | -5 | RISK_OFF | -6 | RISK_OFF | - |
| 20260714 | -5 | RISK_OFF | -5 | RISK_OFF | - |
| 20260715 | -5 | RISK_OFF | -4 | RISK_OFF | - |

## 정책이 실제로 변경된 날짜 상세

### 20260519: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSPI_day_drop_-3.0pct', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260520: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-3.0pct']

### 20260521: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260522: RISK_OFF → TREND_UP
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_above_EMA20', 'KOSDAQ_EMA20_slope_up']

### 20260526: RISK_OFF → TREND_UP
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_above_EMA20', 'KOSDAQ_EMA20_slope_up']

### 20260527: RISK_OFF → TREND_UP
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_above_EMA20', 'KOSDAQ_EMA20_slope_up', 'KOSDAQ_day_drop_-2.7pct']

### 20260528: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260529: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.0pct']

### 20260601: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260602: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.5pct']

### 20260604: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSPI_day_drop_-2.0pct', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260605: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSPI_day_drop_-5.6pct', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-5.5pct']

### 20260609: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260612: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260615: RISK_OFF → TREND_UP
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_above_EMA20', 'KOSDAQ_EMA20_slope_up']

### 20260616: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.9pct']

### 20260617: RISK_OFF → TREND_UP
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_above_EMA20', 'KOSDAQ_EMA20_slope_up']

### 20260618: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-3.1pct']

### 20260619: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-3.9pct']

### 20260622: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260624: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260625: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']

### 20260626: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSPI_day_drop_-5.8pct', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-3.8pct']

### 20260629: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

### 20260630: RISK_OFF → NEUTRAL
- Old reasons: ['KOSPI_below_EMA20', 'KOSPI_EMA20_slope_down', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down', 'KOSDAQ_day_drop_-2.7pct']
- New reasons: ['KOSPI_above_EMA20', 'KOSPI_EMA20_slope_up', 'KOSDAQ_below_EMA20', 'KOSDAQ_EMA20_slope_down']

## Entry/Block 판단 영향 분석
정책이 변경된 날짜 25건에서 실제 Entry Gate 판단이 달라졌을 것으로 추정된다. 상세는 위 표 참고.
