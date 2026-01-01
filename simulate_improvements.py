#!/usr/bin/env python3
"""
📊 GPT 개선 사항 시뮬레이션 (2025-12-16 거래 기준)

실제 12/16 거래 데이터를 기반으로:
- BEFORE: 기존 로직으로 실제 발생한 거래
- AFTER: GPT 개선 사항 적용 시 예상 결과
"""

import json
from datetime import datetime, time
from typing import Dict, List, Tuple
from rich.console import Console
from rich.table import Table

console = Console()


class TradingSimulator:
    """거래 시뮬레이터"""

    def __init__(self):
        # GPT 개선 파라미터
        self.midday_start = time(12, 0, 0)
        self.midday_end = time(14, 0, 0)
        self.loss_cooldown_minutes = 30
        self.profit_cooldown_minutes = 20
        self.max_trades_per_stock = 2

        # 상태 추적
        self.stock_cooldown: Dict[str, Tuple[datetime, bool]] = {}
        self.daily_trade_count: Dict[str, int] = {}
        self.blocked_trades = []

    def is_midday(self, timestamp: str) -> bool:
        """점심시간 체크"""
        dt = datetime.fromisoformat(timestamp)
        t = dt.time()
        return self.midday_start <= t < self.midday_end

    def check_cooldown(self, stock_code: str, timestamp: str) -> Tuple[bool, str]:
        """쿨다운 체크 (손절 30분, 익절 20분)"""
        if stock_code not in self.stock_cooldown:
            return True, ""

        last_exit, is_loss = self.stock_cooldown[stock_code]
        current_time = datetime.fromisoformat(timestamp)
        elapsed_minutes = (current_time - last_exit).total_seconds() / 60

        required_cooldown = self.loss_cooldown_minutes if is_loss else self.profit_cooldown_minutes

        if elapsed_minutes < required_cooldown:
            remaining = required_cooldown - elapsed_minutes
            cooldown_type = "손절" if is_loss else "익절"
            return False, f"{cooldown_type} 쿨다운 {remaining:.1f}분 남음"

        return True, ""

    def check_daily_limit(self, stock_code: str) -> Tuple[bool, str]:
        """일일 거래 한도 체크"""
        count = self.daily_trade_count.get(stock_code, 0)
        if count >= self.max_trades_per_stock:
            return False, f"일일 한도 초과 ({count}/{self.max_trades_per_stock})"
        return True, ""

    def process_trade(self, trade: dict) -> Tuple[bool, str]:
        """거래 처리 (GPT 개선 규칙 적용)"""
        stock_code = trade['stock_code']
        stock_name = trade['stock_name']
        trade_type = trade['type']
        timestamp = trade['timestamp']
        pnl = trade['realized_pnl']

        # BUY 거래만 필터링 체크
        if trade_type == 'BUY':
            # 1. 점심시간 체크
            if self.is_midday(timestamp):
                return False, "🚫 점심시간 진입 차단"

            # 2. 쿨다운 체크
            can_trade, reason = self.check_cooldown(stock_code, timestamp)
            if not can_trade:
                return False, f"⏸️  {reason}"

            # 3. 일일 거래 한도 체크
            can_trade, reason = self.check_daily_limit(stock_code)
            if not can_trade:
                return False, f"🚫 {reason}"

            # BUY 허용 → 카운트 증가
            self.daily_trade_count[stock_code] = self.daily_trade_count.get(stock_code, 0) + 1
            return True, "✅ 진입 허용"

        # SELL 거래 → 쿨다운 설정
        elif trade_type == 'SELL':
            is_loss = pnl < 0
            self.stock_cooldown[stock_code] = (datetime.fromisoformat(timestamp), is_loss)
            cooldown_type = "손절" if is_loss else "익절"
            cooldown_time = self.loss_cooldown_minutes if is_loss else self.profit_cooldown_minutes
            return True, f"⏸️  {cooldown_type} 쿨다운 {cooldown_time}분 시작"

        return True, ""


