# 키움증권 자동 매매 시스템

키움증권 REST API를 사용한 AI 기반 자동 매매 시스템입니다.

**핵심 성과 (trading_system 실전 검증 데이터):**
- 로직 준수시: +5.58% 평균 수익, 50% 승률
- 매수 로직만 준수: +4.17% 평균 수익, **90% 승률**
- 로직 비준수: -1.53% 평균 손실
- **차이: +7.11%p 개선 효과**

## 주요 기능

### 1. 4-엔진 통합 분석 시스템
- **뉴스 분석** (30%): Naver API + Gemini AI 감성 분석
- **기술적 분석** (40%): RSI, MACD, 이동평균, 볼린저밴드
- **수급 분석** (20%): 외국인/기관/프로그램 매매 동향
- **기본 분석** (10%): PER, PBR, ROE, 시가총액

### 2. 자동 매매 시스템
- **실시간 모니터링**: 관심 종목 스캔 및 매수 신호 감지
- **리스크 관리**: 거래당 2% 리스크, 일일 손실 한도
- **6단계 매도 전략**: 분할 익절 + ATR Trailing Stop
- **긴급 중지**: 일일 손실 한도 초과시 자동 청산

### 3. 조건식 → VWAP 필터링 파이프라인 ⭐ NEW
- **1차 필터링**: 키움 조건검색식으로 50~100개 종목 검색
- **2차 검증**: VWAP 전략 사전 검증 (최근 5~7일 백테스트)
- **검증 기준**: 최소 2회 거래, 50% 승률, +0.5% 수익률, PF 1.2 이상
- **실행 결과**: 21개 → 4개 종목 선정 (승률 86%, 평균 수익 +2.53%)

### 4. 포지션 및 리스크 관리
- **포지션 추적**: 실시간 수익률, 분할 매도 단계 관리
- **동적 한도**: 실시간 잔고 기반 리스크 계산
- **하드 리밋**: 절대 초과 불가 안전장치

## 프로젝트 구조

```
kiwoom_trading/
├── .env                     # 환경변수 (API 키)
├── requirements.txt         # Python 패키지
├── kiwoom_api.py           # Kiwoom REST API 클라이언트
├── main_condition_filter.py # 조건식 → VWAP 필터링 파이프라인 ⭐
│
├── analyzers/              # 분석 엔진
│   ├── news_analyzer.py        # 뉴스 분석
│   ├── sentiment_analyzer.py   # AI 감성 분석
│   ├── technical_analyzer.py   # 기술적 분석
│   ├── supply_demand_analyzer.py # 수급 분석
│   ├── fundamental_analyzer.py # 기본 분석
│   ├── analysis_engine.py      # 통합 분석
│   ├── pre_trade_validator.py  # 사전 매수 검증 ⭐
│   └── entry_timing_analyzer.py # VWAP 진입 타이밍
│
├── strategies/             # 매매 전략
│   └── trading_strategy.py     # 진입/목표가/손절가 계산
│
├── config/                 # 전략 설정 ⭐
│   ├── strategy_hybrid.yaml      # 균형 전략 (권장)
│   ├── strategy_aggressive.yaml  # 공격적 전략
│   ├── strategy_conservative.yaml # 보수적 전략
│   └── strategy_partial_exit.yaml # 부분 청산 전략
│
├── core/                   # 자동 매매 핵심
│   ├── auto_trading_handler.py # 메인 트레이딩 루프
│   ├── position_manager.py     # 포지션 관리
│   ├── risk_manager.py         # 리스크 관리
│   ├── order_executor.py       # 주문 실행 (6단계 매도)
│   └── market_monitor.py       # 시장 모니터링
│
├── test/                   # 테스트
│   ├── test_analysis_engine.py
│   ├── test_trading_strategy.py
│   ├── test_pre_trade_validation.py ⭐
│   └── test_auto_trading.py
│
├── docs/                   # 문서
│   ├── ANALYSIS_ENGINE_DESIGN.md
│   ├── TRADING_BUSINESS_LOGIC_ANALYSIS.md
│   ├── AUTO_TRADING_SYSTEM.md
│   ├── condition_filter_pipeline_guide.md ⭐
│   └── pre_trade_validation_guide.md ⭐
│
└── data/                   # 데이터 저장
    ├── positions.json          # 포지션 정보
    ├── risk_log.json           # 거래 로그
    └── watchlist.json          # 관심 종목
```

