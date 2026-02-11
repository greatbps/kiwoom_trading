"""
수급 분석 엔진 (정규화 + 보정 버전)
- 외국인/기관 중심의 정교한 수급 평가
- 규모, 지속성, 일관성 3가지 축 평가
- 정규화된 비율로 시가총액 차이 보정
"""
from typing import Dict, List, Any


class SupplyDemandAnalyzer:
    """수급 분석 엔진 (정규화 + 보정 시스템)"""

    def __init__(self):
        """초기화"""
        # 정규화 임계값 (norm_ratio 기준)
        self.thresholds = {
            'very_strong': 0.5,    # 50% 이상
            'strong': 0.2,          # 20% 이상
            'medium': 0.05          # 5% 이상
        }

        # 보정 가중치
        self.bonuses = {
            'consistency': 1.10,     # 일관성 (4일 이상 연속)
            'acceleration': 1.10,    # 가속 (최근 3일 증가)
            'volume_confirm': 1.05   # 거래량 동반
        }

    def component_score(self, net_buy_5d: float, avg_turnover_5d: float,
                       buy_days_5d: int, last3_trend_ok: bool,
                       avg_turnover_20d: float = None) -> float:
        """
        개별 구성요소 점수 계산 (외국인 또는 기관)

        Args:
            net_buy_5d: 5일 순매수 금액
            avg_turnover_5d: 5일 평균 거래대금
            buy_days_5d: 5일 중 순매수 일수 (0-5)
            last3_trend_ok: 최근 3일 가속 여부
            avg_turnover_20d: 20일 평균 거래대금 (거래량 확인용)

        Returns:
            점수 (0-50)
        """
        # 1) 정규화 비율 계산
        denom = max(avg_turnover_5d, 1)  # 0 방지
        norm_ratio = net_buy_5d / denom

        # 2) 기본 점수 (구간별 선형 매핑)
        if norm_ratio >= self.thresholds['very_strong']:
            # 매우 강한 매수: 50점
            score = 50.0
        elif norm_ratio >= self.thresholds['strong']:
            # 강한 매수: 선형 증가 (0 → 50)
            progress = (norm_ratio - self.thresholds['strong']) / (self.thresholds['very_strong'] - self.thresholds['strong'])
            score = 50.0 * progress
        elif norm_ratio >= self.thresholds['medium']:
            # 중간 매수: 완만한 증가 (0 → 25)
            progress = (norm_ratio - self.thresholds['medium']) / (self.thresholds['strong'] - self.thresholds['medium'])
            score = 25.0 * progress
        elif norm_ratio > 0:
            # 약한 매수: 매우 완만 (0 → 12.5)
            progress = norm_ratio / self.thresholds['medium']
            score = 12.5 * progress
        else:
            # 순매도: 0점
            score = 0.0

        # 3) 일관성 보너스 (4일 이상 연속 매수)
        if buy_days_5d >= 4:
            score *= self.bonuses['consistency']

        # 4) 가속 보너스 (최근 3일 증가 추세)
        if last3_trend_ok:
            score *= self.bonuses['acceleration']

        # 5) 거래량 확인 보너스
        if avg_turnover_20d and avg_turnover_5d > 0:
            volume_ratio = avg_turnover_5d / max(avg_turnover_20d, 1)
            if volume_ratio >= 1.2:  # 평상시 대비 120% 이상
                score *= self.bonuses['volume_confirm']

        # 최대 50점 제한
        return min(score, 50.0)

    def analyze_investor_trend(self, investor_data: List[Dict[str, Any]],
                               chart_data: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        투자자별 매매 동향 분석

        Args:
            investor_data: 투자자별 매매 동향 데이터 (5일 이상 권장)
            chart_data: 차트 데이터 (거래대금 계산용)

        Returns:
            투자자별 분석 결과
        """
        if not investor_data or len(investor_data) == 0:
            return {
                'foreign_score': 0,
                'institution_score': 0,
                'total_score': 50,  # 데이터 없음 시 중립
                'signals': ['수급 데이터 없음']
            }

        # 최근 5일 데이터 추출
        recent_5d = investor_data[:min(5, len(investor_data))]
        recent_20d = investor_data[:min(20, len(investor_data))]

        # 외국인 데이터 추출
        foreign_amounts = [self._parse_number(d.get('frgnr_invsr', '0')) for d in recent_5d]
        foreign_net_5d = sum(foreign_amounts)
        foreign_buy_days = sum(1 for amt in foreign_amounts if amt > 0)
        foreign_trend_ok = self._check_acceleration(foreign_amounts[-3:] if len(foreign_amounts) >= 3 else foreign_amounts)

        # 기관 데이터 추출
        inst_amounts = [self._parse_number(d.get('orgn', '0')) for d in recent_5d]
        inst_net_5d = sum(inst_amounts)
        inst_buy_days = sum(1 for amt in inst_amounts if amt > 0)
        inst_trend_ok = self._check_acceleration(inst_amounts[-3:] if len(inst_amounts) >= 3 else inst_amounts)

        # 거래대금 계산
        if chart_data and len(chart_data) >= 5:
            turnovers_5d = [self._parse_number(d.get('tot_trde_amt', '0')) for d in chart_data[:5]]
            avg_turnover_5d = sum(turnovers_5d) / len(turnovers_5d) if turnovers_5d else 1

            turnovers_20d = [self._parse_number(d.get('tot_trde_amt', '0')) for d in chart_data[:min(20, len(chart_data))]]
            avg_turnover_20d = sum(turnovers_20d) / len(turnovers_20d) if turnovers_20d else None
        else:
            # 차트 데이터 없으면 순매수 금액의 20배로 추정
            avg_turnover_5d = max(abs(foreign_net_5d), abs(inst_net_5d)) * 20
            avg_turnover_20d = avg_turnover_5d

        # 외국인 점수 계산
        foreign_score = self.component_score(
            net_buy_5d=foreign_net_5d,
            avg_turnover_5d=avg_turnover_5d,
            buy_days_5d=foreign_buy_days,
            last3_trend_ok=foreign_trend_ok,
            avg_turnover_20d=avg_turnover_20d
        )

        # 기관 점수 계산
        inst_score = self.component_score(
            net_buy_5d=inst_net_5d,
            avg_turnover_5d=avg_turnover_5d,
            buy_days_5d=inst_buy_days,
            last3_trend_ok=inst_trend_ok,
            avg_turnover_20d=avg_turnover_20d
        )

        # 총점 (외국인 + 기관) - 최대 50점으로 정규화
        total_score = min(foreign_score + inst_score, 100) / 2  # 100점 만점을 50점 만점으로 변환

        # 시그널 생성
        signals = []

        # 외국인 시그널
        if foreign_net_5d > 0:
            norm_ratio_f = foreign_net_5d / max(avg_turnover_5d, 1)
            signals.append(f"외국인 5일 순매수 {self._format_money(foreign_net_5d)}원 (비율: {norm_ratio_f:.1%}) [{foreign_score:.1f}점]")
            if foreign_buy_days >= 4:
                signals.append(f"  └ {foreign_buy_days}일 연속 매수 (일관성 ✅)")
            if foreign_trend_ok:
                signals.append(f"  └ 최근 3일 가속 (추세 ✅)")
        elif foreign_net_5d < 0:
            signals.append(f"외국인 5일 순매도 {self._format_money(abs(foreign_net_5d))}원 ❌")
        else:
            signals.append("외국인 보합 ➡️")

        # 기관 시그널
        if inst_net_5d > 0:
            norm_ratio_i = inst_net_5d / max(avg_turnover_5d, 1)
            signals.append(f"기관 5일 순매수 {self._format_money(inst_net_5d)}원 (비율: {norm_ratio_i:.1%}) [{inst_score:.1f}점]")
            if inst_buy_days >= 4:
                signals.append(f"  └ {inst_buy_days}일 연속 매수 (일관성 ✅)")
            if inst_trend_ok:
                signals.append(f"  └ 최근 3일 가속 (추세 ✅)")
        elif inst_net_5d < 0:
            signals.append(f"기관 5일 순매도 {self._format_money(abs(inst_net_5d))}원 ❌")
        else:
            signals.append("기관 보합 ➡️")

        # 일관성 분석 (둘 다 매수)
        if foreign_net_5d > 0 and inst_net_5d > 0:
            signals.append("🚀 외국인+기관 동반 매수 (일관성 우수)")
        elif foreign_net_5d < 0 and inst_net_5d < 0:
            signals.append("⚠️ 외국인+기관 동반 매도 (주의)")

        # 거래량 동반 확인
        if avg_turnover_20d and avg_turnover_5d / max(avg_turnover_20d, 1) >= 1.2:
            signals.append("📊 거래량 평상시 대비 120% 이상 (강도 ✅)")

        return {
            'foreign_score': round(foreign_score, 2),
            'institution_score': round(inst_score, 2),
            'total_score': round(min(total_score, 50), 2),  # 최대 50점
            'foreign_amount': foreign_net_5d,
            'institution_amount': inst_net_5d,
            'foreign_buy_days': foreign_buy_days,
            'inst_buy_days': inst_buy_days,
            'norm_ratio_foreign': foreign_net_5d / max(avg_turnover_5d, 1),
            'norm_ratio_inst': inst_net_5d / max(avg_turnover_5d, 1),
            'signals': signals
        }

    def analyze(self, investor_data: List[Dict[str, Any]] = None,
               chart_data: List[Dict[str, Any]] = None,
               program_data: List[Dict[str, Any]] = None,
               stock_code: str = None) -> Dict[str, Any]:
        """
        종합 수급 분석

        Args:
            investor_data: 투자자별 매매 동향 데이터
            chart_data: 차트 데이터 (거래대금 계산용)
            program_data: 프로그램 매매 데이터 (현재 미사용)
            stock_code: 종목코드

        Returns:
            수급 분석 결과
            - total_score: 총점 (0-100)
            - foreign: 외국인 분석
            - institution: 기관 분석
            - recommendation: 추천
        """
        # 투자자별 분석
        if investor_data:
            result = self.analyze_investor_trend(investor_data, chart_data)
        else:
            result = {
                'foreign_score': 0,
                'institution_score': 0,
                'total_score': 50,
                'foreign_amount': 0,
                'institution_amount': 0,
                'foreign_buy_days': 0,
                'inst_buy_days': 0,
                'norm_ratio_foreign': 0,
                'norm_ratio_inst': 0,
                'signals': ['수급 데이터 없음']
            }

        # 추천 판단 (50점 만점 기준)
        score = result['total_score']
        if score >= 42:  # 84% 이상 (50점 만점 기준)
            recommendation = "강한 동행 수급 (우수)"
        elif score >= 32:  # 64% 이상
            recommendation = "수급 양호"
        elif score >= 22:  # 44% 이상
            recommendation = "보통 (관망)"
        else:
            recommendation = "수급 약함"

        return {
            'total_score': result['total_score'],
            'recommendation': recommendation,
            'foreign': {
                'score': result['foreign_score'],
                'amount': result['foreign_amount'],
                'buy_days': result['foreign_buy_days'],
                'norm_ratio': result['norm_ratio_foreign']
            },
            'institution': {
                'score': result['institution_score'],
                'amount': result['institution_amount'],
                'buy_days': result['inst_buy_days'],
                'norm_ratio': result['norm_ratio_inst']
            },
            'signals': result['signals']
        }

    def _parse_number(self, value: str) -> float:
        """문자열을 숫자로 변환"""
        if not value or value == 'N/A':
            return 0.0

        try:
            cleaned = value.replace(',', '').replace('+', '').replace('-', '')
            # 원본 부호 확인
            sign = -1 if '-' in str(value) else 1
            return float(cleaned) * sign
        except (ValueError, AttributeError):
            return 0.0

    def _format_money(self, amount: float) -> str:
        """금액 포맷팅 (억/천만 단위)"""
        abs_amount = abs(amount)

        if abs_amount >= 100000000:  # 1억 이상
            return f"{amount / 100000000:.1f}억"
        elif abs_amount >= 10000000:  # 천만 이상
            return f"{amount / 10000000:.0f}천만"
        elif abs_amount >= 10000:  # 만 이상
            return f"{amount / 10000:.0f}만"
        else:
            return f"{amount:.0f}"

    def _check_acceleration(self, recent_amounts: List[float]) -> bool:
        """
        최근 데이터의 가속 여부 확인

        Args:
            recent_amounts: 최근 순매수 금액 리스트 (시간순)

        Returns:
            가속 여부
        """
        if len(recent_amounts) < 2:
            return False

        # 마지막 값이 평균보다 큰지 확인
        if len(recent_amounts) >= 3:
            avg = sum(recent_amounts[:-1]) / len(recent_amounts[:-1])
            return recent_amounts[-1] > avg and recent_amounts[-1] > 0
        else:
            # 2개 데이터면 증가 추세만 확인
            return recent_amounts[-1] > recent_amounts[-2] and recent_amounts[-1] > 0
