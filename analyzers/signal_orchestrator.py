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
from typing import Dict, List, Tuple, Optional
from datetime import datetime, time
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.volatility_regime import VolatilityRegimeDetector  # noqa: E402
from analyzers.relative_strength_filter import RelativeStrengthFilter  # noqa: E402

# V2 Filters (Confidence-based)
from analyzers.multi_timeframe_consensus_v2 import MultiTimeframeConsensusV2  # noqa: E402
from analyzers.liquidity_shift_detector_v2 import LiquidityShiftDetectorV2  # noqa: E402
from analyzers.squeeze_momentum_v2 import SqueezeMomentumProV2  # noqa: E402
from analyzers.pre_trade_validator_v2 import PreTradeValidatorV2  # noqa: E402

# Confidence Aggregator
from trading.confidence_aggregator import ConfidenceAggregator  # noqa: E402

from rich.console import Console  # noqa: E402
import logging  # noqa: E402

console = Console()

# 파일 로거 설정
signal_logger = logging.getLogger('signal_orchestrator')
logger = logging.getLogger(__name__)
signal_logger.setLevel(logging.INFO)
log_file = Path(__file__).parent.parent / 'logs' / 'signal_orchestrator.log'
log_file.parent.mkdir(exist_ok=True)
file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
signal_logger.addHandler(file_handler)
# [T13 FIX] propagate 미설정 → root logger(auto_trading_*.log/stderr)로도 중복 전파되어
# REGIME_BLOCK 등 로그 grep count가 약 2배로 부풀려지던 문제. 전용 FileHandler만 사용.
signal_logger.propagate = False


class SignalTier:
    """시그널 강도 Tier"""
    TIER_1 = 1  # 최강 (포지션 100%)
    TIER_2 = 2  # 중강 (포지션 50-70%)
    TIER_3 = 3  # 약강 (포지션 30-50%)
    REJECTED = 0  # 거부


