#!/usr/bin/env python3
"""
오늘 거래 분석 스크립트
"""
import sqlite3
from datetime import datetime, date
from rich.console import Console
from rich.table import Table
from pathlib import Path
import json

console = Console()


def analyze_today_trades():
    """오늘 거래 분석"""
    today = date.today()
    console.print(f"\n[bold cyan]📊 {today} 거래 분석[/bold cyan]\n")

    db_path = Path("data/trading.db")

    if not db_path.exists():
        console.print("[yellow]⚠️  거래 DB가 없습니다. (database/trading.db)[/yellow]")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 오늘 거래 조회
        query = """
        SELECT
            id,
            stock_code,
            stock_name,
            entry_time,
            entry_price,
            exit_time,
            exit_price,
            quantity,
            profit_loss,
            profit_loss_pct,
            exit_reason,
            position_hold_minutes
        FROM trades
        WHERE DATE(entry_time) = ? OR DATE(exit_time) = ?
        ORDER BY entry_time DESC
        """

        cursor.execute(query, (today.isoformat(), today.isoformat()))
        trades = cursor.fetchall()

        if not trades:
            console.print("[yellow]📭 오늘 거래 기록이 없습니다.[/yellow]")
            console.print("[dim]   - 프로그램이 실행되지 않았거나[/dim]")
            console.print("[dim]   - 매매 신호가 발생하지 않았습니다.[/dim]")

            # 최근 거래 확인
            cursor.execute("""
                SELECT DATE(entry_time) as trade_date, COUNT(*) as count
                FROM trades
                GROUP BY DATE(entry_time)
                ORDER BY trade_date DESC
                LIMIT 5
            """)
            recent = cursor.fetchall()

            if recent:
                console.print("\n[cyan]최근 거래 일자:[/cyan]")
                for trade_date, count in recent:
                    console.print(f"  {trade_date}: {count}건")

            conn.close()
            return

        # 거래 테이블
        table = Table(title=f"오늘의 거래 내역 ({len(trades)}건)")
        table.add_column("번호", style="cyan", justify="right")
        table.add_column("종목", style="yellow")
        table.add_column("진입시간", style="dim")
        table.add_column("진입가", justify="right")
        table.add_column("청산시간", style="dim")
        table.add_column("청산가", justify="right")
        table.add_column("수량", justify="right")
        table.add_column("손익", justify="right")
        table.add_column("손익률", justify="right")
        table.add_column("보유시간", justify="right", style="dim")
        table.add_column("청산사유")

        total_profit = 0
        win_count = 0
        loss_count = 0

        for idx, trade in enumerate(trades, 1):
            (trade_id, stock_code, stock_name, entry_time, entry_price,
             exit_time, exit_price, quantity, profit_loss, profit_loss_pct,
             exit_reason, hold_minutes) = trade

            # 손익 계산
            if profit_loss:
                total_profit += profit_loss
                if profit_loss > 0:
                    win_count += 1
                else:
                    loss_count += 1

            # 손익 색상
            if profit_loss and profit_loss > 0:
                profit_color = "green"
                profit_str = f"+{profit_loss:,.0f}원"
                pct_str = f"+{profit_loss_pct:.2f}%"
            elif profit_loss and profit_loss < 0:
                profit_color = "red"
                profit_str = f"{profit_loss:,.0f}원"
                pct_str = f"{profit_loss_pct:.2f}%"
            else:
                profit_color = "dim"
                profit_str = "진행중"
                pct_str = "-"

            # 보유시간
            if hold_minutes:
                hours = int(hold_minutes // 60)
                mins = int(hold_minutes % 60)
                hold_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
            else:
                hold_str = "-"

            # 청산사유 단축
            reason_short = exit_reason[:20] + "..." if exit_reason and len(exit_reason) > 20 else (exit_reason or "-")

            table.add_row(
                str(idx),
                f"{stock_name}\n({stock_code})",
                entry_time.split()[1][:5] if entry_time else "-",
                f"{entry_price:,.0f}" if entry_price else "-",
                exit_time.split()[1][:5] if exit_time else "-",
                f"{exit_price:,.0f}" if exit_price else "-",
                str(quantity) if quantity else "-",
                f"[{profit_color}]{profit_str}[/{profit_color}]",
                f"[{profit_color}]{pct_str}[/{profit_color}]",
                hold_str,
                reason_short
            )

        console.print(table)
        console.print()

        # 통계
        console.print("[bold cyan]📈 거래 통계[/bold cyan]")
        console.print(f"  총 거래: {len(trades)}건")
        console.print(f"  승리: [green]{win_count}건[/green]")
        console.print(f"  손실: [red]{loss_count}건[/red]")

        if win_count + loss_count > 0:
            win_rate = (win_count / (win_count + loss_count)) * 100
            console.print(f"  승률: {win_rate:.1f}%")

        if total_profit != 0:
            profit_color = "green" if total_profit > 0 else "red"
            console.print(f"  총 손익: [{profit_color}]{total_profit:+,.0f}원[/{profit_color}]")

        conn.close()

    except Exception as e:
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()


def check_risk_log():
    """리스크 로그 확인"""
    console.print(f"\n[bold cyan]🛡️  리스크 이벤트[/bold cyan]\n")

    risk_log_path = Path("data/risk_log.json")

    if not risk_log_path.exists():
        console.print("[dim]리스크 로그 없음[/dim]")
        return

    try:
        with open(risk_log_path, 'r', encoding='utf-8') as f:
            risk_log = json.load(f)

        today = date.today().isoformat()
        today_events = [e for e in risk_log if e.get('timestamp', '').startswith(today)]

        if not today_events:
            console.print("[dim]오늘 리스크 이벤트 없음[/dim]")
            return

        table = Table(title=f"오늘의 리스크 이벤트 ({len(today_events)}건)")
        table.add_column("시간", style="dim")
        table.add_column("종목")
        table.add_column("이벤트")
        table.add_column("사유")

        for event in today_events[-20:]:  # 최근 20개만
            timestamp = event.get('timestamp', '')
            time_str = timestamp.split('T')[1][:8] if 'T' in timestamp else timestamp
            stock_code = event.get('stock_code', '')
            stock_name = event.get('stock_name', stock_code)
            event_type = event.get('event_type', '')
            reason = event.get('reason', '')

            table.add_row(
                time_str,
                f"{stock_name}\n({stock_code})",
                event_type,
                reason[:40]
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]❌ 리스크 로그 읽기 오류: {e}[/red]")


def check_signal_log():
    """시그널 로그 확인"""
    console.print(f"\n[bold cyan]🎯 시그널 로그 (최근)[/bold cyan]\n")

    log_path = Path("logs/signal_orchestrator.log")

    if not log_path.exists():
        console.print("[dim]시그널 로그 없음[/dim]")
        return

    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        today = date.today().isoformat()
        today_lines = [line for line in lines if today in line]

        if not today_lines:
            console.print("[dim]오늘 시그널 로그 없음[/dim]")
            # 최근 로그 표시
            if lines:
                console.print("\n[dim]최근 로그 (5줄):[/dim]")
                for line in lines[-5:]:
                    console.print(f"[dim]{line.strip()}[/dim]")
            return

        # ACCEPT/REJECT 카운트
        accept_count = len([l for l in today_lines if '✅ ACCEPT' in l])
        reject_count = len([l for l in today_lines if '❌ REJECT' in l])

        console.print(f"  총 시그널: {len(today_lines)}개")
        console.print(f"  승인: [green]{accept_count}개[/green]")
        console.print(f"  거부: [red]{reject_count}개[/red]")

        # 최근 10개 시그널 표시
        console.print("\n[dim]최근 시그널 (10개):[/dim]")
        signal_lines = [l for l in today_lines if ('ACCEPT' in l or 'REJECT' in l)]
        for line in signal_lines[-10:]:
            if 'ACCEPT' in line:
                console.print(f"[green]{line.strip()}[/green]")
            else:
                console.print(f"[red]{line.strip()}[/red]")

    except Exception as e:
        console.print(f"[red]❌ 시그널 로그 읽기 오류: {e}[/red]")


if __name__ == "__main__":
    console.print("[bold green]" + "="*60 + "[/bold green]")
    console.print("[bold green]" + "오늘의 거래 분석".center(60) + "[/bold green]")
    console.print("[bold green]" + "="*60 + "[/bold green]")

    analyze_today_trades()
    check_risk_log()
    check_signal_log()

    console.print()
