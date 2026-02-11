"""
한국투자증권 브로커 구현
======================

국내주식 + 해외주식 지원
"""

import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from brokers.broker_base import (
    BrokerBase, Market, Position, Balance,
    OrderSide, OrderType, OrderStatus, OrderResult
)


class KoreaInvestDomesticBroker(BrokerBase):
    """
    한국투자증권 국내주식 브로커

    중기 투자용
    """

    def __init__(self):
        super().__init__(name="한투_국내", market=Market.KR)
        self.api = None

    def initialize(self) -> bool:
        """API 초기화"""
        try:
            from korea_invest_api import KoreaInvestAPI
            self.api = KoreaInvestAPI()
            token = self.api.get_access_token()
            self.is_initialized = token is not None
            return self.is_initialized
        except Exception as e:
            print(f"❌ 한투 국내 초기화 실패: {e}")
            return False

    def get_positions(self) -> List[Position]:
        """보유 포지션 조회"""
        if not self.api:
            return []

        result = self.api.get_domestic_balance()
        if not result['success']:
            return []

        positions = []
        for h in result['data']:
            pos = Position(
                symbol=h.get('pdno', ''),
                name=h.get('prdt_name', ''),
                quantity=int(h.get('hldg_qty', 0)),
                avg_price=float(h.get('pchs_avg_pric', 0)),
                current_price=float(h.get('prpr', 0)),
                profit_pct=float(h.get('evlu_pfls_rt', 0)),
                eval_amount=float(h.get('evlu_amt', 0)),
                market=Market.KR,
                currency="KRW"
            )
            positions.append(pos)

        return positions

    def get_balance(self) -> Balance:
        """계좌 잔고 조회"""
        if not self.api:
            return Balance(0, 0, 0, 0, 0)

        result = self.api.get_domestic_balance()
        if not result['success']:
            return Balance(0, 0, 0, 0, 0)

        # summary에서 추출 (output2)
        summary = result.get('summary', [])
        if summary and len(summary) > 0:
            s = summary[0]
            return Balance(
                total_eval=float(s.get('tot_evlu_amt', 0)),
                total_deposit=float(s.get('dnca_tot_amt', 0)),
                available_cash=float(s.get('nxdy_excc_amt', 0)),
                total_profit=float(s.get('evlu_pfls_smtl_amt', 0)),
                total_profit_pct=float(s.get('tot_evlu_pfls_rt', 0)),
                currency="KRW"
            )

        return Balance(0, 0, 0, 0, 0)

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

        # 주문 유형 변환
        kis_order_type = "01" if order_type == OrderType.MARKET else "00"

        result = self.api.order_domestic_stock(
            stock_code=symbol,
            side=side.value,
            qty=quantity,
            price=int(price) if price > 0 else 0,
            order_type=kis_order_type
        )

        return OrderResult(
            success=result['success'],
            order_no=result.get('order_no', ''),
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status=OrderStatus.SUBMITTED if result['success'] else OrderStatus.FAILED,
            message=result.get('error', '') if not result['success'] else '주문 접수',
            raw_response=result
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


class KoreaInvestOverseasBroker(BrokerBase):
    """
    한국투자증권 해외주식 브로커

    미국주식 중심
    """

    def __init__(self):
        super().__init__(name="한투_해외", market=Market.US)
        self.api = None

    def initialize(self) -> bool:
        """API 초기화"""
        try:
            from korea_invest_api import KoreaInvestAPI
            self.api = KoreaInvestAPI()
            token = self.api.get_access_token()
            self.is_initialized = token is not None
            return self.is_initialized
        except Exception as e:
            print(f"❌ 한투 해외 초기화 실패: {e}")
            return False

    def get_positions(self) -> List[Position]:
        """보유 포지션 조회"""
        if not self.api:
            return []

        result = self.api.get_overseas_balance()
        if not result['success']:
            return []

        positions = []
        for h in result['data']:
            pos = Position(
                symbol=h.get('ovrs_pdno', ''),
                name=h.get('ovrs_item_name', h.get('ovrs_pdno', '')),
                quantity=int(h.get('ovrs_cblc_qty', 0)),
                avg_price=float(h.get('pchs_avg_pric', 0)),
                current_price=float(h.get('now_pric2', 0)),
                profit_pct=float(h.get('evlu_pfls_rt', 0)),
                eval_amount=float(h.get('ovrs_stck_evlu_amt', 0)),
                market=Market.US,
                currency="USD"
            )
            positions.append(pos)

        return positions

    def get_balance(self) -> Balance:
        """계좌 잔고 조회"""
        if not self.api:
            return Balance(0, 0, 0, 0, 0, "USD")

        result = self.api.get_overseas_balance()
        if not result['success']:
            return Balance(0, 0, 0, 0, 0, "USD")

        summary = result.get('summary', {})
        return Balance(
            total_eval=float(summary.get('tot_evlu_pfls_amt', 0)),
            total_deposit=float(summary.get('frcr_pchs_amt1', 0)),
            available_cash=float(summary.get('frcr_ord_psbl_amt1', 0)),
            total_profit=float(summary.get('ovrs_tot_pfls', 0)),
            total_profit_pct=float(summary.get('tot_pftrt', 0)),
            currency="USD"
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

        result = self.api.order_overseas_stock(
            symbol=symbol,
            side=side.value,
            qty=quantity,
            price=price,
            exchange="NASD"  # 나스닥 기본
        )

        return OrderResult(
            success=result['success'],
            order_no=result.get('order_no', ''),
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status=OrderStatus.SUBMITTED if result['success'] else OrderStatus.FAILED,
            message=result.get('error', '') if not result['success'] else '주문 접수',
            raw_response=result
        )

    def place_market_sell(self, symbol: str, quantity: int) -> OrderResult:
        """
        시장가 매도 (현재가 기준 지정가 주문)

        해외주식은 시장가 주문이 없어서 현재가로 지정가 주문
        """
        # 현재가 조회
        positions = self.get_positions()
        current_price = 0.0

        for pos in positions:
            if pos.symbol == symbol:
                current_price = pos.current_price
                break

        if current_price <= 0:
            return OrderResult(success=False, message=f"현재가 조회 실패: {symbol}")

        # 가격을 소수점 2자리로 반올림 (해외주식 규칙)
        current_price = round(current_price, 2)

        # 현재가로 지정가 매도
        return self.place_order(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            price=current_price
        )

    def place_market_buy(self, symbol: str, quantity: int) -> OrderResult:
        """
        시장가 매수 (현재가 기준 지정가 주문)

        해외주식은 시장가 주문이 없어서 현재가로 지정가 주문
        """
        # 현재가 조회 (yfinance 사용)
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            current_price = ticker.info.get('regularMarketPrice', 0)
            if current_price <= 0:
                hist = ticker.history(period='1d')
                if not hist.empty:
                    current_price = float(hist['Close'].iloc[-1])
        except Exception as e:
            return OrderResult(success=False, message=f"현재가 조회 실패: {symbol} - {e}")

        if current_price <= 0:
            return OrderResult(success=False, message=f"현재가 조회 실패: {symbol}")

        # 가격을 소수점 2자리로 반올림 (해외주식 규칙)
        current_price = round(current_price, 2)

        # 현재가로 지정가 매수
        return self.place_order(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            price=current_price
        )

    def is_market_open(self) -> Tuple[bool, str]:
        """미국 시장 개장 여부"""
        ny_tz = ZoneInfo("America/New_York")
        now_ny = datetime.now(ny_tz)

        # 주말 체크
        if now_ny.weekday() >= 5:
            return False, f"주말 휴장 (뉴욕: {now_ny.strftime('%H:%M')})"

        # 정규장: 09:30 ~ 16:00 ET
        market_open = dtime(9, 30)
        market_close = dtime(16, 0)
        current_time = now_ny.time()

        if market_open <= current_time <= market_close:
            return True, f"정규장 (뉴욕: {now_ny.strftime('%H:%M')})"

        # 프리마켓: 04:00 ~ 09:30 ET
        premarket_open = dtime(4, 0)
        if premarket_open <= current_time < market_open:
            return True, f"프리마켓 (뉴욕: {now_ny.strftime('%H:%M')})"

        # 애프터마켓: 16:00 ~ 20:00 ET
        aftermarket_close = dtime(20, 0)
        if market_close < current_time <= aftermarket_close:
            return True, f"애프터마켓 (뉴욕: {now_ny.strftime('%H:%M')})"

        return False, f"장외시간 (뉴욕: {now_ny.strftime('%H:%M')})"
