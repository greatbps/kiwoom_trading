#!/usr/bin/env python3
"""
손절 후 재진입 전략
==================

감정 배제 + 기계적 재진입 룰

원칙:
1. 손절 직후 바로 재진입 금지
2. 추세 회복 확인 후에만 재진입
3. 재진입 시 비중 축소 (50%)
"""

import os
import sys
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

load_dotenv(project_root / '.env')

console = Console()


# ============================================================================
# 재진입 상태
# ============================================================================

class ReentryStatus(Enum):
    """재진입 상태"""
    COOLING = "COOLING"          # 쿨다운 기간 (재진입 금지)
    WATCHING = "WATCHING"        # 관찰 중 (조건 대기)
    READY = "READY"              # 재진입 가능
    BLOCKED = "BLOCKED"          # 재진입 금지 (추세 악화)


# ============================================================================
# 룰 파라미터
# ============================================================================

# 쿨다운 기간 (손절 후 최소 대기일)
COOLDOWN_DAYS = 5

# 재진입 비중 (원래 대비 %)
REENTRY_WEIGHT_PCT = 50

# 재진입 조건
REENTRY_CONDITIONS = {
    'above_ma20_daily': True,     # 일봉 20MA 위
    'macd_positive': True,        # MACD > 0
    'volume_recovery': True,      # 거래량 회복
    'min_bounce_pct': 5.0,        # 최저점 대비 최소 반등 %
}


# ============================================================================
# 데이터 클래스
# ============================================================================

@dataclass
class StoppedStock:
    """손절된 종목"""
    symbol: str
    stock_name: str
    stop_date: date
    stop_price: float
    stop_reason: str
    original_qty: int


@dataclass
class ReentryCandidate:
    """재진입 후보"""
    stock: StoppedStock
    status: ReentryStatus
    days_since_stop: int
    current_price: float = 0.0
    low_since_stop: float = 0.0
    bounce_pct: float = 0.0
    conditions_met: Dict[str, bool] = None
    reentry_qty: int = 0
    message: str = ""


# ============================================================================
# 재진입 전략 엔진
# ============================================================================

