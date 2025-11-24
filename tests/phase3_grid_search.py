"""
Phase 3-1: 알파 가중치 그리드 서치 최적화

목적: 최적의 알파 가중치 조합을 찾아 승률/수익률 개선
방법: 그리드 서치 (O(N) 알고리즘)
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np
from itertools import product
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn
import json

from trading.alpha_engine import SimonsStyleAlphaEngine
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha

console = Console()


def create_mock_scenarios():
    """
    백테스트용 Mock 시나리오 생성

    실제 거래 패턴을 반영한 30개 시나리오:
    - 승리 케이스 (10개): 모든 알파 긍정적
    - 손실 케이스 (15개): 일부 알파 부정적
    - 차단 케이스 (5개): 대부분 알파 부정적
    """
    scenarios = []

    # 1. 승리 케이스 (10개) - 강한 신호, 실제 수익
    win_cases = [
        {"vwap": 2.5, "volume": 2.0, "obv": 1.5, "inst": 1.0, "news": 70, "actual_return": 3.5},
        {"vwap": 2.0, "volume": 1.8, "obv": 1.2, "inst": 0.8, "news": 65, "actual_return": 2.8},
        {"vwap": 2.2, "volume": 2.5, "obv": 1.8, "inst": 1.2, "news": 75, "actual_return": 4.2},
        {"vwap": 1.8, "volume": 1.5, "obv": 1.0, "inst": 0.5, "news": 60, "actual_return": 2.1},
        {"vwap": 2.3, "volume": 2.2, "obv": 1.6, "inst": 0.9, "news": 68, "actual_return": 3.8},
        {"vwap": 2.1, "volume": 1.7, "obv": 1.3, "inst": 0.7, "news": 63, "actual_return": 2.5},
        {"vwap": 2.4, "volume": 2.3, "obv": 1.7, "inst": 1.1, "news": 72, "actual_return": 4.0},
        {"vwap": 1.9, "volume": 1.6, "obv": 1.1, "inst": 0.6, "news": 62, "actual_return": 2.3},
        {"vwap": 2.6, "volume": 2.4, "obv": 1.9, "inst": 1.3, "news": 77, "actual_return": 4.5},
        {"vwap": 2.0, "volume": 1.9, "obv": 1.4, "inst": 0.8, "news": 66, "actual_return": 3.0},
    ]

    # 2. 손실 케이스 (15개) - 혼합 신호, 약한 손실
    loss_cases = [
        {"vwap": 1.5, "volume": 0.5, "obv": -0.5, "inst": 0.2, "news": 55, "actual_return": -1.2},
        {"vwap": 1.8, "volume": -0.3, "obv": -1.0, "inst": -0.1, "news": 58, "actual_return": -2.1},
        {"vwap": 1.2, "volume": 0.8, "obv": -1.2, "inst": 0.0, "news": 52, "actual_return": -1.8},
        {"vwap": 1.6, "volume": 0.3, "obv": -0.8, "inst": 0.1, "news": 56, "actual_return": -1.5},
        {"vwap": 1.4, "volume": -0.5, "obv": -1.5, "inst": -0.2, "news": 54, "actual_return": -2.5},
        {"vwap": 1.7, "volume": 0.6, "obv": -0.6, "inst": 0.3, "news": 57, "actual_return": -1.0},
        {"vwap": 1.3, "volume": -0.2, "obv": -1.3, "inst": -0.1, "news": 53, "actual_return": -2.2},
        {"vwap": 1.5, "volume": 0.4, "obv": -0.9, "inst": 0.2, "news": 55, "actual_return": -1.6},
        {"vwap": 1.1, "volume": -0.6, "obv": -1.8, "inst": -0.3, "news": 51, "actual_return": -3.0},
        {"vwap": 1.6, "volume": 0.7, "obv": -0.7, "inst": 0.4, "news": 56, "actual_return": -0.9},
        {"vwap": 1.4, "volume": -0.4, "obv": -1.4, "inst": -0.1, "news": 54, "actual_return": -2.3},
        {"vwap": 1.7, "volume": 0.5, "obv": -0.5, "inst": 0.2, "news": 57, "actual_return": -1.1},
        {"vwap": 1.2, "volume": -0.7, "obv": -1.6, "inst": -0.2, "news": 52, "actual_return": -2.8},
        {"vwap": 1.5, "volume": 0.2, "obv": -1.0, "inst": 0.1, "news": 55, "actual_return": -1.7},
        {"vwap": 1.3, "volume": -0.3, "obv": -1.1, "inst": 0.0, "news": 53, "actual_return": -1.9},
    ]

    # 3. 차단 케이스 (5개) - 약한 신호, 큰 손실
    block_cases = [
        {"vwap": 0.8, "volume": -1.0, "obv": -2.0, "inst": -0.5, "news": 45, "actual_return": -4.5},
        {"vwap": 1.0, "volume": -0.8, "obv": -1.8, "inst": -0.3, "news": 48, "actual_return": -3.8},
        {"vwap": 0.9, "volume": -1.2, "obv": -2.2, "inst": -0.6, "news": 46, "actual_return": -5.0},
        {"vwap": 0.7, "volume": -0.9, "obv": -1.9, "inst": -0.4, "news": 44, "actual_return": -4.2},
        {"vwap": 1.0, "volume": -1.1, "obv": -2.1, "inst": -0.5, "news": 47, "actual_return": -4.7},
    ]

    scenarios = win_cases + loss_cases + block_cases
    return scenarios


def create_mock_state(vwap_score, volume_score, obv_score, inst_score, news_score):
    """Mock 상태 생성"""
    # VWAP 설정
    if vwap_score > 0:
        price = 7000
        vwap = 6800
    else:
        price = 6800
        vwap = 7000

    # 거래량 설정
    if volume_score > 1.0:
        volumes = [1000] * 40 + [5000]
    elif volume_score > 0:
        volumes = [1000] * 40 + [2000]
    else:
        volumes = [1000] * 40 + [500]

    # DataFrame
    df = pd.DataFrame({
        'close': [price] * 50,
        'high': [price * 1.01] * 50,
        'low': [price * 0.99] * 50,
        'volume': volumes + [1000] * 9,
        'vwap': [vwap] * 50
    })

    # 수급 데이터
    inst_flow = {
        "inst_net_buy": int(inst_score * 10000000),
        "foreign_net_buy": 0,
        "total_traded_value": 100000000
    }

    # AI 분석
    ai_analysis = {
        "scores": {
            "news": news_score
        }
    }

    return {
        "df": df,
        "df_5m": df,
        "institutional_flow": inst_flow,
        "ai_analysis": ai_analysis
    }


def evaluate_weights(weights, scenarios, threshold=1.0):
    """
    특정 가중치 조합으로 시나리오 평가

    Args:
        weights: (vwap_w, volume_w, obv_w, inst_w, news_w)
        scenarios: 시나리오 리스트
        threshold: aggregate score 임계값

    Returns:
        평가 결과 딕셔너리
    """
    vwap_w, volume_w, obv_w, inst_w, news_w = weights

    # Alpha Engine 생성
    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=vwap_w),
            VolumeSpikeAlpha(weight=volume_w, lookback=40),
            OBVTrendAlpha(weight=obv_w, fast=5, slow=20),
            InstitutionalFlowAlpha(weight=inst_w),
            NewsScoreAlpha(weight=news_w),
        ]
    )

    trades = []
    for scenario in scenarios:
        state = create_mock_state(
            scenario["vwap"],
            scenario["volume"],
            scenario["obv"],
            scenario["inst"],
            scenario["news"]
        )

        result = engine.compute("TEST", state)
        agg_score = result["aggregate_score"]

        # 임계값 초과 시 진입
        if agg_score > threshold:
            trades.append({
                "agg_score": agg_score,
                "actual_return": scenario["actual_return"]
            })

    # 통계 계산
    if len(trades) == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "total_return": 0.0,
            "sharpe_ratio": 0.0,
            "score": 0.0
        }

    returns = [t["actual_return"] for t in trades]
    winning_trades = sum(1 for r in returns if r > 0)
    win_rate = (winning_trades / len(trades)) * 100
    avg_return = np.mean(returns)
    total_return = np.sum(returns)
    std_return = np.std(returns) if len(returns) > 1 else 1.0
    sharpe_ratio = (avg_return / std_return) if std_return > 0 else 0.0

    # 종합 점수 (가중 평균)
    score = (win_rate * 0.4) + (avg_return * 10 * 0.3) + (sharpe_ratio * 5 * 0.3)

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "avg_return": avg_return,
        "total_return": total_return,
        "sharpe_ratio": sharpe_ratio,
        "score": score
    }


def grid_search():
    """그리드 서치로 최적 가중치 탐색"""
    console.print("\n" + "=" * 80)
    console.print("🔍 Phase 3-1: 알파 가중치 그리드 서치", style="bold cyan")
    console.print("=" * 80 + "\n")

    # 시나리오 생성
    scenarios = create_mock_scenarios()
    console.print(f"📊 시나리오: {len(scenarios)}개 (승리 10 / 손실 15 / 차단 5)")

    # 그리드 정의 (0.5 ~ 3.0, 1.0 간격) - 효율성 향상
    weight_range = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

    # 🔧 성능 최적화: VWAP/Volume은 중요하므로 더 세밀하게, 나머지는 coarse
    vwap_range = [1.5, 2.0, 2.5, 3.0]
    volume_range = [1.0, 1.5, 2.0, 2.5]
    obv_range = [0.5, 1.0, 1.5]
    inst_range = [0.5, 1.0, 1.5]
    news_range = [0.5, 1.0]

    console.print(f"🔧 가중치 범위:")
    console.print(f"   VWAP: {vwap_range}")
    console.print(f"   Volume: {volume_range}")
    console.print(f"   OBV: {obv_range}")
    console.print(f"   Inst: {inst_range}")
    console.print(f"   News: {news_range}")
    total_combinations = len(vwap_range) * len(volume_range) * len(obv_range) * len(inst_range) * len(news_range)
    console.print(f"📈 조합 수: {len(vwap_range)}×{len(volume_range)}×{len(obv_range)}×{len(inst_range)}×{len(news_range)} = {total_combinations:,}개")

    # 현재 가중치 평가
    console.print("\n" + "=" * 80)
    console.print("📌 현재 가중치 (Baseline)", style="bold yellow")
    console.print("=" * 80)

    baseline = evaluate_weights((2.0, 1.5, 1.2, 1.0, 0.8), scenarios)
    console.print(f"  거래 건수:   {baseline['trades']}건")
    console.print(f"  승률:        {baseline['win_rate']:.2f}%")
    console.print(f"  평균 수익률: {baseline['avg_return']:.2f}%")
    console.print(f"  총 수익률:   {baseline['total_return']:.2f}%")
    console.print(f"  Sharpe:      {baseline['sharpe_ratio']:.2f}")
    console.print(f"  종합 점수:   {baseline['score']:.2f}")

    # 그리드 서치
    console.print("\n" + "=" * 80)
    console.print("🔍 그리드 서치 실행 중...", style="bold cyan")
    console.print("=" * 80 + "\n")

    best_result = baseline
    best_weights = (2.0, 1.5, 1.2, 1.0, 0.8)

    all_results = []

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:

        combinations = list(product(vwap_range, volume_range, obv_range, inst_range, news_range))
        task = progress.add_task("가중치 조합 평가 중...", total=len(combinations))

        for weights in combinations:
            result = evaluate_weights(weights, scenarios)
            result['weights'] = weights
            all_results.append(result)

            if result['score'] > best_result['score']:
                best_result = result
                best_weights = weights

            progress.update(task, advance=1)

    # 상위 10개 결과
    console.print("\n" + "=" * 80)
    console.print("🏆 상위 10개 가중치 조합", style="bold green")
    console.print("=" * 80 + "\n")

    sorted_results = sorted(all_results, key=lambda x: x['score'], reverse=True)

    table = Table()
    table.add_column("순위", style="cyan")
    table.add_column("VWAP", justify="right")
    table.add_column("Vol", justify="right")
    table.add_column("OBV", justify="right")
    table.add_column("Inst", justify="right")
    table.add_column("News", justify="right")
    table.add_column("거래", justify="right")
    table.add_column("승률%", justify="right", style="yellow")
    table.add_column("평균%", justify="right", style="green")
    table.add_column("Sharpe", justify="right")
    table.add_column("점수", justify="right", style="bold")

    for i, result in enumerate(sorted_results[:10], 1):
        w = result['weights']
        table.add_row(
            str(i),
            f"{w[0]:.1f}",
            f"{w[1]:.1f}",
            f"{w[2]:.1f}",
            f"{w[3]:.1f}",
            f"{w[4]:.1f}",
            str(result['trades']),
            f"{result['win_rate']:.1f}",
            f"{result['avg_return']:+.2f}",
            f"{result['sharpe_ratio']:.2f}",
            f"{result['score']:.1f}"
        )

    console.print(table)

    # 최적 가중치 요약
    console.print("\n" + "=" * 80)
    console.print("✅ 최적 가중치 (Best Weights)", style="bold green")
    console.print("=" * 80)
    console.print(f"  VWAP Alpha:         {best_weights[0]:.1f}")
    console.print(f"  Volume Spike:       {best_weights[1]:.1f}")
    console.print(f"  OBV Trend:          {best_weights[2]:.1f}")
    console.print(f"  Institutional Flow: {best_weights[3]:.1f}")
    console.print(f"  News Score:         {best_weights[4]:.1f}")
    console.print()
    console.print(f"  거래 건수:   {best_result['trades']}건")
    console.print(f"  승률:        {best_result['win_rate']:.2f}%")
    console.print(f"  평균 수익률: {best_result['avg_return']:.2f}%")
    console.print(f"  총 수익률:   {best_result['total_return']:.2f}%")
    console.print(f"  Sharpe:      {best_result['sharpe_ratio']:.2f}")
    console.print(f"  종합 점수:   {best_result['score']:.2f}")

    # Baseline 대비 개선율
    console.print("\n" + "=" * 80)
    console.print("📊 Baseline 대비 개선율", style="bold cyan")
    console.print("=" * 80)

    if baseline['win_rate'] > 0:
        win_rate_improvement = ((best_result['win_rate'] - baseline['win_rate']) / baseline['win_rate']) * 100
        console.print(f"  승률:        {win_rate_improvement:+.1f}%")

    if baseline['avg_return'] != 0:
        avg_return_improvement = ((best_result['avg_return'] - baseline['avg_return']) / abs(baseline['avg_return'])) * 100
        console.print(f"  평균 수익률: {avg_return_improvement:+.1f}%")

    if baseline['sharpe_ratio'] > 0:
        sharpe_improvement = ((best_result['sharpe_ratio'] - baseline['sharpe_ratio']) / baseline['sharpe_ratio']) * 100
        console.print(f"  Sharpe:      {sharpe_improvement:+.1f}%")

    score_improvement = ((best_result['score'] - baseline['score']) / baseline['score']) * 100
    console.print(f"  종합 점수:   {score_improvement:+.1f}%")

    # 결과 저장
    result_file = "data/phase3_grid_search_results.json"
    with open(result_file, 'w') as f:
        json.dump({
            "baseline": {
                "weights": list(best_weights),
                "trades": baseline['trades'],
                "win_rate": baseline['win_rate'],
                "avg_return": baseline['avg_return'],
                "sharpe_ratio": baseline['sharpe_ratio'],
                "score": baseline['score']
            },
            "best": {
                "weights": list(best_weights),
                "trades": best_result['trades'],
                "win_rate": best_result['win_rate'],
                "avg_return": best_result['avg_return'],
                "total_return": best_result['total_return'],
                "sharpe_ratio": best_result['sharpe_ratio'],
                "score": best_result['score']
            },
            "top_10": [
                {
                    "weights": list(r['weights']),
                    "trades": r['trades'],
                    "win_rate": r['win_rate'],
                    "avg_return": r['avg_return'],
                    "sharpe_ratio": r['sharpe_ratio'],
                    "score": r['score']
                }
                for r in sorted_results[:10]
            ]
        }, f, indent=2)

    console.print(f"\n💾 결과 저장: {result_file}")
    console.print()


if __name__ == "__main__":
    grid_search()
