목적

이 문서는 다음 두 가지를 통합한 프로젝트 실행 지침이다.

LLM 코딩 실수 방지용 일반 엔지니어링 원칙
Kiwoom Trading 실거래 프로젝트 전용 운영 규칙

본 프로젝트는 실계좌 자동매매 시스템이다.
속도보다 안정성, 재현성, 최소 변경, 검증 가능성을 우선한다.

⚠️ 절대 원칙 (2026-05-11 확정)
- 매매 전략: 스윙(Swing)만 한다. 인트라데이/단타 전략 없음.
- DB: PostgreSQL만 사용한다. SQLite 절대 금지.
- 분석/코드 작업 시 이 두 가지를 반드시 전제로 한다.

1. 핵심 원칙 (Global Engineering Rules)
1.1 구현 전 사고 (Think Before Coding)

코드를 쓰기 전에 반드시 읽는다.

관련 파일을 먼저 읽는다. 읽지 않고 추측하지 않는다.
main_auto_trading.py는 5000줄이다. 함수 전체를 파악한 뒤 수정한다.
기존 패턴을 확인한 후 동일 패턴을 따른다.

가정하지 않는다.
모호함을 숨기지 않는다.
트레이드오프를 명확히 밝힌다.

구현 시작 전 반드시:

자신의 가정을 명시한다.
불확실하면 질문한다.
해석 가능성이 여러 개라면 대안을 제시한다.
더 단순한 접근이 있으면 먼저 제안한다.
불명확하면 작업을 멈추고 질문한다.
반드시 피할 것
"아마 이럴 것이다" 기반 구현
사용자 의도 추정 후 독단적 변경
설정 없이 하드코딩 추가
요청되지 않은 구조 개선
1.2 단순성 우선 (Simplicity First)

최소한의 코드만 작성한다.

원칙
문제 해결에 필요한 최소 코드만 작성
요청되지 않은 기능 추가 금지
추측 기반 추상화 금지
일회성 코드에 클래스/레이어 추가 금지
미래 확장성을 이유로 복잡성 추가 금지
불가능한 시나리오에 대한 방어 코드 금지
자가 점검

다음 질문에 YES면 다시 단순화:

"시니어 엔지니어가 보기에 과하게 복잡한가?"

1.3 정밀한 수정 (Surgical Changes)

필요한 부분만 수정한다.

기존 코드 수정 시
인접 코드 건드리지 않는다
포맷팅 임의 변경 금지
리팩토링 금지
기존 스타일 유지
unrelated dead code 삭제 금지
단, 자신의 수정으로 인해 생긴 것은 정리

허용:

불필요 import 제거
미사용 변수 제거
본인이 만든 dead code 제거

금지:

기존 legacy dead code 정리
unrelated cleanup
변경 검증 기준

변경된 모든 라인은 사용자 요청과 직접 연결되어야 한다.

1.4 목표 중심 실행 (Goal-Driven Execution)

작업은 반드시 검증 가능한 목표로 변환한다.

예시:

"버그 수정"
→ 버그 재현 → 테스트 → 통과 확인
"유효성 검사 추가"
→ 잘못된 입력 테스트 추가 → 통과 확인
"리팩토링"
→ 전후 동작 동일성 검증
1.5 다단계 작업 수행 방식

복잡 작업은 항상 계획 기반으로 진행한다.

형식:

[작업]
→ 검증: [확인 사항]
[작업]
→ 검증: [확인 사항]
[작업]
→ 검증: [확인 사항]
2. 프로젝트 개요

키움증권 API 기반 SMC(Smart Money Concept) 자동매매 시스템.

⚠️ 실계좌 운용 중
코드 변경은 항상 보수적으로 접근한다.

