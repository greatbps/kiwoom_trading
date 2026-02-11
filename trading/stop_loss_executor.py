#!/usr/bin/env python3
"""
STOP_LOSS 반자동 실행기
======================

이미 '정리'로 결정된 종목만 기계적으로 실행

⚠️ 완전 자동매매 아님
⚠️ 전략 판단 자동화 아님
✅ 룰 엔진이 STOP_LOSS로 판단한 종목만 실행

브로커 추상화 적용:
- 국내: BrokerType.KIS_DOMESTIC
- 해외: BrokerType.KIS_OVERSEAS
"""

import os
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

import sys
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv(project_root / '.env')

# 브로커 추상화 임포트
from brokers import get_broker, BrokerType, Market

console = Console()
logger = logging.getLogger(__name__)


# ============================================================================
# 🔒 안전 스위치
# ============================================================================

# 자동 손절 활성화 여부 (수동 스위치)
AUTO_STOP_ENABLED = True  # ✅ 실제 주문 활성화

# 자동 손절 허용 그룹 (A=코어는 제외)
AUTO_STOP_ALLOWED_GROUPS = ["B", "C"]

# 1일 최대 자동 손절 횟수
MAX_DAILY_STOPS = 3


# ============================================================================
# 상태 추적
# ============================================================================

@dataclass
class StopExecution:
    """손절 실행 기록"""
    symbol: str
    stock_name: str
    action: str
    quantity: int
    price: str  # "MARKET" or actual price
    reason: str
    timestamp: str
    order_no: str = ""
    status: str = "pending"  # pending, executed, failed


class StopLossExecutor:
    """STOP_LOSS 반자동 실행기"""

    def __init__(self, market: Market = Market.KR):
        """
        Args:
            market: 시장 구분 (KR: 국내, US: 해외)
        """
        self.market = market

        # 브로커 추상화 사용
        if market == Market.KR:
            self.broker = get_broker(BrokerType.KIS_DOMESTIC)
        else:
            self.broker = get_broker(BrokerType.KIS_OVERSEAS)

        self.executions: List[StopExecution] = []
        self.stopped_today: Set[str] = set()

        # 로그 파일
        self.log_dir = project_root / 'logs'
        self.log_dir.mkdir(exist_ok=True)
        market_suffix = "overseas" if market == Market.US else "domestic"
        self.log_file = self.log_dir / f"stop_loss_{market_suffix}_{date.today().strftime('%Y%m%d')}.json"

        # 오늘 이미 실행된 종목 로드
        self._load_today_stops()

    def _load_today_stops(self):
        """오늘 손절 실행 기록 로드"""
        if self.log_file.exists():
            try:
                with open(self.log_file, 'r') as f:
                    logs = json.load(f)
                    for log in logs:
                        if log.get('status') == 'executed':
                            self.stopped_today.add(log['symbol'])
            except:
                pass

    def _save_execution(self, execution: StopExecution):
        """실행 기록 저장"""
        try:
            if self.log_file.exists():
                with open(self.log_file, 'r') as f:
                    logs = json.load(f)
            else:
                logs = []

            logs.append({
                'symbol': execution.symbol,
                'stock_name': execution.stock_name,
                'action': execution.action,
                'quantity': execution.quantity,
                'price': execution.price,
                'reason': execution.reason,
                'timestamp': execution.timestamp,
                'order_no': execution.order_no,
                'status': execution.status
            })

            with open(self.log_file, 'w') as f:
                json.dump(logs, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"로그 저장 실패: {e}")

    def initialize(self) -> bool:
        """브로커 초기화"""
        return self.broker.initialize()

    def check_and_execute(self, mid_term_results: List) -> List[StopExecution]:
        """
        STOP_LOSS 종목 확인 및 실행

        Args:
            mid_term_results: 중기 엔진 평가 결과 리스트

        Returns:
            실행 결과 리스트
        """
        from trading.mid_term_engine import Action

        console.print()
        console.print(Panel(
            f"[bold]STOP_LOSS 반자동 실행기[/bold]\n\n"
            f"AUTO_STOP_ENABLED = [{'green' if AUTO_STOP_ENABLED else 'red'}]{AUTO_STOP_ENABLED}[/]\n"
            f"허용 그룹: {AUTO_STOP_ALLOWED_GROUPS}\n"
            f"오늘 실행: {len(self.stopped_today)}건",
            title="🔒 안전 스위치",
            border_style="yellow" if not AUTO_STOP_ENABLED else "red"
        ))

        results = []

        for r in mid_term_results:
            pos = r.position

            # STOP_LOSS 아니면 스킵
            if r.action != Action.STOP_LOSS:
                continue

            console.print(f"\n[bold red]🔴 STOP_LOSS 감지: {pos.stock_name}[/bold red]")
            console.print(f"   수익률: {pos.profit_pct:+.1f}%")
            console.print(f"   그룹: {pos.group.value}")
            console.print(f"   사유: {r.reason}")

            # ─────────────────────────────────────────────────────
            # 안전 체크 1: 그룹 허용 여부
            # ─────────────────────────────────────────────────────
            if pos.group.value not in AUTO_STOP_ALLOWED_GROUPS:
                console.print(f"   [yellow]⚠️ 그룹 {pos.group.value}은 자동 손절 제외[/yellow]")
                continue

            # ─────────────────────────────────────────────────────
            # 안전 체크 2: 오늘 이미 손절했는지
            # ─────────────────────────────────────────────────────
            if pos.stock_code in self.stopped_today:
                console.print(f"   [yellow]⚠️ 오늘 이미 손절 실행됨[/yellow]")
                continue

            # ─────────────────────────────────────────────────────
            # 안전 체크 3: 1일 최대 횟수
            # ─────────────────────────────────────────────────────
            if len(self.stopped_today) >= MAX_DAILY_STOPS:
                console.print(f"   [yellow]⚠️ 1일 최대 손절 횟수 초과 ({MAX_DAILY_STOPS})[/yellow]")
                continue

            # ─────────────────────────────────────────────────────
            # 실행 준비
            # ─────────────────────────────────────────────────────
            execution = StopExecution(
                symbol=pos.stock_code,
                stock_name=pos.stock_name,
                action="STOP_LOSS",
                quantity=pos.quantity,
                price="MARKET",
                reason=r.reason,
                timestamp=datetime.now().isoformat()
            )

            # ─────────────────────────────────────────────────────
            # 안전 체크 4: AUTO_STOP_ENABLED
            # ─────────────────────────────────────────────────────
            if not AUTO_STOP_ENABLED:
                console.print(f"\n   [yellow]🔒 AUTO_STOP_ENABLED=False[/yellow]")
                console.print(f"   [dim]실제 주문이 전송되지 않았습니다.[/dim]")
                execution.status = "simulated"
                self._save_execution(execution)
                results.append(execution)
                continue

            # ─────────────────────────────────────────────────────
            # 실제 주문 실행 (브로커 추상화 사용)
            # ─────────────────────────────────────────────────────
            console.print(f"\n   [bold red]🚀 시장가 매도 주문 전송...[/bold red]")
            console.print(f"   [dim]브로커: {self.broker.name}[/dim]")

            try:
                # 브로커 추상화 레이어 사용
                order_result = self.broker.place_market_sell(
                    symbol=pos.stock_code,
                    quantity=pos.quantity
                )

                if order_result.success:
                    execution.order_no = order_result.order_no
                    execution.status = "executed"
                    self.stopped_today.add(pos.stock_code)

                    console.print(f"   [green]✅ 주문 성공![/green]")
                    console.print(f"   주문번호: {execution.order_no}")

                else:
                    execution.status = "failed"
                    execution.reason += f" | 주문실패: {order_result.message}"

                    console.print(f"   [red]❌ 주문 실패: {order_result.message}[/red]")

            except Exception as e:
                execution.status = "failed"
                execution.reason += f" | 예외: {str(e)}"
                console.print(f"   [red]❌ 예외 발생: {e}[/red]")

            self._save_execution(execution)
            results.append(execution)

        # 결과 요약
        console.print()
        console.print("=" * 50)
        console.print(f"[bold]실행 결과: {len(results)}건[/bold]")

        for ex in results:
            status_style = {
                "executed": "green",
                "simulated": "yellow",
                "failed": "red"
            }.get(ex.status, "white")

            console.print(f"  {ex.stock_name}: [{status_style}]{ex.status}[/{status_style}]")

        console.print(f"\n📁 로그: {self.log_file}")

        return results


