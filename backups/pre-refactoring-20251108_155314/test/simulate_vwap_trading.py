"""
VWAP 기반 매매 시뮬레이션
삼성전자 일봉 데이터로 백테스팅
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()

def simulate_vwap_trading(stock_code: str, stock_name: str, chart_data: list):
    """
    VWAP 기반 매매 시뮬레이션

    매수: VWAP 상향 돌파
    매도: VWAP 하향 돌파
    """
    analyzer = EntryTimingAnalyzer()

    # DataFrame 준비
    df = analyzer._prepare_dataframe(chart_data)

    # VWAP 계산
    df = analyzer.calculate_vwap(df)

    # 시그널 생성
    df = analyzer.generate_signals(df)

    # 시뮬레이션 변수
    cash = 10000000  # 초기 자본 1000만원
    position = 0  # 보유 주식 수
    avg_price = 0  # 평균 매수가
    trades = []  # 거래 내역

    # 역순 정렬 (과거부터 시작)
    df = df.iloc[::-1].reset_index(drop=True)

    console.print(f"\n{'='*100}")
    console.print(f"[bold cyan]VWAP 기반 매매 시뮬레이션: {stock_name}[/bold cyan]")
    console.print(f"{'='*100}")
    console.print(f"초기 자본: {cash:,}원\n")

    for idx, row in df.iterrows():
        price = row['close']
        vwap = row['vwap']
        signal = row['signal']

        # 매수 시그널 (VWAP 상향 돌파)
        if signal == 1 and position == 0 and cash > 0:
            # 전량 매수
            quantity = int(cash / price)
            if quantity > 0:
                cost = quantity * price
                cash -= cost
                position = quantity
                avg_price = price

                trades.append({
                    'idx': idx,
                    'type': 'BUY',
                    'price': price,
                    'quantity': quantity,
                    'amount': cost,
                    'vwap': vwap,
                    'cash': cash,
                    'position': position
                })

                console.print(f"[{idx:3d}] [bold green]매수[/bold green]: {price:,}원 × {quantity:,}주 = {cost:,}원 (VWAP: {vwap:,.0f}원)")

        # 매도 시그널 (VWAP 하향 돌파)
        elif signal == -1 and position > 0:
            # 전량 매도
            revenue = position * price
            cash += revenue
            profit = revenue - (position * avg_price)
            profit_rate = (profit / (position * avg_price)) * 100

            trades.append({
                'idx': idx,
                'type': 'SELL',
                'price': price,
                'quantity': position,
                'amount': revenue,
                'vwap': vwap,
                'profit': profit,
                'profit_rate': profit_rate,
                'cash': cash,
                'position': 0
            })

            if profit > 0:
                console.print(f"[{idx:3d}] [bold red]매도[/bold red]: {price:,}원 × {position:,}주 = {revenue:,}원 "
                             f"(VWAP: {vwap:,.0f}원) → 수익: [bold green]+{profit:,.0f}원 (+{profit_rate:.2f}%)[/bold green]")
            else:
                console.print(f"[{idx:3d}] [bold red]매도[/bold red]: {price:,}원 × {position:,}주 = {revenue:,}원 "
                             f"(VWAP: {vwap:,.0f}원) → 손실: [bold red]{profit:,.0f}원 ({profit_rate:.2f}%)[/bold red]")

            position = 0
            avg_price = 0

    # 마지막에 포지션이 남아있으면 현재가로 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price
        unrealized_profit = (current_price - avg_price) * position
        unrealized_rate = (unrealized_profit / (position * avg_price)) * 100

        console.print(f"\n[bold yellow]미청산 포지션[/bold yellow]: {position:,}주 @ {avg_price:,}원 "
                     f"(현재가: {current_price:,}원) → {unrealized_profit:+,.0f}원 ({unrealized_rate:+.2f}%)")
    else:
        final_value = cash

    # 결과 요약
    console.print(f"\n{'='*100}")
    console.print(f"[bold cyan]시뮬레이션 결과[/bold cyan]")
    console.print(f"{'='*100}\n")

    # 통계
    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']

    total_profit = sum([t.get('profit', 0) for t in sell_trades])
    win_trades = [t for t in sell_trades if t.get('profit', 0) > 0]
    loss_trades = [t for t in sell_trades if t.get('profit', 0) <= 0]

    win_rate = (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0

    console.print(f"초기 자본:     {10000000:>15,}원")
    console.print(f"최종 자산:     {final_value:>15,.0f}원")
    total_return = final_value - 10000000
    total_return_rate = (total_return / 10000000) * 100

    if total_return > 0:
        console.print(f"총 수익:       [bold green]{total_return:>+15,.0f}원 (+{total_return_rate:.2f}%)[/bold green]")
    else:
        console.print(f"총 손실:       [bold red]{total_return:>+15,.0f}원 ({total_return_rate:.2f}%)[/bold red]")

    console.print(f"\n거래 횟수:     {len(buy_trades)}회")
    console.print(f"승리:          {len(win_trades)}회")
    console.print(f"패배:          {len(loss_trades)}회")
    console.print(f"승률:          {win_rate:.1f}%")

    if win_trades:
        avg_win = sum([t['profit'] for t in win_trades]) / len(win_trades)
        console.print(f"평균 수익:     [green]+{avg_win:,.0f}원[/green]")

    if loss_trades:
        avg_loss = sum([t['profit'] for t in loss_trades]) / len(loss_trades)
        console.print(f"평균 손실:     [red]{avg_loss:,.0f}원[/red]")

    # 거래 내역 테이블
    if trades:
        console.print(f"\n{'='*100}")
        console.print()

        table = Table(
            title="📊 거래 내역",
            box=box.ROUNDED,
            border_style="cyan",
            show_header=True,
            header_style="bold magenta"
        )

        table.add_column("번호", justify="center", style="cyan", width=6)
        table.add_column("구분", justify="center", width=6)
        table.add_column("가격", justify="right", style="white", width=12)
        table.add_column("수량", justify="right", style="yellow", width=10)
        table.add_column("금액", justify="right", style="white", width=15)
        table.add_column("VWAP", justify="right", style="magenta", width=12)
        table.add_column("손익", justify="right", width=15)

        for i, t in enumerate(trades, 1):
            trade_type = "[bold green]매수[/bold green]" if t['type'] == 'BUY' else "[bold red]매도[/bold red]"

            profit_text = "-"
            if t['type'] == 'SELL':
                profit = t.get('profit', 0)
                profit_rate = t.get('profit_rate', 0)
                if profit > 0:
                    profit_text = f"[bold green]+{profit:,.0f}원\n(+{profit_rate:.2f}%)[/bold green]"
                else:
                    profit_text = f"[bold red]{profit:,.0f}원\n({profit_rate:.2f}%)[/bold red]"

            table.add_row(
                f"{i}",
                trade_type,
                f"{t['price']:,}원",
                f"{t['quantity']:,}주",
                f"{t['amount']:,}원",
                f"{t['vwap']:,.0f}원",
                profit_text
            )

        console.print(table)

    console.print(f"\n{'='*100}\n")

    return {
        'initial_capital': 10000000,
        'final_value': final_value,
        'total_return': total_return,
        'total_return_rate': total_return_rate,
        'trade_count': len(buy_trades),
        'win_count': len(win_trades),
        'loss_count': len(loss_trades),
        'win_rate': win_rate
    }


def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]📈 VWAP 매매 시뮬레이션 (백테스팅)[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    # API 초기화
    console.print("[1] API 초기화...")
    api = KiwoomAPI()
    api.get_access_token()
    console.print("  ✓ 토큰 발급 완료\n")

    # 삼성전자 데이터 조회
    stock_code = "005930"
    stock_name = "삼성전자"

    console.print(f"[2] {stock_name} 일봉 데이터 조회...")
    chart_result = api.get_daily_chart(stock_code=stock_code)

    if chart_result.get('return_code') != 0:
        console.print(f"  [red]✗ 데이터 조회 실패: {chart_result.get('return_msg')}[/red]")
        return

    chart_data = chart_result.get('stk_dt_pole_chart_qry', [])
    if not chart_data:
        console.print(f"  [red]✗ 차트 데이터 없음[/red]")
        return

    console.print(f"  ✓ {len(chart_data)}개 데이터 조회 완료")

    # 최근 200일 데이터 사용 (더 많은 거래 기회)
    chart_data = chart_data[:200]

    # 시뮬레이션 실행
    result = simulate_vwap_trading(stock_code, stock_name, chart_data)

    # 최종 요약
    console.print(Panel(
        f"[bold white]초기 자본:[/bold white] {result['initial_capital']:,}원\n"
        f"[bold white]최종 자산:[/bold white] {result['final_value']:,.0f}원\n"
        f"[bold cyan]총 수익률:[/bold cyan] {result['total_return_rate']:+.2f}%\n\n"
        f"[bold white]거래 횟수:[/bold white] {result['trade_count']}회\n"
        f"[bold white]승률:[/bold white] {result['win_rate']:.1f}%",
        title="[bold green]✅ 시뮬레이션 완료[/bold green]",
        border_style="green",
        box=box.DOUBLE
    ))

if __name__ == "__main__":
    main()
