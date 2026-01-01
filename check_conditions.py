#!/usr/bin/env python3
"""
키움 조건검색식 목록 조회

실제 등록된 조건검색식 번호를 확인하여 main_auto_trading.py에서 사용할 인덱스를 결정합니다.
"""
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from kiwoom_api import KiwoomAPI
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv()
console = Console()


async def main():
    """조건검색식 목록 조회"""

    console.print()
    console.print("=" * 80)
    console.print("🔍 키움 조건검색식 목록 조회", style="bold cyan")
    console.print("=" * 80)
    console.print()

    try:
        # 1. API 초기화
        console.print("[1/3] 키움 API 초기화 중...", style="yellow")
        api = KiwoomAPI()

        # 2. 토큰 발급
        console.print("[2/3] 토큰 발급 중...", style="yellow")
        token_response = api.get_access_token()

        if not token_response or 'access_token' not in token_response:
            console.print("[red]❌ 토큰 발급 실패[/red]")
            return

        access_token = token_response['access_token']
        console.print("[green]✅ 토큰 발급 성공[/green]")

        # 3. 조건검색식 목록 조회
        console.print("[3/3] 조건검색식 목록 조회 중...", style="yellow")

        response = api.get_condition_list(access_token)

        if not response or response.get('return_code') != 0:
            console.print("[red]❌ 조건검색식 조회 실패[/red]")
            return

        condition_list = response.get("data", [])

        console.print()
        console.print("=" * 80)
        console.print(f"✅ 총 {len(condition_list)}개 조건검색식 발견", style="bold green")
        console.print("=" * 80)
        console.print()

        if not condition_list:
            console.print("[yellow]⚠️  등록된 조건검색식이 없습니다.[/yellow]")
            console.print("[yellow]   키움 HTS에서 조건검색식을 먼저 등록해주세요.[/yellow]")
            return

        # 테이블 생성
        table = Table(title="조건검색식 목록", show_header=True, header_style="bold magenta")
        table.add_column("인덱스", style="cyan", justify="center", width=8)
        table.add_column("Seq", style="yellow", justify="center", width=10)
        table.add_column("조건식 이름", style="green", width=40)
        table.add_column("사용", style="white", justify="center", width=8)

        for idx, condition in enumerate(condition_list):
            seq = condition[0] if len(condition) > 0 else "?"
            name = condition[1] if len(condition) > 1 else "?"
            table.add_row(str(idx), str(seq), name, "✓")

        console.print(table)
        console.print()

        # 사용 예시
        console.print("=" * 80)
        console.print("💡 사용 방법", style="bold yellow")
        console.print("=" * 80)
        console.print()

        if len(condition_list) >= 6:
            # 6개 이상이면 0~5 사용
            example_indices = "0,1,2,3,4,5"
        elif len(condition_list) >= 3:
            # 3개 이상이면 처음 3개 사용
            example_indices = ",".join([str(i) for i in range(min(3, len(condition_list)))])
        else:
            # 그 외에는 모두 사용
            example_indices = ",".join([str(i) for i in range(len(condition_list))])

        console.print(f"위 인덱스 번호를 사용하여 main_auto_trading.py를 실행하세요:")
        console.print()
        console.print(f"[cyan]# 백테스트 모드 (추천 조건식 사용)[/cyan]")
        console.print(f"[white]python3 main_auto_trading.py --dry-run --conditions {example_indices}[/white]")
        console.print()
        console.print(f"[cyan]# 실전 모드 (추천 조건식 사용)[/cyan]")
        console.print(f"[white]python3 main_auto_trading.py --live --conditions {example_indices}[/white]")
        console.print()
        console.print("[dim]※ 원하는 조건식만 선택하려면 인덱스 번호를 쉼표로 구분하세요 (예: 0,2,5)[/dim]")
        console.print()

    except Exception as e:
        console.print()
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️  중단되었습니다.[/yellow]")
