# Change Control — Baseline v1.1

> 확정일: 2026-07-02  
> 적용 대상: kiwoom_trading 전체 코드·YAML

---

## 현재 버전

```
ruleset_version: v1.1
상태: Engineering Complete (실거래 검증 진행 중)
```

변경 이력:
- v1.0 — 초기 운영 (스윙 전략 기본 구성)
- v1.1 — 2026-07-02: AI Gate(min=50) + EXPLORATION T+1 + ENTRY_SNAPSHOT + RULESET_VERSION
  - E2E Test: 74/77 PASS, FAIL 0건, Failure Injection 8/8 통과

---

## 증거 등급 (Evidence Level)

> Scientist 제안은 항상 이 등급과 함께 생성된다.  
> `strategy_ai.py`가 자동 판정, `approve_proposal.py`가 E2 미만은 시스템 수준에서 차단한다.

| 등급 | 의미 | 표본 | 정책 변경 |
|---|---|---|---|
| E0 | 가설 (표본 부족) | < 10건 | ❌ 불가 |
| E1 | 초기 경향 | 10~29건 | ❌ 불가 |
| E2 | 통계적 근거 확보 | 30건+, KPI 충족 | ⚠️ 검토 가능 |
| E3 | 반복 재현 | 여러 기간에서 동일 결과 | ✅ v1.2 후보 |

**중요:** E2라도 사람의 검토가 필수다. E2는 "검토를 시작할 수 있다"는 의미이지, "자동 승인"이 아니다.

---

## v1.1 운영 품질 KPI (Production Proven 조건)

> "Engineering Complete"와 "Production Proven"은 다르다.  
> 아래 KPI를 모두 달성해야 v1.1이 완전히 검증된 것으로 간주한다.

| KPI | 목표 | 측정 방법 |
|---|---|---|
| 실거래 수 | 30건 이상 | `ops_weekly_review` → trades.total |
| ENTRY_SNAPSHOT 누락 | 0건 | `ops_daily_check` → ENTRY_SNAPSHOT 항목 |
| AI Gate 오동작 | 0건 | `ops_daily_check` → AI Timeout/오류 |
| 주문 실패율 | < 0.5% | 주문 실패 / 총 주문 |
| 시스템 예외로 인한 거래 실패 | 0건 | `ops_daily_check` → Exception 항목 |
| 로그 복원 가능률 | 100% | 로그 파일 생성 항목 |
| Scientist 제안 생성 성공률 | 100% | `strategy_ai.py` 실행 후 PENDING 상태 |
| Strategy Proposal 승인 프로세스 | 100% 정상 | `approve_proposal.py --list` 응답 |

---

## v1.1 운영 중 변경 규칙

### ✅ 즉시 수정 허용 (버그/장애)

| 사유 | 예시 |
|---|---|
| 치명적 버그 | 주문이 실행되지 않음, DB INSERT 실패 |
| 주문 오류 | 수량 계산 오류, 가격 오류 |
| API 변경 대응 | 키움 API 스펙 변경 |
| 데이터 손실 | 로그 미생성, DB 연결 끊김 |

판단 기준: **"지금 이 버그를 고치지 않으면 실계좌에 손실이 발생하는가?"**  
YES → 즉시 수정. NO → S7 이후로 미룸.

### `--force` 사용 규칙 (approve_proposal.py)

`--force`는 E2 미달인 제안을 강제 승인하는 옵션이다.  
**성능 개선 목적으로는 절대 사용하지 않는다.**

| 허용 사유 | 예시 |
|---|---|
| 치명적 버그 수정 | 잘못된 손절 로직, 주문 실패 |
| 주문/청산 오류 대응 | 가격 오류, 수량 계산 오류 |
| 데이터 손실 방지 | DB 스키마 긴급 수정 |
| API 정책 변경 대응 | 키움 API 스펙 변경 |

| 금지 사유 | 예시 |
|---|---|
| 성능 개선 | "승률이 낮아 보여서" |
| 파라미터 조정 | "AI Gate 50→55 해보고 싶어서" |
| 시장 환경 대응 | "최근 시장이 안 좋아서" |
| 직관적 판단 | "이 제안이 맞을 것 같아서" |

`--force` 사용 시 `force_used: true`가 기록에 남는다.  
주간 리뷰 체크 항목: `--force 사용 건수 = 0이어야 정상`

### ❌ S7(20~30건) 이후로 미루는 변경

다음은 "좋아 보인다"는 이유만으로는 수정하지 않는다.

- AI Gate min_score 조정 (50 → 55 등)
- MKT_CTX를 진입 게이트에 추가
- 새 진입 조건 추가
- 포지션 사이즈 변경
- 쿨다운 일수 변경
- 리스크 파라미터 변경

**허용되지 않는 이유의 예:**
- "승률이 조금 낮은 것 같다."
- "AI 점수를 55로 올려볼까?"
- "조건 하나만 더 넣자."
- "직관적으로 이게 더 나을 것 같다."

이런 변경은 반드시 데이터가 먼저다.

---

## v1.2 진입 조건 (4개 모두 충족 시)

> 이 4가지를 동시에 만족할 때만 v1.2 개발을 시작한다.  
> 하나라도 미달이면 v1.1 운영을 유지한다.

```
□ 1. 실거래 30건 이상 확보
□ 2. 위 KPI 8개 모두 달성
□ 3. Scientist가 동일한 개선 방향을 2주 이상 연속 제안
□ 4. 사람이 그 근거를 검토해도 타당하다고 판단
```

**판단 기준:**  
조건 3은 Scientist의 `findings`와 `conclusion`을 두 차례 이상 비교해서  
일관된 방향(예: "AI Score 60 이상에서 수익, 이하에서 손실")이 나타날 때 충족된다.

조건 4는 자동 승인 없음. 반드시 사람이 `approve_proposal.py --approve` 실행.

**버전 올릴 때 수정할 파일:**
1. `python3 -m analysis.approve_proposal --approve <PROP-ID>`  → ruleset_version 자동 증가
2. `config/strategy_hybrid.yaml` → 파라미터 수동 반영
3. `python3 -m analysis.e2e_integration_test` → 회귀 검증
4. 이 문서 상단 버전 이력 추가

---

## 일일/주간 점검 명령

```bash
# 매일 장 종료 후 (5분)
python3 -m analysis.ops_daily_check

# 매주 금요일 장 후
python3 -m analysis.ops_weekly_review

# Decision Health Check
python3 -m analysis.decision_health_check
```

---

## v1.2 설계 후보 (데이터 확보 후 검토)

다음은 현재 아이디어 단계. S7 분석 결과가 지지할 때만 구현.

| 아이디어 | 검증 필요 데이터 |
|---|---|
| AI Gate min_score 상향 | AI 50~59 구간 승률 |
| MKT_CTX 진입 게이트 활성화 | HH/HL vs LH/LL 승률 차이 30%+ |
| EXPLORATION 보유 기간 최적화 | T+0/T+1/T+2 기대수익 비교 |
| 진입 시각별 필터 | 09시/장중/15:35 성과 차이 |
