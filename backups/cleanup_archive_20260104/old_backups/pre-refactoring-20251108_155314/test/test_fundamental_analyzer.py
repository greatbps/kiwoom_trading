"""
기본(펀더멘털) 분석 엔진 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.fundamental_analyzer import FundamentalAnalyzer
import json


def test_fundamental_analyzer():
    """기본 분석 엔진 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 기본 분석기 생성
    analyzer = FundamentalAnalyzer()

    # 삼성전자 종목코드
    stock_code = "005930"
    stock_name = "삼성전자"

    print(f"=== 기본(펀더멘털) 분석 엔진 테스트 ===")
    print(f"종목: {stock_name} ({stock_code})")

    try:
        # 토큰 발급
        print("\n[1] 토큰 발급 중...")
        api.get_access_token()

        # 주식 기본정보 조회
        print("\n[2] 주식 기본정보 조회 중...")
        result = api.get_stock_info(stock_code=stock_code)

        # 응답 확인
        if result.get('return_code') != 0:
            print(f"  ✗ API 오류: {result.get('return_msg')}")
            return

        stock_info = result
        print(f"  ✓ 기본정보 조회 완료")

        # 기본 분석 실행
        print("\n[3] 기본 분석 실행 중...")
        analysis_result = analyzer.analyze(stock_info)

        # 결과 출력
        print("\n" + "=" * 80)
        print(f"{'기본(펀더멘털) 분석 결과':^80}")
        print("=" * 80)

        # 총점 및 추천
        total_score = analysis_result['total_score']
        recommendation = analysis_result['recommendation']

        print(f"\n📊 종합 점수: {total_score:.2f}/100")
        print(f"💡 투자 추천: {recommendation}")

        if recommendation == "매수":
            print("   ✅ 펀더멘털상 매수 신호")
        elif recommendation == "관망":
            print("   ⏸️  펀더멘털상 관망 추천")
        else:
            print("   ⚠️  펀더멘털상 매도 신호")

        # 종합 시그널
        if analysis_result['all_signals']:
            print(f"\n{'─' * 80}")
            print(f"📋 종합 판단")
            print(f"{'─' * 80}")
            for signal in analysis_result['all_signals']:
                print(f"  • {signal}")

        # 세부 분석 결과
        print(f"\n{'─' * 80}")
        print(f"💰 1. 밸류에이션 분석 (가중치 {analysis_result['weights']['valuation']}%)")
        print(f"{'─' * 80}")
        valuation = analysis_result['valuation']
        print(f"점수: {valuation['score']:.2f}/100")
        for signal in valuation['signals']:
            print(f"  • {signal}")

        print(f"\n{'─' * 80}")
        print(f"📈 2. 수익성 분석 (가중치 {analysis_result['weights']['profitability']}%)")
        print(f"{'─' * 80}")
        profitability = analysis_result['profitability']
        print(f"점수: {profitability['score']:.2f}/100")
        for signal in profitability['signals']:
            print(f"  • {signal}")

        print(f"\n{'─' * 80}")
        print(f"🌍 3. 외국인 보유 분석 (가중치 {analysis_result['weights']['foreign']}%)")
        print(f"{'─' * 80}")
        foreign = analysis_result['foreign']
        print(f"점수: {foreign['score']:.2f}/100")
        for signal in foreign['signals']:
            print(f"  • {signal}")

        print(f"\n{'─' * 80}")
        print(f"💎 4. 시가총액 분석 (가중치 {analysis_result['weights']['market_cap']}%)")
        print(f"{'─' * 80}")
        market_cap = analysis_result['market_cap']
        print(f"점수: {market_cap['score']:.2f}/100")
        for signal in market_cap['signals']:
            print(f"  • {signal}")

        print("\n" + "=" * 80)

        # 기본 정보 요약
        print(f"\n{'─' * 80}")
        print(f"📊 주요 지표 요약")
        print(f"{'─' * 80}")
        print(f"종목명        : {stock_info.get('stk_nm', 'N/A')}")
        print(f"현재가        : {stock_info.get('cur_prc', 'N/A')}원")
        print(f"PER           : {valuation.get('per', 'N/A')}")
        print(f"PBR           : {valuation.get('pbr', 'N/A')}")
        print(f"ROE           : {profitability.get('roe', 'N/A')}%")
        print(f"EPS           : {profitability.get('eps', 'N/A'):,.0f}원" if profitability.get('eps') else "EPS           : N/A")
        print(f"BPS           : {profitability.get('bps', 'N/A'):,.0f}원" if profitability.get('bps') else "BPS           : N/A")
        print(f"시가총액      : {market_cap.get('market_cap', 'N/A'):,.0f}억" if market_cap.get('market_cap') else "시가총액      : N/A")
        print(f"외국인보유    : {foreign.get('foreign_ratio', 'N/A')}%" if foreign.get('foreign_ratio') else "외국인보유    : N/A")
        print("=" * 80)

        # JSON 출력 (디버깅용)
        print("\n[4] 상세 분석 결과 (JSON):")
        print(json.dumps(analysis_result, indent=2, ensure_ascii=False))

        print("\n✓ 기본 분석 완료")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


