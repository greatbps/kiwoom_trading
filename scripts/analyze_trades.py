#!/usr/bin/env python3
"""
MA Cross 전략 복기 분석 리포트

사용법:
  python scripts/analyze_trades.py              # 최근 30일 분석
  python scripts/analyze_trades.py --days 7     # 최근 7일 분석
  python scripts/analyze_trades.py --symbol 005930  # 특정 종목 분석
"""
import sys
import os
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase
import argparse
from rich.console import Console
from rich.table import Table

console = Console()


def print_overall_stats(stats: dict):
    """전체 통계 출력"""
    overall = stats['overall']

    table = Table(title="📊 전체 통계", show_header=True, header_style="bold cyan")
    table.add_column("지표", style="cyan", width=25)
    table.add_column("값", style="yellow", justify="right")

    table.add_row("총 거래 수", str(overall.get('total_trades', 0)))
    table.add_row("승리", f"{overall.get('win_count', 0)}건", style="green")
    table.add_row("패배", f"{overall.get('loss_count', 0)}건", style="red")

    win_count = overall.get('win_count', 0)
    total = overall.get('total_trades', 0)
    if total > 0:
        win_rate = (win_count / total) * 100
        table.add_row("승률", f"{win_rate:.1f}%")

    table.add_row("평균 손익", f"{overall.get('avg_pnl', 0):.2f}%")
    table.add_row("평균 승리", f"{overall.get('avg_win', 0):.2f}%", style="green")
    table.add_row("평균 손실", f"{overall.get('avg_loss', 0):.2f}%", style="red")

    if overall.get('avg_win') and overall.get('avg_loss'):
        rr_ratio = abs(overall.get('avg_win', 0) / overall.get('avg_loss', 1))
        table.add_row("손익비 (R:R)", f"{rr_ratio:.2f}")

    table.add_row("Hard Stop 비율",
                 f"{overall.get('hard_stop_count', 0)}/{total} "
                 f"({(overall.get('hard_stop_count', 0)/max(total, 1)*100):.1f}%)")

    table.add_row("평균 MAE", f"{overall.get('avg_mae', 0):.2f}%", style="magenta")

    console.print(table)


def print_candle_stats(stats: dict):
    """캔들 타입별 통계"""
    candle_stats = stats['by_candle_type']

    if not candle_stats:
        return

    table = Table(title="📈 진입 캔들 타입별 성과", show_header=True, header_style="bold cyan")
    table.add_column("캔들 타입", style="cyan")
    table.add_column("거래 수", justify="right")
    table.add_column("평균 손익", justify="right")

    candle_names = {
        'strong_bull': '장대양봉',
        'weak_bull': '짧은 양봉',
        'doji': '도지/윗꼬리',
        'bear': '음봉'
    }

    for row in candle_stats:
        candle_type = candle_names.get(row['entry_candle_type'], row['entry_candle_type'])
        trades = row['trades']
        avg_pnl = row['avg_pnl']

        style = "green" if avg_pnl > 0 else "red"
        table.add_row(
            candle_type,
            str(trades),
            f"{avg_pnl:+.2f}%",
            style=style
        )

    console.print(table)


def print_delay_stats(stats: dict):
    """진입 지연별 통계"""
    delay_stats = stats['by_delay']

    if not delay_stats:
        return

    table = Table(title="⏱️  Cross 후 진입 지연별 성과", show_header=True, header_style="bold cyan")
    table.add_column("지연 봉 수", style="cyan")
    table.add_column("거래 수", justify="right")
    table.add_column("평균 손익", justify="right")

    for row in delay_stats:
        delay = row['ma_cross_delay_bars']
        trades = row['trades']
        avg_pnl = row['avg_pnl']

        delay_label = "즉시" if delay == 0 else f"{delay}봉 후"
        style = "green" if avg_pnl > 0 else "red"

        table.add_row(
            delay_label,
            str(trades),
            f"{avg_pnl:+.2f}%",
            style=style
        )

    console.print(table)


def print_failure_patterns(stats: dict):
    """실패 패턴 분석"""
    failure_patterns = stats['failure_patterns']

    if not failure_patterns:
        console.print("\n[green]✅ 복합 실패 패턴 없음 (2개 이상 플래그)[/green]\n")
        return

    console.print(f"\n[red]❌ 복합 실패 패턴: {len(failure_patterns)}건[/red]\n")

    table = Table(show_header=True, header_style="bold red")
    table.add_column("날짜", style="cyan")
    table.add_column("종목", style="yellow")
    table.add_column("손익", justify="right")
    table.add_column("실패 플래그", style="red")

    for trade in failure_patterns[:10]:  # 최근 10건
        flags = []
        if trade.get('late_entry'):
            flags.append("늦은진입")
        if trade.get('no_volume'):
            flags.append("거래량부족")
        if trade.get('chasing_entry'):
            flags.append("추격매수")
        if trade.get('near_resistance'):
            flags.append("저항선근처")

        pnl = trade.get('pnl_pct', 0)
        style = "green" if pnl > 0 else "red"

        table.add_row(
            str(trade['trade_date']),
            trade['symbol'],
            f"{pnl:+.2f}%",
            ", ".join(flags),
            style=style
        )

    console.print(table)


