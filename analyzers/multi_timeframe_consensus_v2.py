"""
Multi-Timeframe Consensus V2 - Confidence 반환

기존: Pass/Fail (bool)
개선: Confidence (0~1) + Pass/Fail
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict, Optional
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.multi_timeframe_consensus import MultiTimeframeConsensus
from trading.filters.base_filter import FilterResult
from rich.console import Console

console = Console()


class MultiTimeframeConsensusV2(MultiTimeframeConsensus):
    """
    MTF Consensus V2 - Confidence 기반 필터링

    기존 check_consensus()는 유지 (하위 호환성)
    새로운 check_with_confidence()는 confidence 반환
    """

    def __init__(self, config: Dict = None):
        super().__init__(config)

        # Confidence 계산 파라미터
        self.vwap_threshold_strong = 0.005  # 0.5% 이상 돌파 = 강한 신호
        self.vwap_threshold_weak = 0.001    # 0.1% 이상 돌파 = 약한 신호

    def calculate_vwap_strength(self, price: float, vwap: float) -> float:
        """
        VWAP 돌파 강도 계산

        Returns:
            0.0 ~ 0.4 점수
        """
        if price <= vwap:
            return 0.0

        # 돌파 비율
        strength = (price - vwap) / vwap

        if strength >= self.vwap_threshold_strong:  # 0.5%+ 돌파
            return 0.4
        elif strength >= self.vwap_threshold_weak:  # 0.1%+ 돌파
            # 0.001 ~ 0.005 → 0.1 ~ 0.4 선형 스케일
            score = 0.1 + (strength - self.vwap_threshold_weak) / \
                    (self.vwap_threshold_strong - self.vwap_threshold_weak) * 0.3
            return min(score, 0.4)
        else:
            # 0.1% 미만
            return strength * 100  # 0.1% = 0.01

    def calculate_ema_strength(
        self,
        close_5m: float,
        ema_5m: float,
        close_15m: float,
        ema_15m: float
    ) -> float:
        """
        EMA 정렬 강도 계산

        Returns:
            0.0 ~ 0.3 점수
        """
        score = 0.0

        # 5분봉 EMA 정렬
        if close_5m > ema_5m:
            strength_5m = (close_5m - ema_5m) / ema_5m
            if strength_5m > 0.01:  # 1%+ 괴리
                score += 0.15
            else:
                score += min(strength_5m * 15, 0.15)

        # 15분봉 EMA 정렬
        if close_15m > ema_15m:
            strength_15m = (close_15m - ema_15m) / ema_15m
            if strength_15m > 0.01:  # 1%+ 괴리
                score += 0.15
            else:
                score += min(strength_15m * 15, 0.15)

        return min(score, 0.3)

    def calculate_volume_strength(self, df_1m: pd.DataFrame) -> float:
        """
        거래량 증가 강도 계산

        Returns:
            0.0 ~ 0.3 점수
        """
        try:
            if 'volume' not in df_1m.columns or len(df_1m) < 40:
                return 0.0

            # 거래량 Z-score
            vol = df_1m['volume']
            mean = vol.rolling(40).mean().iloc[-1]
            std = vol.rolling(40).std().iloc[-1]
            current = vol.iloc[-1]

            if std < 1e-9:
                return 0.0

            z = (current - mean) / std

            if z > 3.0:  # 3σ 이상
                return 0.3
            elif z > 2.0:  # 2σ ~ 3σ
                return 0.2
            elif z > 1.0:  # 1σ ~ 2σ
                return 0.1
            else:
                return max(0.0, z / 10)

        except Exception:
            return 0.0

    def check_with_confidence(
        self,
        stock_code: str,
        market: str = 'KOSPI',
        df_1m: pd.DataFrame = None
    ) -> FilterResult:
        """
        MTF Consensus + Confidence 계산

        Returns:
            FilterResult(passed, confidence, reason)
        """
        # 기존 check_consensus() 호출
        consensus, reason, details = self.check_consensus(stock_code, market, df_1m)

        # Confidence 계산
        confidence = 0.0

        if not consensus:
            # Pass하지 못하면 confidence = 0
            return FilterResult(False, 0.0, reason)

        try:
            # 1. VWAP 돌파 강도 (0~0.4)
            price = details.get('1m_price', 0)
            vwap = details.get('1m_vwap', 0)
            vwap_conf = self.calculate_vwap_strength(price, vwap)

            # 2. EMA 정렬 강도 (0~0.3)
            close_5m = details.get('5m_close', 0)
            ema_5m = details.get('5m_ema20', 0)
            close_15m = details.get('15m_close', 0)
            ema_15m = details.get('15m_ema20', 0)
            ema_conf = self.calculate_ema_strength(close_5m, ema_5m, close_15m, ema_15m)

            # 3. 거래량 증가 (0~0.3)
            if df_1m is not None:
                volume_conf = self.calculate_volume_strength(df_1m)
            else:
                volume_conf = 0.0

            # 합산 (0~1.0)
            confidence = vwap_conf + ema_conf + volume_conf
            confidence = min(confidence, 1.0)

            # 상세 정보 추가
            detailed_reason = (
                f"{reason} | "
                f"Conf={confidence:.2f} "
                f"(VWAP:{vwap_conf:.2f} EMA:{ema_conf:.2f} Vol:{volume_conf:.2f})"
            )

            return FilterResult(True, confidence, detailed_reason)

        except Exception as e:
            console.print(f"[dim]⚠️  Confidence 계산 실패: {e}[/dim]")
            # 에러 시 기본 confidence 0.5 (Pass는 했지만 신뢰도 중간)
            return FilterResult(True, 0.5, f"{reason} | Conf=0.5 (default)")


if __name__ == "__main__":
    """테스트 코드"""
    print("=" * 80)
    print("🧪 MTF Consensus V2 (Confidence) 테스트")
    print("=" * 80)

    mtf = MultiTimeframeConsensusV2()

    # 테스트 종목
    test_stocks = [
        ("005930", "KOSPI"),  # 삼성전자
        ("035420", "KOSPI"),  # NAVER
    ]

    for stock_code, market in test_stocks:
        print(f"\n종목: {stock_code} ({market})")

        # V1 (기존)
        consensus, reason, details = mtf.check_consensus(stock_code, market)
        print(f"  V1 (기존): {consensus} - {reason}")

        # V2 (Confidence)
        result = mtf.check_with_confidence(stock_code, market)
        print(f"  V2 (Conf): {result.passed} - {result.reason}")
        print(f"           Confidence = {result.confidence:.2f}")
