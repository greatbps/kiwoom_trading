"""
전략 비교 테스트

3가지 전략을 동일한 10종목에 대해 테스트하고 비교:
1. 공격적 전략 (Aggressive)
2. 보수적 전략 (Conservative)
3. 부분 청산 전략 (Partial Exit)
"""
import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from datetime import datetime
from utils.config_loader import ConfigLoader
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from utils.trade_logger import TradeLogger
import yfinance as yf
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
import time


console = Console()


# 테스트할 종목 10개
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
    """주식 데이터 다운로드 (캐싱)"""
    cache_file = Path(f"cache/{ticker.replace('.', '_')}_{days}d_{interval}.pkl")
    cache_file.parent.mkdir(exist_ok=True)

    # 캐시 확인
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            return df
        except:
            pass

    # 다운로드
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=f"{days}d", interval=interval)

        if df.empty:
            return None

        df.reset_index(inplace=True)
        df.columns = [col.lower() for col in df.columns]

        # 캐시 저장
        df.to_pickle(cache_file)
        return df

    except Exception as e:
        console.print(f"[red]❌ {ticker} 다운로드 실패: {e}[/red]")
        return None


def run_simulation_with_config(config_path: str, stock_data_cache: dict):
    """특정 설정으로 시뮬레이션 실행"""

    config = ConfigLoader(config_path)
    logger = TradeLogger()

    all_trades = []

    for ticker, stock_name in TEST_STOCKS:
        if ticker not in stock_data_cache:
            continue

        df = stock_data_cache[ticker].copy()

        # Analyzer 초기화
        analyzer_config = config.get_analyzer_config()
        analyzer = EntryTimingAnalyzer(**analyzer_config)

        # Signal generation config
        signal_config = config.get_signal_generation_config()
        trailing_config = config.get_trailing_config()
        trailing_kwargs = {
            'use_atr_based': trailing_config.get('use_atr_based', False),
            'atr_multiplier': trailing_config.get('atr_multiplier', 1.5),
            'use_profit_tier': trailing_config.get('use_profit_tier', False),
            'profit_tier_threshold': trailing_config.get('profit_tier_threshold', 3.0)
        }
        partial_config = config.get_partial_exit_config()

        # VWAP, ATR 계산
        df = analyzer.calculate_vwap(df)
        df = analyzer.calculate_atr(df)

        # 시그널 생성
        df = analyzer.generate_signals(df, **signal_config)

        # 포지션 추적
        position = None
        executed_tiers = []

        for idx in range(len(df)):
            row = df.iloc[idx]
            current_price = row['close']
            signal = row['signal']
            current_time = row.get('datetime', datetime.now())

            # 진입 로직
            if position is None and signal == 1:
                # 재진입 체크
                if config.get('re_entry.use_cooldown'):
                    allowed, reason = analyzer.check_re_entry_allowed(ticker, current_time)
                    if not allowed:
                        continue

                # 시간 필터
                if config.get('time_filter.use_time_filter'):
                    allowed, reason = analyzer.check_time_filter(current_time)
                    if not allowed:
                        continue

                # 변동성 필터
                if config.get('filters.use_volatility_filter'):
                    allowed, reason = analyzer.check_volatility_filter(
                        df, idx,
                        min_atr_pct=config.get('filters.min_atr_pct', 0.5),
                        max_atr_pct=config.get('filters.max_atr_pct', 5.0)
                    )
                    if not allowed:
                        continue

                # 진입
                position = {
                    'ticker': ticker,
                    'name': stock_name,
                    'entry_price': current_price,
                    'quantity': 100,
                    'original_quantity': 100,
                    'highest_price': current_price,
                    'trailing_active': False,
                    'entry_time': current_time,
                    'entry_idx': idx
                }
                executed_tiers = []

            # 청산 로직
            elif position is not None:
                # 최고가 갱신
                if current_price > position['highest_price']:
                    position['highest_price'] = current_price

                # 부분 청산 체크
                if partial_config['enabled'] and partial_config['tiers']:
                    should_exit, exit_qty, reason, new_executed = analyzer.check_partial_exit(
                        current_price=current_price,
                        avg_price=position['entry_price'],
                        current_quantity=position['quantity'],
                        exit_tiers=partial_config['tiers'],
                        executed_tiers=executed_tiers
                    )

                    if should_exit:
                        # 부분 청산 기록
                        profit = exit_qty * (current_price - position['entry_price'])
                        profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                        all_trades.append({
                            'ticker': ticker,
                            'name': stock_name,
                            'entry_price': position['entry_price'],
                            'exit_price': current_price,
                            'quantity': exit_qty,
                            'profit': profit,
                            'profit_pct': profit_pct,
                            'reason': reason,
                            'holding_bars': idx - position['entry_idx'],
                            'is_partial': True
                        })

                        position['quantity'] -= exit_qty
                        executed_tiers = new_executed

                        # 전량 청산되면 포지션 종료
                        if position['quantity'] <= 0:
                            analyzer.record_exit(ticker, current_time)
                            position = None
                            executed_tiers = []
                        continue

                # 트레일링 스탑 체크
                atr = row.get('atr', None)
                should_exit, trailing_active, stop_price, exit_reason = analyzer.check_trailing_stop(
                    current_price=current_price,
                    avg_price=position['entry_price'],
                    highest_price=position['highest_price'],
                    trailing_active=position['trailing_active'],
                    atr=atr,
                    **trailing_kwargs
                )

                position['trailing_active'] = trailing_active

                # 트레일링 청산
                if should_exit:
                    profit = position['quantity'] * (current_price - position['entry_price'])
                    profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                    all_trades.append({
                        'ticker': ticker,
                        'name': stock_name,
                        'entry_price': position['entry_price'],
                        'exit_price': current_price,
                        'quantity': position['quantity'],
                        'profit': profit,
                        'profit_pct': profit_pct,
                        'reason': exit_reason,
                        'holding_bars': idx - position['entry_idx'],
                        'is_partial': False
                    })

                    analyzer.record_exit(ticker, current_time)
                    position = None
                    executed_tiers = []

                # VWAP 하향 돌파
                elif signal == -1:
                    profit = position['quantity'] * (current_price - position['entry_price'])
                    profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                    all_trades.append({
                        'ticker': ticker,
                        'name': stock_name,
                        'entry_price': position['entry_price'],
                        'exit_price': current_price,
                        'quantity': position['quantity'],
                        'profit': profit,
                        'profit_pct': profit_pct,
                        'reason': 'VWAP 하향 돌파',
                        'holding_bars': idx - position['entry_idx'],
                        'is_partial': False
                    })

                    analyzer.record_exit(ticker, current_time)
                    position = None
                    executed_tiers = []

    return all_trades


