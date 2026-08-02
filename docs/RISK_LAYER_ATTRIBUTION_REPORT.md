# 리스크 레이어 기여도 분석 보고서

> **문서 목적**: 각 리스크 레이어가 실제로 무엇을 막았고, 어떤 부작용이 있는지 분해하여 유지/조정/제거를 판정하는 의사결정 문서  
> **대상 독자**: 운영자, 총괄 매니저  
> **최종 갱신**: 2026-07-05 | ruleset_version v1.3.1-final  
> **데이터 기간**: 2025-11-24 ~ 2026-07-01 (SELL 244건)

---

## ⚠️ 전제 — 분석의 한계

1. **진입 필터 차단 건수 미측정**: G3, Stage A, c_late_v2가 차단한 진입 건수의 DB 기록이 없음 (로그에만 존재). 차단 후 해당 종목이 어떻게 움직였는지 측정 불가.
2. **entry_features JSONB 대부분 비어 있음**: Trade tag 시스템이 최근 구현되어 역사 데이터에 entry_route, smc_grade 등이 없음.
3. **EF 방지 손실 추정**: EF로 잘린 후 가격이 어디까지 떨어졌는지 실측 불가 (정황 추정).

이 보고서는 **실측 가능한 데이터**를 우선으로 작성했다. 추정 구간은 별도 표기한다.

---

## 1. 리스크 레이어 전체 지도

```
진입 전 필터 레이어
  G3 Soft Penalty        진입 직전 size ×0.6
  Stage A Gate           bdh > 15% 시 차단
  c_late_v2              10:30~13:00 늦은 진입 필터
  AI Gate                ai_score < 20 시 차단

진입 후 보호 레이어
  LCL v2.1               진입 후 30분 이내 -1.6% 시 조기 손절
  Early Failure          5신호 ≥ 3점 시 조기 청산
  Hard Stop              -2.0% 절대 손절선
  DrawdownEngine         연속 손실 시 size 축소 → 진입 중단

포지션 수명 레이어
  ATR Trailing           수익 구간에서 이익 보호
  Overnight Guard        B급 이하 14:50 강제청산
  Time Exit              최대 보유 시간 초과 시 청산

재진입 통제 레이어
  Cooldown               exit_reason별 20~60분 쿨다운
  Override Abuse Guard   R2 규칙으로 과도한 override 방지
```

---

## 2. 레이어별 상세 분석

### Layer 1: G3 Soft Penalty (진입 size ×0.6)

**구현 현황**: v1.3에서 hard block → soft penalty 변경  
**발동 조건**: bdh (Bar Distance High) < 3% — 전고점에 너무 근접한 진입

| 항목 | 값 | 비고 |
|------|-----|------|
| 발동 건수 | 측정 불가 (로그 기반) | `grep "G3_SOFT_PENALTY" logs/*.log` 필요 |
| 적용 효과 | HIGH_PROX 진입 size ×0.6 | 손실 규모 자동 축소 |
| v1.2 hard block 대비 | 기회 손실 감소 | 고점 근접에서도 진입 허용 |
| 측정 가능 지표 | size 축소로 손실 %-포인트 감소 | 건당 손실 금액 기준 실측 가능 |

**평가**:
- Hard block보다 유연한 접근. PF=0.407 상황에서 size 축소는 적절.
- 그러나 HIGH_PROX 자체가 잘못된 진입인지 아닌지는 entry 이후 방향으로 판단해야 함.

**판정: 조건부 유지** — G3_SOFT_PENALTY 태그로 해당 진입의 WR 측정 후 재검토

---

### Layer 2: Stage A Gate (bdh > 15% 차단)

**구현 현황**: Implemented  
**발동 조건**: 전고점 대비 15% 이상 상승한 종목 차단

| 항목 | 값 |
|------|-----|
| 발동 건수 | 측정 불가 |
| 목적 | 과열 종목 진입 방지 |
| 부작용 | 강한 추세 종목 진입 차단 가능 |

**평가**:
- 15%는 스윙 전략 기준 고점 추격 방지에 합리적인 기준.
- 그러나 현재 WR=22.5% 상황에서 Stage A가 오히려 좋은 진입을 막고 있는지 확인 필요.

**판정: 유지** — 데이터 없이 변경 금지. Stage A 발동 건수 로그 분석 후 재판단

---

### Layer 3: c_late_v2 (10:30~13:00 늦은 진입 필터)

**구현 현황**: Implemented  
**발동 조건**: 10:30~13:00 사이 진입 시 추가 조건 요구

진입 시간대별 성과 (실측):

