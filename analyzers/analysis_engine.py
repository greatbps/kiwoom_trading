"""
통합 분석 엔진
- 뉴스, 기술적, 수급, 기본 분석을 통합
- 가중치: 뉴스 30% + 기술적 40% + 수급 15% + 기본 15%
- 수급/기본: 각 50점 만점 (업종 상대평가)
- 시장 상황(Market Regime)에 따른 보정
"""
from typing import Dict, Any, Optional, List
from .news_analyzer import NewsAnalyzer
from .sentiment_analyzer import SentimentAnalyzer
from .technical_analyzer import TechnicalAnalyzer
from .supply_demand_analyzer import SupplyDemandAnalyzer
from .fundamental_analyzer import FundamentalAnalyzer


class AnalysisEngine:
    """통합 분석 엔진"""

    def __init__(self):
        """초기화"""
        # 각 분석 엔진 인스턴스 생성
        self.news_analyzer = NewsAnalyzer()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.technical_analyzer = TechnicalAnalyzer()
        self.supply_demand_analyzer = SupplyDemandAnalyzer()
        self.fundamental_analyzer = FundamentalAnalyzer()

        # 종목-업종 매핑 캐시
        self.stock_sector_cache = {}

        # 전체 가중치 로드 (ConfigManager 사용)
        self.weights = self._load_weights()

        # 시장 상황별 보정 계수 (0.8 ~ 1.2)
        self.market_regime_coefficients = {
            'bull': 1.2,        # 강세장 (상승 신호 강화)
            'neutral': 1.0,     # 중립
            'bear': 0.8         # 약세장 (보수적 접근)
        }

        # 키워드 감성 분석용 키워드 (docs/news_collector.py 기반)
        self.positive_keywords = [
            "상승", "증가", "호조", "개선", "성장", "확대", "신고가", "돌파", "수혜", "기대",
            "긍정", "유리", "강세", "급등", "상승세", "반등", "회복", "성공", "선전", "대박",
            "실적개선", "매출증가", "이익증가", "시장점유율", "신제품", "수주", "계약", "협력"
        ]

        self.negative_keywords = [
            "하락", "감소", "부진", "악화", "축소", "신저가", "하락세", "급락", "폭락",
            "부정", "불리", "약세", "우려", "위험", "손실", "적자", "부실", "위기", "충격",
            "실적악화", "매출감소", "이익감소", "규제", "조사", "소송", "분쟁", "적자"
        ]

    def _calculate_keyword_sentiment(self, news_list: List[Dict]) -> float:
        """
        키워드 기반 감성 점수 계산 (docs/news_collector.py 로직 적용)

        Args:
            news_list: 뉴스 리스트

        Returns:
            감성 점수 (-1.0 ~ 1.0)
        """
        total_sentiment = 0.0
        total_words = 0

        for news in news_list:
            # title과 description 모두 분석
            text = f"{news.get('title', '')} {news.get('description', '')}".lower()
            words = text.split()
            total_words += len(words)

            for word in words:
                # 긍정 키워드 체크
                if any(pos in word for pos in self.positive_keywords):
                    total_sentiment += 1
                # 부정 키워드 체크
                elif any(neg in word for neg in self.negative_keywords):
                    total_sentiment -= 1

        if total_words == 0:
            return 0.0

        # -1 ~ 1 범위로 정규화
        sentiment_score = total_sentiment / total_words
        return max(-1.0, min(1.0, sentiment_score * 10))  # 10배 증폭 후 제한

    def _load_weights(self) -> Dict[str, float]:
        """
        설정 파일에서 가중치 로드

        Returns:
            가중치 딕셔너리 (퍼센트 값, 합계 100)
        """
        try:
            from utils.config_manager import ConfigManager
            config_manager = ConfigManager()
            weights_decimal = config_manager.get_weights()

            # 0-1 범위를 0-100 범위로 변환 (기존 로직과 호환성 유지)
            weights_percent = {k: v * 100 for k, v in weights_decimal.items()}

            return weights_percent
        except Exception as e:
            # 기본 가중치 사용
            print(f"⚠️  설정 파일 로드 실패, 기본 가중치 사용: {e}")
            return {
                'news': 30,           # 뉴스 분석 (100점 만점)
                'technical': 40,      # 기술적 분석 (100점 만점, 가장 높음)
                'supply_demand': 15,  # 수급 분석 (50점 만점)
                'fundamental': 15     # 기본 분석 (50점 만점, 업종 상대평가)
            }

    def analyze_news(self, stock_code: str, stock_name: str) -> Dict[str, Any]:
        """
        뉴스 분석 실행

        Args:
            stock_code: 종목코드
            stock_name: 종목명

        Returns:
            뉴스 분석 결과
        """
        try:
            # 뉴스 수집
            news_data = self.news_analyzer.analyze_news(stock_code, stock_name)
            news_list = news_data.get('news_list', [])
            frequency_score = news_data.get('frequency_score', 50)

            # 디버깅: 뉴스 수집 결과
            print(f"  [dim]뉴스 수집: {len(news_list)}건[/dim]")

            if not news_list:
                print(f"  [dim]⚠️  뉴스 없음 → 기본점수 50점[/dim]")
                return {
                    'score': 50,
                    'sentiment': 'neutral',
                    'confidence': 0,
                    'signals': ['뉴스 데이터 없음']
                }

            # ── 키워드 기반 감성 분석 ────────────────────────────────────────────
            sentiment_score = self._calculate_keyword_sentiment(news_list)
            sentiment_normalized = (sentiment_score + 1) * 50
            final_score = frequency_score * 0.5 + sentiment_normalized * 0.5

            if sentiment_score >= 0.3:
                sentiment_level = "positive"
            elif sentiment_score <= -0.3:
                sentiment_level = "negative"
            else:
                sentiment_level = "neutral"

            print(f"  [dim]키워드 감성: {sentiment_level} ({sentiment_score:.2f}), 최종 점수: {final_score:.1f}[/dim]")
            signals = [
                f"감성: {sentiment_level} (키워드 기반)",
                f"뉴스 건수: {len(news_list)}건",
                f"빈도 점수: {frequency_score:.0f}",
                f"감성 점수: {sentiment_normalized:.0f}",
            ]
            return {
                'score': final_score,
                'sentiment': sentiment_level,
                'confidence': abs(sentiment_score) * 100,
                'impact': min(len(news_list) * 2, 10),
                'news_count': len(news_list),
                'frequency_score': frequency_score,
                'sentiment_score_raw': sentiment_score,
                'material_analysis': None,
                'signals': signals,
                'provider': 'keyword',
            }

        except Exception as e:
            print(f"뉴스 분석 오류: {e}")
            return {
                'score': 50,
                'sentiment': 'neutral',
                'confidence': 0,
                'signals': [f'뉴스 분석 오류: {str(e)}']
            }

    def analyze_technical(self, chart_data: list) -> Dict[str, Any]:
        """
        기술적 분석 실행

        Args:
            chart_data: 차트 데이터 (일봉 또는 분봉)

        Returns:
            기술적 분석 결과
        """
        try:
            if chart_data is None or (isinstance(chart_data, list) and len(chart_data) == 0):
                return {
                    'score': 50,
                    'signals': ['차트 데이터 없음']
                }

            result = self.technical_analyzer.analyze(chart_data)

            # 주요 시그널 추출
            signals = []
            if result.get('trend'):
                signals.append(f"추세: {result['trend']['score']:.0f}점")
            if result.get('momentum'):
                signals.append(f"모멘텀: {result['momentum']['score']:.0f}점")
            if result.get('recommendation'):
                signals.append(f"추천: {result['recommendation']}")

            return {
                'score': result.get('total_score', 50),
                'recommendation': result.get('recommendation', '관망'),
                'signals': signals,
                'details': result
            }

        except Exception as e:
            import traceback
            print(f"기술적 분석 오류: {e}")
            print(f"[dim]상세: {traceback.format_exc()}[/dim]")
            return {
                'score': 50,
                'signals': [f'기술적 분석 오류: {str(e)}']
            }

    def analyze_supply_demand(self, investor_data: list = None,
                             program_data: list = None,
                             chart_data: list = None,
                             stock_code: str = None) -> Dict[str, Any]:
        """
        수급 분석 실행

        Args:
            investor_data: 투자자별 매매 동향
            program_data: 프로그램 매매 데이터
            chart_data: 차트 데이터 (거래대금 계산용)
            stock_code: 종목코드

        Returns:
            수급 분석 결과
        """
        try:
            result = self.supply_demand_analyzer.analyze(
                investor_data=investor_data,
                program_data=program_data,
                chart_data=chart_data,
                stock_code=stock_code
            )

            # 주요 시그널 추출
            signals = []
            if result.get('foreign'):
                signals.append(f"외국인: {result['foreign']['score']:.0f}점")
            if result.get('institution'):
                signals.append(f"기관: {result['institution']['score']:.0f}점")
            if result.get('recommendation'):
                signals.append(f"추천: {result['recommendation']}")

            return {
                'score': result.get('total_score', 50),
                'recommendation': result.get('recommendation', '관망'),
                'signals': signals,
                'details': result
            }

        except Exception as e:
            print(f"수급 분석 오류: {e}")
            return {
                'score': 50,
                'signals': [f'수급 분석 오류: {str(e)}']
            }

    def get_sector_name(self, stock_code: str) -> Optional[str]:
        """
        종목코드로 업종명 조회 (캐시 사용)

        Args:
            stock_code: 종목코드

        Returns:
            업종명 또는 None
        """
        # 캐시 확인
        if stock_code in self.stock_sector_cache:
            return self.stock_sector_cache[stock_code]

        # DB에서 조회
        try:
            import psycopg2
            import os
            from dotenv import load_dotenv

            load_dotenv()

            conn = psycopg2.connect(
                host=os.getenv('POSTGRES_HOST', 'localhost'),
                port=int(os.getenv('POSTGRES_PORT', 5432)),
                database=os.getenv('POSTGRES_DB', 'trading_system'),
                user=os.getenv('POSTGRES_USER', 'postgres'),
                password=os.getenv('POSTGRES_PASSWORD', '')
            )

            cursor = conn.cursor()
            cursor.execute(
                "SELECT sector_name FROM stock_sector_mapping WHERE stock_code = %s LIMIT 1",
                (stock_code,)
            )
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if result:
                sector_name = result[0]
                # 캐시에 저장
                self.stock_sector_cache[stock_code] = sector_name
                return sector_name

            return None

        except Exception as e:
            # DB 조회 실패 시 None 반환
            return None

    def analyze_fundamental(self, stock_info: Dict[str, Any], stock_code: str = None) -> Dict[str, Any]:
        """
        기본 분석 실행

        Args:
            stock_info: 주식 기본정보
            stock_code: 종목코드 (업종 조회용)

        Returns:
            기본 분석 결과
        """
        try:
            if not stock_info:
                return {
                    'score': 25,  # 50점 만점의 기본값 25
                    'signals': ['기본정보 없음']
                }

            # 업종명 조회 시도
            sector_name = self.get_sector_name(stock_code) if stock_code else None

            result = self.fundamental_analyzer.analyze(stock_info, sector_name=sector_name)

            # 주요 시그널 추출
            signals = []
            if result.get('valuation_score') is not None:
                signals.append(f"밸류에이션: {result['valuation_score']:.0f}점")
            if result.get('profitability_score') is not None:
                signals.append(f"수익성: {result['profitability_score']:.0f}점")

            return {
                'score': result.get('score', 25),  # 'total_score' → 'score'로 수정
                'recommendation': '관망',  # fundamental_analyzer는 recommendation을 반환하지 않음
                'signals': signals,
                'details': result
            }

        except Exception as e:
            print(f"기본 분석 오류: {e}")
            return {
                'score': 50,
                'signals': [f'기본 분석 오류: {str(e)}']
            }

    def detect_market_regime(self, technical_result: Dict[str, Any],
                            supply_demand_result: Dict[str, Any]) -> str:
        """
        시장 상황 감지 (강세/중립/약세)

        Args:
            technical_result: 기술적 분석 결과
            supply_demand_result: 수급 분석 결과

        Returns:
            'bull', 'neutral', 'bear'
        """
        # 기술적 점수
        tech_score = technical_result.get('score', 50)

        # 수급 점수
        supply_score = supply_demand_result.get('score', 50)

        # 외국인+기관 합계
        foreign_score = supply_demand_result.get('details', {}).get('foreign', {}).get('score', 50)
        inst_score = supply_demand_result.get('details', {}).get('institution', {}).get('score', 50)

        # 종합 시장 점수
        market_score = (tech_score * 0.6 + supply_score * 0.4)

        # 시장 상황 판단
        if market_score >= 65 and foreign_score >= 60 and inst_score >= 60:
            return 'bull'  # 강세장
        elif market_score <= 35:
            return 'bear'  # 약세장
        else:
            return 'neutral'  # 중립

    def calculate_final_score(self, news_result: Dict[str, Any],
                             technical_result: Dict[str, Any],
                             supply_demand_result: Dict[str, Any],
                             fundamental_result: Dict[str, Any],
                             market_regime: str) -> float:
        """
        최종 점수 계산 (가중치 적용 + 시장 보정)

        Args:
            news_result: 뉴스 분석 결과
            technical_result: 기술적 분석 결과
            supply_demand_result: 수급 분석 결과
            fundamental_result: 기본 분석 결과
            market_regime: 시장 상황

        Returns:
            최종 점수 (0-100)
        """
        # 각 분석 점수
        news_score = news_result.get('score', 50)          # 100점 만점
        tech_score = technical_result.get('score', 50)     # 100점 만점
        supply_score = supply_demand_result.get('score', 25)  # 50점 만점 (기본값 25)
        fundamental_score = fundamental_result.get('score', 25)  # 50점 만점 (기본값 25)

        # 50점 만점 → 100점 만점으로 정규화
        supply_score_normalized = supply_score * 2  # 50점 → 100점 변환
        fundamental_score_normalized = fundamental_score * 2  # 50점 → 100점 변환

        # 가중 평균 (모두 100점 만점 기준)
        weighted_score = (
            news_score * self.weights['news'] +
            tech_score * self.weights['technical'] +
            supply_score_normalized * self.weights['supply_demand'] +
            fundamental_score_normalized * self.weights['fundamental']
        ) / 100

        # 시장 상황 보정 계수 적용
        coefficient = self.market_regime_coefficients.get(market_regime, 1.0)

        # 보정 적용 (50점 기준으로 보정)
        final_score = 50 + (weighted_score - 50) * coefficient

        # 0-100 범위로 제한
        final_score = max(0, min(100, final_score))

        return round(final_score, 2)

    def generate_recommendation(self, final_score: float,
                               news_result: Dict[str, Any],
                               technical_result: Dict[str, Any],
                               supply_demand_result: Dict[str, Any],
                               fundamental_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        투자 추천 생성

        Args:
            final_score: 최종 점수
            news_result: 뉴스 분석 결과
            technical_result: 기술적 분석 결과
            supply_demand_result: 수급 분석 결과
            fundamental_result: 기본 분석 결과

        Returns:
            추천 및 근거
        """
        # 기본 추천
        if final_score >= 70:
            recommendation = "적극 매수"
            action = "BUY"
        elif final_score >= 60:
            recommendation = "매수"
            action = "BUY"
        elif final_score >= 50:
            recommendation = "관망"
            action = "HOLD"
        elif final_score >= 40:
            recommendation = "매도 고려"
            action = "SELL"
        else:
            recommendation = "매도"
            action = "SELL"

        # 근거 생성
        reasons = []

        # 긍정 요인
        positive_factors = []
        if news_result.get('score', 50) >= 60:
            positive_factors.append(f"뉴스 긍정 ({news_result.get('score', 0):.0f}점)")
        if technical_result.get('score', 50) >= 60:
            positive_factors.append(f"기술적 강세 ({technical_result.get('score', 0):.0f}점)")
        if supply_demand_result.get('score', 50) >= 60:
            positive_factors.append(f"수급 양호 ({supply_demand_result.get('score', 0):.0f}점)")
        if fundamental_result.get('score', 50) >= 60:
            positive_factors.append(f"펀더멘털 우수 ({fundamental_result.get('score', 0):.0f}점)")

        # 부정 요인
        negative_factors = []
        if news_result.get('score', 50) <= 40:
            negative_factors.append(f"뉴스 부정 ({news_result.get('score', 0):.0f}점)")
        if technical_result.get('score', 50) <= 40:
            negative_factors.append(f"기술적 약세 ({technical_result.get('score', 0):.0f}점)")
        if supply_demand_result.get('score', 50) <= 40:
            negative_factors.append(f"수급 불량 ({supply_demand_result.get('score', 0):.0f}점)")
        if fundamental_result.get('score', 50) <= 40:
            negative_factors.append(f"펀더멘털 부진 ({fundamental_result.get('score', 0):.0f}점)")

        if positive_factors:
            reasons.append("✅ " + ", ".join(positive_factors))
        if negative_factors:
            reasons.append("⚠️ " + ", ".join(negative_factors))

        # 특별 패턴 감지
        special_signals = []

        # 4개 엔진 모두 강세
        if all(r.get('score', 50) >= 60 for r in [news_result, technical_result, supply_demand_result, fundamental_result]):
            special_signals.append("🚀 전 영역 강세 - 강력한 매수 신호")

        # 기술적 + 수급 동반 강세
        if technical_result.get('score', 50) >= 65 and supply_demand_result.get('score', 50) >= 65:
            special_signals.append("💎 기술적 + 수급 동반 강세")

        # 뉴스 + 펀더멘털 양호
        if news_result.get('score', 50) >= 60 and fundamental_result.get('score', 50) >= 60:
            special_signals.append("📰 뉴스 + 펀더멘털 양호")

        # 4개 엔진 모두 약세
        if all(r.get('score', 50) <= 40 for r in [news_result, technical_result, supply_demand_result, fundamental_result]):
            special_signals.append("❌ 전 영역 약세 - 강력한 매도 신호")

        return {
            'recommendation': recommendation,
            'action': action,
            'reasons': reasons,
            'special_signals': special_signals
        }

    def analyze(self, stock_code: str, stock_name: str,
               chart_data: list = None,
               investor_data: list = None,
               program_data: list = None,
               stock_info: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        종합 분석 실행

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            chart_data: 차트 데이터
            investor_data: 투자자별 매매 동향
            program_data: 프로그램 매매 데이터
            stock_info: 주식 기본정보

        Returns:
            종합 분석 결과
        """
        print(f"\n{'='*80}")
        print(f"종합 분석 시작: {stock_name} ({stock_code})")
        print(f"{'='*80}")

        # 1. 뉴스 분석
        print("\n[1/4] 뉴스 분석 중...")
        news_result = self.analyze_news(stock_code, stock_name)
        print(f"  ✓ 뉴스 점수: {news_result['score']:.2f}/100")

        # 2. 기술적 분석
        print("\n[2/4] 기술적 분석 중...")
        technical_result = self.analyze_technical(chart_data)
        print(f"  ✓ 기술적 점수: {technical_result['score']:.2f}/100")

        # 3. 수급 분석
        print("\n[3/4] 수급 분석 중...")
        supply_demand_result = self.analyze_supply_demand(investor_data, program_data, chart_data, stock_code)
        print(f"  ✓ 수급 점수: {supply_demand_result['score']:.2f}/50")
        if supply_demand_result.get('signals'):
            print(f"     시그널: {', '.join(supply_demand_result['signals'][:3])}")

        # 4. 기본 분석
        print("\n[4/4] 기본 분석 중...")
        fundamental_result = self.analyze_fundamental(stock_info, stock_code=stock_code)
        print(f"  ✓ 기본 점수: {fundamental_result['score']:.2f}/50")
        if fundamental_result.get('signals'):
            print(f"     시그널: {', '.join(fundamental_result['signals'][:3])}")

        # 시장 상황 감지
        market_regime = self.detect_market_regime(technical_result, supply_demand_result)
        print(f"\n시장 상황: {market_regime} (보정계수: {self.market_regime_coefficients[market_regime]})")

        # 최종 점수 계산
        final_score = self.calculate_final_score(
            news_result, technical_result, supply_demand_result, fundamental_result, market_regime
        )

        # 투자 추천 생성
        recommendation_result = self.generate_recommendation(
            final_score, news_result, technical_result, supply_demand_result, fundamental_result
        )

        print(f"\n{'='*80}")
        print(f"최종 점수: {final_score:.2f}/100")
        print(f"투자 추천: {recommendation_result['recommendation']}")
        print(f"{'='*80}")

        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'final_score': final_score,
            'recommendation': recommendation_result['recommendation'],
            'action': recommendation_result['action'],
            'reasons': recommendation_result['reasons'],
            'special_signals': recommendation_result['special_signals'],
            'market_regime': market_regime,
            'news': news_result,
            'technical': technical_result,
            'supply_demand': supply_demand_result,
            'fundamental': fundamental_result,
            'weights': self.weights,
            'scores_breakdown': {
                'news': news_result.get('score', 50),
                'technical': technical_result.get('score', 50),
                'supply_demand': supply_demand_result.get('score', 50),
                'fundamental': fundamental_result.get('score', 50)
            }
        }
