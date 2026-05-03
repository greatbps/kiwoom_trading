"""
최적화된 청산 로직 - 데이터 기반 손익비 개선

주요 개선사항:
1. 초기 실패 컷 추가 (30분 이내 -1.6%, 평균 손실 -2.03% 기반)
2. VWAP 단독 청산 권한 약화 (다중 조건 필요)
3. 트레일링 스탑 중심화
4. 시간 비교 버그 수정
5. DataFrame 컬럼 안전성 체크

🔧 2026-01-27 개선 (GPT 분석 기반):
6. 오버나잇 전용 Exit 로직 (전일 진입분 ≠ 당일 진입분)
7. 09:00~09:30 보호 구간 (시초가 변동성 보호)
8. 무승부 거래 정의 (+0.2% 미만 & 6시간+ 보유 = Draw)

🔥 2026-01-30 V2 업그레이드 (백테스트 검증 완료):
9. Early Failure v2: 45분 + HTF CHoCH + LL 확정 필수
10. VWAP 60분 Rule: 30분 내 손절 금지
11. ATR 트레일링 단계화: +2% OFF / +2~4% x3.0 / +4%+ x2.0
12. 포지션 생명주기: D+1 50%, D+3 청산/트레일링, D+5 강제청산
"""

# ============================================================
# 🔥 V2 FEATURE FLAG - 실전 배포 스위치
# ============================================================
USE_EXIT_V2 = True  # ← 실전 ON (2026-01-30 백테스트 통과)

# V2 파라미터 (2주간 수정 금지)
V2_EARLY_FAILURE_MIN_MINUTES = 45      # 최소 보유시간
V2_EARLY_FAILURE_LOSS_PCT = -2.5       # 손절 기준
V2_VWAP_MIN_MINUTES = 60               # VWAP 판단 최소 시간
V2_ATR_STAGE1_PROFIT = 2.0             # ATR 1단계 시작 (느슨)
V2_ATR_STAGE2_PROFIT = 4.0             # ATR 2단계 시작 (타이트)
V2_ATR_STAGE1_MULT = 3.0               # ATR x3.0 (느슨)
V2_ATR_STAGE2_MULT = 2.0               # ATR x2.0 (타이트)
V2_LIFECYCLE_D1_PARTIAL = 0.5          # D+1 부분청산 비율
V2_LIFECYCLE_D3_PROFIT_THRESHOLD = 3.0 # D+3 익절 기준
V2_LIFECYCLE_D5_FORCE = True           # D+5 강제청산

import logging
from datetime import datetime, time
from typing import Dict, Tuple, Optional
import pandas as pd
from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


