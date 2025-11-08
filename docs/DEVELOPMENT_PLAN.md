# 🚀 Kiwoom Trading System - Development Plan

## 🎯 프로젝트 개요
**키움증권 REST API 기반 자동매매 시스템**
- trading_system(KIS API)의 로직을 키움증권 API로 전환
- 동일한 아키텍처와 기능 구현
- 모든 테스트는 test/ 폴더에서 관리

## 📋 전체 개발 로드맵

### Phase 1: 기초 인프라 구축 ✅ (진행중)
**목표:** 키움증권 API 연동 및 기본 데이터 수집

#### 1.1 API 인증 및 기본 연동 ✅
- [완료] 키움증권 REST API 접속 토큰 발급
- [완료] KiwoomAPI 클래스 기본 구조
- [완료] 환경 변수(.env) 설정
- [완료] test 폴더 구조 생성

#### 1.2 데이터 수집 계층 구축 (다음 단계)
```
data_collectors/
├── kiwoom_collector.py      # 키움증권 API 통합 수집기
├── chart_data_collector.py  # 차트 데이터 (분봉/일봉)
├── news_collector.py         # 뉴스 수집
└── base_collector.py         # 수집기 베이스 클래스
```

**주요 기능:**
- [ ] 실시간 시세 조회 (현재가, 호가, 체결)
- [ ] 분봉/일봉 데이터 수집
- [ ] 종목 검색 및 조건 검색
- [ ] 계좌 정보 조회
- [ ] 보유 종목 조회
- [ ] 주문 가능 금액 조회

#### 1.3 데이터베이스 설계 및 구현
```
database/
├── models.py              # SQLAlchemy 모델
├── db_operations.py       # CRUD 연산
└── database_manager.py    # DB 연결 관리
```

**주요 테이블:**
- `stocks` - 종목 기본 정보
- `filtered_stocks` - 1차 필터링 결과
- `analysis_results` - 2차 분석 결과
- `monitoring_stocks` - 실시간 모니터링 종목
- `trades` - 매매 내역
- `orders` - 주문 내역
- `portfolio` - 포트폴리오 현황
- `account_info` - 계좌 정보

#### 1.4 설정 및 유틸리티
```
config/
├── config.py              # 시스템 전체 설정
└── trading_config.py      # 매매 관련 설정

utils/
├── logger.py              # 로깅 시스템
├── market_schedule.py     # 장 시간 관리
└── error_handler.py       # 에러 처리
```

---

### Phase 2: 분석 엔진 구축
**목표:** 종목 분석 및 스코어링 시스템

#### 2.1 기술적 분석기
```
analyzers/
├── technical_analyzer.py     # 기술적 분석
├── chart_pattern_analyzer.py # 차트 패턴 인식
└── technical_indicators.py   # 기술적 지표 계산
```

**분석 항목:**
- 추세 분석 (이동평균선, 추세선)
- 모멘텀 지표 (RSI, MACD, Stochastic)
- 변동성 지표 (볼린저밴드, ATR)
- 거래량 분석
- 지지/저항 레벨

#### 2.2 뉴스 및 감성 분석
```
analyzers/
├── sentiment_analyzer.py     # 감성 분석
├── gemini_analyzer.py        # Gemini AI 분석
└── news_analyzer.py          # 뉴스 영향도 분석
```

#### 2.3 수급 분석
```
analyzers/
├── supply_demand_analyzer.py # 수급 분석
└── volume_analyzer.py         # 거래량 분석
```

#### 2.4 종합 분석 엔진
```
analyzers/
└── analysis_engine.py         # 종합 분석 및 점수화
```

**점수 산출:**
- 기술적 분석 점수 (40%)
- 뉴스/감성 분석 점수 (30%)
- 수급 분석 점수 (30%)
- 최종 투자 매력도 점수 (100점 만점)

---

### Phase 3: 매매 전략 구현
**목표:** 다양한 매매 전략 모듈화

#### 3.1 전략 베이스 구조
```
strategies/
├── base_strategy.py           # 전략 베이스 클래스
└── strategy_manager.py        # 전략 관리자
```

#### 3.2 핵심 전략 구현
```
strategies/
├── momentum_strategy.py       # 모멘텀 전략
├── breakout_strategy.py       # 돌파 전략
├── eod_strategy.py            # 장마감 전략
├── supertrend_ema_rsi_strategy.py  # 복합 지표 전략
├── vwap_strategy.py           # VWAP 전략
├── scalping_3m_strategy.py    # 3분봉 스캘핑
└── rsi_strategy.py            # RSI 전략
```

**각 전략 구현 요소:**
- 진입 조건 (Entry Conditions)
- 청산 조건 (Exit Conditions)
- 손절매/익절매 설정
- 포지션 사이징
- 리스크 관리

---

### Phase 4: 매매 실행 시스템
**목표:** 실제 주문 실행 및 포지션 관리

#### 4.1 주문 실행 모듈
```
trading/
├── executor.py            # 매매 실행 엔진
├── order_manager.py       # 주문 관리
└── position_manager.py    # 포지션 관리
```

**주요 기능:**
- [ ] 시장가/지정가 주문
- [ ] 주문 취소/정정
- [ ] 주문 체결 확인
- [ ] 주문 이력 저장

