"""
기술적 분석 엔진 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.technical_analyzer import TechnicalAnalyzer
import json


def test_technical_analyzer():
    """기술적 분석 엔진 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 기술적 분석기 생성
    analyzer = TechnicalAnalyzer()

    # 삼성전자 종목코드
    stock_code = "005930"
    stock_name = "삼성전자"

    print(f"=== 기술적 분석 엔진 테스트 ===")
    print(f"종목: {stock_name} ({stock_code})")

    try:
        # 토큰 발급
        print("\n[1] 토큰 발급 중...")
        api.get_access_token()

        # 일봉 데이터 조회 (최근 120일)
        print("\n[2] 일봉 데이터 조회 중...")
        result = api.get_daily_chart(stock_code=stock_code)

        # 응답 확인
        if result.get('return_code') != 0:
            print(f"  ✗ API 오류: {result.get('return_msg')}")
            return

        # 차트 데이터 확인 (키움 API는 최상위에 바로 데이터가 있음)
        if 'stk_dt_pole_chart_qry' in result:
            chart_data = result['stk_dt_pole_chart_qry']
            print(f"  - 조회된 데이터: {len(chart_data)}일")

            # 기술적 분석 실행
            print("\n[3] 기술적 분석 실행 중...")
            analysis_result = analyzer.analyze(chart_data)

            # 결과 출력
            print("\n" + "=" * 80)
            print(f"{'기술적 분석 결과':^80}")
            print("=" * 80)

            # 총점 및 추천
            total_score = analysis_result['total_score']
            recommendation = analysis_result['recommendation']

            print(f"\n📊 종합 점수: {total_score:.2f}/100")
            print(f"💡 투자 추천: {recommendation}")

            if recommendation == "매수":
                print("   ✅ 기술적으로 매수 신호")
            elif recommendation == "관망":
                print("   ⏸️  기술적으로 관망 추천")
            else:
                print("   ⚠️  기술적으로 매도 신호")

            # 세부 분석 결과
            print(f"\n{'─' * 80}")
            print(f"📈 1. 추세 분석 (가중치 {analysis_result['weights']['trend']}%)")
            print(f"{'─' * 80}")
            trend = analysis_result['trend']
            print(f"점수: {trend['score']:.2f}/100")
            for signal in trend['signals']:
                print(f"  • {signal}")

            print(f"\n{'─' * 80}")
            print(f"⚡ 2. 모멘텀 분석 (가중치 {analysis_result['weights']['momentum']}%)")
            print(f"{'─' * 80}")
            momentum = analysis_result['momentum']
            print(f"점수: {momentum['score']:.2f}/100")
            if momentum['rsi_value']:
                print(f"  RSI: {momentum['rsi_value']:.2f}")
            for signal in momentum['signals']:
                print(f"  • {signal}")

            print(f"\n{'─' * 80}")
            print(f"📉 3. 변동성 분석 (가중치 {analysis_result['weights']['volatility']}%)")
            print(f"{'─' * 80}")
            volatility = analysis_result['volatility']
            print(f"점수: {volatility['score']:.2f}/100")
            for signal in volatility['signals']:
                print(f"  • {signal}")

            print(f"\n{'─' * 80}")
            print(f"📊 4. 거래량 분석 (가중치 {analysis_result['weights']['volume']}%)")
            print(f"{'─' * 80}")
            volume = analysis_result['volume']
            print(f"점수: {volume['score']:.2f}/100")
            for signal in volume['signals']:
                print(f"  • {signal}")

            print(f"\n{'─' * 80}")
            print(f"🔍 5. 패턴 분석 (가중치 {analysis_result['weights']['pattern']}%)")
            print(f"{'─' * 80}")
            pattern = analysis_result['pattern']
            print(f"점수: {pattern['score']:.2f}/100")
            for signal in pattern['signals']:
                print(f"  • {signal}")

            print("\n" + "=" * 80)

            # JSON 출력 (디버깅용)
            print("\n[4] 상세 분석 결과 (JSON):")
            print(json.dumps(analysis_result, indent=2, ensure_ascii=False))

            print("\n✓ 기술적 분석 완료")

        else:
            print("차트 데이터를 가져올 수 없습니다.")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


def test_multiple_stocks():
    """여러 종목 비교 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 기술적 분석기 생성
    analyzer = TechnicalAnalyzer()

    # 테스트할 종목들
    stocks = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("035420", "NAVER")
    ]

    print(f"\n{'=' * 80}")
    print(f"{'여러 종목 기술적 분석 비교':^80}")
    print(f"{'=' * 80}")

    try:
        # 토큰 발급
        api.get_access_token()

        results = []

        for stock_code, stock_name in stocks:
            print(f"\n분석 중: {stock_name} ({stock_code})")

            try:
                # 일봉 데이터 조회
                result = api.get_daily_chart(stock_code=stock_code)

                if result.get('return_code') == 0 and 'stk_dt_pole_chart_qry' in result:
                    chart_data = result['stk_dt_pole_chart_qry']

                    # 기술적 분석 실행
                    analysis_result = analyzer.analyze(chart_data)

                    results.append({
                        'code': stock_code,
                        'name': stock_name,
                        'score': analysis_result['total_score'],
                        'recommendation': analysis_result['recommendation']
                    })

            except Exception as e:
                print(f"  오류: {e}")

        # 결과 비교
        print(f"\n{'=' * 80}")
        print(f"{'종목별 기술적 점수 비교':^80}")
        print(f"{'=' * 80}")
        print(f"{'종목명':<15} {'종목코드':<10} {'기술적 점수':>12} {'추천':>10}")
        print("-" * 80)

        # 점수 순으로 정렬
        results.sort(key=lambda x: x['score'], reverse=True)

        for r in results:
            print(f"{r['name']:<15} {r['code']:<10} {r['score']:>12.2f} {r['recommendation']:>10}")

        print("=" * 80)

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


if __name__ == "__main__":
    # 단일 종목 테스트
    test_technical_analyzer()

    # 여러 종목 비교 테스트
    # test_multiple_stocks()
