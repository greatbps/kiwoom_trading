"""
Phase 3 개선 사항 테스트

1. EMA + Volume Breakdown 감지
2. 실제 Kiwoom 주문 API 통합
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.technical_analyzer import TechnicalAnalyzer


def test_ema_breakdown_detection():
    """EMA Breakdown 감지 테스트"""
    print("=" * 80)
    print(f"{'EMA + Volume Breakdown 감지 테스트':^80}")
    print("=" * 80)

    # API 및 분석기 생성
    api = KiwoomAPI()
    analyzer = TechnicalAnalyzer()

    # 토큰 발급
    print("\n[1] 토큰 발급")
    api.get_access_token()

    # 테스트할 종목들
    test_stocks = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("035420", "NAVER"),
    ]

    print("\n[2] EMA Breakdown 감지 테스트")
    print("─" * 80)

    for stock_code, stock_name in test_stocks:
        print(f"\n📊 {stock_name} ({stock_code})")
        print("─" * 80)

        try:
            # 차트 데이터 조회
            chart_result = api.get_daily_chart(stock_code=stock_code)
            if chart_result.get('return_code') != 0 or 'stk_dt_pole_chart_qry' not in chart_result:
                print(f"  ✗ 차트 데이터 조회 실패")
                continue

            chart_data = chart_result['stk_dt_pole_chart_qry']
            print(f"  ✓ 차트 데이터: {len(chart_data)}일")

            # DataFrame 준비
            df = analyzer.prepare_dataframe(chart_data)

            # EMA Breakdown 감지 (20일 EMA)
            breakdown = analyzer.detect_ema_breakdown(df, ema_period=20)

            print(f"\n  [EMA Breakdown 분석 결과]")
            print(f"    Breakdown 감지: {'✅ YES' if breakdown['breakdown_detected'] else '❌ NO'}")
            print(f"    신뢰도: {breakdown['confidence']}")
            print(f"    사유: {breakdown['reason']}")

            if breakdown['breakdown_detected']:
                print(f"\n    [상세 정보]")
                print(f"      현재가: {breakdown.get('current_price', 0):,.0f}원")
                print(f"      EMA(20): {breakdown.get('ema_value', 0):,.0f}원")
                print(f"      EMA 이탈: {breakdown.get('ema_distance_pct', 0):.2f}%")
                print(f"      거래량 급증: {breakdown.get('volume_surge_ratio', 0):.1f}배")
                print(f"      연속 하락: {'예' if breakdown.get('consecutive_decline') else '아니오'}")

        except Exception as e:
            print(f"  ✗ 오류: {e}")
            import traceback
            traceback.print_exc()

    # API 종료
    api.close()

    print("\n" + "=" * 80)
    print(f"{'✓ EMA Breakdown 감지 테스트 완료':^80}")
    print("=" * 80)


def test_order_api_dry_run():
    """
    주문 API 테스트 (Dry Run)

    주의: 실제 주문이 나가지 않도록 극소량으로 테스트하거나,
         모의투자 계좌를 사용하세요.
    """
    print("\n" + "=" * 80)
    print(f"{'주문 API Dry Run 테스트':^80}")
    print("=" * 80)

    print("\n⚠️  주의사항:")
    print("  - 이 테스트는 실제 주문 API를 호출합니다")
    print("  - 모의투자 계좌에서 테스트하는 것을 권장합니다")
    print("  - 실전 계좌 사용시 극소량으로만 테스트하세요")

    # 사용자 확인
    response = input("\n계속하시겠습니까? (yes/no): ")
    if response.lower() != 'yes':
        print("테스트를 취소합니다.")
        return

    # API 생성
    api = KiwoomAPI()

    print("\n[1] 토큰 발급")
    api.get_access_token()

    # 테스트 종목 (삼성전자)
    stock_code = "005930"
    stock_name = "삼성전자"

    print(f"\n[2] 현재가 조회: {stock_name}")

    try:
        # 현재가 조회
        stock_info = api.get_stock_info(stock_code=stock_code)
        if stock_info.get('return_code') != 0:
            print(f"  ✗ 현재가 조회 실패")
            api.close()
            return

        current_price = stock_info.get('cur_prc')
        if current_price:
            current_price = float(str(current_price).replace(',', '').replace('+', '').replace('-', ''))
            print(f"  ✓ 현재가: {current_price:,.0f}원")
        else:
            print(f"  ✗ 현재가 정보 없음")
            api.close()
            return

        # 매수 주문 테스트 (1주, 지정가)
        print(f"\n[3] 매수 주문 테스트 (1주)")
        print(f"  종목: {stock_name} ({stock_code})")
        print(f"  수량: 1주")
        print(f"  가격: {int(current_price)}원 (지정가)")

        # 실제 주문 실행 여부 확인
        confirm = input("\n실제 매수 주문을 실행하시겠습니까? (yes/no): ")
        if confirm.lower() == 'yes':
            buy_result = api.order_buy(
                stock_code=stock_code,
                quantity=1,
                price=int(current_price),
                trade_type="0"  # 지정가
            )

            if buy_result.get('return_code') == 0:
                order_no = buy_result.get('ord_no')
                print(f"\n  ✅ 매수 주문 성공!")
                print(f"    주문번호: {order_no}")
                print(f"    메시지: {buy_result.get('return_msg')}")

                # 주문 취소 테스트
                print(f"\n[4] 주문 취소 테스트")
                cancel_confirm = input(f"주문번호 {order_no}를 취소하시겠습니까? (yes/no): ")

                if cancel_confirm.lower() == 'yes':
                    cancel_result = api.order_cancel(
                        orig_ord_no=order_no,
                        stock_code=stock_code,
                        quantity=0  # 0이면 전체 취소
                    )

                    if cancel_result.get('return_code') == 0:
                        print(f"\n  ✅ 주문 취소 성공!")
                        print(f"    취소 주문번호: {cancel_result.get('ord_no')}")
                        print(f"    메시지: {cancel_result.get('return_msg')}")
                    else:
                        print(f"\n  ❌ 주문 취소 실패")
                        print(f"    메시지: {cancel_result.get('return_msg')}")

            else:
                print(f"\n  ❌ 매수 주문 실패")
                print(f"    메시지: {buy_result.get('return_msg')}")
        else:
            print("  주문 실행을 취소합니다.")

    except Exception as e:
        print(f"\n  ✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 종료
        api.close()

    print("\n" + "=" * 80)
    print(f"{'✓ 주문 API Dry Run 테스트 완료':^80}")
    print("=" * 80)


def test_integration_with_breakdown():
    """
    EMA Breakdown과 주문 API 통합 테스트

    실제 시나리오:
    1. 포지션 보유 중
    2. EMA Breakdown 감지
    3. 자동 매도 주문 실행
    """
    print("\n" + "=" * 80)
    print(f"{'EMA Breakdown + 주문 API 통합 테스트':^80}")
    print("=" * 80)

    print("\n이 테스트는 다음을 시뮬레이션합니다:")
    print("  1. 보유 포지션이 있다고 가정")
    print("  2. EMA Breakdown 감지")
    print("  3. Breakdown 신호에 따른 매도 판단")

    # API 및 분석기 생성
    api = KiwoomAPI()
    analyzer = TechnicalAnalyzer()

    print("\n[1] 토큰 발급")
    api.get_access_token()

    # 테스트 종목
    stock_code = "005930"
    stock_name = "삼성전자"

    print(f"\n[2] {stock_name} 분석")

    try:
        # 차트 데이터 조회
        chart_result = api.get_daily_chart(stock_code=stock_code)
        if chart_result.get('return_code') != 0:
            print(f"  ✗ 차트 데이터 조회 실패")
            api.close()
            return

        chart_data = chart_result['stk_dt_pole_chart_qry']
        print(f"  ✓ 차트 데이터: {len(chart_data)}일")

        # DataFrame 준비
        df = analyzer.prepare_dataframe(chart_data)

        # 현재가
        current_price = float(str(chart_data[0]['cur_prc']).replace(',', '').replace('+', '').replace('-', ''))
        print(f"  ✓ 현재가: {current_price:,.0f}원")

        # EMA Breakdown 감지
        breakdown = analyzer.detect_ema_breakdown(df, ema_period=20)

        print(f"\n[3] EMA Breakdown 분석")
        print(f"  Breakdown 감지: {'✅ YES' if breakdown['breakdown_detected'] else '❌ NO'}")
        print(f"  신뢰도: {breakdown['confidence']}")
        print(f"  사유: {breakdown['reason']}")

        # 매도 판단 로직 (OrderExecutor의 5단계 로직과 동일)
        print(f"\n[4] 매도 판단")

        # 가상 포지션 (100주 보유, 평단가 95,000원)
        position_quantity = 100
        position_avg_price = 95000
        profit_rate = (current_price - position_avg_price) / position_avg_price

        print(f"  가상 포지션: {position_quantity}주 @ {position_avg_price:,}원")
        print(f"  평가손익: {profit_rate * 100:+.2f}%")

        should_sell = False
        sell_reason = ""

        if breakdown['breakdown_detected']:
            confidence = breakdown['confidence']

            if confidence == 'HIGH':
                should_sell = True
                sell_reason = f"5단계: EMA Breakdown (HIGH) - {breakdown['reason']}"
            elif confidence == 'MEDIUM' and profit_rate < 0:
                should_sell = True
                sell_reason = f"5단계: EMA Breakdown (MEDIUM) + 손실 - {breakdown['reason']}"

        if should_sell:
            print(f"\n  ✅ 매도 신호 발생!")
            print(f"    사유: {sell_reason}")
            print(f"    권장 매도 수량: {position_quantity}주")
            print(f"    권장 매도 가격: {int(current_price):,}원 (현재가)")
        else:
            print(f"\n  ❌ 매도 신호 없음 (포지션 유지)")

    except Exception as e:
        print(f"\n  ✗ 오류: {e}")
        import traceback
        traceback.print_exc()

    finally:
        api.close()

    print("\n" + "=" * 80)
    print(f"{'✓ 통합 테스트 완료':^80}")
    print("=" * 80)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Phase 3 개선사항 테스트')
    parser.add_argument('--mode', choices=['ema', 'order', 'integration', 'all'], default='ema',
                        help='테스트 모드 (ema: EMA Breakdown, order: 주문 API, integration: 통합, all: 전체)')

    args = parser.parse_args()

    if args.mode == 'ema':
        test_ema_breakdown_detection()
    elif args.mode == 'order':
        test_order_api_dry_run()
    elif args.mode == 'integration':
        test_integration_with_breakdown()
    elif args.mode == 'all':
        test_ema_breakdown_detection()
        test_integration_with_breakdown()
        # 주문 API는 별도 확인 필요
        print("\n⚠️  주문 API 테스트는 별도로 --mode order로 실행하세요")
