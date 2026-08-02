# Trade Tag Schema — v1.3

> 모든 BUY/SELL 레코드에 반드시 저장되는 표준 필드 정의
> 저장 위치: `trades.entry_features` JSONB 컬럼

---

## 필드 목록

| 필드 | 타입 | 값 예시 | 설명 |
|------|------|---------|------|
| `entry_route` | str | `PRIMARY` / `RAE` | 진입 경로 |
| `entry_setup` | str | `SMC_OB` / `SMC_RECLAIM` / `SMC_RAE` | 구체적 진입 패턴 |
| `smc_grade` | str | `A` / `B` / `C` | CHoCH 등급 |
| `entry_time_bucket` | str | `OPEN` / `OPEN_LATE` / `MID` / `LATE` | 진입 시간대 |
| `g3_penalty_applied` | bool | `true` / `false` | G3 HIGH_PROX soft penalty 적용 여부 |
| `g3_penalty_mult` | float | `0.6` | G3 penalty 배율 (1.0 = 미적용) |
| `ted_valid` | bool\|null | `true` / `false` / `null` | TED 검증 결과 (RAE만) |
| `ted_score` | int\|null | `3` / `null` | TED 통과 조건 수 (0~5) |
| `rae_score` | int\|null | `7` / `null` | RAE 진입 점수 (0~9) |
| `reentry_flag` | bool | `false` | 재진입 여부 |
| `cooldown_override_used` | bool | `false` | cooldown override 사용 여부 |
| `size_components` | dict | (아래 참조) | 사이즈 계산 컴포넌트 분해 |
| `choch_grade` | str\|null | `A` / `B` | CHoCH 등급 (별도 저장) |
| `conf` | float | `0.650` | 진입 신뢰도 |
| `size_mult` | float | `0.35` | 최종 적용 사이즈 배율 |

### `size_components` 구조

```json
{
  "base":         0.5,    // CHoCH 등급 기반 초기값
  "route_mult":   0.7,    // PRIMARY=1.0, RAE=0.7
  "g3_mult":      0.6,    // G3 soft penalty (1.0=미적용)
  "dd_mult":      1.0,    // NORMAL=1.0, CAUTION=0.7, DANGER=0.4, HALT=0.0
  "ted_mult":     1.0,    // TED 실패 시 0.8 (RAE만)
  "reclaim_bonus": 1      // reclaim 감지 시 EF threshold +1 (bool)
}
```

---

## 시간대 (entry_time_bucket) 정의

| 버킷 | 시간 범위 | 특성 |
|------|-----------|------|
| `OPEN` | 09:00 ~ 10:00 | 장 초반, G3 Phase1 (delay ≤ 3분) |
| `OPEN_LATE` | 10:00 ~ 10:30 | G3 Phase2 (delay ≤ 2분) |
| `MID` | 10:30 ~ 13:00 | c_late_v2 적용 구간 |
| `LATE` | 13:00 이후 | c_late_block 적용 |

---

## 로그 태그 일람

| 태그 | 위치 | 의미 |
|------|------|------|
| `[SMC_PRIMARY]` | main | Primary OB 진입 확정 |
| `[SMC_RAE]` | main | RAE 진입 확정 |
| `[REACCEL_ENTRY]` | main | REACCEL 상태 진입 감지 |
| `[G3_SOFT_PENALTY]` | orchestrator | HIGH_PROX → size 축소 적용 |
| `[TED_BLOCK]` | main | TED 실패 → soft penalty 적용 |
| `[RAE_REGISTER]` | rae_detector | RAE 후보 등록 |
| `[RAE_PULLBACK]` | rae_detector | IMPULSE→PULLBACK 전환 |
| `[RAE_REACCEL]` | rae_detector | PULLBACK→REACCEL 전환 |
| `[RAE_SIG]` | rae_detector | RAE 진입 신호 확정 |
| `[RAE_BLOCK]` | main | 충돌 방지 규칙으로 RAE 차단 |
| `[RAE_RESET_DAILY]` | main | 일일 리셋 |
| `[RAE_RESET_INVALIDATED]` | rae_detector | 구조 붕괴 리셋 |
| `[RAE_RESET_STALE]` | rae_detector | 180분 stale 리셋 |
| `[RAE_RESET_AFTER_EXIT]` | main | 청산 후 상태 정리 |

---

## SELL 시점 BUY 태그 참조

`entry_features` JSONB에 저장된 trade tag는 `trades` 테이블에서
동일 `stock_code + entry_time`으로 BUY 레코드를 조회해 참조 가능.

```sql
SELECT
    b.entry_features->>'entry_route'  AS route,
    b.entry_features->>'smc_grade'    AS grade,
    s.exit_category,
    s.profit_rate
FROM trades b
JOIN trades s ON b.stock_code = s.stock_code
             AND b.entry_time  = s.entry_time
             AND s.trade_type  = 'SELL'
WHERE b.trade_type = 'BUY'
ORDER BY b.trade_time DESC;
```
