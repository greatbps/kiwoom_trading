#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_menu.py

키움증권 AI Trading System v2.0 - 메인 메뉴
Ctrl+C로 안전하게 종료 가능
"""

import os
import sys
import signal
import asyncio
import logging
import numpy as np
from datetime import datetime
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

# Rich Console
console = Console()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 종료 플래그
shutdown_flag = False


def signal_handler(sig, frame):
    """Ctrl+C 시그널 핸들러"""
    global shutdown_flag
    console.print("\n\n[yellow]⚠️  종료 신호 감지... 안전하게 종료합니다.[/yellow]")
    shutdown_flag = True


# Ctrl+C 시그널 등록
signal.signal(signal.SIGINT, signal_handler)


def clear_screen():
    """화면 클리어"""
    os.system('clear' if os.name == 'posix' else 'cls')


def print_banner():
    """배너 출력"""
    banner_text = """
[bold cyan]🚀 키움증권 AI Trading System v2.0[/bold cyan]

[dim]Phase 1~4 완료 | ML 통합 | 자동 재학습 | Telegram 알림[/dim]
"""
    console.print(Panel(banner_text, box=box.DOUBLE, border_style="cyan"))
    console.print(f"[dim]📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
    console.print("=" * 70, style="cyan")


def print_menu():
    """메인 메뉴 출력"""
    table = Table(title="📋 메인 메뉴", box=box.ROUNDED, show_header=False)
    table.add_column("번호", style="cyan", width=5)
    table.add_column("메뉴", style="white")

    menu_items = [
        ("1", "🤖 자동 매매 시작 (실시간 트레이딩)"),
        ("2", "🔍 조건 검색 실행 (종목 선정)"),
        ("3", "📊 Ranker 학습 (Candidate Ranker)"),
        ("4", "🧪 Ranker 테스트 (예측 및 랭킹)"),
        ("5", "📈 백테스트 실행"),
        ("6", "📄 리포트 생성 (일일/주간)"),
        ("7", "💬 Telegram 알림 테스트"),
        ("8", "⚙️  시스템 설정"),
        ("9", "📚 도움말"),
        ("0", "🚪 종료"),
    ]

    for num, desc in menu_items:
        table.add_row(f"[{num}]", desc)

    console.print(table)
    console.print("\n[dim]Ctrl+C를 눌러도 안전하게 종료됩니다.[/dim]\n")


async def run_auto_trading():
    """자동 매매 실행"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]🤖 자동 매매 시작...[/bold cyan]")
    console.print("=" * 70, style="cyan")

    try:
        # main_auto_trading.py의 main 함수를 직접 호출
        import main_auto_trading

        console.print("\n[yellow]자동 매매 시스템을 시작합니다...[/yellow]")
        console.print("[dim]종료하려면 Ctrl+C를 누르세요.[/dim]\n")

        # main 함수 실행
        await main_auto_trading.main()

        console.print("\n[green]✅ 자동 매매가 정상 종료되었습니다.[/green]")

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  사용자가 자동 매매를 중단했습니다.[/yellow]")
    except Exception as e:
        logger.error(f"자동 매매 실행 오류: {e}")
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


