# EXP-002: RANGING 조건부 재허용

작성일: 2026-05-28  
상태: 계획 수립 / EXP-001 측정 완료 후 실행  
선행 조건: EXP-001 3거래일 측정 완료 (2026-05-29 리뷰 후)  
담당: detect_choch() + smc_diag 로그

---

## 1. 배경 — EXP-001이 밝힌 것

EXP-001(lb=10 rollback)로 확인된 사실:

```
swing_count (lb=10): avg ~4개  ← lb=20(0.5) 대비 8배 회복
structure_trend 분포 (2026-05-27 기준):
  ranging  : 5/6 종목 (83%)
  bearish  : 1/6 종목 (17%)
  bullish  : 0/6 종목 (0%)

CHoCH = 0건  (bearish 1종목도 LH 미돌파로 정상 차단)
```

→ lb=10 이후 swing collapse는 해결됨  
→ 병목이 `swing extraction` → `RANGING suppression`으로 이동 확인  
→ **EXP-001 시나리오 C 확정**: "swing_count 증가 but CHoCH=0 → RANGING이 진짜 원인"

---

## 2. RANGING 제거 이력

| 날짜 | 변경 | 이유 |
|------|------|------|
| 2026-03-07 | `detect_choch()`: `trend in [BEARISH, RANGING]` → `trend in [BEARISH]` | 추정: ranging 시장 false positive 방지 |

**문제**: ranging 제거 이후 시장의 83%가 ranging 분류 → 사실상 CHoCH 평가 자체 불가

---

## 3. 가설

> RANGING 제거(2026-03-07)가 **과도한 suppression**이었다.
>
> ranging 시장에서도 국지적 LH 돌파(reversal 신호)는 발생하며,  
> 이를 전면 차단하면 유효한 setup을 놓친다.

**단, 무조건 재허용은 아님** — ranging에서 모든 breakout이 reversal은 아니다.  
따라서 조건부 재허용이 필요하다.

---

## 4. 수정 방향 (후보)

### 방안 A: RANGING 단순 재허용 (위험)
```python
# detect_choch()
if structure.trend in [MarketTrend.BEARISH, MarketTrend.RANGING]:
    ...
```
- 장점: 단순, 즉시 적용
- 단점: ranging에서 false positive 급증 가능 (원래 제거한 이유)
- 결론: **사용 안 함**

### 방안 B: 거래량 조건부 RANGING (권장)
```python
# detect_choch()
if structure.trend == MarketTrend.BEARISH:
    pass  # 기존
elif structure.trend == MarketTrend.RANGING:
    _rvol = details.get('rvol', 0)
    if _rvol < ranging_rvol_threshold:  # default 1.5
        return None  # 거래량 부족 ranging → 차단 유지
```
- 장점: ranging + volume spike = 실질적 reversal 신호
- 단점: rvol threshold 추가 튜닝 필요
- YAML: `smc.ranging_choch_enabled: true`, `ranging_rvol_threshold: 1.5`

### 방안 C: swing_count 조건부 RANGING
```python
elif structure.trend == MarketTrend.RANGING:
    if len(structure.swing_points) < ranging_min_swings:  # default 4
        return None
```
- 장점: swing 밀도가 낮은 ranging(= 사실상 directionless)은 차단
- YAML: `ranging_min_swings: 4`

**추천: 방안 B + C 조합** — 거래량 + 구조 밀도 동시 요구

---

## 5. 코드 변경 범위

**파일**: `analyzers/smc/smc_structure.py` — `detect_choch()` 함수  
**규모**: 10줄 이하

**YAML 추가**:
```yaml
smc:
  ranging_choch:
    enabled: false        # EXP-002 시작 시 true로
    rvol_threshold: 1.5   # ranging CHoCH 거래량 최소
    min_swings: 4         # ranging CHoCH 최소 스윙 수
```

**enabled: false 기본** — 실험 시작 시 수동 활성화

---

## 6. 예상 효과 (방안 B+C 기준)

| 단계 | BEFORE (RANGING 제거) | AFTER (조건부 재허용, 예상) |
|------|----------------------|--------------------------|
| CHoCH 평가 가능 종목 | 17% (bearish만) | 83% (ranging 포함) |
| CHoCH 발생 건수 | ≈0 | ranging + vol>1.5x + swing≥4 종목 |
| false positive 위험 | 낮음 | 거래량/구조 조건으로 제어 |
| signal frequency | ≈0 | 측정 가능한 수준 예상 |

---

## 7. 성공/실패/반증 기준

### 전제조건 (표본 충족)

| 조건 | 최소값 |
|------|--------|
| ACCEPT 종목 수 (3일 누적) | ≥ 20 |
| SMC_DIAG 호출 수 | ≥ 10 |
| ranging 종목 중 vol>1.5x 발생 수 | ≥ 3 |
| 평가 기간 | 최소 3 거래일 |

### 성공 기준

| 관측 | 해석 |
|------|------|
| ranging 종목에서 CHoCH 발생 | ranging 제거 과도했음 확인 ✅ |
| CHoCH 발생 종목의 structure_trend=ranging | 기대 경로 ✅ |
| avg swing_count ≥ 4 유지 | EXP-001 효과 유지 ✅ |
| signal=True 발생 | downstream 정상 도달 ✅ |

