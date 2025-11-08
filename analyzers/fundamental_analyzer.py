"""
기본(펀더멘털) 분석 엔진 - 업종 상대평가 기반
- 밸류에이션 30점: PER, PBR (업종 평균 대비)
- 수익성 20점: ROE (업종 평균 대비)
- 총 50점 (수급 50점과 합쳐 100점)
"""
import sys
import os
from typing import Dict, Any, Optional

# DB 매니저 임포트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.sector_data_manager import SectorDataManager


class FundamentalAnalyzer:
    """기본(펀더멘털) 분석 엔진 - 업종 상대평가"""

    def __init__(self):
        """초기화"""
        # 지표별 배점 (총 50점)
        self.weights = {
            'valuation': 30,      # 밸류에이션 (PER 15 + PBR 15)
            'profitability': 20,  # 수익성 (ROE)
        }

        # DB 매니저
        self.db_manager = None

    def _get_db_manager(self) -> SectorDataManager:
        """DB 매니저 싱글톤"""
        if self.db_manager is None:
            self.db_manager = SectorDataManager()
            self.db_manager.connect()
        return self.db_manager

    def _parse_number(self, value: str) -> Optional[float]:
        """문자열을 숫자로 변환"""
        if not value or not isinstance(value, str):
            return None

        try:
            # 쉼표, +/- 기호, % 제거
            cleaned = value.replace(',', '').replace('+', '').replace('-', '').replace('%', '').strip()
            if not cleaned:
                return None
            return float(cleaned)
        except (ValueError, AttributeError):
            return None

    def get_sector_averages(self, sector_name: str, market_type: str = "0") -> Optional[Dict[str, float]]:
        """
        업종 평균 조회 (DB에서)

        Args:
            sector_name: 업종명 (예: "반도체")
            market_type: 시장구분 (0:코스피, 10:코스닥)

        Returns:
            {'avg_per': float, 'avg_pbr': float, 'avg_roe': float} or None
        """
        try:
            db = self._get_db_manager()

            # 업종명으로 직접 조회
            sector_data = db.get_sector_averages_by_name(sector_name, market_type)

            if sector_data:
                # Decimal to float conversion
                avg_per = sector_data.get('avg_per')
                avg_pbr = sector_data.get('avg_pbr')
                avg_roe = sector_data.get('avg_roe')

                return {
                    'avg_per': float(avg_per) if avg_per is not None else None,
                    'avg_pbr': float(avg_pbr) if avg_pbr is not None else None,
                    'avg_roe': float(avg_roe) if avg_roe is not None else None
                }

            return None

        except Exception as e:
            print(f"⚠ 업종 평균 조회 실패: {e}")
            return None

    def analyze_valuation(self, stock_info: Dict[str, Any], sector_avg: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """
        밸류에이션 분석 (PER, PBR) - 업종 상대평가
        총 30점 (PER 15점 + PBR 15점)

        Args:
            stock_info: 주식 기본정보
            sector_avg: 업종 평균 {'avg_per': float, 'avg_pbr': float}

        Returns:
            밸류에이션 분석 결과
        """
        signals = []
        per_score = 0
        pbr_score = 0

        # PER 분석 (15점 만점)
        per = self._parse_number(stock_info.get('per', ''))
        avg_per = sector_avg.get('avg_per') if sector_avg else None

        if per and per > 0:
            if avg_per and avg_per > 0:
                # 업종 평균 대비 상대평가
                ratio = per / avg_per

                if ratio <= 0.5:  # 업종 평균의 50% 이하
                    per_score = 15
                    signals.append(f"PER {per:.1f} (업종평균 {avg_per:.1f}의 {ratio*100:.0f}%) - 매우 저평가 ✅✅")
                elif ratio <= 0.7:  # 70% 이하
                    per_score = 12
                    signals.append(f"PER {per:.1f} (업종평균 {avg_per:.1f}의 {ratio*100:.0f}%) - 저평가 ✅")
                elif ratio <= 0.9:  # 90% 이하
                    per_score = 9
                    signals.append(f"PER {per:.1f} (업종평균 {avg_per:.1f}의 {ratio*100:.0f}%) - 양호 ➡️")
                elif ratio <= 1.1:  # 90~110% (업종 평균 수준)
                    per_score = 7.5
                    signals.append(f"PER {per:.1f} (업종평균 {avg_per:.1f}의 {ratio*100:.0f}%) - 평균 수준 ➡️")
                elif ratio <= 1.3:  # 130% 이하
                    per_score = 5
                    signals.append(f"PER {per:.1f} (업종평균 {avg_per:.1f}의 {ratio*100:.0f}%) - 다소 고평가 ⚠️")
                elif ratio <= 1.5:  # 150% 이하
                    per_score = 2.5
                    signals.append(f"PER {per:.1f} (업종평균 {avg_per:.1f}의 {ratio*100:.0f}%) - 고평가 ⚠️")
                else:  # 150% 초과
                    per_score = 0
                    signals.append(f"PER {per:.1f} (업종평균 {avg_per:.1f}의 {ratio*100:.0f}%) - 매우 고평가 ❌")
            else:
                # 업종 평균 없음 → 절대평가 (기본 점수만)
                if per < 10:
                    per_score = 10
                    signals.append(f"PER {per:.1f} - 저평가 (업종평균 없음)")
                elif per < 20:
                    per_score = 7.5
                    signals.append(f"PER {per:.1f} - 적정 (업종평균 없음)")
                else:
                    per_score = 5
                    signals.append(f"PER {per:.1f} - 고평가 (업종평균 없음)")
        else:
            signals.append("PER 데이터 없음 (적자 또는 미제공)")

        # PBR 분석 (15점 만점)
        pbr = self._parse_number(stock_info.get('pbr', ''))
        avg_pbr = sector_avg.get('avg_pbr') if sector_avg else None

        if pbr and pbr > 0:
            if avg_pbr and avg_pbr > 0:
                # 업종 평균 대비 상대평가
                ratio = pbr / avg_pbr

                if ratio <= 0.5:  # 업종 평균의 50% 이하
                    pbr_score = 15
                    signals.append(f"PBR {pbr:.2f} (업종평균 {avg_pbr:.2f}의 {ratio*100:.0f}%) - 매우 저평가 ✅✅")
                elif ratio <= 0.7:  # 70% 이하
                    pbr_score = 12
                    signals.append(f"PBR {pbr:.2f} (업종평균 {avg_pbr:.2f}의 {ratio*100:.0f}%) - 저평가 ✅")
                elif ratio <= 0.9:  # 90% 이하
                    pbr_score = 9
                    signals.append(f"PBR {pbr:.2f} (업종평균 {avg_pbr:.2f}의 {ratio*100:.0f}%) - 양호 ➡️")
                elif ratio <= 1.1:  # 90~110% (업종 평균 수준)
                    pbr_score = 7.5
                    signals.append(f"PBR {pbr:.2f} (업종평균 {avg_pbr:.2f}의 {ratio*100:.0f}%) - 평균 수준 ➡️")
                elif ratio <= 1.3:  # 130% 이하
                    pbr_score = 5
                    signals.append(f"PBR {pbr:.2f} (업종평균 {avg_pbr:.2f}의 {ratio*100:.0f}%) - 다소 고평가 ⚠️")
                elif ratio <= 1.5:  # 150% 이하
                    pbr_score = 2.5
                    signals.append(f"PBR {pbr:.2f} (업종평균 {avg_pbr:.2f}의 {ratio*100:.0f}%) - 고평가 ⚠️")
                else:  # 150% 초과
                    pbr_score = 0
                    signals.append(f"PBR {pbr:.2f} (업종평균 {avg_pbr:.2f}의 {ratio*100:.0f}%) - 매우 고평가 ❌")
            else:
                # 업종 평균 없음 → 절대평가
                if pbr < 1.0:
                    pbr_score = 10
                    signals.append(f"PBR {pbr:.2f} - 저평가 (업종평균 없음)")
                elif pbr < 2.0:
                    pbr_score = 7.5
                    signals.append(f"PBR {pbr:.2f} - 적정 (업종평균 없음)")
                else:
                    pbr_score = 5
                    signals.append(f"PBR {pbr:.2f} - 고평가 (업종평균 없음)")
        else:
            signals.append("PBR 데이터 없음")

        # 총점 계산 (30점 만점)
        total_score = per_score + pbr_score

        return {
            'score': total_score,
            'signals': signals,
            'per': per,
            'pbr': pbr,
            'per_score': per_score,
            'pbr_score': pbr_score
        }

    def analyze_profitability(self, stock_info: Dict[str, Any], sector_avg: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """
        수익성 분석 (ROE) - 업종 상대평가
        총 20점

        Args:
            stock_info: 주식 기본정보
            sector_avg: 업종 평균 {'avg_roe': float}

        Returns:
            수익성 분석 결과
        """
        signals = []
        score = 0

        # ROE 분석 (20점 만점)
        roe = self._parse_number(stock_info.get('roe', ''))
        avg_roe = sector_avg.get('avg_roe') if sector_avg else None

        if roe is not None:
            if avg_roe and avg_roe > 0:
                # 업종 평균 대비 상대평가
                diff = roe - avg_roe  # 차이 (절대값)

                if diff >= 10:  # 업종 평균 +10%p 이상
                    score = 20
                    signals.append(f"ROE {roe:.1f}% (업종평균 {avg_roe:.1f}% +{diff:.1f}%p) - 탁월 ✅✅")
                elif diff >= 5:  # +5%p 이상
                    score = 16
                    signals.append(f"ROE {roe:.1f}% (업종평균 {avg_roe:.1f}% +{diff:.1f}%p) - 우수 ✅")
                elif diff >= 2:  # +2%p 이상
                    score = 12
                    signals.append(f"ROE {roe:.1f}% (업종평균 {avg_roe:.1f}% +{diff:.1f}%p) - 양호 ➡️")
                elif diff >= -2:  # ±2%p 이내 (평균 수준)
                    score = 10
                    signals.append(f"ROE {roe:.1f}% (업종평균 {avg_roe:.1f}% {diff:+.1f}%p) - 평균 수준 ➡️")
                elif diff >= -5:  # -5%p 이상
                    score = 6
                    signals.append(f"ROE {roe:.1f}% (업종평균 {avg_roe:.1f}% {diff:.1f}%p) - 다소 낮음 ⚠️")
                elif diff >= -10:  # -10%p 이상
                    score = 3
                    signals.append(f"ROE {roe:.1f}% (업종평균 {avg_roe:.1f}% {diff:.1f}%p) - 낮음 ⚠️")
                else:  # -10%p 미만
                    score = 0
                    signals.append(f"ROE {roe:.1f}% (업종평균 {avg_roe:.1f}% {diff:.1f}%p) - 매우 낮음 ❌")
            else:
                # 업종 평균 없음 → 절대평가
                if roe >= 20:
                    score = 16
                    signals.append(f"ROE {roe:.1f}% - 우수 (업종평균 없음)")
                elif roe >= 15:
                    score = 12
                    signals.append(f"ROE {roe:.1f}% - 양호 (업종평균 없음)")
                elif roe >= 10:
                    score = 10
                    signals.append(f"ROE {roe:.1f}% - 보통 (업종평균 없음)")
                elif roe >= 5:
                    score = 6
                    signals.append(f"ROE {roe:.1f}% - 낮음 (업종평균 없음)")
                else:
                    score = 3
                    signals.append(f"ROE {roe:.1f}% - 매우 낮음 (업종평균 없음)")
        else:
            signals.append("ROE 데이터 없음")

        return {
            'score': score,
            'signals': signals,
            'roe': roe
        }

    def analyze(self, stock_info: Dict[str, Any], sector_name: str = None, market_type: str = "0") -> Dict[str, Any]:
        """
        종합 기본 분석 (총 50점)

        Args:
            stock_info: 주식 기본정보 (ka10001 API 응답)
            sector_name: 업종명 (외부에서 제공, 없으면 기본값 사용)
            market_type: 시장구분 (0:코스피, 10:코스닥)

        Returns:
            기본 분석 결과
            {
                'score': 0~50,
                'valuation_score': 0~30,
                'profitability_score': 0~20,
                'signals': [...],
                'details': {...}
            }
        """
        signals = []

        # 업종 정보 추출 (우선순위: 파라미터 > stock_info > 기본값)
        if not sector_name:
            sector_name = stock_info.get('upName', '')

        # 업종 평균 조회
        sector_avg = None
        if sector_name:
            sector_avg = self.get_sector_averages(sector_name, market_type)
            if sector_avg:
                avg_per = sector_avg.get('avg_per')
                avg_pbr = sector_avg.get('avg_pbr')
                avg_roe = sector_avg.get('avg_roe')
                per_str = f"{avg_per:.1f}" if avg_per else "N/A"
                pbr_str = f"{avg_pbr:.2f}" if avg_pbr else "N/A"
                roe_str = f"{avg_roe:.1f}" if avg_roe else "N/A"
                signals.append(f"📊 업종: {sector_name} (평균 PER {per_str}, PBR {pbr_str}, ROE {roe_str}%)")
            else:
                signals.append(f"⚠ 업종: {sector_name} (평균 데이터 없음 - 절대평가)")
        else:
            signals.append("⚠ 업종 정보 없음 (절대평가)")

        # 1. 밸류에이션 분석 (30점)
        valuation_result = self.analyze_valuation(stock_info, sector_avg)
        valuation_score = valuation_result['score']
        signals.extend(valuation_result['signals'])

        # 2. 수익성 분석 (20점)
        profitability_result = self.analyze_profitability(stock_info, sector_avg)
        profitability_score = profitability_result['score']
        signals.extend(profitability_result['signals'])

        # 총점 계산 (50점 만점)
        total_score = valuation_score + profitability_score

        return {
            'score': total_score,
            'valuation_score': valuation_score,
            'profitability_score': profitability_score,
            'signals': signals,
            'details': {
                'valuation': valuation_result,
                'profitability': profitability_result
            }
        }

    def close(self):
        """리소스 정리"""
        if self.db_manager:
            self.db_manager.close()
            self.db_manager = None

    def __del__(self):
        """소멸자"""
        self.close()
