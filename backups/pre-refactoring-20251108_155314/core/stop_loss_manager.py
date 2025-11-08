#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stop_loss_manager.py

손절 관리 시스템 - 사용자 친화적인 손절 규칙 관리
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

# Rich for beautiful output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm, FloatPrompt, IntPrompt
    from rich.progress import Progress
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from utils.logger import get_logger
from .auto_stop_loss_system import AutoStopLossSystem

class StopLossManager:
    """손절 관리 시스템"""

    def __init__(self, trading_handler=None, config=None):
        """손절 관리 시스템 초기화"""
        self.logger = get_logger("StopLossManager")
        self.console = Console() if RICH_AVAILABLE else None
        self.trading_handler = trading_handler
        self.config = config

        # 자동 손절 시스템
        self.auto_stop_loss = AutoStopLossSystem(config, trading_handler)

    async def show_main_menu(self):
        """메인 메뉴 표시"""
        try:
            while True:
                if self.console:
                    self.console.clear()
                    self.console.print(Panel.fit(
                        "🛡️ 자동 손절 관리 시스템\n"
                        "보유 종목의 손절 규칙을 설정하고 관리합니다.",
                        style="bold blue"
                    ))

                    # 현재 상태 표시
                    await self._display_current_status()

                    # 메뉴 선택
                    choices = [
                        "1. 현재 보유 종목 보기",
                        "2. 손절 규칙 추가",
                        "3. 손절 규칙 관리",
                        "4. 자동 모니터링 시작/중지",
                        "5. 손절 실행 기록 보기",
                        "6. 일괄 손절 규칙 설정",
                        "0. 돌아가기"
                    ]

                    for choice in choices:
                        self.console.print(f"  {choice}")

                    selection = Prompt.ask("\n선택", choices=["0", "1", "2", "3", "4", "5", "6"], default="0")

                    if selection == "0":
                        break
                    elif selection == "1":
                        await self._show_current_holdings()
                    elif selection == "2":
                        await self._add_stop_loss_rule()
                    elif selection == "3":
                        await self._manage_stop_loss_rules()
                    elif selection == "4":
                        await self._toggle_monitoring()
                    elif selection == "5":
                        await self._show_execution_history()
                    elif selection == "6":
                        await self._batch_set_stop_loss()

                else:
                    # 텍스트 기반 메뉴
                    print("\n🛡️ 자동 손절 관리 시스템")
                    print("1. 현재 보유 종목 보기")
                    print("2. 손절 규칙 추가")
                    print("3. 손절 규칙 관리")
                    print("4. 자동 모니터링 시작/중지")
                    print("5. 손절 실행 기록 보기")
                    print("6. 일괄 손절 규칙 설정")
                    print("0. 돌아가기")

                    selection = input("\n선택: ").strip()

                    if selection == "0":
                        break
                    elif selection == "1":
                        await self._show_current_holdings()
                    elif selection == "2":
                        await self._add_stop_loss_rule()
                    elif selection == "3":
                        await self._manage_stop_loss_rules()
                    elif selection == "4":
                        await self._toggle_monitoring()
                    elif selection == "5":
                        await self._show_execution_history()
                    elif selection == "6":
                        await self._batch_set_stop_loss()

        except KeyboardInterrupt:
            if self.console:
                self.console.print("\n[yellow]👋 손절 관리를 종료합니다.[/yellow]")
            else:
                print("\n👋 손절 관리를 종료합니다.")
        except Exception as e:
            self.logger.error(f"❌ 메인 메뉴 오류: {e}")

    async def _display_current_status(self):
        """현재 상태 표시"""
        try:
            summary = await self.auto_stop_loss.get_status_summary()

            if self.console:
                status_text = "🟢 모니터링 활성" if summary.get('monitoring_enabled') else "🔴 모니터링 비활성"

                status_table = Table(title="현재 상태")
                status_table.add_column("항목", style="cyan")
                status_table.add_column("값", style="magenta")

                status_table.add_row("모니터링 상태", status_text)
                status_table.add_row("등록된 규칙", f"{summary.get('total_rules', 0)}개")
                status_table.add_row("활성 규칙", f"{summary.get('active_rules', 0)}개")
                status_table.add_row("총 실행", f"{summary.get('total_executions', 0)}회")
                status_table.add_row("성공률", f"{summary.get('success_rate', 0):.1f}%")

                self.console.print(status_table)

        except Exception as e:
            self.logger.error(f"❌ 상태 표시 실패: {e}")

    async def _show_current_holdings(self):
        """현재 보유 종목 표시"""
        try:
            if self.console:
                self.console.print("\n[cyan]📊 현재 보유 종목 조회 중...[/cyan]")

            # 보유 종목 조회
            holdings = await self._get_current_holdings()

            if not holdings:
                if self.console:
                    self.console.print("[yellow]⚠️ 현재 보유 중인 종목이 없습니다.[/yellow]")
                else:
                    print("⚠️ 현재 보유 중인 종목이 없습니다.")

                if self.console:
                    Prompt.ask("\nEnter를 눌러 계속")
                else:
                    input("\nEnter를 눌러 계속...")
                return

            if self.console:
                holdings_table = Table(title="현재 보유 종목")
                holdings_table.add_column("종목", style="cyan")
                holdings_table.add_column("수량", style="magenta", justify="right")
                holdings_table.add_column("평균단가", style="yellow", justify="right")
                holdings_table.add_column("현재가", style="yellow", justify="right")
                holdings_table.add_column("평가손익", style="bold", justify="right")
                holdings_table.add_column("수익률", style="bold", justify="right")
                holdings_table.add_column("손절규칙", style="green")

                for holding in holdings:
                    stock_code = holding.get('stock_code', '')
                    stock_name = holding.get('stock_name', stock_code)
                    quantity = holding.get('quantity', 0)
                    avg_price = holding.get('avg_price', 0)
                    current_price = holding.get('current_price', 0)

                    # 수익률 계산
                    if avg_price > 0:
                        pnl = (current_price - avg_price) * quantity
                        pnl_pct = (current_price - avg_price) / avg_price * 100
                        pnl_style = "green" if pnl >= 0 else "red"
                    else:
                        pnl = 0
                        pnl_pct = 0
                        pnl_style = "dim"

                    # 손절 규칙 확인
                    has_rule = stock_code in self.auto_stop_loss.stop_loss_rules
                    rule_text = "✅ 설정됨" if has_rule else "❌ 미설정"

                    holdings_table.add_row(
                        f"{stock_name}\n({stock_code})",
                        f"{quantity:,}주",
                        f"₩{avg_price:,.0f}",
                        f"₩{current_price:,.0f}",
                        f"[{pnl_style}]₩{pnl:,.0f}[/{pnl_style}]",
                        f"[{pnl_style}]{pnl_pct:+.2f}%[/{pnl_style}]",
                        rule_text
                    )

                self.console.print(holdings_table)

                Prompt.ask("\nEnter를 눌러 계속")

            else:
                # 텍스트 표시
                print("\n📊 현재 보유 종목")
                print("=" * 80)
                for holding in holdings:
                    stock_code = holding.get('stock_code', '')
                    stock_name = holding.get('stock_name', stock_code)
                    quantity = holding.get('quantity', 0)
                    avg_price = holding.get('avg_price', 0)
                    current_price = holding.get('current_price', 0)

                    print(f"\n종목: {stock_name}({stock_code})")
                    print(f"수량: {quantity:,}주")
                    print(f"평균단가: ₩{avg_price:,.0f}")
                    print(f"현재가: ₩{current_price:,.0f}")

                    if avg_price > 0:
                        pnl = (current_price - avg_price) * quantity
                        pnl_pct = (current_price - avg_price) / avg_price * 100
                        print(f"평가손익: ₩{pnl:,.0f} ({pnl_pct:+.2f}%)")

                    has_rule = stock_code in self.auto_stop_loss.stop_loss_rules
                    print(f"손절규칙: {'설정됨' if has_rule else '미설정'}")

                input("\nEnter를 눌러 계속...")

        except Exception as e:
            self.logger.error(f"❌ 보유 종목 표시 실패: {e}")

    async def _add_stop_loss_rule(self):
        """손절 규칙 추가"""
        try:
            if self.console:
                self.console.print("\n[cyan]➕ 손절 규칙 추가[/cyan]")

            # 보유 종목 조회
            holdings = await self._get_current_holdings()
            if not holdings:
                if self.console:
                    self.console.print("[yellow]⚠️ 현재 보유 중인 종목이 없습니다.[/yellow]")
                    Prompt.ask("\nEnter를 눌러 계속")
                else:
                    print("⚠️ 현재 보유 중인 종목이 없습니다.")
                    input("\nEnter를 눌러 계속...")
                return

            # 손절 규칙이 없는 종목만 표시
            available_stocks = []
            for holding in holdings:
                stock_code = holding.get('stock_code', '')
                if stock_code not in self.auto_stop_loss.stop_loss_rules:
                    available_stocks.append(holding)

            if not available_stocks:
                if self.console:
                    self.console.print("[yellow]⚠️ 모든 보유 종목에 손절 규칙이 이미 설정되어 있습니다.[/yellow]")
                    Prompt.ask("\nEnter를 눌러 계속")
                else:
                    print("⚠️ 모든 보유 종목에 손절 규칙이 이미 설정되어 있습니다.")
                    input("\nEnter를 눌러 계속...")
                return

            # 종목 선택
            if self.console:
                self.console.print("\n손절 규칙을 추가할 종목을 선택하세요:")

                stock_choices = {}
                for i, holding in enumerate(available_stocks, 1):
                    stock_code = holding.get('stock_code', '')
                    stock_name = holding.get('stock_name', stock_code)
                    quantity = holding.get('quantity', 0)
                    avg_price = holding.get('avg_price', 0)

                    choice_key = str(i)
                    stock_choices[choice_key] = holding

                    self.console.print(f"  {i}. {stock_name}({stock_code}) - {quantity:,}주 @ ₩{avg_price:,.0f}")

                stock_choices["0"] = None
                self.console.print("  0. 취소")

                selected = Prompt.ask("\n종목 선택", choices=list(stock_choices.keys()), default="0")

                if selected == "0":
                    return

                selected_holding = stock_choices[selected]

            else:
                # 텍스트 기반 선택
                print("\n손절 규칙을 추가할 종목을 선택하세요:")
                for i, holding in enumerate(available_stocks, 1):
                    stock_code = holding.get('stock_code', '')
                    stock_name = holding.get('stock_name', stock_code)
                    quantity = holding.get('quantity', 0)
                    avg_price = holding.get('avg_price', 0)
                    print(f"  {i}. {stock_name}({stock_code}) - {quantity:,}주 @ ₩{avg_price:,.0f}")

                print("  0. 취소")

                try:
                    choice = int(input("\n종목 선택: ").strip())
                    if choice == 0:
                        return
                    if 1 <= choice <= len(available_stocks):
                        selected_holding = available_stocks[choice - 1]
                    else:
                        print("❌ 잘못된 선택입니다.")
                        return
                except ValueError:
                    print("❌ 숫자를 입력해주세요.")
                    return

            # 손절 비율 설정
            stock_code = selected_holding.get('stock_code', '')
            stock_name = selected_holding.get('stock_name', stock_code)
            avg_price = selected_holding.get('avg_price', 0)
            quantity = selected_holding.get('quantity', 0)

            if self.console:
                self.console.print(f"\n[bold]{stock_name}({stock_code})[/bold] 손절 규칙 설정")
                self.console.print(f"매수가: ₩{avg_price:,.0f}")
                self.console.print(f"수량: {quantity:,}주")

                stop_loss_pct = FloatPrompt.ask(
                    "\n손절 비율을 입력하세요 (예: -5.0 = 5% 손절)",
                    default=-5.0
                )

            else:
                print(f"\n{stock_name}({stock_code}) 손절 규칙 설정")
                print(f"매수가: ₩{avg_price:,.0f}")
                print(f"수량: {quantity:,}주")

                try:
                    stop_loss_pct = float(input("\n손절 비율을 입력하세요 (예: -5.0 = 5% 손절) [기본값: -5.0]: ") or "-5.0")
                except ValueError:
                    print("❌ 올바른 숫자를 입력해주세요.")
                    return

            # 손절가 계산 및 확인
            stop_loss_price = avg_price * (1 + stop_loss_pct / 100)

            if self.console:
                self.console.print(f"\n[yellow]확인:[/yellow]")
                self.console.print(f"종목: {stock_name}({stock_code})")
                self.console.print(f"매수가: ₩{avg_price:,.0f}")
                self.console.print(f"손절가: ₩{stop_loss_price:,.0f} ({stop_loss_pct:.1f}%)")
                self.console.print(f"예상 손실: ₩{(stop_loss_price - avg_price) * quantity:,.0f}")

                if Confirm.ask("\n손절 규칙을 추가하시겠습니까?"):
                    success = await self.auto_stop_loss.add_stop_loss_rule(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        purchase_price=avg_price,
                        quantity=quantity,
                        stop_loss_pct=stop_loss_pct
                    )

                    if success:
                        self.console.print("[green]✅ 손절 규칙이 추가되었습니다.[/green]")
                    else:
                        self.console.print("[red]❌ 손절 규칙 추가에 실패했습니다.[/red]")

                Prompt.ask("\nEnter를 눌러 계속")

            else:
                print(f"\n확인:")
                print(f"종목: {stock_name}({stock_code})")
                print(f"매수가: ₩{avg_price:,.0f}")
                print(f"손절가: ₩{stop_loss_price:,.0f} ({stop_loss_pct:.1f}%)")
                print(f"예상 손실: ₩{(stop_loss_price - avg_price) * quantity:,.0f}")

                confirm = input("\n손절 규칙을 추가하시겠습니까? (y/N): ").strip().lower()
                if confirm in ['y', 'yes']:
                    success = await self.auto_stop_loss.add_stop_loss_rule(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        purchase_price=avg_price,
                        quantity=quantity,
                        stop_loss_pct=stop_loss_pct
                    )

                    if success:
                        print("✅ 손절 규칙이 추가되었습니다.")
                    else:
                        print("❌ 손절 규칙 추가에 실패했습니다.")

                input("\nEnter를 눌러 계속...")

        except Exception as e:
            self.logger.error(f"❌ 손절 규칙 추가 실패: {e}")

    async def _manage_stop_loss_rules(self):
        """손절 규칙 관리"""
        try:
            if not self.auto_stop_loss.stop_loss_rules:
                if self.console:
                    self.console.print("[yellow]⚠️ 설정된 손절 규칙이 없습니다.[/yellow]")
                    Prompt.ask("\nEnter를 눌러 계속")
                else:
                    print("⚠️ 설정된 손절 규칙이 없습니다.")
                    input("\nEnter를 눌러 계속...")
                return

            if self.console:
                await self.auto_stop_loss.display_status()

                # 규칙 삭제 옵션
                if Confirm.ask("\n손절 규칙을 삭제하시겠습니까?"):
                    # 삭제할 규칙 선택
                    rule_choices = {}
                    rules = list(self.auto_stop_loss.stop_loss_rules.values())

                    self.console.print("\n삭제할 손절 규칙을 선택하세요:")
                    for i, rule in enumerate(rules, 1):
                        choice_key = str(i)
                        rule_choices[choice_key] = rule.stock_code
                        self.console.print(f"  {i}. {rule.stock_name}({rule.stock_code})")

                    rule_choices["0"] = None
                    self.console.print("  0. 취소")

                    selected = Prompt.ask("\n규칙 선택", choices=list(rule_choices.keys()), default="0")

                    if selected != "0":
                        stock_code = rule_choices[selected]
                        if Confirm.ask(f"\n정말로 {stock_code} 손절 규칙을 삭제하시겠습니까?"):
                            success = await self.auto_stop_loss.remove_stop_loss_rule(stock_code)
                            if success:
                                self.console.print("[green]✅ 손절 규칙이 삭제되었습니다.[/green]")
                            else:
                                self.console.print("[red]❌ 손절 규칙 삭제에 실패했습니다.[/red]")

                Prompt.ask("\nEnter를 눌러 계속")

        except Exception as e:
            self.logger.error(f"❌ 손절 규칙 관리 실패: {e}")

    async def _toggle_monitoring(self):
        """모니터링 시작/중지"""
        try:
            summary = await self.auto_stop_loss.get_status_summary()
            is_monitoring = summary.get('monitoring_enabled', False)

            if is_monitoring:
                if self.console:
                    if Confirm.ask("모니터링을 중지하시겠습니까?"):
                        await self.auto_stop_loss.stop_monitoring()
                        self.console.print("[yellow]⏹️ 모니터링이 중지되었습니다.[/yellow]")
                else:
                    confirm = input("모니터링을 중지하시겠습니까? (y/N): ").strip().lower()
                    if confirm in ['y', 'yes']:
                        await self.auto_stop_loss.stop_monitoring()
                        print("⏹️ 모니터링이 중지되었습니다.")
            else:
                if not self.auto_stop_loss.stop_loss_rules:
                    if self.console:
                        self.console.print("[yellow]⚠️ 손절 규칙이 없습니다. 먼저 손절 규칙을 추가해주세요.[/yellow]")
                    else:
                        print("⚠️ 손절 규칙이 없습니다. 먼저 손절 규칙을 추가해주세요.")
                else:
                    if self.console:
                        if Confirm.ask("모니터링을 시작하시겠습니까?"):
                            await self.auto_stop_loss.start_monitoring()
                            self.console.print("[green]🚀 모니터링이 시작되었습니다.[/green]")
                    else:
                        confirm = input("모니터링을 시작하시겠습니까? (y/N): ").strip().lower()
                        if confirm in ['y', 'yes']:
                            await self.auto_stop_loss.start_monitoring()
                            print("🚀 모니터링이 시작되었습니다.")

            if self.console:
                Prompt.ask("\nEnter를 눌러 계속")
            else:
                input("\nEnter를 눌러 계속...")

        except Exception as e:
            self.logger.error(f"❌ 모니터링 토글 실패: {e}")

    async def _show_execution_history(self):
        """손절 실행 기록 보기"""
        try:
            executions = self.auto_stop_loss.executions

            if not executions:
                if self.console:
                    self.console.print("[yellow]⚠️ 손절 실행 기록이 없습니다.[/yellow]")
                    Prompt.ask("\nEnter를 눌러 계속")
                else:
                    print("⚠️ 손절 실행 기록이 없습니다.")
                    input("\nEnter를 눌러 계속...")
                return

            if self.console:
                history_table = Table(title="손절 실행 기록")
                history_table.add_column("실행시간", style="cyan")
                history_table.add_column("종목", style="magenta")
                history_table.add_column("수량", style="yellow", justify="right")
                history_table.add_column("실행가격", style="yellow", justify="right")
                history_table.add_column("실행금액", style="yellow", justify="right")
                history_table.add_column("상태", style="bold")

                for execution in executions[-20:]:  # 최근 20개만 표시
                    status_style = "green" if execution.status == "FILLED" else "red"

                    history_table.add_row(
                        execution.executed_at.strftime("%m-%d %H:%M"),
                        f"{execution.stock_name}\n({execution.stock_code})",
                        f"{execution.quantity:,}주",
                        f"₩{execution.execution_price:,.0f}" if execution.execution_price else "-",
                        f"₩{execution.execution_amount:,.0f}" if execution.execution_amount else "-",
                        f"[{status_style}]{execution.status}[/{status_style}]"
                    )

                self.console.print(history_table)
                Prompt.ask("\nEnter를 눌러 계속")

        except Exception as e:
            self.logger.error(f"❌ 실행 기록 표시 실패: {e}")

    async def _batch_set_stop_loss(self):
        """일괄 손절 규칙 설정"""
        try:
            # 보유 종목 조회
            holdings = await self._get_current_holdings()
            if not holdings:
                if self.console:
                    self.console.print("[yellow]⚠️ 현재 보유 중인 종목이 없습니다.[/yellow]")
                    Prompt.ask("\nEnter를 눌러 계속")
                else:
                    print("⚠️ 현재 보유 중인 종목이 없습니다.")
                    input("\nEnter를 눌러 계속...")
                return

            # 손절 규칙이 없는 종목만 필터링
            available_stocks = []
            for holding in holdings:
                stock_code = holding.get('stock_code', '')
                if stock_code not in self.auto_stop_loss.stop_loss_rules:
                    available_stocks.append(holding)

            if not available_stocks:
                if self.console:
                    self.console.print("[yellow]⚠️ 모든 보유 종목에 손절 규칙이 이미 설정되어 있습니다.[/yellow]")
                    Prompt.ask("\nEnter를 눌러 계속")
                else:
                    print("⚠️ 모든 보유 종목에 손절 규칙이 이미 설정되어 있습니다.")
                    input("\nEnter를 눌러 계속...")
                return

            # 일괄 손절 비율 설정
            if self.console:
                self.console.print(f"\n[cyan]📋 일괄 손절 규칙 설정 - {len(available_stocks)}개 종목[/cyan]")

                batch_stop_loss_pct = FloatPrompt.ask(
                    "\n모든 종목에 적용할 손절 비율을 입력하세요 (예: -5.0 = 5% 손절)",
                    default=-5.0
                )

                # 확인 표시
                self.console.print(f"\n[yellow]적용 예정 종목:[/yellow]")

                batch_table = Table()
                batch_table.add_column("종목", style="cyan")
                batch_table.add_column("매수가", style="yellow", justify="right")
                batch_table.add_column("손절가", style="red", justify="right")
                batch_table.add_column("예상손실", style="red", justify="right")

                for holding in available_stocks:
                    stock_code = holding.get('stock_code', '')
                    stock_name = holding.get('stock_name', stock_code)
                    avg_price = holding.get('avg_price', 0)
                    quantity = holding.get('quantity', 0)

                    stop_loss_price = avg_price * (1 + batch_stop_loss_pct / 100)
                    expected_loss = (stop_loss_price - avg_price) * quantity

                    batch_table.add_row(
                        f"{stock_name}\n({stock_code})",
                        f"₩{avg_price:,.0f}",
                        f"₩{stop_loss_price:,.0f}",
                        f"₩{expected_loss:,.0f}"
                    )

                self.console.print(batch_table)

                if Confirm.ask(f"\n{len(available_stocks)}개 종목에 {batch_stop_loss_pct:.1f}% 손절 규칙을 일괄 설정하시겠습니까?"):
                    # 일괄 적용
                    with Progress() as progress:
                        task = progress.add_task("손절 규칙 설정 중...", total=len(available_stocks))

                        success_count = 0
                        for holding in available_stocks:
                            stock_code = holding.get('stock_code', '')
                            stock_name = holding.get('stock_name', stock_code)
                            avg_price = holding.get('avg_price', 0)
                            quantity = holding.get('quantity', 0)

                            success = await self.auto_stop_loss.add_stop_loss_rule(
                                stock_code=stock_code,
                                stock_name=stock_name,
                                purchase_price=avg_price,
                                quantity=quantity,
                                stop_loss_pct=batch_stop_loss_pct
                            )

                            if success:
                                success_count += 1

                            progress.advance(task)

                    self.console.print(f"[green]✅ {success_count}/{len(available_stocks)}개 종목에 손절 규칙이 설정되었습니다.[/green]")

                Prompt.ask("\nEnter를 눌러 계속")

            else:
                # 텍스트 기반 일괄 설정
                print(f"\n📋 일괄 손절 규칙 설정 - {len(available_stocks)}개 종목")

                try:
                    batch_stop_loss_pct = float(input("\n모든 종목에 적용할 손절 비율을 입력하세요 (예: -5.0 = 5% 손절) [기본값: -5.0]: ") or "-5.0")
                except ValueError:
                    print("❌ 올바른 숫자를 입력해주세요.")
                    return

                # 확인
                print(f"\n적용 예정 종목:")
                for holding in available_stocks:
                    stock_code = holding.get('stock_code', '')
                    stock_name = holding.get('stock_name', stock_code)
                    avg_price = holding.get('avg_price', 0)
                    quantity = holding.get('quantity', 0)

                    stop_loss_price = avg_price * (1 + batch_stop_loss_pct / 100)
                    expected_loss = (stop_loss_price - avg_price) * quantity

                    print(f"- {stock_name}({stock_code}): ₩{avg_price:,.0f} → ₩{stop_loss_price:,.0f} (예상손실: ₩{expected_loss:,.0f})")

                confirm = input(f"\n{len(available_stocks)}개 종목에 {batch_stop_loss_pct:.1f}% 손절 규칙을 일괄 설정하시겠습니까? (y/N): ").strip().lower()

                if confirm in ['y', 'yes']:
                    success_count = 0
                    for i, holding in enumerate(available_stocks, 1):
                        stock_code = holding.get('stock_code', '')
                        stock_name = holding.get('stock_name', stock_code)
                        avg_price = holding.get('avg_price', 0)
                        quantity = holding.get('quantity', 0)

                        print(f"처리 중... {i}/{len(available_stocks)}")

                        success = await self.auto_stop_loss.add_stop_loss_rule(
                            stock_code=stock_code,
                            stock_name=stock_name,
                            purchase_price=avg_price,
                            quantity=quantity,
                            stop_loss_pct=batch_stop_loss_pct
                        )

                        if success:
                            success_count += 1

                    print(f"✅ {success_count}/{len(available_stocks)}개 종목에 손절 규칙이 설정되었습니다.")

                input("\nEnter를 눌러 계속...")

        except Exception as e:
            self.logger.error(f"❌ 일괄 손절 설정 실패: {e}")

    async def _get_current_holdings(self) -> List[Dict[str, Any]]:
        """현재 보유 종목 조회"""
        try:
            if self.trading_handler:
                response = await self.trading_handler.get_balance()
                holdings = response.get('holdings', [])

                # 보유 수량이 있는 종목만 필터링
                return [h for h in holdings if h.get('quantity', 0) > 0]

            # 데모 데이터
            return [
                {
                    'stock_code': '005930',
                    'stock_name': '삼성전자',
                    'quantity': 100,
                    'avg_price': 75000,
                    'current_price': 72000
                },
                {
                    'stock_code': '000660',
                    'stock_name': 'SK하이닉스',
                    'quantity': 50,
                    'avg_price': 120000,
                    'current_price': 115000
                }
            ]

        except Exception as e:
            self.logger.error(f"❌ 보유 종목 조회 실패: {e}")
            return []

# 사용 예시
async def main():
    """테스트 함수"""
    try:
        # 손절 관리 시스템 초기화
        stop_loss_manager = StopLossManager()

        # 메인 메뉴 실행
        await stop_loss_manager.show_main_menu()

    except KeyboardInterrupt:
        print("\n👋 손절 관리를 종료합니다.")
    except Exception as e:
        print(f"❌ 실행 중 오류 발생: {e}")

if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 테스트 실행
    asyncio.run(main())