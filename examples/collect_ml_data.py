#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
examples/collect_ml_data.py

ML 학습용 데이터 수집 예제 스크립트
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from core.ml_data_collector import MLDataCollector


async def main():
    """메인 함수"""
    # .env 파일 로드
    load_dotenv()

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('logs/ml_data_collection.log', encoding='utf-8')
        ]
    )

    # API 키 확인
    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    if not app_key or not app_secret:
        print("\n=== 키움 API 설정 필요 ===")
        print(".env 파일에 다음 항목을 추가해주세요:")
        print("KIWOOM_APP_KEY=your_app_key")
        print("KIWOOM_APP_SECRET=your_app_secret")
        print("\n키움 OpenAPI 사이트에서 발급받을 수 있습니다:")
        print("https://openapi.kiwoom.com")
        return

    # 수집 대상 종목 (KOSPI 대형주)
    stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
        {"code": "035720", "name": "카카오"},
        {"code": "005380", "name": "현대차"},
        {"code": "051910", "name": "LG화학"},
        {"code": "006400", "name": "삼성SDI"},
        {"code": "035420", "name": "NAVER"},
        {"code": "207940", "name": "삼성바이오로직스"},
        {"code": "068270", "name": "셀트리온"},
        {"code": "028260", "name": "삼성물산"},
    ]

    print("\n=== ML 학습용 데이터 수집 시작 ===")
    print(f"수집 종목 수: {len(stocks)}개")
    print(f"분봉 간격: 5분")
    print(f"모의투자 모드: True")
    print()

    # 데이터 수집기 생성
    async with MLDataCollector(
        app_key=app_key,
        app_secret=app_secret,
        is_mock=True,  # 모의투자 모드
        data_dir="./data/ml_training",
        max_concurrent_tasks=2  # 동시 2개씩 수집
    ) as collector:

        # 종목 추가
        collector.add_stocks_from_list(
            stock_list=stocks,
            minute_interval=5,
            max_pages=50  # 각 종목당 최대 50페이지
        )

        # 데이터 수집 실행
        stats = await collector.collect_all()

        # 결과 출력
        print("\n=== 수집 완료 ===")
        print(f"총 작업: {stats['total_tasks']}개")
        print(f"성공: {stats['completed_tasks']}개")
        print(f"실패: {stats['failed_tasks']}개")
        print(f"성공률: {stats['success_rate']:.1f}%")
        print(f"총 데이터 포인트: {stats['total_data_points']:,}건")
        print(f"소요 시간: {stats['duration']}")
        print()

        # 작업 상태 출력
        print("=== 종목별 수집 현황 ===")
        status_df = collector.get_task_status()
        print(status_df[['stock_name', 'status', 'collected_count', 'error_message']])
        print()

        # 데이터 저장 위치
        print(f"데이터 저장 위치: {collector.data_dir.absolute()}")
        print(f"  - 원본 데이터: {collector.data_dir / 'raw'}")
        print(f"  - 메타데이터: {collector.data_dir / 'metadata'}")


if __name__ == "__main__":
    # logs 디렉토리 생성
    Path("logs").mkdir(exist_ok=True)

    # 실행
    asyncio.run(main())
