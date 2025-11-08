#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/universe_manager.py

Universe Tiering 기반 종목 선정 시스템
- Core Universe: 안정적 대형주 (거래대금 상위)
- Candidate Universe: 전략 검증 통과 종목
- Exploratory Universe: 소형주/고변동성 종목
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
import pandas as pd
import numpy as np

from core.kiwoom_rest_client import KiwoomRESTClient, KiwoomAPIError

# 로거 설정
logger = logging.getLogger(__name__)


@dataclass
class StockMetadata:
    """종목 메타데이터"""
    symbol: str
    name: str
    market: str  # KRX, KOSDAQ, KONEX
    sector: str
    price_today: float
    market_cap: float  # 시가총액 (억원)

    # 유동성 지표 (60일 기준)
    avg_trade_value_60d: float  # 평균 거래대금
    avg_volume_60d: float  # 평균 거래량
    days_traded_60d: int  # 실거래일수

    # 변동성 지표 (30일 기준)
    price_std_30d: float  # 가격 표준편차
    volatility_30d: float  # 변동성

    # 데이터 품질
    days_history: int  # 데이터 보유 일수
    data_quality_score: float  # 0~1 (결측률 기반)

    # Universe 분류
    universe_tier: str  # core, candidate, exploratory

    # 메타정보
    last_updated: str


