"""
10종목 시뮬레이션 테스트

YAML 설정 기반으로 10개 종목에 대해 VWAP 전략 백테스트 수행
"""
import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from datetime import datetime, timedelta
from utils.config_loader import load_config
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.risk_manager import RiskManager
from utils.trade_logger import TradeLogger
import yfinance as yf
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn


console = Console()


# 테스트할 한국 주요 종목 10개 (야후 파이낸스 티커)
TEST_STOCKS = [
    ("005930.KS", "삼성전자"),
    ("000660.KS", "SK하이닉스"),
    ("035420.KS", "NAVER"),
    ("051910.KS", "LG화학"),
    ("006400.KS", "삼성SDI"),
    ("035720.KS", "카카오"),
    ("005380.KS", "현대차"),
    ("000270.KS", "기아"),
    ("068270.KS", "셀트리온"),
    ("207940.KS", "삼성바이오로직스"),
]


def download_stock_data(ticker: str, days: int = 7, interval: str = "5m"):
    """주식 데이터 다운로드"""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=f"{days}d", interval=interval)

        if df.empty:
            return None

        df.reset_index(inplace=True)
        df.columns = [col.lower() for col in df.columns]
        return df

    except Exception as e:
        console.print(f"[red]❌ {ticker} 다운로드 실패: {e}[/red]")
        return None


def run_single_stock_simulation(
    ticker: str,
    stock_name: str,
    df: pd.DataFrame,
    config,
    logger: TradeLogger
):
    """단일 종목 시뮬레이션"""

    # Analyzer 초기화
    analyzer_config = config.get_analyzer_config()
    analyzer = EntryTimingAnalyzer(**analyzer_config)

    # Signal generation config
    signal_config = config.get_signal_generation_config()

    # Trailing config
    trailing_config = config.get_trailing_config()

    # VWAP 계산
    df = analyzer.calculate_vwap(df)

    # ATR 계산 (ATR 기반 트레일링 사용 시)
    df = analyzer.calculate_atr(df)

    # 시그널 생성
    df = analyzer.generate_signals(df, **signal_config)

    # 포지션 추적
    position = None
    trades = []

    # 시뮬레이션
    for idx in range(len(df)):
        row = df.iloc[idx]
        current_price = row['close']
        signal = row['signal']
        current_time = row.get('datetime', datetime.now())

        # 포지션 없을 때 - 매수 시그널 체크
        if position is None and signal == 1:
            # 재진입 체크 (설정된 경우)
            if config.get('re_entry.use_cooldown'):
                allowed, reason = analyzer.check_re_entry_allowed(ticker, current_time)
                if not allowed:
                    logger.log_event('RE_ENTRY_BLOCKED', ticker, {'reason': reason})
                    continue

            # 시간 필터 (설정된 경우)
            if config.get('time_filter.use_time_filter'):
                allowed, reason = analyzer.check_time_filter(current_time)
                if not allowed:
                    logger.log_event('TIME_FILTER_BLOCKED', ticker, {'reason': reason})
                    continue

            # 변동성 필터 (설정된 경우)
            if config.get('filters.use_volatility_filter'):
                allowed, reason = analyzer.check_volatility_filter(
                    df, idx,
                    min_atr_pct=config.get('filters.min_atr_pct', 0.5),
                    max_atr_pct=config.get('filters.max_atr_pct', 5.0)
                )
                if not allowed:
                    logger.log_event('VOLATILITY_BLOCKED', ticker, {'reason': reason})
                    continue

            # 매수 진입
            quantity = 10  # 단순화: 10주 고정
            position = {
                'entry_price': current_price,
                'quantity': quantity,
                'highest_price': current_price,
                'trailing_active': False,
                'entry_time': current_time,
                'entry_idx': idx
            }

            logger.log_signal(ticker, 1, current_price, row['vwap'], {})
            logger.log_entry(ticker, quantity, current_price, 0, 0)

        # 포지션 있을 때 - 청산 체크
        elif position is not None:
            # 최고가 갱신
            if current_price > position['highest_price']:
                position['highest_price'] = current_price

            # ATR 값 가져오기
            atr = row.get('atr', None)

            # 트레일링 스탑 체크
            should_exit, trailing_active, stop_price, exit_reason = analyzer.check_trailing_stop(
                current_price=current_price,
                avg_price=position['entry_price'],
                highest_price=position['highest_price'],
                trailing_active=position['trailing_active'],
                atr=atr,
                **trailing_config
            )

            position['trailing_active'] = trailing_active

            # 청산 실행
            if should_exit:
                profit = position['quantity'] * (current_price - position['entry_price'])
                profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                trades.append({
                    'entry_price': position['entry_price'],
                    'exit_price': current_price,
                    'profit': profit,
                    'profit_pct': profit_pct,
                    'reason': exit_reason,
                    'holding_bars': idx - position['entry_idx']
                })

                logger.log_exit(
                    ticker,
                    position['quantity'],
                    position['entry_price'],
                    current_price,
                    exit_reason,
                    position['highest_price'],
                    position['trailing_active']
                )

                # 재진입 방지용 청산 시간 기록
                analyzer.record_exit(ticker, current_time)

                position = None

            # VWAP 하향 돌파 시 비상 청산
            elif signal == -1:
                profit = position['quantity'] * (current_price - position['entry_price'])
                profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                trades.append({
                    'entry_price': position['entry_price'],
                    'exit_price': current_price,
                    'profit': profit,
                    'profit_pct': profit_pct,
                    'reason': 'VWAP 하향 돌파',
                    'holding_bars': idx - position['entry_idx']
                })

                logger.log_exit(
                    ticker,
                    position['quantity'],
                    position['entry_price'],
                    current_price,
                    'VWAP 하향 돌파',
                    position['highest_price'],
                    position['trailing_active']
                )

                analyzer.record_exit(ticker, current_time)
                position = None

    return trades


