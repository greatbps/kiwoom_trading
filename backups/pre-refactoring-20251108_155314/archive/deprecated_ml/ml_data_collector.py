#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/ml_data_collector.py

ML 학습용 데이터 수집 모듈
- 키움 REST API를 통한 분봉/일봉 데이터 수집
- 여러 종목의 데이터 병렬 수집
- 데이터 정규화 및 저장
- 수집 진행상황 모니터링
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
import json

from core.kiwoom_rest_client import KiwoomRESTClient, KiwoomAPIError, RateLimitError

# 로거 설정
logger = logging.getLogger(__name__)


@dataclass
class CollectionTask:
    """데이터 수집 작업"""
    stock_code: str
    stock_name: str
    minute_interval: int = 5
    max_pages: int = 100
    status: str = "pending"  # pending, running, completed, failed
    collected_count: int = 0
    error_message: Optional[str] = None


class MLDataCollector:
    """ML 학습용 데이터 수집기"""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        data_dir: str = "./data/ml_training",
        max_concurrent_tasks: int = 3
    ):
        """
        Args:
            app_key: 키움 앱 키
            app_secret: 키움 앱 시크릿
            data_dir: 데이터 저장 디렉토리
            max_concurrent_tasks: 최대 동시 작업 수
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.data_dir = Path(data_dir)
        self.max_concurrent_tasks = max_concurrent_tasks

        # 디렉토리 생성
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "raw").mkdir(exist_ok=True)
        (self.data_dir / "processed").mkdir(exist_ok=True)
        (self.data_dir / "metadata").mkdir(exist_ok=True)

        # REST 클라이언트
        self.client: Optional[KiwoomRESTClient] = None

        # 수집 작업 큐
        self.tasks: List[CollectionTask] = []

        # 통계
        self.stats = {
            'total_tasks': 0,
            'completed_tasks': 0,
            'failed_tasks': 0,
            'total_data_points': 0,
            'start_time': None,
            'end_time': None
        }

    async def initialize(self):
        """초기화"""
        self.client = KiwoomRESTClient(
            app_key=self.app_key,
            app_secret=self.app_secret,
            max_requests_per_second=3.0  # 보수적으로 초당 3회
        )
        await self.client.initialize()
        logger.info("ML 데이터 수집기 초기화 완료")

    async def close(self):
        """종료"""
        if self.client:
            await self.client.close()
            self.client = None

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def add_stock(
        self,
        stock_code: str,
        stock_name: str,
        minute_interval: int = 5,
        max_pages: int = 100
    ):
        """
        수집 대상 종목 추가

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            minute_interval: 분봉 간격
            max_pages: 최대 페이지 수
        """
        task = CollectionTask(
            stock_code=stock_code,
            stock_name=stock_name,
            minute_interval=minute_interval,
            max_pages=max_pages
        )
        self.tasks.append(task)
        self.stats['total_tasks'] += 1
        logger.info(f"수집 대상 추가: {stock_name}({stock_code})")

    def add_stocks_from_list(
        self,
        stock_list: List[Dict[str, str]],
        minute_interval: int = 5,
        max_pages: int = 100
    ):
        """
        여러 종목을 리스트로 추가

        Args:
            stock_list: [{"code": "005930", "name": "삼성전자"}, ...]
            minute_interval: 분봉 간격
            max_pages: 최대 페이지 수
        """
        for stock in stock_list:
            self.add_stock(
                stock_code=stock['code'],
                stock_name=stock['name'],
                minute_interval=minute_interval,
                max_pages=max_pages
            )

    async def _collect_single_stock(self, task: CollectionTask) -> bool:
        """
        단일 종목 데이터 수집

        Args:
            task: 수집 작업

        Returns:
            성공 여부
        """
        task.status = "running"
        logger.info(f"[{task.stock_name}] 데이터 수집 시작")

        try:
            # 분봉 데이터 수집
            data = await self.client.get_all_minute_chart_data(
                stock_code=task.stock_code,
                minute_interval=task.minute_interval,
                adjusted_price=True,
                max_pages=task.max_pages
            )

            if not data:
                task.status = "failed"
                task.error_message = "데이터 없음"
                logger.warning(f"[{task.stock_name}] 데이터 없음")
                return False

            task.collected_count = len(data)

            # DataFrame 변환
            df = pd.DataFrame(data)

            # 컬럼명 영문화
            column_mapping = {
                'cntr_tm': 'datetime',
                'cur_prc': 'close',
                'open_pric': 'open',
                'high_pric': 'high',
                'low_pric': 'low',
                'trde_qty': 'volume',
                'pred_pre': 'change',
                'pred_pre_sig': 'change_sign'
            }
            df = df.rename(columns=column_mapping)

            # 데이터 타입 변환
            df['datetime'] = pd.to_datetime(df['datetime'], format='%H%M%S')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 정렬 (시간 순)
            df = df.sort_values('datetime')

            # 원본 데이터 저장 (CSV)
            raw_file = self.data_dir / "raw" / f"{task.stock_code}_{task.minute_interval}min.csv"
            df.to_csv(raw_file, index=False, encoding='utf-8-sig')
            logger.info(f"[{task.stock_name}] 원본 데이터 저장: {raw_file}")

            # 메타데이터 저장
            metadata = {
                'stock_code': task.stock_code,
                'stock_name': task.stock_name,
                'minute_interval': task.minute_interval,
                'data_count': len(df),
                'date_range': {
                    'start': df['datetime'].min().isoformat() if not df.empty else None,
                    'end': df['datetime'].max().isoformat() if not df.empty else None
                },
                'collected_at': datetime.now().isoformat(),
                'columns': list(df.columns)
            }

            metadata_file = self.data_dir / "metadata" / f"{task.stock_code}.json"
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            task.status = "completed"
            self.stats['completed_tasks'] += 1
            self.stats['total_data_points'] += len(df)
            logger.info(f"[{task.stock_name}] 수집 완료: {len(df)}건")

            return True

        except RateLimitError as e:
            logger.warning(f"[{task.stock_name}] Rate Limit 에러: {e}")
            task.status = "pending"  # 재시도 가능하도록
            task.error_message = str(e)
            await asyncio.sleep(30)  # 30초 대기
            return False

        except KiwoomAPIError as e:
            logger.error(f"[{task.stock_name}] API 에러: {e}")
            task.status = "failed"
            task.error_message = str(e)
            self.stats['failed_tasks'] += 1
            return False

        except Exception as e:
            logger.error(f"[{task.stock_name}] 예상치 못한 에러: {e}", exc_info=True)
            task.status = "failed"
            task.error_message = str(e)
            self.stats['failed_tasks'] += 1
            return False

    async def collect_all(self) -> Dict[str, Any]:
        """
        모든 종목 데이터 수집 (병렬)

        Returns:
            수집 결과 통계
        """
        if not self.tasks:
            logger.warning("수집 대상 종목이 없습니다")
            return self.get_stats()

        self.stats['start_time'] = datetime.now()
        logger.info(f"데이터 수집 시작: 총 {len(self.tasks)}개 종목")

        # 세마포어로 동시 실행 제한
        semaphore = asyncio.Semaphore(self.max_concurrent_tasks)

        async def _collect_with_semaphore(task: CollectionTask):
            async with semaphore:
                return await self._collect_single_stock(task)

        # 모든 작업 실행
        results = await asyncio.gather(
            *[_collect_with_semaphore(task) for task in self.tasks],
            return_exceptions=True
        )

        # 실패한 작업 재시도 (한 번만)
        pending_tasks = [task for task in self.tasks if task.status == "pending"]
        if pending_tasks:
            logger.info(f"실패한 작업 재시도: {len(pending_tasks)}개")
            await asyncio.gather(
                *[_collect_with_semaphore(task) for task in pending_tasks],
                return_exceptions=True
            )

        self.stats['end_time'] = datetime.now()

        # 최종 통계
        stats = self.get_stats()
        logger.info("데이터 수집 완료")
        logger.info(f"  성공: {stats['completed_tasks']}개")
        logger.info(f"  실패: {stats['failed_tasks']}개")
        logger.info(f"  총 데이터: {stats['total_data_points']:,}건")
        logger.info(f"  소요 시간: {stats['duration']}")

        return stats

    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 조회"""
        duration = None
        if self.stats['start_time'] and self.stats['end_time']:
            duration = str(self.stats['end_time'] - self.stats['start_time'])

        return {
            'total_tasks': self.stats['total_tasks'],
            'completed_tasks': self.stats['completed_tasks'],
            'failed_tasks': self.stats['failed_tasks'],
            'success_rate': (
                self.stats['completed_tasks'] / self.stats['total_tasks'] * 100
                if self.stats['total_tasks'] > 0 else 0
            ),
            'total_data_points': self.stats['total_data_points'],
            'duration': duration,
            'start_time': self.stats['start_time'].isoformat() if self.stats['start_time'] else None,
            'end_time': self.stats['end_time'].isoformat() if self.stats['end_time'] else None
        }

    def get_task_status(self) -> pd.DataFrame:
        """작업 상태 조회"""
        return pd.DataFrame([asdict(task) for task in self.tasks])

    def load_collected_data(self, stock_code: str, minute_interval: int = 5) -> Optional[pd.DataFrame]:
        """
        수집된 데이터 로드

        Args:
            stock_code: 종목코드
            minute_interval: 분봉 간격

        Returns:
            DataFrame 또는 None
        """
        raw_file = self.data_dir / "raw" / f"{stock_code}_{minute_interval}min.csv"

        if not raw_file.exists():
            logger.warning(f"데이터 파일 없음: {raw_file}")
            return None

        df = pd.read_csv(raw_file)
        df['datetime'] = pd.to_datetime(df['datetime'])

        return df


async def main():
    """테스트 코드"""
    import os
    from dotenv import load_dotenv

    # .env 파일 로드
    load_dotenv()

    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    if not app_key or not app_secret:
        print("환경변수에 KIWOOM_APP_KEY, KIWOOM_APP_SECRET을 설정해주세요")
        return

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 수집 대상 종목
    stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
        {"code": "035720", "name": "카카오"},
    ]

    async with MLDataCollector(app_key, app_secret, is_mock=True) as collector:
        # 종목 추가
        collector.add_stocks_from_list(stocks, minute_interval=5, max_pages=10)

        # 데이터 수집
        stats = await collector.collect_all()

        # 결과 출력
        print("\n=== 수집 통계 ===")
        for key, value in stats.items():
            print(f"{key}: {value}")

        # 작업 상태 출력
        print("\n=== 작업 상태 ===")
        print(collector.get_task_status())


if __name__ == "__main__":
    asyncio.run(main())
