#!/usr/bin/env python3
"""
Phase 4: 통합 테스트

8개 알파 (기존 5 + 신규 3) + Market Regime 동적 가중치 시스템 테스트
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

# 알파 엔진 및 알파들
from trading.alpha_engine import SimonsStyleAlphaEngine
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha
from trading.alphas.momentum_alpha import MomentumAlpha
from trading.alphas.mean_reversion_alpha import MeanReversionAlpha
from trading.alphas.volatility_alpha import VolatilityAlpha

# 동적 가중치 조정기
from trading.dynamic_weight_adjuster import DynamicWeightAdjuster

console = Console()


def create_test_market_data(scenario: str = "normal") -> pd.DataFrame:
    """
    테스트용 시장 데이터 생성

    Args:
        scenario: "normal", "high_vol", "low_vol", "trending_up", "trending_down"

    Returns:
        OHLCV DataFrame
    """
    # 60개 5분봉 데이터
    dates = pd.date_range(end=datetime.now(), periods=60, freq='5min')

    base_price = 50000
    prices = []
    volumes = []

    for i in range(60):
        if scenario == "high_vol":
            # 고변동성: 큰 가격 변동
            noise = np.random.randint(-1000, 1000)
            price = base_price + (i * 50) + noise
            volume = 15000 + np.random.randint(-5000, 5000)

        elif scenario == "low_vol":
            # 저변동성: 작은 가격 변동
            noise = np.random.randint(-100, 100)
            price = base_price + (i * 10) + noise
            volume = 10000 + np.random.randint(-1000, 1000)

        elif scenario == "trending_up":
            # 상승장: 꾸준한 상승
            noise = np.random.randint(-200, 200)
            price = base_price + (i * 150) + noise
            volume = 12000 + np.random.randint(-2000, 3000)

        elif scenario == "trending_down":
            # 하락장: 꾸준한 하락
            noise = np.random.randint(-200, 200)
            price = base_price - (i * 100) + noise
            volume = 11000 + np.random.randint(-2000, 2000)

        else:  # normal
            # 보통: 중간 변동
            noise = np.random.randint(-400, 400)
            price = base_price + (i * 80) + noise
            volume = 12000 + np.random.randint(-3000, 3000)

        prices.append(max(price, 1000))
        volumes.append(max(volume, 1000))

    # OHLCV 데이터 생성
    df = pd.DataFrame({
        'date': dates,
        'open': prices,
        'high': [p + np.random.randint(50, 300) for p in prices],
        'low': [p - np.random.randint(50, 300) for p in prices],
        'close': prices,
        'volume': volumes,
    })

    # 기술적 지표 계산
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    df['ma20'] = df['close'].rolling(20).mean()

    # OBV 계산
    df['obv'] = 0.0
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i-1]:
            df.loc[df.index[i], 'obv'] = df['obv'].iloc[i-1] + df['volume'].iloc[i]
        elif df['close'].iloc[i] < df['close'].iloc[i-1]:
            df.loc[df.index[i], 'obv'] = df['obv'].iloc[i-1] - df['volume'].iloc[i]
        else:
            df.loc[df.index[i], 'obv'] = df['obv'].iloc[i-1]

    # 대문자 컬럼명으로 변경 (일부 알파가 대문자 요구)
    df['Close'] = df['close']
    df['High'] = df['high']
    df['Low'] = df['low']
    df['Open'] = df['open']
    df['Volume'] = df['volume']

    return df


def test_single_regime(regime: str, scenario: str):
    """
    단일 Regime 테스트

    Args:
        regime: Market regime
        scenario: 시장 시나리오
    """
    console.print()
    console.print("=" * 100)
    console.print(f"🧪 Test: Regime={regime}, Scenario={scenario}", style="bold cyan")
    console.print("=" * 100)

    # 시장 데이터 생성
    df = create_test_market_data(scenario)

    # 동적 가중치 조정
    adjuster = DynamicWeightAdjuster()
    weights = adjuster.adjust_weights(regime)

    console.print(f"\n📊 조정된 가중치:")
    for alpha_name, weight in weights.items():
        console.print(f"  {alpha_name:16s}: {weight:.2f}")

    # 알파 엔진 생성 (8개 알파)
    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=weights["VWAP"]),
            VolumeSpikeAlpha(weight=weights["VolumeSpike"], lookback=40),
            OBVTrendAlpha(weight=weights["OBV"], fast=5, slow=20),
            InstitutionalFlowAlpha(weight=weights["Institutional"]),
            NewsScoreAlpha(weight=weights["News"]),
            MomentumAlpha(weight=weights["Momentum"]),
            MeanReversionAlpha(weight=weights["MeanReversion"]),
            VolatilityAlpha(weight=weights["Volatility"]),
        ]
    )

    # State 생성
    state = {
        "df": df,
        "df_5m": df,
        "institutional_flow": {
            "inst_net_buy": 50000000,
            "foreign_net_buy": 30000000,
            "total_traded_value": 1000000000
        },
        "ai_analysis": {
            "news_score": 70
        }
    }

    # 알파 계산
    result = engine.compute("TEST", state)

    # 결과 출력
    console.print(f"\n🎯 Aggregate Score: {result['aggregate_score']:+.2f}")

    # 개별 알파 스코어 테이블
    alpha_table = Table(title="개별 알파 스코어", show_header=True, header_style="bold magenta")
    alpha_table.add_column("Alpha", style="cyan", width=18)
    alpha_table.add_column("Weight", justify="right", style="yellow")
    alpha_table.add_column("Score", justify="right", style="green")
    alpha_table.add_column("Weighted", justify="right", style="bold")
    alpha_table.add_column("Confidence", justify="right", style="dim")
    alpha_table.add_column("Reason", style="dim", width=35)

    for alpha_name, alpha_data in result['weighted_scores'].items():
        alpha_table.add_row(
            alpha_name,
            f"{alpha_data['weight']:.2f}",
            f"{alpha_data['score']:+.2f}",
            f"{alpha_data['weighted_contribution']:+.2f}",
            f"{alpha_data['confidence']:.2f}",
            alpha_data['reason'][:35]
        )

    console.print()
    console.print(alpha_table)

    # 판정
    if result['aggregate_score'] > 1.5:
        verdict = "✅ 강한 매수"
    elif result['aggregate_score'] > 0.5:
        verdict = "⚠️ 약한 매수"
    elif result['aggregate_score'] > -0.5:
        verdict = "➖ 중립"
    elif result['aggregate_score'] > -1.5:
        verdict = "⚠️ 약한 매도"
    else:
        verdict = "❌ 강한 매도"

    console.print(f"\n🔔 판정: {verdict}")

    return result


def run_comprehensive_test():
    """종합 테스트: 모든 Regime × Scenario 조합"""

    console.print()
    console.print("=" * 100)
    console.print("🚀 Phase 4 통합 테스트: 8 Alphas + Dynamic Weights", style="bold green")
    console.print("=" * 100)

    # Regime별 권장 시나리오
    test_cases = [
        ("HIGH_VOL", "high_vol"),
        ("LOW_VOL", "low_vol"),
        ("NORMAL", "normal"),
        ("TRENDING_UP", "trending_up"),
        ("TRENDING_DOWN", "trending_down"),
    ]

    results = {}

    for regime, scenario in test_cases:
        result = test_single_regime(regime, scenario)
        results[f"{regime}_{scenario}"] = result

    # 종합 요약
    console.print()
    console.print("=" * 100)
    console.print("📊 테스트 종합 요약", style="bold yellow")
    console.print("=" * 100)
    console.print()

    summary_table = Table(title="Regime별 Aggregate Score", show_header=True, header_style="bold magenta")
    summary_table.add_column("Regime", style="cyan", width=15)
    summary_table.add_column("Scenario", style="dim", width=15)
    summary_table.add_column("Aggregate Score", justify="right", style="green")
    summary_table.add_column("판정", justify="center")

    for regime, scenario in test_cases:
        key = f"{regime}_{scenario}"
        score = results[key]['aggregate_score']

        if score > 1.5:
            verdict = "✅ 강한 매수"
        elif score > 0.5:
            verdict = "⚠️ 약한 매수"
        elif score > -0.5:
            verdict = "➖ 중립"
        elif score > -1.5:
            verdict = "⚠️ 약한 매도"
        else:
            verdict = "❌ 강한 매도"

        summary_table.add_row(
            regime,
            scenario,
            f"{score:+.2f}",
            verdict
        )

    console.print(summary_table)

    # 핵심 발견
    console.print()
    console.print("=" * 100)
    console.print("💡 핵심 발견", style="bold cyan")
    console.print("=" * 100)
    console.print()

    console.print("✅ 성공 요인:")
    console.print("  1. 8개 알파 통합: 기존 5개 + 신규 3개 (Momentum, MeanReversion, Volatility)")
    console.print("  2. Market Regime 기반 동적 가중치 조정")
    console.print("  3. 고변동성: 단기 지표 강화, 저변동성: 장기 지표 강화")
    console.print("  4. 상승장: 모멘텀/뉴스 강화, 하락장: 보수적 접근")
    console.print()

    console.print("⚠️  주의사항:")
    console.print("  1. 실전 데이터와 Mock 데이터의 차이")
    console.print("  2. Regime 전환 시 가중치 급변 가능성")
    console.print("  3. 신규 알파의 실전 성능 검증 필요")
    console.print("  4. 과최적화 방지 (실전 모니터링 필수)")
    console.print()

    console.print("=" * 100)
    console.print("✅ Phase 4 통합 테스트 완료", style="bold green")
    console.print("=" * 100)


if __name__ == "__main__":
    try:
        run_comprehensive_test()
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️  테스트가 중단되었습니다.[/yellow]")
    except Exception as e:
        console.print()
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()