class ReentryStrategy:
    """손절 후 재진입 전략"""

    def __init__(self):
        self.stopped_stocks: List[StoppedStock] = []
        self.candidates: List[ReentryCandidate] = []

        # 손절 기록 로드
        self._load_stopped_stocks()

    def _load_stopped_stocks(self):
        """손절 기록에서 종목 로드"""
        log_dir = project_root / 'logs'

        # 최근 30일 로그 검색
        for i in range(30):
            d = date.today() - timedelta(days=i)
            log_file = log_dir / f"stop_loss_{d.strftime('%Y%m%d')}.json"

            if log_file.exists():
                try:
                    with open(log_file, 'r') as f:
                        logs = json.load(f)

                    for log in logs:
                        if log.get('status') == 'executed':
                            stock = StoppedStock(
                                symbol=log['symbol'],
                                stock_name=log['stock_name'],
                                stop_date=d,
                                stop_price=0,  # 시장가라 정확한 가격 모름
                                stop_reason=log['reason'],
                                original_qty=log['quantity']
                            )
                            self.stopped_stocks.append(stock)

                except Exception as e:
                    pass

    def evaluate_reentry(self) -> List[ReentryCandidate]:
        """재진입 후보 평가"""
        import yfinance as yf
        import pandas as pd

        self.candidates = []

        for stock in self.stopped_stocks:
            days_since = (date.today() - stock.stop_date).days

            candidate = ReentryCandidate(
                stock=stock,
                status=ReentryStatus.COOLING,
                days_since_stop=days_since,
                conditions_met={},
                reentry_qty=int(stock.original_qty * REENTRY_WEIGHT_PCT / 100)
            )

            # ─────────────────────────────────────────────────────
            # 1. 쿨다운 체크
            # ─────────────────────────────────────────────────────
            if days_since < COOLDOWN_DAYS:
                candidate.status = ReentryStatus.COOLING
                candidate.message = f"쿨다운 {COOLDOWN_DAYS - days_since}일 남음"
                self.candidates.append(candidate)
                continue

            # ─────────────────────────────────────────────────────
            # 2. 시장 데이터 조회
            # ─────────────────────────────────────────────────────
            try:
                ticker = f"{stock.symbol}.KS"
                daily = yf.download(ticker, period="2mo", interval="1d", progress=False)

                if daily.empty:
                    ticker = f"{stock.symbol}.KQ"
                    daily = yf.download(ticker, period="2mo", interval="1d", progress=False)

                if daily.empty:
                    candidate.status = ReentryStatus.BLOCKED
                    candidate.message = "데이터 없음"
                    self.candidates.append(candidate)
                    continue

                # MultiIndex 처리
                if isinstance(daily.columns, pd.MultiIndex):
                    daily.columns = daily.columns.get_level_values(0)

                current_price = float(daily['Close'].iloc[-1])
                candidate.current_price = current_price

                # 손절 이후 최저가
                stop_idx = daily.index >= pd.Timestamp(stock.stop_date)
                if stop_idx.any():
                    low_since = float(daily.loc[stop_idx, 'Low'].min())
                    candidate.low_since_stop = low_since
                    candidate.bounce_pct = ((current_price - low_since) / low_since) * 100

            except Exception as e:
                candidate.status = ReentryStatus.BLOCKED
                candidate.message = f"데이터 오류: {str(e)[:20]}"
                self.candidates.append(candidate)
                continue

            # ─────────────────────────────────────────────────────
            # 3. 재진입 조건 체크
            # ─────────────────────────────────────────────────────
            conditions = {}

            # 조건 1: 20MA 위
            if len(daily) >= 20:
                ma20 = float(daily['Close'].rolling(20).mean().iloc[-1])
                conditions['above_ma20_daily'] = current_price > ma20

            # 조건 2: MACD > 0
            if len(daily) >= 26:
                exp1 = daily['Close'].ewm(span=12, adjust=False).mean()
                exp2 = daily['Close'].ewm(span=26, adjust=False).mean()
                macd = float((exp1 - exp2).iloc[-1])
                conditions['macd_positive'] = macd > 0

            # 조건 3: 거래량 회복 (최근 5일 > 20일 평균)
            if len(daily) >= 20:
                vol_5d = float(daily['Volume'].tail(5).mean())
                vol_20d = float(daily['Volume'].tail(20).mean())
                conditions['volume_recovery'] = vol_5d > vol_20d

            # 조건 4: 최저점 대비 반등
            conditions['min_bounce_pct'] = candidate.bounce_pct >= REENTRY_CONDITIONS['min_bounce_pct']

            candidate.conditions_met = conditions

            # ─────────────────────────────────────────────────────
            # 4. 상태 결정
            # ─────────────────────────────────────────────────────
            all_met = all(conditions.values())
            any_met = any(conditions.values())

            if all_met:
                candidate.status = ReentryStatus.READY
                candidate.message = "재진입 가능"
            elif any_met:
                candidate.status = ReentryStatus.WATCHING
                failed = [k for k, v in conditions.items() if not v]
                candidate.message = f"대기: {', '.join(failed)}"
            else:
                candidate.status = ReentryStatus.BLOCKED
                candidate.message = "추세 미회복"

            self.candidates.append(candidate)

        return self.candidates

    def display_results(self):
        """결과 표시"""
        console.print()
        console.print(Panel(
            f"[bold]손절 후 재진입 전략[/bold]\n\n"
            f"쿨다운: {COOLDOWN_DAYS}일\n"
            f"재진입 비중: {REENTRY_WEIGHT_PCT}%\n"
            f"최소 반등: {REENTRY_CONDITIONS['min_bounce_pct']}%",
            title="🔄 Reentry Strategy",
            border_style="cyan"
        ))

        if not self.candidates:
            console.print("\n[dim]손절 기록 없음[/dim]")
            return

        table = Table(title="재진입 후보 평가")
        table.add_column("종목", style="cyan", width=15)
        table.add_column("손절일", width=10)
        table.add_column("경과", justify="right", width=6)
        table.add_column("현재가", justify="right", width=10)
        table.add_column("반등", justify="right", width=8)
        table.add_column("상태", width=10)
        table.add_column("메시지", width=20)

        status_styles = {
            ReentryStatus.COOLING: "dim",
            ReentryStatus.WATCHING: "yellow",
            ReentryStatus.READY: "green bold",
            ReentryStatus.BLOCKED: "red",
        }

        for c in self.candidates:
            style = status_styles.get(c.status, "white")

            table.add_row(
                c.stock.stock_name[:13],
                c.stock.stop_date.strftime('%m-%d'),
                f"{c.days_since_stop}일",
                f"{c.current_price:,.0f}" if c.current_price else "-",
                f"{c.bounce_pct:+.1f}%" if c.bounce_pct else "-",
                f"[{style}]{c.status.value}[/{style}]",
                c.message
            )

        console.print(table)

        # 조건 상세
        console.print("\n[bold]조건 상세:[/bold]")
        for c in self.candidates:
            if c.conditions_met:
                cond_str = " | ".join([
                    f"{'✅' if v else '❌'}{k}"
                    for k, v in c.conditions_met.items()
                ])
                console.print(f"  {c.stock.stock_name[:12]}: {cond_str}")

        # READY 종목 강조
        ready = [c for c in self.candidates if c.status == ReentryStatus.READY]
        if ready:
            console.print()
            console.print(Panel(
                "\n".join([
                    f"✅ {c.stock.stock_name}\n"
                    f"   재진입 수량: {c.reentry_qty}주 (원래의 {REENTRY_WEIGHT_PCT}%)\n"
                    f"   현재가: {c.current_price:,.0f}원"
                    for c in ready
                ]),
                title="🟢 재진입 가능 종목",
                border_style="green"
            ))

    def generate_reentry_plan(self) -> str:
        """재진입 계획 생성"""
        lines = [
            "=" * 50,
            "📋 손절 후 재진입 계획",
            f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 50,
            "",
            f"📌 룰 설정:",
            f"   - 쿨다운: {COOLDOWN_DAYS}일",
            f"   - 재진입 비중: 원래의 {REENTRY_WEIGHT_PCT}%",
            f"   - 최소 반등: {REENTRY_CONDITIONS['min_bounce_pct']}%",
            ""
        ]

        for c in self.candidates:
            lines.append(f"▶ {c.stock.stock_name} ({c.stock.symbol})")
            lines.append(f"   손절일: {c.stock.stop_date} ({c.days_since_stop}일 전)")
            lines.append(f"   상태: {c.status.value}")
            lines.append(f"   메시지: {c.message}")

            if c.status == ReentryStatus.READY:
                lines.append(f"   → 재진입 수량: {c.reentry_qty}주")
                lines.append(f"   → 현재가: {c.current_price:,.0f}원")
                lines.append(f"   → 진입가 기준: 현재가 or 눌림 대기")

            elif c.status == ReentryStatus.WATCHING:
                lines.append(f"   → 대기 조건:")
                for k, v in (c.conditions_met or {}).items():
                    lines.append(f"      {'✅' if v else '❌'} {k}")

            elif c.status == ReentryStatus.COOLING:
                lines.append(f"   → {COOLDOWN_DAYS - c.days_since_stop}일 후 재평가")

            lines.append("")

        return "\n".join(lines)


# ============================================================================
# 실행
# ============================================================================

def main():
    console.print()
    console.print("=" * 60)
    console.print("[bold]손절 후 재진입 전략 평가[/bold]")
    console.print("=" * 60)

    strategy = ReentryStrategy()

    if not strategy.stopped_stocks:
        console.print("\n[yellow]손절 기록이 없습니다.[/yellow]")
        console.print("[dim]STOP_LOSS 실행 후 이 스크립트를 다시 실행하세요.[/dim]")
        return

    console.print(f"\n[cyan]손절 기록: {len(strategy.stopped_stocks)}건[/cyan]")

    # 평가
    strategy.evaluate_reentry()

    # 결과 표시
    strategy.display_results()

    # 계획 생성
    plan = strategy.generate_reentry_plan()
    console.print("\n" + plan)


if __name__ == "__main__":
    main()
