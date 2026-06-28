# EXP-001: swing_lookback rollback (20 → 10)

작성일: 2026-05-26  
상태: 수정 적용됨 / 측정 중 (최소 3 거래일)  
담당: smc_path_test.py + SMC_DIAG 로그

---

## 1. 문제 정의

**관측된 현상**

> 2026-02-25 이후 `SMC long signal confirmed = 0건`
>
> - 기준: `check_entry_signal()` 반환값 `signal=True`
> - 실거래 진입(execute_buy) 기준 아님
> - SMC 호출 자체 여부와도 구별됨

이 세 단계를 구별해야 하는 이유:

| 단계 | 관측 방법 | 상태 |
|------|-----------|------|
| SMC 호출 유무 | `[SMC_DIAG]` 로그 존재 여부 | ✅ 호출됨 (오늘 확인) |
| SMC signal 발생 | `signal=True` 반환 | ❌ 0건 (오늘 확인) |
| execute_buy 실행 | DB `action=BUY` 레코드 | ❌ 0건 (2026-02-25 이후) |

→ **SMC는 호출되고 있으나 signal을 생성하지 못하는 상태**

---

## 2. 재현 기반 진단

`smc_path_test.py`로 오프라인 재현 (`smc_path_test --compare`, 2026-05-26):

```
swing_lookback    avg swing_count    no_structure    CHoCH
20 (현재)             0.5             100%           0건
10 (rollback)         1.7             100%           0건
```

추가 세부 진단:

```
lookback=5 시: swing_count 4~7
lookback=10 시: swing_count 1~3
lookback=20 시: swing_count 0~1
```

→ **lb=20에서 구조 검출 자체가 collapse 수준**  
→ lb=10에서도 오늘 CHoCH=0: 오늘 시장 자체가 ranging/bullish

---

## 3. 원인 가설 (복합, 우선순위 순)

단일 원인이 아님. 누적된 강화들의 복합 작용:

| 날짜 | 변경 | 가설상 영향 |
|------|------|------------|
| 2026-02-26 | Market Context Layer 추가 | 새 upstream gate — SMC 도달 종목 수 감소 |
| **2026-03-07** | **RANGING 추세에서 CHoCH 제거** | ranging 시장에서 CHoCH 완전 불가 — **영향 큼** |
| 2026-03-18 | swing_lookback 10→20 | swing detection 추가 감소 — **이번 rollback 대상** |
| 2026-03-18 | choch_penetration_pct=0.2 추가 | CHoCH 확정 임계 강화 |

**이번 실험의 가설:**

> `swing_lookback 10→20` 강화가 **이미 감소된 SMC signal frequency를**  
> **추가적으로** 더 떨어뜨렸을 가능성.
>
> 단, lb=10 rollback 후에도 오늘 CHoCH=0이 확인됨.  
> 오늘 시장 조건(코스피 10종목 전부 ranging/bullish)은 SMC long setup(intraday bearish→reversal)과  
> 구조적으로 불일치. 이것도 병목의 일부임.

→ **lb rollback은 필요 조건이지, 충분 조건이 아님**

---

## 4. 수정 내용

```yaml
# config/strategy_hybrid.yaml
swing_lookback: 10  # 🔧 2026-05-26: 20→10 rollback (진단: lb=20 시 swing_count=0~1, signal collapse 확인)
```

적용 시점: 2026-05-27 장 개시 (프로세스 재시작 시)

---

## 5. 예상 병목 이동 (파이프라인)

| 단계 | BEFORE (lb=20) | AFTER (lb=10, 예상) |
|------|----------------|---------------------|
| swing detection | collapse (0~1개) | 회복 (3~5개) |
| CHoCH 평가 가능 여부 | 거의 불가 | bearish trend 종목에서 실행 가능 |
| rejection reason | no_structure 100% | no_sweep / grade_fail / prefilter_fail 일부 등장 |
| signal frequency | ≈ 0 | 측정 가능한 수준 |

**1차 목표: 매수 발생 아님**

> `signal frequency` 0 → **측정 가능한 수준**이 되는 것  
> = dead pipeline이 살아나고 있다는 증거

---

