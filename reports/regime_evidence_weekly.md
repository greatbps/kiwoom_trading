# Regime Evidence Weekly — 2026-07-21 ~ 2026-07-31

## 표본

- REGIME_BLOCK 시뮬레이션 표본(기간 내): 25건
- 누적(전체 기간): 실거래 0건 / REGIME_BLOCK 340건 / 시뮬레이션 25건

## Confusion Matrix (REGIME_BLOCK 사후 시뮬레이션 + PASS 실거래 결과)

| | 실제 손실 | 실제 이익 |
|---|---:|---:|
| Gate가 차단(BLOCK) | TP=20 | FP=5 |
| Gate가 통과(PASS) | FN=0 | TN=0 |

(FLAT 무승부 0건은 분모에서 제외)

- **Gate Precision**: 80.0% — 차단한 것 중 진짜 손실이었던 비율
- **Gate Recall**: N/A (PASS 실거래 표본 없음) — 손실이었던 것 중 게이트가 막은 비율

## Opportunity Loss / Avoided Loss (기간 합산)

- Opportunity Cost(놓친 수익 합, WIN이었을 것들의 1일 수익률 합): +0.00%
- Avoided Loss(막은 손실 합, LOSS였을 것들의 실현손익 합): -19.05%

## Evidence Level

**E1** — 사후 시뮬레이션 25건 (30건 미만)

판정 기준: E3: 실거래 50건+ AND REGIME_BLOCK 100건+ | E2: 시뮬레이션 30건+ 또는 독립소스 재검증 완료 | E1: 시뮬레이션 <30건 | E0: 표본 없음

상세 threshold 변경 조건은 `reports/regime_validation_plan.md` 참조.