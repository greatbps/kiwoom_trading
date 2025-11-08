"""
종목 검색 유틸리티 (Stock Search Utility)
=========================================

종목 코드 또는 종목명으로 검색하여 자동으로 매칭해주는 UX 개선 시스템

주요 기능:
- 종목 코드로 종목명 검색
- 종목명으로 종목 코드 검색
- 부분 검색 및 유사도 검색 지원
- KIS API 연동을 통한 실시간 정보
"""

import asyncio
import logging
from typing import List, Dict, Optional, Tuple
import difflib
import re
from dataclasses import dataclass


@dataclass
class StockInfo:
    """종목 정보"""
    symbol: str
    name: str
    current_price: Optional[int] = None
    market: Optional[str] = None  # KOSPI, KOSDAQ, etc.
    sector: Optional[str] = None
    

class StockSearchEngine:
    """
    종목 검색 엔진
    
    종목 코드/종목명 상호 검색 및 자동완성 기능 제공
    """
    
    def __init__(self, kis_collector=None):
        self.kis_collector = kis_collector
        self.logger = logging.getLogger("StockSearchEngine")
        
        # 주요 종목 코드-종목명 매핑 (캐시)
        self.stock_cache = {
            # 대형주
            '005930': '삼성전자',
            '000660': 'SK하이닉스',
            '035420': 'NAVER',
            '051910': 'LG화학',
            '006400': '삼성SDI',
            '207940': '삼성바이오로직스',
            '005380': '현대차',
            '000270': '기아',
            '035720': '카카오',
            '068270': '셀트리온',
            '028260': '삼성물산',
            '105560': 'KB금융',
            '012330': '현대모비스',
            '066570': 'LG전자',
            '003670': '포스코홀딩스',
            '017670': 'SK텔레콤',
            '096770': 'SK이노베이션',
            '009150': '삼성전기',
            '034020': '두산에너빌리티',
            '018260': '삼성에스디에스',
            
            # 중형주
            '003550': 'LG',
            '032830': '삼성생명',
            '010950': 'S-Oil',
            '047050': '포스코인터내셔널',
            '024110': '기업은행',
            '086790': '하나금융지주',
            '029780': '삼성카드',
            '138040': '메리츠금융지주',
            '005830': 'DB손해보험',
            '008770': '호텔신라',
            
            # IT/바이오
            '036570': '엔씨소프트',
            '251270': '넷마블',
            '112040': '위메이드',
            '263750': '펄어비스',
            '095660': '네오위즈',
            '041510': '에스엠',
            '352820': '하이브',
            '214320': 'KeyHolder',
            '196170': '알테오젠',
            '326030': 'SK바이오팜',
            '302440': 'SK바이오사이언스',
            '091990': '셀트리온헬스케어',
            '214150': '클래시스',
            
            # 2차전지/소재
            '373220': 'LG에너지솔루션',
            '086520': '에코프로',
            '247540': '에코프로비엠',
            '450080': '에코프로머티',
            '096770': 'SK이노베이션',
            '003230': '삼양식품',
            '051915': 'LG화학우',
            '161390': '한국타이어앤테크놀로지',
            
            # 반도체 관련
            '042700': '한미반도체',
            '039030': '이오테크닉스',
            '357780': '솔브레인',
            '108320': 'LX세미콘',
            '095340': 'ISC',
            '131970': '두산테스나',
            '036930': '주성엔지니어링',
            '322000': '에스티마이크로일렉트로닉스',
        }
        
        # 역방향 매핑 (종목명 -> 종목코드)
        self.name_to_symbol = {name: symbol for symbol, name in self.stock_cache.items()}
        
        self.logger.info(f"📋 StockSearchEngine 초기화: {len(self.stock_cache)}개 종목 캐시")
    
    async def search_stock(self, query: str) -> List[StockInfo]:
        """
        종목 검색 (코드 또는 이름)
        
        Args:
            query: 검색어 (종목코드 또는 종목명)
            
        Returns:
            매칭되는 종목 정보 리스트
        """
        try:
            query = query.strip()
            if not query:
                return []
            
            results = []
            
            # 1. 정확한 종목코드 매칭
            if query.isdigit() and len(query) == 6:
                if query in self.stock_cache:
                    stock_info = await self._get_stock_info(query, self.stock_cache[query])
                    if stock_info:
                        results.append(stock_info)
                else:
                    # KIS API로 실시간 조회
                    stock_info = await self._get_stock_info_from_api(query)
                    if stock_info:
                        results.append(stock_info)
            
            # 2. 정확한 종목명 매칭
            elif query in self.name_to_symbol:
                symbol = self.name_to_symbol[query]
                stock_info = await self._get_stock_info(symbol, query)
                if stock_info:
                    results.append(stock_info)
            
            # 3. 부분 검색
            else:
                partial_matches = await self._partial_search(query)
                results.extend(partial_matches)
            
            return results[:10]  # 최대 10개 결과
            
        except Exception as e:
            self.logger.error(f"❌ 종목 검색 실패: {e}")
            return []
    
    async def _partial_search(self, query: str) -> List[StockInfo]:
        """부분 검색"""
        try:
            results = []
            query_lower = query.lower()
            
            # 종목명에서 부분 검색
            for symbol, name in self.stock_cache.items():
                name_lower = name.lower()
                
                # 정확한 부분 매칭
                if query_lower in name_lower:
                    stock_info = await self._get_stock_info(symbol, name)
                    if stock_info:
                        results.append(stock_info)
                        continue
                
                # 유사도 검색 (0.6 이상)
                similarity = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
                if similarity >= 0.6:
                    stock_info = await self._get_stock_info(symbol, name)
                    if stock_info:
                        results.append(stock_info)
            
            # 결과를 유사도 순으로 정렬
            results.sort(key=lambda x: difflib.SequenceMatcher(None, query_lower, x.name.lower()).ratio(), reverse=True)
            
            return results
            
        except Exception as e:
            self.logger.error(f"❌ 부분 검색 실패: {e}")
            return []
    
    async def _get_stock_info(self, symbol: str, name: str) -> Optional[StockInfo]:
        """종목 정보 생성"""
        try:
            stock_info = StockInfo(symbol=symbol, name=name)
            
            # KIS API가 있으면 실시간 가격 조회
            if self.kis_collector:
                try:
                    api_info = await self.kis_collector.get_stock_info(symbol)
                    # current_price는 직접 할당하지 않음 - 실시간 조회 함수 사용
                except:
                    pass  # API 실패해도 기본 정보는 반환
            
            return stock_info
            
        except Exception as e:
            self.logger.error(f"❌ {symbol} 종목 정보 생성 실패: {e}")
            return None
    
    async def _get_stock_info_from_api(self, symbol: str) -> Optional[StockInfo]:
        """KIS API에서 종목 정보 조회"""
        try:
            if not self.kis_collector:
                return None
            
            api_info = await self.kis_collector.get_stock_info(symbol)
            if not api_info:
                return None
            
            # 종목명이 있으면 StockInfo 생성
            name = getattr(api_info, 'name', f'종목{symbol}')
            current_price = getattr(api_info, 'current_price', None)
            
            stock_info = StockInfo(
                symbol=symbol,
                name=name,
                current_price=current_price
            )
            
            # 캐시에 추가
            self.stock_cache[symbol] = name
            self.name_to_symbol[name] = symbol
            
            return stock_info
            
        except Exception as e:
            self.logger.error(f"❌ {symbol} API 조회 실패: {e}")
            return None
    
    async def get_stock_by_code_or_name(self, input_text: str) -> Optional[Tuple[str, str]]:
        """
        종목 코드 또는 종목명 입력으로 (코드, 이름) 튜플 반환
        
        Returns:
            (종목코드, 종목명) 또는 None
        """
        try:
            results = await self.search_stock(input_text)
            
            if not results:
                return None
            
            # 첫 번째 결과 반환
            best_match = results[0]
            return (best_match.symbol, best_match.name)
            
        except Exception as e:
            self.logger.error(f"❌ 종목 매칭 실패: {e}")
            return None
    
    async def interactive_stock_selection(self, query: str) -> Optional[Tuple[str, str]]:
        """
        대화형 종목 선택
        
        여러 결과가 있을 때 사용자가 선택하도록 함
        
        Returns:
            (종목코드, 종목명) 또는 None
        """
        try:
            results = await self.search_stock(query)
            
            if not results:
                print(f"❌ '{query}'에 대한 검색 결과가 없습니다.")
                return None
            
            # 정확히 하나의 결과만 있으면 바로 반환
            if len(results) == 1:
                result = results[0]
                price_info = f" (현재가: {result.current_price:,}원)" if result.current_price else ""
                print(f"✅ 검색 결과: {result.symbol} {result.name}{price_info}")
                return (result.symbol, result.name)
            
            # 여러 결과가 있으면 선택하도록 함
            print(f"\n📋 '{query}' 검색 결과 ({len(results)}개):")
            print("-" * 60)
            print(f"{'번호':<4} {'종목코드':<8} {'종목명':<20} {'현재가'}")
            print("-" * 60)
            
            for i, result in enumerate(results, 1):
                price_str = f"{result.current_price:,}원" if result.current_price else "N/A"
                print(f"{i:<4} {result.symbol:<8} {result.name:<20} {price_str}")
            
            print("-" * 60)
            
            # 사용자 선택
            try:
                choice = input(f"선택하세요 (1-{len(results)}, 취소: Enter): ").strip()
                
                if not choice:
                    print("선택이 취소되었습니다.")
                    return None
                
                choice_num = int(choice)
                
                if 1 <= choice_num <= len(results):
                    selected = results[choice_num - 1]
                    print(f"✅ 선택됨: {selected.symbol} {selected.name}")
                    return (selected.symbol, selected.name)
                else:
                    print("❌ 잘못된 번호입니다.")
                    return None
                    
            except ValueError:
                print("❌ 숫자를 입력해주세요.")
                return None
            
        except Exception as e:
            self.logger.error(f"❌ 대화형 종목 선택 실패: {e}")
            print(f"❌ 종목 선택 중 오류가 발생했습니다: {e}")
            return None


# 테스트 함수
async def test_stock_search():
    """종목 검색 엔진 테스트"""
    print("=== Stock Search Engine Test ===")
    
    search_engine = StockSearchEngine()
    
    # 테스트 케이스들
    test_queries = [
        "005930",      # 정확한 종목코드
        "삼성전자",      # 정확한 종목명
        "삼성",         # 부분 검색
        "SK",          # 부분 검색
        "네이버",       # 정확한 종목명
        "035420",      # NAVER 종목코드
        "바이오",       # 부분 검색
    ]
    
    for query in test_queries:
        print(f"\n🔍 검색어: '{query}'")
        results = await search_engine.search_stock(query)
        
        if results:
            print(f"  결과: {len(results)}개")
            for result in results[:3]:  # 상위 3개만 출력
                price_str = f" ({result.current_price:,}원)" if result.current_price else ""
                print(f"    {result.symbol} {result.name}{price_str}")
        else:
            print("  결과 없음")
    
    print("\n[SUCCESS] Stock search engine test completed!")


if __name__ == "__main__":
    # 테스트 실행
    asyncio.run(test_stock_search())