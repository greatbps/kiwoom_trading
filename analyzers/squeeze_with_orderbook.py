#!/usr/bin/env python3
"""
스퀴즈 모멘텀 + 호가창 통합 전략

기존 스퀴즈 모멘텀에 호가창 필터를 추가하여
승률 50% → 55-60% 향상을 목표로 함
"""

from typing import Tuple, Dict, Optional
import pandas as pd
from datetime import datetime, timedelta
from rich.console import Console

from analyzers.squeeze_momentum import SqueezeMomentumPro
from analyzers.order_book_filter import OrderBookFilter

console = Console()


class SqueezeWithOrderBook:
    """
    스퀴즈 모멘텀 + 호가창 통합 전략

    Phase 1: 보수적 필터
    - 스퀴즈로 방향 결정
    - 호가창으로 타이밍 결정
    - 목표: 승률 50% → 55%
    """

    def __init__(self, enable_orderbook: bool = True):
        """
        Args:
            enable_orderbook: 호가창 필터 활성화 여부
                              (False면 기존 스퀴즈만 사용)
        """
        self.squeeze = SqueezeMomentumPro()
        self.orderbook = OrderBookFilter()
        self.enable_orderbook = enable_orderbook

        # 쿨다운 관리
        self.cooldown_until: Dict[str, datetime] = {}

        # 통계
        self.stats = {
            'total_signals': 0,
            'squeeze_pass': 0,
            'orderbook_pass': 0,
            'orderbook_blocked': 0,
            'cooldown_blocked': 0
        }

    def check_cooldown(self, stock_code: str) -> Tuple[bool, str]:
        """
        쿨다운 체크

        Returns:
            (in_cooldown, reason)
        """
        if stock_code not in self.cooldown_until:
            return False, ""

        cooldown_end = self.cooldown_until[stock_code]
        now = datetime.now()

        if now < cooldown_end:
            remaining = (cooldown_end - now).total_seconds() / 60
            return True, f"쿨다운 중 (남은 시간: {remaining:.1f}분)"

        # 쿨다운 종료
        del self.cooldown_until[stock_code]
        return False, ""

    def set_cooldown(
        self,
        stock_code: str,
        stop_type: str,
        loss_pct: float
    ):
        """
        쿨다운 설정 (차등)

        Args:
            stock_code: 종목코드
            stop_type: 'RAPID' or 'TREND'
            loss_pct: 손실률 (음수)
        """
        duration_min = self.orderbook.get_cooldown_duration(stop_type, loss_pct)

        if duration_min > 0:
            cooldown_end = datetime.now() + timedelta(minutes=duration_min)
            self.cooldown_until[stock_code] = cooldown_end
            console.print(
                f"[yellow]⏸️  {stock_code} 쿨다운 {duration_min}분 설정 "
                f"(종료: {cooldown_end.strftime('%H:%M')})[/yellow]"
            )

    def check_entry_signal(
        self,
        stock_code: str,
        df: pd.DataFrame,
        current_price: float,
        vwap: float,
        vwap_5min: float,
        # 호가창 데이터
        recent_5min_volume: float = None,
        prev_5min_volume: float = None,
        sell_1st_qty: float = None,
        sell_1st_avg_1min: float = None,
        sell_total_current: float = None,
        sell_total_avg: float = None,
        execution_strength: float = None,
        stock_avg_strength: float = 100.0,
        price_stable_sec: float = 0.0,
        recent_high_5min: float = None,
        # 스퀴즈 상태
        squeeze_current: bool = None,
        squeeze_prev: bool = None,
        squeeze_off_count: int = None
    ) -> Tuple[bool, str, Dict]:
        """
        통합 진입 신호 체크

        Returns:
            (signal, reason, details)
        """
        self.stats['total_signals'] += 1
        details = {}

        # 1. 쿨다운 체크
        in_cooldown, cooldown_reason = self.check_cooldown(stock_code)
        if in_cooldown:
            self.stats['cooldown_blocked'] += 1
            return False, cooldown_reason, {'stage': 'cooldown'}

        # 2. 스퀴즈 모멘텀 체크
        squeeze_signal, squeeze_reason, squeeze_tier = self.squeeze.generate_signal(
            df, current_price
        )

        details['squeeze'] = {
            'signal': squeeze_signal,
            'reason': squeeze_reason,
            'tier': squeeze_tier
        }

        if not squeeze_signal:
            return False, f"스퀴즈: {squeeze_reason}", details

        self.stats['squeeze_pass'] += 1

        # 3. 호가창 필터 (활성화된 경우만)
        if not self.enable_orderbook:
            # 호가창 비활성화 → 스퀴즈만으로 진입
            return True, f"스퀴즈 Tier{squeeze_tier} 진입", details

        # 호가창 필수 데이터 확인
        if any(x is None for x in [
            recent_5min_volume, prev_5min_volume,
            sell_1st_qty, sell_1st_avg_1min,
            execution_strength, recent_high_5min
        ]):
            console.print(f"[yellow]⚠️  {stock_code} 호가창 데이터 부족, 스퀴즈만 사용[/yellow]")
            return True, f"스퀴즈 Tier{squeeze_tier} 진입 (호가 데이터 없음)", details

        # 스퀴즈 상태 자동 계산 (제공 안 된 경우)
        if squeeze_current is None or squeeze_prev is None:
            # df에서 squeeze 계산
            squeeze_on, momentum_up, sq_details = self.squeeze.check_squeeze(df)
            squeeze_current = not squeeze_on  # OFF = False

            # 이전 봉 계산
            if len(df) >= 2:
                df_prev = df.iloc[:-1]
                squeeze_on_prev, _, _ = self.squeeze.check_squeeze(df_prev)
                squeeze_prev = not squeeze_on_prev
            else:
                squeeze_prev = squeeze_current

            # OFF 카운트 계산 (간단히 0 또는 1로 가정)
            if squeeze_current and not squeeze_prev:
                squeeze_off_count = 1
            elif not squeeze_current:
                squeeze_off_count = 0
            else:
                squeeze_off_count = 1

        # 4. 스퀴즈 색상 확인 (차단 조건용)
        from utils.squeeze_momentum_realtime import get_current_squeeze_signal
        from utils.squeeze_momentum_realtime import calculate_squeeze_momentum
        df_for_color = calculate_squeeze_momentum(df.copy())
        signal_info = get_current_squeeze_signal(df_for_color)
        squeeze_color = signal_info.get('color', 'gray')

        # 5. 차단 조건 우선 체크 (하나라도 걸리면 진입 차단)
        blocked, block_reason = self.orderbook.check_block_conditions(
            execution_strength=execution_strength,
            sell_total_current=sell_total_current or 0,
            sell_total_avg=sell_total_avg or 1,
            squeeze_color=squeeze_color,
            debug=True
        )

        if blocked:
            self.stats['orderbook_blocked'] += 1
            return False, f"차단: {block_reason}", details

        # 6. 호가창 진입 조건 (느슨: 2/6 통과면 OK)
        # Tier 3 신호는 1/6만 통과해도 허용 (높은 신호 강도)
        min_pass = 2
        if squeeze_tier >= 3:
            min_pass = 1  # Tier 3는 1개 조건만 통과해도 OK
            console.print(f"[cyan]  ⭐ Tier 3 신호 → 호가창 기준 완화 (1/6)[/cyan]")

        ob_pass, ob_reason, ob_details = self.orderbook.check_entry_conditions_loose(
            stock_code=stock_code,
            current_price=current_price,
            vwap=vwap,
            squeeze_current=squeeze_current,
            squeeze_prev=squeeze_prev,
            squeeze_off_count=squeeze_off_count,
            recent_5min_volume=recent_5min_volume,
            prev_5min_volume=prev_5min_volume,
            sell_1st_qty=sell_1st_qty,
            sell_1st_avg_1min=sell_1st_avg_1min,
            execution_strength=execution_strength,
            stock_avg_strength=stock_avg_strength,
            price_stable_sec=price_stable_sec,
            recent_high_5min=recent_high_5min,
            min_pass=min_pass,
            debug=True
        )

        details['orderbook'] = ob_details
        details['squeeze_color'] = squeeze_color

        if not ob_pass:
            self.stats['orderbook_blocked'] += 1
            return False, f"호가창 부족: {ob_reason}", details

        # ✅ 모든 조건 통과!
        self.stats['orderbook_pass'] += 1

        # 진입 성공 시 쿨다운 설정 (재진입 방지)
        cooldown_minutes = 3  # 기본 3분
        if squeeze_tier >= 3:
            cooldown_minutes = 5  # Tier 3는 5분 (더 신중)

        cooldown_end = datetime.now() + timedelta(minutes=cooldown_minutes)
        self.cooldown_until[stock_code] = cooldown_end
        console.print(
            f"[cyan]⏸️  {stock_code} 재진입 쿨다운 {cooldown_minutes}분 "
            f"(종료: {cooldown_end.strftime('%H:%M')})[/cyan]"
        )

        return True, f"스퀴즈 Tier{squeeze_tier} + 호가창 진입 확정", details

    def check_exit_signal(
        self,
        stock_code: str,
        entry_price: float,
        current_price: float,
        vwap: float,
        vwap_5min: float,
        execution_strength: float,
        sqz_color: str
    ) -> Tuple[bool, str, Optional[str]]:
        """
        통합 청산 신호 체크

        Returns:
            (should_exit, reason, exit_type)
            exit_type: None (보유) or 'RAPID' or 'TREND'
        """
        # 1. 스퀴즈 청산 신호 (기존 로직)
        if sqz_color in ['dark_red', 'bright_red']:
            loss_pct = ((current_price - entry_price) / entry_price) * 100
            self.set_cooldown(stock_code, 'TREND', loss_pct)
            return True, f"스퀴즈 {sqz_color} 전환", 'TREND'

        if sqz_color == 'dark_green':
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            if profit_pct >= 1.0:
                return True, "스퀴즈 Dark Green 부분 익절", 'PARTIAL'

        # 2. 호가창 듀얼 손절
        if self.enable_orderbook:
            should_stop, stop_reason, stop_type = self.orderbook.check_stop_loss_dual(
                current_price=current_price,
                vwap=vwap,
                vwap_5min=vwap_5min,
                execution_strength=execution_strength
            )

            if should_stop:
                loss_pct = ((current_price - entry_price) / entry_price) * 100
                self.set_cooldown(stock_code, stop_type, loss_pct)
                return True, stop_reason, stop_type

        # 보유
        return False, "", None

    def get_statistics(self) -> Dict:
        """전략 통계 반환"""
        total = self.stats['total_signals']
        if total == 0:
            return self.stats

        return {
            **self.stats,
            'squeeze_pass_rate': self.stats['squeeze_pass'] / total * 100,
            'orderbook_pass_rate': self.stats['orderbook_pass'] / total * 100,
            'orderbook_block_rate': self.stats['orderbook_blocked'] / total * 100,
            'cooldown_block_rate': self.stats['cooldown_blocked'] / total * 100
        }

    def print_statistics(self):
        """통계 출력"""
        stats = self.get_statistics()

        console.print("\n" + "="*80)
        console.print("[bold cyan]스퀴즈 + 호가창 전략 통계[/bold cyan]")
        console.print("="*80)
        console.print(f"총 신호: {stats['total_signals']}회")
        console.print(f"스퀴즈 통과: {stats['squeeze_pass']}회 ({stats.get('squeeze_pass_rate', 0):.1f}%)")
        console.print(f"호가창 통과: {stats['orderbook_pass']}회 ({stats.get('orderbook_pass_rate', 0):.1f}%)")
        console.print(f"호가창 차단: {stats['orderbook_blocked']}회 ({stats.get('orderbook_block_rate', 0):.1f}%)")
        console.print(f"쿨다운 차단: {stats['cooldown_blocked']}회 ({stats.get('cooldown_block_rate', 0):.1f}%)")
        console.print("="*80 + "\n")


