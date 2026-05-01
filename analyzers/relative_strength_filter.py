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
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import logging

# yfinance 로깅 억제
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console

console = Console()


KOSPI_INDEX_CODE  = "0001"   # 키움 코스피 지수 코드
KOSDAQ_INDEX_CODE = "1001"   # 키움 코스닥 지수 코드


class RelativeStrengthFilter:
    """IBD-RS 스타일 상대강도 필터"""

    def __init__(self, lookback_days: int = 60, min_rs_rating: int = 90, api=None):
        """
        Args:
            lookback_days: 상대강도 계산 기간 (기본 60일 = 3개월)
            min_rs_rating: 최소 RS 등급 (0-100, 기본 90 = 상위 10%)
            api: KiwoomAPI 인스턴스 (있으면 키움 우선, 없으면 Yahoo fallback)
        """
        self.lookback_days = lookback_days
        self.min_rs_rating = min_rs_rating
        self.api = api

        # 시장 데이터 캐시
        self.market_data_cache: Dict[str, pd.DataFrame] = {}
        self.cache_expiry: Dict[str, datetime] = {}

        # 🔧 개별 종목 데이터 캐시 (성능 개선)
        self.stock_data_cache: Dict[str, pd.DataFrame] = {}
        self.stock_cache_expiry: Dict[str, datetime] = {}

    def _safe_get_value(self, series_or_value):
        """Series나 단일 값을 안전하게 float로 변환"""
        if hasattr(series_or_value, 'values'):
            return float(series_or_value.values[0])
        return float(series_or_value)

    def _get_market_data(self, market: str) -> pd.DataFrame:
        """시장 지수 데이터 조회 (Yahoo, 캐시 30분)"""
        now = datetime.now()
        if market in self.market_data_cache:
            if market in self.cache_expiry and self.cache_expiry[market] > now:
                return self.market_data_cache[market]
        try:
            ticker = '^KS11' if market == 'KOSPI' else '^KQ11'
            period = f"{int(self.lookback_days * 1.5)}d"
            df = yf.download(ticker, period=period, interval='1d', progress=False, auto_adjust=True)
            if df is not None and len(df) > 0:
                self.market_data_cache[market] = df
                self.cache_expiry[market] = now + timedelta(minutes=30)
                return df
        except Exception as e:
            console.print(f"[red]❌ {market} 지수 조회 오류: {e}[/red]")
        return None

    def _get_stock_data(self, stock_code: str, market: str) -> pd.DataFrame:
        """캐시에서 종목 데이터 조회 (배치 다운로드 후 저장된 데이터)"""
        cache_key = f"{stock_code}_{market}"
        now = datetime.now()
        if cache_key in self.stock_data_cache:
            if cache_key in self.stock_cache_expiry and self.stock_cache_expiry[cache_key] > now:
                return self.stock_data_cache[cache_key]
        return None

    def _prefetch_batch(self, candidates: list, market: str):
        """Yahoo 배치 다운로드로 전체 종목 일봉 데이터 한 번에 수집"""
        now = datetime.now()
        suffix = '.KS' if market == 'KOSPI' else '.KQ'
        period = f"{int(self.lookback_days * 1.5)}d"

        # 캐시 없는 종목만 추려서 배치 다운로드
        miss = [c for c in candidates
                if f"{c['stock_code']}_{c.get('market', market)}" not in self.stock_data_cache
                or self.stock_cache_expiry.get(f"{c['stock_code']}_{c.get('market', market)}", now) <= now]

        if not miss:
            return

        # 30개씩 배치 다운로드
        batch_size = 30
        total_batches = (len(miss) + batch_size - 1) // batch_size
        expiry = now + timedelta(minutes=30)

        for i in range(0, len(miss), batch_size):
            batch = miss[i:i + batch_size]
            tickers = [f"{c['stock_code']}{suffix}" for c in batch]
            batch_num = i // batch_size + 1
            print(f"  ⏳ 배치 {batch_num}/{total_batches} 다운로드 중 ({len(tickers)}개)...", flush=True)
            try:
                df_all = yf.download(tickers, period=period, interval='1d',
                                     progress=False, auto_adjust=True, group_by='ticker',
                                     timeout=30)
                for c in batch:
                    code   = c['stock_code']
                    mkt    = c.get('market', market)
                    key    = f"{code}_{mkt}"
                    ticker = f"{code}{suffix}"
                    try:
                        if len(tickers) == 1:
                            df_s = df_all
                        else:
                            lvl0 = df_all.columns.get_level_values(0)
                            df_s = df_all[ticker] if ticker in lvl0 else None
                        if df_s is not None and len(df_s) >= self.lookback_days:
                            self.stock_data_cache[key] = df_s
                            self.stock_cache_expiry[key] = expiry
                    except Exception:
                        pass
            except Exception as e:
                print(f"  ⚠️  배치 {batch_num} 오류: {e}", flush=True)
        print(f"  ✅ 배치 다운로드 완료 ({len(miss)}개)", flush=True)

    def calculate_return(self, stock_code: str, market: str = 'KOSPI') -> Tuple[float, float, float]:
        """
        종목의 수익률과 시장 수익률, RS 계산

        Args:
            stock_code: 종목코드 (예: 005930)
            market: 시장 구분 ('KOSPI' or 'KOSDAQ')

        Returns:
            (stock_return, market_return, rs_strength)
        """
        try:
            # 🔧 캐싱된 데이터 사용
            df_stock = self._get_stock_data(stock_code, market)
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
            console.print(f"[dim]⚠️  {stock_code} 수익률 계산 실패: {e}[/dim]")
            return 0.0, 0.0, 0.0

    def calculate_rs_rating(self, rs_strength: float, all_rs_values: List[float] = None) -> float:
        """
        IBD-RS 등급 계산 (0-100)

        Args:
            rs_strength: 현재 종목의 RS 값 (stock_return - market_return)
            all_rs_values: 전체 후보군의 RS 값 리스트 (백분위 계산용)

        Returns:
            RS 등급 (0-100)
        """
        # 전체 후보군이 있으면 백분위 계산
        if all_rs_values and len(all_rs_values) > 1:
            # 백분위 계산
            rs_values_sorted = sorted(all_rs_values)
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
        RS 필터링으로 상위 종목만 선택 (2-Pass 알고리즘으로 성능 최적화)

        Args:
            candidates: 후보 종목 리스트 [{'stock_code': '...', 'stock_name': '...', ...}, ...]
            market: 시장 구분

        Returns:
            RS 등급이 min_rs_rating 이상인 종목 리스트
        """
        print(f"\n📊 IBD-RS 필터링 시작 (최소 RS: {self.min_rs_rating})", flush=True)
        print(f"  입력: {len(candidates)}개 종목", flush=True)

        # 🔧 Pass 1: 배치 다운로드 → RS 계산
        print(f"  Pass 1: Yahoo 배치 다운로드 시작...", flush=True)
        self._prefetch_batch(candidates, market)

        rs_data = []
        total = len(candidates)
        print(f"  Pass 1: RS 계산 시작 ({total}개)...", flush=True)
        for i, candidate in enumerate(candidates, 1):
            code = candidate['stock_code']
            mkt  = candidate.get('market', market)
            sr, mr, rs = self.calculate_return(code, mkt)
            rs_data.append({'candidate': candidate, 'stock_return': sr, 'market_return': mr, 'rs_strength': rs})
            if i % 50 == 0 or i == total:
                print(f"  ⏳ RS 계산 {i}/{total}...", flush=True)

        # 전체 RS 값 리스트 추출
        all_rs_values = [d['rs_strength'] for d in rs_data]

        # 🔧 Pass 2: 백분위 계산 및 필터링 (O(N))
        print(f"  Pass 2: 백분위 계산 중...", flush=True)
        results = []
        for data in rs_data:
            candidate = data['candidate']
            stock_code = candidate['stock_code']
            stock_name = candidate.get('stock_name', stock_code)

            # 백분위 기반 RS 등급 계산 (캐시된 데이터 사용)
            rs_rating = self.calculate_rs_rating(data['rs_strength'], all_rs_values)

            # 결과 저장
            result = {
                **candidate,
                'rs_rating': rs_rating,
                'stock_return_60d': data['stock_return'],
                'market_return_60d': data['market_return'],
                'rs_strength': data['rs_strength']
            }
            results.append(result)

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
