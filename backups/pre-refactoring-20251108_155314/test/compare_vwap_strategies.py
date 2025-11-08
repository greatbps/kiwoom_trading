"""
VWAP 전략 비교 테스트
여러 파라미터 조합을 테스트하여 최적 전략 찾기
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

def run_backtest(
    chart_data,
    use_trend_filter: bool,
    use_volume_filter: bool,
    stop_loss_pct: float,
    take_profit_pct: float
):
    """백테스팅 실행"""

    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(
        df,
        use_trend_filter=use_trend_filter,
        use_volume_filter=use_volume_filter
    )

    cash = 10000000
    position = 0
    avg_price = 0
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        vwap = row['vwap']
        signal = row['signal']

        # 손절/익절 체크
        if position > 0:
            profit_rate = ((price - avg_price) / avg_price) * 100

            if profit_rate <= -stop_loss_pct:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                trades.append({'type': 'STOP_LOSS', 'profit': profit, 'profit_rate': profit_rate})
                position = 0
                avg_price = 0
                continue

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
                cost = quantity * price
                cash -= cost
                position = quantity
                avg_price = price
                trades.append({'type': 'BUY'})

        # 매도
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

    # 통계
    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] in ['SELL', 'STOP_LOSS', 'TAKE_PROFIT']]
    win_trades = [t for t in sell_trades if t.get('profit', 0) > 0]

    return {
        'final_value': final_value,
        'return': final_value - 10000000,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': len(buy_trades),
        'win_rate': (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0,
        'sell_trades': len(sell_trades)
    }

def main():
    console.print("\n[bold cyan]📊 VWAP 전략 파라미터 비교 테스트[/bold cyan]\n")

    # 데이터 다운로드
    console.print("데이터 다운로드 중...")
    data = download_samsung_data()
    chart_data = prepare_chart_data(data)
    console.print(f"✓ {len(chart_data)}개 5분봉 데이터 준비 완료\n")

    # 테스트 조합
    test_cases = [
        # (추세 필터, 거래량 필터, 손절%, 익절%, 이름)
        (False, False, 2.0, 3.0, "기본 (필터 없음)"),
        (True, False, 2.0, 3.0, "추세 필터만"),
        (False, True, 2.0, 3.0, "거래량 필터만"),
        (True, True, 2.0, 3.0, "추세+거래량 필터"),
        (True, True, 1.5, 2.0, "필터+손익비 1.5:2"),
        (True, True, 1.0, 1.5, "필터+손익비 1:1.5"),
        (True, True, 3.0, 5.0, "필터+손익비 3:5"),
    ]

    results = []

    for trend, volume, stop, profit, name in test_cases:
        result = run_backtest(chart_data, trend, volume, stop, profit)
        result['name'] = name
        result['trend_filter'] = trend
        result['volume_filter'] = volume
        result['stop_loss'] = stop
        result['take_profit'] = profit
        results.append(result)

    # 결과 테이블
    table = Table(
        title="🎯 VWAP 전략 성과 비교",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    table.add_column("전략", style="cyan", width=25)
    table.add_column("수익률", justify="right", width=10)
    table.add_column("수익금", justify="right", width=12)
    table.add_column("거래", justify="center", width=6)
    table.add_column("승률", justify="right", width=8)
    table.add_column("손절", justify="center", width=6)
    table.add_column("익절", justify="center", width=6)

    # 수익률 순으로 정렬
    results_sorted = sorted(results, key=lambda x: x['return_pct'], reverse=True)

    for i, r in enumerate(results_sorted, 1):
        # 순위 표시
        rank = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"

        # 수익률 색상
        if r['return_pct'] > 0:
            return_text = f"[green]+{r['return_pct']:.2f}%[/green]"
            profit_text = f"[green]+{r['return']:,.0f}원[/green]"
        elif r['return_pct'] < 0:
            return_text = f"[red]{r['return_pct']:.2f}%[/red]"
            profit_text = f"[red]{r['return']:,.0f}원[/red]"
        else:
            return_text = f"{r['return_pct']:.2f}%"
            profit_text = f"{r['return']:,.0f}원"

        table.add_row(
            f"{rank} {r['name']}",
            return_text,
            profit_text,
            f"{r['trade_count']}회",
            f"{r['win_rate']:.0f}%" if r['sell_trades'] > 0 else "-",
            f"{r['stop_loss']:.1f}%",
            f"{r['take_profit']:.1f}%"
        )

    console.print(table)
    console.print()

    # 최적 전략 출력
    best = results_sorted[0]
    console.print(f"[bold green]🏆 최고 성과 전략:[/bold green] {best['name']}")
    console.print(f"   수익률: [bold]{best['return_pct']:+.2f}%[/bold] ({best['return']:+,.0f}원)")
    console.print(f"   거래 횟수: {best['trade_count']}회")
    console.print(f"   승률: {best['win_rate']:.1f}%")
    console.print()

if __name__ == "__main__":
    main()