# ============================================================================
# 실행
# ============================================================================

def run_stop_loss_check(market: Market = Market.KR):
    """
    STOP_LOSS 체크 및 실행

    Args:
        market: 시장 구분 (Market.KR: 국내, Market.US: 해외)
    """
    from trading.mid_term_engine import MidTermEngine

    market_name = "국내" if market == Market.KR else "해외"

    console.print()
    console.print("=" * 60)
    console.print(f"[bold]STOP_LOSS 반자동 실행 ({market_name})[/bold]")
    console.print("=" * 60)

    # 1. 중기 엔진으로 평가
    console.print(f"\n[cyan]1. 중기 룰 엔진 평가 중 ({market_name})...[/cyan]")

    engine = MidTermEngine(market=market)
    if not engine.initialize():
        console.print("[red]❌ 중기 엔진 초기화 실패[/red]")
        return

    engine.fetch_positions()
    results = engine.evaluate_all()

    # 결과 표시
    engine.display_results()

    # 2. STOP_LOSS 실행
    console.print(f"\n[cyan]2. STOP_LOSS 실행 체크 ({market_name})...[/cyan]")

    executor = StopLossExecutor(market=market)
    if not executor.initialize():
        console.print("[red]❌ 실행기 초기화 실패[/red]")
        return

    executions = executor.check_and_execute(results)

    return executions


def run_all_stop_loss_checks():
    """국내 + 해외 모두 체크"""
    console.print(Panel(
        "[bold]STOP_LOSS 전체 점검[/bold]\n\n"
        "국내 + 해외 모든 포지션 평가",
        title="🔒 Stop Loss Check",
        border_style="red"
    ))

    # 국내
    domestic_results = run_stop_loss_check(Market.KR)

    # 해외
    console.print("\n")
    overseas_results = run_stop_loss_check(Market.US)

    return {
        'domestic': domestic_results,
        'overseas': overseas_results
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--overseas":
        run_stop_loss_check(Market.US)
    elif len(sys.argv) > 1 and sys.argv[1] == "--all":
        run_all_stop_loss_checks()
    else:
        run_stop_loss_check(Market.KR)
