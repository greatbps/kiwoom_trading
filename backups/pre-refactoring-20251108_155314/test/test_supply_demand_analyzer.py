"""
수급 분석 엔진 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.supply_demand_analyzer import SupplyDemandAnalyzer
import json


def test_supply_demand_analyzer():
    """수급 분석 엔진 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 수급 분석기 생성
    analyzer = SupplyDemandAnalyzer()

    # 삼성전자 종목코드
    stock_code = "005930"
    stock_name = "삼성전자"

    print(f"=== 수급 분석 엔진 테스트 ===")
    print(f"종목: {stock_name} ({stock_code})")

    try:
        # 토큰 발급
        print("\n[1] 토큰 발급 중...")
        api.get_access_token()

        # 투자자별 매매 동향 조회
        print("\n[2] 투자자별 매매 동향 조회 중...")
        investor_result = api.get_investor_trend(
            stock_code=stock_code,
            amt_qty_tp="1",  # 금액
            trde_tp="0",     # 순매수
            unit_tp="1"      # 1원 단위 (원본 금액)
        )

        investor_data = None
        if investor_result.get('return_code') == 0 and 'stk_invsr_orgn' in investor_result:
            investor_data = investor_result['stk_invsr_orgn']
            print(f"  - 조회된 데이터: {len(investor_data)}일")
        else:
            print(f"  ✗ 조회 실패: {investor_result.get('return_msg')}")

        # 프로그램 매매 조회
        print("\n[3] 프로그램 매매 조회 중...")
        program_result = api.get_program_trading(
            mrkt_tp="P00101",  # 코스피
            stex_tp="1"        # KRX
        )

        program_data = None
        if program_result.get('return_code') == 0 and 'stk_prm_trde_prst' in program_result:
            program_data = program_result['stk_prm_trde_prst']
            print(f"  - 조회된 데이터: {len(program_data)}개 종목")
        else:
            print(f"  ✗ 조회 실패: {program_result.get('return_msg')}")

        # 수급 분석 실행
        print("\n[4] 수급 분석 실행 중...")
        analysis_result = analyzer.analyze(
            investor_data=investor_data,
            program_data=program_data,
            stock_code=stock_code
        )

        # 결과 출력
        print("\n" + "=" * 80)
        print(f"{'수급 분석 결과':^80}")
        print("=" * 80)

        # 총점 및 추천
        total_score = analysis_result['total_score']
        recommendation = analysis_result['recommendation']

        print(f"\n📊 종합 점수: {total_score:.2f}/100")
        print(f"💡 투자 추천: {recommendation}")

        if recommendation == "매수":
            print("   ✅ 수급상 매수 신호")
        elif recommendation == "관망":
            print("   ⏸️  수급상 관망 추천")
        else:
            print("   ⚠️  수급상 매도 신호")

        # 세부 분석 결과
        print(f"\n{'─' * 80}")
        print(f"🌍 1. 외국인 분석 (가중치 {analysis_result['weights']['foreign']}%)")
        print(f"{'─' * 80}")
        foreign = analysis_result['foreign']
        print(f"점수: {foreign['score']:.2f}/100")
        print(f"순매수 금액: {foreign['amount']:,.0f}원")
        for signal in foreign['signals']:
            print(f"  • {signal}")

        print(f"\n{'─' * 80}")
        print(f"🏢 2. 기관 분석 (가중치 {analysis_result['weights']['institution']}%)")
        print(f"{'─' * 80}")
        institution = analysis_result['institution']
        print(f"점수: {institution['score']:.2f}/100")
        print(f"순매수 금액: {institution['amount']:,.0f}원")
        for signal in institution['signals']:
            print(f"  • {signal}")

        print(f"\n{'─' * 80}")
        print(f"👥 3. 개인 분석 (가중치 {analysis_result['weights']['individual']}%)")
        print(f"{'─' * 80}")
        individual = analysis_result['individual']
        print(f"점수: {individual['score']:.2f}/100")
        print(f"순매수 금액: {individual['amount']:,.0f}원")
        for signal in individual['signals']:
            print(f"  • {signal}")

        print(f"\n{'─' * 80}")
        print(f"🤖 4. 프로그램 매매 (가중치 {analysis_result['weights']['program']}%)")
        print(f"{'─' * 80}")
        program = analysis_result['program']
        print(f"점수: {program['score']:.2f}/100")
        for signal in program['signals']:
            print(f"  • {signal}")

        print(f"\n{'─' * 80}")
        print(f"💪 5. 수급 강도 (가중치 {analysis_result['weights']['strength']}%)")
        print(f"{'─' * 80}")
        strength = analysis_result['strength']
        print(f"점수: {strength['score']:.2f}/100")
        for signal in strength['signals']:
            print(f"  • {signal}")

        # 전체 시그널
        print(f"\n{'─' * 80}")
        print(f"📋 종합 시그널")
        print(f"{'─' * 80}")
        for signal in analysis_result['all_signals']:
            print(f"  • {signal}")

        print("\n" + "=" * 80)

        # JSON 출력 (디버깅용)
        print("\n[5] 상세 분석 결과 (JSON):")
        print(json.dumps(analysis_result, indent=2, ensure_ascii=False))

        print("\n✓ 수급 분석 완료")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


