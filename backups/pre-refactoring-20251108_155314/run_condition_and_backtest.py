#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_condition_and_backtest.py

조건검색 → VWAP 필터 → 백테스트 통합 실행
"""

import asyncio
import logging
from pathlib import Path

from rich.console import Console
from dotenv import load_dotenv
import os

from kiwoom_api import KiwoomAPI
from main_condition_filter import KiwoomVWAPPipeline
from backtest_with_ranker import BacktestRunner
from utils.backtest_integration import convert_vwap_results_to_backtest_input
from utils.feature_calculator import FeatureCalculator
from core.kiwoom_rest_client import KiwoomRESTClient

# 환경변수 로드
load_dotenv()

console = Console()
logger = logging.getLogger(__name__)


async def main():
    """조건검색 + VWAP 필터 + 백테스트 통합 실행"""
    logging.basicConfig(level=logging.INFO)

    console.print("\n" + "=" * 120, style="bold cyan")
    console.print(f"{'조건검색 → VWAP 필터 → 백테스트 통합 파이프라인':^120}", style="bold cyan")
    console.print("=" * 120, style="bold cyan")
    console.print()

    # 1. API 클라이언트 생성
    console.print("[1] 시스템 초기화")
    api = KiwoomAPI()
    console.print("  ✓ API 클라이언트 생성")
    console.print()

    # 2. AccessToken 발급
    console.print("[2] AccessToken 발급")
    api.get_access_token()

    if not api.access_token:
        console.print("[red]❌ 토큰 발급 실패[/red]")
        return

    access_token = api.access_token
    console.print("✓ 접근 토큰 발급 성공", style="green")
    console.print()

    # 3. 조건검색 + VWAP 필터 실행
    console.print("[3] 조건검색 + VWAP 필터 실행")
    pipeline = KiwoomVWAPPipeline(access_token, api)

    # 사용할 조건식 인덱스 (실제 환경에 맞게 수정)
    CONDITION_INDICES = [17, 18, 19]  # Momentum, Breakout, EOD

    await pipeline.run_pipeline(condition_indices=CONDITION_INDICES)
    console.print()

    # 4. VWAP 검증 통과 종목 확인
    if not pipeline.validated_stocks:
        console.print("[yellow]⚠️  VWAP 검증 통과 종목이 없습니다.[/yellow]")
        console.print("[yellow]   백테스트를 건너뜁니다.[/yellow]")
        return

    console.print(f"[green]✅ VWAP 검증 통과: {len(pipeline.validated_stocks)}개 종목[/green]")
    console.print()

    # 5. Feature Calculator 생성 (실제 데이터 사용 시)
    feature_calculator = None
    use_real_features = console.input(
        "\n[yellow]Feature를 실제 API 데이터로 계산하시겠습니까? (y/n, 기본: n): [/yellow]"
    ).strip().lower() or "n"

    if use_real_features == 'y':
        console.print("\n[4] Feature Calculator 초기화")
        app_key = os.getenv('KIWOOM_APP_KEY')
        app_secret = os.getenv('KIWOOM_APP_SECRET')

        if not app_key or not app_secret:
            console.print("[red]❌ 환경변수 설정 필요[/red]")
            console.print("[yellow]   기본값으로 Feature 생성합니다.[/yellow]")
        else:
            api_client_for_features = KiwoomRESTClient(app_key, app_secret)
            await api_client_for_features.initialize()
            feature_calculator = FeatureCalculator(api_client_for_features)
            console.print("  ✓ Feature Calculator 초기화 완료")
            console.print()

    # 6. 백테스트 입력 데이터로 변환
    console.print("[5] 백테스트 데이터 변환")
    candidates = await convert_vwap_results_to_backtest_input(
        pipeline.validated_stocks,
        feature_calculator=feature_calculator
    )
    console.print(f"  ✓ 백테스트 대상: {len(candidates)}개")
    console.print()

    # Feature Calculator 종료
    if feature_calculator and hasattr(feature_calculator.api_client, 'close'):
        await feature_calculator.api_client.close()

    # 7. 백테스트 실행 여부 확인
    console.print("=" * 120, style="yellow")
    choice = console.input("[yellow]백테스트를 실행하시겠습니까? (y/n, 기본: y): [/yellow]").strip().lower() or "y"

    if choice != 'y':
        console.print("[yellow]백테스트를 건너뜁니다.[/yellow]")
        return

    # 8. 실제 데이터 사용 여부 확인
    use_real_data_choice = console.input(
        "[yellow]백테스트에 실제 키움 API 데이터를 사용하시겠습니까? (y/n, 기본: n): [/yellow]"
    ).strip().lower() or "n"
    use_real_data = (use_real_data_choice == 'y')

    # 9. REST API 클라이언트 생성 (실제 데이터 사용 시)
    api_client = None
    if use_real_data:
        console.print("\n[6] REST API 클라이언트 초기화")
        app_key = os.getenv('KIWOOM_APP_KEY')
        app_secret = os.getenv('KIWOOM_APP_SECRET')

        if not app_key or not app_secret:
            console.print("[red]❌ 환경변수에 KIWOOM_APP_KEY, KIWOOM_APP_SECRET을 설정해주세요[/red]")
            console.print("[yellow]   Mock 데이터로 백테스트를 진행합니다.[/yellow]")
            use_real_data = False
        else:
            api_client = KiwoomRESTClient(app_key, app_secret)
            await api_client.initialize()
            console.print("  ✓ REST API 클라이언트 초기화 완료")
            console.print()

    # 10. 백테스트 파라미터 입력
    console.print("\n[7] 백테스트 파라미터 설정")
    holding_period = int(console.input("[yellow]보유 기간 (일, 기본: 5): [/yellow]").strip() or "5")
    take_profit_pct = float(console.input("[yellow]익절 기준 (%, 기본: 3.0): [/yellow]").strip() or "3.0")
    stop_loss_pct = float(console.input("[yellow]손절 기준 (%, 기본: -2.0): [/yellow]").strip() or "-2.0")
    console.print()

    # 11. 백테스트 실행
    console.print("[8] 백테스트 실행")
    runner = BacktestRunner(
        use_real_data=use_real_data,
        api_client=api_client
    )

    results = await runner.run_backtest(
        candidates,
        holding_period=holding_period,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct
    )

    # 12. 결과 출력
    runner.display_results(results)

    # 13. API 클라이언트 종료
    if api_client:
        await api_client.close()

    console.print("\n" + "=" * 120, style="bold green")
    console.print(f"{'파이프라인 완료':^120}", style="bold green")
    console.print("=" * 120, style="bold green")
    console.print()
    console.print("[green]✅ 백테스트 결과가 ./backtest_results/ 디렉토리에 저장되었습니다.[/green]")
    console.print("[yellow]💡 이 데이터를 ML 모델 학습에 사용할 수 있습니다 (메뉴 [3]).[/yellow]")


if __name__ == "__main__":
    asyncio.run(main())
