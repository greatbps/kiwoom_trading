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
        ✅ 핵심: Squeeze OFF 첫 봉인지 확인

        "변동성 압축이 끝나고, 처음으로 방향성이 발생한 순간"

        Args:
            current_squeeze: 현재 봉 squeeze 상태 (True=ON, False=OFF)
            prev_squeeze: 직전 봉 squeeze 상태
            squeeze_off_count: Squeeze OFF 후 경과 봉 수

        Returns:
            (is_first_off, reason)

        Example:
            # 올바른 진입
            prev=True, current=False, count=1 → True

            # 추격 매수 (차단)
            prev=False, current=False, count=3 → False
        """
        # ❌ Squeeze ON 상태
        if current_squeeze:
            return False, "Squeeze 아직 ON (압축 중)"

        # ❌ 이미 여러 봉 지난 추격
        if squeeze_off_count > 1:
            return False, f"Squeeze OFF 후 {squeeze_off_count}봉 경과 (추격 금지)"

        # ✅ 첫 해제 봉!
        if prev_squeeze and not current_squeeze and squeeze_off_count == 1:
            return True, "Squeeze OFF 첫 봉 - 진입 타이밍!"

        return False, "Squeeze OFF 조건 미충족"

    def check_volume_surge(
        self,
        stock_code: str,
        recent_5min_volume: float,
        prev_5min_volume: float,
        threshold: float = 1.3
    ) -> Tuple[bool, str]:
        """
        진입 조건 ②: 거래량 ≥ 직전 5분 평균 × 1.3

        Args:
            stock_code: 종목코드
            recent_5min_volume: 최근 5분 거래량
            prev_5min_volume: 직전 5분 평균 거래량
            threshold: 증가율 임계값 (기본 1.3 = 30% 증가)

        Returns:
            (pass, reason)
        """
        if prev_5min_volume == 0:
            return False, "이전 거래량 데이터 없음"

        surge_ratio = recent_5min_volume / prev_5min_volume

        if surge_ratio >= threshold:
            return True, f"거래량 급증 ({surge_ratio:.1f}배)"

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

        Args:
            current_sell_1st: 현재 매도 1호가 잔량
            avg_sell_1st_1min: 직전 1분 평균 매도 1호가
            threshold: 감소 임계값 (0.8 = 20% 감소)

        Returns:
            (pass, reason)
        """
        if avg_sell_1st_1min == 0:
            return False, "매도호가 평균 데이터 없음"

        reduction_ratio = current_sell_1st / avg_sell_1st_1min

        if reduction_ratio < threshold:
            return True, f"매도 1호가 감소 ({reduction_ratio:.2f} < {threshold})"

        return False, f"매도호가 과다 ({reduction_ratio:.2f} ≥ {threshold})"

    def check_execution_strength_relative(
        self,
        stock_code: str,
        current_strength: float,
        stock_avg_strength: float,
        absolute_min: float = 90.0,
        relative_multiplier: float = 1.1
    ) -> Tuple[bool, str]:
        """
        진입 조건 ⑤: 체결강도 ≥ max(90%, 종목평균 × 1.1)

        ⚠️ 중요: 종목별 상대 기준 사용
        - 삼성전자: 평균 100% → 110% 이상 필요
        - 코스닥 저가주: 평균 120% → 132% 이상 필요

        Args:
            stock_code: 종목코드
            current_strength: 현재 체결강도
            stock_avg_strength: 종목 20일 평균 체결강도
            absolute_min: 절대 하한 (기본 90%)
            relative_multiplier: 상대 배수 (기본 1.1)

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
        recent_high_5min: float
    ) -> Tuple[bool, str, Dict]:
        """
        Phase 1 전체 진입 조건 검사

        Returns:
            (pass, reason, details)
        """
        results = {}

        # ① Squeeze OFF 첫 봉
        sq_pass, sq_reason = self.check_squeeze_off_first_bar(
            squeeze_current, squeeze_prev, squeeze_off_count
        )
        results['squeeze_off'] = {'pass': sq_pass, 'reason': sq_reason}
        if not sq_pass:
            return False, sq_reason, results

        # ② 거래량 급증
        vol_pass, vol_reason = self.check_volume_surge(
            stock_code, recent_5min_volume, prev_5min_volume
        )
        results['volume'] = {'pass': vol_pass, 'reason': vol_reason}
        if not vol_pass:
            return False, vol_reason, results

        # ③ VWAP 위
        vwap_pass = current_price > vwap
        vwap_reason = f"현재가 {current_price:,.0f} > VWAP {vwap:,.0f}" if vwap_pass else f"VWAP 이탈"
        results['vwap'] = {'pass': vwap_pass, 'reason': vwap_reason}
        if not vwap_pass:
            return False, vwap_reason, results

        # ④ 매도 1호가 감소
        sell_pass, sell_reason = self.check_sell_order_reduction(
            sell_1st_qty, sell_1st_avg_1min
        )
        results['sell_order'] = {'pass': sell_pass, 'reason': sell_reason}
        if not sell_pass:
            return False, sell_reason, results

        # ⑤ 체결강도 (상대 기준)
        exec_pass, exec_reason = self.check_execution_strength_relative(
            stock_code, execution_strength, stock_avg_strength
        )
        results['execution_strength'] = {'pass': exec_pass, 'reason': exec_reason}
        if not exec_pass:
            return False, exec_reason, results

        # ⑥ 가격 정체 체크
        price_pass, price_reason = self.check_price_stability(price_stable_sec)
        results['price_stability'] = {'pass': price_pass, 'reason': price_reason}
        if not price_pass:
            return False, price_reason, results

        # ✅ 모든 조건 통과!
        return True, "Phase 1 전체 진입 조건 충족", results

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

        # ❌ 금지 3: 체결강도 90% 미만
        if execution_strength < 90.0:
            return True, f"체결강도 {execution_strength:.1f}% < 90% (매도 우위)"

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