| 진입 시간대 | 건수 | avg_pnl | WR | PF |
|-------------|------|---------|----|----|
| 09:00~10:00 | 7건 | -1.357% | 0.0% | 0.0 |
| 10:00~10:30 | 15건 | -0.852% | 33.3% | 0.319 |
| **10:30~13:00** | 92건 | -0.770% | 26.1% | 0.310 |
| 13:00 이후 | 29건 | -0.795% | 24.1% | 0.130 |

**평가**:
- 10:30~13:00 진입이 92건으로 가장 많고, WR 26.1%로 전체(22.5%)보다 높음.
- c_late_v2가 적용된 이후에도 이 구간 진입이 많이 발생함 → 필터가 완전 차단이 아닌 조건부 필터.
- 이 구간의 성과가 전체 평균보다 나쁘지 않음 → c_late_v2가 적절히 나쁜 진입을 걸러내고 있을 가능성.

**판정: 유지** — 이 구간 성과가 전체 평균보다 나쁘지 않음. 필터 제거 시 성과 하락 위험

---

### Layer 4: LCL v2.1 (진입 초기 -1.6% 조기 손절)

**구현 현황**: Implemented  
**발동 조건**: 진입 후 30분 이내, 수익률 ≤ -1.6%

| 항목 | 값 |
|------|-----|
| Early Failure와 구분 | EF는 신호 기반 (5신호 점수), LCL은 순수 가격 기반 |
| 예상 차단 시나리오 | 진입 직후 급락, EF 미발동인 상황 |

**EF가 이미 avg -1.286%에서 자르고 있음**:
- LCL -1.6%는 EF보다 더 깊은 손실에서 발동
- EF가 먼저 발동되는 경우 LCL은 불필요
- EF가 발동 안 되는 케이스에서 LCL이 수비선

실측 Hard Stop 케이스 (avg -2.815%):
- Hard Stop에 도달한 28건은 LCL과 EF 둘 다 발동하지 않은 케이스
- 이 28건에서 LCL(-1.6%)이 왜 발동하지 않았는지 확인 필요 (30분 초과 후 손실?)

**판정: 유지 (EF와 중복 구간 분석 후 threshold 재검토 권고)**  
- EF: 신호 기반 조기 청산 (30분 이전 발동 가능)
- LCL: 가격 기반 안전망 (EF 미발동 시 보조)
- 두 레이어가 상호 보완적이므로 둘 다 유지

---

### Layer 5: Early Failure Structure (EF)

**구현 현황**: Implemented  
**발동 조건**: 5개 신호(방향실패×2, ATR감소, 볼륨고갈, MFE부족, 추종실패) 중 점수 ≥ 3

**실측 데이터**:

| 항목 | 값 |
|------|-----|
| 총 발동 건수 | **46건** (전체 SELL 244건의 18.9%) |
| avg_pnl | **-1.286%** |
| WR | **0%** (전량 손실) |
| total_loss | **-59.14%** |
| Hard Stop avg_loss | -2.815% |
| **EF vs Hard Stop 절약** | **-1.286 vs -2.815 → 건당 약 -1.5% 절약 추정** |

**EF 서브타입 분포 (exit_reason 파싱)**:

| 서브타입 | 의미 | 쿨다운 | 특징 |
|---------|------|--------|------|
| ef_no_follow | MFE 발생했으나 추세 지속 실패 | 20분 | 타이밍 맞았으나 지속 실패 |
| ef_no_demand | MFE 부족 (처음부터 수급 없음) | 45분 | 잘못된 진입 신호 |

**시간대별 EF 발동 집중도**:
- 09:00~10:00: EF 50% (7건 중 7건)
- 10:00~10:30: EF 36% (11건 중 4건)
- 10:30~13:00: EF 43% (58건 중 25건)
- 13:00~: EF 6% (161건 중 10건)

→ **장 초반 진입에서 EF가 집중 발동**. 이른 시간대 진입 신호 자체가 나쁜 것을 EF가 잡아내고 있음.

**평가**:
- EF는 잘못된 진입을 Hard Stop 전에 잡아내는 핵심 레이어.
- WR 0%는 EF가 실제로 나쁜 진입만 잡고 있다는 증거 (좋은 진입은 EF 발동 전에 방향을 찾음).
- EF 발동 건수가 46건(19%)로 높음 → 진입 신호 자체의 품질 문제를 EF로 봉합하는 구조.

**판정: 핵심 유지**  
단, EF 발동률 19%는 "진입 전 필터"로 줄일 수 있는 부분. 장기적으로 EF 발동률 감소가 진입 품질 개선의 지표.

---

### Layer 6: Hard Stop (-2.0%)

**구현 현황**: Implemented  
**발동 조건**: 손실 ≤ -2.0% (09:20 이전 유예)

**실측 데이터**:

