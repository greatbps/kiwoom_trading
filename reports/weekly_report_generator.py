"""
주간 자동매매 성과 리포트 v1.0
- WIN / DRAW / LOSS 분리 지표
- Draw는 "관측된 무반응"으로 취급
- 2026-01-27 GPT 분석 기반 설계
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from collections import defaultdict


class WeeklyReportGenerator:
    """주간 리포트 생성기"""

    def __init__(self, config: Dict = None):
        """
        Args:
            config: 설정 (draw_trade 기준 등)
        """
        self.config = config or {}

        # Draw 분류 기준
        draw_config = self.config.get('risk_control', {}).get('draw_trade', {})
        self.draw_profit_threshold = draw_config.get('profit_threshold_pct', 0.2)
        self.draw_min_hold_hours = draw_config.get('min_hold_hours', 6)

    def classify_trade(
        self,
        profit_pct: float,
        hold_hours: float
    ) -> str:
        """
        거래를 WIN / DRAW / LOSS로 분류

        Draw 조건: |profit| < 0.2% AND hold >= 6h
        """
        if abs(profit_pct) < self.draw_profit_threshold and hold_hours >= self.draw_min_hold_hours:
            return 'DRAW'
        return 'WIN' if profit_pct > 0 else 'LOSS'

    def calculate_hold_hours(self, entry_time, exit_time) -> float:
        """보유 시간(시간) 계산"""
        if isinstance(entry_time, str):
            entry_time = datetime.fromisoformat(entry_time)
        if isinstance(exit_time, str):
            exit_time = datetime.fromisoformat(exit_time)

        if entry_time and exit_time:
            delta = exit_time - entry_time
            return delta.total_seconds() / 3600
        return 0

    def parse_profit_from_reason(self, exit_reason: str) -> float:
        """청산 사유에서 수익률 파싱"""
        import re
        if not exit_reason:
            return 0.0

        # 패턴: +1.23% 또는 -1.23%
        match = re.search(r'([+-]?\d+\.?\d*)%', exit_reason)
        if match:
            return float(match.group(1))
        return 0.0

    def generate_report(self, trades: List[Dict], week_start: str = None) -> str:
        """
        주간 리포트 생성

        Args:
            trades: 거래 리스트 (risk_log.json 형식)
            week_start: 주 시작일 (YYYY-MM-DD)

        Returns:
            포맷된 리포트 문자열
        """
        if not week_start:
            # 이번 주 월요일
            today = datetime.now()
            week_start = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')

        week_end = (datetime.strptime(week_start, '%Y-%m-%d') + timedelta(days=6)).strftime('%Y-%m-%d')

        # ========================================
        # 거래 분류
        # ========================================
        classified_trades = []
        win_trades = []
        draw_trades = []
        loss_trades = []

        # BUY/SELL 매칭
        buy_positions = {}

        for trade in trades:
            trade_type = trade.get('type', '')
            stock_code = trade.get('stock_code', '')

            if trade_type == 'BUY':
                buy_positions[stock_code] = trade
            elif trade_type == 'SELL' and stock_code in buy_positions:
                buy_trade = buy_positions.pop(stock_code)

                # 보유 시간 계산
                entry_time = buy_trade.get('timestamp')
                exit_time = trade.get('timestamp')
                hold_hours = self.calculate_hold_hours(entry_time, exit_time)

                # 수익률 (realized_pnl 또는 exit_reason에서 파싱)
                profit_pct = self.parse_profit_from_reason(trade.get('reason', ''))

                # 분류
                result = self.classify_trade(profit_pct, hold_hours)

                trade_info = {
                    'stock_name': trade.get('stock_name', 'N/A'),
                    'stock_code': stock_code,
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'hold_hours': hold_hours,
                    'profit_pct': profit_pct,
                    'realized_pnl': trade.get('realized_pnl', 0),
                    'entry_reason': buy_trade.get('reason', ''),
                    'exit_reason': trade.get('reason', ''),
                    'result': result
                }

                classified_trades.append(trade_info)

                if result == 'WIN':
                    win_trades.append(trade_info)
                elif result == 'DRAW':
                    draw_trades.append(trade_info)
                else:
                    loss_trades.append(trade_info)

        # ========================================
        # 통계 계산
        # ========================================
        total_trades = len(classified_trades)
        total_pnl = sum(t['realized_pnl'] for t in classified_trades)

        # 승률 (WIN 기준, DRAW 제외)
        win_loss_count = len(win_trades) + len(loss_trades)
        win_rate = (len(win_trades) / win_loss_count * 100) if win_loss_count > 0 else 0

        # 손익비 계산
        avg_win = sum(t['profit_pct'] for t in win_trades) / len(win_trades) if win_trades else 0
        avg_loss = abs(sum(t['profit_pct'] for t in loss_trades) / len(loss_trades)) if loss_trades else 1
        rr_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # ========================================
        # 리포트 생성
        # ========================================
        report = []
        report.append("=" * 60)
        report.append("📊 [주간 자동매매 성과 리포트 v1.0]")
        report.append("=" * 60)
        report.append("")

        # ① 요약 KPI
        report.append("┌─────────────────────────────────────────────────────────┐")
        report.append(f"│ 기간: {week_start} ~ {week_end}")
        report.append("├─────────────────────────────────────────────────────────┤")
        report.append(f"│ 총 거래: {total_trades}건")
        report.append(f"│ WIN / DRAW / LOSS: {len(win_trades)} / {len(draw_trades)} / {len(loss_trades)}")
        report.append(f"│ 실제 승률 (WIN 기준): {win_rate:.1f}%")
        report.append(f"│ 손익비 (WIN / LOSS): {rr_ratio:.2f}R")
        report.append(f"│ 총 실현손익: {total_pnl:+,.0f}원")
        report.append("└─────────────────────────────────────────────────────────┘")
        report.append("")

        # ② 거래 유형 분포
        report.append("📈 거래 유형 분포")
        report.append("-" * 40)

        if total_trades > 0:
            win_pct = len(win_trades) / total_trades * 100
            draw_pct = len(draw_trades) / total_trades * 100
            loss_pct = len(loss_trades) / total_trades * 100

            win_bar = "█" * int(win_pct / 5) + "░" * (20 - int(win_pct / 5))
            draw_bar = "█" * int(draw_pct / 5) + "░" * (20 - int(draw_pct / 5))
            loss_bar = "█" * int(loss_pct / 5) + "░" * (20 - int(loss_pct / 5))

            report.append(f"  WIN  : {win_bar} ({win_pct:.0f}%)")
            report.append(f"  DRAW : {draw_bar} ({draw_pct:.0f}%)")
            report.append(f"  LOSS : {loss_bar} ({loss_pct:.0f}%)")
        else:
            report.append("  거래 없음")
        report.append("")

        # ③ DRAW 거래 상세
        report.append("🔘 DRAW 거래 상세 (시스템의 심장)")
        report.append("-" * 60)
        if draw_trades:
            report.append(f"{'종목':<12} {'수익률':>8} {'보유시간':>8} {'청산사유':<25}")
            report.append("-" * 60)
            for t in draw_trades:
                stock = t['stock_name'][:10]
                profit = f"{t['profit_pct']:+.2f}%"
                hold = f"{t['hold_hours']:.1f}h"
                reason = t['exit_reason'][:25] if t['exit_reason'] else 'N/A'

                # Draw 태그 추론
                tag = self._infer_draw_tag(t)

                report.append(f"{stock:<12} {profit:>8} {hold:>8} {reason:<25}")
                report.append(f"  └─ 태그: {tag}")
        else:
            report.append("  DRAW 거래 없음 (좋은 신호)")
        report.append("")

        # ④ LOSS 거래 요약
        report.append("❌ LOSS 거래 요약 (감정 배제)")
        report.append("-" * 40)
        if loss_trades:
            early_failure = sum(1 for t in loss_trades if 'Early Failure' in (t['exit_reason'] or ''))
            structure_exit = sum(1 for t in loss_trades if 'CHoCH' in (t['exit_reason'] or '') or '구조' in (t['exit_reason'] or ''))
            hard_stop = sum(1 for t in loss_trades if 'Hard Stop' in (t['exit_reason'] or ''))

            report.append(f"  LOSS {len(loss_trades)}건")
            report.append(f"  - Early Failure: {early_failure}")
            report.append(f"  - 구조 붕괴 손절: {structure_exit}")
            report.append(f"  - Hard Stop: {hard_stop}")
            report.append(f"  → 시스템 오류: {'없음' if early_failure == 0 else '확인 필요'}")
        else:
            report.append("  LOSS 거래 없음 ✅")
        report.append("")

        # ⑤ 시스템 상태 판정
        report.append("🔧 시스템 상태 판정")
        report.append("-" * 40)

        # 상태 판정 로직
        health_status = "🟢 Stable"
        health_notes = []

        if total_trades > 0:
            draw_ratio = len(draw_trades) / total_trades
            if draw_ratio > 0.5:
                health_status = "🟡 Warning"
                health_notes.append("Draw 비중 과다 (>50%)")

            early_failure_count = sum(1 for t in loss_trades if 'Early Failure' in (t['exit_reason'] or ''))
            if early_failure_count > 2:
                health_status = "🟡 Warning"
                health_notes.append(f"Early Failure 다발 ({early_failure_count}건)")

        report.append(f"  System Health: {health_status}")
        if health_notes:
            for note in health_notes:
                report.append(f"  - {note}")
        else:
            report.append("  - 정상 운영 중")
        report.append("")

        # ⑥ WIN 거래 상세 (참고용)
        report.append("✅ WIN 거래 상세")
        report.append("-" * 60)
        if win_trades:
            for t in win_trades:
                stock = t['stock_name'][:10]
                profit = f"{t['profit_pct']:+.2f}%"
                pnl = f"+{t['realized_pnl']:,.0f}원"
                hold = f"{t['hold_hours']:.1f}h"
                report.append(f"  {stock}: {profit} ({pnl}) / {hold}")
        else:
            report.append("  WIN 거래 없음")
        report.append("")

        report.append("=" * 60)
        report.append(f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("=" * 60)

        return "\n".join(report)

    def _infer_draw_tag(self, trade: Dict) -> str:
        """Draw 거래 태그 추론"""
        exit_reason = trade.get('exit_reason', '') or ''
        hold_hours = trade.get('hold_hours', 0)
        profit_pct = trade.get('profit_pct', 0)

        # 오버나잇 무반응
        if hold_hours > 15:
            if 'Open Range' in exit_reason or 'ATR 트레일링' in exit_reason:
                return "overnight_no_followthrough"

        # 변동성 압축
        if abs(profit_pct) < 0.1:
            return "volatility_compression"

        # 구조 정체
        if hold_hours > 6 and abs(profit_pct) < 0.2:
            return "structure_stall"

        return "unclassified"

    def save_report(self, report: str, filepath: str = None) -> str:
        """리포트 파일 저장"""
        if not filepath:
            week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y%m%d')
            filepath = f"reports/weekly_report_{week_start}.txt"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)

        return filepath


def generate_current_week_report():
    """현재 주 리포트 생성 (CLI용)"""
    import yaml

    # Config 로드
    try:
        with open('config/strategy_hybrid.yaml', 'r') as f:
            config = yaml.safe_load(f)
    except:
        config = {}

    # risk_log.json 로드
    try:
        with open('data/risk_log.json', 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("❌ data/risk_log.json 파일을 찾을 수 없습니다.")
        return

    # 주간 거래 추출
    weekly_trades = data.get('weekly_trades', [])
    week_start = data.get('week_start', datetime.now().strftime('%Y-%m-%d'))

    # 리포트 생성
    generator = WeeklyReportGenerator(config)
    report = generator.generate_report(weekly_trades, week_start)

    # 출력
    print(report)

    # 파일 저장
    filepath = generator.save_report(report)
    print(f"\n📁 리포트 저장: {filepath}")


if __name__ == "__main__":
    generate_current_week_report()
