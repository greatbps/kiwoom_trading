"""
L6: Pre-Trade Validator V2 - Confidence 반환

기존: validate_trade() → (allowed, reason, stats)
개선: check_with_confidence() → FilterResult(passed, confidence, reason)
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.pre_trade_validator import PreTradeValidator
from trading.filters.base_filter import FilterResult
from utils.config_loader import ConfigLoader
from rich.console import Console

# Phase 1: RSVI Integration (2025-11-30)
from analyzers.volume_indicators import attach_rsvi_indicators, calculate_rsvi_score

console = Console()


class PreTradeValidatorV2(PreTradeValidator):
    """
    L6 Pre-Trade Validator V2 - Confidence 기반

    기존 validate_trade()는 유지 (하위 호환성)
    새로운 check_with_confidence()는 FilterResult 반환
    """

    def __init__(self, config: ConfigLoader, **kwargs):
        super().__init__(config, **kwargs)

        # Confidence 가중치
        self.pf_weight = 0.4           # Profit Factor (40%)
        self.win_rate_weight = 0.3     # 승률 (30%)
        self.avg_profit_weight = 0.3   # 평균 수익률 (30%)

    def calculate_profit_factor_confidence(self, pf: float) -> float:
        """
        Profit Factor → Confidence

        Args:
            pf: Profit Factor (총수익/총손실)

        Returns:
            0.0 ~ 0.4 점수
        """
        if pf < 1.0:
            # PF < 1.0 이면 손실 전략
            return 0.0

        # PF를 0~0.4 범위로 변환
        # 1.0 = 0.0, 1.15 = 0.15, 1.5+ = 0.4
        if pf >= 1.5:
            return self.pf_weight
        elif pf >= self.min_profit_factor:  # 1.15+
            # 1.15 ~ 1.5 → 0.15 ~ 0.4 선형 스케일
            normalized = (pf - self.min_profit_factor) / (1.5 - self.min_profit_factor)
            return (0.15 + normalized * 0.25) * (self.pf_weight / 0.4)
        else:
            # 1.0 ~ 1.15 → 0.0 ~ 0.15 선형 스케일
            normalized = (pf - 1.0) / (self.min_profit_factor - 1.0)
            return (normalized * 0.15) * (self.pf_weight / 0.4)

    def calculate_win_rate_confidence(
        self,
        win_count: int,
        total_trades: int
    ) -> float:
        """
        승률 → Confidence (윌슨 하한 기반)

        Args:
            win_count: 승리 횟수
            total_trades: 전체 거래 횟수

        Returns:
            0.0 ~ 0.3 점수
        """
        if total_trades == 0:
            return 0.0

        # 윌슨 하한 (보수적 승률 추정)
        wlb = self._wilson_lower_bound(win_count, total_trades) * 100.0

        # 승률을 0~0.3 범위로 변환
        # 30% 이하 = 0.0, 40% = 0.15, 60%+ = 0.3
        if wlb >= 60.0:
            return self.win_rate_weight
        elif wlb >= self.min_win_rate:  # 40%+
            # 40% ~ 60% → 0.15 ~ 0.3
            normalized = (wlb - self.min_win_rate) / (60.0 - self.min_win_rate)
            return (0.15 + normalized * 0.15) * (self.win_rate_weight / 0.3)
        elif wlb >= 30.0:
            # 30% ~ 40% → 0.0 ~ 0.15
            normalized = (wlb - 30.0) / (self.min_win_rate - 30.0)
            return (normalized * 0.15) * (self.win_rate_weight / 0.3)
        else:
            return 0.0

    def calculate_avg_profit_confidence(self, avg_profit_pct: float) -> float:
        """
        평균 수익률 → Confidence

        Args:
            avg_profit_pct: 평균 수익률 (%)

        Returns:
            0.0 ~ 0.3 점수
        """
        if avg_profit_pct <= 0:
            return 0.0

        # 평균 수익률을 0~0.3 범위로 변환
        # 0.3% = 0.1, 0.5% = 0.15, 1.0%+ = 0.3
        if avg_profit_pct >= 1.0:
            return self.avg_profit_weight
        elif avg_profit_pct >= self.min_avg_profit:  # 0.3%+
            # 0.3% ~ 1.0% → 0.1 ~ 0.3
            normalized = (avg_profit_pct - self.min_avg_profit) / (1.0 - self.min_avg_profit)
            return (0.1 + normalized * 0.2) * (self.avg_profit_weight / 0.3)
        else:
            # 0% ~ 0.3% → 0.0 ~ 0.1
            normalized = avg_profit_pct / self.min_avg_profit
            return (normalized * 0.1) * (self.avg_profit_weight / 0.3)

    def check_with_confidence(
        self,
        stock_code: str,
        stock_name: str,
        historical_data: pd.DataFrame,
        current_price: float,
        current_time,
        historical_data_30m: Optional[pd.DataFrame] = None
    ) -> FilterResult:
        """
        L6 Pre-Trade Validation + RSVI + Confidence 계산 (Phase 1)

        Phase 1 개선사항 (2025-11-30):
        - RSVI 하드컷: vol_z20 < -1.0 AND vroc10 < -0.5 → 즉시 차단
        - RSVI 점수 계산 (0.0 ~ 1.0)
        - 최종 confidence = 0.3 * backtest + 0.7 * rsvi
        - Threshold: 0.4

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            historical_data: 과거 데이터 (5분봉)
            current_price: 현재가
            current_time: 현재 시간
            historical_data_30m: 과거 데이터 (30분봉, optional)

        Returns:
            FilterResult(passed, confidence, reason)
        """
        if historical_data is None or historical_data.empty:
            reason = "L6: 과거 데이터 없음"
            return FilterResult(False, 0.0, reason)

        # ========================
        # Phase 1: RSVI 통합
        # ========================
        try:
            df = historical_data.copy()

            # ChatGPT 제안: DataFrame 정렬 (Yahoo 역순 대비)
            if 'datetime' in df.columns:
                df = df.sort_values(by='datetime')
            elif df.index.name == 'datetime' or hasattr(df.index, 'tz'):
                df = df.sort_index()

            # 1. RSVI 지표 추가
            if "vol_z20" not in df.columns or "vroc10" not in df.columns:
                df = attach_rsvi_indicators(df)

            latest = df.iloc[-1]
            vol_z20 = float(latest.get("vol_z20", 0.0))
            vroc10 = float(latest.get("vroc10", 0.0))

            if np.isnan(vol_z20):
                vol_z20 = 0.0
            if np.isnan(vroc10):
                vroc10 = 0.0

            # 2. RSVI 하드컷: 완전히 죽은 거래량은 진입 불가
            if vol_z20 < -1.0 and vroc10 < -0.5:
                reason = (
                    f"L6 RSVI 하드컷: 거래량 매우 약함 | "
                    f"vol_z20={vol_z20:.2f}, vroc10={vroc10:.2f}"
                )
                return FilterResult(False, 0.0, reason)

            # 3. RSVI 점수 계산
            rsvi_score = calculate_rsvi_score(vol_z20, vroc10)

        except Exception as e:
            # ChatGPT 제안: 에러 로깅 강화
            console.print(
                f"[yellow]⚠️  RSVI 계산 오류 ({stock_code}): {e} "
                f"→ Default Score 0.5 적용[/yellow]"
            )
            # RSVI 실패 시 기본값 0.5 (중간)
            vol_z20 = 0.0
            vroc10 = 0.0
            rsvi_score = 0.5

        # ========================
        # 기존 백테스트 검증
        # ========================
        allowed, reason, stats = self.validate_trade(
            stock_code, stock_name, df,
            current_price, current_time, historical_data_30m
        )

        if not allowed:
            # 백테스트 검증 실패 시 confidence = 0
            return FilterResult(False, 0.0, f"L6 검증 실패: {reason}")

        # Confidence 계산
        try:
            # 1. Profit Factor (0~0.4)
            pf = stats.get('profit_factor', 0)
            pf_conf = self.calculate_profit_factor_confidence(pf)

            # 2. 승률 (윌슨 하한 기반, 0~0.3)
            win_count = stats.get('win_count', 0)
            total_trades = stats.get('total_trades', 0)
            win_rate_conf = self.calculate_win_rate_confidence(win_count, total_trades)

            # 3. 평균 수익률 (0~0.3)
            avg_profit_pct = stats.get('avg_profit_pct', 0)
            avg_profit_conf = self.calculate_avg_profit_confidence(avg_profit_pct)

            # 백테스트 confidence (0~1.0)
            backtest_conf = pf_conf + win_rate_conf + avg_profit_conf
            backtest_conf = min(backtest_conf, 1.0)

            # ChatGPT 제안: backtest_conf None 처리
            backtest_conf = backtest_conf or 0.0

            # Fallback Stage 패널티 적용
            fallback_stage = stats.get('fallback_stage', 0)
            if fallback_stage > 0:
                # Stage 1: -10%, Stage 2: -20%, Stage 3: -30%
                penalty = fallback_stage * 0.1
                backtest_conf = max(backtest_conf - penalty, 0.2)  # 최소 0.2 유지

            # ========================
            # ChatGPT Safety Gate 추가
            # ========================
            # 백테스트 신뢰도가 너무 낮으면(0.1 미만) RSVI가 좋아도 진입 차단
            # (과거에 무조건 손실을 봤던 패턴은 거래량 터져도 위험)
            BACKTEST_MIN_THRESHOLD = 0.1
            if backtest_conf < BACKTEST_MIN_THRESHOLD:
                reason = (
                    f"L6 Safety Gate: 백테스트 점수 과락 "
                    f"(BT={backtest_conf:.2f} < {BACKTEST_MIN_THRESHOLD:.2f}) | "
                    f"RSVI={rsvi_score:.2f} (무시)"
                )
                console.print(f"[red]🚫 {stock_code}: {reason}[/red]")
                return FilterResult(False, backtest_conf, reason)

            # ========================
            # Phase 1: RSVI + Backtest 결합
            # ========================
            # 최종 confidence = 0.6 * backtest + 0.4 * rsvi (2026-03-05 튜닝: BT 오염 영향 완화)
            final_confidence = (0.6 * backtest_conf) + (0.4 * rsvi_score)
            final_confidence = max(0.0, min(1.0, final_confidence))

            # Threshold 체크 (0.4)
            threshold = 0.25  # 2026-03-05 튜닝: 0.4 → 0.25 (BT 연패 오염 완화)
            if final_confidence < threshold:
                reason = (
                    f"L6+RSVI: Confidence 부족 ({final_confidence:.2f} < {threshold:.2f}) | "
                    f"BT={backtest_conf:.2f}, RSVI={rsvi_score:.2f}"
                )
                return FilterResult(False, final_confidence, reason)

            # 상세 정보
            wlb = self._wilson_lower_bound(win_count, total_trades) * 100.0 if total_trades > 0 else 0

            detailed_reason = (
                f"L6+RSVI 통과 | "
                f"Conf={final_confidence:.2f} (BT:{backtest_conf:.2f} RSVI:{rsvi_score:.2f})\n"
                f"  └ RSVI: vol_z20={vol_z20:+.2f}, vroc10={vroc10:+.2f}\n"
                f"  └ 백테스트 {total_trades}회, 승률(윌슨하한) {wlb:.1f}%, "
                f"PF {pf:.2f}, 평균 {avg_profit_pct:+.2f}%"
            )

            if fallback_stage > 0:
                detailed_reason += f"\n  └ Stage {fallback_stage} Fallback (conf -{penalty*100:.0f}%)"

            return FilterResult(True, final_confidence, detailed_reason)

        except Exception as e:
            console.print(f"[dim]⚠️  L6 Confidence 계산 실패: {e}[/dim]")
            # 에러 시 기본 confidence 0.5 (Pass는 했지만 신뢰도 중간)
            return FilterResult(True, 0.5, f"L6 검증 통과 | Conf=0.5 (default)")


if __name__ == "__main__":
    """테스트 코드"""
    from utils.config_loader import ConfigLoader

    print("=" * 80)
    print("🧪 Pre-Trade Validator V2 (Confidence) 테스트")
    print("=" * 80)

    # Config 로드
    config = ConfigLoader()
    validator = PreTradeValidatorV2(config)

    # 테스트 케이스 (수동 시뮬레이션)
    test_cases = [
        {
            "name": "강한 전략 (PF 1.8, 승률 60%)",
            "stats": {
                'total_trades': 10,
                'win_count': 6,
                'loss_count': 4,
                'win_rate': 60.0,
                'avg_profit_pct': 1.2,
                'profit_factor': 1.8
            }
        },
        {
            "name": "중간 전략 (PF 1.2, 승률 45%)",
            "stats": {
                'total_trades': 8,
                'win_count': 4,
                'loss_count': 4,
                'win_rate': 50.0,
                'avg_profit_pct': 0.5,
                'profit_factor': 1.2
            }
        },
        {
            "name": "약한 전략 (PF 1.05, 승률 35%)",
            "stats": {
                'total_trades': 6,
                'win_count': 2,
                'loss_count': 4,
                'win_rate': 33.3,
                'avg_profit_pct': 0.2,
                'profit_factor': 1.05
            }
        }
    ]

    for test in test_cases:
        print(f"\n{test['name']}")
        stats = test['stats']

        # Confidence 계산
        pf_conf = validator.calculate_profit_factor_confidence(stats['profit_factor'])
        win_conf = validator.calculate_win_rate_confidence(stats['win_count'], stats['total_trades'])
        avg_conf = validator.calculate_avg_profit_confidence(stats['avg_profit_pct'])

        total_conf = pf_conf + win_conf + avg_conf
        total_conf = min(total_conf, 1.0)

        print(f"  PF {stats['profit_factor']:.2f} → conf {pf_conf:.2f}")
        print(f"  승률 {stats['win_rate']:.1f}% → conf {win_conf:.2f}")
        print(f"  평균수익 {stats['avg_profit_pct']:+.2f}% → conf {avg_conf:.2f}")
        print(f"  📊 최종 Confidence = {total_conf:.2f}")
