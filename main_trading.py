#!/usr/bin/env python3
"""
통합 트레이딩 시스템
==================

1. 키움 (단기)
2. 한투 국내 (중기)
3. 한투 해외 (중기)

사용법:
  ./run.sh          대시보드
  ./run.sh sl       STOP_LOSS 실행
  ./run.sh reset    기준가 리셋 (현재가로)
"""

import sys
import json
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from brokers import get_broker, BrokerType, Market, OrderSide

console = Console()

# 기준가 파일
BASELINE_FILE = project_root / 'trading' / 'baseline_prices.json'
STOP_LOSS_PCT = -12.0  # -12% 손절


def load_baseline():
    """기준가 로드"""
    if BASELINE_FILE.exists():
        with open(BASELINE_FILE, 'r') as f:
            return json.load(f)
    return {'domestic': {}, 'overseas': {}}


def save_baseline(data):
    """기준가 저장"""
    data['updated'] = datetime.now().isoformat()
    with open(BASELINE_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class TradingSystem:
    """통합 트레이딩 시스템"""

    def __init__(self):
        self.kiwoom = get_broker(BrokerType.KIWOOM)
        self.kis_domestic = get_broker(BrokerType.KIS_DOMESTIC)
        self.kis_overseas = get_broker(BrokerType.KIS_OVERSEAS)

        self.positions = {
            'kiwoom': [],
            'domestic': [],
            'overseas': []
        }

        self.baseline = load_baseline()

    def initialize(self):
        """브로커 초기화"""
        console.print("[dim]브로커 연결 중...[/dim]")

        self.kiwoom.initialize()
        self.kis_domestic.initialize()
        self.kis_overseas.initialize()

        console.print("[green]✅ 연결 완료[/green]\n")

    def fetch_all(self):
        """전체 포지션 조회"""
        self.positions['kiwoom'] = self.kiwoom.get_positions()
        self.positions['domestic'] = self.kis_domestic.get_positions()
        self.positions['overseas'] = self.kis_overseas.get_positions()

    def display(self):
        """대시보드 표시"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _, kr_status = self.kiwoom.is_market_open()
        _, us_status = self.kis_overseas.is_market_open()

        console.print(Panel(
            f"[bold]📊 통합 트레이딩 시스템[/bold]\n\n"
            f"국내: {kr_status} | 미국: {us_status}\n"
            f"[dim]{now}[/dim]",
            border_style="blue"
        ))

        # 키움
        console.print("\n[bold cyan]━━━ 1. 키움 (단기) ━━━[/bold cyan]")
        self._display_positions(self.positions['kiwoom'], "KRW", "kiwoom")

        # 한투 국내
        console.print("\n[bold yellow]━━━ 2. 한투 국내 (중기) ━━━[/bold yellow]")
        self._display_positions(self.positions['domestic'], "KRW", "domestic", show_action=True)

        # 한투 해외
        console.print("\n[bold magenta]━━━ 3. 한투 해외 (중기) ━━━[/bold magenta]")
        self._display_positions(self.positions['overseas'], "USD", "overseas", show_action=True)

        # STOP_LOSS 요약
        self._display_stop_loss_summary()

    def _get_baseline_pct(self, symbol: str, current_price: float, market: str) -> float:
        """기준가 대비 수익률 계산"""
        baseline_prices = self.baseline.get(market, {})
        baseline = baseline_prices.get(symbol, current_price)
        if baseline <= 0:
            return 0.0
        return ((current_price - baseline) / baseline) * 100

    def _is_stop_loss_by_baseline(self, symbol: str, current_price: float, market: str) -> bool:
        """손절 대상 여부 (기준가 대비 -12%) - 레거시"""
        pct = self._get_baseline_pct(symbol, current_price, market)
        return pct <= STOP_LOSS_PCT

    def _is_stop_loss(self, position) -> bool:
        """손절 대상 여부 (평균매수가 대비 -12%) - 실제 손실 기준"""
        return position.profit_pct <= STOP_LOSS_PCT

    def _display_positions(self, positions, currency, market_key, show_action=False):
        """포지션 테이블 표시"""
        if not positions:
            console.print("  [dim]보유 없음[/dim]")
            return

        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("종목", width=12)
        table.add_column("수량", justify="right", width=6)
        table.add_column("현재가", justify="right", width=10)
        table.add_column("수익률", justify="right", width=10)

        if show_action:
            table.add_column("상태", width=12)

        total_eval = sum(p.eval_amount for p in positions)

        for p in positions:
            # 평균매수가 기준 수익률 사용 (브로커에서 계산된 값)
            profit_pct = p.profit_pct
            style = "green" if profit_pct >= 0 else "red"

            if currency == "USD":
                price_str = f"${p.current_price:.2f}"
            else:
                price_str = f"{p.current_price:,.0f}"

            row = [
                p.name[:10] if len(p.name) > 10 else p.name,
                f"{p.quantity:,}",
                price_str,
                f"[{style}]{profit_pct:+.1f}%[/{style}]"
            ]

            if show_action:
                if self._is_stop_loss(p):
                    row.append(f"[red bold]🔴 STOP_LOSS[/red bold]")
                else:
                    row.append(f"[dim]OK[/dim]")

            table.add_row(*row)

        console.print(table)

        if currency == "USD":
            console.print(f"  [bold]평가: ${total_eval:,.2f}[/bold]")
        else:
            console.print(f"  [bold]평가: {total_eval:,.0f}원[/bold]")

    def _display_stop_loss_summary(self):
        """STOP_LOSS 요약 (평균매수가 대비)"""
        stop_loss_items = []

        for p in self.positions['domestic']:
            if self._is_stop_loss(p):
                stop_loss_items.append(('domestic', p, p.profit_pct))

        for p in self.positions['overseas']:
            if self._is_stop_loss(p):
                stop_loss_items.append(('overseas', p, p.profit_pct))

        if stop_loss_items:
            console.print(f"\n[bold red]🚨 STOP_LOSS 대상 ({len(stop_loss_items)}건)[/bold red]")
            for market, p, pct in stop_loss_items:
                market_name = "국내" if market == "domestic" else "해외"
                console.print(f"   🔴 [{market_name}] {p.symbol} 기준가대비 {pct:+.1f}%")
            console.print(f"\n   [dim]실행: ./run.sh sl[/dim]")
        else:
            console.print(f"\n[green]✅ 손절 대상 없음 (평균매수가 대비 -{abs(STOP_LOSS_PCT):.0f}% 이상 하락 시 손절)[/green]")

    def execute_stop_loss(self):
        """STOP_LOSS 실행 (평균매수가 대비)"""
        console.print("\n[bold red]═══ STOP_LOSS 실행 ═══[/bold red]\n")

        executed = []

        # 국내
        for p in self.positions['domestic']:
            if self._is_stop_loss(p):
                console.print(f"[국내] {p.name} ({p.symbol}) 평균매수가대비 {p.profit_pct:+.1f}%")
                console.print(f"   → 시장가 매도 {p.quantity}주...")

                result = self.kis_domestic.place_market_sell(p.symbol, p.quantity)
                if result.success:
                    console.print(f"   [green]✅ 주문 성공 (주문번호: {result.order_no})[/green]")
                    executed.append(('domestic', p.symbol))
                else:
                    console.print(f"   [red]❌ 실패: {result.message}[/red]")

        # 해외
        for p in self.positions['overseas']:
            if self._is_stop_loss(p):
                console.print(f"\n[해외] {p.symbol} ${p.current_price:.2f} 평균매수가대비 {p.profit_pct:+.1f}%")
                console.print(f"   → 현재가 매도 {p.quantity}주...")

                result = self.kis_overseas.place_market_sell(p.symbol, p.quantity)
                if result.success:
                    console.print(f"   [green]✅ 주문 성공 (주문번호: {result.order_no})[/green]")
                    executed.append(('overseas', p.symbol))
                else:
                    console.print(f"   [red]❌ 실패: {result.message}[/red]")

        if not executed:
            console.print("[dim]손절 대상 없음[/dim]")

        console.print(f"\n[bold]실행 완료: {len(executed)}건[/bold]")
        return executed

    def reset_baseline(self):
        """기준가 리셋 (현재가로)"""
        console.print("\n[bold]기준가 리셋[/bold]\n")

        self.baseline = {'domestic': {}, 'overseas': {}}

        for p in self.positions['domestic']:
            self.baseline['domestic'][p.symbol] = p.current_price
            console.print(f"  [국내] {p.symbol}: {p.current_price:,.0f}원")

        for p in self.positions['overseas']:
            self.baseline['overseas'][p.symbol] = p.current_price
            console.print(f"  [해외] {p.symbol}: ${p.current_price:.2f}")

        save_baseline(self.baseline)
        console.print(f"\n[green]✅ 저장 완료[/green]")


def main():
    system = TradingSystem()
    system.initialize()
    system.fetch_all()

    # 인자 처리
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == 'sl':
            system.display()
            system.execute_stop_loss()
            return
        elif cmd == 'reset':
            system.reset_baseline()
            return

    # 기본: 대시보드
    system.display()


if __name__ == "__main__":
    main()
