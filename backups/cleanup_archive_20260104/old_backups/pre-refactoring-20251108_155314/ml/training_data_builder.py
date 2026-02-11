#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ml/training_data_builder.py

백테스트 결과를 활용한 Ranker 학습 데이터 생성

백테스트 결과에서:
- Features: 조건검색 시점의 종목 특성
- Labels: 실제 거래 결과 (수익률, 수익 여부)
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class TrainingDataBuilder:
    """백테스트 결과로 Ranker 학습 데이터 생성"""

    def __init__(
        self,
        backtest_results_dir: str = "./backtest_results",
        output_dir: str = "./ml/data"
    ):
        """
        Args:
            backtest_results_dir: 백테스트 결과 디렉토리
            output_dir: 학습 데이터 저장 디렉토리
        """
        self.backtest_dir = Path(backtest_results_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def load_backtest_results(self, date_range: Optional[int] = 30) -> List[Dict]:
        """
        백테스트 결과 파일 로드

        Args:
            date_range: 최근 N일 결과만 사용 (None이면 전체)

        Returns:
            백테스트 결과 리스트
        """
        results = []

        if not self.backtest_dir.exists():
            logger.warning(f"백테스트 결과 디렉토리 없음: {self.backtest_dir}")
            return results

        # JSON 파일 검색
        json_files = list(self.backtest_dir.glob("backtest_*.json"))

        if date_range is not None:
            cutoff_date = datetime.now() - timedelta(days=date_range)
            json_files = [
                f for f in json_files
                if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff_date
            ]

        logger.info(f"백테스트 결과 파일: {len(json_files)}개")

        for filepath in json_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    results.append(data)
            except Exception as e:
                logger.error(f"파일 로드 실패 {filepath}: {e}")

        return results

    def extract_features_from_backtest(
        self,
        backtest_data: Dict
    ) -> pd.DataFrame:
        """
        백테스트 결과에서 Feature 추출

        Args:
            backtest_data: 백테스트 결과 (JSON)

        Returns:
            Feature DataFrame
        """
        trades = backtest_data.get('trades', [])

        if not trades:
            return pd.DataFrame()

        records = []

        for trade in trades:
            # 기본 정보
            symbol = trade.get('symbol', '')
            entry_date = trade.get('entry_date', '')
            exit_date = trade.get('exit_date', '')

            # 수익률
            profit_pct = trade.get('profit_pct', 0.0)
            is_profitable = profit_pct > 0

            # Feature 추출 (백테스트 시점의 상태)
            # 실제로는 backtest_data에 조건검색 시점의 데이터가 포함되어야 함
            features = trade.get('entry_features', {})

            record = {
                'symbol': symbol,
                'entry_date': entry_date,
                'exit_date': exit_date,
                'actual_profit_pct': profit_pct,
                'is_profitable': int(is_profitable),

                # Features (백테스트 결과에서 추출)
                'vwap_backtest_winrate': features.get('vwap_backtest_winrate', 0.0),
                'vwap_avg_profit': features.get('vwap_avg_profit', 0.0),
                'current_vwap_distance': features.get('current_vwap_distance', 0.0),
                'volume_z_score': features.get('volume_z_score', 0.0),
                'recent_return_5d': features.get('recent_return_5d', 0.0),
                'market_volatility': features.get('market_volatility', 15.0),
                'sector_strength': features.get('sector_strength', 0.0),
                'price_momentum': features.get('price_momentum', 0.0),
            }

            records.append(record)

        df = pd.DataFrame(records)
        return df

    def build_training_dataset(
        self,
        date_range: int = 30,
        min_samples: int = 100
    ) -> Optional[pd.DataFrame]:
        """
        학습 데이터셋 생성

        Args:
            date_range: 최근 N일 백테스트 결과 사용
            min_samples: 최소 샘플 수

        Returns:
            학습용 DataFrame
        """
        logger.info("백테스트 결과 로드 중...")
        backtest_results = self.load_backtest_results(date_range)

        if not backtest_results:
            logger.warning("백테스트 결과가 없습니다")
            return None

        # 모든 백테스트 결과 통합
        all_data = []

        for bt_data in backtest_results:
            df = self.extract_features_from_backtest(bt_data)
            if not df.empty:
                all_data.append(df)

        if not all_data:
            logger.warning("추출된 데이터가 없습니다")
            return None

        # 통합
        dataset = pd.concat(all_data, ignore_index=True)

        # 중복 제거 (같은 종목, 같은 날짜)
        dataset = dataset.drop_duplicates(subset=['symbol', 'entry_date'])

        logger.info(f"학습 데이터: {len(dataset)}개 샘플")

        if len(dataset) < min_samples:
            logger.warning(f"샘플 수 부족: {len(dataset)} < {min_samples}")
            return None

        # 데이터 저장
        output_path = self.output_dir / f"training_data_{datetime.now().strftime('%Y%m%d')}.csv"
        dataset.to_csv(output_path, index=False, encoding='utf-8')
        logger.info(f"학습 데이터 저장: {output_path}")

        return dataset

    def generate_synthetic_data(
        self,
        n_samples: int = 500,
        seed: int = 42
    ) -> pd.DataFrame:
        """
        합성 데이터 생성 (백테스트 결과 없을 때 테스트용)

        Args:
            n_samples: 샘플 수
            seed: Random seed

        Returns:
            합성 데이터 DataFrame
        """
        np.random.seed(seed)

        # Feature 생성
        data = {
            'symbol': [f"{i:06d}" for i in range(n_samples)],
            'entry_date': [
                (datetime.now() - timedelta(days=np.random.randint(1, 60))).strftime('%Y-%m-%d')
                for _ in range(n_samples)
            ],
            'vwap_backtest_winrate': np.random.uniform(0.3, 0.8, n_samples),
            'vwap_avg_profit': np.random.uniform(-2, 5, n_samples),
            'current_vwap_distance': np.random.uniform(-5, 5, n_samples),
            'volume_z_score': np.random.uniform(-2, 4, n_samples),
            'recent_return_5d': np.random.uniform(-10, 10, n_samples),
            'market_volatility': np.random.uniform(10, 30, n_samples),
            'sector_strength': np.random.uniform(-5, 5, n_samples),
            'price_momentum': np.random.uniform(-3, 3, n_samples),
        }

        df = pd.DataFrame(data)

        # Target 생성 (Feature 기반 노이즈 추가)
        profit = (
            df['vwap_avg_profit'] * 0.5 +
            df['volume_z_score'] * 0.3 +
            df['price_momentum'] * 0.2 +
            np.random.normal(0, 2, n_samples)
        )

        df['actual_profit_pct'] = profit
        df['is_profitable'] = (profit > 0).astype(int)

        logger.info(f"합성 데이터 생성: {len(df)}개")

        return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    builder = TrainingDataBuilder()

    # 백테스트 결과가 있으면 로드, 없으면 합성 데이터
    dataset = builder.build_training_dataset(date_range=30)

    if dataset is None:
        logger.info("백테스트 결과 없음 → 합성 데이터 생성")
        dataset = builder.generate_synthetic_data(n_samples=500)

    print(f"\n학습 데이터 요약:")
    print(dataset.info())
    print(f"\n수익 통계:")
    print(dataset['actual_profit_pct'].describe())
    print(f"\n승률: {dataset['is_profitable'].mean() * 100:.1f}%")
