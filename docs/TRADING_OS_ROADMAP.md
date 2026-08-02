# Trading OS 확장 로드맵

> **문서 목적**: 스윙 전략 이후 Trading OS를 어디까지 어떤 순서로 확장할지 정의하는 상위 설계 문서  
> **대상 독자**: 총괄 매니저, 장기 설계자  
> **최종 갱신**: 2026-07-05 | ruleset_version v1.3.1-final  
> **현재 상태**: Single strategy engine (SMC), PF=0.407, E1 단계

---

## ⚠️ 전제 조건

**현재 시스템은 수익을 내지 못하고 있다 (PF=0.407).**

이 로드맵은 "미래 비전"을 위한 설계이지만, Phase A가 해결되기 전까지 Phase B, C는 시작하지 않는다.  
"더 큰 시스템"이 "더 잘 버는 시스템"을 의미하지 않는다.  
확장은 현재 전략이 수익을 낼 때 의미가 있다.

---

## 1. 현재 위치

### 현재 구현 상태 (2026-07-05 기준)

```
Layer 0 (브로커/데이터)     키움증권 API, PostgreSQL, 실시간 가격
Layer 1 (신호 생성)         SMC (CHoCH + Sweep + OB) 단일 전략
Layer 2 (필터/게이트)       Signal Orchestrator L0~L6, AI Gate
Layer 3 (포지션 사이징)     DrawdownEngine + Session Guard + Cooldown
Layer 4 (청산 관리)         EF + LCL + Hard Stop + ATR Trailing
Layer 5 (연구/검증)         AI Research Layer (Observer/Analyst/Scientist/Governance)
```

### 현재 미완성

```
- RAE (2nd wave entry): 구현됨, disabled
- ML Filter: Shadow 모드
- Portfolio: 없음 (단일 전략)
- Multi-strategy: 없음
- Capital allocation: 고정 기준 (동적 배분 없음)
```

### 핵심 한계

| 한계 | 설명 |
|------|------|
| 단일 알파 소스 | SMC 신호 하나에만 의존. 시장 레짐 변화에 취약 |
| 포트폴리오 없음 | 종목별 독립 관리, 상관관계 고려 안 함 |
| 자본 배분 정적 | 전략 성과에 따른 동적 배분 없음 |
| 연구-운영 분리 미흡 | Scientist가 제안해도 승인 → 구현 파이프라인이 느림 |

---

## 2. 5계층 목표 아키텍처

```
현재          목표 (장기)
─────         ─────────────────────────────────────────────────────
Layer 0:      Data/Broker Layer
              키움 API, 시장 데이터, PostgreSQL
              (현재 완성)

Layer 1:      Strategy Engine
              SMC (현재) → SMC + RAE + 추가 전략들
              각 전략은 독립 신호 + 독립 출력

Layer 2:      Portfolio / Risk OS
              전략 간 자본 배분, 종목 상관관계
              전략별 drawdown 독립 추적
              (현재 없음)

Layer 3:      Research / Validation OS
              가설 → 실험 → 증거 → 승인 파이프라인
              Scientist가 자동으로 실험 설계 → 검증
              (현재: 수동 + AI Research Layer)

Layer 4:      AI Supervisor / Governance
              Constitution 8조항 자동 검토
              전략 성과 자동 평가 + 배분 조정 제안
              (현재: Governance AI 온디맨드)
```

---

## 3. Phase A — 현재 전략 완성 (2026 Q3~Q4)

**목표**: PF ≥ 1.0, WR ≥ 30%, avg_pnl ≥ +0.2% 달성  
**기간**: P0 조건 달성까지 (시간 제한 없음)  
**상태**: 진행 중

### A-1: 진입 신호 품질 검증 (최우선)

**현재 문제**: EF 발동 46건(19%), WR 22.5%, PF 0.407  
**가설**: CHoCH 방향성 예측 정확도가 50% 미만일 가능성

| 작업 | 방법 | 측정 지표 |
|------|------|-----------|
| CHoCH 등급별 방향성 분석 | BUY 후 30분 내 가격 방향 vs CHoCH 방향 | 방향 일치율 ≥ 55% |
| EF 발동 건 vs Trailing 도달 건 비교 | entry_features 태그 활용 | EF 건의 공통 특성 파악 |
| 시장 레짐별 성과 분리 | market_context regime 기반 | TREND/RANGE별 PF 비교 |

**현재 구현 필요**: `analysis/entry_quality_analyzer.py` (미존재)

### A-2: RAE Go-Live (PF ≥ 1.0 달성 후)

상세 절차: `docs/RAE_GO_LIVE_PLAYBOOK.md` 참조  
**선결 조건**: Primary PF ≥ 1.0 달성 (현재 0.407)

### A-3: ML Filter 실전 전환 (데이터 50건 + AUC ≥ 0.60)

**현재**: Shadow 모드  
**전환 조건**:
1. 50건 이상 레이블된 데이터
2. AUC ≥ 0.60
3. Shadow vs Live 성과 비교 4주

### A-4: E2 달성 및 주간 심사 체계 확립

