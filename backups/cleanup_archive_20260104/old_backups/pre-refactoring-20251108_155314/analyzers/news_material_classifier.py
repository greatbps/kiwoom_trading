"""
뉴스 재료 분류 모듈
뉴스를 단기/중기/장기 재료로 분류하고 투자 시간 프레임에 따라 점수 조정
"""
from typing import List, Dict, Tuple
import re


class NewsMaterialClassifier:
    """뉴스 재료 분류 클래스"""

    def __init__(self):
        """키워드 패턴 초기화"""
        # 단기재료 키워드 (Short-term: 즉각적 영향, 단기 모멘텀)
        self.short_term_keywords = {
            'M&A': ['인수합병', 'M&A', '인수', '합병', '피인수'],
            '유상증자': ['제3자배정', '유상증자', '증자', '제3자 배정'],
            '신규사업': ['신규사업', '신사업', '사업진출', '새로운 사업'],
            '임상결과': ['임상', '임상시험', '임상결과', '임상 통과', '신약 개발'],
            '정책수혜': ['정책수혜', '정부지원', '보조금', '정부정책', '규제완화'],
            '신규수주': ['신규수주', '수주', '계약체결', '납품계약', '공급계약'],
            '테마': ['테마주', '급등', '관심 집중', '화제'],
            '기관매수': ['기관매수', '외국인매수', '순매수', '대량매수'],
            '실적발표': ['어닝서프라이즈', '깜짝실적', '예상치 상회'],
            '배당': ['배당 확대', '배당증가', '특별배당', '분기배당'],
            '공모주': ['IPO', '상장', '신규상장', '공모'],
            '특허': ['특허출원', '특허등록', '독점권', '지적재산'],
            '이벤트': ['주주총회', '기업설명회', 'IR'],
            '공시': ['공시', '잠정실적', '실적발표'],
            '조직개편': ['경영진교체', 'CEO교체', '조직개편'],
            '소송': ['소송', '특허분쟁', '법적분쟁', '승소']
        }

        # 중기재료 키워드 (Mid-term: 3-12개월 영향)
        self.mid_term_keywords = {
            '산업호황': ['산업호황', '업황개선', '수요증가', '시장확대'],
            '턴어라운드': ['턴어라운드', '실적개선', '흑자전환', '수익성개선'],
            '최대실적': ['사상최대', '역대최대', '최대실적', '최고실적'],
            '대형계약': ['대형계약', '장기계약', '공급계약', '독점계약'],
            '해외진출': ['해외진출', '글로벌진출', '수출확대', '해외법인'],
            '원가절감': ['원가절감', '비용절감', '수익성개선', '마진개선'],
            '기술우위': ['기술우위', '기술력', '원천기술', '핵심기술'],
            '경쟁사': ['경쟁사이탈', '독과점', '시장점유율', '1위'],
            '구조조정': ['구조조정', '사업재편', '포트폴리오조정', '사업매각']
        }

        # 장기재료 키워드 (Long-term: 1년 이상 지속 영향)
        self.long_term_keywords = {
            '주주매입': ['최대주주매입', '대주주매입', '임원매수', '내부자거래'],
            '장기계약': ['장기공급', '장기납품', '프레임계약', '독점공급'],
            '자산취득': ['공장증설', '부동산취득', '설비투자', '신공장'],
            '자사주': ['자사주매입', '자사주소각', '자사주'],
            '부채상환': ['부채상환', '차입금상환', '재무구조개선', '부채비율개선'],
            '액면분할': ['액면분할', '주식분할'],
            '배당정책': ['배당정책', '배당성향', '주주환원', '지속배당'],
            'ESG': ['ESG', '친환경', '탄소중립', '지속가능'],
            '성장동력': ['신성장동력', '미래먹거리', '차세대', '혁신기술'],
            '핵심인재': ['핵심인재', '전문경영인', 'CEO영입', '임원영입'],
            '전략적제휴': ['전략적제휴', '협력계약', '조인트벤처', 'JV'],
            '사업다각화': ['사업다각화', '포트폴리오확대', '신규분야']
        }

    def classify(self, news_list: List[Dict]) -> Dict:
        """
        뉴스 리스트를 분석하여 재료 유형 분류

        Args:
            news_list: 뉴스 리스트 [{'title': ..., 'description': ...}, ...]

        Returns:
            {
                'short_term': [재료타입들],
                'mid_term': [재료타입들],
                'long_term': [재료타입들],
                'material_score': 재료 점수,
                'timeframe': 주요 시간프레임,
                'multiplier': 점수 배수
            }
        """
        if not news_list:
            return self._get_empty_result()

        # 모든 뉴스 텍스트 결합
        combined_text = self._combine_text(news_list)

        # 각 카테고리별 매칭
        short_matches = self._find_matches(combined_text, self.short_term_keywords)
        mid_matches = self._find_matches(combined_text, self.mid_term_keywords)
        long_matches = self._find_matches(combined_text, self.long_term_keywords)

        # 주요 시간프레임 결정
        timeframe, multiplier = self._determine_timeframe(
            len(short_matches),
            len(mid_matches),
            len(long_matches)
        )

        # 재료 점수 계산 (0-100)
        material_score = self._calculate_material_score(
            short_matches, mid_matches, long_matches
        )

        return {
            'short_term': short_matches,
            'mid_term': mid_matches,
            'long_term': long_matches,
            'material_score': material_score,
            'timeframe': timeframe,
            'multiplier': multiplier,
            'has_material': len(short_matches) + len(mid_matches) + len(long_matches) > 0
        }

    def _combine_text(self, news_list: List[Dict]) -> str:
        """뉴스 텍스트 결합"""
        texts = []
        for news in news_list:
            title = news.get('title', '')
            description = news.get('description', '')
            texts.append(f"{title} {description}")
        return " ".join(texts)

    def _find_matches(self, text: str, keyword_dict: Dict[str, List[str]]) -> List[str]:
        """키워드 매칭"""
        matches = []
        for category, keywords in keyword_dict.items():
            for keyword in keywords:
                if keyword in text:
                    matches.append(category)
                    break  # 카테고리당 1번만 카운트
        return matches

    def _determine_timeframe(self, short_count: int, mid_count: int, long_count: int) -> Tuple[str, float]:
        """
        주요 시간프레임 결정 및 점수 배수 반환

        장기재료 > 중기재료 > 단기재료 순으로 배수가 높음 (지속 가능성)

        Returns:
            (timeframe, multiplier)
        """
        if short_count == 0 and mid_count == 0 and long_count == 0:
            return 'NONE', 1.0

        # 가장 많이 매칭된 시간프레임 선택
        max_count = max(short_count, mid_count, long_count)

        if long_count == max_count:
            return 'LONG', 1.3   # 장기: 지속 가능한 호재 (최고 배수)
        elif mid_count == max_count:
            return 'MID', 1.2    # 중기: 중기 성장 동력
        else:
            return 'SHORT', 1.1  # 단기: 단기 모멘텀 (낮은 배수)

    def _calculate_material_score(self, short_matches: List[str],
                                   mid_matches: List[str],
                                   long_matches: List[str]) -> float:
        """
        재료 점수 계산 (0-100)

        장기재료 > 중기재료 > 단기재료 순으로 가중치가 높음 (지속 가능성)
        """
        # 각 시간프레임별 가중치 (장기 > 중기 > 단기)
        long_weight = 40   # 장기재료: 지속 가능한 가치 (최고 가중치)
        mid_weight = 35    # 중기재료: 성장 동력
        short_weight = 25  # 단기재료: 단기 모멘텀 (낮은 가중치)

        # 매칭 개수에 따른 점수 (최대 3개까지만 유의미)
        short_score = min(len(short_matches), 3) * (short_weight / 3)
        mid_score = min(len(mid_matches), 3) * (mid_weight / 3)
        long_score = min(len(long_matches), 3) * (long_weight / 3)

        total_score = short_score + mid_score + long_score

        # 복합 재료 보너스 (여러 시간프레임에 걸친 재료가 있으면 +10점)
        timeframes_present = sum([
            1 if short_matches else 0,
            1 if mid_matches else 0,
            1 if long_matches else 0
        ])

        if timeframes_present >= 2:
            total_score += 10

        return min(round(total_score, 2), 100)

    def _get_empty_result(self) -> Dict:
        """빈 결과 반환"""
        return {
            'short_term': [],
            'mid_term': [],
            'long_term': [],
            'material_score': 0,
            'timeframe': 'NONE',
            'multiplier': 1.0,
            'has_material': False
        }


if __name__ == "__main__":
    # 테스트
    classifier = NewsMaterialClassifier()

    # 테스트 뉴스
    test_news = [
        {
            'title': '삼성전자, 2조원 규모 자사주 매입 결정',
            'description': '주주가치 제고를 위한 자사주 매입 및 소각 계획 발표'
        },
        {
            'title': '대형 수주 계약 체결',
            'description': '글로벌 기업과 3년간 독점 공급 계약'
        },
        {
            'title': '신약 임상 3상 통과',
            'description': '미국 FDA 승인 기대감 고조'
        }
    ]

    result = classifier.classify(test_news)

    print("=== 뉴스 재료 분석 결과 ===")
    print(f"단기재료: {result['short_term']}")
    print(f"중기재료: {result['mid_term']}")
    print(f"장기재료: {result['long_term']}")
    print(f"재료 점수: {result['material_score']}")
    print(f"주요 시간프레임: {result['timeframe']}")
    print(f"점수 배수: {result['multiplier']}")
    print(f"재료 존재: {result['has_material']}")