class SignalOrchestrator:
    """L0-L6 시그널 파이프라인 통합 오케스트레이터"""

    def __init__(self, config: Dict, api=None, db=None):
        """
        Args:
            config: 전략 설정
            api: 키움 API (L4 수급 데이터용)
            db: TradingDatabase 인스턴스 (G3 signal_events 기록용, None이면 로그만)
        """
        self.config = config
        self.api = api
        self.db = db

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
            min_rs_rating=70,  # 2026-06-08: 80→70 (상위 30%), 일 3~8개 → 더 많은 종목 확보
            api=api,
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
        _l6_cfg = config.get('orchestrator', {}).get('l6', {})
        self.validator = PreTradeValidatorV2(
            config=config,
            lookback_days=5,         # 🔧 FIX: 문서 명세 복원 (10 → 5)
            min_trades=2,            # 🔧 FIX: 문서 명세 복원 (6 → 2)
            min_win_rate=_l6_cfg.get('min_win_rate', 40.0),
            min_avg_profit=_l6_cfg.get('min_avg_profit', 0.3),
            min_profit_factor=_l6_cfg.get('min_profit_factor', 1.15),
        )

        # Confidence Aggregator
        self.confidence_aggregator = ConfidenceAggregator()

        # Phase 4: 8-Alpha System + Dynamic Weight Adjuster
        # Phase 4: 신규 알파
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

        # ACCEPT 이벤트 추적 — {symbol: (timestamp, price)}
        # diagnostic_mode=True 시 main loop이 이 종목을 watchlist 강제 포함
        # recent_accepts: 최신 accept (window 체크용, 매 accept마다 갱신)
        self.recent_accepts: dict = {}
        # recent_accepts_first: 당일 최초 accept (t0 관측용, 하루 1회만 기록)
        self.recent_accepts_first: dict = {}  # {symbol: (timestamp, price)}

        # D1: pre-candidate 추적 — {symbol: (timestamp, price, date)}
        # L3 통과 시 등록, 당일 최초값만 보존 (덮어쓰기 금지)
        self.pre_candidates: dict = {}  # {symbol: (datetime, int_price, date)}

        # G3 평가 시각 추적 — {symbol: datetime}
        self._g3_eval_times: dict = {}
        # v1.2: Phase2 SMC score 예외용 힌트 캐시 (main에서 설정)
        self._last_smc_score_hint: dict = {}   # {symbol: float}
        self._last_reclaim_hint: dict = {}     # {symbol: bool}

    def set_smc_hints(self, stock_code: str, smc_score: float, reclaim_detected: bool) -> None:
        """v1.2: G3 Phase2 예외를 위한 SMC 힌트 설정 (main에서 CHoCH 평가 후 호출)"""
        self._last_smc_score_hint[stock_code] = smc_score
        self._last_reclaim_hint[stock_code] = reclaim_detected

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
            logger.debug(f"[L6_FALLBACK] {stock_code} stage={fallback_stage}")

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
            logger.debug(f"[REJECT_L1] {stock_code} | {l1_reason}")
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
            logger.debug(f"[REJECT_L3] {stock_code} | {l3_result.reason[:60]}")
            return result

        # D1: L3 통과 → pre-candidate 등록 (YAML pre_candidate.enabled=true 시)
        _d1_cfg = self.config.get('pre_candidate', {})
        if _d1_cfg.get('enabled', False):
            self._register_pre_candidate(stock_code, current_price)

        # L4: Liquidity Shift
        l4_result = self.liquidity_detector.check_with_confidence(stock_code)
        result['details']['l4_liquidity'] = l4_result.reason
        result['details']['l4_confidence'] = l4_result.confidence

        # L4는 선택사항 (낮은 수급이라도 진행 가능)
        if not l4_result.passed:
            logger.debug(f"[L4_SKIP] {stock_code}: 수급 전환 없음")

        # L5: Squeeze Momentum
        l5_result = self.squeeze.check_with_confidence(df)
        result['details']['l5_squeeze'] = l5_result.reason
        result['details']['l5_confidence'] = l5_result.confidence

        # L5도 선택사항 (Squeeze 없어도 VWAP 돌파만으로 진행 가능)
        if not l5_result.passed:
            logger.debug(f"[L5_SKIP] {stock_code}: Squeeze 없음")

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
            logger.debug(f"[REJECT_L6] {stock_code} | {l6_result.reason[:60]}")
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
            msg = f"[REJECT_CONF] {stock_code} | {aggregation_reason}"
            logger.debug(msg)
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

        # Multi-Alpha 임계값 체크 — v1.2: alpha_sizing_mode 시 block → size 축소
        _orch_cfg = self.config.get('orchestrator', {})
        ALPHA_THRESHOLD = _orch_cfg.get('alpha_threshold', 0.8)
        _alpha_sizing_mode = _orch_cfg.get('alpha_sizing_mode', False)
        if aggregate_score <= ALPHA_THRESHOLD:
            if _alpha_sizing_mode:
                # score 미달 → 차단 대신 size 축소
                _alpha_min_mult = _orch_cfg.get('alpha_sizing_min_mult', 0.5)
                result['position_size_multiplier'] = min(result.get('position_size_multiplier', 1.0), _alpha_min_mult)
                logger.debug(f"[ALPHA_SIZING] {stock_code} | score={aggregate_score:+.2f} → size×{_alpha_min_mult}")
            else:
                self.stats['alpha_rejected'] += 1
                result['rejection_level'] = 'ALPHA'
                result['rejection_reason'] = f"Multi-Alpha 점수 부족 ({aggregate_score:+.2f} <= {ALPHA_THRESHOLD})"
                logger.debug(f"[REJECT_ALPHA] {stock_code} | score={aggregate_score:+.2f}")
                return result

        # G3: 사전 진입 필터 (L6 통과 직후 / ACCEPT 기록 직전)
        # v1.2: Phase별 delay threshold 분리
        #   Phase1 (09:30~10:00): delay≤3분 허용
        #   Phase2 (10:00~11:30): delay≤2분, SMC score≥80+reclaim 예외
        #   Phase3 (11:30~ ):     c_late_block이 처리
        if self.config.get('late_entry_control', {}).get('g3_delay0_quality_gate', {}).get('enabled', False):
            _g3_cfg = self.config.get('late_entry_control', {}).get('g3_delay0_quality_gate', {})
            _g3_now = datetime.now()
            _g3_hour = _g3_now.hour
            _g3_minute = _g3_now.minute

            # Phase별 delay threshold 결정
            _block_before_hour = _g3_cfg.get('block_before_hour', 10)
            _phase2_end_str = _g3_cfg.get('phase2_end_time', '11:30')
            _phase2_end_h, _phase2_end_m = map(int, _phase2_end_str.split(':'))
            _in_phase1 = _g3_hour < _block_before_hour
            _in_phase2 = (not _in_phase1) and (
                _g3_hour < _phase2_end_h or (_g3_hour == _phase2_end_h and _g3_minute < _phase2_end_m)
            )

            if _in_phase1:
                _g3_threshold = float(_g3_cfg.get('phase1_delay_threshold_min', 3.0))
            else:
                _g3_threshold = float(_g3_cfg.get('delay_threshold_min', 2.0))

            # first_signal_time: pre_candidate 있으면 그 시각, 없으면 현재(=첫 신호)
            _pre_ts, _ = self.get_pre_candidate_info(stock_code)
            if _pre_ts is not None:
                _g3_first_signal_time = _pre_ts
                _g3_delay_min = (_g3_now - _pre_ts).total_seconds() / 60.0
            else:
                _g3_first_signal_time = _g3_now
                _g3_delay_min = 0.0

            _g3_is_delay0 = _g3_delay_min <= _g3_threshold
            self._g3_eval_times[stock_code] = _g3_now

            if _g3_is_delay0 and self._is_early_open():
                result['rejection_level'] = 'G3'
                result['rejection_reason'] = (f"G3_EARLY_OPEN: 장초반({_g3_now.strftime('%H:%M')}) "
                                              f"delay={_g3_delay_min:.0f}m 즉시진입 차단")
                _msg = (f"[G3_REJECT][EARLY_OPEN] {stock_code} "
                        f"first_signal={_g3_first_signal_time.strftime('%H:%M')} "
                        f"g3_eval={_g3_now.strftime('%H:%M')} "
                        f"delay={_g3_delay_min:.0f}m price={current_price:,.0f}")
                logger.info(_msg)
                signal_logger.info(_msg)
                if self.db:
                    try:
                        self.db.log_signal_event(
                            event_type="G3_REJECT", stock_code=stock_code,
                            g3_stage="EARLY_OPEN", reject_reason=result['rejection_reason'],
                            first_signal_time=_g3_first_signal_time,
                            g3_eval_time=_g3_now,
                            event_data={"price": int(current_price), "delay_min": round(_g3_delay_min, 1),
                                        "time": _g3_now.isoformat()}
                        )
                    except Exception:
                        pass
                return result

            _is_hp, _bdh_pct = self._is_high_proximity(df)
            if _g3_is_delay0 and _is_hp:
                # Phase2 예외: SMC score ≥ 80 AND reclaim 발생 시 HIGH_PROX 우회
                _p2_score_thr = _g3_cfg.get('phase2_smc_score_exception', 80)
                _smc_score_hint = getattr(self, '_last_smc_score_hint', {}).get(stock_code, 0.0)
                _reclaim_hint = getattr(self, '_last_reclaim_hint', {}).get(stock_code, False)
                _p2_exception = (
                    _in_phase2
                    and _smc_score_hint >= _p2_score_thr
                    and _reclaim_hint
                )
                if _p2_exception:
                    logger.info(
                        f"[G3_P2_EXCEPT] {stock_code} HIGH_PROX 우회 "
                        f"smc_score={_smc_score_hint:.0f}≥{_p2_score_thr} reclaim=True"
                    )
                else:
                    # v1.3: soft_penalty_mode → size 축소 (hard block 대신)
                    _g3_soft_mode = _g3_cfg.get('soft_penalty_mode', False)
                    _g3_soft_mult = float(_g3_cfg.get('soft_penalty_mult', 0.6))
                    _min_bdh_cfg = _g3_cfg.get('min_bdh_pct', 3.0)
                    if _g3_soft_mode:
                        result['position_size_multiplier'] = min(
                            result.get('position_size_multiplier', 1.0), _g3_soft_mult
                        )
                        _msg = (f"[G3_SOFT_PENALTY][HIGH_PROX] {stock_code} "
                                f"bdh={_bdh_pct:.1f}%<{_min_bdh_cfg}% "
                                f"→ size×{_g3_soft_mult} delay={_g3_delay_min:.0f}m")
                        logger.info(_msg)
                        signal_logger.info(_msg)
                    else:
                        result['rejection_level'] = 'G3'
                        result['rejection_reason'] = (f"G3_HIGH_PROX: 당일 고저범위 {_bdh_pct:.1f}% "
                                                      f"< {_min_bdh_cfg}% delay={_g3_delay_min:.0f}m 차단")
                        _msg = (f"[G3_REJECT][HIGH_PROX] {stock_code} "
                                f"bdh={_bdh_pct:.1f}% "
                                f"first_signal={_g3_first_signal_time.strftime('%H:%M')} "
                                f"g3_eval={_g3_now.strftime('%H:%M')} "
                                f"delay={_g3_delay_min:.0f}m")
                        logger.info(_msg)
                        signal_logger.info(_msg)
                        if self.db:
                            try:
                                self.db.log_signal_event(
                                    event_type="G3_REJECT", stock_code=stock_code,
                                    g3_stage="HIGH_PROX", reject_reason=result['rejection_reason'],
                                    first_signal_time=_g3_first_signal_time,
                                    g3_eval_time=_g3_now,
                                    event_data={"price": int(current_price), "bdh_pct": round(_bdh_pct, 2),
                                                "delay_min": round(_g3_delay_min, 1), "time": _g3_now.isoformat()}
                                )
                            except Exception:
                                pass
                        return result

            # Stage A 추가 품질 게이트 (G3 통과 delay=0 잔여 이상 거래 차단)
            # G3가 활성화된 경우에만 실행 (_g3_is_delay0 / _bdh_pct 의존)
            # C2: bdh > max_bdh_pct → 비정상 변동성 (서킷브레이커/상장일) delay=0 차단
            _sa_cfg = self.config.get('late_entry_control', {}).get('stage_a_quality_gate', {})
            if _sa_cfg.get('enabled', False) and _g3_is_delay0:
                _sa_max_bdh = _sa_cfg.get('max_bdh_pct', 15.0)
                if _bdh_pct is not None and float(_bdh_pct) > _sa_max_bdh:
                    result['rejection_level'] = 'STAGE_A'
                    result['rejection_reason'] = (
                        f"STAGE_A_ANOMALY: 당일 고저범위 {_bdh_pct:.1f}% > {_sa_max_bdh}% "
                        f"(비정상 변동성) delay={_g3_delay_min:.0f}m 차단"
                    )
                    _sa_msg = (f"[STAGE_A_REJECT][ANOMALY] {stock_code} "
                               f"bdh={_bdh_pct:.1f}% > {_sa_max_bdh}% "
                               f"first_signal={_g3_first_signal_time.strftime('%H:%M')} "
                               f"g3_eval={_g3_now.strftime('%H:%M')} "
                               f"delay={_g3_delay_min:.0f}m")
                    logger.info(_sa_msg)
                    signal_logger.info(_sa_msg)
                    if self.db:
                        try:
                            self.db.log_signal_event(
                                event_type="STAGE_A_REJECT", stock_code=stock_code,
                                g3_stage="ANOMALY", reject_reason=result['rejection_reason'],
                                first_signal_time=_g3_first_signal_time,
                                g3_eval_time=_g3_now,
                                event_data={"price": int(current_price), "bdh_pct": round(_bdh_pct, 2),
                                            "max_bdh": _sa_max_bdh, "delay_min": round(_g3_delay_min, 1),
                                            "time": _g3_now.isoformat()}
                            )
                        except Exception:
                            pass
                    return result

        # 모든 레벨 통과!
        self.stats['total_accepted'] += 1
        result['allowed'] = True

        # ACCEPT 이벤트를 단일 튜플 (timestamp, price)로 기록.
        # time과 price는 반드시 같은 이벤트 시점이어야 H-003 gap 계산이 유효함.
        _now_ts = datetime.now()
        _entry = (_now_ts.timestamp(), int(current_price))  # 동일 이벤트, 동시 캡처

        # recent_accepts: 항상 최신 accept로 갱신 (get_recent_accepts window 체크용)
        self.recent_accepts[stock_code] = _entry

        # recent_accepts_first: 당일 최초 ACCEPT만 보존 (덮어쓰기 금지 — t0 오염 방지)
        # 정의: candidate_first_time = candidate_first_price 와 같은 이벤트 시각
        #       즉, 당일 이 종목이 처음으로 L0~L6 ACCEPT된 그 단일 순간
        _existing = self.recent_accepts_first.get(stock_code)
        if _existing is None:
            self.recent_accepts_first[stock_code] = _entry
        else:
            # 날짜가 바뀐 경우만 리셋 (자정 경계)
            from datetime import date
            if date.fromtimestamp(_existing[0]) < _now_ts.date():
                self.recent_accepts_first[stock_code] = _entry

        # D1: pre-candidate → candidate 승격 감지 및 로그
        _pre_ts, _pre_p = self.get_pre_candidate_info(stock_code)
        if _pre_ts is not None:
            _gap_min = (_now_ts - _pre_ts).total_seconds() / 60.0
            _promo_msg = (f"[PRE_TO_CAND] 종목={stock_code} "
                          f"pre={_pre_ts.strftime('%H:%M')} "
                          f"cand={_now_ts.strftime('%H:%M')} "
                          f"gap={_gap_min:.0f}m pre_price={_pre_p:,} cand_price={int(current_price):,}")
            logger.info(_promo_msg)
            signal_logger.info(_promo_msg)

        # [SIGNAL_PIPELINE] 통합 타임스탬프 로그 (orchestrator 레벨 — CHOCH_RAW/ENTRY는 SMC 단에서 기록)
        _spipe_pre_ts, _ = self.get_pre_candidate_info(stock_code)
        _spipe_g3_ts = self._g3_eval_times.get(stock_code)
        _spipe_first = _spipe_pre_ts or _now_ts   # first_signal = pre_cand 없으면 현재 ACCEPT 시각
        _spipe_msg = (
            f"[SIGNAL_PIPELINE] {stock_code} "
            f"L3={_spipe_pre_ts.strftime('%H:%M') if _spipe_pre_ts else 'N/A'} "
            f"G3_EVAL={_spipe_g3_ts.strftime('%H:%M') if _spipe_g3_ts else 'N/A'} "
            f"FIRST_SIGNAL={_spipe_first.strftime('%H:%M')} "
            f"ACCEPT={_now_ts.strftime('%H:%M')} "
            f"(CHOCH_RAW/ENTRY=pending)"
        )
        logger.info(_spipe_msg)
        signal_logger.info(_spipe_msg)
        if self.db:
            try:
                self.db.log_signal_event(
                    event_type="SIGNAL_PIPELINE", stock_code=stock_code,
                    first_signal_time=_spipe_first,
                    g3_eval_time=_spipe_g3_ts,
                    event_data={"price": int(current_price), "accept_time": _now_ts.isoformat(),
                                "has_pre_candidate": _spipe_pre_ts is not None}
                )
            except Exception:
                pass

        # Confidence 기반 포지션 크기 결정 (0.6 ~ 1.0)
        position_multiplier = self.confidence_aggregator.calculate_position_multiplier(final_confidence)
        result['position_size_multiplier'] = position_multiplier

        # ─────────────────────────────────────────────────────────────────
        # Layer 3 대안: Momentum Boost — 강한 모멘텀 시 진입 포지션 확대
        # Breakout Add-on(execute_buy 금지) 대신 초기 position_size_multiplier 상향
        # 현재: enabled=false (YAML smc.momentum_boost)
        # ─────────────────────────────────────────────────────────────────
        _mb_cfg = self.config.get('smc.momentum_boost', {})
        if _mb_cfg.get('enabled', False) and df is not None and len(df) >= 5:
            try:
                _mb_cond = _mb_cfg.get('conditions', {})
                _mb_bdh = float(df.get('below_day_high_pct', pd.Series([0])).iloc[-1]
                                if 'below_day_high_pct' in df.columns else 0)
                _mb_rsi = float(df['rsi'].iloc[-1]) if 'rsi' in df.columns else 0
                _mb_vol_r = 0.0
                if 'volume' in df.columns and len(df) >= 6:
                    _avg_v = float(df['volume'].iloc[-6:-1].mean())
                    if _avg_v > 0:
                        _mb_vol_r = float(df['volume'].iloc[-1]) / _avg_v

                _mb_bdh_ok  = _mb_bdh >= _mb_cond.get('min_bdh_pct', 3.0)
                _mb_rsi_ok  = _mb_rsi >= _mb_cond.get('min_rsi', 55)
                _mb_vol_ok  = _mb_vol_r >= _mb_cond.get('min_volume_ratio', 1.5)

                if _mb_bdh_ok and _mb_rsi_ok and _mb_vol_ok:
                    _boost = _mb_cfg.get('boost_mult', 1.2)
                    _cap   = _mb_cfg.get('max_mult', 1.3)
                    _boosted = min(position_multiplier * _boost, _cap)
                    result['position_size_multiplier'] = _boosted
                    _boost_msg = (
                        f"[MOMENTUM_BOOST] {stock_code} "
                        f"bdh={_mb_bdh:.1f}% rsi={_mb_rsi:.0f} vol={_mb_vol_r:.1f}x "
                        f"→ pos_mult {position_multiplier:.2f} → {_boosted:.2f}"
                    )
                    logger.info(_boost_msg)
                    signal_logger.info(_boost_msg)
                    position_multiplier = _boosted
            except Exception:
                pass

        # 🟡 후보 승인 로그 — 오케스트레이터(L0~L6) 통과. 실제 주문 전 단계.
        import os
        msg = f"🟡 CANDIDATE_ACCEPT {stock_code} @{current_price:.0f}원 | PID:{os.getpid()} | conf={final_confidence:.2f} alpha={aggregate_score:+.2f} pos_mult={position_multiplier:.2f}"
        console.print(f"[yellow]{msg}[/yellow]")
        signal_logger.info(msg)

        return result

    def get_recent_accepts(self, window_minutes: int = 35) -> set:
        """최근 window_minutes 내 ACCEPT된 종목 코드 집합 반환."""
        cutoff = datetime.now().timestamp() - window_minutes * 60
        return {s for s, v in self.recent_accepts.items() if (v[0] if isinstance(v, tuple) else v) >= cutoff}

    def get_candidate_info(self, stock_code: str) -> tuple:
        """
        t0 = (candidate_first_time, candidate_first_price) 반환.

        정의:
          candidate_first_time  = 당일 이 종목이 처음으로 L0~L6 ACCEPT된 시각
          candidate_first_price = 그 동일한 시각의 현재가
          → 두 값은 반드시 같은 ACCEPT 이벤트 시점 (동일 튜플에서 추출)

        다회 accept 시: 당일 첫 번째 accept 값만 유지 (덮어쓰기 금지)
        없으면: (None, None) — 주문 없는 swing 매수, 당일 orchestrator 미통과 등
        """
        v = self.recent_accepts_first.get(stock_code)
        if v is None:
            return None, None
        ts, price = v
        return datetime.fromtimestamp(ts), price

    # ── G3 사전 진입 필터 (evaluate_signal 내부 전용) ───────────────────────────

    def _is_early_open(self) -> bool:
        """EARLY_OPEN 조건: 장 초반(hour < block_before_hour) 진입 차단
        v1.2: Phase1(09:30~10:00)은 delay threshold를 3분으로 완화 — block은 동일하게 유지
        """
        g3_cfg = self.config.get('late_entry_control', {}).get('g3_delay0_quality_gate', {})
        if not g3_cfg.get('enabled', False):
            return False
        block_before_hour = g3_cfg.get('block_before_hour', 10)
        return datetime.now().hour < block_before_hour

    def _is_high_proximity(self, df) -> tuple:
        """HIGH_PROX 조건: 당일 고저 범위 < min_bdh_pct% → 진입 여유 없음.
        bdh = (day_high - day_low) / day_low * 100
        Returns: (is_high_prox: bool, bdh_pct: float)
        """
        g3_cfg = self.config.get('late_entry_control', {}).get('g3_delay0_quality_gate', {})
        if not g3_cfg.get('enabled', False):
            return False, None
        min_bdh = g3_cfg.get('min_bdh_pct', 3.0)
        try:
            if df is None or df.empty:
                return False, None
            high_col = 'high' if 'high' in df.columns else ('High' if 'High' in df.columns else None)
            low_col  = 'low'  if 'low'  in df.columns else ('Low'  if 'Low'  in df.columns else None)
            if high_col is None or low_col is None:
                return False, None
            day_high = float(df[high_col].max())
            day_low  = float(df[low_col].min())
            if day_low <= 0:
                return False, None
            bdh_pct = (day_high - day_low) / day_low * 100
            return bdh_pct < min_bdh, bdh_pct
        except Exception:
            return False, None

    # ── D1 pre-candidate 공개 API ───────────────────────────────────────────────

    def _register_pre_candidate(self, stock_code: str, price: float) -> bool:
        """D1: L3 통과 시 pre-candidate 등록 (당일 최초값만, 덮어쓰기 금지)"""
        from datetime import datetime as _dt, date as _date
        now = _dt.now()
        today = now.date()
        existing = self.pre_candidates.get(stock_code)
        if existing is not None and existing[2] == today:
            return False  # 당일 이미 등록됨

        self.pre_candidates[stock_code] = (now, int(price), today)
        _msg = (f"[PRE_CANDIDATE] 종목={stock_code} time={now.strftime('%H:%M')} "
                f"price={price:,.0f} stage=L3")
        logger.info(_msg)
        signal_logger.info(_msg)
        return True

    def get_pre_candidate_info(self, stock_code: str):
        """D1: pre-candidate 등록 정보 반환 — (datetime, price) 또는 (None, None)"""
        from datetime import date as _date
        v = self.pre_candidates.get(stock_code)
        if v is None:
            return None, None
        ts, price, dt = v
        if dt != _date.today():
            return None, None  # 당일이 아니면 만료
        return ts, price

    def get_g3_eval_time(self, stock_code: str):
        """G3 평가 시각 반환 — evaluate_signal 내 G3 블록이 실행된 시각 (없으면 None)"""
        return self._g3_eval_times.get(stock_code)

    def get_first_signal_time(self, stock_code: str):
        """first_signal_time 반환: pre_candidate_first_time 또는 None (pipeline 분석용)"""
        ts, _ = self.get_pre_candidate_info(stock_code)
        return ts

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
            self.current_weights = self.weight_adjuster.adjust_weights(regime, rv_percentile)

            # 변경 사항 출력
            self.weight_adjuster.print_weight_comparison(regime, self.current_weights)

            # Alpha Engine 재생성
            self._create_alpha_engine()

            weights_changed = True

            console.print("[green]✅ Alpha Engine이 새로운 가중치로 업데이트되었습니다.[/green]")
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
