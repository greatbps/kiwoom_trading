#!/usr/bin/env python3
"""
Phase 3-2: Bayesian Optimization으로 알파 가중치 미세 조정

Grid Search 결과를 기반으로 Bayesian Optimization을 적용하여
최적 가중치를 소수점 단위로 정밀하게 찾습니다.

Requirements:
    pip install scikit-optimize
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import json
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

# Bayesian Optimization
from skopt import gp_minimize
from skopt.space import Real
from skopt.utils import use_named_args

# Alpha Engine
from trading.alpha_engine import SimonsStyleAlphaEngine
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha

console = Console()


def create_mock_scenarios():
    """
    Mock 시나리오 생성 (phase3_grid_search.py와 동일)

    Returns:
        30개 시나리오 (승리 10, 손실 15, 차단 5)
    """

    # 10개 승리 케이스 (모든 알파 긍정, 실제 수익률 높음)
    win_cases = [
        {"vwap": 2.5, "volume": 2.0, "obv": 1.5, "inst": 1.0, "news": 70, "actual_return": 3.5},
        {"vwap": 2.2, "volume": 1.8, "obv": 1.2, "inst": 0.8, "news": 75, "actual_return": 3.2},
        {"vwap": 2.8, "volume": 2.5, "obv": 1.8, "inst": 1.2, "news": 80, "actual_return": 4.0},
        {"vwap": 2.0, "volume": 1.5, "obv": 1.0, "inst": 0.6, "news": 72, "actual_return": 2.8},
        {"vwap": 2.4, "volume": 2.2, "obv": 1.4, "inst": 1.0, "news": 78, "actual_return": 3.6},
        {"vwap": 2.1, "volume": 1.7, "obv": 1.1, "inst": 0.7, "news": 73, "actual_return": 3.0},
        {"vwap": 2.6, "volume": 2.3, "obv": 1.6, "inst": 1.1, "news": 76, "actual_return": 3.8},
        {"vwap": 2.3, "volume": 1.9, "obv": 1.3, "inst": 0.9, "news": 74, "actual_return": 3.3},
        {"vwap": 2.7, "volume": 2.4, "obv": 1.7, "inst": 1.3, "news": 79, "actual_return": 3.9},
        {"vwap": 1.9, "volume": 1.6, "obv": 0.9, "inst": 0.5, "news": 71, "actual_return": 2.7},
    ]

    # 15개 손실 케이스 (혼재 신호, 실제 손실)
    loss_cases = [
        {"vwap": 1.5, "volume": 0.5, "obv": -0.5, "inst": 0.2, "news": 55, "actual_return": -1.2},
        {"vwap": 1.2, "volume": 0.3, "obv": -0.8, "inst": 0.1, "news": 52, "actual_return": -1.5},
        {"vwap": 1.8, "volume": 0.8, "obv": -0.2, "inst": 0.4, "news": 58, "actual_return": -0.9},
        {"vwap": 1.0, "volume": 0.2, "obv": -1.0, "inst": 0.0, "news": 50, "actual_return": -1.8},
        {"vwap": 1.4, "volume": 0.6, "obv": -0.4, "inst": 0.3, "news": 54, "actual_return": -1.3},
        {"vwap": 1.3, "volume": 0.4, "obv": -0.6, "inst": 0.2, "news": 53, "actual_return": -1.4},
        {"vwap": 1.6, "volume": 0.7, "obv": -0.3, "inst": 0.4, "news": 56, "actual_return": -1.0},
        {"vwap": 1.1, "volume": 0.3, "obv": -0.9, "inst": 0.1, "news": 51, "actual_return": -1.6},
        {"vwap": 1.7, "volume": 0.9, "obv": -0.1, "inst": 0.5, "news": 57, "actual_return": -0.8},
        {"vwap": 0.9, "volume": 0.1, "obv": -1.2, "inst": -0.1, "news": 48, "actual_return": -2.0},
        {"vwap": 1.5, "volume": 0.6, "obv": -0.5, "inst": 0.3, "news": 55, "actual_return": -1.1},
        {"vwap": 1.2, "volume": 0.4, "obv": -0.7, "inst": 0.2, "news": 52, "actual_return": -1.5},
        {"vwap": 1.4, "volume": 0.5, "obv": -0.6, "inst": 0.2, "news": 54, "actual_return": -1.3},
        {"vwap": 1.6, "volume": 0.8, "obv": -0.2, "inst": 0.4, "news": 56, "actual_return": -0.9},
        {"vwap": 1.1, "volume": 0.2, "obv": -1.0, "inst": 0.0, "news": 51, "actual_return": -1.7},
    ]

    # 5개 차단 케이스 (약한 신호, 큰 손실 - 반드시 거부해야 함)
    block_cases = [
        {"vwap": 0.8, "volume": -1.0, "obv": -2.0, "inst": -0.5, "news": 45, "actual_return": -4.5},
        {"vwap": 0.5, "volume": -1.5, "obv": -2.5, "inst": -1.0, "news": 40, "actual_return": -5.2},
        {"vwap": 0.6, "volume": -1.2, "obv": -2.2, "inst": -0.7, "news": 42, "actual_return": -4.8},
        {"vwap": 0.7, "volume": -0.8, "obv": -1.8, "inst": -0.4, "news": 44, "actual_return": -4.2},
        {"vwap": 0.4, "volume": -1.8, "obv": -2.8, "inst": -1.2, "news": 38, "actual_return": -5.5},
    ]

    return win_cases + loss_cases + block_cases


def create_mock_state(scenario: dict) -> dict:
    """Mock 상태 생성 (알파 엔진 입력용)"""
    import pandas as pd
    from datetime import datetime, timedelta

    # 60개 데이터 포인트 생성
    dates = pd.date_range(end=datetime.now(), periods=60, freq='5min')

    # VWAP 신호 반영
    base_price = 50000
    vwap_strength = scenario['vwap']
    prices = [base_price + (i * 100 * vwap_strength) for i in range(60)]

    # Volume 신호 반영
    volume_strength = scenario['volume']
    volumes = [10000 * (1 + volume_strength) for _ in range(60)]

    df = pd.DataFrame({
        'date': dates,
        'close': prices,
        'volume': volumes,
        'vwap': [p * 0.98 for p in prices],  # VWAP은 가격 대비 약간 낮게
    })

    # OBV 계산
    df['obv'] = scenario['obv'] * 1000000

    # 기관/외인 수급
    institutional_flow = {
        'inst_net_buy': scenario['inst'] * 10000000,
        'foreign_net_buy': scenario['inst'] * 5000000,
        'total_traded_value': 100000000
    }

    # AI 뉴스 분석 (News Score)
    ai_analysis = {
        'news_score': scenario['news']
    }

    return {
        'df': df,
        'df_5m': df,
        'institutional_flow': institutional_flow,
        'ai_analysis': ai_analysis
    }


def evaluate_weights(weights, scenarios, threshold=1.0, verbose=False):
    """
    가중치 조합 평가

    Args:
        weights: [vwap, volume, obv, inst, news]
        scenarios: 시나리오 리스트
        threshold: 매수 임계값
        verbose: 상세 로그 출력

    Returns:
        dict: 평가 결과
    """
    vwap_w, volume_w, obv_w, inst_w, news_w = weights

    # 알파 엔진 생성
    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=vwap_w),
            VolumeSpikeAlpha(weight=volume_w, lookback=40),
            OBVTrendAlpha(weight=obv_w, fast=5, slow=20),
            InstitutionalFlowAlpha(weight=inst_w),
            NewsScoreAlpha(weight=news_w),
        ]
    )

    # 시나리오 평가
    trades = []

    for scenario in scenarios:
        state = create_mock_state(scenario)
        result = engine.compute("TEST", state)

        # aggregate_score가 임계값을 넘으면 매수
        if result["aggregate_score"] > threshold:
            trades.append({
                "aggregate_score": result["aggregate_score"],
                "actual_return": scenario["actual_return"]
            })

    if len(trades) == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "total_return": 0.0,
            "sharpe_ratio": 0.0,
            "score": 0.0
        }

    # 성과 지표 계산
    returns = [t["actual_return"] for t in trades]
    winning_trades = len([r for r in returns if r > 0])

    win_rate = (winning_trades / len(trades)) * 100
    avg_return = np.mean(returns)
    total_return = sum(returns)
    sharpe_ratio = avg_return / np.std(returns) if np.std(returns) > 0 else 0

    # 종합 점수 (승률 40% + 평균 수익률 30% + Sharpe Ratio 30%)
    score = (win_rate * 0.4) + (avg_return * 10 * 0.3) + (sharpe_ratio * 5 * 0.3)

    if verbose:
        console.print(f"  Weights: [{vwap_w:.2f}, {volume_w:.2f}, {obv_w:.2f}, {inst_w:.2f}, {news_w:.2f}]")
        console.print(f"  Trades: {len(trades)}, Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.2f}%")
        console.print(f"  Sharpe: {sharpe_ratio:.2f}, Score: {score:.2f}")
        console.print()

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "avg_return": avg_return,
        "total_return": total_return,
        "sharpe_ratio": sharpe_ratio,
        "score": score
    }


def run_bayesian_optimization():
    """Bayesian Optimization 실행"""

    console.print("=" * 100)
    console.print("🧠 Phase 3-2: Bayesian Optimization (알파 가중치 미세 조정)", style="bold green")
    console.print("=" * 100)
    console.print()

    # Mock 시나리오 생성
    scenarios = create_mock_scenarios()
    console.print(f"📊 시나리오: {len(scenarios)}개 (승리 10 / 손실 15 / 차단 5)")
    console.print()

    # Grid Search 결과 로드 (초기값으로 사용)
    grid_result_path = project_root / "data" / "phase3_grid_search_results.json"

    if grid_result_path.exists():
        with open(grid_result_path, 'r', encoding='utf-8') as f:
            grid_results = json.load(f)

        grid_best_weights = grid_results['best']['weights']
        console.print("📌 Grid Search 최적값 (시작점):")
        console.print(f"   VWAP: {grid_best_weights[0]}, Vol: {grid_best_weights[1]}, "
                     f"OBV: {grid_best_weights[2]}, Inst: {grid_best_weights[3]}, News: {grid_best_weights[4]}")
        console.print(f"   승률: {grid_results['best']['win_rate']:.1f}%, "
                     f"평균 수익: {grid_results['best']['avg_return']:.2f}%, "
                     f"Sharpe: {grid_results['best']['sharpe_ratio']:.2f}")
        console.print()
    else:
        # Grid Search 결과가 없으면 기본값 사용
        grid_best_weights = [1.5, 1.0, 0.5, 0.5, 1.0]
        console.print("[yellow]⚠️  Grid Search 결과가 없습니다. 기본값 사용.[/yellow]")
        console.print()

    # Bayesian Optimization 검색 공간 정의
    # Grid Search 최적값 주변을 더 세밀하게 탐색 (±30% 범위)
    search_space = [
        Real(grid_best_weights[0] * 0.7, grid_best_weights[0] * 1.3, name='vwap'),
        Real(grid_best_weights[1] * 0.7, grid_best_weights[1] * 1.3, name='volume'),
        Real(grid_best_weights[2] * 0.5, grid_best_weights[2] * 1.5, name='obv'),
        Real(grid_best_weights[3] * 0.5, grid_best_weights[3] * 1.5, name='inst'),
        Real(grid_best_weights[4] * 0.7, grid_best_weights[4] * 1.3, name='news'),
    ]

    console.print("🔍 Bayesian Optimization 검색 공간:")
    console.print(f"   VWAP:   [{search_space[0].low:.2f}, {search_space[0].high:.2f}]")
    console.print(f"   Volume: [{search_space[1].low:.2f}, {search_space[1].high:.2f}]")
    console.print(f"   OBV:    [{search_space[2].low:.2f}, {search_space[2].high:.2f}]")
    console.print(f"   Inst:   [{search_space[3].low:.2f}, {search_space[3].high:.2f}]")
    console.print(f"   News:   [{search_space[4].low:.2f}, {search_space[4].high:.2f}]")
    console.print()

    # 목적 함수 정의 (최소화 문제로 변환: -score를 반환)
    @use_named_args(search_space)
    def objective(**params):
        weights = [params['vwap'], params['volume'], params['obv'], params['inst'], params['news']]
        result = evaluate_weights(weights, scenarios, threshold=1.0)
        # Bayesian Optimization은 최소화 문제이므로 -score 반환
        return -result['score']

    # Bayesian Optimization 실행
    console.print("🚀 Bayesian Optimization 시작 (50회 반복)...")
    console.print("[dim]Gaussian Process를 사용한 확률적 탐색 진행 중...[/dim]")
    console.print()

    n_calls = 50  # 50회 평가 (Grid Search 288회 대비 훨씬 효율적)

    result = gp_minimize(
        objective,
        search_space,
        n_calls=n_calls,
        n_initial_points=10,  # 초기 랜덤 탐색 10회
        acq_func='EI',  # Expected Improvement
        random_state=42,
        verbose=False
    )

    console.print(f"✅ 최적화 완료 ({n_calls}회 평가)")
    console.print()

    # 최적 가중치
    optimal_weights = result.x
    optimal_score = -result.fun  # 최소화 문제였으므로 다시 -를 곱함

    console.print("=" * 100)
    console.print("🏆 Bayesian Optimization 결과", style="bold yellow")
    console.print("=" * 100)
    console.print()

    # 최적 가중치로 재평가 (상세 정보 출력)
    optimal_result = evaluate_weights(optimal_weights, scenarios, threshold=1.0, verbose=False)

    console.print("✅ 최적 가중치 (Bayesian):")
    console.print(f"   VWAP:   {optimal_weights[0]:.3f}")
    console.print(f"   Volume: {optimal_weights[1]:.3f}")
    console.print(f"   OBV:    {optimal_weights[2]:.3f}")
    console.print(f"   Inst:   {optimal_weights[3]:.3f}")
    console.print(f"   News:   {optimal_weights[4]:.3f}")
    console.print()

    console.print("📊 성과 지표:")
    console.print(f"   거래 건수:    {optimal_result['trades']}건")
    console.print(f"   승률:         {optimal_result['win_rate']:.2f}%")
    console.print(f"   평균 수익률:  {optimal_result['avg_return']:.2f}%")
    console.print(f"   총 수익률:    {optimal_result['total_return']:.2f}%")
    console.print(f"   Sharpe Ratio: {optimal_result['sharpe_ratio']:.2f}")
    console.print(f"   종합 점수:    {optimal_result['score']:.2f}")
    console.print()

    # Grid Search 결과와 비교
    if grid_result_path.exists():
        grid_baseline_result = evaluate_weights(grid_best_weights, scenarios, threshold=1.0)

        console.print("=" * 100)
        console.print("📈 개선율 비교 (Grid Search → Bayesian)", style="bold cyan")
        console.print("=" * 100)
        console.print()

        # 비교 테이블
        comparison_table = Table(title="성과 비교", show_header=True, header_style="bold magenta")
        comparison_table.add_column("지표", style="cyan")
        comparison_table.add_column("Grid Search", justify="right", style="yellow")
        comparison_table.add_column("Bayesian", justify="right", style="green")
        comparison_table.add_column("개선율", justify="right", style="bold")

        win_rate_improve = ((optimal_result['win_rate'] - grid_baseline_result['win_rate']) / grid_baseline_result['win_rate']) * 100 if grid_baseline_result['win_rate'] > 0 else 0
        avg_return_improve = ((optimal_result['avg_return'] - grid_baseline_result['avg_return']) / grid_baseline_result['avg_return']) * 100 if grid_baseline_result['avg_return'] > 0 else 0
        sharpe_improve = ((optimal_result['sharpe_ratio'] - grid_baseline_result['sharpe_ratio']) / grid_baseline_result['sharpe_ratio']) * 100 if grid_baseline_result['sharpe_ratio'] > 0 else 0
        score_improve = ((optimal_result['score'] - grid_baseline_result['score']) / grid_baseline_result['score']) * 100 if grid_baseline_result['score'] > 0 else 0

        comparison_table.add_row("승률", f"{grid_baseline_result['win_rate']:.2f}%", f"{optimal_result['win_rate']:.2f}%", f"{win_rate_improve:+.1f}%")
        comparison_table.add_row("평균 수익률", f"{grid_baseline_result['avg_return']:.2f}%", f"{optimal_result['avg_return']:.2f}%", f"{avg_return_improve:+.1f}%")
        comparison_table.add_row("Sharpe Ratio", f"{grid_baseline_result['sharpe_ratio']:.2f}", f"{optimal_result['sharpe_ratio']:.2f}", f"{sharpe_improve:+.1f}%")
        comparison_table.add_row("종합 점수", f"{grid_baseline_result['score']:.2f}", f"{optimal_result['score']:.2f}", f"{score_improve:+.1f}%")

        console.print(comparison_table)
        console.print()

    # 결과 저장
    output_path = project_root / "data" / "phase3_bayesian_results.json"

    output_data = {
        "optimization_date": datetime.now().isoformat(),
        "n_calls": n_calls,
        "search_space": {
            "vwap": [search_space[0].low, search_space[0].high],
            "volume": [search_space[1].low, search_space[1].high],
            "obv": [search_space[2].low, search_space[2].high],
            "inst": [search_space[3].low, search_space[3].high],
            "news": [search_space[4].low, search_space[4].high],
        },
        "grid_baseline": {
            "weights": grid_best_weights,
            "score": grid_baseline_result['score'] if grid_result_path.exists() else 0,
            "win_rate": grid_baseline_result['win_rate'] if grid_result_path.exists() else 0,
            "avg_return": grid_baseline_result['avg_return'] if grid_result_path.exists() else 0,
            "sharpe_ratio": grid_baseline_result['sharpe_ratio'] if grid_result_path.exists() else 0,
        },
        "bayesian_optimal": {
            "weights": [float(w) for w in optimal_weights],
            "trades": optimal_result['trades'],
            "win_rate": optimal_result['win_rate'],
            "avg_return": optimal_result['avg_return'],
            "total_return": optimal_result['total_return'],
            "sharpe_ratio": optimal_result['sharpe_ratio'],
            "score": optimal_result['score'],
        },
        "improvement": {
            "win_rate": win_rate_improve if grid_result_path.exists() else 0,
            "avg_return": avg_return_improve if grid_result_path.exists() else 0,
            "sharpe_ratio": sharpe_improve if grid_result_path.exists() else 0,
            "score": score_improve if grid_result_path.exists() else 0,
        }
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    console.print(f"💾 결과 저장: {output_path}")
    console.print()

    console.print("=" * 100)
    console.print("✅ Phase 3-2 완료!", style="bold green")
    console.print("=" * 100)
    console.print()
    console.print("[cyan]💡 다음 단계: 실전 시스템에 Bayesian 최적 가중치 적용[/cyan]")
    console.print()

    return optimal_weights, optimal_result


if __name__ == "__main__":
    try:
        run_bayesian_optimization()
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️  최적화가 중단되었습니다.[/yellow]")
    except Exception as e:
        console.print()
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()
