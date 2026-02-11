#!/usr/bin/env python3
"""
실전 안전 체크리스트
==================

장 시작 전 / 장 마감 후 점검 자동화

⚠️ 이 체크리스트를 통과해야만 자동 손절 활성화
"""

import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

load_dotenv(project_root / '.env')

console = Console()


@dataclass
class CheckItem:
    """체크 항목"""
    name: str
    passed: bool
    message: str
    critical: bool = False  # True면 실패 시 자동매매 차단


class DailyChecklist:
    """일일 체크리스트"""

    def __init__(self):
        self.checks: List[CheckItem] = []
        self.all_passed = False

    def run_pre_market(self) -> bool:
        """
        장 시작 전 체크리스트

        Returns:
            모든 critical 항목 통과 여부
        """
        console.print()
        console.print(Panel(
            "[bold]장 시작 전 체크리스트[/bold]\n"
            f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            title="🌅 Pre-Market Check",
            border_style="cyan"
        ))

        self.checks = []

        # ─────────────────────────────────────────────────────────
        # 1. API 연결 상태
        # ─────────────────────────────────────────────────────────
        console.print("\n[dim]1. API 연결 확인...[/dim]")
        try:
            from korea_invest_api import KoreaInvestAPI
            api = KoreaInvestAPI()
            token = api.get_access_token()

            if token:
                self.checks.append(CheckItem(
                    name="한투 API 연결",
                    passed=True,
                    message="토큰 발급 성공",
                    critical=True
                ))
            else:
                self.checks.append(CheckItem(
                    name="한투 API 연결",
                    passed=False,
                    message="토큰 발급 실패",
                    critical=True
                ))
        except Exception as e:
            self.checks.append(CheckItem(
                name="한투 API 연결",
                passed=False,
                message=f"오류: {str(e)[:30]}",
                critical=True
            ))

        # ─────────────────────────────────────────────────────────
        # 2. 잔고 조회 가능
        # ─────────────────────────────────────────────────────────
        console.print("[dim]2. 잔고 조회 확인...[/dim]")
        try:
            result = api.get_domestic_balance()
            if result['success']:
                count = len(result['data'])
                self.checks.append(CheckItem(
                    name="잔고 조회",
                    passed=True,
                    message=f"{count}개 종목 확인",
                    critical=True
                ))
            else:
                self.checks.append(CheckItem(
                    name="잔고 조회",
                    passed=False,
                    message=result.get('error', '실패'),
                    critical=True
                ))
        except Exception as e:
            self.checks.append(CheckItem(
                name="잔고 조회",
                passed=False,
                message=f"오류: {str(e)[:30]}",
                critical=True
            ))

        # ─────────────────────────────────────────────────────────
        # 3. STOP_LOSS 대상 확인
        # ─────────────────────────────────────────────────────────
        console.print("[dim]3. STOP_LOSS 대상 확인...[/dim]")
        try:
            from trading.mid_term_engine import MidTermEngine, Action

            engine = MidTermEngine()
            engine.api = api  # 재사용
            engine.fetch_positions()

            # 간단 평가 (시장 데이터 없이)
            from trading.mid_term_engine import evaluate_position, MarketData, STOCK_GROUP_MAP, PositionGroup, Position

            stop_targets = []
            for pos in engine.positions:
                # 수익률 기준 간단 체크
                if pos.profit_pct <= -12:
                    stop_targets.append(pos.stock_name)

            if stop_targets:
                self.checks.append(CheckItem(
                    name="STOP_LOSS 대상",
                    passed=True,  # 정보성
                    message=f"{len(stop_targets)}건: {', '.join(stop_targets)[:30]}",
                    critical=False
                ))
            else:
                self.checks.append(CheckItem(
                    name="STOP_LOSS 대상",
                    passed=True,
                    message="없음 (양호)",
                    critical=False
                ))

        except Exception as e:
            self.checks.append(CheckItem(
                name="STOP_LOSS 대상",
                passed=False,
                message=f"오류: {str(e)[:30]}",
                critical=False
            ))

        # ─────────────────────────────────────────────────────────
        # 4. 오늘 손절 실행 기록
        # ─────────────────────────────────────────────────────────
        console.print("[dim]4. 오늘 손절 기록 확인...[/dim]")
        from datetime import date
        import json

        log_file = project_root / 'logs' / f"stop_loss_{date.today().strftime('%Y%m%d')}.json"
        if log_file.exists():
            try:
                with open(log_file, 'r') as f:
                    logs = json.load(f)
                executed = [l for l in logs if l.get('status') == 'executed']
                self.checks.append(CheckItem(
                    name="오늘 손절 기록",
                    passed=True,
                    message=f"{len(executed)}건 실행됨",
                    critical=False
                ))
            except:
                self.checks.append(CheckItem(
                    name="오늘 손절 기록",
                    passed=True,
                    message="파일 읽기 오류",
                    critical=False
                ))
        else:
            self.checks.append(CheckItem(
                name="오늘 손절 기록",
                passed=True,
                message="없음 (첫 실행)",
                critical=False
            ))

        # ─────────────────────────────────────────────────────────
        # 5. 시장 시간 확인
        # ─────────────────────────────────────────────────────────
        console.print("[dim]5. 시장 시간 확인...[/dim]")
        now = datetime.now()
        market_open = dtime(9, 0)
        market_close = dtime(15, 30)

        if now.weekday() >= 5:
            self.checks.append(CheckItem(
                name="시장 시간",
                passed=False,
                message="주말 휴장",
                critical=True
            ))
        elif market_open <= now.time() <= market_close:
            self.checks.append(CheckItem(
                name="시장 시간",
                passed=True,
                message="장중",
                critical=False
            ))
        elif now.time() < market_open:
            self.checks.append(CheckItem(
                name="시장 시간",
                passed=True,
                message=f"장 시작 전 (09:00 개장)",
                critical=False
            ))
        else:
            self.checks.append(CheckItem(
                name="시장 시간",
                passed=False,
                message="장 마감",
                critical=True
            ))

        # ─────────────────────────────────────────────────────────
        # 결과 표시
        # ─────────────────────────────────────────────────────────
        self._display_results()

        # Critical 항목 통과 여부
        critical_failed = [c for c in self.checks if c.critical and not c.passed]
        self.all_passed = len(critical_failed) == 0

        if self.all_passed:
            console.print("\n[green]✅ 모든 필수 항목 통과[/green]")
            console.print("[dim]AUTO_STOP_ENABLED = True 설정 가능[/dim]")
        else:
            console.print("\n[red]❌ 필수 항목 실패[/red]")
            for c in critical_failed:
                console.print(f"   - {c.name}: {c.message}")
            console.print("[yellow]⚠️ 자동 손절 비활성화 권장[/yellow]")

        return self.all_passed

    def run_post_market(self) -> bool:
        """
        장 마감 후 체크리스트

        Returns:
            모든 항목 정상 여부
        """
        console.print()
        console.print(Panel(
            "[bold]장 마감 후 체크리스트[/bold]\n"
            f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            title="🌙 Post-Market Check",
            border_style="blue"
        ))

        self.checks = []

        # ─────────────────────────────────────────────────────────
        # 1. 오늘 실행된 주문 확인
        # ─────────────────────────────────────────────────────────
        console.print("\n[dim]1. 오늘 주문 실행 확인...[/dim]")
        from datetime import date
        import json

        log_file = project_root / 'logs' / f"stop_loss_{date.today().strftime('%Y%m%d')}.json"
        if log_file.exists():
            try:
                with open(log_file, 'r') as f:
                    logs = json.load(f)

                executed = [l for l in logs if l.get('status') == 'executed']
                simulated = [l for l in logs if l.get('status') == 'simulated']
                failed = [l for l in logs if l.get('status') == 'failed']

                self.checks.append(CheckItem(
                    name="손절 실행",
                    passed=len(failed) == 0,
                    message=f"실행:{len(executed)} 시뮬:{len(simulated)} 실패:{len(failed)}",
                    critical=False
                ))

                if failed:
                    for f in failed:
                        console.print(f"   [red]실패: {f['stock_name']} - {f.get('reason', '')}[/red]")

            except Exception as e:
                self.checks.append(CheckItem(
                    name="손절 실행",
                    passed=False,
                    message=f"로그 읽기 오류: {e}",
                    critical=False
                ))
        else:
            self.checks.append(CheckItem(
                name="손절 실행",
                passed=True,
                message="오늘 실행 기록 없음",
                critical=False
            ))

        # ─────────────────────────────────────────────────────────
        # 2. 잔고 변화 확인
        # ─────────────────────────────────────────────────────────
        console.print("[dim]2. 현재 잔고 확인...[/dim]")
        try:
            from korea_invest_api import KoreaInvestAPI
            api = KoreaInvestAPI()
            api.get_access_token()

            result = api.get_domestic_balance()
            if result['success']:
                holdings = result['data']
                total_eval = sum(float(h.get('evlu_amt', 0)) for h in holdings)
                total_profit = sum(float(h.get('evlu_pfls_amt', 0)) for h in holdings)

                self.checks.append(CheckItem(
                    name="현재 잔고",
                    passed=True,
                    message=f"{len(holdings)}종목 / 평가:{total_eval:,.0f}원",
                    critical=False
                ))
            else:
                self.checks.append(CheckItem(
                    name="현재 잔고",
                    passed=False,
                    message=result.get('error', '조회 실패'),
                    critical=False
                ))
        except Exception as e:
            self.checks.append(CheckItem(
                name="현재 잔고",
                passed=False,
                message=f"오류: {str(e)[:30]}",
                critical=False
            ))

        # ─────────────────────────────────────────────────────────
        # 3. 내일 STOP_LOSS 후보
        # ─────────────────────────────────────────────────────────
        console.print("[dim]3. 내일 STOP_LOSS 후보 확인...[/dim]")
        try:
            from trading.mid_term_engine import MidTermEngine

            engine = MidTermEngine()
            engine.api = api
            engine.fetch_positions()

            # -10% ~ -12% 구간 (내일 손절 가능성)
            warning_zone = [p for p in engine.positions if -12 < p.profit_pct <= -10]
            stop_zone = [p for p in engine.positions if p.profit_pct <= -12]

            msg_parts = []
            if stop_zone:
                msg_parts.append(f"손절대상:{len(stop_zone)}")
            if warning_zone:
                msg_parts.append(f"경고구간:{len(warning_zone)}")

            if msg_parts:
                self.checks.append(CheckItem(
                    name="내일 주의 종목",
                    passed=True,
                    message=" / ".join(msg_parts),
                    critical=False
                ))
            else:
                self.checks.append(CheckItem(
                    name="내일 주의 종목",
                    passed=True,
                    message="없음 (양호)",
                    critical=False
                ))

        except Exception as e:
            self.checks.append(CheckItem(
                name="내일 주의 종목",
                passed=False,
                message=f"오류: {str(e)[:30]}",
                critical=False
            ))

        # ─────────────────────────────────────────────────────────
        # 결과 표시
        # ─────────────────────────────────────────────────────────
        self._display_results()

        failed = [c for c in self.checks if not c.passed]
        self.all_passed = len(failed) == 0

        if self.all_passed:
            console.print("\n[green]✅ 모든 항목 정상[/green]")
        else:
            console.print(f"\n[yellow]⚠️ {len(failed)}개 항목 확인 필요[/yellow]")

        return self.all_passed

    def _display_results(self):
        """결과 테이블 표시"""
        table = Table(box=None, show_header=True, header_style="dim")
        table.add_column("항목", width=20)
        table.add_column("상태", width=6, justify="center")
        table.add_column("내용", width=35)

        for c in self.checks:
            status = "[green]✅[/green]" if c.passed else "[red]❌[/red]"
            critical = "[red]*[/red]" if c.critical else " "
            table.add_row(
                f"{critical}{c.name}",
                status,
                c.message
            )

        console.print()
        console.print(table)
        console.print("[dim]* = 필수 항목[/dim]")


# ============================================================================
# 실행
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description='일일 체크리스트')
    parser.add_argument('--pre', action='store_true', help='장 시작 전 체크')
    parser.add_argument('--post', action='store_true', help='장 마감 후 체크')

    args = parser.parse_args()

    checklist = DailyChecklist()

    if args.post:
        checklist.run_post_market()
    else:
        # 기본값: 장 시작 전
        checklist.run_pre_market()


if __name__ == "__main__":
    main()
