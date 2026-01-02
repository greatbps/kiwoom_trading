# 코드 인스펙션 계획

생성일: 2026-01-02
목적: 코드 품질 개선, 사용하지 않는 코드 제거, 리팩토링 대상 식별

---

## 📋 인스펙션 도구

- **Ruff**: Python linting (PEP8, 사용하지 않는 임포트 등)
- **Vulture**: Dead code detection (미사용 함수, 변수, 임포트)
- **Manual Review**: 중복 코드, 복잡도, 아키텍처 개선

---

## 🎯 우선순위별 파일 분류

### Priority 1: 핵심 실행 파일 (즉시)

**메인 시스템**
- [ ] `main_auto_trading.py` ⚠️ **4,721 lines** - 핵심 자동매매 시스템
- [ ] `kiwoom_api.py` - 키움 API 클라이언트
- [ ] `main_menu.py` - 메인 진입점

**핵심 컴포넌트**
- [ ] `core/risk_manager.py` ✅ 최근 수정
- [ ] `core/order_executor.py` - 주문 실행
- [ ] `core/position_manager.py` - 포지션 관리
- [ ] `core/trade_reconciliation.py` ✅ 최근 수정
- [ ] `analyzers/signal_orchestrator.py` ✅ 최근 수정
- [ ] `analyzers/pre_trade_validator.py` - 진입 전 검증

**데이터베이스**
- [ ] `database/trading_db.py` - DB 인터페이스
- [ ] `market_utils.py` - 시장 유틸리티

---

### Priority 2: 전략 및 분석 (1주일 내)

**전략 엔진**
- [ ] `strategies/trading_strategy.py`
- [ ] `strategy/condition_engine.py`
- [ ] `strategy/vwap_filter.py`
- [ ] `trading/alpha_engine.py`

**분석기**
- [ ] `analyzers/technical_analyzer.py`
- [ ] `analyzers/sentiment_analyzer.py`
- [ ] `analyzers/news_analyzer.py`
- [ ] `analyzers/liquidity_shift_detector.py`
- [ ] `analyzers/squeeze_momentum.py`
- [ ] `utils/squeeze_momentum_realtime.py`

**트레이딩 로직**
- [ ] `trading/trade_state_manager.py`
- [ ] `trading/exit_logic_optimized.py`
- [ ] `trading/signal_detector.py`

---

### Priority 3: 유틸리티 및 헬퍼 (2주일 내)

**유틸리티**
- [ ] `utils/error_handler.py`
- [ ] `utils/logger.py`
- [ ] `utils/cache.py`
- [ ] `utils/display.py`
- [ ] `utils/performance_optimizer.py`

**설정 및 관리**
- [ ] `config/config_manager.py`
- [ ] `config/env_config.py`
- [ ] `core/auth_manager.py`

---

### Priority 4: 테스트 코드 정리 (검토 필요)

**사용 중인 테스트**
- [ ] `test_time_filter.py` ✅ 최근 생성
- [ ] `test_stockgravity_preservation.py` ✅ 최근 생성
- [ ] `test/test_auto_trading.py`
- [ ] `tests/test_final_integration.py`

**미사용 가능성 높은 테스트 (삭제 검토)**
- [ ] `test/` 디렉토리 내 100+ 테스트 파일들
- [ ] `tests/` 디렉토리 내 phase1-4 테스트들
- [ ] 중복된 분석 스크립트들

---

### Priority 5: 삭제/아카이브 후보 (확인 후 제거)

**Deprecated/Archive**
- [ ] `archive/deprecated_ml/` - ML 관련 구버전
- [ ] `backup/deprecated/` - 백업 파일들
- [ ] `analyzers/risk_manager.py` - core/risk_manager.py와 중복?

**중복 가능성**
- [ ] `database/trading_db_v2.py` vs `trading_db.py`
- [ ] `analyzers/pre_trade_validator_v2.py` vs `pre_trade_validator.py`
- [ ] `analyzers/liquidity_shift_detector_v2.py` vs `liquidity_shift_detector.py`
- [ ] 여러 분석 스크립트들 (`analyze_*.py`)

---

## 📊 인스펙션 체크리스트

각 파일당 다음 항목 체크:

### 1. Ruff Linting
- [ ] Import 순서 및 정리
- [ ] 사용하지 않는 임포트 제거
- [ ] f-string 불필요한 사용 제거
- [ ] Bare except 수정
- [ ] 변수명 undefined 체크

### 2. Vulture Dead Code
- [ ] 사용하지 않는 함수
- [ ] 사용하지 않는 클래스
- [ ] 사용하지 않는 변수
- [ ] 사용하지 않는 import

### 3. Manual Review
- [ ] 중복 코드 식별
- [ ] 복잡도 (함수 > 50줄, 클래스 > 300줄)
- [ ] 주석/문서화 상태
- [ ] 에러 핸들링 적절성
- [ ] 타입 힌팅 추가 필요

---

## 🚀 실행 계획

### Week 1: Priority 1 (핵심 파일)
1. `main_auto_trading.py` 분석 ✅ (진행 중)
2. `kiwoom_api.py` 분석
3. `core/` 핵심 파일들 분석
4. `analyzers/signal_orchestrator.py` 분석

### Week 2: Priority 2 (전략/분석)
5. 전략 엔진 파일들
6. 주요 분석기들
7. 트레이딩 로직

### Week 3: Priority 3-5 (유틸/정리)
8. 유틸리티 검토
9. 테스트 코드 정리
10. 미사용 파일 아카이브/삭제

---

## 📈 현재 발견된 이슈 (main_auto_trading.py)

### Ruff Issues (50개)
- E402: Module level import not at top (많음)
- F401: Unused imports (SignalTier, Panel, Live, InvalidationReason)
- F811: Redefinition of `time`
- F541: f-string without placeholders (10+개)
- F821: Undefined name `code` (line 615)
- E722: Bare except (3개)
- F841: Unused local variables (3개)

### Vulture Issues (8개)
- 사용하지 않는 임포트 4개
- Redundant if-condition (line 5251)
- Unused variables: frame, sig (line 5461)

### 즉시 조치 필요
1. ❌ Line 615: `F821 Undefined name 'code'` - 버그 가능성
2. ⚠️ 사용하지 않는 import 제거
3. ⚠️ Bare except를 구체적 예외로 변경

---

## 📝 다음 단계

1. **main_auto_trading.py 즉시 수정** (발견된 버그 및 경고)
2. **kiwoom_api.py 분석** (다음 우선순위)
3. **core/ 모듈 순차 분석**
4. **정기 리포트 생성** (주간 단위)

---

## 🔍 통계

- **전체 Python 파일**: 277개
- **핵심 파일 (P1)**: 11개
- **전략/분석 (P2)**: 15개
- **유틸리티 (P3)**: 10개
- **테스트 (P4)**: 100+ 개
- **삭제 후보 (P5)**: 검토 필요

**예상 작업 기간**: 3주
**예상 정리 효과**: 30-40% 코드베이스 축소 가능
