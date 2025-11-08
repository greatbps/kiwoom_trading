"""
개선된 전략 테스트

개선 사항:
1. VWAP 돌파 지속성 확인 (페이크 브레이크 방지)
2. 거래대금 절대값 필터 (유동성 확보)
3. 시장 모멘텀 필터 (코스피 지수)
4. ATR 기반 트레일링 스탑 (변동성 적응)
5. 리스크 관리 시스템 통합
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
from rich.panel import Panel
from rich import box

console = Console()

# 테스트 종목
STOCKS = {
    '삼성전자': {'ticker': '005930.KS', 'sector': '반도체'},
    'SK하이닉스': {'ticker': '000660.KS', 'sector': '반도체'},
    '한국전력': {'ticker': '015760.KS', 'sector': '전력'},
    'LG에너지솔루션': {'ticker': '373220.KS', 'sector': '배터리'},
}

# 코스피 지수
KOSPI_TICKER = '^KS11'

def download_data(ticker: str) -> pd.DataFrame:
    """데이터 다운로드"""
    try:
        data = yf.download(tickers=ticker, period='7d', interval='5m', progress=False)
        if data.empty:
            return None
        return data
    except:
        return None

def prepare_chart_data(df: pd.DataFrame) -> list:
    """차트 데이터 변환"""
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

def simulate_improved_strategy(
    chart_data: list,
    market_data_df: pd.DataFrame,
    use_atr: bool = True,
    use_breakout_confirm: bool = True,
    use_volume_value: bool = True,
    use_market_filter: bool = True
):
    """개선된 전략 시뮬레이션"""

    # Analyzer 초기화
    analyzer = EntryTimingAnalyzer(
        trailing_activation_pct=1.5,
        trailing_ratio=1.0,
        stop_loss_pct=1.0,
        breakout_confirm_candles=2,
        min_volume_value=1_000_000_000  # 10억원
    )

    # Risk Manager 초기화
    risk_mgr = RiskManager(
        initial_capital=10_000_000,
        daily_max_loss_pct=2.0,
        max_drawdown_pct=10.0,
        max_trades_per_day=5,
        position_risk_pct=1.0
    )

    # DataFrame 준비
    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)

    # ATR 계산
    if use_atr:
        df = analyzer.calculate_atr(df)

    # 시장 데이터 준비
    if use_market_filter and market_data_df is not None:
        market_df = market_data_df.copy()
        if isinstance(market_df.columns, pd.MultiIndex):
            market_df.columns = [col[0] for col in market_df.columns]
        market_df.rename(columns={'Close': 'close', 'High': 'high', 'Low': 'low', 'Open': 'open', 'Volume': 'volume'}, inplace=True)
        market_df = analyzer.calculate_vwap(market_df)
    else:
        market_df = None

    # 시그널 생성
    df = analyzer.generate_signals(
        df,
        use_trend_filter=True,
        use_volume_filter=True,
        use_breakout_confirm=use_breakout_confirm,
        use_volume_value_filter=use_volume_value,
        market_data=market_df
    )

    # 시뮬레이션 변수
    position = 0
    avg_price = 0
    highest_price = 0
    trailing_active = False
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']
        atr = row['atr'] if use_atr and 'atr' in row else None

        # 포지션 보유 중
        if position > 0:
            # 고가 갱신
            if price > highest_price:
                highest_price = price

            # 트레일링 스탑 체크
            should_exit, trailing_active, stop_price, reason = analyzer.check_trailing_stop(
                current_price=price,
                avg_price=avg_price,
                highest_price=highest_price,
                trailing_active=trailing_active,
                atr=atr,
                use_atr_based=use_atr
            )

            # VWAP 하향 돌파도 청산
            if not should_exit and signal == -1:
                should_exit = True
                reason = 'VWAP 하향 돌파'

            # 청산 실행
            if should_exit:
                revenue = position * price
                profit = revenue - (position * avg_price)
                profit_rate = ((price - avg_price) / avg_price) * 100
                highest_profit_rate = ((highest_price - avg_price) / avg_price) * 100

                # Risk Manager 업데이트
                risk_mgr.update_trade(profit, reason)

                trades.append({
                    'type': 'SELL',
                    'reason': reason,
                    'price': price,
                    'profit': profit,
                    'profit_rate': profit_rate,
                    'highest_price': highest_price,
                    'highest_profit_rate': highest_profit_rate,
                    'trailing_active': trailing_active
                })

                position = 0
                avg_price = 0
                highest_price = 0
                trailing_active = False

        # 매수 시그널
        if signal == 1 and position == 0:
            # Risk Manager 체크
            can_trade, reason = risk_mgr.can_trade()
            if not can_trade:
                continue

            # 포지션 크기 계산 (ATR 기반 손절가)
            if use_atr and atr is not None:
                stop_loss_price = price - (atr * 2)
            else:
                stop_loss_price = price * (1 - risk_mgr.position_risk_pct / 100)

            quantity, risk_amount, msg = risk_mgr.calculate_position_size(
                entry_price=price,
                stop_loss_price=stop_loss_price
            )

            if quantity > 0:
                position = quantity
                avg_price = price
                highest_price = price
                trailing_active = False

                trades.append({
                    'type': 'BUY',
                    'price': price,
                    'quantity': quantity,
                    'risk_amount': risk_amount
                })

    # 최종 통계
    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']
    win_trades = [t for t in sell_trades if t.get('profit', 0) > 0]
    trailing_exits = [t for t in sell_trades if '트레일링' in t.get('reason', '')]

    stats = risk_mgr.get_statistics()

    return {
        'final_value': stats['current_capital'],
        'return': stats['total_return'],
        'return_pct': stats['total_return_pct'],
        'trade_count': len(buy_trades),
        'exit_count': len(sell_trades),
        'win_count': len(win_trades),
        'win_rate': stats['win_rate'],
        'trailing_count': len(trailing_exits),
        'trades': trades,
        'risk_stats': stats
    }

def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]🚀 개선된 VWAP 전략 테스트[/bold cyan]\n"
        "[yellow]✅ 돌파 지속성 확인 | ✅ 거래대금 필터 | ✅ 시장 모멘텀 | ✅ ATR 트레일링[/yellow]",
        border_style="cyan"
    ))
    console.print()

    # 코스피 지수 다운로드
    console.print("[bold]코스피 지수 다운로드 중...[/bold]")
    kospi_data = download_data(KOSPI_TICKER)
    if kospi_data is not None:
        console.print(f"  ✓ 코스피: {len(kospi_data)}개")
    else:
        console.print("  [yellow]⚠ 코스피 데이터 없음 (시장 필터 비활성화)[/yellow]")
    console.print()

    # 종목 데이터 다운로드
    console.print("[bold]종목 데이터 다운로드 중...[/bold]")
    stock_data = {}
    for name, info in STOCKS.items():
        data = download_data(info['ticker'])
        if data is not None:
            chart_data = prepare_chart_data(data)
            if chart_data and len(chart_data) >= 20:
                stock_data[name] = {
                    'chart_data': chart_data,
                    'sector': info['sector']
                }
                console.print(f"  ✓ {name:20s}: {len(chart_data):4d}개")

    console.print()

    # 전략 비교 테스트
    console.print("[bold cyan]전략 비교 테스트 시작...[/bold cyan]")
    console.print()

    comparison_results = []

    # 1. 기본 전략 (개선 전)
    console.print("[bold]1. 기본 전략 (개선 전)[/bold]")
    for name, data in stock_data.items():
        result = simulate_improved_strategy(
            chart_data=data['chart_data'],
            market_data_df=None,
            use_atr=False,
            use_breakout_confirm=False,
            use_volume_value=False,
            use_market_filter=False
        )
        result['stock_name'] = name
        result['strategy'] = '기본'
        comparison_results.append(result)
        console.print(f"  {name:20s}: {result['return_pct']:+6.2f}% (거래 {result['trade_count']}회)")
    console.print()

    # 2. 개선 전략 (모든 필터 적용)
    console.print("[bold]2. 개선 전략 (모든 필터)[/bold]")
    for name, data in stock_data.items():
        result = simulate_improved_strategy(
            chart_data=data['chart_data'],
            market_data_df=kospi_data,
            use_atr=True,
            use_breakout_confirm=True,
            use_volume_value=True,
            use_market_filter=True
        )
        result['stock_name'] = name
        result['strategy'] = '개선'
        comparison_results.append(result)
        console.print(f"  {name:20s}: {result['return_pct']:+6.2f}% (거래 {result['trade_count']}회)")
    console.print()

    # 결과 비교 테이블
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]📊 전략 비교 결과[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    table = Table(
        title="기본 vs 개선 전략",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    table.add_column("종목", style="cyan", width=20)
    table.add_column("기본 수익률", justify="right", width=12)
    table.add_column("개선 수익률", justify="right", width=12)
    table.add_column("개선폭", justify="right", width=10)
    table.add_column("기본 거래", justify="center", width=10)
    table.add_column("개선 거래", justify="center", width=10)

    for name in stock_data.keys():
        basic = [r for r in comparison_results if r['stock_name'] == name and r['strategy'] == '기본'][0]
        improved = [r for r in comparison_results if r['stock_name'] == name and r['strategy'] == '개선'][0]

        diff = improved['return_pct'] - basic['return_pct']

        basic_text = f"[green]+{basic['return_pct']:.2f}%[/green]" if basic['return_pct'] > 0 else f"[red]{basic['return_pct']:.2f}%[/red]"
        improved_text = f"[green]+{improved['return_pct']:.2f}%[/green]" if improved['return_pct'] > 0 else f"[red]{improved['return_pct']:.2f}%[/red]"

        if diff > 0:
            diff_text = f"[bold green]+{diff:.2f}%[/bold green]"
        elif diff < 0:
            diff_text = f"[bold red]{diff:.2f}%[/bold red]"
        else:
            diff_text = f"{diff:.2f}%"

        table.add_row(
            name,
            basic_text,
            improved_text,
            diff_text,
            f"{basic['trade_count']}회",
            f"{improved['trade_count']}회"
        )

    console.print(table)
    console.print()

    # 평균 성과
    basic_avg = sum([r['return_pct'] for r in comparison_results if r['strategy'] == '기본']) / len(stock_data)
    improved_avg = sum([r['return_pct'] for r in comparison_results if r['strategy'] == '개선']) / len(stock_data)

    console.print(f"[bold]평균 수익률 비교:[/bold]")
    console.print(f"  기본 전략: {basic_avg:+.2f}%")
    console.print(f"  개선 전략: {improved_avg:+.2f}%")
    console.print(f"  개선 효과: [bold green]{improved_avg - basic_avg:+.2f}%p[/bold green]")
    console.print()

    # 개선 효과 분석
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]💡 개선 효과 분석[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    console.print("✅ [bold]적용된 개선 사항:[/bold]")
    console.print("  1. VWAP 돌파 지속성 확인 (2 캔들) - 페이크 브레이크 방지")
    console.print("  2. 거래대금 최소 10억원 필터 - 유동성 확보")
    console.print("  3. 코스피 지수 모멘텀 필터 - 시장 환경 확인")
    console.print("  4. ATR 기반 트레일링 스탑 - 변동성 적응형 청산")
    console.print("  5. 리스크 관리 시스템 - 일일 손실 2%, 드로다운 10% 제한")
    console.print()

    if improved_avg > basic_avg:
        console.print(f"[bold green]✅ 전략 개선 성공![/bold green]")
        console.print(f"   평균 수익률 {improved_avg - basic_avg:+.2f}%p 향상")
    else:
        console.print(f"[yellow]⚠️ 개선 효과 제한적[/yellow]")
        console.print(f"   추가 최적화 필요")

    console.print()

if __name__ == "__main__":
    main()
