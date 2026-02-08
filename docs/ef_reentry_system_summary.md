# EF Subtype & Reentry Guard System — 의사결정 요약

> 작성: 2026-02-08 | 설계 동결 스냅샷 (Optimizer 재실행 전 기준점)

---

## 1. 시스템 목적

진입 실패를 **"왜 실패했는지"** 자동 분류하고,
실패 유형별로 **재진입 대기 시간과 예외 허용 범위를 다르게** 적용하는 구조.
목표는 "잘 사는 것"이 아니라 **"같은 실수를 반복하지 않는 것"**.

---

## 2. EF Subtype 설계 철학

| Subtype | 의미 | 핵심 신호 | 판단 |
|---|---|---|---|
| **no_follow** | 타이밍은 맞았으나 추세 지속 실패 | Signal D(MFE) 미발동 | 구조는 살아있음 → 재기회 가능 |
| **no_demand** | 애초에 수급이 없었던 가짜 신호 | Signal D(MFE) 발동 | 시장이 거부 → 강제 대기 |

**분류 기준**: `'MFE부족(1)' in signals` → no_demand, 그 외 → no_follow

---

## 3. Guard 역할 분담

```
진입 실패 발생
  │
  ├─ EF Subtype 분류 (no_follow / no_demand)
  │     → exit_reason에 [no_follow] 또는 [no_demand] 태그
  │
  ├─ 차등 쿨다운 적용
  │     ef_no_follow: 20분  (구조 살아있음)
  │     ef_no_demand: 45분  (수급 부재)
  │     early_failure: 60분  (미분류 fallback)
  │
  ├─ R1: Drift 감시 ─────────────────────────
  │     no_demand 비율 추적 (일간 15:30 리포트)
  │     ≤40% OK │ 40~60% WARN │ >60% CRITICAL
  │     → "진입 신호 품질"의 선행 지표
  │
  ├─ R2: Override 남용 방지 ──────────────────
  │     override_count / ef_no_follow_blocked > 30%
  │     → 당일 override 전면 비활성화
  │     → "예외가 규칙이 되는 것" 방지
  │
  └─ Override (쿨다운 기간 내 재진입 허용 조건)
        ├─ Squeeze: BB pctile≤15 + vol≥2.5x + sqzON
        ├─ Momentum: ROC3≥2.5% + RSI≥65 + VWAP위
        └─ Close: 14:30~15:20 + VWAP위 + EMA20위 + vol≥1.5x
              ⚠ 현재 enabled: false (T3 검증 대기)
```

**절대 규칙**: ef_no_demand와 미분류 early_failure는 **어떤 override로도 우회 불가**.

---

## 4. T3 판정 기준

### 목적
"종가 Override를 켜도 되는가?" 를 판단하기 위한 실매매 로그 검증.

### 확인 항목 (1~2 거래일)

| # | 항목 | 기준 | 판정 |
|---|---|---|---|
| 1 | EF subtype 분류 정상 동작 | exit_reason에 [no_follow]/[no_demand] 태그 존재 | Y/N |
| 2 | R1 drift 경고 레벨 | OK 또는 WARN (CRITICAL 아님) | Y/N |
| 3 | R2 override_disabled_today | 발동 안 됨 (또는 정당한 사유) | Y/N |
| 4 | blocked_by_reason 분포 | ef_no_follow, ef_no_demand 키 정상 집계 | Y/N |
| 5 | override 거래 없음 확인 | close_override=false 상태에서 close override 미발동 | Y/N |

### ON 전환 조건
- 위 5항목 **전부 Y** → `close_override: enabled: true`
- 1개라도 N → 해당 항목 원인 분석 후 재검증

---

## 5. ON/OFF 결정 규칙

| 상황 | 동작 |
|---|---|
| T3 전부 통과 | `close_override: enabled: true` |
| R1 CRITICAL 3일 연속 | `ef_sensitivity_analyzer --days 7` 실행 → mfe_ratio 재조정 |
| R2 disable_today 2일 연속 | override 조건 강화 or `max_override_ratio_pct` 하향 검토 |
| 종가 Override ON 후 3거래일 | override_count 중 close 비율 확인 → 30% 미만 유지 |

---

## 6. 변경 금지 구역

| 항목 | 이유 |
|---|---|
| `blocked_reasons`에서 ef_no_demand 제거 | 수급 부재 종목 재진입은 도박 |
| `record_blocked()` → override 순서 변경 | R2 분모 정확성 파괴 |
| R1 min_sample < 5 | 노이즈로 잘못된 경고 |
| R2 action을 warn_only로 장기 운영 | 자동매매에서 경고만은 의미 없음 |

### 변경 가능 구역 (데이터 기반으로만)

| 항목 | 조건 |
|---|---|
| ef_no_follow 쿨다운 (현 20분) | cooldown_optimizer 결과로만 조정 |
| ef_no_demand 쿨다운 (현 45분) | cooldown_optimizer 결과로만 조정 |
| close_override 임계값 | T3 로그 + 1주일 운영 데이터 기반 |
| R2 max_override_ratio_pct (현 30%) | override 거래의 실제 수익률 확인 후 |

---

## 7. 향후 로드맵

```
현재 ──→ T3 실매매 검증 (1~2일)
     ──→ close_override ON
     ──→ Optimizer 재실행 (EF subtype 반영 효과 확인)
     ──→ 운영 대시보드 연결 (ef_ratio, override_count, drift 시각화)
```

---

> **이 문서는 "설계 동결 스냅샷"입니다.**
> Optimizer 재실행 전/후 비교, T3 판정, 향후 파라미터 변경의 기준점으로 사용하세요.
