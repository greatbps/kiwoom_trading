#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ml_train_menu.py

Candidate Ranker 학습 메뉴 (main_menu.py에서 호출)
"""

import logging
from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


async def train_ranker_menu():
    """Candidate Ranker 모델 학습"""
    console.print("\n" + "=" * 70, style="cyan")
    console.print("[bold cyan]📊 Candidate Ranker 학습[/bold cyan]")
    console.print("=" * 70, style="cyan")

    console.print("\n[bold]🎯 Ranker의 역할:[/bold]")
    console.print("  • 조건검색 + VWAP 통과 종목을 점수화")
    console.print("  • buy_probability와 predicted_return 산출")
    console.print("  • 상위 K개만 실제 매매 대상으로 선정")

    console.print("\n[bold]📌 학습 단계:[/bold]")
    console.print("  1️⃣  백테스트 결과 로드 (학습 데이터)")
    console.print("  2️⃣  Feature 추출 및 전처리")
    console.print("  3️⃣  LightGBM 모델 학습 (Classifier + Regressor)")
    console.print("  4️⃣  모델 평가 및 저장")

    console.print("\n" + "=" * 70, style="cyan")
    choice = console.input("[yellow]Ranker 학습을 시작하시겠습니까? (y/n, 기본: y): [/yellow]").strip().lower() or "y"

    if choice != 'y':
        console.print("[yellow]취소되었습니다.[/yellow]")
        console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")
        return

    try:
        from ml.training_data_builder import TrainingDataBuilder
        from ml.candidate_ranker import CandidateRanker

        # Step 1: 학습 데이터 로드
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]1️⃣  백테스트 결과 로드 중...[/bold]")
        console.print("=" * 70, style="cyan")

        builder = TrainingDataBuilder()
        dataset = builder.build_training_dataset(date_range=60)

        if dataset is None or len(dataset) < 100:
            console.print("\n[yellow]⚠️  백테스트 결과가 충분하지 않습니다.[/yellow]")
            console.print("[yellow]   합성 데이터로 테스트 학습을 진행합니다.[/yellow]")
            dataset = builder.generate_synthetic_data(n_samples=500)

        console.print(f"\n[green]✅ 학습 데이터 준비 완료: {len(dataset)}개 샘플[/green]")
        console.print(f"   승률: [cyan]{dataset['is_profitable'].mean() * 100:.1f}%[/cyan]")
        console.print(f"   평균 수익률: [cyan]{dataset['actual_profit_pct'].mean():.2f}%[/cyan]")

        # Step 2: 모델 학습
        console.print("\n" + "=" * 70, style="cyan")
        console.print("[bold]2️⃣  LightGBM 모델 학습 중...[/bold]")
        console.print("=" * 70, style="cyan")

        ranker = CandidateRanker()
        metrics = ranker.train(dataset, target_col='actual_profit_pct')

        console.print(f"\n[green]✅ 모델 학습 완료[/green]")
        console.print(f"\n[bold]📊 성능 지표:[/bold]")
        console.print(f"   Classifier AUC: [cyan]{metrics['classifier']['auc']:.3f}[/cyan]")
        console.print(f"   Classifier Accuracy: [cyan]{metrics['classifier']['accuracy']:.3f}[/cyan]")
        console.print(f"   Regressor RMSE: [cyan]{metrics['regressor']['rmse']:.3f}[/cyan]")
        console.print(f"   Regressor MAE: [cyan]{metrics['regressor']['mae']:.3f}[/cyan]")

        # Step 3: Feature Importance
        console.print("\n[bold]🔍 Feature Importance:[/bold]")
        importance = ranker.get_feature_importance()
        for idx, row in importance.head(5).iterrows():
            console.print(f"   {idx+1}. {row['feature']}: [cyan]{row['importance_classifier']:.0f}[/cyan]")

        console.print("\n" + "=" * 70, style="green")
        console.print("[bold green]✅ Ranker 학습 완료![/bold green]")
        console.print("=" * 70, style="green")
        console.print(f"\n[bold]📁 모델 저장 위치:[/bold]")
        console.print(f"   [dim]./models/ranker/[/dim]")

    except ImportError as e:
        console.print(f"\n[red]❌ 필요한 라이브러리가 설치되지 않았습니다: {e}[/red]")
        console.print("[yellow]   설치 명령: pip install lightgbm scikit-learn pandas numpy[/yellow]")
    except Exception as e:
        logger.error(f"Ranker 학습 오류: {e}")
        console.print(f"[red]❌ 오류: {e}[/red]")
        import traceback
        traceback.print_exc()

    console.input("\n[dim][Enter]를 눌러 메인 메뉴로 돌아가기...[/dim]")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(train_ranker_menu())
