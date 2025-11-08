"""
VWAP 매매 시뮬레이션 - 부분 청산 전략
1. 익절 +1.5% 도달 시 50% 청산
2. MA5 터치 시 추가 50% 청산 (잔여 포지션)
3. MA10 터치 시 전량 청산
4. VWAP 하향 돌파 시 전량 청산
5. 손절 -1.0% 이하 시 전량 청산
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

def simulate_partial_exit(chart_data):
    """부분 청산 전략 시뮬레이션"""

    analyzer = EntryTimingAnalyzer()
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    # MA 계산
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma10'] = df['close'].rolling(window=10).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()

    # 시뮬레이션 변수
    cash = 10000000
    position = 0  # 현재 보유 주식 수
    initial_position = 0  # 최초 매수 주식 수
    avg_price = 0
    first_exit_done = False  # 1차 익절 완료 여부
    trades = []

    console.print(f"\n{'='*100}")
    console.print(f"[bold cyan]VWAP 매매 시뮬레이션 - 부분 청산 전략[/bold cyan]")
    console.print(f"{'='*100}")
    console.print(f"초기 자본: {cash:,}원")
    console.print(f"익절 전략: +1.5% 시 50% 익절 → MA5 터치 시 추가 청산 → MA10 터치 시 전량 청산")
    console.print(f"손절: -1.0%\n")

    for idx, row in df.iterrows():
        price = row['close']
        vwap = row['vwap']
        signal = row['signal']
        ma5 = row['ma5']
        ma10 = row['ma10']
        ma20 = row['ma20']

        # 포지션이 있을 때
        if position > 0:
            profit_rate = ((price - avg_price) / avg_price) * 100

            # 1️⃣ 손절 우선 체크 (-1.0% 이하)
            if profit_rate <= -1.0:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)

                trades.append({
                    'idx': idx,
                    'type': 'STOP_LOSS',
                    'price': price,
                    'quantity': position,
                    'ratio': 1.0,
                    'profit': profit,
                    'profit_rate': profit_rate
                })

                console.print(f"[{idx:3d}] [bold red]손절[/bold red]: {price:,.0f}원 × {position:,}주 (전량) "
                             f"→ 손실: [bold red]{profit:,.0f}원 ({profit_rate:.2f}%)[/bold red]")

                position = 0
                initial_position = 0
                avg_price = 0
                first_exit_done = False
                continue

            # 2️⃣ 1차 익절 (+1.5% 이상, 50% 청산)
            if not first_exit_done and profit_rate >= 1.5:
                sell_qty = initial_position // 2  # 50% 청산
                if sell_qty > 0:
                    revenue = sell_qty * price
                    cash += revenue
                    profit = revenue - (sell_qty * avg_price)

                    trades.append({
                        'idx': idx,
                        'type': 'TAKE_PROFIT_1',
                        'price': price,
                        'quantity': sell_qty,
                        'ratio': 0.5,
                        'profit': profit,
                        'profit_rate': profit_rate
                    })

                    position -= sell_qty
                    first_exit_done = True

                    console.print(f"[{idx:3d}] [bold yellow]1차 익절[/bold yellow]: {price:,.0f}원 × {sell_qty:,}주 (50%) "
                                 f"→ 수익: [bold green]+{profit:,.0f}원 (+{profit_rate:.2f}%)[/bold green]")
                    console.print(f"       잔여 포지션: {position:,}주")
                    continue

            # 3️⃣ MA5 터치 시 추가 청산 (잔여 50%)
            if first_exit_done and position > 0 and not pd.isna(ma5):
                if price <= ma5:
                    sell_qty = position  # 잔여 전량
                    revenue = sell_qty * price
                    cash += revenue
                    profit = revenue - (sell_qty * avg_price)
                    profit_rate_exit = ((price - avg_price) / avg_price) * 100

                    trades.append({
                        'idx': idx,
                        'type': 'MA5_EXIT',
                        'price': price,
                        'quantity': sell_qty,
                        'ratio': 0.5,
                        'profit': profit,
                        'profit_rate': profit_rate_exit
                    })

                    console.print(f"[{idx:3d}] [bold cyan]MA5 터치[/bold cyan]: {price:,.0f}원 × {sell_qty:,}주 (잔여) "
                                 f"→ 수익: [bold green]+{profit:,.0f}원 (+{profit_rate_exit:.2f}%)[/bold green]")

                    position = 0
                    initial_position = 0
                    avg_price = 0
                    first_exit_done = False
                    continue

            # 4️⃣ MA10 터치 시 전량 청산
            if position > 0 and not pd.isna(ma10):
                if price <= ma10:
                    revenue = position * price
                    cash += revenue
                    profit = revenue - (position * avg_price)
                    profit_rate_exit = ((price - avg_price) / avg_price) * 100

                    trades.append({
                        'idx': idx,
                        'type': 'MA10_EXIT',
                        'price': price,
                        'quantity': position,
                        'ratio': 1.0,
                        'profit': profit,
                        'profit_rate': profit_rate_exit
                    })

                    console.print(f"[{idx:3d}] [bold magenta]MA10 터치[/bold magenta]: {price:,.0f}원 × {position:,}주 (전량) "
                                 f"→ 수익: [bold green]+{profit:,.0f}원 (+{profit_rate_exit:.2f}%)[/bold green]")

                    position = 0
                    initial_position = 0
                    avg_price = 0
                    first_exit_done = False
                    continue

            # 5️⃣ VWAP 하향 돌파 시 전량 청산
            if signal == -1:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                profit_rate_exit = ((price - avg_price) / avg_price) * 100

                trades.append({
                    'idx': idx,
                    'type': 'VWAP_EXIT',
                    'price': price,
                    'quantity': position,
                    'ratio': 1.0,
                    'profit': profit,
                    'profit_rate': profit_rate_exit
                })

                console.print(f"[{idx:3d}] [bold red]VWAP 하향[/bold red]: {price:,.0f}원 × {position:,}주 (전량) "
                             f"→ {'수익' if profit > 0 else '손실'}: "
                             f"[bold {'green' if profit > 0 else 'red'}]{profit:+,.0f}원 ({profit_rate_exit:+.2f}%)[/bold {'green' if profit > 0 else 'red'}]")

                position = 0
                initial_position = 0
                avg_price = 0
                first_exit_done = False
                continue

        # 매수 시그널
        if signal == 1 and position == 0 and cash > 0:
            quantity = int(cash / price)
            if quantity > 0:
                cost = quantity * price
                cash -= cost
                position = quantity
                initial_position = quantity
                avg_price = price
                first_exit_done = False

                trades.append({
                    'idx': idx,
                    'type': 'BUY',
                    'price': price,
                    'quantity': quantity,
                    'ratio': 1.0
                })

                console.print(f"[{idx:3d}] [bold green]매수[/bold green]: {price:,.0f}원 × {quantity:,}주 = {cost:,.0f}원")

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

    # 청산 타입별 분류
    take_profit_1 = [t for t in trades if t['type'] == 'TAKE_PROFIT_1']
    ma5_exits = [t for t in trades if t['type'] == 'MA5_EXIT']
    ma10_exits = [t for t in trades if t['type'] == 'MA10_EXIT']
    vwap_exits = [t for t in trades if t['type'] == 'VWAP_EXIT']
    stop_losses = [t for t in trades if t['type'] == 'STOP_LOSS']

    total_profit = sum([t.get('profit', 0) for t in exit_trades])
    win_trades = [t for t in exit_trades if t.get('profit', 0) > 0]

    console.print(f"초기 자본:     {10000000:>15,}원")
    console.print(f"최종 자산:     {final_value:>15,.0f}원")
    total_return = final_value - 10000000
    total_return_rate = (total_return / 10000000) * 100

    if total_return > 0:
        console.print(f"총 수익:       [bold green]{total_return:>+15,.0f}원 (+{total_return_rate:.2f}%)[/bold green]")
    else:
        console.print(f"총 손실:       [bold red]{total_return:>+15,.0f}원 ({total_return_rate:.2f}%)[/bold red]")

    console.print(f"\n매수 횟수:     {len(buy_trades)}회")
    console.print(f"청산 내역:")
    console.print(f"  ├─ 1차 익절(+1.5%, 50%): {len(take_profit_1)}회")
    console.print(f"  ├─ MA5 터치(잔여):      {len(ma5_exits)}회")
    console.print(f"  ├─ MA10 터치(전량):     {len(ma10_exits)}회")
    console.print(f"  ├─ VWAP 하향(전량):     {len(vwap_exits)}회")
    console.print(f"  └─ 손절(-1%, 전량):     {len(stop_losses)}회")

    console.print(f"\n평균 청산 수익률: {sum([t.get('profit_rate', 0) for t in exit_trades]) / len(exit_trades) if exit_trades else 0:.2f}%")

    if win_trades:
        avg_win = sum([t['profit'] for t in win_trades]) / len(win_trades)
        console.print(f"평균 수익:     [green]+{avg_win:,.0f}원[/green]")

    console.print(f"\n{'='*100}\n")

    return {
        'final_value': final_value,
        'total_return': total_return,
        'total_return_rate': total_return_rate,
        'trade_count': len(buy_trades),
        'exit_count': len(exit_trades)
    }

def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]📈 VWAP 매매 시뮬레이션 - 부분 청산 전략[/bold cyan]",
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
    console.print("\n[3] 부분 청산 전략 시뮬레이션 시작...")
    result = simulate_partial_exit(chart_data)

    # 최종 요약
    console.print(Panel(
        f"[bold white]초기 자본:[/bold white] {10000000:,}원\n"
        f"[bold white]최종 자산:[/bold white] {result['final_value']:,.0f}원\n"
        f"[bold cyan]총 수익률:[/bold cyan] {result['total_return_rate']:+.2f}%\n\n"
        f"[bold white]매수 횟수:[/bold white] {result['trade_count']}회\n"
        f"[bold white]청산 횟수:[/bold white] {result['exit_count']}회",
        title="[bold green]✅ 시뮬레이션 완료[/bold green]",
        border_style="green",
        box=box.DOUBLE
    ))

if __name__ == "__main__":
    main()
