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

from datetime import datetime, time
from typing import Dict, Tuple, Optional
import pandas as pd
from rich.console import Console

console = Console()


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
        청산 신호 체크

        Args:
            position: 포지션 정보 dict
            current_price: 현재가
            df: 기술적 지표가 포함된 DataFrame

        Returns:
            (should_exit, exit_reason, additional_info)
        """

        # ========================================
        # 0. 데이터 검증 및 초기화
        # ========================================

        # entry_price 안전 추출 (바이너리 데이터 버그 방지)
        entry_price = self._safe_get_price(position, 'entry_price')
        if entry_price <= 0:
            console.print(f"[red]⚠️ 비정상 진입가: {position.get('entry_price')}[/red]")
            return False, "ERROR_INVALID_ENTRY_PRICE", None

        # 수익률 계산
        profit_pct = ((current_price - entry_price) / entry_price) * 100

        # 보유 시간 계산
        entry_time = position.get('entry_time') or position.get('entry_date')
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)

        # 🔧 2026-01-27: 오버나잇 포지션 여부 판단
        is_overnight_position = False
        if entry_time:
            entry_date = entry_time.date() if hasattr(entry_time, 'date') else entry_time
            today = datetime.now().date()
            is_overnight_position = entry_date < today

        # 🔧 2026-01-27: 09:00~09:30 보호 구간 체크
        current_time = datetime.now().time()
        in_morning_protection = (
            self.overnight_exit_enabled and
            is_overnight_position and
            self.overnight_morning_protection_start <= current_time <= self.overnight_morning_protection_end
        )

        if in_morning_protection:
            # 오버나잇 포지션 + 09:00~09:30 = ATR 트레일링 비활성화
            console.print(
                f"[cyan]🛡️ 오버나잇 보호: {entry_time.strftime('%m/%d')} 진입 → "
                f"09:00~09:30 ATR 트레일링 비활성화[/cyan]"
            )
            # 보호 구간에서는 Hard Stop만 적용 (아래에서 처리)

        # 최고가 업데이트
        highest_price = position.get('highest_price', entry_price)
        if current_price > highest_price:
            highest_price = current_price
            position['highest_price'] = highest_price

        # 🔧 FIX: 문서 명세에 따른 청산 우선순위 재정렬

        # ========================================
        # -1순위: 동적 min_hold_time 락 (변동성 기반)
        # ========================================
        entry_time = position.get('entry_time')
        elapsed_minutes = 0
        if entry_time:
            elapsed_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # 스퀴즈 색상 먼저 확인 (동적 계산 및 예외 판단용)
        sqz_color = None
        try:
            from utils.squeeze_momentum_realtime import check_squeeze_momentum_filter
            _, _, sqz_details = check_squeeze_momentum_filter(df, for_entry=False)
            sqz_color = sqz_details.get('color', 'gray')
        except Exception:
            sqz_color = 'gray'

        # 🔒 동적 min_hold_time 계산 (변동성 + 스퀴즈 상태 + 조건부 연장)
        absolute_lock_minutes = self.calculate_dynamic_min_hold_time(
            df=df,
            current_price=current_price,
            squeeze_color=sqz_color,
            position=position
        )
        in_absolute_lock = elapsed_minutes < absolute_lock_minutes

        if in_absolute_lock:
            # 예외 조건: 복합 붕괴 시그널 (AND 조건)
            squeeze_collapse = sqz_color in ['dark_red', 'bright_red']
            vwap_broken = False

            # VWAP 하향 이탈 체크
            try:
                if 'vwap' in df.columns:
                    vwap = df['vwap'].iloc[-1]
                    if current_price < vwap * 0.995:  # 0.5% 이상 이탈
                        vwap_broken = True
            except Exception:
                pass

            # 복합 붕괴: Squeeze DR/BR + VWAP 이탈
            if squeeze_collapse and vwap_broken:
                console.print(
                    f"[red]🔓 락 예외: 복합 붕괴 시그널 "
                    f"(스퀴즈 {sqz_color} + VWAP 이탈, {elapsed_minutes:.1f}분)[/red]"
                )
            # 🔧 2026-02-07: Early Failure Structure 예외 (동적 락 내 5~15분)
            else:
                ef_exit, ef_reason, ef_info = self.check_early_failure_structure(
                    position=position,
                    current_price=current_price,
                    df=df,
                    elapsed_minutes=elapsed_minutes
                )
                if ef_exit and ef_info:
                    action = ef_info.get('action', 'exit_market')
                    console.print(
                        f"[red]🔓 락 예외: Early Failure 구조 "
                        f"(score={ef_info.get('score')}, {elapsed_minutes:.1f}분)[/red]"
                    )
                    if action == 'exit_market':
                        return True, ef_reason, ef_info
                    elif action == 'reduce_half':
                        ef_info['partial_exit'] = True
                        ef_info['exit_ratio'] = 0.5
                        ef_info['stage'] = 98
                        return False, ef_reason, ef_info
                    elif action == 'trail_stop':
                        position['trailing_active'] = True
                        position['trailing_stop_price'] = current_price * 0.995
                        console.print(
                            f"[yellow]🔄 Early Failure 구조: 타이트 트레일링 전환 "
                            f"(스탑: {current_price * 0.995:,.0f}원)[/yellow]"
                        )
                # 단순 DR/BR만 있으면 경고만
                elif squeeze_collapse:
                    console.print(
                        f"[yellow]⚠️ 스퀴즈 {sqz_color} 전환 감지, VWAP 정상 - 홀딩 유지[/yellow]"
                    )
                    return False, f"동적 락 ({elapsed_minutes:.1f}분, 단순 DR 경고)", None
                else:
                    console.print(
                        f"[cyan]🔒 동적 락 ({elapsed_minutes:.1f}/{absolute_lock_minutes}분) "
                        f"- 청산 불가 (스퀴즈: {sqz_color})[/cyan]"
                    )
                    return False, f"동적 락 ({elapsed_minutes:.1f}/{absolute_lock_minutes}분)", None

        # 🔧 Phase 3: 최소 보유 시간 이전에는 손절 금지 (하드 스톱 제외)
        below_min_hold = False
        if self.min_hold_enabled and elapsed_minutes < self.min_hold_minutes:
            below_min_hold = True

        # ========================================
        # -0.5순위: 20분 경과 후 MFE < 0.5% 강제 청산 (No-Move Exit)
        # 🔧 2026-03-05: 실데이터 기반 (10분MFE 0.43%, 30분MFE 0.70%)
        # 20분 지났는데 0.5% 못 찍으면 → 힘 없는 거래 조기 탈출
        # ========================================
        no_move_exit_enabled = self.config.get('risk_control.no_move_exit.enabled', True)
        no_move_minutes = self.config.get('risk_control.no_move_exit.minutes', 20)
        no_move_mfe_threshold = self.config.get('risk_control.no_move_exit.mfe_pct', 0.5)
        if (no_move_exit_enabled and entry_time
                and elapsed_minutes >= no_move_minutes
                and not position.get('no_move_exit_checked')):
            position['no_move_exit_checked'] = True  # 1회만 체크
            mfe_pct = (highest_price - entry_price) / entry_price * 100
            if mfe_pct < no_move_mfe_threshold and current_price < entry_price * 1.003:
                reason = (
                    f"No-Move Exit ({elapsed_minutes:.0f}분 경과, "
                    f"MFE {mfe_pct:.2f}% < {no_move_mfe_threshold}%)"
                )
                logger.info(f"[NO_MOVE_EXIT] {reason}")
                console.print(f"[yellow]⏱️ {reason}[/yellow]")
                return True, reason, {'no_move_exit': True}

        # ========================================
        # 0순위: Early Failure Cut (V2: 45분 + 다중조건)
        # ========================================
        if self.early_failure_enabled and entry_time:

            # 🔥 V2 로직: 45분 + HTF CHoCH 무효화 + VWAP -2.5% + LL 확정
            if USE_EXIT_V2:
                # V2 조건 1: 최소 45분 경과 필수
                if elapsed_minutes < V2_EARLY_FAILURE_MIN_MINUTES:
                    console.print(
                        f"[cyan]🔒 Early Failure v2: 보호 구간 "
                        f"({elapsed_minutes:.1f}/{V2_EARLY_FAILURE_MIN_MINUTES}분)[/cyan]"
                    )
                    # 45분 미경과 시 Early Failure 절대 금지
                else:
                    # V2 조건 체크 (모두 충족해야 손절)
                    v2_conditions = {
                        'min_time_passed': elapsed_minutes >= V2_EARLY_FAILURE_MIN_MINUTES,
                        'htf_choch_invalidated': False,
                        'vwap_broken': False,
                        'll_confirmed': False
                    }

                    # 조건 2: HTF CHoCH 무효화 체크
                    entry_direction = position.get('direction', 'long')
                    try:
                        from analyzers.smc.smc_structure import SMCStructureAnalyzer
                        analyzer = SMCStructureAnalyzer()
                        df_cols = df.copy()
                        df_cols.columns = [c.lower() for c in df_cols.columns]
                        structure = analyzer.analyze_structure(df_cols)
                        choch = analyzer.detect_choch(df_cols, structure)

                        if choch:
                            if entry_direction == 'long' and choch.direction == 'bearish':
                                v2_conditions['htf_choch_invalidated'] = True
                            elif entry_direction == 'short' and choch.direction == 'bullish':
                                v2_conditions['htf_choch_invalidated'] = True
                    except Exception:
                        pass

                    # 조건 3: VWAP -2.5% 이탈
                    try:
                        if 'vwap' in df.columns:
                            vwap = df['vwap'].iloc[-1]
                            vwap_deviation = ((current_price / vwap) - 1) * 100
                            if vwap_deviation <= V2_EARLY_FAILURE_LOSS_PCT:
                                v2_conditions['vwap_broken'] = True
                    except Exception:
                        pass

                    # 조건 4: LL(Lower Low) 확정
                    try:
                        if len(df) >= 20:
                            recent_lows = df['low'].tail(20)
                            current_low = df['low'].iloc[-1]
                            prior_low = recent_lows.iloc[:-1].min()
                            if current_low < prior_low:
                                v2_conditions['ll_confirmed'] = True
                    except Exception:
                        pass

                    # 모든 조건 충족 시에만 손절
                    all_v2_conditions_met = all(v2_conditions.values())

                    if all_v2_conditions_met and profit_pct <= V2_EARLY_FAILURE_LOSS_PCT:
                        return True, f"🚨 Early Failure v2 ({profit_pct:.2f}%, 모든 조건 충족)", {
                            'profit_pct': profit_pct,
                            'use_market_order': True,
                            'emergency': True,
                            'reason': 'EARLY_FAILURE_CUT_V2',
                            'v2_conditions': v2_conditions
                        }
                    else:
                        # 조건 미충족 시 상태 로깅
                        unmet = [k for k, v in v2_conditions.items() if not v]
                        console.print(
                            f"[yellow]⚠️ Early Failure v2: 관찰 중 "
                            f"(미충족: {', '.join(unmet)})[/yellow]"
                        )

            # 🔄 V1 로직 (USE_EXIT_V2 = False 시)
            else:
                # 구조 기반 손절 조건 체크
                structure_broken = False
                structure_reason = ""

                # 조건 1: CHoCH 반전 (SMC 구조 붕괴)
                entry_direction = position.get('direction', 'long')

                try:
                    from analyzers.smc import SMCStrategy
                    from analyzers.smc.smc_structure import SMCStructureAnalyzer

                    analyzer = SMCStructureAnalyzer()
                    df_cols = df.copy()
                    df_cols.columns = [c.lower() for c in df_cols.columns]
                    structure = analyzer.analyze_structure(df_cols)
                    choch = analyzer.detect_choch(df_cols, structure)

                    if choch:
                        if entry_direction == 'long' and choch.direction == 'bearish':
                            structure_broken = True
                            structure_reason = "CHoCH 하락 전환"
                        elif entry_direction == 'short' and choch.direction == 'bullish':
                            structure_broken = True
                            structure_reason = "CHoCH 상승 전환"
                except Exception:
                    pass

                # 조건 2: VWAP 완전 이탈 (-0.8% 이상)
                vwap_broken = False
                try:
                    if 'vwap' in df.columns:
                        vwap = df['vwap'].iloc[-1]
                        if current_price < vwap * 0.992:
                            vwap_broken = True
                            if not structure_broken:
                                structure_reason = f"VWAP 완전 이탈 ({((current_price/vwap)-1)*100:.2f}%)"
                except Exception:
                    pass

                # 구조 붕괴 시에만 Early Failure 적용
                if (structure_broken or vwap_broken) and profit_pct <= self.early_failure_loss:
                    combined_reason = structure_reason or "구조 붕괴"
                    return True, f"🚨 Early Failure Cut ({combined_reason}, {profit_pct:.2f}%)", {
                        'profit_pct': profit_pct,
                        'use_market_order': True,
                        'emergency': True,
                        'reason': 'EARLY_FAILURE_CUT_STRUCTURE',
                        'structure_broken': structure_broken,
                        'vwap_broken': vwap_broken
                    }

        # ========================================
        # 1순위: Hard Stop → 전량 시장가 손절 (문서 명세)
        # ========================================
        # ⚠️ 30분 절대 락으로 비활성화됨
        # 🔴 GPT 개선: 부분 청산 후 손절가 상향 (BE 보호)
        partial_stage = position.get('partial_exit_stage', 0)
        adjusted_hard_stop = self.hard_stop_pct

        # 🔧 2026-02-06: 구조 기반 손절 (SMC 전략)
        structure_stop_price = position.get('structure_stop_price')
        use_structure_stop = False
        struct_stop_config = self.risk_control.get('structure_based_stop', {})

        if structure_stop_price and struct_stop_config.get('enabled', True):
            use_structure_stop = True
            max_stop_pct = struct_stop_config.get('max_stop_pct', 3.0)

            # 안전장치: 구조 손절이 max_stop_pct 초과하면 cap
            structure_loss_pct = ((entry_price - structure_stop_price) / entry_price) * 100
            if structure_loss_pct > max_stop_pct:
                structure_stop_price = entry_price * (1 - max_stop_pct / 100)
                console.print(
                    f"[yellow]⚠️ 구조 손절 cap 적용: {structure_loss_pct:.1f}% → -{max_stop_pct}% "
                    f"({structure_stop_price:,.0f}원)[/yellow]"
                )

        # 🔧 2026-01-20: 당일 매수 종목 강화 손절 적용
        is_same_day_entry = False
        same_day_label = ""
        if self.same_day_enabled and entry_time:
            entry_date = entry_time.date() if hasattr(entry_time, 'date') else entry_time
            today = datetime.now().date()
            if entry_date == today:
                is_same_day_entry = True
                if not use_structure_stop:
                    adjusted_hard_stop = self.same_day_stop_loss_pct  # 당일 매수: 타이트한 손절 (기본 1.5%)
                    same_day_label = " [당일매수강화]"
                    console.print(f"[yellow]⚡ 당일 매수 강화 손절 적용: -{self.same_day_stop_loss_pct}%[/yellow]")

        if partial_stage >= 1:  # 1차 부분 청산 후
            adjusted_hard_stop = 0.3  # -0.3% (사실상 BE)
            use_structure_stop = False  # 부분 청산 후에는 BE 보호 우선
        if partial_stage >= 2:  # 2차 부분 청산 후
            adjusted_hard_stop = -0.2  # +0.2% 보장 (손절 → 익절로 전환)
            use_structure_stop = False

        # 🔧 2026-02-06: 구조 기반 손절 체크
        if use_structure_stop and structure_stop_price:
            if current_price <= structure_stop_price:
                structure_loss_pct = ((current_price - entry_price) / entry_price) * 100
                console.print(
                    f"[red]📍 구조 손절 발동: 현재가 {current_price:,.0f} <= "
                    f"구조 손절가 {structure_stop_price:,.0f} ({structure_loss_pct:+.2f}%)[/red]"
                )
                return True, f"구조 손절 ({structure_stop_price:,.0f}원, {profit_pct:.2f}%){same_day_label}", {
                    'profit_pct': profit_pct,
                    'use_market_order': True,
                    'emergency': True,
                    'is_same_day_entry': is_same_day_entry,
                    'structure_stop_price': structure_stop_price
                }

        # 기존 퍼센트 기반 Hard Stop (구조 손절이 없거나 부분 청산 후)
        if profit_pct <= -adjusted_hard_stop:
            return True, f"Hard Stop (-{adjusted_hard_stop}%, {profit_pct:.2f}%){same_day_label} [부분청산 {partial_stage}차]", {
                'profit_pct': profit_pct,
                'use_market_order': True,  # 시장가 플래그
                'emergency': True,
                'is_same_day_entry': is_same_day_entry
            }

        # ========================================
        # 2-2순위: R-기반 부분 익절 (TP1=1.5R/50%, TP2=3R/나머지50%)
        # ========================================
        r_tp1_price = position.get('r_tp1_price')
        r_tp2_price = position.get('r_tp2_price')
        if r_tp1_price and r_tp2_price and not below_min_hold:
            entry_price = position.get('entry_price', current_price)
            if entry_price > 0:
                tp1_pct = (r_tp1_price - entry_price) / entry_price * 100
                tp2_pct = (r_tp2_price - entry_price) / entry_price * 100
                partial_stage = position.get('partial_exit_stage', 0)
                # 상태 기반 체크: TP1 미달성이면 TP2 체크 안 함 (부분익절 스킵 방지)
                if partial_stage < 1 and profit_pct >= tp1_pct:
                    return False, f"[R_TP1] +{profit_pct:.1f}% ≥ 1.5R({tp1_pct:.1f}%) → 50% 부분익절", {
                        'partial_exit': True, 'stage': 1, 'exit_ratio': 0.5, 'profit_pct': profit_pct
                    }
                elif partial_stage == 1 and profit_pct >= tp2_pct:
                    return False, f"[R_TP2] +{profit_pct:.1f}% ≥ 3R({tp2_pct:.1f}%) → 잔여 50% 청산", {
                        'partial_exit': True, 'stage': 2, 'exit_ratio': 0.5, 'profit_pct': profit_pct
                    }

        # ========================================
        # 2-2.5순위: TIME EXIT — N봉 후 0.5R 미달 → 횡보 포지션 강제 청산
        #  A급(eq=A AND choch=A): 일반 TIME_EXIT 면제
        #                        단, 소프트 상한(max_bars_mult×) 초과 시 강제 종료
        # ========================================
        _r_pct         = position.get('r_pct', 0)
        _time_exit_cfg = self.config.get('risk_control.time_exit', {})
        _a_ext_cfg     = self.config.get('smc.a_grade_hold_extension', {})

        _is_a_grade = (
            _a_ext_cfg.get('enabled', False) and
            _r_pct > 0 and
            position.get('eq_grade') == 'A' and
            position.get('choch_grade_log') == 'A'
        )
        _te_exempt        = _is_a_grade and _a_ext_cfg.get('exempt_time_exit', False)
        _te_bars          = _time_exit_cfg.get('bars', 10)
        _bars_since_entry = int(elapsed_minutes / 5) if entry_time else 0

        # A급 소프트 상한: time_exit.enabled 여부와 무관하게 항상 독립 실행
        # (enabled:false 로 꺼도 최대 보유 한도는 살아있어야 계좌 묶임 방지)
        if _te_exempt and entry_time and not below_min_hold:
            _mult  = _a_ext_cfg.get('max_bars_mult', 2)
            _a_max = _te_bars * _mult
            if _bars_since_entry >= _a_max:
                _af_reason = (
                    f"[A_FORCE_EXIT] A급 연장 최대 보유 초과 "
                    f"({_bars_since_entry}봉 ≥ {_a_max}봉, pnl={profit_pct:+.2f}%)"
                )
                logger.info(_af_reason)
                return True, _af_reason, {'a_force_exit': True, 'profit_pct': profit_pct}

        # 일반 TIME_EXIT (B/C급, 또는 A급이지만 exempt 아닌 경우)
        if (
            not _te_exempt and
            _time_exit_cfg.get('enabled', True) and
            _r_pct > 0 and entry_time and not below_min_hold
        ):
            _te_r_thr        = _time_exit_cfg.get('r_threshold', 0.5)
            _r_threshold_pct = _r_pct * _te_r_thr
            if _bars_since_entry >= _te_bars and profit_pct < _r_threshold_pct:
                _te_reason = (
                    f"[TIME_EXIT] {_bars_since_entry}봉({elapsed_minutes:.0f}분) 경과, "
                    f"수익 {profit_pct:.2f}% < 0.5R({_r_threshold_pct:.2f}%)"
                )
                logger.info(_te_reason)
                return True, _te_reason, {'time_exit': True, 'profit_pct': profit_pct}

        # ========================================
        # 2-2.7순위: A급 연장 보호 장치 3종 (TIME_EXIT 면제 대가)
        #  ① STRUCTURE_EXIT 2단계: VWAP 이탈(50% 축소) → bearish CHoCH(전량 청산)
        #  ② PROFIT_LOCK 티어형: MFE 커질수록 보호선 올라감
        # ========================================
        if _is_a_grade and _te_exempt:
            _r_amount = entry_price * _r_pct / 100  # 1R in 원 단위

            # ① STRUCTURE_EXIT 2단계 ─────────────────────────────────────────
            if _a_ext_cfg.get('structure_exit', True):
                _struct_stage = position.get('a_struct_exit_stage', 0)

                # Stage 1: VWAP 이탈 → 50% 부분 청산 (선제 대응)
                if _struct_stage == 0 and _a_ext_cfg.get('structure_weak_vwap', True):
                    try:
                        if 'vwap' in df.columns:
                            _vwap = df['vwap'].iloc[-1]
                            _vwap_thr = _a_ext_cfg.get('structure_weak_vwap_pct', 0.2)
                            if _vwap > 0 and current_price < _vwap * (1 - _vwap_thr / 100):
                                position['a_struct_exit_stage'] = 1
                                _sw_reason = (
                                    f"[STRUCT_WEAK] A급 연장 VWAP 이탈 "
                                    f"({((current_price / _vwap) - 1) * 100:.2f}%) → 50% 축소"
                                )
                                logger.info(_sw_reason)
                                return False, _sw_reason, {
                                    'partial_exit': True, 'stage': 'struct_weak',
                                    'exit_ratio': 0.5, 'profit_pct': profit_pct,
                                    'struct_reduce': True,
                                }
                    except Exception:
                        pass

                # Stage 2: bearish CHoCH 확정 → 전량 청산
                try:
                    from analyzers.smc.smc_structure import SMCStructureAnalyzer
                    _sa     = SMCStructureAnalyzer()
                    _df_tmp = df.copy()
                    _df_tmp.columns = [c.lower() for c in _df_tmp.columns]
                    _struct = _sa.analyze_structure(_df_tmp)
                    _choch  = _sa.detect_choch(_df_tmp, _struct)
                    if _choch and _choch.direction == 'bearish':
                        _se_reason = (
                            f"[STRUCTURE_EXIT] A급 연장 bearish CHoCH 확정 "
                            f"(pnl={profit_pct:+.2f}%)"
                        )
                        logger.info(_se_reason)
                        return True, _se_reason, {
                            'structure_exit': True, 'profit_pct': profit_pct
                        }
                except Exception:
                    pass

            # ② PROFIT_LOCK 티어형 ────────────────────────────────────────────
            _mfe_pct = (
                (highest_price - entry_price) / entry_price * 100
                if highest_price > entry_price else 0.0
            )
            _mfe_r = _mfe_pct / _r_pct if _r_pct > 0 else 0.0

            _default_tiers = [
                {'mfe_r': 1.5, 'floor_r': 0.5},
                {'mfe_r': 2.5, 'floor_r': 1.0},
                {'mfe_r': 4.0, 'floor_r': 2.0},
            ]
            _tiers = _a_ext_cfg.get('profit_lock_tiers', _default_tiers)

            # 달성한 MFE_R 중 가장 높은 티어의 보호선 적용
            _lock_floor_price = None
            _active_tier      = None
            for _tier in reversed(_tiers):
                if _mfe_r >= _tier['mfe_r']:
                    _lock_floor_price = entry_price + _tier['floor_r'] * _r_amount
                    _active_tier      = _tier
                    break

            if _lock_floor_price is not None and current_price < _lock_floor_price:
                _lock_floor_pct = (_lock_floor_price - entry_price) / entry_price * 100
                _pl_reason = (
                    f"[PROFIT_LOCK] A급 연장 수익락 이탈 "
                    f"(MFE={_mfe_r:.1f}R → 보호선=+{_lock_floor_pct:.2f}%, "
                    f"현재={profit_pct:+.2f}%)"
                )
                logger.info(_pl_reason)
                return True, _pl_reason, {
                    'profit_lock': True, 'profit_pct': profit_pct,
                    'lock_tier': _active_tier,
                }

            # ③ NO_PROGRESS_EXIT — Profit Lock 미도달 + 장기 횡보 컷
            # "잘 가는 놈만 오래 들고 간다": MFE가 1.5R 못 넘으면 1.5배 시점에 정리
            _np_mfe_r  = _a_ext_cfg.get('no_progress_mfe_r',  1.5)
            _np_max    = _a_ext_cfg.get('no_progress_bars', 15)
            if _mfe_r < _np_mfe_r and _bars_since_entry >= _np_max:
                _np_reason = (
                    f"[NO_PROGRESS_EXIT] A급 연장 진행 없음 "
                    f"(MFE={_mfe_r:.2f}R < {_np_mfe_r}R, "
                    f"{_bars_since_entry}봉 ≥ {_np_max:.0f}봉, "
                    f"pnl={profit_pct:+.2f}%)"
                )
                logger.info(_np_reason)
                return True, _np_reason, {'no_progress_exit': True, 'profit_pct': profit_pct}

        # ========================================
        # 2-3순위: 부분 청산 (문서 명세: +4%/40%, +6%/40%)
        # ========================================
        # 🔧 FIX: 최소 보유 시간 체크 추가 (초단타 방지)
        if self.partial_exit_enabled and not below_min_hold:
            partial_stage = position.get('partial_exit_stage', 0)

            # 역순으로 체크 (높은 수익부터)
            for idx, tier in enumerate(reversed(self.partial_tiers), start=1):
                tier_num = len(self.partial_tiers) - idx + 1

                if partial_stage < tier_num and profit_pct >= tier['profit_pct']:
                    return False, f"부분청산 {tier_num}차 준비 (+{tier['profit_pct']}%, {tier['exit_ratio']*100:.0f}%)", {
                        'partial_exit': True,
                        'stage': tier_num,
                        'exit_ratio': tier['exit_ratio'],
                        'profit_pct': profit_pct
                    }

        # ========================================
        # 3.5순위: Squeeze Momentum 청산 필터 (설정 활성화 시)
        # ========================================
        # 실전 분석 기반 색상별 액션:
        # - Bright Green: 절대 보유 (아이티센글로벌 교훈)
        # - Dark Green: 부분 익절 권장 (휴림로봇 성공)
        # - Red: 전량 청산 권장

        # position에서 설정 가져오기 (self.config가 없으면 건너뛰기)
        if hasattr(self, 'config'):
            squeeze_config = self.config.get('squeeze_momentum', {})
        else:
            squeeze_config = {}

        if squeeze_config.get('enabled', False) and squeeze_config.get('exit_filter', {}).get('enabled', False):
            from utils.squeeze_momentum_realtime import check_squeeze_momentum_filter

            try:
                sqz_passed, sqz_reason, sqz_details = check_squeeze_momentum_filter(df, for_entry=False)
                sqz_color = sqz_details.get('color', 'gray')

                # Bright Green: 강제 보유 (설정 활성화 시)
                if sqz_color == 'bright_green' and squeeze_config.get('exit_filter', {}).get('bright_green', {}).get('force_hold', False):
                    # 트레일링 스탑은 유지할지 확인
                    ignore_trailing = squeeze_config.get('exit_filter', {}).get('bright_green', {}).get('ignore_trailing_stop', False)

                    if not ignore_trailing:
                        # 트레일링 스탑만 허용, 다른 청산은 차단
                        console.print("[cyan]🟢 Squeeze: Bright Green - 보유 강제 (트레일링만 허용)[/cyan]")
                        # 트레일링 스탑 체크는 다음 단계에서 진행
                    else:
                        # 모든 청산 차단
                        console.print("[cyan]🟢 Squeeze: Bright Green - 보유 강제 (청산 금지)[/cyan]")
                        return False, "Squeeze: Bright Green 보유 필수", None

                # Dark Green: 부분 익절 권장 (수익 중일 때만)
                elif sqz_color == 'dark_green':
                    dark_green_config = squeeze_config.get('exit_filter', {}).get('dark_green', {})
                    if dark_green_config.get('enabled', False):
                        min_profit = dark_green_config.get('min_profit_pct', 1.0)

                        if profit_pct >= min_profit:
                            exit_ratio = dark_green_config.get('partial_exit_ratio', 0.3)
                            console.print(f"[yellow]🟡 Squeeze: Dark Green - 부분 익절 권장 ({exit_ratio*100:.0f}%)[/yellow]")
                            return False, f"Squeeze: Dark Green 부분익절 ({profit_pct:+.2f}%)", {
                                'partial_exit': True,
                                'stage': 99,  # 특수 스퀴즈 청산 단계
                                'exit_ratio': exit_ratio,
                                'profit_pct': profit_pct,
                                'reason': 'SQUEEZE_DARK_GREEN'
                            }

                # Red (dark_red/bright_red): 전량 청산 권장
                elif sqz_color in ['dark_red', 'bright_red']:
                    red_config = squeeze_config.get('exit_filter', {}).get('red', {})
                    if red_config.get('enabled', False) and red_config.get('full_exit', False):
                        min_profit = red_config.get('min_profit_pct', 0.5)

                        if profit_pct >= min_profit:
                            console.print(f"[red]🔴 Squeeze: {sqz_color} - 전량 청산 권장[/red]")
                            return True, f"Squeeze: {sqz_color} 모멘텀 반전 ({profit_pct:+.2f}%)", {
                                'profit_pct': profit_pct,
                                'reason': 'SQUEEZE_RED_REVERSAL'
                            }

            except Exception as e:
                console.print(f"[dim]⚠️ Squeeze Momentum 청산 필터 오류: {e}[/dim]")
                # 에러 시 무시하고 계속 진행

        # ========================================
        # 4순위: ATR 트레일링 스탑 (V2: 단계화)
        # ========================================
        # 🔥 V2: +0~2% OFF / +2~4% ATR×3.0 / +4%+ ATR×2.0

        # 🔧 2026-01-27: 오버나잇 보호 구간에서는 ATR 트레일링 비활성화
        if in_morning_protection:
            console.print(
                f"[cyan]🛡️ 오버나잇 보호 중: ATR 트레일링 스킵 "
                f"(09:00~09:30, 수익률: {profit_pct:+.2f}%)[/cyan]"
            )
        else:
            # 🔥 V2 ATR 트레일링 단계화
            if USE_EXIT_V2:
                # V2: 수익률별 ATR 배수 결정
                if profit_pct < V2_ATR_STAGE1_PROFIT:
                    # +2% 미만: 트레일링 OFF (구조 기준만)
                    atr_multiplier = None  # 비활성화
                    console.print(
                        f"[cyan]📊 ATR v2: OFF (수익 {profit_pct:+.2f}% < +{V2_ATR_STAGE1_PROFIT}%)[/cyan]"
                    )
                elif profit_pct < V2_ATR_STAGE2_PROFIT:
                    # +2% ~ +4%: ATR × 3.0 (느슨)
                    atr_multiplier = V2_ATR_STAGE1_MULT
                    console.print(
                        f"[yellow]📊 ATR v2: ×{atr_multiplier} 느슨 "
                        f"(수익 {profit_pct:+.2f}% in +{V2_ATR_STAGE1_PROFIT}~{V2_ATR_STAGE2_PROFIT}%)[/yellow]"
                    )
                else:
                    # +4% 이상: ATR × 2.0 (타이트)
                    atr_multiplier = V2_ATR_STAGE2_MULT
                    console.print(
                        f"[green]📊 ATR v2: ×{atr_multiplier} 타이트 "
                        f"(수익 {profit_pct:+.2f}% >= +{V2_ATR_STAGE2_PROFIT}%)[/green]"
                    )

                # ATR 트레일링 적용 (atr_multiplier가 설정된 경우만)
                if atr_multiplier is not None:
                    position['trailing_active'] = True

                    # ATR 값 가져오기
                    atr_value = 0
                    try:
                        if 'atr' in df.columns and len(df) > 0:
                            atr_value = df['atr'].iloc[-1]
                    except Exception:
                        atr_value = entry_price * 0.02  # 기본값 2%

                    # V2 트레일링 스탑 계산: 고가 - ATR × 배수
                    trailing_stop_price = highest_price - (atr_value * atr_multiplier)

                    # 최소 잠금 수익 보장
                    min_lock_price = entry_price * (1 + self.trailing_min_lock / 100)
                    trailing_stop_price = max(trailing_stop_price, min_lock_price)

                    position['trailing_stop_price'] = trailing_stop_price
                    position['atr_multiplier'] = atr_multiplier

                    # 트레일링 스탑 발동 체크
                    if current_price <= trailing_stop_price:
                        return True, f"ATR 트레일링 v2 (×{atr_multiplier}, {profit_pct:+.2f}%)", {
                            'profit_pct': profit_pct,
                            'highest_price': highest_price,
                            'trailing_stop_price': trailing_stop_price,
                            'atr_multiplier': atr_multiplier
                        }

            # 🔄 V1 로직 (USE_EXIT_V2 = False 시)
            else:
                if position.get('trailing_active') or (profit_pct >= self.trailing_activation and not below_min_hold):
                    position['trailing_active'] = True

                    # 🔧 2026-01-20: 당일 매수 종목은 타이트한 트레일링 적용
                    # 🔧 2026-04-01: A+ 포지션
                    #   TP 미도달: 넓게(1.0%) → 추세 유지
                    #   TP 도달 후: 좁게(0.5%) → 수익 잠금
                    trailing_dist = self.trailing_distance
                    if position.get('a_plus_mode', False):
                        if position.get('a_plus_tp_hit', False):
                            trailing_dist = 0.5   # TP 후: tight (수익 잠금)
                        else:
                            trailing_dist = max(self.trailing_distance, 1.0)  # TP 전: wide
                    elif is_same_day_entry and self.same_day_enabled:
                        trailing_dist = self.same_day_trailing_ratio
                        console.print(f"[yellow]⚡ 당일 매수 타이트 트레일링: {trailing_dist}%[/yellow]")

                    # 🔧 2026-01-27: 오버나잇 포지션 + 09:30 이후 = Open Range 기반 스탑
                    if is_overnight_position and self.overnight_use_open_range:
                        open_range_high = position.get('open_range_high')
                        open_range_low = position.get('open_range_low')

                        if open_range_high and open_range_low:
                            open_range = open_range_high - open_range_low
                            overnight_stop = open_range_low - (open_range * self.overnight_open_range_multiplier)

                            if current_price <= overnight_stop:
                                console.print(
                                    f"[yellow]📉 오버나잇 Open Range 스탑 발동: "
                                    f"OR Low {open_range_low:,.0f} - {open_range * self.overnight_open_range_multiplier:,.0f} "
                                    f"= {overnight_stop:,.0f}[/yellow]"
                                )
                                return True, f"오버나잇 Open Range 스탑 ({profit_pct:+.2f}%)", {
                                    'profit_pct': profit_pct,
                                    'open_range_high': open_range_high,
                                    'open_range_low': open_range_low,
                                    'overnight_stop': overnight_stop
                                }

                    # 트레일링 스탑 라인 계산
                    trailing_stop_price = highest_price * (1 - trailing_dist / 100)

                    # 최소 잠금 수익 보장
                    min_lock_price = entry_price * (1 + self.trailing_min_lock / 100)
                    trailing_stop_price = max(trailing_stop_price, min_lock_price)

                    position['trailing_stop_price'] = trailing_stop_price

                    # 트레일링 스탑 발동 체크
                    if current_price <= trailing_stop_price:
                        return True, f"ATR 트레일링 스탑 ({profit_pct:+.2f}%)", {
                            'profit_pct': profit_pct,
                            'highest_price': highest_price,
                            'trailing_stop_price': trailing_stop_price
                        }

        # ========================================
        # 5순위: VWAP 청산 (V2: 60분 보호)
        # ========================================
        # 🔥 V2: 60분 내 VWAP 기반 손절 금지

        if profit_pct < self.vwap_profit_threshold:
            # V2: 60분 보호 규칙
            if USE_EXIT_V2:
                if elapsed_minutes < V2_VWAP_MIN_MINUTES:
                    console.print(
                        f"[cyan]🔒 VWAP v2 보호: {elapsed_minutes:.1f}/{V2_VWAP_MIN_MINUTES}분 "
                        f"(VWAP 손절 금지)[/cyan]"
                    )
                    # 60분 미경과 시 VWAP 손절 금지
                else:
                    vwap_exit_check = self._check_vwap_exit(df, current_price, profit_pct)
                    if vwap_exit_check[0]:
                        return vwap_exit_check
            else:
                # V1: 기존 로직
                vwap_exit_check = self._check_vwap_exit(df, current_price, profit_pct)
                if vwap_exit_check[0]:
                    return vwap_exit_check

        # ========================================
        # 5.5순위: 포지션 생명주기 (V2: D+1/D+3/D+5)
        # ========================================
        # 🔥 V2: 장기 보유 규칙 (스윙 트레이딩 최적화)

        if USE_EXIT_V2 and entry_time:
            entry_date = entry_time.date() if hasattr(entry_time, 'date') else entry_time
            today = datetime.now().date()
            holding_days = (today - entry_date).days

            # D+5: 무조건 전량 청산
            if V2_LIFECYCLE_D5_FORCE and holding_days >= 5:
                console.print(
                    f"[red]📅 생명주기 D+5: 강제 전량 청산 "
                    f"(보유 {holding_days}일, 수익 {profit_pct:+.2f}%)[/red]"
                )
                return True, f"생명주기 D+5 강제청산 ({profit_pct:+.2f}%)", {
                    'profit_pct': profit_pct,
                    'holding_days': holding_days,
                    'reason': 'LIFECYCLE_D5_FORCE'
                }

            # D+3: 수익률 기반 판단
            if holding_days >= 3:
                if profit_pct < V2_LIFECYCLE_D3_PROFIT_THRESHOLD:
                    # +0% ~ +3%: 전량 청산
                    console.print(
                        f"[yellow]📅 생명주기 D+3: 전량 청산 "
                        f"(보유 {holding_days}일, 수익 {profit_pct:+.2f}% < +{V2_LIFECYCLE_D3_PROFIT_THRESHOLD}%)[/yellow]"
                    )
                    return True, f"생명주기 D+3 청산 ({profit_pct:+.2f}%)", {
                        'profit_pct': profit_pct,
                        'holding_days': holding_days,
                        'reason': 'LIFECYCLE_D3_LOW_PROFIT'
                    }
                else:
                    # +3% 이상: 트레일링 강제 ON (위에서 ATR 처리됨)
                    console.print(
                        f"[green]📅 생명주기 D+3: 트레일링 강제 ON "
                        f"(보유 {holding_days}일, 수익 {profit_pct:+.2f}% >= +{V2_LIFECYCLE_D3_PROFIT_THRESHOLD}%)[/green]"
                    )
                    position['trailing_active'] = True

            # D+1: 익일 종가 기준 VWAP 체크 (50% 부분청산)
            if holding_days >= 1:
                try:
                    if 'vwap' in df.columns:
                        vwap = df['vwap'].iloc[-1]
                        if current_price < vwap:
                            # VWAP 하회 시 50% 부분청산 신호
                            console.print(
                                f"[yellow]📅 생명주기 D+1: VWAP 하회 → 50% 부분청산 권장 "
                                f"(보유 {holding_days}일)[/yellow]"
                            )
                            # 부분청산 신호 반환 (전량 청산 아님)
                            if not position.get('d1_partial_done'):
                                return False, f"생명주기 D+1 부분청산 ({profit_pct:+.2f}%)", {
                                    'partial_exit': True,
                                    'stage': 100,  # D+1 특수 단계
                                    'exit_ratio': V2_LIFECYCLE_D1_PARTIAL,
                                    'profit_pct': profit_pct,
                                    'reason': 'LIFECYCLE_D1_VWAP_BELOW'
                                }
                except Exception:
                    pass

        # ========================================
        # 6순위: 시간 기반 청산 (문서 명세: 15:00 이후 전량 청산)
        # 🔧 FIX: eod_policy.enabled가 True일 때만 작동
        # ========================================
        if self.time_based_exit_enabled:
            current_time = datetime.now().time()

            # 🔥 CRITICAL FIX: EOD Manager 익일 보유 결정 존중
            if position.get('allow_overnight_final_confirm', False):
                # 익일 보유 승인된 종목은 시간 기반 청산 제외
                console.print(f"[cyan]✓ 익일 보유 승인 종목 - 시간 청산 제외 (Score: {position.get('eod_score', 0):.2f})[/cyan]")
                return False, None, None

            # 15:00 - 전량 강제 청산 (익일 보유 제외)
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