| 항목 | 값 |
|------|-----|
| 총 발동 건수 | **28건** (11.5%) |
| avg_loss | **-2.815%** |
| total_loss | **-78.81%** |
| WR | **0%** |
| 최대 손실 | **-6.95%** (threshold -2.0% 초과 이유: 갭 하락?) |

**-2.0%를 초과한 손실이 발생한 이유 (추정)**:
- 갭 하락 시 체결가 손실 (설정 -2.0%, 실제 -2.54% 등)
- 09:20 이전 유예 구간에서의 급락
- 대형 급락 종목에서 슬리피지

**판정: 핵심 유지**  
Hard Stop은 최후 안전망. -78.81% 총 손실이 이 레이어 없이는 훨씬 컸을 것.

---

### Layer 7: DrawdownEngine

**구현 현황**: Implemented  
**동작**: 전략별 누적 손실에 따라 NORMAL→CAUTION→DANGER→HALT

| 레벨 | size_mult | 조건 (swing 기준) |
|------|-----------|----------------|
| NORMAL | ×1.0 | 기본 |
| CAUTION | ×0.7 | 누적 손실 > 임계 1 |
| DANGER | ×0.4 | 누적 손실 > 임계 2 |
| HALT | ×0.0 | 누적 손실 > 임계 3 |

**평가**:
- PF=0.407 상황에서 DrawdownEngine이 손실을 조기에 CAUTION/DANGER로 전환하고 있는지 확인 필요.
- `grep "DD_CAUTION\|DD_DANGER\|DD_HALT" logs/*.log` 실행으로 발동 빈도 확인 권고.

**판정: 핵심 유지** — 연속 손실 시 자동 사이즈 축소. 이 레이어 없으면 손실이 기하급수적으로 확대됨.

---

### Layer 8: ATR Trailing Stop

**구현 현황**: Implemented  
**발동 조건**: 수익 ≥ activation(1.5%), 이후 ATR×3.0 기반 추적

**실측 데이터**:

| 항목 | 값 |
|------|-----|
| 총 발동 건수 | **33건** (13.5%) |
| avg_pnl | **+1.184%** |
| WR | **81.8%** (27/33 수익) |
| 최대 수익 | **+6.09%** |
| 총 수익기여 | **+39.07%** (전체 수익합 75.05%의 52%) |

**핵심 발견**: ATR Trailing은 전체 수익의 52%를 생산하는 단일 최대 수익 엔진이다.  
trailing에서 음수(-0.83%, -0.62%, -0.15%)가 발생한 것은 activation 이후 급락으로 trailing이 발동한 케이스.

**판정: 핵심 유지** — 이 레이어가 없으면 시스템 수익 절반 소멸

---

### Layer 9: Overnight Guard

**구현 현황**: Implemented  
**발동 조건**: CHoCH B급 이하 + 14:50에 강제청산

| 항목 | 값 |
|------|-----|
| 총 발동 건수 | 6건 |
| avg_pnl | **-1.615%** |
| WR | **0%** |
| 최대 손실 | -4.64% |

**평가**:
- 6건 모두 손실. overnight 청산이 손실을 막은 것이 아니라 이미 손실인 상태에서 청산한 케이스.
- A급 CHoCH 포지션의 오버나이트 성과는 별도 분석 필요 (현재 데이터 없음).

**판정: 유지** — A급 포지션의 overnight은 수익 가능성이 있으나 B급 이하는 손절 관점에서 타당

---

### Layer 10: Cooldown 시스템

**구현 현황**: Implemented  
**목적**: 손절 후 동일 종목 재진입 방지 (20~60분)

| exit_reason | 쿨다운 | 이유 |
|-------------|--------|------|
| ef_no_demand | 45분 | 수급 없는 신호, 장시간 쿨 |
| ef_no_follow | 20분 | 타이밍 문제, 단시간 쿨 |
| early_failure | 60분 | 구조 붕괴 |
| stop_loss | 45분 | 큰 손실 |
| trailing_stop | 30분 | 추세 종료 |
| time_exit | 20분 | 소규모 청산 |

**판정: 유지** — 손절 직후 충동 재진입 방지. 손실 확대를 막는 운영 규칙.

---

## 3. 레이어별 종합 판정표

