"""
L4: Liquidity Shift 감지기 (수급 전환)
- 기관/외국인 순매수 Z-score
- 호가 잔량 불균형 (Order Imbalance)
- 승률 업그레이드의 핵심 레벨
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict, Optional
from datetime import datetime, timedelta
from pathlib import Path
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console

console = Console()


class LiquidityShiftDetector:
    """수급 전환 감지기"""

    def __init__(
        self,
        api=None,
        inst_z_threshold: float = 1.0,  # 초기 1.0, 실전 1.5
        foreign_z_threshold: float = 1.0,
        order_imbalance_threshold: float = 0.2,  # 초기 0.2, 실전 0.3
        lookback_days: int = 20
    ):
        """
        Args:
            api: 키움 API 인스턴스
            inst_z_threshold: 기관 순매수 Z-score 임계값
            foreign_z_threshold: 외국인 순매수 Z-score 임계값
            order_imbalance_threshold: 호가 불균형 임계값
            lookback_days: 순매수 Z-score 계산 기간
        """
        self.api = api
        self.inst_z_threshold = inst_z_threshold
        self.foreign_z_threshold = foreign_z_threshold
        self.order_imbalance_threshold = order_imbalance_threshold
        self.lookback_days = lookback_days

        # 캐시 (1분간 유효)
        self.cache: Dict[str, Dict] = {}
        self.cache_expiry: Dict[str, datetime] = {}

    def _get_investor_trend_data(self, stock_code: str) -> Optional[pd.DataFrame]:
        """
        키움 API에서 투자자별 매매 동향 조회

        Args:
            stock_code: 종목코드

        Returns:
            투자자별 순매수 데이터 DataFrame
        """
        if not self.api:
            return None

        try:
            # 키움 API: get_investor_trend()
            result = self.api.get_investor_trend(
                stock_code=stock_code,
                amt_qty_tp="1",  # 금액
                trde_tp="0",     # 순매수
                unit_tp="1000"   # 천원
            )

            if result.get('return_code') != 0:
                return None

            # output에서 데이터 추출
            output = result.get('output', [])
            if not output:
                return None

            # DataFrame 변환
            df = pd.DataFrame(output)

            # 필요한 컬럼: 날짜, 기관, 외국인
            # 키움 API 응답 구조에 맞게 조정 필요
            # 예: dt, orgn(기관), frgnr_invsr(외국인)

            return df

        except Exception as e:
            console.print(f"[dim]⚠️  {stock_code} 투자자 동향 조회 실패: {e}[/dim]")
            return None

    def _get_order_book(self, stock_code: str) -> Optional[Dict]:
        """
        실시간 호가 잔량 조회

        Args:
            stock_code: 종목코드

        Returns:
            호가 데이터 dict
        """
        if not self.api:
            return None

        try:
            # 키움 API: get_stock_quote() 또는 실시간 호가 조회
            # 여기서는 간단히 get_stock_price로 대체
            result = self.api.get_stock_price(stock_code)

            if result.get('return_code') != 0:
                return None

            # 호가 데이터 추출
            # 매수호가 잔량 합계, 매도호가 잔량 합계
            # API 응답 구조에 맞게 조정

            return result.get('output', {})

        except Exception as e:
            console.print(f"[dim]⚠️  {stock_code} 호가 조회 실패: {e}[/dim]")
            return None

    def calculate_institutional_z_score(
        self,
        stock_code: str,
        investor_data: pd.DataFrame = None
    ) -> Tuple[float, float]:
        """
        기관 순매수 Z-score 계산

        Args:
            stock_code: 종목코드
            investor_data: 투자자 동향 데이터 (없으면 API 조회)

        Returns:
            (inst_z_score, foreign_z_score)
        """
        if investor_data is None:
            investor_data = self._get_investor_trend_data(stock_code)

        if investor_data is None or len(investor_data) < self.lookback_days:
            return 0.0, 0.0

        try:
            # 기관 순매수 (컬럼명은 실제 API 응답에 맞게 조정)
            # 예: 'orgn' or 'inst_net_buy'
            inst_col = None
            foreign_col = None

            # 컬럼 찾기
            for col in investor_data.columns:
                col_lower = str(col).lower()
                if 'orgn' in col_lower or 'inst' in col_lower:
                    inst_col = col
                if 'frgn' in col_lower or 'foreign' in col_lower:
                    foreign_col = col

            if inst_col is None or foreign_col is None:
                # 컬럼을 찾지 못한 경우 기본값
                return 0.0, 0.0

            # 최근 lookback_days 일 데이터
            inst_net_buy = investor_data[inst_col].tail(self.lookback_days)
            foreign_net_buy = investor_data[foreign_col].tail(self.lookback_days)

            # 수치형 변환
            inst_net_buy = pd.to_numeric(inst_net_buy, errors='coerce')
            foreign_net_buy = pd.to_numeric(foreign_net_buy, errors='coerce')

            # Z-score 계산
            inst_mean = inst_net_buy.mean()
            inst_std = inst_net_buy.std()

            if inst_std > 0:
                inst_z = (inst_net_buy.iloc[-1] - inst_mean) / inst_std
            else:
                inst_z = 0.0

            foreign_mean = foreign_net_buy.mean()
            foreign_std = foreign_net_buy.std()

            if foreign_std > 0:
                foreign_z = (foreign_net_buy.iloc[-1] - foreign_mean) / foreign_std
            else:
                foreign_z = 0.0

            return float(inst_z), float(foreign_z)

        except Exception as e:
            console.print(f"[dim]⚠️  {stock_code} Z-score 계산 실패: {e}[/dim]")
            return 0.0, 0.0

    def calculate_order_imbalance(
        self,
        stock_code: str,
        order_book: Dict = None
    ) -> float:
        """
        호가 잔량 불균형 계산

        Order Imbalance = (매수호가 잔량 - 매도호가 잔량) / (매수호가 잔량 + 매도호가 잔량)

        Args:
            stock_code: 종목코드
            order_book: 호가 데이터 (없으면 API 조회)

        Returns:
            order_imbalance (-1.0 ~ +1.0)
        """
        if order_book is None:
            order_book = self._get_order_book(stock_code)

        if not order_book:
            return 0.0

        try:
            # 호가 데이터에서 매수/매도 잔량 추출
            # 키움 API 응답 구조에 맞게 조정
            # 예: 'total_bid_qty', 'total_ask_qty'

            bid_qty = 0
            ask_qty = 0

            # 매수호가 1~10 합계
            for i in range(1, 11):
                bid_key = f'bid_qty{i}'
                ask_key = f'ask_qty{i}'

                if bid_key in order_book:
                    bid_qty += int(order_book.get(bid_key, 0))
                if ask_key in order_book:
                    ask_qty += int(order_book.get(ask_key, 0))

            # Order Imbalance 계산
            total_qty = bid_qty + ask_qty

            if total_qty > 0:
                order_imbalance = (bid_qty - ask_qty) / total_qty
            else:
                order_imbalance = 0.0

            return float(order_imbalance)

        except Exception as e:
            console.print(f"[dim]⚠️  {stock_code} Order Imbalance 계산 실패: {e}[/dim]")
            return 0.0

    def detect_shift(self, stock_code: str) -> Tuple[bool, float, str]:
        """
        수급 전환 감지

        Args:
            stock_code: 종목코드

        Returns:
            (shift_detected, strength, reason)
            shift_detected: 수급 전환 감지 여부
            strength: 수급 강도 (0.0~1.0)
            reason: 상세 이유
        """
        # 캐시 확인 (1분간 유효)
        now = datetime.now()
        cache_key = f"liquidity_{stock_code}"

        if cache_key in self.cache:
            if cache_key in self.cache_expiry and self.cache_expiry[cache_key] > now:
                cached = self.cache[cache_key]
                return cached['detected'], cached['strength'], cached['reason']

        # API가 없으면 기본값 반환
        if not self.api:
            return True, 0.5, "L4 API 미연결 (기본 통과)"

        # 1. 기관/외국인 순매수 Z-score
        inst_z, foreign_z = self.calculate_institutional_z_score(stock_code)

        # 2. 호가 불균형
        order_imbalance = self.calculate_order_imbalance(stock_code)

        # 3. 수급 전환 판단
        inst_strong = inst_z > self.inst_z_threshold
        foreign_strong = foreign_z > self.foreign_z_threshold
        order_strong = order_imbalance > self.order_imbalance_threshold

        # 조건: (기관 OR 외국인) AND 호가 불균형
        shift_detected = (inst_strong or foreign_strong) and order_strong

        # 수급 강도 계산 (0.0~1.0)
        # inst_z, foreign_z 중 큰 값 + order_imbalance
        max_z = max(inst_z, foreign_z, 0)
        strength = min((max_z / 3.0) * 0.7 + abs(order_imbalance) * 0.3, 1.0)

        # 이유 생성
        components = []
        if inst_strong:
            components.append(f"기관 Z={inst_z:.2f}")
        if foreign_strong:
            components.append(f"외인 Z={foreign_z:.2f}")
        if order_strong:
            components.append(f"호가={order_imbalance:+.2f}")

        if shift_detected:
            reason = f"수급 강세: {', '.join(components)}"
        else:
            reason = f"수급 약세: 기관 Z={inst_z:.2f}, 외인 Z={foreign_z:.2f}, 호가={order_imbalance:+.2f}"

        # 캐시 저장 (1분)
        self.cache[cache_key] = {
            'detected': shift_detected,
            'strength': strength,
            'reason': reason
        }
        self.cache_expiry[cache_key] = now + timedelta(minutes=1)

        return shift_detected, strength, reason

    def get_flow_data(self, stock_code: str) -> Optional[Dict]:
        """
        기관/외인 수급 데이터 조회 (InstitutionalFlowAlpha용)

        Returns:
            {
                "inst_net_buy": int,      # 기관 순매수 (원)
                "foreign_net_buy": int,   # 외인 순매수 (원)
                "total_traded_value": int # 총 거래대금 (원)
            }
        """
        if not self.api:
            return None

        try:
            # 투자자별 매매 동향 조회
            df = self._get_investor_trend_data(stock_code)

            if df is None or len(df) == 0:
                return None

            # 최근 1일 데이터 (가장 최근)
            latest = df.iloc[0] if len(df) > 0 else None
            if latest is None:
                return None

            # 키움 API 응답 구조에 따라 필드명 조정 필요
            # 예상 필드: orgn (기관), frgnr_invsr (외국인), etc.
            # 여기서는 일반적인 구조로 작성

            inst_net_buy = int(latest.get('orgn', 0)) if 'orgn' in latest else 0
            foreign_net_buy = int(latest.get('frgnr_invsr', 0)) if 'frgnr_invsr' in latest else 0

            # 총 거래대금 추정 (기관+외인+개인의 절대값 합)
            # 실제 API 응답에 total_traded_value가 있으면 그것 사용
            if 'total_traded_value' in latest:
                total_traded_value = int(latest['total_traded_value'])
            else:
                # 추정: 순매수의 절대값으로 대략적인 거래대금 계산
                individual = int(latest.get('indvdl', 0)) if 'indvdl' in latest else 0
                total_traded_value = abs(inst_net_buy) + abs(foreign_net_buy) + abs(individual)

            return {
                "inst_net_buy": inst_net_buy,
                "foreign_net_buy": foreign_net_buy,
                "total_traded_value": max(total_traded_value, 1)  # 0 방지
            }

        except Exception as e:
            console.print(f"[dim]⚠️  {stock_code} 수급 데이터 조회 실패: {e}[/dim]")
            return None


if __name__ == "__main__":
    """테스트 코드"""

    print("=" * 80)
    print("🧪 Liquidity Shift Detector 테스트")
    print("=" * 80)

    # API 없이 테스트
    detector = LiquidityShiftDetector(api=None)

    print("\n📊 수급 전환 감지 (API 미연결)")
    print("-" * 80)

    detected, strength, reason = detector.detect_shift('005930')

    print(f"  감지: {'✅ YES' if detected else '❌ NO'}")
    print(f"  강도: {strength * 100:.0f}%")
    print(f"  이유: {reason}")

    print("\n" + "=" * 80)
    print("✅ 테스트 완료")
    print("=" * 80)
    print("\n⚠️  실전 사용 시 키움 API 연동 필요:")
    print("  - get_investor_trend() 로 기관/외국인 순매수 조회")
    print("  - get_stock_quote() 또는 실시간 호가로 매수/매도 잔량 조회")
