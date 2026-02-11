"""
SMC 신호 생성

- Order Block 탐지
- SMCStrategy 통합 클래스 (check_entry_signal, check_exit_signal)
"""

from dataclasses import dataclass
from typing import Tuple, Dict, Optional, List
import pandas as pd
from rich.console import Console

from .smc_structure import (
    SMCStructureAnalyzer,
    MarketStructure,
    StructureBreakEvent,
    StructureBreak,
    MarketTrend
)
from .smc_utils import (
    find_swing_points,
    detect_liquidity_sweep,
    SwingPoint,
    LiquiditySweep
)

console = Console()


@dataclass
class OrderBlock:
    """오더블록 데이터"""
    index: int
    high: float
    low: float
    open_price: float
    close_price: float
    type: str              # 'bullish' | 'bearish'
    timestamp: Optional[pd.Timestamp] = None
    mitigated: bool = False  # 이미 터치되었는지

    def __repr__(self):
        return f"OrderBlock({self.type}@{self.low:.0f}-{self.high:.0f})"


@dataclass
class SMCSignalResult:
    """SMC 시그널 결과"""
    signal: bool
    direction: str         # 'long' | 'short' | 'none'
    reason: str
    confidence: float      # 0.0 ~ 1.0
    details: Dict


class CHoCHGrade:
    """
    CHoCH 등급 시스템 (2026-01-23 추가)

    A급: 최고 품질 - 풀 비중 진입
    B급: 중간 품질 - 50% 비중
    C급: 저품질 - 진입 금지
    """
    A = 'A'  # HTF 구조 일치 + Sweep + OB 명확 + 스퀴즈 수축
    B = 'B'  # Sweep 없음 또는 OB 약함
    C = 'C'  # 횡보 내 CHoCH, 변동성 미확장

    # 등급별 비중 배율
    WEIGHT_MULTIPLIER = {
        'A': 1.0,   # 풀 비중
        'B': 0.5,   # 50% 비중
        'C': 0.0    # 진입 금지
    }

    # 등급별 최소 신뢰도
    MIN_CONFIDENCE = {
        'A': 0.80,
        'B': 0.60,
        'C': 0.40
    }


