"""
Gemini AI를 활용한 뉴스 분석 테스트
네이버 뉴스 API로 뉴스 수집 → Gemini로 감성 및 영향도 분석
"""
import sys
import os
import json
from datetime import datetime

# 상위 디렉토리의 모듈을 import하기 위해 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import requests
import google.generativeai as genai


class NaverNewsCollector:
    """네이버 뉴스 검색 API 수집기"""

    def __init__(self):
        load_dotenv()
        self.client_id = os.getenv("NAVER_CLIENT_ID")
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET")

        if not self.client_id or not self.client_secret:
            raise ValueError("네이버 API 키가 설정되지 않았습니다.")

    def search_news(self, query: str, display: int = 10):
        """
        네이버 뉴스 검색

        Args:
            query: 검색어 (종목명)
            display: 검색 결과 개수 (최대 100)

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
            "sort": "date"  # 최신순
        }

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()

            result = response.json()
            items = result.get("items", [])

            print(f"✓ 네이버 뉴스 검색 완료: {len(items)}개 발견")
            return items

        except requests.exceptions.RequestException as e:
            print(f"✗ 네이버 뉴스 검색 실패: {e}")
            return []


class GeminiNewsAnalyzer:
    """Gemini AI 뉴스 분석기"""

    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY")

        if not self.api_key:
            raise ValueError("Gemini API 키가 설정되지 않았습니다.")

        genai.configure(api_key=self.api_key)
        # Gemini 2.5 Flash Lite 모델 사용 (최신 모델)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')

    def analyze_news_sentiment(self, news_list: list, stock_name: str):
        """
        뉴스 감성 분석

        Args:
            news_list: 뉴스 리스트
            stock_name: 종목명

        Returns:
            분석 결과
        """
        if not news_list:
            return None

        # 뉴스 제목과 설명 결합
        news_text = "\n".join([
            f"- {item['title']} : {item['description']}"
            for item in news_list[:5]  # 최대 5개만 분석
        ])

        # Gemini 프롬프트
        prompt = f"""
다음은 "{stock_name}" 종목에 대한 최신 뉴스입니다.

{news_text}

위 뉴스들을 분석하여 다음 정보를 JSON 형식으로 제공해주세요:

1. sentiment: 전체적인 감성 (VERY_POSITIVE, POSITIVE, NEUTRAL, NEGATIVE, VERY_NEGATIVE 중 하나)
2. sentiment_score: 감성 점수 (-100 ~ +100, 음수는 부정, 양수는 긍정)
3. confidence: 분석 신뢰도 (0.0 ~ 1.0)
4. impact: 주가 영향도 (HIGH, MEDIUM, LOW 중 하나)
5. positive_factors: 긍정적 요인 리스트 (최대 3개)
6. negative_factors: 부정적 요인 리스트 (최대 3개)
7. summary: 전체 요약 (2-3 문장)
8. recommendation: 투자 의견 (STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL 중 하나)

반드시 유효한 JSON 형식으로만 응답해주세요. 다른 텍스트는 포함하지 마세요.
"""

        try:
            print("\n[Gemini AI 분석 중...]")
            response = self.model.generate_content(prompt)
            result_text = response.text.strip()

            # JSON 파싱 시도
            # 코드 블록 제거
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            result = json.loads(result_text)

            print("✓ Gemini AI 분석 완료")
            return result

        except json.JSONDecodeError as e:
            print(f"✗ JSON 파싱 실패: {e}")
            print(f"응답 내용:\n{result_text}")
            return None
        except Exception as e:
            print(f"✗ Gemini AI 분석 실패: {e}")
            return None


def test_news_analysis(stock_name: str = "삼성전자"):
    """뉴스 분석 테스트 실행"""

    print("=" * 70)
    print(f"📰 {stock_name} 뉴스 분석 테스트")
    print("=" * 70)

    try:
        # 1. 네이버 뉴스 수집
        print(f"\n[1단계] 네이버 뉴스 검색")
        print("-" * 70)

        news_collector = NaverNewsCollector()
        news_list = news_collector.search_news(stock_name, display=5)

        if not news_list:
            print("검색된 뉴스가 없습니다.")
            return

        # 뉴스 제목 출력
        print(f"\n📋 수집된 뉴스 ({len(news_list)}개):")
        for i, news in enumerate(news_list, 1):
            # HTML 태그 제거
            title = news['title'].replace('<b>', '').replace('</b>', '')
            print(f"{i}. {title}")
            print(f"   {news['link']}")

        # 2. Gemini 분석
        print(f"\n[2단계] Gemini AI 감성 분석")
        print("-" * 70)

        analyzer = GeminiNewsAnalyzer()
        analysis = analyzer.analyze_news_sentiment(news_list, stock_name)

        if not analysis:
            print("분석 결과를 가져올 수 없습니다.")
            return

        # 3. 결과 출력
        print(f"\n[3단계] 분석 결과")
        print("=" * 70)

        print(f"\n📊 감성 분석 결과:")
        print(f"  • 감성: {analysis.get('sentiment', 'N/A')}")
        print(f"  • 점수: {analysis.get('sentiment_score', 0)}/100")
        print(f"  • 신뢰도: {analysis.get('confidence', 0):.2%}")
        print(f"  • 영향도: {analysis.get('impact', 'N/A')}")

        positive = analysis.get('positive_factors', [])
        if positive:
            print(f"\n✅ 긍정적 요인:")
            for factor in positive:
                print(f"  • {factor}")

        negative = analysis.get('negative_factors', [])
        if negative:
            print(f"\n❌ 부정적 요인:")
            for factor in negative:
                print(f"  • {factor}")

        print(f"\n📝 요약:")
        print(f"  {analysis.get('summary', 'N/A')}")

        print(f"\n💡 투자 의견: {analysis.get('recommendation', 'N/A')}")

        print("\n" + "=" * 70)
        print("✓ 테스트 완료")
        print("=" * 70)

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 기본 테스트: 삼성전자
    test_news_analysis("삼성전자")

    # 다른 종목 테스트하려면:
    # test_news_analysis("SK하이닉스")
    # test_news_analysis("네이버")
