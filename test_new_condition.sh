#!/bin/bash

# 새로운 조건검색식 테스트
# 사용법: ./test_new_condition.sh "조건식명"

CONDITION_NAME="${1:-bottom}"

echo "================================="
echo "🧪 조건검색식 테스트: $CONDITION_NAME"
echo "================================="
echo ""

# main_auto_trading.py를 테스트 모드로 실행
# --skip-wait: 대기 시간 건너뛰기
# --dry-run: 실제 매매 없이 조건검색만

python3 << EOF
import asyncio
from main_auto_trading import IntegratedTradingSystem
from core.kiwoom_rest_client import KiwoomRESTClient
from rich.console import Console
import os
from dotenv import load_dotenv

load_dotenv()

console = Console()

async def test():
    # API 클라이언트
    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    api = KiwoomRESTClient(app_key, app_secret)

    # 토큰 발급
    console.print("[1/2] Access Token 발급 중...")
    api.token_cache_file.unlink(missing_ok=True)  # 캐시 삭제
    access_token = await api.get_access_token()

    if not access_token:
        console.print("[red]❌ Token 발급 실패[/red]")
        return

    console.print(f"[green]✓ Token: {access_token[:20]}...[/green]")
    console.print()

    # 시스템 초기화 (조건식 없이)
    console.print("[2/2] 시스템 초기화 중...")
    system = IntegratedTradingSystem(access_token, api, [], skip_wait=True)

    try:
        # WebSocket 연결 및 로그인
        await system.connect()
        await system.login()

        # 조건식 목록 조회
        await system.get_condition_list()

        console.print()
        console.print("="*80, style="bold yellow")
        console.print(f"{'🔍 \"$CONDITION_NAME\" 조건식 검색':^80}", style="bold yellow")
        console.print("="*80, style="bold yellow")
        console.print()

        # 조건식 찾기
        target = None
        for idx, cond in enumerate(system.condition_list):
            seq, name = cond[0], cond[1]
            if "$CONDITION_NAME".lower() in name.lower():
                target = (idx, seq, name)
                console.print(f"[green]✓ 찾음: [{idx}] {name} (seq: {seq})[/green]")
                break

        if not target:
            console.print(f"[red]❌ \"$CONDITION_NAME\" 조건식을 찾을 수 없습니다.[/red]")
            console.print()
            console.print("[yellow]📋 전체 조건식 목록:[/yellow]")
            for idx, cond in enumerate(system.condition_list):
                console.print(f"  [{idx}] {cond[1]}")
            return

        idx, seq, name = target
        console.print()

        # 조건검색 실행
        console.print(f"[yellow]🔍 조건검색 실행 중...[/yellow]")
        stocks = await system.search_condition(seq, name)

        console.print()
        console.print("="*80, style="bold green")
        console.print(f"{'✅ 검색 결과':^80}", style="bold green")
        console.print("="*80, style="bold green")
        console.print()

        if not stocks:
            console.print("[yellow]📭 검색 결과 없음[/yellow]")
            console.print()
            console.print("[dim]💡 가능한 원인:[/dim]")
            console.print("[dim]   - 현재 시간에 조건을 만족하는 종목이 없음[/dim]")
            console.print("[dim]   - 장 시간 외 (09:00-15:30 장 중에 다시 시도)[/dim]")
        else:
            console.print(f"[green]🎉 총 {len(stocks)}개 종목 검색됨[/green]")
            console.print()

            from rich.table import Table
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("번호", width=6)
            table.add_column("종목코드", width=10)

            for i, code in enumerate(stocks[:30], 1):
                table.add_row(str(i), code)

            console.print(table)

            if len(stocks) > 30:
                console.print(f"[dim]... 외 {len(stocks)-30}개[/dim]")

            console.print()
            console.print("[green]✅ 조건검색식이 정상 작동합니다![/green]")

        console.print()

    finally:
        if system.websocket:
            await system.websocket.close()

asyncio.run(test())
EOF
