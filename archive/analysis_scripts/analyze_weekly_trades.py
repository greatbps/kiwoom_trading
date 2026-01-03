#!/usr/bin/env python3
"""
이번주 거래 분석 및 ML 개선 포인트 도출
"""
import json
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

def load_trades():
    """거래 데이터 로드"""
    with open('data/risk_log.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data

def parse_trades(weekly_trades):
    """거래 데이터를 완결된 거래 단위로 파싱"""
    trades_by_stock = defaultdict(list)

    for trade in weekly_trades:
        stock_code = trade['stock_code']
        trades_by_stock[stock_code].append(trade)

    completed_trades = []

    for stock_code, stock_trades in trades_by_stock.items():
        buys = [t for t in stock_trades if t['type'] == 'BUY']
        sells = [t for t in stock_trades if t['type'] == 'SELL']

        # 매수-매도 매칭
        for buy in buys:
            buy_time = datetime.fromisoformat(buy['timestamp'])
            buy_qty = buy['quantity']
            buy_price = buy['price']

            # 이후 매도 찾기
            matching_sells = []
            remaining_qty = buy_qty

            for sell in sells:
                sell_time = datetime.fromisoformat(sell['timestamp'])
                if sell_time > buy_time and remaining_qty > 0:
                    sell_qty = min(sell['quantity'], remaining_qty)
                    matching_sells.append({
                        'sell': sell,
                        'qty': sell_qty
                    })
                    remaining_qty -= sell_qty

            # 완결 거래 기록
            if matching_sells:
                total_sell_amount = sum(m['qty'] * m['sell']['price'] for m in matching_sells)
                total_qty = sum(m['qty'] for m in matching_sells)
                avg_sell_price = total_sell_amount / total_qty if total_qty > 0 else 0

                profit_pct = ((avg_sell_price - buy_price) / buy_price) * 100
                hold_time = (datetime.fromisoformat(matching_sells[-1]['sell']['timestamp']) - buy_time).total_seconds() / 60

                completed_trades.append({
                    'stock_code': stock_code,
                    'stock_name': buy['stock_name'],
                    'buy_time': buy_time,
                    'sell_time': datetime.fromisoformat(matching_sells[-1]['sell']['timestamp']),
                    'buy_price': buy_price,
                    'sell_price': avg_sell_price,
                    'quantity': total_qty,
                    'profit_pct': profit_pct,
                    'profit_amount': total_sell_amount - (buy_price * total_qty),
                    'hold_minutes': hold_time,
                    'is_win': profit_pct > 0
                })

    return completed_trades

def analyze_time_patterns(completed_trades):
    """시간대별 패턴 분석"""
    time_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total_profit': 0})

    for trade in completed_trades:
        hour = trade['buy_time'].hour
        time_slot = f"{hour:02d}:00-{hour+1:02d}:00"

        if trade['is_win']:
            time_stats[time_slot]['wins'] += 1
        else:
            time_stats[time_slot]['losses'] += 1
        time_stats[time_slot]['total_profit'] += trade['profit_pct']

    return time_stats

def analyze_failures(completed_trades):
    """실패 거래 분석"""
    failures = [t for t in completed_trades if not t['is_win']]

    failure_patterns = {
        'early_cut': [],  # 30분 이내 손절
        'late_hold': [],  # 장시간 보유 후 손절
        'big_loss': []    # 큰 손실 (-2% 이상)
    }

    for trade in failures:
        if trade['hold_minutes'] <= 30:
            failure_patterns['early_cut'].append(trade)
        if trade['hold_minutes'] >= 120:
            failure_patterns['late_hold'].append(trade)
        if trade['profit_pct'] <= -2.0:
            failure_patterns['big_loss'].append(trade)

    return failure_patterns

def main():
    console.print("\n" + "=" * 80, style="bold cyan")
    console.print("📊 이번주 거래 분석 및 ML 개선 포인트 도출", style="bold cyan")
    console.print("=" * 80 + "\n", style="bold cyan")

    # 1. 데이터 로드
    data = load_trades()
    week_start = data['week_start']
    weekly_trades = data['weekly_trades']
    weekly_pnl = data['weekly_realized_pnl']

    console.print(f"[cyan]분석 기간: {week_start} ~ 현재[/cyan]")
    console.print(f"[cyan]주간 손익: {weekly_pnl:,.0f}원[/cyan]\n")

    # 2. 완결 거래 파싱
    completed_trades = parse_trades(weekly_trades)

    if not completed_trades:
        console.print("[yellow]⚠️  완결된 거래가 없습니다.[/yellow]")
        return

    # 3. 전체 통계
    total_trades = len(completed_trades)
    wins = sum(1 for t in completed_trades if t['is_win'])
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    avg_win = sum(t['profit_pct'] for t in completed_trades if t['is_win']) / wins if wins > 0 else 0
    avg_loss = sum(t['profit_pct'] for t in completed_trades if not t['is_win']) / losses if losses > 0 else 0

    avg_hold_win = sum(t['hold_minutes'] for t in completed_trades if t['is_win']) / wins if wins > 0 else 0
    avg_hold_loss = sum(t['hold_minutes'] for t in completed_trades if not t['is_win']) / losses if losses > 0 else 0

    # 통계 테이블
    stats_table = Table(title="📈 전체 거래 통계", box=box.ROUNDED)
    stats_table.add_column("항목", style="cyan")
    stats_table.add_column("값", style="yellow", justify="right")

    stats_table.add_row("총 거래 수", f"{total_trades}건")
    stats_table.add_row("승리", f"{wins}건", style="green")
    stats_table.add_row("패배", f"{losses}건", style="red")
    stats_table.add_row("승률", f"{win_rate:.1f}%", style="bold green" if win_rate >= 50 else "bold red")
    stats_table.add_row("평균 승리", f"{avg_win:+.2f}%", style="green")
    stats_table.add_row("평균 손실", f"{avg_loss:+.2f}%", style="red")
    stats_table.add_row("손익비", f"{abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "N/A")
    stats_table.add_row("평균 보유(승)", f"{avg_hold_win:.0f}분")
    stats_table.add_row("평균 보유(패)", f"{avg_hold_loss:.0f}분")

    console.print(stats_table)
    console.print()

    # 4. 거래 상세
    trades_table = Table(title="📋 거래 상세", box=box.ROUNDED)
    trades_table.add_column("날짜", style="cyan")
    trades_table.add_column("시간", style="dim")
    trades_table.add_column("종목", style="white")
    trades_table.add_column("진입가", justify="right")
    trades_table.add_column("청산가", justify="right")
    trades_table.add_column("수익률", justify="right")
    trades_table.add_column("보유시간", justify="right")
    trades_table.add_column("결과", justify="center")

    for trade in sorted(completed_trades, key=lambda x: x['buy_time']):
        result_emoji = "✅" if trade['is_win'] else "❌"
        result_style = "green" if trade['is_win'] else "red"

        trades_table.add_row(
            trade['buy_time'].strftime("%m/%d"),
            trade['buy_time'].strftime("%H:%M"),
            trade['stock_name'],
            f"{trade['buy_price']:,.0f}",
            f"{trade['sell_price']:,.0f}",
            f"{trade['profit_pct']:+.2f}%",
            f"{trade['hold_minutes']:.0f}분",
            result_emoji,
            style=result_style
        )

    console.print(trades_table)
    console.print()

    # 5. 시간대별 패턴
    time_stats = analyze_time_patterns(completed_trades)

    time_table = Table(title="⏰ 시간대별 패턴", box=box.ROUNDED)
    time_table.add_column("시간대", style="cyan")
    time_table.add_column("총 거래", justify="right")
    time_table.add_column("승/패", justify="center")
    time_table.add_column("승률", justify="right")
    time_table.add_column("평균 수익", justify="right")

    for time_slot in sorted(time_stats.keys()):
        stats = time_stats[time_slot]
        total = stats['wins'] + stats['losses']
        wr = (stats['wins'] / total * 100) if total > 0 else 0
        avg_profit = stats['total_profit'] / total if total > 0 else 0

        style = "green" if wr >= 50 else "red"

        time_table.add_row(
            time_slot,
            str(total),
            f"{stats['wins']}/{stats['losses']}",
            f"{wr:.0f}%",
            f"{avg_profit:+.2f}%",
            style=style
        )

    console.print(time_table)
    console.print()

    # 6. 실패 패턴 분석
    failure_patterns = analyze_failures(completed_trades)

    console.print("[bold red]🔍 실패 거래 분석[/bold red]\n")

    console.print(f"[red]조기 손절 (30분 이내): {len(failure_patterns['early_cut'])}건[/red]")
    for trade in failure_patterns['early_cut']:
        console.print(f"  • {trade['stock_name']}: {trade['profit_pct']:+.2f}% ({trade['hold_minutes']:.0f}분)")

    console.print(f"\n[red]장시간 보유 후 손절 (120분 이상): {len(failure_patterns['late_hold'])}건[/red]")
    for trade in failure_patterns['late_hold']:
        console.print(f"  • {trade['stock_name']}: {trade['profit_pct']:+.2f}% ({trade['hold_minutes']:.0f}분)")

    console.print(f"\n[red]큰 손실 (-2% 이상): {len(failure_patterns['big_loss'])}건[/red]")
    for trade in failure_patterns['big_loss']:
        console.print(f"  • {trade['stock_name']}: {trade['profit_pct']:+.2f}%")

    # 7. ML 개선 포인트
    console.print("\n" + "=" * 80, style="bold yellow")
    console.print("🎯 ML 개선 포인트", style="bold yellow")
    console.print("=" * 80 + "\n", style="bold yellow")

    improvements = []

    # 승률 분석
    if win_rate < 50:
        improvements.append({
            'priority': 'HIGH',
            'issue': f'낮은 승률 ({win_rate:.1f}%)',
            'recommendation': 'L3 신뢰도 임계값 상향 조정 (0.6 → 0.65)',
            'ml_param': 'confidence_threshold'
        })

    # 손익비 분석
    rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    if rr_ratio < 1.5:
        improvements.append({
            'priority': 'HIGH',
            'issue': f'낮은 손익비 ({rr_ratio:.2f})',
            'recommendation': '익절 목표 상향 (부분 청산 비율 조정)',
            'ml_param': 'partial_exit_tiers'
        })

    # 시간대 분석
    bad_time_slots = [slot for slot, stats in time_stats.items()
                      if (stats['wins'] + stats['losses']) > 0 and
                      (stats['wins'] / (stats['wins'] + stats['losses'])) < 0.3]
    if bad_time_slots:
        improvements.append({
            'priority': 'MEDIUM',
            'issue': f'특정 시간대 저승률: {", ".join(bad_time_slots)}',
            'recommendation': '해당 시간대 진입 가중치 감소 또는 차단',
            'ml_param': 'time_weight'
        })

    # 조기 손절 분석
    if len(failure_patterns['early_cut']) >= 2:
        improvements.append({
            'priority': 'HIGH',
            'issue': f'조기 손절 빈발 ({len(failure_patterns["early_cut"])}건)',
            'recommendation': '진입 신호 품질 개선 - L1/L2 필터 강화',
            'ml_param': 'vwap_filter, volume_filter'
        })

    # 개선 포인트 테이블
    if improvements:
        improve_table = Table(box=box.ROUNDED)
        improve_table.add_column("우선순위", style="bold")
        improve_table.add_column("문제점", style="yellow")
        improve_table.add_column("개선 방안", style="green")
        improve_table.add_column("ML 파라미터", style="cyan")

        for imp in sorted(improvements, key=lambda x: 0 if x['priority'] == 'HIGH' else 1):
            priority_style = "bold red" if imp['priority'] == 'HIGH' else "bold yellow"
            improve_table.add_row(
                imp['priority'],
                imp['issue'],
                imp['recommendation'],
                imp['ml_param'],
                style=priority_style if imp['priority'] == 'HIGH' else None
            )

        console.print(improve_table)
    else:
        console.print("[green]✅ 현재 성과가 양호합니다![/green]")

    console.print("\n" + "=" * 80 + "\n", style="bold cyan")

if __name__ == "__main__":
    main()
