#!/usr/bin/env python3
"""
중기 계좌(5202-2235) 모니터링 실행 스크립트

Usage:
    python monitor_trend_account.py           # 1회 조회
    python monitor_trend_account.py --live    # 실시간 모니터링
    python monitor_trend_account.py --report  # 일일 리포트
"""

import sys
import argparse
import time
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.panel import Panel

# 프로젝트 경로 추가
sys.path.insert(0, '.')

from trading.trend_account_monitor import TrendAccountMonitor

console = Console()


def one_time_check():
    """1회 조회"""
    console.print()
    console.print("=" * 60)
    console.print("📈 중기 계좌 (5202-2235) 조회")
    console.print("=" * 60)

    monitor = TrendAccountMonitor()

    if not monitor.initialize():
        return

    holdings = monitor.fetch_holdings()

    if holdings:
        console.print(f"\n[green]✅ {len(holdings)}개 종목 조회 완료[/green]\n")
        monitor.display_holdings()
        monitor.display_exit_alerts()
    else:
        console.print("\n[yellow]보유 종목이 없습니다.[/yellow]")
        console.print("[dim]주식을 이체한 후 다시 조회해주세요.[/dim]")

    console.print()


def live_monitor(interval: int = 60):
    """실시간 모니터링"""
    console.print()
    console.print("=" * 60)
    console.print("📈 중기 계좌 실시간 모니터링")
    console.print(f"갱신 주기: {interval}초 | Ctrl+C로 종료")
    console.print("=" * 60)

    monitor = TrendAccountMonitor()

    if not monitor.initialize():
        return

    try:
        while True:
            console.clear()
            console.print(f"[dim]마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
            console.print()

            holdings = monitor.fetch_holdings()

            if holdings:
                monitor.display_holdings()
                monitor.display_exit_alerts()

                # 청산 시그널 있으면 알림
                exit_signals = monitor.get_exit_signals()
                if exit_signals:
                    console.print("\n🚨 [bold red]청산 시그널 발생![/bold red]")
                    for h in exit_signals:
                        console.print(f"   {h.stock_name}: {h.exit_reason}")
            else:
                console.print("[yellow]보유 종목이 없습니다.[/yellow]")

            console.print(f"\n[dim]다음 갱신: {interval}초 후...[/dim]")
            time.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]모니터링 종료[/yellow]")


def daily_report():
    """일일 리포트"""
    monitor = TrendAccountMonitor()

    if not monitor.initialize():
        return

    monitor.fetch_holdings()

    report = monitor.get_daily_report()
    console.print(report)

    # 파일로도 저장
    filename = f"logs/trend_account_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(report)

    console.print(f"\n[green]리포트 저장: {filename}[/green]")


def main():
    parser = argparse.ArgumentParser(description='중기 계좌 모니터링')
    parser.add_argument('--live', action='store_true', help='실시간 모니터링')
    parser.add_argument('--report', action='store_true', help='일일 리포트 생성')
    parser.add_argument('--interval', type=int, default=60, help='갱신 주기 (초)')

    args = parser.parse_args()

    if args.live:
        live_monitor(args.interval)
    elif args.report:
        daily_report()
    else:
        one_time_check()


if __name__ == "__main__":
    main()
