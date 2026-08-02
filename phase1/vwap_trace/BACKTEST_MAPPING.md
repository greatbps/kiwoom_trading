# BACKTEST_MAPPING — 운영 ↔ 백테스트 1:1 대응

Iteration 6-2

## 매핑 가능

| 운영 코드 | 백테스트 입력 | 비고 |
|---|---|---|
| 5분봉 `open_pric` | `open` | 부호 접두 제거 필요 |
| 5분봉 `high_pric` | `high` | 〃 |
| 5분봉 `low_pric` | `low` | 〃 |
| 5분봉 `cur_prc` | `close` | 〃 |
| 5분봉 `trde_qty` | `volume` | |
| `acc_trde_qty` | `acc_volume` | 누적 — 봉별 아님 |
| `cntr_tm` | `datetime` (Asia/Seoul) | UTC 변환 금지 |
| `calculate_vwap(use_rolling=True, window=20)` | **동일 함수 호출** | 재구현 금지 |
| `RISK_PER_TRADE` 사이징 | 동일 공식 | `risk_manager.py:301` |
| 조건검색 종목 | Iter6-1 데이터셋 112종목 | ⚠️ 아래 참조 |

## 매핑 불가능 — ❌

| 운영 코드 | 사유 |
|---|---|
| **VWAP 돌파 판정** | 코드에 4종 공존. 어느 것이 쓰였는지 불명 |
| **`analysis['total_score']`** | 산출 함수 미특정. 값 자체가 없어 0.0 이 찍힘 |
| **AI Score Threshold** | 위와 동일 |
| **조건검색 실시간 매칭** | 과거 시점의 조건검색 결과를 재현할 수 없다. 조건식 정의가 키움 서버에 있고 API 로 내려받을 수 없다 |
| **`stats` / `analysis` 딕셔너리** | `order_executor` 가 `stock_info` 에서 받는데 생성처 미확인 |
| **RS 필터 (L2)** | `signal_orchestrator.check_l2_rs_filter` — 시장 전체 대비 상대강도. 과거 시점 재현 시 전 종목 데이터 필요 |
| **호가창 조건** | 틱/호가 데이터 없음 |

## ⚠️ 유니버스 매핑의 한계

Iteration 6-1 데이터셋 112종목은 **실제로 거래된 종목**이다.
운영은 조건검색이 **매 스캔마다 다른 종목 집합**을 내놓는다.

백테스트에서 "그날 조건검색이 무엇을 뽑았는지" 를 재현할 수 없으므로,
**112종목 전체를 매일 후보로 두는 것과 실제 운영은 다르다.**

이건 CHoCH 백테스트에서 겪은 것과 같은 종류의 괴리다.
