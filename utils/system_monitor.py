#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/system_monitor.py

시스템 상태 모니터링 대시보드
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import json
from pathlib import Path

# Optional psutil import
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("Warning: psutil not installed. System resource monitoring will be limited.")

# Rich 라이브러리
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.progress import Progress
from rich.text import Text
from rich.columns import Columns
from rich.align import Align

# Windows Unicode 문제 해결
import os
if os.name == 'nt':  # Windows
    try:
        os.system("chcp 65001 > nul 2>&1")
        console = Console(force_terminal=True, legacy_windows=False)
    except:
        console = Console(legacy_windows=True)
else:
    console = Console()

from utils.logger import get_logger
from database.models import MonitoringStock, MonitoringType, MonitoringStatus


class SystemMonitor:
    """시스템 상태 모니터링 대시보드"""
    
    def __init__(self, trading_system=None):
        self.trading_system = trading_system
        self.logger = get_logger("SystemMonitor")
        self.is_monitoring = False
        self.monitoring_task = None
        
    async def start_dashboard(self, refresh_interval: int = 5):
        """실시간 대시보드 시작"""
        try:
            self.is_monitoring = True
            
            with Live(self._generate_dashboard(), refresh_per_second=1/refresh_interval) as live:
                while self.is_monitoring:
                    await asyncio.sleep(refresh_interval)
                    live.update(self._generate_dashboard())
                    
        except Exception as e:
            self.logger.error(f"❌ 대시보드 실행 오류: {e}")
        finally:
            self.is_monitoring = False
    
    def stop_dashboard(self):
        """대시보드 중지"""
        self.is_monitoring = False
    
    def _generate_dashboard(self) -> Layout:
        """대시보드 레이아웃 생성"""
        layout = Layout()
        
        # 상단: 시스템 정보
        layout.split_column(
            Layout(name="header", size=8),
            Layout(name="body"),
            Layout(name="footer", size=3)
        )
        
        # 헤더: 시스템 상태
        layout["header"].update(self._get_system_status_panel())
        
        # 바디: 좌우 분할
        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right")
        )
        
        # 좌측: 모니터링 상태
        layout["left"].update(self._get_monitoring_status_panel())
        
        # 우측: 시스템 리소스
        layout["right"].update(self._get_resource_status_panel())
        
        # 푸터: 업데이트 시간
        layout["footer"].update(
            Align.center(
                Text(f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 
                     style="dim")
            )
        )
        
        return layout
    
    def _get_system_status_panel(self) -> Panel:
        """시스템 상태 패널"""
        # 시스템 컴포넌트 상태
        status_items = []
        
        if self.trading_system:
            # 컴포넌트 상태 체크
            components = {
                "🔌 KIS API": hasattr(self.trading_system, 'data_collector') and self.trading_system.data_collector,
                "📊 분석 엔진": hasattr(self.trading_system, 'analysis_engine') and self.trading_system.analysis_engine,
                "🤖 AI 컨트롤러": hasattr(self.trading_system, 'ai_controller') and self.trading_system.ai_controller,
                "💾 데이터베이스": hasattr(self.trading_system, 'db_manager') and self.trading_system.db_manager,
                "💰 매매 실행": hasattr(self.trading_system, 'trading_executor') and self.trading_system.trading_executor,
                "📱 알림 시스템": hasattr(self.trading_system, 'notification_manager') and self.trading_system.notification_manager
            }
            
            for name, status in components.items():
                emoji = "🟢" if status else "🔴"
                status_items.append(f"{emoji} {name}")
        else:
            status_items.append("⚠️ TradingSystem 미연결")
        
        # 컬럼으로 배치
        columns = Columns(status_items, equal=True, expand=True)
        
        return Panel(
            columns,
            title="🖥️ 시스템 상태",
            border_style="cyan"
        )
    
    def _get_monitoring_status_panel(self) -> Panel:
        """모니터링 상태 패널"""
        table = Table()
        table.add_column("구분", style="cyan")
        table.add_column("상태", justify="center")
        table.add_column("종목 수", justify="right")
        table.add_column("마지막 실행", style="dim")
        
        if self.trading_system and hasattr(self.trading_system, 'db_manager') and self.trading_system.db_manager:
            try:
                with self.trading_system.db_manager.get_session() as session:
                    # 자동매매 모니터링
                    trading_stocks = session.query(MonitoringStock).filter(
                        MonitoringStock.monitoring_type == MonitoringType.TRADING,
                        MonitoringStock.status == MonitoringStatus.ACTIVE.value
                    ).all()

                    # 감시 제거 모니터링
                    removal_stocks = session.query(MonitoringStock).filter(
                        MonitoringStock.monitoring_type == MonitoringType.REMOVAL_WATCH,
                        MonitoringStock.status == MonitoringStatus.ACTIVE.value
                    ).all()
                    
                    # 자동매매 상태
                    auto_trader_status = "🟢 실행중" if (
                        hasattr(self.trading_system, 'db_auto_trading_handler') and
                        self.trading_system.db_auto_trading_handler and
                        self.trading_system.db_auto_trading_handler.auto_trader.is_monitoring
                    ) else "🔴 중지"
                    
                    table.add_row(
                        "📈 자동매매 모니터링",
                        auto_trader_status,
                        str(len(trading_stocks)),
                        "N/A"
                    )
                    
                    # 감시 제거 스케줄러 상태
                    from database.models import MonitoringSchedulerState
                    scheduler_state = MonitoringSchedulerState.get_scheduler_state(
                        session, "removal_scheduler"
                    )
                    
                    scheduler_status = "🔴 중지"
                    last_run = "미실행"
                    
                    if scheduler_state:
                        scheduler_status = "🟢 실행중" if scheduler_state.is_running else "🔴 중지"
                        if scheduler_state.last_run_time:
                            last_run = scheduler_state.last_run_time.strftime('%H:%M')
                    
                    table.add_row(
                        "🕐 감시 제거 스케줄러",
                        scheduler_status,
                        str(len(removal_stocks)),
                        last_run
                    )
                    
            except Exception as e:
                table.add_row("⚠️ DB 조회 오류", str(e), "0", "N/A")
        else:
            table.add_row("⚠️ DB 미연결", "오류", "0", "N/A")
        
        return Panel(
            table,
            title="📊 모니터링 상태",
            border_style="green"
        )
    
    def _get_resource_status_panel(self) -> Panel:
        """시스템 리소스 상태"""
        table = Table()
        table.add_column("리소스", style="yellow")
        table.add_column("사용량", justify="center")
        table.add_column("상태", justify="center")
        
        if HAS_PSUTIL:
            try:
                # CPU 사용량
                cpu_percent = psutil.cpu_percent(interval=0.1)
                cpu_status = "🟢" if cpu_percent < 70 else "🟡" if cpu_percent < 90 else "🔴"
                table.add_row("CPU", f"{cpu_percent:.1f}%", cpu_status)
                
                # 메모리 사용량
                memory = psutil.virtual_memory()
                memory_status = "🟢" if memory.percent < 70 else "🟡" if memory.percent < 90 else "🔴"
                table.add_row(
                    "메모리", 
                    f"{memory.percent:.1f}% ({memory.used//1024//1024//1024:.1f}GB)",
                    memory_status
                )
                
                # 디스크 사용량
                disk = psutil.disk_usage('D:\\' if os.path.exists('D:\\') else '/')
                disk_status = "🟢" if disk.percent < 80 else "🟡" if disk.percent < 95 else "🔴"
                table.add_row("디스크", f"{disk.percent:.1f}%", disk_status)
                
                # 네트워크 상태 (단순화)
                network = psutil.net_io_counters()
                if network:
                    net_status = "🟢" if network.bytes_sent > 0 and network.bytes_recv > 0 else "🟡"
                    table.add_row(
                        "네트워크", 
                        f"송신:{network.bytes_sent//1024//1024:.0f}MB", 
                        net_status
                    )
                
            except Exception as e:
                table.add_row("⚠️ 리소스 조회 오류", str(e), "🔴")
        else:
            # psutil 미설치 시 메시지만 표시
            table.add_row("시스템 리소스", "psutil 설치 필요", "⚠️")
        
        return Panel(
            table,
            title="⚡ 시스템 리소스",
            border_style="yellow"
        )
    
    def get_system_status(self) -> Dict[str, Any]:
        """시스템 상태 반환 (API용)"""
        status = {
            'timestamp': datetime.now().isoformat(),
            'system': {},
            'components': {},
            'monitoring': {}
        }
        
        # 시스템 리소스 (psutil 사용 가능시에만)
        if HAS_PSUTIL:
            try:
                status['system'] = {
                    'cpu_percent': psutil.cpu_percent(),
                    'memory_percent': psutil.virtual_memory().percent,
                    'disk_percent': psutil.disk_usage('/').percent if os.path.exists('/') else psutil.disk_usage('C:\\').percent
                }
            except:
                status['system'] = {'error': 'psutil error'}
        else:
            status['system'] = {'status': 'psutil not available'}
        
        if self.trading_system:
            # 컴포넌트 상태
            status['components'] = {
                'kis_api': hasattr(self.trading_system, 'data_collector') and self.trading_system.data_collector is not None,
                'analysis_engine': hasattr(self.trading_system, 'analysis_engine') and self.trading_system.analysis_engine is not None,
                'ai_controller': hasattr(self.trading_system, 'ai_controller') and self.trading_system.ai_controller is not None,
                'database': hasattr(self.trading_system, 'db_manager') and self.trading_system.db_manager is not None,
                'trading_executor': hasattr(self.trading_system, 'trading_executor') and self.trading_system.trading_executor is not None,
                'notification_manager': hasattr(self.trading_system, 'notification_manager') and self.trading_system.notification_manager is not None
            }
            
            # 모니터링 상태
            if hasattr(self.trading_system, 'db_manager') and self.trading_system.db_manager:
                try:
                    with self.trading_system.db_manager.get_session() as session:
                        trading_count = session.query(MonitoringStock).filter(
                            MonitoringStock.monitoring_type == MonitoringType.TRADING,
                            MonitoringStock.status == MonitoringStatus.ACTIVE.value
                        ).count()

                        removal_count = session.query(MonitoringStock).filter(
                            MonitoringStock.monitoring_type == MonitoringType.REMOVAL_WATCH,
                            MonitoringStock.status == MonitoringStatus.ACTIVE.value
                        ).count()
                        
                        status['monitoring'] = {
                            'auto_trading_stocks': trading_count,
                            'removal_watch_stocks': removal_count,
                            'auto_trader_running': (
                                hasattr(self.trading_system, 'db_auto_trading_handler') and
                                self.trading_system.db_auto_trading_handler and
                                self.trading_system.db_auto_trading_handler.auto_trader.is_monitoring
                            )
                        }
                except Exception as e:
                    status['monitoring']['error'] = str(e)
        
        return status
    
    def save_status_log(self, log_path: str = "logs/system_status.json"):
        """시스템 상태 로그 저장"""
        try:
            status = self.get_system_status()
            
            log_file = Path(log_path)
            log_file.parent.mkdir(exist_ok=True)
            
            # 기존 로그 읽기 (최대 100개 유지)
            logs = []
            if log_file.exists():
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        logs = json.load(f)
                        if len(logs) >= 100:
                            logs = logs[-99:]  # 최신 99개만 유지
                except:
                    logs = []
            
            # 새 상태 추가
            logs.append(status)
            
            # 로그 저장
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            self.logger.error(f"❌ 상태 로그 저장 실패: {e}")


async def main():
    """대시보드 테스트"""
    monitor = SystemMonitor()
    
    try:
        console.print("[bold cyan]시스템 모니터 대시보드 시작[/bold cyan]")
        console.print("종료: Ctrl+C")
        
        await monitor.start_dashboard(refresh_interval=2)
        
    except KeyboardInterrupt:
        console.print("\n[yellow]대시보드를 종료합니다[/yellow]")
    except Exception as e:
        console.print(f"[red]오류: {e}[/red]")


if __name__ == "__main__":
    asyncio.run(main())