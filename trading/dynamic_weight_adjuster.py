"""
Dynamic Weight Adjuster - Market Regime 기반 알파 가중치 동적 조정

장세(Market Regime)에 따라 알파 가중치를 자동으로 조정합니다.
"""

from typing import Dict, List
import numpy as np
from rich.console import Console

console = Console()


class DynamicWeightAdjuster:
    """
    Market Regime 기반 동적 가중치 조정기

    Regimes:
    1. HIGH_VOL (고변동성): VWAP/Volume 가중치 증가
    2. LOW_VOL (저변동성): OBV/Inst 가중치 증가, Mean Reversion 활성화
    3. NORMAL (보통): 기본 가중치 사용
    4. TRENDING_UP (상승장): News Score/Momentum 가중치 증가
    5. TRENDING_DOWN (하락장): 모든 가중치 축소 (보수적)
    """

    def __init__(self):
        """초기화"""
        # Phase 3-1 Grid Search 최적 가중치 (기준선)
        self.baseline_weights = {
            "VWAP": 1.5,
            "VolumeSpike": 1.0,
            "OBV": 0.5,
            "Institutional": 0.5,
            "News": 1.0,
            "Momentum": 1.0,      # Phase 4 신규
            "MeanReversion": 0.8,  # Phase 4 신규
            "Volatility": 0.6,     # Phase 4 신규
        }

        # Regime별 가중치 조정 비율 (multiplier)
        self.regime_adjustments = {
            "HIGH_VOL": {
                # 고변동성: 단기 지표 강화, 장기 지표 약화
                "VWAP": 1.3,           # 1.5 → 1.95
                "VolumeSpike": 1.4,    # 1.0 → 1.4
                "OBV": 0.6,            # 0.5 → 0.3
                "Institutional": 0.6,   # 0.5 → 0.3
                "News": 1.2,           # 1.0 → 1.2
                "Momentum": 1.3,       # 1.0 → 1.3
                "MeanReversion": 0.5,  # 0.8 → 0.4 (평균 회귀 약함)
                "Volatility": 1.5,     # 0.6 → 0.9
            },
            "LOW_VOL": {
                # 저변동성: 장기 지표 강화, 평균 회귀 활성화
                "VWAP": 0.8,           # 1.5 → 1.2
                "VolumeSpike": 0.7,    # 1.0 → 0.7
                "OBV": 1.5,            # 0.5 → 0.75
                "Institutional": 1.5,   # 0.5 → 0.75
                "News": 0.9,           # 1.0 → 0.9
                "Momentum": 0.7,       # 1.0 → 0.7
                "MeanReversion": 1.5,  # 0.8 → 1.2 (평균 회귀 강화)
                "Volatility": 0.5,     # 0.6 → 0.3
            },
            "NORMAL": {
                # 보통: 기본 가중치 유지
                "VWAP": 1.0,
                "VolumeSpike": 1.0,
                "OBV": 1.0,
                "Institutional": 1.0,
                "News": 1.0,
                "Momentum": 1.0,
                "MeanReversion": 1.0,
                "Volatility": 1.0,
            },
            "TRENDING_UP": {
                # 상승장: 모멘텀/뉴스 강화
                "VWAP": 1.1,           # 1.5 → 1.65
                "VolumeSpike": 1.2,    # 1.0 → 1.2
                "OBV": 1.2,            # 0.5 → 0.6
                "Institutional": 1.1,   # 0.5 → 0.55
                "News": 1.5,           # 1.0 → 1.5 (뉴스 효과 강화)
                "Momentum": 1.4,       # 1.0 → 1.4
                "MeanReversion": 0.6,  # 0.8 → 0.48
                "Volatility": 1.1,     # 0.6 → 0.66
            },
            "TRENDING_DOWN": {
                # 하락장: 모든 가중치 축소 (보수적)
                "VWAP": 0.8,           # 1.5 → 1.2
                "VolumeSpike": 0.7,    # 1.0 → 0.7
                "OBV": 0.7,            # 0.5 → 0.35
                "Institutional": 0.8,   # 0.5 → 0.4
                "News": 0.6,           # 1.0 → 0.6
                "Momentum": 0.5,       # 1.0 → 0.5 (하락 모멘텀 경계)
                "MeanReversion": 1.2,  # 0.8 → 0.96 (반등 기대)
                "Volatility": 0.8,     # 0.6 → 0.48
            },
        }

    def adjust_weights(self, regime: str, volatility_percentile: float = 0.5) -> Dict[str, float]:
        """
        Market Regime에 따라 가중치 조정

        Args:
            regime: 'HIGH_VOL', 'LOW_VOL', 'NORMAL', 'TRENDING_UP', 'TRENDING_DOWN'
            volatility_percentile: 변동성 백분위 (0~1), 미세 조정용

        Returns:
            조정된 가중치 dict
        """
        # 1. Regime 기본 조정
        if regime not in self.regime_adjustments:
            console.print(f"[yellow]⚠️  알 수 없는 Regime: {regime}, NORMAL 사용[/yellow]")
            regime = "NORMAL"

        adjustments = self.regime_adjustments[regime]

        # 2. 기본 가중치에 조정 비율 적용
        adjusted_weights = {}
        for alpha_name, baseline_weight in self.baseline_weights.items():
            multiplier = adjustments.get(alpha_name, 1.0)
            adjusted_weights[alpha_name] = baseline_weight * multiplier

        # 3. 변동성 백분위에 따른 미세 조정 (선택사항)
        if regime == "HIGH_VOL" and volatility_percentile > 0.8:
            # 극고변동성: VWAP/Volume 추가 강화
            adjusted_weights["VWAP"] *= 1.1
            adjusted_weights["VolumeSpike"] *= 1.1
            adjusted_weights["Volatility"] *= 1.2
        elif regime == "LOW_VOL" and volatility_percentile < 0.2:
            # 극저변동성: Mean Reversion 추가 강화 (Squeeze)
            adjusted_weights["MeanReversion"] *= 1.3
            adjusted_weights["Volatility"] *= 0.7

        return adjusted_weights

    def get_regime_description(self, regime: str) -> str:
        """
        Regime 설명 반환

        Args:
            regime: Regime 이름

        Returns:
            설명 문자열
        """
        descriptions = {
            "HIGH_VOL": "고변동성 (단기 지표 강화, Breakout 전략)",
            "LOW_VOL": "저변동성 (장기 지표 강화, Mean Reversion 전략)",
            "NORMAL": "보통 (기본 가중치, 균형 전략)",
            "TRENDING_UP": "상승장 (모멘텀/뉴스 강화, 추세 추종)",
            "TRENDING_DOWN": "하락장 (보수적 접근, 반등 대기)",
        }
        return descriptions.get(regime, "알 수 없는 장세")

    def print_weight_comparison(self, regime: str, adjusted_weights: Dict[str, float]):
        """
        기본 가중치와 조정된 가중치 비교 출력

        Args:
            regime: Regime 이름
            adjusted_weights: 조정된 가중치
        """
        from rich.table import Table

        console.print()
        console.print(f"[bold cyan]🎯 Market Regime: {regime}[/bold cyan]")
        console.print(f"[dim]{self.get_regime_description(regime)}[/dim]")
        console.print()

        table = Table(title="알파 가중치 조정", show_header=True, header_style="bold magenta")
        table.add_column("Alpha", style="cyan", width=16)
        table.add_column("Baseline", justify="right", style="yellow")
        table.add_column("Adjusted", justify="right", style="green")
        table.add_column("Change", justify="right", style="bold")

        for alpha_name in self.baseline_weights.keys():
            baseline = self.baseline_weights[alpha_name]
            adjusted = adjusted_weights.get(alpha_name, baseline)
            change_pct = ((adjusted - baseline) / baseline) * 100 if baseline > 0 else 0

            change_str = f"{change_pct:+.0f}%"
            if change_pct > 0:
                change_style = "green"
            elif change_pct < 0:
                change_style = "red"
            else:
                change_style = "dim"

            table.add_row(
                alpha_name,
                f"{baseline:.2f}",
                f"{adjusted:.2f}",
                f"[{change_style}]{change_str}[/{change_style}]"
            )

        console.print(table)
        console.print()

    def detect_trend_regime(self, df: 'pd.DataFrame', market: str = 'KOSPI') -> str:
        """
        추세 판단 (상승장/하락장)

        Args:
            df: 시장 지수 데이터 (OHLCV)
            market: 시장 이름

        Returns:
            'TRENDING_UP', 'TRENDING_DOWN', or None
        """
        if df is None or len(df) < 60:
            return None

        # 최근 가격과 이동평균 비교
        current_price = df['Close'].iloc[-1]
        ma_20 = df['Close'].rolling(20).mean().iloc[-1]
        ma_60 = df['Close'].rolling(60).mean().iloc[-1]

        # 추세 강도 (20일 수익률)
        returns_20d = ((current_price - df['Close'].iloc[-20]) / df['Close'].iloc[-20]) * 100

        # 상승장 조건
        if current_price > ma_20 > ma_60 and returns_20d > 5:
            return "TRENDING_UP"

        # 하락장 조건
        if current_price < ma_20 < ma_60 and returns_20d < -5:
            return "TRENDING_DOWN"

        return None

    def get_combined_regime(self, volatility_regime: str, trend_regime: str = None) -> str:
        """
        변동성 Regime과 추세 Regime 결합

        Args:
            volatility_regime: 'HIGH_VOL', 'LOW_VOL', 'NORMAL'
            trend_regime: 'TRENDING_UP', 'TRENDING_DOWN', or None

        Returns:
            최종 Regime
        """
        # 추세가 명확하면 추세 우선
        if trend_regime in ["TRENDING_UP", "TRENDING_DOWN"]:
            return trend_regime

        # 그 외에는 변동성 Regime 사용
        return volatility_regime


def test_dynamic_weights():
    """테스트 코드"""
    console.print("=" * 100)
    console.print("🧪 Dynamic Weight Adjuster 테스트", style="bold green")
    console.print("=" * 100)
    console.print()

    adjuster = DynamicWeightAdjuster()

    # 각 Regime별 가중치 조정 테스트
    regimes = ["HIGH_VOL", "LOW_VOL", "NORMAL", "TRENDING_UP", "TRENDING_DOWN"]

    for regime in regimes:
        adjusted_weights = adjuster.adjust_weights(regime)
        adjuster.print_weight_comparison(regime, adjusted_weights)

    console.print("=" * 100)
    console.print("✅ 테스트 완료", style="bold green")
    console.print("=" * 100)


if __name__ == "__main__":
    test_dynamic_weights()
