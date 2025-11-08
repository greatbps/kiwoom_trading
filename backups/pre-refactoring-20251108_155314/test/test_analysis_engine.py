"""
통합 분석 엔진 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.analysis_engine import AnalysisEngine
import json


def test_analysis_engine():
    """통합 분석 엔진 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 통합 분석 엔진 생성
    engine = AnalysisEngine()

    # 삼성전자 종목코드
    stock_code = "005930"
    stock_name = "삼성전자"

    print(f"{'='*80}")
    print(f"{'통합 분석 엔진 테스트':^80}")
    print(f"{'='*80}")
    print(f"종목: {stock_name} ({stock_code})")
    print(f"{'='*80}")

    try:
        # 토큰 발급
        print("\n[준비] 토큰 발급 중...")
        api.get_access_token()

        # 데이터 수집
        print("\n[데이터 수집 시작]")
        print("-" * 80)

        # 1. 일봉 차트 데이터
        print("1. 일봉 차트 조회 중...")
        chart_result = api.get_daily_chart(stock_code=stock_code)
        chart_data = None
        if chart_result.get('return_code') == 0 and 'stk_dt_pole_chart_qry' in chart_result:
            chart_data = chart_result['stk_dt_pole_chart_qry']
            print(f"   ✓ {len(chart_data)}일 데이터 조회 완료")
        else:
            print(f"   ✗ 조회 실패")

        # 2. 투자자별 매매 동향
        print("2. 투자자별 매매 동향 조회 중...")
        investor_result = api.get_investor_trend(
            stock_code=stock_code,
            amt_qty_tp="1",
            trde_tp="0",
            unit_tp="1"
        )
        investor_data = None
        if investor_result.get('return_code') == 0 and 'stk_invsr_orgn' in investor_result:
            investor_data = investor_result['stk_invsr_orgn']
            print(f"   ✓ {len(investor_data)}일 데이터 조회 완료")
        else:
            print(f"   ✗ 조회 실패")

        # 3. 프로그램 매매
        print("3. 프로그램 매매 조회 중...")
        program_result = api.get_program_trading(mrkt_tp="P00101", stex_tp="1")
        program_data = None
        if program_result.get('return_code') == 0 and 'stk_prm_trde_prst' in program_result:
            program_data = program_result['stk_prm_trde_prst']
            print(f"   ✓ {len(program_data)}개 종목 데이터 조회 완료")
        else:
            print(f"   ✗ 조회 실패")

        # 4. 주식 기본정보
        print("4. 주식 기본정보 조회 중...")
        stock_info_result = api.get_stock_info(stock_code=stock_code)
        stock_info = None
        if stock_info_result.get('return_code') == 0:
            stock_info = stock_info_result
            print(f"   ✓ 기본정보 조회 완료")
        else:
            print(f"   ✗ 조회 실패")

        print("-" * 80)
        print("[데이터 수집 완료]\n")

        # 통합 분석 실행
        result = engine.analyze(
            stock_code=stock_code,
            stock_name=stock_name,
            chart_data=chart_data,
            investor_data=investor_data,
            program_data=program_data,
            stock_info=stock_info
        )

        # 결과 출력
        print("\n\n")
        print("=" * 80)
        print(f"{'통합 분석 결과':^80}")
        print("=" * 80)

        # 최종 점수 및 추천
        print(f"\n🎯 최종 점수: {result['final_score']:.2f}/100")
        print(f"💡 투자 추천: {result['recommendation']}")
        print(f"📊 액션: {result['action']}")
        print(f"🌍 시장 상황: {result['market_regime']}")

        # 추천 근거
        if result['reasons']:
            print(f"\n{'─'*80}")
            print("📋 추천 근거")
            print(f"{'─'*80}")
            for reason in result['reasons']:
                print(f"  {reason}")

        # 특별 시그널
        if result['special_signals']:
            print(f"\n{'─'*80}")
            print("⭐ 특별 시그널")
            print(f"{'─'*80}")
            for signal in result['special_signals']:
                print(f"  {signal}")

        # 각 분석 엔진 점수
        print(f"\n{'─'*80}")
        print("📊 분석 엔진별 점수")
        print(f"{'─'*80}")
        scores = result['scores_breakdown']
        weights = result['weights']

        print(f"1. 뉴스 분석      : {scores['news']:6.2f}/100 (가중치 {weights['news']:2d}%)")
        print(f"2. 기술적 분석    : {scores['technical']:6.2f}/100 (가중치 {weights['technical']:2d}%)")
        print(f"3. 수급 분석      : {scores['supply_demand']:6.2f}/100 (가중치 {weights['supply_demand']:2d}%)")
        print(f"4. 기본 분석      : {scores['fundamental']:6.2f}/100 (가중치 {weights['fundamental']:2d}%)")

        # 가중 평균 계산
        weighted_avg = (
            scores['news'] * weights['news'] +
            scores['technical'] * weights['technical'] +
            scores['supply_demand'] * weights['supply_demand'] +
            scores['fundamental'] * weights['fundamental']
        ) / 100

        print(f"\n가중 평균        : {weighted_avg:6.2f}/100")
        print(f"시장 보정 후     : {result['final_score']:6.2f}/100")

        # 각 분석 엔진 상세 결과
        print(f"\n{'='*80}")
        print("상세 분석 결과")
        print(f"{'='*80}")

        # 1. 뉴스 분석
        print(f"\n{'─'*80}")
        print(f"📰 1. 뉴스 분석 ({scores['news']:.2f}점)")
        print(f"{'─'*80}")
        for signal in result['news']['signals']:
            print(f"  • {signal}")

        # 2. 기술적 분석
        print(f"\n{'─'*80}")
        print(f"📈 2. 기술적 분석 ({scores['technical']:.2f}점)")
        print(f"{'─'*80}")
        for signal in result['technical']['signals']:
            print(f"  • {signal}")

        # 3. 수급 분석
        print(f"\n{'─'*80}")
        print(f"💰 3. 수급 분석 ({scores['supply_demand']:.2f}점)")
        print(f"{'─'*80}")
        for signal in result['supply_demand']['signals']:
            print(f"  • {signal}")

        # 4. 기본 분석
        print(f"\n{'─'*80}")
        print(f"🏢 4. 기본 분석 ({scores['fundamental']:.2f}점)")
        print(f"{'─'*80}")
        for signal in result['fundamental']['signals']:
            print(f"  • {signal}")

        print("\n" + "=" * 80)

        # JSON 출력 (옵션)
        print("\n[전체 결과 JSON]")
        # 너무 길어서 주요 부분만 출력
        summary = {
            'stock': {'code': result['stock_code'], 'name': result['stock_name']},
            'final_score': result['final_score'],
            'recommendation': result['recommendation'],
            'action': result['action'],
            'market_regime': result['market_regime'],
            'scores': result['scores_breakdown'],
            'weights': result['weights']
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))

        print("\n✓ 통합 분석 완료")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


