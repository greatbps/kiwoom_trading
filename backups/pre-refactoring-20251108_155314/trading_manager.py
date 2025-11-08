#!/usr/bin/env python3
"""
키움 API 트레이딩 매니저

실제 매매 로직을 구현하는 고수준 매니저 클래스
- 주문 관리 (매수/매도/취소/정정)
- 포지션 관리 (보유 종목, 미체결 주문)
- 리스크 관리 (손절/익절)
- 매매 전략 실행
"""
import time
from typing import Dict, List, Optional, Any
from datetime import datetime
from kiwoom_api import KiwoomAPI


class TradingManager:
    """키움 API 트레이딩 매니저"""

    def __init__(self, api: KiwoomAPI, max_stocks: int = 10,
                 max_position_ratio: float = 0.1):
        """
        초기화

        Args:
            api: KiwoomAPI 인스턴스
            max_stocks: 최대 보유 종목 수
            max_position_ratio: 1종목당 최대 투자 비율 (예: 0.1 = 10%)
        """
        self.api = api
        self.max_stocks = max_stocks
        self.max_position_ratio = max_position_ratio

        # 캐시
        self._positions = {}  # 보유 포지션
        self._unexecuted_orders = {}  # 미체결 주문
        self._last_update = None

    def update_positions(self) -> Dict[str, Any]:
        """
        보유 포지션 업데이트

        Returns:
            보유 포지션 딕셔너리 {종목코드: 포지션정보}
        """
        try:
            result = self.api.get_account_evaluation()

            if result.get('return_code') != 0:
                print(f"✗ 포지션 업데이트 실패: {result.get('return_msg')}")
                return self._positions

            # 응답에서 보유 종목 추출
            positions = {}
            for key in ['acnt_evlt_prst', 'output2', 'data']:
                if key in result and isinstance(result[key], list):
                    for item in result[key]:
                        stock_code = item.get('stk_cd')
                        if stock_code:
                            positions[stock_code] = {
                                'stock_code': stock_code,
                                'stock_name': item.get('stk_nm'),
                                'quantity': int(item.get('rmnd_qty', 0)),
                                'avg_price': float(item.get('buy_uv', 0)),
                                'current_price': float(item.get('cur_prc', 0)),
                                'eval_amount': float(item.get('evlt_amt', 0)),
                                'profit': float(item.get('evltv_prft', 0)),
                                'profit_rate': float(item.get('prft_rt', 0)),
                            }
                    break

            self._positions = positions
            self._last_update = datetime.now()
            print(f"✓ 포지션 업데이트: {len(positions)}개 종목 보유")
            return positions

        except Exception as e:
            print(f"✗ 포지션 업데이트 중 오류: {e}")
            return self._positions

    def update_unexecuted_orders(self) -> Dict[str, Any]:
        """
        미체결 주문 업데이트

        Returns:
            미체결 주문 딕셔너리 {주문번호: 주문정보}
        """
        try:
            result = self.api.get_unexecuted_orders()

            if result.get('return_code') != 0:
                print(f"✗ 미체결 주문 업데이트 실패: {result.get('return_msg')}")
                return self._unexecuted_orders

            # 응답에서 미체결 주문 추출
            orders = {}
            for key in ['ord_noexe', 'output', 'data']:
                if key in result and isinstance(result[key], list):
                    for item in result[key]:
                        order_no = item.get('ord_no')
                        if order_no:
                            orders[order_no] = {
                                'order_no': order_no,
                                'orig_order_no': item.get('orig_ord_no'),
                                'stock_code': item.get('stk_cd'),
                                'stock_name': item.get('stk_nm'),
                                'order_qty': int(item.get('ord_qty', 0)),
                                'order_price': float(item.get('ord_uv', 0)),
                                'executed_qty': int(item.get('cntr_qty', 0)),
                                'unexecuted_qty': int(item.get('noexe_qty', 0)),
                                'side': item.get('buy_sel_tp_nm'),  # 매수/매도
                                'order_date': item.get('ord_dt'),
                                'order_time': item.get('ord_time'),
                            }
                    break

            self._unexecuted_orders = orders
            print(f"✓ 미체결 주문 업데이트: {len(orders)}건")
            return orders

        except Exception as e:
            print(f"✗ 미체결 주문 업데이트 중 오류: {e}")
            return self._unexecuted_orders

    def get_available_cash(self) -> float:
        """
        주문 가능 현금 조회

        Returns:
            주문 가능 금액
        """
        try:
            result = self.api.get_balance()

            if result.get('return_code') != 0:
                print(f"✗ 예수금 조회 실패: {result.get('return_msg')}")
                return 0.0

            # 주문가능금액 추출
            for key in ['entr_dtl_prst', 'output', 'data']:
                if key in result:
                    data = result[key]
                    if isinstance(data, dict):
                        return float(data.get('ord_alow_amt', 0))

            return 0.0

        except Exception as e:
            print(f"✗ 예수금 조회 중 오류: {e}")
            return 0.0

    def calculate_order_quantity(self, stock_code: str, price: float,
                                 invest_ratio: float = None) -> int:
        """
        주문 수량 계산

        Args:
            stock_code: 종목코드
            price: 주문 가격
            invest_ratio: 투자 비율 (None이면 max_position_ratio 사용)

        Returns:
            주문 수량
        """
        if invest_ratio is None:
            invest_ratio = self.max_position_ratio

        # 가용 현금 조회
        available_cash = self.get_available_cash()

        # 투자 금액 계산
        invest_amount = available_cash * invest_ratio

        # 수량 계산 (소수점 버림)
        quantity = int(invest_amount / price)

        print(f"  가용현금: {available_cash:,.0f}원")
        print(f"  투자비율: {invest_ratio*100:.1f}%")
        print(f"  투자금액: {invest_amount:,.0f}원")
        print(f"  주문가격: {price:,.0f}원")
        print(f"  주문수량: {quantity}주")

        return quantity

    def buy(self, stock_code: str, price: float = 0, quantity: int = None,
            invest_ratio: float = None, trade_type: str = "0") -> Optional[str]:
        """
        매수 주문

        Args:
            stock_code: 종목코드
            price: 주문가격 (0이면 시장가)
            quantity: 주문수량 (None이면 자동 계산)
            invest_ratio: 투자비율 (None이면 max_position_ratio)
            trade_type: 매매구분 (0:보통, 3:시장가, 6:최유리)

        Returns:
            주문번호 (실패시 None)
        """
        print(f"\n[매수 주문 시작] {stock_code}")

        # 보유 종목 수 체크
        if len(self._positions) >= self.max_stocks:
            if stock_code not in self._positions:
                print(f"✗ 최대 보유 종목 수({self.max_stocks}) 초과")
                return None

        # 수량 계산
        if quantity is None:
            if price == 0:
                # 시장가 주문: 현재가 조회
                quote = self.api.get_stock_quote(stock_code)
                if quote.get('return_code') != 0:
                    print(f"✗ 현재가 조회 실패")
                    return None

                # 호가 데이터에서 매수호가1 사용
                for key in ['stk_hoga', 'output', 'data']:
                    if key in quote and isinstance(quote[key], dict):
                        price = float(quote[key].get('buy_hoga_1', 0))
                        break

                if price == 0:
                    print(f"✗ 매수가격을 확인할 수 없습니다")
                    return None

            quantity = self.calculate_order_quantity(stock_code, price, invest_ratio)

        if quantity <= 0:
            print(f"✗ 주문수량이 0 이하입니다")
            return None

        # 매수 주문 실행
        try:
            result = self.api.order_buy(
                stock_code=stock_code,
                quantity=quantity,
                price=int(price) if price > 0 else 0,
                trade_type=trade_type
            )

            if result.get('return_code') == 0:
                order_no = result.get('ord_no')
                print(f"✓ 매수 주문 성공 - 주문번호: {order_no}")
                return order_no
            else:
                print(f"✗ 매수 주문 실패: {result.get('return_msg')}")
                return None

        except Exception as e:
            print(f"✗ 매수 주문 중 오류: {e}")
            return None

    def sell(self, stock_code: str, price: float = 0, quantity: int = None,
             trade_type: str = "0") -> Optional[str]:
        """
        매도 주문

        Args:
            stock_code: 종목코드
            price: 주문가격 (0이면 시장가)
            quantity: 주문수량 (None이면 보유 전량)
            trade_type: 매매구분 (0:보통, 3:시장가, 6:최유리)

        Returns:
            주문번호 (실패시 None)
        """
        print(f"\n[매도 주문 시작] {stock_code}")

        # 보유 종목 확인
        if stock_code not in self._positions:
            print(f"✗ 보유하지 않은 종목입니다")
            return None

        position = self._positions[stock_code]

        # 수량 확인
        if quantity is None:
            quantity = position['quantity']

        if quantity <= 0:
            print(f"✗ 매도할 수량이 없습니다")
            return None

        if quantity > position['quantity']:
            print(f"✗ 보유 수량({position['quantity']})보다 많은 수량입니다")
            return None

        # 매도 주문 실행
        try:
            result = self.api.order_sell(
                stock_code=stock_code,
                quantity=quantity,
                price=int(price) if price > 0 else 0,
                trade_type=trade_type
            )

            if result.get('return_code') == 0:
                order_no = result.get('ord_no')
                print(f"✓ 매도 주문 성공 - 주문번호: {order_no}")
                return order_no
            else:
                print(f"✗ 매도 주문 실패: {result.get('return_msg')}")
                return None

        except Exception as e:
            print(f"✗ 매도 주문 중 오류: {e}")
            return None

    def cancel_order(self, order_no: str, stock_code: str,
                    quantity: int = 0) -> bool:
        """
        주문 취소

        Args:
            order_no: 주문번호
            stock_code: 종목코드
            quantity: 취소수량 (0이면 전량)

        Returns:
            성공 여부
        """
        print(f"\n[주문 취소] 주문번호: {order_no}")

        try:
            result = self.api.order_cancel(
                orig_ord_no=order_no,
                stock_code=stock_code,
                quantity=quantity
            )

            if result.get('return_code') == 0:
                print(f"✓ 주문 취소 성공")
                return True
            else:
                print(f"✗ 주문 취소 실패: {result.get('return_msg')}")
                return False

        except Exception as e:
            print(f"✗ 주문 취소 중 오류: {e}")
            return False

    def cancel_all_orders(self, stock_code: str = None) -> int:
        """
        미체결 주문 일괄 취소

        Args:
            stock_code: 종목코드 (None이면 모든 종목)

        Returns:
            취소된 주문 수
        """
        print(f"\n[미체결 주문 일괄 취소]")

        # 미체결 주문 업데이트
        self.update_unexecuted_orders()

        if not self._unexecuted_orders:
            print("  취소할 미체결 주문이 없습니다")
            return 0

        # 주문 취소
        cancelled_count = 0
        for order_no, order in self._unexecuted_orders.items():
            # 특정 종목만 취소
            if stock_code and order['stock_code'] != stock_code:
                continue

            if self.cancel_order(order_no, order['stock_code']):
                cancelled_count += 1
                time.sleep(0.2)  # API 호출 간격

        print(f"✓ {cancelled_count}건의 주문 취소 완료")
        return cancelled_count

    def check_stop_loss(self, stop_loss_ratio: float = -0.03) -> List[str]:
        """
        손절 체크

        Args:
            stop_loss_ratio: 손절 비율 (예: -0.03 = -3%)

        Returns:
            손절 대상 종목 리스트
        """
        stop_loss_stocks = []

        for stock_code, position in self._positions.items():
            profit_rate = position['profit_rate']

            if profit_rate <= stop_loss_ratio:
                print(f"\n[손절 알림] {stock_code} {position['stock_name']}")
                print(f"  수익률: {profit_rate*100:.2f}%")
                print(f"  손익: {position['profit']:,.0f}원")
                stop_loss_stocks.append(stock_code)

        return stop_loss_stocks

    def check_take_profit(self, take_profit_ratio: float = 0.05) -> List[str]:
        """
        익절 체크

        Args:
            take_profit_ratio: 익절 비율 (예: 0.05 = 5%)

        Returns:
            익절 대상 종목 리스트
        """
        take_profit_stocks = []

        for stock_code, position in self._positions.items():
            profit_rate = position['profit_rate']

            if profit_rate >= take_profit_ratio:
                print(f"\n[익절 알림] {stock_code} {position['stock_name']}")
                print(f"  수익률: {profit_rate*100:.2f}%")
                print(f"  손익: {position['profit']:,.0f}원")
                take_profit_stocks.append(stock_code)

        return take_profit_stocks

    def execute_stop_loss(self, stop_loss_ratio: float = -0.03) -> int:
        """
        손절 실행 (시장가 매도)

        Args:
            stop_loss_ratio: 손절 비율

        Returns:
            손절 매도 주문 수
        """
        print(f"\n[손절 실행] 손절 기준: {stop_loss_ratio*100:.1f}%")

        # 포지션 업데이트
        self.update_positions()

        # 손절 대상 확인
        stop_loss_stocks = self.check_stop_loss(stop_loss_ratio)

        if not stop_loss_stocks:
            print("  손절 대상 종목이 없습니다")
            return 0

        # 손절 매도
        sell_count = 0
        for stock_code in stop_loss_stocks:
            # 시장가 전량 매도
            order_no = self.sell(stock_code, price=0, trade_type="3")
            if order_no:
                sell_count += 1
            time.sleep(0.2)  # API 호출 간격

        print(f"✓ {sell_count}개 종목 손절 매도 완료")
        return sell_count

    def execute_take_profit(self, take_profit_ratio: float = 0.05) -> int:
        """
        익절 실행 (시장가 매도)

        Args:
            take_profit_ratio: 익절 비율

        Returns:
            익절 매도 주문 수
        """
        print(f"\n[익절 실행] 익절 기준: {take_profit_ratio*100:.1f}%")

        # 포지션 업데이트
        self.update_positions()

        # 익절 대상 확인
        take_profit_stocks = self.check_take_profit(take_profit_ratio)

        if not take_profit_stocks:
            print("  익절 대상 종목이 없습니다")
            return 0

        # 익절 매도
        sell_count = 0
        for stock_code in take_profit_stocks:
            # 시장가 전량 매도
            order_no = self.sell(stock_code, price=0, trade_type="3")
            if order_no:
                sell_count += 1
            time.sleep(0.2)  # API 호출 간격

        print(f"✓ {sell_count}개 종목 익절 매도 완료")
        return sell_count

    def print_summary(self):
        """현재 계좌 상태 출력"""
        print("\n" + "=" * 80)
        print("  계좌 현황")
        print("=" * 80)

        # 보유 종목
        print(f"\n[보유 종목] {len(self._positions)}개")
        for stock_code, position in self._positions.items():
            print(f"  {stock_code} {position['stock_name']}")
            print(f"    수량: {position['quantity']:,}주")
            print(f"    평균가: {position['avg_price']:,.0f}원")
            print(f"    현재가: {position['current_price']:,.0f}원")
            print(f"    평가금액: {position['eval_amount']:,.0f}원")
            print(f"    손익: {position['profit']:,.0f}원 ({position['profit_rate']*100:.2f}%)")

        # 미체결 주문
        print(f"\n[미체결 주문] {len(self._unexecuted_orders)}건")
        for order_no, order in self._unexecuted_orders.items():
            print(f"  {order['stock_code']} {order['stock_name']}")
            print(f"    주문번호: {order_no}")
            print(f"    구분: {order['side']}")
            print(f"    주문가격: {order['order_price']:,.0f}원")
            print(f"    주문수량: {order['order_qty']:,}주")
            print(f"    미체결: {order['unexecuted_qty']:,}주")

        # 가용 현금
        available_cash = self.get_available_cash()
        print(f"\n[가용 현금] {available_cash:,.0f}원")

        print("=" * 80)
