"""
기존 프로그램에서 추출한 종목 리스트 분석
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.analysis_engine import AnalysisEngine
from strategies.trading_strategy import TradingStrategy
import time


# 종목 리스트 (기존 프로그램 1차 추출 종목)
STOCK_LIST = [
    ("317330", "덕산테코피아"),
    ("079900", "전진건설로봇"),
    ("092460", "한라IMS"),
    ("005850", "에스엘"),
    ("073570", "리튬포어스"),
    ("042600", "새로닉스"),
    ("183300", "코미코"),
    ("207940", "삼성바이오로직스"),
    ("348370", "엔켐"),
    ("039440", "에스티아이"),
    ("282330", "BGF리테일"),
    ("120110", "코오롱인더"),
    ("007660", "이수페타시스"),
    ("112040", "위메이드"),
    ("032940", "원익"),
    ("039830", "오로라"),
    ("044480", "빌리언스"),
    ("032500", "케이엠더블유"),
    ("030720", "동원수산"),
    ("000660", "SK하이닉스"),
    ("052400", "코나아이"),
    ("009620", "삼보산업"),
    ("089890", "코세스"),
    ("001820", "삼화콘덴서"),
    ("330860", "네패스아크"),
    ("025900", "동화기업"),
    ("440110", "파두"),
    ("020150", "롯데에너지머티리얼즈"),
    ("010140", "삼성중공업"),
    ("101670", "하이드로리튬"),
]


def analyze_stock_list():
    """종목 리스트 통합 분석"""
    print("=" * 100)
    print(f"{'종목 리스트 통합 분석 (상위 30개)':^100}")
    print("=" * 100)

    # API 및 엔진 초기화
    api = KiwoomAPI()
    analysis_engine = AnalysisEngine()
    trading_strategy = TradingStrategy()

    print("\n[1] 토큰 발급")
    api.get_access_token()

    results = []

    print(f"\n[2] 종목 분석 시작 ({len(STOCK_LIST)}개 종목)")
    print("─" * 100)

    for idx, (stock_code, stock_name) in enumerate(STOCK_LIST, 1):
        print(f"\n[{idx}/{len(STOCK_LIST)}] {stock_name} ({stock_code}) 분석 중...")

        try:
            # 데이터 수집
            chart_result = api.get_daily_chart(stock_code=stock_code)
            chart_data = chart_result.get('stk_dt_pole_chart_qry') if chart_result.get('return_code') == 0 else None

            if not chart_data:
                print(f"  ✗ 차트 데이터 조회 실패")
                continue

            investor_result = api.get_investor_trend(stock_code=stock_code, amt_qty_tp="1", trde_tp="0", unit_tp="1")
            investor_data = investor_result.get('stk_invsr_orgn') if investor_result.get('return_code') == 0 else None

            program_result = api.get_program_trading(mrkt_tp="P00101", stex_tp="1")
            program_data = program_result.get('stk_prm_trde_prst') if program_result.get('return_code') == 0 else None

            stock_info_result = api.get_stock_info(stock_code=stock_code)
            stock_info = stock_info_result if stock_info_result.get('return_code') == 0 else None

            # 현재가 추출
            current_price = None
            if stock_info and stock_info.get('cur_prc'):
                current_price = float(str(stock_info['cur_prc']).replace(',', '').replace('+', '').replace('-', ''))

            if not current_price and chart_data:
                latest_candle = chart_data[0]
                current_price = float(str(latest_candle.get('cur_prc', 0)).replace(',', '').replace('+', '').replace('-', ''))

            if not current_price:
                print(f"  ✗ 현재가 정보 없음")
                continue

            # 통합 분석
            analysis_result = analysis_engine.analyze(
                stock_code=stock_code,
                stock_name=stock_name,
                chart_data=chart_data,
                investor_data=investor_data,
                program_data=program_data,
                stock_info=stock_info
            )

            # 매매 계획
            trading_plan = trading_strategy.generate_trading_plan(
                stock_code=stock_code,
                stock_name=stock_name,
                current_price=current_price,
                account_balance=10000000,
                chart_data=chart_data,
                analysis_result=analysis_result
            )

            # 결과 저장
            results.append({
                'rank': idx,
                'stock_code': stock_code,
                'stock_name': stock_name,
                'current_price': current_price,
                'final_score': analysis_result['final_score'],
                'recommendation': analysis_result['recommendation'],
                'action': analysis_result['action'],
                'entry_signal': trading_plan['entry_signal']['signal'],
                'entry_confidence': trading_plan['entry_signal']['confidence'],
                'scores': analysis_result['scores_breakdown'],
                'quantity': trading_plan['position']['quantity'],
                'investment': trading_plan['position']['investment']
            })

            print(f"  ✓ 점수: {analysis_result['final_score']:.2f} | 추천: {analysis_result['recommendation']} | 신호: {trading_plan['entry_signal']['signal']}")

            # API 호출 제한 (0.2초 대기)
            time.sleep(0.2)

        except Exception as e:
            print(f"  ✗ 오류: {e}")
            continue

    # API 종료
    api.close()

    # 결과 정렬 (점수 순)
    results.sort(key=lambda x: x['final_score'], reverse=True)

    # 결과 출력
    print("\n\n")
    print("=" * 120)
    print(f"{'종목별 통합 분석 결과 (점수 순)':^120}")
    print("=" * 120)
    print(f"{'순위':<6} {'종목명':<14} {'코드':<8} {'현재가':>12} {'최종점수':>10} {'추천':>10} {'진입신호':>12} {'수량':>8} {'투자금':>14}")
    print("─" * 120)

    for idx, r in enumerate(results, 1):
        print(f"{idx:<6} {r['stock_name']:<14} {r['stock_code']:<8} {r['current_price']:>12,.0f}원 "
              f"{r['final_score']:>10.2f} {r['recommendation']:>10} {r['entry_signal']:>12} "
              f"{r['quantity']:>8,}주 {r['investment']:>14,}원")

    print("=" * 120)

    # 매수 추천 종목
    buy_candidates = [r for r in results if r['entry_signal'] in ['BUY', 'STRONG_BUY'] and r['quantity'] > 0]

    if buy_candidates:
        print(f"\n\n🎯 매수 추천 종목 ({len(buy_candidates)}개)")
        print("=" * 120)
        print(f"{'순위':<6} {'종목명':<14} {'코드':<8} {'현재가':>12} {'점수':>10} {'신호':>12} {'신뢰도':>10} {'수량':>8} {'투자금':>14}")
        print("─" * 120)

        for idx, r in enumerate(buy_candidates, 1):
            print(f"{idx:<6} {r['stock_name']:<14} {r['stock_code']:<8} {r['current_price']:>12,.0f}원 "
                  f"{r['final_score']:>10.2f} {r['entry_signal']:>12} {r['entry_confidence']:>10} "
                  f"{r['quantity']:>8,}주 {r['investment']:>14,}원")

        print("=" * 120)

        # 상위 3개 상세 정보
        print(f"\n\n📋 상위 3개 종목 상세 분석")
        for idx, r in enumerate(buy_candidates[:3], 1):
            print("\n" + "─" * 120)
            print(f"{idx}위. {r['stock_name']} ({r['stock_code']}) - 점수: {r['final_score']:.2f}")
            print("─" * 120)
            print(f"  현재가: {r['current_price']:,}원")
            print(f"  추천: {r['recommendation']} ({r['action']})")
            print(f"  진입 신호: {r['entry_signal']} ({r['entry_confidence']})")
            print(f"  매수 수량: {r['quantity']:,}주")
            print(f"  투자 금액: {r['investment']:,}원")
            print(f"\n  [엔진별 점수]")
            print(f"    뉴스: {r['scores']['news']:.2f}")
            print(f"    기술: {r['scores']['technical']:.2f}")
            print(f"    수급: {r['scores']['supply_demand']:.2f}")
            print(f"    기본: {r['scores']['fundamental']:.2f}")

    else:
        print(f"\n\n⚠️  현재 매수 추천 종목이 없습니다.")

    print("\n\n✓ 분석 완료")


if __name__ == "__main__":
    analyze_stock_list()