def print_recommendations(stats: dict):
    """데이터 기반 권장사항"""
    overall = stats['overall']
    candle_stats = stats['by_candle_type']
    delay_stats = stats['by_delay']

    console.print("\n[bold cyan]💡 데이터 기반 권장사항:[/bold cyan]\n")

    # Hard Stop 비율 분석
    hard_stop_count = overall.get('hard_stop_count', 0)
    total_trades = overall.get('total_trades', 1)
    hard_stop_ratio = (hard_stop_count / total_trades) * 100

    if hard_stop_ratio > 30:
        console.print(f"  ⚠️  Hard Stop 비율이 높습니다 ({hard_stop_ratio:.1f}%)")
        console.print(f"     → 진입 조건을 강화하거나 Hard Stop을 -2.5%로 완화 검토\n")

    # 캔들 타입 분석
    if candle_stats:
        worst_candle = min(candle_stats, key=lambda x: x['avg_pnl'])
        if worst_candle['avg_pnl'] < -0.5:
            candle_names = {
                'strong_bull': '장대양봉',
                'weak_bull': '짧은 양봉',
                'doji': '도지/윗꼬리',
                'bear': '음봉'
            }
            candle_name = candle_names.get(worst_candle['entry_candle_type'], worst_candle['entry_candle_type'])
            console.print(f"  ❌ '{candle_name}' 진입의 평균 손익: {worst_candle['avg_pnl']:+.2f}%")
            console.print(f"     → 이 캔들 타입에서의 진입을 피하세요\n")

    # 진입 지연 분석
    if delay_stats and len(delay_stats) > 1:
        immediate = next((x for x in delay_stats if x['ma_cross_delay_bars'] == 0), None)
        delayed = [x for x in delay_stats if x['ma_cross_delay_bars'] >= 2]

        if immediate and delayed:
            delayed_avg = sum(x['avg_pnl'] * x['trades'] for x in delayed) / sum(x['trades'] for x in delayed)
            if immediate['avg_pnl'] > delayed_avg + 1.0:
                console.print(f"  ⏱️  즉시 진입이 {abs(immediate['avg_pnl'] - delayed_avg):.1f}% 더 우수")
                console.print(f"     → Cross 후 2봉 이상 지연된 진입은 피하세요\n")

    # MAE 분석
    avg_mae = overall.get('avg_mae', 0)
    if avg_mae and abs(avg_mae) > 1.5:
        console.print(f"  📉 평균 MAE (최대 역행): {avg_mae:.2f}%")
        console.print(f"     → 진입 후 눌림을 견디는 전략 필요 또는 눌림 대기 진입 검토\n")


def main():
    parser = argparse.ArgumentParser(description='MA Cross 전략 복기 분석')
    parser.add_argument('--days', type=int, default=30,
                       help='분석 기간 (일, 기본: 30일)')
    parser.add_argument('--symbol', type=str, default=None,
                       help='특정 종목 분석 (선택)')
    args = parser.parse_args()

    console.print(f"\n[bold cyan]{'='*80}[/bold cyan]")
    console.print(f"[bold cyan]MA Cross 전략 복기 분석 리포트[/bold cyan]")
    console.print(f"[bold cyan]기간: 최근 {args.days}일[/bold cyan]")
    if args.symbol:
        console.print(f"[bold cyan]종목: {args.symbol}[/bold cyan]")
    console.print(f"[bold cyan]{'='*80}[/bold cyan]\n")

    # DB 연결
    db = TradingDatabase()

    # 통계 조회
    stats = db.get_trade_review_stats(days=args.days)

    # 통계 출력
    if stats['overall']['total_trades'] == 0:
        console.print("[yellow]⚠️  복기 데이터가 없습니다.[/yellow]")
        console.print("[dim]거래 시 자동으로 trade_review 테이블에 데이터가 저장됩니다.[/dim]\n")
        db.close()
        return

    print_overall_stats(stats)
    console.print()

    print_candle_stats(stats)
    console.print()

    print_delay_stats(stats)
    console.print()

    print_failure_patterns(stats)
    console.print()

    print_recommendations(stats)

    console.print(f"[bold cyan]{'='*80}[/bold cyan]\n")

    db.close()


if __name__ == '__main__':
    main()
