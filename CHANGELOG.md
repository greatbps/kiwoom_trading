# 변경 이력 (Changelog)

---

## v2.8.0-beta patch2 — 2026-06-28 | Architecture Acceptance Test

### 추가
- **`analysis/acceptance_test.py`** — Trading OS 아키텍처 검증 도구
  - 32개 항목, 5개 카테고리
  - 실행: `python3 -m analysis.acceptance_test`

```
Category A (7): DB Schema & Integrity
Category B (3): Constitution Compliance (불변성 계약)
Category C (6): Research Layer Isolation (Rule Engine 독립성)
Category D (4): Data Contract Compliance
Category E (12): Operational Readiness
```

### 최초 실행 결과 (2026-06-28)
```
32/32 PASS — Architecture Acceptance Test 통과
v2.8.0-beta Release Candidate 조건 충족
```

### 검증 원칙
Unit Test(함수) + Integration Test(연결) + **Architecture Acceptance Test(약속)**.  
세 번째가 이 시스템의 핵심 검증이다:  
"Rule Engine은 항상 독립적으로 실행되고, Research는 Execution을 오염시키지 않는다."

---

## v2.8.0-beta patch — 2026-06-28 | Governance Framework + Architecture Stability Rules

### 추가
- **`docs/governance/GD-001`** — 첫 번째 Governance Decision 기록
  - "Platform v2.8.0 Release Deferred" — Constitution이 실제 결정을 통제한 첫 사례
  - Evidence 부재 → Article 6+7 적용 → beta 유지 결정 기록
- **`CONSTITUTION.md`** — 세 가지 추가
  - "No Architecture Changes Without Evidence" — Architecture 수정 가능 조건 3개 명문화
  - Governance Decision Records 목록 (GD-001~)
  - Operating Cadence — Daily/Weekly/Monthly/Quarterly 운영 주기

### 의미
GD-001은 단순 기록이 아니다.  
Constitution이 "운영 데이터 없이 v2.8.0은 아직 아니다"고 판단한 첫 번째 사례.  
이 결정 이후 프로젝트에서 증명 책임(Burden of Proof)이 역전되었다:  
*기능이 있으니까 Release* → *증명했으니까 Release*

---

## v2.8.0-beta — 2026-06-27 | Trading OS Foundation Complete

> **Architecture v1.0 완성. Platform v2.8.0은 운영 검증 후 릴리스.**
>
> 설계가 완성되었다고 플랫폼이 완성된 것은 아니다.  
> 아키텍처는 증거가 아직 없는 가설이다. 운영 데이터가 이것을 검증해야 한다.

### 추가
- **`docs/adr/`** — Architecture Decision Records (5개)
  - ADR-001: AI는 실주문하지 않는다 (Latency/Reproducibility/Governance)
  - ADR-002: Research Queue — Unknown은 유효한 상태 (억지 결론 거부)
  - ADR-003: Decision Log는 Append-Only (역사는 수정 불가)
  - ADR-004: Contract-Centric AI Plugin (모델 중심 거부, Research Driver Model)
  - ADR-005: Governance는 독립 엔진 (Research→Production 직통 거부)
- **Deprecation Policy** (`ARCHITECTURE.md`) — Strategy 생명주기 관리
  - Knowledge는 `is_active=FALSE` 비활성화, Strategy는 `retired` 은퇴 — 둘은 다르다

### 변경
- **`CONSTITUTION.md`** — 3번째 핵심 철학:  
  `"Trading OS는 모델 중심이 아니라 계약 중심이다."`

### 버전 체계

| 버전 | 의미 | 상태 |
|------|------|------|
| Architecture v1.0 | Constitution + Architecture + Data Contract 완성 | ✅ 완료 |
| Platform v2.8.0-beta | 구현 완료, 운영 검증 전 | ⬅️ 현재 |
| Platform v2.8.0 | 3~6개월 운영 및 검증 완료 | ⏳ 이후 |

### v2.8.0 정식 릴리스 조건 (모두 충족 시)

```
□ decision_log BUY ≥ 100건
□ scientist_predictions ≥ 50건
□ Calibration Grade 산출 가능 (evaluation_date 도달 예측 포함)
□ OS Health Report ≥ 8주 누적
□ Hypothesis → Experiment → Approval → Strategy 배포 최소 1 사이클 완료
```

