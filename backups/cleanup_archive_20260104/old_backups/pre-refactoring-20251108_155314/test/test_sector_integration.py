"""
업종 상대평가 통합 테스트
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzers.analysis_engine import AnalysisEngine
from kiwoom_api import KiwoomAPI

def main():
    print("=" * 80)
    print("업종 상대평가 통합 테스트")
    print("=" * 80)

    # API 및 엔진 초기화
    api = KiwoomAPI()
    api.get_access_token()

    engine = AnalysisEngine()

    # 테스트 종목 목록
    test_stocks = [
        "001440",  # 대한전선 (전기/전자)
        "005070",  # 코스모신소재
        "005930",  # 삼성전자 (전기/전자)
    ]

    for stock_code in test_stocks:
        print(f"\n{'='*80}")
        print(f"[{stock_code}] 종합 분석 테스트")
        print(f"{'='*80}")

        try:
            # 종목 기본 정보 조회
            stock_info = api.get_stock_info(stock_code)
            stock_name = stock_info.get('name', stock_code)

            print(f"종목명: {stock_name}")
            print(f"PER: {stock_info.get('per', 'N/A')}")
            print(f"PBR: {stock_info.get('pbr', 'N/A')}")
            print(f"ROE: {stock_info.get('roe', 'N/A')}")

            # 업종 조회
            sector_name = engine.get_sector_name(stock_code)
            print(f"업종: {sector_name if sector_name else '정보 없음'}")

            # 종합 분석 실행
            analysis_result = engine.analyze(stock_code, stock_name, stock_info=stock_info)

            print(f"\n[분석 결과]")
            print(f"  총점: {analysis_result['final_score']:.1f}/100")
            print(f"  추천: {analysis_result['recommendation']}")
            print(f"  뉴스: {analysis_result['scores_breakdown']['news']:.1f}")
            print(f"  기술: {analysis_result['scores_breakdown']['technical']:.1f}")
            print(f"  수급: {analysis_result['scores_breakdown']['supply_demand']:.1f}")
            print(f"  기본: {analysis_result['scores_breakdown']['fundamental']:.1f}")

            # 기본 분석 상세 정보
            fundamental_result = analysis_result.get('fundamental', {})
            if fundamental_result:
                print(f"\n[기본 분석 상세]")
                print(f"  점수: {fundamental_result.get('score', 0)}/50")
                print(f"  밸류에이션: {fundamental_result.get('valuation_score', 0)}/30")
                print(f"  수익성: {fundamental_result.get('profitability_score', 0)}/20")

                signals = fundamental_result.get('signals', [])
                if signals:
                    print(f"\n  시그널:")
                    for signal in signals[:5]:  # 처음 5개만
                        print(f"    - {signal}")

        except Exception as e:
            print(f"✗ 분석 실패: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*80}")
    print("테스트 완료")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