3. 핵심 실행 파일
파일	역할
main_auto_trading.py	메인 트레이딩 루프 (~5000+ lines)
config/strategy_hybrid.yaml	전략 파라미터 (코드 수정 전 YAML 먼저)
watchdog.py	프로세스 감시/재시작
api_server.py	대시보드 백엔드 API (port 8765)
swing_runner.py	스윙 종목 선정 크론 (15:35 실행)
swing_executor.py	스윙 매수 실행 크론 (09:00 실행)
4. 시스템 아키텍처
main_auto_trading.py
├── Signal Orchestrator (L0~L6 독립 파이프라인)
│   └── logs/signal_orchestrator.log
├── SMC Strategy (CHoCH → Sweep → OB → Entry)
│   ├── analyzers/smc/smc_signals.py      ← EDT 필터 포함
│   ├── analyzers/smc/smc_structure.py
│   └── analyzers/smc/smc_utils.py
├── core/risk_manager.py
├── core/edt_sizer.py                     ← Kelly × 섹터/시장 세션 가드
├── core/drawdown_engine.py               ← DD 레벨 (NORMAL/CAUTION/DANGER/HALT)
├── core/market_context.py
├── metrics/reentry_metrics.py
└── trading/exit_logic_optimized.py

분석/오프라인 도구
├── analysis/edt_performance_tracker.py  ← EDT 성과 추적 + Kelly 갱신
├── swing_runner.py                       ← 스윙 종목 선정
└── swing_executor.py                     ← 스윙 매수 실행
5. 중요 전략 구조
Signal Orchestrator 와 SMC는 독립
Orchestrator ACCEPT ≠ SMC 진입

둘 다 조건 충족해야 매수 가능.

6. SMC 진입 흐름
check_entry_signal()
  → 시간 필터
  → Market Context / Market Sensor
  → smc_strategy.check_entry_signal()
      → analyze_structure()
      → detect_choch()
      → detect_liquidity_sweep()
      → check_entry_prefilter()
      → displacement_filter
      → evaluate_choch_grade()
      → signal=True
      → execute_buy()
7. 포지션 사이즈 체계
케이스	Size
A급 + Sweep	~100%
B급 + Sweep	~40%
B급 fallback	~20%
C급 fallback	~12%
추가 감산
Conservative Mode → ×0.5
Loss Streak Guard → ×LSG_mult
8. 핵심 YAML 규칙

전략 수정 시 우선순위:

코드 수정 < YAML 파라미터 조정

새 기능 추가 시:

YAML 설정 추가
config.get() 사용
enabled: true/false 포함
9. 절대 금지 사항
실거래 위험 행동 금지
금지
execute_buy() 직접 호출 추가
execute_sell() 직접 호출 추가
risk_log.json 임의 조작
time_filter 비활성화
dry-run 없이 테스트
보호 로직 우회
Hard Stop 우회
LSG 우회
api_server.py 포트(8765) 임의 변경
.env 파일 내 API 키 코드에 직접 삽입

긴급 정지 절차 (시스템 이상 시)
# 1. 자동매매 즉시 중단
kill $(pgrep -f "main_auto_trading.py")

# 2. watchdog 중단 (자동재시작 방지)
kill $(pgrep -f "watchdog.py")

# 3. api_server 중단
kill $(pgrep -f "api_server.py")

# 4. 포지션 확인 후 수동 청산 (HTS에서 직접)
python3 check_account_pnl.py
10. 개발 전 체크리스트

코드 수정 전 반드시:

실계좌 영향 여부 판단
YAML 조정으로 해결 가능한지 확인
변경 범위 최소화
영향 파일 명시
성공 기준 정의
11. 개발 후 필수 검증
최소 검증
python3 -m py_compile <파일>
주요 검증
python3 -m py_compile main_auto_trading.py
python3 -m py_compile analyzers/smc/smc_signals.py
12. 로그 시스템
주요 로그 파일
파일	용도
logs/signal_orchestrator.log	Orchestrator 결과
logs/smc_decision_YYYYMMDD.log	CHoCH 기록
logs/sweep_attempt_YYYYMMDD.log	Sweep 탐지
logs/auto_trading_YYYYMMDD.log	메인 로그
logs/auto_trading_errors.log	에러 로그
logs/reentry_report_YYYY-MM-DD.json	재진입 리포트
data/risk_log.json	연패 기록
13. 로그 태그 규칙