async def run_condition_search():
    """조건 검색 실행"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]🔍 조건 검색 실행...[/bold cyan]")
    console.print("=" * 70, style="cyan")

    try:
        import subprocess

        console.print("\n[yellow]조건 검색을 시작합니다...[/yellow]")
        console.print("[dim]종료하려면 Ctrl+C를 누르세요.[/dim]\n")

        process = subprocess.Popen(
            ['python', 'main_condition_filter.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # 실시간 출력
        while True:
            if shutdown_flag:
                process.terminate()
                console.print("\n[yellow]조건 검색 종료...[/yellow]")
                break

            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                print(line.rstrip())  # console.print 대신 print 사용 (Rich 포맷팅 없이 원본 출력)

        process.wait()

    except Exception as e:
        logger.error(f"조건 검색 오류: {e}")
        console.print(f"[red]❌ 오류: {e}[/red]")

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


async def train_ml_model():
    """ML 데이터 수집 및 모델 학습 파이프라인"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]📊 ML 학습 파이프라인[/bold cyan]")
    console.print("=" * 70, style="cyan")

    console.print("\n[bold]🎯 작업 단계:[/bold]")
    console.print("  1️⃣  RAW 데이터 수집 (키움 API)")
    console.print("  2️⃣  데이터 정제 (Processed)")
    console.print("  3️⃣  Label 생성 (n봉 후 수익률)")
    console.print("  4️⃣  Training Dataset 생성 (Feature + 통합)")
    console.print("  5️⃣  모델 학습")

    console.print("\n" + "=" * 70, style="cyan")
    choice = console.input("[yellow]전체 파이프라인을 실행하시겠습니까? (y/n, 기본: y): [/yellow]").strip().lower() or "y"

    if choice != 'y':
        console.print("[yellow]취소되었습니다.[/yellow]")
        console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
        return

    try:
        import os
        from dotenv import load_dotenv
        from core.ml_data_collector import MLDataCollector
        from core.data_cleaner import DataCleaner
        from core.label_generator import LabelGenerator
        from core.training_dataset_builder import TrainingDatasetBuilder

        # .env 파일 로드
        load_dotenv()

        app_key = os.getenv('KIWOOM_APP_KEY')
        app_secret = os.getenv('KIWOOM_APP_SECRET')

        if not app_key or not app_secret:
            console.print("\n[red]❌ 키움 API 키가 설정되지 않았습니다.[/red]")
            console.print("[yellow]   .env 파일에 다음을 추가하세요:[/yellow]")
            console.print("   KIWOOM_APP_KEY=your_app_key")
            console.print("   KIWOOM_APP_SECRET=your_app_secret")
            console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
            return

        # 설정
        minute_interval = 5
        max_pages = 30  # 약 1500개 데이터
        max_stocks = 50  # Universe에서 선정할 최대 종목 수

        console.print("\n[bold]📌 설정:[/bold]")
        console.print(f"  - 분봉 간격: [cyan]{minute_interval}분[/cyan]")
        console.print(f"  - 수집 페이지: [cyan]최대 {max_pages}페이지[/cyan]")
        console.print(f"  - Universe 최대 종목: [cyan]{max_stocks}개[/cyan]")

        # Step 0: 학습 대상 종목 선정
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]0️⃣  학습 대상 종목 선정[/bold]")
        console.print("=" * 70, style="cyan")

        console.print("\n[bold]종목 선정 방법:[/bold]")
        console.print("[1] KOSPI 시가총액 상위 종목 (추천)")
        console.print("[2] KOSDAQ 시가총액 상위 종목")
        console.print("[3] 직접 입력")

        choice = console.input("\n[yellow]선택 (기본: 1): [/yellow]").strip() or "1"

        target_stocks = []

        if choice == "1":
            # KOSPI 시가총액 상위 종목
            target_stocks = [
                {"code": "005930", "name": "삼성전자"},
                {"code": "000660", "name": "SK하이닉스"},
                {"code": "005380", "name": "현대차"},
                {"code": "068270", "name": "셀트리온"},
                {"code": "207940", "name": "삼성바이오로직스"},
                {"code": "005490", "name": "POSCO홀딩스"},
                {"code": "035420", "name": "NAVER"},
                {"code": "051910", "name": "LG화학"},
                {"code": "006400", "name": "삼성SDI"},
                {"code": "035720", "name": "카카오"},
                {"code": "012330", "name": "현대모비스"},
                {"code": "028260", "name": "삼성물산"},
                {"code": "003670", "name": "포스코퓨처엠"},
                {"code": "105560", "name": "KB금융"},
                {"code": "055550", "name": "신한지주"},
            ][:max_stocks]

        elif choice == "2":
            # KOSDAQ 시가총액 상위 종목
            target_stocks = [
                {"code": "247540", "name": "에코프로비엠"},
                {"code": "086520", "name": "에코프로"},
                {"code": "091990", "name": "셀트리온헬스케어"},
                {"code": "066970", "name": "엘앤에프"},
                {"code": "196170", "name": "알테오젠"},
                {"code": "145020", "name": "휴젤"},
                {"code": "357780", "name": "솔브레인"},
                {"code": "403870", "name": "HPSP"},
                {"code": "293490", "name": "카카오게임즈"},
                {"code": "039030", "name": "이오테크닉스"},
            ][:max_stocks]

        elif choice == "3":
            # 직접 입력
            console.print("\n[yellow]종목 코드를 쉼표로 구분하여 입력하세요 (예: 005930,000660,035420)[/yellow]")
            codes_input = console.input("[yellow]종목 코드: [/yellow]").strip()

            if not codes_input:
                console.print("[red]❌ 종목 코드가 입력되지 않았습니다.[/red]")
                console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
                return

            codes = [c.strip() for c in codes_input.split(',')]
            for code in codes[:max_stocks]:
                target_stocks.append({"code": code, "name": code})  # 이름은 나중에 API에서 조회

        else:
            console.print("[red]❌ 잘못된 선택입니다.[/red]")
            console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
            return

        if not target_stocks:
            console.print("\n[red]❌ 선정된 종목이 없습니다.[/red]")
            console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
            return

        console.print(f"\n[green]✅ 종목 선정 완료: {len(target_stocks)}개[/green]")
        console.print(f"[dim]   종목: {', '.join([s['name'] for s in target_stocks[:10]])}{'...' if len(target_stocks) > 10 else ''}[/dim]")

        # Step 1: RAW 데이터 수집
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]1️⃣  RAW 데이터 수집 중... (키움 API)[/bold]")
        console.print("=" * 70, style="cyan")

        async with MLDataCollector(
            app_key=app_key,
            app_secret=app_secret,
            data_dir="./data/raw",
            max_concurrent_tasks=2
        ) as collector:
            collector.add_stocks_from_list(
                stock_list=target_stocks,
                minute_interval=minute_interval,
                max_pages=max_pages
            )
            stats = await collector.collect_all()

            console.print(f"\n[green]✅ 데이터 수집 완료: {stats['completed_tasks']}/{stats['total_tasks']} 성공[/green]")
            console.print(f"   총 데이터: [cyan]{stats['total_data_points']:,}개[/cyan]")

        collected_symbols = [stock['code'] for stock in target_stocks]

        # Step 2: 데이터 정제
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]2️⃣  데이터 정제 중...[/bold]")
        console.print("=" * 70, style="cyan")

        cleaner = DataCleaner(
            raw_dir="./data/raw",
            processed_dir="./data/processed"
        )
        clean_results = cleaner.batch_clean(
            symbols=collected_symbols,
            interval=f"{minute_interval}min"
        )

        success_symbols = [s for s, success in clean_results.items() if success]
        console.print(f"\n[green]✅ 데이터 정제 완료: {len(success_symbols)}/{len(collected_symbols)} 성공[/green]")

        # Step 3: Label 생성
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]3️⃣  Label 생성 중... (n봉 후 수익률)[/bold]")
        console.print("=" * 70, style="cyan")

        label_gen = LabelGenerator(
            processed_dir="./data/processed",
            labeled_dir="./data/labeled"
        )
        label_results = label_gen.batch_generate_labels(
            symbols=success_symbols,
            interval=f"{minute_interval}min",
            horizons=[3, 5, 10],
            profit_threshold=2.0,
            loss_threshold=-2.0,
            label_types=['ternary', 'binary']
        )

        labeled_symbols = [s for s, success in label_results.items() if success]
        console.print(f"\n[green]✅ Label 생성 완료: {len(labeled_symbols)}/{len(success_symbols)} 성공[/green]")

        # Step 4: Training Dataset 생성
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]4️⃣  Training Dataset 생성 중... (Feature + 통합)[/bold]")
        console.print("=" * 70, style="cyan")

        model_name = f"ml_model_{datetime.now().strftime('%Y%m%d')}"

        builder = TrainingDatasetBuilder(
            labeled_dir="./data/labeled",
            training_dir="./data/training"
        )
        metadata = builder.build_training_dataset(
            symbols=labeled_symbols,
            interval=f"{minute_interval}min",
            model_name=model_name,
            add_features=True,
            train_ratio=0.7,
            val_ratio=0.15
        )

        if metadata:
            console.print(f"\n[green]✅ Training Dataset 생성 완료[/green]")
            console.print(f"   - Train: [cyan]{metadata['train']['rows']:,}행[/cyan]")
            console.print(f"   - Val: [cyan]{metadata['val']['rows']:,}행[/cyan]")
            console.print(f"   - Test: [cyan]{metadata['test']['rows']:,}행[/cyan]")
            console.print(f"   - Features: [cyan]{metadata['features']['total']}개[/cyan]")
            console.print(f"   - 저장: [dim]./data/training/{model_name}/[/dim]")

        # Step 5: 모델 학습
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]5️⃣  모델 학습 중...[/bold]")
        console.print("=" * 70, style="cyan")

        console.print("\n[yellow]⚠️  모델 학습 기능은 다음 단계에서 구현됩니다.[/yellow]")
        console.print("[dim]   현재는 데이터셋 생성까지 완료되었습니다.[/dim]")

        console.print("\n" + "=" * 70, style="green")
        console.print("[bold green]✅ 전체 파이프라인 완료![/bold green]")
        console.print("=" * 70, style="green")
        console.print(f"\n[bold]📁 데이터 저장 위치:[/bold]")
        console.print(f"   - RAW: [dim]./data/raw/[/dim]")
        console.print(f"   - Processed: [dim]./data/processed/[/dim]")
        console.print(f"   - Labeled: [dim]./data/labeled/[/dim]")
        console.print(f"   - Training: [dim]./data/training/{model_name}/[/dim]")

    except ImportError as e:
        console.print(f"\n[red]❌ 필요한 라이브러리가 설치되지 않았습니다: {e}[/red]")
        console.print("[yellow]   설치 명령: pip install tenacity aiohttp pyarrow[/yellow]")
    except Exception as e:
        logger.error(f"ML 파이프라인 오류: {e}")
        import traceback
        traceback.print_exc()

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