def main():
    """메인 시뮬레이션"""
    console.print("\n[bold green]╔══════════════════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║          10종목 VWAP 전략 시뮬레이션              ║[/bold green]")
    console.print("[bold green]╚══════════════════════════════════════════════════════╝[/bold green]\n")

    # 설정 로드
    config = load_config()
    console.print(f"[cyan]📄 설정 파일 로드 완료: config/strategy_config.yaml[/cyan]\n")

    # 로거 초기화
    logger = TradeLogger()

    # 전체 결과 저장
    all_results = []

    # 10종목 시뮬레이션
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:

        task = progress.add_task("[cyan]종목 다운로드 및 시뮬레이션...", total=len(TEST_STOCKS))

        for ticker, stock_name in TEST_STOCKS:
            progress.update(task, description=f"[cyan]{stock_name} ({ticker}) 처리 중...")

            # 데이터 다운로드
            df = download_stock_data(ticker, days=7, interval="5m")

            if df is None or len(df) < 50:
                console.print(f"[yellow]⚠️  {stock_name}: 데이터 부족, 스킵[/yellow]")
                progress.advance(task)
                continue

            # 시뮬레이션 실행
            trades = run_single_stock_simulation(ticker, stock_name, df, config, logger)

            # 결과 집계
            if trades:
                total_profit = sum(t['profit'] for t in trades)
                avg_profit_pct = sum(t['profit_pct'] for t in trades) / len(trades)
                win_trades = [t for t in trades if t['profit'] > 0]
                win_rate = len(win_trades) / len(trades) * 100 if trades else 0
                avg_holding = sum(t['holding_bars'] for t in trades) / len(trades)

                all_results.append({
                    'ticker': ticker,
                    'name': stock_name,
                    'trades': len(trades),
                    'total_profit': total_profit,
                    'avg_profit_pct': avg_profit_pct,
                    'win_rate': win_rate,
                    'avg_holding_bars': avg_holding,
                    'best_trade': max(trades, key=lambda x: x['profit_pct'])['profit_pct'],
                    'worst_trade': min(trades, key=lambda x: x['profit_pct'])['profit_pct']
                })
            else:
                all_results.append({
                    'ticker': ticker,
                    'name': stock_name,
                    'trades': 0,
                    'total_profit': 0,
                    'avg_profit_pct': 0,
                    'win_rate': 0,
                    'avg_holding_bars': 0,
                    'best_trade': 0,
                    'worst_trade': 0
                })

            progress.advance(task)

    # 결과 출력
    console.print("\n[bold cyan]═══ 종목별 시뮬레이션 결과 ═══[/bold cyan]\n")

    table = Table(title="10종목 백테스트 성과")
    table.add_column("종목", style="cyan", width=12)
    table.add_column("거래", justify="right", style="yellow")
    table.add_column("평균수익률", justify="right", style="magenta")
    table.add_column("승률", justify="right", style="green")
    table.add_column("최고", justify="right", style="bright_green")
    table.add_column("최저", justify="right", style="bright_red")
    table.add_column("평균보유", justify="right", style="blue")

    for result in all_results:
        profit_color = "green" if result['avg_profit_pct'] > 0 else "red"

        table.add_row(
            result['name'],
            str(result['trades']),
            f"[{profit_color}]{result['avg_profit_pct']:+.2f}%[/{profit_color}]",
            f"{result['win_rate']:.0f}%",
            f"{result['best_trade']:+.2f}%",
            f"{result['worst_trade']:+.2f}%",
            f"{result['avg_holding_bars']:.0f}봉"
        )

    console.print(table)

    # 전체 통계
    total_trades = sum(r['trades'] for r in all_results)
    active_stocks = [r for r in all_results if r['trades'] > 0]

    if active_stocks:
        console.print("\n[bold cyan]═══ 전체 통계 ═══[/bold cyan]\n")

        stats_table = Table()
        stats_table.add_column("항목", style="cyan")
        stats_table.add_column("값", style="yellow")

        overall_avg_profit = sum(r['avg_profit_pct'] for r in active_stocks) / len(active_stocks)
        overall_win_rate = sum(r['win_rate'] for r in active_stocks) / len(active_stocks)
        best_stock = max(active_stocks, key=lambda x: x['avg_profit_pct'])
        worst_stock = min(active_stocks, key=lambda x: x['avg_profit_pct'])

        stats_table.add_row("총 거래 횟수", f"{total_trades}회")
        stats_table.add_row("거래 발생 종목", f"{len(active_stocks)}/10종목")
        stats_table.add_row("평균 수익률", f"{overall_avg_profit:+.2f}%")
        stats_table.add_row("평균 승률", f"{overall_win_rate:.1f}%")
        stats_table.add_row("최고 성과 종목", f"{best_stock['name']} ({best_stock['avg_profit_pct']:+.2f}%)")
        stats_table.add_row("최저 성과 종목", f"{worst_stock['name']} ({worst_stock['avg_profit_pct']:+.2f}%)")

        console.print(stats_table)

    # 로그 저장
    logger.save_summary()
    console.print(f"\n[green]✅ 로그 저장 완료: {logger.log_file}[/green]")
    console.print(f"[green]✅ 요약 저장 완료: {logger.summary_file}[/green]\n")


if __name__ == "__main__":
    main()
