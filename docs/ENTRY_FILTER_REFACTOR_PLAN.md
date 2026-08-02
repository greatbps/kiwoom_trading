# 진입 필터 교정안

> **문서 목적**: 진입 신호 품질 분석 + CHoCH 등급 재교정 결과를 바탕으로, 실제 어떤 필터를 어떻게 바꿀지 결정하는 개발 상위 설계 문서  
> **관련 문서**: `ENTRY_SIGNAL_QUALITY_REPORT.md`, `CHOCH_GRADE_RECALIBRATION_REPORT.md`  
> **최종 갱신**: 2026-07-05 | ruleset_version v1.3.1-final

---

## ⚠️ 교정 원칙

이 문서의 모든 판단은 다음 3개 원칙을 따른다:

**원칙 A**: 수익 거래(ALPHA_EXIT)와 손실 거래(RISK/EXPERIMENT)를 분리하는 특징을 우선 반영한다.

**원칙 B**: 단순 차단식 필터 추가보다 등급 재가중치, size 축소, route 제한, 시간대 제한 등 **차등 대응**을 우선한다.

**원칙 C**: time_exit/EF 비율이 높으면 "손절 부족"이 아니라 "진입 edge 부족" 문제로 본다.

---

## 1. 분석 기반 — 핵심 발견 요약

| 발견 | 데이터 | 함의 |
|------|--------|------|
| ALPHA_EXIT율 16.8% | 19/113건 | 6건 중 1건만 수익 구조 진입 |
| LOSS_EF율 49.6% | 56/113건 | 절반이 즉각 역방향 |
| SMC_A 2026-01 WR=100% | 4건 | 강세장 유효 |
| SMC_A 2026-02 WR=9% | 11건 | 하락장 완전 붕괴 |
| SMC_B WR=0% | 4건 | B급 진입 가치 없음 |
| 09:00~10:00 ALPHA=0% | 8건 | 장 개장 직후 진입 구조적 실패 |
| 10:30~13:00 ALPHA=21.5% | 65건 | 최선 구간 |

**핵심 원인**: 시장 레짐 대응 없이 진입 신호를 그대로 사용

---

## 2. 필터별 판정표

| 필터 | 현재 규칙 | 문제 | 변경안 | 기대 효과 | 위험 |
|------|-----------|------|--------|-----------|------|
| **시장 레짐 게이트** | 없음 (market_context 있으나 진입 차단 미연결) | 하락장에서 모든 신호가 무의미 | BEAR 레짐 → 진입 차단 or size×0.2 | 2026-02 같은 하락기 손실 방지 | 일부 반등 기회 놓침 |
| **CHoCH min_grade** | B급 이상 허용 | B급 WR=0%, PF=0 | **A급만 허용** (min_choch_grade: 'A') | B급 4건 제거, WR 소폭 개선 | 진입 건수 감소 |
| **09:00~10:00 진입** | c_late_v2로 일부 필터링, 개장 초반은 열려 있음 | ALPHA=0%, EF=75% | 09:00~09:30 진입 차단 (swing 전용) | 구조적 실패 구간 제거 | 일부 OPEN 기회 놓침 |
| **G3 soft penalty** | bdh<3% → size×0.6 | 효과 미측정 (tag 없음) | 유지 + 효과 측정 시작 | — | — |
| **Stage A gate** | bdh>15% → 차단 | 발동 건수 미측정 | 유지 + 발동 건수 측정 | — | — |
| **c_late_v2** | 10:30~13:00 추가 조건 | 이 구간이 최선 성과(ALPHA=21.5%) | 유지 (조정 불필요) | — | — |
| **Prefilter 2-of-3** | HTF+Sweep+Reclaim 중 2개 | 시장 레짐 포함 안 됨 | 유지 + 레짐 게이트 별도 추가 | — | 변경 과도 |
| **RVOL hard gate** | RVOL bonus (soft) | hard gate 없음 | **RVOL < 0.7 → 차단 권고** (검토 단계) | 저거래량 실패 진입 제거 | 데이터 부족으로 즉시 적용 보류 |
| **EDT filter** | 조기 하락 추세 차단 | 이미 구현됨 | 유지 | — | — |
| **HTF trend check** | Prefilter 조건 1 | 이미 구현됨 | 유지 | — | — |
| **Reclaim requirement** | Prefilter 조건 3 | 이미 구현됨 | 유지 | — | — |
| **Sweep requirement** | Prefilter 조건 2 | 이미 구현됨 | 유지 | — | — |
| **CHoCH grade cut** | C급 차단 | 이미 구현됨 | B급도 차단으로 강화 | B급 WR=0% 제거 | 진입 건수 감소 |

---

## 3. 교정 우선순위

### Priority 1 — 즉시 적용 (YAML만, 코드 수정 없음)

**변경 내용**: `min_choch_grade: 'B'` → `min_choch_grade: 'A'`

```yaml
# config/strategy_hybrid.yaml
swing:
  entry:
    min_choch_grade: 'A'   # B → A
```

- 영향: B급 진입 차단 (전체 113건 중 4건, 3.5%)
- 기대: WR +1~2%p, avg_pnl +0.1%p
- 리스크: A급도 하락장에서 실패 → 레짐 게이트가 더 중요
- **즉시 적용 가능**

---

### Priority 2 — 단기 조치 (코드 수정 필요, ~1주 이내)

**변경 내용**: 09:00~09:30 SMC 진입 차단