이 조건은 Architecture Article 6("Every Improvement Must Be Measurable")과  
Article 7("Everything is an Experiment Until Proven Otherwise")을  
버전 정책에 적용한 것이다.

### Architecture v1.0 완성 현황

```
Constitution (Why)       3개 핵심 철학, 8개 조항
Architecture (How)       6원칙, 3엔진, Deprecation Policy, ADR 색인
Data Contract (Contract) 6객체 계약, AI Plug-in 규칙
CLAUDE.md (What)         운영 규칙
docs/adr/                거부한 대안들의 이유 (5개, 이후 추가)
```

---

## v2.7.0 — 2026-06-27 | Data Contract v1.0 + Event Bus v0 + P7

### 추가
- **DATA_CONTRACT.md** — Trading OS 4번째 핵심 문서
  - 6개 객체 계약 (market_context / trading_session / decision_log /
    hypothesis / knowledge_base / scientist_predictions / research_environment)
  - AI Plug-in 계약: `scientist_predictions` 계약 만족 시 어떤 AI도 연결 가능
  - Event Bus v0 이벤트 명세 + v1 마이그레이션 경로
  - 새 객체 추가 시 체크리스트
- **`system_events` 테이블** — Event Bus v0 (경량 이벤트 로그)
  - Research Layer가 Execution을 직접 폴링하지 않아도 되는 기반
  - event_type / source / entity_type / entity_id / payload JSONB
- **Constitution Article 7**: "Everything is an Experiment Until Proven Otherwise"

### 4계층 문서 구조 완성
```
CONSTITUTION.md   — 왜(Why): 8개 조항, 판단 기준
ARCHITECTURE.md   — 어떻게(How): 6원칙, 3엔진, Phase 로드맵
DATA_CONTRACT.md  — 무엇을(Contract): 객체 계약, AI Plug-in 규칙
CLAUDE.md         — 운영 규칙(What): 코딩 가이드, 일일 운영
```

### 정의 (2026-06-27 확정)
> Trading OS는 실시간 주문 시스템이 아니라, 증거 기반으로 전략을 진화시키는
> 연구 운영체제이며, Rule Engine·Scientist AI·Governance를 동일한 Calibration
> 규칙 아래 평가하는 플랫폼이다.

---

## v2.6.0 — 2026-06-27 | OS Health Report (Phase 5 운영 지표)

### 추가
- **`analysis/os_health_report.py`** — Trading OS 플랫폼 자체 건강 측정
  - 전략 성과가 아니라 OS가 올바르게 운영되고 있는지를 측정
  - 매주 토요일 08:47 자동 실행
  - 섹션: Execution / Research / Knowledge / Scientist AI / Governance
  - Scientist AI 설명 정확도·예측 적중률 (Phase 3c 이후 채워짐)
  - 텔레그램 주간 리포트 발송
- **크론**: `47 8 * * 6` 토요일 08:47 (overnight weekly 직후)

### Trading OS v1.0 완성 선언 (2026-06-27)

토요일 아침 자동 리포트 전체 일정:
```
08:00  overnight_tracker --weekly   (전략 KPI 4개)
08:47  os_health_report             (플랫폼 건강 지표)
```

이 두 보고서가 함께 있으면 매주 두 가지 질문에 답할 수 있다:
- "이번 주 전략 성과는?" → overnight_tracker
- "이번 주 OS가 올바르게 작동했는가?" → os_health_report

---

## v2.5.0 — 2026-06-27 | Trading OS Constitution v1.0

### 추가
- **CONSTITUTION.md** — Trading OS 헌법 (설계 변경의 최종 판단 기준)
  - Article 1: Execution is Sacred
  - Article 2: Every Claim Needs Evidence
  - Article 3: History Cannot Be Rewritten
  - Article 4: Unknown is a Valid State
  - Article 5: AI is a Researcher, Not a Trader
  - Article 6: Evolution Must Be Measured
  - Article 7: Separation of Concerns
  - 핵심 철학: "데이터 → AI → 사람 순서 불변"
  - 설계 변경 판단 기준 7문항 (하나라도 "예"이면 중단)
  - 개정 조건 / 개정 불가 조건 명시
- ARCHITECTURE.md 상단에 CONSTITUTION.md 참조 추가

