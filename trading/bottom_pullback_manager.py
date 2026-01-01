"""
Bottom Pullback 전략 Manager

핵심 로직:
1. 조건검색 신호 발생 → WAIT_PULLBACK 상태
2. 장중 VWAP 이탈 → Pullback 감지
3. 신호봉 저가 유지 + VWAP 재돌파 → 매수 신호
4. 날짜 변경 시 미체결 신호 리셋

상태 머신:
- WAIT_PULLBACK: 조건 신호 발생, Pullback 대기
- PULLBACK_DETECTED: VWAP 이탈 감지
- READY_TO_ENTER: Pullback 후 재돌파, 매수 준비
- INVALIDATED: 무효화 (저가 이탈 or 시간 초과)
"""

from datetime import datetime, time as time_class
from typing import Dict, Optional, Tuple
from rich.console import Console
import pandas as pd

console = Console()


class BottomPullbackManager:
    """Bottom Pullback 전략 상태 관리"""

    def __init__(self, config: dict, state_manager=None):
        """
        Args:
            config: strategy_hybrid.yaml의 condition_strategies.bottom_pullback
            state_manager: TradeStateManager 인스턴스 (무효화 기록용)
        """
        self.config = config
        self.pullback_config = config.get('pullback', {})

        # ✅ StateManager 연동
        self.state_manager = state_manager

        # 신호 추적 딕셔너리
        # {stock_code: {state, signal_date, signal_low, signal_vwap, ...}}
        self.signals: Dict[str, Dict] = {}

        # 오늘 날짜 (리셋 체크용)
        self.current_date = datetime.now().date()

        console.print("[dim]✓ BottomPullbackManager 초기화 완료[/dim]")

    def register_signal(
        self,
        stock_code: str,
        stock_name: str,
        signal_price: float,
        signal_low: float,
        signal_vwap: float,
        market: str = "KOSDAQ"
    ) -> bool:
        """
        조건검색 신호 등록

        Returns:
            True: 신호 등록 성공
            False: 중복 신호 (같은 날 이미 등록)
        """
        today = datetime.now().date()

        # 날짜 변경 체크
        if today != self.current_date:
            self.reset_daily()
            self.current_date = today

        # 이미 오늘 등록된 신호인지 체크
        if stock_code in self.signals:
            existing = self.signals[stock_code]
            if existing['signal_date'] == today:
                console.print(
                    f"[yellow]⚠️  {stock_name} ({stock_code}): "
                    f"오늘 이미 신호 등록됨 (중복 방지)[/yellow]"
                )
                return False

        # 새 신호 등록
        self.signals[stock_code] = {
            'stock_name': stock_name,
            'market': market,
            'state': 'WAIT_PULLBACK',
            'signal_date': today,
            'signal_time': datetime.now(),
            'signal_price': signal_price,
            'signal_low': signal_low,
            'signal_vwap': signal_vwap,
            'below_vwap_detected': False,  # VWAP 이탈 감지 여부
            'pullback_used': False,        # 진입 완료 여부
        }

        console.print()
        console.print("=" * 80, style="bold cyan")
        console.print(f"🎯 Bottom 신호 등록: {stock_name} ({stock_code})", style="bold cyan")
        console.print("=" * 80, style="bold cyan")
        console.print(f"  신호 가격: {signal_price:,.0f}원")
        console.print(f"  신호 저가: {signal_low:,.0f}원")
        console.print(f"  신호 VWAP: {signal_vwap:,.0f}원")
        console.print(f"  상태: WAIT_PULLBACK")
        console.print()

        return True

    def check_pullback(
        self,
        stock_code: str,
        current_price: float,
        current_vwap: float,
        current_low: float,
        recent_volume: float,
        avg_volume_5: float,
        df: pd.DataFrame = None
    ) -> Tuple[bool, str]:
        """
        Pullback 조건 체크

        Returns:
            (ready_to_enter, reason)
            - ready_to_enter: True면 매수 신호 발생
            - reason: 상태 설명
        """
        if stock_code not in self.signals:
            return False, "신호 없음"

        signal = self.signals[stock_code]

        # 이미 진입했거나 무효화된 신호
        if signal['pullback_used']:
            return False, "이미 진입 완료"

        if signal['state'] == 'INVALIDATED':
            return False, signal.get('invalidation_reason', '무효화됨')

        # 1. 무효화 조건 체크 (신호봉 저가 이탈)
        invalidate_config = self.pullback_config.get('invalidation', {})
        break_pct = invalidate_config.get('break_signal_low_pct', -0.5)
        signal_low = signal['signal_low']

        break_threshold = signal_low * (1 + break_pct / 100)

        if current_low < break_threshold:
            reason = f"신호봉 저가 이탈 ({signal_low:,.0f} → {current_low:,.0f})"
            self._invalidate_signal(stock_code, reason)
            return False, reason

        # 2. 시간 제한 체크 (✅ 동적 시간 제한 적용)
        max_wait = self._get_dynamic_timeout(df)
        elapsed = (datetime.now() - signal['signal_time']).total_seconds() / 60

        if elapsed > max_wait:
            # ✅ ATR 정보 추가 (동적 시간 제한 사용 시)
            atr_info = ""
            if df is not None and invalidate_config.get('use_dynamic_timeout', False):
                atr_pct = self._calculate_atr_pct(df)
                atr_info = f", ATR: {atr_pct:.2f}%"

            reason = f"시간 초과 ({elapsed:.0f}분 > {max_wait}분{atr_info})"
            self._invalidate_signal(stock_code, reason)
            return False, reason

        # 3. 시간 윈도우 체크
        time_window = self.pullback_config.get('time_window', {})
        start_time = time_window.get('start', '09:30')
        end_time = time_window.get('end', '14:30')

        current_time = datetime.now().time()
        start = datetime.strptime(start_time, '%H:%M').time()
        end = datetime.strptime(end_time, '%H:%M').time()

        if not (start <= current_time <= end):
            return False, f"진입 시간 외 ({start_time}-{end_time})"

        # 4. VWAP 이탈 감지 (✅ 정량화 적용)
        signal_vwap = signal['signal_vwap']

        # ✅ VWAP 이탈 임계값 설정 (기본값: -0.3%)
        break_config = self.pullback_config.get('break_conditions', {})
        vwap_break_threshold_pct = break_config.get('vwap_break_threshold_pct', -0.3)

        if not signal['below_vwap_detected']:
            # 아직 VWAP 이탈 안 함
            # ✅ 정량화: VWAP 대비 threshold_pct 이상 이탈 필요
            price_vs_vwap_pct = ((current_price - current_vwap) / current_vwap) * 100

            if price_vs_vwap_pct <= vwap_break_threshold_pct:
                signal['below_vwap_detected'] = True
                signal['state'] = 'PULLBACK_DETECTED'
                console.print(
                    f"[yellow]📉 {signal['stock_name']} ({stock_code}): "
                    f"VWAP 이탈 감지 ({current_price:,.0f} < {current_vwap:,.0f}, "
                    f"{price_vs_vwap_pct:+.2f}% ≤ {vwap_break_threshold_pct}%)[/yellow]"
                )
            return False, "VWAP 이탈 대기 중"

        # 5. VWAP 재돌파 체크 (✅ 정량화 적용)
        if signal['state'] == 'PULLBACK_DETECTED':
            # VWAP 이탈 후 → 재돌파 확인
            reclaim_config = self.pullback_config.get('reclaim_conditions', {})

            # ✅ VWAP 재돌파 임계값 설정 (기본값: +0.2%)
            vwap_reclaim_threshold_pct = reclaim_config.get('vwap_reclaim_threshold_pct', 0.2)

            # ✅ 정량화: VWAP 대비 threshold_pct 이상 돌파 필요
            price_vs_vwap_pct = ((current_price - current_vwap) / current_vwap) * 100

            if price_vs_vwap_pct >= vwap_reclaim_threshold_pct:
                # 거래량 조건 체크
                min_vol_ratio = reclaim_config.get('min_volume_ratio', 1.0)
                volume_ratio = recent_volume / avg_volume_5 if avg_volume_5 > 0 else 0

                if volume_ratio >= min_vol_ratio:
                    # ✅ Pullback 조건 충족!
                    signal['state'] = 'READY_TO_ENTER'

                    console.print()
                    console.print("=" * 80, style="bold green")
                    console.print(
                        f"✅ Bottom Pullback 완료: {signal['stock_name']} ({stock_code})",
                        style="bold green"
                    )
                    console.print("=" * 80, style="bold green")
                    console.print(f"  신호가: {signal['signal_price']:,.0f}원")
                    console.print(f"  현재가: {current_price:,.0f}원")
                    console.print(f"  VWAP: {current_vwap:,.0f}원")
                    console.print(f"  VWAP 대비: {price_vs_vwap_pct:+.2f}% (기준: {vwap_reclaim_threshold_pct:+.2f}%)")
                    console.print(f"  거래량 배율: {volume_ratio:.2f}x (기준: {min_vol_ratio}x)")
                    console.print("  → 매수 진입 가능!")
                    console.print()

                    return True, "Pullback 완료"
                else:
                    return False, f"거래량 부족 ({volume_ratio:.2f}x < {min_vol_ratio}x)"
            else:
                return False, f"VWAP 재돌파 대기 중 ({price_vs_vwap_pct:+.2f}% < {vwap_reclaim_threshold_pct:+.2f}%)"

        return False, f"상태: {signal['state']}"

    def mark_entered(self, stock_code: str):
        """매수 진입 완료 표시"""
        if stock_code in self.signals:
            self.signals[stock_code]['pullback_used'] = True
            self.signals[stock_code]['state'] = 'IN_POSITION'
            console.print(
                f"[green]✓ {self.signals[stock_code]['stock_name']} ({stock_code}): "
                f"진입 완료 표시[/green]"
            )

    def _invalidate_signal(self, stock_code: str, reason: str):
        """신호 무효화"""
        if stock_code in self.signals:
            signal = self.signals[stock_code]
            signal['state'] = 'INVALIDATED'
            signal['invalidation_reason'] = reason

            # ✅ StateManager에 무효화 기록
            if self.state_manager:
                # InvalidationReason import
                try:
                    from trading.trade_state_manager import InvalidationReason
                except ImportError:
                    InvalidationReason = None

                if InvalidationReason:
                    # 무효화 사유 매핑
                    reason_map = {
                        "신호봉 저가 이탈": InvalidationReason.SIGNAL_LOW_BREAK,
                        "시간 초과": InvalidationReason.TIME_EXPIRED,
                        "진입 시간대 이탈": InvalidationReason.TIME_WINDOW_EXIT,
                    }

                    # 사유 분석 (괄호 앞 부분 추출)
                    base_reason = reason.split('(')[0].strip()
                    invalidation_reason = reason_map.get(
                        base_reason,
                        InvalidationReason.MANUAL
                    )

                    self.state_manager.mark_invalidated(
                        stock_code=stock_code,
                        stock_name=signal['stock_name'],
                        strategy_tag='bottom_pullback',
                        reason=invalidation_reason,
                        signal_price=signal.get('signal_price', 0),
                        invalidation_price=signal.get('current_price', 0)
                    )

            console.print()
            console.print(
                f"[red]❌ {signal['stock_name']} ({stock_code}): "
                f"신호 무효화 - {reason}[/red]"
            )
            console.print()

    def get_signal_watchlist(self) -> Dict[str, Dict]:
        """
        현재 대기 중인 신호 목록 반환

        Returns:
            {stock_code: signal_info}
        """
        return {
            code: info
            for code, info in self.signals.items()
            if info['state'] not in ['IN_POSITION', 'INVALIDATED']
        }

    def reset_daily(self):
        """일일 리셋 - 미체결 신호 전부 제거"""
        console.print()
        console.print("=" * 80, style="bold yellow")
        console.print("🔄 Bottom 전략 일일 리셋", style="bold yellow")
        console.print("=" * 80, style="bold yellow")

        before_count = len(self.signals)

        # 포지션 진입한 신호만 보존, 나머지 제거
        self.signals = {
            code: info
            for code, info in self.signals.items()
            if info['state'] == 'IN_POSITION'
        }

        after_count = len(self.signals)
        removed = before_count - after_count

        console.print(f"  제거된 신호: {removed}개")
        console.print(f"  보존된 포지션: {after_count}개")
        console.print()

    def remove_signal(self, stock_code: str):
        """신호 제거 (청산 시)"""
        if stock_code in self.signals:
            del self.signals[stock_code]
            console.print(f"[dim]✓ {stock_code}: Bottom 신호 제거[/dim]")

    def get_signal_info(self, stock_code: str) -> Optional[Dict]:
        """신호 정보 조회"""
        return self.signals.get(stock_code)

    def _calculate_atr_pct(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        ATR (Average True Range) 퍼센트 계산

        Args:
            df: OHLC 데이터프레임
            period: ATR 계산 기간 (기본 14)

        Returns:
            ATR 퍼센트 (종가 대비)
        """
        if df is None or len(df) < period:
            return 0.0

        try:
            # True Range 계산
            high = df['high']
            low = df['low']
            close = df['close'].shift(1)

            tr1 = high - low
            tr2 = abs(high - close)
            tr3 = abs(low - close)

            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            # ATR 계산 (이동평균)
            atr = tr.rolling(window=period).mean().iloc[-1]

            # ATR 퍼센트 (현재가 대비)
            current_price = df['close'].iloc[-1]
            atr_pct = (atr / current_price) * 100 if current_price > 0 else 0.0

            return atr_pct

        except Exception as e:
            console.print(f"[yellow]⚠️  ATR 계산 오류: {e}[/yellow]")
            return 0.0

    def _get_dynamic_timeout(self, df: pd.DataFrame = None) -> int:
        """
        동적 시간 제한 계산 (변동성 기반)

        Args:
            df: OHLC 데이터프레임

        Returns:
            동적 대기 시간 (분)
        """
        invalidate_config = self.pullback_config.get('invalidation', {})

        # 동적 시간 제한 비활성화 시 기본값 반환
        if not invalidate_config.get('use_dynamic_timeout', False):
            return invalidate_config.get('max_wait_minutes', 180)

        # DataFrame 없으면 기본값
        if df is None:
            return invalidate_config.get('max_wait_minutes', 180)

        # ATR 계산
        atr_pct = self._calculate_atr_pct(df)

        # 변동성 임계값
        vol_high = invalidate_config.get('volatility_threshold_high', 3.0)
        vol_low = invalidate_config.get('volatility_threshold_low', 1.5)

        # 동적 대기 시간
        high_vol_minutes = invalidate_config.get('high_volatility_minutes', 120)
        low_vol_minutes = invalidate_config.get('low_volatility_minutes', 240)
        default_minutes = invalidate_config.get('max_wait_minutes', 180)

        # 변동성 기반 시간 결정
        if atr_pct >= vol_high:
            # 고변동성: 짧은 대기
            return high_vol_minutes
        elif atr_pct <= vol_low:
            # 저변동성: 긴 대기
            return low_vol_minutes
        else:
            # 중간 변동성: 기본값
            return default_minutes