#### 4.2 리스크 관리
```
trading/
└── risk_manager.py        # 리스크 관리자
```

**리스크 관리 요소:**
- 손절매 자동 실행
- 익절매 자동 실행
- 최대 손실 제한
- 포지션 사이징
- 일일 손실 한도

---

### Phase 5: 실시간 자동매매 시스템
**목표:** 실시간 모니터링 및 자동 매매

#### 5.1 스케줄러 구현
```
core/
└── scheduler.py           # 실시간 스케줄러
```

**스케줄 작업:**
- 08:30 - 장전 종목 분석 및 선정
- 09:00-15:30 - 실시간 모니터링 (3분 간격)
- 실시간 신호 생성 및 자동 주문
- 16:00 - 일일 결산 및 리포트

#### 5.2 자동매매 핸들러
```
core/
├── auto_trading_handler.py    # 자동매매 핸들러
└── db_auto_trading_handler.py # DB 통합 자동매매
```

#### 5.3 모니터링 시스템
```
monitoring/
├── monitoring_scheduler.py    # 모니터링 스케줄러
└── performance_monitor.py     # 성능 모니터
```

---

### Phase 6: 알림 시스템
**목표:** 실시간 알림 및 리포팅

#### 6.1 알림 모듈
```
notifications/
├── telegram_notifier.py       # 텔레그램 알림
└── notification_manager.py    # 알림 관리자
```

**알림 유형:**
- 매매 신호 발생
- 주문 체결 알림
- 손익 알림
- 시스템 에러 알림
- 일일 리포트

---

### Phase 7: 백테스팅 시스템
**목표:** 전략 검증 및 최적화

#### 7.1 백테스팅 엔진
```
backtesting/
├── backtesting_engine.py      # 백테스팅 엔진
├── strategy_validator.py      # 전략 검증기
└── performance_visualizer.py  # 성과 시각화
```

**백테스팅 기능:**
- 과거 데이터 기반 시뮬레이션
- 전략별 성과 비교
- 승률, 손익률, MDD 계산
- 결과 리포트 생성

---

### Phase 8: AI 고도화
**목표:** AI 기반 예측 및 최적화

#### 8.1 AI 분석기
```
analyzers/
├── ai_predictor.py            # AI 예측 시스템
├── ai_risk_manager.py         # AI 리스크 관리
├── market_regime_detector.py  # 시장 상황 감지
└── ai_controller.py           # AI 컨트롤러
```

#### 8.2 전략 최적화
```
analyzers/
└── strategy_optimizer.py      # 전략 최적화기
```

---

## 🔧 기술 스택

### Backend
- **Python 3.9+** - 메인 언어
- **PostgreSQL** - 데이터베이스
- **SQLAlchemy** - ORM
- **asyncio/aiohttp** - 비동기 처리

### 외부 API
- **키움증권 REST API** - 주식 데이터 및 매매
- **Google Gemini API** - AI 뉴스 분석
- **Telegram Bot API** - 알림

### 라이브러리
- **pandas/numpy** - 데이터 분석
- **TA-Lib** - 기술적 분석
- **requests** - HTTP 통신
- **APScheduler** - 작업 스케줄링
- **python-dotenv** - 환경 변수 관리

---

## 📝 개발 원칙

### 1. 코드 구조
- **모듈화**: 각 기능을 독립적인 모듈로 분리
- **재사용성**: 공통 기능은 베이스 클래스로 추상화
- **확장성**: 새로운 전략/분석기 추가가 용이하도록 설계

### 2. 테스트
- **모든 테스트는 test/ 폴더에서 관리**
- 각 Phase별로 테스트 파일 작성
- 단위 테스트 우선 작성

### 3. 문서화
- 각 모듈에 docstring 작성
- README.md 지속적 업데이트
- API 문서화

### 4. 에러 처리
- 모든 API 호출에 에러 처리
- 로그 기록
- 사용자 친화적 에러 메시지

### 5. 보안
- API 키는 .env 파일로 관리
- .gitignore에 민감 정보 추가
- 로그에 민감 정보 노출 방지

---

## 🎯 현재 상태 (2025-10-23)

### ✅ 완료
- [x] 프로젝트 초기 설정
- [x] 키움증권 API 토큰 발급 구현
- [x] 기본 KiwoomAPI 클래스 구조
- [x] test 폴더 구조 생성
- [x] 환경 변수 설정

### 🔄 진행중
- [ ] Phase 1.2: 데이터 수집 계층 구축

### 📋 다음 작업
1. 키움증권 API 공식 문서 상세 분석
2. 주요 API 엔드포인트 확인 및 테스트
3. 데이터 수집기 구현 시작

---

## 📚 참고 자료
- [키움증권 OpenAPI 공식 포털](https://openapi.kiwoom.com/)
- [키움 REST API 개발 가이드](https://download.kiwoom.com/web/openapi/kiwoom_openapi_plus_devguide_ver_1.1.pdf)
- trading_system 프로젝트 아키텍처

---

## 📞 이슈 및 문의
- GitHub Issues 활용
- 개발 진행 상황은 이 문서에 지속적으로 업데이트