## 6. 성공 / 실패 / 반증 기준

### 성공 판단 전제조건 (표본 미달 시 판단 보류)

| 조건 | 최소값 |
|------|--------|
| ACCEPT 종목 수 (3일 누적) | ≥ 20 |
| SMC_DIAG 호출 수 | ≥ 10 |
| intraday trending 종목 수 | ≥ 5 (trend≠ranging) |
| 평가 기간 | 최소 3 거래일 (시장 regime 다양성 확보) |

표본 미달이면: "데이터 부족 — 판단 보류"

### 성공 기준 (표본 충족 시)

| 관측 | 해석 |
|------|------|
| `no_structure` 비율 감소 | swing detection 복구 ✅ |
| `no_sweep` / `grade_fail` 등장 | 병목이 downstream으로 이동 ✅ |
| avg swing_count ≥ 3 (trending 종목 기준) | lb=10 효과 확인 ✅ |
| CHoCH 일부 발생 | SMC viability 회복 시작 ✅ |

### 실패 기준

| 관측 | 해석 |
|------|------|
| 여전히 swing_count 0~1 | rollback 미적용 또는 다른 버그 |
| no_structure 100% 유지 | lb 외 다른 병목 (구조 문제) |
| SMC 호출 자체 없음 | upstream 문제 재발 (EC_HALT 등) |

### 반증 조건 (가설 자체가 틀렸을 때)

| 결과 | 해석 |
|------|------|
| swing_count 증가했지만 CHoCH=0 유지 (3일) | **lb 문제가 핵심 아님. RANGING 제거가 진짜 원인** |
| CHoCH 발생했지만 signal=0 | downstream gate 병목 (sweep/prefilter/grade) |
| trending 종목에서도 no_structure 100% | structure detection 코드 버그 가능성 |
| SMC signal 증가 but PnL 악화 (이후 단계) | 과완화 side effect — 원복 검토 |

---

## 7. 부작용 감시

lb=10 rollback이 의도치 않게 너무 많은 스윙을 만들 경우:

| 증상 | 임계값 | 조치 |
|------|--------|------|
| false positive 급증 | signal rate > 10회/일 | lb=12~15로 중간점 찾기 |
| grade C 비율 증가 | C급 > 40% | min_choch_grade 검토 |
| sweep 없는 진입 증가 | no_sweep 여전히 0 but signal 급증 | sweep 조건 재점검 |

---

## 8. 측정 명령 (매일 실행)

```bash
# 1. SMC_DIAG reason 분포 확인
cat logs/smc_diag_$(date +%Y%m%d).jsonl | \
  python3 -c "
import sys, json
from collections import Counter
rows = [json.loads(l) for l in sys.stdin]
reasons = Counter(r.get('smc_reason','')[:60] for r in rows)
print(f'총 {len(rows)}건')
for r, c in reasons.most_common(): print(f'  {c:3d}건  {r}')
"

# 2. ACCEPT 종목으로 lb=20 vs lb=10 비교
python3 -m analysis.smc_path_test \
  --symbols [오늘_ACCEPT_목록] \
  --compare

# 3. avg swing_count 추이 (3일치 비교)
grep '\[SMC_DIAG\]' logs/auto_trading_$(date +%Y%m%d).log | grep "choch="
```

---

## 9. 다음 단계 (조건부)

| 단계 | 조건 | 내용 |
|------|------|------|
| **1 ✅** | 완료 | swing_lookback 10 rollback |
| **2** | lb=10 이후에도 trending 종목 CHoCH=0 (3일) | RANGING 조건부 재허용 검토 |
| **3** | CHoCH 발생 but no signal | sweep / prefilter / grade 분석 |
| **4** | signal 발생 but no execute_buy | upstream gate 분석 (Market Context / Market Sensor) |

RANGING 재허용 방식 (2단계 시):
```python
# 완전 허용 아님 — 조건부
if trend == RANGING and volume > 2.0 * avg_volume:
    # conditional ranging CHoCH
```

---

## 10. 회고 예정일

- 2026-05-29 (3거래일 후): 1차 측정 결과 해석
- 판단 기준: 섹션 6 성공/실패/반증 기준 적용