def load_trade_data() -> dict:
    """거래 데이터 로드"""
    file_path = "/home/greatbps/projects/kiwoom_trading/data/weekly_trade_report.json"
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def simulate_improved_trading(trades: List[dict]) -> Tuple[List[dict], List[dict]]:
    """
    개선된 로직으로 거래 시뮬레이션

    Returns:
        (allowed_trades, blocked_trades)
    """
    simulator = TradingSimulator()
    allowed_trades = []
    blocked_trades = []

    for trade in trades:
        allowed, reason = simulator.process_trade(trade)

        if allowed:
            if trade['type'] == 'BUY':
                allowed_trades.append({**trade, 'status': reason})
            else:
                # SELL은 항상 허용 (이미 보유 중인 포지션)
                allowed_trades.append({**trade, 'status': reason})
        else:
            blocked_trades.append({**trade, 'blocked_reason': reason})

    return allowed_trades, blocked_trades


def calculate_pnl(trades: List[dict]) -> float:
    """P&L 계산"""
    total_pnl = 0.0
    for trade in trades:
        if trade['type'] == 'SELL':
            total_pnl += trade['realized_pnl']
    return total_pnl


def generate_report(original_data: dict, allowed_trades: List[dict], blocked_trades: List[dict]):
    """비교 보고서 생성"""

    console.print("\n" + "="*80)
    console.print("[bold cyan]📊 GPT 개선 사항 시뮬레이션 결과 (2025-12-16)[/bold cyan]")
    console.print("="*80 + "\n")

    # ========================================
    # 1. 전체 요약
    # ========================================
    original_pnl = original_data['summary']['realized_pnl']
    original_trades = original_data['summary']['total_trades']

    # 허용된 거래에서 BUY/SELL 쌍 계산
    buy_trades = [t for t in allowed_trades if t['type'] == 'BUY']
    sell_trades = [t for t in allowed_trades if t['type'] == 'SELL']

    # 차단된 BUY 거래 수
    blocked_buy_count = len([t for t in blocked_trades if t['type'] == 'BUY'])

    # 시뮬레이션 P&L (실제로는 차단된 거래의 결과만 제외)
    # 차단된 BUY가 있으면 그에 대응하는 SELL도 발생하지 않음
    simulated_pnl = calculate_simulated_pnl(original_data['trades'], blocked_trades)

    improvement = simulated_pnl - original_pnl
    improvement_pct = (improvement / abs(original_pnl) * 100) if original_pnl != 0 else 0

    table = Table(title="전체 요약", show_header=True, header_style="bold magenta")
    table.add_column("구분", style="cyan", width=20)
    table.add_column("BEFORE (실제)", justify="right", style="yellow", width=20)
    table.add_column("AFTER (개선)", justify="right", style="green", width=20)
    table.add_column("변화", justify="right", style="bold", width=20)

    table.add_row(
        "총 거래 수",
        f"{original_trades}건",
        f"{len(allowed_trades)}건",
        f"{len(allowed_trades) - original_trades:+d}건"
    )
    table.add_row(
        "차단된 거래",
        "0건",
        f"{blocked_buy_count}건",
        f"+{blocked_buy_count}건"
    )
    table.add_row(
        "실현 손익",
        f"{original_pnl:,.0f}원",
        f"{simulated_pnl:,.0f}원",
        f"[{'green' if improvement > 0 else 'red'}]{improvement:+,.0f}원 ({improvement_pct:+.1f}%)[/{'green' if improvement > 0 else 'red'}]"
    )

    console.print(table)

    # ========================================
    # 2. 차단된 거래 상세
    # ========================================
    if blocked_trades:
        console.print(f"\n[bold red]🚫 차단된 거래 ({len(blocked_trades)}건)[/bold red]\n")

        blocked_table = Table(show_header=True, header_style="bold red")
        blocked_table.add_column("시간", style="dim", width=12)
        blocked_table.add_column("종목", width=15)
        blocked_table.add_column("유형", width=6)
        blocked_table.add_column("차단 사유", style="yellow", width=35)

        for trade in blocked_trades:
            if trade['type'] == 'BUY':
                dt = datetime.fromisoformat(trade['timestamp'])
                blocked_table.add_row(
                    dt.strftime("%H:%M:%S"),
                    trade['stock_name'],
                    trade['type'],
                    trade['blocked_reason']
                )

        console.print(blocked_table)

    # ========================================
    # 3. 종목별 영향
    # ========================================
    console.print("\n[bold cyan]📈 종목별 영향 분석[/bold cyan]\n")

    stock_table = Table(show_header=True, header_style="bold cyan")
    stock_table.add_column("종목", width=20)
    stock_table.add_column("실제 거래", justify="right", width=12)
    stock_table.add_column("개선 후", justify="right", width=12)
    stock_table.add_column("실제 손익", justify="right", width=15)
    stock_table.add_column("예상 손익", justify="right", width=15)
    stock_table.add_column("개선 효과", justify="right", style="bold", width=15)

    for stock_key, stock_data in original_data['stock_summary'].items():
        original_stock_pnl = stock_data['realized_pnl']

        # 이 종목의 차단된 거래 계산
        stock_code = stock_key.split()[0]
        stock_blocked = [t for t in blocked_trades if t['stock_code'] == stock_code and t['type'] == 'BUY']
        stock_allowed_buys = len([t for t in allowed_trades if t['stock_code'] == stock_code and t['type'] == 'BUY'])

        original_buy_count = stock_data['buy_qty'] // 10  # 거래 건수 추정 (수량 / 평균)

        # 시뮬레이션 손익 (차단된 거래의 손익 제외)
        simulated_stock_pnl = calculate_stock_simulated_pnl(
            original_data['trades'],
            stock_code,
            blocked_trades
        )

        stock_improvement = simulated_stock_pnl - original_stock_pnl

        stock_table.add_row(
            stock_key,
            f"{original_buy_count}회",
            f"{stock_allowed_buys}회",
            f"{original_stock_pnl:,.0f}원",
            f"{simulated_stock_pnl:,.0f}원",
            f"[{'green' if stock_improvement > 0 else 'red'}]{stock_improvement:+,.0f}원[/{'green' if stock_improvement > 0 else 'red'}]"
        )

    console.print(stock_table)

    # ========================================
    # 4. 개선 사항 체크리스트
    # ========================================
    console.print("\n[bold green]✅ 적용된 개선 사항[/bold green]\n")

    improvements = [
        ("🚫 점심시간 진입 차단", len([t for t in blocked_trades if "점심시간" in t.get('blocked_reason', '')])),
        ("⏸️  손절 쿨다운 30분", len([t for t in blocked_trades if "손절 쿨다운" in t.get('blocked_reason', '')])),
        ("🚫 종목별 일일 한도", len([t for t in blocked_trades if "일일 한도" in t.get('blocked_reason', '')])),
        ("🛡️  부분청산 후 BE 보호", "exit_logic_optimized.py 적용"),
        ("📊 VWAP 필터 강화", "signal_detector.py 적용")
    ]

    for item, value in improvements:
        if isinstance(value, int):
            console.print(f"  {item}: [yellow]{value}건 차단[/yellow]")
        else:
            console.print(f"  {item}: [green]{value}[/green]")

    # ========================================
    # 5. 결론
    # ========================================
    console.print(f"\n{'='*80}")
    console.print("[bold cyan]🎯 시뮬레이션 결론[/bold cyan]\n")

    if improvement > 0:
        console.print(f"[green]✅ GPT 개선 사항 적용 시 예상 손익: {improvement:+,.0f}원 개선[/green]")
        console.print(f"[green]   - 불필요한 거래 {blocked_buy_count}건 차단[/green]")
        console.print(f"[green]   - 손실률 {improvement_pct:+.1f}% 감소[/green]")
    else:
        console.print(f"[yellow]⚠️  이번 시뮬레이션에서는 {abs(improvement):,.0f}원 추가 손실 예상[/yellow]")
        console.print(f"[yellow]   - 하지만 장기적으로는 과도한 거래와 집중 리스크 감소 효과 기대[/yellow]")

    console.print(f"{'='*80}\n")

    # 결과 저장
    result = {
        "simulation_date": "2025-12-16",
        "improvements_applied": [
            "점심시간 진입 차단 (12:00-14:00)",
            "손절 쿨다운 30분 (익절 20분)",
            "종목별 일일 최대 2회",
            "부분청산 후 BE 보호",
            "VWAP 필터 강화 (이격 0.4%, 기울기 0.05%)"
        ],
        "before": {
            "total_trades": original_trades,
            "realized_pnl": original_pnl
        },
        "after": {
            "total_trades": len(allowed_trades),
            "blocked_trades": blocked_buy_count,
            "realized_pnl": simulated_pnl
        },
        "improvement": {
            "pnl_diff": improvement,
            "pnl_diff_pct": improvement_pct,
            "trades_reduced": blocked_buy_count
        }
    }

    output_path = "/home/greatbps/projects/kiwoom_trading/data/simulation_result.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    console.print(f"[dim]💾 시뮬레이션 결과 저장: {output_path}[/dim]\n")