def test_multiple_stocks():
    """여러 종목 수급 비교 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 수급 분석기 생성
    analyzer = SupplyDemandAnalyzer()

    # 테스트할 종목들
    stocks = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("035420", "NAVER")
    ]

    print(f"\n{'=' * 80}")
    print(f"{'여러 종목 수급 분석 비교':^80}")
    print(f"{'=' * 80}")

    try:
        # 토큰 발급
        api.get_access_token()

        results = []

        for stock_code, stock_name in stocks:
            print(f"\n분석 중: {stock_name} ({stock_code})")

            try:
                # 투자자별 매매 동향 조회
                investor_result = api.get_investor_trend(
                    stock_code=stock_code,
                    amt_qty_tp="1",
                    trde_tp="0",
                    unit_tp="1"
                )

                investor_data = None
                if investor_result.get('return_code') == 0 and 'stk_invsr_orgn' in investor_result:
                    investor_data = investor_result['stk_invsr_orgn']

                # 수급 분석 실행
                analysis_result = analyzer.analyze(
                    investor_data=investor_data,
                    stock_code=stock_code
                )

                results.append({
                    'code': stock_code,
                    'name': stock_name,
                    'score': analysis_result['total_score'],
                    'recommendation': analysis_result['recommendation'],
                    'foreign': analysis_result['foreign']['amount'],
                    'institution': analysis_result['institution']['amount']
                })

            except Exception as e:
                print(f"  오류: {e}")

        # 결과 비교
        print(f"\n{'=' * 80}")
        print(f"{'종목별 수급 점수 비교':^80}")
        print(f"{'=' * 80}")
        print(f"{'종목명':<15} {'종목코드':<10} {'수급점수':>12} {'추천':>10} {'외국인':>15} {'기관':>15}")
        print("-" * 80)

        # 점수 순으로 정렬
        results.sort(key=lambda x: x['score'], reverse=True)

        for r in results:
            foreign_str = f"{r['foreign']/100000000:+.1f}억" if abs(r['foreign']) >= 100000000 else f"{r['foreign']/10000:+.0f}만"
            inst_str = f"{r['institution']/100000000:+.1f}억" if abs(r['institution']) >= 100000000 else f"{r['institution']/10000:+.0f}만"

            print(f"{r['name']:<15} {r['code']:<10} {r['score']:>12.2f} {r['recommendation']:>10} {foreign_str:>15} {inst_str:>15}")

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
    test_supply_demand_analyzer()

    # 여러 종목 비교 테스트
    # test_multiple_stocks()