### 선언
이 버전으로 **설계 단계가 완전히 종료**된다.
이제부터 이 시스템의 주인공은 설계자가 아니라 **운영 데이터**다.
다음 변화는 decision_log, market_context, knowledge_base에 쌓이는 데이터가 이끌 것이다.

---

## v2.4.0 — 2026-06-27 | Architecture Principles v1.0 완성

### 아키텍처 확정
- **ARCHITECTURE.md** — Trading OS 공식 아키텍처 문서 (삭제 금지)
- **6대 원칙 확정** (P1~P6): P6 추가 — 모든 개선은 사전 선언 지표를 통과해야 함
- **Three Engine View**: Execution / Research / Governance 3엔진 구조 명시
- **Phase 3c 진입 조건 체크리스트** ARCHITECTURE.md에 포함

### 추가
- **`research_environment` 테이블** (Meta Governance)
  - RE-001 활성: Claude Haiku + MIE v1.0, scientist_level=0(L0)
  - hypotheses/knowledge_base/research_notebook/experiments/decision_log 모두 RE FK 연결
  - RE 변경 시 모든 연구 결과가 어느 환경에서 나왔는지 추적 가능
- **Scientist AI Level System** (L0~L4, ARCHITECTURE.md)
  - L0=설명 / L1=가설 초안 / L2=Queue 등록 / L3=실험 요청 / L4=PM 제안
  - Level 승격 = PM이 research_environment.scientist_level 수동 변경만 가능
- **Digital Twin — Parallel Research Cluster** (Phase 4 개념, ARCHITECTURE.md)
- **P6**: knowledge_base evidence_level 제약 + pre-registration 요건

### 이 버전으로 아키텍처 설계 단계 종료
다음 단계: Phase 3c 데이터 누적 + Scientist AI (L0) 첫 실행

---

## v2.3.0 — 2026-06-27 | Trading Session + Research Notebook

### 추가
- **`trading_sessions` 테이블** — 하루를 하나의 연구 단위로 정의 (Time Layer)
  - 07:32 MIE 실행 시 자동 오픈 (`status: open`)
  - 16:37 Session Review 시 마감 (`status: reviewed`)
  - market_context → decisions → trades → review → knowledge 하나의 축으로 연결
- **`research_notebook` 테이블** — AI 연구노트 스키마 (Scientist AI Phase 3c 대비)
  - hypothesis_id, experiment_id, session_id 교차 연결
  - `generated_hypothesis_ids[]`: "이 노트가 H-041을 만들었다" 계보 추적
- **`decision_log.session_id` FK** — 모든 BUY/SELL 결정이 해당 날의 세션에 자동 연결
- **`analysis/session_review.py`** — EOD AI 리뷰 엔진
  - 당일 거래 집계 + Claude Haiku로 리뷰 생성
  - major_theme, review_tags, next_session_note 자동 생성
  - 텔레그램 EOD 브리핑 발송
- **크론 등록**: `37 16 * * 1-5` 평일 16:37 세션 마감 자동 실행
- 기존 `trading_sessions` (실행 세션용) → `execution_sessions`로 보존

### 데이터 흐름
```
07:32  MIE        → market_context + trading_session (open)
09:xx  BUY        → decision_log.session_id = 오늘 session.id
16:37  Session Rev → trading_session (reviewed) + 텔레그램
토요일  Weekly     → 주간 overnight KPI 4개
```

### Scientist AI가 나중에 쓸 쿼리 예시
```sql
-- 세션 레짐별 평균 PnL
SELECT regime, AVG(daily_pnl_pct), COUNT(*) FROM trading_sessions GROUP BY regime;

-- Risk-Off 세션의 BUY 결정 신호 패턴
SELECT dl.signals FROM decision_log dl
JOIN trading_sessions ts ON ts.id = dl.session_id
WHERE ts.regime = 'Risk-Off' AND dl.decision_type = 'BUY';
```

---

## v2.2.0 — 2026-06-27 | Market Intelligence Engine (MIE)

### 추가
- **`market_context` 테이블** — Trading OS의 Context Layer (First-Class Object)
  - 매일 시장 레짐, 변동성 수준, 섹터 로테이션, 위험도/기회 점수 저장
  - 원시 데이터(raw_data JSONB) 보존 → 결정 시점 완전 재현 가능
