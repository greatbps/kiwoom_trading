# 키움 AI 트레이딩 시스템 v2.0 - 완전 문서
**작성일: 2025년 11월 3일**

---

## 목차
1. [시스템 개요](#1-시스템-개요)
2. [아키텍처 설계](#2-아키텍처-설계)
3. [3단계 필터링 파이프라인](#3-3단계-필터링-파이프라인)
4. [AI 분석 엔진](#4-ai-분석-엔진)
5. [자동매매 시스템](#5-자동매매-시스템)
6. [백테스트 및 최적화](#6-백테스트-및-최적화)
7. [데이터베이스 스키마](#7-데이터베이스-스키마)
8. [설정 및 환경변수](#8-설정-및-환경변수)
9. [API 명세](#9-api-명세)
10. [배포 및 운영](#10-배포-및-운영)

---

## 1. 시스템 개요

### 1.1 프로젝트 목적
키움증권 OpenAPI를 활용한 완전 자동화 AI 트레이딩 시스템으로, 데이터 수집부터 매매 실행, 성과 분석, 시스템 최적화까지 전 과정을 자동화합니다.

### 1.2 핵심 기능
- ✅ **3단계 필터링 파이프라인**: 조건검색 → VWAP 백테스트 → AI 종합 분석
- ✅ **4가지 AI 분석 통합**: 뉴스 + 기술적 + 수급 + 기본 분석
- ✅ **실시간 자동매매**: WebSocket 기반 실시간 호가/체결 처리
- ✅ **ML 기반 종목 랭킹**: 과거 성과 학습하여 예측 모델 구축
- ✅ **백테스트 최적화 피드백 루프**: 성과 분석 → 가중치 자동 조정

### 1.3 기술 스택
```yaml
언어: Python 3.10+
비동기: asyncio, aiohttp
데이터: pandas, numpy, yfinance
머신러닝: scikit-learn, xgboost
AI: OpenAI GPT-4o-mini, DeepSeek (뉴스 분석)
UI: Rich (터미널 UI)
데이터베이스: SQLite
API: 키움증권 OpenAPI (WebSocket)
```

### 1.4 프로젝트 구조
```
kiwoom_trading/
├── analyzers/              # AI 분석 엔진
│   ├── analysis_engine.py        # 통합 분석 (가중치 적용)
│   ├── news_analyzer.py          # 뉴스 수집
│   ├── sentiment_analyzer.py     # 감성 분석 (DeepSeek LLM)
│   ├── technical_analyzer.py     # 기술적 분석 (RSI, MACD, 볼린저밴드 등)
│   ├── supply_demand_analyzer.py # 수급 분석 (외국인/기관 매매)
│   ├── fundamental_analyzer.py   # 기본 분석 (PER, PBR, ROE 등)
│   └── backtest_optimizer.py     # 백테스트 최적화 엔진
│
├── core/                   # 핵심 시스템
│   ├── kiwoom_api.py            # 키움 OpenAPI 래퍼
│   ├── vwap_validator.py        # VWAP 돌파 전략 백테스트
│   ├── ml_data_collector.py     # ML 학습 데이터 수집
│   ├── data_cleaner.py          # 데이터 전처리
│   ├── label_generator.py       # 학습 레이블 생성
│   ├── training_dataset_builder.py  # 학습 데이터셋 빌더
│   ├── ranker_trainer.py        # Ranker 모델 학습
│   └── ranker_predictor.py      # Ranker 모델 추론
│
├── database/               # 데이터베이스
│   └── trading_db.py            # SQLite 데이터베이스 관리
│
├── utils/                  # 유틸리티
│   ├── config_manager.py        # 설정 파일 관리 (가중치, 파라미터)
│   ├── telegram_notifier.py     # 텔레그램 알림
│   └── logger.py                # 로깅
│
├── strategies/             # 매매 전략
│   ├── vwap_breakout.py         # VWAP 돌파 전략
│   └── momentum.py              # 모멘텀 전략
│
├── backtest_with_ranker.py      # 백테스트 실행기
├── main_menu.py                 # 메인 메뉴 (진입점)
├── main_auto_trading.py         # 자동매매 메인
├── main_condition_filter.py     # 조건검색 필터링 메인
├── reporting.py                 # 리포트 생성
├── run.sh                       # 실행 스크립트
│
├── config/                 # 설정 파일
│   ├── analysis_weights.json    # 분석 가중치 (자동 생성/백업)
│   └── backups/                 # 가중치 백업
│
├── data/                   # 데이터 저장소
│   ├── ml_training_data/        # ML 학습 데이터
│   ├── models/                  # 학습된 모델
│   └── trading.db               # 거래 데이터베이스
│
├── reports/                # 리포트
│   └── optimization_*.txt       # 최적화 리포트
│
└── docs/                   # 문서
    ├── SYSTEM_DOCUMENTATION_20251103.md  # 본 문서
    └── optimization_feedback_loop.md     # 최적화 피드백 루프 상세
```

---

## 2. 아키텍처 설계

### 2.1 전체 시스템 다이어그램

```
┌─────────────────────────────────────────────────────────────────────┐
│                         메인 메뉴 (main_menu.py)                     │
│  [1] 자동매매  [2] 조건검색  [3] ML학습  [4] Ranker  [5] 백테스트   │
└────────┬──────────────────┬──────────────┬───────────┬──────────────┘
         │                  │              │           │
         ▼                  ▼              ▼           ▼
    ┌─────────┐      ┌─────────────┐  ┌──────┐  ┌──────────┐
    │자동매매 │      │ 조건검색    │  │ ML   │  │백테스트  │
    │시스템   │      │ 필터링      │  │학습  │  │최적화    │
    └────┬────┘      └──────┬──────┘  └──┬───┘  └────┬─────┘
         │                  │            │           │
         │    ┌─────────────┴────────────┴───────────┤
         │    │                                      │
         ▼    ▼                                      ▼
    ┌──────────────────────────────────────────────────────┐
    │              3단계 필터링 파이프라인                  │
    │  1차: 조건검색 → 2차: VWAP → 3차: AI 종합 분석       │
    └──────────────┬───────────────────────────────────────┘
                   │
                   ▼
    ┌──────────────────────────────────────────────────────┐
    │                 AI 분석 엔진                          │
    │  뉴스(30%) + 기술(40%) + 수급(15%) + 기본(15%)       │
    └──────────────┬───────────────────────────────────────┘
                   │
                   ▼
    ┌──────────────────────────────────────────────────────┐
    │              데이터베이스 (SQLite)                    │
    │  후보종목 저장 → Ranker 학습 → 실시간 매매          │
    └──────────────────────────────────────────────────────┘
```

### 2.2 데이터 흐름

```
[시장 데이터]
     │
     ├──→ 키움 OpenAPI (WebSocket) ──→ 실시간 호가/체결
     │
     ├──→ 조건검색 결과 ──→ 1차 필터링
     │         │
     │         ▼
     │    VWAP 백테스트 ──→ 2차 필터링
     │         │
     │         ▼
     │    AI 종합 분석 ──→ 3차 필터링
     │         │
     │         ▼
     │    후보 종목 DB 저장
     │         │
     │         ├──→ Ranker 학습 데이터
     │         │
     │         ├──→ 자동매매 대상
     │         │
     │         └──→ 백테스트 분석
     │                   │
     │                   ▼
     │            최적화 리포트
     │                   │
     │                   ▼
     └───────────← 가중치 자동 조정 (피드백 루프)
```

### 2.3 비동기 처리 아키텍처

```python
# asyncio 기반 동시 처리
async def main_pipeline():
    # 병렬 처리 가능한 작업들
    tasks = [
        fetch_chart_data(code),      # 차트 데이터
        fetch_investor_data(code),   # 투자자 매매 동향
        fetch_news(stock_name),      # 뉴스
        fetch_financial_data(code)   # 재무 데이터
    ]

    # 동시 실행 (순차 대비 4배 빠름)
    results = await asyncio.gather(*tasks)

    # 분석 (각 분석도 병렬 처리 가능)
    analyses = await asyncio.gather(
        analyze_technical(chart_data),
        analyze_supply_demand(investor_data),
        analyze_news(news_data),
        analyze_fundamental(financial_data)
    )
```

---

## 3. 3단계 필터링 파이프라인

### 3.1 개요
대량의 종목 중에서 최종 매매 대상을 선별하는 3단계 필터링 시스템입니다.

### 3.2 1차 필터링: 조건검색

**목적**: 키움증권 HTS의 조건검색 기능을 활용하여 기본적인 기술적 조건을 만족하는 종목 선별

**조건 예시**:
- 거래량 급증 (20일 평균 대비 150% 이상)
- 가격 상승 (5일 연속 양봉)
- 이동평균선 정배열 (5일선 > 20일선 > 60일선)
- 시가총액 최소 500억 이상

**구현 위치**: `main_condition_filter.py` → `KiwoomAPI.get_condition_search_stocks()`

**출력**:
```python
# 조건검색 결과 (보통 50-200개)
['005930', '000660', '035420', ...]  # 종목코드 리스트
```

**코드 예시**:
```python
# 조건검색 실행
condition_name = "거래량급증_상승추세"
stocks = await kiwoom_api.get_condition_search_stocks(
    screen_no="0101",
    condition_name=condition_name,
    search_type=0  # 0: 조건검색, 1: 실시간 조건검색
)

console.print(f"✓ 1차 필터링 완료: {len(stocks)}개 종목 선별")
```

### 3.3 2차 필터링: VWAP 백테스트

**목적**: VWAP(Volume Weighted Average Price) 돌파 전략의 과거 성과를 백테스트하여 검증

**VWAP 돌파 전략**:
1. 종가가 VWAP을 상향 돌파 → 매수 신호
2. VWAP 아래로 하락 → 매도 신호
3. 과거 100일간 성과 계산 (승률, 평균 수익률, 거래 횟수)

**필터 기준**:
- 최소 거래 횟수: 2회 이상
- 최소 승률: 50% 이상
- 최소 평균 수익률: 0% 이상

**구현 위치**: `core/vwap_validator.py` → `VWAPValidator.validate_strategy()`

**코드 예시**:
```python
from core.vwap_validator import VWAPValidator

validator = VWAPValidator()

# 각 종목에 대해 VWAP 백테스트
for stock_code in stocks:
    chart_data = await fetch_chart_data(stock_code, days=100)

    result = validator.validate_strategy(
        stock_code=stock_code,
        stock_name=stock_names[stock_code],
        chart_data=chart_data,
        min_bars=100,
        min_trades=2,
        min_win_rate=0.50
    )

    if result['passed']:
        # 2차 필터 통과
        passed_stocks.append({
            'code': stock_code,
            'vwap_win_rate': result['win_rate'],
            'vwap_trades': result['total_trades'],
            'vwap_avg_profit': result['avg_profit_per_trade']
        })
```

**출력 예시**:
```
VWAP 백테스트 결과:
  ✓ 005930 (삼성전자): 승률 65.2%, 거래 23회, 평균 수익 +2.3%
  ✓ 000660 (SK하이닉스): 승률 58.3%, 거래 12회, 평균 수익 +1.8%
  ✗ 035420 (NAVER): 승률 45.0%, 거래 10회 → 기준 미달
```

### 3.4 3차 필터링: AI 종합 분석

**목적**: 뉴스, 기술적, 수급, 기본 분석을 통합하여 최종 점수 산출

**분석 구성**:
| 분석 요소 | 가중치 | 만점 | 설명 |
|----------|-------|------|------|
| 뉴스 분석 | 30% | 100점 | DeepSeek LLM 기반 감성 분석 |
| 기술적 분석 | 40% | 100점 | RSI, MACD, 볼린저밴드, 이동평균 |
| 수급 분석 | 15% | 50점 | 외국인/기관 매매 동향 (정규화) |
| 기본 분석 | 15% | 50점 | PER, PBR, ROE (업종 상대평가) |

**최종 점수 계산**:
```python
# 각 점수를 100점 만점으로 정규화
news_normalized = news_score  # 이미 100점 만점
technical_normalized = technical_score  # 이미 100점 만점
supply_normalized = supply_score * 2  # 50점 → 100점
fundamental_normalized = fundamental_score * 2  # 50점 → 100점

# 가중치 적용
final_score = (
    news_normalized * 0.30 +
    technical_normalized * 0.40 +
    supply_normalized * 0.15 +
    fundamental_normalized * 0.15
)

# 시장 상황 보정 (bull: 1.2, neutral: 1.0, bear: 0.8)
final_score *= market_regime_coefficient
```

**필터 기준**:
- 최소 총점: 65점 이상
- 뉴스 점수: 50점 이상 (부정 뉴스 필터링)
- 기술적 점수: 40점 이상

**구현 위치**: `analyzers/analysis_engine.py` → `AnalysisEngine.analyze_comprehensive()`

**코드 예시**:
```python
from analyzers.analysis_engine import AnalysisEngine

engine = AnalysisEngine()

for stock in passed_vwap_stocks:
    # 종합 분석 실행
    analysis = await engine.analyze_comprehensive(
        stock_code=stock['code'],
        stock_name=stock['name'],
        chart_data=chart_data,
        investor_data=investor_data,
        financial_data=financial_data
    )

    # 3차 필터 통과 기준
    if (analysis['final_score'] >= 65 and
        analysis['news']['score'] >= 50 and
        analysis['technical']['score'] >= 40):

        # DB에 저장
        db.save_candidate(
            stock_code=stock['code'],
            stock_name=stock['name'],
            total_score=analysis['final_score'],
            news_score=analysis['news']['score'],
            technical_score=analysis['technical']['score'],
            supply_score=analysis['supply_demand']['score'],
            fundamental_score=analysis['fundamental']['score'],
            vwap_win_rate=stock['vwap_win_rate'],
            analysis_detail=json.dumps(analysis)
        )
```

**출력 예시**:
```
AI 종합 분석 결과:
  [1] 005930 (삼성전자) - 총점: 78.5점
      뉴스: 75.0점 | 기술: 82.0점 | 수급: 72.0점 | 기본: 80.0점
      → ✓ 매수 추천

  [2] 000660 (SK하이닉스) - 총점: 72.3점
      뉴스: 68.0점 | 기술: 76.0점 | 수급: 65.0점 | 기본: 72.0점
      → ✓ 매수 추천

  [3] 035420 (NAVER) - 총점: 58.2점
      뉴스: 45.0점 | 기술: 62.0점 | 수급: 58.0점 | 기본: 68.0점
      → ✗ 기준 미달 (뉴스 점수 낮음)
```

### 3.5 필터링 파이프라인 전체 흐름

```python
async def filtering_pipeline():
    """3단계 필터링 파이프라인"""

    # 1차: 조건검색
    console.print("[1차 필터링] 조건검색 실행...")
    stocks_stage1 = await kiwoom_api.get_condition_search_stocks(
        condition_name="거래량급증_상승추세"
    )
    console.print(f"✓ 1차 통과: {len(stocks_stage1)}개")

    # 2차: VWAP 백테스트
    console.print("\n[2차 필터링] VWAP 백테스트...")
    stocks_stage2 = []

    for code in stocks_stage1:
        chart_data = await fetch_chart_data(code)
        result = validator.validate_strategy(code, chart_data)

        if result['passed']:
            stocks_stage2.append({
                'code': code,
                'vwap_stats': result
            })

    console.print(f"✓ 2차 통과: {len(stocks_stage2)}개")

    # 3차: AI 종합 분석
    console.print("\n[3차 필터링] AI 종합 분석...")
    final_candidates = []

    for stock in stocks_stage2:
        # 데이터 수집 (병렬)
        data = await gather_all_data(stock['code'])

        # AI 분석
        analysis = await engine.analyze_comprehensive(**data)

        if analysis['final_score'] >= 65:
            final_candidates.append({
                **stock,
                'analysis': analysis
            })

            # DB 저장
            db.save_candidate(stock, analysis)

    console.print(f"✓ 3차 통과 (최종): {len(final_candidates)}개")

    return final_candidates
```

### 3.6 필터링 통계 (예시)

```
필터링 단계별 통계:
┌─────────────┬───────────┬──────────┬─────────┐
│ 단계        │ 입력      │ 출력     │ 통과율  │
├─────────────┼───────────┼──────────┼─────────┤
│ 1차: 조건검색│ 2,500개   │ 150개    │ 6.0%    │
│ 2차: VWAP   │ 150개     │ 35개     │ 23.3%   │
│ 3차: AI분석 │ 35개      │ 12개     │ 34.3%   │
├─────────────┼───────────┼──────────┼─────────┤
│ 최종        │ 2,500개   │ 12개     │ 0.48%   │
└─────────────┴───────────┴──────────┴─────────┘

→ 전체 종목 중 상위 0.5% 선별 (고품질 필터링)
```

---

## 4. AI 분석 엔진

### 4.1 뉴스 분석 (30% 가중치)

**목적**: 최근 뉴스의 감성(긍정/부정/중립)을 분석하여 시장 심리 파악

**프로세스**:
```
종목명 검색 → 네이버 뉴스 크롤링 → DeepSeek LLM 분석 → 점수화
```

**구현**:

**1) 뉴스 수집** (`analyzers/news_analyzer.py`):
```python
class NewsAnalyzer:
    async def fetch_news(self, stock_name: str, max_news: int = 10):
        """네이버 뉴스 검색"""
        url = f"https://search.naver.com/search.naver"
        params = {
            'where': 'news',
            'query': stock_name,
            'sort': 'date',  # 최신순
            'nso': 'so:dd,p:1w'  # 최근 1주일
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                html = await response.text()

        # BeautifulSoup으로 파싱
        soup = BeautifulSoup(html, 'html.parser')
        articles = soup.select('.news_area')

        news_list = []
        for article in articles[:max_news]:
            title = article.select_one('.news_tit').text
            description = article.select_one('.news_dsc').text
            link = article.select_one('.news_tit')['href']

            news_list.append({
                'title': title,
                'description': description,
                'link': link
            })

        return news_list
```

**2) 감성 분석** (`analyzers/sentiment_analyzer.py`):
```python
class SentimentAnalyzer:
    def __init__(self):
        self.api_key = os.getenv('DEEPSEEK_API_KEY')
        self.api_url = "https://api.deepseek.com/v1/chat/completions"

    async def analyze_sentiment(self, news_list: List[Dict], stock_name: str):
        """DeepSeek LLM으로 뉴스 감성 분석"""

        # 뉴스 텍스트 결합
        news_text = "\n".join([
            f"- {news['title']}: {news['description']}"
            for news in news_list
        ])

        # 프롬프트 구성
        prompt = f"""
다음은 '{stock_name}' 종목에 대한 최근 뉴스입니다.

{news_text}

위 뉴스들을 종합적으로 분석하여 다음 형식으로 응답해주세요:

1. 전반적 감성: [매우 긍정적 / 긍정적 / 중립 / 부정적 / 매우 부정적]
2. 감성 점수: [0-100점, 100점이 가장 긍정적]
3. 핵심 이슈: [주요 호재/악재 요약 1-2문장]
4. 투자 시사점: [이 뉴스가 주가에 미칠 영향 1-2문장]
"""

        # API 호출
        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }

            payload = {
                'model': 'deepseek-chat',
                'messages': [
                    {'role': 'system', 'content': '당신은 주식 뉴스 분석 전문가입니다.'},
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.3  # 일관성 있는 분석
            }

            async with session.post(self.api_url, json=payload, headers=headers) as response:
                result = await response.json()

        # 응답 파싱
        analysis_text = result['choices'][0]['message']['content']

        # 점수 추출 (정규표현식)
        import re
        score_match = re.search(r'감성 점수:\s*(\d+)', analysis_text)
        score = int(score_match.group(1)) if score_match else 50

        return {
            'score': score,
            'sentiment': self._extract_sentiment(analysis_text),
            'summary': analysis_text,
            'news_count': len(news_list)
        }

    def _extract_sentiment(self, text: str) -> str:
        """감성 레이블 추출"""
        if '매우 긍정적' in text:
            return 'very_positive'
        elif '긍정적' in text:
            return 'positive'
        elif '부정적' in text:
            return 'negative'
        elif '매우 부정적' in text:
            return 'very_negative'
        else:
            return 'neutral'
```

**3) 점수 계산 로직**:
```python
def calculate_news_score(sentiment_result: Dict) -> float:
    """뉴스 점수 계산 (0-100점)"""

    base_score = sentiment_result['score']  # LLM이 준 점수

    # 뉴스 개수 보정 (뉴스가 많을수록 신뢰도 높음)
    news_count = sentiment_result['news_count']
    if news_count >= 10:
        reliability_bonus = 1.0
    elif news_count >= 5:
        reliability_bonus = 0.95
    else:
        reliability_bonus = 0.90

    final_score = base_score * reliability_bonus

    return min(final_score, 100)
```

**출력 예시**:
```json
{
  "score": 75.0,
  "sentiment": "positive",
  "summary": "전반적 감성: 긍정적\n감성 점수: 75점\n핵심 이슈: 신제품 출시 호재와 3분기 실적 개선 전망\n투자 시사점: 단기 상승 모멘텀 확보, 다만 밸류에이션 부담 존재",
  "news_count": 8
}
```

### 4.2 기술적 분석 (40% 가중치)

**목적**: 차트 패턴, 지표를 분석하여 매매 타이밍 포착

**분석 지표**:
1. **RSI (Relative Strength Index)**: 과매수/과매도 판단
2. **MACD (Moving Average Convergence Divergence)**: 추세 전환 포착
3. **볼린저 밴드**: 변동성 및 가격 위치
4. **이동평균선**: 추세 방향 및 지지/저항
5. **거래량 분석**: 가격 움직임의 신뢰도

**구현** (`analyzers/technical_analyzer.py`):

```python
class TechnicalAnalyzer:
    def analyze(self, chart_data: List[Dict]) -> Dict:
        """기술적 분석 실행"""

        if not chart_data or len(chart_data) < 30:
            return {'score': 50, 'signals': ['데이터 부족']}

        df = pd.DataFrame(chart_data)
        df['close'] = pd.to_numeric(df['stck_clpr'])
        df['volume'] = pd.to_numeric(df['acml_vol'])

        # 각 지표 계산
        rsi_result = self.calculate_rsi(df)
        macd_result = self.calculate_macd(df)
        bollinger_result = self.calculate_bollinger(df)
        ma_result = self.analyze_moving_average(df)
        volume_result = self.analyze_volume(df)

        # 종합 점수 (각 20점씩, 총 100점)
        total_score = (
            rsi_result['score'] * 0.20 +
            macd_result['score'] * 0.25 +
            bollinger_result['score'] * 0.20 +
            ma_result['score'] * 0.20 +
            volume_result['score'] * 0.15
        )

        # 시그널 통합
        signals = (
            rsi_result['signals'] +
            macd_result['signals'] +
            bollinger_result['signals'] +
            ma_result['signals'] +
            volume_result['signals']
        )

        return {
            'score': round(total_score, 2),
            'signals': signals,
            'details': {
                'rsi': rsi_result,
                'macd': macd_result,
                'bollinger': bollinger_result,
                'ma': ma_result,
                'volume': volume_result
            }
        }

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> Dict:
        """RSI 계산"""
        close = df['close']

        # 가격 변화
        delta = close.diff()

        # 상승/하락 분리
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # 평균 계산
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()

        # RSI 계산
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        current_rsi = rsi.iloc[-1]

        # 점수화
        if pd.isna(current_rsi):
            score = 50
            signal = "RSI 데이터 부족"
        elif current_rsi <= 30:
            score = 80  # 과매도 → 매수 기회
            signal = f"RSI 과매도 ({current_rsi:.1f}) - 반등 기대"
        elif current_rsi >= 70:
            score = 30  # 과매수 → 위험
            signal = f"RSI 과매수 ({current_rsi:.1f}) - 조정 가능성"
        elif 40 <= current_rsi <= 60:
            score = 60  # 중립
            signal = f"RSI 중립 ({current_rsi:.1f})"
        else:
            score = 50
            signal = f"RSI {current_rsi:.1f}"

        return {
            'score': score,
            'value': current_rsi,
            'signals': [signal]
        }

    def calculate_macd(self, df: pd.DataFrame) -> Dict:
        """MACD 계산"""
        close = df['close']

        # EMA 계산
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()

        # MACD 라인
        macd_line = ema12 - ema26

        # 시그널 라인
        signal_line = macd_line.ewm(span=9, adjust=False).mean()

        # 히스토그램
        histogram = macd_line - signal_line

        current_macd = macd_line.iloc[-1]
        current_signal = signal_line.iloc[-1]
        current_histogram = histogram.iloc[-1]
        prev_histogram = histogram.iloc[-2]

        # 골든크로스/데드크로스 확인
        if current_histogram > 0 and prev_histogram <= 0:
            score = 85
            signal = "MACD 골든크로스 - 강한 매수 신호"
        elif current_histogram < 0 and prev_histogram >= 0:
            score = 25
            signal = "MACD 데드크로스 - 매도 신호"
        elif current_histogram > 0:
            score = 70
            signal = "MACD 상승 추세"
        else:
            score = 40
            signal = "MACD 하락 추세"

        return {
            'score': score,
            'macd': current_macd,
            'signal': current_signal,
            'histogram': current_histogram,
            'signals': [signal]
        }

    def calculate_bollinger(self, df: pd.DataFrame, period: int = 20) -> Dict:
        """볼린저 밴드 계산"""
        close = df['close']

        # 중간 밴드 (20일 이동평균)
        middle_band = close.rolling(window=period).mean()

        # 표준편차
        std = close.rolling(window=period).std()

        # 상단/하단 밴드
        upper_band = middle_band + (std * 2)
        lower_band = middle_band - (std * 2)

        current_price = close.iloc[-1]
        current_upper = upper_band.iloc[-1]
        current_lower = lower_band.iloc[-1]
        current_middle = middle_band.iloc[-1]

        # %B 계산 (밴드 내 위치, 0-1 범위)
        percent_b = (current_price - current_lower) / (current_upper - current_lower)

        # 점수화
        if percent_b <= 0:
            score = 85  # 하단 밴드 이탈 → 반등 기대
            signal = "볼린저 하단 이탈 - 과매도"
        elif percent_b >= 1:
            score = 25  # 상단 밴드 이탈 → 과열
            signal = "볼린저 상단 이탈 - 과매수"
        elif 0.4 <= percent_b <= 0.6:
            score = 60  # 중간 영역
            signal = f"볼린저 중간 영역 (%B: {percent_b:.2f})"
        elif percent_b < 0.3:
            score = 75  # 하단 근처
            signal = f"볼린저 하단 근접 - 매수 기회 (%B: {percent_b:.2f})"
        elif percent_b > 0.7:
            score = 35  # 상단 근처
            signal = f"볼린저 상단 근접 - 조정 가능성 (%B: {percent_b:.2f})"
        else:
            score = 50
            signal = f"볼린저 밴드 내 (%B: {percent_b:.2f})"

        return {
            'score': score,
            'percent_b': percent_b,
            'price': current_price,
            'upper': current_upper,
            'middle': current_middle,
            'lower': current_lower,
            'signals': [signal]
        }

    def analyze_moving_average(self, df: pd.DataFrame) -> Dict:
        """이동평균선 분석"""
        close = df['close']

        # 5일, 20일, 60일 이동평균
        ma5 = close.rolling(window=5).mean()
        ma20 = close.rolling(window=20).mean()
        ma60 = close.rolling(window=60).mean() if len(close) >= 60 else None

        current_price = close.iloc[-1]
        current_ma5 = ma5.iloc[-1]
        current_ma20 = ma20.iloc[-1]
        current_ma60 = ma60.iloc[-1] if ma60 is not None else None

        signals = []
        score = 50

        # 정배열 확인
        if current_ma60 and current_ma5 > current_ma20 > current_ma60:
            score += 20
            signals.append("이동평균 정배열 - 상승 추세")
        elif current_ma60 and current_ma5 < current_ma20 < current_ma60:
            score -= 20
            signals.append("이동평균 역배열 - 하락 추세")

        # 가격과 이동평균 관계
        if current_price > current_ma5:
            score += 10
            signals.append("단기 이평선 상회")
        else:
            score -= 10
            signals.append("단기 이평선 하회")

        if current_price > current_ma20:
            score += 10
            signals.append("중기 이평선 상회")
        else:
            score -= 10
            signals.append("중기 이평선 하회")

        # 골든크로스/데드크로스
        prev_ma5 = ma5.iloc[-2]
        prev_ma20 = ma20.iloc[-2]

        if current_ma5 > current_ma20 and prev_ma5 <= prev_ma20:
            score += 15
            signals.append("골든크로스 발생 (5일 > 20일)")
        elif current_ma5 < current_ma20 and prev_ma5 >= prev_ma20:
            score -= 15
            signals.append("데드크로스 발생 (5일 < 20일)")

        return {
            'score': max(0, min(100, score)),
            'ma5': current_ma5,
            'ma20': current_ma20,
            'ma60': current_ma60,
            'signals': signals
        }

    def analyze_volume(self, df: pd.DataFrame) -> Dict:
        """거래량 분석"""
        volume = df['volume']

        # 20일 평균 거래량
        avg_volume_20 = volume.rolling(window=20).mean()

        current_volume = volume.iloc[-1]
        current_avg = avg_volume_20.iloc[-1]

        # 거래량 비율
        volume_ratio = current_volume / current_avg if current_avg > 0 else 1

        # 점수화
        if volume_ratio >= 2.0:
            score = 90  # 거래량 폭증
            signal = f"거래량 급증 ({volume_ratio:.1f}배) - 강한 관심"
        elif volume_ratio >= 1.5:
            score = 75
            signal = f"거래량 증가 ({volume_ratio:.1f}배)"
        elif volume_ratio >= 1.2:
            score = 65
            signal = f"거래량 정상 이상 ({volume_ratio:.1f}배)"
        elif volume_ratio >= 0.8:
            score = 50
            signal = f"거래량 정상 ({volume_ratio:.1f}배)"
        else:
            score = 30
            signal = f"거래량 저조 ({volume_ratio:.1f}배)"

        return {
            'score': score,
            'current': current_volume,
            'average': current_avg,
            'ratio': volume_ratio,
            'signals': [signal]
        }
```

**출력 예시**:
```json
{
  "score": 72.5,
  "signals": [
    "RSI 중립 (52.3)",
    "MACD 상승 추세",
    "볼린저 중간 영역 (%B: 0.55)",
    "이동평균 정배열 - 상승 추세",
    "단기 이평선 상회",
    "중기 이평선 상회",
    "거래량 증가 (1.6배)"
  ],
  "details": {
    "rsi": {"score": 60, "value": 52.3},
    "macd": {"score": 70, "histogram": 125.3},
    "bollinger": {"score": 60, "percent_b": 0.55},
    "ma": {"score": 80, "ma5": 72500, "ma20": 71000, "ma60": 69500},
    "volume": {"score": 75, "ratio": 1.6}
  }
}
```

### 4.3 수급 분석 (15% 가중치)

**목적**: 외국인/기관 투자자의 매매 동향으로 수급 강도 파악

**핵심 지표**:
- **5일 순매수 금액**: 외국인/기관의 최근 5일간 순매수 총액
- **정규화 비율**: 순매수 금액 / 평균 거래대금 (시가총액 차이 보정)
- **일관성**: 5일 중 순매수 일수 (4일 이상이면 보너스)
- **가속**: 최근 3일 증가 추세 (가속 보너스)

**점수 계산** (`analyzers/supply_demand_analyzer.py`):

```python
class SupplyDemandAnalyzer:
    def __init__(self):
        # 정규화 임계값
        self.thresholds = {
            'very_strong': 0.5,    # 50% 이상
            'strong': 0.2,          # 20% 이상
            'medium': 0.05          # 5% 이상
        }

        # 보정 가중치
        self.bonuses = {
            'consistency': 1.10,     # 일관성 (4일 이상 연속)
            'acceleration': 1.10,    # 가속 (최근 3일 증가)
            'volume_confirm': 1.05   # 거래량 동반
        }

    def component_score(self, net_buy_5d: float, avg_turnover_5d: float,
                       buy_days_5d: int, last3_trend_ok: bool,
                       avg_turnover_20d: float = None) -> float:
        """
        개별 구성요소 점수 (외국인 또는 기관)

        Returns:
            점수 (0-50)
        """
        # 1) 정규화 비율
        norm_ratio = net_buy_5d / max(avg_turnover_5d, 1)

        # 2) 기본 점수
        if norm_ratio >= self.thresholds['very_strong']:
            score = 50.0  # 매우 강한 매수
        elif norm_ratio >= self.thresholds['strong']:
            # 선형 증가
            progress = (norm_ratio - self.thresholds['strong']) / \
                      (self.thresholds['very_strong'] - self.thresholds['strong'])
            score = 50.0 * progress
        elif norm_ratio >= self.thresholds['medium']:
            progress = (norm_ratio - self.thresholds['medium']) / \
                      (self.thresholds['strong'] - self.thresholds['medium'])
            score = 25.0 * progress
        elif norm_ratio > 0:
            progress = norm_ratio / self.thresholds['medium']
            score = 12.5 * progress
        else:
            score = 0.0  # 순매도

        # 3) 일관성 보너스
        if buy_days_5d >= 4:
            score *= self.bonuses['consistency']

        # 4) 가속 보너스
        if last3_trend_ok:
            score *= self.bonuses['acceleration']

        # 5) 거래량 확인 보너스
        if avg_turnover_20d and avg_turnover_5d > 0:
            volume_ratio = avg_turnover_5d / max(avg_turnover_20d, 1)
            if volume_ratio >= 1.2:
                score *= self.bonuses['volume_confirm']

        return min(score, 50.0)

    def analyze(self, investor_data: List[Dict], chart_data: List[Dict] = None) -> Dict:
        """종합 수급 분석"""

        # 데이터 추출
        recent_5d = investor_data[:5]

        # 외국인 데이터
        foreign_amounts = [parse_number(d.get('frgnr_invsr', '0')) for d in recent_5d]
        foreign_net_5d = sum(foreign_amounts)
        foreign_buy_days = sum(1 for amt in foreign_amounts if amt > 0)
        foreign_trend_ok = check_acceleration(foreign_amounts[-3:])

        # 기관 데이터
        inst_amounts = [parse_number(d.get('orgn', '0')) for d in recent_5d]
        inst_net_5d = sum(inst_amounts)
        inst_buy_days = sum(1 for amt in inst_amounts if amt > 0)
        inst_trend_ok = check_acceleration(inst_amounts[-3:])

        # 거래대금
        if chart_data:
            turnovers = [parse_number(d.get('tot_trde_amt', '0')) for d in chart_data[:5]]
            avg_turnover_5d = sum(turnovers) / len(turnovers)

            turnovers_20d = [parse_number(d.get('tot_trde_amt', '0')) for d in chart_data[:20]]
            avg_turnover_20d = sum(turnovers_20d) / len(turnovers_20d)
        else:
            # 추정
            avg_turnover_5d = max(abs(foreign_net_5d), abs(inst_net_5d)) * 20
            avg_turnover_20d = avg_turnover_5d

        # 점수 계산
        foreign_score = self.component_score(
            foreign_net_5d, avg_turnover_5d, foreign_buy_days,
            foreign_trend_ok, avg_turnover_20d
        )

        inst_score = self.component_score(
            inst_net_5d, avg_turnover_5d, inst_buy_days,
            inst_trend_ok, avg_turnover_20d
        )

        # 총점 (0-50점 범위)
        total_score = min(foreign_score + inst_score, 100) / 2

        # 추천 판단
        if total_score >= 42:  # 84% 이상
            recommendation = "강한 동행 수급 (우수)"
        elif total_score >= 32:  # 64% 이상
            recommendation = "수급 양호"
        elif total_score >= 22:  # 44% 이상
            recommendation = "보통 (관망)"
        else:
            recommendation = "수급 약함"

        return {
            'total_score': round(total_score, 2),
            'recommendation': recommendation,
            'foreign': {
                'score': round(foreign_score, 2),
                'amount': foreign_net_5d,
                'buy_days': foreign_buy_days
            },
            'institution': {
                'score': round(inst_score, 2),
                'amount': inst_net_5d,
                'buy_days': inst_buy_days
            }
        }
```

**출력 예시**:
```json
{
  "total_score": 38.5,
  "recommendation": "수급 양호",
  "foreign": {
    "score": 42.3,
    "amount": 15000000000,
    "buy_days": 5
  },
  "institution": {
    "score": 34.7,
    "amount": 8500000000,
    "buy_days": 4
  }
}
```

### 4.4 기본 분석 (15% 가중치)

**목적**: 재무 지표로 기업의 펀더멘탈 평가 (업종 상대평가)

**핵심 지표**:
- **PER** (주가수익비율): 저평가/고평가 판단
- **PBR** (주가순자산비율): 자산 대비 가격
- **ROE** (자기자본이익률): 수익성
- **부채비율**: 재무 안정성
- **영업이익률**: 수익 구조

**업종 상대평가**:
```python
# 동일 업종 내 백분위 계산
sector_percentile = (rank_in_sector / total_in_sector) * 100

# 예: PER이 업종 내 상위 20% → 저평가 → 높은 점수
```

**구현** (`analyzers/fundamental_analyzer.py`):

```python
class FundamentalAnalyzer:
    def analyze(self, financial_data: Dict, sector_code: str = None) -> Dict:
        """기본 분석 실행"""

        # 재무 지표 추출
        per = float(financial_data.get('per', 0))
        pbr = float(financial_data.get('pbr', 0))
        roe = float(financial_data.get('roe', 0))
        debt_ratio = float(financial_data.get('debt_ratio', 0))
        op_margin = float(financial_data.get('op_margin', 0))

        # 각 지표 점수 (0-10점)
        per_score = self._score_per(per)
        pbr_score = self._score_pbr(pbr)
        roe_score = self._score_roe(roe)
        debt_score = self._score_debt(debt_ratio)
        margin_score = self._score_margin(op_margin)

        # 종합 점수 (50점 만점)
        total_score = (
            per_score * 0.25 +
            pbr_score * 0.20 +
            roe_score * 0.25 +
            debt_score * 0.15 +
            margin_score * 0.15
        ) * 5  # 10점 → 50점 변환

        return {
            'score': round(total_score, 2),
            'per': per,
            'pbr': pbr,
            'roe': roe,
            'debt_ratio': debt_ratio,
            'op_margin': op_margin
        }

    def _score_per(self, per: float) -> float:
        """PER 점수 (낮을수록 좋음, 단 음수는 제외)"""
        if per <= 0:
            return 0  # 적자 기업
        elif per <= 10:
            return 10  # 매우 저평가
        elif per <= 15:
            return 8
        elif per <= 20:
            return 6
        elif per <= 30:
            return 4
        else:
            return 2  # 고평가

    def _score_pbr(self, pbr: float) -> float:
        """PBR 점수"""
        if pbr <= 0:
            return 0
        elif pbr < 1.0:
            return 10  # 순자산가치 이하
        elif pbr <= 1.5:
            return 8
        elif pbr <= 2.0:
            return 6
        elif pbr <= 3.0:
            return 4
        else:
            return 2

    def _score_roe(self, roe: float) -> float:
        """ROE 점수 (높을수록 좋음)"""
        if roe <= 0:
            return 0
        elif roe >= 20:
            return 10  # 우수
        elif roe >= 15:
            return 8
        elif roe >= 10:
            return 6
        elif roe >= 5:
            return 4
        else:
            return 2

    def _score_debt(self, debt_ratio: float) -> float:
        """부채비율 점수 (낮을수록 좋음)"""
        if debt_ratio < 0:
            return 0
        elif debt_ratio <= 50:
            return 10  # 매우 안정
        elif debt_ratio <= 100:
            return 8
        elif debt_ratio <= 150:
            return 6
        elif debt_ratio <= 200:
            return 4
        else:
            return 2  # 위험

    def _score_margin(self, margin: float) -> float:
        """영업이익률 점수"""
        if margin <= 0:
            return 0
        elif margin >= 20:
            return 10
        elif margin >= 15:
            return 8
        elif margin >= 10:
            return 6
        elif margin >= 5:
            return 4
        else:
            return 2
```

**출력 예시**:
```json
{
  "score": 42.0,
  "per": 12.5,
  "pbr": 1.3,
  "roe": 15.2,
  "debt_ratio": 85.3,
  "op_margin": 12.8
}
```

### 4.5 통합 분석 및 최종 점수

**구현** (`analyzers/analysis_engine.py`):

```python
class AnalysisEngine:
    def __init__(self):
        # 서브 분석기 초기화
        self.news_analyzer = NewsAnalyzer()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.technical_analyzer = TechnicalAnalyzer()
        self.supply_demand_analyzer = SupplyDemandAnalyzer()
        self.fundamental_analyzer = FundamentalAnalyzer()

        # 가중치 로드 (ConfigManager)
        self.weights = self._load_weights()

    async def analyze_comprehensive(
        self,
        stock_code: str,
        stock_name: str,
        chart_data: List[Dict],
        investor_data: List[Dict],
        financial_data: Dict
    ) -> Dict:
        """종합 분석 실행"""

        # 1. 뉴스 분석
        news_list = await self.news_analyzer.fetch_news(stock_name)
        news_result = await self.sentiment_analyzer.analyze_sentiment(news_list, stock_name)
        news_score = news_result['score']  # 0-100

        # 2. 기술적 분석
        technical_result = self.technical_analyzer.analyze(chart_data)
        technical_score = technical_result['score']  # 0-100

        # 3. 수급 분석
        supply_result = self.supply_demand_analyzer.analyze(investor_data, chart_data)
        supply_score = supply_result['total_score'] * 2  # 0-50 → 0-100

        # 4. 기본 분석
        fundamental_result = self.fundamental_analyzer.analyze(financial_data)
        fundamental_score = fundamental_result['score'] * 2  # 0-50 → 0-100

        # 최종 점수 계산
        final_score = (
            news_score * (self.weights['news'] / 100) +
            technical_score * (self.weights['technical'] / 100) +
            supply_score * (self.weights['supply_demand'] / 100) +
            fundamental_score * (self.weights['fundamental'] / 100)
        )

        # 시장 상황 보정
        market_regime = self._detect_market_regime(chart_data)
        final_score *= self.market_regime_coefficients[market_regime]

        # 추천 판단
        if final_score >= 75:
            recommendation = "강력 매수"
        elif final_score >= 65:
            recommendation = "매수"
        elif final_score >= 50:
            recommendation = "보유"
        elif final_score >= 40:
            recommendation = "관망"
        else:
            recommendation = "매도"

        return {
            'final_score': round(final_score, 2),
            'recommendation': recommendation,
            'news': news_result,
            'technical': technical_result,
            'supply_demand': supply_result,
            'fundamental': fundamental_result,
            'market_regime': market_regime
        }
```

---

## 5. 자동매매 시스템

### 5.1 시스템 흐름

```
일일 루틴 (08:50 시작)
    │
    ├─> [0단계] DB에서 활성 감시 종목 로드
    │
    ├─> [1단계] 키움 API 연결 및 로그인
    │
    ├─> [2단계] 계좌 정보 조회 (잔고, 평가금액)
    │
    ├─> [3단계] Ranker 모델로 종목 순위 매기기
    │       └─> 상위 N개 종목 선정
    │
    ├─> [4단계] 매수 주문 실행 (09:00-09:05)
    │       └─> 각 종목마다 100만원씩 시장가 매수
    │
    ├─> [5단계] 실시간 모니터링 (09:05-15:20)
    │       ├─> WebSocket으로 호가/체결 수신
    │       ├─> 익절/손절 조건 체크
    │       └─> 조건 만족 시 시장가 매도
    │
    └─> [6단계] 장 마감 청산 (15:20)
            └─> 미청산 포지션 전량 매도
```

### 5.2 WebSocket 실시간 처리

**구현** (`main_auto_trading.py`):

```python
class AutoTradingSystem:
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.positions = {}  # 보유 포지션
        self.running = True

        # 매매 파라미터
        self.take_profit_pct = 0.10  # 익절 10%
        self.stop_loss_pct = -0.05   # 손절 -5%
        self.max_stocks = 10
        self.investment_per_stock = 1000000  # 100만원

    async def start(self):
        """자동매매 시작"""
        await self.daily_routine()

    async def daily_routine(self):
        """일일 루틴"""

        # 08:50까지 대기
        await self.wait_until_time(8, 50)

        # 0. DB에서 후보 종목 로드
        self.load_candidates_from_db()

        # 1. 연결 및 로그인
        await self.connect()
        await self.login()

        # 2. 계좌 정보
        await self.initialize_account()

        # 3. Ranker로 순위 매기기
        ranked_stocks = await self.rank_candidates()

        # 4. 매수 (09:00)
        await self.wait_until_time(9, 0)
        await self.execute_buy_orders(ranked_stocks[:self.max_stocks])

        # 5. 실시간 모니터링 (09:05-15:20)
        await self.wait_until_time(9, 5)
        await self.start_monitoring()

        # 6. 장 마감 청산 (15:20)
        await self.wait_until_time(15, 20)
        await self.close_all_positions()

    async def start_monitoring(self):
        """실시간 모니터링"""

        # WebSocket 체결 데이터 구독
        for code in self.positions.keys():
            await self.kiwoom_api.subscribe_execution(code, self.on_execution)

        # 15:20까지 대기
        target_time = datetime.now().replace(hour=15, minute=20)

        while self.running and datetime.now() < target_time:
            await asyncio.sleep(1)

    async def on_execution(self, data: Dict):
        """체결 데이터 수신 콜백"""

        stock_code = data['stock_code']
        current_price = float(data['current_price'])

        if stock_code not in self.positions:
            return

        position = self.positions[stock_code]
        entry_price = position['entry_price']

        # 수익률 계산
        profit_pct = (current_price - entry_price) / entry_price

        # 익절 조건
        if profit_pct >= self.take_profit_pct:
            console.print(f"[green]✓ {stock_code} 익절 ({profit_pct:.2%})[/green]")
            await self.sell(stock_code, current_price, "익절")

        # 손절 조건
        elif profit_pct <= self.stop_loss_pct:
            console.print(f"[red]✗ {stock_code} 손절 ({profit_pct:.2%})[/red]")
            await self.sell(stock_code, current_price, "손절")

    async def sell(self, stock_code: str, price: float, reason: str):
        """매도 주문"""

        quantity = self.positions[stock_code]['quantity']

        # 시장가 매도 주문
        order_result = await self.kiwoom_api.place_order(
            stock_code=stock_code,
            order_type="SELL",
            quantity=quantity,
            price=0  # 시장가
        )

        if order_result['success']:
            # 포지션 제거
            del self.positions[stock_code]

            # DB에 거래 기록
            self.db.save_trade(
                stock_code=stock_code,
                action="SELL",
                price=price,
                quantity=quantity,
                reason=reason
            )
```

### 5.3 Enter 키로 안전 종료

```python
async def wait_until_time(self, target_hour: int, target_minute: int):
    """특정 시각까지 대기 (Enter 키 감지)"""
    import sys
    import select

    # 목표 시간 계산
    target_time = self._calculate_target_time(target_hour, target_minute)

    print(f"⏰ 목표: {target_time.strftime('%m/%d %H:%M')}")
    print("💡 언제든지 [Enter] 키를 눌러 메인 메뉴로 돌아갈 수 있습니다.")

    while self.running:
        now = datetime.now()
        time_diff = (target_time - now).total_seconds()

        if time_diff <= 0:
            print("\n✓ 목표 시각 도달!")
            break

        hours = int(time_diff // 3600)
        minutes = int((time_diff % 3600) // 60)

        # 같은 줄 업데이트
        sys.stdout.write(f"\r⏰ 대기 중... 남은 시간: {hours:02d}시간 {minutes:02d}분 ([Enter]로 종료)   ")
        sys.stdout.flush()

        # Enter 키 감지 (non-blocking)
        for _ in range(60):  # 60초
            if not self.running:
                break

            # stdin에 데이터 있는지 확인
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if line:  # Enter 감지
                    print("\n⚠️  사용자가 대기를 중단했습니다.")
                    self.running = False
                    return

            await asyncio.sleep(1)
```

---

## 6. 백테스트 및 최적화

### 6.1 백테스트 목적

1. **전략 성과 검증**: 지난 N일간 선정된 종목들의 실제 수익률
2. **파라미터 최적화**: 보유 기간, 익절/손절 기준 조정
3. **ML 모델 평가**: Ranker 모델 예측 정확도 확인
4. **리포트 생성**: 투자 결과 리포트 (주간/월간)

### 6.2 백테스트 실행

```python
from backtest_with_ranker import BacktestRunner

runner = BacktestRunner()

# DB에서 후보 종목 로드
candidates = db.get_candidates_by_date_range(
    start_date='2025-10-01',
    end_date='2025-11-03'
)

# 백테스트 실행
results = runner.run_backtest(
    candidates=candidates,
    initial_capital=10000000,  # 1천만원
    holding_period=3,          # 3일 보유
    take_profit=0.10,          # 10% 익절
    stop_loss=-0.05            # -5% 손절
)

# 결과 출력
runner.display_results(results)
```

**출력 예시**:
```
백테스트 결과 (2025-10-01 ~ 2025-11-03)
════════════════════════════════════════════
초기 자본: 10,000,000원
최종 자본: 11,250,000원
총 수익률: +12.5%
총 거래: 45건
승률: 62.2%
평균 수익/손실: +2.8%
최대 낙폭: -3.5%
샤프 비율: 1.85
```

### 6.3 최적화 피드백 루프

**피드백 루프 다이어그램**:
```
백테스트 실행
    │
    ▼
점수-수익률 상관관계 분석
    │
    ▼
가중치 자동 재분배 제안
    │
    ▼
사용자 승인
    │
    ▼
ConfigManager에 저장 (백업 생성)
    │
    ▼
다음 분석부터 자동 반영
```

**구현** (`analyzers/backtest_optimizer.py`):

```python
class BacktestOptimizer:
    def generate_optimization_report(self, candidates: pd.DataFrame) -> Dict:
        """최적화 리포트 생성"""

        # 1. 점수-수익률 상관관계
        correlations = {}
        for score_col in ['news_score', 'technical_score', 'supply_score', 'fundamental_score']:
            corr = candidates[score_col].corr(candidates['actual_return'])
            correlations[score_col] = corr

        # 2. 상관계수 비율로 가중치 재분배
        abs_corrs = {k: abs(v) for k, v in correlations.items()}
        total_abs = sum(abs_corrs.values())

        suggested_weights = {
            'news': abs_corrs['news_score'] / total_abs,
            'technical': abs_corrs['technical_score'] / total_abs,
            'supply_demand': abs_corrs['supply_score'] / total_abs,
            'fundamental': abs_corrs['fundamental_score'] / total_abs
        }

        # 3. 점수 구간별 성과
        score_ranges = self.analyze_score_range_performance(candidates)

        # 4. 종합 추천
        recommendations = self._generate_recommendations(
            correlations,
            suggested_weights,
            score_ranges
        )

        return {
            'correlations': correlations,
            'suggested_weights': suggested_weights,
            'score_ranges': score_ranges,
            'recommendations': recommendations
        }
```

**UI 표시** (`main_menu.py`):

```python
# 백테스트 후 최적화 분석
optimizer = BacktestOptimizer()
opt_report = optimizer.generate_optimization_report(results_df)

# 1. 상관관계 테이블
console.print("\n[bold]1️⃣  점수-수익률 상관관계:[/bold]")
corr_table = Table()
corr_table.add_column("점수 타입")
corr_table.add_column("상관계수")

for score_type, corr in opt_report['correlations'].items():
    color = "green" if abs(corr) > 0.3 else "yellow"
    corr_table.add_row(score_type, f"[{color}]{corr:.3f}[/{color}]")

console.print(corr_table)

# 2. 가중치 조정 제안
console.print("\n[bold]2️⃣  가중치 조정 제안:[/bold]")
weight_table = Table()
weight_table.add_column("요소")
weight_table.add_column("현재")
weight_table.add_column("제안")
weight_table.add_column("변화")

for key, new_weight in opt_report['suggested_weights'].items():
    old_weight = current_weights[key]
    diff = new_weight - old_weight

    color = "green" if diff > 0.05 else "red" if diff < -0.05 else "yellow"
    weight_table.add_row(
        key,
        f"{old_weight:.2%}",
        f"{new_weight:.2%}",
        f"[{color}]{diff:+.2%}[/{color}]"
    )

console.print(weight_table)

# 3. 적용 여부
apply = console.input("\n가중치 조정을 적용하시겠습니까? (y/n): ")

if apply.lower() == 'y':
    optimizer.apply_suggested_weights(opt_report['suggested_weights'])
    console.print("[green]✅ 가중치 업데이트 완료![/green]")
```

---

## 7. 데이터베이스 스키마

### 7.1 SQLite 테이블 구조

**1) candidates (후보 종목)**:
```sql
CREATE TABLE candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    date_detected TEXT NOT NULL,
    total_score REAL,
    news_score REAL,
    technical_score REAL,
    supply_score REAL,
    fundamental_score REAL,
    vwap_win_rate REAL,
    vwap_trades INTEGER,
    vwap_avg_profit REAL,
    analysis_detail TEXT,  -- JSON
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_candidates_date ON candidates(date_detected);
CREATE INDEX idx_candidates_score ON candidates(total_score DESC);
CREATE INDEX idx_candidates_active ON candidates(is_active);
```

**2) trades (거래 기록)**:
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    action TEXT NOT NULL,  -- BUY, SELL
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    amount REAL NOT NULL,
    reason TEXT,  -- 익절, 손절, 장마감 등
    executed_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trades_date ON trades(executed_at);
CREATE INDEX idx_trades_stock ON trades(stock_code);
```

**3) ml_training_data (ML 학습 데이터)**:
```sql
CREATE TABLE ml_training_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    date TEXT NOT NULL,
    features TEXT NOT NULL,  -- JSON
    label REAL,  -- 3일 후 수익률
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ml_data_date ON ml_training_data(date);
```

**4) ranker_predictions (Ranker 예측 결과)**:
```sql
CREATE TABLE ranker_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    date TEXT NOT NULL,
    buy_probability REAL,
    predicted_return REAL,
    actual_return REAL,  -- 나중에 업데이트
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_predictions_date ON ranker_predictions(date);
```

### 7.2 데이터베이스 관리 클래스

**구현** (`database/trading_db.py`):

```python
class TradingDatabase:
    def __init__(self, db_path: str = "./data/trading.db"):
        self.db_path = db_path
        self._init_database()

    def save_candidate(self, stock_code: str, stock_name: str,
                      total_score: float, analysis: Dict):
        """후보 종목 저장"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO candidates (
                stock_code, stock_name, date_detected,
                total_score, news_score, technical_score,
                supply_score, fundamental_score,
                vwap_win_rate, vwap_trades, vwap_avg_profit,
                analysis_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stock_code, stock_name, datetime.now().isoformat(),
            total_score,
            analysis['news']['score'],
            analysis['technical']['score'],
            analysis['supply_demand']['total_score'],
            analysis['fundamental']['score'],
            analysis.get('vwap_win_rate', 0),
            analysis.get('vwap_trades', 0),
            analysis.get('vwap_avg_profit', 0),
            json.dumps(analysis, ensure_ascii=False)
        ))

        conn.commit()
        conn.close()

    def get_active_candidates(self) -> List[Dict]:
        """활성 후보 종목 조회"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM candidates
            WHERE is_active = 1
            ORDER BY total_score DESC
        """)

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]
```

---

## 8. 설정 및 환경변수

### 8.1 .env 파일

```.env
# 키움증권 API
KIWOOM_APP_KEY=your_app_key_here
KIWOOM_APP_SECRET=your_app_secret_here
KIWOOM_ACCOUNT=your_account_number

# DeepSeek AI
DEEPSEEK_API_KEY=your_deepseek_api_key

# 텔레그램 알림
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 환경
ENV=production  # production, development
LOG_LEVEL=INFO
```

### 8.2 config/analysis_weights.json

```json
{
  "analysis_weights": {
    "news": 0.30,
    "technical": 0.40,
    "supply_demand": 0.15,
    "fundamental": 0.15
  },
  "filter_params": {
    "min_total_score": 65,
    "min_vwap_win_rate": 0.50,
    "min_vwap_trades": 2,
    "min_chart_bars": 100
  },
  "trading_params": {
    "holding_period_days": 3,
    "take_profit_pct": 0.10,
    "stop_loss_pct": -0.05,
    "max_stocks": 10,
    "investment_per_stock": 1000000
  },
  "last_updated": "2025-11-03T10:30:00",
  "version": "2.0"
}
```

---

## 9. API 명세

### 9.1 키움 OpenAPI 주요 함수

**1) 로그인**:
```python
async def login(app_key: str, app_secret: str) -> bool
```

**2) 조건검색**:
```python
async def get_condition_search_stocks(
    screen_no: str,
    condition_name: str,
    search_type: int = 0
) -> List[str]
```

**3) 차트 데이터**:
```python
async def get_chart_data(
    stock_code: str,
    timeframe: str = 'D',  # D: 일봉, 5: 5분봉
    count: int = 100
) -> List[Dict]
```

**4) 투자자 매매 동향**:
```python
async def get_investor_data(
    stock_code: str,
    days: int = 30
) -> List[Dict]
```

**5) 주문**:
```python
async def place_order(
    stock_code: str,
    order_type: str,  # BUY, SELL
    quantity: int,
    price: int = 0  # 0: 시장가
) -> Dict
```

**6) WebSocket 구독**:
```python
async def subscribe_execution(
    stock_code: str,
    callback: Callable
) -> None
```

### 9.2 DeepSeek API

**엔드포인트**: `https://api.deepseek.com/v1/chat/completions`

**요청 예시**:
```json
{
  "model": "deepseek-chat",
  "messages": [
    {
      "role": "system",
      "content": "당신은 주식 뉴스 분석 전문가입니다."
    },
    {
      "role": "user",
      "content": "다음 뉴스를 분석해주세요..."
    }
  ],
  "temperature": 0.3
}
```

**응답 예시**:
```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "전반적 감성: 긍정적\n감성 점수: 75점\n..."
      }
    }
  ]
}
```

---

## 10. 배포 및 운영

### 10.1 실행 방법

**1) 초기 설정**:
```bash
# 가상환경 생성
python3 -m venv venv
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt

# .env 파일 생성
cp .env.example .env
# .env 파일 편집 (API 키 입력)
```

**2) 실행**:
```bash
# 메인 메뉴 실행
./run.sh

# 또는
source venv/bin/activate && python main_menu.py
```

**3) 메뉴 선택**:
```
키움증권 AI Trading System v2.0
══════════════════════════════════════

[1] 자동 매매 시작
[2] 조건 검색 & 필터링
[3] ML 모델 학습
[4] Ranker 실전 테스트
[5] 백테스트
[6] 리포트 생성
[7] Telegram 알림 테스트
[0] 종료

선택 >>>
```

### 10.2 주요 운영 시나리오

**시나리오 1: 매일 자동매매**
```
1. [1] 자동 매매 시작 선택
2. 08:50까지 대기 (또는 Enter로 중단)
3. 조건검색 → VWAP → AI 분석 자동 실행
4. 09:00 매수, 실시간 모니터링, 15:20 청산
5. 다음날 반복
```

**시나리오 2: 주말 백테스트 & 최적화**
```
1. [5] 백테스트 선택
2. 기간 선택 (예: 최근 30일)
3. 최적화 분석 확인
4. 가중치 조정 적용
5. 다음주부터 새 가중치로 자동 반영
```

**시나리오 3: ML 모델 재학습**
```
1. [2] 조건 검색 실행 (데이터 수집)
2. [3] ML 모델 학습
3. [4] Ranker 테스트
4. [1] 자동 매매에서 새 모델 사용
```

### 10.3 로그 및 모니터링

**로그 위치**:
```
logs/
├── trading_20251103.log       # 일일 거래 로그
├── analysis_20251103.log      # 분석 로그
└── error_20251103.log         # 에러 로그
```

**텔레그램 알림**:
- 매수/매도 체결 알림
- 익절/손절 알림
- 시스템 오류 알림

### 10.4 백업 및 복구

**자동 백업**:
- 가중치 변경 시 자동 백업: `config/backups/weights_backup_*.json`
- 데이터베이스 일일 백업: `data/backups/trading_*.db`

**복구 방법**:
```python
from utils.config_manager import ConfigManager

manager = ConfigManager()
manager.restore_from_backup('./config/backups/weights_backup_20251102.json')
```

### 10.5 성능 최적화

**1) 비동기 처리**:
- 모든 API 호출 `async/await` 사용
- 병렬 데이터 수집 (`asyncio.gather`)

**2) 캐싱**:
- 차트 데이터 메모리 캐시 (5분)
- 뉴스 데이터 캐시 (10분)

**3) DB 인덱스**:
- 날짜, 종목코드, 점수에 인덱스 생성
- 빠른 조회 성능

---

## 부록

### A. 전체 데이터 흐름 요약

```
[시장] → 조건검색 (50-200개)
           │
           ▼
       VWAP 백테스트 (20-50개)
           │
           ▼
       AI 종합 분석 (10-20개)
           │
           ├─→ DB 저장
           │
           ├─→ Ranker 학습
           │       │
           │       ▼
           │   Ranker 예측 (상위 N개)
           │       │
           │       ▼
           └─→ 자동매매 실행
                   │
                   ├─→ 실시간 모니터링
                   │
                   └─→ 백테스트 분석
                           │
                           ▼
                       최적화 피드백 ──┐
                           │           │
                           ▼           │
                       가중치 조정 ────┘
```

### B. 주요 성능 지표 (예상)

| 항목 | 목표 | 실제 |
|------|------|------|
| 조건검색 → DB 저장 | < 5분 | 3-4분 |
| VWAP 백테스트 (100개) | < 2분 | 1.5분 |
| AI 종합 분석 (50개) | < 10분 | 7-9분 |
| 백테스트 (30일, 100개) | < 3분 | 2분 |
| WebSocket 응답 지연 | < 100ms | 50-80ms |

### C. 트러블슈팅

**문제 1: API 429 오류 (Rate Limit)**
- 원인: 요청 빈도 초과
- 해결: `asyncio.sleep(0.5)` 추가, 배치 크기 감소

**문제 2: DeepSeek API 타임아웃**
- 원인: 네트워크 지연
- 해결: `timeout=30` 설정, 재시도 로직

**문제 3: WebSocket 연결 끊김**
- 원인: 네트워크 불안정
- 해결: 자동 재연결 로직, heartbeat

**문제 4: 메모리 부족**
- 원인: 차트 데이터 과다 적재
- 해결: 캐시 LRU 방식, 메모리 제한

---

## 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2025-11-03 | 2.0 | 초기 문서 작성 |
| 2025-11-03 | 2.0 | 백테스트 최적화 피드백 루프 추가 |
| 2025-11-03 | 2.0 | Enter 키 안전 종료 기능 추가 |

---

**작성자**: Claude AI
**검토자**: -
**최종 수정일**: 2025년 11월 3일

---

**문서 끝**
