"""
주문 실행 및 리스크 관리

매수/매도 주문 실행, 부분 청산, 리스크 관리
"""
from typing import Dict, Optional, Tuple
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import box

from kiwoom_api import KiwoomAPI
from exceptions import (
    handle_api_errors,
    handle_trading_errors,
    OrderFailedError,
    InsufficientFundsError
)
from config.config_manager import ConfigManager
from core.risk_manager import RiskManager
from database.trading_db import TradingDatabase

console = Console()


class OrderExecutor:
    """주문 실행 및 리스크 관리 담당"""

    def __init__(
        self,
        api: KiwoomAPI,
        config: ConfigManager,
        risk_manager: RiskManager,
        db: Optional[TradingDatabase] = None
    ):
        """
        Args:
            api: KiwoomAPI 인스턴스
            config: ConfigManager 인스턴스
            risk_manager: RiskManager 인스턴스
            db: TradingDatabase 인스턴스 (optional)
        """
        self.api = api
        self.config = config
        self.risk_manager = risk_manager
        self.db = db

    @handle_trading_errors(notify_user=True, log_errors=True)
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    def execute_buy(
        self,
        stock_code: str,
        stock_name: str,
        current_price: float,
        current_cash: float,
        positions_value: float,
        position_count: int,
        stock_info: Optional[Dict] = None,
        entry_confidence: float = 1.0  # 🔧 FIX: 진입 신뢰도 파라미터 추가 (문서 명세)
    ) -> Optional[Dict]:
        """
        매수 주문 실행 (리스크 관리 포함)

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            current_price: 현재가
            current_cash: 현재 예수금
            positions_value: 보유 포지션 평가액
            position_count: 보유 포지션 개수
            stock_info: 종목 정보 (백테스트 stats 포함, optional)
            entry_confidence: 진입 신뢰도 (0.0~1.0, STRONG_BUY=1.0, BUY=0.7)

        Returns:
            포지션 정보 dict (실패 시 None)
            {
                'stock_code': str,
                'stock_name': str,
                'entry_price': float,
                'quantity': int,
                'entry_time': datetime,
                'highest_price': float,
                'trailing_active': bool,
                'order_no': str,
                'trade_id': int  # DB ID
            }
        """
        console.print()
        console.print("=" * 80, style="green")
        console.print(f"🔔 매수 신호 발생: {stock_name} ({stock_code})", style="bold green")
        console.print(f"   가격: {current_price:,.0f}원")
        console.print(f"   시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 손절가 계산 (임시로 -3%)
        stop_loss_price = current_price * 0.97

        # 🔧 FIX: 포지션 크기 계산 (진입 신뢰도 반영)
        # 문서 명세: STRONG_BUY=1.0, BUY=0.7
        position_calc = self.risk_manager.calculate_position_size(
            current_balance=current_cash,
            current_price=current_price,
            stop_loss_price=stop_loss_price,
            entry_confidence=entry_confidence
        )

        quantity = position_calc['quantity']
        amount = position_calc['investment']

        # 진입 가능 여부 확인
        can_enter, reason = self.risk_manager.can_open_position(
            current_balance=current_cash,
            current_positions_value=positions_value,
            position_count=position_count,
            position_size=amount
        )

        if not can_enter:
            console.print(f"[yellow]⚠️  매수 불가: {reason}[/yellow]")
            console.print("=" * 80, style="yellow")
            return None

        console.print(f"[dim]📊 포지션 계산:[/dim]")
        console.print(f"[dim]   - 투자금액: {amount:,.0f}원 (리스크: {position_calc['risk_amount']:,.0f}원)[/dim]")
        console.print(f"[dim]   - 매수수량: {quantity}주[/dim]")
        console.print(f"[dim]   - 포지션비율: {position_calc['position_ratio']:.1f}%[/dim]")

        # 실제 키움 API 매수 주문
        try:
            console.print(f"[yellow]📡 키움 API 매수 주문 전송 중...[/yellow]")
            order_result = self.api.order_buy(
                stock_code=stock_code,
                quantity=quantity,
                price=int(current_price),
                trade_type="0"  # 지정가 주문
            )

            if order_result.get('return_code') != 0:
                raise OrderFailedError(
                    f"매수 주문 실패: {order_result.get('return_msg')}",
                    order_type='BUY',
                    stock_code=stock_code,
                    details=order_result
                )

            order_no = order_result.get('ord_no')
            console.print(f"[green]✓ 매수 주문 성공 - 주문번호: {order_no}[/green]")

        except InsufficientFundsError as e:
            console.print(f"[red]❌ 잔고 부족: {e}[/red]")
            console.print("=" * 80, style="red")
            return None
        except OrderFailedError as e:
            console.print(f"[red]❌ 매수 주문 실패: {e}[/red]")
            console.print("=" * 80, style="red")
            return None

        # 포지션 생성
        entry_time = datetime.now()
        position = {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'name': stock_name,  # 하위 호환성
            'avg_price': current_price,
            'entry_price': current_price,
            'entry_time': entry_time,
            'entry_date': entry_time,  # 보유일 계산용
            'quantity': quantity,
            'initial_quantity': quantity,  # 초기 수량 (부분 청산 추적용)
            'current_price': current_price,
            'highest_price': current_price,
            'trailing_active': False,
            'trade_id': None,  # DB trade_id 저장용
            'partial_exit_stage': 0,  # 부분 청산 단계 (0: 미진행, 1: 1차 완료, 2: 2차 완료)
            'total_realized_profit': 0.0,  # 누적 실현 손익
            'order_no': order_no
        }

        # DB에 매수 거래 저장
        if self.db and stock_info:
            stats = stock_info.get('stats', {})
            analysis = stock_info.get('analysis', {})
            scores = analysis.get('scores', {})

            # ========== 진입 컨텍스트 수집 (ML 학습용) ==========
            entry_context = {
                # 가격 정보
                'price': current_price,
                'vwap': stock_info.get('vwap'),
                'vwap_diff_pct': stock_info.get('vwap_diff_pct'),

                # 이동평균
                'ma5': stock_info.get('ma5'),
                'ma20': stock_info.get('ma20'),
                'ma60': stock_info.get('ma60'),

                # 기술적 지표
                'rsi14': stock_info.get('rsi14'),
                'williams_r': stock_info.get('williams_r'),
                'macd': stock_info.get('macd'),
                'macd_signal': stock_info.get('macd_signal'),
                'stoch_k': stock_info.get('stoch_k'),
                'stoch_d': stock_info.get('stoch_d'),

                # 거래량
                'volume': stock_info.get('volume'),
                'volume_ma20': stock_info.get('volume_ma20'),
                'volume_ratio': stock_info.get('volume_ratio'),  # 평균 대비 배수

                # 캔들 정보
                'candle': {
                    'open': stock_info.get('open'),
                    'high': stock_info.get('high'),
                    'low': stock_info.get('low'),
                    'close': stock_info.get('close'),
                },

                # ATR & 변동성
                'atr': stock_info.get('atr'),
                'atr_pct': stock_info.get('atr_pct'),

                # 시장 컨텍스트 (가능하면)
                'market_kospi_change': stock_info.get('market_kospi_change'),
                'market_kosdaq_change': stock_info.get('market_kosdaq_change'),

                # 메타 정보
                'entry_time': entry_time.isoformat(),
                'condition_name': 'VWAP+AI',
            }

            # 필터 점수 (통과한 필터 정보)
            filter_scores = {
                'vwap_breakout': stock_info.get('vwap_breakout', True),
                'trend_filter': stock_info.get('trend_filter_pass', True),
                'volume_filter': stock_info.get('volume_filter_pass', True),
                'williams_r_filter': stock_info.get('williams_r_filter_pass', True),
                'volume_multiplier_value': stock_info.get('volume_ratio'),
                'williams_r_value': stock_info.get('williams_r'),
            }

            import json
            trade_data = {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'trade_type': 'BUY',
                'trade_time': entry_time.isoformat(),
                'price': current_price,
                'quantity': quantity,
                'amount': amount,
                'condition_name': 'VWAP+AI',
                'strategy_config': 'hybrid',
                'entry_reason': f"VWAP 상향 돌파 (종합점수: {analysis.get('total_score', 0):.1f})",

                # VWAP 백테스트 결과
                'vwap_validation_score': stats.get('avg_profit_pct', 0),
                'sim_win_rate': stats.get('win_rate'),
                'sim_avg_profit': stats.get('avg_profit_pct'),
                'sim_max_profit': stats.get('max_profit_pct'),
                'sim_max_loss': stats.get('max_loss_pct'),
                'sim_total_trades': stats.get('total_trades'),

                # AI 점수
                'ai_total_score': analysis.get('total_score'),
                'ai_score_news': scores.get('news'),
                'ai_score_technical': scores.get('technical'),
                'ai_score_supply_demand': scores.get('supply_demand'),
                'ai_score_fundamental': scores.get('fundamental'),
                'ai_score_vwap': scores.get('vwap'),

                # 뉴스 분석
                'news_sentiment': analysis.get('news_sentiment'),
                'news_impact': analysis.get('news_impact'),

                # ========== 컨텍스트 (신규) ==========
                'entry_context': json.dumps(entry_context, ensure_ascii=False),
                'filter_scores': json.dumps(filter_scores, ensure_ascii=False),
            }

            try:
                trade_id = self.db.insert_trade(trade_data)
                position['trade_id'] = trade_id
                console.print(f"[dim]   ✓ DB 저장 완료 (trade_id: {trade_id})[/dim]")
            except Exception as e:
                console.print(f"[yellow]⚠️  DB 저장 실패: {e}[/yellow]")

        # 리스크 관리자에 거래 기록
        self.risk_manager.record_trade(
            stock_code=stock_code,
            stock_name=stock_name,
            trade_type='BUY',
            quantity=quantity,
            price=current_price,
            realized_pnl=0
        )

        console.print(f"✅ 매수 완료 (주문번호: {order_no})")
        console.print("=" * 80, style="green")
        console.print()

        return position

    @handle_trading_errors(notify_user=True, log_errors=True)
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    def execute_sell(
        self,
        stock_code: str,
        position: Dict,
        current_price: float,
        profit_pct: float,
        reason: str,
        current_indicators: Optional[Dict] = None
    ) -> bool:
        """
        매도 주문 실행 (전량 청산)

        Args:
            stock_code: 종목 코드
            position: 포지션 정보 dict
            current_price: 현재가
            profit_pct: 수익률
            reason: 청산 이유

        Returns:
            성공 여부
        """
        # entry_time이 없을 수 있으므로 안전하게 처리
        entry_time = position.get('entry_time') or position.get('entry_date')
        if entry_time:
            holding_duration = (datetime.now() - entry_time).seconds
        else:
            holding_duration = 0

        realized_profit = (current_price - position['entry_price']) * position['quantity']

        console.print()
        console.print("=" * 80, style="red")
        console.print(f"🔔 매도 신호 발생: {position['name']} ({stock_code})", style="bold red")
        console.print(f"   매수가: {position['entry_price']:,.0f}원")
        console.print(f"   매도가: {current_price:,.0f}원")
        console.print(f"   수익률: {profit_pct:+.2f}%")
        console.print(f"   실현손익: {realized_profit:+,.0f}원")
        console.print(f"   사유: {reason}")
        console.print(f"   보유시간: {holding_duration // 60}분")

        # DB에 매도 정보 저장
        if self.db:
            trade_id = position.get('trade_id')
            if trade_id:
                # ========== 청산 컨텍스트 수집 (ML 학습용) ==========
                import json
                exit_time = datetime.now()
                highest_price = position.get('highest_price', current_price)
                highest_profit_pct = ((highest_price - position['entry_price']) / position['entry_price'] * 100)

                exit_context = {
                    # 가격 정보
                    'price': current_price,
                    'entry_price': position['entry_price'],
                    'highest_price': highest_price,
                    'highest_profit_pct': highest_profit_pct,
                    'profit_pct': profit_pct,
                    'profit_preservation_pct': (profit_pct / highest_profit_pct * 100) if highest_profit_pct > 0 else 0,

                    # 트레일링 정보
                    'trailing_activated': position.get('trailing_active', False),
                    'trailing_activation_price': position.get('trailing_activation_price'),  # 활성화 시점 가격

                    # 부분 청산 정보
                    'partial_exit_stage': position.get('partial_exit_stage', 0),
                    'total_realized_profit': position.get('total_realized_profit', 0.0),
                    'initial_quantity': position.get('initial_quantity', position['quantity']),
                    'remaining_quantity': position['quantity'],

                    # 기술적 지표 (current_indicators가 있으면)
                    'rsi14': current_indicators.get('rsi14') if current_indicators else None,
                    'williams_r': current_indicators.get('williams_r') if current_indicators else None,
                    'volume_ratio': current_indicators.get('volume_ratio') if current_indicators else None,
                    'vwap': current_indicators.get('vwap') if current_indicators else None,
                    'vwap_diff_pct': current_indicators.get('vwap_diff_pct') if current_indicators else None,
                    'macd': current_indicators.get('macd') if current_indicators else None,

                    # 청산 메타 정보
                    'exit_time': exit_time.isoformat(),
                    'reason': reason,
                    'holding_duration_seconds': holding_duration,
                    'holding_duration_minutes': holding_duration // 60,
                }

                sell_trade = {
                    'stock_code': stock_code,
                    'stock_name': position['name'],
                    'trade_type': 'SELL',
                    'trade_time': exit_time.isoformat(),
                    'price': float(current_price),
                    'quantity': int(position['quantity']),
                    'amount': float(current_price * position['quantity']),
                    'exit_reason': reason,
                    'realized_profit': float(realized_profit),
                    'profit_rate': float(profit_pct),
                    'holding_duration': int(holding_duration),
                    'exit_context': json.dumps(exit_context, ensure_ascii=False)
                }
                try:
                    self.db.insert_trade(sell_trade)
                    console.print(f"[dim]   ✓ DB 저장 완료 (exit_context 포함)[/dim]")
                except Exception as e:
                    console.print(f"[yellow]⚠️  DB 저장 실패: {e}[/yellow]")

        # 실제 키움 API 매도 주문
        try:
            console.print(f"[yellow]📡 키움 API 매도 주문 전송 중...[/yellow]")
            order_result = self.api.order_sell(
                stock_code=stock_code,
                quantity=position['quantity'],
                price=0,  # 시장가 매도
                trade_type="3"  # 시장가
            )

            if order_result.get('return_code') != 0:
                raise OrderFailedError(
                    f"매도 주문 실패: {order_result.get('return_msg')}",
                    order_type='SELL',
                    stock_code=stock_code,
                    details=order_result
                )

            order_no = order_result.get('ord_no')
            console.print(f"[green]✓ 매도 주문 성공 - 주문번호: {order_no}[/green]")

        except OrderFailedError as e:
            console.print(f"[red]❌ 매도 주문 실패: {e}[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            console.print("=" * 80, style="yellow")
            return False

        # 리스크 관리자에 거래 기록
        self.risk_manager.record_trade(
            stock_code=stock_code,
            stock_name=position['name'],
            trade_type='SELL',
            quantity=position['quantity'],
            price=current_price,
            realized_pnl=realized_profit
        )

        console.print(f"✅ 매도 완료 (주문번호: {order_no})")
        console.print("=" * 80, style="red")
        console.print()

        return True

    @handle_trading_errors(notify_user=True, log_errors=True)
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    def execute_partial_sell(
        self,
        stock_code: str,
        position: Dict,
        current_price: float,
        profit_pct: float,
        exit_ratio: float,
        stage: int
    ) -> bool:
        """
        부분 청산 실행

        Args:
            stock_code: 종목 코드
            position: 포지션 정보 dict
            current_price: 현재가
            profit_pct: 수익률
            exit_ratio: 청산 비율 (0.0~1.0)
            stage: 청산 단계 (1, 2)

        Returns:
            성공 여부
        """
        # 이미 해당 단계 청산 완료되었는지 확인
        if position.get('partial_exit_stage', 0) >= stage:
            return False

        # 청산할 수량 계산 (초기 수량 대비)
        initial_quantity = position.get('initial_quantity', position['quantity'])
        partial_quantity = int(initial_quantity * exit_ratio)

        # 최소 1주는 청산해야 함
        if partial_quantity < 1:
            return False

        # 현재 보유 수량보다 많이 팔 수 없음
        if partial_quantity > position['quantity']:
            partial_quantity = position['quantity']

        # 실현 손익 계산
        realized_profit = (current_price - position['entry_price']) * partial_quantity

        console.print()
        console.print("=" * 80, style="yellow")
        console.print(f"🎯 부분 청산 {stage}단계: {position['name']} ({stock_code})", style="bold yellow")
        console.print(f"   매수가: {position['entry_price']:,.0f}원")
        console.print(f"   매도가: {current_price:,.0f}원")
        console.print(f"   수익률: {profit_pct:+.2f}%")
        console.print(f"   청산비율: {exit_ratio*100:.0f}% ({partial_quantity}/{initial_quantity}주)")
        console.print(f"   실현손익: {realized_profit:+,.0f}원")
        console.print(f"   남은수량: {position['quantity'] - partial_quantity}주")

        # DB에 부분 매도 거래 저장
        if self.db:
            trade_id = position.get('trade_id')
            if trade_id:
                entry_time = position.get('entry_time') or position.get('entry_date')
                holding_duration = (datetime.now() - entry_time).seconds if entry_time else 0

                partial_sell_trade = {
                    'stock_code': stock_code,
                    'stock_name': position['name'],
                    'trade_type': 'SELL',
                    'trade_time': datetime.now().isoformat(),
                    'price': current_price,
                    'quantity': partial_quantity,
                    'amount': current_price * partial_quantity,
                    'exit_reason': f'부분청산 {stage}단계 (+{profit_pct:.1f}%)',
                    'realized_profit': realized_profit,
                    'profit_rate': profit_pct,
                    'holding_duration': holding_duration
                }
                try:
                    self.db.insert_trade(partial_sell_trade)
                    console.print(f"[dim]   ✓ DB 저장 완료[/dim]")
                except Exception as e:
                    console.print(f"[yellow]⚠️  DB 저장 실패: {e}[/yellow]")

        # 실제 키움 API 부분 매도 주문
        try:
            console.print(f"[yellow]📡 키움 API 부분 매도 주문 전송 중...[/yellow]")
            order_result = self.api.order_sell(
                stock_code=stock_code,
                quantity=partial_quantity,
                price=0,  # 시장가 매도
                trade_type="3"  # 시장가
            )

            if order_result.get('return_code') != 0:
                raise OrderFailedError(
                    f"부분 매도 주문 실패: {order_result.get('return_msg')}",
                    order_type='SELL',
                    stock_code=stock_code,
                    details=order_result
                )

            order_no = order_result.get('ord_no')
            console.print(f"[green]✓ 부분 매도 주문 성공 - 주문번호: {order_no}[/green]")

        except OrderFailedError as e:
            console.print(f"[red]❌ 부분 매도 주문 실패: {e}[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            console.print("=" * 80, style="yellow")
            return False

        # 리스크 관리자에 거래 기록
        self.risk_manager.record_trade(
            stock_code=stock_code,
            stock_name=position['name'],
            trade_type='SELL',
            quantity=partial_quantity,
            price=current_price,
            realized_pnl=realized_profit
        )

        # 포지션 업데이트
        position['quantity'] -= partial_quantity
        position['partial_exit_stage'] = stage
        position['total_realized_profit'] += realized_profit

        console.print(f"✅ 부분 청산 완료 (주문번호: {order_no})")
        console.print("=" * 80, style="yellow")
        console.print()

        return True

    def get_order_summary(self, positions: Dict[str, Dict]) -> Table:
        """
        보유 포지션 요약 테이블 생성

        Args:
            positions: 포지션 dict {stock_code: position_info}

        Returns:
            Rich Table 객체
        """
        table = Table(
            title="💼 보유 포지션 현황",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta"
        )

        table.add_column("종목코드", style="cyan", width=8)
        table.add_column("종목명", style="white", width=12)
        table.add_column("수량", justify="right", width=8)
        table.add_column("매수가", justify="right", width=10)
        table.add_column("현재가", justify="right", width=10)
        table.add_column("수익률", justify="right", width=10)
        table.add_column("평가손익", justify="right", width=12)
        table.add_column("청산단계", justify="center", width=8)

        total_invested = 0.0
        total_value = 0.0

        for stock_code, position in positions.items():
            quantity = position.get('quantity', 0)
            entry_price = position.get('entry_price', 0)
            current_price = position.get('current_price', entry_price)

            invested = entry_price * quantity
            value = current_price * quantity
            profit = value - invested
            profit_pct = (profit / invested * 100) if invested > 0 else 0

            total_invested += invested
            total_value += value

            # 청산 단계 표시
            stage = position.get('partial_exit_stage', 0)
            stage_str = f"{stage}/2" if stage > 0 else "-"

            # 수익률에 따른 색상
            profit_color = "green" if profit_pct >= 0 else "red"

            table.add_row(
                stock_code,
                position.get('name', stock_code),
                f"{quantity}주",
                f"{entry_price:,.0f}원",
                f"{current_price:,.0f}원",
                f"[{profit_color}]{profit_pct:+.2f}%[/{profit_color}]",
                f"[{profit_color}]{profit:+,.0f}원[/{profit_color}]",
                stage_str
            )

        # 합계 행
        if positions:
            total_profit = total_value - total_invested
            total_profit_pct = (total_profit / total_invested * 100) if total_invested > 0 else 0
            profit_color = "green" if total_profit >= 0 else "red"

            table.add_row(
                "",
                "합계",
                f"{len(positions)}개",
                f"{total_invested:,.0f}원",
                f"{total_value:,.0f}원",
                f"[bold {profit_color}]{total_profit_pct:+.2f}%[/bold {profit_color}]",
                f"[bold {profit_color}]{total_profit:+,.0f}원[/bold {profit_color}]",
                "",
                style="bold"
            )

        return table