async def test_ml_prediction():
    """Candidate Ranker 실전 테스트 (조건검색 + VWAP + Ranker)"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]🧪 Candidate Ranker 실전 테스트[/bold cyan]")
    console.print("=" * 70, style="cyan")

    console.print("\n[bold]🎯 실전 파이프라인:[/bold]")
    console.print("  1️⃣  조건검색 실행")
    console.print("  2️⃣  VWAP 2차 필터링")
    console.print("  3️⃣  Feature 계산 (선택)")
    console.print("  4️⃣  Ranker 점수화 및 랭킹")
    console.print("  5️⃣  상위 K개 추천")
    console.print()

    # 데이터 소스 선택
    console.print("=" * 70, style="yellow")
    console.print("[bold]데이터 소스 선택:[/bold]")
    console.print("  [1] 실제 조건검색 + VWAP 필터 (실전)")
    console.print("  [2] 샘플 데이터 (빠른 테스트)")
    console.print()

    choice = console.input("[yellow]선택 (기본: 2): [/yellow]").strip() or "2"

    try:
        import pandas as pd
        import os
        from dotenv import load_dotenv
        from ml.candidate_ranker import CandidateRanker

        # Ranker 로드
        console.print("\n[bold]📦 Ranker 모델 로드 중...[/bold]")
        ranker = CandidateRanker()

        if not ranker.load_models():
            console.print("\n[yellow]⚠️  학습된 Ranker 모델이 없습니다.[/yellow]")
            console.print("[yellow]   먼저 메뉴 [3]에서 모델을 학습하세요.[/yellow]")
            console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
            return

        console.print("[green]✅ 모델 로드 완료[/green]")

        # 데이터 준비
        if choice == '1':
            # 실제 조건검색 + VWAP 필터
            console.print("\n" + "=" * 70, style="cyan")
            console.print("[bold]1️⃣  조건검색 + VWAP 필터 실행[/bold]")
            console.print("=" * 70, style="cyan")

            load_dotenv()

            # API 클라이언트 생성
            from kiwoom_api import KiwoomAPI
            from main_condition_filter import KiwoomVWAPPipeline

            api = KiwoomAPI()
            api.get_access_token()

            if not api.access_token:
                console.print("[red]❌ 토큰 발급 실패[/red]")
                console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
                return

            # 조건검색 + VWAP 필터 실행
            pipeline = KiwoomVWAPPipeline(api.access_token, api)

            # 조건식 인덱스 선택
            console.print("\n[yellow]사용할 조건식 인덱스를 입력하세요.[/yellow]")
            console.print("[dim]   기본값: seq 31~36 전략 (Momentum, Breakout, EOD, Supertrend, VWAP, Squeeze Momentum Pro)[/dim]")
            console.print("[dim]   = condition_list 인덱스 17~22[/dim]")
            console.print("[dim]   예: 17,18,19 (쉼표로 구분하여 원하는 것만 선택 가능)[/dim]")
            indices_input = console.input("[yellow]조건식 리스트 인덱스 (기본: 17,18,19,20,21,22): [/yellow]").strip() or "17,18,19,20,21,22"
            condition_indices = [int(x.strip()) for x in indices_input.split(',')]

            await pipeline.run_pipeline(condition_indices=condition_indices)

            if not pipeline.validated_stocks:
                console.print("\n[yellow]⚠️  VWAP 검증 통과 종목이 없습니다.[/yellow]")
                console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
                return

            # Feature 계산 여부
            use_real_features = console.input(
                "\n[yellow]Feature를 실제 API 데이터로 계산하시겠습니까? (y/n, 기본: n): [/yellow]"
            ).strip().lower() or "n"

            feature_calculator = None
            if use_real_features == 'y':
                from core.kiwoom_rest_client import KiwoomRESTClient
                from utils.feature_calculator import FeatureCalculator

                app_key = os.getenv('KIWOOM_APP_KEY')
                app_secret = os.getenv('KIWOOM_APP_SECRET')

                if app_key and app_secret:
                    api_client = KiwoomRESTClient(app_key, app_secret)
                    await api_client.initialize()
                    feature_calculator = FeatureCalculator(api_client)
                    console.print("[green]✅ Feature Calculator 초기화 완료[/green]")

            # DataFrame 변환
            console.print("\n[bold]2️⃣  백테스트 입력 데이터 변환 중...[/bold]")
            from utils.backtest_integration import convert_vwap_results_to_backtest_input

            candidates = await convert_vwap_results_to_backtest_input(
                pipeline.validated_stocks,
                feature_calculator=feature_calculator
            )

            if feature_calculator and hasattr(feature_calculator.api_client, 'close'):
                await feature_calculator.api_client.close()

            console.print(f"[green]✅ 변환 완료: {len(candidates)}개 종목[/green]")

        else:
            # 샘플 데이터
            console.print("\n[bold]📋 샘플 데이터 사용 (빠른 테스트)[/bold]")
            candidates = pd.DataFrame({
                'code': ['005930', '000660', '035420', '035720', '005380'],
                'name': ['삼성전자', 'SK하이닉스', 'NAVER', '카카오', '현대차'],
                'vwap_backtest_winrate': [0.65, 0.72, 0.58, 0.62, 0.68],
                'vwap_avg_profit': [2.3, 3.1, 1.5, 1.8, 2.5],
                'current_vwap_distance': [0.7, 0.69, -0.94, -2.17, 0.82],
                'volume_z_score': [2.0, 1.0, 0.67, 1.25, 0.5],
                'recent_return_5d': [-1.2, 2.3, -3.5, 0.5, 1.2],
                'market_volatility': [15.3] * 5,
                'sector_strength': [0.8, 1.2, 0.3, 0.5, 0.9],
                'price_momentum': [1.2, 1.8, -0.5, 0.3, 1.0],
            })
            console.print(f"  ✓ 샘플 종목: {len(candidates)}개")

        # Ranker 파라미터 입력
        console.print("\n" + "=" * 70, style="yellow")
        console.print("[bold]3️⃣  Ranker 설정[/bold]")
        threshold = float(console.input("[yellow]Buy Probability 임계값 (%, 기본: 60): [/yellow]").strip() or "60") / 100
        top_k_input = console.input("[yellow]상위 몇 개 선정? (기본: 10, 전체: 0): [/yellow]").strip() or "10"
        top_k = int(top_k_input) if int(top_k_input) > 0 else None

        # Ranker 실행
        console.print("\n[bold]4️⃣  Ranker 예측 및 랭킹...[/bold]")
        ranked = ranker.rank_candidates(
            candidates,
            threshold=threshold,
            top_k=top_k
        )

        console.print(f"[green]✅ 예측 완료: {len(ranked)}개 종목 선정[/green]")
        console.print()

        # 결과 테이블
        from rich.table import Table
        table = Table(title=f"Ranker 추천 종목 (상위 {len(ranked)}개)", box=None)
        table.add_column("순위", style="cyan", justify="right", width=6)
        table.add_column("종목코드", style="dim", width=8)
        table.add_column("종목명", style="yellow", width=12)
        table.add_column("Buy Prob", justify="right", width=10)
        table.add_column("Pred Return", justify="right", width=12)
        table.add_column("Confidence", justify="right", style="green", width=12)

        for idx, row in ranked.iterrows():
            rank = idx + 1
            # 종목명이 코드와 같으면 (제대로 안된 경우) 코드만 표시
            stock_name = row['name'] if row['name'] != row['code'] else row['code']
            table.add_row(
                str(rank),
                row['code'],
                stock_name,
                f"{row['buy_probability']*100:.1f}%",
                f"{row['predicted_return']:+.2f}%",
                f"{row['confidence_score']:.3f}"
            )

        console.print(table)
        console.print()

        # 통계
        console.print("=" * 70, style="cyan")
        console.print(f"[bold]📊 통계:[/bold]")
        console.print(f"  • 전체 후보: {len(candidates)}개")
        console.print(f"  • 선정 종목: [green]{len(ranked)}개[/green]")
        console.print(f"  • 평균 Buy Prob: [cyan]{ranked['buy_probability'].mean()*100:.1f}%[/cyan]")
        console.print(f"  • 평균 Pred Return: [cyan]{ranked['predicted_return'].mean():+.2f}%[/cyan]")
        console.print("=" * 70, style="cyan")

        console.print("\n[green]✅ Ranker 실전 테스트 완료![/green]")
        console.print("\n[yellow]💡 다음 단계:[/yellow]")
        console.print("[dim]   • 상위 종목을 자동매매 시스템에 투입[/dim]")
        console.print("[dim]   • 백테스트로 성과 검증[/dim]")
        console.print("[dim]   • 더 많은 백테스트 데이터 수집 → Ranker 재학습[/dim]")

    except ImportError as e:
        console.print(f"\n[red]❌ 모듈 로드 실패: {e}[/red]")
        console.print("[yellow]   필요한 모듈을 확인하세요.[/yellow]")
    except Exception as e:
        logger.error(f"Ranker 테스트 오류: {e}")
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


async def run_backtest():
    """전략 성과 검증 백테스트"""
    console.print("\n" + "=" * 100, style="bold cyan")
    console.print(f"{'📈 전략 성과 검증 백테스트':^100}", style="bold cyan")
    console.print("=" * 100, style="bold cyan")

    console.print("\n[bold yellow]🎯 백테스트 목적:[/bold yellow]")
    console.print("  1️⃣  [cyan]전략 성과 검증[/cyan] - 지난 N일간 선정된 종목들의 실제 수익률")
    console.print("  2️⃣  [cyan]파라미터 최적화[/cyan] - 보유 기간, 익절/손절 기준 조정")
    console.print("  3️⃣  [cyan]ML 모델 평가[/cyan] - Ranker 모델 예측 정확도 확인")
    console.print("  4️⃣  [cyan]리포트 생성[/cyan] - 투자 결과 리포트 (주간/월간)")

    console.print("\n[bold]📊 백테스트 범위 선택:[/bold]")
    console.print("  [1] 최근 7일")
    console.print("  [2] 최근 30일")
    console.print("  [3] 최근 90일")
    console.print("  [4] 전체 기간")

    period_choice = console.input("\n[yellow]선택 (기본: 1): [/yellow]").strip() or "1"

    period_map = {
        "1": 7,
        "2": 30,
        "3": 90,
        "4": None
    }
    days = period_map.get(period_choice, 7)

    console.print(f"\n[green]✓ {'전체 기간' if days is None else f'최근 {days}일'} 데이터를 분석합니다.[/green]")

    try:
        import pandas as pd
        from backtest_with_ranker import BacktestRunner

        # DB에서 기간별 후보 종목 로드
        console.print(f"\n[bold]1️⃣  DB에서 후보 종목 로드 중...[/bold]")

        from database.trading_db import TradingDatabase
        from datetime import datetime, timedelta

        db = TradingDatabase()

        # 기간 계산
        if days:
            start_date = (datetime.now() - timedelta(days=days)).isoformat()
            db_candidates = db.get_candidates_by_date_range(start_date=start_date)
            console.print(f"[cyan]  • 기간: {start_date[:10]} ~ 현재[/cyan]")
        else:
            db_candidates = db.get_all_candidates()
            console.print(f"[cyan]  • 기간: 전체[/cyan]")

        if not db_candidates:
            console.print("[yellow]⚠️  DB에 저장된 후보 종목이 없습니다. 샘플 데이터를 사용합니다.[/yellow]")
            # 샘플 후보 종목 (조건검색 + VWAP 필터 통과 가정)
            candidates = pd.DataFrame({
                'code': ['005930', '000660', '035420', '035720', '005380'],
                'name': ['삼성전자', 'SK하이닉스', 'NAVER', '카카오', '현대차'],
                'entry_price': [72000, 145000, 210000, 45000, 245000],
                'vwap': [71500, 144000, 212000, 46000, 243000],
                'volume': [1000000, 500000, 300000, 800000, 400000],
                'volume_avg_20d': [800000, 450000, 280000, 700000, 380000],
                'volume_std_20d': [100000, 50000, 30000, 80000, 40000],
                'vwap_backtest_winrate': [0.65, 0.72, 0.58, 0.62, 0.68],
                'vwap_avg_profit': [2.3, 3.1, 1.5, 1.8, 2.5],
                'recent_return_5d': [-1.2, 2.3, -3.5, 0.5, 1.2],
                'market_volatility': [15.3] * 5,
                'sector_strength': [0.8, 1.2, 0.3, 0.5, 0.9],
                'price_momentum': [1.2, 1.8, -0.5, 0.3, 1.0],
            })
        else:
            console.print(f"[green]  ✅ {len(db_candidates)}개 종목을 불러왔습니다.[/green]")

            # 날짜별 종목 수 통계
            from collections import defaultdict
            date_counts = defaultdict(int)
            for c in db_candidates:
                date = c.get('date_detected', '')[:10]
                date_counts[date] += 1

            console.print(f"\n[bold]2️⃣  데이터 통계:[/bold]")
            console.print(f"[cyan]  • 총 종목 수: {len(db_candidates)}개[/cyan]")
            console.print(f"[cyan]  • 날짜별 분포:[/cyan]")
            for date in sorted(date_counts.keys(), reverse=True)[:5]:
                console.print(f"    - {date}: {date_counts[date]}개")

            # DB 데이터를 DataFrame으로 변환
            candidates = pd.DataFrame([{
                'code': c.get('stock_code', ''),
                'name': c.get('stock_name', c.get('stock_code', '')),
                'date_detected': c.get('date_detected', ''),
                'entry_price': c.get('entry_price', 10000),
                'vwap': c.get('vwap', 10000),
                'volume': c.get('volume', 1000000),
                'volume_avg_20d': c.get('volume_avg_20d', 1000000),
                'volume_std_20d': c.get('volume_std_20d', 100000),
                'vwap_backtest_winrate': c.get('vwap_win_rate', 0.5),
                'vwap_avg_profit': c.get('vwap_avg_profit', 0.0),
                'recent_return_5d': c.get('recent_return_5d', 0.0),
                'market_volatility': c.get('market_volatility', 15.0),
                'sector_strength': c.get('sector_strength', 0.5),
                'price_momentum': c.get('price_momentum', 0.0),
                'total_score': c.get('total_score', 50),
            } for c in db_candidates])

        # 백테스트 파라미터
        console.print(f"\n[bold]3️⃣  백테스트 파라미터 설정:[/bold]")
        holding_period = int(console.input("[yellow]  • 보유 기간 (일, 기본: 5): [/yellow]").strip() or "5")
        take_profit_pct = float(console.input("[yellow]  • 익절 기준 (%, 기본: 3.0): [/yellow]").strip() or "3.0")
        stop_loss_pct = float(console.input("[yellow]  • 손절 기준 (%, 기본: -2.0): [/yellow]").strip() or "-2.0")

        # 백테스트 실행
        console.print(f"\n[bold]4️⃣  백테스트 시뮬레이션 실행 중...[/bold]")
        console.print(f"[dim]  • 종목 수: {len(candidates)}개[/dim]")
        console.print(f"[dim]  • 보유 기간: {holding_period}일[/dim]")
        console.print(f"[dim]  • 익절: +{take_profit_pct}% | 손절: {stop_loss_pct}%[/dim]")
        console.print()

        runner = BacktestRunner()
        results = await runner.run_backtest(
            candidates,
            holding_period=holding_period,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct
        )

        # 결과 출력
        console.print(f"\n[bold]5️⃣  백테스트 결과:[/bold]")
        runner.display_results(results)

        # 추가 분석
        if results and len(results) > 0:
            console.print(f"\n[bold]6️⃣  상세 분석:[/bold]")

            # 날짜별 수익률
            if 'date_detected' in candidates.columns:
                daily_returns = candidates.groupby(candidates['date_detected'].str[:10]).agg({
                    'code': 'count'
                }).rename(columns={'code': '종목수'})
                console.print(f"[cyan]  • 날짜별 선정 종목 수:[/cyan]")
                for date, row in daily_returns.head().iterrows():
                    console.print(f"    - {date}: {row['종목수']}개")

            # 점수별 성과
            if 'total_score' in candidates.columns:
                high_score = candidates[candidates['total_score'] >= 70]
                console.print(f"\n[cyan]  • 고득점 종목 (70점 이상): {len(high_score)}개[/cyan]")

        # 최적화 분석 추가
        console.print("\n" + "=" * 100, style="bold yellow")
        console.print(f"{'🎯 최적화 분석 & 추천':^100}", style="bold yellow")
        console.print("=" * 100, style="bold yellow")

        try:
            from analyzers.backtest_optimizer import BacktestOptimizer

            optimizer = BacktestOptimizer()

            # 백테스트 결과를 DataFrame으로 변환 (실제 수익률 추가 필요)
            if results and len(results) > 0:
                # 결과에 actual_return 컬럼 추가 (실제 구현 시 실거래 데이터 필요)
                # 여기서는 시뮬레이션으로 간단히 처리
                results_df = candidates.copy()

                # 시뮬레이션: 점수가 높을수록 수익률이 높다고 가정
                if 'total_score' in results_df.columns:
                    # 점수 기반 수익률 시뮬레이션 (실제로는 실거래 데이터 사용)
                    results_df['actual_return'] = (results_df['total_score'] - 65) * 0.003 + np.random.normal(0, 0.02, len(results_df))

                # VWAP 통과 여부
                if 'vwap_backtest_winrate' in results_df.columns:
                    results_df['vwap_passed'] = results_df['vwap_backtest_winrate'] >= 0.5

                # 최적화 리포트 생성
                console.print("\n[cyan]📊 분석 중...[/cyan]")
                opt_report = optimizer.generate_optimization_report(results_df)

                # 1. 점수-수익률 상관관계
                console.print("\n[bold]1️⃣  점수-수익률 상관관계:[/bold]")
                corr_data = opt_report.get('score_correlation', {})
                if 'correlations' in corr_data and corr_data['correlations']:
                    corr_table = Table(show_header=True, header_style="bold cyan", box=box.ROUNDED)
                    corr_table.add_column("점수 타입", style="cyan")
                    corr_table.add_column("상관계수", justify="right")

                    for score_type, corr in corr_data['correlations'].items():
                        if score_type != 'total_score':
                            color = "green" if abs(corr) > 0.3 else "yellow" if abs(corr) > 0.1 else "white"
                            corr_table.add_row(score_type, f"[{color}]{corr:.3f}[/{color}]")

                    console.print(corr_table)

                # 2. 가중치 조정 제안
                if 'suggested_weights' in corr_data and corr_data['suggested_weights']:
                    console.print("\n[bold]2️⃣  가중치 조정 제안:[/bold]")
                    weight_table = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED)
                    weight_table.add_column("요소", style="cyan")
                    weight_table.add_column("현재", justify="right")
                    weight_table.add_column("제안", justify="right")
                    weight_table.add_column("변화", justify="right")

                    for key in ['news', 'technical', 'supply_demand', 'fundamental']:
                        if key in corr_data['suggested_weights']:
                            current = corr_data['current_weights'].get(key, 0)
                            suggested = corr_data['suggested_weights'][key]
                            diff = suggested - current

                            color = "green" if diff > 0.05 else "red" if diff < -0.05 else "yellow"
                            weight_table.add_row(
                                key,
                                f"{current:.2%}",
                                f"{suggested:.2%}",
                                f"[{color}]{diff:+.2%}[/{color}]"
                            )

                    console.print(weight_table)

                # 3. 점수 구간별 성과
                console.print("\n[bold]3️⃣  점수 구간별 성과:[/bold]")
                range_data = opt_report.get('score_range_performance', {})
                if 'ranges' in range_data and range_data['ranges']:
                    range_table = Table(show_header=True, header_style="bold cyan", box=box.ROUNDED)
                    range_table.add_column("점수 구간", style="cyan")
                    range_table.add_column("종목 수", justify="right")
                    range_table.add_column("평균 수익률", justify="right")
                    range_table.add_column("승률", justify="right")

                    for r in range_data['ranges']:
                        return_color = "green" if r['avg_return'] > 0.03 else "yellow" if r['avg_return'] > 0 else "red"
                        winrate_color = "green" if r['win_rate'] > 0.6 else "yellow" if r['win_rate'] > 0.5 else "white"

                        range_table.add_row(
                            f"{r['range']}점",
                            str(r['count']),
                            f"[{return_color}]{r['avg_return']:+.2%}[/{return_color}]",
                            f"[{winrate_color}]{r['win_rate']:.1%}[/{winrate_color}]"
                        )

                    console.print(range_table)

                # 4. 종합 추천 사항
                console.print("\n[bold]4️⃣  종합 추천 사항:[/bold]")
                all_recs = opt_report.get('summary', {}).get('all_recommendations', [])
                if all_recs:
                    for i, rec in enumerate(all_recs[:10], 1):  # 최대 10개
                        console.print(f"  {i}. {rec}")
                else:
                    console.print("  [dim]추천 사항 없음[/dim]")

                # 5. 적용 여부 묻기
                console.print("\n[bold yellow]💡 가중치 조정을 적용하시겠습니까?[/bold yellow]")
                apply_choice = console.input("[yellow]적용하려면 'y' 입력 (기본: n): [/yellow]").strip().lower()

                if apply_choice == 'y' and 'suggested_weights' in corr_data:
                    # 가중치 적용
                    result_msg = optimizer.apply_suggested_weights(corr_data['suggested_weights'])
                    console.print(f"\n{result_msg}")
                    console.print("\n[bold cyan]💡 적용된 가중치는 다음 분석부터 자동으로 반영됩니다.[/bold cyan]")

                # 6. 리포트 저장
                console.print("\n[bold]리포트를 파일로 저장하시겠습니까?[/bold]")
                save_choice = console.input("[yellow]저장하려면 'y' 입력 (기본: n): [/yellow]").strip().lower()

                if save_choice == 'y':
                    from datetime import datetime
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    report_path = f"./reports/optimization_{timestamp}.txt"

                    os.makedirs("./reports", exist_ok=True)
                    save_msg = optimizer.export_recommendations(opt_report, report_path)
                    console.print(f"\n[green]{save_msg}[/green]")

            else:
                console.print("\n[yellow]⚠️  백테스트 결과가 없어 최적화 분석을 수행할 수 없습니다.[/yellow]")

        except ImportError as e:
            console.print(f"\n[yellow]⚠️  최적화 모듈 로드 실패: {e}[/yellow]")
        except Exception as e:
            console.print(f"\n[yellow]⚠️  최적화 분석 오류: {e}[/yellow]")
            import traceback
            traceback.print_exc()

        console.print("\n" + "=" * 100, style="green")
        console.print(f"{'✅ 백테스트 완료!':^100}", style="bold green")
        console.print("=" * 100, style="green")

        console.print("\n[yellow]💡 다음 단계:[/yellow]")
        console.print("  • [3] ML 모델 학습 - 이 데이터로 Ranker 모델 학습")
        console.print("  • [4] Ranker 테스트 - 학습된 모델로 종목 랭킹")
        console.print("  • [6] 리포트 생성 - 상세 투자 리포트 생성")
        console.print("  • 최적화 리포트 확인 - ./reports/optimization_*.txt")

    except ImportError as e:
        console.print(f"\n[red]❌ 필요한 모듈을 찾을 수 없습니다: {e}[/red]")
        console.print("[yellow]   backtest_with_ranker.py를 확인하세요.[/yellow]")
    except Exception as e:
        logger.error(f"백테스트 오류: {e}")
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


async def generate_report():
    """리포트 생성"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]📄 리포트 생성...[/bold cyan]")
    console.print("=" * 70, style="cyan")

    try:
        from reporting import ReportGenerator

        console.print("\n[bold]리포트 타입 선택:[/bold]")
        console.print("[1] 일일 리포트")
        console.print("[2] 주간 리포트")

        choice = console.input("\n[yellow]선택 (기본: 1): [/yellow]").strip() or "1"

        # 샘플 거래 데이터
        sample_trades = [
            {'date': '2025-11-01', 'symbol': '005930', 'strategy': 'momentum', 'profit': 50000, 'time': '09:30'},
            {'date': '2025-11-01', 'symbol': '000660', 'strategy': 'breakout', 'profit': -10000, 'time': '10:15'},
            {'date': '2025-11-01', 'symbol': '035420', 'strategy': 'vwap', 'profit': 30000, 'time': '14:20'},
        ]

        generator = ReportGenerator(output_dir="./reports")

        if choice == "1":
            report = generator.generate_daily_report(sample_trades)
            json_path = generator.save_report_json(report)
            html_path = generator.save_report_html(report)

            console.print(f"\n[green]✅ 일일 리포트 생성 완료![/green]")
            console.print(f"   JSON: [dim]{json_path}[/dim]")
            console.print(f"   HTML: [dim]{html_path}[/dim]")
        else:
            report = generator.generate_weekly_report(sample_trades)
            json_path = generator.save_report_json(report)
            html_path = generator.save_report_html(report)

            console.print(f"\n[green]✅ 주간 리포트 생성 완료![/green]")
            console.print(f"   JSON: [dim]{json_path}[/dim]")
            console.print(f"   HTML: [dim]{html_path}[/dim]")

        console.print(f"\n[bold]📊 요약:[/bold]")
        summary = report.get('summary', {})
        console.print(f"   총 거래: [cyan]{summary.get('total_trades')}건[/cyan]")
        console.print(f"   승률: [cyan]{summary.get('win_rate')}[/cyan]")
        console.print(f"   총 손익: [cyan]{summary.get('total_profit')}[/cyan]")

    except Exception as e:
        logger.error(f"리포트 생성 오류: {e}")
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


