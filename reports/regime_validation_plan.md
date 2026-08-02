# Regime Gate Threshold 변경 조건 명문화

> 생성일: 2026-07-27 | 이 문서는 threshold를 지금 바꾸는 것이 아니라, **앞으로 언제 재검토할 수 있는지**를 미리 명문화한다.

## 현재 정책

`config/strategy_hybrid.yaml` — `market_regime.score_thresholds.risk_off_max = -3` (변경하지 않음)

## Threshold 변경 검토를 시작할 수 있는 조건 (아래 전부 충족 시에만)

| # | 조건 | 현재 상태 | 충족 여부 |
|---|---|---|---|
| 1 | 실거래(PASS→체결) 50건 이상 누적 | `research.decision_ledger` decision='PASS' 카운트 | 0건 → 미충족 |
| 2 | REGIME_BLOCK 실사례 100건 이상 누적 | `research.decision_ledger` decision_reason_code='REGIME_BLOCKED' 카운트 | (변동 중, `analysis.regime_daily_report`로 매일 확인) → 미충족 |
| 3 | Opportunity Loss가 증가 추세 | `analysis.regime_evidence_weekly`의 Opportunity Cost 추이 | 현재 Phase1/2 분석상 오히려 손실회피 방향(음의 Opportunity Cost) → 미충족 |
| 4 | Gate Precision 하락 추세 | `analysis.regime_evidence_weekly`의 Precision | 현재 100%(표본 5건, 통계적으로 유의미하지 않은 수준) |
| 5 | False Negative 증가 | 동일 리포트의 FN 카운트 | PASS 표본 없어 측정 불가 |
| 6 | Profit Factor 악화 | `analysis.regime_daily_report`의 실거래 PF | 실거래 표본 없어 측정 불가 |

**판정 규칙**: 위 6개 조건이 **모두** 충족되어야 재검토를 시작한다. 하나라도 미충족이면 현재 정책을 유지한다. (1)(2)는 정량적 표본 조건, (3)(4)(5)(6)은 방향성 악화 조건 — 표본이 쌓여도 방향이 "게이트가 여전히 옳다"면 재검토 사유가 되지 않는다.

## 왜 이 임계값인가

- 50건/100건은 이 프로젝트의 기존 Evidence Level 관례(`analysis/eq_shadow_report.py` 등에서 이미 사용 중인 최소 표본 기준, `analysis/performance_metrics.py`의 `passes_threshold(min_trades=100)` 관례)와 일치시켰다 — 새로운 임의 숫자를 만들지 않았다.
- Opportunity Loss/Precision/Recall/PF 4개 지표를 동시에 요구하는 이유: 표본이 쌓여도 단일 지표만으로 정책을 바꾸면 우연/노이즈에 흔들릴 수 있다. 여러 지표가 동시에 같은 방향(게이트가 손해를 끼치고 있다)을 가리켜야 재검토를 시작한다.

## 확인 방법 (자동화됨, 수동 판단 불필요)

```
python3 -m analysis.regime_daily_report        # 매일 EOD 자동 실행 (cron)
python3 -m analysis.regime_evidence_weekly      # 매주 금요일 자동 실행 (cron)
```

두 리포트의 "Evidence Level" 섹션이 **E3**에 도달하고, 위 6개 조건 표가 전부 "충족"으로 바뀌는 시점에만 사람이 재검토 회의를 소집한다.
