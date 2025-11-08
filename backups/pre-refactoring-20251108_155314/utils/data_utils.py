#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/data_utils.py

데이터 수집 및 처리 유틸리티
"""

import asyncio
from typing import List, Tuple
from rich.console import Console

console = Console()

class DataUtils:
    """데이터 처리 유틸리티 클래스"""
    
    def __init__(self):
        pass
    def _enhance_stock_names(self, stocks: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """종목명이 없거나 부족한 경우 pykrx로 보완"""
        try:
            enhanced_stocks = []
            for symbol, name in stocks:
                # 종목명이 없거나 기본값인 경우 pykrx로 보완 시도
                if not name or name.startswith('종목') or len(name.strip()) == 0:
                    try:
                        from pykrx import stock as pykrx_stock
                        real_name = pykrx_stock.get_market_ticker_name(symbol)
                        if real_name and real_name.strip():
                            name = real_name.strip()
                        else:
                            # pykrx로도 찾을 수 없으면 기본 포맷 사용
                            name = f"종목{symbol}"
                    except Exception:
                        # pykrx 실패시 기본 포맷 사용
                        name = f"종목{symbol}"
                
                enhanced_stocks.append((symbol, name))
            
            return enhanced_stocks
            
        except Exception as e:
            console.print(f"[dim]⚠️ 종목명 보완 실패: {e}[/dim]")
            return stocks  # 원본 그대로 반환
    async def safe_get_filtered_stocks(self, collector, limit: int = 50) -> List[Tuple[str, str]]:
        """KISCollector에서 안전하게 필터링된 종목 조회"""
        try:
            if hasattr(collector, 'get_filtered_stocks'):
                stocks = await collector.get_filtered_stocks(limit)
            elif hasattr(collector, 'get_filtered_stocks_pykrx'):
                stocks = await collector.get_filtered_stocks_pykrx(limit)
            else:
                # 기본 종목 리스트 반환
                all_stocks = await collector.get_stock_list()
                stocks = all_stocks[:limit]
            
            # 종목명이 없는 경우 동적으로 조회
            enhanced_stocks = []
            for symbol, name in stocks:
                if not name or name.startswith('종목'):
                    # 동적 종목명 조회 (하드코딩 없음)
                    name = await self._get_dynamic_stock_name(symbol, collector)
                
                # 클린업된 종목명 사용
                if hasattr(collector, '_clean_stock_name'):
                    name = collector._clean_stock_name(name)
                
                enhanced_stocks.append((symbol, name))
            
            return enhanced_stocks
            
        except Exception as e:
            if hasattr(collector, 'logger'):
                collector.logger.error(f"❌ 안전한 종목 조회 실패: {e}")
            else:
                console.print(f"[red]❌ 안전한 종목 조회 실패: {e}[/red]")
            
            # 최후의 수단 - 빈 리스트 반환하거나 API에서 동적 조회
            return await self._get_fallback_stocks(collector)
    
    async def _get_fallback_stocks(self, collector) -> List[Tuple[str, str]]:
        """fallback 종목 리스트 - 동적 조회"""
        try:
            # KRX나 다른 API에서 동적으로 주요 종목 조회
            if hasattr(collector, 'get_major_stocks'):
                return await collector.get_major_stocks(limit=5)
            else:
                # 정말 최후의 수단 - 빈 리스트
                return []
        except Exception:
            return []    
    
    async def _get_dynamic_stock_name(self, symbol: str, collector) -> str:
        """동적 종목명 조회 (하드코딩 없음)"""
        # 1. pykrx 재시도
        try:
            from pykrx import stock as pykrx_stock
            real_name = pykrx_stock.get_market_ticker_name(symbol)
            if real_name and real_name.strip():
                return real_name.strip()
        except Exception:
            pass
        
        # 2. KIS API 재조회
        try:
            if hasattr(collector, 'get_stock_info'):
                stock_info = await collector.get_stock_info(symbol)
                if stock_info and stock_info.get('name'):
                    return stock_info['name']
        except Exception:
            pass
        
        # 3. 기본 이름
        return f'종목{symbol}'
    
    
    def check_collector_methods(self, collector) -> bool:
        """KISCollector 필수 메서드 존재 여부 확인"""
        required_methods = ['get_stock_list', 'get_stock_info']
        optional_methods = ['get_filtered_stocks', 'collect_filtered_stocks', 'get_filtered_stocks_pykrx']
        
        missing_required = [m for m in required_methods if not hasattr(collector, m)]
        available_optional = [m for m in optional_methods if hasattr(collector, m)]
        
        if missing_required:
            if hasattr(collector, 'logger'):
                collector.logger.error(f"❌ 필수 메서드 누락: {missing_required}")
            else:
                console.print(f"[red]❌ 필수 메서드 누락: {missing_required}[/red]")
            return False
        
        if not available_optional:
            if hasattr(collector, 'logger'):
                collector.logger.warning("⚠️ 필터링 메서드가 없어 기본 종목 리스트만 사용 가능")
            else:
                console.print("[yellow]⚠️ 필터링 메서드가 없어 기본 종목 리스트만 사용 가능[/yellow]")
        
        if hasattr(collector, 'logger'):
            collector.logger.info(f"✅ 필수 메서드: {required_methods}")
            collector.logger.info(f"📋 사용 가능한 선택 메서드: {available_optional}")
        else:
            console.print(f"[green]✅ 필수 메서드: {required_methods}[/green]")
            console.print(f"[blue]📋 사용 가능한 선택 메서드: {available_optional}[/blue]")
        
        return True
    
    async def ensure_collector_methods(self, collector) -> bool:
        """KISCollector에 필요한 메서드가 없으면 추가"""
        try:
            # get_filtered_stocks 메서드가 없으면 추가
            if not hasattr(collector, 'get_filtered_stocks'):
                async def get_filtered_stocks(limit: int = 50, use_cache: bool = True):
                    """필터링된 종목 리스트 반환"""
                    try:
                        # pykrx 방식 우선 시도
                        if hasattr(collector, 'get_filtered_stocks_pykrx'):
                            return await collector.get_filtered_stocks_pykrx(limit, use_cache)
                        
                        # 기본 방식
                        if hasattr(collector, 'logger'):
                            collector.logger.info(f"🔍 기본 필터링 시작 (목표: {limit}개)")
                        
                        # 전체 종목 리스트 조회
                        all_stocks = await collector.get_stock_list()
                        
                        # 샘플링
                        import random
                        sample_size = min(300, len(all_stocks))
                        sample_stocks = random.sample(all_stocks, sample_size)
                        
                        filtered_stocks = []
                        for symbol, name in sample_stocks:
                            if len(filtered_stocks) >= limit:
                                break
                                
                            try:
                                stock_info = await collector.get_stock_info(symbol)
                                if stock_info and collector._meets_filter_criteria(stock_info):
                                    filtered_stocks.append((symbol, stock_info['name']))
                                await asyncio.sleep(0.05)
                            except:
                                continue
                        
                        if not filtered_stocks:
                            return await collector._get_major_stocks_as_fallback(limit)
                        
                        return filtered_stocks
                        
                    except Exception as e:
                        if hasattr(collector, 'logger'):
                            collector.logger.error(f"❌ 필터링 실패: {e}")
                        return await collector._get_major_stocks_as_fallback(limit)
                
                # 메서드를 collector 객체에 바인딩
                import types
                collector.get_filtered_stocks = types.MethodType(get_filtered_stocks, collector)
                if hasattr(collector, 'logger'):
                    collector.logger.info("✅ get_filtered_stocks 메서드 추가됨")
            
            return True
            
        except Exception as e:
            if hasattr(collector, 'logger'):
                collector.logger.error(f"❌ 메서드 추가 실패: {e}")
            else:
                console.print(f"[red]❌ 메서드 추가 실패: {e}[/red]")
            return False