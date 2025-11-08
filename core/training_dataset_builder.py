#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/training_dataset_builder.py

ML 학습용 최종 Dataset 생성
- 여러 종목 통합
- Feature Engineering 적용
- Train/Val/Test 분할 (Time-series CV)
- 데이터 버전 관리
"""

import logging
import hashlib
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import pandas as pd
import numpy as np

from ai.feature_engineer import FeatureEngineer

# 로거 설정
logger = logging.getLogger(__name__)


class TrainingDatasetBuilder:
    """학습 데이터셋 빌더"""

    def __init__(
        self,
        labeled_dir: str = "./data/labeled",
        training_dir: str = "./data/training"
    ):
        """
        Args:
            labeled_dir: Label 데이터 디렉토리
            training_dir: 학습 데이터 저장 디렉토리
        """
        self.labeled_dir = Path(labeled_dir)
        self.training_dir = Path(training_dir)

        # 디렉토리 생성
        self.training_dir.mkdir(parents=True, exist_ok=True)

        # Feature Engineer
        self.feature_engineer = FeatureEngineer()

    def load_multiple_stocks(
        self,
        symbols: List[str],
        interval: str = "5min"
    ) -> pd.DataFrame:
        """
        여러 종목 데이터 로드 및 통합

        Args:
            symbols: 종목 리스트
            interval: 간격

        Returns:
            통합된 DataFrame
        """
        dfs = []

        logger.info(f"{len(symbols)}개 종목 데이터 로드 중...")

        for symbol in symbols:
            if interval == "daily":
                labeled_file = self.labeled_dir / f"{symbol}_daily_labeled.parquet"
            else:
                labeled_file = self.labeled_dir / f"{symbol}_{interval}_labeled.parquet"

            if not labeled_file.exists():
                logger.warning(f"[{symbol}] Label 데이터 없음")
                continue

            df = pd.read_parquet(labeled_file)

            # 종목 정보 확인
            if 'symbol' not in df.columns:
                df['symbol'] = symbol

            dfs.append(df)
            logger.info(f"  [{ symbol}] {len(df)}행 로드")

        if not dfs:
            logger.error("로드된 데이터 없음")
            return pd.DataFrame()

        # 통합
        combined_df = pd.concat(dfs, ignore_index=True)

        logger.info(f"통합 완료: {len(combined_df)}행, {len(dfs)}개 종목")

        return combined_df

    def add_features(
        self,
        df: pd.DataFrame,
        price_col: str = 'close'
    ) -> pd.DataFrame:
        """
        Feature Engineering 적용

        Args:
            df: DataFrame
            price_col: 가격 컬럼

        Returns:
            Feature가 추가된 DataFrame
        """
        logger.info("Feature Engineering 시작...")

        df = df.copy()

        # 날짜 컬럼 확인
        time_col = 'datetime' if 'datetime' in df.columns else 'date'

        # 종목별로 Feature 생성
        result_dfs = []

        for symbol in df['symbol'].unique():
            symbol_df = df[df['symbol'] == symbol].copy()

            # Feature 생성
            try:
                symbol_df = self.feature_engineer.add_all_features(symbol_df)
                result_dfs.append(symbol_df)
                logger.info(f"  [{symbol}] Feature 생성 완료")
            except Exception as e:
                logger.error(f"  [{symbol}] Feature 생성 실패: {e}")
                continue

        if not result_dfs:
            logger.error("Feature 생성된 데이터 없음")
            return df

        # 재통합
        df_with_features = pd.concat(result_dfs, ignore_index=True)

        feature_cols = [col for col in df_with_features.columns
                       if col not in df.columns]

        logger.info(f"Feature 생성 완료: {len(feature_cols)}개 Feature 추가")

        return df_with_features

    def split_dataset(
        self,
        df: pd.DataFrame,
        train_ratio: float = 0.7,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        time_col: str = 'datetime'
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Time-series 기반 데이터 분할

        Args:
            df: DataFrame
            train_ratio: 학습 비율
            val_ratio: 검증 비율
            test_ratio: 테스트 비율
            time_col: 시간 컬럼

        Returns:
            (train_df, val_df, test_df)
        """
        logger.info("데이터 분할 시작...")

        df = df.copy()

        # 시간 컬럼 확인
        if time_col not in df.columns:
            time_col = 'date' if 'date' in df.columns else 'datetime'

        # 시간 순 정렬
        df = df.sort_values(time_col)

        # 분할 지점 계산
        n = len(df)
        train_end = int(n * train_ratio)
        val_end = int(n * (train_ratio + val_ratio))

        train_df = df.iloc[:train_end].copy()
        val_df = df.iloc[train_end:val_end].copy()
        test_df = df.iloc[val_end:].copy()

        logger.info(f"데이터 분할 완료:")
        logger.info(f"  - Train: {len(train_df)}행 ({len(train_df)/n*100:.1f}%)")
        logger.info(f"  - Val: {len(val_df)}행 ({len(val_df)/n*100:.1f}%)")
        logger.info(f"  - Test: {len(test_df)}행 ({len(test_df)/n*100:.1f}%)")

        return train_df, val_df, test_df

    def dataset_hash(self, df: pd.DataFrame) -> str:
        """
        데이터셋 해시 생성

        Args:
            df: DataFrame

        Returns:
            해시 문자열
        """
        return hashlib.sha256(
            pd.util.hash_pandas_object(df, index=True).values
        ).hexdigest()[:12]

    def build_training_dataset(
        self,
        symbols: List[str],
        interval: str = "5min",
        model_name: str = "lightgbm_v1",
        add_features: bool = True,
        train_ratio: float = 0.7,
        val_ratio: float = 0.15
    ) -> Dict[str, Any]:
        """
        최종 학습 데이터셋 생성

        Args:
            symbols: 종목 리스트
            interval: 간격
            model_name: 모델 이름
            add_features: Feature 추가 여부
            train_ratio: 학습 비율
            val_ratio: 검증 비율

        Returns:
            메타데이터
        """
        logger.info("=" * 80)
        logger.info(f"Training Dataset 생성: {model_name}")
        logger.info("=" * 80)

        # 1. 여러 종목 데이터 로드
        df = self.load_multiple_stocks(symbols, interval)

        if df.empty:
            logger.error("데이터 없음")
            return {}

        # 2. Feature Engineering
        if add_features:
            df = self.add_features(df)

        # 3. 결측치 제거 (Feature 생성 후)
        initial_rows = len(df)
        df = df.dropna()
        final_rows = len(df)

        logger.info(f"결측치 제거: {initial_rows}행 → {final_rows}행")

        # 4. 데이터 분할
        train_df, val_df, test_df = self.split_dataset(
            df,
            train_ratio=train_ratio,
            val_ratio=val_ratio
        )

        # 5. 저장
        model_dir = self.training_dir / model_name
        model_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        train_file = model_dir / f"{timestamp}_train.parquet"
        val_file = model_dir / f"{timestamp}_val.parquet"
        test_file = model_dir / f"{timestamp}_test.parquet"

        train_df.to_parquet(train_file, index=False, compression='snappy')
        val_df.to_parquet(val_file, index=False, compression='snappy')
        test_df.to_parquet(test_file, index=False, compression='snappy')

        logger.info(f"저장 완료:")
        logger.info(f"  - Train: {train_file}")
        logger.info(f"  - Val: {val_file}")
        logger.info(f"  - Test: {test_file}")

        # 6. 메타데이터 생성
        time_col = 'datetime' if 'datetime' in df.columns else 'date'

        metadata = {
            'version': timestamp,
            'model_name': model_name,
            'symbols': symbols,
            'interval': interval,
            'num_symbols': len(symbols),
            'train': {
                'rows': len(train_df),
                'file': str(train_file.name),
                'hash': self.dataset_hash(train_df),
                'date_range': {
                    'start': train_df[time_col].min().isoformat(),
                    'end': train_df[time_col].max().isoformat()
                }
            },
            'val': {
                'rows': len(val_df),
                'file': str(val_file.name),
                'hash': self.dataset_hash(val_df),
                'date_range': {
                    'start': val_df[time_col].min().isoformat(),
                    'end': val_df[time_col].max().isoformat()
                }
            },
            'test': {
                'rows': len(test_df),
                'file': str(test_file.name),
                'hash': self.dataset_hash(test_df),
                'date_range': {
                    'start': test_df[time_col].min().isoformat(),
                    'end': test_df[time_col].max().isoformat()
                }
            },
            'features': {
                'total': len(df.columns),
                'feature_columns': [col for col in df.columns
                                   if col not in ['symbol', time_col]],
                'added_features': add_features
            },
            'created_at': datetime.now().isoformat()
        }

        metadata_file = model_dir / f"{timestamp}_metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        logger.info(f"메타데이터 저장: {metadata_file}")

        logger.info("=" * 80)
        logger.info("Training Dataset 생성 완료")
        logger.info("=" * 80)

        return metadata


def main():
    """테스트 코드"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    builder = TrainingDatasetBuilder()

    # 테스트 종목
    test_symbols = ["005930", "000660"]

    # Dataset 생성
    metadata = builder.build_training_dataset(
        symbols=test_symbols,
        interval="5min",
        model_name="test_model_v1",
        add_features=True,
        train_ratio=0.7,
        val_ratio=0.15
    )

    if metadata:
        print("\n=== Dataset 메타데이터 ===")
        print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