- **`analysis/market_intelligence.py`** — Market Intelligence Engine
  - 출력 ①: `market_context` DB 레코드 (핵심 자산)
  - 출력 ②: 텔레그램 모닝 브리핑 (부산물)
  - 출력 ③: `knowledge_base` 레짐 이력 항목
  - Claude Haiku 사용 (빠름 + 저렴, 일일 브리핑 최적)
  - `--dry-run` / `--no-telegram` 옵션 지원
- **`decision_log.market_context_id` FK** — BUY 결정과 시장 컨텍스트 연결
  - `_get_today_market_context_id()` — 매 BUY마다 당일 컨텍스트 자동 조회
  - 연결 이후 "Risk-Off에서의 CHoCH A급 승률" 같은 쿼리 가능
- **크론 등록**: `32 7 * * 1-5` 평일 07:32 자동 실행

### 설계 원칙
```
market_context → decision_log → trades
      ↓                ↓
knowledge_base    strategy_versions
```
Scientist AI가 나중에 이 연결을 타고 "어떤 레짐에서 어떤 전략이 작동했는가"를 SQL 한 번으로 분석 가능.

---

## v2.1.0 — 2026-06-27 | Trading OS 기반 스키마

### 추가
- **5개 핵심 테이블 생성** (DB migration)
  - `hypotheses`: 가설 생명주기 (draft→deployed/archived, 9단계 상태 머신)
  - `experiments`: 가설당 N개 실험 (backtest/walk_forward/paper_trading)
  - `strategy_versions`: 전략 버전 이력 + yaml_snapshot
  - `knowledge_base`: 성공+실패 학습 보존 (finding_type: positive/negative/conditional)
  - `decision_log`: 모든 매매 결정 블랙박스 (신호 스냅샷 + 필터 결과)
- **v2.0.0 strategy_versions 등록** (yaml_snapshot 포함)
- **KB-001**: overnight B급 wrong_stop 패턴 지식 기록
- **H-001**: v1→v2 overnight 전환 가설 (status: deployed)
- **decision_log 연동**: execute_buy에서 BUY 결정 즉시 기록 (choch_grade, RSI, RVOL, EMA trend)

### 설계 원칙
- `parent_hypothesis_id`: 실패 가설이 발전하면 계보 추적 가능
- `knowledge_base_id`: Scientist AI가 중복 가설 생성 방지
- `experiment_ids[]`: 어떤 실험이 전략 버전에 반영되었는지 역추적

---

## v2.0.0 — 2026-06-27 | CODE FREEZE / 운영 단계 전환

### 운영 원칙 확정
- 전략 변경은 FORCED_CLOSE 50건 이상 + 충분한 통계적 근거 확보 후에만 수행
- 토요일 리포트를 기준으로 판단, 단일 거래 사례로 정책 변경 금지
- Champion/Challenger 비교는 누적 데이터 기준
- Calibration 안정화 전까지 Confidence 산식 고정

### 주요 변경
- **overnight_close 정책 v2_weak_trend 확정**
  - B/C급 + 손실 < -1.5% → FORCED_CLOSE
  - 등급 무관 손실 < -3.0% → FORCED_CLOSE (hard floor)
  - 그 외 → KEPT (스윙 오버나이트 보유)
- **overnight_tracking DB 구축** (`analysis/overnight_tracker.py`)
  - 4-layer 스키마: 결정 → 결과 → 경로(MFE/MAE/Path Shape) → 시장 컨텍스트
  - EOL (Expected Opportunity Loss) 측정
  - Confidence Calibration
- **주간 자동 리포트** (매주 토요일 08:00 크론)
  - KPI 1+2: Confidence 구간별 wrong 비율 + 기회비용(EOL)
  - KPI 3: Policy Version별 Expectancy
  - Confidence Calibration
  - KPI 4: 이번 달 실현 PnL
  - Health Check 블록 (✓/✗ 상태 + Warnings)
- **Dry Run 완료**: 0건/NULL/표본부족 예외 모두 검증

### v3 진입 조건 (모두 충족 시)
1. Calibration 표본 ≥ 50건
2. Calibration "잘 보정됨 ✓"
3. Policy Champion이 통계적으로 우세
4. Wrong Stop 비율 주간 안정화
5. 최소 2~3개월 운영 데이터 확보

---