class UniverseManager:
    """Universe Tiering 기반 종목 관리"""

    # Universe 기준값 (한국 시장 기준)
    CORE_CRITERIA = {
        'min_trade_value_60d': 500_000_000,  # 5억원
        'min_price': 1000,  # 1,000원
        'min_history_days': 250,  # 약 1년
        'min_traded_days_60d': 50,  # 60일 중 최소 50일 거래
    }

    CANDIDATE_CRITERIA = {
        'min_trade_value_60d': 100_000_000,  # 1억원
        'min_price': 500,  # 500원
        'min_history_days': 100,  # 약 5개월
    }

    EXPLORATORY_CRITERIA = {
        'min_trade_value_60d': 10_000_000,  # 1천만원
        'min_price': 100,  # 100원
        'min_history_days': 60,  # 약 3개월
    }

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        data_dir: str = "./data/universe"
    ):
        """
        Args:
            app_key: 키움 앱 키
            app_secret: 키움 앱 시크릿
            data_dir: Universe 데이터 저장 디렉토리
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.data_dir = Path(data_dir)

        # 디렉토리 생성
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # REST 클라이언트
        self.client: Optional[KiwoomRESTClient] = None

        # Universe 저장
        self.stock_metadata: Dict[str, StockMetadata] = {}
        self.core_universe: List[str] = []
        self.candidate_universe: List[str] = []
        self.exploratory_universe: List[str] = []

    async def initialize(self):
        """초기화"""
        self.client = KiwoomRESTClient(
            app_key=self.app_key,
            app_secret=self.app_secret,
            max_requests_per_second=5.0
        )
        await self.client.initialize()
        logger.info("Universe Manager 초기화 완료")

    async def close(self):
        """종료"""
        if self.client:
            await self.client.close()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def fetch_stock_list(self) -> List[Dict[str, Any]]:
        """
        전체 종목 리스트 조회

        키움 API는 전체 종목 리스트를 제공하지 않으므로,
        조건검색을 통해 거래량/거래대금 기준으로 종목을 선별합니다.

        Returns:
            종목 리스트
        """
        logger.info("조건검색을 통한 종목 리스트 조회 중...")

        try:
            # 키움 조건검색 API (ka10010)
            # 거래량 급증 또는 거래대금 상위 종목 조회
            result = await self.client._request(
                api_id="ka10010",
                path="/api/dostk/cndsch",
                body={
                    "user_id": "",  # 사용자 ID (optional)
                    "scr_no": "0001",  # 화면번호
                    "cond_nm": "",  # 조건명
                    "cond_no": ""  # 조건번호
                }
            )

            # 응답 파싱
            stock_list = result.get('list', [])

            if not stock_list:
                stock_list = []
                logger.warning("조건검색 결과가 없습니다")

            logger.info(f"조건검색 종목 수: {len(stock_list)}개")
            return stock_list

        except KiwoomAPIError as e:
            logger.error(f"조건검색 실패: {e}")
            # Fallback: 주요 지수 구성종목으로 대체
            return await self._fetch_major_stocks()

    async def _fetch_major_stocks(self) -> List[Dict[str, Any]]:
        """
        Fallback: 기존 조건검색 결과 파일 활용

        main_condition_filter.py의 결과를 재사용
        """
        logger.info("기존 조건검색 결과로 Fallback")

        import glob
        import json

        # 최신 조건검색 결과 파일 찾기
        result_files = glob.glob("condition_results_*.json")
        if not result_files:
            logger.warning("조건검색 결과 파일이 없습니다")
            return []

        # 가장 최신 파일
        latest_file = max(result_files)
        logger.info(f"조건검색 결과 파일 로드: {latest_file}")

        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 종목 코드 추출
            stock_list = []
            for stock in data.get('stocks', []):
                stock_list.append({
                    'code': stock.get('code', ''),
                    'name': stock.get('name', ''),
                    'price': stock.get('current_price', 0)
                })

            logger.info(f"조건검색 결과: {len(stock_list)}개 종목")
            return stock_list

        except Exception as e:
            logger.error(f"조건검색 결과 로드 실패: {e}")
            return []

    async def calculate_stock_metrics(
        self,
        symbol: str,
        name: str,
        days: int = 250
    ) -> Optional[StockMetadata]:
        """
        개별 종목의 메트릭 계산

        Args:
            symbol: 종목코드
            name: 종목명
            days: 분석 기간

        Returns:
            StockMetadata 또는 None
        """
        try:
            # 일봉 데이터 조회 (최근 250일)
            result = await self.client._request(
                api_id="ka10081",
                path="/api/dostk/chart",
                body={
                    "stk_cd": f"{symbol}_KRX",
                    "inqr_strt_dt": "",  # 연속조회로 처리
                    "inqr_end_dt": "",
                    "upd_stkpc_tp": "1"  # 수정주가
                }
            )

            # 데이터 파싱
            chart_data = result.get('body', {}).get('stk_daily_pole_chart_qry', [])
            if not chart_data:
                return None

            df = pd.DataFrame(chart_data[:days])

            # 컬럼 매핑
            column_mapping = {
                'trd_dd': 'date',
                'stck_oprc': 'open',
                'stck_hgpr': 'high',
                'stck_lwpr': 'low',
                'stck_clpr': 'close',
                'acml_vol': 'volume',
                'acml_tr_pbmn': 'trade_value'  # 거래대금
            }

            for old_col, new_col in column_mapping.items():
                if old_col in df.columns:
                    df[new_col] = pd.to_numeric(df[old_col], errors='coerce')

            # 데이터 품질 확인
            if df.empty or len(df) < 60:
                return None

            # 지표 계산
            price_today = df['close'].iloc[0]

            # 60일 평균 (거래대금, 거래량)
            df_60d = df.head(60)
            avg_trade_value_60d = df_60d['trade_value'].mean()
            avg_volume_60d = df_60d['volume'].mean()
            days_traded_60d = df_60d[df_60d['volume'] > 0].shape[0]

            # 30일 변동성
            df_30d = df.head(30)
            price_std_30d = df_30d['close'].std()
            returns_30d = df_30d['close'].pct_change().dropna()
            volatility_30d = returns_30d.std() * np.sqrt(252)  # 연환산

            # 데이터 품질 점수 (결측률 기반)
            total_cells = len(df) * len(df.columns)
            missing_cells = df.isnull().sum().sum()
            data_quality_score = 1.0 - (missing_cells / total_cells)

            # Universe 분류
            universe_tier = self._classify_universe(
                avg_trade_value_60d=avg_trade_value_60d,
                price_today=price_today,
                days_history=len(df),
                days_traded_60d=days_traded_60d
            )

            # 시가총액 계산 (간단 추정: 발행주식수는 별도 API 필요)
            # 여기서는 거래대금 기반 추정치 사용
            market_cap = avg_trade_value_60d / avg_volume_60d * 10000  # 억원 단위

            metadata = StockMetadata(
                symbol=symbol,
                name=name,
                market="KRX",  # 실제로는 API에서 구분 필요
                sector="",  # 별도 API 필요
                price_today=price_today,
                market_cap=market_cap,
                avg_trade_value_60d=avg_trade_value_60d,
                avg_volume_60d=avg_volume_60d,
                days_traded_60d=days_traded_60d,
                price_std_30d=price_std_30d,
                volatility_30d=volatility_30d,
                days_history=len(df),
                data_quality_score=data_quality_score,
                universe_tier=universe_tier,
                last_updated=datetime.now().isoformat()
            )

            logger.info(f"[{name}] {universe_tier} - 거래대금: {avg_trade_value_60d/1e8:.1f}억")

            return metadata

        except Exception as e:
            logger.error(f"[{symbol}] 메트릭 계산 실패: {e}")
            return None

    def _classify_universe(
        self,
        avg_trade_value_60d: float,
        price_today: float,
        days_history: int,
        days_traded_60d: int
    ) -> str:
        """
        종목을 Universe로 분류

        Returns:
            'core', 'candidate', 'exploratory', 'excluded'
        """
        # Core Universe
        if (avg_trade_value_60d >= self.CORE_CRITERIA['min_trade_value_60d'] and
            price_today >= self.CORE_CRITERIA['min_price'] and
            days_history >= self.CORE_CRITERIA['min_history_days'] and
            days_traded_60d >= self.CORE_CRITERIA['min_traded_days_60d']):
            return 'core'

        # Candidate Universe
        if (avg_trade_value_60d >= self.CANDIDATE_CRITERIA['min_trade_value_60d'] and
            price_today >= self.CANDIDATE_CRITERIA['min_price'] and
            days_history >= self.CANDIDATE_CRITERIA['min_history_days']):
            return 'candidate'

        # Exploratory Universe
        if (avg_trade_value_60d >= self.EXPLORATORY_CRITERIA['min_trade_value_60d'] and
            price_today >= self.EXPLORATORY_CRITERIA['min_price'] and
            days_history >= self.EXPLORATORY_CRITERIA['min_history_days']):
            return 'exploratory'

        return 'excluded'

    async def build_universe(
        self,
        stock_list: Optional[List[str]] = None,
        max_stocks: int = 100
    ):
        """
        Universe 구축

        Args:
            stock_list: 분석할 종목 리스트 (None이면 전체)
            max_stocks: 최대 분석 종목 수
        """
        logger.info("=" * 80)
        logger.info("Universe 구축 시작")
        logger.info("=" * 80)

        # 종목 리스트 가져오기
        if stock_list is None:
            all_stocks = await self.fetch_stock_list()
            stock_list = [s['stk_cd'] for s in all_stocks[:max_stocks]]

        # 각 종목 메트릭 계산
        tasks = []
        for symbol in stock_list[:max_stocks]:
            # 종목명은 실제로 API에서 가져와야 함
            task = self.calculate_stock_metrics(symbol, symbol)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Universe 분류
        self.core_universe = []
        self.candidate_universe = []
        self.exploratory_universe = []

        for metadata in results:
            if metadata is None or isinstance(metadata, Exception):
                continue

            self.stock_metadata[metadata.symbol] = metadata

            if metadata.universe_tier == 'core':
                self.core_universe.append(metadata.symbol)
            elif metadata.universe_tier == 'candidate':
                self.candidate_universe.append(metadata.symbol)
            elif metadata.universe_tier == 'exploratory':
                self.exploratory_universe.append(metadata.symbol)

        # 통계 출력
        logger.info("=" * 80)
        logger.info("Universe 구축 완료")
        logger.info(f"  Core Universe: {len(self.core_universe)}개")
        logger.info(f"  Candidate Universe: {len(self.candidate_universe)}개")
        logger.info(f"  Exploratory Universe: {len(self.exploratory_universe)}개")
        logger.info(f"  Total: {len(self.stock_metadata)}개")
        logger.info("=" * 80)

    def save_universe(self, filename: str = "universe.json"):
        """Universe 저장"""
        data = {
            'metadata': {symbol: asdict(meta) for symbol, meta in self.stock_metadata.items()},
            'core': self.core_universe,
            'candidate': self.candidate_universe,
            'exploratory': self.exploratory_universe,
            'created_at': datetime.now().isoformat(),
            'criteria': {
                'core': self.CORE_CRITERIA,
                'candidate': self.CANDIDATE_CRITERIA,
                'exploratory': self.EXPLORATORY_CRITERIA
            }
        }

        filepath = self.data_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Universe 저장: {filepath}")

    def load_universe(self, filename: str = "universe.json"):
        """Universe 로드"""
        filepath = self.data_dir / filename

        if not filepath.exists():
            logger.warning(f"Universe 파일 없음: {filepath}")
            return

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 메타데이터 복원
        self.stock_metadata = {
            symbol: StockMetadata(**meta)
            for symbol, meta in data['metadata'].items()
        }

        self.core_universe = data['core']
        self.candidate_universe = data['candidate']
        self.exploratory_universe = data['exploratory']

        logger.info(f"Universe 로드: {filepath}")
        logger.info(f"  Core: {len(self.core_universe)}개")
        logger.info(f"  Candidate: {len(self.candidate_universe)}개")
        logger.info(f"  Exploratory: {len(self.exploratory_universe)}개")

    def get_universe_df(self) -> pd.DataFrame:
        """Universe를 DataFrame으로 변환"""
        if not self.stock_metadata:
            return pd.DataFrame()

        df = pd.DataFrame([asdict(meta) for meta in self.stock_metadata.values()])
        return df.sort_values('avg_trade_value_60d', ascending=False)


async def main():
    """테스트 코드"""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    if not app_key or not app_secret:
        print("환경변수에 KIWOOM_APP_KEY, KIWOOM_APP_SECRET 설정 필요")
        return

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 테스트 종목 (대형주)
    test_stocks = [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "035720",  # 카카오
        "005380",  # 현대차
        "035420",  # NAVER
    ]

    async with UniverseManager(app_key, app_secret, is_mock=True) as manager:
        # Universe 구축
        await manager.build_universe(stock_list=test_stocks)

        # 저장
        manager.save_universe()

        # DataFrame 출력
        df = manager.get_universe_df()
        print("\n=== Universe Summary ===")
        print(df[['symbol', 'name', 'universe_tier', 'avg_trade_value_60d', 'price_today']])


if __name__ == "__main__":
    asyncio.run(main())
