"""
Signal Orchestrator - L0~L6 시그널 파이프라인 통합 관리자

시그널 계층 구조:
L0: 시스템/리스크 필터 (장 시간, 계좌 손실 한도)
L1: 장세/환경 필터 (RV 기반)
L2: 종목 필터 (RS 상대강도)
L3: 방향성 컨센서스 (MTF)
L4: 수급/오더플로우 (Liquidity Shift)
L5: 타이밍/트리거 (VWAP, Squeeze, Volume)
L6: 사전 검증 (Pre-Trade Validator)
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime, time
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.volatility_regime import VolatilityRegimeDetector
from analyzers.relative_strength_filter import RelativeStrengthFilter

# V2 Filters (Confidence-based)
from analyzers.multi_timeframe_consensus_v2 import MultiTimeframeConsensusV2
from analyzers.liquidity_shift_detector_v2 import LiquidityShiftDetectorV2
from analyzers.squeeze_momentum_v2 import SqueezeMomentumProV2
from analyzers.pre_trade_validator_v2 import PreTradeValidatorV2

# Confidence Aggregator
from trading.confidence_aggregator import ConfidenceAggregator

from rich.console import Console
import logging

console = Console()

# 파일 로거 설정
signal_logger = logging.getLogger('signal_orchestrator')
signal_logger.setLevel(logging.INFO)
log_file = Path(__file__).parent.parent / 'logs' / 'signal_orchestrator.log'
log_file.parent.mkdir(exist_ok=True)
file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
signal_logger.addHandler(file_handler)


class SignalTier:
    """시그널 강도 Tier"""
    TIER_1 = 1  # 최강 (포지션 100%)
    TIER_2 = 2  # 중강 (포지션 50-70%)
    TIER_3 = 3  # 약강 (포지션 30-50%)
    REJECTED = 0  # 거부


class SignalOrchestrator:
    """L0-L6 시그널 파이프라인 통합 오케스트레이터"""

    def __init__(self, config: Dict, api=None):
        """
        Args:
            config: 전략 설정
            api: 키움 API (L4 수급 데이터용)
        """
        self.config = config
        self.api = api

        # L1: 장세 필터
        self.regime_detector = VolatilityRegimeDetector(
            rv_window=10,
            rv_lookback=100,
            high_vol_percentile=0.6,
            low_vol_percentile=0.4
        )

        # L2: RS 필터
        self.rs_filter = RelativeStrengthFilter(
            lookback_days=60,
            min_rs_rating=80  # 초기 30%, 실전 20%로 조정
        )

        # L3: MTF V2 (Confidence-based)
        self.mtf_consensus = MultiTimeframeConsensusV2(config)

        # L4: Liquidity Shift V2 (Confidence-based)
        self.liquidity_detector = LiquidityShiftDetectorV2(
            api=api,
            inst_z_threshold=1.0,
            foreign_z_threshold=1.0,
            order_imbalance_threshold=0.2,
            lookback_days=20
        )

        # L5: Squeeze Momentum V2 (Confidence-based)
        self.squeeze = SqueezeMomentumProV2(
            bb_period=20,
            bb_std=2.0,
            kc_period=20,
            kc_atr_mult=1.5,
            momentum_period=20
        )

        # L6: Pre-Trade Validator V2 (Confidence-based)
        self.validator = PreTradeValidatorV2(
            config=config,
            lookback_days=5,         # 🔧 FIX: 문서 명세 복원 (10 → 5)
            min_trades=2,            # 🔧 FIX: 문서 명세 복원 (6 → 2)
            min_win_rate=40.0,
            min_avg_profit=0.3,
            min_profit_factor=1.15
        )

        # Confidence Aggregator
        self.confidence_aggregator = ConfidenceAggregator()

        # Phase 4: 8-Alpha System + Dynamic Weight Adjuster
        from trading.alpha_engine import SimonsStyleAlphaEngine
        from trading.alphas.vwap_alpha import VWAPAlpha
        from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
        from trading.alphas.obv_trend_alpha import OBVTrendAlpha
        from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
        from trading.alphas.news_score_alpha import NewsScoreAlpha
        # Phase 4: 신규 알파
        from trading.alphas.momentum_alpha import MomentumAlpha
        from trading.alphas.mean_reversion_alpha import MeanReversionAlpha
        from trading.alphas.volatility_alpha import VolatilityAlpha
        # Phase 4: 동적 가중치 조정기
        from trading.dynamic_weight_adjuster import DynamicWeightAdjuster

        # Dynamic Weight Adjuster 초기화
        self.weight_adjuster = DynamicWeightAdjuster()

        # 현재 Market Regime (초기값: NORMAL)
        self.current_regime = "NORMAL"
        self.current_weights = self.weight_adjuster.adjust_weights(self.current_regime)

        # Alpha Engine 초기화 (8 alphas with dynamic weights)
        self._create_alpha_engine()

        # 통계
        self.stats = {
            'l0_blocked': 0,
            'l1_blocked': 0,
            'l2_filtered': 0,
            'l3_blocked': 0,
            'l4_weak': 0,
            'l5_triggered': 0,
            'l6_blocked': 0,
            'total_accepted': 0,
            'alpha_rejected': 0  # Phase 2: Multi-Alpha 차단
        }

    def check_l0_system_filter(self, current_cash: float = 0, daily_pnl: float = 0) -> Tuple[bool, str]:
        """
        L0: 시스템/리스크 필터

        Args:
            current_cash: 현재 잔고
            daily_pnl: 금일 손익

        Returns:
            (pass, reason)
        """
        # 1. 진입 시간 체크 (10:00 이후만 체크, 종료 시간 제한 없음)
        now = datetime.now()
        current_time = now.time()

        entry_start = time(10, 0, 0)  # 10시 이후 매수 (장초반 가격 불안정)
        # entry_end = time(14, 59, 0)   # ❌ 비활성화: 종료 시간 제한 없음

        if current_time < entry_start:
            self.stats['l0_blocked'] += 1
            return False, f"진입 시간 외 ({current_time.strftime('%H:%M')}, 10:00 이전)"

        # 2. 요일 체크 (토요일=5, 일요일=6)
        if now.weekday() >= 5:
            self.stats['l0_blocked'] += 1
            return False, "주말"

        # 3. 일일 손실 한도
        max_daily_loss_pct = self.config.get('risk_control', {}).get('max_daily_loss_pct', 3.0)

        if current_cash > 0:
            daily_loss_pct = (daily_pnl / current_cash) * 100

            if daily_loss_pct <= -max_daily_loss_pct:
                self.stats['l0_blocked'] += 1
                return False, f"일일 손실 한도 초과 ({daily_loss_pct:.2f}%)"

        return True, "OK"

    def check_l1_regime_filter(self, market: str = 'KOSPI') -> Tuple[bool, str, float]:
        """
        L1: 장세/환경 필터

        Args:
            market: 시장 구분

        Returns:
            (use_trend, reason, confidence)
        """
        use_trend, reason, confidence = self.regime_detector.should_use_trend_strategy(market)

        if not use_trend:
            self.stats['l1_blocked'] += 1

        return use_trend, reason, confidence

    def check_l2_rs_filter(self, candidates: List[Dict], market: str = 'KOSPI') -> List[Dict]:
        """
        L2: 종목 필터 (RS)

        Args:
            candidates: 후보 종목 리스트
            market: 시장 구분

        Returns:
            필터링된 종목 리스트
        """
        filtered = self.rs_filter.filter_candidates(candidates, market)

        self.stats['l2_filtered'] += (len(candidates) - len(filtered))

        return filtered

    def check_l3_mtf_consensus(
        self,
        stock_code: str,
        market: str = 'KOSPI',
        df_1m: pd.DataFrame = None
    ) -> Tuple[bool, str, Dict]:
        """
        L3: Multi-Timeframe Consensus

        Args:
            stock_code: 종목코드
            market: 시장 구분
            df_1m: 1분봉 데이터

        Returns:
            (consensus, reason, details)
        """
        consensus, reason, details = self.mtf_consensus.check_consensus(stock_code, market, df_1m)

        if not consensus:
            self.stats['l3_blocked'] += 1

        return consensus, reason, details

    def check_l4_liquidity_shift(self, stock_code: str) -> Tuple[bool, float, str]:
        """
        L4: 수급/오더플로우 체크

        Args:
            stock_code: 종목코드

        Returns:
            (strong_liquidity, strength, reason)
        """
        detected, strength, reason = self.liquidity_detector.detect_shift(stock_code)

        if not detected:
            self.stats['l4_weak'] += 1

        return detected, strength, reason

    def check_l5_trigger(
        self,
        stock_code: str,
        current_price: float,
        df: pd.DataFrame
    ) -> Tuple[bool, str, int]:
        """
        L5: 타이밍/트리거 (VWAP + Squeeze Momentum)

        Args:
            stock_code: 종목코드
            current_price: 현재가
            df: OHLCV 데이터

        Returns:
            (triggered, reason, tier)
        """
        # 기본 진입 조건 (VWAP 돌파)
        if 'vwap' not in df.columns:
            return False, "VWAP 데이터 없음", SignalTier.REJECTED

        vwap = df['vwap'].iloc[-1]
        price_above_vwap = current_price > vwap

        if not price_above_vwap:
            return False, f"VWAP 미돌파 ({current_price:.0f} < {vwap:.0f})", SignalTier.REJECTED

        # 거래량 체크
        volume_ok = True
        if 'volume' in df.columns and len(df) >= 20:
            vol_ma = df['volume'].rolling(20).mean().iloc[-1]
            current_vol = df['volume'].iloc[-1]

            volume_ok = current_vol >= vol_ma * 0.8

            if not volume_ok:
                return False, "거래량 부족", SignalTier.REJECTED

        # Squeeze Momentum 체크
        squeeze_signal, squeeze_reason, squeeze_tier = self.squeeze.generate_signal(df, current_price)

        # Tier 판단
        if squeeze_signal and squeeze_tier == 1:
            # Squeeze Tier 1: 최강 시그널
            tier = SignalTier.TIER_1
            reason = squeeze_reason
        elif squeeze_signal and squeeze_tier == 2:
            # Squeeze Tier 2: 중강 시그널
            tier = SignalTier.TIER_2
            reason = squeeze_reason
        else:
            # Squeeze 없음: 기본 VWAP 돌파만
            tier = SignalTier.TIER_2
            reason = f"VWAP 돌파 ({current_price:.0f} > {vwap:.0f})"

        self.stats['l5_triggered'] += 1
        return True, reason, tier

    def check_l6_validator(
        self,
        stock_code: str,
        stock_name: str,
        current_price: float,
        df: pd.DataFrame
    ) -> Tuple[bool, str, float, int]:
        """
        L6: Pre-Trade Validator (+ 샘플 부족 폴백 로직 지원, fallback_stage 반환 추가)

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            current_price: 현재가
            df: OHLCV 데이터

        Returns:
            (allowed, reason, entry_ratio)
            entry_ratio: 1.0 (정상), 0.5 (Stage 1 폴백), 0.3 (Stage 2 폴백), 0.0 (차단)
        """
        # VWAP 검증
        from datetime import datetime
        allowed, reason, stats = self.validator.validate_trade(
            stock_code=stock_code,
            stock_name=stock_name,
            historical_data=df,
            current_price=current_price,
            current_time=datetime.now()
        )

        # 샘플 부족 폴백 단계 확인 (문서 명세)
        entry_ratio = stats.get('entry_ratio', 1.0)  # 기본값 1.0 (100%)
        fallback_stage = stats.get('fallback_stage', 0)

        if not allowed:
            self.stats['l6_blocked'] += 1

        # 폴백 모드 로깅
        if fallback_stage > 0:
            console.print(f"[yellow]⚠️  {stock_code}: L6 Fallback Stage {fallback_stage}, entry_ratio={entry_ratio}[/yellow]")

        # 🔧 FIX: fallback_stage도 반환 (문서 명세: Stage 결정에 필요)
        return allowed, reason, entry_ratio, fallback_stage

    def calculate_stage(
        self,
        fallback_stage: int,
        confidence: float,
        tier: 'SignalTier'
    ) -> Tuple[int, float]:
        """
        포지션 크기 Stage 결정 (문서 명세: Stage 1/2/3)

        Args:
            fallback_stage: Validator fallback stage (0, 1, 2, 3)
            confidence: 전체 신뢰도 (L1 confidence)
            tier: 신호 Tier

        Returns:
            (stage, stage_multiplier)
            - Stage 1: 100% (정상, 높은 신뢰도)
            - Stage 2: 60% (경고, 중간 신뢰도 또는 fallback_stage=1)
            - Stage 3: 30% (주의, 낮은 신뢰도 또는 fallback_stage>=2)
        """
        # 🔧 FIX: 문서 명세에 따른 Stage 결정 로직

        # fallback_stage가 2 이상이면 무조건 Stage 3
        if fallback_stage >= 2:
            return 3, 0.30

        # fallback_stage가 1이면 Stage 2
        if fallback_stage == 1:
            return 2, 0.60

        # fallback_stage == 0인 경우, confidence와 tier로 판단
        # Tier 1이고 confidence가 높으면 Stage 1
        if tier == SignalTier.TIER_1 and confidence >= 0.8:
            return 1, 1.0

        # Tier 2이거나 중간 confidence면 Stage 2
        if tier == SignalTier.TIER_2 or (tier == SignalTier.TIER_1 and confidence >= 0.6):
            return 2, 0.60

        # Tier 3이거나 낮은 confidence면 Stage 3
        return 3, 0.30

    def evaluate_signal(
        self,
        stock_code: str,
        stock_name: str,
        current_price: float,
        df: pd.DataFrame,
        market: str = 'KOSPI',
        current_cash: float = 0,
        daily_pnl: float = 0
    ) -> Dict:
        """
        전체 시그널 파이프라인 실행 (Confidence-based)

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            current_price: 현재가
            df: OHLCV 데이터
            market: 시장 구분
            current_cash: 현재 잔고
            daily_pnl: 금일 손익

        Returns:
            시그널 평가 결과 dict
        """
        result = {
            'allowed': False,
            'confidence': 0.0,
            'position_size_multiplier': 0.0,
            'rejection_level': None,
            'rejection_reason': None,
            'details': {}
        }

        # L0: 시스템 필터 (Pass/Fail만)
        l0_pass, l0_reason = self.check_l0_system_filter(current_cash, daily_pnl)
        if not l0_pass:
            result['rejection_level'] = 'L0'
            result['rejection_reason'] = l0_reason
            import os
            msg = f"❌ REJECT {stock_code} | PID:{os.getpid()} | L0 | {l0_reason}"
            console.print(f"[red]{msg}[/red]")
            signal_logger.info(msg)
            return result

        # Phase 4: Market Regime 업데이트 및 가중치 동적 조정
        regime, weights_changed = self.update_regime(market)
        result['details']['market_regime'] = regime
        result['details']['weights_updated'] = weights_changed

        # L1: 장세 필터 (Pass/Fail만, 향후 confidence 추가 가능)
        l1_pass, l1_reason, l1_confidence = self.check_l1_regime_filter(market)
        result['details']['l1_regime'] = l1_reason
        result['details']['l1_confidence'] = l1_confidence

        if not l1_pass:
            result['rejection_level'] = 'L1'
            result['rejection_reason'] = l1_reason
            console.print(f"[red]❌ REJECT[/red] {stock_code} | L1 | {l1_reason}")
            return result

        # L3-L6: Confidence-based 필터링
        from trading.filters.base_filter import FilterResult

        # L3: MTF Consensus (L2는 조건검색 단계에서 이미 필터링됨)
        l3_result = self.mtf_consensus.check_with_confidence(stock_code, market, df)
        result['details']['l3_mtf'] = l3_result.reason
        result['details']['l3_confidence'] = l3_result.confidence

        if not l3_result.passed:
            result['rejection_level'] = 'L3'
            result['rejection_reason'] = l3_result.reason
            console.print(f"[red]❌ REJECT[/red] {stock_code} @{current_price:.0f}원 | L3 | {l3_result.reason[:60]}")
            return result

        # L4: Liquidity Shift
        l4_result = self.liquidity_detector.check_with_confidence(stock_code)
        result['details']['l4_liquidity'] = l4_result.reason
        result['details']['l4_confidence'] = l4_result.confidence

        # L4는 선택사항 (낮은 수급이라도 진행 가능)
        # 단, confidence가 0이면 경고
        if not l4_result.passed:
            console.print(f"[yellow]⚠️  {stock_code}: L4 수급 전환 없음 (진행 가능)[/yellow]")

        # L5: Squeeze Momentum
        l5_result = self.squeeze.check_with_confidence(df)
        result['details']['l5_squeeze'] = l5_result.reason
        result['details']['l5_confidence'] = l5_result.confidence

        # L5도 선택사항 (Squeeze 없어도 VWAP 돌파만으로 진행 가능)
        if not l5_result.passed:
            console.print(f"[yellow]⚠️  {stock_code}: L5 Squeeze 없음 (진행 가능)[/yellow]")

        # L6: Pre-Trade Validator
        from datetime import datetime
        l6_result = self.validator.check_with_confidence(
            stock_code=stock_code,
            stock_name=stock_name,
            historical_data=df,
            current_price=current_price,
            current_time=datetime.now()
        )
        result['details']['l6_validator'] = l6_result.reason
        result['details']['l6_confidence'] = l6_result.confidence

        if not l6_result.passed:
            result['rejection_level'] = 'L6'
            result['rejection_reason'] = l6_result.reason
            console.print(f"[red]❌ REJECT[/red] {stock_code} @{current_price:.0f}원 | L6 | {l6_result.reason[:60]}")
            return result

        # Confidence 결합
        filter_results = {
            "L3_MTF": l3_result,
            "L4_LIQUIDITY": l4_result if l4_result.passed else FilterResult(True, 0.3, "L4 Default"),
            "L5_SQUEEZE": l5_result if l5_result.passed else FilterResult(True, 0.3, "L5 Default"),
            "L6_VALIDATOR": l6_result
        }

        final_confidence, should_pass, aggregation_reason = self.confidence_aggregator.aggregate(filter_results)

        result['confidence'] = final_confidence
        result['aggregation_reason'] = aggregation_reason

        if not should_pass:
            # Confidence 부족 (< 0.4)
            result['rejection_level'] = 'CONFIDENCE'
            result['rejection_reason'] = aggregation_reason
            msg = f"❌ REJECT {stock_code} @{current_price:.0f}원 | CONFIDENCE | {aggregation_reason}"
            console.print(f"[red]{msg}[/red]")
            signal_logger.info(msg)
            return result

        # Phase 2: Multi-Alpha Engine 실행
        state = {
            "df": df,
            "df_5m": df,  # 5분봉 (없으면 1분봉 재사용)
            "institutional_flow": self._get_institutional_flow(stock_code),
            "ai_analysis": None  # 나중에 AI 분석 통합 시 사용
        }

        alpha_result = self.alpha_engine.compute(stock_code, state)
        aggregate_score = alpha_result["aggregate_score"]

        result['aggregate_score'] = aggregate_score
        result['alpha_breakdown'] = alpha_result["alphas"]

        # Multi-Alpha 임계값 체크 (임시 완화: 1.0 → 0.8)
        ALPHA_THRESHOLD = 0.8
        if aggregate_score <= ALPHA_THRESHOLD:
            # aggregate_score가 임계값 이하면 매수 조건 미달
            self.stats['alpha_rejected'] += 1
            result['rejection_level'] = 'ALPHA'
            result['rejection_reason'] = f"Multi-Alpha 점수 부족 ({aggregate_score:+.2f} <= {ALPHA_THRESHOLD})"
            console.print(f"[red]❌ REJECT[/red] {stock_code} @{current_price:.0f}원 | ALPHA | score={aggregate_score:+.2f} (threshold={ALPHA_THRESHOLD})")
            return result

        # 모든 레벨 통과!
        self.stats['total_accepted'] += 1
        result['allowed'] = True

        # Confidence 기반 포지션 크기 결정 (0.6 ~ 1.0)
        position_multiplier = self.confidence_aggregator.calculate_position_multiplier(final_confidence)
        result['position_size_multiplier'] = position_multiplier

        # ✅ 승인 로그 (프로세스 ID 포함)
        import os
        msg = f"✅ ACCEPT {stock_code} @{current_price:.0f}원 | PID:{os.getpid()} | conf={final_confidence:.2f} alpha={aggregate_score:+.2f} pos_mult={position_multiplier:.2f}"
        console.print(f"[green]{msg}[/green]")
        signal_logger.info(msg)

        return result

    def _get_institutional_flow(self, stock_code: str) -> Optional[Dict]:
        """
        기관/외인 수급 데이터 조회 (L4 Liquidity Detector 활용)

        Returns:
            {
                "inst_net_buy": int,
                "foreign_net_buy": int,
                "total_traded_value": int
            }
        """
        if not self.api:
            return None

        try:
            # L4 Liquidity Detector가 이미 수급 데이터를 수집하고 있음
            # 해당 데이터를 재사용
            return self.liquidity_detector.get_flow_data(stock_code)
        except Exception as e:
            console.print(f"[yellow]⚠️  수급 데이터 조회 실패: {e}[/yellow]")
            return None

    def _create_alpha_engine(self):
        """
        Alpha Engine 생성 (현재 가중치 기반)

        Phase 4: 8개 알파 (기존 5 + 신규 3) + 동적 가중치
        """
        from trading.alpha_engine import SimonsStyleAlphaEngine
        from trading.alphas.vwap_alpha import VWAPAlpha
        from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
        from trading.alphas.obv_trend_alpha import OBVTrendAlpha
        from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
        from trading.alphas.news_score_alpha import NewsScoreAlpha
        from trading.alphas.momentum_alpha import MomentumAlpha
        from trading.alphas.mean_reversion_alpha import MeanReversionAlpha
        from trading.alphas.volatility_alpha import VolatilityAlpha

        weights = self.current_weights

        self.alpha_engine = SimonsStyleAlphaEngine(
            alphas=[
                # Phase 2-3: 기존 5개 알파
                VWAPAlpha(weight=weights["VWAP"]),
                VolumeSpikeAlpha(weight=weights["VolumeSpike"], lookback=40),
                OBVTrendAlpha(weight=weights["OBV"], fast=5, slow=20),
                InstitutionalFlowAlpha(weight=weights["Institutional"]),
                NewsScoreAlpha(weight=weights["News"]),
                # Phase 4: 신규 3개 알파
                MomentumAlpha(weight=weights["Momentum"]),
                MeanReversionAlpha(weight=weights["MeanReversion"]),
                VolatilityAlpha(weight=weights["Volatility"]),
            ]
        )

    def update_regime(self, market: str = 'KOSPI'):
        """
        Market Regime 감지 및 가중치 업데이트

        Args:
            market: 시장 구분 ('KOSPI', 'KOSDAQ')

        Returns:
            (regime, weights_changed)
        """
        # L1 Regime Detector로 변동성 체제 파악
        regime, rv_percentile, details = self.regime_detector.get_market_regime(market)

        # Regime이 변경되었는지 확인
        weights_changed = False

        if regime != self.current_regime:
            console.print(f"\n[bold yellow]🔄 Market Regime 변경: {self.current_regime} → {regime}[/bold yellow]")
            self.current_regime = regime

            # 가중치 재조정
            old_weights = self.current_weights.copy()
            self.current_weights = self.weight_adjuster.adjust_weights(regime, rv_percentile)

            # 변경 사항 출력
            self.weight_adjuster.print_weight_comparison(regime, self.current_weights)

            # Alpha Engine 재생성
            self._create_alpha_engine()

            weights_changed = True

            console.print(f"[green]✅ Alpha Engine이 새로운 가중치로 업데이트되었습니다.[/green]")
            console.print()

        return regime, weights_changed

    def get_stats(self) -> Dict:
        """통계 조회"""
        stats = self.stats.copy()
        # Phase 4: Regime 정보 추가
        stats['current_regime'] = self.current_regime
        return stats


if __name__ == "__main__":
    """테스트 코드"""

    print("=" * 80)
    print("🧪 Signal Orchestrator 테스트")
    print("=" * 80)

    # 테스트용 config
    test_config = {
        'risk_control': {
            'max_daily_loss_pct': 3.0
        }
    }

    # Orchestrator 생성
    orchestrator = SignalOrchestrator(test_config)

    # L0 테스트
    print("\n📊 L0: 시스템 필터")
    print("-" * 80)
    l0_pass, l0_reason = orchestrator.check_l0_system_filter(
        current_cash=10000000,
        daily_pnl=-100000
    )
    print(f"  결과: {'✅ PASS' if l0_pass else '❌ BLOCK'}")
    print(f"  이유: {l0_reason}")

    # L1 테스트
    print("\n📊 L1: 장세 필터")
    print("-" * 80)
    l1_pass, l1_reason, l1_conf = orchestrator.check_l1_regime_filter('KOSPI')
    print(f"  결과: {'✅ PASS' if l1_pass else '❌ BLOCK'}")
    print(f"  이유: {l1_reason}")
    print(f"  신뢰도: {l1_conf * 100:.0f}%")

    print("\n" + "=" * 80)
    print("✅ 테스트 완료")
    print("=" * 80)

    # 통계 출력
    stats = orchestrator.get_stats()
    print("\n📊 통계:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