새 로그는 반드시:

[TAG_NAME]

형식 사용.

예시:

[C_GRADE_FALLBACK]
[LSG_BLOCK]
[TREND_SIG]
[DISP_BLOCK]
14. Trend Breakout 전략
활성 조건
SMC Sweep 부족
→ regime == TREND
→ TrendBreakoutStrategy 활성
진입 유형
BREAKOUT
N봉 고점 돌파
거래량 증가
EMA 정배열
PULLBACK
EMA20 눌림
추세 유지
거래량 확인
15. 현재 전략 상태 (2026-05-07 기준)
기능	상태	비고
SMC 진입	활성	mode=smc
스윙 전략	활성	기본 전략 (2026-05-04~), 크론 등록
C_GRADE_FALLBACK	활성	일 최대 2회
Sweep Fallback (B급)	활성	일 최대 3회
EDT 필터	활성	check_early_downtrend() in smc_signals.py
EDT Sizer	활성	Kelly × 심볼가중 × DD캡 × 세션가드 4종
Session Guards	활성	HALT복구/섹터집중/무거운장/승리과열
Conservative Mode	비활성	Hard Stop 0회
Loss Streak Guard	비활성	연패 0회
Overnight Close	활성	B급 이하 14:50 강제청산
Market Context	활성	NO_TRADE_DAY 게이트
Trend Breakout	활성	레짐 TREND 감지 시, 일 최대 2회
SOXL	장기보유	자동매매 분석/손절 대상 아님 — 제외
16. 자주 사용하는 운영 명령
컴파일 검증
python3 -m py_compile main_auto_trading.py && \
python3 -m py_compile analyzers/smc/smc_signals.py && \
python3 -m py_compile core/edt_sizer.py && \
echo "OK"
거래 현황
grep "매수완료\|매도완료\|EDT_GUARD\|HALT" logs/auto_trading_$(date +%Y%m%d).log
연패 상태
python3 -c "
import json
d=json.load(open('data/risk_log.json'))
print('연패:', d['consecutive_losses'])
"
계좌/포지션 확인
python3 check_account_pnl.py
EDT Kelly 갱신 (오프라인)
python3 -m analysis.edt_performance_tracker --days 30
api_server 상태
curl -s http://localhost:8765/api/health
17. Claude 작업 행동 규칙
수정 제안 시

반드시 먼저:

변경 목적
영향 범위
리스크
대안
검증 방법

을 설명한다.

코드 작성 시

우선순위:

안정성 > 단순성 > 가독성 > 확장성 > 성능
실거래 코드 작업 시

항상 보수적으로 행동한다.

허용:

작은 수정
YAML 기반 조정
로깅 강화
검증 코드 추가

주의:

진입 조건 변경
포지션 사이즈 변경
리스크 관리 수정
시간 필터 수정

고위험:

execute_buy 흐름 변경
risk_manager 수정
stop logic 수정
regime detection 수정
18. 수정-확인 루프 (Verify-Always Loop)

모든 코드 변경은 다음 루프를 따른다:

수정 전   → 관련 파일 읽기 (Read before write)
수정      → 최소 범위 변경
컴파일    → python3 -m py_compile <파일>
동작 확인 → 로그 또는 API 응답으로 검증
완료      → 변경 내용 사용자에게 요약 보고

롤백 전략

파일 수정 전 백업이 필요하다고 판단되면:
cp <파일> <파일>.bak_$(date +%Y%m%d_%H%M%S)

단, 불필요한 백업 파일을 남기지 않는다.
백업이 필요 없다면 git이 역할을 대신한다.

