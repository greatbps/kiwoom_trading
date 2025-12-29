"""
최적화된 청산 로직 - 데이터 기반 손익비 개선

주요 개선사항:
1. 초기 실패 컷 추가 (30분 이내 -1.6%, 평균 손실 -2.03% 기반)
2. VWAP 단독 청산 권한 약화 (다중 조건 필요)
3. 트레일링 스탑 중심화
4. 시간 비교 버그 수정
5. DataFrame 컬럼 안전성 체크
"""

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

        # 시간 청산 설정
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
        except:
            return time(15, 0, 0)  # 기본값

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
            holding_minutes = (datetime.now() - entry_time).total_seconds() / 60
        else:
            holding_minutes = 0

        # 최고가 업데이트
        highest_price = position.get('highest_price', entry_price)
        if current_price > highest_price:
            highest_price = current_price
            position['highest_price'] = highest_price

        # 🔧 FIX: 문서 명세에 따른 청산 우선순위 재정렬

        # ========================================
        # -1순위: 최소 보유 시간 체크 (Phase 3: 초단타 방지)
        # ========================================
        entry_time = position.get('entry_time')
        elapsed_minutes = 0
        if entry_time:
            elapsed_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # 🔧 Phase 3: 최소 보유 시간 이전에는 손절 금지 (하드 스톱 제외)
        below_min_hold = False
        if self.min_hold_enabled and elapsed_minutes < self.min_hold_minutes:
            below_min_hold = True

        # ========================================
        # 0순위: Early Failure Cut (최우선!) - 30분 이내 -1.6%
        # ========================================
        # 📊 ML 개선 (2025-12-16): min_hold 체크 제거 - 손실 방지가 우선
        # 버그: min_hold로 인해 early_failure가 작동 안 함 (0-30분 동안 비활성화)
        # 결과: -1.6% 컷이 작동 안 하고 -2.5% 하드스탑까지 손실 확대
        if self.early_failure_enabled:  # min_hold 체크 제거!
            if entry_time:
                if elapsed_minutes <= self.early_failure_window:
                    if profit_pct <= self.early_failure_loss:  # -1.6% 이하
                        return True, f"🚨 Early Failure Cut ({elapsed_minutes:.1f}분, {profit_pct:.2f}%)", {
                            'profit_pct': profit_pct,
                            'use_market_order': True,  # 시장가 즉시 청산
                            'emergency': True,
                            'reason': 'EARLY_FAILURE_CUT'
                        }

        # ========================================
        # 1순위: Hard Stop → 전량 시장가 손절 (문서 명세)
        # 🔴 GPT 개선: 부분 청산 후 손절가 상향 (BE 보호)
        # ========================================
        # 부분 청산 단계에 따라 손절가 조정
        partial_stage = position.get('partial_exit_stage', 0)
        adjusted_hard_stop = self.hard_stop_pct

        if partial_stage >= 1:  # 1차 부분 청산 후
            adjusted_hard_stop = 0.3  # -0.3% (사실상 BE)
        if partial_stage >= 2:  # 2차 부분 청산 후
            adjusted_hard_stop = -0.2  # +0.2% 보장 (손절 → 익절로 전환)

        if profit_pct <= -adjusted_hard_stop:
            return True, f"Hard Stop (-{adjusted_hard_stop}%, {profit_pct:.2f}%) [부분청산 {partial_stage}차]", {
                'profit_pct': profit_pct,
                'use_market_order': True,  # 시장가 플래그
                'emergency': True
            }

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
        # 4순위: ATR 트레일링 스탑 (문서 명세: 고가 - ATR×2)
        # ========================================
        # 🔧 FIX: 최소 보유 시간 체크 추가 (초단타 방지)
        # 단, 이미 활성화된 경우는 계속 추적 (손실 방지)

        # 이미 트레일링이 활성화된 경우 OR 활성화 조건 충족 시
        if position.get('trailing_active') or (profit_pct >= self.trailing_activation and not below_min_hold):
            # 트레일링 활성화
            position['trailing_active'] = True

            # 트레일링 스탑 라인 계산
            trailing_stop_price = highest_price * (1 - self.trailing_distance / 100)

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
        # 5순위: EMA + Volume Breakdown (문서 명세: 추세 붕괴 시)
        # ========================================

        # +2.0% 이상 수익 구간에서는 VWAP 무시 (문서: profit_threshold_for_ignore)
        if profit_pct < self.vwap_profit_threshold:
            vwap_exit_check = self._check_vwap_exit(df, current_price, profit_pct)

            if vwap_exit_check[0]:
                return vwap_exit_check

        # ========================================
        # 6순위: 시간 기반 청산 (문서 명세: 15:00 이후 전량 청산)
        # ========================================
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
                except:
                    try:
                        price = struct.unpack('<d', price)[0]  # double (fallback)
                    except:
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
