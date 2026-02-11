#!/usr/bin/env python3
"""
브로커 추상화 레이어 테스트
==========================

각 브로커의 기본 기능 테스트
"""

import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from brokers import get_broker, BrokerType, Market

console = Console()


def test_broker(broker_type: BrokerType):
    """개별 브로커 테스트"""
    console.print(f"\n[bold cyan]{'='*50}[/bold cyan]")
    console.print(f"[bold]테스트: {broker_type.value}[/bold]")
    console.print(f"[bold cyan]{'='*50}[/bold cyan]")

    broker = get_broker(broker_type)
    console.print(f"브로커: {broker}")

    # 1. 초기화
    console.print("\n[yellow]1. 초기화...[/yellow]")
    if not broker.initialize():
        console.print("[red]❌ 초기화 실패[/red]")
        return False
    console.print("[green]✅ 초기화 성공[/green]")

    # 2. 시장 상태
    console.print("\n[yellow]2. 시장 상태...[/yellow]")
    is_open, status = broker.is_market_open()
    console.print(f"   개장: {'✅' if is_open else '❌'} {status}")

    # 3. 포지션 조회
    console.print("\n[yellow]3. 포지션 조회...[/yellow]")
    positions = broker.get_positions()

    if not positions:
        console.print("   [dim]보유 종목 없음[/dim]")
    else:
        table = Table(title=f"보유 종목 ({len(positions)}개)")
        table.add_column("종목", style="cyan")
        table.add_column("수량", justify="right")
        table.add_column("평균가", justify="right")
        table.add_column("현재가", justify="right")
        table.add_column("수익률", justify="right")

        for pos in positions:
            profit_style = "green" if pos.profit_pct >= 0 else "red"
            table.add_row(
                f"{pos.name[:12]} ({pos.symbol})",
                f"{pos.quantity:,}",
                f"{pos.avg_price:,.0f}" if broker.market == Market.KR else f"${pos.avg_price:.2f}",
                f"{pos.current_price:,.0f}" if broker.market == Market.KR else f"${pos.current_price:.2f}",
                f"[{profit_style}]{pos.profit_pct:+.2f}%[/{profit_style}]"
            )

        console.print(table)

    # 4. 잔고 조회
    console.print("\n[yellow]4. 잔고 조회...[/yellow]")
    balance = broker.get_balance()
    console.print(f"   총평가: {balance.total_eval:,.0f} {balance.currency}")
    console.print(f"   예수금: {balance.total_deposit:,.0f} {balance.currency}")
    console.print(f"   주문가능: {balance.available_cash:,.0f} {balance.currency}")

    return True


def main():
    console.print(Panel(
        "[bold]브로커 추상화 레이어 테스트[/bold]\n\n"
        "각 브로커의 초기화, 조회 기능 검증",
        title="🧪 Broker Test",
        border_style="blue"
    ))

    results = {}

    # 키움 테스트
    try:
        results['KIWOOM'] = test_broker(BrokerType.KIWOOM)
    except Exception as e:
        console.print(f"[red]❌ 키움 테스트 실패: {e}[/red]")
        results['KIWOOM'] = False

    # 한투 국내 테스트
    try:
        results['KIS_DOMESTIC'] = test_broker(BrokerType.KIS_DOMESTIC)
    except Exception as e:
        console.print(f"[red]❌ 한투 국내 테스트 실패: {e}[/red]")
        results['KIS_DOMESTIC'] = False

    # 한투 해외 테스트
    try:
        results['KIS_OVERSEAS'] = test_broker(BrokerType.KIS_OVERSEAS)
    except Exception as e:
        console.print(f"[red]❌ 한투 해외 테스트 실패: {e}[/red]")
        results['KIS_OVERSEAS'] = False

    # 결과 요약
    console.print("\n")
    console.print(Panel(
        "\n".join([
            f"{'✅' if v else '❌'} {k}"
            for k, v in results.items()
        ]),
        title="📋 테스트 결과",
        border_style="green" if all(results.values()) else "red"
    ))


if __name__ == "__main__":
    main()
