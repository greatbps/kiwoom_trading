#!/usr/bin/env python3
"""
간단한 조건검색식 테스트
"""

import asyncio
import sys
import os
from datetime import datetime
from rich.console import Console
from rich.table import Table

# 환경 변수 로드
from dotenv import load_dotenv
load_dotenv()

console = Console()

# 임포트
from core.kiwoom_rest_client import KiwoomRESTClient
from trading.websocket_client import KiwoomWebSocketClient


async def test_condition(condition_name: str = "bottom"):
    """조건검색식 테스트"""

    console.print()
    console.print("="*80, style="bold cyan")
    console.print(f"{'🧪 조건검색식 테스트':^80}", style="bold cyan")
    console.print("="*80, style="bold cyan")
    console.print()

    # API 클라이언트 초기화
    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    if not app_key or not app_secret:
        console.print("[red]❌ 환경변수 없음 (.env 파일 확인)[/red]")
        return

    api = KiwoomRESTClient(app_key, app_secret)

    # Access Token 가져오기
    console.print("[1/4] Access Token 확인 중...")
    access_token = await api.get_access_token()

    if not access_token:
        console.print("[red]❌ Access Token 없음[/red]")
        return

    console.print(f"[green]✓ Token: {access_token[:20]}...[/green]")
    console.print()

    # WebSocket 연결
    console.print("[2/4] WebSocket 연결 중...")
    ws = KiwoomWebSocketClient(access_token)

    try:
        await ws.connect()
        console.print("[green]✓ 연결 완료[/green]")
        console.print()

        # 로그인
        console.print("[3/4] 로그인 중...")
        await ws.send_message("PINGPONG")
        response = await ws.receive_message(timeout=5.0)

        if response:
            console.print("[green]✓ 로그인 완료[/green]")
        console.print()

        # 조건식 목록 조회
        console.print("[4/4] 조건검색식 목록 조회 중...")
        await ws.send_message("CNSRLST")
        response = await ws.receive_message(timeout=10.0)

        if not response or response.get("return_code") != 0:
            console.print("[red]❌ 조건식 목록 조회 실패[/red]")
            return

        conditions = response.get("data", [])
        console.print(f"[green]✓ 총 {len(conditions)}개 조건식 조회[/green]")
        console.print()

        # 전체 목록 표시
        console.print("="*80, style="bold green")
        console.print(f"{'📋 전체 조건검색식 목록':^80}", style="bold green")
        console.print("="*80, style="bold green")
        console.print()

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("번호", style="cyan", width=6)
        table.add_column("SEQ", style="dim", width=10)
        table.add_column("조건식 명칭", style="green", width=50)

        target_condition = None
        target_idx = None

        for idx, condition in enumerate(conditions):
            seq = condition[0] if len(condition) > 0 else "?"
            name = condition[1] if len(condition) > 1 else "?"

            # bottom 전략 찾기 (대소문자 무시)
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
            console.print("[yellow]💡 위 목록에서 정확한 이름을 확인하세요.[/yellow]")
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

        await ws.send_message("CNSRREQ", {
            "seq": seq,
            "search_type": "1",
            "stex_tp": "K"
        })

        response = await ws.receive_message(timeout=30.0)
        elapsed = (datetime.now() - start_time).total_seconds()

        if not response:
            console.print(f"[red]❌ 응답 없음 (타임아웃 30초)[/red]")
            return

        console.print()

        # 결과 확인
        stocks = response.get("data", [])

        console.print("="*80, style="bold green")
        console.print(f"{'✅ 조건검색 결과':^80}", style="bold green")
        console.print("="*80, style="bold green")
        console.print()

        if not stocks:
            console.print(f"[yellow]📭 검색 결과 없음 (검색 시간: {elapsed:.2f}초)[/yellow]")
            console.print()
            console.print("[dim]💡 가능한 원인:[/dim]")
            console.print("[dim]   - 현재 시간에 조건을 만족하는 종목이 없음[/dim]")
            console.print("[dim]   - 장 시간 외 (09:00-15:30 장 중에 다시 시도)[/dim]")
            console.print("[dim]   - 조건식 설정 확인 필요 (HTS에서 확인)[/dim]")
        else:
            console.print(f"[green]🎉 총 {len(stocks)}개 종목 검색됨 (검색 시간: {elapsed:.2f}초)[/green]")
            console.print()

            # 결과 테이블
            result_table = Table(show_header=True, header_style="bold magenta")
            result_table.add_column("번호", style="cyan", width=6)
            result_table.add_column("종목코드", style="yellow", width=10)
            result_table.add_column("종목명", style="green", width=30)

            for i, stock_code in enumerate(stocks[:30], 1):  # 최대 30개만 표시
                # 종목 코드만 있을 수도 있음
                if isinstance(stock_code, dict):
                    code = stock_code.get('stk_cd', '')
                    name = stock_code.get('stk_nm', '')
                else:
                    code = stock_code
                    name = ''

                result_table.add_row(str(i), code, name)

            console.print(result_table)

            if len(stocks) > 30:
                console.print()
                console.print(f"[dim]... 외 {len(stocks) - 30}개 종목[/dim]")

            console.print()
            console.print("[green]✅ 조건검색식이 정상적으로 작동합니다![/green]")

        console.print()
        console.print("="*80, style="bold cyan")
        console.print()

    except Exception as e:
        console.print()
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()

    finally:
        # WebSocket 종료
        if ws.websocket:
            await ws.websocket.close()


async def main():
    """메인 실행"""
    # 명령행 인자로 조건식 이름 지정 가능
    condition_name = sys.argv[1] if len(sys.argv) > 1 else "bottom"

    await test_condition(condition_name)


if __name__ == "__main__":
    asyncio.run(main())
