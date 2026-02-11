"""
Trend Account Monitor - 중기 계좌 보유종목 모니터링
==================================================

5202-2235 계좌의 보유 종목을 모니터링하고
Daily Squeeze 기반 청산 시그널을 생성

기능:
1. 계좌 보유 종목 조회
2. 실시간 수익률 모니터링
3. Daily Squeeze 상태 체크
4. 청산 시그널 알림
"""

import os
import logging
from datetime import datetime, time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from dotenv import load_dotenv
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel

# 환경변수 로드
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class TrendHolding:
    """중기 계좌 보유 종목"""
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: float = 0.0

    # 수익률
    profit_loss: float = 0.0
    profit_loss_pct: float = 0.0

    # Daily Squeeze 상태
    squeeze_on: bool = True
    momentum: float = 0.0
    momentum_prev: float = 0.0
    momentum_slope: float = 0.0

    # 기타 정보
    eval_amount: float = 0.0
    holding_days: int = 0
    entry_date: Optional[datetime] = None

    # 시그널
    exit_signal: str = ""
    exit_reason: str = ""

    def update_price(self, current_price: float):
        """현재가 업데이트"""
        self.current_price = current_price
        self.eval_amount = current_price * self.quantity
        self.profit_loss = (current_price - self.avg_price) * self.quantity
        self.profit_loss_pct = ((current_price - self.avg_price) / self.avg_price) * 100 if self.avg_price > 0 else 0

    def update_squeeze(self, squeeze_on: bool, momentum: float, momentum_prev: float):
        """Squeeze 상태 업데이트"""
        self.squeeze_on = squeeze_on
        self.momentum_prev = self.momentum if self.momentum != 0 else momentum_prev
        self.momentum = momentum
        self.momentum_slope = momentum - self.momentum_prev

        # 청산 시그널 체크
        self._check_exit_signal()

    def _check_exit_signal(self):
        """청산 시그널 체크 (B(Pro) 규칙)"""
        self.exit_signal = ""
        self.exit_reason = ""

        # Squeeze ON이면 무조건 HOLD
        if self.squeeze_on:
            return

        # 모멘텀 음전 + 기울기 하락 → 청산 시그널
        if self.momentum < 0 and self.momentum_slope < 0:
            self.exit_signal = "EXIT"
            self.exit_reason = f"Mom음전({self.momentum:.2f})+기울기하락({self.momentum_slope:.3f})"


