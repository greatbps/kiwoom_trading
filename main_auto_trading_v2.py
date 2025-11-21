"""
키움 조건식 → VWAP 필터링 → 실시간 자동매매 통합 시스템 (간소화 버전)

TradingOrchestrator를 사용한 모듈화된 자동 매매 시스템

전체 플로우:
1. 조건식 6개로 1차 필터링 (50~100개 종목)
2. VWAP 사전 검증으로 2차 필터링 (5~20개 종목)
3. 선정 종목 실시간 모니터링
4. VWAP 매수 신호 감지 → 사전 검증 → 자동 매수
5. 보유 중 모니터링 → VWAP 매도 신호 또는 트레일링 스탑 → 자동 매도
6. 무한 루프 (Ctrl+C로 중지)
"""
import asyncio
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from dotenv import load_dotenv

# 프로젝트 루트 경로
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 모듈 임포트
from kiwoom_api import KiwoomAPI
from config.config_manager import ConfigManager
from core.risk_manager import RiskManager
from database.trading_db_v2 import TradingDatabaseV2
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.pre_trade_validator import PreTradeValidator

# Trading 패키지 임포트 (새로운 모듈화된 시스템)
from trading import TradingOrchestrator

# 환경변수 로드
load_dotenv()

console = Console()


async def main():
    """메인 함수"""

    console.print()
    console.print("=" * 120, style="bold cyan")
    console.print(f"{'🚀 키움 자동 매매 시스템 (모듈화 버전)':^120}", style="bold cyan")
    console.print("=" * 120, style="bold cyan")
    console.print()

    try:
        # ========================================
        # 1단계: 시스템 초기화
        # ========================================
        console.print("[bold cyan]1단계: 시스템 초기화[/bold cyan]")
        console.print()

        # 설정 로드
        console.print("  📋 설정 파일 로드 중...")
        config = ConfigManager.load('config/trading_config.yaml', environment='development')
        console.print("  ✅ 설정 로드 완료")

        # API 초기화
        console.print("  🔑 키움 API 초기화 중...")
        api = KiwoomAPI(config)

        # 토큰 발급
        console.print("  🔐 액세스 토큰 발급 중...")
        token = api.get_access_token()
        if not token:
            console.print("[red]❌ 액세스 토큰 발급 실패[/red]")
            return
        console.print("  ✅ 액세스 토큰 발급 완료")

        # 리스크 관리자 초기화
        console.print("  📊 리스크 관리자 초기화 중...")
        risk_manager = RiskManager(config)
        console.print("  ✅ 리스크 관리자 초기화 완료")

        # 분석기 초기화
        console.print("  🔬 분석기 초기화 중...")
        analyzer = EntryTimingAnalyzer()
        validator = PreTradeValidator(config)
        console.print("  ✅ 분석기 초기화 완료")

        # DB 초기화
        console.print("  💾 데이터베이스 초기화 중...")
        db = TradingDatabaseV2('database/trading.db')
        console.print("  ✅ 데이터베이스 초기화 완료")

        console.print()

        # ========================================
        # 2단계: TradingOrchestrator 생성
        # ========================================
        console.print("[bold cyan]2단계: Trading Orchestrator 생성[/bold cyan]")
        console.print()

        orchestrator = TradingOrchestrator(
            api=api,
            config=config,
            risk_manager=risk_manager,
            validator=validator,
            analyzer=analyzer,
            db=db
        )

        console.print("  ✅ TradingOrchestrator 생성 완료")
        console.print()

        # ========================================
        # 3단계: 계좌 정보 로드
        # ========================================
        console.print("[bold cyan]3단계: 계좌 정보 로드[/bold cyan]")
        console.print()

        init_ok = await orchestrator.initialize()

        if not init_ok:
            console.print("[yellow]⚠️  계좌 정보 로드 실패 (기본값으로 진행)[/yellow]")

        console.print()

        # ========================================
        # 4단계: 조건검색 + VWAP 필터링
        # ========================================
        console.print("[bold cyan]4단계: 조건검색 + VWAP 필터링[/bold cyan]")
        console.print()

        # 사용자에게 조건검색 실행 여부 확인
        console.print("[yellow]조건검색을 실행하시겠습니까?[/yellow]")
        console.print("  [1] 예 - 조건검색 실행 (권장)")
        console.print("  [2] 아니오 - DB에서 활성 종목 로드")
        console.print()

        # 간소화를 위해 자동으로 조건검색 실행
        console.print("[cyan]→ 조건검색 자동 실행[/cyan]")
        console.print()

        await orchestrator.run_condition_filtering("VWAP돌파")

        console.print()

        # ========================================
        # 5단계: 실시간 모니터링 시작
        # ========================================
        console.print("[bold cyan]5단계: 실시간 모니터링 시작[/bold cyan]")
        console.print()

        console.print(Panel.fit(
            "[bold green]시스템이 정상적으로 시작되었습니다![/bold green]\n\n"
            "📊 실시간 모니터링 중...\n"
            "🔄 5분마다 조건검색 자동 재실행\n"
            "💰 1분마다 종목 체크\n"
            "🛑 Ctrl+C로 종료",
            title="✅ 시스템 준비 완료",
            border_style="green"
        ))
        console.print()

        # 실시간 모니터링 시작
        await orchestrator.monitor_and_trade()

    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️  사용자가 시스템을 종료했습니다.[/yellow]")
        console.print()
    except Exception as e:
        console.print()
        console.print(f"[red]❌ 시스템 오류: {e}[/red]")
        console.print()
        import traceback
        traceback.print_exc()
    finally:
        console.print()
        console.print("=" * 120, style="bold cyan")
        console.print(f"{'🏁 자동 매매 시스템 종료':^120}", style="bold cyan")
        console.print("=" * 120, style="bold cyan")
        console.print()