## 설치

### 1. 가상환경 생성 및 활성화

```bash
cd kiwoom_trading

# 가상환경 생성 (최초 1회만)
python3 -m venv venv

# 가상환경 활성화
source venv/bin/activate  # Linux/Mac
# 또는
venv\Scripts\activate  # Windows
```

### 2. 패키지 설치

```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정

`.env` 파일에 다음 정보를 설정하세요:

```env
# 키움증권 API
KIWOOM_USER_ID="your_user_id"
KIWOOM_APP_KEY="your_app_key"
KIWOOM_APP_SECRET="your_app_secret"
KIWOOM_ACCOUNT_NUMBER="0000-00"  # 계좌번호 (앞자리-뒷자리)
```

## 사용법

### 1. 통합 분석 실행

```bash
source venv/bin/activate
python test/test_analysis_engine.py
```

**결과 예시:**
```
종목별 통합 분석 점수 비교
────────────────────────────────────────────────────────────────────
종목명        코드      최종점수  추천      뉴스    기술    수급    기본    시장
────────────────────────────────────────────────────────────────────
SK하이닉스    000660    67.50    매수      97.50   52.25   55.00   63.50   neutral
삼성전자      005930    64.00    매수      81.00   56.75   55.00   60.00   neutral
```

### 2. 매매 전략 테스트

```bash
source venv/bin/activate
python test/test_trading_strategy.py
```

**결과 예시:**
```
매매 계획서
────────────────────────────────────────────────────────────────────
현재가: 98,400원
진입 신호: BUY (70% 비율)
매수 수량: 21주 (2,066,400원)
목표가:
  1차: 100,221원 (+1.85%) - ATR 1.5배
  2차: 101,436원 (+3.09%) - ATR 2.5배
  3차: 103,257원 (+4.94%) - ATR 4.0배
손절가: 93,480원 (-5.00%)
리스크/리워드: 1:0.37
```

### 3. 조건식 → VWAP 필터링 파이프라인 ⭐ NEW

```bash
source venv/bin/activate
python main_condition_filter.py
```

**결과 예시:**
```
[1단계] 조건식 검색
  - Momentum 전략: 7개
  - Breakout 전략: 16개
  - EOD 전략: 1개
  → 총 21개 종목 (중복 제거)

[2단계] VWAP 사전 검증
  - 검증 기준: 2회 거래, 50% 승률, +0.5% 수익, PF 1.2
  → 4개 종목 통과 (81% 거부)

[최종 선정]
  1위: 005690 - 승률 88.9%, 수익 +3.49% (9회)
  2위: 103590 - 승률 80.0%, 수익 +3.32% (5회)
  3위: 011500 - 승률 75.0%, 수익 +1.73% (8회)
  4위: 062040 - 승률 100.0%, 수익 +1.59% (2회)
```

**상세 가이드:** [docs/condition_filter_pipeline_guide.md](docs/condition_filter_pipeline_guide.md)

### 4. 자동 매매 시스템 테스트

```bash
source venv/bin/activate
python test/test_auto_trading.py --mode full
```

**결과 예시:**
```
✅ 매수 후보 2개 발견
  1. SK하이닉스 - BUY (점수: 67.20)
  2. 삼성전자 - BUY (점수: 64.00)

📊 계좌 현황
  현금: 10,000,000원
  포지션: 0원 (0개)
  일일 손익: +0원
```

### 5. 자동 매매 실행 (실전)

```python
from core.auto_trading_handler import AutoTradingHandler

# 자동 매매 핸들러 생성
handler = AutoTradingHandler(
    account_no="12345678-01",
    initial_balance=10000000,
    risk_per_trade=0.02,
    max_position_size=0.30
)