## [2025-10-25] 병렬 처리 최적화

### ✨ 신규 기능
- **병렬 처리 시스템**: asyncio + ThreadPoolExecutor를 활용한 고성능 종목 분석
  - 최대 5개 종목 동시 처리
  - API 호출 제한 준수 (초당 5회)
  - 기존 대비 **3.94배 속도 향상**

### 📈 성능 개선
| 종목 수 | 순차 처리 | 병렬 처리 | 개선 효과 |
|--------|----------|----------|----------|
| 10개   | 1.7분    | 0.4분    | **75% 단축** |
| 100개  | 17.1분   | 4.3분    | **75% 단축** |

### 📝 수정된 파일
- `test/test_condition_search.py`: 조건검색 시스템에 병렬 처리 통합
- `test/benchmark_analysis.py`: 순차 처리 성능 벤치마크
- `test/benchmark_parallel.py`: 병렬 처리 성능 벤치마크
- `test/PERFORMANCE_BENCHMARK_RESULTS.md`: 성능 측정 결과 문서

### 🔧 기술 세부사항
- **병목 구간 분석**:
  - Gemini API 호출: 3-4초 (뉴스 분석)
  - 수급 분석 API: 3-4초
  - 기술 분석: 1-2초
  - 기본 분석: 0.5-1초

- **최적화 방법**:
  ```python
  # ThreadPoolExecutor로 병렬 실행
  with ThreadPoolExecutor(max_workers=5) as executor:
      tasks = [
          loop.run_in_executor(
              executor,
              analyze_single_stock_sync,
              stock_code,
              api,
              engine,
              strategy,
              threshold
          )
          for stock_code in chunk
      ]
      results = await asyncio.gather(*tasks)
  ```

### ⚙️ 사용 방법

#### 순차 처리 벤치마크
```bash
source venv/bin/activate
python test/benchmark_analysis.py
```

#### 병렬 처리 벤치마크
```bash
source venv/bin/activate
python test/benchmark_parallel.py
```

#### 조건검색 시스템 (병렬 처리 적용)
```bash
source venv/bin/activate
python test/test_condition_search.py
```

### 📊 실측 결과
```
병렬 처리 최적화 성공!

개선 사항:
  • 10개 종목: 102.7초 → 26.1초 (75% 단축)
  • 100개 종목: 17.1분 → 4.3분 (75% 단축)
  • 속도 향상: 3.94배 빠름!

기술 스택:
  • Python asyncio + ThreadPoolExecutor
  • 최대 5개 종목 동시 처리
  • API 호출 제한 준수 (초당 5회)
```

---

## [2025-10-24] 업종 상대평가 통합

### ✨ 신규 기능
- **업종 상대평가 시스템**: 종목을 업종 평균과 비교하여 밸류에이션 평가
  - 950개 종목 → 45개 업종 매핑 완료
  - PER/PBR/ROE 업종 평균 대비 평가
  - 기본 분석 점수에 통합

### 📝 수정된 파일
- `analyzers/fundamental_analyzer.py`: 업종 상대평가 로직 추가
- `db/sector_data_manager.py`: `get_sector_averages_by_name()` 메서드 추가
- `scripts/update_sector_averages.py`: 업종 평균 업데이트 스크립트

### 🗄️ 데이터베이스
- `stock_sector_mapping` 테이블: 950개 종목 매핑 완료
- `sector_averages` 테이블: 45개 업종 평균 지표

---

## [2025-10-23] 분석 시스템 완성

### ✨ 신규 기능
- **4개 분석 엔진 통합**: 뉴스(30%) + 기술(40%) + 수급(15%) + 기본(15%)
- **매매 전략 시스템**: 진입/청산 신호 생성, 리스크 관리
- **조건검색 자동매매**: WebSocket 기반 실시간 조건검색

### 📊 분석 점수 체계
- 뉴스 분석: 100점 만점 (30% 가중치)
- 기술 분석: 100점 만점 (40% 가중치)
- 수급 분석: 50점 → 100점 정규화 (15% 가중치)
- 기본 분석: 50점 → 100점 정규화 (15% 가중치)

### 🎯 점수 임계값
- 70점 이상: 2차 필터링 통과
- 80점 이상: 적극 매수 추천

---

**작성일**: 2025-10-25
**최종 수정**: 2025-10-25
