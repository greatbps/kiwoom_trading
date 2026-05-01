#!/usr/bin/env python3
"""
상세 거래 분석기 - 깊이 있는 인사이트 제공
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.markup import escape

console = Console()

DB_PATH = Path("data/trades.db")


def _load_trades_from_db(date_str: str) -> list[dict]:
    """trades.db에서 특정 날짜의 거래를 로드."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT timestamp, stock_code, stock_name, trade_type,
                  quantity, price, quantity * price, realized_pnl, reason
           FROM trades
           WHERE trade_date = ?
           ORDER BY id ASC""",
        (date_str,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            'timestamp':    r[0],
            'stock_code':   r[1],
            'stock_name':   r[2] or r[1],
            'type':         r[3],
            'quantity':     int(r[4] or 0),
            'price':        float(r[5] or 0),
            'amount':       float(r[6] or 0),
            'realized_pnl': float(r[7] or 0),
            'reason':       r[8] or '',
        })
    return result


def analyze_today_detailed(date_str: str = None):
    """상세 거래 분석"""

    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    trades = _load_trades_from_db(date_str)
    daily_pnl = sum(t['realized_pnl'] for t in trades if t['type'] == 'SELL')

    if not trades:
        console.print(f"[yellow]📭 {date_str}에 거래 내역이 없습니다.[/yellow]")
        return

    # ========================================
    # 헤더
    # ========================================
    console.print()
    console.print("="*100, style="bold cyan")
    console.print(f"{'📊 ' + date_str + ' 거래 상세 분석':^100}", style="bold cyan")
    console.print("="*100, style="bold cyan")
    console.print()

    # ========================================
    # 1. 전체 요약
    # ========================================
    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']

    total_buy_amount = sum(t['amount'] for t in buy_trades)
    total_sell_amount = sum(t['amount'] for t in sell_trades)

    # 🔧 FIX: Rich markup 에러 방지 - 색상을 변수로 분리
    pnl_color = 'green' if daily_pnl >= 0 else 'red'

    # 🔧 FIX: Division by zero 방지
    pnl_pct = (daily_pnl / total_buy_amount * 100) if total_buy_amount > 0 else 0.0

    console.print(Panel.fit(
        f"[cyan]총 거래:[/cyan] {len(trades)}건 (BUY {len(buy_trades)}, SELL {len(sell_trades)})\n"
        f"[cyan]총 매수금액:[/cyan] {total_buy_amount:,.0f}원\n"
        f"[cyan]총 매도금액:[/cyan] {total_sell_amount:,.0f}원\n"
        f"[{pnl_color}]실현 손익:[/{pnl_color}] "
        f"[{pnl_color}]{daily_pnl:+,.0f}원 ({pnl_pct:+.2f}%)[/{pnl_color}]",
        title="[bold]📋 거래 요약[/bold]",
        border_style="cyan"
    ))
    console.print()

    # ========================================
    # 2. 거래별 상세 분석
    # ========================================
    console.print("[bold magenta]📝 거래별 상세 분석[/bold magenta]")
    console.print()

    # 종목별로 그룹화
    stocks = {}
    for trade in trades:
        code = trade['stock_code']
        if code not in stocks:
            stocks[code] = {
                'name': trade['stock_name'],
                'trades': [],
                'buys': [],
                'sells': []
            }
        stocks[code]['trades'].append(trade)
        if trade['type'] == 'BUY':
            stocks[code]['buys'].append(trade)
        else:
            stocks[code]['sells'].append(trade)

    # 종목별 분석
    for stock_code, stock_data in stocks.items():
        stock_trades = stock_data['trades']
        stock_name = stock_data['name']
        safe_stock_name = escape(stock_name)  # 🔧 FIX: markup 에러 방지

        console.print(f"[bold yellow]{'─'*100}[/bold yellow]")
        console.print(f"[bold yellow]🔸 {safe_stock_name} ({stock_code})[/bold yellow]")
        console.print(f"[bold yellow]{'─'*100}[/bold yellow]")
        console.print()

        # 매수 분석
        if stock_data['buys']:
            console.print("[cyan]📥 매수 내역:[/cyan]")
            total_qty = 0
            total_amount = 0

            for i, buy in enumerate(stock_data['buys'], 1):
                ts = datetime.fromisoformat(buy['timestamp'])
                qty = buy['quantity']
                price = buy['price']
                amount = buy['amount']

                total_qty += qty
                total_amount += amount

                # 이전 매수와 비교
                price_change = ""
                time_gap = ""
                if i > 1:
                    prev_buy = stock_data['buys'][i-2]
                    prev_price = prev_buy['price']
                    prev_ts = datetime.fromisoformat(prev_buy['timestamp'])

                    price_diff = price - prev_price
                    price_diff_pct = (price_diff / prev_price) * 100
                    time_diff = (ts - prev_ts).total_seconds() / 60

                    if price_diff > 0:
                        price_change = f"[red]▲ +{price_diff:,.0f}원 (+{price_diff_pct:.2f}%)[/red]"
                    elif price_diff < 0:
                        price_change = f"[green]▼ {price_diff:,.0f}원 ({price_diff_pct:.2f}%)[/green]"
                    else:
                        price_change = f"[dim]→ 동일가[/dim]"

                    time_gap = f"[dim]({time_diff:.0f}분 후)[/dim]"

                # 매수 이유 표시
                buy_reason = buy.get('reason', '')
                reason_str = f"[cyan]({buy_reason})[/cyan]" if buy_reason else ""

                console.print(
                    f"  [{i}] {ts.strftime('%H:%M:%S')} - "
                    f"{qty}주 @ {price:,}원 = {amount:,}원 "
                    f"{price_change} {time_gap} {reason_str}"
                )

            avg_price = total_amount / total_qty if total_qty > 0 else 0
            console.print(f"  [bold]→ 총 매수: {total_qty}주 @ 평단가 {avg_price:,.0f}원 (총 {total_amount:,}원)[/bold]")
            console.print()

        # 매도 분석
        if stock_data['sells']:
            console.print("[cyan]📤 매도 내역:[/cyan]")
            total_sell_qty = 0
            total_sell_amount = 0

            for i, sell in enumerate(stock_data['sells'], 1):
                ts = datetime.fromisoformat(sell['timestamp'])
                qty = sell['quantity']
                price = sell['price']
                amount = sell['amount']
                pnl = sell['realized_pnl']

                total_sell_qty += qty
                total_sell_amount += amount

                # 보유 시간 계산 (첫 매수부터)
                if stock_data['buys']:
                    first_buy_ts = datetime.fromisoformat(stock_data['buys'][0]['timestamp'])
                    hold_time = (ts - first_buy_ts).total_seconds() / 60
                    hold_time_str = f"{int(hold_time)}분" if hold_time < 60 else f"{hold_time/60:.1f}시간"
                else:
                    hold_time_str = "?"

                # 손익률 계산
                buy_price = (amount - pnl) / qty if qty > 0 else 0
                pnl_pct = (pnl / (amount - pnl)) * 100 if (amount - pnl) != 0 else 0

                pnl_color = "green" if pnl >= 0 else "red"

                # 매도 이유 표시
                sell_reason = sell.get('reason', '')
                reason_str = f"[magenta]({sell_reason})[/magenta]" if sell_reason else ""

                console.print(
                    f"  [{i}] {ts.strftime('%H:%M:%S')} - "
                    f"{qty}주 @ {price:,}원 (매수가: {buy_price:,.0f}원) "
                    f"[{pnl_color}]P&L: {pnl:+,.0f}원 ({pnl_pct:+.2f}%)[/{pnl_color}] "
                    f"[dim](보유 {hold_time_str})[/dim] {reason_str}"
                )

            console.print()

        # 미결제 포지션
        total_buy_qty = sum(b['quantity'] for b in stock_data['buys'])
        total_sell_qty = sum(s['quantity'] for s in stock_data['sells'])
        remaining = total_buy_qty - total_sell_qty

        if remaining > 0:
            console.print(f"[yellow]⚠️  미결제 포지션: {remaining}주[/yellow]")

            # 미결제 포지션의 평단가 계산
            if stock_data['buys']:
                total_buy_amount = sum(b['amount'] for b in stock_data['buys'])
                sold_amount = sum(s['amount'] - s['realized_pnl'] for s in stock_data['sells'])
                remaining_amount = total_buy_amount - sold_amount
                avg_buy_price = remaining_amount / remaining if remaining > 0 else 0

                console.print(f"   평단가: {avg_buy_price:,.0f}원 (투자금: {remaining_amount:,.0f}원)")
            console.print()

        # 종목별 총 손익
        stock_pnl = sum(s['realized_pnl'] for s in stock_data['sells'])
        stock_pnl_color = "green" if stock_pnl >= 0 else "red"
        # 🔧 FIX: safe_stock_name은 이미 위에서 생성됨
        console.print(f"[{stock_pnl_color}]💰 {safe_stock_name} 실현 손익: {stock_pnl:+,.0f}원[/{stock_pnl_color}]")
        console.print()

    # ========================================
    # 3. 시간대별 거래 패턴
    # ========================================
    console.print("[bold magenta]⏰ 시간대별 거래 패턴[/bold magenta]")
    console.print()

    # 시간대별 분류
    morning = []  # 09:00-12:00
    midday = []   # 12:00-14:00
    afternoon = [] # 14:00-15:30

    for trade in trades:
        ts = datetime.fromisoformat(trade['timestamp'])
        hour = ts.hour

        if hour < 12:
            morning.append(trade)
        elif hour < 14:
            midday.append(trade)
        else:
            afternoon.append(trade)

    console.print(f"  오전 (09:00-12:00): {len(morning)}건")
    console.print(f"  점심 (12:00-14:00): {len(midday)}건 {'[red]⚠️ GPT 개선사항 위반![/red]' if midday else '[green]✓[/green]'}")
    console.print(f"  오후 (14:00-15:30): {len(afternoon)}건")
    console.print()

    # ========================================
    # 4. 문제점 및 개선사항
    # ========================================
    console.print("[bold red]⚠️  발견된 문제점[/bold red]")
    console.print()

    issues = []

    # 점심시간 거래 체크
    if midday:
        console.print(f"  [red]❌ 점심시간 거래 {len(midday)}건 발생[/red]")
        for t in midday:
            ts = datetime.fromisoformat(t['timestamp'])
            safe_name = escape(t['stock_name'])  # 🔧 FIX: markup 에러 방지
            console.print(f"     - {ts.strftime('%H:%M:%S')} {t['type']} {safe_name}")
        issues.append("점심시간 거래")
        console.print()

    # 추가 매수 분석 (평단가 상승)
    for stock_code, stock_data in stocks.items():
        if len(stock_data['buys']) > 1:
            buys = stock_data['buys']
            for i in range(1, len(buys)):
                if buys[i]['price'] > buys[i-1]['price']:
                    price_increase = buys[i]['price'] - buys[i-1]['price']
                    pct = (price_increase / buys[i-1]['price']) * 100

                    console.print(
                        f"  [yellow]⚠️  {stock_data['name']}: 추가 매수 시 평단가 상승[/yellow]"
                    )
                    console.print(
                        f"     - {buys[i-1]['price']:,}원 → {buys[i]['price']:,}원 "
                        f"(+{price_increase:,}원, +{pct:.2f}%)"
                    )
                    issues.append(f"{stock_data['name']} 평단가 상승")
                    console.print()

    # 짧은 보유 시간
    for stock_code, stock_data in stocks.items():
        if stock_data['buys'] and stock_data['sells']:
            first_buy = datetime.fromisoformat(stock_data['buys'][0]['timestamp'])
            last_sell = datetime.fromisoformat(stock_data['sells'][-1]['timestamp'])
            hold_minutes = (last_sell - first_buy).total_seconds() / 60

            if hold_minutes < 30:
                console.print(
                    f"  [yellow]⚠️  {stock_data['name']}: 짧은 보유 시간 ({hold_minutes:.0f}분)[/yellow]"
                )
                console.print(f"     - 목표: 30분 이상 보유")
                issues.append(f"{stock_data['name']} 짧은 보유")
                console.print()

    if not issues:
        console.print("  [green]✅ 발견된 문제 없음[/green]")
        console.print()

    # ========================================
    # 5. 결론 및 제안
    # ========================================
    console.print("[bold cyan]📌 결론 및 개선 제안[/bold cyan]")
    console.print()

    if daily_pnl < 0:
        console.print(f"  [red]📉 오늘 손실: {daily_pnl:,.0f}원[/red]")
        console.print()
        console.print("  [yellow]🔧 개선 방향:[/yellow]")

        if midday:
            console.print("     • 점심시간 진입 차단 코드 확인 필요")

        # 추가 매수 관련
        avg_up_trades = [s for s in stocks.values() if len(s['buys']) > 1 and
                        any(s['buys'][i]['price'] > s['buys'][i-1]['price'] for i in range(1, len(s['buys'])))]
        if avg_up_trades:
            console.print("     • 추가 매수 시 평단가 상승 방지 로직 추가")
            console.print("       (가격 하락 시에만 추가 매수 허용)")

        # 보유 시간
        short_holds = [s for s in stocks.values() if s['buys'] and s['sells'] and
                      (datetime.fromisoformat(s['sells'][-1]['timestamp']) -
                       datetime.fromisoformat(s['buys'][0]['timestamp'])).total_seconds() / 60 < 30]
        if short_holds:
            console.print("     • min_hold_time 체크 강화")
            console.print("       (30분 미만 조기 청산 방지)")

    else:
        console.print(f"  [green]📈 오늘 수익: {daily_pnl:,.0f}원[/green]")
        console.print()
        console.print("  [green]✅ 잘된 점:[/green]")
        console.print("     • 수익 실현 성공")
        if not midday:
            console.print("     • 점심시간 거래 차단 정상 작동")

    console.print()
    console.print("="*100, style="bold cyan")
    console.print()


if __name__ == "__main__":
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    analyze_today_detailed(date_str)
