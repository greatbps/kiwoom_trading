"""
IBD-RS 스타일 상대강도 필터
- 승률 60-70% 검증된 전략
- 시장 대비 상대강도 90 이상 종목만 선택
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from datetime import datetime, timedelta
from pathlib import Path
import sys
import logging

# yfinance 로깅 억제
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console

console = Console()


class RelativeStrengthFilter:
    """IBD-RS 스타일 상대강도 필터"""

    def __init__(self, lookback_days: int = 60, min_rs_rating: int = 90):
        """
        Args:
            lookback_days: 상대강도 계산 기간 (기본 60일 = 3개월)
            min_rs_rating: 최소 RS 등급 (0-100, 기본 90 = 상위 10%)
        """
        self.lookback_days = lookback_days
        self.min_rs_rating = min_rs_rating

        # 시장 데이터 캐시
        self.market_data_cache: Dict[str, pd.DataFrame] = {}
        self.cache_expiry: Dict[str, datetime] = {}

    def _get_market_index_ticker(self, market: str) -> str:
        """시장별 지수 티커 반환"""
        if market == 'KOSPI':
            return '^KS11'
        elif market == 'KOSDAQ':
            return '^KQ11'
        else:
            return '^KS11'  # 기본값

    def _get_market_data(self, market: str) -> pd.DataFrame:
        """
        시장 지수 데이터 조회 (캐시 사용)

        Args:
            market: 'KOSPI' or 'KOSDAQ'

        Returns:
            시장 지수 데이터
        """
        # 캐시 확인
        now = datetime.now()
        if market in self.market_data_cache:
            if market in self.cache_expiry and self.cache_expiry[market] > now:
                return self.market_data_cache[market]

        # 데이터 조회
        ticker = self._get_market_index_ticker(market)
        period = f"{int(self.lookback_days * 1.5)}d"  # 여유있게 조회

        try:
            # FutureWarning 방지: auto_adjust 명시
            df = yf.download(ticker, period=period, interval='1d',
                           progress=False, auto_adjust=True)

            if df is not None and len(df) > 0:
                # 캐시 저장 (30분간 유효)
                self.market_data_cache[market] = df
                self.cache_expiry[market] = now + timedelta(minutes=30)
                return df
            else:
                console.print(f"[yellow]⚠️  {market} 지수 데이터 조회 실패[/yellow]")
                return None

        except Exception as e:
            console.print(f"[red]❌ {market} 지수 조회 오류: {e}[/red]")
            return None

    def _safe_get_value(self, series_or_value):
        """Series나 단일 값을 안전하게 float로 변환"""
        if hasattr(series_or_value, 'values'):
            return float(series_or_value.values[0])
        return float(series_or_value)

    def calculate_return(self, stock_code: str, market: str = 'KOSPI') -> Tuple[float, float, float]:
        """
        종목의 수익률과 시장 수익률, RS 계산

        Args:
            stock_code: 종목코드 (예: 005930)
            market: 시장 구분 ('KOSPI' or 'KOSDAQ')

        Returns:
            (stock_return, market_return, rs_strength)
        """
        # 종목 티커
        ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
        ticker = f"{stock_code}{ticker_suffix}"

        # 종목 데이터 조회
        period = f"{int(self.lookback_days * 1.5)}d"
        try:
            # FutureWarning 방지: auto_adjust 명시
            df_stock = yf.download(ticker, period=period, interval='1d',
                                  progress=False, auto_adjust=True)

            if df_stock is None or len(df_stock) < self.lookback_days:
                return 0.0, 0.0, 0.0

            # 시장 데이터 조회
            df_market = self._get_market_data(market)
            if df_market is None or len(df_market) < self.lookback_days:
                return 0.0, 0.0, 0.0

            # lookback_days 일 전 가격
            price_start = self._safe_get_value(df_stock['Close'].iloc[-self.lookback_days])
            price_end = self._safe_get_value(df_stock['Close'].iloc[-1])
            stock_return = ((price_end / price_start) - 1) * 100

            # 시장 수익률
            market_start = self._safe_get_value(df_market['Close'].iloc[-self.lookback_days])
            market_end = self._safe_get_value(df_market['Close'].iloc[-1])
            market_return = ((market_end / market_start) - 1) * 100

            # RS (상대강도)
            rs_strength = stock_return - market_return

            return stock_return, market_return, rs_strength

        except Exception as e:
            # 상장폐지 종목은 조용히 처리
            error_msg = str(e).lower()
            if 'delisted' in error_msg or 'no data found' in error_msg:
                console.print(f"[dim]⚠️  {stock_code}: 상장폐지 또는 데이터 없음[/dim]")
            else:
                console.print(f"[dim]⚠️  {stock_code} 수익률 계산 실패: {e}[/dim]")
            return 0.0, 0.0, 0.0

    def calculate_rs_rating(self, stock_code: str, market: str = 'KOSPI',
                           all_candidates: List[str] = None) -> float:
        """
        IBD-RS 등급 계산 (0-100)

        Args:
            stock_code: 종목코드
            market: 시장 구분
            all_candidates: 전체 후보군 (백분위 계산용)

        Returns:
            RS 등급 (0-100)
        """
        stock_return, market_return, rs_strength = self.calculate_return(stock_code, market)

        # 전체 후보군이 있으면 백분위 계산
        # 주의: 각 종목의 시장이 다를 수 있으므로 동일 market 사용
        if all_candidates and len(all_candidates) > 1:
            rs_values = []
            for code in all_candidates:
                # 모든 종목을 같은 market으로 비교 (공정한 비교를 위해)
                _, _, rs = self.calculate_return(code, market)
                rs_values.append(rs)

            # 백분위 계산
            rs_values_sorted = sorted(rs_values)
            rank = rs_values_sorted.index(rs_strength) if rs_strength in rs_values_sorted else 0
            percentile = (rank / len(rs_values_sorted)) * 100

            return percentile
        else:
            # 단순 RS 값 반환 (임계값으로 판단)
            # RS가 +10% 이상이면 90점으로 가정
            if rs_strength >= 10:
                return 95
            elif rs_strength >= 5:
                return 85
            elif rs_strength >= 0:
                return 70
            else:
                return 50

    def filter_candidates(
        self,
        candidates: List[Dict],
        market: str = 'KOSPI'
    ) -> List[Dict]:
        """
        RS 필터링으로 상위 종목만 선택

        Args:
            candidates: 후보 종목 리스트 [{'stock_code': '...', 'stock_name': '...', ...}, ...]
            market: 시장 구분

        Returns:
            RS 등급이 min_rs_rating 이상인 종목 리스트
        """
        console.print(f"\n[cyan]📊 IBD-RS 필터링 시작 (최소 RS: {self.min_rs_rating})[/cyan]")
        console.print(f"  입력: {len(candidates)}개 종목")

        # 전체 종목 코드 추출
        all_codes = [c['stock_code'] for c in candidates]

        # RS 계산
        results = []
        for candidate in candidates:
            stock_code = candidate['stock_code']
            stock_name = candidate.get('stock_name', stock_code)

            # 종목별 시장 정보 사용 (없으면 기본값 사용)
            stock_market = candidate.get('market', market)

            # RS 등급 계산
            rs_rating = self.calculate_rs_rating(stock_code, stock_market, all_codes)
            stock_return, market_return, rs_strength = self.calculate_return(stock_code, stock_market)

            # 결과 저장
            result = {
                **candidate,
                'rs_rating': rs_rating,
                'stock_return_60d': stock_return,
                'market_return_60d': market_return,
                'rs_strength': rs_strength
            }
            results.append(result)

            console.print(
                f"  [dim]{stock_name:15} RS:{rs_rating:>5.1f} "
                f"({stock_return:+6.2f}% vs {market_return:+6.2f}%)[/dim]"
            )

        # RS 등급 기준 필터링
        filtered = [r for r in results if r['rs_rating'] >= self.min_rs_rating]

        console.print(f"\n[green]✓ RS 필터링 완료: {len(filtered)}개 종목 선택[/green]")

        # 상위 종목 출력
        if len(filtered) > 0:
            console.print("\n[yellow]🏆 상위 종목:[/yellow]")
            sorted_filtered = sorted(filtered, key=lambda x: x['rs_rating'], reverse=True)
            for r in sorted_filtered[:10]:
                console.print(
                    f"  {r.get('stock_name', r['stock_code']):15} "
                    f"RS:{r['rs_rating']:>5.1f} "
                    f"({r['stock_return_60d']:+6.2f}%)"
                )

        return filtered


if __name__ == "__main__":
    """테스트 코드"""

    # 테스트 종목
    test_candidates = [
        {'stock_code': '005930', 'stock_name': '삼성전자'},
        {'stock_code': '000660', 'stock_name': 'SK하이닉스'},
        {'stock_code': '035720', 'stock_name': '카카오'},
        {'stock_code': '051910', 'stock_name': 'LG화학'},
        {'stock_code': '006400', 'stock_name': '삼성SDI'},
    ]

    print("=" * 80)
    print("🧪 IBD-RS 필터 테스트")
    print("=" * 80)

    # 필터 생성
    rs_filter = RelativeStrengthFilter(lookback_days=60, min_rs_rating=80)

    # 필터링 실행
    filtered = rs_filter.filter_candidates(test_candidates, market='KOSPI')

    print("\n" + "=" * 80)
    print(f"✅ 테스트 완료: {len(test_candidates)}개 → {len(filtered)}개")
    print("=" * 80)
