"""
최종 전략 비교: 분봉 청산 vs 일봉 청산
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

def download_samsung_data():
    ticker = "005930.KS"
    data = yf.download(tickers=ticker, period='7d', interval='5m', progress=False)
    return data

def prepare_chart_data(df):
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

def strategy_minute_exit(chart_data, stop_loss_pct, take_profit_pct):
    """분봉 청산 전략: 매 5분봉마다 익절/손절 체크"""
    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    cash = 10000000
    position = 0
    avg_price = 0
    trades = []
    exit_count = 0

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']

        if position > 0:
            profit_rate = ((price - avg_price) / avg_price) * 100

            # 손절
            if profit_rate <= -stop_loss_pct:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                trades.append({'type': 'EXIT', 'profit': profit, 'profit_rate': profit_rate})
                position = 0
                avg_price = 0
                exit_count += 1
                continue

            # 익절
            elif profit_rate >= take_profit_pct:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                trades.append({'type': 'EXIT', 'profit': profit, 'profit_rate': profit_rate})
                position = 0
                avg_price = 0
                exit_count += 1
                continue

        # 매수
        if signal == 1 and position == 0 and cash > 0:
            quantity = int(cash / price)
            if quantity > 0:
                cash -= quantity * price
                position = quantity
                avg_price = price
                trades.append({'type': 'BUY'})

        # VWAP 매도
        elif signal == -1 and position > 0:
            revenue = position * price
            cash += revenue
            profit = revenue - (position * avg_price)
            profit_rate = (profit / (position * avg_price)) * 100
            trades.append({'type': 'EXIT', 'profit': profit, 'profit_rate': profit_rate})
            position = 0
            avg_price = 0
            exit_count += 1

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price

    buy_count = len([t for t in trades if t['type'] == 'BUY'])
    exit_trades = [t for t in trades if t['type'] == 'EXIT']
    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]

    return {
        'final_value': final_value,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': buy_count,
        'exit_count': exit_count,
        'win_rate': (len(win_trades) / len(exit_trades) * 100) if exit_trades else 0,
        'avg_profit_rate': sum([t.get('profit_rate', 0) for t in exit_trades]) / len(exit_trades) if exit_trades else 0
    }

def strategy_daily_exit(chart_data, stop_loss_pct, take_profit_pct):
    """일봉 청산 전략: 일봉 종가에서만 익절/손절 체크"""
    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    # 날짜 추가
    df['date'] = pd.to_datetime([item['dt'] for item in chart_data], format='%Y%m%d')

    cash = 10000000
    position = 0
    avg_price = 0
    trades = []
    exit_count = 0

    dates = df['date'].dt.date.unique()

    for date in dates:
        daily_bars = df[df['date'].dt.date == date].copy()

        if len(daily_bars) == 0:
            continue

        # 장중: 진입 시그널만 체크
        for idx, row in daily_bars.iterrows():
            if row['signal'] == 1 and position == 0 and cash > 0:
                price = row['close']
                quantity = int(cash / price)
                if quantity > 0:
                    cash -= quantity * price
                    position = quantity
                    avg_price = price
                    trades.append({'type': 'BUY'})
                    break

        # 종가: 청산 체크
        if position > 0:
            daily_close_bar = daily_bars.iloc[-1]
            close_price = daily_close_bar['close']
            close_signal = daily_close_bar['signal']

            profit_rate = ((close_price - avg_price) / avg_price) * 100

            should_exit = False

            if profit_rate <= -stop_loss_pct:
                should_exit = True
            elif profit_rate >= take_profit_pct:
                should_exit = True
            elif close_signal == -1:
                should_exit = True

            if should_exit:
                revenue = position * close_price
                cash += revenue
                profit = revenue - (position * avg_price)
                trades.append({'type': 'EXIT', 'profit': profit, 'profit_rate': profit_rate})
                position = 0
                avg_price = 0
                exit_count += 1

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price

    buy_count = len([t for t in trades if t['type'] == 'BUY'])
    exit_trades = [t for t in trades if t['type'] == 'EXIT']
    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]

    return {
        'final_value': final_value,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': buy_count,
        'exit_count': exit_count,
        'win_rate': (len(win_trades) / len(exit_trades) * 100) if exit_trades else 0,
        'avg_profit_rate': sum([t.get('profit_rate', 0) for t in exit_trades]) / len(exit_trades) if exit_trades else 0
    }

def main():
    console.print("\n[bold cyan]📊 최종 전략 비교: 분봉 청산 vs 일봉 청산[/bold cyan]\n")

    # 데이터 다운로드
    console.print("데이터 다운로드 중...")
    data = download_samsung_data()
    chart_data = prepare_chart_data(data)
    console.print(f"✓ {len(chart_data)}개 5분봉 데이터 준비 완료\n")

    results = []

    # 분봉 청산 전략들
    console.print("[1] 분봉 청산 전략 테스트...")
    minute_configs = [
        (1.0, 1.5, "분봉 청산 (손절 1%, 익절 1.5%)"),
        (1.5, 2.0, "분봉 청산 (손절 1.5%, 익절 2%)"),
        (2.0, 3.0, "분봉 청산 (손절 2%, 익절 3%)"),
    ]

    for stop, profit, name in minute_configs:
        result = strategy_minute_exit(chart_data, stop, profit)
        result['name'] = name
        result['type'] = 'minute'
        results.append(result)

    # 일봉 청산 전략들
    console.print("[2] 일봉 청산 전략 테스트...")
    daily_configs = [
        (1.0, 1.5, "일봉 청산 (손절 1%, 익절 1.5%)"),
        (1.5, 2.0, "일봉 청산 (손절 1.5%, 익절 2%)"),
        (2.0, 3.0, "일봉 청산 (손절 2%, 익절 3%)"),
    ]

    for stop, profit, name in daily_configs:
        result = strategy_daily_exit(chart_data, stop, profit)
        result['name'] = name
        result['type'] = 'daily'
        results.append(result)

    # 결과 테이블
    console.print()
    table = Table(
        title="🎯 청산 타이밍 전략 비교",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    table.add_column("전략", style="cyan", width=30)
    table.add_column("타입", justify="center", width=8)
    table.add_column("수익률", justify="right", width=10)
    table.add_column("거래", justify="center", width=6)
    table.add_column("청산", justify="center", width=6)
    table.add_column("승률", justify="right", width=8)
    table.add_column("평균수익률", justify="right", width=12)

    # 수익률 순으로 정렬
    results_sorted = sorted(results, key=lambda x: x['return_pct'], reverse=True)

    for i, r in enumerate(results_sorted, 1):
        rank = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"

        type_text = "[yellow]분봉[/yellow]" if r['type'] == 'minute' else "[cyan]일봉[/cyan]"

        if r['return_pct'] > 0:
            return_text = f"[green]+{r['return_pct']:.2f}%[/green]"
        elif r['return_pct'] < 0:
            return_text = f"[red]{r['return_pct']:.2f}%[/red]"
        else:
            return_text = f"{r['return_pct']:.2f}%"

        avg_profit_color = "green" if r['avg_profit_rate'] > 0 else "red"
        avg_profit_text = f"[{avg_profit_color}]{r['avg_profit_rate']:+.2f}%[/{avg_profit_color}]"

        table.add_row(
            f"{rank} {r['name']}",
            type_text,
            return_text,
            f"{r['trade_count']}회",
            f"{r['exit_count']}회",
            f"{r['win_rate']:.0f}%" if r['exit_count'] > 0 else "-",
            avg_profit_text if r['exit_count'] > 0 else "-"
        )

    console.print(table)
    console.print()

    # 최적 전략
    best = results_sorted[0]
    console.print(f"[bold green]🏆 최고 성과:[/bold green] {best['name']}")
    console.print(f"   수익률: [bold]{best['return_pct']:+.2f}%[/bold]")
    console.print(f"   거래: {best['trade_count']}회, 청산: {best['exit_count']}회")
    console.print(f"   승률: {best['win_rate']:.1f}%, 평균 청산 수익률: {best['avg_profit_rate']:+.2f}%")
    console.print()

    # 분봉 vs 일봉 비교
    minute_results = [r for r in results if r['type'] == 'minute']
    daily_results = [r for r in results if r['type'] == 'daily']

    avg_minute_return = sum([r['return_pct'] for r in minute_results]) / len(minute_results)
    avg_daily_return = sum([r['return_pct'] for r in daily_results]) / len(daily_results)

    console.print("[bold cyan]📈 분봉 vs 일봉 비교[/bold cyan]")
    console.print(f"  분봉 청산 평균 수익률: {avg_minute_return:+.2f}%")
    console.print(f"  일봉 청산 평균 수익률: {avg_daily_return:+.2f}%")
    console.print(f"  차이: {avg_daily_return - avg_minute_return:+.2f}%p")
    console.print()

if __name__ == "__main__":
    main()
