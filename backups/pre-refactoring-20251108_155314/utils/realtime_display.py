#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/realtime_display.py

200개 종목 실시간 모니터링을 위한 고성능 디스플레이 시스템
- 실시간 데이터 시각화
- 메모리 효율적인 렌더링
- 다중 레이아웃 지원
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import math
from collections import deque

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich.align import Align
from rich.box import ROUNDED, HEAVY, DOUBLE
from rich.tree import Tree

from utils.logger import get_logger


class DisplayMode(Enum):
    """디스플레이 모드"""
    COMPACT = "compact"          # 압축형 (200개 모두)
    DETAILED = "detailed"        # 상세형 (상위 50개)
    PRIORITY = "priority"        # 우선순위 (매수/알림 위주)
    DASHBOARD = "dashboard"      # 대시보드 (통계 + 주요 종목)


class UpdateFrequency(Enum):
    """업데이트 주기"""
    REALTIME = 0.5      # 0.5초
    FAST = 1.0          # 1초
    NORMAL = 2.0        # 2초
    SLOW = 5.0          # 5초


@dataclass
class DisplayStock:
    """디스플레이용 종목 데이터"""
    symbol: str
    name: str
    price: float
    change: float
    change_rate: float
    volume: int
    priority: int
    alert_count: int = 0
    last_update: datetime = None
    trend: str = "→"  # ↑, ↓, →

    def get_color(self) -> str:
        """변동률에 따른 색상"""
        if self.change_rate > 0:
            return "green"
        elif self.change_rate < 0:
            return "red"
        else:
            return "white"

    def get_trend_symbol(self) -> str:
        """트렌드 심볼"""
        if self.change_rate > 2:
            return "🚀"
        elif self.change_rate > 0:
            return "📈"
        elif self.change_rate < -2:
            return "💥"
        elif self.change_rate < 0:
            return "📉"
        else:
            return "➖"