class OptimizedExitLogic:
    """최적화된 청산 로직"""

    def __init__(self, config: Dict):
        """
        Args:
            config: strategy_config.yaml에서 로드한 설정
        """
        self.config = config

        # 리스크 관리 설정
        self.risk_control = config.get('risk_control', {})
        self.hard_stop_pct = self.risk_control.get('hard_stop_pct', 2.0)
        self.technical_stop_pct = self.risk_control.get('technical_stop_pct', 1.2)

        # 초기 실패 컷 설정
        self.early_failure = self.risk_control.get('early_failure', {})
        self.early_failure_enabled = self.early_failure.get('enabled', True)
        self.early_failure_window = self.early_failure.get('window_minutes', 30)  # 🔧 FIX: 15→30분 (노이즈 견디기)
        self.early_failure_loss = self.early_failure.get('loss_cut_pct', -1.6)    # 🔧 FIX: -0.6→-1.6% (평균 손실 -2.03%의 80%)

        # 🔧 Phase 3: 최소 보유 시간 설정
        self.min_hold_time = self.risk_control.get('min_hold_time', {})
        self.min_hold_enabled = self.min_hold_time.get('enabled', False)
        self.min_hold_minutes = self.min_hold_time.get('minutes', 30)

        # 🔧 2026-02-19: Loss Streak Guard — EF threshold override
        self.ef_threshold_override: Optional[int] = None

        # 🔧 2026-05-02: Hard stop 조건부 완화를 위한 현재 regime
        # main_auto_trading.py에서 주기적으로 업데이트 (self.exit_logic.market_regime = regime)
        self.market_regime: str = 'NEUTRAL'

        # 🔧 2026-01-20: 당일 매수 종목 강화 손절 설정
        self.same_day_entry = self.risk_control.get('same_day_entry', {})
        self.same_day_enabled = self.same_day_entry.get('enabled', False)
        self.same_day_stop_loss_pct = self.same_day_entry.get('stop_loss_pct', 1.5)  # 당일 매수: 타이트한 손절
        self.same_day_trailing_ratio = self.same_day_entry.get('trailing_ratio', 0.8)

        # 🔧 2026-01-27: 오버나잇 전용 Exit 로직
        self.overnight_exit = self.risk_control.get('overnight_exit', {})
        self.overnight_exit_enabled = self.overnight_exit.get('enabled', True)
        self.overnight_morning_protection_start = self._parse_time(
            self.overnight_exit.get('morning_protection_start', '09:00:00')
        )
        self.overnight_morning_protection_end = self._parse_time(
            self.overnight_exit.get('morning_protection_end', '09:30:00')
        )
        self.overnight_use_open_range = self.overnight_exit.get('use_open_range', True)
        self.overnight_open_range_multiplier = self.overnight_exit.get('open_range_multiplier', 1.5)

        # 🔧 2026-01-27: 무승부(Draw) 거래 정의
        self.draw_trade = self.risk_control.get('draw_trade', {})
        self.draw_trade_enabled = self.draw_trade.get('enabled', True)
        self.draw_profit_threshold = self.draw_trade.get('profit_threshold_pct', 0.2)  # +0.2% 미만
        self.draw_min_hold_hours = self.draw_trade.get('min_hold_hours', 6)  # 6시간 이상

        # 부분 청산 설정
        self.partial_exit = config.get('partial_exit', {})
        self.partial_exit_enabled = self.partial_exit.get('enabled', True)
        self.partial_tiers = self.partial_exit.get('tiers', [])

        # 트레일링 스탑 설정
        self.trailing_stop = config.get('trailing_stop', {})
        self.trailing_activation = self.trailing_stop.get('activation_profit_pct', 1.5)
        self.trailing_distance = self.trailing_stop.get('distance_pct', 0.8)
        self.trailing_min_lock = self.trailing_stop.get('min_lock_profit_pct', 0.5)

        # VWAP 청산 설정
        self.vwap_exit = config.get('vwap_exit', {})
        self.vwap_profit_threshold = self.vwap_exit.get('profit_threshold_for_ignore', 1.5)
        self.vwap_multi_condition = self.vwap_exit.get('multi_condition_required', True)

        # 시간 청산 설정 (🔧 FIX: eod_policy.enabled 체크 추가)
        self.eod_policy = config.get('eod_policy', {})
        self.time_based_exit_enabled = self.eod_policy.get('enabled', False)  # 기본값: 비활성화

        self.time_based_exit = config.get('time_based_exit', {})
        self.loss_exit_time_str = self.time_based_exit.get('loss_breakeven_exit_time', '15:00:00')
        self.final_exit_time_str = self.time_based_exit.get('final_force_exit_time', '15:10:00')
        self.loss_threshold = self.time_based_exit.get('loss_breakeven_threshold_pct', 0.3)

        # 시간 객체로 변환 (문자열 비교 버그 방지)
        self.loss_exit_time = self._parse_time(self.loss_exit_time_str)
        self.final_exit_time = self._parse_time(self.final_exit_time_str)

    def _parse_time(self, time_str: str) -> time:
        """시간 문자열을 time 객체로 변환"""
        try:
            parts = time_str.split(':')
            return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
        except (ValueError, IndexError):
            return time(15, 0, 0)  # 기본값

    def calculate_dynamic_min_hold_time(
        self,
        df: pd.DataFrame,
        current_price: float,
        squeeze_color: str = None,
        position: Dict = None
    ) -> int:
        """
        변동성 기반 동적 min_hold_time 계산

        🔧 2026-02-06: 조건부 보유 시간 연장 추가
        HTF 추세 일치 + ATR 확장 + 거래량 평균 이상 중 2개 이상 충족 시 1.5배 연장

        Args:
            df: OHLCV + ATR 데이터
            current_price: 현재가
            squeeze_color: 스퀴즈 색상 (bright_green, dark_green, etc.)
            position: 포지션 정보 (조건부 보유 판단용)

        Returns:
            조정된 min_hold_time (분)
        """
        BASE_MIN_HOLD = 30  # 기본 30분
        MIN_LIMIT = 5       # 최소 5분
        MAX_LIMIT = 60      # 최대 60분

        # 1. ATR 기반 변동성 계산
        try:
            if 'atr' in df.columns and len(df) > 0:
                atr = df['atr'].iloc[-1]
                if atr > 0 and current_price > 0:
                    volatility_score = atr / current_price  # 비율
                else:
                    volatility_score = 0.005  # 기본값 0.5%
            else:
                # ATR 없으면 고가-저가 범위로 추정
                recent_high = df['high'].tail(10).max() if 'high' in df.columns else current_price
                recent_low = df['low'].tail(10).min() if 'low' in df.columns else current_price
                volatility_score = (recent_high - recent_low) / current_price if current_price > 0 else 0.005
        except Exception:
            volatility_score = 0.005  # 에러 시 기본값

        # 2. 변동성 → min_hold_time 매핑
        # 공식: min_hold = BASE * (0.5 / volatility)
        # 변동성 0.5% → 30분, 0.25% → 60분, 1.0% → 15분
        adjusted_min_hold = BASE_MIN_HOLD * (0.005 / max(volatility_score, 0.001))

        # 3. Squeeze 상태별 보정
        if squeeze_color in ['bright_green', 'dark_green']:
            adjusted_min_hold *= 1.5  # BG/DG는 1.5배 더 홀딩

        # 4. 범위 제한
        adjusted_min_hold = max(MIN_LIMIT, min(adjusted_min_hold, MAX_LIMIT))

        # 🔧 2026-02-06: 조건부 보유 시간 연장
        conditional_hold_config = self.risk_control.get('conditional_hold', {})
        if conditional_hold_config.get('enabled', True) and position is not None:
            hold_conditions_met = 0
            hold_condition_labels = []

            # 조건 1: HTF 추세 일치 (포지션 방향 = HTF 추세)
            try:
                direction = position.get('direction', 'long')
                htf_trend_aligned = position.get('htf_trend_aligned', False)
                if htf_trend_aligned:
                    hold_conditions_met += 1
                    hold_condition_labels.append('HTF추세일치')
            except Exception:
                pass

            # 조건 2: ATR 확장 (현재 ATR > 이전 ATR)
            try:
                if 'atr' in df.columns and len(df) >= 2:
                    current_atr = df['atr'].iloc[-1]
                    prev_atr = df['atr'].iloc[-2]
                    if current_atr > prev_atr:
                        hold_conditions_met += 1
                        hold_condition_labels.append('ATR확장')
            except Exception:
                pass

            # 조건 3: 거래량 평균 이상
            try:
                if 'volume' in df.columns and len(df) >= 20:
                    current_vol = df['volume'].iloc[-1]
                    avg_vol = df['volume'].tail(20).mean()
                    if current_vol > avg_vol:
                        hold_conditions_met += 1
                        hold_condition_labels.append('거래량↑')
            except Exception:
                pass

            min_conditions = conditional_hold_config.get('min_conditions', 2)
            hold_multiplier = conditional_hold_config.get('hold_multiplier', 1.5)

            if hold_conditions_met >= min_conditions:
                adjusted_min_hold *= hold_multiplier
                adjusted_min_hold = min(adjusted_min_hold, MAX_LIMIT * hold_multiplier)
                console.print(
                    f"[green]📊 조건부 홀딩 연장: {', '.join(hold_condition_labels)} "
                    f"({hold_conditions_met}/{min_conditions}) → ×{hold_multiplier}[/green]"
                )

        console.print(
            f"[cyan]📊 동적 홀딩: 변동성 {volatility_score*100:.2f}% → "
            f"min_hold {int(adjusted_min_hold)}분 (스퀴즈: {squeeze_color or 'N/A'})[/cyan]"
        )

        return int(adjusted_min_hold)

    def check_early_failure_structure(
        self,
        position: Dict,
        current_price: float,
        df: pd.DataFrame,
        elapsed_minutes: float
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        🔧 2026-02-07: Early Failure Structure Filter (빠른 구조 실패 감지)

        진입 후 2~3캔들(5~15분) 이내에 구조 실패 신호를 점수로 평가.
        Score: direction_fail(2) + atr_decay(1) + volume_dry(1) >= threshold

        Args:
            position: 포지션 정보 dict
            current_price: 현재가
            df: OHLCV + 기술적 지표 DataFrame
            elapsed_minutes: 진입 후 경과 시간 (분)

        Returns:
            (should_exit, reason, additional_info)
        """
        config = self.risk_control.get('early_failure_structure', {})
        if not config.get('enabled', True):
            return False, "", None

        observe_minutes = config.get('observe_minutes', 15)
        min_observe_minutes = config.get('min_observe_minutes', 5)
        # 🔧 2026-02-19: Loss Streak Guard — EF threshold override
        threshold = (self.ef_threshold_override
                     if self.ef_threshold_override is not None
                     else config.get('score_threshold', 3))

        # 관찰 구간 체크: min_observe ~ observe_minutes 사이만 판단
        if elapsed_minutes < min_observe_minutes or elapsed_minutes > observe_minutes:
            return False, "", None

        entry_price = self._safe_get_price(position, 'entry_price')
        if entry_price <= 0:
            return False, "", None

        direction = position.get('direction', 'long')
        score = 0
        signals = []

        # Signal A: Direction Failure (점수 2)
        # 진입 후 새로운 고점 미갱신 (롱) / 새로운 저점 미갱신 (숏)
        highest_since_entry = position.get('highest_price', entry_price)
        if direction == 'long':
            if highest_since_entry <= entry_price * 1.001:  # 0.1% 이상 고점 미갱신
                score += 2
                signals.append('방향실패(2)')
        else:
            lowest_since_entry = position.get('lowest_price', entry_price)
            if lowest_since_entry >= entry_price * 0.999:  # 0.1% 이상 저점 미갱신
                score += 2
                signals.append('방향실패(2)')

        # Signal B: ATR Decay (점수 1)
        atr_at_entry = position.get('atr_at_entry')
        atr_decay_ratio = config.get('atr_decay_ratio', 0.85)
        try:
            if atr_at_entry and atr_at_entry > 0 and 'atr' in df.columns and len(df) > 0:
                current_atr = df['atr'].iloc[-1]
                if current_atr > 0 and current_atr / atr_at_entry < atr_decay_ratio:
                    score += 1
                    signals.append('ATR감쇠(1)')
        except Exception:
            pass

        # Signal C: Volume Dry-up (점수 1)
        volume_dry_ratio = config.get('volume_dry_ratio', 0.8)
        try:
            if 'volume' in df.columns and len(df) >= 20:
                current_vol = df['volume'].iloc[-1]
                avg_vol = df['volume'].tail(20).mean()
                if avg_vol > 0 and current_vol / avg_vol < volume_dry_ratio:
                    score += 1
                    signals.append('거래량고갈(1)')
        except Exception:
            pass

        # Signal D: MFE Failure (점수 1) — 진입 후 MFE가 ATR 대비 부족
        mfe_ratio_threshold = config.get('mfe_ratio', 0.25)
        mfe_value = 0.0
        mfe_ratio_actual = 0.0
        try:
            atr_entry = position.get('atr_at_entry')
            if atr_entry and atr_entry > 0:
                if direction == 'long':
                    mfe_value = highest_since_entry - entry_price
                else:
                    mfe_value = entry_price - position.get('lowest_price', entry_price)
                mfe_ratio_actual = mfe_value / atr_entry
                if mfe_ratio_actual < mfe_ratio_threshold:
                    score += 1
                    signals.append('MFE부족(1)')
        except Exception:
            pass

        # Signal E: Follow-Through Failure (점수 1) — 최근 N캔들 종가 모두 진입가 아래
        follow_through_candles = config.get('follow_through_candles', 3)
        try:
            if 'close' in df.columns and len(df) >= follow_through_candles:
                recent_closes = df['close'].iloc[-follow_through_candles:]
                if direction == 'long':
                    if (recent_closes < entry_price).all():
                        score += 1
                        signals.append('추종실패(1)')
                else:
                    if (recent_closes > entry_price).all():
                        score += 1
                        signals.append('추종실패(1)')
        except Exception:
            pass

        # Signal F: Entry RVOL Collapse — 진입 직후 N봉 내 거래량 급감 + 저점 이탈
        # guards:
        #   ① 최소 2봉 후 발동 (1봉 일시 눌림 오컷 방지)
        #   ② 스윙 저점(최근 3봉 low min) 이탈 확인 — "눌림 후 지지" vs "실패 붕괴" 구분
        #   ③ 가변 점수: rvol_ratio < 0.3 → 3점, < 0.5 → 2점 (붕괴 강도 반영)
        rvol_follow_bars       = config.get('rvol_follow_bars', 3)
        rvol_follow_ratio      = config.get('rvol_follow_ratio', 0.5)
        rvol_follow_min_bars   = config.get('rvol_follow_min_bars', 2)
        rvol_follow_price_drop = config.get('rvol_follow_price_drop', 0.003)
        _bars_since_ef = int(elapsed_minutes / 5) if elapsed_minutes > 0 else 0
        try:
            _entry_rvol = position.get('rvol_at_entry')
            if (
                _entry_rvol and _entry_rvol > 0
                and _bars_since_ef >= rvol_follow_min_bars
                and _bars_since_ef <= rvol_follow_bars
                and 'volume' in df.columns and len(df) >= 20
                and 'low' in df.columns
            ):
                _cur_vol_f  = df['volume'].iloc[-1]
                _avg_vol_f  = df['volume'].tail(20).mean()
                _cur_rvol_f = _cur_vol_f / _avg_vol_f if _avg_vol_f > 0 else 0.0
                _rvol_ratio = _cur_rvol_f / _entry_rvol

                # 가변 점수 (붕괴 강도)
                if _rvol_ratio < 0.3:
                    _f_score = 3   # 완전 붕괴 → 3점
                elif _rvol_ratio < rvol_follow_ratio:
                    _f_score = 2   # 50% 미만 → 2점
                else:
                    _f_score = 0

                if _f_score > 0:
                    # 스윙 저점 이탈 확인 — 최근 3봉 low의 최솟값 기준
                    _recent_low_f   = df['low'].tail(3).min()
                    _support_broken = current_price < _recent_low_f * (1 - rvol_follow_price_drop)
                    _price_chg_f    = (current_price - entry_price) / entry_price

                    if _support_broken:
                        score += _f_score
                        signals.append(
                            f'RVOL붕괴({_f_score})[{_entry_rvol:.1f}x→{_cur_rvol_f:.1f}x]'
                        )
                        logger.info(
                            f"[SIGNAL_F] rvol_drop={_cur_rvol_f:.2f} "
                            f"(entry={_entry_rvol:.1f}x ratio={_rvol_ratio:.2f}) "
                            f"price_change={_price_chg_f*100:+.2f}% "
                            f"support_broken=True recent_low={_recent_low_f:.0f} "
                            f"score_added={_f_score} bars={_bars_since_ef}"
                        )
        except Exception:
            pass

        # 로깅 (관찰 구간 내)
        console.print(
            f"[dim]🔍 Early Failure 구조: score={score}/{threshold} "
            f"({', '.join(signals) if signals else '정상'}, "
            f"{elapsed_minutes:.1f}분/{observe_minutes}분)[/dim]"
        )

        # 임계값 판단
        if score >= threshold:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            action = config.get('action', 'exit_market')

            # 🔧 2026-02-08: EF Subtype 분류 (no_follow vs no_demand)
            # no_follow: MFE 발생(D 미발동) + 추종 실패 → 타이밍은 맞았으나 지속 실패
            # no_demand: MFE 부족(D 발동) → 애초에 수급이 없었던 가짜 신호
            has_mfe_failure = 'MFE부족(1)' in signals
            ef_subtype = 'no_demand' if has_mfe_failure else 'no_follow'

            console.print(
                f"[red]🚨 Early Failure[{ef_subtype}] 구조 발동: {'+'.join(signals)} "
                f"(score={score}, 액션={action}, {profit_pct:+.2f}%)[/red]"
            )

            return True, f"Early Failure[{ef_subtype}] 구조 ({'+'.join(signals)}, score={score})", {
                'profit_pct': profit_pct,
                'score': score,
                'signals': signals,
                'action': action,
                'use_market_order': action == 'exit_market',
                'emergency': True,
                'reason': 'EARLY_FAILURE_STRUCTURE',
                'ef_subtype': ef_subtype,
                'mfe_value': mfe_value,
                'mfe_ratio': mfe_ratio_actual,
            }

        return False, "", None


    def check_exit_signal(
        self,
        position: Dict,
        current_price: float,
        df: pd.DataFrame
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        청산 신호 체크 — Intraday Trend Capture 구조 (2026-04-17 리팩토링)

        우선순위:
          1. 5분 최소 락 (노이즈 방지)
          2. 구조 손절 / Hard Stop (즉시, 락 무시)
          3. TP1 후 BE 스탑 (손실 방지)
          4. R-TP1 부분 익절 (1.5R → 50%)
          5. R-TP2 부분 익절 (3R → 잔여 50%) + 트레일링 ON
          6. A급 보호: STRUCTURE_EXIT / PROFIT_LOCK / NO_PROGRESS / A_FORCE_EXIT
          7. ATR 트레일링 (단순화: +2% ON, profit별 배수)
          8. EOD 15:00 시간 청산
        """

        # ====================================================
        # 0. 데이터 검증 및 기본값 초기화
        # ====================================================
        entry_price = self._safe_get_price(position, 'entry_price')
        if entry_price <= 0:
            console.print(f"[red]⚠️ 비정상 진입가: {position.get('entry_price')}[/red]")
            return False, "ERROR_INVALID_ENTRY_PRICE", None

        profit_pct = ((current_price - entry_price) / entry_price) * 100

        entry_time = position.get('entry_time') or position.get('entry_date')
        if entry_time and isinstance(entry_time, str):
            entry_time = datetime.fromisoformat(entry_time)

        elapsed_minutes = 0
        if entry_time:
            elapsed_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # 최고가 업데이트
        highest_price = position.get('highest_price', entry_price)
        if current_price > highest_price:
            highest_price = current_price
            position['highest_price'] = highest_price

        partial_stage = position.get('partial_exit_stage', 0)

        # ====================================================
        # 1. 최소 락 5분 (동적 락 완전 제거 → 고정 5분)
        # ====================================================
        _min_hold = self.config.get('risk_control.min_hold_minutes', 5)
        if elapsed_minutes < _min_hold:
            return False, f"최소락 ({elapsed_minutes:.1f}/{_min_hold}분)", None

        # ====================================================
        # 1-b. 긴급 Hard Stop (fail-safe, 구조 손절보다 선행)
        #      갭다운/급락/테마주 수직 낙하 시 구조 손절이 못 잡는 케이스 방어
        # ====================================================
        _emg_pct = self.config.get('risk_control.emergency_stop_pct', 6.0)
        # 🔧 2026-05-02: Bull regime(TREND) 시 hard stop 조건부 완화
        _relax_cfg = (self.config.get('risk_control') or {}).get('hard_stop_relax', {})
        if (_relax_cfg.get('enabled', False)
                and getattr(self, 'market_regime', 'NEUTRAL') == 'TREND'):
            _relax_pct = _relax_cfg.get('bull_pct', 8.0)
            if _relax_pct > _emg_pct:
                logger.debug(
                    f"[HARD_STOP_RELAX] TREND regime → emg_pct {_emg_pct}% → {_relax_pct}%"
                )
                _emg_pct = _relax_pct
        if profit_pct <= -_emg_pct:
            # 🔧 1봉 유예: 마지막 봉 종가가 직전 봉 저가를 하향 이탈해야 진짜 붕괴
            # 단순 스파이크(저가 찍고 회복) vs 실제 붕괴를 구분
            _candle_confirms_breakdown = True  # 기본: 즉시 발동
            _candle_confirm_enabled = self.config.get('risk_control.emergency_stop_candle_confirm', True)
            if _candle_confirm_enabled:
                try:
                    if len(df) >= 2 and 'close' in df.columns and 'low' in df.columns:
                        _last_close = float(df['close'].iloc[-1])
                        _prev_low   = float(df['low'].iloc[-2])
                        if _last_close >= _prev_low:
                            # 종가가 직전 봉 저가 이상 = 아직 스파이크 가능성
                            _candle_confirms_breakdown = False
                            console.print(
                                f"[yellow]⚠️ 긴급손절 유예: 종가({_last_close:,.0f}) ≥ 직전저가({_prev_low:,.0f}) "
                                f"→ 스파이크 의심, 1봉 관찰[/yellow]"
                            )
                except Exception:
                    pass  # 데이터 없으면 즉시 발동

            if _candle_confirms_breakdown:
                _emg_reason = f"[HARD_STOP_EMERGENCY] {profit_pct:.2f}% ≤ -{_emg_pct}% (candle confirmed)"
                logger.info(_emg_reason)
                console.print(f"[bold red]🚨 긴급 손절: {profit_pct:.2f}% ≤ -{_emg_pct}% (하향 이탈 확인)[/bold red]")
                return True, _emg_reason, {
                    'profit_pct': profit_pct, 'use_market_order': True, 'emergency': True,
                }

        # ====================================================
        # 2. 구조 손절 / Hard Stop (가장 높은 우선순위)
        # ====================================================
        structure_stop_price = position.get('structure_stop_price')
        struct_stop_config   = self.risk_control.get('structure_based_stop', {})
        max_stop_pct         = struct_stop_config.get('max_stop_pct', 5.0)  # -5% cap

        if structure_stop_price and struct_stop_config.get('enabled', True):
            # cap 적용
            structure_loss_pct = (entry_price - structure_stop_price) / entry_price * 100
            if structure_loss_pct > max_stop_pct:
                structure_stop_price = entry_price * (1 - max_stop_pct / 100)
                console.print(
                    f"[yellow]⚠️ 구조 손절 cap: {structure_loss_pct:.1f}% → -{max_stop_pct}% "
                    f"({structure_stop_price:,.0f}원)[/yellow]"
                )

            # 🔧 A/A+ 등급 + TP1 전 + entry_confidence ≥ 0.7: 손절 0.5% 완화
            # "진짜 A급" (고확신 신호)만 보호 → 애매한 A-성격은 즉시 컷
            _grade          = position.get('choch_grade') or position.get('choch_grade_log', '')
            _confidence     = position.get('entry_confidence', 0.0)
            _a_noise_buffer = self.config.get('risk_control.a_grade_stop_buffer_pct', 0.5)
            _min_conf       = self.config.get('risk_control.a_grade_stop_buffer_min_confidence', 0.7)
            _qualifies      = (
                partial_stage < 1 and
                _grade in ('A', 'A+') and
                _a_noise_buffer > 0 and
                _confidence >= _min_conf
            )
            if _qualifies:
                _buffered_stop = structure_stop_price * (1 - _a_noise_buffer / 100)
                logger.debug(
                    f"[A_STOP_BUFFER] {_grade}급 conf={_confidence:.2f}≥{_min_conf} TP1 전 손절 완화: "
                    f"{structure_stop_price:,.0f} → {_buffered_stop:,.0f} (-{_a_noise_buffer}%)"
                )
                structure_stop_price = _buffered_stop
            elif partial_stage < 1 and _grade in ('A', 'A+') and _confidence < _min_conf:
                logger.debug(
                    f"[A_STOP_BUFFER_SKIP] {_grade}급이지만 conf={_confidence:.2f}<{_min_conf} → 완화 미적용"
                )

            if current_price <= structure_stop_price:
                _sl_pct = (current_price - entry_price) / entry_price * 100
                logger.info(f"[STRUCTURE_STOP] {current_price:,.0f} ≤ {structure_stop_price:,.0f} ({_sl_pct:.2f}%)")
                console.print(f"[red]📍 구조 손절 발동: {current_price:,.0f} ≤ {structure_stop_price:,.0f} ({_sl_pct:+.2f}%)[/red]")
                return True, f"[STRUCTURE_STOP] {structure_stop_price:,.0f}원 ({_sl_pct:.2f}%)", {
                    'profit_pct': profit_pct, 'use_market_order': True,
                    'emergency': True, 'structure_stop_price': structure_stop_price,
                }
        else:
            # 구조 손절 없으면 % 기반 Hard Stop (-max_stop_pct)
            if profit_pct <= -max_stop_pct:
                logger.info(f"[HARD_STOP] {profit_pct:.2f}% ≤ -{max_stop_pct}%")
                return True, f"[HARD_STOP] -{max_stop_pct}% ({profit_pct:.2f}%)", {
                    'profit_pct': profit_pct, 'use_market_order': True, 'emergency': True,
                }

        # ====================================================
        # 3. TP2 이후 → BE 스탑 (손실 방지)
        #    TP1(2R/25%) 후엔 trailing floor가 원금 보호
        #    TP2(4R/25%) 후에야 BE+buffer 발동 — 휩쏘 방어
        # ====================================================
        if partial_stage >= 2:
            # +0.2% 버퍼: 한국장 호가/슬리피지로 인한 휩쏘 방어
            _be_buffer = self.config.get('risk_control.be_stop_buffer_pct', 0.2)
            _be_stop   = entry_price * (1 + _be_buffer / 100)
            if current_price <= _be_stop:
                _be_pct = (current_price - entry_price) / entry_price * 100
                logger.info(f"[BE_STOP] TP2 후 BE+{_be_buffer}% 손절 ({_be_pct:.2f}%)")
                console.print(f"[yellow]🔒 BE 스탑(+{_be_buffer}%): {current_price:,.0f} ({_be_pct:+.2f}%)[/yellow]")
                return True, f"[BE_STOP] TP2 후 BE+{_be_buffer}% 손절 ({_be_pct:.2f}%)", {
                    'profit_pct': profit_pct, 'use_market_order': False, 'emergency': False,
                }

        # ====================================================
        # 4. R-기반 부분 익절 (TP1=2R/25%, TP2=4R/25%, 잔여50%=trailing)
        # ====================================================
        r_tp1_price = position.get('r_tp1_price')
        r_tp2_price = position.get('r_tp2_price')
        r_pct       = position.get('r_pct', 0)

        if r_tp1_price and r_tp2_price:
            tp1_pct = (r_tp1_price - entry_price) / entry_price * 100
            tp2_pct = (r_tp2_price - entry_price) / entry_price * 100

            if partial_stage < 1 and current_price >= r_tp1_price:
                _r1_reason = f"[R_TP1] +{profit_pct:.1f}% ≥ 2R({tp1_pct:.1f}%) → 25% 부분익절"
                logger.info(_r1_reason)
                return False, _r1_reason, {
                    'partial_exit': True, 'stage': 1, 'exit_ratio': 0.25, 'profit_pct': profit_pct,
                }
            elif partial_stage == 1 and current_price >= r_tp2_price:
                position['trailing_active'] = True
                _r2_reason = f"[R_TP2] +{profit_pct:.1f}% ≥ 4R({tp2_pct:.1f}%) → 25% 부분익절 + 트레일링 ON (잔여50%)"
                logger.info(_r2_reason)
                return False, _r2_reason, {
                    'partial_exit': True, 'stage': 2, 'exit_ratio': 0.25, 'profit_pct': profit_pct,
                }

        # ====================================================
        # 5. A급 연장 보호 장치 (eq=A AND choch=A/A+)
        #    TIME_EXIT 면제 대신: STRUCTURE_EXIT + PROFIT_LOCK + NO_PROGRESS + A_FORCE_EXIT
        # ====================================================
        _a_ext_cfg = self.config.get('smc.a_grade_hold_extension', {})
        _is_a_grade = (
            _a_ext_cfg.get('enabled', False) and
            r_pct > 0 and
            position.get('eq_grade') == 'A' and
            position.get('choch_grade_log') in ('A', 'A+')
        )

        if _is_a_grade:
            _r_amount        = entry_price * r_pct / 100
            _mfe_pct         = (highest_price - entry_price) / entry_price * 100 if highest_price > entry_price else 0.0
            _mfe_r           = _mfe_pct / r_pct if r_pct > 0 else 0.0
            _bars_since      = int(elapsed_minutes / 5)
            _te_bars         = self.config.get('risk_control.time_exit', {}).get('bars', 10)

            # ① STRUCTURE_EXIT: bearish CHoCH 확정 → 전량 청산
            if _a_ext_cfg.get('structure_exit', True):
                try:
                    from analyzers.smc.smc_structure import SMCStructureAnalyzer
                    _sa     = SMCStructureAnalyzer()
                    _df_tmp = df.copy()
                    _df_tmp.columns = [c.lower() for c in _df_tmp.columns]
                    _struct = _sa.analyze_structure(_df_tmp)
                    _choch  = _sa.detect_choch(_df_tmp, _struct)
                    if _choch and _choch.direction == 'bearish':
                        _se_reason = f"[STRUCTURE_EXIT] A급 bearish CHoCH (pnl={profit_pct:+.2f}%)"
                        logger.info(_se_reason)
                        return True, _se_reason, {'structure_exit': True, 'profit_pct': profit_pct}
                except Exception:
                    pass

            # ② PROFIT_LOCK 티어형: MFE R 달성할수록 보호선 상승
            _default_tiers = [
                {'mfe_r': 1.5, 'floor_r': 0.5},
                {'mfe_r': 2.5, 'floor_r': 1.0},
                {'mfe_r': 4.0, 'floor_r': 2.0},
            ]
            _tiers           = _a_ext_cfg.get('profit_lock_tiers', _default_tiers)
            _lock_floor_price = None
            _active_tier     = None
            for _tier in reversed(_tiers):
                if _mfe_r >= _tier['mfe_r']:
                    _lock_floor_price = entry_price + _tier['floor_r'] * _r_amount
                    _active_tier      = _tier
                    break

            if _lock_floor_price is not None and current_price < _lock_floor_price:
                _floor_pct = (_lock_floor_price - entry_price) / entry_price * 100
                _pl_reason = (
                    f"[PROFIT_LOCK] MFE={_mfe_r:.1f}R → 보호선=+{_floor_pct:.2f}%, "
                    f"현재={profit_pct:+.2f}%"
                )
                logger.info(_pl_reason)
                return True, _pl_reason, {
                    'profit_lock': True, 'profit_pct': profit_pct, 'lock_tier': _active_tier,
                }

            # ③ NO_PROGRESS_EXIT: 1.5R 미달 + N봉 경과 → 힘 없는 거래 컷
            _np_mfe_r = _a_ext_cfg.get('no_progress_mfe_r', 1.5)
            _np_max   = _a_ext_cfg.get('no_progress_bars', 15)
            if _mfe_r < _np_mfe_r and _bars_since >= _np_max:
                _np_reason = (
                    f"[NO_PROGRESS_EXIT] MFE={_mfe_r:.2f}R < {_np_mfe_r}R, "
                    f"{_bars_since}봉 ≥ {_np_max}봉, pnl={profit_pct:+.2f}%"
                )
                logger.info(_np_reason)
                return True, _np_reason, {'no_progress_exit': True, 'profit_pct': profit_pct}

            # ④ A_FORCE_EXIT: 최대 보유 봉수 초과 (time_exit.bars × max_bars_mult)
            _mult  = _a_ext_cfg.get('max_bars_mult', 2)
            _a_max = _te_bars * _mult
            if _bars_since >= _a_max:
                _af_reason = f"[A_FORCE_EXIT] {_bars_since}봉 ≥ {_a_max}봉, pnl={profit_pct:+.2f}%"
                logger.info(_af_reason)
                return True, _af_reason, {'a_force_exit': True, 'profit_pct': profit_pct}

        # ====================================================
        # 6. ATR 트레일링 — 3단 tightening (수익 커질수록 타이트)
        #    +2~5%  : ATR×3.0 (느슨 — 추세 유지)
        #    +5~8%  : ATR×2.5 (중간 — 수익 잠금 시작)
        #    +8%+   : ATR×2.0 (타이트 — 최대 수익 보호)
        # ====================================================
        _trailing_on_pct = self.config.get('risk_control.trailing_activation_pct', 2.0)
        if position.get('trailing_active') or profit_pct >= _trailing_on_pct:
            position['trailing_active'] = True

            atr_value = 0.0
            try:
                if 'atr' in df.columns and len(df) > 0:
                    atr_value = float(df['atr'].iloc[-1])
            except Exception:
                pass
            if atr_value <= 0:
                atr_value = entry_price * 0.02  # 2% 기본값

            # 3단 tightening
            _tr_cfg = self.config.get('risk_control.trailing_tiers', {})
            _t1_pct  = _tr_cfg.get('tier1_profit', 5.0)
            _t2_pct  = _tr_cfg.get('tier2_profit', 8.0)
            _t1_mult = _tr_cfg.get('tier1_mult', 2.5)
            _t2_mult = _tr_cfg.get('tier2_mult', 2.0)
            _base_mult = _tr_cfg.get('base_mult', 3.0)

            if profit_pct >= _t2_pct:
                atr_multiplier = _t2_mult   # +8%+: 타이트
            elif profit_pct >= _t1_pct:
                atr_multiplier = _t1_mult   # +5~8%: 중간
            else:
                atr_multiplier = _base_mult  # +2~5%: 느슨

            trailing_stop_price = highest_price - atr_value * atr_multiplier

            # TP1 이후: 원금 손실 방지 (entry 이하 차단)
            # TP2 이후: BE+buffer 보장 (호가/슬리피지 방어)
            if partial_stage >= 2:
                _be_buffer = self.config.get('risk_control.be_stop_buffer_pct', 0.2)
                _min_floor = entry_price * (1 + _be_buffer / 100)
                trailing_stop_price = max(trailing_stop_price, _min_floor)
            elif partial_stage >= 1:
                trailing_stop_price = max(trailing_stop_price, entry_price)

            position['trailing_stop_price'] = trailing_stop_price

            if current_price <= trailing_stop_price:
                _tr_reason = (
                    f"[TRAILING_STOP] ATR×{atr_multiplier} "
                    f"(고가={highest_price:,.0f} → 스탑={trailing_stop_price:,.0f}, "
                    f"pnl={profit_pct:+.2f}%)"
                )
                logger.info(_tr_reason)
                return True, _tr_reason, {
                    'profit_pct': profit_pct,
                    'highest_price': highest_price,
                    'trailing_stop_price': trailing_stop_price,
                    'atr_multiplier': atr_multiplier,
                }

        # ====================================================
        # 7. EOD 시간 기반 청산 (15:00, 오버나이트 승인 제외)
        # ====================================================
        if self.time_based_exit_enabled:
            # 오버나이트 승인된 포지션은 시간 청산 제외
            if position.get('allow_overnight_final_confirm', False):
                console.print(
                    f"[cyan]✓ 오버나이트 승인 포지션 - 시간 청산 제외[/cyan]"
                )
                return False, None, None

            current_time = datetime.now().time()
            if current_time >= self.loss_exit_time:
                return True, f"시간 기반 청산 (15:00, {profit_pct:+.2f}%)", {'profit_pct': profit_pct}

        # 청산 신호 없음
        return False, None, None


    def _safe_get_price(self, position: Dict, key: str) -> float:
        """
        안전하게 가격 추출 (바이너리 데이터 버그 방지)

        Args:
            position: 포지션 dict
            key: 가격 키 ('entry_price', 'avg_price' 등)

        Returns:
            float 가격, 실패 시 0
        """
        try:
            price = position.get(key, 0)

            # bytes 타입이면 변환 (DB에 정수로 저장됨)
            if isinstance(price, bytes):
                # Little-endian 8바이트 정수 변환
                try:
                    import struct
                    price = struct.unpack('<q', price)[0]  # int64 (우선)
                except struct.error:
                    try:
                        price = struct.unpack('<d', price)[0]  # double (fallback)
                    except struct.error:
                        console.print(f"[red]⚠️ {key} 바이너리 변환 실패: {price}[/red]")
                        return 0

            return float(price)
        except Exception as e:
            console.print(f"[red]⚠️ {key} 추출 실패: {e}[/red]")
            return 0

    def _check_vwap_exit(
        self,
        df: pd.DataFrame,
        current_price: float,
        profit_pct: float
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        VWAP 기반 청산 체크 (다중 조건 필요)

        Returns:
            (should_exit, exit_reason, additional_info)
        """

        if not self.vwap_multi_condition:
            # 단일 조건만 체크 (기존 방식)
            if 'signal' in df.columns and df['signal'].iloc[-1] == -1:
                return True, "VWAP 하향 돌파", {'profit_pct': profit_pct}
            return False, None, None

        # 다중 조건 체크
        conditions_met = 0
        condition_details = []

        # 조건 1: VWAP 하향 돌파
        if 'signal' in df.columns and df['signal'].iloc[-1] == -1:
            conditions_met += 1
            condition_details.append("VWAP↓")

        # 조건 2: EMA3 하향 이탈
        if 'close' in df.columns and len(df) >= 3:
            ema_fast = df['close'].ewm(span=3, adjust=False).mean().iloc[-1]
            if current_price < ema_fast:
                conditions_met += 1
                condition_details.append("EMA3↓")

        # 조건 3: RSI 모멘텀 약화
        if 'rsi' in df.columns:
            rsi_value = df['rsi'].iloc[-1]
            if rsi_value < 45:
                conditions_met += 1
                condition_details.append(f"RSI{rsi_value:.1f}")

        # 2개 이상 동시 충족 시 청산
        if conditions_met >= 2:
            reason = f"다중 약화 신호 ({'+'.join(condition_details)})"
            return True, reason, {
                'profit_pct': profit_pct,
                'conditions_met': conditions_met,
                'details': condition_details
            }

        return False, None, None

    def get_exit_summary(self, position: Dict) -> str:
        """포지션 청산 관련 요약 정보"""
        entry_price = self._safe_get_price(position, 'entry_price')
        highest_price = position.get('highest_price', entry_price)
        trailing_active = position.get('trailing_active', False)
        partial_stage = position.get('partial_exit_stage', 0)

        summary = f"진입가 {entry_price:,.0f}원"

        if highest_price > entry_price:
            max_profit = ((highest_price - entry_price) / entry_price * 100)
            summary += f" | 최고가 {highest_price:,.0f}원 (+{max_profit:.2f}%)"

        if trailing_active:
            trailing_price = position.get('trailing_stop_price', 0)
            summary += f" | 트레일링 활성 (스탑: {trailing_price:,.0f}원)"

        if partial_stage > 0:
            summary += f" | 부분청산 {partial_stage}차 완료"

        return summary

    # ========================================
    # 🔧 2026-01-27: 새로운 헬퍼 함수들
    # ========================================

    def classify_trade_result(
        self,
        profit_pct: float,
        hold_hours: float
    ) -> str:
        """
        거래 결과를 WIN / LOSS / DRAW로 분류

        Args:
            profit_pct: 수익률 (%)
            hold_hours: 보유 시간 (시간)

        Returns:
            'WIN', 'LOSS', 또는 'DRAW'
        """
        if not self.draw_trade_enabled:
            # Draw 분류 비활성화 시 기존 로직
            return 'WIN' if profit_pct > 0 else 'LOSS'

        # Draw 조건: +0.2% 미만 & 6시간 이상 보유
        if (abs(profit_pct) < self.draw_profit_threshold and
            hold_hours >= self.draw_min_hold_hours):
            return 'DRAW'

        return 'WIN' if profit_pct > 0 else 'LOSS'

    def record_open_range(
        self,
        position: Dict,
        df: pd.DataFrame,
        current_time: datetime = None
    ) -> bool:
        """
        오버나잇 포지션의 09:00~09:30 Open Range 기록

        Args:
            position: 포지션 정보
            df: OHLCV 데이터 (5분봉 기준)
            current_time: 현재 시간 (테스트용)

        Returns:
            True if Open Range가 기록됨
        """
        if current_time is None:
            current_time = datetime.now()

        # 09:30 이후에만 기록 가능
        if current_time.time() < self.overnight_morning_protection_end:
            return False

        # 이미 기록되어 있으면 스킵
        if position.get('open_range_high') and position.get('open_range_low'):
            return True

        try:
            # 09:00~09:30 데이터 필터링
            if 'datetime' not in df.columns and df.index.name != 'datetime':
                # datetime 인덱스 또는 컬럼이 없으면 시간 기반 필터링 불가
                console.print("[dim]⚠️ Open Range 기록 실패: datetime 컬럼 없음[/dim]")
                return False

            # datetime 컬럼 사용
            if 'datetime' in df.columns:
                df_morning = df[
                    (df['datetime'].dt.time >= time(9, 0)) &
                    (df['datetime'].dt.time <= time(9, 30))
                ]
            else:
                # datetime 인덱스 사용
                df_morning = df.between_time('09:00', '09:30')

            if len(df_morning) == 0:
                console.print("[dim]⚠️ Open Range 기록 실패: 09:00~09:30 데이터 없음[/dim]")
                return False

            # Open Range 계산
            open_range_high = df_morning['high'].max()
            open_range_low = df_morning['low'].min()

            position['open_range_high'] = open_range_high
            position['open_range_low'] = open_range_low

            console.print(
                f"[cyan]📊 Open Range 기록: "
                f"High {open_range_high:,.0f} / Low {open_range_low:,.0f} "
                f"(Range: {open_range_high - open_range_low:,.0f})[/cyan]"
            )

            return True

        except Exception as e:
            console.print(f"[red]⚠️ Open Range 기록 오류: {e}[/red]")
            return False

    def get_trade_result_tag(
        self,
        profit_pct: float,
        hold_hours: float,
        exit_reason: str
    ) -> str:
        """
        거래 결과 태그 생성 (로그/리포트용)

        Args:
            profit_pct: 수익률
            hold_hours: 보유 시간
            exit_reason: 청산 사유

        Returns:
            결과 태그 문자열
        """
        result = self.classify_trade_result(profit_pct, hold_hours)

        if result == 'DRAW':
            return f"🔘 DRAW ({profit_pct:+.2f}%, {hold_hours:.1f}h)"
        elif result == 'WIN':
            return f"✅ WIN ({profit_pct:+.2f}%, {hold_hours:.1f}h)"
        else:
            return f"❌ LOSS ({profit_pct:+.2f}%, {hold_hours:.1f}h)"
