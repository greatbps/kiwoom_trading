# news_sentiment_v2.py
"""
v2 버전: AI 기반 감성 분석 및 시간 가중치 적용

주요 기능:
- HuggingFace Transformers를 활용한 감성 분석 (선택적)
- 시간 가중치: 최근 뉴스에 더 높은 가중치 부여
- 비동기 처리 (async/await)
- 캐시 시스템 (JSON 파일 기반)
- 한국 뉴스 수집 (네이버 API)
"""
import asyncio
import aiohttp
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# HuggingFace transformers (선택적)
try:
    from transformers import pipeline
    HF_AVAILABLE = True
except Exception:
    HF_AVAILABLE = False
    logger.info("HuggingFace transformers 미설치 - 키워드 기반 감성분석 사용")


class NewsSentimentAnalyzerV2:
    """AI 기반 감성 분석기 (v2)

    Features:
      - HuggingFace sentiment model (선택적, fallback: keyword method)
      - Time-weighted aggregation (최근 뉴스에 높은 가중치)
      - Async fetching (네이버 뉴스 API)
      - Translation caching
      - DB fallback: JSON cache file
    """

    def __init__(self, symbols: List[str], hf_model_name: str = 'nlptown/bert-base-multilingual-uncased-sentiment',
                 max_concurrency: int = 6, client_id: str = None, client_secret: str = None):
        """
        초기화

        Args:
            symbols: 분석할 종목 리스트 (한국 주식 코드 또는 이름)
            hf_model_name: HuggingFace 모델 이름
            max_concurrency: 최대 동시 요청 수
            client_id: 네이버 API 클라이언트 ID
            client_secret: 네이버 API 클라이언트 시크릿
        """
        self.symbols = symbols
        self.sentiment_scores = {s: 0.0 for s in symbols}
        self.last_update = None
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._semaphore = asyncio.Semaphore(max_concurrency)

        # 네이버 API 설정
        from dotenv import load_dotenv
        load_dotenv()
        self.client_id = client_id or os.getenv("NAVER_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("NAVER_CLIENT_SECRET")

        # HF pipeline lazy init
        self.hf_model_name = hf_model_name
        self._hf_pipeline = None

        # caching
        self.translation_cache = {}  # {text_hash: translated_text}
        self.summary_cache = {}  # {symbol: {summary, updated_at}}
        cache_dir = Path('cache')
        cache_dir.mkdir(exist_ok=True)
        self.cache_file = cache_dir / 'news_sentiment_v2.json'

        # 키워드 사전 (한국어)
        self.positive_keywords = {
            '급등', '상승', '호재', '증가', '실적개선', '수주', '신제품', '특허', '투자', '확대',
            '긍정', '성장', '매수', '강세', '돌파', '신고가', '수익', '이익', '개선', '호황'
        }
        self.negative_keywords = {
            '급락', '하락', '악재', '감소', '실적악화', '취소', '리콜', '소송', '적자', '감원',
            '부정', '위축', '매도', '약세', '하회', '신저가', '손실', '적자', '악화', '불황'
        }

        # load caches
        self._load_disk_cache()

    def _load_disk_cache(self):
        """디스크 캐시 로드"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.summary_cache = json.load(f)
                    logger.info(f"캐시 로드: {len(self.summary_cache)}개 항목")
        except Exception as e:
            logger.warning(f"캐시 로드 실패: {e}")

    def _save_disk_cache(self):
        """디스크 캐시 저장"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.summary_cache, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.warning(f"캐시 저장 실패: {e}")

    def _ensure_hf(self):
        """HuggingFace pipeline 초기화"""
        if not HF_AVAILABLE:
            return None
        if self._hf_pipeline is None:
            try:
                self._hf_pipeline = pipeline('sentiment-analysis', model=self.hf_model_name)
                logger.info("HuggingFace 감성 분석 모델 로드 완료")
            except Exception as e:
                logger.warning(f"HF 모델 로드 실패: {e}")
                self._hf_pipeline = None
        return self._hf_pipeline

    async def _hf_sentiment_async(self, texts: List[str]) -> List[float]:
        """HF 모델을 threadpool에서 실행 (비동기)"""
        pipe = self._ensure_hf()
        if not pipe:
            # fallback to keyword
            return [self._keyword_sentiment(t) for t in texts]

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(self._executor, pipe, texts)

        # 점수 매핑 (1-5 stars -> -1.0 to 1.0)
        mapped = []
        for r in results:
            label = r.get('label', '3 stars')
            # Extract number
            match = re.search(r'(\d+)', label)
            if match:
                stars = int(match.group(1))
                # 1 star = -1.0, 3 stars = 0.0, 5 stars = 1.0
                score = (stars - 3) / 2.0
                mapped.append(max(-1.0, min(1.0, score)))
            else:
                mapped.append(0.0)
        return mapped

    def _keyword_sentiment(self, text: str) -> float:
        """키워드 기반 감성 분석"""
        if not text:
            return 0.0
        t = text.lower()
        pos = sum(1 for k in self.positive_keywords if k in t)
        neg = sum(1 for k in self.negative_keywords if k in t)
        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total

    def _time_weight(self, published: datetime) -> float:
        """시간 가중치 계산 (exponential decay)

        최근 뉴스일수록 가중치 높음. 12시간 half-life.
        """
        age = (datetime.now() - published).total_seconds()
        half_life = 60 * 60 * 12  # 12시간
        weight = 2 ** (-age / half_life)
        return max(0.0, min(1.0, weight))

    async def _fetch_naver_news(self, query: str, display: int = 10) -> List[Dict]:
        """네이버 뉴스 검색 API로 뉴스 수집 (async)"""
        if not self.client_id or not self.client_secret:
            logger.warning("네이버 API 키 미설정 - 뉴스 수집 불가")
            return []

        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret
        }
        params = {
            "query": query,
            "display": display,
            "sort": "date"
        }

        async with self._semaphore:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, params=params, timeout=10) as resp:
                        if resp.status != 200:
                            logger.debug(f"네이버 뉴스 HTTP {resp.status} for {query}")
                            return []
                        data = await resp.json()
            except Exception as e:
                logger.debug(f"네이버 뉴스 fetch 실패 for {query}: {e}")
                return []

        items = data.get('items', [])
        processed = []
        for item in items:
            title = self._remove_html_tags(item.get('title', ''))
            desc = self._remove_html_tags(item.get('description', ''))
            pub_date_str = item.get('pubDate', '')

            # Parse pub_date (RFC 822 format)
            pub_date = self._parse_rfc822_date(pub_date_str)

            processed.append({
                'title': title,
                'description': desc,
                'published': pub_date or datetime.now(),
                'link': item.get('link', '')
            })

        return processed

    def _remove_html_tags(self, text: str) -> str:
        """HTML 태그 제거"""
        clean = re.compile('<.*?>')
        text = re.sub(clean, '', text)
        text = text.replace('&quot;', '"').replace('&amp;', '&')
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        return text.strip()

    def _parse_rfc822_date(self, date_str: str) -> Optional[datetime]:
        """RFC 822 날짜 파싱"""
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(date_str)
        except:
            return None

    async def analyze_symbol(self, symbol: str):
        """종목에 대한 뉴스 수집 및 감성 분석"""
        news = await self._fetch_naver_news(symbol, display=10)

        if not news:
            logger.info(f"{symbol} 뉴스 없음")
            self.sentiment_scores[symbol] = 0.0
            return 0.0

        texts = []
        weights = []
        for item in news:
            txt = (item.get('title', '') + ' ' + item.get('description', '')).strip()
            if not txt:
                continue
            w = self._time_weight(item.get('published', datetime.now()))
            texts.append(txt)
            weights.append(w)

        if not texts:
            self.sentiment_scores[symbol] = 0.0
            return 0.0

        # HF 감성 분석
        hf_scores = await self._hf_sentiment_async(texts)

        # 가중 평균
        total_w = sum(weights)
        if total_w == 0:
            weighted = 0.0
        else:
            weighted = sum(s * w for s, w in zip(hf_scores, weights)) / total_w

        # 캐시 저장
        now = datetime.now().isoformat()
        self.summary_cache[symbol] = {
            'sentiment_score': weighted,
            'news_count': len(news),
            'updated_at': now
        }
        self._save_disk_cache()

        self.sentiment_scores[symbol] = float(weighted)
        self.last_update = datetime.now()
        logger.info(f"{symbol} 감성 점수: {weighted:.2f} ({len(news)}건)")
        return float(weighted)

    async def update_all(self):
        """모든 종목 업데이트"""
        logger.info("📰 뉴스 감성분석 시작...")
        tasks = [self.analyze_symbol(s) for s in self.symbols]
        await asyncio.gather(*tasks)
        logger.info("📊 감성분석 완료")

    def get_sentiment_score(self, symbol: str) -> float:
        """종목의 감성 점수 반환 (-1.0 ~ 1.0)"""
        return float(self.sentiment_scores.get(symbol, 0.0))

    def get_all_scores(self):
        """모든 감성 점수 반환"""
        return dict(self.sentiment_scores)


async def sentiment_updater_task(analyzer: NewsSentimentAnalyzerV2, interval_minutes: int = 15):
    """백그라운드 업데이트 태스크"""
    while True:
        try:
            await analyzer.update_all()
            await asyncio.sleep(interval_minutes * 60)
        except asyncio.CancelledError:
            logger.info("감성분석 태스크 종료")
            break
        except Exception as e:
            logger.error(f"감성분석 태스크 오류: {e}")
            await asyncio.sleep(60)


if __name__ == "__main__":
    # 간단한 테스트
    import sys
    logging.basicConfig(level=logging.INFO)

    async def test():
        analyzer = NewsSentimentAnalyzerV2(symbols=['삼성전자', 'SK하이닉스'])
        await analyzer.update_all()

        for symbol in analyzer.symbols:
            score = analyzer.get_sentiment_score(symbol)
            print(f"{symbol}: {score:+.2f}")

    asyncio.run(test())
