#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/core/scheduler.py

Real-time Trading Scheduler - 3분봉 기반 실시간 모니터링 및 자동 매매
"""

import asyncio
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from utils.logger import get_logger
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


class TradingScheduler:
    """실시간 매매 스케줄러"""
    
    def __init__(self, trading_system):
        self.trading_system = trading_system
        self.logger = get_logger("TradingScheduler")
        
        # 스케줄러 초기화
        self.scheduler = AsyncIOScheduler()
        
        # 매매 시간 설정
        self.market_open_time = time(9, 0)      # 09:00
        self.market_close_time = time(15, 30)   # 15:30
        self.pre_market_time = time(8, 30)      # 08:30 사전 분석
        
        # 모니터링 설정
        self.monitoring_interval = 3  # 3분 간격
        self.active_strategies = []
        self.monitored_stocks = {}
        
        # 상태 관리
        self.is_running = False
        self.is_market_hours = False
        self.last_analysis_time = None
        
        self.logger.info("✅ TradingScheduler 초기화 완료")
    
    async def start(self):
        """스케줄러 시작"""
        try:
            if self.is_running:
                self.logger.warning("⚠️ 스케줄러가 이미 실행 중입니다")
                return
            
            self.logger.info("🚀 실시간 매매 스케줄러 시작")
            
            # 스케줄 등록
            await self._register_schedules()
            
            # 스케줄러 시작
            self.scheduler.start()
            self.is_running = True
            
            # 현재 상태 확인 및 즉시 실행 여부 결정
            if self._is_market_hours():
                self.logger.info("📈 현재 장중 - 즉시 모니터링 시작")
                await self._start_real_time_monitoring()
            else:
                self.logger.info("🕐 장외 시간 - 다음 장 개장 대기 중")
            
            console.print(Panel(
                "[green]🚀 실시간 매매 스케줄러 시작됨[/green]\n"
                f"• 모니터링 간격: {self.monitoring_interval}분\n"
                f"• 장 시작: {self.market_open_time}\n"
                f"• 장 마감: {self.market_close_time}\n"
                f"• 현재 상태: {'[green]장중[/green]' if self._is_market_hours() else '[yellow]장외[/yellow]'}",
                title="📊 Trading Scheduler",
                border_style="green"
            ))
            
        except Exception as e:
            self.logger.error(f"❌ 스케줄러 시작 실패: {e}")
            raise
    
    async def stop(self):
        """스케줄러 중지"""
        try:
            if not self.is_running:
                self.logger.warning("⚠️ 스케줄러가 실행 중이 아닙니다")
                return
            
            self.logger.info("🛑 실시간 매매 스케줄러 중지")
            
            # 스케줄러 중지
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            self.is_market_hours = False
            
            console.print(Panel(
                "[red]🛑 실시간 매매 스케줄러 중지됨[/red]",
                title="📊 Trading Scheduler",
                border_style="red"
            ))
            
        except Exception as e:
            self.logger.error(f"❌ 스케줄러 중지 실패: {e}")
    
    async def _register_schedules(self):
        """스케줄 등록"""
        try:
            # 1. 장 개장 전 사전 분석 (08:30)
            self.scheduler.add_job(
                self._pre_market_analysis,
                CronTrigger(hour=8, minute=30, second=0),
                id='pre_market_analysis',
                name='장 개장 전 사전 분석',
                misfire_grace_time=60
            )
            
            # 2. 장 개장 시작 (09:00)
            self.scheduler.add_job(
                self._market_open,
                CronTrigger(hour=9, minute=0, second=0),
                id='market_open',
                name='장 개장 시작',
                misfire_grace_time=60
            )
            
            # 3. 실시간 모니터링 (3분마다, 장중만)
            self.scheduler.add_job(
                self._real_time_monitoring,
                IntervalTrigger(minutes=self.monitoring_interval),
                id='real_time_monitoring',
                name='실시간 3분봉 모니터링',
                misfire_grace_time=30
            )
            
            # 4. 전체 전략 순차 실행 (15:30)
            self.scheduler.add_job(
                self._run_all_strategies_sequential,
                CronTrigger(hour=15, minute=30, second=0),
                id='all_strategies_sequential',
                name='전체 전략 순차 실행',
                misfire_grace_time=180
            )

            # 5. 장 마감 처리 (15:45)
            self.scheduler.add_job(
                self._market_close,
                CronTrigger(hour=15, minute=45, second=0),
                id='market_close',
                name='장 마감 처리',
                misfire_grace_time=60
            )

            # 6. 일일 정산 (16:00)
            self.scheduler.add_job(
                self._daily_settlement,
                CronTrigger(hour=16, minute=0, second=0),
                id='daily_settlement',
                name='일일 정산 및 리포트',
                misfire_grace_time=300
            )

            self.logger.info("✅ 모든 스케줄 등록 완료")
            
        except Exception as e:
            self.logger.error(f"❌ 스케줄 등록 실패: {e}")
            raise
    
    async def _pre_market_analysis(self):
        """장 개장 전 사전 분석 (08:30)"""
        try:
            self.logger.info("🌅 장 개장 전 사전 분석 시작")
            
            # 활성 전략 확인
            available_strategies = list(self.trading_system.strategies.keys())
            
            console.print(Panel(
                f"[blue]🌅 장 개장 전 사전 분석[/blue]\n"
                f"• 사용 가능한 전략: {len(available_strategies)}개\n"
                f"• 전략 목록: {', '.join(available_strategies)}",
                title="📊 Pre-Market Analysis",
                border_style="blue"
            ))
            
            # 기본 전략으로 사전 분석 실행
            default_strategy = "momentum"  # 기본값
            if default_strategy in available_strategies:
                self.logger.info(f"📊 {default_strategy} 전략으로 사전 분석 실행")
                
                # 시장 분석 실행
                analysis_results = await self.trading_system.run_market_analysis(
                    strategy=default_strategy, 
                    limit=None
                )
                
                if analysis_results:
                    self.logger.info(f"✅ 사전 분석 완료: {len(analysis_results)}개 종목 분석")
                    
                    # 상위 종목들을 모니터링 대상으로 설정
                    top_stocks = analysis_results[:20]  # 상위 20개
                    self.monitored_stocks = {
                        stock.get('symbol', ''): {
                            'strategy': default_strategy,
                            'score': stock.get('score', 0),
                            'action': stock.get('action', 'HOLD'),
                            'confidence': stock.get('confidence', 50),
                            'last_price': stock.get('current_price', 0),
                            'added_time': datetime.now()
                        }
                        for stock in top_stocks
                        if stock.get('symbol')
                    }
                    
                    self.logger.info(f"📋 모니터링 대상 설정: {len(self.monitored_stocks)}개 종목")
                else:
                    self.logger.warning("⚠️ 사전 분석 결과 없음")
            
        except Exception as e:
            self.logger.error(f"❌ 장 개장 전 사전 분석 실패: {e}")
    
    async def _market_open(self):
        """장 개장 시작 (09:00)"""
        try:
            self.logger.info("📈 장 개장 - 실시간 모니터링 시작")
            self.is_market_hours = True
            
            console.print(Panel(
                "[green]📈 장 개장 - 실시간 매매 시작[/green]\n"
                f"• 모니터링 대상: {len(self.monitored_stocks)}개 종목\n"
                f"• 모니터링 간격: {self.monitoring_interval}분\n"
                f"• 자동 매매: {'활성화' if self.trading_system.config.trading.TRADING_ENABLED else '비활성화'}",
                title="📊 Market Open",
                border_style="green"
            ))
            
            # 즉시 한 번 모니터링 실행
            await self._start_real_time_monitoring()
            
        except Exception as e:
            self.logger.error(f"❌ 장 개장 처리 실패: {e}")
    
    async def _start_real_time_monitoring(self):
        """실시간 모니터링 시작"""
        try:
            if not self.monitored_stocks:
                self.logger.info("📋 모니터링 대상 종목이 없어 기본 분석 실행")
                await self._pre_market_analysis()
            
            await self._real_time_monitoring()
            
        except Exception as e:
            self.logger.error(f"❌ 실시간 모니터링 시작 실패: {e}")
    
    async def _real_time_monitoring(self):
        """실시간 3분봉 모니터링"""
        try:
            current_time = datetime.now()
            
            # 장중 시간 확인
            if not self._is_market_hours():
                # self.logger.debug("🕐 장외 시간 - 모니터링 스킵")
                return
            
            self.logger.info(f"🔍 실시간 모니터링 실행 ({current_time.strftime('%H:%M:%S')})")
            
            if not self.monitored_stocks:
                self.logger.warning("⚠️ 모니터링 대상 종목 없음")
                return
            
            # 모니터링 대상 종목들 실시간 분석
            monitoring_results = []
            
            for symbol, stock_info in self.monitored_stocks.items():
                try:
                    # 현재가 조회
                    current_stock_data = await self.trading_system.data_collector.get_stock_info(symbol)
                    
                    if not current_stock_data:
                        continue
                    
                    # 전략별 신호 생성
                    strategy_name = stock_info.get('strategy', 'momentum')
                    strategy = self.trading_system.strategies.get(strategy_name)
                    
                    if strategy:
                        # StockData를 Dict로 변환 (전략이 Dict를 기대하므로)
                        stock_dict = {
                            'symbol': symbol,
                            'current_price': current_stock_data.current_price,
                            'change_rate': current_stock_data.change_rate,
                            'volume': current_stock_data.volume,
                            'trading_value': current_stock_data.trading_value,
                            'market_cap': current_stock_data.market_cap,
                            'high_52w': current_stock_data.high_52w,
                            'low_52w': current_stock_data.low_52w,
                            'pe_ratio': current_stock_data.pe_ratio,
                            'pbr': current_stock_data.pbr
                        }
                        
                        signal = await strategy.generate_signals(stock_dict)
                        
                        if signal and signal.get('action') in ['STRONG_BUY', 'BUY', 'STRONG_SELL', 'SELL']:
                            monitoring_results.append({
                                'symbol': symbol,
                                'current_price': current_stock_data.current_price,
                                'signal': signal,
                                'strategy': strategy_name
                            })
                            
                            # 매매 신호 발생 시 자동 주문 실행
                            if hasattr(self.trading_system, 'trading_executor'):
                                await self._execute_trading_signal(symbol, signal, stock_dict)
                
                except Exception as e:
                    self.logger.error(f"❌ {symbol} 모니터링 실패: {e}")
                    continue
            
            # 결과 표시
            if monitoring_results:
                self._display_monitoring_results(monitoring_results)
            
            self.last_analysis_time = current_time
            self.logger.info(f"✅ 실시간 모니터링 완료: {len(monitoring_results)}개 신호")
            
        except Exception as e:
            self.logger.error(f"❌ 실시간 모니터링 실패: {e}")
    
    async def _execute_trading_signal(self, symbol: str, signal: Dict, stock_data: Dict):
        """매매 신호 실행"""
        try:
            action = signal.get('action')
            confidence = signal.get('confidence', 50)
            current_price = stock_data.get('current_price', 0)
            
            # 신뢰도가 낮으면 스킵
            if confidence < 70:
                self.logger.info(f"📊 {symbol} 신뢰도 부족으로 매매 스킵 (신뢰도: {confidence}%)")
                return
            
            # 매수 신호
            if action in ['STRONG_BUY', 'BUY']:
                quantity = await self._calculate_buy_quantity(symbol, current_price, confidence)
                
                if quantity > 0:
                    self.logger.info(f"🚀 {symbol} 매수 신호 실행: {quantity}주 @ {current_price:,}원")
                    
                    # 실제 매수 주문 실행
                    buy_result = await self.trading_system.trading_executor.execute_buy_order(
                        symbol=symbol,
                        quantity=quantity,
                        price=None,  # 시장가
                        order_type="MARKET"
                    )
                    
                    if buy_result.get('success'):
                        self.logger.info(f"✅ {symbol} 매수 주문 성공: {buy_result}")
                        
                        # 리스크 관리 설정
                        if hasattr(self.trading_system, 'risk_manager'):
                            await self.trading_system.risk_manager.setup_automatic_stop_loss(symbol)
                    else:
                        self.logger.error(f"❌ {symbol} 매수 주문 실패: {buy_result}")
            
            # 매도 신호
            elif action in ['STRONG_SELL', 'SELL']:
                # 보유 포지션 확인
                if hasattr(self.trading_system, 'position_manager'):
                    position = await self.trading_system.position_manager.get_position(symbol)
                    
                    if position and position.get('quantity', 0) > 0:
                        sell_quantity = position.get('quantity')
                        
                        self.logger.info(f"📉 {symbol} 매도 신호 실행: {sell_quantity}주 @ {current_price:,}원")
                        
                        # 실제 매도 주문 실행
                        sell_result = await self.trading_system.trading_executor.execute_sell_order(
                            symbol=symbol,
                            quantity=sell_quantity,
                            price=None,  # 시장가
                            order_type="MARKET"
                        )
                        
                        if sell_result.get('success'):
                            self.logger.info(f"✅ {symbol} 매도 주문 성공: {sell_result}")
                        else:
                            self.logger.error(f"❌ {symbol} 매도 주문 실패: {sell_result}")
                    else:
                        self.logger.info(f"📊 {symbol} 보유 포지션 없어 매도 스킵")
            
        except Exception as e:
            self.logger.error(f"❌ {symbol} 매매 신호 실행 실패: {e}")
    
    async def _calculate_buy_quantity(self, symbol: str, price: int, confidence: int) -> int:
        """매수 수량 계산"""
        try:
            # 기본 매수 금액 (신뢰도에 따라 조정)
            base_amount = 1000000  # 100만원
            confidence_multiplier = confidence / 100
            
            target_amount = int(base_amount * confidence_multiplier)
            quantity = target_amount // price
            
            return max(1, quantity)  # 최소 1주
            
        except Exception as e:
            self.logger.error(f"❌ 매수 수량 계산 실패: {e}")
            return 0
    
    def _display_monitoring_results(self, results: List[Dict]):
        """모니터링 결과 표시"""
        try:
            table = Table(title="🔍 실시간 모니터링 결과")
            table.add_column("종목", style="cyan")
            table.add_column("현재가", style="yellow")
            table.add_column("신호", style="bold")
            table.add_column("신뢰도", style="green")
            table.add_column("전략", style="blue")
            
            for result in results:
                signal = result['signal']
                action_color = {
                    'STRONG_BUY': '[bold green]',
                    'BUY': '[green]',
                    'STRONG_SELL': '[bold red]',
                    'SELL': '[red]'
                }.get(signal.get('action', ''), '[yellow]')
                
                table.add_row(
                    result['symbol'],
                    f"{result['current_price']:,}원",
                    f"{action_color}{signal.get('action', 'HOLD')}[/]",
                    f"{signal.get('confidence', 0):.1f}%",
                    result['strategy']
                )
            
            console.print(table)
            
        except Exception as e:
            self.logger.error(f"❌ 모니터링 결과 표시 실패: {e}")
    
    async def _run_all_strategies_sequential(self):
        """전체 전략 순차 실행 (15:30) - 8개 전략 통합 분석"""
        try:
            self.logger.info("🔄 전체 전략 순차 실행 시작 (15:30)")

            console.print(Panel(
                "[bold yellow]🔄 전체 전략 순차 실행 시작[/bold yellow]\n"
                "• 8개 전략을 순차적으로 실행하여 종합 분석\n"
                "• HTS 조건검색 → 7개 영역 종합 분석\n"
                "• 매수 추천 종목 자동 모니터링 추가",
                title="📊 All Strategies Sequential Execution",
                border_style="yellow"
            ))

            # analysis_handlers를 통해 전체 전략 순차 실행
            if hasattr(self.trading_system, 'analysis_handlers') and self.trading_system.analysis_handlers:
                analysis_results = await self.trading_system.analysis_handlers.run_analysis_for_strategy(
                    strategy_name="all",
                    limit=999
                )

                if analysis_results:
                    buy_signals = [r for r in analysis_results if r.get('recommendation') in ['BUY', 'STRONG_BUY', 'WEAK_BUY']]

                    self.logger.info(f"✅ 전체 전략 순차 실행 완료: {len(analysis_results)}개 분석, {len(buy_signals)}개 매수 신호")

                    console.print(Panel(
                        f"[green]✅ 전체 전략 순차 실행 완료[/green]\n"
                        f"• 총 분석 종목: {len(analysis_results)}개\n"
                        f"• 매수 신호: {len(buy_signals)}개\n"
                        f"• 자동 모니터링 추가 예정",
                        title="📊 Execution Results",
                        border_style="green"
                    ))

                    # 매수 추천 종목 자동 모니터링 추가
                    if buy_signals:
                        added_count = await self.trading_system.analysis_handlers.auto_add_buy_recommendations_to_monitoring(buy_signals)
                        self.logger.info(f"📋 {added_count}개 종목 자동 모니터링 추가 완료")
                else:
                    self.logger.warning("⚠️ 전체 전략 순차 실행 결과 없음")
            else:
                self.logger.error("❌ analysis_handlers가 초기화되지 않음")

        except Exception as e:
            self.logger.error(f"❌ 전체 전략 순차 실행 실패: {e}")

    async def _market_close(self):
        """장 마감 처리 (15:45)"""
        try:
            self.logger.info("🌅 장 마감 - 실시간 모니터링 중지")
            self.is_market_hours = False

            console.print(Panel(
                "[blue]🌅 장 마감 - 실시간 매매 중지[/blue]\n"
                f"• 오늘 모니터링한 종목: {len(self.monitored_stocks)}개\n"
                f"• 마지막 분석 시간: {self.last_analysis_time.strftime('%H:%M:%S') if self.last_analysis_time else 'N/A'}",
                title="📊 Market Close",
                border_style="blue"
            ))

        except Exception as e:
            self.logger.error(f"❌ 장 마감 처리 실패: {e}")
    
    async def _daily_settlement(self):
        """일일 정산 (16:00)"""
        try:
            self.logger.info("📊 일일 정산 및 리포트 생성")
            
            # 포트폴리오 정산
            if hasattr(self.trading_system, 'position_manager'):
                portfolio_metrics = await self.trading_system.position_manager.calculate_portfolio_metrics()
                
                console.print(Panel(
                    f"[cyan]📊 일일 정산 리포트[/cyan]\n"
                    f"• 총 포지션: {portfolio_metrics.get('total_positions', 0)}개\n"
                    f"• 총 평가금액: {portfolio_metrics.get('total_value', 0):,}원\n"
                    f"• 총 손익: {portfolio_metrics.get('total_pnl', 0):,}원\n"
                    f"• 수익률: {portfolio_metrics.get('total_pnl_rate', 0):.2f}%",
                    title="📈 Daily Settlement",
                    border_style="cyan"
                ))
            
            # 다음 날을 위해 모니터링 대상 초기화
            self.monitored_stocks.clear()
            
        except Exception as e:
            self.logger.error(f"❌ 일일 정산 실패: {e}")
    
    def _is_market_hours(self) -> bool:
        """장중 시간 확인"""
        now = datetime.now().time()
        
        # 주말 체크
        weekday = datetime.now().weekday()
        if weekday >= 5:  # 토요일(5), 일요일(6)
            return False
        
        return self.market_open_time <= now <= self.market_close_time
    
    def get_status(self) -> Dict[str, Any]:
        """스케줄러 상태 반환"""
        return {
            'is_running': self.is_running,
            'is_market_hours': self.is_market_hours,
            'monitored_stocks_count': len(self.monitored_stocks),
            'last_analysis_time': self.last_analysis_time.isoformat() if self.last_analysis_time else None,
            'next_run_time': self.scheduler.get_jobs()[0].next_run_time.isoformat() if self.scheduler.get_jobs() else None
        }
    
    async def add_monitoring_stock(self, symbol: str, strategy: str = "momentum"):
        """모니터링 종목 추가"""
        try:
            if symbol not in self.monitored_stocks:
                self.monitored_stocks[symbol] = {
                    'strategy': strategy,
                    'added_time': datetime.now(),
                    'score': 0,
                    'action': 'HOLD',
                    'confidence': 50,
                    'last_price': 0
                }
                self.logger.info(f"📋 모니터링 종목 추가: {symbol} ({strategy} 전략)")
                return True
            return False
            
        except Exception as e:
            self.logger.error(f"❌ 모니터링 종목 추가 실패: {e}")
            return False
    
    def remove_monitoring_stock(self, symbol: str):
        """모니터링 종목 제거"""
        try:
            if symbol in self.monitored_stocks:
                del self.monitored_stocks[symbol]
                self.logger.info(f"📋 모니터링 종목 제거: {symbol}")
                return True
            return False
            
        except Exception as e:
            self.logger.error(f"❌ 모니터링 종목 제거 실패: {e}")
            return False