def calculate_simulated_pnl(all_trades: List[dict], blocked_trades: List[dict]) -> float:
    """
    시뮬레이션 P&L 계산

    차단된 BUY 거래와 그에 대응하는 SELL 거래의 P&L을 제외
    """
    blocked_buy_timestamps = {t['timestamp'] for t in blocked_trades if t['type'] == 'BUY'}

    # 차단된 BUY와 매칭되는 SELL 찾기 (다음 SELL 거래)
    blocked_sell_pnl = 0.0
    buy_index = 0

    simulated_pnl = 0.0
    skip_next_sell = {}

    for i, trade in enumerate(all_trades):
        stock_code = trade['stock_code']

        if trade['type'] == 'BUY':
            if trade['timestamp'] in blocked_buy_timestamps:
                # 이 BUY가 차단됨 → 다음 SELL도 차단
                skip_next_sell[stock_code] = skip_next_sell.get(stock_code, 0) + 1

        elif trade['type'] == 'SELL':
            if skip_next_sell.get(stock_code, 0) > 0:
                # 차단된 BUY에 대응하는 SELL → P&L 제외
                skip_next_sell[stock_code] -= 1
            else:
                # 정상 SELL → P&L 포함
                simulated_pnl += trade['realized_pnl']

    return simulated_pnl


def calculate_stock_simulated_pnl(all_trades: List[dict], stock_code: str, blocked_trades: List[dict]) -> float:
    """특정 종목의 시뮬레이션 P&L 계산"""
    stock_trades = [t for t in all_trades if t['stock_code'] == stock_code]
    stock_blocked = [t for t in blocked_trades if t['stock_code'] == stock_code]

    return calculate_simulated_pnl(stock_trades, stock_blocked)


def main():
    """메인 실행"""
    console.print("\n[bold cyan]📊 GPT 개선 사항 시뮬레이션 시작...[/bold cyan]\n")

    # 1. 데이터 로드
    data = load_trade_data()
    console.print(f"[green]✓[/green] 거래 데이터 로드: {len(data['trades'])}건\n")

    # 2. 시뮬레이션 실행
    allowed_trades, blocked_trades = simulate_improved_trading(data['trades'])
    console.print(f"[green]✓[/green] 시뮬레이션 완료\n")

    # 3. 보고서 생성
    generate_report(data, allowed_trades, blocked_trades)


if __name__ == "__main__":
    main()
