"""
거래 데이터베이스 테스트
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


def test_database():
    """데이터베이스 기능 테스트"""

    # 테스트용 DB 생성
    db = TradingDatabase("data/test_trading.db")

    console.print("[bold cyan]=" * 60)
    console.print("[bold cyan]거래 데이터베이스 테스트")
    console.print("[bold cyan]=" * 60)
    console.print()

    # ==================== 1. 2차 필터링 점수 저장 ====================
    console.print("[yellow]1. 2차 필터링 점수 저장 테스트[/yellow]")

    score_data = {
        'stock_code': '005930',
        'stock_name': '삼성전자',
        'validation_time': datetime.now().isoformat(),
        'vwap_win_rate': 66.7,
        'vwap_avg_profit': 1.25,
        'vwap_trade_count': 9,
        'vwap_profit_factor': 2.15,
        'vwap_max_profit': 5.2,
        'vwap_max_loss': -2.1,
        'news_sentiment_score': 75.5,
        'news_impact_type': 'mid',
        'news_keywords': ['반도체', '실적', '호조'],
        'news_titles': ['삼성전자 3분기 실적 호조', '반도체 업황 개선'],
        'news_count': 12,
        'total_score': 70.2,
        'weight_vwap': 0.7,
        'weight_news': 0.3,
        'is_passed': 1
    }

    score_id = db.insert_validation_score(score_data)
    console.print(f"✓ 점수 저장 완료 (ID: {score_id})", style="green")
    console.print()

    # ==================== 2. 필터링 이력 저장 ====================
    console.print("[yellow]2. 필터링 이력 저장 테스트[/yellow]")

    filter_data = {
        'filter_time': datetime.now().isoformat(),
        'filter_type': '1차',
        'condition_name': 'Momentum',
        'stocks_found': 25,
        'stock_codes': ['005930', '000660', '035420'],
        'stocks_passed': 0,
        'stocks_failed': 0,
        'passed_stocks': [],
        'schedule_type': 'manual',
        'is_new_stock': 0
    }

    filter_id = db.insert_filter_history(filter_data)
    console.print(f"✓ 필터링 이력 저장 완료 (ID: {filter_id})", style="green")
    console.print()

    # ==================== 3. 시뮬레이션 결과 저장 ====================
    console.print("[yellow]3. 시뮬레이션 결과 저장 테스트[/yellow]")

    sim_data = {
        'stock_code': '005930',
        'stock_name': '삼성전자',
        'simulation_time': datetime.now().isoformat(),
        'lookback_days': 7,
        'total_trades': 9,
        'win_rate': 66.7,
        'avg_profit_rate': 1.25,
        'profit_factor': 2.15,
        'max_profit': 5.2,
        'max_loss': -2.1,
        'news_sentiment': 'positive',
        'news_impact': 'mid',
        'news_keywords': ['반도체', '실적'],
        'news_titles': ['삼성전자 3분기 실적 호조'],
        'news_score': 75.5,
        'trade_details': [
            {'entry': 70000, 'exit': 71500, 'profit': 2.14},
            {'entry': 71000, 'exit': 69500, 'profit': -2.11}
        ]
    }

    sim_id = db.insert_simulation(sim_data)
    console.print(f"✓ 시뮬레이션 결과 저장 완료 (ID: {sim_id})", style="green")
    console.print()

    # ==================== 4. 거래 이력 저장 (매수) ====================
    console.print("[yellow]4. 거래 이력 저장 테스트 (매수)[/yellow]")

    buy_trade = {
        'stock_code': '005930',
        'stock_name': '삼성전자',
        'trade_type': 'BUY',
        'trade_time': datetime.now().isoformat(),
        'price': 70000,
        'quantity': 10,
        'amount': 700000,
        'condition_name': 'Momentum',
        'strategy_config': 'hybrid',
        'entry_reason': 'VWAP 상향 돌파',
        'vwap_validation_score': 70.2,
        'sim_win_rate': 66.7,
        'sim_avg_profit': 1.25,
        'sim_trade_count': 9,
        'sim_profit_factor': 2.15,
        'news_sentiment': 'positive',
        'news_impact': 'mid',
        'news_keywords': ['반도체', '실적', '호조'],
        'news_titles': ['삼성전자 3분기 실적 호조', '반도체 업황 개선']
    }

    trade_id = db.insert_trade(buy_trade)
    console.print(f"✓ 매수 거래 저장 완료 (ID: {trade_id})", style="green")
    console.print()

    # ==================== 5. 거래 이력 업데이트 (매도) ====================
    console.print("[yellow]5. 거래 이력 업데이트 테스트 (매도)[/yellow]")

    exit_data = {
        'exit_reason': '목표가 도달 (1차 익절)',
        'realized_profit': 15000,
        'profit_rate': 2.14,
        'holding_duration': 3600  # 1시간
    }

    db.update_trade_exit(trade_id, exit_data)
    console.print(f"✓ 매도 정보 업데이트 완료", style="green")
    console.print()

    # ==================== 6. 데이터 조회 ====================
    console.print("[yellow]6. 데이터 조회 테스트[/yellow]")
    console.print()

    # 6-1. 2차 필터링 점수 조회
    console.print("[cyan]6-1. 2차 필터링 점수 조회[/cyan]")
    scores = db.get_validation_scores(is_passed=True, limit=10)

    table = Table(title="2차 필터링 점수", box=box.ROUNDED)
    table.add_column("종목", style="yellow")
    table.add_column("VWAP 승률", justify="right")
    table.add_column("VWAP 수익", justify="right")
    table.add_column("뉴스 점수", justify="right")
    table.add_column("뉴스 영향", justify="center")
    table.add_column("종합 점수", justify="right", style="bold green")

    for score in scores:
        table.add_row(
            f"{score['stock_name']} ({score['stock_code']})",
            f"{score['vwap_win_rate']:.1f}%",
            f"{score['vwap_avg_profit']:+.2f}%",
            f"{score['news_sentiment_score']:.1f}",
            score['news_impact_type'],
            f"{score['total_score']:.1f}"
        )

    console.print(table)
    console.print()

    # 6-2. 거래 이력 조회
    console.print("[cyan]6-2. 거래 이력 조회[/cyan]")
    trades = db.get_trades(stock_code='005930')

    table2 = Table(title="거래 이력", box=box.ROUNDED)
    table2.add_column("시각", style="dim")
    table2.add_column("종목")
    table2.add_column("구분", justify="center")
    table2.add_column("가격", justify="right")
    table2.add_column("수량", justify="right")
    table2.add_column("수익률", justify="right")

    for trade in trades:
        trade_time = datetime.fromisoformat(trade['trade_time']).strftime('%m-%d %H:%M')
        profit_style = "green" if (trade['profit_rate'] or 0) > 0 else "red"

        table2.add_row(
            trade_time,
            trade['stock_name'],
            trade['trade_type'],
            f"{trade['price']:,}",
            str(trade['quantity']),
            f"{trade['profit_rate'] or 0:+.2f}%" if trade['profit_rate'] else "-",
            style=profit_style if trade['profit_rate'] else ""
        )

    console.print(table2)
    console.print()

    # ==================== 7. 통계 조회 ====================
    console.print("[yellow]7. 거래 통계 조회 테스트[/yellow]")

    stats = db.get_trade_statistics()

    table3 = Table(title="거래 통계", box=box.DOUBLE)
    table3.add_column("항목", style="cyan")
    table3.add_column("값", justify="right", style="yellow")

    table3.add_row("총 거래", f"{stats.get('total_trades', 0)}회")
    table3.add_row("총 매수", f"{stats.get('total_buys', 0)}회")
    table3.add_row("총 매도", f"{stats.get('total_sells', 0)}회")
    table3.add_row("승리 거래", f"{stats.get('winning_trades', 0)}회")
    table3.add_row("승률", f"{stats.get('win_rate', 0):.1f}%")
    table3.add_row("평균 수익률", f"{stats.get('avg_profit_rate', 0):+.2f}%")
    table3.add_row("총 수익", f"{stats.get('total_profit', 0):+,.0f}원")
    table3.add_row("최대 수익률", f"{stats.get('max_profit_rate', 0):+.2f}%")
    table3.add_row("최대 손실률", f"{stats.get('min_profit_rate', 0):+.2f}%")

    console.print(table3)
    console.print()

    console.print("[bold green]✓ 모든 데이터베이스 테스트 완료![/bold green]")
    console.print()
    console.print(f"[dim]데이터베이스 위치: {db.db_path}[/dim]")


if __name__ == "__main__":
    test_database()