def analyze_trades(trades):
    """거래 분석"""
    if not trades:
        return {
            'total_trades': 0,
            'total_profit': 0,
            'avg_profit_pct': 0,
            'win_rate': 0,
            'avg_holding_bars': 0,
            'best_trade': 0,
            'worst_trade': 0,
            'profit_factor': 0,
            'max_drawdown': 0
        }

    total_profit = sum(t['profit'] for t in trades)
    avg_profit_pct = sum(t['profit_pct'] for t in trades) / len(trades)

    win_trades = [t for t in trades if t['profit'] > 0]
    loss_trades = [t for t in trades if t['profit'] < 0]

    win_rate = len(win_trades) / len(trades) * 100 if trades else 0
    avg_holding = sum(t['holding_bars'] for t in trades) / len(trades)

    best_trade = max(trades, key=lambda x: x['profit_pct'])['profit_pct']
    worst_trade = min(trades, key=lambda x: x['profit_pct'])['profit_pct']

    # Profit Factor 계산
    total_wins = sum(t['profit'] for t in win_trades) if win_trades else 0
    total_losses = abs(sum(t['profit'] for t in loss_trades)) if loss_trades else 1
    profit_factor = total_wins / total_losses if total_losses > 0 else 0

    # 간단한 최대 낙폭 계산
    cumulative = 0
    peak = 0
    max_dd = 0
    for trade in trades:
        cumulative += trade['profit']
        if cumulative > peak:
            peak = cumulative
        dd = ((peak - cumulative) / peak * 100) if peak > 0 else 0
        max_dd = max(max_dd, dd)

    return {
        'total_trades': len(trades),
        'total_profit': total_profit,
        'avg_profit_pct': avg_profit_pct,
        'win_rate': win_rate,
        'avg_holding_bars': avg_holding,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'profit_factor': profit_factor,
        'max_drawdown': max_dd,
        'win_count': len(win_trades),
        'loss_count': len(loss_trades)
    }


