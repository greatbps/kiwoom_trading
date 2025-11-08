"""
VWAP 매매 시뮬레이션 (Yahoo Finance 5분봉 데이터)
삼성전자 5분봉 데이터로 백테스팅
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

    console.print("\n[1] Yahoo Finance에서 삼성전자 데이터 다운로드...")

    # 삼성전자 티커: 005930.KS (KRX)
    ticker = "005930.KS"

    # 최근 7일, 5분봉
    console.print(f"  티커: {ticker}")
    console.print(f"  기간: 최근 7일")
    console.print(f"  간격: 5분봉")

    data = yf.download(
        tickers=ticker,
        period='7d',
        interval='5m',
        progress=False
    )

    if data.empty:
        console.print("  [red]✗ 데이터 다운로드 실패[/red]")
        return None

    console.print(f"  [green]✓ {len(data)}개 데이터 다운로드 완료[/green]")

    # 데이터 구조 확인
    console.print(f"\n[데이터 구조]")
    console.print(f"  시작: {data.index[0]}")
    console.print(f"  종료: {data.index[-1]}")
    console.print(f"  컬럼: {list(data.columns)}")

    return data

def prepare_chart_data(df):
    """Yahoo Finance 데이터를 키움 형식으로 변환"""

    # MultiIndex 컬럼 평탄화
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    chart_data = []

    for idx, row in df.iterrows():
        # NaN 값 필터링
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

def simulate_vwap_trading(
    chart_data,
    use_trend_filter: bool = True,
    use_volume_filter: bool = True,
    stop_loss_pct: float = 2.0,  # 손절 비율 (%)
    take_profit_pct: float = 3.0  # 익절 비율 (%)
):
    """
    VWAP 기반 매매 시뮬레이션 (개선 버전)

    Args:
        chart_data: 차트 데이터
        use_trend_filter: 추세 필터 사용 여부
        use_volume_filter: 거래량 필터 사용 여부
        stop_loss_pct: 손절 비율 (%)
        take_profit_pct: 익절 비율 (%)
    """

    analyzer = EntryTimingAnalyzer()

    # DataFrame 준비
    df = analyzer._prepare_dataframe(chart_data)

    # VWAP 계산
    df = analyzer.calculate_vwap(df)

    # 시그널 생성 (필터 적용)
    df = analyzer.generate_signals(
        df,
        use_trend_filter=use_trend_filter,
        use_volume_filter=use_volume_filter
    )

    # 시뮬레이션 변수
    cash = 10000000  # 초기 자본 1000만원
    position = 0  # 보유 주식 수
    avg_price = 0  # 평균 매수가
    trades = []  # 거래 내역

    console.print(f"\n{'='*100}")
    console.print(f"[bold cyan]VWAP 매매 시뮬레이션 (5분봉) - 개선 버전[/bold cyan]")
    console.print(f"{'='*100}")
    console.print(f"초기 자본: {cash:,}원")
    console.print(f"데이터: {len(df)}개 5분봉")
    console.print(f"필터: 추세={use_trend_filter}, 거래량={use_volume_filter}")
    console.print(f"손절: -{stop_loss_pct}%, 익절: +{take_profit_pct}%\n")

    for idx, row in df.iterrows():
        price = row['close']
        vwap = row['vwap']
        signal = row['signal']

        # 포지션이 있을 때: 손절/익절 체크
        if position > 0:
            profit_rate = ((price - avg_price) / avg_price) * 100

            # 손절: -2% 이하
            if profit_rate <= -stop_loss_pct:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)

                trades.append({
                    'idx': idx,
                    'type': 'STOP_LOSS',
                    'price': price,
                    'quantity': position,
                    'amount': revenue,
                    'vwap': vwap,
                    'profit': profit,
                    'profit_rate': profit_rate,
                    'cash': cash,
                    'position': 0
                })

                console.print(f"[{idx:3d}] [bold yellow]손절[/bold yellow]: {price:,.0f}원 × {position:,}주 = {revenue:,.0f}원 "
                             f"→ 손실: [bold red]{profit:,.0f}원 ({profit_rate:.2f}%)[/bold red]")

                position = 0
                avg_price = 0
                continue

            # 익절: +3% 이상
            elif profit_rate >= take_profit_pct:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)

                trades.append({
                    'idx': idx,
                    'type': 'TAKE_PROFIT',
                    'price': price,
                    'quantity': position,
                    'amount': revenue,
                    'vwap': vwap,
                    'profit': profit,
                    'profit_rate': profit_rate,
                    'cash': cash,
                    'position': 0
                })

                console.print(f"[{idx:3d}] [bold yellow]익절[/bold yellow]: {price:,.0f}원 × {position:,}주 = {revenue:,.0f}원 "
                             f"→ 수익: [bold green]+{profit:,.0f}원 (+{profit_rate:.2f}%)[/bold green]")

                position = 0
                avg_price = 0
                continue

        # 매수 시그널 (VWAP 상향 돌파 + 필터 통과)
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

                console.print(f"[{idx:3d}] [bold green]매수[/bold green]: {price:,.0f}원 × {quantity:,}주 = {cost:,.0f}원 (VWAP: {vwap:,.0f}원)")

        # 매도 시그널 (VWAP 하향 돌파 + 필터 통과)
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
                console.print(f"[{idx:3d}] [bold red]매도[/bold red]: {price:,.0f}원 × {position:,}주 = {revenue:,.0f}원 "
                             f"(VWAP: {vwap:,.0f}원) → 수익: [bold green]+{profit:,.0f}원 (+{profit_rate:.2f}%)[/bold green]")
            else:
                console.print(f"[{idx:3d}] [bold red]매도[/bold red]: {price:,.0f}원 × {position:,}주 = {revenue:,.0f}원 "
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

        console.print(f"\n[bold yellow]미청산 포지션[/bold yellow]: {position:,}주 @ {avg_price:,.0f}원 "
                     f"(현재가: {current_price:,.0f}원) → {unrealized_profit:+,.0f}원 ({unrealized_rate:+.2f}%)")
    else:
        final_value = cash

    # 결과 요약
    console.print(f"\n{'='*100}")
    console.print(f"[bold cyan]시뮬레이션 결과[/bold cyan]")
    console.print(f"{'='*100}\n")

    # 통계
    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] in ['SELL', 'STOP_LOSS', 'TAKE_PROFIT']]

    # 거래 타입별 분류
    signal_sells = [t for t in trades if t['type'] == 'SELL']
    stop_losses = [t for t in trades if t['type'] == 'STOP_LOSS']
    take_profits = [t for t in trades if t['type'] == 'TAKE_PROFIT']

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
    console.print(f"  ├─ 시그널 매도: {len(signal_sells)}회")
    console.print(f"  ├─ 손절:       {len(stop_losses)}회")
    console.print(f"  └─ 익절:       {len(take_profits)}회")
    console.print(f"\n승리:          {len(win_trades)}회")
    console.print(f"패배:          {len(loss_trades)}회")
    console.print(f"승률:          {win_rate:.1f}%")

    if win_trades:
        avg_win = sum([t['profit'] for t in win_trades]) / len(win_trades)
        console.print(f"평균 수익:     [green]+{avg_win:,.0f}원[/green]")

    if loss_trades:
        avg_loss = sum([t['profit'] for t in loss_trades]) / len(loss_trades)
        console.print(f"평균 손실:     [red]{avg_loss:,.0f}원[/red]")

    # 거래 내역 테이블
    if trades and len(trades) <= 20:  # 20개 이하만 출력
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
        table.add_column("손익", justify="right", width=15)

        for i, t in enumerate(trades, 1):
            # 거래 타입 표시
            if t['type'] == 'BUY':
                trade_type = "[bold green]매수[/bold green]"
            elif t['type'] == 'SELL':
                trade_type = "[bold red]매도[/bold red]"
            elif t['type'] == 'STOP_LOSS':
                trade_type = "[bold yellow]손절[/bold yellow]"
            elif t['type'] == 'TAKE_PROFIT':
                trade_type = "[bold yellow]익절[/bold yellow]"
            else:
                trade_type = t['type']

            profit_text = "-"
            if t['type'] in ['SELL', 'STOP_LOSS', 'TAKE_PROFIT']:
                profit = t.get('profit', 0)
                profit_rate = t.get('profit_rate', 0)
                if profit > 0:
                    profit_text = f"[bold green]+{profit:,.0f}원\n(+{profit_rate:.2f}%)[/bold green]"
                else:
                    profit_text = f"[bold red]{profit:,.0f}원\n({profit_rate:.2f}%)[/bold red]"

            table.add_row(
                f"{i}",
                trade_type,
                f"{t['price']:,.0f}원",
                f"{t['quantity']:,}주",
                f"{t['amount']:,.0f}원",
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
        "[bold cyan]📈 VWAP 매매 시뮬레이션 (Yahoo Finance 5분봉)[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    # Yahoo Finance에서 데이터 다운로드
    data = download_samsung_data()

    if data is None or data.empty:
        console.print("[red]데이터 다운로드 실패[/red]")
        return

    # 키움 형식으로 변환
    console.print("\n[2] 데이터 변환 중...")
    chart_data = prepare_chart_data(data)
    console.print(f"  ✓ {len(chart_data)}개 5분봉 데이터 변환 완료")

    # 시뮬레이션 실행
    console.print("\n[3] VWAP 매매 시뮬레이션 시작...")
    result = simulate_vwap_trading(chart_data)

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