def test_multiple_stocks():
    """여러 종목 기본 분석 비교 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 기본 분석기 생성
    analyzer = FundamentalAnalyzer()

    # 테스트할 종목들
    stocks = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("035420", "NAVER"),
        ("005380", "현대차"),
        ("051910", "LG화학")
    ]

    print(f"\n{'=' * 100}")
    print(f"{'여러 종목 기본 분석 비교':^100}")
    print(f"{'=' * 100}")

    try:
        # 토큰 발급
        api.get_access_token()

        results = []

        for stock_code, stock_name in stocks:
            print(f"\n분석 중: {stock_name} ({stock_code})")

            try:
                # 주식 기본정보 조회
                result = api.get_stock_info(stock_code=stock_code)

                if result.get('return_code') == 0:
                    # 기본 분석 실행
                    analysis_result = analyzer.analyze(result)

                    results.append({
                        'code': stock_code,
                        'name': stock_name,
                        'score': analysis_result['total_score'],
                        'recommendation': analysis_result['recommendation'],
                        'per': analysis_result['valuation'].get('per', 0),
                        'pbr': analysis_result['valuation'].get('pbr', 0),
                        'roe': analysis_result['profitability'].get('roe', 0),
                        'market_cap': analysis_result['market_cap'].get('market_cap', 0),
                        'foreign': analysis_result['foreign'].get('foreign_ratio', 0)
                    })

            except Exception as e:
                print(f"  오류: {e}")

        # 결과 비교
        print(f"\n{'=' * 130}")
        print(f"{'종목별 기본 분석 점수 비교':^130}")
        print(f"{'=' * 130}")
        print(f"{'종목명':<12} {'코드':<8} {'점수':>8} {'추천':>8} {'PER':>8} {'PBR':>8} {'ROE':>8} {'시총(억)':>12} {'외국인':>10}")
        print("-" * 130)

        # 점수 순으로 정렬
        results.sort(key=lambda x: x['score'], reverse=True)

        for r in results:
            per_str = f"{r['per']:.1f}" if r['per'] else "N/A"
            pbr_str = f"{r['pbr']:.2f}" if r['pbr'] else "N/A"
            roe_str = f"{r['roe']:.1f}%" if r['roe'] else "N/A"
            cap_str = f"{r['market_cap']:,.0f}" if r['market_cap'] else "N/A"
            for_str = f"{r['foreign']:.1f}%" if r['foreign'] else "N/A"

            print(f"{r['name']:<12} {r['code']:<8} {r['score']:>8.2f} {r['recommendation']:>8} "
                  f"{per_str:>8} {pbr_str:>8} {roe_str:>8} {cap_str:>12} {for_str:>10}")

        print("=" * 130)

        # 가장 좋은 종목 추천
        if results:
            best = results[0]
            print(f"\n🏆 펀더멘털 최우량 종목: {best['name']} (점수: {best['score']:.2f}/100)")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


if __name__ == "__main__":
    # 단일 종목 테스트
    test_fundamental_analyzer()

    # 여러 종목 비교 테스트
    # test_multiple_stocks()
