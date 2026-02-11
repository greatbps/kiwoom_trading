#!/usr/bin/env python3
"""
통합 트레이딩 시스템
==================

키움 (단기) + 한투 (중기 국내/해외) 전체 통합 대시보드

계좌 구성:
- 키움 5765-7162: 단기 스캘핑/자동매매
- 한투 64556264-01 국내: 중기 투자 (ETF)
- 한투 64556264-01 해외: 미국주식 중기 투자
"""

import os
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# 프로젝트 루트
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 환경변수 로드
load_dotenv(project_root / '.env')

# 브로커 추상화
from brokers import get_broker, BrokerType, Market

console = Console()
logger = logging.getLogger(__name__)


class IntegratedMonitor:
    """통합 계좌 모니터링 (브로커 추상화 사용)"""

    def __init__(self):
        # 브로커들
        self.kiwoom = get_broker(BrokerType.KIWOOM)
        self.kis_domestic = get_broker(BrokerType.KIS_DOMESTIC)
        self.kis_overseas = get_broker(BrokerType.KIS_OVERSEAS)

        # 데이터
        self.kiwoom_positions = []
        self.domestic_positions = []
        self.overseas_positions = []

        # 중기 평가 결과
        self.domestic_results = []
        self.overseas_results = []

        # 통계
        self.stats = {
            'kiwoom_eval': 0,
            'kiwoom_profit_pct': 0,
            'domestic_eval': 0,
            'domestic_profit_pct': 0,
            'overseas_eval': 0,
            'overseas_profit_pct': 0,
        }

        self.last_update = None

    def initialize(self) -> bool:
        """브로커 초기화"""
        console.print("[dim]브로커 연결 중...[/dim]")

        results = []

        # 키움
        if self.kiwoom.initialize():
            console.print("  ✅ 키움 연결")
            results.append(True)
        else:
            console.print("  ⚠️ 키움 연결 실패")
            results.append(False)

        # 한투 국내
        if self.kis_domestic.initialize():
            console.print("  ✅ 한투 국내 연결")
            results.append(True)
        else:
            console.print("  ❌ 한투 국내 연결 실패")
            results.append(False)

        # 한투 해외
        if self.kis_overseas.initialize():
            console.print("  ✅ 한투 해외 연결")
            results.append(True)
        else:
            console.print("  ❌ 한투 해외 연결 실패")
            results.append(False)

        return any(results)

    def fetch_all(self):
        """전체 데이터 조회"""
        # 키움
        try:
            self.kiwoom_positions = self.kiwoom.get_positions()
            self._calc_stats('kiwoom', self.kiwoom_positions)
        except Exception as e:
            logger.error(f"키움 조회 실패: {e}")

        # 한투 국내
        try:
            self.domestic_positions = self.kis_domestic.get_positions()
            self._calc_stats('domestic', self.domestic_positions)
            self._evaluate_midterm_domestic()
        except Exception as e:
            logger.error(f"한투 국내 조회 실패: {e}")

        # 한투 해외
        try:
            self.overseas_positions = self.kis_overseas.get_positions()
            self._calc_stats('overseas', self.overseas_positions)
            self._evaluate_midterm_overseas()
        except Exception as e:
            logger.error(f"한투 해외 조회 실패: {e}")

        self.last_update = datetime.now()

    def _calc_stats(self, key: str, positions):
        """통계 계산"""
        if not positions:
            self.stats[f'{key}_eval'] = 0
            self.stats[f'{key}_profit_pct'] = 0
            return

        total_eval = sum(p.eval_amount for p in positions)
        total_invested = sum(p.avg_price * p.quantity for p in positions)

        self.stats[f'{key}_eval'] = total_eval
        self.stats[f'{key}_profit_pct'] = (
            (total_eval - total_invested) / total_invested * 100
            if total_invested > 0 else 0
        )

    def _evaluate_midterm_domestic(self):
        """국내 중기 평가"""
        from trading.mid_term_engine import (
            Action, PositionGroup, Position, MarketData,
            evaluate_position, STOCK_GROUP_MAP
        )

        self.domestic_results = []
        total_eval = sum(p.eval_amount for p in self.domestic_positions)

        for bp in self.domestic_positions:
            weight = (bp.eval_amount / total_eval * 100) if total_eval > 0 else 0

            pos = Position(
                stock_code=bp.symbol,
                stock_name=bp.name,
                quantity=bp.quantity,
                avg_price=bp.avg_price,
                current_price=bp.current_price,
                profit_pct=bp.profit_pct,
                eval_amount=bp.eval_amount,
                group=STOCK_GROUP_MAP.get(bp.symbol, PositionGroup.B_TREND),
                weight_pct=weight
            )

            result = evaluate_position(pos, MarketData())
            self.domestic_results.append(result)

    def _evaluate_midterm_overseas(self):
        """해외 중기 평가"""
        from trading.mid_term_engine import (
            Action, PositionGroup, Position, MarketData,
            evaluate_position, STOCK_GROUP_MAP
        )

        self.overseas_results = []
        total_eval = sum(p.eval_amount for p in self.overseas_positions)

        for bp in self.overseas_positions:
            weight = (bp.eval_amount / total_eval * 100) if total_eval > 0 else 0

            pos = Position(
                stock_code=bp.symbol,
                stock_name=bp.name,
                quantity=bp.quantity,
                avg_price=bp.avg_price,
                current_price=bp.current_price,
                profit_pct=bp.profit_pct,
                eval_amount=bp.eval_amount,
                group=STOCK_GROUP_MAP.get(bp.symbol, PositionGroup.B_TREND),
                weight_pct=weight
            )

            result = evaluate_position(pos, MarketData())
            self.overseas_results.append(result)

    def get_action_style(self, action_value: str) -> tuple:
        """Action 스타일 반환"""
        styles = {
            'STOP_LOSS': ('🔴', 'red bold'),
            'TRAILING_STOP': ('🟢', 'green'),
            'REDUCE': ('🟡', 'yellow'),
            'ADD_ON_PULLBACK': ('🔵', 'cyan'),
            'HOLD': ('⚪', 'white'),
        }
        return styles.get(action_value, ('⚪', 'white'))

    def display(self):
        """대시보드 표시"""
        console.clear()

        # 헤더
        update_str = self.last_update.strftime('%Y-%m-%d %H:%M:%S') if self.last_update else '-'

        # 시장 상태
        _, kiwoom_status = self.kiwoom.is_market_open()
        _, us_status = self.kis_overseas.is_market_open()

        console.print(Panel(
            f"[bold]📊 통합 트레이딩 대시보드[/bold]\n\n"
            f"국내: {kiwoom_status} | 미국: {us_status}\n"
            f"[dim]업데이트: {update_str}[/dim]",
            border_style="blue"
        ))

        # ═══════════════════════════════════════════════════════════
        # 키움 단기
        # ═══════════════════════════════════════════════════════════
        profit_style = "green" if self.stats['kiwoom_profit_pct'] >= 0 else "red"
        console.print(f"\n[bold cyan]━━━ 📈 키움 단기 (5765-7162) ━━━[/bold cyan]")

        if self.kiwoom_positions:
            table = Table(box=None, show_header=False, padding=(0, 1))
            table.add_column("종목", width=14)
            table.add_column("수량", justify="right", width=6)
            table.add_column("수익률", justify="right", width=10)

            for p in self.kiwoom_positions[:5]:
                style = "green" if p.profit_pct >= 0 else "red"
                table.add_row(
                    p.name[:12],
                    f"{p.quantity:,}",
                    f"[{style}]{p.profit_pct:+.1f}%[/{style}]"
                )
            console.print(table)
        else:
            console.print("[dim]  보유 없음[/dim]")

        console.print(f"  [bold]평가: {self.stats['kiwoom_eval']:,.0f}원[/bold] [{profit_style}]{self.stats['kiwoom_profit_pct']:+.1f}%[/{profit_style}]")

        # ═══════════════════════════════════════════════════════════
        # 한투 국내 중기
        # ═══════════════════════════════════════════════════════════
        profit_style = "green" if self.stats['domestic_profit_pct'] >= 0 else "red"
        console.print(f"\n[bold yellow]━━━ 📊 한투 중기 국내 ━━━[/bold yellow]")

        if self.domestic_positions:
            table = Table(box=None, show_header=False, padding=(0, 1))
            table.add_column("종목", width=16)
            table.add_column("수익률", justify="right", width=8)
            table.add_column("Action", width=14)

            for i, p in enumerate(self.domestic_positions):
                style = "green" if p.profit_pct >= 0 else "red"

                action = "HOLD"
                if i < len(self.domestic_results):
                    action = self.domestic_results[i].action.value

                icon, action_style = self.get_action_style(action)

                table.add_row(
                    p.name[:14],
                    f"[{style}]{p.profit_pct:+.1f}%[/{style}]",
                    f"{icon} [{action_style}]{action}[/{action_style}]"
                )
            console.print(table)
        else:
            console.print("[dim]  보유 없음[/dim]")

        console.print(f"  [bold]평가: {self.stats['domestic_eval']:,.0f}원[/bold] [{profit_style}]{self.stats['domestic_profit_pct']:+.1f}%[/{profit_style}]")

        # ═══════════════════════════════════════════════════════════
        # 한투 해외 중기
        # ═══════════════════════════════════════════════════════════
        profit_style = "green" if self.stats['overseas_profit_pct'] >= 0 else "red"
        console.print(f"\n[bold magenta]━━━ 🌍 한투 중기 해외 ━━━[/bold magenta]")

        if self.overseas_positions:
            table = Table(box=None, show_header=False, padding=(0, 1))
            table.add_column("종목", width=8)
            table.add_column("현재가", justify="right", width=10)
            table.add_column("수익률", justify="right", width=8)
            table.add_column("Action", width=14)

            for i, p in enumerate(self.overseas_positions):
                style = "green" if p.profit_pct >= 0 else "red"

                action = "HOLD"
                if i < len(self.overseas_results):
                    action = self.overseas_results[i].action.value

                icon, action_style = self.get_action_style(action)

                table.add_row(
                    p.symbol,
                    f"${p.current_price:.2f}",
                    f"[{style}]{p.profit_pct:+.1f}%[/{style}]",
                    f"{icon} [{action_style}]{action}[/{action_style}]"
                )
            console.print(table)
        else:
            console.print("[dim]  보유 없음[/dim]")

        console.print(f"  [bold]평가: ${self.stats['overseas_eval']:,.2f}[/bold] [{profit_style}]{self.stats['overseas_profit_pct']:+.1f}%[/{profit_style}]")

        # ═══════════════════════════════════════════════════════════
        # 요약 / 경고
        # ═══════════════════════════════════════════════════════════
        console.print(f"\n{'═' * 50}")

        # 총계 (환율 1450원 가정)
        exchange_rate = 1450
        total_krw = (
            self.stats['kiwoom_eval'] +
            self.stats['domestic_eval'] +
            self.stats['overseas_eval'] * exchange_rate
        )
        console.print(f"[bold]💰 총 자산: {total_krw:,.0f}원[/bold]")

        # STOP_LOSS 경고
        stop_loss_items = []

        for r in self.domestic_results:
            if r.action.value == 'STOP_LOSS':
                stop_loss_items.append(f"{r.position.stock_name[:10]} ({r.position.profit_pct:+.1f}%)")

        for r in self.overseas_results:
            if r.action.value == 'STOP_LOSS':
                stop_loss_items.append(f"{r.position.stock_code} ({r.position.profit_pct:+.1f}%)")

        if stop_loss_items:
            console.print(f"\n[bold red]🚨 STOP_LOSS 대상 ({len(stop_loss_items)}건)[/bold red]")
            for item in stop_loss_items:
                console.print(f"   🔴 {item}")
            console.print(f"\n   [dim]실행: ./run.sh stoploss[/dim]")

    def run(self, interval: int = 60):
        """모니터링 루프"""
        console.print("\n[cyan]데이터 조회 중...[/cyan]")
        self.fetch_all()
        self.display()

        try:
            while True:
                console.print(f"\n[dim]다음 갱신: {interval}초 (Ctrl+C 종료)[/dim]")
                time.sleep(interval)
                self.fetch_all()
                self.display()

        except KeyboardInterrupt:
            console.print("\n[yellow]모니터링 종료[/yellow]")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='통합 트레이딩 시스템')
    parser.add_argument('--interval', '-i', type=int, default=60, help='갱신 주기 (초)')
    parser.add_argument('--once', '-1', action='store_true', help='1회만 실행')

    args = parser.parse_args()

    monitor = IntegratedMonitor()

    if not monitor.initialize():
        console.print("[red]브로커 연결 실패[/red]")
        return

    if args.once:
        monitor.fetch_all()
        monitor.display()
    else:
        monitor.run(interval=args.interval)


if __name__ == "__main__":
    main()