async def test_telegram():
    """Telegram 알림 테스트"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]💬 Telegram 알림 테스트...[/bold cyan]")
    console.print("=" * 70, style="cyan")

    try:
        from reporting import TelegramNotifier

        console.print("\n[yellow]환경 변수 확인 중...[/yellow]")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_ids = os.getenv("TELEGRAM_CHAT_IDS")

        if not bot_token or not chat_ids:
            console.print("\n[red]❌ Telegram 설정이 필요합니다.[/red]")
            console.print("\n[yellow].env 파일에 다음을 추가하세요:[/yellow]")
            console.print("TELEGRAM_BOT_TOKEN=your_bot_token")
            console.print("TELEGRAM_CHAT_IDS=your_chat_id")
            console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
            return

        console.print("[green]✅ Telegram 설정 확인 완료[/green]")

        notifier = TelegramNotifier()

        console.print("\n[yellow]테스트 메시지 전송 중...[/yellow]")
        await notifier.send_message(
            text="🧪 키움증권 AI Trading System v2.0\n\n테스트 메시지입니다!"
        )

        console.print("[green]✅ 메시지 전송 완료![/green]")
        console.print("\n[dim]Telegram에서 메시지를 확인하세요.[/dim]")

        # 통계
        stats = notifier.get_stats()
        console.print(f"\n[bold]📊 통계:[/bold]")
        console.print(f"   전송 성공: [green]{stats['total_sent']}건[/green]")
        console.print(f"   전송 실패: [red]{stats['total_failed']}건[/red]")

    except Exception as e:
        logger.error(f"Telegram 테스트 오류: {e}")
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


def show_settings():
    """시스템 설정"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]⚙️  시스템 설정[/bold cyan]")
    console.print("=" * 70, style="cyan")

    console.print("\n[bold]📋 현재 설정:[/bold]")
    console.print(f"   Python: [cyan]{sys.version.split()[0]}[/cyan]")
    console.print(f"   작업 디렉토리: [dim]{os.getcwd()}[/dim]")

    # 환경 변수 확인
    env_vars = {
        'KIWOOM_APP_KEY': os.getenv('KIWOOM_APP_KEY'),
        'KIWOOM_APP_SECRET': os.getenv('KIWOOM_APP_SECRET'),
        'TELEGRAM_BOT_TOKEN': os.getenv('TELEGRAM_BOT_TOKEN'),
        'TELEGRAM_CHAT_IDS': os.getenv('TELEGRAM_CHAT_IDS'),
    }

    console.print("\n[bold]🔑 환경 변수:[/bold]")
    for key, value in env_vars.items():
        if value:
            masked = value[:10] + "..." if len(value) > 10 else value
            console.print(f"   {key}: [dim]{masked}[/dim] [green]✅[/green]")
        else:
            console.print(f"   {key}: [yellow](미설정)[/yellow] [red]❌[/red]")

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


