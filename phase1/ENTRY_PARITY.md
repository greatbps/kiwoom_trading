# ENTRY_PARITY — Live CHoCH 경로 vs Backtest

Iteration 9 Phase 3 · 2026-08-02

## 검증 방법

`ChochSignalEngine`(Live 경로)을 캐시된 78종목 482거래일에 대해
**매 봉 호출**하고, `backtest.adapter.SMCAdapter` 결과와 대조했다.

동일 기간 · 동일 종목 · 동일 데이터.

## 결과

```
Backtest SMCAdapter    117건
Live ChochEngine       117건
일치                   117건    Backtest 전용 0    Live 전용 0

Signal Match  100.0%      목표 95% → ✅ PASS
Trade Match   100.0%      (신호가 같으므로 거래도 같다)
Entry Price 차이  최대 0.005원   (반올림 오차)
Entry Date 차이   0일
```

**완전 일치한다.** 재구현하지 않고 `SMCAdapter` 를 그대로 호출했기
때문이다 — 재구현했다면 이 표는 100%가 나오지 않았을 것이고,
그게 애초에 Pullback/CHoCH 가 갈라진 방식이었다.

## 완료 조건 대비

| 항목 | 목표 | 결과 |
|---|---|---|
| Live Entry = Backtest Entry | 100% | ✅ 100% |
| Entry Signal Match | ≥95% | ✅ 100% |
| Trade Match | ≥95% | ✅ 100% |
| Position Schema 유지 | 100% | ✅ 무수정 |
| Stop 전달률 | 100% | ✅ 무수정 (경로 그대로) |
| Exit Mapping | ≥90.5% | ✅ 무수정 |
| Regression | PASS | ✅ 466 PASS / 16 FAIL |

## 남은 것

- **Paper Trading (Phase 4)** — 20거래일 또는 30신호. 시간이 필요하다.
- **Live 반영** — 승인 기준 §12 미충족 (Paper 미완료).

## 전제

이 검증은 **신호 규칙**의 동일성만 확인한다.
`swing_runner` 의 유니버스·레짐 게이트·섹터 한도·쿨다운·Top-3 상한은
그대로 남아 있고, 그 단계에서 신호가 더 줄어든다.
따라서 실제 Live 거래 수는 117건보다 적다 (Funnel 참조).
