#!/usr/bin/env python3
"""조건검색식 목록 조회"""

import asyncio
from core.kiwoom_websocket import KiwoomWebSocket
from rich.console import Console

console = Console()


async def get_conditions():
    """조건식 목록 조회"""
    ws = KiwoomWebSocket()

    try:
        # 연결 및 로그인
        await ws.connect()
        await ws.login()

        # 조건식 목록 조회
        await ws.send_message("CNSRLST")
        response = await ws.receive_message(timeout=10.0)

        if response and response.get("return_code") == 0:
            conditions = response.get("data", [])

            console.print(f"\n[bold cyan]📋 총 {len(conditions)}개 조건검색식[/bold cyan]\n")

            # 17-22번 조건식 표시
            target_indices = [17, 18, 19, 20, 21, 22]

            for idx in target_indices:
                if idx < len(conditions):
                    seq = conditions[idx][0] if len(conditions[idx]) > 0 else "?"
                    name = conditions[idx][1] if len(conditions[idx]) > 1 else "?"
                    console.print(f"  [green]{idx:2d}번[/green]: {name} [dim](seq: {seq})[/dim]")
                else:
                    console.print(f"  [red]{idx}번: 없음[/red]")

            console.print()

        else:
            console.print("[red]❌ 조건식 조회 실패[/red]")

    except Exception as e:
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()
    finally:
        if ws.websocket:
            await ws.websocket.close()


if __name__ == "__main__":
    asyncio.run(get_conditions())
