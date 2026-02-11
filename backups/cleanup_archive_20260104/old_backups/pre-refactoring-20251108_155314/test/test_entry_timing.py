"""
진입 타이밍 분석기 테스트
VWAP 기반 매수/매도 시그널 테스트
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()

def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]🎯 VWAP 진입 타이밍 분석 테스트[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    # API 초기화
    console.print("[1] API 초기화...")
    api = KiwoomAPI()
    api.get_access_token()
    console.print("  ✓ 토큰 발급 완료\n")

    # 분석기 초기화
    analyzer = EntryTimingAnalyzer()

    # 테스트 종목
    test_stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
        {"code": "005070", "name": "코스모신소재"},
    ]

    console.print("[2] 일봉 데이터 조회 및 VWAP 분석 (테스트용)\n")
    console.print("[yellow]※ 실제로는 5분봉 데이터를 사용합니다 (장 시간에만 가능)[/yellow]\n")

    results = []

    for stock in test_stocks:
        console.print(f"{'─' * 80}")
        console.print(f"[bold yellow]{stock['name']} ({stock['code']})[/bold yellow]")

        try:
            # 일봉 데이터 조회 (테스트용 - 실제로는 5분봉 사용)
            chart_result = api.get_daily_chart(
                stock_code=stock['code']
            )

            if chart_result.get('return_code') != 0:
                console.print(f"  [red]✗ 데이터 조회 실패: {chart_result.get('return_msg')}[/red]\n")
                continue

            chart_data = chart_result.get('stk_dt_pole_chart_qry', [])

            if not chart_data:
                console.print(f"  [red]✗ 차트 데이터 없음[/red]\n")
                continue

            console.print(f"  ✓ 일봉 데이터: {len(chart_data)}개 (테스트용)")

            # 진입 타이밍 분석
            timing_result = analyzer.analyze_entry_timing(stock['code'], chart_data)

            console.print(f"  • 현재가: {timing_result['current_price']:,.0f}원")
            console.print(f"  • VWAP: {timing_result['vwap']:,.0f}원")
            console.print(f"  • 가격 vs VWAP: {timing_result['price_vs_vwap']:+.2f}%")
            console.print(f"  • 시그널: {timing_result['signal']} ({'매수' if timing_result['signal'] == 1 else '매도' if timing_result['signal'] == -1 else '관망'})")
            console.print(f"  • {timing_result['recommendation']}")

            results.append({
                'name': stock['name'],
                'code': stock['code'],
                **timing_result
            })

        except Exception as e:
            console.print(f"  [red]✗ 오류: {e}[/red]")
            import traceback
            traceback.print_exc()

        console.print()

    # 결과 테이블
    if results:
        console.print(f"\n{'=' * 80}")
        console.print()

        table = Table(
            title="📊 VWAP 진입 타이밍 분석 결과",
            box=box.ROUNDED,
            border_style="cyan",
            show_header=True,
            header_style="bold magenta"
        )

        table.add_column("종목명", style="cyan", width=12)
        table.add_column("현재가", justify="right", style="white", width=12)
        table.add_column("VWAP", justify="right", style="yellow", width=12)
        table.add_column("차이", justify="right", style="white", width=10)
        table.add_column("시그널", justify="center", width=8)
        table.add_column("진입 가능", justify="center", width=10)

        for r in results:
            # 시그널 표시
            if r['signal'] == 1:
                signal_text = "[bold green]매수[/bold green]"
                signal_icon = "🟢"
            elif r['signal'] == -1:
                signal_text = "[bold red]매도[/bold red]"
                signal_icon = "🔴"
            else:
                signal_text = "[yellow]관망[/yellow]"
                signal_icon = "🟡"

            # 진입 가능 여부
            can_enter_text = "[bold green]✅ YES[/bold green]" if r['can_enter'] else "[dim]❌ NO[/dim]"

            # 차이 색상
            diff = r['price_vs_vwap']
            if diff > 0:
                diff_text = f"[green]+{diff:.2f}%[/green]"
            elif diff < 0:
                diff_text = f"[red]{diff:.2f}%[/red]"
            else:
                diff_text = f"{diff:.2f}%"

            table.add_row(
                r['name'],
                f"{r['current_price']:,.0f}원",
                f"{r['vwap']:,.0f}원",
                diff_text,
                f"{signal_icon} {signal_text}",
                can_enter_text
            )

        console.print(table)
        console.print()

        # 매수 가능 종목 요약
        buy_candidates = [r for r in results if r['can_enter']]

        if buy_candidates:
            console.print(Panel(
                f"[bold green]✅ 매수 가능 종목: {len(buy_candidates)}개[/bold green]\n\n" +
                "\n".join([f"  • {r['name']}: {r['recommendation']}" for r in buy_candidates]),
                title="[bold green]매수 시그널 발생[/bold green]",
                border_style="green",
                box=box.DOUBLE
            ))
        else:
            console.print(Panel(
                "[yellow]현재 매수 시그널 발생 종목 없음\n\n진입 타이밍 대기 중...[/yellow]",
                title="[yellow]⏸️ 관망[/yellow]",
                border_style="yellow"
            ))

    console.print(f"\n{'=' * 80}")
    console.print("✓ 테스트 완료")
    console.print(f"{'=' * 80}\n")

if __name__ == "__main__":
    main()