def test_multiple_stocks():
    """여러 종목 통합 분석 비교"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 통합 분석 엔진 생성
    engine = AnalysisEngine()

    # 테스트할 종목들
    stocks = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("035420", "NAVER")
    ]

    print(f"\n{'='*100}")
    print(f"{'여러 종목 통합 분석 비교':^100}")
    print(f"{'='*100}")

    try:
        # 토큰 발급
        api.get_access_token()

        results = []

        for stock_code, stock_name in stocks:
            print(f"\n{'─'*100}")
            print(f"분석 중: {stock_name} ({stock_code})")
            print(f"{'─'*100}")

            try:
                # 데이터 수집
                chart_result = api.get_daily_chart(stock_code=stock_code)
                chart_data = chart_result.get('stk_dt_pole_chart_qry') if chart_result.get('return_code') == 0 else None

                investor_result = api.get_investor_trend(stock_code=stock_code, amt_qty_tp="1", trde_tp="0", unit_tp="1")
                investor_data = investor_result.get('stk_invsr_orgn') if investor_result.get('return_code') == 0 else None

                program_result = api.get_program_trading(mrkt_tp="P00101", stex_tp="1")
                program_data = program_result.get('stk_prm_trde_prst') if program_result.get('return_code') == 0 else None

                stock_info_result = api.get_stock_info(stock_code=stock_code)
                stock_info = stock_info_result if stock_info_result.get('return_code') == 0 else None

                # 통합 분석
                result = engine.analyze(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    chart_data=chart_data,
                    investor_data=investor_data,
                    program_data=program_data,
                    stock_info=stock_info
                )

                results.append(result)

            except Exception as e:
                print(f"  오류: {e}")

        # 결과 비교
        print(f"\n{'='*130}")
        print(f"{'종목별 통합 분석 점수 비교':^130}")
        print(f"{'='*130}")
        print(f"{'종목명':<12} {'코드':<8} {'최종점수':>10} {'추천':>12} {'뉴스':>8} {'기술':>8} {'수급':>8} {'기본':>8} {'시장':>10}")
        print("-" * 130)

        # 점수 순으로 정렬
        results.sort(key=lambda x: x['final_score'], reverse=True)

        for r in results:
            scores = r['scores_breakdown']
            print(f"{r['stock_name']:<12} {r['stock_code']:<8} {r['final_score']:>10.2f} {r['recommendation']:>12} "
                  f"{scores['news']:>8.2f} {scores['technical']:>8.2f} {scores['supply_demand']:>8.2f} "
                  f"{scores['fundamental']:>8.2f} {r['market_regime']:>10}")

        print("=" * 130)

        # 최고점 종목
        if results:
            best = results[0]
            print(f"\n🏆 최고 점수 종목: {best['stock_name']} (최종점수: {best['final_score']:.2f}/100)")
            print(f"   추천: {best['recommendation']} ({best['action']})")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


if __name__ == "__main__":
    # 단일 종목 테스트
    # test_analysis_engine()

    # 여러 종목 비교 테스트
    test_multiple_stocks()
