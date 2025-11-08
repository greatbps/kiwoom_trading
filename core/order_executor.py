"""
주문 실행자 - 매수/매도 주문 실행

실제 Kiwoom API를 사용하여 주문을 실행합니다.
6단계 고도화 매도 전략을 구현합니다.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal
import time

from kiwoom_api import KiwoomAPI
from .position_manager import Position, PositionManager
from .risk_manager import RiskManager


@dataclass
class OrderResult:
    """주문 결과"""
    success: bool
    message: str
    order_no: Optional[str] = None
    executed_quantity: int = 0
    executed_price: float = 0.0
    timestamp: Optional[datetime] = None


class OrderExecutor:
    """
    주문 실행자

    매수/매도 주문을 실행하고 결과를 추적합니다.
    6단계 고도화 매도 전략을 구현합니다:
    1. Hard Stop (-3%): 전량 매도
    2. Partial TP 1 (+4%): 40% 매도
    3. Partial TP 2 (+6%): 40% 매도, trailing 활성화
    4. ATR Trailing: 나머지 20% trailing
    5. EMA + Volume Breakdown: 추세 전환 감지
    6. Time Filter: 15:00 전 강제 청산
    """

    # 6단계 매도 전략 파라미터
    HARD_STOP_RATE = -0.03  # -3% 하드 스탑
    PARTIAL_TP1_RATE = 0.04  # +4% 1차 익절
    PARTIAL_TP2_RATE = 0.06  # +6% 2차 익절

    PARTIAL_SELL_RATIO_1 = 0.40  # 1차 익절시 40% 매도
    PARTIAL_SELL_RATIO_2 = 0.40  # 2차 익절시 40% 매도
    TRAILING_RATIO = 0.20  # trailing은 나머지 20%

    FORCE_CLOSE_TIME = "15:00:00"  # 장 마감 전 강제 청산 시간

    def __init__(
        self,
        api: KiwoomAPI,
        position_manager: PositionManager,
        risk_manager: RiskManager,
        account_no: str
    ):
        """
        초기화

        Args:
            api: Kiwoom API 인스턴스
            position_manager: 포지션 관리자
            risk_manager: 리스크 관리자
            account_no: 계좌번호
        """
        self.api = api
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.account_no = account_no

    def execute_buy(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        price: float,
        targets: dict,
        stop_loss: float,
        atr: float,
        entry_signal: str,
        entry_score: float
    ) -> OrderResult:
        """
        매수 주문 실행

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            quantity: 수량
            price: 가격
            targets: 목표가 {'target1', 'target2', 'target3'}
            stop_loss: 손절가
            atr: ATR 값
            entry_signal: 진입 신호
            entry_score: 진입 점수

        Returns:
            주문 결과
        """
        if quantity <= 0:
            return OrderResult(
                success=False,
                message="매수 수량이 0 이하입니다",
                timestamp=datetime.now()
            )

        try:
            # 실제 Kiwoom API 매수 주문 호출
            print(f"\n[매수 주문 실행]")
            print(f"  종목: {stock_name} ({stock_code})")
            print(f"  수량: {quantity:,}주")
            print(f"  가격: {price:,}원 (지정가)")
            print(f"  금액: {quantity * price:,}원")
            print(f"  목표: {targets['target1']:,}원 / {targets['target2']:,}원 / {targets['target3']:,}원")
            print(f"  손절: {stop_loss:,}원")

            # Kiwoom API 매수 주문 (지정가)
            api_result = self.api.order_buy(
                stock_code=stock_code,
                quantity=quantity,
                price=int(price),  # 정수로 변환
                trade_type="0"  # 0: 보통(지정가)
            )

            # API 응답 확인
            if api_result.get('return_code') != 0:
                return OrderResult(
                    success=False,
                    message=f"매수 주문 실패: {api_result.get('return_msg')}",
                    timestamp=datetime.now()
                )

            # 주문번호 추출
            order_no = api_result.get('ord_no', f"ORD{int(time.time())}")

            # 포지션 생성
            position = Position(
                stock_code=stock_code,
                stock_name=stock_name,
                quantity=quantity,
                avg_price=price,
                current_price=price,
                buy_time=datetime.now(),
                target1=targets['target1'],
                target2=targets['target2'],
                target3=targets['target3'],
                stop_loss=stop_loss,
                atr=atr,
                entry_signal=entry_signal,
                entry_score=entry_score
            )

            # 포지션 관리자에 추가
            self.position_manager.add_position(position)

            # 리스크 관리자에 거래 기록
            self.risk_manager.record_trade(
                stock_code=stock_code,
                stock_name=stock_name,
                trade_type='BUY',
                quantity=quantity,
                price=price
            )

            return OrderResult(
                success=True,
                message=f"매수 주문 성공: {api_result.get('return_msg')}",
                order_no=order_no,
                executed_quantity=quantity,
                executed_price=price,
                timestamp=datetime.now()
            )

        except Exception as e:
            return OrderResult(
                success=False,
                message=f"매수 주문 실패: {e}",
                timestamp=datetime.now()
            )

    def execute_sell(
        self,
        stock_code: str,
        quantity: int,
        price: float,
        reason: str
    ) -> OrderResult:
        """
        매도 주문 실행

        Args:
            stock_code: 종목코드
            quantity: 수량
            price: 가격
            reason: 매도 사유

        Returns:
            주문 결과
        """
        position = self.position_manager.get_position(stock_code)
        if not position:
            return OrderResult(
                success=False,
                message=f"포지션을 찾을 수 없습니다: {stock_code}",
                timestamp=datetime.now()
            )

        if quantity <= 0 or quantity > position.remaining_quantity:
            return OrderResult(
                success=False,
                message=f"잘못된 매도 수량: {quantity} (보유: {position.remaining_quantity})",
                timestamp=datetime.now()
            )

        try:
            # 손익 계산
            realized_pnl = (price - position.avg_price) * quantity
            pnl_rate = ((price - position.avg_price) / position.avg_price) * 100

            print(f"\n[매도 주문 실행]")
            print(f"  종목: {position.stock_name} ({stock_code})")
            print(f"  수량: {quantity:,}주 (잔여: {position.remaining_quantity - quantity:,}주)")
            print(f"  가격: {price:,}원 (지정가)")
            print(f"  손익: {realized_pnl:+,.0f}원 ({pnl_rate:+.2f}%)")
            print(f"  사유: {reason}")

            # 실제 Kiwoom API 매도 주문 호출 (지정가)
            api_result = self.api.order_sell(
                stock_code=stock_code,
                quantity=quantity,
                price=int(price),  # 정수로 변환
                trade_type="0"  # 0: 보통(지정가)
            )

            # API 응답 확인
            if api_result.get('return_code') != 0:
                return OrderResult(
                    success=False,
                    message=f"매도 주문 실패: {api_result.get('return_msg')}",
                    timestamp=datetime.now()
                )

            # 주문번호 추출
            order_no = api_result.get('ord_no', f"ORD{int(time.time())}")

            # 리스크 관리자에 거래 기록
            self.risk_manager.record_trade(
                stock_code=stock_code,
                stock_name=position.stock_name,
                trade_type='SELL',
                quantity=quantity,
                price=price,
                realized_pnl=realized_pnl
            )

            # 포지션 업데이트는 check_exit_signals에서 처리

            return OrderResult(
                success=True,
                message=f"매도 주문 성공: {api_result.get('return_msg')} - {reason}",
                order_no=order_no,
                executed_quantity=quantity,
                executed_price=price,
                timestamp=datetime.now()
            )

        except Exception as e:
            return OrderResult(
                success=False,
                message=f"매도 주문 실패: {e}",
                timestamp=datetime.now()
            )

    def check_exit_signals(self, position: Position, current_price: float, current_time: str = None, chart_data: list = None) -> Optional[tuple]:
        """
        6단계 고도화 매도 전략 체크

        Args:
            position: 포지션
            current_price: 현재가
            current_time: 현재 시간 (HH:MM:SS)
            chart_data: 차트 데이터 (EMA Breakdown 감지용, 선택)

        Returns:
            (매도 수량, 매도 사유) 또는 None
        """
        # 현재가 업데이트
        position.update_price(current_price)

        profit_rate = position.profit_loss_rate / 100  # %를 비율로 변환

        # 6. Time Filter - 장 마감 전 강제 청산 (최우선)
        if current_time and current_time >= self.FORCE_CLOSE_TIME:
            return position.remaining_quantity, "6단계: 장 마감 전 강제 청산"

        # 1. Hard Stop (-3%)
        if profit_rate <= self.HARD_STOP_RATE:
            return position.remaining_quantity, f"1단계: Hard Stop ({profit_rate:.2%})"

        # 2. Partial TP 1 (+4%)
        if position.stage == 0 and profit_rate >= self.PARTIAL_TP1_RATE:
            sell_quantity = int(position.quantity * self.PARTIAL_SELL_RATIO_1)
            if sell_quantity > 0:
                # 매도 후 stage 업데이트
                new_stage = 1
                return sell_quantity, f"2단계: 1차 익절 ({profit_rate:.2%}, 40% 매도)", new_stage

        # 3. Partial TP 2 (+6%)
        if position.stage == 1 and profit_rate >= self.PARTIAL_TP2_RATE:
            sell_quantity = int(position.quantity * self.PARTIAL_SELL_RATIO_2)
            if sell_quantity > 0:
                # 매도 후 trailing 활성화
                new_stage = 2
                return sell_quantity, f"3단계: 2차 익절 ({profit_rate:.2%}, 40% 매도, Trailing 활성화)", new_stage

        # 4. ATR Trailing Stop (2차 익절 후)
        if position.stage >= 2:
            if position.should_exit_by_trailing():
                return position.remaining_quantity, f"4단계: Trailing Stop 도달 ({current_price:,.0f}원 <= {position.trailing_stop:,.0f}원)"

        # 5. EMA + Volume Breakdown (추세 전환 감지)
        if chart_data and len(chart_data) > 0:
            from analyzers.technical_analyzer import TechnicalAnalyzer
            analyzer = TechnicalAnalyzer()

            # DataFrame 준비
            df = analyzer.prepare_dataframe(chart_data)

            # EMA Breakdown 감지
            breakdown = analyzer.detect_ema_breakdown(df, ema_period=20)

            if breakdown['breakdown_detected']:
                confidence = breakdown['confidence']
                reason = breakdown['reason']

                # HIGH 신뢰도면 즉시 매도
                if confidence == 'HIGH':
                    return position.remaining_quantity, f"5단계: EMA Breakdown (HIGH) - {reason}"

                # MEDIUM 신뢰도 + 손실 상태면 매도
                elif confidence == 'MEDIUM' and profit_rate < 0:
                    return position.remaining_quantity, f"5단계: EMA Breakdown (MEDIUM) - {reason}"

        # 기본 손절가 체크 (5단계 실패시 백업)
        if current_price <= position.stop_loss:
            return position.remaining_quantity, f"손절가 도달 ({current_price:,.0f}원 <= {position.stop_loss:,.0f}원)"

        return None

    def process_exit_signal(
        self,
        position: Position,
        current_price: float,
        current_time: str = None,
        chart_data: list = None
    ) -> Optional[OrderResult]:
        """
        매도 신호 처리

        Args:
            position: 포지션
            current_price: 현재가
            current_time: 현재 시간
            chart_data: 차트 데이터 (EMA Breakdown 감지용)

        Returns:
            주문 결과 (매도 없으면 None)
        """
        exit_signal = self.check_exit_signals(position, current_price, current_time, chart_data)

        if exit_signal is None:
            return None

        # 튜플 언패킹
        if len(exit_signal) == 3:
            sell_quantity, reason, new_stage = exit_signal
        else:
            sell_quantity, reason = exit_signal
            new_stage = position.stage

        # 매도 실행
        result = self.execute_sell(
            stock_code=position.stock_code,
            quantity=sell_quantity,
            price=current_price,
            reason=reason
        )

        # 성공시 포지션 업데이트
        if result.success:
            self.position_manager.update_stage(
                stock_code=position.stock_code,
                new_stage=new_stage,
                sold_quantity=sell_quantity
            )

        return result

    def get_account_balance(self) -> float:
        """
        계좌 잔고 조회

        Returns:
            현재 잔고
        """
        # TODO: 실제 Kiwoom API 계좌 조회
        # result = self.api.get_account_balance(self.account_no)
        # return result['balance']

        # 시뮬레이션용
        return 10000000  # 1천만원
