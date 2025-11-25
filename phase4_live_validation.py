#!/usr/bin/env python3
"""
Phase 4 실전 검증 스크립트

SignalOrchestrator의 8-Alpha + Dynamic Weights 시스템을
실제 키움 API 데이터로 검증합니다.

- 실제 매매는 하지 않음 (Dry-run)
- 실시간 Market Regime 감지
- 8개 알파 스코어 및 가중치 변화 모니터링
- Aggregate Score 계산 및 판정 확인
"""
import sys
from pathlib import Path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import asyncio
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich import box
from dotenv import load_dotenv

from kiwoom_api import KiwoomAPI
from config.config_manager import ConfigManager
from analyzers.signal_orchestrator import SignalOrchestrator
from database.trading_db_v2 import TradingDatabaseV2

load_dotenv()
console = Console()


class Phase4LiveValidator:
    """Phase 4 실전 검증기"""

    def __init__(self):
        """초기화"""
        self.api = None
        self.config = None
        self.orchestrator = None
        self.db = None

        # 검증 통계
        self.stats = {
            'total_evaluations': 0,
            'regime_changes': 0,
            'weight_adjustments': 0,
            'buy_signals': 0,
            'sell_signals': 0,
            'neutral_signals': 0,
            'errors': 0,
        }

        # Regime 기록
        self.current_regime = None
        self.regime_history = []

    async def initialize(self):
        """시스템 초기화"""

        console.print()
        console.print("=" * 100, style="bold cyan")
        console.print(f"{'🧪 Phase 4 실전 검증 시스템 초기화':^100}", style="bold cyan")
        console.print("=" * 100, style="bold cyan")
        console.print()

        # 1. 설정 로드
        console.print("[cyan]1. 설정 파일 로드 중...[/cyan]")
        try:
            config_manager = ConfigManager()
            self.config = config_manager.load('config/trading_config.yaml', environment='development')
            console.print("   ✅ 설정 로드 완료")
        except Exception as e:
            console.print(f"[red]   ❌ 설정 로드 실패: {e}[/red]")
            import traceback
            traceback.print_exc()
            return False

        # 2. 키움 API 초기화
        console.print("[cyan]2. 키움 API 초기화 중...[/cyan]")
        try:
            self.api = KiwoomAPI(self.config)
            token = self.api.get_access_token()

            if not token:
                console.print("[red]   ❌ 액세스 토큰 발급 실패[/red]")
                return False

            console.print("   ✅ 키움 API 연결 완료")
        except Exception as e:
            console.print(f"[red]   ❌ API 초기화 실패: {e}[/red]")
            return False

        # 3. DB 초기화
        console.print("[cyan]3. 데이터베이스 초기화 중...[/cyan]")
        try:
            self.db = TradingDatabaseV2('database/trading.db')
            console.print("   ✅ 데이터베이스 연결 완료")
        except Exception as e:
            console.print(f"[red]   ❌ DB 초기화 실패: {e}[/red]")
            return False

        # 4. SignalOrchestrator 초기화 (Phase 4 통합)
        console.print("[cyan]4. SignalOrchestrator 초기화 중 (8-Alpha + Dynamic Weights)...[/cyan]")
        try:
            self.orchestrator = SignalOrchestrator(self.config, self.api)
            self.current_regime = self.orchestrator.current_regime
            console.print(f"   ✅ SignalOrchestrator 초기화 완료")
            console.print(f"   📊 초기 Regime: {self.current_regime}")
            console.print(f"   🎯 8개 알파 로드 완료")
        except Exception as e:
            console.print(f"[red]   ❌ Orchestrator 초기화 실패: {e}[/red]")
            import traceback
            traceback.print_exc()
            return False

        console.print()
        console.print("[bold green]✅ 시스템 초기화 완료![/bold green]")
        console.print()

        return True

    async def validate_stock(self, stock_code: str, stock_name: str = None):
        """
        단일 종목 검증

        Args:
            stock_code: 종목 코드
            stock_name: 종목명 (선택)

        Returns:
            검증 결과 dict
        """
        try:
            console.print(f"\n[cyan]🔍 검증 중: {stock_code} {stock_name or ''}[/cyan]")

            # SignalOrchestrator로 신호 평가
            result = self.orchestrator.evaluate_signal(
                symbol=stock_code,
                market='KOSPI'
            )

            # 통계 업데이트
            self.stats['total_evaluations'] += 1

            # Regime 변경 감지
            if result['details'].get('weights_updated'):
                self.stats['regime_changes'] += 1
                self.stats['weight_adjustments'] += 1
                new_regime = result['details'].get('market_regime')

                console.print(f"[bold yellow]🔄 Regime 변경 감지: {self.current_regime} → {new_regime}[/bold yellow]")

                self.regime_history.append({
                    'timestamp': datetime.now(),
                    'old_regime': self.current_regime,
                    'new_regime': new_regime,
                })

                self.current_regime = new_regime

            # 신호 판정
            tier = result.get('tier')
            if tier in ['TIER_S', 'TIER_A']:
                self.stats['buy_signals'] += 1
                signal_type = "✅ 매수"
                signal_color = "green"
            elif tier in ['TIER_F']:
                self.stats['sell_signals'] += 1
                signal_type = "❌ 차단"
                signal_color = "red"
            else:
                self.stats['neutral_signals'] += 1
                signal_type = "➖ 중립"
                signal_color = "yellow"

            # 결과 출력
            console.print(f"[{signal_color}]  판정: {signal_type} (Tier: {tier})[/{signal_color}]")

            # 알파 스코어 출력
            alpha_result = result['details'].get('alpha_result')
            if alpha_result:
                aggregate_score = alpha_result.get('aggregate_score', 0)
                console.print(f"  📊 Aggregate Score: {aggregate_score:+.2f}")

                # 상위 3개 알파 출력
                weighted_scores = alpha_result.get('weighted_scores', {})
                if weighted_scores:
                    alpha_contribs = []
                    for alpha_name, alpha_data in weighted_scores.items():
                        contrib = alpha_data.get('weighted_contribution', 0)
                        alpha_contribs.append((alpha_name, contrib))

                    alpha_contribs.sort(key=lambda x: abs(x[1]), reverse=True)
                    top_3 = alpha_contribs[:3]

                    console.print(f"  🎯 주요 알파:")
                    for alpha_name, contrib in top_3:
                        color = "green" if contrib > 0 else "red" if contrib < 0 else "dim"
                        console.print(f"     - {alpha_name:18s}: [{color}]{contrib:+.2f}[/{color}]")

            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'tier': tier,
                'signal_type': signal_type,
                'aggregate_score': alpha_result.get('aggregate_score', 0) if alpha_result else 0,
                'regime': result['details'].get('market_regime'),
                'timestamp': datetime.now(),
                'result': result
            }

        except Exception as e:
            self.stats['errors'] += 1
            console.print(f"[red]  ❌ 검증 실패: {e}[/red]")
            import traceback
            traceback.print_exc()
            return None

    async def run_validation(self, test_stocks: list, interval_seconds: int = 60):
        """
        실전 검증 실행

        Args:
            test_stocks: 검증할 종목 리스트 [{'code': '005930', 'name': '삼성전자'}, ...]
            interval_seconds: 검증 간격 (초)
        """

        console.print()
        console.print("=" * 100, style="bold green")
        console.print(f"{'🚀 Phase 4 실전 검증 시작':^100}", style="bold green")
        console.print("=" * 100, style="bold green")
        console.print()

        console.print(f"[bold]검증 설정:[/bold]")
        console.print(f"  • 종목 수: {len(test_stocks)}개")
        console.print(f"  • 검증 간격: {interval_seconds}초")
        console.print(f"  • 초기 Regime: {self.current_regime}")
        console.print()
        console.print(Panel.fit(
            "[bold yellow]⚠️ 실전 데이터 검증 모드[/bold yellow]\n\n"
            "실제 키움 API 데이터로 신호를 생성하지만\n"
            "[bold green]실제 매매는 하지 않습니다 (Dry-run)[/bold green]",
            border_style="yellow"
        ))
        console.print()

        # 검증 결과 저장
        validation_results = []

        try:
            cycle = 1
            while True:
                console.print("=" * 100, style="dim")
                console.print(f"[bold cyan]검증 사이클 #{cycle} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold cyan]")
                console.print("=" * 100, style="dim")

                # 각 종목 검증
                for stock in test_stocks:
                    result = await self.validate_stock(
                        stock_code=stock['code'],
                        stock_name=stock.get('name')
                    )

                    if result:
                        validation_results.append(result)

                    # 종목 간 딜레이 (API 부하 방지)
                    await asyncio.sleep(2)

                # 사이클 통계 출력
                self._print_cycle_stats(cycle)

                cycle += 1

                # 다음 사이클까지 대기
                console.print()
                console.print(f"[dim]다음 검증까지 {interval_seconds}초 대기...[/dim]")
                console.print(f"[dim]Ctrl+C로 종료[/dim]")
                console.print()

                await asyncio.sleep(interval_seconds)

        except KeyboardInterrupt:
            console.print()
            console.print("[yellow]⚠️ 사용자가 검증을 중단했습니다.[/yellow]")

        # 최종 통계 출력
        self._print_final_stats(validation_results)

        return validation_results

    def _print_cycle_stats(self, cycle: int):
        """사이클 통계 출력"""

        console.print()
        console.print(f"[bold]📊 사이클 #{cycle} 통계:[/bold]")

        table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
        table.add_column("항목", style="cyan")
        table.add_column("값", justify="right", style="white")

        table.add_row("총 평가", f"{self.stats['total_evaluations']:,}회")
        table.add_row("Regime 변경", f"{self.stats['regime_changes']:,}회")
        table.add_row("가중치 조정", f"{self.stats['weight_adjustments']:,}회")
        table.add_row("", "")
        table.add_row("✅ 매수 신호", f"[green]{self.stats['buy_signals']:,}회[/green]")
        table.add_row("➖ 중립 신호", f"[yellow]{self.stats['neutral_signals']:,}회[/yellow]")
        table.add_row("❌ 차단 신호", f"[red]{self.stats['sell_signals']:,}회[/red]")
        table.add_row("", "")
        table.add_row("⚠️ 오류", f"[red]{self.stats['errors']:,}회[/red]")

        console.print(table)
        console.print(f"  현재 Regime: [bold yellow]{self.current_regime}[/bold yellow]")
        console.print()

    def _print_final_stats(self, results: list):
        """최종 통계 출력"""

        console.print()
        console.print("=" * 100, style="bold green")
        console.print(f"{'📊 Phase 4 실전 검증 최종 통계':^100}", style="bold green")
        console.print("=" * 100, style="bold green")
        console.print()

        # 기본 통계
        console.print("[bold]1️⃣ 검증 통계:[/bold]")
        console.print(f"  총 평가: {self.stats['total_evaluations']:,}회")
        console.print(f"  성공률: {((self.stats['total_evaluations'] - self.stats['errors']) / max(self.stats['total_evaluations'], 1) * 100):.1f}%")
        console.print()

        # 신호 분포
        console.print("[bold]2️⃣ 신호 분포:[/bold]")
        total_signals = self.stats['buy_signals'] + self.stats['neutral_signals'] + self.stats['sell_signals']

        if total_signals > 0:
            console.print(f"  ✅ 매수: {self.stats['buy_signals']:,}회 ({self.stats['buy_signals']/total_signals*100:.1f}%)")
            console.print(f"  ➖ 중립: {self.stats['neutral_signals']:,}회 ({self.stats['neutral_signals']/total_signals*100:.1f}%)")
            console.print(f"  ❌ 차단: {self.stats['sell_signals']:,}회 ({self.stats['sell_signals']/total_signals*100:.1f}%)")
        console.print()

        # Regime 변경 기록
        console.print("[bold]3️⃣ Market Regime 변경 기록:[/bold]")
        if self.regime_history:
            for i, change in enumerate(self.regime_history[:10], 1):  # 최근 10개
                timestamp = change['timestamp'].strftime('%H:%M:%S')
                console.print(f"  {i}. [{timestamp}] {change['old_regime']} → {change['new_regime']}")
        else:
            console.print(f"  변경 없음 (현재 Regime: {self.current_regime})")
        console.print()

        # 종목별 통계
        if results:
            console.print("[bold]4️⃣ 종목별 신호 요약:[/bold]")

            # 종목별 집계
            stock_stats = {}
            for r in results:
                code = r['stock_code']
                if code not in stock_stats:
                    stock_stats[code] = {
                        'name': r.get('stock_name', code),
                        'buy': 0,
                        'neutral': 0,
                        'sell': 0,
                        'avg_score': []
                    }

                if r['signal_type'] == "✅ 매수":
                    stock_stats[code]['buy'] += 1
                elif r['signal_type'] == "❌ 차단":
                    stock_stats[code]['sell'] += 1
                else:
                    stock_stats[code]['neutral'] += 1

                stock_stats[code]['avg_score'].append(r['aggregate_score'])

            # 테이블 출력
            table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("종목", style="cyan")
            table.add_column("매수", justify="right", style="green")
            table.add_column("중립", justify="right", style="yellow")
            table.add_column("차단", justify="right", style="red")
            table.add_column("평균 Score", justify="right", style="white")

            for code, stats in sorted(stock_stats.items(),
                                     key=lambda x: sum(x[1]['avg_score'])/len(x[1]['avg_score']) if x[1]['avg_score'] else 0,
                                     reverse=True):
                avg_score = sum(stats['avg_score']) / len(stats['avg_score']) if stats['avg_score'] else 0
                score_color = "green" if avg_score > 0.5 else "red" if avg_score < -0.5 else "white"

                table.add_row(
                    f"{stats['name']} ({code})",
                    str(stats['buy']),
                    str(stats['neutral']),
                    str(stats['sell']),
                    f"[{score_color}]{avg_score:+.2f}[/{score_color}]"
                )

            console.print(table)

        console.print()
        console.print("=" * 100, style="bold green")
        console.print(f"{'✅ Phase 4 실전 검증 완료':^100}", style="bold green")
        console.print("=" * 100, style="bold green")
        console.print()