class SMCStrategy:
    """
    SMC (Smart Money Concepts) 통합 전략

    진입 조건:
    - LONG: CHoCH(상승 전환) + liquidity_sweep_low (저점 스윕 후)
    - SHORT: CHoCH(하락 전환) + liquidity_sweep_high (고점 스윕 후)

    인터페이스:
    - check_entry_signal() -> Tuple[bool, str, Dict]
    - check_exit_signal() -> Tuple[bool, str, Dict]
    """

    def __init__(
        self,
        swing_lookback: int = 5,
        min_swing_size_pct: float = 0.3,
        sweep_threshold_pct: float = 0.1,
        sweep_lookback: int = 20,
        ob_lookback: int = 10,
        require_liquidity_sweep: bool = True,
        long_only: bool = True,
        # 🔧 2026-01-23: CHoCH 등급 필터 추가
        min_choch_grade: str = 'B',        # 최소 허용 등급 (A, B, C)
        require_squeeze_on: bool = False,   # Squeeze ON 필수 여부
        require_vwap_above: bool = False,   # VWAP 위 필수 여부
        grade_b_weight: float = 0.5,        # B급 CHoCH 비중 배율
        # 🔧 2026-01-29: MTF Bias 필터 (30분봉 추세 체크)
        mtf_bias_enabled: bool = True,      # 30분봉 추세 필터 활성화
        mtf_timeframe: str = '30min',       # MTF 타임프레임
        # 🔧 2026-02-06: 진입 프리필터 (품질 개선)
        prefilter_enabled: bool = True,     # 프리필터 활성화
        prefilter_min_conditions: int = 2,  # 최소 충족 조건 수
        prefilter_require_htf_trend: bool = True,
        prefilter_require_liquidity_sweep: bool = True,
        prefilter_require_reclaim: bool = True,
        reclaim_lookback: int = 5,          # CHoCH 후 몇 캔들 내 되돌림 확인
        reclaim_tolerance_pct: float = 0.3  # broken level 대비 허용 범위 (%)
    ):
        """
        Args:
            swing_lookback: 스윙 탐지 lookback
            min_swing_size_pct: 최소 스윙 크기 (%)
            sweep_threshold_pct: 유동성 스윕 최소 돌파율
            sweep_lookback: 유동성 스윕 탐색 범위
            ob_lookback: 오더블록 탐색 범위
            require_liquidity_sweep: 진입 시 유동성 스윕 필수 여부
            long_only: 롱만 진입 (숏 신호 무시)
            min_choch_grade: 최소 허용 CHoCH 등급 (A/B/C)
            require_squeeze_on: Squeeze ON 상태 필수 여부
            require_vwap_above: VWAP 위 필수 여부
            grade_b_weight: B급 CHoCH 비중 배율
        """
        self.structure_analyzer = SMCStructureAnalyzer(
            swing_lookback=swing_lookback,
            min_swing_size_pct=min_swing_size_pct
        )
        self.sweep_threshold_pct = sweep_threshold_pct
        self.sweep_lookback = sweep_lookback
        self.ob_lookback = ob_lookback
        self.require_liquidity_sweep = require_liquidity_sweep
        self.long_only = long_only

        # 🔧 2026-01-23: CHoCH 등급 필터
        self.min_choch_grade = min_choch_grade
        self.require_squeeze_on = require_squeeze_on
        self.require_vwap_above = require_vwap_above
        self.grade_b_weight = grade_b_weight

        # 🔧 2026-01-29: MTF Bias 필터
        self.mtf_bias_enabled = mtf_bias_enabled
        self.mtf_timeframe = mtf_timeframe

        # 🔧 2026-02-06: 진입 프리필터
        self.prefilter_enabled = prefilter_enabled
        self.prefilter_min_conditions = prefilter_min_conditions
        self.prefilter_require_htf_trend = prefilter_require_htf_trend
        self.prefilter_require_liquidity_sweep = prefilter_require_liquidity_sweep
        self.prefilter_require_reclaim = prefilter_require_reclaim
        self.reclaim_lookback = reclaim_lookback
        self.reclaim_tolerance_pct = reclaim_tolerance_pct

        # 통계
        self.stats = {
            'total_checks': 0,
            'choch_detected': 0,
            'bos_detected': 0,
            'liquidity_sweeps': 0,
            'entry_signals': 0,
            'long_signals': 0,
            'short_signals': 0,
            # 🔧 2026-01-23: 등급별 통계
            'grade_a_signals': 0,
            'grade_b_signals': 0,
            'grade_c_rejected': 0,
            # 🔧 2026-02-06: 프리필터 통계
            'prefilter_passed': 0,
            'prefilter_rejected': 0
        }

    def evaluate_choch_grade(
        self,
        df: pd.DataFrame,
        choch: StructureBreakEvent,
        structure: MarketStructure,
        liquidity_sweep: Optional[LiquiditySweep],
        order_block: Optional[OrderBlock]
    ) -> Tuple[str, float, Dict]:
        """
        CHoCH 등급 평가 (2026-01-23 추가)

        등급 기준:
        - A급: HTF 구조 일치 + Sweep + OB 명확 + 변동성 수축/확장 중
        - B급: 일부 조건 미충족 (Sweep 없음 OR OB 약함)
        - C급: 횡보 내 CHoCH OR 변동성 미확장

        Args:
            df: OHLCV DataFrame
            choch: CHoCH 이벤트
            structure: 시장 구조
            liquidity_sweep: 유동성 스윕 (있으면)
            order_block: 오더블록 (있으면)

        Returns:
            (grade, score, grade_details)
        """
        score = 0.0
        grade_details = {
            'htf_aligned': False,
            'has_sweep': False,
            'has_strong_ob': False,
            'volatility_contracting': False,
            'squeeze_on': False,
            'vwap_position': 'unknown',
            'factors': []
        }

        # 1. HTF 구조 일치 체크 (+25점)
        # CHoCH 방향과 상위 구조가 일치하는지
        if choch.direction == 'bullish':
            # 상승 전환: 이전 하락 추세 → 상승 전환
            if structure.trend in [MarketTrend.BEARISH, MarketTrend.RANGING]:
                score += 25
                grade_details['htf_aligned'] = True
                grade_details['factors'].append('HTF구조일치(+25)')
        elif choch.direction == 'bearish':
            if structure.trend in [MarketTrend.BULLISH, MarketTrend.RANGING]:
                score += 25
                grade_details['htf_aligned'] = True
                grade_details['factors'].append('HTF구조일치(+25)')

        # 2. Liquidity Sweep 체크 (+25점)
        if liquidity_sweep is not None:
            # Sweep 방향이 CHoCH 방향과 일치해야 함
            if liquidity_sweep.direction == choch.direction:
                score += 25
                grade_details['has_sweep'] = True
                grade_details['factors'].append('Sweep확인(+25)')
            else:
                score += 10  # 방향 불일치 시 부분 점수
                grade_details['factors'].append('Sweep방향불일치(+10)')

        # 3. Order Block 품질 체크 (+20점)
        if order_block is not None:
            # OB 크기 평가 (고가-저가 범위)
            ob_range = order_block.high - order_block.low
            avg_price = (order_block.high + order_block.low) / 2
            ob_range_pct = (ob_range / avg_price) * 100 if avg_price > 0 else 0

            if ob_range_pct >= 0.5:  # 충분히 큰 OB
                score += 20
                grade_details['has_strong_ob'] = True
                grade_details['factors'].append(f'강한OB({ob_range_pct:.2f}%, +20)')
            elif ob_range_pct >= 0.2:  # 중간 OB
                score += 10
                grade_details['factors'].append(f'약한OB({ob_range_pct:.2f}%, +10)')

        # 4. 변동성 수축/확장 체크 (+15점) - Squeeze 상태
        try:
            # Squeeze 상태 확인
            if 'sqz_on' in df.columns:
                sqz_on = df['sqz_on'].iloc[-1] if len(df) > 0 else False
                if sqz_on:
                    score += 15
                    grade_details['squeeze_on'] = True
                    grade_details['volatility_contracting'] = True
                    grade_details['factors'].append('Squeeze수축(+15)')
            else:
                # BB와 KC 수동 계산
                if all(col in df.columns for col in ['close', 'high', 'low']):
                    recent = df.tail(20)
                    if len(recent) >= 20:
                        bb_std = recent['close'].std()
                        avg_range = (recent['high'] - recent['low']).mean()
                        if bb_std < avg_range * 0.7:  # 변동성 수축
                            score += 15
                            grade_details['volatility_contracting'] = True
                            grade_details['factors'].append('변동성수축(+15)')
        except Exception:
            pass

        # 5. VWAP 위치 체크 (+15점)
        try:
            if 'vwap' in df.columns and 'close' in df.columns:
                vwap = df['vwap'].iloc[-1]
                close = df['close'].iloc[-1]
                if close > vwap:
                    score += 15
                    grade_details['vwap_position'] = 'above'
                    grade_details['factors'].append('VWAP위(+15)')
                else:
                    grade_details['vwap_position'] = 'below'
                    grade_details['factors'].append('VWAP아래(0)')
        except Exception:
            pass

        # 총점 기반 등급 결정 (100점 만점)
        # 🔧 2026-02-10 F4: A급 기준 상향 (70→80)
        # 이전: HTF(25)+Sweep(25)+OB(20)=70 → 자동 A급 (사실상 필터 무의미)
        # 이후: 80+ 필요 → Squeeze(15) 또는 VWAP(15) 추가 확인 필수
        # A급: 80점 이상
        # B급: 50-79점 (B급 = 50% 비중)
        # C급: 50점 미만 (진입 금지)

        if score >= 80:
            grade = CHoCHGrade.A
        elif score >= 50:
            grade = CHoCHGrade.B
        else:
            grade = CHoCHGrade.C

        grade_details['score'] = score
        grade_details['grade'] = grade

        return grade, score, grade_details

    def detect_reclaim_candle(
        self,
        df: pd.DataFrame,
        choch: StructureBreakEvent,
        lookback: int = None
    ) -> bool:
        """
        🔧 2026-02-06: 되돌림 캔들 확인

        CHoCH 발생 후 lookback 캔들 내에서 broken level 부근으로
        되돌린 캔들이 존재하는지 확인합니다.

        Args:
            df: OHLCV DataFrame (소문자 컬럼)
            choch: CHoCH 이벤트
            lookback: 확인할 캔들 수 (None이면 self.reclaim_lookback 사용)

        Returns:
            True: 되돌림 캔들 존재
        """
        if lookback is None:
            lookback = self.reclaim_lookback

        broken_level = choch.broken_level
        tolerance = broken_level * (self.reclaim_tolerance_pct / 100)
        choch_idx = choch.index

        # CHoCH 이후 캔들 범위
        start_idx = choch_idx + 1
        end_idx = min(choch_idx + lookback + 1, len(df))

        if start_idx >= len(df):
            return False

        for i in range(start_idx, end_idx):
            candle_close = df['close'].iloc[i]

            if choch.direction == 'bullish':
                # 롱: close가 broken_level의 ±tolerance 이내로 되돌림
                if abs(candle_close - broken_level) <= tolerance:
                    return True
                # 또는 broken_level 아래로 갔다가 다시 위로 올라온 경우
                if df['low'].iloc[i] <= broken_level and candle_close >= broken_level:
                    return True
            elif choch.direction == 'bearish':
                # 숏: close가 broken_level의 ±tolerance 이내로 되돌림
                if abs(candle_close - broken_level) <= tolerance:
                    return True
                if df['high'].iloc[i] >= broken_level and candle_close <= broken_level:
                    return True

        return False

    def check_entry_prefilter(
        self,
        df: pd.DataFrame,
        df_htf: pd.DataFrame,
        choch: StructureBreakEvent,
        liquidity_sweep,
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        🔧 2026-02-06: SMC 진입 프리필터

        CHoCH 감지 후, 등급 평가 전에 3가지 조건 중 min_conditions 이상 충족 필수:
        1. HTF 추세 생존 (15m~1H에서 HH/HL 롱 or LH/LL 숏 패턴)
        2. 유동성 청산 확인 (liquidity sweep 존재)
        3. 되돌림 캔들 확인 (broken level로 되돌림)

        Args:
            df: OHLCV DataFrame (소문자 컬럼)
            df_htf: 상위 타임프레임 DataFrame (30분봉)
            choch: CHoCH 이벤트
            liquidity_sweep: 유동성 스윕 (있으면)
            debug: 디버그 로그 출력

        Returns:
            (passed, reason, details)
        """
        details = {
            'htf_trend_alive': False,
            'liquidity_swept': False,
            'reclaim_detected': False,
            'volume_confirmed': False,  # 🔧 2026-02-10 F3
            'conditions_met': 0,
            'min_required': self.prefilter_min_conditions
        }

        conditions_met = 0

        # 조건 1: HTF 추세 생존 체크
        # 🔧 2026-02-10 F2: 횡보/에러/데이터부족 시 무조건 통과 제거
        # CHoCH 방향과 HTF 추세가 명확히 일치할 때만 통과
        if self.prefilter_require_htf_trend:
            try:
                if df_htf is not None and len(df_htf) >= 20:
                    mtf_direction = 'long' if choch.direction == 'bullish' else 'short'
                    mtf_allowed, mtf_reason, mtf_details = self.check_mtf_bias(df_htf, mtf_direction)

                    is_uptrend = mtf_details.get('is_uptrend', False)
                    is_downtrend = mtf_details.get('is_downtrend', False)

                    if choch.direction == 'bullish' and is_uptrend:
                        details['htf_trend_alive'] = True
                        conditions_met += 1
                    elif choch.direction == 'bearish' and is_downtrend:
                        details['htf_trend_alive'] = True
                        conditions_met += 1
                    # 횡보/중립 → 통과 안 함 (이전: 무조건 통과)
                # 데이터 부족 → 통과 안 함 (이전: 무조건 통과)
            except Exception:
                pass  # 에러 → 통과 안 함 (이전: 무조건 통과)

        # 조건 2: 유동성 청산 확인
        if self.prefilter_require_liquidity_sweep:
            if liquidity_sweep is not None:
                details['liquidity_swept'] = True
                conditions_met += 1

        # 조건 3: 되돌림 캔들 확인
        if self.prefilter_require_reclaim:
            reclaim = self.detect_reclaim_candle(df, choch)
            if reclaim:
                details['reclaim_detected'] = True
                conditions_met += 1

        # 🔧 2026-02-10 F3: 거래량 확인 (현재 봉 volume > 20봉 평균)
        # 거래량 없는 가짜 CHoCH 제거
        try:
            if 'volume' in df.columns and len(df) >= 20:
                current_vol = df['volume'].iloc[-1]
                avg_vol_20 = df['volume'].tail(20).mean()
                if avg_vol_20 > 0 and current_vol >= avg_vol_20:
                    details['volume_confirmed'] = True
                    conditions_met += 1
        except Exception:
            pass

        details['conditions_met'] = conditions_met

        # 최소 조건 충족 확인
        if conditions_met >= self.prefilter_min_conditions:
            self.stats['prefilter_passed'] += 1
            reason = (
                f"프리필터 통과 ({conditions_met}/{self.prefilter_min_conditions}): "
                f"HTF={'✅' if details['htf_trend_alive'] else '❌'} "
                f"Sweep={'✅' if details['liquidity_swept'] else '❌'} "
                f"Reclaim={'✅' if details['reclaim_detected'] else '❌'} "
                f"Vol={'✅' if details['volume_confirmed'] else '❌'}"
            )
            if debug:
                console.print(f"[green]  ✅ {reason}[/green]")
            return True, reason, details
        else:
            self.stats['prefilter_rejected'] += 1
            reason = (
                f"프리필터 차단 ({conditions_met}/{self.prefilter_min_conditions}): "
                f"HTF={'✅' if details['htf_trend_alive'] else '❌'} "
                f"Sweep={'✅' if details['liquidity_swept'] else '❌'} "
                f"Reclaim={'✅' if details['reclaim_detected'] else '❌'} "
                f"Vol={'✅' if details['volume_confirmed'] else '❌'}"
            )
            if debug:
                console.print(f"[yellow]  ❌ {reason}[/yellow]")
            return False, reason, details

    def check_mtf_bias(
        self,
        df_htf: pd.DataFrame,
        direction: str = 'long'
    ) -> Tuple[bool, str, Dict]:
        """
        🔧 2026-01-29: MTF (Multi-Timeframe) Bias 필터

        30분봉 기준으로 상위 타임프레임 추세를 확인합니다.
        - LONG 진입 시: 30분봉이 하락 추세면 진입 차단
        - SHORT 진입 시: 30분봉이 상승 추세면 진입 차단

        핵심 로직:
        - Higher High / Higher Low 유지 중 → 상승 추세 → LONG 허용
        - Lower High / Lower Low 유지 중 → 하락 추세 → LONG 차단
        - 최근 구조 변화(CHoCH) 후 횡보 → 중립 → LONG 허용

        Args:
            df_htf: 상위 타임프레임 OHLCV (30분봉)
            direction: 진입 방향 ('long' or 'short')

        Returns:
            (allowed, reason, details)
        """
        details = {}

        if df_htf is None or len(df_htf) < 20:
            # 데이터 부족 시 허용 (안전망)
            return True, "MTF: 데이터 부족 (허용)", details

        try:
            # 컬럼 소문자
            df = df_htf.copy()
            df.columns = [c.lower() for c in df.columns]

            # 30분봉 구조 분석
            htf_structure = self.structure_analyzer.analyze_structure(df)
            htf_trend = htf_structure.trend.value

            details['htf_trend'] = htf_trend
            details['htf_timeframe'] = self.mtf_timeframe

            # 최근 스윙 포인트 분석
            recent_highs = []
            recent_lows = []

            for sp in htf_structure.swing_points[-6:]:  # 최근 6개 스윙
                if sp.is_high:
                    recent_highs.append(sp.price)
                else:
                    recent_lows.append(sp.price)

            # 추세 판단 (더 정교한 로직)
            is_downtrend = False
            is_uptrend = False

            if len(recent_highs) >= 2 and len(recent_lows) >= 2:
                # Lower High & Lower Low = 하락 추세
                lh_pattern = recent_highs[-1] < recent_highs[-2]
                ll_pattern = recent_lows[-1] < recent_lows[-2]

                # Higher High & Higher Low = 상승 추세
                hh_pattern = recent_highs[-1] > recent_highs[-2]
                hl_pattern = recent_lows[-1] > recent_lows[-2]

                is_downtrend = lh_pattern and ll_pattern
                is_uptrend = hh_pattern and hl_pattern

                details['lh_pattern'] = lh_pattern
                details['ll_pattern'] = ll_pattern
                details['hh_pattern'] = hh_pattern
                details['hl_pattern'] = hl_pattern

            details['is_downtrend'] = is_downtrend
            details['is_uptrend'] = is_uptrend

            # LONG 진입 판단
            if direction == 'long':
                if is_downtrend:
                    # 🚫 하락 추세 속 LONG 진입 차단
                    console.print(
                        f"[red]🚫 MTF Bias 차단: 30분봉 하락 추세 "
                        f"(LH+LL) → LONG 진입 금지[/red]"
                    )
                    return False, "MTF: 30분봉 하락 추세 (LONG 차단)", details

                elif is_uptrend or htf_trend in ['bullish', 'neutral']:
                    # ✅ 상승 추세 또는 중립 → LONG 허용
                    console.print(
                        f"[green]✅ MTF Bias 통과: 30분봉 {htf_trend} "
                        f"→ LONG 허용[/green]"
                    )
                    return True, f"MTF: 30분봉 {htf_trend} (LONG 허용)", details

                else:
                    # 판단 불가 시 허용 (보수적)
                    return True, f"MTF: 30분봉 {htf_trend} (판단 유보, 허용)", details

            # SHORT 진입 판단 (현재 long_only=true이므로 거의 사용 안 됨)
            elif direction == 'short':
                if is_uptrend:
                    return False, "MTF: 30분봉 상승 추세 (SHORT 차단)", details
                return True, f"MTF: 30분봉 {htf_trend} (SHORT 허용)", details

        except Exception as e:
            console.print(f"[dim]⚠️ MTF Bias 체크 오류: {e}[/dim]")
            return True, f"MTF: 체크 오류 ({e})", details

        return True, "MTF: 기본 허용", details

    def find_order_block(
        self,
        df: pd.DataFrame,
        break_event: StructureBreakEvent
    ) -> Optional[OrderBlock]:
        """
        오더블록 탐지 (BOS/CHoCH 직전 마지막 반대 캔들)

        상승 BOS/CHoCH -> 직전 하락 캔들이 Bullish OB
        하락 BOS/CHoCH -> 직전 상승 캔들이 Bearish OB

        Args:
            df: OHLCV DataFrame
            break_event: BOS/CHoCH 이벤트

        Returns:
            OrderBlock 또는 None
        """
        if break_event is None or df is None:
            return None

        # 컬럼 소문자
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        break_idx = break_event.index
        search_start = max(0, break_idx - self.ob_lookback)

        # 상승 돌파 -> 직전 음봉 찾기 (Bullish OB)
        if break_event.direction == 'bullish':
            for i in range(break_idx - 1, search_start - 1, -1):
                candle = df.iloc[i]
                # 음봉 (close < open)
                if candle['close'] < candle['open']:
                    timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                    return OrderBlock(
                        index=i,
                        high=candle['high'],
                        low=candle['low'],
                        open_price=candle['open'],
                        close_price=candle['close'],
                        type='bullish',
                        timestamp=timestamp
                    )

        # 하락 돌파 -> 직전 양봉 찾기 (Bearish OB)
        elif break_event.direction == 'bearish':
            for i in range(break_idx - 1, search_start - 1, -1):
                candle = df.iloc[i]
                # 양봉 (close > open)
                if candle['close'] > candle['open']:
                    timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                    return OrderBlock(
                        index=i,
                        high=candle['high'],
                        low=candle['low'],
                        open_price=candle['open'],
                        close_price=candle['close'],
                        type='bearish',
                        timestamp=timestamp
                    )

        return None

    def check_entry_signal(
        self,
        df: pd.DataFrame,
        debug: bool = True,
        df_htf: pd.DataFrame = None  # 🔧 2026-01-29: MTF Bias용 30분봉
    ) -> Tuple[bool, str, Dict]:
        """
        SMC 진입 신호 체크

        Args:
            df: OHLCV DataFrame (5분봉 권장)
            debug: 디버그 로그 출력
            df_htf: 상위 타임프레임 DataFrame (30분봉, MTF Bias 필터용)

        Returns:
            (signal, reason, details)

        진입 조건:
        1. CHoCH 발생 (추세 전환)
        2. Liquidity Sweep 발생 (선택적)
        3. Order Block 영역 근처 (추가 확인)
        4. MTF Bias 통과 (30분봉 추세 체크) - 2026-01-29 추가
        """
        self.stats['total_checks'] += 1
        details: Dict = {}

        # 데이터 검증
        if df is None or len(df) < 50:
            return False, "SMC: 데이터 부족", details

        # 컬럼 소문자
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        # 1. 시장 구조 분석
        structure = self.structure_analyzer.analyze_structure(df)
        details['structure'] = {
            'trend': structure.trend.value,
            'swing_count': len(structure.swing_points),
            'last_hh': structure.last_hh.price if structure.last_hh else None,
            'last_hl': structure.last_hl.price if structure.last_hl else None,
            'last_lh': structure.last_lh.price if structure.last_lh else None,
            'last_ll': structure.last_ll.price if structure.last_ll else None
        }

        if debug:
            console.print(f"[cyan]  SMC 구조: {structure.trend.value}, 스윙 {len(structure.swing_points)}개[/cyan]")

        # 2. CHoCH 탐지 (핵심!)
        choch = self.structure_analyzer.detect_choch(df, structure)

        if choch is None:
            # BOS 체크 (추세 지속, 참고용)
            bos = self.structure_analyzer.detect_bos(df, structure)
            if bos:
                self.stats['bos_detected'] += 1
                details['bos'] = {
                    'direction': bos.direction,
                    'broken_level': bos.broken_level
                }
                return False, f"SMC: BOS({bos.direction}) 발생 - 추세 지속, CHoCH 대기", details

            return False, "SMC: 구조 변화 없음", details

        self.stats['choch_detected'] += 1
        details['choch'] = {
            'type': choch.type.value,
            'direction': choch.direction,
            'broken_level': choch.broken_level,
            'price': choch.price
        }

        if debug:
            console.print(f"[green]  CHoCH 발생: {choch.direction} @{choch.price:.0f}[/green]")

        # 3. Liquidity Sweep 체크
        swing_points = structure.swing_points
        liquidity_sweep = detect_liquidity_sweep(
            df, swing_points,
            lookback=self.sweep_lookback,
            sweep_threshold_pct=self.sweep_threshold_pct
        )

        if liquidity_sweep:
            self.stats['liquidity_sweeps'] += 1
            details['liquidity_sweep'] = {
                'swept_level': liquidity_sweep.swept_level,
                'direction': liquidity_sweep.direction,
                'sweep_high': liquidity_sweep.sweep_high,
                'sweep_low': liquidity_sweep.sweep_low
            }
            if debug:
                console.print(f"[green]  Liquidity Sweep: {liquidity_sweep.direction} @{liquidity_sweep.swept_level:.0f}[/green]")

        # 🔧 2026-02-06: 진입 프리필터 (등급 평가 전)
        if self.prefilter_enabled:
            pf_passed, pf_reason, pf_details = self.check_entry_prefilter(
                df=df,
                df_htf=df_htf,
                choch=choch,
                liquidity_sweep=liquidity_sweep,
                debug=debug
            )
            details['prefilter'] = pf_details
            if not pf_passed:
                return False, f"SMC: CHoCH 발생, {pf_reason}", details

        # 4. Order Block 미리 확인 (등급 평가용)
        ob = self.find_order_block(df, choch)

        # 🔧 2026-01-23: CHoCH 등급 평가
        choch_grade, grade_score, grade_details = self.evaluate_choch_grade(
            df=df,
            choch=choch,
            structure=structure,
            liquidity_sweep=liquidity_sweep,
            order_block=ob
        )

        details['choch_grade'] = {
            'grade': choch_grade,
            'score': grade_score,
            'factors': grade_details.get('factors', []),
            'squeeze_on': grade_details.get('squeeze_on', False),
            'vwap_position': grade_details.get('vwap_position', 'unknown')
        }

        if debug:
            grade_emoji = {'A': '🅰️', 'B': '🅱️', 'C': '🇨'}
            console.print(
                f"[cyan]  CHoCH 등급: {grade_emoji.get(choch_grade, '❓')} {choch_grade}급 "
                f"(점수: {grade_score:.0f}/100) - {', '.join(grade_details.get('factors', []))}[/cyan]"
            )

        # 🔧 C급 CHoCH 차단 (횡보 내 CHoCH, 저품질)
        grade_order = {'A': 1, 'B': 2, 'C': 3}
        min_grade_order = grade_order.get(self.min_choch_grade, 2)
        current_grade_order = grade_order.get(choch_grade, 3)

        if current_grade_order > min_grade_order:
            self.stats['grade_c_rejected'] += 1
            if debug:
                console.print(f"[yellow]  ❌ CHoCH {choch_grade}급 차단 (최소 {self.min_choch_grade}급 필요)[/yellow]")
            return False, f"SMC: CHoCH {choch_grade}급 (최소 {self.min_choch_grade}급 필요, 점수: {grade_score:.0f})", details

        # 🔧 Squeeze ON 필수 옵션 체크
        if self.require_squeeze_on and not grade_details.get('squeeze_on', False):
            if debug:
                console.print(f"[yellow]  ❌ Squeeze OFF 상태 - 진입 차단[/yellow]")
            return False, f"SMC: CHoCH {choch_grade}급 발생, Squeeze OFF (수축 대기)", details

        # 🔧 VWAP 위 필수 옵션 체크
        if self.require_vwap_above and grade_details.get('vwap_position') != 'above':
            if debug:
                console.print(f"[yellow]  ❌ VWAP 아래 - 진입 차단[/yellow]")
            return False, f"SMC: CHoCH {choch_grade}급 발생, VWAP 아래 (돌파 대기)", details

        # 🔧 2026-01-29: MTF Bias 필터 (30분봉 추세 체크) - L0 필터
        if self.mtf_bias_enabled and df_htf is not None:
            # CHoCH 방향에 따른 MTF 체크
            mtf_direction = 'long' if choch.direction == 'bullish' else 'short'
            mtf_allowed, mtf_reason, mtf_details = self.check_mtf_bias(df_htf, mtf_direction)

            details['mtf_bias'] = {
                'allowed': mtf_allowed,
                'reason': mtf_reason,
                'htf_trend': mtf_details.get('htf_trend', 'unknown'),
                'is_downtrend': mtf_details.get('is_downtrend', False),
                'is_uptrend': mtf_details.get('is_uptrend', False)
            }

            if not mtf_allowed:
                if debug:
                    console.print(f"[yellow]  ❌ MTF Bias 차단: {mtf_reason}[/yellow]")
                return False, f"SMC: CHoCH {choch_grade}급 발생, {mtf_reason}", details
            else:
                if debug:
                    console.print(f"[green]  ✅ MTF Bias 통과: {mtf_reason}[/green]")

        # 5. 진입 조건 평가
        signal = False
        reason = ""
        confidence = 0.0
        direction = 'none'

        # LONG 조건
        if choch.direction == 'bullish':
            direction = 'long'

            if self.require_liquidity_sweep:
                if liquidity_sweep and liquidity_sweep.direction == 'bullish':
                    signal = True
                    confidence = 0.85
                    reason = f"LONG: CHoCH[{choch_grade}급](상승전환) + Liquidity Sweep Low"
                else:
                    reason = f"CHoCH[{choch_grade}급] 상승 발생, Liquidity Sweep 대기"
            else:
                signal = True
                # 등급별 신뢰도 조정
                base_confidence = 0.70 if liquidity_sweep else 0.60
                if choch_grade == CHoCHGrade.A:
                    confidence = min(base_confidence + 0.15, 0.95)
                elif choch_grade == CHoCHGrade.B:
                    confidence = base_confidence
                reason = f"LONG: CHoCH[{choch_grade}급](상승전환)"
                if liquidity_sweep:
                    reason += " + Liquidity Sweep"

        # SHORT 조건
        elif choch.direction == 'bearish':
            direction = 'short'

            # 롱온리 모드에서는 숏 무시
            if self.long_only:
                return False, f"SMC: CHoCH[{choch_grade}급](하락전환) - 롱온리 모드로 숏 무시", details

            if self.require_liquidity_sweep:
                if liquidity_sweep and liquidity_sweep.direction == 'bearish':
                    signal = True
                    confidence = 0.85
                    reason = f"SHORT: CHoCH[{choch_grade}급](하락전환) + Liquidity Sweep High"
                else:
                    reason = f"CHoCH[{choch_grade}급] 하락 발생, Liquidity Sweep 대기"
            else:
                signal = True
                base_confidence = 0.70 if liquidity_sweep else 0.60
                if choch_grade == CHoCHGrade.A:
                    confidence = min(base_confidence + 0.15, 0.95)
                elif choch_grade == CHoCHGrade.B:
                    confidence = base_confidence
                reason = f"SHORT: CHoCH[{choch_grade}급](하락전환)"
                if liquidity_sweep:
                    reason += " + Liquidity Sweep"

        # 6. Order Block 정보 추가 (이미 위에서 확인됨)
        if signal and ob:
            details['order_block'] = {
                'type': ob.type,
                'high': ob.high,
                'low': ob.low,
                'index': ob.index
            }
            confidence = min(confidence + 0.05, 1.0)
            reason += f" + OB({ob.type})"
            if debug:
                console.print(f"[green]  Order Block: {ob.type} @{ob.low:.0f}-{ob.high:.0f}[/green]")

        # 7. 현재가 정보 및 등급별 비중 배율
        current_price = df['close'].iloc[-2] if len(df) >= 2 else 0
        details['current_price'] = current_price
        details['confidence'] = confidence
        details['direction'] = direction

        # 🔧 2026-02-06: 구조 기반 손절가 계산
        if signal:
            structure_stop = self._calculate_structure_stop(
                df=df,
                structure=structure,
                choch=choch,
                current_price=current_price,
                debug=debug
            )
            if structure_stop is not None:
                details['structure_stop_price'] = structure_stop

        # 🔧 2026-01-23: 등급별 비중 배율 추가
        if choch_grade == CHoCHGrade.A:
            weight_multiplier = 1.0
        elif choch_grade == CHoCHGrade.B:
            weight_multiplier = self.grade_b_weight  # 기본 0.5
        else:
            weight_multiplier = 0.0  # C급은 이미 위에서 차단됨

        details['weight_multiplier'] = weight_multiplier

        # 통계 업데이트
        if signal:
            self.stats['entry_signals'] += 1
            if direction == 'long':
                self.stats['long_signals'] += 1
            else:
                self.stats['short_signals'] += 1

            # 🔧 2026-01-23: 등급별 통계
            if choch_grade == CHoCHGrade.A:
                self.stats['grade_a_signals'] += 1
            elif choch_grade == CHoCHGrade.B:
                self.stats['grade_b_signals'] += 1

        if debug:
            if signal:
                weight_label = f", 비중 {weight_multiplier*100:.0f}%" if weight_multiplier < 1.0 else ""
                console.print(f"[bold green]  진입 신호: {reason} (신뢰도: {confidence:.0%}{weight_label})[/bold green]")
            else:
                console.print(f"[yellow]  SMC: {reason}[/yellow]")

        return signal, reason, details

    def _calculate_structure_stop(
        self,
        df: pd.DataFrame,
        structure: MarketStructure,
        choch: StructureBreakEvent,
        current_price: float,
        debug: bool = True
    ) -> Optional[float]:
        """
        🔧 2026-02-06: 구조 기반 손절가 계산

        롱 진입 시: 직전 HL 또는 스윙로우 - ATR * 0.5
        숏 진입 시: 직전 LH 또는 스윙하이 + ATR * 0.5

        Args:
            df: OHLCV DataFrame (소문자 컬럼)
            structure: 시장 구조
            choch: CHoCH 이벤트
            current_price: 현재가
            debug: 디버그 로그 출력

        Returns:
            구조 기반 손절가 (None이면 계산 불가)
        """
        try:
            # ATR 값 가져오기
            atr = 0
            if 'atr' in df.columns and len(df) > 0:
                atr = df['atr'].iloc[-1]
            if atr <= 0:
                atr = current_price * 0.02  # 기본값 2%

            atr_buffer_mult = 0.5  # 구조점 아래 ATR × 0.5 버퍼

            if choch.direction == 'bullish':
                # 롱: 직전 HL 또는 스윙로우
                swing_low_price = None

                # 1순위: 직전 HL
                if structure.last_hl:
                    swing_low_price = structure.last_hl.price

                # 2순위: 최근 스윙 로우
                if swing_low_price is None:
                    swing_lows = [sp for sp in structure.swing_points if sp.type == 'low']
                    if swing_lows:
                        swing_low_price = swing_lows[-1].price

                if swing_low_price is not None:
                    structure_stop = swing_low_price - (atr * atr_buffer_mult)
                    if debug:
                        console.print(
                            f"[cyan]  📍 구조 손절: 스윙로우 {swing_low_price:.0f} "
                            f"- ATR*{atr_buffer_mult} ({atr*atr_buffer_mult:.0f}) "
                            f"= {structure_stop:.0f}[/cyan]"
                        )
                    return structure_stop

            elif choch.direction == 'bearish':
                # 숏: 직전 LH 또는 스윙하이
                swing_high_price = None

                if structure.last_lh:
                    swing_high_price = structure.last_lh.price

                if swing_high_price is None:
                    swing_highs = [sp for sp in structure.swing_points if sp.type == 'high']
                    if swing_highs:
                        swing_high_price = swing_highs[-1].price

                if swing_high_price is not None:
                    structure_stop = swing_high_price + (atr * atr_buffer_mult)
                    if debug:
                        console.print(
                            f"[cyan]  📍 구조 손절: 스윙하이 {swing_high_price:.0f} "
                            f"+ ATR*{atr_buffer_mult} ({atr*atr_buffer_mult:.0f}) "
                            f"= {structure_stop:.0f}[/cyan]"
                        )
                    return structure_stop

        except Exception as e:
            if debug:
                console.print(f"[dim]⚠️ 구조 손절 계산 오류: {e}[/dim]")

        return None

    def check_exit_signal(
        self,
        df: pd.DataFrame,
        entry_direction: str = 'long',
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        SMC 청산 신호 체크

        Args:
            df: OHLCV DataFrame
            entry_direction: 진입 방향 ('long' | 'short')
            debug: 디버그 로그 출력

        Returns:
            (should_exit, reason, details)

        청산 조건:
        1. 반대 방향 CHoCH 발생 (추세 전환)
        2. 반대 방향 BOS 발생 (추세 지속 확인)
        """
        details: Dict = {}

        if df is None or len(df) < 50:
            return False, "SMC: 데이터 부족", details

        # 시장 구조 분석
        structure = self.structure_analyzer.analyze_structure(df)
        details['structure'] = {
            'trend': structure.trend.value,
            'swing_count': len(structure.swing_points)
        }

        # CHoCH 체크
        choch = self.structure_analyzer.detect_choch(df, structure)
        if choch:
            details['choch'] = {
                'direction': choch.direction,
                'price': choch.price
            }

            # Long 포지션 + 하락 CHoCH -> 청산
            if entry_direction == 'long' and choch.direction == 'bearish':
                if debug:
                    console.print(f"[red]  SMC 청산: CHoCH 하락 전환[/red]")
                return True, "CHoCH 하락 전환 - Long 청산", details

            # Short 포지션 + 상승 CHoCH -> 청산
            if entry_direction == 'short' and choch.direction == 'bullish':
                if debug:
                    console.print(f"[red]  SMC 청산: CHoCH 상승 전환[/red]")
                return True, "CHoCH 상승 전환 - Short 청산", details

        # BOS 체크 (반대 방향)
        bos = self.structure_analyzer.detect_bos(df, structure)
        if bos:
            details['bos'] = {
                'direction': bos.direction,
                'price': bos.price
            }

            if entry_direction == 'long' and bos.direction == 'bearish':
                if debug:
                    console.print(f"[red]  SMC 청산: BOS 하락 지속[/red]")
                return True, "BOS 하락 지속 - Long 청산", details

            if entry_direction == 'short' and bos.direction == 'bullish':
                if debug:
                    console.print(f"[red]  SMC 청산: BOS 상승 지속[/red]")
                return True, "BOS 상승 지속 - Short 청산", details

        return False, "청산 조건 미충족", details

    def get_stats(self) -> Dict:
        """통계 반환"""
        stats = self.stats.copy()

        # 비율 계산
        if stats['total_checks'] > 0:
            stats['choch_rate'] = stats['choch_detected'] / stats['total_checks']
            stats['signal_rate'] = stats['entry_signals'] / stats['total_checks']
        else:
            stats['choch_rate'] = 0
            stats['signal_rate'] = 0

        return stats

    def reset_stats(self):
        """통계 초기화"""
        for key in self.stats:
            self.stats[key] = 0