# ============================================
# 사용 예시
# ============================================

if __name__ == "__main__":
    import yfinance as yf

    console.print("\n" + "="*80)
    console.print("[bold cyan]스퀴즈 + 호가창 통합 전략 테스트[/bold cyan]")
    console.print("="*80 + "\n")

    # 전략 생성
    strategy = SqueezeWithOrderBook(enable_orderbook=True)

    # 테스트 데이터
    ticker = "005930.KS"
    df = yf.download(ticker, period='1mo', interval='1d', progress=False)

    if df is None or len(df) == 0:
        console.print("[red]데이터 조회 실패[/red]")
    else:
        # 컬럼명 소문자
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() for col in df.columns]
        else:
            df.columns = df.columns.str.lower()

        # 테스트 파라미터
        current_price = df['close'].iloc[-1]
        vwap = df['close'].rolling(20).mean().iloc[-1]

        # 진입 신호 체크
        signal, reason, details = strategy.check_entry_signal(
            stock_code="005930",
            df=df,
            current_price=current_price,
            vwap=vwap,
            vwap_5min=vwap,
            recent_5min_volume=1000000,
            prev_5min_volume=800000,
            sell_1st_qty=5000,
            sell_1st_avg_1min=7000,
            execution_strength=115.0,
            stock_avg_strength=100.0,
            price_stable_sec=3.0,
            recent_high_5min=current_price * 1.01
        )

        console.print(f"신호: {'✅ 진입' if signal else '❌ 대기'}")
        console.print(f"이유: {reason}")
        console.print()

        # 스퀴즈 상세
        if 'squeeze' in details:
            sq = details['squeeze']
            console.print(f"스퀴즈: {sq['reason']} (Tier {sq['tier']})")

        # 호가창 상세
        if 'orderbook' in details:
            console.print("\n호가창 조건:")
            for cond, result in details['orderbook'].items():
                status = "✓" if result['pass'] else "✗"
                color = "green" if result['pass'] else "red"
                console.print(f"  [{color}]{status} {cond}: {result['reason']}[/{color}]")

    # 통계 출력
    strategy.print_statistics()

    console.print("="*80)
    console.print("[bold green]테스트 완료[/bold green]")
    console.print("="*80 + "\n")
