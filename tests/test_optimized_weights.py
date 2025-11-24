#!/usr/bin/env python3
"""
Phase 3-1 최적화된 가중치 테스트 스크립트

장 시간 체크 없이 최적화된 알파 가중치를 테스트합니다.
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table

# 최적화된 알파 엔진 임포트
from trading.alpha_engine import SimonsStyleAlphaEngine
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha

console = Console()


def create_test_data(scenario: dict) -> dict:
    """테스트용 시장 데이터 생성"""

    # 60개 데이터 포인트 생성 (5분봉 기준)
    dates = pd.date_range(end=datetime.now(), periods=60, freq='5min')

    base_price = 50000
    prices = []
    volumes = []

    for i in range(60):
        # 가격 변동
        if i < 40:
            price = base_price + (i * 100) + np.random.randint(-200, 200)
        else:
            # 최근 20개 데이터에 시나리오 신호 반영
            price = base_price + (i * scenario.get('price_trend', 100)) + np.random.randint(-300, 300)

        prices.append(price)

        # 거래량 (시나리오에 따라 변동)
        base_vol = 10000
        if i >= 50:  # 최근 10개에 볼륨 스파이크
            vol = base_vol * (1 + scenario.get('volume_spike', 0.5)) + np.random.randint(0, 5000)
        else:
            vol = base_vol + np.random.randint(-2000, 2000)

        volumes.append(max(vol, 1000))

    df = pd.DataFrame({
        'date': dates,
        'open': prices,
        'high': [p + np.random.randint(50, 200) for p in prices],
        'low': [p - np.random.randint(50, 200) for p in prices],
        'close': prices,
        'volume': volumes,
    })

    # 기술적 지표 계산
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    df['ma20'] = df['close'].rolling(20).mean()

    # OBV 계산
    df['obv'] = 0
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i-1]:
            df.loc[i, 'obv'] = df['obv'].iloc[i-1] + df['volume'].iloc[i]
        elif df['close'].iloc[i] < df['close'].iloc[i-1]:
            df.loc[i, 'obv'] = df['obv'].iloc[i-1] - df['volume'].iloc[i]
        else:
            df.loc[i, 'obv'] = df['obv'].iloc[i-1]

    # 1분봉도 생성 (간단하게 5분봉을 복제)
    df_1m = df.copy()

    # 기관/외인 수급 데이터
    institutional_flow = {
        'inst_net_buy': scenario.get('inst_buy', 0) * 1000000,
        'foreign_net_buy': scenario.get('foreign_buy', 0) * 1000000,
        'total_traded_value': 100000000
    }

    state = {
        'df': df,
        'df_5m': df,
        'df_1m': df_1m,
        'institutional_flow': institutional_flow,
        'ai_analysis': None
    }

    return state, df['close'].iloc[-1]


def test_engine_with_scenarios():
    """다양한 시나리오에서 최적화된 엔진 테스트"""

    console.print("=" * 100)
    console.print("🧪 Phase 3-1 최적화된 알파 가중치 테스트", style="bold green")
    console.print("=" * 100)
    console.print()

    # 최적화된 알파 엔진 생성
    console.print("📊 최적화된 알파 엔진 초기화")
    console.print()

    optimized_engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=1.5),                      # Optimized
            VolumeSpikeAlpha(weight=1.0, lookback=40),  # Optimized
            OBVTrendAlpha(weight=0.5, fast=5, slow=20), # Optimized
            InstitutionalFlowAlpha(weight=0.5),         # Optimized
            NewsScoreAlpha(weight=1.0),                 # Optimized
        ]
    )

    console.print("✅ 가중치: [VWAP=1.5, Vol=1.0, OBV=0.5, Inst=0.5, News=1.0]")
    console.print()

    # Baseline 엔진 (비교용)
    baseline_engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=2.0),
            VolumeSpikeAlpha(weight=1.5, lookback=40),
            OBVTrendAlpha(weight=1.2, fast=5, slow=20),
            InstitutionalFlowAlpha(weight=1.0),
            NewsScoreAlpha(weight=0.8),
        ]
    )

    console.print("📊 Baseline 엔진 초기화")
    console.print("✅ 가중치: [VWAP=2.0, Vol=1.5, OBV=1.2, Inst=1.0, News=0.8]")
    console.print()

    # 테스트 시나리오 정의
    test_scenarios = [
        {
            'name': '강한 상승 신호 (모든 알파 긍정)',
            'price_trend': 150,
            'volume_spike': 1.5,
            'inst_buy': 50,
            'foreign_buy': 30,
            'expected_action': 'BUY'
        },
        {
            'name': '중간 신호 (일부 알파 긍정)',
            'price_trend': 80,
            'volume_spike': 0.8,
            'inst_buy': 20,
            'foreign_buy': 10,
            'expected_action': 'CAUTION'
        },
        {
            'name': '약한 신호 (혼재)',
            'price_trend': 30,
            'volume_spike': 0.3,
            'inst_buy': -10,
            'foreign_buy': 5,
            'expected_action': 'SKIP'
        },
        {
            'name': '부정 신호 (하락)',
            'price_trend': -50,
            'volume_spike': -0.2,
            'inst_buy': -30,
            'foreign_buy': -20,
            'expected_action': 'SKIP'
        },
        {
            'name': '거래량 급증 (가격 보합)',
            'price_trend': 10,
            'volume_spike': 2.0,
            'inst_buy': 10,
            'foreign_buy': 5,
            'expected_action': 'CAUTION'
        },
    ]

    # 결과 테이블
    results_table = Table(title="시나리오별 테스트 결과 비교", show_header=True, header_style="bold magenta")
    results_table.add_column("시나리오", style="cyan", width=30)
    results_table.add_column("예상", style="dim", justify="center")
    results_table.add_column("Baseline 점수", justify="right", style="yellow")
    results_table.add_column("Optimized 점수", justify="right", style="green")
    results_table.add_column("개선율", justify="right", style="bold")
    results_table.add_column("판정", justify="center")

    console.print("🔍 시나리오 테스트 실행 중...")
    console.print()

    total_improvement = 0
    scenarios_tested = 0

    for scenario in test_scenarios:
        # 테스트 데이터 생성
        state, current_price = create_test_data(scenario)

        # Baseline 엔진 평가
        baseline_result = baseline_engine.compute("TEST001", state)
        baseline_score = baseline_result['aggregate_score']

        # Optimized 엔진 평가
        optimized_result = optimized_engine.compute("TEST001", state)
        optimized_score = optimized_result['aggregate_score']

        # 개선율 계산
        if baseline_score != 0:
            improvement = ((optimized_score - baseline_score) / abs(baseline_score)) * 100
        else:
            improvement = 0 if optimized_score == 0 else 100

        total_improvement += improvement
        scenarios_tested += 1

        # 판정
        if optimized_score > 1.5:
            verdict = "✅ 매수"
        elif optimized_score > 0.5:
            verdict = "⚠️  관망"
        else:
            verdict = "❌ 차단"

        # 결과 추가
        results_table.add_row(
            scenario['name'],
            scenario['expected_action'],
            f"{baseline_score:+.2f}",
            f"{optimized_score:+.2f}",
            f"{improvement:+.1f}%",
            verdict
        )

    console.print(results_table)
    console.print()

    # 요약 통계
    avg_improvement = total_improvement / scenarios_tested if scenarios_tested > 0 else 0

    console.print("=" * 100)
    console.print("📊 테스트 요약", style="bold yellow")
    console.print("=" * 100)
    console.print()
    console.print(f"  테스트 시나리오: {scenarios_tested}개")
    console.print(f"  평균 개선율: {avg_improvement:+.1f}%")
    console.print()

    if avg_improvement > 0:
        console.print("[green]✅ 최적화된 가중치가 전반적으로 더 나은 성능을 보입니다.[/green]")
    else:
        console.print("[yellow]⚠️  일부 시나리오에서 Baseline이 더 나은 성능을 보입니다.[/yellow]")

    console.print()
    console.print("=" * 100)
    console.print("💡 다음 단계: Bayesian Optimization으로 미세 조정", style="bold cyan")
    console.print("=" * 100)
    console.print()

    return optimized_engine, baseline_engine


if __name__ == "__main__":
    try:
        test_engine_with_scenarios()
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️  테스트가 중단되었습니다.[/yellow]")
    except Exception as e:
        console.print()
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()
