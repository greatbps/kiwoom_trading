"""
매매 전략 엔진 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.analysis_engine import AnalysisEngine
from strategies.trading_strategy import TradingStrategy
import json


def test_trading_strategy():
    """매매 전략 엔진 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 분석 엔진 생성
    analysis_engine = AnalysisEngine()

    # 매매 전략 엔진 생성
    strategy = TradingStrategy(
        risk_per_trade=0.02,  # 거래당 2% 리스크
        max_position_size=0.3  # 최대 30% 투자
    )

    # 삼성전자 종목코드
    stock_code = "005930"
    stock_name = "삼성전자"

    # 가상 계좌 잔고
    account_balance = 10000000  # 1천만원

    print(f"{'='*80}")
    print(f"{'매매 전략 엔진 테스트':^80}")
    print(f"{'='*80}")
    print(f"종목: {stock_name} ({stock_code})")
    print(f"계좌 잔고: {account_balance:,}원")
    print(f"{'='*80}")

    try:
        # 토큰 발급
        print("\n[1] 토큰 발급 중...")
        api.get_access_token()

        # 데이터 수집
        print("\n[2] 데이터 수집 중...")

        # 차트 데이터
        chart_result = api.get_daily_chart(stock_code=stock_code)
        chart_data = None
        if chart_result.get('return_code') == 0 and 'stk_dt_pole_chart_qry' in chart_result:
            chart_data = chart_result['stk_dt_pole_chart_qry']
            print(f"   ✓ 차트 데이터 {len(chart_data)}일")

        # 투자자 데이터
        investor_result = api.get_investor_trend(stock_code=stock_code, amt_qty_tp="1", trde_tp="0", unit_tp="1")
        investor_data = investor_result.get('stk_invsr_orgn') if investor_result.get('return_code') == 0 else None

        # 프로그램 매매
        program_result = api.get_program_trading(mrkt_tp="P00101", stex_tp="1")
        program_data = program_result.get('stk_prm_trde_prst') if program_result.get('return_code') == 0 else None

        # 기본정보
        stock_info_result = api.get_stock_info(stock_code=stock_code)
        stock_info = stock_info_result if stock_info_result.get('return_code') == 0 else None

        # 현재가 추출
        current_price = None
        if stock_info and stock_info.get('cur_prc'):
            current_price = float(str(stock_info['cur_prc']).replace(',', '').replace('+', '').replace('-', ''))
            print(f"   ✓ 현재가: {current_price:,}원")

        if not current_price and chart_data:
            # 차트에서 최신 가격 추출
            latest_candle = chart_data[0]
            current_price = float(str(latest_candle.get('cur_prc', 0)).replace(',', '').replace('+', '').replace('-', ''))
            print(f"   ✓ 현재가 (차트): {current_price:,}원")

        if not current_price:
            print("   ✗ 현재가를 가져올 수 없습니다.")
            return

        # 통합 분석 실행
        print("\n[3] 통합 분석 실행 중...")
        analysis_result = analysis_engine.analyze(
            stock_code=stock_code,
            stock_name=stock_name,
            chart_data=chart_data,
            investor_data=investor_data,
            program_data=program_data,
            stock_info=stock_info
        )

        print(f"   ✓ 최종 점수: {analysis_result['final_score']:.2f}/100")
        print(f"   ✓ 추천: {analysis_result['recommendation']}")

        # 매매 계획 생성
        print("\n[4] 매매 계획 생성 중...")
        trading_plan = strategy.generate_trading_plan(
            stock_code=stock_code,
            stock_name=stock_name,
            current_price=current_price,
            account_balance=account_balance,
            chart_data=chart_data,
            analysis_result=analysis_result
        )

        # 결과 출력
        print("\n\n")
        print("=" * 80)
        print(f"{'매매 계획서':^80}")
        print("=" * 80)

        # 기본 정보
        print(f"\n📌 종목 정보")
        print(f"{'─'*80}")
        print(f"종목명        : {trading_plan['stock_name']}")
        print(f"종목코드      : {trading_plan['stock_code']}")
        print(f"현재가        : {trading_plan['current_price']:,}원")
        print(f"계좌 잔고     : {account_balance:,}원")

        # 진입 신호
        entry = trading_plan['entry_signal']
        print(f"\n🎯 진입 신호")
        print(f"{'─'*80}")
        print(f"신호          : {entry['signal']}")
        print(f"신뢰도        : {entry['confidence']}")
        print(f"진입 비율     : {entry['entry_ratio']*100:.0f}%")
        print(f"분할 전략     : {entry['split_strategy']['description']}")
        if entry['conditions']:
            print(f"\n조건:")
            for condition in entry['conditions']:
                print(f"  {condition}")

        # 포지션 사이징
        position = trading_plan['position']
        print(f"\n💰 포지션 사이징")
        print(f"{'─'*80}")
        print(f"매수 수량     : {position['quantity']:,}주")
        print(f"투자 금액     : {position['investment']:,}원")
        print(f"포지션 비율   : {position['position_ratio']:.2f}%")
        print(f"리스크 금액   : {position['risk_amount']:,}원 (계좌의 2%)")
        print(f"최대 손실     : {position['max_loss']:,}원")

        # 목표가
        targets = trading_plan['targets']
        print(f"\n🎯 목표가 (분할 매도)")
        print(f"{'─'*80}")
        print(f"1차 목표      : {targets['target1']:,}원 (+{targets['target1_gain']:.2f}%) - {targets['target1_method']}")
        print(f"2차 목표      : {targets['target2']:,}원 (+{targets['target2_gain']:.2f}%) - {targets['target2_method']}")
        print(f"3차 목표      : {targets['target3']:,}원 (+{targets['target3_gain']:.2f}%) - {targets['target3_method']}")
        print(f"ATR           : {targets['atr']:.2f}")

        # 손절가
        stop_loss = trading_plan['stop_loss']
        print(f"\n🛡️ 손절가")
        print(f"{'─'*80}")
        print(f"손절가        : {stop_loss['stop_loss']:,}원 ({stop_loss['loss_rate']:.2f}%)")
        print(f"방법          : {stop_loss['method']}")
        print(f"ATR           : {stop_loss['atr']:.2f}")

        # 리스크/리워드
        rr = trading_plan['risk_reward']
        print(f"\n⚖️ 리스크/리워드 비율 (1차 목표 기준)")
        print(f"{'─'*80}")
        print(f"리스크/리워드 : 1:{rr['risk_reward_ratio']:.2f}")
        print(f"예상 수익     : {rr['potential_profit']:,}원 (+{rr['potential_profit_rate']:.2f}%)")
        print(f"예상 손실     : {rr['potential_loss']:,}원 ({rr['potential_loss_rate']:.2f}%)")
        print(f"평가          : {'✅ 양호' if rr['is_acceptable'] else f'⚠️ 낮음 (최소 1:{rr['min_required']} 필요)'}")

        # 분할 매도 계획
        split_sell = trading_plan['split_sell_plan']
        if split_sell:
            print(f"\n📊 분할 매도 계획")
            print(f"{'─'*80}")
            for i, plan in enumerate(split_sell, 1):
                print(f"{i}. {plan['target']:,}원 - {plan['quantity']:,}주 ({plan['ratio']}%) - {plan['method']}")

        # 최종 추천
        print(f"\n💡 최종 추천")
        print(f"{'─'*80}")
        print(f"{trading_plan['recommendation']}")

        print("\n" + "=" * 80)

        # 매매 실행 시뮬레이션
        if entry['signal'] in ['BUY', 'STRONG_BUY'] and position['quantity'] > 0:
            print(f"\n{'='*80}")
            print(f"{'매매 실행 시뮬레이션':^80}")
            print(f"{'='*80}")

            total_investment = position['investment']
            total_quantity = position['quantity']

            print(f"\n[매수]")
            print(f"  종목: {stock_name} ({stock_code})")
            print(f"  수량: {total_quantity:,}주")
            print(f"  단가: {current_price:,}원")
            print(f"  금액: {total_investment:,}원")
            print(f"  손절: {stop_loss['stop_loss']:,}원 설정")

            print(f"\n[분할 매도 목표]")
            for i, plan in enumerate(split_sell, 1):
                profit = (plan['target'] - current_price) * plan['quantity']
                print(f"  {i}차 {plan['target']:,}원 도달시: {plan['quantity']:,}주 매도 (수익 {profit:,}원)")

            # 시나리오 분석
            print(f"\n[시나리오 분석]")

            # 1차 목표 달성시
            scenario1_profit = (targets['target1'] - current_price) * total_quantity
            scenario1_rate = (targets['target1'] - current_price) / current_price * 100
            print(f"  ✅ 1차 목표 달성: {scenario1_profit:,}원 (+{scenario1_rate:.2f}%)")

            # 3차 목표 달성시
            scenario3_profit = (targets['target3'] - current_price) * total_quantity
            scenario3_rate = (targets['target3'] - current_price) / current_price * 100
            print(f"  🚀 3차 목표 달성: {scenario3_profit:,}원 (+{scenario3_rate:.2f}%)")

            # 손절시
            stop_loss_amount = (stop_loss['stop_loss'] - current_price) * total_quantity
            stop_loss_rate = (stop_loss['stop_loss'] - current_price) / current_price * 100
            print(f"  ❌ 손절 발동시: {stop_loss_amount:,}원 ({stop_loss_rate:.2f}%)")

        print("\n" + "=" * 80)

        # JSON 출력 (옵션)
        print("\n[상세 매매 계획 JSON]")
        summary = {
            'stock': {'code': trading_plan['stock_code'], 'name': trading_plan['stock_name']},
            'current_price': trading_plan['current_price'],
            'entry_signal': trading_plan['entry_signal']['signal'],
            'position': {
                'quantity': trading_plan['position']['quantity'],
                'investment': trading_plan['position']['investment']
            },
            'targets': {
                'target1': trading_plan['targets']['target1'],
                'target2': trading_plan['targets']['target2'],
                'target3': trading_plan['targets']['target3']
            },
            'stop_loss': trading_plan['stop_loss']['stop_loss'],
            'risk_reward': trading_plan['risk_reward']['risk_reward_ratio']
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))

        print("\n✓ 매매 전략 테스트 완료")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


if __name__ == "__main__":
    test_trading_strategy()
