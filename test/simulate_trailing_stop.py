"""
VWAP 매매 시뮬레이션 - 트레일링 스탑 전략

진입: 5분봉 VWAP 돌파
청산: 트레일링 스탑 (고가 추적 후 일정 비율 하락 시)
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

# 테스트 종목 (Yahoo Finance 티커)
STOCKS = {
    '삼성전자': '005930.KS',       # 대형주
    'LG에너지솔루션': '373220.KS',  # 배터리
    '한국전력': '015760.KS',        # 원자력
    '셀트리온': '068270.KS'         # AI/바이오 (AI는 한국에 상장 ETF 적음)
}

def download_stock_data(ticker, name):
    """종목별 5분봉 데이터 다운로드"""
    try:
        data = yf.download(
            tickers=ticker,
            period='7d',
            interval='5m',
            progress=False
        )
        if data.empty:
            console.print(f"  [red]✗ {name} 데이터 없음[/red]")
            return None
        return data
    except Exception as e:
        console.print(f"  [red]✗ {name} 다운로드 실패: {e}[/red]")
        return None

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

def simulate_trailing_stop(
    chart_data,
    stock_name,
    activation_pct=2.0,    # 트레일링 스탑 활성화 수익률 (%)
    trail_ratio=1.2,       # 트레일링 스탑 비율 (%)
    stop_loss_pct=1.0      # 기본 손절 (%)
):
    """
    트레일링 스탑 전략

    로직:
    1. 매수 후 수익률이 activation_pct(2%) 이상 도달하면 트레일링 스탑 활성화
    2. 활성화 후 고가 추적, 고가 대비 trail_ratio(1.2%) 하락 시 청산
    3. 활성화 전에는 stop_loss_pct(1%) 손절만 적용
    """

    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    cash = 10000000
    position = 0
    avg_price = 0
    highest_price = 0  # 매수 후 최고가
    trailing_stop_price = 0  # 트레일링 스탑 가격
    trailing_active = False  # 트레일링 스탑 활성화 여부
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']

        # 포지션 보유 중
        if position > 0:
            profit_rate = ((price - avg_price) / avg_price) * 100

            # 고가 갱신
            if price > highest_price:
                highest_price = price

                # 트레일링 스탑 활성화 조건: 수익률 2% 이상
                if profit_rate >= activation_pct and not trailing_active:
                    trailing_active = True
                    trailing_stop_price = highest_price * (1 - trail_ratio / 100)

                # 트레일링 스탑 활성화 중: 스탑 가격 업데이트 (상승만)
                elif trailing_active:
                    new_stop_price = highest_price * (1 - trail_ratio / 100)
                    if new_stop_price > trailing_stop_price:
                        trailing_stop_price = new_stop_price

            # 청산 조건 체크
            should_exit = False
            exit_type = None

            # 1. 트레일링 스탑 청산
            if trailing_active and price <= trailing_stop_price:
                should_exit = True
                exit_type = 'TRAILING_STOP'

            # 2. 기본 손절 (트레일링 활성화 전)
            elif not trailing_active and profit_rate <= -stop_loss_pct:
                should_exit = True
                exit_type = 'STOP_LOSS'

            # 3. VWAP 하향 돌파 (비상 탈출)
            elif signal == -1:
                should_exit = True
                exit_type = 'VWAP_EXIT'

            # 청산 실행
            if should_exit:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                highest_profit_rate = ((highest_price - avg_price) / avg_price) * 100

                trades.append({
                    'idx': idx,
                    'type': exit_type,
                    'price': price,
                    'quantity': position,
                    'profit': profit,
                    'profit_rate': profit_rate,
                    'highest_price': highest_price,
                    'highest_profit_rate': highest_profit_rate,
                    'trailing_active': trailing_active
                })

                position = 0
                avg_price = 0
                highest_price = 0
                trailing_stop_price = 0
                trailing_active = False

        # 매수 시그널
        if signal == 1 and position == 0 and cash > 0:
            quantity = int(cash / price)
            if quantity > 0:
                cost = quantity * price
                cash -= cost
                position = quantity
                avg_price = price
                highest_price = price
                trailing_active = False

                trades.append({
                    'idx': idx,
                    'type': 'BUY',
                    'price': price,
                    'quantity': quantity
                })

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price

    buy_count = len([t for t in trades if t['type'] == 'BUY'])
    exit_trades = [t for t in trades if t['type'] != 'BUY']
    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]

    return {
        'stock_name': stock_name,
        'final_value': final_value,
        'return': final_value - 10000000,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': buy_count,
        'exit_count': len(exit_trades),
        'win_count': len(win_trades),
        'win_rate': (len(win_trades) / len(exit_trades) * 100) if exit_trades else 0,
        'trades': trades,
        'position': position
    }

def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]📈 VWAP + 트레일링 스탑 전략 (4개 종목 테스트)[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    console.print("[bold]테스트 종목:[/bold]")
    for name, ticker in STOCKS.items():
        console.print(f"  - {name} ({ticker})")
    console.print()

    console.print("[bold]전략 파라미터:[/bold]")
    console.print(f"  - 트레일링 활성화: 수익률 +2.0% 도달 시")
    console.print(f"  - 트레일링 비율: 고가 대비 -1.2%")
    console.print(f"  - 기본 손절: -1.0%")
    console.print()

    # 데이터 다운로드 및 시뮬레이션
    results = []

    for stock_name, ticker in STOCKS.items():
        console.print(f"[bold cyan]{'='*80}[/bold cyan]")
        console.print(f"[bold]{stock_name} ({ticker})[/bold]")
        console.print(f"[bold cyan]{'='*80}[/bold cyan]")

        # 다운로드
        data = download_stock_data(ticker, stock_name)
        if data is None or data.empty:
            console.print()
            continue

        console.print(f"  ✓ {len(data)}개 5분봉 데이터 다운로드 완료")

        # 데이터 변환
        chart_data = prepare_chart_data(data)
        if not chart_data:
            console.print(f"  [red]✗ 데이터 변환 실패[/red]\n")
            continue

        console.print(f"  ✓ {len(chart_data)}개 데이터 변환 완료")

        # 시뮬레이션
        result = simulate_trailing_stop(
            chart_data,
            stock_name,
            activation_pct=2.0,
            trail_ratio=1.2,
            stop_loss_pct=1.0
        )

        results.append(result)

        # 종목별 결과 출력
        color = "green" if result['return_pct'] > 0 else "red" if result['return_pct'] < 0 else "yellow"
        console.print(f"  수익률: [bold {color}]{result['return_pct']:+.2f}%[/bold {color}] "
                     f"({result['return']:+,.0f}원)")
        console.print(f"  거래: {result['trade_count']}회, "
                     f"청산: {result['exit_count']}회, "
                     f"승률: {result['win_rate']:.0f}%")

        # 거래 내역
        if result['exit_count'] > 0:
            console.print(f"\n  [bold]청산 내역:[/bold]")
            for t in result['trades']:
                if t['type'] != 'BUY':
                    profit_color = "green" if t['profit'] > 0 else "red"
                    exit_name = "트레일링" if t['type'] == 'TRAILING_STOP' else "손절" if t['type'] == 'STOP_LOSS' else "VWAP"
                    console.print(f"    [{exit_name}] {t['price']:,.0f}원 → "
                                 f"[{profit_color}]{t['profit']:+,.0f}원 ({t['profit_rate']:+.2f}%)[/{profit_color}] "
                                 f"(최고가 {t['highest_price']:,.0f}원, +{t['highest_profit_rate']:.2f}%)")

        console.print()

    # 종합 결과 테이블
    if results:
        console.print(f"[bold cyan]{'='*80}[/bold cyan]")
        console.print(f"[bold cyan]📊 종합 결과[/bold cyan]")
        console.print(f"[bold cyan]{'='*80}[/bold cyan]\n")

        table = Table(
            title="종목별 성과 비교",
            box=box.ROUNDED,
            border_style="cyan",
            show_header=True,
            header_style="bold magenta"
        )

        table.add_column("순위", justify="center", width=6)
        table.add_column("종목", style="cyan", width=18)
        table.add_column("수익률", justify="right", width=10)
        table.add_column("수익금", justify="right", width=13)
        table.add_column("거래", justify="center", width=6)
        table.add_column("승률", justify="right", width=8)

        # 수익률 순 정렬
        results_sorted = sorted(results, key=lambda x: x['return_pct'], reverse=True)

        for i, r in enumerate(results_sorted, 1):
            rank = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"

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
                rank,
                r['stock_name'],
                return_text,
                profit_text,
                f"{r['trade_count']}회",
                f"{r['win_rate']:.0f}%" if r['exit_count'] > 0 else "-"
            )

        console.print(table)
        console.print()

        # 평균 성과
        avg_return = sum([r['return_pct'] for r in results]) / len(results)
        total_return = sum([r['return'] for r in results])

        console.print(f"[bold]평균 수익률:[/bold] {avg_return:+.2f}%")
        console.print(f"[bold]총 수익:[/bold] {total_return:+,.0f}원 (4종목 합계)")
        console.print()

if __name__ == "__main__":
    main()