def show_menu():
    """메뉴 표시 (기존 호환성을 위해 유지)"""
    console.print()
    console.print("=" * 120, style="bold magenta")
    console.print(f"{'📋 자동 매매 메뉴':^120}", style="bold magenta")
    console.print("=" * 120, style="bold magenta")
    console.print()
    console.print("  [1] 🚀 자동 매매 시작 (조건검색 + VWAP 필터링 + 실시간 모니터링)")
    console.print("  [2] 📊 조건검색 + VWAP 필터링만 실행")
    console.print("  [3] 💰 현재 계좌 잔고 조회")
    console.print("  [4] 🔍 보유 종목 현황 조회")
    console.print("  [0] 🚪 종료")
    console.print()
    console.print("=" * 120, style="bold magenta")
    console.print()


async def menu_mode():
    """메뉴 모드 (기존 호환성을 위해 유지)"""
    console.print()
    console.print("=" * 120, style="bold cyan")
    console.print(f"{'🚀 키움 자동 매매 시스템 (메뉴 모드)':^120}", style="bold cyan")
    console.print("=" * 120, style="bold cyan")
    console.print()

    # 시스템 초기화
    config = ConfigManager.load('config/trading_config.yaml', environment='development')
    api = KiwoomAPI(config)
    token = api.get_access_token()

    if not token:
        console.print("[red]❌ 액세스 토큰 발급 실패[/red]")
        return

    risk_manager = RiskManager(config)
    analyzer = EntryTimingAnalyzer()
    validator = PreTradeValidator(config)
    db = TradingDatabaseV2('database/trading.db')

    orchestrator = TradingOrchestrator(
        api=api,
        config=config,
        risk_manager=risk_manager,
        validator=validator,
        analyzer=analyzer,
        db=db
    )

    while True:
        show_menu()

        try:
            choice = input("메뉴 선택: ").strip()

            if choice == "1":
                # 자동 매매 시작
                await orchestrator.initialize()
                await orchestrator.run_condition_filtering("VWAP돌파")
                await orchestrator.monitor_and_trade()

            elif choice == "2":
                # 조건검색 + 필터링만
                await orchestrator.run_condition_filtering("VWAP돌파")

            elif choice == "3":
                # 계좌 잔고 조회
                await orchestrator.initialize()
                status = orchestrator.get_system_status()
                console.print()
                console.print(f"  💰 예수금: {status['available_cash']:,.0f}원")
                console.print(f"  📊 총 자산: {status['total_assets']:,.0f}원")
                console.print()

            elif choice == "4":
                # 보유 종목 현황
                await orchestrator.initialize()
                status = orchestrator.get_system_status()
                console.print()
                console.print(f"  📈 보유 종목: {status['position_count']}개")
                console.print(f"  💵 투자 금액: {status['total_invested']:,.0f}원")
                console.print(f"  💰 평가 금액: {status['total_value']:,.0f}원")
                console.print(f"  📊 평가 손익: {status['total_profit']:+,.0f}원")
                console.print()

            elif choice == "0":
                console.print()
                console.print("[cyan]👋 프로그램을 종료합니다.[/cyan]")
                console.print()
                break

            else:
                console.print()
                console.print("[red]❌ 잘못된 선택입니다. 다시 선택해주세요.[/red]")
                console.print()

        except KeyboardInterrupt:
            console.print()
            console.print("[yellow]⚠️  메뉴로 돌아갑니다.[/yellow]")
            console.print()
            continue
        except Exception as e:
            console.print()
            console.print(f"[red]❌ 오류 발생: {e}[/red]")
            console.print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='키움 자동 매매 시스템')
    parser.add_argument('--menu', action='store_true', help='메뉴 모드로 실행')
    args = parser.parse_args()

    if args.menu:
        # 메뉴 모드
        asyncio.run(menu_mode())
    else:
        # 자동 실행 모드 (기본)
        asyncio.run(main())
