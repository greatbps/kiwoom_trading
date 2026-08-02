# 늦은 진입 수정안 백테스트 리포트
**생성일**: 2026-07-04
**분석 거래**: 124건 (LOSS+EF+WIN)

## Baseline
- 승률 21.8% | 평균손익 -0.946% | PF 0.239 | EF 32.3%

## Part 1 — 후보 선정 지연 수정안 비교

| 수정안 | 평균앞당김 | 진입가개선률 | 승률 | 평균손익 | PF | EF% | 난이도 |
|---|---|---|---|---|---|---|---|
| Fix1-A: t0 직진입 | ~45분 단축 | -0.40% | 40.4% (+18.6%p) | -0.196% (+0.750%p) | 0.778 (+0.539) | 38.3% (+6.0%p) | 중간 |
| Fix1-B: t0+10분 (루프주기 단축) | ~10분 단축 | -0.22% | 52.6% (+30.8%p) | -0.011% (+0.935%p) | 0.982 (+0.743) | 21.1% (-11.2%p) | 낮음 |
| Fix1-C: t0+20분 (Alpha 임계 완화) | ~20분 단축 | -0.37% | 50.0% (+28.2%p) | +0.133% (+1.079%p) | 1.223 (+0.984) | 22.2% (-10.1%p) | 낮음 |

## Part 2 — SMC/CHoCH 진입 지연 수정안 비교

| 수정안 | 평균앞당김 | 진입가개선률 | 승률 | 평균손익 | PF | EF% | 난이도 |
|---|---|---|---|---|---|---|---|
| Fix2-A: Reclaim 제거 (t2-5분) | ~5분 단축 | -0.08% | 54.5% (+32.7%p) | -0.084% (+0.862%p) | 0.861 (+0.622) | 22.7% (-9.6%p) | 중간 |
| Fix2-B: Displacement 완화 (t2-10분) | ~10분 단축 | -0.22% | 52.6% (+30.8%p) | -0.011% (+0.935%p) | 0.982 (+0.743) | 21.1% (-11.2%p) | 낮음 |
| Fix2-C: RVOL 게이트 해제 (t2-3분) | ~3분 단축 | -0.05% | 54.5% (+32.7%p) | -0.118% (+0.828%p) | 0.809 (+0.570) | 22.7% (-9.6%p) | 낮음 |

## 수정안 상세

### Fix1-A: t0 직진입
Orchestrator ACCEPT 시점(t0)에 SMC CHoCH 대기 없이 즉시 매수.
병목 제거: L6 Validator hard gate + Alpha threshold 우회 + reclaim/displacement 생략.
진입 신호: Orchestrator ACCEPT 단독.
위험: CHoCH 미확인으로 허위 신호 통과 증가 가능.
- **적용 대상**: 47건 / 전체 124건
- **실전 반영**: execute_buy() 내 SMC 검증 skip 분기 추가. 실거래 반영 전 Shadow 필수.

### Fix1-B: t0+10분 (루프주기 단축)
5분 루프 → 실시간 이벤트 트리거로 전환해 평균 10분 단축.
병목 제거: 5분 고정 폴링 제거 (L6/Alpha 대기 시간 단축).
구현: ACCEPT 이벤트 발생 시 즉시 SMC 체크 트리거.
위험: 낮음. 기존 SMC 진입 조건 유지.
- **적용 대상**: 19건 / 전체 124건
- **실전 반영**: signal_orchestrator에 ACCEPT 콜백 추가. 루프 주기 변경 없이 이벤트 드리븐 전환.

### Fix1-C: t0+20분 (Alpha 임계 완화)
Alpha threshold 0.8→0.5 완화 + L6 min_win_rate 40→30% 완화.
효과: 더 많은 종목이 t0에서 ACCEPT → 후속 SMC 대기 시간 단축.
병목 완화: Alpha 차단 비율 감소 → ACCEPT 빈도 증가 → 평균 20분 단축 추정.
위험: 낮은 품질 후보 일부 통과. YAML 수정만으로 적용 가능.
- **적용 대상**: 18건 / 전체 124건
- **실전 반영**: YAML: swing.alpha_threshold: 0.5 + L6 min_win_rate: 30. 코드 수정 불필요.

### Fix2-A: Reclaim 제거 (t2-5분)
require_reclaim: false — CHoCH 확인 후 reclaim candle 대기 생략.
병목 제거: reclaim_lookback=5분 대기 → 즉시 진입.
조건: CHoCH + HTF trend만으로 통과 (현재 min_conditions=1 이미 완화됨).
위험: CHoCH 직후 되돌림 미확인 → MAE 증가 가능.
- **적용 대상**: 22건 / 전체 124건
- **실전 반영**: YAML: entry_prefilter.require_reclaim: false. 코드 수정 불필요.

### Fix2-B: Displacement 완화 (t2-10분)
atr_multiplier 1.2→0.6, body_ratio_min 0.5→0.3 완화.
병목 제거: 강한 displacement 봉 대기 없이 CHoCH 즉시 유효.
효과: displacement 기준 충족 대기 시간(평균 10분) 단축.
위험: 약한 displacement(작은 봉)에서도 진입 → 추세 지속성 약화 가능.
- **적용 대상**: 19건 / 전체 124건
- **실전 반영**: YAML: displacement_filter.atr_multiplier: 0.6, body_ratio_min: 0.3.

### Fix2-C: RVOL 게이트 해제 (t2-3분)
rvol_min 1.3→0.0 (비활성화) — 거래량 하드 게이트 제거.
병목 제거: RVOL 1.3x 충족 대기 시간 → 즉시 통과.
효과: 거래량 폭발 직전 더 빠른 진입 가능, 약 3분 단축.
위험: 저거래량 CHoCH 통과 → 슬리피지 증가 가능.
- **적용 대상**: 22건 / 전체 124건
- **실전 반영**: YAML: entry_prefilter.rvol_min: 0.0. 코드 수정 불필요.

---
*생성: 2026-07-04 by backtest_early_entry.py*