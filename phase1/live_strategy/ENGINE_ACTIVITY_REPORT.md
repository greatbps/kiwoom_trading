# ENGINE_ACTIVITY_REPORT

Iteration 6-4 · 근거: 코드 grep · DB · 로그

| 엔진 | 분류 | 근거 |
|---|---|---|
| **`swing`** (swing_runner + swing_executor) | **ACTIVE** | 최근 BUY 2026-07-01. 최근 90일 5건. 크론 등록 확인 (`35 15` · `0 9`) |
| `exploration` | **INACTIVE** | 최근 BUY 2026-06-09 (90일 내 2건). 코드는 `main:7295` 에서 호출되나 이후 매수 0 |
| `smc` | **INACTIVE** | 최근 BUY 2026-02-25. 로그(`smc_decision`)는 최근 30일 60건 — **호출되나 BUY 없음** |
| `experiment` | **LEGACY** | BUY 5건, 2026-03-26 이후 0 |
| `order_executor` (VWAP+AI) | **DEAD** | `main_auto_trading` 에서 호출 **0건** (grep). BUY 96건은 2026-01-16 이 마지막 |
| `squeeze_orderbook` | **확인 불가** | 코드 호출 `main:6130` 존재. 로그 태그 0건 · BUY 0건. **태그 부재인지 미실행인지 구분 불가** |
| `ma_cross` | **확인 불가** | 코드 호출 `main:6233` 존재. 로그·BUY 0건. 위와 동일 |
| `two_tf` | **확인 불가** | 코드 호출 `main:6308` 존재. 로그·BUY 0건. 위와 동일 |
| `pending_signal` (`_flush_pending_signals`) | **확인 불가** | 코드 호출 `main:4801` 존재. `_emit_signal` 로그 태그 0건 |

## 분류 기준

- **ACTIVE** — 최근 90일 BUY 발생
- **INACTIVE** — 호출 경로 존재, 최근 90일 BUY 0 또는 극소
- **DEAD** — 호출 0건 (grep 확인)
- **LEGACY** — 과거 BUY 있었으나 3개월 이상 0
- **확인 불가** — 코드 호출은 있으나 로그 태그가 없어 실행 여부 판정 불가

⚠️ **4개 엔진이 "확인 불가" 다.** 로그 태그로만 판정했기 때문이다.
실행 여부를 확정하려면 계측 추가가 필요한데, 이번 Iteration 은
Logging 추가를 금지한다.
