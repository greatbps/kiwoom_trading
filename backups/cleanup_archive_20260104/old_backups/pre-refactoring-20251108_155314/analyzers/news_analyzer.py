"""
뉴스 수집 및 분석 모듈
네이버 뉴스 API를 통해 뉴스를 수집하고 전처리
"""
import os
from typing import List, Dict, Optional
from datetime import datetime
from dotenv import load_dotenv
import requests


class NewsAnalyzer:
    """뉴스 수집 및 분석 클래스"""

    def __init__(self):
        """초기화"""
        load_dotenv()
        self.client_id = os.getenv("NAVER_CLIENT_ID")
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET")

        if not self.client_id or not self.client_secret:
            raise ValueError("네이버 API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")

    def collect_news(self, query: str, display: int = 10, sort: str = "date") -> List[Dict]:
        """
        네이버 뉴스 검색 API로 뉴스 수집

        Args:
            query: 검색어 (종목명)
            display: 검색 결과 개수 (최대 100)
            sort: 정렬 기준 (date: 최신순, sim: 관련도순)

        Returns:
            뉴스 리스트
        """
        url = "https://openapi.naver.com/v1/search/news.json"

        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret
        }

        params = {
            "query": query,
            "display": display,
            "sort": sort
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()

            result = response.json()
            items = result.get("items", [])

            # 뉴스 전처리
            processed_news = self._preprocess_news(items)

            return processed_news

        except requests.exceptions.Timeout:
            print(f"✗ 네이버 뉴스 검색 타임아웃")
            return []
        except requests.exceptions.RequestException as e:
            print(f"✗ 네이버 뉴스 검색 실패: {e}")
            return []

    def _preprocess_news(self, news_items: List[Dict]) -> List[Dict]:
        """
        뉴스 데이터 전처리

        Args:
            news_items: 원본 뉴스 리스트

        Returns:
            전처리된 뉴스 리스트
        """
        processed = []

        for item in news_items:
            # HTML 태그 제거
            title = self._remove_html_tags(item.get('title', ''))
            description = self._remove_html_tags(item.get('description', ''))

            # 발행 시간 파싱
            pub_date = item.get('pubDate', '')

            processed.append({
                'title': title,
                'description': description,
                'link': item.get('link', ''),
                'pub_date': pub_date,
                'original_link': item.get('originallink', ''),
            })

        return processed

    def _remove_html_tags(self, text: str) -> str:
        """
        HTML 태그 제거

        Args:
            text: HTML 태그가 포함된 텍스트

        Returns:
            태그가 제거된 텍스트
        """
        import re
        clean = re.compile('<.*?>')
        clean_text = re.sub(clean, '', text)

        # HTML 엔티티 변환
        clean_text = clean_text.replace('&quot;', '"')
        clean_text = clean_text.replace('&amp;', '&')
        clean_text = clean_text.replace('&lt;', '<')
        clean_text = clean_text.replace('&gt;', '>')

        return clean_text.strip()

    def analyze_news(self, stock_code: str, stock_name: str, display: int = 10) -> Dict:
        """
        종목 뉴스 수집 및 기본 분석

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            display: 수집할 뉴스 개수

        Returns:
            분석 결과
        """
        # 뉴스 수집
        news_list = self.collect_news(stock_name, display=display)

        if not news_list:
            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'news_count': 0,
                'news_list': [],
                'collected_at': datetime.now().isoformat()
            }

        # 뉴스 빈도 점수 계산 (0~100)
        frequency_score = min(len(news_list) * 10, 100)  # 10개 이상이면 만점

        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'news_count': len(news_list),
            'news_list': news_list,
            'frequency_score': frequency_score,
            'collected_at': datetime.now().isoformat()
        }


if __name__ == "__main__":
    # 간단한 테스트
    analyzer = NewsAnalyzer()
    result = analyzer.analyze_news("005930", "삼성전자", display=5)

    print(f"종목: {result['stock_name']}")
    print(f"뉴스 개수: {result['news_count']}")
    print(f"빈도 점수: {result['frequency_score']}")

    for i, news in enumerate(result['news_list'][:3], 1):
        print(f"\n{i}. {news['title']}")
