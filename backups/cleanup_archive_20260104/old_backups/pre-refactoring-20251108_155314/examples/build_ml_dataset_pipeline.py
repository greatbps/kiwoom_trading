#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
examples/build_ml_dataset_pipeline.py

ML 학습용 데이터셋 생성 전체 파이프라인
RAW → Processed → Labeled → Training Dataset
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 경로 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from core.ml_data_collector import MLDataCollector
from core.data_cleaner import DataCleaner
from core.label_generator import LabelGenerator
from core.training_dataset_builder import TrainingDatasetBuilder


async def main():
    """
    전체 파이프라인 실행

    1. RAW 데이터 수집 (키움 API)
    2. Processed 데이터 생성 (정제)
    3. Label 생성 (n봉 후 수익률)
    4. Training Dataset 생성 (Feature + 통합)
    """
    # .env 파일 로드
    load_dotenv()

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('logs/ml_dataset_pipeline.log', encoding='utf-8')
        ]
    )

    logger = logging.getLogger(__name__)

    # API 키 확인
    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    if not app_key or not app_secret:
        print("\n=== 키움 API 설정 필요 ===")
        print(".env 파일에 다음 항목을 추가해주세요:")
        print("KIWOOM_APP_KEY=your_app_key")
        print("KIWOOM_APP_SECRET=your_app_secret")
        return

    # ========================================
    # 설정
    # ========================================
    # 학습 대상 종목 (Core Universe 후보)
    target_stocks = [
        {"code": "005930", "name": "삼성전자"},
        {"code": "000660", "name": "SK하이닉스"},
        {"code": "035720", "name": "카카오"},
        {"code": "035420", "name": "NAVER"},
        {"code": "005380", "name": "현대차"},
    ]

    # 파라미터
    minute_interval = 5  # 5분봉
    max_pages = 50  # 각 종목당 최대 50페이지 (약 2500개 데이터)

    # Label 설정
    horizons = [3, 5, 10, 15]  # 3봉, 5봉, 10봉, 15봉 후
    profit_threshold = 2.0  # 익절 기준 +2%
    loss_threshold = -2.0  # 손절 기준 -2%

    # Train/Val/Test 분할
    train_ratio = 0.7
    val_ratio = 0.15

    # 모델 이름
    model_name = f"ml_model_{datetime.now().strftime('%Y%m%d')}"

    print("\n" + "=" * 80)
    print("ML 학습용 데이터셋 생성 파이프라인")
    print("=" * 80)
    print(f"대상 종목: {len(target_stocks)}개")
    print(f"분봉 간격: {minute_interval}분")
    print(f"예측 수평: {horizons}")
    print(f"모델 이름: {model_name}")
    print("=" * 80)
    print()

    # ========================================
    # Step 1: RAW 데이터 수집
    # ========================================
    print("\n" + "=" * 80)
    print("Step 1: RAW 데이터 수집 (키움 API)")
    print("=" * 80)

    async with MLDataCollector(
        app_key=app_key,
        app_secret=app_secret,
        data_dir="./data/raw",
        max_concurrent_tasks=2
    ) as collector:

        # 종목 추가
        collector.add_stocks_from_list(
            stock_list=target_stocks,
            minute_interval=minute_interval,
            max_pages=max_pages
        )

        # 데이터 수집
        stats = await collector.collect_all()

        print(f"\n✓ 데이터 수집 완료: {stats['completed_tasks']}/{stats['total_tasks']} 성공")
        print(f"  총 데이터: {stats['total_data_points']:,}개")

    # 수집 성공한 종목만 추출
    collected_symbols = [stock['code'] for stock in target_stocks]

    # ========================================
    # Step 2: Processed 데이터 생성 (정제)
    # ========================================
    print("\n" + "=" * 80)
    print("Step 2: Processed 데이터 생성 (정제)")
    print("=" * 80)

    cleaner = DataCleaner(
        raw_dir="./data/raw",
        processed_dir="./data/processed"
    )

    clean_results = cleaner.batch_clean(
        symbols=collected_symbols,
        interval=f"{minute_interval}min"
    )

    success_symbols = [s for s, success in clean_results.items() if success]
    print(f"\n✓ 데이터 정제 완료: {len(success_symbols)}/{len(collected_symbols)} 성공")

    # ========================================
    # Step 3: Label 생성
    # ========================================
    print("\n" + "=" * 80)
    print("Step 3: Label 생성 (n봉 후 수익률)")
    print("=" * 80)

    label_gen = LabelGenerator(
        processed_dir="./data/processed",
        labeled_dir="./data/labeled"
    )

    label_results = label_gen.batch_generate_labels(
        symbols=success_symbols,
        interval=f"{minute_interval}min",
        horizons=horizons,
        profit_threshold=profit_threshold,
        loss_threshold=loss_threshold,
        label_types=['ternary', 'binary']
    )

    labeled_symbols = [s for s, success in label_results.items() if success]
    print(f"\n✓ Label 생성 완료: {len(labeled_symbols)}/{len(success_symbols)} 성공")

    # ========================================
    # Step 4: Training Dataset 생성
    # ========================================
    print("\n" + "=" * 80)
    print("Step 4: Training Dataset 생성 (Feature + 통합)")
    print("=" * 80)

    builder = TrainingDatasetBuilder(
        labeled_dir="./data/labeled",
        training_dir="./data/training"
    )

    metadata = builder.build_training_dataset(
        symbols=labeled_symbols,
        interval=f"{minute_interval}min",
        model_name=model_name,
        add_features=True,  # Feature Engineering 적용
        train_ratio=train_ratio,
        val_ratio=val_ratio
    )

    if metadata:
        print(f"\n✓ Training Dataset 생성 완료")
        print(f"  - Train: {metadata['train']['rows']:,}행")
        print(f"  - Val: {metadata['val']['rows']:,}행")
        print(f"  - Test: {metadata['test']['rows']:,}행")
        print(f"  - Features: {metadata['features']['total']}개")
        print(f"  - 저장 위치: ./data/training/{model_name}/")

    # ========================================
    # 완료
    # ========================================
    print("\n" + "=" * 80)
    print("✓ 전체 파이프라인 완료!")
    print("=" * 80)
    print(f"\n다음 단계:")
    print(f"1. 데이터 확인: ./data/training/{model_name}/")
    print(f"2. 모델 학습: python ai/ml_model_trainer.py")
    print(f"3. 모델 평가: python ai/model_evaluator.py")
    print()


if __name__ == "__main__":
    # logs 디렉토리 생성
    Path("logs").mkdir(exist_ok=True)

    # 실행
    asyncio.run(main())