class RealtimeDisplay:
    """실시간 모니터링 디스플레이 시스템"""

    def __init__(self, monitoring_handler=None):
        self.monitoring_handler = monitoring_handler
        self.logger = get_logger("RealtimeDisplay")

        # 콘솔 설정
        self.console = Console(width=150, height=40)

        # 디스플레이 설정
        self.display_mode = DisplayMode.DASHBOARD
        self.update_frequency = UpdateFrequency.NORMAL
        self.max_display_rows = 35

        # 데이터 저장소
        self.display_stocks: Dict[str, DisplayStock] = {}
        self.system_stats: Dict[str, Any] = {}
        self.alert_history: deque = deque(maxlen=50)

        # 렌더링 상태
        self.live_display = None
        self.is_running = False
        self.last_render_time = 0
        self.render_count = 0

        # 레이아웃 설정
        self.layout = Layout()
        self._setup_layout()

        self.logger.info("🖥️ RealtimeDisplay 초기화 완료")

    def _setup_layout(self):
        """레이아웃 설정"""
        # 메인 레이아웃 분할
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )

        # 메인 영역 분할
        self.layout["main"].split_row(
            Layout(name="stocks", ratio=2),
            Layout(name="sidebar", size=40)
        )

        # 사이드바 분할
        self.layout["sidebar"].split_column(
            Layout(name="stats", size=12),
            Layout(name="alerts", ratio=1)
        )

    async def start_display(self, mode: DisplayMode = DisplayMode.DASHBOARD,
                           frequency: UpdateFrequency = UpdateFrequency.NORMAL):
        """디스플레이 시작"""
        try:
            if self.is_running:
                self.logger.warning("이미 디스플레이가 실행 중입니다")
                return

            self.display_mode = mode
            self.update_frequency = frequency
            self.is_running = True

            # Live 디스플레이 시작
            self.live_display = Live(
                self.layout,
                console=self.console,
                refresh_per_second=1 / frequency.value,
                vertical_overflow="visible"
            )

            with self.live_display:
                await self._display_loop()

        except KeyboardInterrupt:
            self.logger.info("사용자에 의한 디스플레이 중지")
        except Exception as e:
            self.logger.error(f"❌ 디스플레이 시작 실패: {e}")
        finally:
            self.is_running = False

    async def stop_display(self):
        """디스플레이 중지"""
        self.is_running = False
        if self.live_display:
            self.live_display.stop()

    async def _display_loop(self):
        """디스플레이 메인 루프"""
        while self.is_running:
            try:
                start_time = time.time()

                # 1. 데이터 업데이트
                await self._update_display_data()

                # 2. 레이아웃 렌더링
                self._render_layout()

                # 3. 성능 통계
                self._update_render_stats(start_time)

                # 4. 업데이트 주기 대기
                await asyncio.sleep(self.update_frequency.value)

            except Exception as e:
                self.logger.error(f"❌ 디스플레이 루프 오류: {e}")
                await asyncio.sleep(1)

    async def _update_display_data(self):
        """디스플레이 데이터 업데이트"""
        try:
            if not self.monitoring_handler:
                return

            # 모니터링 상태 조회
            monitoring_status = await self.monitoring_handler.get_monitoring_status()
            self.system_stats = monitoring_status

            # 종목 데이터 업데이트
            storage = self.monitoring_handler.memory_storage

            for symbol in list(self.display_stocks.keys()):
                latest_data = storage.get_latest_data(symbol)

                if latest_data:
                    # 기존 데이터 업데이트
                    if symbol in self.display_stocks:
                        display_stock = self.display_stocks[symbol]
                        old_price = display_stock.price

                        display_stock.price = latest_data['price']
                        display_stock.change_rate = latest_data['change']
                        display_stock.volume = latest_data['volume']
                        display_stock.last_update = datetime.fromtimestamp(latest_data['timestamp'])

                        # 트렌드 계산
                        if latest_data['price'] > old_price:
                            display_stock.trend = "↑"
                        elif latest_data['price'] < old_price:
                            display_stock.trend = "↓"
                        else:
                            display_stock.trend = "→"

        except Exception as e:
            self.logger.error(f"❌ 디스플레이 데이터 업데이트 실패: {e}")

    def _render_layout(self):
        """레이아웃 렌더링"""
        try:
            # 헤더 렌더링
            self.layout["header"].update(self._render_header())

            # 메인 영역 렌더링
            if self.display_mode == DisplayMode.COMPACT:
                self.layout["stocks"].update(self._render_compact_stocks())
            elif self.display_mode == DisplayMode.DETAILED:
                self.layout["stocks"].update(self._render_detailed_stocks())
            elif self.display_mode == DisplayMode.PRIORITY:
                self.layout["stocks"].update(self._render_priority_stocks())
            else:  # DASHBOARD
                self.layout["stocks"].update(self._render_dashboard_stocks())

            # 사이드바 렌더링
            self.layout["stats"].update(self._render_system_stats())
            self.layout["alerts"].update(self._render_alert_panel())

            # 푸터 렌더링
            self.layout["footer"].update(self._render_footer())

        except Exception as e:
            self.logger.error(f"❌ 레이아웃 렌더링 실패: {e}")

    def _render_header(self) -> Panel:
        """헤더 렌더링"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        monitoring_count = len(self.display_stocks)

        header_text = Text()
        header_text.append("🚀 ", style="bold yellow")
        header_text.append("실시간 모니터링 시스템", style="bold white")
        header_text.append(f" | {current_time}", style="dim white")
        header_text.append(f" | 모니터링: {monitoring_count}개 종목", style="cyan")
        header_text.append(f" | 모드: {self.display_mode.value.upper()}", style="magenta")

        return Panel(
            Align.center(header_text),
            style="bold blue",
            box=HEAVY
        )

    def _render_compact_stocks(self) -> Panel:
        """압축형 종목 테이블"""
        table = Table(
            "순위", "종목코드", "종목명", "현재가", "변동률", "거래량", "업데이트",
            title="📊 압축형 모니터링 (200개 종목)",
            box=ROUNDED,
            show_header=True,
            header_style="bold cyan"
        )

        # 변동률 기준 정렬
        sorted_stocks = sorted(
            self.display_stocks.values(),
            key=lambda x: abs(x.change_rate),
            reverse=True
        )

        for i, stock in enumerate(sorted_stocks[:self.max_display_rows], 1):
            color = stock.get_color()
            trend = stock.get_trend_symbol()

            table.add_row(
                str(i),
                stock.symbol,
                stock.name[:8],  # 이름 줄임
                f"{stock.price:,.0f}",
                f"{trend} {stock.change_rate:+.2f}%",
                f"{stock.volume:,}",
                stock.last_update.strftime("%H:%M:%S") if stock.last_update else "-",
                style=color
            )

        return Panel(table, style="white")

    def _render_detailed_stocks(self) -> Panel:
        """상세형 종목 테이블"""
        table = Table(
            "종목코드", "종목명", "현재가", "전일대비", "변동률", "거래량", "우선순위", "알림",
            title="📈 상세 모니터링 (상위 50개)",
            box=ROUNDED,
            show_header=True,
            header_style="bold green"
        )

        # 우선순위와 변동률 기준 정렬
        sorted_stocks = sorted(
            self.display_stocks.values(),
            key=lambda x: (x.priority, abs(x.change_rate)),
            reverse=True
        )

        for stock in sorted_stocks[:50]:
            color = stock.get_color()
            trend = stock.get_trend_symbol()

            priority_text = "🔥" if stock.priority == 1 else "⭐" if stock.priority == 2 else "📊"

            table.add_row(
                stock.symbol,
                stock.name[:12],
                f"{stock.price:,.0f}",
                f"{stock.change:+,.0f}",
                f"{trend} {stock.change_rate:+.2f}%",
                f"{stock.volume:,}",
                f"{priority_text} {stock.priority}",
                f"🚨 {stock.alert_count}" if stock.alert_count > 0 else "-",
                style=color
            )

        return Panel(table, style="white")

    def _render_priority_stocks(self) -> Panel:
        """우선순위 종목 테이블"""
        table = Table(
            "우선순위", "종목코드", "종목명", "현재가", "변동률", "상태", "마지막 알림",
            title="🎯 우선순위 모니터링",
            box=DOUBLE,
            show_header=True,
            header_style="bold red"
        )

        # 우선순위별 정렬
        priority_stocks = sorted(
            self.display_stocks.values(),
            key=lambda x: (x.priority, x.alert_count, abs(x.change_rate)),
            reverse=True
        )

        for stock in priority_stocks[:30]:
            color = stock.get_color()
            trend = stock.get_trend_symbol()

            # 우선순위 표시
            if stock.priority == 1:
                priority_display = "🔥 CRITICAL"
                priority_color = "bold red"
            elif stock.priority == 2:
                priority_display = "⭐ HIGH"
                priority_color = "bold yellow"
            elif stock.priority == 3:
                priority_display = "📊 MEDIUM"
                priority_color = "white"
            else:
                priority_display = "📋 LOW"
                priority_color = "dim white"

            # 상태 표시
            if stock.alert_count > 0:
                status = f"🚨 {stock.alert_count}건"
                status_color = "bold red"
            else:
                status = "정상"
                status_color = "green"

            table.add_row(
                Text(priority_display, style=priority_color),
                stock.symbol,
                stock.name[:10],
                f"{stock.price:,.0f}",
                f"{trend} {stock.change_rate:+.2f}%",
                Text(status, style=status_color),
                stock.last_update.strftime("%H:%M:%S") if stock.last_update else "-",
                style=color
            )

        return Panel(table, style="white")

    def _render_dashboard_stocks(self) -> Panel:
        """대시보드형 주요 종목"""
        # 상위 20개 종목만 표시
        table = Table(
            "순위", "종목", "현재가", "변동률", "거래량", "트렌드", "상태",
            title="🎛️ 대시보드 - 주요 종목",
            box=ROUNDED,
            show_header=True,
            header_style="bold magenta"
        )

        # 종합 점수로 정렬 (우선순위 + 변동률 + 알림 횟수)
        sorted_stocks = sorted(
            self.display_stocks.values(),
            key=lambda x: (5 - x.priority) * 10 + abs(x.change_rate) + x.alert_count * 5,
            reverse=True
        )

        for i, stock in enumerate(sorted_stocks[:20], 1):
            color = stock.get_color()
            trend = stock.get_trend_symbol()

            # 상태 종합
            status_items = []
            if stock.alert_count > 0:
                status_items.append(f"🚨{stock.alert_count}")
            if stock.priority <= 2:
                status_items.append("⭐")
            if abs(stock.change_rate) > 5:
                status_items.append("🔥")

            status = " ".join(status_items) if status_items else "📊"

            table.add_row(
                f"#{i}",
                f"{stock.symbol}\n{stock.name[:8]}",
                f"{stock.price:,.0f}원",
                f"{trend}\n{stock.change_rate:+.2f}%",
                f"{stock.volume//1000:,}K" if stock.volume > 1000 else f"{stock.volume}",
                stock.trend,
                status,
                style=color
            )

        return Panel(table, style="white")

    def _render_system_stats(self) -> Panel:
        """시스템 통계 패널"""
        if not self.system_stats:
            return Panel("📊 통계 로딩 중...", title="시스템 통계")

        stats_text = Text()

        # 모니터링 상태
        is_running = self.system_stats.get('is_running', False)
        status_color = "green" if is_running else "red"
        status_text = "🟢 실행 중" if is_running else "🔴 중지됨"

        stats_text.append(f"상태: {status_text}\n", style=status_color)

        # 기본 통계
        if 'collector_status' in self.system_stats:
            collector = self.system_stats['collector_status']
            stats_text.append(f"총 종목: {collector.get('total_stocks', 0)}개\n")
            stats_text.append(f"활성 종목: {collector.get('active_stocks', 0)}개\n")
            stats_text.append(f"성공률: {collector.get('success_rate', 0):.1%}\n")

        # 성능 통계
        if 'performance_stats' in self.system_stats:
            perf = self.system_stats['performance_stats']
            stats_text.append(f"총 데이터: {perf.get('total_data_points', 0):,}개\n")
            stats_text.append(f"알림 발생: {perf.get('alerts_triggered', 0):,}회\n")
            stats_text.append(f"처리 시간: {perf.get('avg_processing_time', 0):.3f}초\n")

        # 메모리 사용량
        if 'storage_stats' in self.system_stats:
            storage = self.system_stats['storage_stats']
            memory_mb = storage.get('memory_usage_mb', 0)
            cache_hit_rate = storage.get('cache_hit_rate', 0)

            stats_text.append(f"메모리: {memory_mb:.1f}MB\n")
            stats_text.append(f"캐시 적중률: {cache_hit_rate:.1%}\n")

        # 업데이트 시간
        last_update = self.system_stats.get('performance_stats', {}).get('last_update_time')
        if last_update:
            if isinstance(last_update, str):
                update_text = last_update
            else:
                update_text = last_update.strftime("%H:%M:%S")
            stats_text.append(f"마지막 업데이트: {update_text}")

        return Panel(
            stats_text,
            title="📊 시스템 통계",
            border_style="cyan",
            box=ROUNDED
        )

    def _render_alert_panel(self) -> Panel:
        """알림 패널"""
        if not self.alert_history:
            alert_text = Text("최근 알림이 없습니다.", style="dim white")
        else:
            alert_text = Text()

            for alert in list(self.alert_history)[-10:]:  # 최근 10개
                timestamp = alert.get('timestamp', '')
                message = alert.get('message', '')
                priority = alert.get('priority', 3)

                # 우선순위별 색상
                if priority <= 2:
                    style = "bold red"
                    icon = "🚨"
                elif priority == 3:
                    style = "yellow"
                    icon = "⚠️"
                else:
                    style = "white"
                    icon = "ℹ️"

                alert_text.append(f"{icon} {message}\n", style=style)

        return Panel(
            alert_text,
            title="🚨 최근 알림",
            border_style="red",
            box=ROUNDED
        )

    def _render_footer(self) -> Panel:
        """푸터 렌더링"""
        footer_text = Text()

        # 렌더링 통계
        footer_text.append(f"렌더링: {self.render_count:,}회", style="dim white")
        footer_text.append(" | ", style="dim white")
        footer_text.append(f"FPS: {1/self.update_frequency.value:.1f}", style="dim cyan")
        footer_text.append(" | ", style="dim white")

        # 메모리 정보
        if self.system_stats and 'storage_stats' in self.system_stats:
            memory_mb = self.system_stats['storage_stats'].get('memory_usage_mb', 0)
            footer_text.append(f"메모리: {memory_mb:.1f}MB", style="dim yellow")
            footer_text.append(" | ", style="dim white")

        # 단축키 정보
        footer_text.append("단축키: ", style="dim white")
        footer_text.append("Ctrl+C", style="bold red")
        footer_text.append(" 종료", style="dim white")

        return Panel(
            Align.center(footer_text),
            style="dim blue"
        )

    def _update_render_stats(self, start_time: float):
        """렌더링 통계 업데이트"""
        self.render_count += 1
        self.last_render_time = time.time() - start_time

    def add_stock_to_display(self, symbol: str, name: str, priority: int = 3):
        """디스플레이에 종목 추가"""
        self.display_stocks[symbol] = DisplayStock(
            symbol=symbol,
            name=name,
            price=0,
            change=0,
            change_rate=0,
            volume=0,
            priority=priority,
            last_update=datetime.now()
        )

    def remove_stock_from_display(self, symbol: str):
        """디스플레이에서 종목 제거"""
        self.display_stocks.pop(symbol, None)

    def add_alert_to_history(self, alert: Dict[str, Any]):
        """알림 히스토리에 추가"""
        self.alert_history.append({
            'timestamp': datetime.now().strftime("%H:%M:%S"),
            'message': alert.get('message', ''),
            'priority': alert.get('priority', 3),
            'symbol': alert.get('symbol', '')
        })

    def set_display_mode(self, mode: DisplayMode):
        """디스플레이 모드 변경"""
        self.display_mode = mode
        self.logger.info(f"🖥️ 디스플레이 모드 변경: {mode.value}")

    def set_update_frequency(self, frequency: UpdateFrequency):
        """업데이트 주기 변경"""
        self.update_frequency = frequency
        self.logger.info(f"⏱️ 업데이트 주기 변경: {frequency.value}초")

    async def load_monitoring_stocks(self):
        """모니터링 종목 로드"""
        try:
            if not self.monitoring_handler:
                return

            # DB에서 활성 종목 로드
            from database.monitoring_models import MonitoringStock, MonitoringStatus

            with self.monitoring_handler.db_manager.get_session() as session:
                active_stocks = session.query(MonitoringStock).filter(
                    MonitoringStock.status == MonitoringStatus.ACTIVE.value
                ).all()

                for stock in active_stocks:
                    priority = 1 if stock.buy_price else 3  # 매수한 종목은 우선순위 1

                    self.add_stock_to_display(
                        symbol=stock.symbol,
                        name=stock.name,
                        priority=priority
                    )

                self.logger.info(f"📊 {len(active_stocks)}개 종목을 디스플레이에 로드")

        except Exception as e:
            self.logger.error(f"❌ 모니터링 종목 로드 실패: {e}")

    def get_display_statistics(self) -> Dict[str, Any]:
        """디스플레이 통계"""
        return {
            'display_mode': self.display_mode.value,
            'update_frequency': self.update_frequency.value,
            'total_stocks': len(self.display_stocks),
            'render_count': self.render_count,
            'last_render_time': self.last_render_time,
            'alert_history_count': len(self.alert_history),
            'is_running': self.is_running
        }