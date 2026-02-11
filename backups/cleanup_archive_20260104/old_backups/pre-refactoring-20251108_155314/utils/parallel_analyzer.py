#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
병렬 분석 유틸리티 - 뉴스 검색 + KIS API 호출 최적화
"""

import asyncio
from typing import List, Dict, Tuple, Optional, Any
from utils.logger import get_logger
from utils.rate_limiter import get_rate_limiter

logger = get_logger("ParallelAnalyzer")

class ParallelStockAnalyzer:
    """병렬 종목 분석 처리"""
    
    def __init__(self, data_collector, news_collector=None, analysis_engine=None):
        self.data_collector = data_collector
        self.news_collector = news_collector
        self.analysis_engine = analysis_engine
        self.rate_limiter = get_rate_limiter()
    
    async def analyze_stock_parallel(self, symbol: str, name: str, strategy: str) -> Optional[Dict]:
        """단일 종목 병렬 분석 (뉴스 + KIS API)"""
        try:
            # 병렬 실행: 뉴스 검색 + KIS API 호출
            tasks = []
            
            # 1. 뉴스 검색 (독립적, rate limit 불필요)
            if self.news_collector:
                tasks.append(self._fetch_news(symbol, name))
            else:
                tasks.append(asyncio.create_task(self._empty_result()))
            
            # 2. KIS API: 종목 정보 (rate limit 적용)
            tasks.append(self._fetch_stock_info(symbol))
            
            # 3. KIS API: 차트 데이터 (rate limit 적용)
            tasks.append(self._fetch_chart_data(symbol))
            
            # 병렬 실행
            news_data, stock_info, chart_data = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 에러 처리
            if isinstance(stock_info, Exception):
                error_type = type(stock_info).__name__
                error_msg = str(stock_info)
                logger.warning(f"{symbol} ({name}) 종목 정보 조회 실패 [{error_type}]: {error_msg}")
                return None
            
            # 결과 통합
            stock_data = {
                'symbol': symbol,
                'name': name,
                'strategy': strategy,
                'news': news_data if not isinstance(news_data, Exception) else [],
                'chart_data': chart_data if not isinstance(chart_data, Exception) else None,
            }
            
            # stock_info 통합
            if stock_info and hasattr(stock_info, '__dict__'):
                stock_data.update(stock_info.__dict__)
            
            # 분석 엔진 실행
            if self.analysis_engine:
                result = await self.analysis_engine.analyze_comprehensive(
                    symbol=symbol,
                    name=name,
                    stock_data=stock_data,
                    strategy=strategy
                )
                return result
            
            return stock_data
            
        except Exception as e:
            logger.error(f"{symbol} 병렬 분석 실패: {e}")
            return None
    
    async def analyze_stocks_batch(
        self,
        stocks: List[Tuple[str, str]],
        strategy: str,
        max_concurrent: int = 8
    ) -> List[Dict]:
        """다중 종목 배치 분석 (동시 실행 제한)

        Note: max_concurrent=8로 설정
        - 각 종목당 2개의 KIS API 호출 (stock_info + chart_data)
        - 8 stocks × 2 calls = 16 concurrent API calls
        - Rate limiter: 18 calls/sec로 설정되어 안전
        """
        results = []
        
        # 세마포어로 동시 실행 수 제한
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def analyze_with_semaphore(symbol: str, name: str):
            async with semaphore:
                return await self.analyze_stock_parallel(symbol, name, strategy)
        
        # 모든 종목 병렬 분석
        tasks = [
            analyze_with_semaphore(symbol, name)
            for symbol, name in stocks
        ]
        
        logger.info(f"📊 {len(stocks)}개 종목 병렬 분석 시작 (최대 동시 {max_concurrent}개)")
        
        # gather로 모두 실행
        analysis_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 성공한 결과만 필터링
        for result in analysis_results:
            if result and not isinstance(result, Exception):
                results.append(result)
        
        logger.info(f"✅ 병렬 분석 완료: {len(results)}/{len(stocks)}개 성공")
        return results
    
    async def _fetch_news(self, symbol: str, name: str) -> List[Dict]:
        """뉴스 검색 (병렬) - 네이버 API 우선, 크롤링 폴백"""
        try:
            if not self.news_collector:
                return []

            # 하이브리드 검색 사용
            from data_collectors.news_search_wrapper import search_news_hybrid
            news_items = await search_news_hybrid(self.news_collector, name, symbol)
            return news_items[:5] if news_items else []
        except Exception as e:
            logger.debug(f"{symbol} 뉴스 검색 실패: {e}")
            return []
    
    async def _fetch_stock_info(self, symbol: str):
        """종목 정보 조회 (rate limit 적용)"""
        try:
            await self.rate_limiter.acquire()
            return await self.data_collector.get_stock_info(symbol)
        except Exception as e:
            logger.debug(f"{symbol} 종목 정보 조회 실패: {e}")
            raise
    
    async def _fetch_chart_data(self, symbol: str):
        """차트 데이터 조회 (rate limit 적용)"""
        try:
            await self.rate_limiter.acquire()
            # OHLCV 데이터 조회 (최근 30일)
            return await self.data_collector.get_ohlcv_data(symbol, period='D', count=30)
        except Exception as e:
            logger.debug(f"{symbol} 차트 데이터 조회 실패: {e}")
            return None
    
    async def _empty_result(self):
        """빈 결과 반환"""
        return []
