# Regime Gate 운영 Dashboard — 스펙 문서

> 생성일: 2026-07-27 | **이 문서는 스펙(설계)만 다룬다. 실제 trading-os 웹 프론트엔드 구현은 이번 작업 범위 밖이며 별도 작업으로 진행해야 한다.**

## 목적

운영자가 5초 안에 "지금 Regime Gate가 뭘 하고 있는지"를 파악할 수 있어야 한다.

## 위젯 레이아웃 (와이어프레임, 텍스트)

```
┌─────────────────────────────────────────────────────────┐
│  REGIME GATE STATUS                    Evidence: [E1]    │
├─────────────────────────────────────────────────────────┤
│  Regime: RISK_OFF          Score: -4                     │
│                                                            │
│   KOSPI   6,647  (EMA20 7,245.9  ↓)                       │
│   KOSDAQ    767  (EMA20   818.1  ↓)                       │
├─────────────────────────────────────────────────────────┤
│  오늘  Block: 10   Pass: 0   Candidates: 10               │
├─────────────────────────────────────────────────────────┤
│  Opportunity Loss (오늘)                                   │
│  Net Policy Value: +3.82%  [REGIME EFFECTIVE]              │
└─────────────────────────────────────────────────────────┘
```

## 데이터 소스 매핑

| 위젯 항목 | 데이터 소스 | 갱신 주기 |
|---|---|---|
| Regime / Score | `research.decision_ledger.feature_snapshot->>'regime'`, `->>'regime_score'` (오늘 최신 1건) | 실시간(신규 REGIME_BLOCK/PASS 시마다) — API 엔드포인트가 최신 행 조회 |
| KOSPI/KOSDAQ 종가·EMA20 | 동일 feature_snapshot의 `kospi_close`/`kospi_ema20`/`kosdaq_close`/`kosdaq_ema20` | 상동 |
| Block/Pass/Candidates | `analysis.gate_funnel._query_funnel()` 쿼리를 API 엔드포인트로 노출 | 폴링(예: 1분) |
| Opportunity Loss | `analysis/data/regime_block_detail.csv` 당일분 + `analysis.regime_block_simulator.run_simulation()` 결과 | EOD 배치 갱신(장중엔 전날 값 표시) |
| Evidence Status | `analysis/regime_evidence_common.classify_evidence_level()` | EOD 배치 갱신 |

## 구현 시 고려사항 (별도 작업)

1. `api_server.py`에 위 데이터소스를 조회하는 신규 엔드포인트 추가 필요(예: `GET /api/regime/status`) — 기존 `api_server.py:1923` 부근의 `risk_log.json` 조회 패턴과 유사하게, 여기선 Postgres 조회로 구현
2. 프론트엔드(trading-os) 쪽에 새 위젯 컴포넌트 추가 필요 — 기존 대시보드 레이아웃 확인 후 배치 위치 결정
3. 실시간성이 필요한 항목(Regime/Score)과 배치성 항목(Opportunity Loss)을 같은 갱신주기로 묶지 말 것 — 위 표의 "갱신 주기" 컬럼 참고
4. 이번 스펙은 코드/신규 테이블 없이 기존 `decision_ledger`(Task1) + CSV(Task2) 데이터만으로 완전히 그릴 수 있음 — 추가 데이터 파이프라인 불필요
