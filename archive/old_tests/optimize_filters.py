"""
필터 파라미터 최적화

필터 조합을 테스트하여 최적 균형점 찾기
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.risk_manager import RiskManager
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

STOCKS = {
    '삼성전자': '005930.KS',
    'SK하이닉스': '000660.KS',
    '한국전력': '015760.KS',
    'LG에너지솔루션': '373220.KS',
}

def download_data(ticker: str):
    try:
        return yf.download(tickers=ticker, period='7d', interval='5m', progress=False)
    except:
        return None

def prepare_chart_data(df: pd.DataFrame):
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

def simulate_with_params(
    chart_data,
    breakout_candles=2,
    min_volume_value=1_000_000_000,
    use_breakout_confirm=True,
    use_volume_value=True,
    use_atr=False
):
    """파라미터별 시뮬레이션"""
    analyzer = EntryTimingAnalyzer(
        trailing_activation_pct=1.5,
        trailing_ratio=1.0,
        stop_loss_pct=1.0,
        breakout_confirm_candles=breakout_candles,
        min_volume_value=min_volume_value
    )

    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    if use_atr:
        df = analyzer.calculate_atr(df)

    df = analyzer.generate_signals(
        df,
        use_trend_filter=True,
        use_volume_filter=True,
        use_breakout_confirm=use_breakout_confirm,
        use_volume_value_filter=use_volume_value,
        market_data=None  # 시장 필터 제외
    )

    # 시뮬레이션
    cash = 10000000
    position = 0
    avg_price = 0
    highest_price = 0
    trailing_active = False
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']
        atr = row['atr'] if use_atr and 'atr' in row else None

        if position > 0:
            if price > highest_price:
                highest_price = price

            should_exit, trailing_active, stop_price, reason = analyzer.check_trailing_stop(
                current_price=price,
                avg_price=avg_price,
                highest_price=highest_price,
                trailing_active=trailing_active,
                atr=atr,
                use_atr_based=use_atr
            )

            if not should_exit and signal == -1:
                should_exit = True
                reason = 'VWAP'

            if should_exit:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                profit_rate = ((price - avg_price) / avg_price) * 100

                trades.append({
                    'type': 'SELL',
                    'profit': profit,
                    'profit_rate': profit_rate
                })

                position = 0
                avg_price = 0
                highest_price = 0
                trailing_active = False

        if signal == 1 and position == 0 and cash > 0:
            quantity = int(cash / price)
            if quantity > 0:
                cash -= quantity * price
                position = quantity
                avg_price = price
                highest_price = price
                trades.append({'type': 'BUY'})

    final_value = cash
    if position > 0:
        final_value += position * df.iloc[-1]['close']

    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']
    win_trades = [t for t in sell_trades if t.get('profit', 0) > 0]

    return {
        'final_value': final_value,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': len(buy_trades),
        'win_rate': (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0
    }

def main():
    console.print("\n[bold cyan]📊 필터 파라미터 최적화[/bold cyan]\n")

    # 데이터 다운로드
    console.print("데이터 다운로드 중...")
    stock_data = {}
    for name, ticker in STOCKS.items():
        data = download_data(ticker)
        if data is not None:
            chart_data = prepare_chart_data(data)
            if chart_data:
                stock_data[name] = chart_data
                console.print(f"  ✓ {name}: {len(chart_data)}개")
    console.print()

    # 필터 조합 테스트
    test_configs = [
        # (설명, breakout_candles, min_volume_value, use_breakout, use_volume_value, use_atr)
        ("기본 (개선 전)", 2, 1_000_000_000, False, False, False),
        ("돌파 확인만", 2, 1_000_000_000, True, False, False),
        ("거래대금만 (10억)", 2, 1_000_000_000, False, True, False),
        ("거래대금만 (1억)", 2, 100_000_000, False, True, False),
        ("두 필터 (10억)", 2, 1_000_000_000, True, True, False),
        ("두 필터 (1억)", 2, 100_000_000, True, True, False),
        ("완화 (1캔들, 1억)", 1, 100_000_000, True, True, False),
        ("ATR 트레일링", 1, 100_000_000, True, True, True),
    ]

    all_results = []

    for config_name, breakout_candles, min_vol, use_breakout, use_vol_value, use_atr in test_configs:
        console.print(f"[yellow]{config_name}[/yellow] 테스트 중...")

        config_results = []
        for stock_name, chart_data in stock_data.items():
            result = simulate_with_params(
                chart_data=chart_data,
                breakout_candles=breakout_candles,
                min_volume_value=min_vol,
                use_breakout_confirm=use_breakout,
                use_volume_value=use_vol_value,
                use_atr=use_atr
            )
            result['stock_name'] = stock_name
            config_results.append(result)

        # 평균 계산
        avg_return = sum([r['return_pct'] for r in config_results]) / len(config_results)
        total_trades = sum([r['trade_count'] for r in config_results])
        avg_win_rate = sum([r['win_rate'] for r in config_results]) / len(config_results)

        all_results.append({
            'config_name': config_name,
            'avg_return': avg_return,
            'total_trades': total_trades,
            'avg_win_rate': avg_win_rate,
            'stock_results': config_results
        })

        console.print(f"  평균 수익률: {avg_return:+.2f}%, 총 거래: {total_trades}회, 평균 승률: {avg_win_rate:.0f}%\n")

    # 결과 테이블
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]최적 필터 조합 분석[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]\n")

    table = Table(
        title="🎯 필터 파라미터 성과 비교",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    table.add_column("순위", justify="center", width=6)
    table.add_column("필터 조합", style="cyan", width=25)
    table.add_column("평균수익률", justify="right", width=12)
    table.add_column("총 거래", justify="center", width=8)
    table.add_column("평균승률", justify="right", width=10)

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
            f"{r['total_trades']}회",
            f"{r['avg_win_rate']:.0f}%"
        )

    console.print(table)
    console.print()

    # 최적 파라미터
    best = results_sorted[0]
    console.print(f"[bold green]🏆 최적 필터 조합:[/bold green] {best['config_name']}")
    console.print(f"   평균 수익률: {best['avg_return']:+.2f}%")
    console.print(f"   총 거래: {best['total_trades']}회")
    console.print(f"   평균 승률: {best['avg_win_rate']:.1f}%")
    console.print()

    console.print("[bold cyan]💡 결론:[/bold cyan]")
    if best['total_trades'] == 0:
        console.print("  [red]⚠️ 모든 필터 조합에서 거래 없음 - 시장 환경이나 데이터 문제 가능성[/red]")
    elif best['total_trades'] < 5:
        console.print("  [yellow]⚠️ 거래 빈도 너무 낮음 - 필터 추가 완화 필요[/yellow]")
    else:
        console.print(f"  [green]✅ 적절한 필터 조합 발견 - 거래 빈도와 수익률 균형[/green]")

    console.print()

if __name__ == "__main__":
    main()
