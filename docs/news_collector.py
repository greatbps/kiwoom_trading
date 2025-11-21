#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/data_collectors/news_collector.py

네이버 뉴스 검색 및 재료 분석 클래스 (개선된 버전)
"""

import time
import requests
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
import json
from bs4 import BeautifulSoup
import urllib.parse
import sys
from pathlib import Path
import asyncio
import aiohttp
from core.trading_system import AnalysisResult, StockData

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.logger import get_logger

# ================================
# 재료 분류 키워드 및 가중치
# ================================
MATERIAL_KEYWORDS = {
    "장기재료": {
        "keywords": [
            # 경영권/지배구조
            "경영권변화", "대주주변경", "경영진교체", "오너교체", "승계", "세대교체",
            "지배구조개선", "거버넌스", "투명경영", "전문경영인", "사외이사", "독립이사",
            "최대 주주", "주요 주주", "경영권", "핵심 인재", "경영진",
            
            # ESG 경영
            "ESG", "ESG경영", "지속가능경영", "친환경", "탄소중립", "그린경영", "사회공헌",
            "윤리경영", "컴플라이언스", "환경보호", "재생에너지", "친환경제품", "녹색기술",
            
            # 배당정책
            "배당정책", "배당확대", "주주친화정책", "배당성향", "안정배당", "누진배당",
            "특별배당", "중간배당", "분기배당", "주주환원정책", "주주가치제고", "배당",
            "자사주 매입", "부채 상환", "자사주 소각", "액면분할",
            
            # 신성장동력
            "신성장동력", "미래먹거리", "차세대사업", "4차산업혁명", "디지털전환", "DX",
            "인공지능", "AI", "빅데이터", "클라우드", "IoT", "로봇", "자율주행", "전기차",
            "배터리", "바이오", "헬스케어", "신재생에너지", "수소", "메타버스", "블록체인",
            "신성장 동력", "전략적 제휴",
            
            # 장기 트렌드
            "메가트렌드", "구조적성장", "장기성장", "지속성장", "안정성장", "성장지속성",
            "장기수익성", "지속가능성", "장기전망", "중장기계획", "비전2030", "장기목표",
            "단일 판매", "공급계약", "유형자산", "생산설비"
        ],
        "weight": 3.0  # 가장 높은 가중치
    },
    
    "중기재료": {
        "keywords": [
            # 산업/시장 동향
            "산업성장", "시장확대", "시장점유율", "업계1위", "글로벌시장", "해외진출",
            "수출증가", "내수회복", "경기회복", "업황개선", "수요증가", "공급부족",
            "가격상승", "마진개선", "원가절감", "산업 호황", "대규모 수주",
            
            # 기술우위/경쟁력
            "기술우위", "경쟁력강화", "차별화기술", "독점기술", "원천기술보유", "기술격차",
            "고부가가치", "프리미엄제품", "기술혁신", "R&D투자", "연구개발", "기술개발",
            "기술력 우수", "반사이익",
            
            # 턴어라운드
            "턴어라운드", "실적반등", "회복", "정상화", "구조조정완료", "리스트럭처링",
            "경영정상화", "흑자전환", "적자탈출", "부채감축", "재무구조개선", "구조조정",
            "사업부 매각", "수익성 개선", "사업 축소", "철수",
            
            # 해외진출/글로벌화
            "해외진출", "글로벌진출", "현지법인", "해외공장", "수출확대", "글로벌파트너십",
            "해외인수", "국제협력", "글로벌브랜드", "해외시장개척", "수출주력", "해외 진출",
            
            # 사업구조 변화
            "사업구조개선", "포트폴리오재편", "비핵심사업매각", "선택과집중", "핵심역량강화",
            "사업다각화", "신성장동력", "미래사업", "고수익사업", "안정적수익구조",
            "원자재 가격", "경쟁사"
        ],
        "weight": 2.0  # 중간 가중치
    },
    
    "단기재료": {
        "keywords": [
            # M&A 관련
            "인수합병", "M&A", "인수", "합병", "매수", "인수제안", "지분매입", "경영권인수",
            "TOB", "우호적인수", "적대적인수", "MBO", "LBO", "제3자 배정",
            
            # 실적 관련
            "실적발표", "어닝서프라이즈", "어닝쇼크", "분기실적", "연간실적", "매출증가", 
            "영업이익", "당기순이익", "실적호조", "실적개선", "깜짝실적", "컨센서스상회",
            "가이던스상향", "실적전망상향", "사상 최대 실적",
            
            # 신사업/신제품
            "신사업", "신제품", "신기술", "신규사업", "사업다각화", "신규진출", "사업확장",
            "제품출시", "상용화", "양산", "대량생산", "제품개발완료", "출시예정", "신규 사업",
            
            # 수주/계약
            "수주", "대형수주", "계약체결", "납품계약", "공급계약", "장기계약", "독점계약",
            "MOU", "업무협약", "전략적제휴", "파트너십", "공동개발", "협력계약", "신규 수주",
            
            # 정부정책/규제
            "정부지원", "정책수혜", "규제완화", "인허가", "승인", "허가취득", "정부발주",
            "국책사업", "정부과제", "R&D지원", "세제혜택", "보조금", "정책지원", "정부 정책",
            
            # 특허/지적재산권
            "특허", "특허출원", "특허등록", "특허소송", "라이센스", "기술이전", "지적재산권",
            "특허권", "원천기술", "핵심특허", "국제특허", "특허포트폴리오",
            
            # 주가/주주관련
            "자사주매입", "배당증가", "배당확대", "주식분할", "액면분할", "무상증자",
            "유상증자", "주주환원", "배당정책", "주주가치", "자회사 상장", "공개매수",
            "회사 분할", "인적분할", "물적분할",
            
            # 기타 단기 이벤트
            "IPO", "상장", "코스닥이전", "코스피승격", "편입", "신규편입", "지수편입",
            "스핀오프", "분할", "합병", "기업분할", "조직개편",
            
            # 신기술/트렌드
            "AI", "인공지능", "2차전지", "로봇", "방산", "임상", "신약 개발",
            "기관 매수", "외국인 매수", "지분 변동", "투자경고", "거래정지"
        ],
        "weight": 1.0  # 기본 가중치
    }
}

# 제외 키워드
EXCLUDE_KEYWORDS = [
    # ETF 관련 (더 구체적으로 지정)
    "ETF", "ETN", "상장지수펀드", "인버스", "레버리지", "KODEX", "TIGER", "ARIRANG", 
    "KINDEX", "TREX", "HANARO", "TIMEFOLIO", "FOCUS", "SOL", "SMART",
    "KODEX ETF", "TIGER ETF", "ARIRANG ETF", "ETF펀드", "지수펀드",
    
    # 우선주
    "우선주", "우선", "1우", "2우", "3우", "4우", "5우",
    
    # 투자주의 종목
    "관리종목", "투자주의", "투자경고", "거래정지", "상장폐지",
    "투자위험", "감리", "공시위반", "해명자료"
]

class NewsCollector:
    """Gemini AI 통합 네이버 뉴스 수집 및 분석 클래스"""
    
    def __init__(self, config=None):
        self.config = config
        self.logger = get_logger("NewsCollector")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        })
        
        # 네이버 API 설정 (있는 경우)
        if config and hasattr(config.api, 'NAVER_CLIENT_ID'):
            self.naver_client_id = config.api.NAVER_CLIENT_ID
            self.naver_client_secret = config.api.NAVER_CLIENT_SECRET
        else:
            self.naver_client_id = None
            self.naver_client_secret = None
        
        # LLM 제거됨
    
    def is_excluded_stock(self, stock_name: str, stock_code: str = "") -> bool:
        """제외 대상 종목인지 확인"""
        stock_text = f"{stock_name} {stock_code}".upper()
        
        for keyword in EXCLUDE_KEYWORDS:
            if keyword.upper() in stock_text:
                self.logger.debug(f"제외 종목 발견: {stock_name} (키워드: {keyword})")
                return True
        
        return False
    
    async def search_naver_news_api(self, query: str, display: int = 10) -> List[Dict]:
        """네이버 뉴스 API를 사용한 검색 - 최근 3일 뉴스만 필터링"""
        if not self.naver_client_id or not self.naver_client_secret:
            return []
        
        try:
            from datetime import datetime, timedelta
            
            url = "https://openapi.naver.com/v1/search/news.json"
            headers = {
                "X-Naver-Client-Id": self.naver_client_id,
                "X-Naver-Client-Secret": self.naver_client_secret
            }
            params = {
                "query": query,
                "display": display,
                "start": 1,
                "sort": "date"
            }
            
            # 최근 3일 기준 날짜 계산
            three_days_ago = datetime.now() - timedelta(days=3)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        news_items = []
                        
                        for item in data.get('items', []):
                            pub_date_str = item.get('pubDate', '')
                            
                            # 날짜 필터링 - 최근 3일 뉴스만 포함
                            try:
                                # RFC 2822 형식 파싱 (예: "Tue, 09 Aug 2025 15:30:00 +0900")
                                pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
                                pub_date_naive = pub_date.replace(tzinfo=None)
                                
                                if pub_date_naive < three_days_ago:
                                    continue  # 3일 이전 뉴스는 제외
                                    
                            except (ValueError, TypeError):
                                # 날짜 파싱 실패 시 포함 (최신 뉴스일 가능성 높음)
                                pass
                            
                            news_item = {
                                'title': item.get('title', '').replace('<b>', '').replace('</b>', ''),
                                'link': item.get('link', ''),
                                'description': item.get('description', '').replace('<b>', '').replace('</b>', ''),
                                'pubDate': pub_date_str,
                                'source': 'naver_api'
                            }
                            news_items.append(news_item)
                        
                        self.logger.debug(f"📰 {query} 뉴스 검색: 총 {len(data.get('items', []))}개 중 최근 3일 뉴스 {len(news_items)}개 선별")
                        return news_items
                    
        except Exception as e:
            self.logger.warning(f"네이버 API 검색 실패: {e}")
        
        return []
    
    def search_stock_news_naver(self, stock_name: str, stock_code: str) -> List[Dict]:
        """개선된 네이버 뉴스 검색"""
        try:
            # 제외 대상 종목 체크
            if self.is_excluded_stock(stock_name, stock_code):
                self.logger.info(f"   🚫 {stock_name} - 제외 대상 종목")
                return []
            
            self.logger.info(f"   🔍 네이버에서 {stock_name} 뉴스 검색 중...")
            
            search_queries = [
                stock_name,
                f"{stock_name} 주식",
                f"{stock_name} 기업",
                stock_code
            ]
            
            all_news_items = []
            
            for query in search_queries:
                try:
                    encoded_query = urllib.parse.quote(query)
                    
                    urls = [
                        f"https://search.naver.com/search.naver?where=news&query={encoded_query}&sort=1&pd=1&ds=&de=",
                        f"https://finance.naver.com/item/news.naver?code={stock_code}",
                        f"https://search.naver.com/search.naver?where=news&query={encoded_query}&sort=0"
                    ]
                    
                    for url in urls:
                        try:
                            response = self.session.get(url, timeout=15)
                            response.raise_for_status()
                            
                            soup = BeautifulSoup(response.content, 'html.parser')
                            
                            news_selectors = [
                                'div.news_area',
                                'div.bx',
                                'dl.newsList',
                                'div.newsflash_item',
                                'li.newsList',
                                'div.section_news'
                            ]
                            
                            news_elements = []
                            for selector in news_selectors:
                                elements = soup.select(selector)
                                if elements:
                                    news_elements = elements[:15]
                                    break
                            
                            if not news_elements:
                                news_elements = soup.select('a[href*="news"], a[href*="article"]')[:10]
                            
                            for element in news_elements:
                                try:
                                    title_selectors = [
                                        'a.news_tit',
                                        'a.sh_blog_title', 
                                        '.title',
                                        'a.nclicks\\(fls\\.title\\)',
                                        'dt a',
                                        'strong.tit_news'
                                    ]
                                    
                                    title_elem = None
                                    for selector in title_selectors:
                                        title_elem = element.select_one(selector)
                                        if title_elem:
                                            break
                                    
                                    if not title_elem:
                                        title_elem = element.find('a') if element.name != 'a' else element
                                    
                                    if not title_elem:
                                        continue
                                        
                                    title = title_elem.get_text(strip=True)
                                    if not title or len(title) < 10:
                                        continue
                                        
                                    link = title_elem.get('href', '')
                                    if link and not link.startswith('http'):
                                        link = 'https://news.naver.com' + link
                                    
                                    date_selectors = [
                                        '.info_group .info',
                                        '.date',
                                        '.write_date',
                                        'span.date'
                                    ]
                                    
                                    date_text = ""
                                    for selector in date_selectors:
                                        date_elem = element.select_one(selector)
                                        if date_elem:
                                            date_text = date_elem.get_text(strip=True)
                                            break
                                    
                                    summary_selectors = [
                                        '.api_txt_lines',
                                        '.lede',
                                        '.summary',
                                        'dd'
                                    ]
                                    
                                    summary = ""
                                    for selector in summary_selectors:
                                        summary_elem = element.select_one(selector)
                                        if summary_elem:
                                            summary = summary_elem.get_text(strip=True)
                                            break
                                    
                                    combined_text = f"{title} {summary}".lower()
                                    stock_terms = [stock_name.lower(), stock_code]
                                    
                                    is_relevant = False
                                    for term in stock_terms:
                                        if term in combined_text:
                                            is_relevant = True
                                            break
                                    
                                    if not is_relevant and len(stock_name) >= 2:
                                        if stock_name[:2] in combined_text:
                                            is_relevant = True
                                    
                                    if is_relevant:
                                        news_item = {
                                            'title': title,
                                            'link': link,
                                            'date': date_text,
                                            'summary': summary if summary else title,
                                            'stock_name': stock_name,
                                            'stock_code': stock_code,
                                            'query_used': query,
                                            'source': 'naver_crawl'
                                        }
                                        
                                        if not any(existing['title'] == title for existing in all_news_items):
                                            all_news_items.append(news_item)
                                        
                                except Exception as e:
                                    continue
                                    
                        except Exception as e:
                            continue
                            
                    time.sleep(0.5)
                    
                except Exception as e:
                    continue
            
            unique_news = []
            seen_titles = set()
            
            for news in all_news_items:
                title_key = news['title'][:50]
                if title_key not in seen_titles:
                    seen_titles.add(title_key)
                    unique_news.append(news)
            
            final_news = unique_news  # 전체 뉴스 반환 (10개 제한 제거)
            
            self.logger.info(f"   📰 {stock_name} 뉴스 {len(final_news)}개 수집")
            
            if len(final_news) == 0:
                self.logger.info(f"   ⚠️ {stock_name} 관련 뉴스를 찾을 수 없습니다.")
                
            return final_news
            
        except Exception as e:
            self.logger.warning(f"   ⚠️ 네이버 뉴스 검색 실패: {e}")
            return []
    
    def analyze_material_with_weights(self, news_item: Dict) -> Tuple[str, List[str], float]:
        """가중치가 적용된 재료 분석"""
        text = f"{news_item['title']} {news_item.get('summary', '')}".lower()
        
        found_materials = []
        material_type = "재료없음"
        total_score = 0.0
        
        # 각 재료 타입별로 키워드 검색 및 점수 계산
        type_scores = {}
        
        for mat_type, config in MATERIAL_KEYWORDS.items():
            keywords = config["keywords"]
            weight = config["weight"]
            matched_keywords = []
            type_score = 0.0
            
            for keyword in keywords:
                if keyword.lower() in text:
                    matched_keywords.append(keyword)
                    type_score += weight
            
            if matched_keywords:
                found_materials.extend(matched_keywords)
                type_scores[mat_type] = type_score
                total_score += type_score
        
        # 가장 높은 점수의 재료 타입을 주 재료로 선정
        if type_scores:
            # 장기 > 중기 > 단기 순으로 우선순위 적용
            priority_order = ["장기재료", "중기재료", "단기재료"]
            for mat_type in priority_order:
                if mat_type in type_scores:
                    material_type = mat_type
                    break
        
        return material_type, found_materials, total_score
    
    def calculate_news_sentiment_score(self, news_items: List[Dict]) -> float:
        """뉴스 감정 점수 계산 (기본적인 키워드 기반)"""
        positive_keywords = [
            "상승", "증가", "호조", "개선", "성장", "확대", "신고가", "돌파", "수혜", "기대",
            "긍정", "유리", "강세", "급등", "상승세", "반등", "회복", "성공", "선전", "대박"
        ]
        
        negative_keywords = [
            "하락", "감소", "부진", "악화", "축소", "신저가", "하락세", "급락", "폭락", 
            "부정", "불리", "약세", "우려", "위험", "손실", "적자", "부실", "위기", "충격"
        ]
        
        total_sentiment = 0.0
        total_words = 0
        
        for news in news_items:
            text = f"{news['title']} {news.get('summary', '')}".lower()
            words = text.split()
            total_words += len(words)
            
            for word in words:
                if any(pos in word for pos in positive_keywords):
                    total_sentiment += 1
                elif any(neg in word for neg in negative_keywords):
                    total_sentiment -= 1
        
        if total_words == 0:
            return 0.0
        
        # -1 ~ 1 범위로 정규화
        sentiment_score = total_sentiment / total_words
        return max(-1, min(1, sentiment_score))
    
    def analyze_stock_materials(self, stock_info: Dict) -> Dict:
        """종목별 재료 종합 분석 (가중치 적용) - 안전한 속성 접근"""
        # 안전한 속성 접근
        stock_name = self.safe_get_attr(stock_info, 'name', 
                                    self.safe_get_attr(stock_info, '종목명', ''))
        stock_code = self.safe_get_attr(stock_info, 'symbol', 
                                    self.safe_get_attr(stock_info, '종목코드', ''))
        
        self.logger.info(f"\n🔍 {stock_name} ({stock_code}) 재료 분석 중...")
        
        # 제외 대상 종목 체크
        if self.is_excluded_stock(stock_name, stock_code):
            return {
                'stock_info': stock_info,
                'material_summary': {
                    'primary_material': "제외종목",
                    'total_score': 0.0,
                    'weighted_score': 0.0,
                    'news_count': 0,
                    'material_keywords': [],
                    'sentiment_score': 0.0,
                    'excluded': True,
                    'exclude_reason': "ETF/우선주/투자주의 종목"
                },
                'news_analysis': []
            }
        
        news_items = self.search_stock_news_naver(stock_name, stock_code)
        
        if not news_items:
            return {
                'stock_info': stock_info,
                'material_summary': {
                    'primary_material': "재료없음",
                    'total_score': 0.0,
                    'weighted_score': 0.0,
                    'news_count': 0,
                    'material_keywords': [],
                    'sentiment_score': 0.0,
                    'excluded': False
                },
                'news_analysis': []
            }
        
        news_analysis = []
        all_materials = []
        total_score = 0.0
        
        for news in news_items:
            material_type, keywords, score = self.analyze_material_with_weights(news)
            
            analysis = {
                'title': news['title'],
                'date': news['date'],
                'material_type': material_type,
                'keywords': keywords,
                'score': score,
                'link': news.get('link', ''),
                'summary': news.get('summary', '')
            }
            
            news_analysis.append(analysis)
            all_materials.extend(keywords)
            total_score += score
            
            if material_type != "재료없음":
                self.logger.info(f"   📄 {material_type}: {news['title'][:50]}...")
                self.logger.info(f"      키워드: {', '.join(keywords[:3])}{'...' if len(keywords) > 3 else ''}")
        
        # 재료 카운트
        material_counts = {}
        for analysis in news_analysis:
            mat_type = analysis['material_type']
            if mat_type != "재료없음":
                material_counts[mat_type] = material_counts.get(mat_type, 0) + 1
        
        # 주요 재료 결정 (가중치 기반)
        primary_material = "재료없음"
        if material_counts:
            # 장기 > 중기 > 단기 순으로 우선순위
            for mat_type in ["장기재료", "중기재료", "단기재료"]:
                if mat_type in material_counts:
                    primary_material = mat_type
                    break
        
        # 감정 점수 계산
        sentiment_score = self.calculate_news_sentiment_score(news_items)
        
        # 가중치가 적용된 최종 점수 계산
        weighted_score = total_score
        if sentiment_score > 0:
            weighted_score *= (1 + sentiment_score * 0.2)  # 긍정적 감정 보너스
        elif sentiment_score < 0:
            weighted_score *= (1 + sentiment_score * 0.1)  # 부정적 감정 페널티
        
        unique_keywords = list(set(all_materials))
        
        result = {
            'stock_info': stock_info,
            'material_summary': {
                'primary_material': primary_material,
                'total_score': total_score,
                'weighted_score': weighted_score,
                'news_count': len(news_items),
                'material_keywords': unique_keywords,
                'material_counts': material_counts,
                'sentiment_score': sentiment_score,
                'excluded': False
            },
            'news_analysis': news_analysis
        }
        
        self.logger.info(f"   ✅ 재료 분석 완료: {primary_material} (가중치 점수: {weighted_score:.2f})")
        return result
    
    def get_material_score_ranking(self, analysis_results: List[Dict]) -> List[Dict]:
        """재료 점수별 종목 순위 정렬 (가중치 적용)"""
        valid_results = []
        
        for result in analysis_results:
            summary = result.get('material_summary', {})
            
            # 제외 종목은 순위에서 제외
            if summary.get('excluded', False):
                continue
            
            # 재료가 있는 종목만 포함
            if summary.get('primary_material') != "재료없음" and summary.get('weighted_score', 0) > 0:
                result['final_score'] = summary.get('weighted_score', 0)
                valid_results.append(result)
        
        # 가중치 점수순 정렬
        sorted_results = sorted(valid_results, key=lambda x: x['final_score'], reverse=True)
        
        # 순위 추가
        for i, result in enumerate(sorted_results, 1):
            result['material_rank'] = i
        
        return sorted_results
    
    def safe_get_attr(self, data, attr_name, default=None):
        """안전한 속성 접근 유틸리티"""
        try:
            if isinstance(data, dict):
                return data.get(attr_name, default)
            else:
                return getattr(data, attr_name, default)
        except (AttributeError, TypeError):
            return default
    
    def get_news_analysis_summary(self, stock_name: str, stock_code: str) -> Dict:
        """단일 종목의 뉴스 분석 요약 - 안전한 속성 접근"""
        stock_info = {
            'name': stock_name,
            'symbol': stock_code
        }
        
        analysis_result = self.analyze_stock_materials(stock_info)
        summary = analysis_result.get('material_summary', {})
        
        return {
            'has_material': summary.get('primary_material') != "재료없음",
            'material_type': summary.get('primary_material'),
            'material_score': summary.get('weighted_score', 0),
            'news_count': summary.get('news_count', 0),
            'sentiment_score': summary.get('sentiment_score', 0),
            'keywords': summary.get('material_keywords', [])[:5],  # 상위 5개 키워드만
            'excluded': summary.get('excluded', False)
        }
    async def analyze_symbol(self, symbol: str, name: str, strategy: str) -> Optional[AnalysisResult]:
        """개별 종목 분석 - 안전한 데이터 전달"""
        try:
            # 1. 기본 데이터 수집
            stock_data = await self.data_collector.get_stock_info(symbol)
            if not stock_data:
                return None
            
            # 2. 뉴스 분석 - 안전한 방식으로 호출
            try:
                # stock_data가 딕셔너리인지 확인하고 필요한 정보만 추출
                news_data_input = {
                    'name': stock_data.get('name', name) if isinstance(stock_data, dict) else getattr(stock_data, 'name', name),
                    'symbol': stock_data.get('symbol', symbol) if isinstance(stock_data, dict) else getattr(stock_data, 'symbol', symbol)
                }
                news_data = self.news_collector.analyze_stock_materials(news_data_input)
            except Exception as e:
                self.logger.warning(f"⚠️ {symbol} 뉴스 분석 실패, 건너뛰기: {e}")
                news_data = None
            
            # 3. 종합 분석
            analysis_result = await self.analysis_engine.analyze_comprehensive(
                symbol=symbol,
                name=name,
                stock_data=stock_data,
                news_data=news_data,
                strategy=strategy
            )
            
            # 4. 전략별 신호 생성
            strategy_obj = self.strategies.get(strategy)
            if not strategy_obj:
                self.logger.error(f"❌ 알 수 없는 전략: {strategy}, 사용 가능: {list(self.strategies.keys())}")
                return None
            
            signals = await strategy_obj.generate_signals(stock_data, analysis_result)
            
            # 5. 리스크 평가
            risk_level = await self._evaluate_risk(stock_data, analysis_result)
            
            # 6. 매수/매도 가격 계산
            entry_price, stop_loss, take_profit = await self._calculate_trade_prices(
                stock_data, signals, strategy_obj
            )
            
            # 7. 결과 생성
            result = AnalysisResult(
                symbol=symbol,
                name=name,
                score=analysis_result.get('comprehensive_score', 0),
                signals=signals,
                analysis_time=datetime.now(),
                strategy=strategy,
                recommendation=self._get_recommendation(analysis_result, signals),
                risk_level=risk_level,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ {symbol} 분석 실패: {e}")
            return None
    
    async def analyze_with_gemini(self, stock_name: str, stock_code: str, news_data: List[Dict] = None) -> Dict:
        """Gemini AI를 활용한 뉴스 분석"""
        try:
            # LLM 제거됨 - 기본 분석 사용
            return self._get_default_gemini_analysis()
            
            # 뉴스 데이터가 없으면 수집
            if not news_data:
                news_data = self.search_stock_news_naver(stock_name, stock_code)
            
            if not news_data:
                # 뉴스 데이터가 없을 때 더 적극적인 검색 시도
                self.logger.warning(f"⚠️ {stock_name} 초기 뉴스 검색 실패, 추가 검색 시도 중...")
                
                # 대체 검색어로 다시 시도
                alternative_searches = [
                    stock_code,  # 종목코드로 검색
                    f"{stock_name} 주가",  # 주가 키워드 추가
                    f"{stock_name} 실적",  # 실적 키워드 추가
                    f"{stock_name} 공시"   # 공시 키워드 추가
                ]
                
                for search_term in alternative_searches:
                    try:
                        news_data = self.search_stock_news_naver(search_term, stock_code)
                        if news_data:
                            self.logger.info(f"✅ {stock_name} 대체 검색어 '{search_term}'으로 뉴스 {len(news_data)}건 발견")
                            break
                    except Exception as e:
                        self.logger.debug(f"대체 검색 '{search_term}' 실패: {e}")
                        continue
                
                if not news_data:
                    self.logger.warning(f"⚠️ {stock_name}({stock_code}) 모든 뉴스 검색 시도 실패 - 기본 분석 사용")
                    return self._get_default_gemini_analysis_with_context(stock_name, stock_code)
            
            # Gemini로 감성 분석
            sentiment_result = await self.gemini_analyzer.analyze_news_sentiment(
                stock_code, stock_name, news_data
            )
            
            # Gemini로 시장 영향도 분석
            impact_result = await self.gemini_analyzer.analyze_market_impact(
                stock_code, stock_name, news_data
            )
            
            # 결과 통합
            combined_analysis = {
                'symbol': stock_code,
                'name': stock_name,
                'news_count': len(news_data),
                'analysis_timestamp': datetime.now().isoformat(),
                
                # 감성 분석 결과
                'sentiment': {
                    'overall_sentiment': sentiment_result.get('sentiment', 'NEUTRAL'),
                    'confidence': sentiment_result.get('confidence', 0.5),
                    'overall_score': sentiment_result.get('overall_score', 50),
                    'positive_factors': sentiment_result.get('positive_factors', []),
                    'negative_factors': sentiment_result.get('negative_factors', []),
                    'key_keywords': sentiment_result.get('key_keywords', []),
                    'short_term_outlook': sentiment_result.get('short_term_outlook', ''),
                    'medium_term_outlook': sentiment_result.get('medium_term_outlook', ''),
                    'summary': sentiment_result.get('summary', '')
                },
                
                # 시장 영향도 분석 결과
                'market_impact': {
                    'impact_level': impact_result.get('impact_level', 'MEDIUM'),
                    'impact_score': impact_result.get('impact_score', 50),
                    'duration': impact_result.get('duration', 'MEDIUM_TERM'),
                    'price_direction': impact_result.get('price_direction', 'NEUTRAL'),
                    'volatility_expected': impact_result.get('volatility_expected', 'MEDIUM'),
                    'trading_volume_impact': impact_result.get('trading_volume_impact', 'NORMAL'),
                    'sector_impact': impact_result.get('sector_impact', ''),
                    'key_risks': impact_result.get('key_risks', []),
                    'catalysts': impact_result.get('catalysts', []),
                    'target_price_change': impact_result.get('target_price_change', '0%'),
                    'recommendation': impact_result.get('recommendation', 'HOLD')
                },
                
                # 종합 평가
                'final_assessment': {
                    'investment_grade': self._calculate_investment_grade(sentiment_result, impact_result),
                    'risk_level': self._assess_risk_level(sentiment_result, impact_result),
                    'trading_strategy': self._suggest_trading_strategy(sentiment_result, impact_result),
                    'key_points': self._extract_key_points(sentiment_result, impact_result)
                }
            }
            
            self.logger.info(f"✅ {stock_name} Gemini 분석 완료 - 감성: {sentiment_result.get('sentiment', 'NEUTRAL')}, 영향도: {impact_result.get('impact_level', 'MEDIUM')}")
            return combined_analysis
            
        except Exception as e:
            self.logger.error(f"❌ {stock_name} Gemini 분석 실패: {e}")
            return self._get_default_gemini_analysis()
    
    def _get_default_gemini_analysis(self) -> Dict:
        """기본 Gemini 분석 결과"""
        return self._get_default_gemini_analysis_with_context("UNKNOWN", "UNKNOWN")
    
    def _get_default_gemini_analysis_with_context(self, stock_name: str, stock_code: str) -> Dict:
        """컨텍스트가 포함된 기본 Gemini 분석 결과"""
        return {
            'symbol': stock_code,
            'name': stock_name,
            'news_count': 0,
            'analysis_timestamp': datetime.now().isoformat(),
            'data_status': 'NO_NEWS_DATA',
            'sentiment': {
                'overall_sentiment': 'NEUTRAL',
                'confidence': 0.3,  # 데이터 부족으로 낮은 신뢰도
                'overall_score': 50,
                'positive_factors': ['뉴스 데이터 부족으로 분석 불가'],
                'negative_factors': ['뉴스 데이터 부족으로 분석 불가'],
                'key_keywords': [],
                'short_term_outlook': f'{stock_name} 뉴스 정보 부족으로 중립적 전망',
                'medium_term_outlook': f'{stock_name} 추가 정보 수집 후 재분석 필요',
                'summary': f'{stock_name}({stock_code}) 뉴스 데이터 부족으로 AI 분석 제한'
            },
            'market_impact': {
                'impact_level': 'LOW',  # 정보 부족시 낮은 영향도
                'impact_score': 40,     # 보수적 점수
                'duration': 'SHORT_TERM',
                'price_direction': 'NEUTRAL',
                'volatility_expected': 'LOW',
                'trading_volume_impact': 'NORMAL',
                'sector_impact': '정보 부족',
                'key_risks': [],
                'catalysts': [],
                'target_price_change': '0%',
                'recommendation': 'HOLD'
            },
            'final_assessment': {
                'investment_grade': 'C',
                'risk_level': 'MEDIUM',
                'trading_strategy': 'WAIT_AND_SEE',
                'key_points': ['정보 부족으로 신중한 접근 권장']
            }
        }
    
    def _calculate_investment_grade(self, sentiment_result: Dict, impact_result: Dict) -> str:
        """투자 등급 계산"""
        sentiment_score = sentiment_result.get('overall_score', 50)
        impact_score = impact_result.get('impact_score', 50)
        
        combined_score = (sentiment_score + impact_score) / 2
        
        if combined_score >= 85:
            return 'A+'
        elif combined_score >= 80:
            return 'A'
        elif combined_score >= 75:
            return 'A-'
        elif combined_score >= 70:
            return 'B+'
        elif combined_score >= 65:
            return 'B'
        elif combined_score >= 60:
            return 'B-'
        elif combined_score >= 55:
            return 'C+'
        elif combined_score >= 50:
            return 'C'
        elif combined_score >= 45:
            return 'C-'
        elif combined_score >= 40:
            return 'D+'
        elif combined_score >= 35:
            return 'D'
        else:
            return 'D-'
    
    def _assess_risk_level(self, sentiment_result: Dict, impact_result: Dict) -> str:
        """리스크 레벨 평가"""
        volatility = impact_result.get('volatility_expected', 'MEDIUM')
        confidence = sentiment_result.get('confidence', 0.5)
        
        if volatility == 'VERY_HIGH' or confidence < 0.3:
            return 'HIGH'
        elif volatility == 'HIGH' or confidence < 0.5:
            return 'MEDIUM'
        else:
            return 'LOW'
    
    def _suggest_trading_strategy(self, sentiment_result: Dict, impact_result: Dict) -> str:
        """매매 전략 제안"""
        sentiment = sentiment_result.get('sentiment', 'NEUTRAL')
        impact_level = impact_result.get('impact_level', 'MEDIUM')
        price_direction = impact_result.get('price_direction', 'NEUTRAL')
        
        if sentiment in ['VERY_POSITIVE', 'POSITIVE'] and impact_level in ['VERY_HIGH', 'HIGH']:
            if price_direction in ['STRONG_UP', 'UP']:
                return 'AGGRESSIVE_BUY'
            else:
                return 'MODERATE_BUY'
        elif sentiment in ['VERY_NEGATIVE', 'NEGATIVE'] and impact_level in ['VERY_HIGH', 'HIGH']:
            if price_direction in ['STRONG_DOWN', 'DOWN']:
                return 'AGGRESSIVE_SELL'
            else:
                return 'MODERATE_SELL'
        elif sentiment == 'NEUTRAL' or impact_level in ['LOW', 'VERY_LOW']:
            return 'WAIT_AND_SEE'
        else:
            return 'CAUTIOUS_APPROACH'
    
    def _extract_key_points(self, sentiment_result: Dict, impact_result: Dict) -> List[str]:
        """핵심 포인트 추출"""
        key_points = []
        
        # 긍정적 요인
        positive_factors = sentiment_result.get('positive_factors', [])
        if positive_factors:
            key_points.append(f"긍정 요인: {', '.join(positive_factors[:3])}")
        
        # 부정적 요인
        negative_factors = sentiment_result.get('negative_factors', [])
        if negative_factors:
            key_points.append(f"우려 요인: {', '.join(negative_factors[:3])}")
        
        # 상승 촉매
        catalysts = impact_result.get('catalysts', [])
        if catalysts:
            key_points.append(f"상승 촉매: {', '.join(catalysts[:2])}")
        
        # 주요 리스크
        risks = impact_result.get('key_risks', [])
        if risks:
            key_points.append(f"주요 리스크: {', '.join(risks[:2])}")
        
        # 예상 주가 변동
        price_change = impact_result.get('target_price_change', '0%')
        if price_change != '0%':
            key_points.append(f"예상 변동률: {price_change}")
        
        return key_points if key_points else ['특별한 이슈 없음']