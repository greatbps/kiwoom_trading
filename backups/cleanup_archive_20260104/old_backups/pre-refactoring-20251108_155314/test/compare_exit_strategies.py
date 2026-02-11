"""
청산 전략 비교 테스트
1. 전량 청산 (익절/손절 고정)
2. 부분 청산 (익절 + MA 기반)
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
    """삼성전자 5분봉 데이터 다운로드"""
    ticker = "005930.KS"
    data = yf.download(tickers=ticker, period='7d', interval='5m', progress=False)
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

def strategy_full_exit(chart_data, stop_loss_pct, take_profit_pct):
    """전량 청산 전략 (기존)"""
    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    cash = 10000000
    position = 0
    avg_price = 0
    trades = []

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
                trades.append({'type': 'STOP_LOSS', 'profit': profit, 'profit_rate': profit_rate})
                position = 0
                avg_price = 0
                continue

            # 익절
            elif profit_rate >= take_profit_pct:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                trades.append({'type': 'TAKE_PROFIT', 'profit': profit, 'profit_rate': profit_rate})
                position = 0
                avg_price = 0
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
            trades.append({'type': 'SELL', 'profit': profit, 'profit_rate': profit_rate})
            position = 0
            avg_price = 0

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price

    buy_count = len([t for t in trades if t['type'] == 'BUY'])
    exit_trades = [t for t in trades if t['type'] != 'BUY']
    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]

    return {
        'final_value': final_value,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': buy_count,
        'win_rate': (len(win_trades) / len(exit_trades) * 100) if exit_trades else 0,
        'exit_count': len(exit_trades),
        'avg_profit_rate': sum([t.get('profit_rate', 0) for t in exit_trades]) / len(exit_trades) if exit_trades else 0
    }

def strategy_partial_exit(chart_data):
    """부분 청산 전략 (MA 기반)"""
    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    # MA 계산
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma10'] = df['close'].rolling(window=10).mean()

    cash = 10000000
    position = 0
    initial_position = 0
    avg_price = 0
    first_exit_done = False
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']
        ma5 = row['ma5']
        ma10 = row['ma10']

        if position > 0:
            profit_rate = ((price - avg_price) / avg_price) * 100

            # 손절 -1.0%
            if profit_rate <= -1.0:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                trades.append({'type': 'STOP_LOSS', 'profit': profit, 'profit_rate': profit_rate})
                position = 0
                initial_position = 0
                avg_price = 0
                first_exit_done = False
                continue

            # 1차 익절 +1.5%, 50% 청산
            if not first_exit_done and profit_rate >= 1.5:
                sell_qty = initial_position // 2
                if sell_qty > 0:
                    revenue = sell_qty * price
                    cash += revenue
                    profit = revenue - (sell_qty * avg_price)
                    trades.append({'type': 'PARTIAL_TP', 'profit': profit, 'profit_rate': profit_rate})
                    position -= sell_qty
                    first_exit_done = True
                    continue

            # MA5 터치 (잔여 청산)
            if first_exit_done and position > 0 and not pd.isna(ma5) and price <= ma5:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                profit_rate_exit = ((price - avg_price) / avg_price) * 100
                trades.append({'type': 'MA5_EXIT', 'profit': profit, 'profit_rate': profit_rate_exit})
                position = 0
                initial_position = 0
                avg_price = 0
                first_exit_done = False
                continue

            # MA10 터치 (전량 청산)
            if position > 0 and not pd.isna(ma10) and price <= ma10:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                profit_rate_exit = ((price - avg_price) / avg_price) * 100
                trades.append({'type': 'MA10_EXIT', 'profit': profit, 'profit_rate': profit_rate_exit})
                position = 0
                initial_position = 0
                avg_price = 0
                first_exit_done = False
                continue

            # VWAP 하향 돌파 (전량 청산)
            if signal == -1:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                profit_rate_exit = ((price - avg_price) / avg_price) * 100
                trades.append({'type': 'VWAP_EXIT', 'profit': profit, 'profit_rate': profit_rate_exit})
                position = 0
                initial_position = 0
                avg_price = 0
                first_exit_done = False
                continue

        # 매수
        if signal == 1 and position == 0 and cash > 0:
            quantity = int(cash / price)
            if quantity > 0:
                cash -= quantity * price
                position = quantity
                initial_position = quantity
                avg_price = price
                first_exit_done = False
                trades.append({'type': 'BUY'})

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price

    buy_count = len([t for t in trades if t['type'] == 'BUY'])
    exit_trades = [t for t in trades if t['type'] != 'BUY']
    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]

    return {
        'final_value': final_value,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': buy_count,
        'win_rate': (len(win_trades) / len(exit_trades) * 100) if exit_trades else 0,
        'exit_count': len(exit_trades),
        'avg_profit_rate': sum([t.get('profit_rate', 0) for t in exit_trades]) / len(exit_trades) if exit_trades else 0
    }

def main():
    console.print("\n[bold cyan]📊 청산 전략 비교 테스트[/bold cyan]\n")

    # 데이터 다운로드
    console.print("데이터 다운로드 중...")
    data = download_samsung_data()
    chart_data = prepare_chart_data(data)
    console.print(f"✓ {len(chart_data)}개 5분봉 데이터 준비 완료\n")

    results = []

    # 전량 청산 전략들
    full_exit_configs = [
        (1.0, 1.5, "전량 청산 (손절 1%, 익절 1.5%)"),
        (1.5, 2.0, "전량 청산 (손절 1.5%, 익절 2%)"),
        (2.0, 3.0, "전량 청산 (손절 2%, 익절 3%)"),
    ]

    for stop, profit, name in full_exit_configs:
        result = strategy_full_exit(chart_data, stop, profit)
        result['name'] = name
        result['type'] = 'full_exit'
        results.append(result)

    # 부분 청산 전략
    partial_result = strategy_partial_exit(chart_data)
    partial_result['name'] = "부분 청산 (1.5% 50%익절 + MA 기반)"
    partial_result['type'] = 'partial_exit'
    results.append(partial_result)

    # 결과 테이블
    table = Table(
        title="🎯 청산 전략 성과 비교",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    table.add_column("전략", style="cyan", width=35)
    table.add_column("수익률", justify="right", width=10)
    table.add_column("거래", justify="center", width=6)
    table.add_column("청산", justify="center", width=6)
    table.add_column("승률", justify="right", width=8)
    table.add_column("평균수익률", justify="right", width=12)

    # 수익률 순으로 정렬
    results_sorted = sorted(results, key=lambda x: x['return_pct'], reverse=True)

    for i, r in enumerate(results_sorted, 1):
        rank = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"

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
            return_text,
            f"{r['trade_count']}회",
            f"{r['exit_count']}회",
            f"{r['win_rate']:.0f}%" if r['exit_count'] > 0 else "-",
            avg_profit_text
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

if __name__ == "__main__":
    main()