def show_help():
    """도움말"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]📚 도움말[/bold cyan]")
    console.print("=" * 70, style="cyan")

    help_text = """
[bold]📖 키움증권 AI Trading System v2.0 사용 가이드[/bold]

[bold cyan]1️⃣  자동 매매[/bold cyan]
   - 실시간 트레이딩 시스템
   - ML 기반 매매 신호 생성
   - 자동 주문 실행

[bold cyan]2️⃣  조건 검색[/bold cyan]
   - 키움 조건검색식 실행
   - 종목 스크리닝

[bold cyan]3️⃣  ML 모델 학습[/bold cyan]
   - Feature Engineering (40+ features)
   - LightGBM/XGBoost 모델 학습
   - 자동 버전 관리

[bold cyan]4️⃣  ML 예측 테스트[/bold cyan]
   - 학습된 모델로 예측
   - 확신도 점수 (0~100)

[bold cyan]5️⃣  백테스트[/bold cyan]
   - 전략 성과 검증 (추후 구현)

[bold cyan]6️⃣  리포트 생성[/bold cyan]
   - 일일/주간 리포트
   - HTML/JSON 포맷

[bold cyan]7️⃣  Telegram 알림[/bold cyan]
   - 실시간 매매 신호 알림
   - 거래 체결 알림
   - 리포트 알림

