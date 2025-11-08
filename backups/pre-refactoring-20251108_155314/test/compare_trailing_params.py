"""
트레일링 스탑 파라미터 비교 테스트
활성화 기준과 비율을 최적화
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

STOCKS = {
    '삼성전자': '005930.KS',
    'LG에너지솔루션': '373220.KS',
    '한국전력': '015760.KS',
    '셀트리온': '068270.KS'
}

def download_stock_data(ticker):
    try:
        data = yf.download(tickers=ticker, period='7d', interval='5m', progress=False)
        return data if not data.empty else None
    except:
        return None

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

def simulate_trailing_stop(chart_data, activation_pct, trail_ratio, stop_loss_pct=1.0):
    """트레일링 스탑 시뮬레이션"""
    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    cash = 10000000
    position = 0
    avg_price = 0
    highest_price = 0
    trailing_stop_price = 0
    trailing_active = False
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']

        if position > 0:
            profit_rate = ((price - avg_price) / avg_price) * 100

            # 고가 갱신
            if price > highest_price:
                highest_price = price

                if profit_rate >= activation_pct and not trailing_active:
                    trailing_active = True
                    trailing_stop_price = highest_price * (1 - trail_ratio / 100)
                elif trailing_active:
                    new_stop_price = highest_price * (1 - trail_ratio / 100)
                    if new_stop_price > trailing_stop_price:
                        trailing_stop_price = new_stop_price

            # 청산 조건
            should_exit = False
            exit_type = None

            if trailing_active and price <= trailing_stop_price:
                should_exit = True
                exit_type = 'TRAILING_STOP'
            elif not trailing_active and profit_rate <= -stop_loss_pct:
                should_exit = True
                exit_type = 'STOP_LOSS'
            elif signal == -1:
                should_exit = True
                exit_type = 'VWAP_EXIT'

            if should_exit:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                highest_profit_rate = ((highest_price - avg_price) / avg_price) * 100

                trades.append({
                    'type': exit_type,
                    'profit': profit,
                    'profit_rate': profit_rate,
                    'highest_profit_rate': highest_profit_rate,
                    'trailing_active': trailing_active
                })

                position = 0
                avg_price = 0
                highest_price = 0
                trailing_stop_price = 0
                trailing_active = False

        # 매수
        if signal == 1 and position == 0 and cash > 0:
            quantity = int(cash / price)
            if quantity > 0:
                cash -= quantity * price
                position = quantity
                avg_price = price
                highest_price = price
                trailing_active = False
                trades.append({'type': 'BUY'})

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price

    buy_count = len([t for t in trades if t['type'] == 'BUY'])
    exit_trades = [t for t in trades if t['type'] != 'BUY']
    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]
    trailing_trades = [t for t in exit_trades if t['type'] == 'TRAILING_STOP']

    return {
        'final_value': final_value,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': buy_count,
        'exit_count': len(exit_trades),
        'win_rate': (len(win_trades) / len(exit_trades) * 100) if exit_trades else 0,
        'trailing_count': len(trailing_trades),
        'avg_profit_rate': sum([t.get('profit_rate', 0) for t in exit_trades]) / len(exit_trades) if exit_trades else 0
    }

def main():
    console.print("\n[bold cyan]📊 트레일링 스탑 파라미터 최적화 테스트[/bold cyan]\n")

    # 데이터 다운로드
    console.print("데이터 다운로드 중...")
    stock_data = {}
    for name, ticker in STOCKS.items():
        data = download_stock_data(ticker)
        if data is not None:
            chart_data = prepare_chart_data(data)
            if chart_data:
                stock_data[name] = chart_data
                console.print(f"  ✓ {name}: {len(chart_data)}개")

    console.print()

    # 테스트 파라미터 조합
    test_configs = [
        # (활성화%, 트레일링%, 이름)
        (2.0, 1.2, "기본 (2.0%, 1.2%)"),
        (1.5, 1.0, "개선 (1.5%, 1.0%)"),
        (1.5, 1.2, "중간 (1.5%, 1.2%)"),
        (1.5, 1.5, "여유 (1.5%, 1.5%)"),
        (1.0, 1.0, "타이트 (1.0%, 1.0%)"),
    ]

    all_results = []

    for activation, trail, config_name in test_configs:
        console.print(f"[{config_name}] 테스트 중...")

        config_results = []
        for stock_name, chart_data in stock_data.items():
            result = simulate_trailing_stop(chart_data, activation, trail)
            result['stock_name'] = stock_name
            result['config_name'] = config_name
            result['activation'] = activation
            result['trail'] = trail
            config_results.append(result)

        # 평균 계산
        avg_return = sum([r['return_pct'] for r in config_results]) / len(config_results)
        avg_win_rate = sum([r['win_rate'] for r in config_results]) / len(config_results)
        total_trades = sum([r['trade_count'] for r in config_results])
        total_trailing = sum([r['trailing_count'] for r in config_results])

        all_results.append({
            'config_name': config_name,
            'activation': activation,
            'trail': trail,
            'avg_return': avg_return,
            'avg_win_rate': avg_win_rate,
            'total_trades': total_trades,
            'total_trailing': total_trailing,
            'stock_results': config_results
        })

        console.print(f"  평균 수익률: {avg_return:+.2f}%, 평균 승률: {avg_win_rate:.0f}%, "
                     f"총 거래: {total_trades}회, 트레일링: {total_trailing}회\n")

    # 결과 테이블
    console.print("[bold cyan]={'*80}[/bold cyan]")
    console.print("[bold cyan]파라미터별 성과 비교[/bold cyan]")
    console.print("[bold cyan]{'='*80}[/bold cyan]\n")

    table = Table(
        title="🎯 트레일링 스탑 파라미터 최적화",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    table.add_column("순위", justify="center", width=6)
    table.add_column("파라미터", style="cyan", width=22)
    table.add_column("평균수익률", justify="right", width=12)
    table.add_column("평균승률", justify="right", width=10)
    table.add_column("거래", justify="center", width=6)
    table.add_column("트레일링", justify="center", width=10)
    table.add_column("활성화", justify="center", width=8)
    table.add_column("비율", justify="center", width=6)

    # 평균 수익률 순 정렬
    results_sorted = sorted(all_results, key=lambda x: x['avg_return'], reverse=True)

    for i, r in enumerate(results_sorted, 1):
        rank = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"

        if r['avg_return'] > 0:
            return_text = f"[green]+{r['avg_return']:.2f}%[/green]"
        elif r['avg_return'] < 0:
            return_text = f"[red]{r['avg_return']:.2f}%[/red]"
        else:
            return_text = f"{r['avg_return']:.2f}%"

        table.add_row(
            rank,
            r['config_name'],
            return_text,
            f"{r['avg_win_rate']:.0f}%",
            f"{r['total_trades']}회",
            f"{r['total_trailing']}회",
            f"{r['activation']}%",
            f"{r['trail']}%"
        )

    console.print(table)
    console.print()

    # 최적 파라미터
    best = results_sorted[0]
    console.print(f"[bold green]🏆 최적 파라미터:[/bold green] {best['config_name']}")
    console.print(f"   활성화: {best['activation']}%, 트레일링 비율: {best['trail']}%")
    console.print(f"   평균 수익률: {best['avg_return']:+.2f}%")
    console.print(f"   평균 승률: {best['avg_win_rate']:.1f}%")
    console.print(f"   총 거래: {best['total_trades']}회 (트레일링: {best['total_trailing']}회)")
    console.print()

    # 종목별 상세 결과
    console.print("[bold cyan]종목별 상세 결과 (최적 파라미터)[/bold cyan]\n")
    for stock_result in best['stock_results']:
        color = "green" if stock_result['return_pct'] > 0 else "red" if stock_result['return_pct'] < 0 else "yellow"
        console.print(f"  {stock_result['stock_name']:12s}: "
                     f"[{color}]{stock_result['return_pct']:+6.2f}%[/{color}] "
                     f"(거래 {stock_result['trade_count']}회, 트레일링 {stock_result['trailing_count']}회)")

    console.print()

if __name__ == "__main__":
    main()
