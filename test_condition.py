#!/usr/bin/env python3
"""
조건검색식 테스트

새로 만든 조건식이 제대로 작동하는지 확인
"""

import asyncio
import sys
from datetime import datetime
from rich.console import Console
from rich.table import Table

console = Console()

# main_auto_trading.py에서 필요한 부분만 가져오기
sys.path.insert(0, '/home/greatbps/projects/kiwoom_trading')

from main_auto_trading import IntegratedTradingSystem


async def test_condition(condition_name: str = "bottom"):
    """조건검색식 테스트"""

    console.print()
    console.print("="*80, style="bold cyan")
    console.print(f"{'🧪 조건검색식 테스트':^80}", style="bold cyan")
    console.print("="*80, style="bold cyan")
    console.print()

    # 시스템 초기화
    console.print("[yellow]시스템 초기화 중...[/yellow]")
    system = IntegratedTradingSystem(
        condition_indices=[],  # 일단 빈 리스트로 시작
        use_live=False
    )

    try:
        # WebSocket 연결
        console.print("[1/3] WebSocket 연결 중...")
        await system.connect()

        # 로그인
        console.print("[2/3] 로그인 중...")
        if not await system.login(max_retries=2):
            console.print("[red]❌ 로그인 실패[/red]")
            return

        # 조건식 목록 조회
        console.print("[3/3] 조건검색식 목록 조회 중...")
        if not await system.get_condition_list():
            console.print("[red]❌ 조건식 목록 조회 실패[/red]")
            return

        console.print()
        console.print("="*80, style="bold green")
        console.print(f"{'📋 전체 조건검색식 목록':^80}", style="bold green")
        console.print("="*80, style="bold green")
        console.print()

        # 조건식 목록 표시
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("번호", style="cyan", width=6)
        table.add_column("SEQ", style="dim", width=10)
        table.add_column("조건식 명칭", style="green", width=50)

        target_condition = None
        target_idx = None

        for idx, condition in enumerate(system.condition_list):
            seq = condition[0] if len(condition) > 0 else "?"
            name = condition[1] if len(condition) > 1 else "?"

            # bottom 전략 찾기
            if condition_name.lower() in name.lower():
                table.add_row(
                    f"[bold yellow]{idx}[/bold yellow]",
                    f"[bold yellow]{seq}[/bold yellow]",
                    f"[bold yellow]{name} ← 🎯 TARGET[/bold yellow]"
                )
                target_condition = condition
                target_idx = idx
            else:
                table.add_row(str(idx), seq, name)

        console.print(table)
        console.print()

        # bottom 전략 찾았는지 확인
        if target_condition is None:
            console.print(f"[red]❌ '{condition_name}' 조건식을 찾을 수 없습니다.[/red]")
            console.print()
            console.print("[yellow]💡 조건식 이름을 정확히 입력하거나 키움 HTS에서 확인하세요.[/yellow]")
            return

        seq = target_condition[0]
        name = target_condition[1]

        console.print("="*80, style="bold yellow")
        console.print(f"{'🎯 조건검색 실행':^80}", style="bold yellow")
        console.print("="*80, style="bold yellow")
        console.print()
        console.print(f"[cyan]조건식:[/cyan] [{target_idx}] {name} (seq: {seq})")
        console.print()

        # 조건검색 실행
        console.print(f"[yellow]🔍 검색 중...[/yellow]")
        start_time = datetime.now()

        stocks = await system.search_condition(seq, name)

        elapsed = (datetime.now() - start_time).total_seconds()

        console.print()
        console.print("="*80, style="bold green")
        console.print(f"{'✅ 조건검색 결과':^80}", style="bold green")
        console.print("="*80, style="bold green")
        console.print()

        if not stocks:
            console.print(f"[yellow]📭 검색 결과 없음 (검색 시간: {elapsed:.2f}초)[/yellow]")
            console.print()
            console.print("[dim]💡 가능한 원인:[/dim]")
            console.print("[dim]   - 현재 시간에 조건을 만족하는 종목이 없음[/dim]")
            console.print("[dim]   - 장 시간 외 (장 중에 다시 시도)[/dim]")
            console.print("[dim]   - 조건식 설정 확인 필요[/dim]")
        else:
            console.print(f"[green]🎉 총 {len(stocks)}개 종목 검색됨 (검색 시간: {elapsed:.2f}초)[/green]")
            console.print()

            # 결과 테이블
            result_table = Table(show_header=True, header_style="bold magenta")
            result_table.add_column("번호", style="cyan", width=6)
            result_table.add_column("종목코드", style="yellow", width=10)
            result_table.add_column("종목명", style="green", width=30)

            for i, stock in enumerate(stocks[:20], 1):  # 최대 20개만 표시
                code = stock.get('stk_cd', '')
                name = stock.get('stk_nm', '')
                result_table.add_row(str(i), code, name)

            console.print(result_table)

            if len(stocks) > 20:
                console.print()
                console.print(f"[dim]... 외 {len(stocks) - 20}개 종목[/dim]")

            console.print()
            console.print("[green]✅ 조건검색식이 정상적으로 작동합니다![/green]")

        console.print()
        console.print("="*80, style="bold cyan")

    except Exception as e:
        console.print()
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()

    finally:
        # WebSocket 종료
        if system.websocket:
            await system.websocket.close()
        console.print()


async def main():
    """메인 실행"""
    # 명령행 인자로 조건식 이름 지정 가능
    condition_name = sys.argv[1] if len(sys.argv) > 1 else "bottom"

    await test_condition(condition_name)


if __name__ == "__main__":
    asyncio.run(main())