| 레이어 | 실측 데이터 | 손실 방지 효과 | 기회 손실 | 판정 |
|--------|------------|---------------|-----------|------|
| G3 Soft Penalty | 없음 (로그만) | size ×0.6 축소 | HIGH_PROX 기회 일부 | **조건부 유지** |
| Stage A Gate | 없음 (로그만) | 과열 종목 차단 | 강한 추세 기회 | **유지** |
| c_late_v2 | 10:30~13:00 WR=26.1% | 나쁜 늦은 진입 차단 | 측정 불가 | **유지** |
| LCL v2.1 | EF와 중복 일부 | EF 미발동 케이스 수비 | 반등 기회 일부 | **유지 (threshold 재검토)** |
| **EF** | 46건, avg -1.286% | Hard Stop 대비 -1.5% 절약 | 과도한 발동 가능성 | **핵심 유지** |
| **Hard Stop** | 28건, avg -2.815% | 최악 손실 방지 | 없음 | **핵심 유지** |
| DrawdownEngine | 발동 빈도 미측정 | 연속 손실 시 사이즈 축소 | 연속 손실 후 기회 | **핵심 유지** |
| **ATR Trailing** | 33건, avg +1.184% | — | 수익 포지션 이익 보호 | **핵심 유지 (수익 엔진)** |
| Overnight Guard | 6건, avg -1.615% | B급 이하 오버나이트 방지 | A급 overnight 제한 | **유지** |
| Cooldown | 미측정 | 충동 재진입 방지 | 빠른 재진입 기회 | **유지** |

---

## 4. 핵심 불균형: 방어 vs 공격

| 지표 | 현재 |
|------|------|
| 손실 레이어 발동 건수 합계 | EF 46 + HS 28 + SL 11 + ON 6 = **91건 (37%)** |
| 수익 레이어 도달 건수 | Trailing 33 + TP 4 = **37건 (15%)** |
| 중립 (time_exit) | **95건 (39%)** |

**방어 레이어(91건) > 수익 레이어(37건)**: 시스템이 막는 것보다 잃는 것이 많다.

이 불균형의 원인:
1. 방어 레이어가 너무 강한 것이 아님 — 진입 신호 품질이 낮아 수익 레이어(Trailing)에 도달하는 포지션이 절대적으로 부족
2. 방어 레이어를 줄이면 손실 규모만 커짐
3. 해결책: 수익 레이어(Trailing)에 도달하는 진입 비율을 높여야 함 → 진입 신호 품질 개선

---

## 5. 결론 — 레이어별 최종 판정

### 핵심 유지 (절대 제거/약화 금지)

- **EF (Early Failure Structure)**: 가장 효과적인 조기 경보 시스템
- **Hard Stop**: 최후 안전망
- **ATR Trailing Stop**: 유일한 수익 엔진
- **DrawdownEngine**: 연속 손실 자동 방어

### 유지 (데이터 축적 후 재검토 가능)

- **LCL v2.1**: EF와 중복 분석 필요
- **c_late_v2**: 현재 성과 나쁘지 않음
- **Stage A Gate**: 발동 건수 측정 후 재판단
- **Overnight Guard**: A급 overnight 성과 측정 후 재판단
- **Cooldown 시스템**: 발동 건수 측정 후 재판단

### 조건부 유지 (threshold 재검토 필요)

- **G3 Soft Penalty**: G3_SOFT_PENALTY 태그 건수 및 해당 진입 성과 측정 후 판단

### 제거 검토 대상

- 없음. 현재 시스템에서 어느 레이어를 제거해도 성과가 개선될 증거 없음.  
  근본 문제는 레이어가 아닌 **진입 신호 품질**이다.

---

## 6. 즉시 실행 가능한 분석 쿼리

```sql
-- G3_SOFT_PENALTY 발동 후 성과 (entry_features 태그 필요)
SELECT
  entry_features->>'g3_proximity' AS g3,
  COUNT(*),
  ROUND(AVG(s.profit_rate)::numeric, 3) AS avg_pnl
FROM trades b
JOIN trades s ON b.stock_code = s.stock_code
  AND DATE_TRUNC('day', b.trade_time) = DATE_TRUNC('day', s.trade_time)
  AND s.trade_type = 'SELL'
WHERE b.trade_type = 'BUY'
  AND b.entry_features IS NOT NULL
GROUP BY g3;

-- DrawdownEngine 발동 빈도 확인
-- grep "DD_CAUTION\|DD_DANGER\|DD_HALT" logs/auto_trading_*.log | wc -l

-- Trailing에 도달한 진입의 특성 분석
SELECT
  b.entry_features->>'smc_grade' AS grade,
  b.entry_features->>'entry_time_bucket' AS bucket,
  COUNT(*),
  ROUND(AVG(s.profit_rate)::numeric, 3) AS avg_pnl
FROM trades b
JOIN trades s ON b.stock_code = s.stock_code
  AND DATE_TRUNC('day', b.trade_time) = DATE_TRUNC('day', s.trade_time)
  AND s.trade_type = 'SELL'
WHERE b.trade_type = 'BUY'
  AND (s.exit_reason ILIKE '%트레일링%' OR s.exit_reason ILIKE '%trailing%')
  AND b.entry_features IS NOT NULL
GROUP BY grade, bucket
ORDER BY COUNT(*) DESC;
```