**현재**: E1 (29건 수준)  
**E2 달성 조건**: 30건 + `docs/E2_REVIEW_CHECKLIST.md` 5개 항목 충족  
**E2 이후**: Scientist AI 판단 신뢰도 상승, 가설 검증 속도 향상

---

## 4. Phase B — 전략 프레임워크 일반화 (2027 이후 조건부)

**전제 조건**: Phase A 완료 (PF ≥ 1.0, WR ≥ 30%)  
**목표**: 단일 SMC 전략 → 복수 전략을 수용하는 확장 가능한 프레임워크

### B-1: 전략 추상화 레이어

현재 `main_auto_trading.py`는 SMC에 특화되어 있다.  
전략을 독립 모듈로 분리하여 새 전략 추가가 YAML 설정으로 가능하도록:

```yaml
# 목표 구조 (현재 없음)
strategies:
  smc_primary:
    enabled: true
    class: SMCStrategy
    weight: 1.0
  rae_extension:
    enabled: false
    class: RAEStrategy
    weight: 0.5
  momentum:
    enabled: false
    class: MomentumStrategy
    weight: 0.0
```

### B-2: Portfolio Risk OS

복수 전략 동시 운영 시 필요한 포트폴리오 레이어:

| 기능 | 설명 |
|------|------|
| 자본 배분 엔진 | 전략별 성과에 따른 동적 비율 조정 |
| 상관관계 관리 | 동일 업종/레짐 과집중 방지 |
| 전략별 DrawdownEngine | 각 전략 독립 손실 추적 |
| 포트폴리오 레벨 Hard Stop | 전체 시스템 손실 한도 |

**현재 상태**: 설계 단계 미진입 (Phase A 완료 전까지 설계 보류)

### B-3: 추가 전략 후보 (검토 단계, 구현 아님)

**스윙 전용 원칙**을 유지하는 범위 내:

| 전략 후보 | 개요 | 조건 |
|-----------|------|------|
| Momentum 스윙 | 강한 추세 초기에 진입, SMC와 다른 레짐 | Phase B 이후 |
| Mean Reversion 스윙 | 과매도 구간 되돌림 | Phase B 이후 |
| Sector Rotation | 업종 로테이션 기반 종목 선정 | Phase C 이후 |

⚠️ 위 전략은 **아이디어 목록**이다. Research Notebook에만 기록하며 구현 계획이 없다.

---

## 5. Phase C — Portfolio/Strategy Approval OS (2027~ 조건부)

**전제 조건**: Phase B 완료 (복수 전략 안정 운영)  
**목표**: AI Supervisor가 전략 배분을 자동으로 제안하고, Governance가 승인하는 체계

### C-1: Research/Validation OS 자동화

현재 Scientist는 매주 금요일 수동 가설 검증. 목표:

```
가설 제안 (Scientist)
     ↓
실험 설계 자동화 (Research OS)
     ↓
Shadow 실험 자동 실행
     ↓
증거 수집 → E2 달성 확인
     ↓
승인 요청 → Governance 자동 검토
     ↓
YAML 파라미터 조정 (제한적 자동화)
```

### C-2: AI Supervisor 역할 확대

현재 Governance AI: Constitution 8조항 검토 (온디맨드)  
목표: 전략 성과를 주간 자동 평가 → 배분 조정 제안

```
매주 월요일 자동 실행:
1. 전략별 최근 4주 PF/WR/avg_pnl 계산
2. 부진 전략 → 배분 축소 제안
3. 우수 전략 → 배분 확대 제안
4. Human 승인 필요 (자동 집행 없음)
```

---

## 6. 현재 금지 사항 (Constitution 준수)

CLAUDE.md Phase A 금지 사항 (2026 Q3):
- 새로운 AI Agent / LLM / Dashboard / Strategy 추가
- 운영 스키마 신규 DB 테이블 (research 스키마는 허용, GD-007)
- 구현 없이 설계만 하는 "Architecture 변경"

이 로드맵 자체는 **계획 문서**이며 구현 승인 문서가 아니다.  
각 Phase 진입 시 별도 GD (Governance Decision)이 필요하다.

---

## 7. 로드맵 요약표

| Phase | 목표 | 선결 조건 | 예상 시작 | 현재 상태 |
|-------|------|-----------|-----------|-----------|
| **A-1** | 진입 신호 품질 검증 | 없음 | **즉시** | 미시작 |
| **A-2** | RAE Go-Live | Primary PF ≥ 1.0 | PF 달성 후 | 보류 |
| **A-3** | ML Filter 실전 전환 | 50건 + AUC≥0.60 | 데이터 달성 후 | Shadow 중 |
| **A-4** | E2 달성 | 30건 + KPI | 거래 누적 후 | E1 (29건) |
| **B** | 전략 프레임워크 일반화 | Phase A 완료 | 2027 이후 | 미설계 |
| **C** | Portfolio/Strategy OS | Phase B 완료 | 2028 이후 | 미설계 |

**다음 단계**: A-1 (진입 신호 품질 분석)부터 시작. 이것이 모든 Phase의 출발점.
