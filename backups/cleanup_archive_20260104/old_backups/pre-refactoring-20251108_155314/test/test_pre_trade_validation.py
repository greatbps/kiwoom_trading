"""
사전 매수 검증 시스템 테스트

실제 매수 전 시뮬레이션으로 검증하는 시스템 데모
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from datetime import datetime
from utils.config_loader import load_config
from analyzers.pre_trade_validator import PreTradeValidator, AdaptiveValidator
import yfinance as yf
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box


console = Console()


# 테스트 종목
TEST_STOCKS = [
    ("005930.KS", "삼성전자"),
    ("000660.KS", "SK하이닉스"),
    ("035420.KS", "NAVER"),
    ("051910.KS", "LG화학"),
    ("035720.KS", "카카오"),
]


def download_stock_data(ticker: str, days: int = 7):
    """주식 데이터 다운로드"""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=f"{days}d", interval="5m")

        if df.empty:
            return None

        df.reset_index(inplace=True)
        df.columns = [col.lower() for col in df.columns]
        return df

    except Exception as e:
        console.print(f"[red]❌ {ticker} 다운로드 실패: {e}[/red]")
        return None


def simulate_real_time_signal(ticker: str, stock_name: str, validator: PreTradeValidator):
    """실시간 신호 시뮬레이션"""

    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]📡 {stock_name} ({ticker}) - 매수 신호 발생![/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")

    # 과거 데이터 다운로드 (검증용)
    console.print(f"[yellow]⏳ 과거 데이터 다운로드 중...[/yellow]")
    historical_data = download_stock_data(ticker, days=7)

    if historical_data is None or len(historical_data) < 100:
        console.print(f"[red]❌ 데이터 부족으로 검증 불가[/red]\n")
        return False

    console.print(f"[green]✅ {len(historical_data)}개 봉 데이터 로드 완료[/green]\n")

    # 사전 검증 실행
    console.print(f"[yellow]🔍 사전 검증 시뮬레이션 실행 중...[/yellow]")

    current_price = historical_data['close'].iloc[-1]
    current_time = datetime.now()

    allowed, reason, stats = validator.validate_trade(
        stock_code=ticker,
        stock_name=stock_name,
        historical_data=historical_data,
        current_price=current_price,
        current_time=current_time
    )

    # 결과 출력
    console.print()

    if allowed:
        panel = Panel(
            validator.get_validation_summary(stats) + f"\n{reason}",
            title=f"[bold green]✅ 매수 승인 - {stock_name}[/bold green]",
            border_style="green",
            box=box.DOUBLE
        )
    else:
        panel = Panel(
            validator.get_validation_summary(stats) + f"\n{reason}",
            title=f"[bold red]❌ 매수 거부 - {stock_name}[/bold red]",
            border_style="red",
            box=box.DOUBLE
        )

    console.print(panel)

    return allowed


def test_basic_validator():
    """기본 검증기 테스트"""
    console.print("\n[bold green]╔══════════════════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║       사전 매수 검증 시스템 테스트 (기본)        ║[/bold green]")
    console.print("[bold green]╚══════════════════════════════════════════════════════╝[/bold green]\n")

    # 설정 로드 (하이브리드 전략)
    config = load_config("config/strategy_hybrid.yaml")

    # 검증기 초기화
    validator = PreTradeValidator(
        config=config,
        lookback_days=5,
        min_trades=2,          # 최소 2회 거래
        min_win_rate=50.0,     # 최소 50% 승률
        min_avg_profit=0.5,    # 최소 +0.5% 수익률
        min_profit_factor=1.2  # 최소 PF 1.2
    )

    console.print(f"[cyan]검증 기준:[/cyan]")
    console.print(f"  - 최소 거래: {validator.min_trades}회")
    console.print(f"  - 최소 승률: {validator.min_win_rate}%")
    console.print(f"  - 최소 평균 수익률: {validator.min_avg_profit:+.2f}%")
    console.print(f"  - 최소 Profit Factor: {validator.min_profit_factor}")

    # 결과 집계
    results = []

    # 각 종목 테스트
    for ticker, stock_name in TEST_STOCKS:
        allowed = simulate_real_time_signal(ticker, stock_name, validator)
        results.append({
            'ticker': ticker,
            'name': stock_name,
            'allowed': allowed
        })

    # 최종 요약
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]최종 요약[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")

    summary_table = Table(title="검증 결과 요약")
    summary_table.add_column("종목", style="cyan")
    summary_table.add_column("티커", style="yellow")
    summary_table.add_column("결과", style="bold")

    approved = 0
    rejected = 0

    for result in results:
        if result['allowed']:
            summary_table.add_row(
                result['name'],
                result['ticker'],
                "[green]✅ 승인[/green]"
            )
            approved += 1
        else:
            summary_table.add_row(
                result['name'],
                result['ticker'],
                "[red]❌ 거부[/red]"
            )
            rejected += 1

    console.print(summary_table)

    console.print(f"\n[bold]승인: {approved}개 / 거부: {rejected}개 / 총: {len(results)}개[/bold]\n")


def test_adaptive_validator():
    """적응형 검증기 테스트"""
    console.print("\n[bold green]╔══════════════════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║      사전 매수 검증 시스템 테스트 (적응형)       ║[/bold green]")
    console.print("[bold green]╚══════════════════════════════════════════════════════╝[/bold green]\n")

    # 설정 로드
    config = load_config("config/strategy_hybrid.yaml")

    # 적응형 검증기
    validator = AdaptiveValidator(config=config)

    # 시장 데이터 다운로드 (KOSPI)
    console.print("[cyan]📊 시장 상황 분석 중... (KOSPI)[/cyan]")
    market_data = download_stock_data("^KS11", days=30)

    if market_data is not None:
        condition = validator.detect_market_condition(market_data)
        validator.set_market_condition(condition)

        console.print(f"\n[bold yellow]🌍 시장 상황: {condition}[/bold yellow]")
        console.print(f"[cyan]검증 기준 자동 조정:[/cyan]")
        console.print(f"  - 최소 승률: {validator.min_win_rate}%")
        console.print(f"  - 최소 평균 수익률: {validator.min_avg_profit:+.2f}%")
        console.print(f"  - 최소 Profit Factor: {validator.min_profit_factor}\n")

    # 테스트 종목 (샘플)
    ticker, stock_name = TEST_STOCKS[0]
    simulate_real_time_signal(ticker, stock_name, validator)


def main():
    """메인 실행"""
    console.print("\n[bold]사전 매수 검증 시스템을 테스트합니다.[/bold]\n")
    console.print("[cyan]이 시스템은 실제 매수 전에 해당 종목의 최근 성과를 시뮬레이션하여[/cyan]")
    console.print("[cyan]매수 여부를 자동으로 판단합니다.[/cyan]\n")

    # 1. 기본 검증기 테스트
    test_basic_validator()

    # 2. 적응형 검증기 테스트
    console.print("\n" + "="*60 + "\n")
    test_adaptive_validator()

    console.print("\n[bold green]✅ 모든 테스트 완료![/bold green]\n")


if __name__ == "__main__":
    main()