class TrendAccountMonitor:
    """
    중기 계좌 모니터링 시스템

    5202-2235 계좌의 보유 종목을 모니터링
    """

    def __init__(self, account_number: Optional[str] = None):
        self.account_number = account_number or os.getenv('KIWOOM_TREND_ACCOUNT', '5202-2235')
        self.holdings: Dict[str, TrendHolding] = {}
        self.api = None
        self.last_update: Optional[datetime] = None

        # 통계
        self.total_invested = 0.0
        self.total_eval = 0.0
        self.total_profit = 0.0
        self.total_profit_pct = 0.0

        logger.info(f"TrendAccountMonitor 초기화: {self.account_number}")

    def initialize(self) -> bool:
        """
        API 초기화 및 계좌 연결
        """
        try:
            from kiwoom_api import KiwoomAPI

            # 중기 계좌용 API 인스턴스 생성
            self.api = KiwoomAPI(account_number=self.account_number)

            # 토큰 발급 (get_access_token은 토큰 문자열 반환)
            token = self.api.get_access_token()
            if not token:
                logger.error("토큰 발급 실패")
                return False

            console.print(f"[green]✅ 중기 계좌 연결 성공: {self.account_number}[/green]")
            return True

        except Exception as e:
            logger.error(f"초기화 실패: {e}")
            console.print(f"[red]❌ 초기화 실패: {e}[/red]")
            return False

    def fetch_holdings(self) -> List[TrendHolding]:
        """
        보유 종목 조회
        """
        if not self.api:
            console.print("[red]❌ API가 초기화되지 않았습니다[/red]")
            return []

        try:
            # 계좌 정보 조회
            result = self.api.get_account_info()

            if result.get('return_code') != 0:
                console.print(f"[red]❌ 계좌 조회 실패: {result.get('return_msg')}[/red]")
                return []

            holdings_data = result.get('data', [])

            # 보유 종목 파싱
            self.holdings.clear()

            for item in holdings_data:
                stock_code = item.get('stk_cd', item.get('stock_code', ''))
                if not stock_code:
                    continue

                # 종목코드 정리 (A 제거)
                if stock_code.startswith('A'):
                    stock_code = stock_code[1:]

                holding = TrendHolding(
                    stock_code=stock_code,
                    stock_name=item.get('stk_nm', item.get('stock_name', '')),
                    quantity=int(item.get('hold_qty', item.get('quantity', 0))),
                    avg_price=float(item.get('avg_buy_price', item.get('avg_price', 0))),
                    current_price=float(item.get('cur_price', item.get('current_price', 0))),
                    eval_amount=float(item.get('eval_amt', 0)),
                    profit_loss=float(item.get('eval_pl', item.get('profit_loss', 0))),
                    profit_loss_pct=float(item.get('eval_pl_rt', item.get('profit_loss_pct', 0)))
                )

                self.holdings[stock_code] = holding

            # 총계 계산
            self._calculate_totals()
            self.last_update = datetime.now()

            return list(self.holdings.values())

        except Exception as e:
            logger.error(f"보유종목 조회 실패: {e}")
            console.print(f"[red]❌ 보유종목 조회 실패: {e}[/red]")
            return []

    def _calculate_totals(self):
        """총계 계산"""
        self.total_invested = sum(h.avg_price * h.quantity for h in self.holdings.values())
        self.total_eval = sum(h.eval_amount for h in self.holdings.values())
        self.total_profit = sum(h.profit_loss for h in self.holdings.values())
        self.total_profit_pct = ((self.total_eval - self.total_invested) / self.total_invested * 100) if self.total_invested > 0 else 0

    def update_squeeze_data(self, squeeze_data: Dict[str, Dict]):
        """
        Squeeze 데이터 일괄 업데이트

        Args:
            squeeze_data: {
                "종목코드": {
                    "squeeze_on": bool,
                    "momentum": float,
                    "momentum_prev": float
                }
            }
        """
        for code, data in squeeze_data.items():
            if code in self.holdings:
                self.holdings[code].update_squeeze(
                    squeeze_on=data.get('squeeze_on', True),
                    momentum=data.get('momentum', 0.0),
                    momentum_prev=data.get('momentum_prev', 0.0)
                )

    def update_prices(self, price_data: Dict[str, float]):
        """
        현재가 일괄 업데이트

        Args:
            price_data: {"종목코드": 현재가}
        """
        for code, price in price_data.items():
            if code in self.holdings:
                self.holdings[code].update_price(price)

        self._calculate_totals()
        self.last_update = datetime.now()

    def get_exit_signals(self) -> List[TrendHolding]:
        """청산 시그널 발생 종목 조회"""
        return [h for h in self.holdings.values() if h.exit_signal == "EXIT"]

    def display_holdings(self):
        """보유 종목 테이블 표시"""
        table = Table(title=f"📈 중기 계좌 ({self.account_number}) 보유 현황")

        table.add_column("종목명", style="cyan", width=12)
        table.add_column("코드", style="dim")
        table.add_column("수량", justify="right")
        table.add_column("평균가", justify="right")
        table.add_column("현재가", justify="right")
        table.add_column("수익률", justify="right")
        table.add_column("Squeeze", justify="center")
        table.add_column("Mom", justify="right")
        table.add_column("시그널", justify="center")

        for h in self.holdings.values():
            # 수익률 색상
            profit_style = "green" if h.profit_loss_pct >= 0 else "red"
            profit_str = f"[{profit_style}]{h.profit_loss_pct:+.2f}%[/{profit_style}]"

            # Squeeze 상태
            squeeze_str = "🟢 ON" if h.squeeze_on else "🔴 OFF"

            # 모멘텀
            mom_style = "green" if h.momentum >= 0 else "red"
            mom_str = f"[{mom_style}]{h.momentum:.2f}[/{mom_style}]"

            # 시그널
            signal_str = "⚠️ EXIT" if h.exit_signal else "✅ HOLD"

            table.add_row(
                h.stock_name[:10],
                h.stock_code,
                f"{h.quantity:,}",
                f"{h.avg_price:,.0f}",
                f"{h.current_price:,.0f}",
                profit_str,
                squeeze_str,
                mom_str,
                signal_str
            )

        # 합계 행
        total_style = "green" if self.total_profit_pct >= 0 else "red"
        table.add_row(
            "─" * 10, "─" * 6, "─" * 5, "─" * 8, "─" * 8, "─" * 8, "─" * 8, "─" * 6, "─" * 8
        )
        table.add_row(
            "[bold]합계[/bold]",
            "",
            "",
            f"{self.total_invested:,.0f}",
            f"{self.total_eval:,.0f}",
            f"[{total_style}]{self.total_profit_pct:+.2f}%[/{total_style}]",
            "",
            "",
            ""
        )

        console.print(table)

        if self.last_update:
            console.print(f"[dim]마지막 업데이트: {self.last_update.strftime('%H:%M:%S')}[/dim]")

    def display_exit_alerts(self):
        """청산 알림 표시"""
        exit_signals = self.get_exit_signals()

        if not exit_signals:
            return

        console.print()
        console.print(Panel(
            "\n".join([
                f"⚠️ [bold red]{h.stock_name}[/bold red] ({h.stock_code})",
                f"   사유: {h.exit_reason}",
                f"   현재 수익률: {h.profit_loss_pct:+.2f}%"
            ] for h in exit_signals),
            title="🚨 청산 시그널",
            border_style="red"
        ))

    def get_summary(self) -> Dict[str, Any]:
        """요약 정보 반환"""
        return {
            "account": self.account_number,
            "holdings_count": len(self.holdings),
            "total_invested": self.total_invested,
            "total_eval": self.total_eval,
            "total_profit": self.total_profit,
            "total_profit_pct": self.total_profit_pct,
            "exit_signals_count": len(self.get_exit_signals()),
            "last_update": self.last_update.isoformat() if self.last_update else None
        }

    def get_daily_report(self) -> str:
        """일일 리포트 생성"""
        lines = [
            "=" * 60,
            f"📊 중기 계좌 일일 리포트",
            f"계좌: {self.account_number}",
            f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            "",
            f"📦 보유 종목: {len(self.holdings)}개",
            f"💰 총 투자금: {self.total_invested:,.0f}원",
            f"💵 평가금액: {self.total_eval:,.0f}원",
            f"📈 총 수익: {self.total_profit:+,.0f}원 ({self.total_profit_pct:+.2f}%)",
            "",
            "-" * 60,
            "종목별 현황:",
            "-" * 60
        ]

        for h in self.holdings.values():
            squeeze_status = "ON" if h.squeeze_on else "OFF"
            signal = "⚠️ EXIT" if h.exit_signal else "HOLD"

            lines.extend([
                f"\n{h.stock_name} ({h.stock_code})",
                f"  수량: {h.quantity:,}주 | 평균가: {h.avg_price:,.0f}원",
                f"  현재가: {h.current_price:,.0f}원 | 수익률: {h.profit_loss_pct:+.2f}%",
                f"  Squeeze: {squeeze_status} | Mom: {h.momentum:.2f} | Slope: {h.momentum_slope:.3f}",
                f"  시그널: {signal}"
            ])

            if h.exit_signal:
                lines.append(f"  [청산사유] {h.exit_reason}")

        lines.extend([
            "",
            "=" * 60
        ])

        return "\n".join(lines)


# =============================================================================
# 실행 함수
# =============================================================================

def run_monitor():
    """모니터링 실행"""
    console.print()
    console.print("=" * 60)
    console.print("📈 중기 계좌 모니터링 시작")
    console.print("=" * 60)

    # 모니터 초기화
    monitor = TrendAccountMonitor()

    if not monitor.initialize():
        console.print("[red]초기화 실패. 종료합니다.[/red]")
        return

    # 보유 종목 조회
    console.print("\n[cyan]보유 종목 조회 중...[/cyan]")
    holdings = monitor.fetch_holdings()

    if not holdings:
        console.print("[yellow]보유 종목이 없습니다.[/yellow]")
        return

    console.print(f"[green]✅ {len(holdings)}개 종목 조회 완료[/green]\n")

    # 테이블 표시
    monitor.display_holdings()

    # 청산 알림
    monitor.display_exit_alerts()

    # 리포트 출력
    console.print("\n" + monitor.get_daily_report())


if __name__ == "__main__":
    run_monitor()
