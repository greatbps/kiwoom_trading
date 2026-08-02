# RAE Go-Live 플레이북

> **문서 목적**: RAE를 언제, 어떻게 활성화할지 결정하는 운영 승인 절차  
> **대상 독자**: 운영자 (의사결정), 개발자 (체크리스트 실행)  
> **최종 갱신**: 2026-07-05 | ruleset_version v1.3.1-final  
> **현재 상태**: `rae.enabled: false` — RAE 실거래 0건, Shadow 데이터 없음

---

## ⚠️ 선결 조건 — 이 플레이북 실행 전 필수 확인

**RAE 활성화는 Primary 전략이 수익을 낼 때만 유효하다.**

현재 Primary 전략 지표:
- PF = 0.407 (수익 구조 아님)
- WR = 22.5%
- avg_pnl = -0.447%

**Primary PF ≥ 1.0, WR ≥ 30%를 달성하기 전까지 RAE Go-Live 프로세스를 시작하지 않는다.**  
잘못된 Primary 신호에 RAE를 붙이면 손실이 배로 확대된다.

---

## 1. RAE란 무엇인가

### 개요

RAE (Re-Acceleration Entry)는 Primary CHoCH 진입 후, 그 추세가 유지되는 동안 발생하는 **두 번째 파동 진입**이다.

```
Primary 진입 (CHoCH) → [보유 중] → IMPULSE 확인
                                      ↓
                              PULLBACK 대기 (되돌림)
                                      ↓
                              REACCEL 확인 (재가속 신호)
                                      ↓
                              RAE 진입 (2nd wave)
```

### 상태 기계

| 상태 | 의미 | 전이 조건 |
|------|------|-----------|
| IDLE | 비활성 | Primary 진입 발생 시 → IMPULSE |
| IMPULSE | Primary 진입 후 모니터링 | 가격이 entry 이후 0.5R 이상 상승 |
| PULLBACK | 되돌림 대기 중 | 가격이 고점에서 pullback_max_r(1.5R) 이내 하락 |
| REACCEL | 재가속 조건 확인 중 | 볼륨/모멘텀 재활성 신호 감지 |
| ENTRY | 진입 신호 발생 | TED 조건 충족 or soft penalty |

### RAE 진입 후 사이징

```
RAE size = base × route_mult(0.7) × g3_mult × dd_mult × ted_mult
```
- `route_mult = 0.7`: Primary보다 작은 사이즈
- `ted_mult = 0.8 if TED 실패`: TED 확인 실패 시 추가 감소

### 핵심 YAML 파라미터

```yaml
rae:
  enabled: false                 # 현재 비활성
  pullback_max_r: 1.5            # 허용 되돌림 최대값
  reaccel_volume_ratio: 1.2      # 재가속 볼륨 기준
  reaccel_momentum_pct: 0.3      # 재가속 모멘텀 기준
  confidence_threshold: 0.6      # 진입 신뢰도 최소값
  stale_timeout_minutes: 180     # 상태 만료 시간

ted:
  enabled: false                 # rae 종속
  min_pass_count: 3              # 5개 조건 중 최소 통과 수
```

---

## 2. Primary↔RAE 충돌 방지 규칙

RAE가 활성화될 경우 Primary 포지션과 충돌을 방지하는 규칙:

| 규칙 | 조건 | 동작 |
|------|------|------|
| **Rule A** | Primary 보유 중에 Primary 신호 재발생 | RAE 상태 초기화 후 무시 |
| **Rule B** | RAE 상태 중 Primary 포지션 청산 | RAE 상태 즉시 초기화 |
| **Rule C** | 동일 종목에 Primary + RAE 동시 보유 금지 | RAE 진입 직전 Primary 보유 확인 |
| **Rule D** | RAE 진입 후 Primary 재진입 시도 | RAE 존재 시 Primary 차단 |

이 규칙들은 코드에 구현되어 있으며 `rae.enabled: true` 시 자동 작동한다.

---

## 3. Shadow 모드 운영 절차

### Shadow 모드란

- `rae.enabled: false`이지만 RAE 신호 로그는 기록됨
- `logs/auto_trading_YYYYMMDD.log`에 `[RAE_REGISTER]`, `[RAE_PULLBACK]`, `[RAE_REACCEL]`, `[RAE_SIG]` 태그로 기록
- 실거래 발동 없음

### Shadow 데이터 수집 방법

```bash
# RAE 신호 발생 횟수 확인
grep "RAE_SIG" logs/auto_trading_*.log | wc -l

# RAE 신호 발생 후 가격 방향 확인 (수동)
grep "RAE_SIG" logs/auto_trading_*.log | head -20

# 종목별 RAE 신호 분포
grep "RAE_SIG" logs/auto_trading_*.log | awk '{print $NF}' | sort | uniq -c
```

### Shadow 데이터 분석 기준

최소 **20건의 RAE 신호** 발생 후 아래를 수동 측정:
1. RAE 신호 발생 후 5봉(25분) 내 방향 일치율 (목표: ≥ 50%)
2. Primary 진입 이후 RAE 신호까지의 평균 소요 시간
3. PULLBACK 깊이 분포 (pullback_max_r=1.5 적절성 확인)
4. TED 조건 통과율 (min_pass_count=3 적절성 확인)

---

## 4. Go-Live 조건 (7개 항목 전부 충족 필요)

### Phase 0 전제 조건

