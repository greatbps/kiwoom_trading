#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 모델 학습 테스트 스크립트
"""
import sys
import os

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai.feature_engineer import FeatureEngineer, generate_sample_data
from ai.ml_model_trainer import MLModelTrainer
import pandas as pd
import numpy as np
import logging

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

def create_training_data(days=200):
    """학습용 데이터 생성 (Features + Target)"""

    # 1. 가격 데이터 생성
    price_data = generate_sample_data(days=days)

    # 2. Feature Engineering
    feature_engineer = FeatureEngineer()
    feature_set = feature_engineer.extract_features(price_data, symbol='005930')

    # 3. FeatureSet을 DataFrame으로 변환
    feature_dict = feature_set.to_dict()

    # 4. 각 시점에 대한 features를 수집 (여기서는 마지막 시점만 사용하므로 샘플 데이터 생성)
    # 실제로는 여러 시점의 데이터를 모아야 하지만, 테스트용으로 간단히 구현
    features_list = []
    for i in range(min(len(price_data), days)):
        try:
            # 각 시점까지의 데이터로 feature 추출
            df_slice = price_data.iloc[:i+20].copy()  # 최소 20일 필요
            if len(df_slice) < 20:
                continue

            fs = feature_engineer.extract_features(df_slice, symbol='005930')
            fd = fs.to_dict()

            # target 생성: 다음날 가격이 오르면 1, 내리면 0
            if i < len(price_data) - 1:
                future_price = price_data.iloc[i + 1]['close']
                current_price = price_data.iloc[i]['close']
                fd['target'] = 1 if future_price > current_price else 0
                fd['future_return'] = (future_price - current_price) / current_price
            else:
                fd['target'] = 0
                fd['future_return'] = 0.0

            features_list.append(fd)
        except Exception as e:
            logger.debug(f"Skipping index {i}: {e}")
            continue

    # DataFrame으로 변환
    df = pd.DataFrame(features_list)

    # 불필요한 컬럼 제거
    cols_to_drop = ['symbol', 'timestamp']
    df = df.drop(columns=[c for c in cols_to_drop if c in df.columns])

    return df

def main():
    """ML 모델 학습 테스트"""

    logger.info("=" * 80)
    logger.info("🤖 ML 모델 학습 시작")
    logger.info("=" * 80)

    try:
        # 1. 학습 데이터 준비
        logger.info("\n1️⃣ 학습 데이터 준비 중...")
        df = create_training_data(days=200)
        logger.info(f"✅ 데이터 준비 완료: {len(df)} 샘플")
        logger.info(f"   Feature 수: {len(df.columns) - 2} 개 (target, future_return 제외)")
        logger.info(f"   컬럼: {list(df.columns[:10])}...")

        # Target 분포 확인
        target_dist = df['target'].value_counts()
        logger.info(f"   Target 분포: {dict(target_dist)}")

        # 2. 모델 학습
        logger.info("\n2️⃣ 모델 학습 중...")
        logger.info("   모델 타입: LightGBM")

        trainer = MLModelTrainer(model_type='lightgbm')
        model, metrics = trainer.train(
            df=df,
            target_column='target',
            test_size=0.2
        )

        # 3. 결과 출력
        logger.info("\n" + "=" * 80)
        logger.info("📈 학습 완료!")
        logger.info("=" * 80)
        logger.info(f"✅ Accuracy: {metrics.accuracy:.1%}")
        logger.info(f"✅ Precision: {metrics.precision:.1%}")
        logger.info(f"✅ Recall: {metrics.recall:.1%}")
        logger.info(f"✅ F1 Score: {metrics.f1_score:.3f}")
        logger.info(f"✅ ROC AUC: {metrics.roc_auc:.3f}")
        logger.info(f"✅ Win Rate: {metrics.win_rate:.1%}")
        logger.info(f"✅ Average Profit: {metrics.avg_profit:.2%}")
        logger.info(f"✅ Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
        logger.info("=" * 80)
        logger.info(f"✅ Train Samples: {metrics.train_samples}")
        logger.info(f"✅ Test Samples: {metrics.test_samples}")
        logger.info(f"✅ Feature Count: {metrics.feature_count}")
        logger.info(f"✅ Training Time: {metrics.training_time:.2f}s")

        # 4. 모델 저장
        logger.info("\n3️⃣ 모델 저장 중...")
        model_path = trainer.save_model(model, metrics)
        logger.info(f"✅ 모델 저장 완료: {model_path}")

        logger.info("\n" + "=" * 80)
        logger.info("🎉 모든 작업이 완료되었습니다!")
        logger.info("=" * 80)

        return 0

    except Exception as e:
        logger.error(f"\n❌ 학습 중 오류 발생: {e}", exc_info=True)
        return 1

if __name__ == '__main__':
    sys.exit(main())
