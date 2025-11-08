"""
종합 전략 테스트

모든 개선사항 통합 테스트:
1. 리스크 관리 시스템
2. VWAP + 필터 조합
3. 일봉 추세 강도 필터 (EMA + RSI)
4. ATR 기반 트레일링 스탑
5. 로그 시스템
6. R:R 기반 포지션 크기
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.risk_manager import RiskManager
from utils.trade_logger import TradeLogger
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from datetime import datetime

console = Console()

# 테스트 종목
STOCKS = {
    '삼성전자': '005930.KS',
    'SK하이닉스': '000660.KS',
    '한국전력': '015760.KS',
    'LG에너지솔루션': '373220.KS',
}

KOSPI_TICKER = '^KS11'

def download_data(ticker: str, period='7d', interval='5m'):
    """데이터 다운로드"""
    try:
        return yf.download(tickers=ticker, period=period, interval=interval, progress=False)
    except:
        return None

def prepare_chart_data(df: pd.DataFrame):
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

def prepare_dataframe(df: pd.DataFrame):
    """DataFrame 정리"""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    df.rename(columns={
        'Close': 'close',
        'High': 'high',
        'Low': 'low',
        'Open': 'open',
        'Volume': 'volume'
    }, inplace=True)
    return df

def run_comprehensive_test(
    stock_name: str,
    chart_data: list,
    daily_data_df: pd.DataFrame,
    market_data_df: pd.DataFrame,
    logger: TradeLogger,
    config: dict
):
    """종합 전략 시뮬레이션"""

    # Analyzer 초기화
    analyzer = EntryTimingAnalyzer(
        trailing_activation_pct=config['trailing_activation'],
        trailing_ratio=config['trailing_ratio'],
        stop_loss_pct=config['stop_loss'],
        breakout_confirm_candles=config['breakout_candles'],
        min_volume_value=config['min_volume_value']
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

    # ATR & RSI 계산
    if config['use_atr']:
        df = analyzer.calculate_atr(df)

    # 시장 데이터 준비
    market_df = None
    if config['use_market_filter'] and market_data_df is not None:
        market_df = market_data_df.copy()
        market_df = analyzer.calculate_vwap(market_df)

    # 일봉 데이터 준비
    daily_df = None
    if config['use_daily_trend'] and daily_data_df is not None:
        daily_df = daily_data_df.copy()

    # 시그널 생성
    df = analyzer.generate_signals(
        df,
        use_trend_filter=config['use_trend_filter'],
        use_volume_filter=config['use_volume_filter'],
        use_breakout_confirm=config['use_breakout_confirm'],
        use_volume_value_filter=config['use_volume_value_filter'],
        market_data=market_df,
        daily_data=daily_df,
        use_daily_trend_filter=config['use_daily_trend']
    )

    # 시뮬레이션
    position = 0
    avg_price = 0
    highest_price = 0
    trailing_active = False
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']
        atr = row['atr'] if config['use_atr'] and 'atr' in row else None

        # 시그널 로깅
        if signal != 0:
            logger.log_signal(
                stock_code=stock_name,
                signal=signal,
                price=price,
                vwap=row['vwap'],
                indicators={
                    'atr': atr if atr else None,
                    'ma': row['ma'] if 'ma' in row else None
                }
            )

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
                use_atr_based=config['use_atr']
            )

            # VWAP 하향 돌파
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

                # 로깅
                logger.log_exit(
                    stock_code=stock_name,
                    quantity=position,
                    entry_price=avg_price,
                    exit_price=price,
                    reason=reason,
                    highest_price=highest_price,
                    trailing_active=trailing_active
                )

                trades.append({
                    'type': 'SELL',
                    'reason': reason,
                    'price': price,
                    'profit': profit,
                    'profit_rate': profit_rate,
                    'highest_profit_rate': highest_profit_rate
                })

                position = 0
                avg_price = 0
                highest_price = 0
                trailing_active = False

        # 매수 시그널
        if signal == 1 and position == 0:
            # Risk Manager 체크
            can_trade, risk_reason = risk_mgr.can_trade()
            if not can_trade:
                logger.log_risk_check(can_trade=False, reason=risk_reason)
                continue

            # 손절가 계산
            if config['use_atr'] and atr is not None:
                stop_loss_price = price - (atr * 2)
            else:
                stop_loss_price = price * (1 - config['stop_loss'] / 100)

            # 목표가 계산 (트레일링 활성화 기준)
            take_profit_price = price * (1 + config['trailing_activation'] / 100)

            # 포지션 크기 계산 (R:R 검증)
            quantity, risk_amount, msg = risk_mgr.calculate_position_size(
                entry_price=price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                min_rr_ratio=1.5
            )

            if quantity > 0:
                position = quantity
                avg_price = price
                highest_price = price
                trailing_active = False

                # 로깅
                logger.log_entry(
                    stock_code=stock_name,
                    quantity=quantity,
                    price=price,
                    risk_amount=risk_amount,
                    stop_loss=stop_loss_price,
                    strategy=config['name']
                )

                trades.append({
                    'type': 'BUY',
                    'price': price,
                    'quantity': quantity
                })

    # 최종 통계
    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']
    win_trades = [t for t in sell_trades if t.get('profit', 0) > 0]

    stats = risk_mgr.get_statistics()

    return {
        'stock_name': stock_name,
        'config_name': config['name'],
        'final_value': stats['current_capital'],
        'return': stats['total_return'],
        'return_pct': stats['total_return_pct'],
        'trade_count': len(buy_trades),
        'exit_count': len(sell_trades),
        'win_count': len(win_trades),
        'win_rate': stats['win_rate'],
        'trades': trades,
        'risk_stats': stats
    }

def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]🚀 종합 전략 테스트[/bold cyan]\n"
        "[yellow]✅ 모든 개선사항 통합 테스트[/yellow]",
        border_style="cyan"
    ))
    console.print()

    # 로거 초기화
    session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = TradeLogger(log_dir="logs", session_name=session_name)

    console.print(f"[bold]세션:[/bold] {session_name}")
    console.print(f"[bold]로그 디렉토리:[/bold] logs/")
    console.print()

    # 데이터 다운로드
    console.print("[bold cyan]1. 데이터 다운로드[/bold cyan]")

    # 코스피 지수 (5분봉)
    kospi_5m = download_data(KOSPI_TICKER, period='7d', interval='5m')
    if kospi_5m is not None:
        kospi_5m = prepare_dataframe(kospi_5m)
        console.print(f"  ✓ 코스피 (5분): {len(kospi_5m)}개")

    # 종목 데이터
    stock_data = {}
    for name, ticker in STOCKS.items():
        # 5분봉
        data_5m = download_data(ticker, period='7d', interval='5m')
        # 일봉
        data_1d = download_data(ticker, period='3mo', interval='1d')

        if data_5m is not None and data_1d is not None:
            chart_data = prepare_chart_data(data_5m)
            daily_df = prepare_dataframe(data_1d)

            if chart_data and len(chart_data) >= 20:
                stock_data[name] = {
                    'chart_data': chart_data,
                    'daily_data': daily_df
                }
                console.print(f"  ✓ {name:20s}: 5분 {len(chart_data):4d}개, 일봉 {len(daily_df):3d}개")

    console.print()

    # 테스트 구성
    test_configs = [
        {
            'name': '기본 전략',
            'trailing_activation': 1.5,
            'trailing_ratio': 1.0,
            'stop_loss': 1.0,
            'breakout_candles': 0,
            'min_volume_value': 100_000_000,
            'use_trend_filter': True,
            'use_volume_filter': True,
            'use_breakout_confirm': False,
            'use_volume_value_filter': False,
            'use_market_filter': False,
            'use_daily_trend': False,
            'use_atr': False
        },
        {
            'name': '개선 전략 (시장 필터)',
            'trailing_activation': 1.5,
            'trailing_ratio': 1.0,
            'stop_loss': 1.0,
            'breakout_candles': 0,
            'min_volume_value': 100_000_000,
            'use_trend_filter': True,
            'use_volume_filter': True,
            'use_breakout_confirm': False,
            'use_volume_value_filter': False,
            'use_market_filter': True,
            'use_daily_trend': False,  # RSI 과매수로 모든 진입 차단됨
            'use_atr': False
        },
        {
            'name': '최종 전략 (ATR 트레일링)',
            'trailing_activation': 1.5,
            'trailing_ratio': 1.0,
            'stop_loss': 1.0,
            'breakout_candles': 0,
            'min_volume_value': 100_000_000,
            'use_trend_filter': True,
            'use_volume_filter': True,
            'use_breakout_confirm': False,
            'use_volume_value_filter': False,
            'use_market_filter': True,
            'use_daily_trend': False,  # RSI 과매수로 모든 진입 차단됨
            'use_atr': True            # ATR 기반 트레일링
        }
    ]

    # 시뮬레이션 실행
    console.print("[bold cyan]2. 전략 시뮬레이션[/bold cyan]")
    console.print()

    all_results = []

    for config in test_configs:
        console.print(f"[yellow]{config['name']}[/yellow] 테스트 중...")

        config_results = []
        for stock_name, data in stock_data.items():
            result = run_comprehensive_test(
                stock_name=stock_name,
                chart_data=data['chart_data'],
                daily_data_df=data['daily_data'],
                market_data_df=kospi_5m,
                logger=logger,
                config=config
            )
            config_results.append(result)
            console.print(f"  {stock_name:20s}: {result['return_pct']:+6.2f}% ({result['trade_count']}회)")

        all_results.extend(config_results)
        console.print()

    # 로그 요약 저장
    logger.save_summary()
    logger.print_summary()

    # 결과 비교 테이블
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]📊 전략 비교 결과[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    # 전략별 평균 계산
    strategy_summary = {}
    for config in test_configs:
        config_name = config['name']
        config_results = [r for r in all_results if r['config_name'] == config_name]

        strategy_summary[config_name] = {
            'avg_return': sum([r['return_pct'] for r in config_results]) / len(config_results),
            'total_trades': sum([r['trade_count'] for r in config_results]),
            'avg_win_rate': sum([r['win_rate'] for r in config_results]) / len(config_results),
            'results': config_results
        }

    # 요약 테이블
    summary_table = Table(
        title="전략별 성과 요약",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    summary_table.add_column("전략", style="cyan", width=30)
    summary_table.add_column("평균 수익률", justify="right", width=14)
    summary_table.add_column("총 거래", justify="center", width=10)
    summary_table.add_column("평균 승률", justify="right", width=12)

    for config_name, summary in strategy_summary.items():
        if summary['avg_return'] > 0:
            return_text = f"[green]+{summary['avg_return']:.2f}%[/green]"
        elif summary['avg_return'] < 0:
            return_text = f"[red]{summary['avg_return']:.2f}%[/red]"
        else:
            return_text = f"{summary['avg_return']:.2f}%"

        summary_table.add_row(
            config_name,
            return_text,
            f"{summary['total_trades']}회",
            f"{summary['avg_win_rate']:.1f}%"
        )

    console.print(summary_table)
    console.print()

    # 종목별 상세 비교
    console.print("[bold cyan]종목별 전략 비교[/bold cyan]")
    console.print()

    for stock_name in stock_data.keys():
        stock_table = Table(
            title=f"📈 {stock_name}",
            box=box.SIMPLE,
            show_header=True,
            header_style="bold yellow"
        )

        stock_table.add_column("전략", width=30)
        stock_table.add_column("수익률", justify="right", width=12)
        stock_table.add_column("거래", justify="center", width=8)
        stock_table.add_column("승률", justify="right", width=10)

        for config_name in strategy_summary.keys():
            result = [r for r in all_results if r['stock_name'] == stock_name and r['config_name'] == config_name][0]

            if result['return_pct'] > 0:
                return_text = f"[green]+{result['return_pct']:.2f}%[/green]"
            elif result['return_pct'] < 0:
                return_text = f"[red]{result['return_pct']:.2f}%[/red]"
            else:
                return_text = f"{result['return_pct']:.2f}%"

            win_rate_text = f"{result['win_rate']:.0f}%" if result['exit_count'] > 0 else "-"

            stock_table.add_row(
                config_name,
                return_text,
                f"{result['trade_count']}회",
                win_rate_text
            )

        console.print(stock_table)
        console.print()

    # 최종 결론
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]💡 테스트 결론[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    best_strategy = max(strategy_summary.items(), key=lambda x: x[1]['avg_return'])

    console.print(f"[bold green]🏆 최고 성과 전략:[/bold green] {best_strategy[0]}")
    console.print(f"   평균 수익률: {best_strategy[1]['avg_return']:+.2f}%")
    console.print(f"   총 거래: {best_strategy[1]['total_trades']}회")
    console.print(f"   평균 승률: {best_strategy[1]['avg_win_rate']:.1f}%")
    console.print()

    console.print(f"[bold]로그 파일:[/bold]")
    console.print(f"  - 이벤트 로그: logs/trade_log_{session_name}.jsonl")
    console.print(f"  - 요약: logs/summary_{session_name}.json")
    console.print()

if __name__ == "__main__":
    main()
