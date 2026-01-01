#!/usr/bin/env python3
"""
자동 재시작 로직 테스트

실제 코드와 동일한 구조로 짧은 시간으로 테스트
"""

import asyncio
from datetime import datetime, timedelta
from rich.console import Console

console = Console()


class AutoRestartTester:
    def __init__(self):
        self.running = True
        self.cycle_count = 0

    async def daily_routine(self):
        """실제 거래 로직 시뮬레이션 (5초 실행)"""
        self.cycle_count += 1
        console.print(f"\n[green]{'='*60}[/green]")
        console.print(f"[green]🔄 사이클 #{self.cycle_count} 시작[/green]")
        console.print(f"[green]{'='*60}[/green]\n")

        console.print(f"[cyan]⏰ {datetime.now().strftime('%H:%M:%S')} - 거래 루틴 실행 중...[/cyan]")

        # 5초 동안 "거래" 시뮬레이션
        for i in range(5):
            await asyncio.sleep(1)
            console.print(f"[dim]  ├─ {i+1}초 경과...[/dim]")

        console.print(f"[green]✅ {datetime.now().strftime('%H:%M:%S')} - 거래 루틴 완료[/green]")

    async def run_with_auto_restart(self):
        """자동 재시작 로직 (실제 코드와 동일한 구조)"""
        console.print("\n[bold cyan]╔══════════════════════════════════════════════════════════╗[/bold cyan]")
        console.print("[bold cyan]║          자동 재시작 로직 테스트                        ║[/bold cyan]")
        console.print("[bold cyan]║  - 5초 거래 → 30초 대기 → 자동 재시작 반복             ║[/bold cyan]")
        console.print("[bold cyan]║  - Ctrl+C로 중지                                         ║[/bold cyan]")
        console.print("[bold cyan]╚══════════════════════════════════════════════════════════╝[/bold cyan]\n")

        try:
            while self.running:
                # ===== 1. 일일 루틴 실행 =====
                await self.daily_routine()

                # 종료 신호 확인
                if not self.running:
                    break

                # ===== 2. 다음 실행까지 대기 (실제: 내일 08:50, 테스트: 30초 후) =====
                console.print()
                console.print("[green]✅ 오늘 거래 종료[/green]")
                console.print("[cyan]💤 다음 실행까지 30초 대기합니다...[/cyan]")
                console.print()

                # 다음 실행 시각 계산 (테스트: 현재 + 30초)
                now = datetime.now()
                next_run = now + timedelta(seconds=30)

                wait_seconds = (next_run - now).total_seconds()
                console.print(f"[dim]다음 실행 시각: {next_run.strftime('%H:%M:%S')} (약 {wait_seconds:.0f}초 후)[/dim]")
                console.print()

                # ===== 3. 루프 대기 (실제: 1시간 단위, 테스트: 5초 단위) =====
                console.print("[yellow]🔍 대기 루프 시작 (5초마다 체크)[/yellow]")

                loop_count = 0
                while self.running and datetime.now() < next_run:
                    # 🔴 핵심: 남은 시간을 매번 재계산!
                    remaining_seconds = (next_run - datetime.now()).total_seconds()

                    if remaining_seconds <= 0:
                        console.print("[green]  └─ ✅ 대기 시간 종료 (remaining <= 0)[/green]")
                        break

                    loop_count += 1
                    sleep_time = min(5, remaining_seconds)  # 최대 5초씩 체크 (실제: 3600초)

                    console.print(f"[dim]  ├─ 루프 #{loop_count}: 남은 시간 {remaining_seconds:.1f}초, {sleep_time:.1f}초 대기...[/dim]")

                    await asyncio.sleep(sleep_time)

                    if not self.running:
                        console.print("[yellow]  └─ ⚠️  종료 신호 감지[/yellow]")
                        break

                # ===== 4. 대기 완료 후 다시 루프 시작 =====
                if self.running:
                    console.print()
                    console.print(f"[green]✨ {datetime.now().strftime('%H:%M:%S')} - 대기 완료! 다시 시작합니다![/green]")
                    console.print()

                    # 여기서 while self.running 루프의 처음으로 돌아가서 daily_routine() 재실행!

        except KeyboardInterrupt:
            console.print()
            console.print("[yellow]⚠️  사용자가 중지했습니다. (Ctrl+C)[/yellow]")
            self.running = False
        except Exception as e:
            console.print(f"[red]❌ 오류: {e}[/red]")
            import traceback
            traceback.print_exc()
        finally:
            console.print()
            console.print(f"[cyan]📊 총 {self.cycle_count}번 사이클 실행됨[/cyan]")
            console.print("[cyan]프로그램 종료[/cyan]")


async def main():
    tester = AutoRestartTester()
    await tester.run_with_auto_restart()


if __name__ == "__main__":
    asyncio.run(main())
