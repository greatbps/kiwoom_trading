"""
L5: Squeeze Momentum Pro V2 - Confidence 반환

기존: check_squeeze() → (squeeze_on, momentum_up, details)
개선: check_with_confidence() → FilterResult(passed, confidence, reason)
"""

import pandas as pd
import numpy as np
from typing import Dict
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.squeeze_momentum import SqueezeMomentumPro
from trading.filters.base_filter import FilterResult
from rich.console import Console

console = Console()


class SqueezeMomentumProV2(SqueezeMomentumPro):
    """
    L5 Squeeze Momentum Pro V2 - Confidence 기반

    기존 check_squeeze()는 유지 (하위 호환성)
    새로운 check_with_confidence()는 FilterResult 반환
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Confidence 가중치
        self.squeeze_weight = 0.4       # Squeeze 강도 (40%)
        self.momentum_weight = 0.3      # 모멘텀 방향 (30%)
        self.width_weight = 0.3         # BB Width (30%)

    def calculate_squeeze_confidence(
        self,
        bb_upper: float,
        bb_lower: float,
        kc_upper: float,
        kc_lower: float
    ) -> float:
        """
        Squeeze 강도 → Confidence

        BB가 KC 안에 깊숙이 들어갈수록 높은 점수

        Returns:
            0.0 ~ 0.4 점수
        """
        # Squeeze 여부
        squeeze_on = (bb_upper < kc_upper) and (bb_lower > kc_lower)

        if not squeeze_on:
            return 0.0

        # Squeeze 강도 계산
        # BB가 KC 중심에 가까울수록 강한 Squeeze
        kc_width = kc_upper - kc_lower
        bb_width = bb_upper - bb_lower

        if kc_width < 1e-9:
            return 0.0

        # BB가 KC의 몇 %를 차지하는가?
        ratio = bb_width / kc_width  # 0~1

        # ratio가 작을수록 강한 Squeeze
        # 0.5 미만이면 최대 점수
        if ratio < 0.5:
            squeeze_strength = 1.0
        elif ratio < 0.8:
            squeeze_strength = (0.8 - ratio) / 0.3  # 0.5~0.8 → 1.0~0
        else:
            squeeze_strength = 0.0

        conf = squeeze_strength * self.squeeze_weight
        return float(conf)

    def calculate_momentum_confidence(
        self,
        momentum: pd.Series
    ) -> float:
        """
        모멘텀 상승 강도 → Confidence

        Returns:
            0.0 ~ 0.3 점수
        """
        if len(momentum) < 3:
            return 0.0

        try:
            # 최근 3봉 모멘텀 값
            m1 = momentum.iloc[-1]
            m2 = momentum.iloc[-2]
            m3 = momentum.iloc[-3]

            # 연속 상승 체크
            continuous_up = (m1 > m2) and (m2 > m3)

            if not continuous_up:
                # 상승하지 않으면 0
                return 0.0

            # 상승 강도 계산
            # 최근 2봉의 상승폭 합산
            gain1 = m1 - m2
            gain2 = m2 - m3

            # 정규화 (표준편차 기준)
            momentum_std = momentum.rolling(20).std().iloc[-1]
            if momentum_std < 1e-9:
                return 0.0

            total_gain_z = (gain1 + gain2) / (momentum_std + 1e-9)

            # Z-score를 0~0.3 범위로 변환
            # 1σ = 0.1, 2σ = 0.2, 3σ+ = 0.3
            if total_gain_z > 3.0:
                conf = self.momentum_weight
            else:
                conf = min(total_gain_z / 3.0, 1.0) * self.momentum_weight

            return float(conf)

        except Exception:
            return 0.0

    def calculate_bb_width_confidence(
        self,
        bb_width: float,
        bb_width_ma: float
    ) -> float:
        """
        BB Width → Confidence

        Width가 좁을수록 (변동성 수축) 높은 점수

        Returns:
            0.0 ~ 0.3 점수
        """
        if bb_width_ma < 1e-9:
            return 0.0

        # 현재 Width가 평균의 몇 %인가?
        width_ratio = bb_width / bb_width_ma

        # 평균 대비 좁을수록 높은 점수
        # 0.5 이하면 최대 점수 (평균의 50% 이하)
        if width_ratio < 0.5:
            width_strength = 1.0
        elif width_ratio < 1.0:
            width_strength = (1.0 - width_ratio) / 0.5  # 0.5~1.0 → 1.0~0
        else:
            # 평균보다 넓으면 0
            width_strength = 0.0

        conf = width_strength * self.width_weight
        return float(conf)

    def check_with_confidence(
        self,
        df: pd.DataFrame
    ) -> FilterResult:
        """
        L5 Squeeze Momentum + Confidence 계산

        Returns:
            FilterResult(passed, confidence, reason)
        """
        # 기존 check_squeeze() 호출
        squeeze_on, momentum_up, details = self.check_squeeze(df)

        if not squeeze_on:
            return FilterResult(False, 0.0, "L5 Squeeze Off (변동성 확대)")

        if not momentum_up:
            return FilterResult(False, 0.0, "L5 모멘텀 하락")

        # Confidence 계산
        try:
            # 1. Squeeze 강도 (0~0.4)
            bb_upper = details['bb_upper']
            bb_lower = details['bb_lower']
            kc_upper = details['kc_upper']
            kc_lower = details['kc_lower']
            squeeze_conf = self.calculate_squeeze_confidence(
                bb_upper, bb_lower, kc_upper, kc_lower
            )

            # 2. 모멘텀 상승 강도 (0~0.3)
            momentum = self.calculate_momentum(df)
            momentum_conf = self.calculate_momentum_confidence(momentum)

            # 3. BB Width (0~0.3)
            bb_width = details['bb_width']
            bb_width_ma = details['bb_width_ma']
            width_conf = self.calculate_bb_width_confidence(bb_width, bb_width_ma)

            # 합산 (0~1.0)
            confidence = squeeze_conf + momentum_conf + width_conf
            confidence = min(confidence, 1.0)

            # 상세 정보
            detailed_reason = (
                f"L5 Squeeze: ON, Momentum: UP | "
                f"Conf={confidence:.2f} "
                f"(Squeeze:{squeeze_conf:.2f} Mom:{momentum_conf:.2f} Width:{width_conf:.2f})"
            )

            return FilterResult(True, confidence, detailed_reason)

        except Exception as e:
            console.print(f"[dim]⚠️  L5 Confidence 계산 실패: {e}[/dim]")
            # 에러 시 기본 confidence 0.5
            return FilterResult(True, 0.5, "L5 Squeeze: ON, Momentum: UP | Conf=0.5 (default)")


if __name__ == "__main__":
    """테스트 코드"""
    import yfinance as yf

    print("=" * 80)
    print("🧪 Squeeze Momentum Pro V2 (Confidence) 테스트")
    print("=" * 80)

    squeeze = SqueezeMomentumProV2()

    # 테스트 종목
    test_tickers = ["005930.KS", "035420.KS"]  # 삼성전자, NAVER

    for ticker in test_tickers:
        print(f"\n종목: {ticker}")

        # 데이터 다운로드
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)

        if df is None or len(df) < 50:
            print("  데이터 부족")
            continue

        # 컬럼명 소문자 변환
        df.columns = df.columns.str.lower()

        # V1 (기존)
        squeeze_on, momentum_up, details = squeeze.check_squeeze(df)
        print(f"  V1 (기존): Squeeze={squeeze_on}, Momentum={momentum_up}")

        # V2 (Confidence)
        result = squeeze.check_with_confidence(df)
        print(f"  V2 (Conf): {result.passed} - {result.reason}")
        print(f"           Confidence = {result.confidence:.2f}")