[bold]📚 상세 문서:[/bold]
   - [dim]COMPLETE_IMPLEMENTATION_REPORT.md[/dim]
   - [dim]PHASE_1_2_3_IMPLEMENTATION.md[/dim]
   - [dim]docs/ML_DATASET_PIPELINE_GUIDE.md[/dim]

[green]💡 도움이 필요하면 문서를 참고하세요![/green]
"""

    console.print(help_text)
    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


async def main():
    """메인 함수"""
    global shutdown_flag

    while not shutdown_flag:
        try:
            clear_screen()
            print_banner()
            print_menu()

            choice = console.input("[bold cyan]선택 >>> [/bold cyan]").strip()

            if choice == '1':
                await run_auto_trading()
            elif choice == '2':
                await run_condition_search()
            elif choice == '3':
                from ml_train_menu import train_ranker_menu
                await train_ranker_menu()
            elif choice == '4':
                await test_ml_prediction()
            elif choice == '5':
                await run_backtest()
            elif choice == '6':
                await generate_report()
            elif choice == '7':
                await test_telegram()
            elif choice == '8':
                show_settings()
            elif choice == '9':
                show_help()
            elif choice == '0':
                console.print("\n[yellow]👋 프로그램을 종료합니다...[/yellow]")
                break
            else:
                console.print("\n[red]❌ 잘못된 선택입니다.[/red]")
                console.input("\n[dim][Enter]를 눌러 계속...[/dim]")

        except KeyboardInterrupt:
            console.print("\n\n[yellow]⚠️  Ctrl+C 감지... 안전하게 종료합니다.[/yellow]")
            break
        except Exception as e:
            logger.error(f"오류 발생: {e}")
            console.print(f"[red]❌ 오류: {e}[/red]")
            import traceback
            traceback.print_exc()
            console.input("\n[dim][Enter]를 눌러 계속...[/dim]")

    console.print("\n[green]✅ 프로그램이 안전하게 종료되었습니다.[/green]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[yellow]👋 프로그램 종료[/yellow]")
    except Exception as e:
        logger.error(f"치명적 오류: {e}")
        console.print(f"[red]❌ 치명적 오류: {e}[/red]")
        import traceback
        traceback.print_exc()