| # | 조건 | 현재 상태 | 확인 방법 |
|---|------|-----------|-----------|
| P0-1 | **Primary PF ≥ 1.0** (30건 이상) | **미충족 (PF=0.407)** | DB 쿼리 |
| P0-2 | **Primary WR ≥ 30%** (30건 이상) | **미충족 (WR=22.5%)** | DB 쿼리 |

**P0 조건 미충족 → Go-Live 절차 시작 불가. 여기서 멈춤.**

### Phase 1 — Shadow 기준

| # | 조건 | 기준값 | 확인 방법 |
|---|------|--------|-----------|
| 1 | Shadow RAE 신호 건수 | **≥ 20건** | `grep "RAE_SIG" logs/*.log \| wc -l` |
| 2 | RAE 신호 방향 일치율 (25분 기준) | **≥ 50%** | 수동 측정 |
| 3 | Primary↔RAE 충돌 없음 (24건 이상 검증) | **0건 충돌** | `grep "RAE_BLOCK" logs/*.log` |
| 4 | TED 통과율 | **≥ 40%** | `grep "TED_" logs/*.log` |

### Phase 2 — Pilot Live 기준 (제한 활성화)

| # | 조건 | 기준값 |
|---|------|--------|
| 5 | Pilot Live 기간 | **2주** (매일 확인) |
| 6 | Pilot Live RAE 거래 WR | **≥ 35%** (최소 10건) |
| 7 | Pilot Live RAE 거래 avg R | **≥ 0.25R** |

---

## 5. 3단계 활성화 절차

### Phase 1: Shadow 모드 (현재)

**기간**: P0 충족 후 시작, 최소 2주

```yaml
# config/strategy_hybrid.yaml
rae:
  enabled: false    # 유지
```

- 모든 RAE 신호 로그 기록됨
- 수동으로 신호 발생 후 가격 확인
- 20건 달성 → Phase 2 판단

### Phase 2: Pilot Live

**기간**: 2주, 하루 최대 1건 RAE 진입

```yaml
# config/strategy_hybrid.yaml (Phase 2 진입 시)
rae:
  enabled: true
  max_rae_per_day: 1          # 신규 추가 파라미터 (구현 필요)
  pilot_mode: true            # 신규 추가 파라미터 (구현 필요)
```

- `max_rae_per_day: 1`은 현재 구현 필요
- 매일 장 마감 후 당일 RAE 거래 결과 확인
- 2주 후 Phase 2 기준(WR≥35%, avg R≥0.25) 평가

### Phase 3: Full Live

```yaml
rae:
  enabled: true
  # max_rae_per_day 제한 해제
```

- 이 단계부터 RAE가 Primary와 동등하게 동작
- 최초 Full Live 이후 2주간 매일 RAE 전용 지표 확인

---

## 6. 모니터링 지표

RAE 활성화 이후 매일 확인해야 할 지표:

```bash
# RAE 거래 결과
psql -U postgres trading_system -c "
SELECT
  entry_features->>'entry_route' AS route,
  COUNT(*) AS n,
  ROUND(AVG(profit_rate)::numeric, 3) AS avg_pnl,
  ROUND(SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END)::numeric
    / NULLIF(COUNT(*), 0) * 100, 1) AS wr_pct
FROM trades b
JOIN trades s ON b.stock_code = s.stock_code
  AND DATE_TRUNC('day', b.trade_time) = DATE_TRUNC('day', s.trade_time)
  AND s.trade_type = 'SELL'
WHERE b.trade_type = 'BUY'
  AND b.entry_features->>'entry_route' = 'RAE'
  AND s.profit_rate IS NOT NULL
GROUP BY route;
"

# RAE vs Primary 비교
grep "RAE_SIG\|SMC_RAE" logs/auto_trading_$(date +%Y%m%d).log | wc -l
```

---

## 7. 롤백 조건

다음 중 하나라도 발생 시 즉시 `rae.enabled: false`로 되돌림:

| 조건 | 기준 |
|------|------|
| RAE WR 급락 | 10건 이상에서 WR < 20% |
| RAE avg_pnl 악화 | avg_pnl < -1.5% (10건 이상) |
| Primary↔RAE 충돌 발생 | 1건이라도 발생 시 즉시 조사 |
| DrawdownEngine DANGER | dd_level ≥ DANGER 진입 시 자동으로 RAE size=0 |
| 총 일일 손실 한도 초과 | 당일 누적 손실 > 일일 한도 |

### 긴급 롤백 절차

```bash
# 1. YAML 수정
sed -i 's/enabled: true/enabled: false/' config/strategy_hybrid.yaml

# 2. 프로세스 재시작 (swing_runner가 다음 날 pickup)
# 장중에는 현재 보유 포지션 수동 확인 후 진행

# 3. 확인
grep "rae.enabled" config/strategy_hybrid.yaml
```

---

## 8. 의사결정 요약

| 단계 | 현재 상태 | 다음 액션 | 담당 |
|------|-----------|-----------|------|
| P0: Primary 성과 검증 | **미완료** (PF=0.407) | Primary PF≥1.0 달성 | Scientist AI + 운영자 |
| Phase 1: Shadow 20건 | **시작 전** | P0 후 시작 | 자동 (로그 기록됨) |
| Phase 2: Pilot Live | **시작 전** | Phase 1 완료 후 | 운영자 수동 YAML 수정 |
| Phase 3: Full Live | **시작 전** | Phase 2 완료 후 | 운영자 승인 필요 |

**결론: 현재 RAE Go-Live 프로세스는 P0(Primary 성과 개선)이 해결될 때까지 보류.**
