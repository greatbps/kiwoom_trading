"""
뉴스 분석 모듈 통합 테스트
analyzers 패키지의 NewsAnalyzer + SentimentAnalyzer 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzers import NewsAnalyzer, SentimentAnalyzer


def test_news_analysis_module(stock_code: str = "005930", stock_name: str = "삼성전자"):
    """통합 뉴스 분석 테스트"""

    print("=" * 70)
    print(f"📰 {stock_name} ({stock_code}) 뉴스 분석 모듈 테스트")
    print("=" * 70)

    try:
        # 1단계: 뉴스 수집
        print(f"\n[1단계] 뉴스 수집")
        print("-" * 70)

        news_analyzer = NewsAnalyzer()
        news_result = news_analyzer.analyze_news(stock_code, stock_name, display=5)

        print(f"✓ 뉴스 수집 완료: {news_result['news_count']}개")
        print(f"  빈도 점수: {news_result['frequency_score']}/100")

        if news_result['news_count'] == 0:
            print("✗ 수집된 뉴스가 없습니다.")
            return

        # 수집된 뉴스 출력
        print(f"\n📋 수집된 뉴스:")
        for i, news in enumerate(news_result['news_list'][:3], 1):
            print(f"{i}. {news['title']}")

        # 2단계: 감성 분석
        print(f"\n[2단계] AI 감성 분석")
        print("-" * 70)

        sentiment_analyzer = SentimentAnalyzer()
        sentiment_result = sentiment_analyzer.analyze_sentiment(
            news_result['news_list'],
            stock_name,
            max_news=5
        )

        print(f"✓ 감성 분석 완료")

        # 3단계: 결과 출력
        print(f"\n[3단계] 분석 결과")
        print("=" * 70)

        print(f"\n📊 감성 분석 결과:")
        print(f"  • 감성: {sentiment_result['sentiment']}")
        print(f"  • 감성 점수: {sentiment_result['sentiment_score']}/100 "
              f"({sentiment_result['sentiment_score']+100}/200)")
        print(f"  • 신뢰도: {sentiment_result['confidence']:.2%}")
        print(f"  • 영향도: {sentiment_result['impact']}")
        print(f"  • 뉴스 개수: {sentiment_result['news_count']}개")

        positive = sentiment_result.get('positive_factors', [])
        if positive:
            print(f"\n✅ 긍정적 요인:")
            for factor in positive:
                print(f"  • {factor}")

        negative = sentiment_result.get('negative_factors', [])
        if negative:
            print(f"\n❌ 부정적 요인:")
            for factor in negative:
                print(f"  • {factor}")

        print(f"\n📝 요약:")
        print(f"  {sentiment_result.get('summary', 'N/A')}")

        print(f"\n💡 투자 의견: {sentiment_result.get('recommendation', 'N/A')}")

        print(f"\n🎯 최종 점수: {sentiment_result['final_score']:.2f}/100")

        # 점수 구성 상세
        print(f"\n📈 점수 구성 상세:")
        sentiment_score_normalized = (sentiment_result['sentiment_score'] + 100) / 2
        print(f"  • 감성 점수 (40%): {sentiment_score_normalized:.2f}")
        impact_scores = {'HIGH': 100, 'MEDIUM': 60, 'LOW': 30}
        impact_score = impact_scores.get(sentiment_result['impact'], 60)
        print(f"  • 영향도 점수 (30%): {impact_score}")
        print(f"  • 빈도 점수 (20%): {news_result['frequency_score']}")
        print(f"  • 신뢰도 점수 (10%): {sentiment_result['confidence'] * 100:.2f}")

        print("\n" + "=" * 70)
        print("✓ 테스트 완료")
        print("=" * 70)

        return {
            'news': news_result,
            'sentiment': sentiment_result
        }

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # 기본 테스트: 삼성전자
    result = test_news_analysis_module("005930", "삼성전자")

    # 다른 종목 테스트하려면:
    # test_news_analysis_module("000660", "SK하이닉스")
    # test_news_analysis_module("035420", "NAVER")