**구현 방법**: `signal_orchestrator.py`의 시간대 필터에 09:00~09:30 구간 REJECT 추가

```python
# signal_orchestrator.py 또는 main_auto_trading.py
if entry_mode == 'smc' and 0 <= entry_min < 9*60+30:
    return False, "09:00~09:30 SMC 진입 차단 (ALPHA=0% 구간)"
```

- 영향: 09:00~10:00에서 09:00~09:30 서브구간 차단 (정확한 건수 미측정)
- 기대: 최악 구간(ALPHA=0%, EF=75%) 일부 제거
- 리스크: 가끔 나오는 강한 갭 기회 놓침
- **주의**: CLAUDE.md 체크리스트 6개 항목 사전 확인 필요

---

### Priority 3 — 중기 조치 (설계 + 코드, 1~2주)

**변경 내용**: 시장 레짐 게이트 추가

**설계**:

```
swing_runner.py (15:35 실행, 다음날 후보 선정 시):
  1. KOSPI/KOSDAQ 당일 종가 기준 MA5 vs MA20 계산
  2. BEAR 판정 시 → next_day_regime = 'BEAR'
  3. next_day_regime을 swing_orders_*.json에 저장

swing_executor.py (09:00 실행, 매수 시):
  1. swing_orders_*.json에서 next_day_regime 읽기
  2. BEAR → min_score 상향 또는 진입 차단
```

**레짐 판단 기준** (단순 시작, 이후 개선):

| 레짐 | 조건 | 진입 처리 |
|------|------|-----------|
| BULL | KOSPI MA5 > MA20 AND 최근 3일 양봉 | 정상 진입 |
| SIDEWAYS | MA5 ≈ MA20 (±0.5%) | size × 0.7 |
| BEAR | KOSPI MA5 < MA20 AND 최근 3일 내 -2% 이상 | 진입 차단 |

**구현 파일**:
- `swing_runner.py`: 레짐 계산 + json 저장
- `swing_executor.py`: json 읽기 + 조건 적용
- `config/strategy_hybrid.yaml`: `market_regime_gate.enabled: true`

---

### Priority 4 — 데이터 축적 후 검토 (1개월 이상)

**RVOL Hard Gate**:
- 현재 RVOL은 CHoCH 점수 보너스로만 사용
- 데이터 30건 이상 + RVOL별 성과 분석 후 임계값 설정
- 현재: 즉시 적용 보류

**Prefilter 3-of-3 강화**:
- 현재 2-of-3 (HTF+Sweep+Reclaim 중 2개)
- 3-of-3으로 강화하면 진입 건수가 크게 줄어 E2 달성이 더 늦어짐
- 레짐 게이트 효과 확인 후 검토

---

## 4. 교정 전후 예상 변화

| 지표 | 현재 (113건) | Priority 1 후 예상 | Priority 2+3 후 예상 |
|------|-------------|------------------|---------------------|
| WR | 23.9% | ~25% | ~30%+ |
| PF | 0.291 | ~0.35 | ~0.60+ |
| avg_pnl | -0.769% | -0.65% | -0.3%+ |
| ALPHA율 | 16.8% | ~18% | ~22%+ |
| 진입 건수/월 | 현재 수준 | 소폭 감소 | 감소 (레짐 차단) |

※ 예상치는 현재 데이터 기반 추정. 실제 적용 후 재측정 필요.

---

## 5. 변경하지 않을 것

| 항목 | 이유 |
|------|------|
| EF (Early Failure Structure) | 핵심 손실 방지 레이어. 손대지 않음 |
| Hard Stop (-2.0%) | 최후 안전망. 손대지 않음 |
| ATR Trailing Stop | 유일한 수익 엔진. 손대지 않음 |
| DrawdownEngine | 연속 손실 자동 방어. 손대지 않음 |
| RAE | 현재 Primary PF<1.0이므로 활성화 금지 |
| LCL v2.1 | EF와 함께 안전망. 현재 유지 |

---

## 6. 교정 완료 기준

다음 지표를 30건 이상 거래 후 확인:

| 지표 | 현재 | 목표 |
|------|------|------|
| WR | 23.9% | ≥ 30% |
| PF | 0.291 | ≥ 0.80 |
| ALPHA율 | 16.8% | ≥ 22% |
| LOSS_EF율 | 49.6% | ≤ 35% |
| 09:00~09:30 ALPHA율 | 0% | — (차단) |

목표 미달 시 → CHOCH_GRADE_RECALIBRATION_REPORT 재검토 후 다음 단계 교정.

---

## 7. 즉시 실행 요약

### 지금 할 수 있는 것 (운영자 결정)

```bash
# YAML 수정 (B급 차단)
grep "min_choch_grade" config/strategy_hybrid.yaml
# 현재 값 확인 후 'B' → 'A' 변경

# 검증
python3 -m py_compile analyzers/smc/smc_signals.py
```

### 다음 1주 이내 (개발자 작업)

1. `signal_orchestrator.py` 또는 `main_auto_trading.py`: 09:00~09:30 시간대 차단 추가
2. 변경 후 acceptance_test 재실행: `python3 -m analysis.acceptance_test`

### 다음 2주 이내 (설계 후 구현)

시장 레짐 게이트 설계 → Governance AI 검토 (GD 필요) → 구현

**Governance Decision 필요 이유**: 레짐 게이트는 진입 차단 정책의 근본적 변경이다. GD 없이 구현 금지.
