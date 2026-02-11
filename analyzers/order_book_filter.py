#!/usr/bin/env python3
"""
호가창 기반 진입/청산 필터 (Phase 1 - 보수적)

GPT + Claude 통합 전략:
- 스퀴즈는 방향, 호가는 타이밍
- 손실 분포를 바꾸는 전략
- 승률 목표: 50% → 55%
"""

from typing import Tuple, Dict, Optional
from datetime import datetime, timedelta
import pandas as pd
from rich.console import Console

console = Console()


class OrderBookFilter:
    """호가창 기반 필터 (Phase 1)"""

    def __init__(self):
        """
        Phase 1: 보수적 필터
        - 큰 손실만 차단
        - 승률 50% → 55% 목표
        """
        self.stock_avg_cache: Dict[str, Dict] = {}  # 종목별 평균 캐시
        self.last_update: Dict[str, datetime] = {}

    def calculate_stock_averages(
        self,
        stock_code: str,
        recent_data: pd.DataFrame
    ) -> Dict:
        """
        종목별 20일 평균 계산 (체결강도, 거래량 등)

        Args:
            stock_code: 종목코드
            recent_data: 최근 20일 데이터 (OHLCV + 체결강도)

        Returns:
            {
                'avg_execution_strength': float,
                'avg_volume': float,
                'last_updated': datetime
            }
        """
        if len(recent_data) < 10:
            # 데이터 부족 시 기본값
            return {
                'avg_execution_strength': 100.0,
                'avg_volume': recent_data['volume'].mean() if len(recent_data) > 0 else 0,
                'last_updated': datetime.now()
            }

        # 체결강도 20일 평균
        if 'execution_strength' in recent_data.columns:
            avg_exec = recent_data['execution_strength'].tail(20).mean()
        else:
            avg_exec = 100.0

        # 거래량 20일 평균
        avg_vol = recent_data['volume'].tail(20).mean()

        result = {
            'avg_execution_strength': avg_exec,
            'avg_volume': avg_vol,
            'last_updated': datetime.now()
        }

        # 캐시 저장
        self.stock_avg_cache[stock_code] = result
        self.last_update[stock_code] = datetime.now()

        return result

    def check_squeeze_off_first_bar(
        self,
        current_squeeze: bool,
        prev_squeeze: bool,
        squeeze_off_count: int
    ) -> Tuple[bool, str]:
        """
        ✅ 핵심: Squeeze OFF 1~2봉 허용

        🔥 GPT 분석 반영: 실전에서는 2번째 봉에서 방향 확정되는 경우 많음
        - 첫 봉: 호가창 정신없음
        - 2번째 봉: 방향 확정 후 안정적 진입

        Args:
            current_squeeze: 현재 봉 squeeze 상태 (True=ON, False=OFF)
            prev_squeeze: 직전 봉 squeeze 상태
            squeeze_off_count: Squeeze OFF 후 경과 봉 수

        Returns:
            (is_first_off, reason)

        Example:
            # 올바른 진입 (1~2봉)
            count=1 → True
            count=2 → True

            # 추격 매수 차단 (3봉 이상)
            count=3 → False
        """
        # ❌ Squeeze ON 상태
        if current_squeeze:
            return False, "Squeeze 아직 ON (압축 중)"

        # ❌ 3봉 이상 지난 추격
        if squeeze_off_count > 2:  # 🔥 GPT 권장: 1 → 2 (1~2봉 허용)
            return False, f"Squeeze OFF 후 {squeeze_off_count}봉 경과 (추격 금지)"

        # ✅ 1~2봉 진입 허용!
        if not current_squeeze and squeeze_off_count <= 2:
            return True, f"Squeeze OFF {squeeze_off_count}봉 - 진입 타이밍!"

        return False, "Squeeze OFF 조건 미충족"

    def check_volume_surge(
        self,
        stock_code: str,
        recent_5min_volume: float,
        prev_5min_volume: float,
        threshold: float = 1.05  # 🔥 실전 반영: 1.1 → 1.05 (대형주 5% 증가도 유의미)
    ) -> Tuple[bool, str]:
        """
        진입 조건 ②: 거래량 ≥ 직전 5분 평균 × 1.05

        🔥 실전 분석 반영: 대형주는 거래량이 안정적
        - 1.3배 급증은 중소형주 기준
        - 대형주는 1.05배(5% 증가)도 의미있는 신호
        - 실전: 464080 종목이 1.05배로 차단됨 → 완화

        Args:
            stock_code: 종목코드
            recent_5min_volume: 최근 5분 거래량
            prev_5min_volume: 직전 5분 평균 거래량
            threshold: 증가율 임계값 (기본 1.05 = 5% 증가)

        Returns:
            (pass, reason)
        """
        if prev_5min_volume == 0:
            return False, "이전 거래량 데이터 없음"

        surge_ratio = recent_5min_volume / prev_5min_volume

        if surge_ratio >= threshold:
            return True, f"거래량 증가 ({surge_ratio:.2f}배)"

        return False, f"거래량 부족 ({surge_ratio:.2f}배 < {threshold}배)"

    def check_sell_order_reduction(
        self,
        current_sell_1st: float,
        avg_sell_1st_1min: float,
        threshold: float = 0.8
    ) -> Tuple[bool, str]:
        """
        진입 조건 ④: 매도 1호가 < 1분 평균 × 0.8

        매도 물량 감소 = 체결 임박 신호

        🔥 실전 반영: 평균 데이터가 없으면 스킵 (통과 처리)
        - main_auto_trading.py에서 현재값을 평균으로 전달 (간소화)
        - 실제 평균 데이터 구현 전까지는 이 조건 스킵

        Args:
            current_sell_1st: 현재 매도 1호가 잔량
            avg_sell_1st_1min: 직전 1분 평균 매도 1호가
            threshold: 감소 임계값 (0.8 = 20% 감소)

        Returns:
            (pass, reason)
        """
        # 🔥 평균 데이터 없음 OR 현재값=평균값 (실제 평균 아님) → 스킵
        if avg_sell_1st_1min == 0:
            return True, "매도호가 평균 데이터 없음 (조건 스킵)"

        # 🔥 현재값 = 평균값 → 간소화 모드 (실제 평균 아님)
        if abs(current_sell_1st - avg_sell_1st_1min) < 0.01:
            return True, "매도호가 평균 미구현 (조건 스킵)"

        reduction_ratio = current_sell_1st / avg_sell_1st_1min

        if reduction_ratio < threshold:
            return True, f"매도 1호가 감소 ({reduction_ratio:.2f} < {threshold})"

        return False, f"매도호가 과다 ({reduction_ratio:.2f} ≥ {threshold})"

    def check_execution_strength_relative(
        self,
        stock_code: str,
        current_strength: float,
        stock_avg_strength: float,
        absolute_min: float = 80.0,  # 🔥 GPT 권장: 90 → 80 (대형주 적합)
        relative_multiplier: float = 1.05  # 🔥 GPT 권장: 1.1 → 1.05 (완화)
    ) -> Tuple[bool, str]:
        """
        진입 조건 ⑤: 체결강도 ≥ max(80%, 종목평균 × 1.05)

        🔥 GPT 분석 반영: 대형주는 80-85%가 매수 우위
        - 기존 90%는 상한가급 상황에서만 가능
        - 1.1배도 과도하게 까다로움

        Args:
            stock_code: 종목코드
            current_strength: 현재 체결강도
            stock_avg_strength: 종목 20일 평균 체결강도
            absolute_min: 절대 하한 (기본 80%)
            relative_multiplier: 상대 배수 (기본 1.05)

        Returns:
            (pass, reason)
        """
        # 종목별 상대 기준
        relative_threshold = stock_avg_strength * relative_multiplier

        # 최종 임계값: max(절대 하한, 상대 기준)
        final_threshold = max(absolute_min, relative_threshold)

        if current_strength >= final_threshold:
            return True, f"체결강도 충족 ({current_strength:.1f}% ≥ {final_threshold:.1f}%)"

        return False, f"체결강도 부족 ({current_strength:.1f}% < {final_threshold:.1f}%)"

    def check_price_stability(
        self,
        price_stable_seconds: float,
        max_stable_seconds: float = 5.0
    ) -> Tuple[bool, str]:
        """
        진입 조건 ⑥: 동일가 체결 ≤ 5초

        같은 가격에 오래 머무름 = 매수세 약함

        Args:
            price_stable_seconds: 동일 가격 유지 시간 (초)
            max_stable_seconds: 최대 허용 시간 (기본 5초)

        Returns:
            (pass, reason)
        """
        if price_stable_seconds <= max_stable_seconds:
            return True, f"가격 변동 정상 ({price_stable_seconds:.1f}초)"

        return False, f"가격 정체 ({price_stable_seconds:.1f}초 > {max_stable_seconds}초)"

    def check_entry_conditions_phase1(
        self,
        stock_code: str,
        current_price: float,
        vwap: float,
        squeeze_current: bool,
        squeeze_prev: bool,
        squeeze_off_count: int,
        recent_5min_volume: float,
        prev_5min_volume: float,
        sell_1st_qty: float,
        sell_1st_avg_1min: float,
        execution_strength: float,
        stock_avg_strength: float,
        price_stable_sec: float,
        recent_high_5min: float,
        debug: bool = True  # 🔥 GPT 권장: 디버그 로그 옵션
    ) -> Tuple[bool, str, Dict]:
        """
        Phase 1 전체 진입 조건 검사

        🔥 GPT 권장: 실패 즉시 return + 상세 로그

        Returns:
            (pass, reason, details)
        """
        results = {}

        # 🔥 디버그 로그 시작
        if debug:
            console.print(f"[cyan]호가창 체크: {stock_code}[/cyan]")

        # ① Squeeze OFF 첫 봉
        sq_pass, sq_reason = self.check_squeeze_off_first_bar(
            squeeze_current, squeeze_prev, squeeze_off_count
        )
        results['squeeze_off'] = {'pass': sq_pass, 'reason': sq_reason}
        if debug:
            status = "✓" if sq_pass else "✗"
            console.print(f"  {status} ① Squeeze OFF: {sq_reason}")
        if not sq_pass:
            return False, sq_reason, results

        # ② 거래량 급증
        vol_pass, vol_reason = self.check_volume_surge(
            stock_code, recent_5min_volume, prev_5min_volume
        )
        vol_ratio = recent_5min_volume / prev_5min_volume if prev_5min_volume > 0 else 0
        results['volume'] = {'pass': vol_pass, 'reason': vol_reason}
        if debug:
            status = "✓" if vol_pass else "✗"
            console.print(f"  {status} ② 거래량: {vol_ratio:.2f}배 (기준: 1.1)")
        if not vol_pass:
            return False, vol_reason, results

        # ③ VWAP 위
        vwap_pass = current_price > vwap
        vwap_reason = f"현재가 {current_price:,.0f} > VWAP {vwap:,.0f}" if vwap_pass else f"VWAP 이탈"
        results['vwap'] = {'pass': vwap_pass, 'reason': vwap_reason}
        if debug:
            status = "✓" if vwap_pass else "✗"
            console.print(f"  {status} ③ VWAP: {current_price:,.0f} vs {vwap:,.0f}")
        if not vwap_pass:
            return False, vwap_reason, results

        # ④ 매도 1호가 감소
        sell_pass, sell_reason = self.check_sell_order_reduction(
            sell_1st_qty, sell_1st_avg_1min
        )
        results['sell_order'] = {'pass': sell_pass, 'reason': sell_reason}
        if debug:
            status = "✓" if sell_pass else "✗"
            console.print(f"  {status} ④ 매도1호가: {sell_1st_qty:,.0f} vs 평균 {sell_1st_avg_1min:,.0f}")
        if not sell_pass:
            return False, sell_reason, results

        # ⑤ 체결강도 (상대 기준)
        exec_pass, exec_reason = self.check_execution_strength_relative(
            stock_code, execution_strength, stock_avg_strength
        )
        results['execution_strength'] = {'pass': exec_pass, 'reason': exec_reason}
        if debug:
            status = "✓" if exec_pass else "✗"
            console.print(f"  {status} ⑤ 체결강도: {execution_strength:.1f}% (기준: 80%)")
        if not exec_pass:
            return False, exec_reason, results

        # ⑥ 가격 정체 체크
        price_pass, price_reason = self.check_price_stability(price_stable_sec)
        results['price_stability'] = {'pass': price_pass, 'reason': price_reason}
        if debug:
            status = "✓" if price_pass else "✗"
            console.print(f"  {status} ⑥ 가격정체: {price_stable_sec:.1f}초 (기준: 5초)")
        if not price_pass:
            return False, price_reason, results

        # ✅ 모든 조건 통과!
        if debug:
            console.print(f"[green]  ✅ 호가창 6개 조건 모두 통과![/green]")
        return True, "Phase 1 전체 진입 조건 충족", results

    def check_entry_conditions_loose(
        self,
        stock_code: str,
        current_price: float,
        vwap: float,
        squeeze_current: bool,
        squeeze_prev: bool,
        squeeze_off_count: int,
        recent_5min_volume: float,
        prev_5min_volume: float,
        sell_1st_qty: float,
        sell_1st_avg_1min: float,
        execution_strength: float,
        stock_avg_strength: float,
        price_stable_sec: float,
        recent_high_5min: float,
        min_pass: int = 2,  # 최소 N개 조건 통과하면 OK
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        느슨한 진입 조건 - N/6 통과면 OK

        Args:
            min_pass: 최소 통과 조건 수 (기본 2개)

        Returns:
            (pass, reason, details)
        """
        results = {}

        if debug:
            console.print(f"[cyan]호가창 체크 (느슨): {stock_code} (최소 {min_pass}/6)[/cyan]")

        # ① Squeeze OFF 첫 봉
        sq_pass, sq_reason = self.check_squeeze_off_first_bar(
            squeeze_current, squeeze_prev, squeeze_off_count
        )
        results['squeeze_off'] = {'pass': sq_pass, 'reason': sq_reason}

        # ② 거래량 급증
        vol_pass, vol_reason = self.check_volume_surge(
            stock_code, recent_5min_volume, prev_5min_volume
        )
        results['volume'] = {'pass': vol_pass, 'reason': vol_reason}

        # ③ VWAP 상단 (보조 조건 - 실패해도 진행)
        vwap_pass = current_price > vwap
        vwap_reason = f"현재가 {current_price:,.0f} > VWAP {vwap:,.0f}" if vwap_pass else f"VWAP 이탈"
        results['vwap'] = {'pass': vwap_pass, 'reason': vwap_reason}

        # ④ 매도호가 감소 (스킵 가능)
        sell_pass, sell_reason = self.check_sell_order_reduction(
            sell_1st_qty, sell_1st_avg_1min
        )
        results['sell_order'] = {'pass': sell_pass, 'reason': sell_reason}

        # ⑤ 체결강도 (보조 조건)
        exec_pass, exec_reason = self.check_execution_strength_relative(
            stock_code, execution_strength, stock_avg_strength
        )
        results['execution'] = {'pass': exec_pass, 'reason': exec_reason}

        # ⑥ 가격 안정성 (보조 조건)
        price_pass, price_reason = self.check_price_stability(price_stable_sec)
        results['price_stability'] = {'pass': price_pass, 'reason': price_reason}

        # 통과한 조건 개수 계산
        passed_count = sum([1 for r in results.values() if r.get('pass', False)])

        if debug:
            for key, result in results.items():
                status = "✓" if result.get('pass') else "✗"
                console.print(f"  {status} {key}: {result.get('reason', 'N/A')}")
            console.print(f"[cyan]  → 통과: {passed_count}/6 (최소 {min_pass}개 필요)[/cyan]")

        # min_pass개 이상 통과하면 OK
        if passed_count >= min_pass:
            return True, f"호가창 {passed_count}/6 통과 (최소 {min_pass})", results
        else:
            return False, f"호가창 {passed_count}/6 통과 부족 (최소 {min_pass} 필요)", results

    def check_block_conditions(
        self,
        execution_strength: float,
        sell_total_current: float,
        sell_total_avg: float,
        squeeze_color: str = None,
        debug: bool = True
    ) -> Tuple[bool, str]:
        """
        차단 조건 체크 - 하나라도 걸리면 진입 차단

        Args:
            execution_strength: 현재 체결강도
            sell_total_current: 현재 매도호가 총합
            sell_total_avg: 평균 매도호가 총합
            squeeze_color: 스퀴즈 색상 (bright_green, dark_green, dark_red, bright_red)

        Returns:
            (blocked, reason)
        """
        # ❌ 차단 1: 체결강도 < 60%
        if execution_strength < 60.0:
            if debug:
                console.print(f"[red]  ❌ 차단: 체결강도 {execution_strength:.1f}% < 60%[/red]")
            return True, f"체결강도 약함 ({execution_strength:.1f}% < 60%)"

        # ❌ 차단 2: 매도호가 급증 (30% 이상)
        if sell_total_avg > 0:
            sell_surge = (sell_total_current / sell_total_avg - 1) * 100
            if sell_surge > 30:
                if debug:
                    console.print(f"[red]  ❌ 차단: 매도호가 급증 {sell_surge:.1f}% > 30%[/red]")
                return True, f"매도호가 급증 ({sell_surge:.1f}%)"

        # ❌ 차단 3: 스퀴즈 색상 변경 (🟡DG, 🔴DR, 🟠BR)
        if squeeze_color in ['dark_green', 'dark_red', 'bright_red']:
            color_map = {
                'dark_green': '🟡DG',
                'dark_red': '🔴DR',
                'bright_red': '🟠BR'
            }
            if debug:
                console.print(f"[red]  ❌ 차단: 스퀴즈 {color_map.get(squeeze_color)} 전환[/red]")
            return True, f"스퀴즈 {color_map.get(squeeze_color)} 전환"

        # ✅ 차단 조건 없음
        if debug:
            console.print(f"[green]  ✅ 차단 조건 없음[/green]")
        return False, ""

    def check_entry_blockers_phase1(
        self,
        current_price: float,
        recent_high_5min: float,
        sell_total_current: float,
        sell_total_avg: float,
        execution_strength: float
    ) -> Tuple[bool, str]:
        """
        진입 금지 조건 (하나라도 걸리면 차단)

        🔥 GPT 분석 반영: 체결강도 중복 체크 제거
        - 이미 check_execution_strength_relative에서 체크함
        - 중복 필터는 승률이 아니라 미체결만 증가

        Returns:
            (blocked, reason)
        """
        # ❌ 금지 1: 고점 대비 -2% 이상 하락 (추격 방지)
        if recent_high_5min > 0:
            drawdown_pct = ((current_price - recent_high_5min) / recent_high_5min) * 100
            if drawdown_pct < -2.0:
                return True, f"고점 대비 {drawdown_pct:.2f}% 하락 (추격 금지)"

        # ❌ 금지 2: 매도호가 총합 30% 이상 급증
        if sell_total_avg > 0:
            sell_surge = (sell_total_current / sell_total_avg - 1) * 100
            if sell_surge > 30:
                return True, f"매도호가 {sell_surge:.1f}% 급증 (대량 물량)"

        # 🔥 금지 3 삭제: 체결강도 중복 체크 제거 (GPT 권장)
        # 이미 Phase 1 진입조건에서 80% 이상 체크함

        # ✅ 진입 가능
        return False, ""

    def check_stop_loss_dual(
        self,
        current_price: float,
        vwap: float,
        vwap_5min: float,
        execution_strength: float,
        execution_threshold: float = 80.0,
        vwap_stop_pct: float = 0.8
    ) -> Tuple[bool, str, Optional[str]]:
        """
        듀얼 손절 구조

        1차 (급변 대응): VWAP -0.8%
        2차 (추세 붕괴): 5분 VWAP 이탈 + 체결강도 < 80%

        Returns:
            (should_stop, reason, stop_type)
            stop_type: 'RAPID' (급락) or 'TREND' (추세)
        """
        # 1차 손절: VWAP -0.8%
        vwap_stop_price = vwap * (1 - vwap_stop_pct / 100)
        if current_price < vwap_stop_price:
            loss_pct = ((current_price - vwap) / vwap) * 100
            return True, f"급락 손절 (VWAP {loss_pct:.2f}%)", 'RAPID'

        # 2차 손절: 5분 VWAP 이탈 + 체결강도 붕괴
        if current_price < vwap_5min and execution_strength < execution_threshold:
            return True, f"추세 붕괴 손절 (5분 VWAP 이탈 + 체결강도 {execution_strength:.1f}%)", 'TREND'

        # 보유
        return False, "", None

    def get_cooldown_duration(
        self,
        stop_type: str,
        loss_pct: float
    ) -> int:
        """
        차등 쿨다운 시간 (분)

        Args:
            stop_type: 'RAPID' (급락) or 'TREND' (추세)
            loss_pct: 손실률 (음수)

        Returns:
            쿨다운 시간 (분)
        """
        # 급락 손절 (-2% 이상)
        if abs(loss_pct) >= 2.0:
            return 30

        # 추세 붕괴 손절
        if stop_type == 'TREND':
            return 15

        # 체결 붕괴 손절
        if stop_type == 'RAPID':
            return 15

        # 전략 손절 (조건 이탈)
        return 0  # 쿨다운 없음


# ============================================
# 사용 예시
# ============================================

if __name__ == "__main__":
    console.print("\n" + "="*80)
    console.print("[bold cyan]호가창 필터 Phase 1 테스트[/bold cyan]")
    console.print("="*80 + "\n")

    # 필터 생성
    filter_obj = OrderBookFilter()

    # 테스트 데이터
    test_cases = [
        {
            'name': '✅ 완벽한 진입',
            'stock_code': '005930',
            'current_price': 75000,
            'vwap': 74500,
            'squeeze_current': False,
            'squeeze_prev': True,
            'squeeze_off_count': 1,
            'recent_5min_volume': 130000,
            'prev_5min_volume': 100000,
            'sell_1st_qty': 5000,
            'sell_1st_avg_1min': 7000,
            'execution_strength': 115.0,
            'stock_avg_strength': 100.0,
            'price_stable_sec': 3.0,
            'recent_high_5min': 75100,
        },
        {
            'name': '❌ 추격 매수',
            'stock_code': '005930',
            'current_price': 75000,
            'vwap': 74500,
            'squeeze_current': False,
            'squeeze_prev': False,  # 이미 OFF
            'squeeze_off_count': 3,  # 3봉 경과
            'recent_5min_volume': 130000,
            'prev_5min_volume': 100000,
            'sell_1st_qty': 5000,
            'sell_1st_avg_1min': 7000,
            'execution_strength': 115.0,
            'stock_avg_strength': 100.0,
            'price_stable_sec': 3.0,
            'recent_high_5min': 75100,
        },
        {
            'name': '❌ 체결강도 부족',
            'stock_code': '005930',
            'current_price': 75000,
            'vwap': 74500,
            'squeeze_current': False,
            'squeeze_prev': True,
            'squeeze_off_count': 1,
            'recent_5min_volume': 130000,
            'prev_5min_volume': 100000,
            'sell_1st_qty': 5000,
            'sell_1st_avg_1min': 7000,
            'execution_strength': 85.0,  # 90% 미만
            'stock_avg_strength': 100.0,
            'price_stable_sec': 3.0,
            'recent_high_5min': 75100,
        }
    ]

    for test in test_cases:
        console.print(f"\n[bold]{test['name']}[/bold]")
        console.print("-" * 80)

        passed, reason, details = filter_obj.check_entry_conditions_phase1(
            stock_code=test['stock_code'],
            current_price=test['current_price'],
            vwap=test['vwap'],
            squeeze_current=test['squeeze_current'],
            squeeze_prev=test['squeeze_prev'],
            squeeze_off_count=test['squeeze_off_count'],
            recent_5min_volume=test['recent_5min_volume'],
            prev_5min_volume=test['prev_5min_volume'],
            sell_1st_qty=test['sell_1st_qty'],
            sell_1st_avg_1min=test['sell_1st_avg_1min'],
            execution_strength=test['execution_strength'],
            stock_avg_strength=test['stock_avg_strength'],
            price_stable_sec=test['price_stable_sec'],
            recent_high_5min=test['recent_high_5min']
        )

        if passed:
            console.print(f"[green]✅ 진입 가능: {reason}[/green]")
        else:
            console.print(f"[red]❌ 진입 차단: {reason}[/red]")

        # 상세 결과
        for condition, result in details.items():
            status = "✓" if result['pass'] else "✗"
            color = "green" if result['pass'] else "red"
            console.print(f"  [{color}]{status} {condition}: {result['reason']}[/{color}]")

    console.print("\n" + "="*80)
    console.print("[bold green]테스트 완료[/bold green]")
    console.print("="*80 + "\n")