def main():
    """메인 실행"""
    console.print("\n[bold green]╔══════════════════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║          3가지 전략 비교 시뮬레이션               ║[/bold green]")
    console.print("[bold green]╚══════════════════════════════════════════════════════╝[/bold green]\n")

    # 1. 데이터 다운로드 (캐싱)
    console.print("[cyan]📥 주식 데이터 다운로드 중...[/cyan]\n")
    stock_data_cache = {}

    for ticker, stock_name in TEST_STOCKS:
        df = download_stock_data(ticker, days=7, interval="5m")
        if df is not None and len(df) >= 50:
            stock_data_cache[ticker] = df
            console.print(f"  ✅ {stock_name}: {len(df)}개 봉")

    console.print(f"\n[green]✅ {len(stock_data_cache)}/10 종목 데이터 준비 완료[/green]\n")

    # 2. 전략별 시뮬레이션
    strategies = [
        ("공격적 전략", "config/strategy_aggressive.yaml"),
        ("보수적 전략", "config/strategy_conservative.yaml"),
        ("부분 청산 전략", "config/strategy_partial_exit.yaml")
    ]

    results = {}

    for idx, (strategy_name, config_path) in enumerate(strategies, 1):
        console.print(f"[bold cyan]{'='*60}[/bold cyan]")
        console.print(f"[bold cyan]{idx}. {strategy_name} 시뮬레이션 실행 중...[/bold cyan]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")

        start_time = time.time()
        trades = run_simulation_with_config(config_path, stock_data_cache)
        elapsed = time.time() - start_time

        analysis = analyze_trades(trades)
        results[strategy_name] = analysis

        console.print(f"[green]✅ 완료 ({elapsed:.1f}초) - {analysis['total_trades']}건 거래[/green]\n")

    # 3. 결과 비교 테이블
    console.print("\n[bold cyan]╔══════════════════════════════════════════════════════╗[/bold cyan]")
    console.print("[bold cyan]║               전략 비교 결과                         ║[/bold cyan]")
    console.print("[bold cyan]╚══════════════════════════════════════════════════════╝[/bold cyan]\n")

    table = Table(title="전략별 성과 비교")
    table.add_column("지표", style="cyan", width=20)
    table.add_column("공격적", justify="right", style="yellow")
    table.add_column("보수적", justify="right", style="green")
    table.add_column("부분청산", justify="right", style="magenta")

    metrics = [
        ("총 거래 횟수", lambda x: f"{x['total_trades']}회"),
        ("승률", lambda x: f"{x['win_rate']:.1f}%"),
        ("평균 수익률", lambda x: f"{x['avg_profit_pct']:+.2f}%"),
        ("총 실현 수익", lambda x: f"{x['total_profit']:,.0f}원"),
        ("최고 거래", lambda x: f"{x['best_trade']:+.2f}%"),
        ("최저 거래", lambda x: f"{x['worst_trade']:+.2f}%"),
        ("Profit Factor", lambda x: f"{x['profit_factor']:.2f}"),
        ("평균 보유시간", lambda x: f"{x['avg_holding_bars']:.0f}봉"),
        ("최대 낙폭", lambda x: f"{x['max_drawdown']:.2f}%"),
    ]

    for metric_name, formatter in metrics:
        table.add_row(
            metric_name,
            formatter(results["공격적 전략"]),
            formatter(results["보수적 전략"]),
            formatter(results["부분 청산 전략"])
        )

    console.print(table)

    # 4. 승자 판정
    console.print("\n[bold cyan]═══ 전략 분석 ═══[/bold cyan]\n")

    best_profit = max(results.items(), key=lambda x: x[1]['total_profit'])
    best_winrate = max(results.items(), key=lambda x: x[1]['win_rate'])
    best_pf = max(results.items(), key=lambda x: x[1]['profit_factor'])

    console.print(f"🏆 [bold green]최고 수익:[/bold green] {best_profit[0]} ({best_profit[1]['total_profit']:,.0f}원)")
    console.print(f"🎯 [bold yellow]최고 승률:[/bold yellow] {best_winrate[0]} ({best_winrate[1]['win_rate']:.1f}%)")
    console.print(f"📊 [bold magenta]최고 PF:[/bold magenta] {best_pf[0]} (PF {best_pf[1]['profit_factor']:.2f})")

    console.print("\n")


if __name__ == "__main__":
    main()