### 실패 기준

| 관측 | 해석 |
|------|------|
| ranging 종목 CHoCH 여전히 0 | 조건이 너무 엄격하거나 다른 차단 존재 |
| no_structure 비율 변화 없음 | ranging 제거 외 다른 병목 |

### 반증 기준

| 관측 | 해석 |
|------|------|
| ranging CHoCH 급증 (>10회/일) | 조건 불충분 — threshold 강화 필요 |
| signal 발생 but grade C > 40% | ranging에서 품질 낮은 setup 과다 |
| execute_buy 후 빠른 stop 증가 | false positive 실전 확인 → 원복 |

---

## 8. 부작용 감시

| 증상 | 임계값 | 조치 |
|------|--------|------|
| ranging CHoCH 과잉 | > 5회/일 | rvol_threshold 높이기 (1.5→2.0) |
| grade C 비율 | > 40% | min_choch_grade 재검토 |
| PREFILTER 통과율 | > 30% | prefilter 조건 점검 |

---

## 9. 측정 명령 (매일)

```bash
# ── 활성화 전 필수 베이스라인 (오늘 장 마감 후 실행) ──────────────────────────
python3 -c "
import json
from collections import Counter

rows = [json.loads(l) for l in open('logs/smc_diag_$(date +%Y%m%d).jsonl') if l.strip()]
gs   = [r.get('gate_snapshot', {}) for r in rows]
print(f'Total DIAG entries: {len(rows)}')

# 1. shadow observation: candidate=True + cfg=False 조합 확인
shadow = sum(1 for g in gs if g.get('ranging_choch_cand') and not g.get('ranging_choch_cfg'))
print(f'ranging_choch_candidate=True  cfg=False (shadow): {shadow}건')

# 2. transition_origin 분포
origins = Counter(g.get('transition_origin') for g in gs)
print('transition_origin:')
for k, v in origins.most_common(): print(f'  {v:3d}건  {k}')

# 3. choch_mode 분포 (CHoCH 발생 but 이후 차단된 케이스)
modes = Counter(g.get('choch_mode') for g in gs)
print('choch_mode (gate_snapshot):')
for k, v in modes.most_common(): print(f'  {v:3d}건  {k}')

# 4. structure_trend 분포 (EXP-001 베이스라인 연속)
st = Counter(g.get('structure_trend', '?') for g in gs)
print('structure_trend:')
for t, c in st.most_common(): print(f'  {c:3d}건  {t}')
"

# ── fixture 갱신 (매일 장 마감 후) ─────────────────────────────────────────────
python3 -m analysis.smc_path_test \
  --symbols [오늘_ACCEPT_목록] \
  --compare --save-fixture
```

**활성화 전 합격 기준 (3항목 모두 통과 시 `enabled: true` 전환)**

| 항목 | 기준 | 이유 |
|------|------|------|
| shadow 건수 | ≥ 1건 | JSONL에 new field 정상 기록 확인 |
| transition_origin=T9a | ≥ 1건 | T9a 시나리오 실제 발생 확인 |
| ranging_choch_candidate baseline | 수집 완료 | 활성화 후 비교 기준점 |

---

## 10. 실행 순서

| 단계 | 조건 | 내용 |
|------|------|------|
| **0** ✅ | 완료 | swing_count/high/low observability 추가 |
| **0** ✅ | 완료 | structure_trend 분리 (htf_trend와 별도) |
| **0** ✅ | 완료 | historical replay fixture + 테스트 |
| **0** ✅ | 완료 | prev_trend/transition_note/transition_origin 추가 (T9a trace) |
| **0** ✅ | 완료 | ranging_choch_candidate shadow observation 추가 |
| **0** ✅ | 완료 | choch_mode (NORMAL/T9a_RECOVERED) 추가 |
| **0** ✅ | 완료 | EXP-002 코드 격리 완성 (enabled=false, 65/65 tests) |
| **1** ⏳ | 2026-05-29 | EXP-001 3일 측정 리뷰 + 활성화 전 baseline 확인 (섹션 9) |
| **2** | EXP-001 리뷰 후 | YAML `ranging_choch.enabled: true` |
| **3** | ranging CHoCH 발생 후 | sweep/prefilter/grade + choch_mode 분석 |
| **4** | signal 발생 후 | NORMAL vs T9a_RECOVERED 승률 비교 |

---

## 11. 회고 예정일

- EXP-001 리뷰: **2026-05-29** (3거래일 후)
- EXP-002 1차 측정: EXP-001 리뷰 후 3거래일

---

## 12. T9b 테스트 (이미 존재)

`tests/unit/test_smc_synthetic.py::test_ranging_allowed_restores_choch`가  
RANGING 재허용 시 CHoCH 복구를 검증한다.

EXP-002 코드 수정 후 이 테스트가 통과하면:
- 코드 수정이 의도대로 동작한다는 단위 수준 확인
- 이후 historical replay(TR*)로 실 데이터 확인

**검증 순서**: T9b → TR* (replay) → live smc_diag 측정
