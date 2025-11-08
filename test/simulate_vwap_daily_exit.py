"""
VWAP 매매 시뮬레이션 - 실전형 (분봉 진입 + 일봉 청산)

진입: 5분봉 VWAP 돌파 시 즉시 매수
청산: 일봉 종가에서만 익절/손절/시그널 체크
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()

def download_samsung_data():
    """삼성전자 5분봉 데이터 다운로드"""
    ticker = "005930.KS"
    data = yf.download(
        tickers=ticker,
        period='7d',
        interval='5m',
        progress=False
    )
    return data

def prepare_chart_data(df):
    """Yahoo Finance 데이터를 키움 형식으로 변환"""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    chart_data = []
    for idx, row in df.iterrows():
        if pd.isna(row['Close']) or pd.isna(row['Volume']):
            continue

        chart_data.append({
            'dt': idx.strftime('%Y%m%d'),
            'tic_tm': idx.strftime('%H%M%S'),
            'open_pric': float(row['Open']),
            'high_pric': float(row['High']),
            'low_pric': float(row['Low']),
            'cur_prc': float(row['Close']),
            'trde_qty': int(row['Volume']) if row['Volume'] > 0 else 1
        })

    return chart_data

def simulate_intraday_entry_daily_exit(chart_data, stop_loss_pct=1.0, take_profit_pct=1.5):
    """
    분봉 진입 + 일봉 종가 청산 전략

    - 장중: 5분봉으로 VWAP 진입 시그널 감지 → 즉시 매수
    - 종가: 일봉 종가에서만 익절/손절/VWAP 하향 체크 → 청산
    """

    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    # 날짜별로 그룹화
    df['date'] = pd.to_datetime(df.index if hasattr(df, 'index') and len(df.index) > 0 else range(len(df)))
    if 'dt' in chart_data[0]:
        df['date'] = pd.to_datetime([item['dt'] for item in chart_data], format='%Y%m%d')

    # 시뮬레이션 변수
    cash = 10000000
    position = 0
    avg_price = 0
    entry_date = None
    trades = []

    console.print(f"\n{'='*100}")
    console.print(f"[bold cyan]VWAP 매매 시뮬레이션 - 실전형 (분봉 진입 + 일봉 청산)[/bold cyan]")
    console.print(f"{'='*100}")
    console.print(f"초기 자본: {cash:,}원")
    console.print(f"진입: 5분봉 VWAP 돌파 (즉시)")
    console.print(f"청산: 일봉 종가 (익절 {take_profit_pct}%, 손절 {stop_loss_pct}%)")
    console.print(f"데이터: {len(df)}개 5분봉\n")

    # 날짜별로 데이터 처리
    dates = df['date'].dt.date.unique()

    for date in dates:
        daily_bars = df[df['date'].dt.date == date].copy()

        if len(daily_bars) == 0:
            continue

        # 장중: 5분봉 VWAP 진입 시그널 감지
        for idx, row in daily_bars.iterrows():
            signal = row['signal']
            price = row['close']
            vwap = row['vwap']

            # 매수 시그널 (포지션 없을 때만)
            if signal == 1 and position == 0 and cash > 0:
                quantity = int(cash / price)
                if quantity > 0:
                    cost = quantity * price
                    cash -= cost
                    position = quantity
                    avg_price = price
                    entry_date = date

                    trades.append({
                        'date': date,
                        'bar_idx': idx,
                        'type': 'BUY',
                        'price': price,
                        'quantity': quantity,
                        'vwap': vwap
                    })

                    console.print(f"[{date}] [bold green]매수[/bold green]: {price:,.0f}원 × {quantity:,}주 = {cost:,.0f}원 (VWAP: {vwap:,.0f}원)")
                    break  # 하루 1번만 매수

        # 종가: 일봉 종가에서만 청산 체크
        if position > 0:
            # 해당 일자의 마지막 5분봉 = 종가
            daily_close_bar = daily_bars.iloc[-1]
            close_price = daily_close_bar['close']
            close_vwap = daily_close_bar['vwap']
            close_signal = daily_close_bar['signal']

            profit_rate = ((close_price - avg_price) / avg_price) * 100

            should_exit = False
            exit_type = None
            exit_reason = None

            # 1. 손절 체크
            if profit_rate <= -stop_loss_pct:
                should_exit = True
                exit_type = 'STOP_LOSS'
                exit_reason = f"손절 ({profit_rate:.2f}%)"

            # 2. 익절 체크
            elif profit_rate >= take_profit_pct:
                should_exit = True
                exit_type = 'TAKE_PROFIT'
                exit_reason = f"익절 (+{profit_rate:.2f}%)"

            # 3. VWAP 하향 돌파 체크 (일봉 종가 기준)
            elif close_signal == -1:
                should_exit = True
                exit_type = 'VWAP_EXIT'
                exit_reason = f"VWAP 하향 돌파 ({profit_rate:+.2f}%)"

            # 청산 실행
            if should_exit:
                revenue = position * close_price
                cash += revenue
                profit = revenue - (position * avg_price)

                trades.append({
                    'date': date,
                    'bar_idx': daily_bars.index[-1],
                    'type': exit_type,
                    'price': close_price,
                    'quantity': position,
                    'vwap': close_vwap,
                    'profit': profit,
                    'profit_rate': profit_rate
                })

                profit_color = "green" if profit > 0 else "red"
                console.print(f"[{date}] [bold {profit_color}]{exit_reason}[/bold {profit_color}]: "
                             f"{close_price:,.0f}원 × {position:,}주 = {revenue:,.0f}원 "
                             f"(VWAP: {close_vwap:,.0f}원) → "
                             f"[bold {profit_color}]{profit:+,.0f}원 ({profit_rate:+.2f}%)[/bold {profit_color}]")

                position = 0
                avg_price = 0
                entry_date = None

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price
        unrealized_profit = (current_price - avg_price) * position
        unrealized_rate = (unrealized_profit / (position * avg_price)) * 100

        console.print(f"\n[bold yellow]미청산 포지션[/bold yellow]: {position:,}주 @ {avg_price:,.0f}원 "
                     f"(현재가: {current_price:,.0f}원) → {unrealized_profit:+,.0f}원 ({unrealized_rate:+.2f}%)")

    # 결과 통계
    console.print(f"\n{'='*100}")
    console.print(f"[bold cyan]시뮬레이션 결과[/bold cyan]")
    console.print(f"{'='*100}\n")

    buy_trades = [t for t in trades if t['type'] == 'BUY']
    exit_trades = [t for t in trades if t['type'] != 'BUY']

    take_profits = [t for t in trades if t['type'] == 'TAKE_PROFIT']
    stop_losses = [t for t in trades if t['type'] == 'STOP_LOSS']
    vwap_exits = [t for t in trades if t['type'] == 'VWAP_EXIT']

    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]
    loss_trades = [t for t in exit_trades if t.get('profit', 0) <= 0]

    console.print(f"초기 자본:     {10000000:>15,}원")
    console.print(f"최종 자산:     {final_value:>15,.0f}원")
    total_return = final_value - 10000000
    total_return_rate = (total_return / 10000000) * 100

    if total_return > 0:
        console.print(f"총 수익:       [bold green]{total_return:>+15,.0f}원 (+{total_return_rate:.2f}%)[/bold green]")
    else:
        console.print(f"총 손실:       [bold red]{total_return:>+15,.0f}원 ({total_return_rate:.2f}%)[/bold red]")

    console.print(f"\n거래 횟수:     {len(buy_trades)}회")
    console.print(f"청산 내역:")
    console.print(f"  ├─ 익절: {len(take_profits)}회")
    console.print(f"  ├─ 손절: {len(stop_losses)}회")
    console.print(f"  └─ VWAP 하향: {len(vwap_exits)}회")

    console.print(f"\n승리:          {len(win_trades)}회")
    console.print(f"패배:          {len(loss_trades)}회")
    console.print(f"승률:          {(len(win_trades) / len(exit_trades) * 100) if exit_trades else 0:.1f}%")

    if win_trades:
        avg_win = sum([t['profit'] for t in win_trades]) / len(win_trades)
        console.print(f"평균 수익:     [green]+{avg_win:,.0f}원[/green]")

    if loss_trades:
        avg_loss = sum([t['profit'] for t in loss_trades]) / len(loss_trades)
        console.print(f"평균 손실:     [red]{avg_loss:,.0f}원[/red]")

    # 거래 내역 테이블
    if trades and len(trades) <= 30:
        console.print(f"\n{'='*100}")
        console.print()

        table = Table(
            title="📊 거래 내역",
            box=box.ROUNDED,
            border_style="cyan",
            show_header=True,
            header_style="bold magenta"
        )

        table.add_column("날짜", justify="center", style="cyan", width=12)
        table.add_column("구분", justify="center", width=10)
        table.add_column("가격", justify="right", style="white", width=12)
        table.add_column("수량", justify="right", style="yellow", width=10)
        table.add_column("VWAP", justify="right", style="magenta", width=12)
        table.add_column("손익", justify="right", width=15)

        for t in trades:
            if t['type'] == 'BUY':
                trade_type = "[bold green]매수[/bold green]"
                profit_text = "-"
            elif t['type'] == 'TAKE_PROFIT':
                trade_type = "[bold yellow]익절[/bold yellow]"
                profit = t.get('profit', 0)
                profit_rate = t.get('profit_rate', 0)
                profit_text = f"[bold green]+{profit:,.0f}원\n(+{profit_rate:.2f}%)[/bold green]"
            elif t['type'] == 'STOP_LOSS':
                trade_type = "[bold red]손절[/bold red]"
                profit = t.get('profit', 0)
                profit_rate = t.get('profit_rate', 0)
                profit_text = f"[bold red]{profit:,.0f}원\n({profit_rate:.2f}%)[/bold red]"
            else:  # VWAP_EXIT
                trade_type = "[bold magenta]VWAP[/bold magenta]"
                profit = t.get('profit', 0)
                profit_rate = t.get('profit_rate', 0)
                color = "green" if profit > 0 else "red"
                profit_text = f"[bold {color}]{profit:+,.0f}원\n({profit_rate:+.2f}%)[/bold {color}]"

            table.add_row(
                str(t['date']),
                trade_type,
                f"{t['price']:,.0f}원",
                f"{t['quantity']:,}주",
                f"{t['vwap']:,.0f}원",
                profit_text
            )

        console.print(table)

    console.print(f"\n{'='*100}\n")

    return {
        'final_value': final_value,
        'total_return': total_return,
        'total_return_rate': total_return_rate,
        'trade_count': len(buy_trades),
        'win_count': len(win_trades),
        'loss_count': len(loss_trades),
        'win_rate': (len(win_trades) / len(exit_trades) * 100) if exit_trades else 0
    }

def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]📈 VWAP 매매 - 실전형 (분봉 진입 + 일봉 청산)[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    # 데이터 다운로드
    console.print("[1] Yahoo Finance에서 삼성전자 데이터 다운로드...")
    data = download_samsung_data()

    if data is None or data.empty:
        console.print("[red]데이터 다운로드 실패[/red]")
        return

    console.print(f"  ✓ {len(data)}개 데이터 다운로드 완료")

    # 데이터 변환
    console.print("\n[2] 데이터 변환 중...")
    chart_data = prepare_chart_data(data)
    console.print(f"  ✓ {len(chart_data)}개 5분봉 데이터 변환 완료")

    # 시뮬레이션 실행
    console.print("\n[3] 시뮬레이션 시작...")
    result = simulate_intraday_entry_daily_exit(chart_data, stop_loss_pct=1.0, take_profit_pct=1.5)

    # 최종 요약
    console.print(Panel(
        f"[bold white]초기 자본:[/bold white] {10000000:,}원\n"
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
