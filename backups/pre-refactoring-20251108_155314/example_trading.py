#!/usr/bin/env python3
"""
키움 API 트레이딩 매니저 사용 예제

실제 매매 전략을 구현하는 예제 코드
"""
import time
from datetime import datetime
from kiwoom_api import KiwoomAPI
from trading_manager import TradingManager


def example_1_basic_trading():
    """예제 1: 기본 매매"""
    print("\n" + "=" * 80)
    print("  예제 1: 기본 매매")
    print("=" * 80)

    # API 초기화
    api = KiwoomAPI()
    manager = TradingManager(
        api=api,
        max_stocks=5,  # 최대 5개 종목
        max_position_ratio=0.1  # 종목당 10% 투자
    )

    # 1. 포지션 및 미체결 주문 업데이트
    print("\n[1단계] 현재 상태 조회")
    manager.update_positions()
    manager.update_unexecuted_orders()
    manager.print_summary()

    # 2. 매수 (삼성전자)
    print("\n[2단계] 매수 주문")
    stock_code = "005930"  # 삼성전자

    # 현재가 조회
    quote = api.get_stock_quote(stock_code)
    if quote.get('return_code') == 0:
        # 호가 데이터에서 매수호가1 가져오기
        for key in ['stk_hoga', 'output', 'data']:
            if key in quote and isinstance(quote[key], dict):
                buy_price = float(quote[key].get('buy_hoga_1', 0))
                print(f"현재 매수호가: {buy_price:,.0f}원")
                break

        # 매수 주문 (자동 수량 계산)
        order_no = manager.buy(
            stock_code=stock_code,
            price=buy_price,
            invest_ratio=0.1  # 10% 투자
        )

        if order_no:
            print(f"✓ 매수 주문 완료: {order_no}")

    # 3. 상태 확인
    time.sleep(2)
    manager.update_positions()
    manager.update_unexecuted_orders()
    manager.print_summary()

    # API 종료
    api.close()


def example_2_stop_loss_take_profit():
    """예제 2: 손절/익절 자동 실행"""
    print("\n" + "=" * 80)
    print("  예제 2: 손절/익절 자동 실행")
    print("=" * 80)

    # API 초기화
    api = KiwoomAPI()
    manager = TradingManager(api=api)

    # 포지션 업데이트
    print("\n[포지션 업데이트]")
    manager.update_positions()

    # 손절/익절 체크
    print("\n[손절/익절 체크]")
    stop_loss_ratio = -0.03  # -3% 손절
    take_profit_ratio = 0.05  # +5% 익절

    stop_loss_stocks = manager.check_stop_loss(stop_loss_ratio)
    take_profit_stocks = manager.check_take_profit(take_profit_ratio)

    # 손절 실행
    if stop_loss_stocks:
        print(f"\n손절 대상: {len(stop_loss_stocks)}개")
        confirm = input("손절을 실행하시겠습니까? (y/n): ")
        if confirm.lower() == 'y':
            manager.execute_stop_loss(stop_loss_ratio)

    # 익절 실행
    if take_profit_stocks:
        print(f"\n익절 대상: {len(take_profit_stocks)}개")
        confirm = input("익절을 실행하시겠습니까? (y/n): ")
        if confirm.lower() == 'y':
            manager.execute_take_profit(take_profit_ratio)

    # API 종료
    api.close()


def example_3_cancel_orders():
    """예제 3: 미체결 주문 취소"""
    print("\n" + "=" * 80)
    print("  예제 3: 미체결 주문 취소")
    print("=" * 80)

    # API 초기화
    api = KiwoomAPI()
    manager = TradingManager(api=api)

    # 미체결 주문 조회
    print("\n[미체결 주문 조회]")
    manager.update_unexecuted_orders()

    if not manager._unexecuted_orders:
        print("미체결 주문이 없습니다.")
        api.close()
        return

    # 미체결 주문 출력
    print(f"\n미체결 주문: {len(manager._unexecuted_orders)}건")
    for order_no, order in manager._unexecuted_orders.items():
        print(f"\n주문번호: {order_no}")
        print(f"  종목: {order['stock_code']} {order['stock_name']}")
        print(f"  구분: {order['side']}")
        print(f"  주문가격: {order['order_price']:,.0f}원")
        print(f"  미체결수량: {order['unexecuted_qty']:,}주")

    # 주문 취소
    confirm = input("\n모든 미체결 주문을 취소하시겠습니까? (y/n): ")
    if confirm.lower() == 'y':
        cancelled_count = manager.cancel_all_orders()
        print(f"\n✓ {cancelled_count}건의 주문이 취소되었습니다")

    # API 종료
    api.close()