async def main():
    """메인 함수"""

    try:
        # Phase 4 검증기 생성
        validator = Phase4LiveValidator()

        # 초기화
        init_ok = await validator.initialize()

        if not init_ok:
            console.print("[red]❌ 초기화 실패[/red]")
            return

        # 테스트 종목 선택
        console.print("[bold yellow]검증할 종목을 선택하세요:[/bold yellow]")
        console.print()
        console.print("[1] KOSPI 대형주 5개 (삼성전자, SK하이닉스, NAVER, 현대차, 카카오)")
        console.print("[2] KOSPI 시총 상위 10개")
        console.print("[3] 직접 입력")
        console.print()

        choice = console.input("[yellow]선택 (기본: 1): [/yellow]").strip() or "1"

        test_stocks = []

        if choice == "1":
            test_stocks = [
                {'code': '005930', 'name': '삼성전자'},
                {'code': '000660', 'name': 'SK하이닉스'},
                {'code': '035420', 'name': 'NAVER'},
                {'code': '005380', 'name': '현대차'},
                {'code': '035720', 'name': '카카오'},
            ]

        elif choice == "2":
            test_stocks = [
                {'code': '005930', 'name': '삼성전자'},
                {'code': '000660', 'name': 'SK하이닉스'},
                {'code': '005380', 'name': '현대차'},
                {'code': '068270', 'name': '셀트리온'},
                {'code': '207940', 'name': '삼성바이오로직스'},
                {'code': '005490', 'name': 'POSCO홀딩스'},
                {'code': '035420', 'name': 'NAVER'},
                {'code': '051910', 'name': 'LG화학'},
                {'code': '006400', 'name': '삼성SDI'},
                {'code': '035720', 'name': '카카오'},
            ]

        elif choice == "3":
            console.print()
            codes_input = console.input("[yellow]종목 코드 입력 (쉼표 구분, 예: 005930,000660): [/yellow]").strip()

            if codes_input:
                codes = [c.strip() for c in codes_input.split(',')]
                for code in codes:
                    test_stocks.append({'code': code, 'name': code})

        if not test_stocks:
            console.print("[red]❌ 종목이 선택되지 않았습니다.[/red]")
            return

        # 검증 간격 설정
        console.print()
        interval_input = console.input("[yellow]검증 간격 (초, 기본: 60): [/yellow]").strip() or "60"
        interval_seconds = int(interval_input)

        # 검증 실행
        results = await validator.run_validation(test_stocks, interval_seconds)

        # 결과 저장 옵션
        if results:
            console.print()
            save_choice = console.input("[yellow]검증 결과를 파일로 저장하시겠습니까? (y/n, 기본: n): [/yellow]").strip().lower()

            if save_choice == 'y':
                import json
                from pathlib import Path

                # 결과 디렉토리 생성
                results_dir = Path("./validation_results")
                results_dir.mkdir(exist_ok=True)

                # 파일명 생성
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = results_dir / f"phase4_validation_{timestamp}.json"

                # 결과 직렬화 (datetime 처리)
                serializable_results = []
                for r in results:
                    serializable_results.append({
                        'stock_code': r['stock_code'],
                        'stock_name': r['stock_name'],
                        'tier': r['tier'],
                        'signal_type': r['signal_type'],
                        'aggregate_score': r['aggregate_score'],
                        'regime': r['regime'],
                        'timestamp': r['timestamp'].isoformat(),
                    })

                # JSON 저장
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump({
                        'validation_stats': validator.stats,
                        'regime_history': [
                            {
                                'timestamp': h['timestamp'].isoformat(),
                                'old_regime': h['old_regime'],
                                'new_regime': h['new_regime']
                            }
                            for h in validator.regime_history
                        ],
                        'results': serializable_results
                    }, f, ensure_ascii=False, indent=2)

                console.print(f"[green]✅ 결과 저장 완료: {filename}[/green]")

    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️ 검증이 중단되었습니다.[/yellow]")
    except Exception as e:
        console.print()
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()
    finally:
        console.print()
        console.print("=" * 100, style="bold cyan")
        console.print(f"{'🏁 Phase 4 실전 검증 종료':^100}", style="bold cyan")
        console.print("=" * 100, style="bold cyan")
        console.print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️ 프로그램이 종료되었습니다.[/yellow]")
    except Exception as e:
        console.print()
        console.print(f"[red]❌ 치명적 오류: {e}[/red]")
        import traceback
        traceback.print_exc()