20. 기능 추가 전 필수 체크리스트 (2026-05-14 확정)

새 기능 구현 전에 아래 6개 항목을 모두 확인한다.
"동작하나?" 보다 이 질문들이 먼저다.

1. Observability — 실패했을 때 로그만 보고 원인을 알 수 있는가?
   □ 성공 로그 있음
   □ 실패 로그 있음
   □ 실패 이유가 분리됨 (단순 "미통과" 아님)
   □ 주요 변수값이 출력됨

2. Explainability — 2주 후 내가 이 결과를 해석할 수 있는가?
   □ composite score는 컴포넌트로 분해 가능
   □ threshold 근거가 로그에 보임
   □ reject reason이 enum화됨 (자유 문자열 금지)

3. Analytics compatibility — 나중에 통계 분석 가능한 형태로 저장되는가?
   □ reason normalized (BOS_ONLY, CHOCH_MISSING 등)
   □ timestamp / symbol / regime / alpha 포함
   □ CSV/DB friendly 구조

4. State consistency — 재시작 후에도 상태가 꼬이지 않는가?
   □ restart safe
   □ duplicate execution 방지
   □ cache invalidation 존재

5. Silent failure — 기능이 망가져도 에러 없이 통과할 수 있는가?
   □ 예: log_no_sig() 호출 누락 → 시스템 정상, 데이터만 안 쌓임
   □ expected log count 검증 또는 health check 존재

6. Research readiness — 이 결과를 가설 검증에 쓸 수 있는가?
   □ factor analysis 가능 (alpha, regime, hour 등 저장)
   □ forward outcome 추적 가능
   □ bucket analysis 가능

실패 교훈 (2026-05-14):
오늘 수정한 사항 대부분이 "전략 문제"가 아니라
"observability/analytics completeness 문제"였다.
4~5개월 반복 수정의 근본 원인.

19. 최종 원칙

이 프로젝트의 목표는:

"더 똑똑한 코드"
가 아니라
"실거래에서 안정적으로 살아남는 시스템"
이다.

따라서:

최소 변경
검증 가능성
재현성
보수적 수정
YAML 우선 접근

을 항상 유지한다.

의심스러우면 하지 않는다. (When in doubt, don't.)

21. 운영자 모드 — 주간 리뷰 (2026-06-28~, GD-003)

이 프로젝트는 2026-06-28부터 "운영자 모드"에 들어갔다.

"새로운 기능 제안 회의" 대신 "운영 데이터 리뷰"만 한다.

주간 리뷰 4개 질문:

  1. 이번 주 새로 발견된 증거는 무엇인가?
     → knowledge_base 신규 항목, decision_log 패턴, session_review 내용

  2. 기존 가설을 반박하는 데이터가 있는가?
     → 예상과 다른 결과, regime별 성과 이탈, 반복되는 SKIP 패턴

  3. Calibration은 개선되고 있는가?
     → scientist_predictions 평가 결과, Unknown Rate 추이

  4. Architecture를 변경해야 할 정도의 증거가 있는가?
     → "No Architecture Changes Without Evidence" (CONSTITUTION.md) 기준 적용

Phase A (2026 Q3) 금지 사항:
  - 새로운 AI Agent / LLM / Dashboard / Strategy / Prompt
  - 운영 스키마(public schema) 신규 DB 테이블

  ※ GD-007 (2026-06-30) 예외: research 스키마 신규 테이블은 허용
     (decision_ledger, candidates, future_returns 등 Canonical Data Model)
     조건: DATA_CONTRACT.md 계약 사전 확정 + PM 명시적 승인

  아이디어가 생기면 research_notebook에 제목과 근거만 기록한다.
  데이터가 그 아이디어를 지지하면 그때 가설로 격상한다.

  Idea → Research Notebook → Evidence → Hypothesis → Experiment → Approval → Implementation

운영 상태 확인:
  python3 -m analysis.os_status
  python3 -m analysis.acceptance_test