def example_4_monitoring():
    """예제 4: 실시간 모니터링 (손절/익절 자동 실행)"""
    print("\n" + "=" * 80)
    print("  예제 4: 실시간 모니터링")
    print("=" * 80)

    # API 초기화
    api = KiwoomAPI()
    manager = TradingManager(api=api)

    # 설정
    stop_loss_ratio = -0.03  # -3% 손절
    take_profit_ratio = 0.05  # +5% 익절
    check_interval = 60  # 60초마다 체크

    print(f"\n[모니터링 설정]")
    print(f"  손절 기준: {stop_loss_ratio*100:.1f}%")
    print(f"  익절 기준: {take_profit_ratio*100:.1f}%")
    print(f"  체크 간격: {check_interval}초")
    print(f"\n모니터링을 시작합니다. (Ctrl+C로 종료)")

    try:
        while True:
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 체크 중...")

            # 포지션 업데이트
            manager.update_positions()

            # 손절 실행
            manager.execute_stop_loss(stop_loss_ratio)

            # 익절 실행
            manager.execute_take_profit(take_profit_ratio)

            # 대기
            time.sleep(check_interval)

    except KeyboardInterrupt:
        print("\n\n모니터링을 종료합니다.")

    # API 종료
    api.close()


def example_5_batch_buy():
    """예제 5: 여러 종목 일괄 매수"""
    print("\n" + "=" * 80)
    print("  예제 5: 여러 종목 일괄 매수")
    print("=" * 80)

    # API 초기화
    api = KiwoomAPI()
    manager = TradingManager(
        api=api,
        max_stocks=10,
        max_position_ratio=0.05  # 종목당 5%
    )

    # 매수 종목 리스트
    buy_list = [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "035420",  # NAVER
        "051910",  # LG화학
        "006400",  # 삼성SDI
    ]

    print(f"\n[매수 종목 리스트] {len(buy_list)}개")
    for code in buy_list:
        print(f"  - {code}")

    # 일괄 매수
    print(f"\n[일괄 매수 시작]")
    success_count = 0

    for stock_code in buy_list:
        print(f"\n{stock_code} 매수 시도...")

        try:
            # 현재가 조회
            quote = api.get_stock_quote(stock_code)
            if quote.get('return_code') != 0:
                print(f"  ✗ 호가 조회 실패")
                continue

            # 매수호가 추출
            buy_price = 0
            for key in ['stk_hoga', 'output', 'data']:
                if key in quote and isinstance(quote[key], dict):
                    buy_price = float(quote[key].get('buy_hoga_1', 0))
                    break

            if buy_price == 0:
                print(f"  ✗ 매수가 확인 실패")
                continue

            # 매수 주문
            order_no = manager.buy(
                stock_code=stock_code,
                price=buy_price,
                invest_ratio=0.05  # 5% 투자
            )

            if order_no:
                success_count += 1
                print(f"  ✓ 매수 성공")

            # API 호출 간격
            time.sleep(0.5)

        except Exception as e:
            print(f"  ✗ 오류 발생: {e}")
            continue

    print(f"\n[일괄 매수 완료]")
    print(f"  성공: {success_count}/{len(buy_list)}")

    # 결과 확인
    time.sleep(2)
    manager.update_positions()
    manager.update_unexecuted_orders()
    manager.print_summary()

    # API 종료
    api.close()


def main():
    """메인 함수"""
    print("\n" + "=" * 80)
    print("  키움 API 트레이딩 매니저 사용 예제")
    print("=" * 80)

    print("\n예제를 선택하세요:")
    print("  1. 기본 매매 (매수/조회)")
    print("  2. 손절/익절 자동 실행")
    print("  3. 미체결 주문 취소")
    print("  4. 실시간 모니터링 (손절/익절 자동)")
    print("  5. 여러 종목 일괄 매수")
    print("  0. 종료")

    choice = input("\n선택 (0-5): ").strip()

    if choice == '1':
        example_1_basic_trading()
    elif choice == '2':
        example_2_stop_loss_take_profit()
    elif choice == '3':
        example_3_cancel_orders()
    elif choice == '4':
        example_4_monitoring()
    elif choice == '5':
        example_5_batch_buy()
    elif choice == '0':
        print("\n프로그램을 종료합니다.")
    else:
        print("\n잘못된 선택입니다.")


if __name__ == "__main__":
    main()
