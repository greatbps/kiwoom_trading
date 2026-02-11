"""
키움증권 브로커 구현
==================

국내주식 단기매매용
"""

import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import List, Optional, Tuple

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from brokers.broker_base import (
    BrokerBase, Market, Position, Balance,
    OrderSide, OrderType, OrderStatus, OrderResult
)


class KiwoomBroker(BrokerBase):
    """
    키움증권 국내주식 브로커

    단기/스캘핑 매매용
    """

    def __init__(self, account_number: str = None):
        super().__init__(name="키움_국내", market=Market.KR)
        self.api = None
        self.account_number = account_number or os.getenv('KIWOOM_ACCOUNT', '6259-3479')

    def initialize(self) -> bool:
        """API 초기화"""
        try:
            from kiwoom_api import KiwoomAPI
            self.api = KiwoomAPI(account_number=self.account_number)
            token = self.api.get_access_token()
            self.is_initialized = token is not None
            return self.is_initialized
        except Exception as e:
            print(f"❌ 키움 초기화 실패: {e}")
            return False

    def get_positions(self) -> List[Position]:
        """보유 포지션 조회"""
        if not self.api:
            return []

        result = self.api.get_account_info()
        if result.get('return_code') != 0:
            return []

        positions = []
        # 키움 API는 day_bal_rt 에 보유종목 리스트가 있음
        holdings = result.get('day_bal_rt', result.get('data', []))

        def safe_int(val, default=0):
            if val is None or val == '':
                return default
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        def safe_float(val, default=0.0):
            if val is None or val == '':
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        for h in holdings:
            # 키움 API 필드명: stk_cd, stk_nm, rmnd_qty, buy_uv, cur_prc, prft_rt, evlt_amt
            symbol = h.get('stk_cd', h.get('stock_code', ''))

            # 빈 데이터 필터링 (새벽 데이터 정리 시간 등)
            if not symbol or symbol == '':
                continue

            if symbol.startswith('A'):
                symbol = symbol[1:]

            quantity = safe_int(h.get('rmnd_qty', h.get('hold_qty', h.get('quantity', 0))))

            # 수량 0인 종목 제외
            if quantity <= 0:
                continue

            pos = Position(
                symbol=symbol,
                name=h.get('stk_nm', h.get('stock_name', '')),
                quantity=quantity,
                avg_price=safe_float(h.get('buy_uv', h.get('avg_buy_price', h.get('avg_price', 0)))),
                current_price=safe_float(h.get('cur_prc', h.get('cur_price', h.get('current_price', 0)))),
                profit_pct=safe_float(h.get('prft_rt', h.get('eval_pl_rt', h.get('profit_loss_pct', 0)))),
                eval_amount=safe_float(h.get('evlt_amt', h.get('eval_amt', 0))),
                market=Market.KR,
                currency="KRW"
            )
            positions.append(pos)

        return positions

    def get_balance(self) -> Balance:
        """계좌 잔고 조회"""
        if not self.api:
            return Balance(0, 0, 0, 0, 0)

        result = self.api.get_account_info()
        if result.get('return_code') != 0:
            return Balance(0, 0, 0, 0, 0)

        # 데이터에서 추출
        data = result.get('data', [])
        total_eval = sum(float(h.get('eval_amt', 0)) for h in data)
        total_invested = sum(
            float(h.get('avg_buy_price', h.get('avg_price', 0))) *
            int(h.get('hold_qty', h.get('quantity', 0)))
            for h in data
        )

        profit = total_eval - total_invested
        profit_pct = (profit / total_invested * 100) if total_invested > 0 else 0

        return Balance(
            total_eval=total_eval,
            total_deposit=0,  # 별도 조회 필요
            available_cash=0,
            total_profit=profit,
            total_profit_pct=profit_pct,
            currency="KRW"
        )

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0
    ) -> OrderResult:
        """주문 실행"""
        if not self.api:
            return OrderResult(success=False, message="API 미초기화")

        try:
            # 키움 주문 API 호출
            if side == OrderSide.BUY:
                if order_type == OrderType.MARKET:
                    result = self.api.buy_market(symbol, quantity)
                else:
                    result = self.api.buy_limit(symbol, quantity, int(price))
            else:
                if order_type == OrderType.MARKET:
                    result = self.api.sell_market(symbol, quantity)
                else:
                    result = self.api.sell_limit(symbol, quantity, int(price))

            success = result.get('return_code') == 0

            return OrderResult(
                success=success,
                order_no=result.get('order_no', ''),
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                status=OrderStatus.SUBMITTED if success else OrderStatus.FAILED,
                message=result.get('return_msg', ''),
                raw_response=result
            )

        except Exception as e:
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                quantity=quantity,
                message=str(e)
            )

    def is_market_open(self) -> Tuple[bool, str]:
        """시장 개장 여부"""
        now = datetime.now()

        # 주말 체크
        if now.weekday() >= 5:
            return False, "주말 휴장"

        # 정규장: 09:00 ~ 15:30
        market_open = dtime(9, 0)
        market_close = dtime(15, 30)

        if market_open <= now.time() <= market_close:
            return True, "장중"
        elif now.time() < market_open:
            return False, "장 시작 전"
        else:
            return False, "장 마감"
