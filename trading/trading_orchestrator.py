"""
거래 시스템 오케스트레이터

모든 모듈을 조율하여 전체 자동 매매 시스템 운영
"""
from typing import Dict, Set, Optional
from datetime import datetime
import asyncio
from rich.console import Console

from kiwoom_api import KiwoomAPI
from config.config_manager import ConfigManager
from core.risk_manager import RiskManager
from database.trading_db_v2 import TradingDatabaseV2 as TradingDatabase
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.pre_trade_validator import PreTradeValidator

from trading.websocket_client import KiwoomWebSocketClient
from trading.position_tracker import PositionTracker
from trading.account_manager import AccountManager
from trading.signal_detector import SignalDetector
from trading.order_executor import OrderExecutor
from trading.market_monitor import MarketMonitor
from trading.condition_scanner import ConditionScanner

console = Console()


class TradingOrchestrator:
    """
    전체 트레이딩 시스템 오케스트레이터

    모든 모듈을 조율하여 자동 매매 시스템을 운영합니다.
    """

    def __init__(
        self,
        api: KiwoomAPI,
        config: ConfigManager,
        risk_manager: RiskManager,
        validator: PreTradeValidator,
        analyzer: EntryTimingAnalyzer,
        db: Optional[TradingDatabase] = None
    ):
        """
        Args:
            api: KiwoomAPI 인스턴스
            config: ConfigManager 인스턴스
            risk_manager: RiskManager 인스턴스
            validator: PreTradeValidator 인스턴스
            analyzer: EntryTimingAnalyzer 인스턴스
            db: TradingDatabase 인스턴스 (optional)
        """
        self.api = api
        self.config = config
        self.risk_manager = risk_manager
        self.validator = validator
        self.analyzer = analyzer
        self.db = db

        # 모듈 초기화
        self.position_tracker = PositionTracker()
        self.account_manager = AccountManager(api)
        self.signal_detector = SignalDetector(config, analyzer)
        self.order_executor = OrderExecutor(api, config, risk_manager, db)
        self.market_monitor = MarketMonitor(api)
        self.condition_scanner = ConditionScanner(api, validator, db)

        # 감시 종목 및 검증된 종목 정보
        self.watchlist: Set[str] = set()
        self.validated_stocks: Dict[str, Dict] = {}

        # 시스템 상태
        self.running = False

    async def initialize(self) -> bool:
        """
        시스템 초기화

        Returns:
            초기화 성공 여부
        """
        console.print()
        console.print("=" * 120, style="bold cyan")
        console.print(f"{'🚀 자동 매매 시스템 초기화':^120}", style="bold cyan")
        console.print("=" * 120, style="bold cyan")
        console.print()

        # 1. 계좌 정보 초기화
        account_ok = await self.account_manager.initialize()
        if not account_ok:
            console.print("[yellow]⚠️  계좌 정보 초기화 실패 (기본값으로 진행)[/yellow]")

        # 2. 보유 종목을 포지션 트래커에 로드
        holdings = self.account_manager.get_all_holdings()
        for holding in holdings:
            stock_code = holding.get('stock_code')
            if stock_code:
                self.position_tracker.add_position(
                    stock_code=stock_code,
                    stock_name=holding.get('stock_name', stock_code),
                    entry_price=holding.get('avg_price', 0),
                    quantity=holding.get('quantity', 0),
                    entry_time=holding.get('entry_date', datetime.now())
                )

        console.print()
        console.print(f"[green]✅ 시스템 초기화 완료[/green]")
        console.print()

        return True

    async def run_condition_filtering(self, condition_name: str = "VWAP돌파") -> None:
        """
        조건검색 + VWAP 필터링 실행

        Args:
            condition_name: 조건식 이름
        """
        console.print()
        console.print("=" * 120, style="bold magenta")
        console.print(f"{'📊 조건검색 + VWAP 필터링':^120}", style="bold magenta")
        console.print("=" * 120, style="bold magenta")
        console.print()

        # 1. 조건식 검색
        stock_list = self.condition_scanner.run_condition_search(condition_name)

        if not stock_list:
            console.print("[yellow]⚠️  조건검색 결과가 없습니다.[/yellow]")

            # DB에서 활성 종목 로드 시도
            console.print("[cyan]💡 DB에서 활성 종목 로드를 시도합니다...[/cyan]")
            self.validated_stocks = self.condition_scanner.load_candidates_from_db(limit=100)
            self.watchlist = set(self.validated_stocks.keys())

            if self.watchlist:
                console.print(f"[green]✅ DB에서 {len(self.watchlist)}개 종목 로드 완료[/green]")
            else:
                console.print("[yellow]⚠️  감시 종목이 없습니다.[/yellow]")

            return

        # 2. VWAP 백테스트 필터링
        vwap_config = self.config.get_section('vwap_validation')
        min_win_rate = vwap_config.get('min_win_rate', 40.0)
        min_avg_profit = vwap_config.get('min_avg_profit_pct', 1.0)

        self.validated_stocks = self.condition_scanner.filter_with_vwap(
            stock_list,
            min_win_rate=min_win_rate,
            min_avg_profit=min_avg_profit
        )

        # 3. watchlist 업데이트
        self.watchlist = set(self.validated_stocks.keys())

        # 4. 결과 표시
        self.condition_scanner.display_filtered_stocks(self.validated_stocks)

        console.print(f"[green]✅ 최종 선정: {len(self.watchlist)}개 종목[/green]")
        console.print()

    async def monitor_and_trade(self) -> None:
        """
        실시간 모니터링 및 매매 루프
        """
        console.print()
        console.print("=" * 120, style="bold magenta")
        console.print(f"{'🎯 실시간 모니터링 시작':^120}", style="bold magenta")
        console.print("=" * 120, style="bold magenta")
        console.print()

        console.print(f"🎯 초기 모니터링 대상: {len(self.watchlist)}개 종목")
        console.print(f"⏰ 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"🔄 5분마다 조건검색 재실행 → 새 종목 자동 추가")
        console.print()

        if len(self.watchlist) == 0:
            console.print("[yellow]⚠️  감시 종목이 없습니다![/yellow]")
            console.print("[yellow]   조건검색을 먼저 실행하거나 장 시작 후 자동 실행을 기다리세요.[/yellow]")
            console.print()

        # 모니터링 간격 설정
        check_interval = 60  # 1분마다 종목 체크
        rescan_interval = 300  # 5분마다 조건검색 재실행

        last_check = datetime.now()
        last_rescan = datetime.now()

        self.running = True

        try:
            while self.running:
                current_time = datetime.now()

                # 장 시간인지 체크
                if self.market_monitor.is_market_open():
                    # 5분마다 조건검색 재실행
                    if (current_time - last_rescan).seconds >= rescan_interval:
                        console.print()
                        console.print("[cyan]🔄 5분 경과 - 조건검색 재실행 중...[/cyan]")
                        await self.run_condition_filtering()
                        last_rescan = current_time
                        console.print(f"[green]✅ 현재 모니터링 종목: {len(self.watchlist)}개[/green]")
                        console.print()

                    # 1분마다 종목 체크
                    elif (current_time - last_check).seconds >= check_interval:
                        await self._check_all_stocks()
                        last_check = current_time
                    else:
                        # 남은 시간 카운트다운
                        elapsed = (current_time - last_check).seconds
                        remaining = check_interval - elapsed

                        rescan_elapsed = (current_time - last_rescan).seconds
                        rescan_remaining = rescan_interval - rescan_elapsed
                        rescan_min = rescan_remaining // 60
                        rescan_sec = rescan_remaining % 60

                        import sys
                        sys.stdout.write(f"\r다음 체크: {remaining}초 후 | 다음 재검색: {rescan_min}분 {rescan_sec}초 후 | Ctrl+C: 종료   ")
                        sys.stdout.flush()
                else:
                    # 장 시간이 아니면 대기
                    import sys
                    sys.stdout.write(f"\r💤 장중 아님 | 대기 중... ({current_time.strftime('%H:%M:%S')})   ")
                    sys.stdout.flush()

                await asyncio.sleep(1)

        except KeyboardInterrupt:
            print()
            self.shutdown()

    async def _check_all_stocks(self) -> None:
        """모든 종목 체크 (매수/매도 신호 감지)"""
        # 1. 종목 데이터 수집
        positions_dict = {
            pos.stock_code: {
                'name': pos.stock_name,
                'entry_price': pos.entry_price,
                'quantity': pos.quantity,
                'highest_price': pos.current_price,
                'trailing_active': False,
                'partial_exit_stage': 0
            }
            for pos in self.position_tracker.get_active_positions()
        }

        stock_data_list = self.market_monitor.monitor_stocks(
            self.watchlist,
            self.validated_stocks,
            positions_dict
        )

        # 2. 모니터링 상태 표시
        self.market_monitor.display_monitoring_status(stock_data_list, positions_dict)

        # 3. 각 종목별 신호 체크
        for stock_data in stock_data_list:
            stock_code = stock_data['code']
            stock_name = stock_data['name']
            df = stock_data.get('dataframe')

            if not stock_data.get('data_available') or df is None:
                continue

            # 보유 종목: 매도 신호 체크
            if stock_data.get('is_holding'):
                await self._check_exit_signal(stock_code, stock_name, df)
            # 미보유 종목: 매수 신호 체크
            else:
                await self._check_entry_signal(stock_code, stock_name, df)

    async def _check_entry_signal(self, stock_code: str, stock_name: str, df) -> None:
        """매수 신호 체크 및 실행"""
        signal = self.signal_detector.check_entry_signal(stock_code, stock_name, df)

        if signal:
            # 사전 검증 재확인
            stock_info = self.validated_stocks.get(stock_code)
            validation = self.validator.validate_stock(stock_code, stock_name)

            if validation.get('allowed'):
                console.print(f"[green]   ✅ 검증 통과 - 매수 실행[/green]")

                # 매수 주문 실행
                position = self.order_executor.execute_buy(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    current_price=signal['current_price'],
                    current_cash=self.account_manager.get_available_cash(),
                    positions_value=self.account_manager.get_positions_value(),
                    position_count=self.position_tracker.get_position_count(),
                    stock_info=stock_info
                )

                if position:
                    # 포지션 트래커에 추가
                    self.position_tracker.add_position(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        entry_price=position['entry_price'],
                        quantity=position['quantity'],
                        entry_time=position['entry_time']
                    )

                    # 계좌 잔고 업데이트
                    await self.account_manager.update_balance()
            else:
                console.print(f"[red]   ❌ 검증 실패: {validation.get('reason', 'Unknown')}[/red]")

    async def _check_exit_signal(self, stock_code: str, stock_name: str, df) -> None:
        """매도 신호 체크 및 실행"""
        # ✅ 포지션 존재 확인 (중복 방지)
        position_obj = self.position_tracker.get_position(stock_code)
        if not position_obj:
            console.print(f"[dim]   {stock_code}: 포지션 없음 (이미 청산)[/dim]")
            return

        # ✅ 이미 매도 중인지 확인 (중복 방지)
        if hasattr(position_obj, 'is_selling') and position_obj.is_selling:
            console.print(f"[dim]   {stock_code}: 매도 처리 중... (중복 방지)[/dim]")
            return

        position_dict = {
            'entry_price': position_obj.entry_price,
            'quantity': position_obj.quantity,
            'highest_price': position_obj.current_price,
            'trailing_active': getattr(position_obj, 'trailing_active', False),
            'partial_exit_stage': getattr(position_obj, 'partial_exit_stage', 0),
            'name': position_obj.stock_name
        }

        signal = self.signal_detector.check_exit_signal(
            stock_code,
            stock_name,
            position_dict,
            df
        )

        if signal:
            # ✅ 매도 플래그 설정 (중복 방지)
            position_obj.is_selling = True

            try:
                # 부분 청산
                if signal['exit_type'] == 'partial':
                    success = self.order_executor.execute_partial_sell(
                        stock_code=stock_code,
                        position=position_dict,
                        current_price=signal['current_price'],
                        profit_pct=signal['profit_pct'],
                        exit_ratio=signal['exit_ratio'],
                        stage=signal['stage']
                    )

                    if success:
                        # 포지션 수량 업데이트
                        position_obj.record_partial_sell(
                            stage=signal['stage'],
                            quantity=int(position_dict['quantity'] * signal['exit_ratio']),
                            price=signal['current_price']
                        )
                        # ✅ 부분 청산 완료 후 플래그 해제
                        position_obj.is_selling = False
                    else:
                        # ✅ 매도 실패 시 플래그 해제
                        position_obj.is_selling = False

                # 전량 청산
                elif signal['exit_type'] == 'full':
                    success = self.order_executor.execute_sell(
                        stock_code=stock_code,
                        position=position_dict,
                        current_price=signal['current_price'],
                        profit_pct=signal['profit_pct'],
                        reason=signal['reason']
                    )

                    if success:
                        # ✅ 포지션 즉시 제거 (중복 방지)
                        self.position_tracker.remove_position(stock_code)
                        console.print(f"[green]   ✅ {stock_code}: 포지션 제거 완료[/green]")
                    else:
                        # ✅ 매도 실패 시 플래그 해제
                        position_obj.is_selling = False

                # 잔고 업데이트
                await self.account_manager.update_balance()

            except Exception as e:
                # ✅ 예외 발생 시 플래그 해제
                console.print(f"[red]❌ {stock_code} 매도 실행 중 오류: {e}[/red]")
                position_obj.is_selling = False
                raise

    def shutdown(self) -> None:
        """시스템 종료"""
        self.running = False

        console.print()
        console.print("[yellow]⚠️  종료 신호 감지... 안전하게 종료합니다.[/yellow]")
        console.print()

        # 미청산 포지션 표시
        active_positions = self.position_tracker.get_active_positions()
        if active_positions:
            console.print(f"[yellow]⚠️  미청산 포지션: {len(active_positions)}개[/yellow]")

            for pos in active_positions:
                console.print(f"  - {pos.stock_name} ({pos.stock_code}): {pos.entry_price:,.0f}원에 매수")

            console.print()

        console.print("[green]✅ 자동 매매 종료 완료[/green]")
        console.print()

    def get_system_status(self) -> Dict:
        """
        시스템 상태 조회

        Returns:
            시스템 상태 dict
        """
        market_status = self.market_monitor.get_market_status()

        return {
            'running': self.running,
            'market_open': market_status['is_open'],
            'market_status': market_status['status_message'],
            'watchlist_count': len(self.watchlist),
            'position_count': self.position_tracker.get_position_count(),
            'total_invested': self.position_tracker.get_total_invested(),
            'total_value': self.position_tracker.get_total_value(),
            'total_profit': self.position_tracker.get_total_profit(),
            'available_cash': self.account_manager.get_available_cash(),
            'total_assets': self.account_manager.get_total_assets()
        }
