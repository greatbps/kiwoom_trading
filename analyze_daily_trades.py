#!/usr/bin/env python3
"""
일일 거래 분석기

- 장 종료 시 자동 실행
- 수동 실행 가능
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()


def analyze_today_trades(date_str: str = None):
    """오늘 거래 분석

    Args:
        date_str: 분석할 날짜 (YYYY-MM-DD), None이면 오늘
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    console.print()
    console.print(f"[bold cyan]{'='*80}[/bold cyan]")
    console.print(f"[bold cyan]📊 {date_str} 거래 분석[/bold cyan]")
    console.print(f"[bold cyan]{'='*80}[/bold cyan]")
    console.print()

    # risk_log.json 로드
    risk_log_path = Path("data/risk_log.json")

    if not risk_log_path.exists():
        console.print("[red]❌ data/risk_log.json 파일을 찾을 수 없습니다.[/red]")
        return None

    try:
        with open(risk_log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        console.print(f"[red]❌ 파일 읽기 실패: {e}[/red]")
        return None

    # 오늘 날짜와 일치하는지 확인
    log_date = data.get('today', '')

    if log_date != date_str:
        console.print(f"[yellow]⚠️  로그 날짜 불일치: 로그={log_date}, 요청={date_str}[/yellow]")
        console.print(f"[yellow]   가장 최근 데이터({log_date})를 분석합니다.[/yellow]")
        console.print()
        date_str = log_date

    trades = data.get('daily_trades', [])
    daily_pnl = data.get('daily_realized_pnl', 0.0)

    if not trades:
        console.print(f"[yellow]📭 {date_str}에 거래 내역이 없습니다.[/yellow]")
        console.print()
        return None

    # ========================================
    # 1. 거래 요약
    # ========================================
    buy_count = len([t for t in trades if t['type'] == 'BUY'])
    sell_count = len([t for t in trades if t['type'] == 'SELL'])

    console.print("[bold]📋 거래 요약[/bold]")
    console.print(f"  • 총 거래: {len(trades)}건 (BUY {buy_count}건, SELL {sell_count}건)")
    # 🔧 FIX: Rich markup 에러 방지 - 색상을 변수로 분리
    pnl_color = 'green' if daily_pnl >= 0 else 'red'
    console.print(f"  • 실현 손익: [{pnl_color}]{daily_pnl:+,.0f}원[/{pnl_color}]")
    console.print()

    # ========================================
    # 2. 거래 상세 테이블
    # ========================================
    table = Table(title=f"{date_str} 거래 내역", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=4)
    table.add_column("시간", width=10)
    table.add_column("유형", width=6)
    table.add_column("종목", width=20)
    table.add_column("수량", justify="right", width=8)
    table.add_column("가격", justify="right", width=12)
    table.add_column("P&L", justify="right", width=12)
    table.add_column("비고", width=20)

    # GPT 개선 사항 체크
    midday_violations = []

    for i, trade in enumerate(trades, 1):
        ts = datetime.fromisoformat(trade['timestamp'])
        time_str = ts.strftime('%H:%M:%S')

        # 점심시간 체크
        note = ""
        if trade['type'] == 'BUY' and 12 <= ts.hour < 14:
            note = "⚠️ 점심시간"
            midday_violations.append(trade)

        pnl_color = "green" if trade['realized_pnl'] >= 0 else "red"

        table.add_row(
            str(i),
            time_str,
            trade['type'],
            f"{trade['stock_name']} ({trade['stock_code']})",
            f"{trade['quantity']}주",
            f"{trade['price']:,.0f}원",
            f"[{pnl_color}]{trade['realized_pnl']:+,.0f}원[/{pnl_color}]",
            note
        )

    console.print(table)
    console.print()

    # ========================================
    # 3. GPT 개선 사항 체크
    # ========================================
    console.print("[bold cyan]🔍 GPT 개선 사항 체크[/bold cyan]")
    console.print()

    issues = []

    # 점심시간 진입 체크
    if midday_violations:
        console.print(f"[red]❌ 점심시간 진입 ({len(midday_violations)}건)[/red]")
        for t in midday_violations:
            ts = datetime.fromisoformat(t['timestamp'])
            console.print(f"   - {ts.strftime('%H:%M:%S')} {t['stock_name']}")
        issues.append(f"점심시간 진입 {len(midday_violations)}건")
    else:
        console.print("[green]✅ 점심시간 진입 차단 정상 작동[/green]")

    # 종목별 거래 횟수 체크
    stock_trade_count = {}
    for t in trades:
        if t['type'] == 'BUY':
            stock_code = t['stock_code']
            stock_trade_count[stock_code] = stock_trade_count.get(stock_code, 0) + 1

    over_limit_stocks = {k: v for k, v in stock_trade_count.items() if v > 2}

    if over_limit_stocks:
        console.print(f"[red]❌ 종목별 일일 한도 초과 ({len(over_limit_stocks)}종목)[/red]")
        for code, count in over_limit_stocks.items():
            stock_name = next((t['stock_name'] for t in trades if t['stock_code'] == code), code)
            console.print(f"   - {stock_name}: {count}회 (한도: 2회)")
        issues.append(f"일일 한도 초과 {len(over_limit_stocks)}종목")
    else:
        console.print("[green]✅ 종목별 일일 한도 준수[/green]")

    console.print()

    # ========================================
    # 4. 종목별 손익
    # ========================================
    stock_pnl = {}
    for t in trades:
        if t['type'] == 'SELL':
            key = f"{t['stock_name']} ({t['stock_code']})"
            stock_pnl[key] = stock_pnl.get(key, 0) + t['realized_pnl']

    if stock_pnl:
        console.print("[bold]📈 종목별 손익[/bold]")
        for stock, pnl in sorted(stock_pnl.items(), key=lambda x: x[1], reverse=True):
            color = "green" if pnl >= 0 else "red"
            console.print(f"  • {stock}: [{color}]{pnl:+,.0f}원[/{color}]")
        console.print()

    # ========================================
    # 5. 결론
    # ========================================
    console.print(f"[bold cyan]{'='*80}[/bold cyan]")

    if issues:
        console.print(f"[yellow]⚠️  발견된 문제: {len(issues)}건[/yellow]")
        for issue in issues:
            console.print(f"[yellow]   - {issue}[/yellow]")
    else:
        console.print("[green]✅ 모든 GPT 개선 사항 정상 작동 중[/green]")

    console.print(f"[bold cyan]{'='*80}[/bold cyan]")
    console.print()

    # 결과 반환
    return {
        'date': date_str,
        'total_trades': len(trades),
        'buy_count': buy_count,
        'sell_count': sell_count,
        'realized_pnl': daily_pnl,
        'issues': issues,
        'midday_violations': len(midday_violations),
        'over_limit_stocks': len(over_limit_stocks)
    }


def main():
    """메인 실행"""
    # 명령행 인자로 날짜 지정 가능
    date_str = sys.argv[1] if len(sys.argv) > 1 else None

    result = analyze_today_trades(date_str)

    if result is None:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
