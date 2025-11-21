"""
AI 기반 뉴스 감성 분석 모듈
Gemini AI / DeepSeek / GPT를 활용한 뉴스 감성 및 영향도 분석
"""
import os
import json
from typing import List, Dict, Optional
from datetime import datetime
from dotenv import load_dotenv
import google.generativeai as genai
from openai import OpenAI
from analyzers.news_material_classifier import NewsMaterialClassifier


class SentimentAnalyzer:
    """AI 기반 감성 분석 클래스"""

    def __init__(self, provider: str = None):
        """
        초기화

        Args:
            provider: AI 제공자 (gemini, deepseek, gpt, None이면 환경변수에서 자동 선택)
        """
        load_dotenv()

        # 제공자 자동 선택
        if provider is None:
            provider = os.getenv("PRIMARY_ANALYZER", "gemini").lower()

        self.provider = provider

        # 재료 분류기 초기화
        self.material_classifier = NewsMaterialClassifier()

        # 제공자별 초기화
        if self.provider == "gemini":
            self._init_gemini()
        elif self.provider == "deepseek":
            self._init_deepseek()
        elif self.provider == "gpt":
            self._init_gpt()
        else:
            raise ValueError(f"지원하지 않는 제공자: {self.provider}")

    def _init_gemini(self):
        """Gemini 초기화"""
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API 키가 설정되지 않았습니다.")

        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")

    def _init_deepseek(self):
        """DeepSeek 초기화"""
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DeepSeek API 키가 설정되지 않았습니다.")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com",
            max_retries=0,  # 재시도 비활성화 (타임아웃 우선)
            timeout=3.0     # 3초 타임아웃
        )

    def _init_gpt(self):
        """GPT 초기화"""
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API 키가 설정되지 않았습니다.")

        self.client = OpenAI(
            api_key=self.api_key,
            max_retries=0,  # 재시도 비활성화 (타임아웃 우선)
            timeout=3.0     # 3초 타임아웃
        )

    def analyze_sentiment(self, news_list: List[Dict], stock_name: str, max_news: int = 5) -> Dict:
        """
        뉴스 감성 분석

        Args:
            news_list: 뉴스 리스트
            stock_name: 종목명
            max_news: 분석할 최대 뉴스 개수

        Returns:
            감성 분석 결과
        """
        if not news_list:
            return self._get_empty_result()

        # 최대 개수만큼만 분석
        news_to_analyze = news_list[:max_news]

        # 1단계: 재료 분석
        material_result = self.material_classifier.classify(news_to_analyze)

        # 뉴스 텍스트 결합
        news_text = self._combine_news_text(news_to_analyze)

        # 2단계: AI 감성 분석 실행
        analysis = self._call_ai_api(news_text, stock_name)

        if not analysis:
            print(f"    [dim]⚠️  {self.provider.upper()} AI 응답 없음 (타임아웃 or 실패) → 기본점수 50점[/dim]")
            return self._get_empty_result()

        # 3단계: 최종 점수 계산 (재료 점수 반영)
        final_score = self._calculate_final_score(analysis, len(news_list), material_result)
        print(f"    [dim]✓ {self.provider.upper()} 분석 성공: sentiment_score={analysis.get('sentiment_score', 0)}, final_score={final_score}[/dim]")

        return {
            **analysis,
            'final_score': final_score,
            'news_count': len(news_list),
            'analyzed_at': datetime.now().isoformat(),
            'provider': self.provider,
            'material_analysis': material_result  # 재료 분석 결과 추가
        }

    def _combine_news_text(self, news_list: List[Dict]) -> str:
        """
        뉴스 텍스트 결합

        Args:
            news_list: 뉴스 리스트

        Returns:
            결합된 뉴스 텍스트
        """
        combined = []
        for news in news_list:
            title = news.get('title', '')
            description = news.get('description', '')
            combined.append(f"- {title} : {description}")

        return "\n".join(combined)

    def _call_ai_api(self, news_text: str, stock_name: str) -> Optional[Dict]:
        """
        AI API 호출 (제공자별 분기)

        Args:
            news_text: 분석할 뉴스 텍스트
            stock_name: 종목명

        Returns:
            분석 결과 (실패 시 None)
        """
        try:
            if self.provider == "gemini":
                return self._call_gemini(news_text, stock_name)
            elif self.provider == "deepseek":
                return self._call_deepseek(news_text, stock_name)
            elif self.provider == "gpt":
                return self._call_gpt(news_text, stock_name)
        except Exception as e:
            print(f"✗ {self.provider.upper()} AI 분석 실패: {e}")
            return None

    def _call_gemini(self, news_text: str, stock_name: str) -> Optional[Dict]:
        """Gemini API 호출 (타임아웃 3초)"""
        import signal

        def timeout_handler(signum, frame):
            raise TimeoutError("Gemini API 타임아웃 (3초)")

        try:
            # 타임아웃 설정 (3초)
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(3)

            prompt = self._create_prompt(news_text, stock_name)
            response = self.model.generate_content(prompt)
            result_text = response.text.strip()

            # 타임아웃 해제
            signal.alarm(0)

            return self._parse_json_response(result_text)
        except TimeoutError as e:
            print(f"⚠️  Gemini API 타임아웃 (3초 초과)")
            signal.alarm(0)  # 타임아웃 해제
            return None

    def _call_deepseek(self, news_text: str, stock_name: str) -> Optional[Dict]:
        """DeepSeek API 호출 (타임아웃 3초, 재시도 없음)"""
        from openai import APITimeoutError, APIError
        prompt = self._create_prompt(news_text, stock_name)

        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "당신은 주식 뉴스 감성 분석 전문가입니다. 반드시 JSON 형식으로만 응답하세요."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1000
                # timeout과 max_retries는 클라이언트 초기화 시 설정됨
            )

            result_text = response.choices[0].message.content.strip()
            return self._parse_json_response(result_text)
        except APITimeoutError:
            # 타임아웃은 정상 동작 (응답 느린 경우 스킵)
            return None
        except APIError as e:
            print(f"⚠️  DeepSeek API 오류: {e}")
            return None
        except Exception as e:
            print(f"⚠️  DeepSeek 예상치 못한 오류 ({e.__class__.__name__}): {e}")
            return None

    def _call_gpt(self, news_text: str, stock_name: str) -> Optional[Dict]:
        """GPT API 호출 (타임아웃 3초, 재시도 없음)"""
        from openai import APITimeoutError, APIError
        prompt = self._create_prompt(news_text, stock_name)

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "당신은 주식 뉴스 감성 분석 전문가입니다. 반드시 JSON 형식으로만 응답하세요."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1000,
                response_format={"type": "json_object"}
                # timeout과 max_retries는 클라이언트 초기화 시 설정됨
            )

            result_text = response.choices[0].message.content.strip()
            return self._parse_json_response(result_text)
        except APITimeoutError:
            # 타임아웃은 정상 동작 (응답 느린 경우 스킵)
            return None
        except APIError as e:
            print(f"⚠️  GPT API 오류: {e}")
            return None
        except Exception as e:
            print(f"⚠️  GPT 예상치 못한 오류 ({e.__class__.__name__}): {e}")
            return None

    def _create_prompt(self, news_text: str, stock_name: str) -> str:
        """
        AI 프롬프트 생성

        Args:
            news_text: 뉴스 텍스트
            stock_name: 종목명

        Returns:
            프롬프트
        """
        return f"""
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

**중요:** 반드시 유효한 JSON 형식으로만 응답해주세요. 다른 텍스트는 포함하지 마세요.

예시:
{{
  "sentiment": "POSITIVE",
  "sentiment_score": 65,
  "confidence": 0.85,
  "impact": "MEDIUM",
  "positive_factors": ["실적 개선", "신제품 출시", "수주 증가"],
  "negative_factors": ["경쟁 심화"],
  "summary": "전반적으로 긍정적인 뉴스가 많으며...",
  "recommendation": "BUY"
}}
"""

    def _parse_json_response(self, response_text: str) -> Optional[Dict]:
        """
        JSON 응답 파싱

        Args:
            response_text: AI 응답 텍스트

        Returns:
            파싱된 JSON (실패 시 None)
        """
        try:
            # 코드 블록 제거
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            # JSON 파싱
            result = json.loads(response_text)
            return result

        except json.JSONDecodeError as e:
            print(f"✗ JSON 파싱 실패: {e}")
            print(f"응답 내용:\n{response_text[:500]}")
            return None

    def _calculate_final_score(self, analysis: Dict, news_count: int, material_result: Dict = None) -> float:
        """
        최종 점수 계산

        Args:
            analysis: AI 분석 결과
            news_count: 뉴스 개수
            material_result: 재료 분석 결과

        Returns:
            최종 점수 (0~100)
        """
        # 감성 점수 (-100 ~ +100 → 0 ~ 100 변환)
        sentiment_score = analysis.get('sentiment_score', 0)
        normalized_sentiment = (sentiment_score + 100) / 2  # 0~100

        # 영향도 점수
        impact = analysis.get('impact', 'MEDIUM')
        impact_score = {
            'HIGH': 100,
            'MEDIUM': 60,
            'LOW': 30
        }.get(impact, 60)

        # 뉴스 빈도 점수
        frequency_score = min(news_count * 10, 100)

        # 신뢰도
        confidence = analysis.get('confidence', 0.8)

        # 기본 점수 계산 (가중 평균)
        base_score = (
            normalized_sentiment * 0.4 +  # 40%
            impact_score * 0.3 +           # 30%
            frequency_score * 0.2 +        # 20%
            confidence * 100 * 0.1         # 10%
        )

        # 재료 분석이 있으면 점수 조정
        if material_result and material_result.get('has_material'):
            # 재료 점수 (0-100)
            material_score = material_result.get('material_score', 0)

            # 재료 배수 (1.0-1.3)
            multiplier = material_result.get('multiplier', 1.0)

            # 최종 점수 = (기본 점수 * 0.7 + 재료 점수 * 0.3) * 배수
            final_score = (base_score * 0.7 + material_score * 0.3) * multiplier

            return min(round(final_score, 2), 100)

        return round(base_score, 2)

    def _get_empty_result(self) -> Dict:
        """
        빈 결과 반환

        Returns:
            빈 분석 결과
        """
        return {
            'sentiment': 'NEUTRAL',
            'sentiment_score': 0,
            'confidence': 0.0,
            'impact': 'LOW',
            'positive_factors': [],
            'negative_factors': [],
            'summary': '분석할 뉴스가 없습니다.',
            'recommendation': 'HOLD',
            'final_score': 50.0,
            'news_count': 0,
            'analyzed_at': datetime.now().isoformat(),
            'provider': self.provider
        }


if __name__ == "__main__":
    # 간단한 테스트
    analyzer = SentimentAnalyzer()

    # 테스트 뉴스
    test_news = [
        {
            'title': '삼성전자, 2분기 영업이익 10조 예상',
            'description': '실적 개선 기대감으로 주가 상승세'
        },
        {
            'title': '신제품 출시로 점유율 확대',
            'description': '글로벌 시장에서 호평'
        }
    ]

    result = analyzer.analyze_sentiment(test_news, "삼성전자")

    print(f"제공자: {result['provider']}")
    print(f"감성: {result['sentiment']}")
    print(f"점수: {result['sentiment_score']}")
    print(f"최종 점수: {result['final_score']}")
    print(f"투자 의견: {result['recommendation']}")