# 자동 매매 시작 (Ctrl+C로 중지)
handler.start()
```

## 핵심 모듈

### AutoTradingHandler (core/auto_trading_handler.py)
메인 트레이딩 루프 - 실시간 모니터링 및 자동 실행

### PositionManager (core/position_manager.py)
보유 종목 추적, 분할 매도 단계 관리, Trailing Stop 자동 업데이트

### RiskManager (core/risk_manager.py)
리스크 관리 - 일일 한도, 긴급 중지, 포트폴리오 리스크 지표

### OrderExecutor (core/order_executor.py)
주문 실행 및 6단계 고도화 매도 전략 구현

### MarketMonitor (core/market_monitor.py)
관심 종목 스캔, 매수 신호 감지, 실시간 현재가 조회

## 6단계 고도화 매도 전략

trading_system 프로젝트의 실전 검증된 매도 전략을 구현했습니다.

### 단계별 매도 로직

| 단계 | 조건 | 행동 | 비율 |
|------|------|------|------|
| **1단계** | 손실 -3% | 전량 매도 (Hard Stop) | 100% |
| **2단계** | 수익 +4% | 1차 익절 | 40% |
| **3단계** | 수익 +6% | 2차 익절 + Trailing 활성화 | 40% |
| **4단계** | Trailing Stop 도달 | ATR 2배 Trailing | 20% |
| **5단계** | EMA+Volume 전환 | 추세 전환 감지 매도 | 잔여 |
| **6단계** | 15:00 이후 | 강제 청산 (갭 리스크 회피) | 전량 |

### 전략 효과

- **원금 보호**: 1차 익절(+4%)에서 40% 매도로 원금 일부 회수
- **수익 극대화**: 2차 익절(+6%) 후 Trailing으로 추세 추종
- **손실 제한**: Hard Stop(-3%)으로 손실 확대 방지
- **갭 리스크 회피**: 장 마감 전 청산으로 익일 갭 리스크 제거

## API 참고 문서

- [키움증권 OpenAPI 공식 포털](https://openapi.kiwoom.com/)
- [키움 REST API 개발 가이드](https://download.kiwoom.com/web/openapi/kiwoom_openapi_plus_devguide_ver_1.1.pdf)
- **상세 문서**: [docs/AUTO_TRADING_SYSTEM.md](docs/AUTO_TRADING_SYSTEM.md)

## 주요 기능 구현

- **인증**: HMAC-SHA256 서명 기반 OAuth 2.0 인증
- **토큰 관리**: 24시간 유효한 접근 토큰 자동 관리
- **API 호출**: 주식 현재가 조회, 계좌 정보 조회, 잔고 조회

## 주의사항

1. **API 키 보안**: `.env` 파일은 절대 Git에 커밋하지 마세요.
2. **IP 등록**: 키움증권 OpenAPI 포털에서 사용할 IP를 등록해야 합니다.
3. **계좌 등록**: 실전투자/모의투자 계좌를 포털에서 등록해야 합니다.
4. **요청 제한**: API 호출 횟수 제한이 있으니 주의하세요.
5. **BASE_URL**: 현재 `https://api.kiwoom.com` 사용 (실제 서비스 URL은 포털 확인 필요)

## 문제 해결

### 토큰 발급 실패

- API 키와 시크릿이 올바른지 확인하세요.
- 키움증권 OpenAPI 포털에서 앱 키를 발급받았는지 확인하세요.
- IP가 포털에 등록되어 있는지 확인하세요.
- 실제 API 엔드포인트는 키움증권 공식 문서를 참고하세요.

### 계좌 조회 실패

- 계좌번호가 포털에 등록되어 있는지 확인하세요.
- 해당 계좌가 API 사용 권한이 있는지 확인하세요.

### API 응답 오류

- 키움증권 REST API는 공식 문서에서 제공하는 정확한 엔드포인트와 요청 형식을 사용해야 합니다.
- 현재 코드는 일반적인 REST API 구조를 따르며, 실제 사용 시 공식 문서를 참고하여 수정이 필요할 수 있습니다.

## 개발 로드맵

### Phase 1-2: 기본 시스템 ✅ 완료
- ✅ 4-엔진 통합 분석 시스템
- ✅ 매매 전략 엔진
- ✅ 자동 매매 핸들러
- ✅ 포지션/리스크 관리

### Phase 3: 실전 운영 개선 ✅ 완료
- ✅ **EMA + Volume Breakdown 매도 신호** (5단계)
- ✅ **실제 Kiwoom 주문 API** (매수/매도/정정/취소)
- ✅ 6단계 매도 전략 완성

상세 문서: [docs/PHASE3_IMPROVEMENTS.md](docs/PHASE3_IMPROVEMENTS.md)

### Phase 4: 백테스팅 (예정)
- [ ] 과거 데이터 기반 전략 검증
- [ ] 파라미터 최적화

## 라이선스

이 프로젝트는 개인 학습 및 연구 목적으로 작성되었습니다.
