#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/label_generator.py

ML 학습용 Label 생성 모듈
- n봉 후 수익률 계산
- Binary/Multi-class Classification Label
- Regression Label (수익률 %)
- 클래스 불균형 분석
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import pandas as pd
import numpy as np
from datetime import datetime

# 로거 설정
logger = logging.getLogger(__name__)


class LabelGenerator:
    """Label 생성 클래스"""

    def __init__(
        self,
        processed_dir: str = "./data/processed",
        labeled_dir: str = "./data/labeled"
    ):
        """
        Args:
            processed_dir: Processed 데이터 디렉토리
            labeled_dir: Label 추가된 데이터 저장 디렉토리
        """
        self.processed_dir = Path(processed_dir)
        self.labeled_dir = Path(labeled_dir)

        # 디렉토리 생성
        self.labeled_dir.mkdir(parents=True, exist_ok=True)

    def calculate_forward_returns(
        self,
        df: pd.DataFrame,
        horizons: list = [3, 5, 10, 15],
        price_col: str = 'close'
    ) -> pd.DataFrame:
        """
        미래 수익률 계산 (n봉 후)

        Args:
            df: DataFrame (정제된 데이터)
            horizons: 예측 수평 (봉 수)
            price_col: 가격 컬럼명

        Returns:
            수익률 컬럼이 추가된 DataFrame
        """
        df = df.copy()

        for n in horizons:
            # n봉 후 가격
            future_price = df[price_col].shift(-n)

            # 수익률 계산 (%)
            df[f'return_{n}bars'] = ((future_price - df[price_col]) / df[price_col] * 100)

        return df

    def generate_classification_labels(
        self,
        df: pd.DataFrame,
        horizon: int = 5,
        profit_threshold: float = 2.0,
        loss_threshold: float = -2.0,
        label_type: str = 'ternary'
    ) -> pd.DataFrame:
        """
        Classification Label 생성

        Args:
            df: DataFrame
            horizon: 예측 수평 (봉 수)
            profit_threshold: 익절 기준 (%)
            loss_threshold: 손절 기준 (%)
            label_type: 'binary', 'ternary', 'multi'

        Returns:
            Label 컬럼이 추가된 DataFrame
        """
        df = df.copy()

        # 수익률 컬럼 확인
        return_col = f'return_{horizon}bars'
        if return_col not in df.columns:
            df = self.calculate_forward_returns(df, [horizon])

        returns = df[return_col]

        if label_type == 'binary':
            # Binary: 상승(1) vs 하락(0)
            df[f'label_{horizon}bars_binary'] = (returns > 0).astype(int)

        elif label_type == 'ternary':
            # Ternary: 익절(+1), 보합(0), 손절(-1)
            df[f'label_{horizon}bars_ternary'] = pd.cut(
                returns,
                bins=[-np.inf, loss_threshold, profit_threshold, np.inf],
                labels=[-1, 0, 1]
            ).astype(float)

        elif label_type == 'multi':
            # Multi-class: 5단계
            # -2: 큰 손실 (<-5%)
            # -1: 작은 손실 (-5% ~ -2%)
            #  0: 보합 (-2% ~ +2%)
            # +1: 작은 이익 (+2% ~ +5%)
            # +2: 큰 이익 (>+5%)
            df[f'label_{horizon}bars_multi'] = pd.cut(
                returns,
                bins=[-np.inf, -5, loss_threshold, profit_threshold, 5, np.inf],
                labels=[-2, -1, 0, 1, 2]
            ).astype(float)

        return df

    def generate_regression_labels(
        self,
        df: pd.DataFrame,
        horizons: list = [3, 5, 10, 15]
    ) -> pd.DataFrame:
        """
        Regression Label 생성 (수익률 그대로 사용)

        Args:
            df: DataFrame
            horizons: 예측 수평 리스트

        Returns:
            Label 컬럼이 추가된 DataFrame
        """
        df = df.copy()

        # 수익률 계산 (이미 있으면 스킵)
        existing_returns = [col for col in df.columns if col.startswith('return_')]

        if not existing_returns:
            df = self.calculate_forward_returns(df, horizons)

        # Regression target으로 사용
        for n in horizons:
            return_col = f'return_{n}bars'
            if return_col in df.columns:
                df[f'target_{n}bars_regression'] = df[return_col]

        return df

    def analyze_label_distribution(
        self,
        df: pd.DataFrame,
        label_col: str
    ) -> Dict[str, Any]:
        """
        Label 분포 분석

        Args:
            df: DataFrame
            label_col: Label 컬럼명

        Returns:
            분포 통계
        """
        labels = df[label_col].dropna()

        if labels.empty:
            return {}

        value_counts = labels.value_counts().sort_index()
        proportions = labels.value_counts(normalize=True).sort_index()

        # 불균형 비율 계산
        max_class_ratio = proportions.max() / proportions.min() if len(proportions) > 1 else 1.0

        stats = {
            'label_column': label_col,
            'total_samples': len(labels),
            'num_classes': len(value_counts),
            'value_counts': value_counts.to_dict(),
            'proportions': proportions.to_dict(),
            'class_imbalance_ratio': float(max_class_ratio),
            'most_common_class': int(value_counts.idxmax()),
            'least_common_class': int(value_counts.idxmin())
        }

        return stats

    def create_labeled_dataset(
        self,
        symbol: str,
        interval: str = "5min",
        horizons: list = [3, 5, 10, 15],
        profit_threshold: float = 2.0,
        loss_threshold: float = -2.0,
        label_types: list = ['ternary', 'binary']
    ) -> Optional[pd.DataFrame]:
        """
        Label이 추가된 데이터셋 생성

        Args:
            symbol: 종목코드
            interval: 간격
            horizons: 예측 수평 리스트
            profit_threshold: 익절 기준
            loss_threshold: 손절 기준
            label_types: 생성할 Label 타입 리스트

        Returns:
            Label이 추가된 DataFrame
        """
        logger.info(f"[{symbol}] Label 생성 시작")

        # Processed 데이터 로드
        if interval == "daily":
            processed_file = self.processed_dir / f"{symbol}_daily.parquet"
        else:
            processed_file = self.processed_dir / f"{symbol}_{interval}.parquet"

        if not processed_file.exists():
            logger.warning(f"Processed 데이터 없음: {processed_file}")
            return None

        df = pd.read_parquet(processed_file)
        initial_rows = len(df)

        # 1. 수익률 계산
        df = self.calculate_forward_returns(df, horizons)

        # 2. Classification Labels 생성
        for label_type in label_types:
            for horizon in horizons:
                df = self.generate_classification_labels(
                    df,
                    horizon=horizon,
                    profit_threshold=profit_threshold,
                    loss_threshold=loss_threshold,
                    label_type=label_type
                )

        # 3. Regression Labels 생성
        df = self.generate_regression_labels(df, horizons)

        # 4. 미래 데이터가 없는 마지막 N행 제거
        max_horizon = max(horizons)
        df = df.iloc[:-max_horizon]

        final_rows = len(df)

        logger.info(f"[{symbol}] Label 생성 완료:")
        logger.info(f"  - 원본: {initial_rows}행 → Label: {final_rows}행")
        logger.info(f"  - 제거: {initial_rows - final_rows}행 (미래 데이터 없음)")

        # 5. Label 분포 분석
        for label_type in label_types:
            for horizon in horizons:
                label_col = f'label_{horizon}bars_{label_type}'
                if label_col in df.columns:
                    stats = self.analyze_label_distribution(df, label_col)

                    logger.info(f"  - {label_col}:")
                    logger.info(f"      클래스 수: {stats['num_classes']}")
                    logger.info(f"      분포: {stats['proportions']}")
                    logger.info(f"      불균형 비율: {stats['class_imbalance_ratio']:.2f}x")

        # 6. 저장
        if interval == "daily":
            labeled_file = self.labeled_dir / f"{symbol}_daily_labeled.parquet"
        else:
            labeled_file = self.labeled_dir / f"{symbol}_{interval}_labeled.parquet"

        df.to_parquet(labeled_file, index=False, compression='snappy')
        logger.info(f"  - 저장: {labeled_file}")

        # 7. 메타데이터 저장
        metadata = {
            'symbol': symbol,
            'interval': interval,
            'rows': final_rows,
            'horizons': horizons,
            'profit_threshold': profit_threshold,
            'loss_threshold': loss_threshold,
            'label_types': label_types,
            'columns': list(df.columns),
            'created_at': datetime.now().isoformat()
        }

        metadata_file = self.labeled_dir / f"{symbol}_{interval}_labeled_meta.json"
        import json
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        return df

    def load_labeled_dataset(
        self,
        symbol: str,
        interval: str = "5min"
    ) -> Optional[pd.DataFrame]:
        """
        Label이 추가된 데이터셋 로드

        Args:
            symbol: 종목코드
            interval: 간격

        Returns:
            DataFrame 또는 None
        """
        if interval == "daily":
            labeled_file = self.labeled_dir / f"{symbol}_daily_labeled.parquet"
        else:
            labeled_file = self.labeled_dir / f"{symbol}_{interval}_labeled.parquet"

        if not labeled_file.exists():
            logger.warning(f"Label 데이터 없음: {labeled_file}")
            return None

        df = pd.read_parquet(labeled_file)

        # 날짜 컬럼 파싱
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])

        return df

    def batch_generate_labels(
        self,
        symbols: list,
        interval: str = "5min",
        **kwargs
    ) -> Dict[str, bool]:
        """
        여러 종목 일괄 Label 생성

        Args:
            symbols: 종목 리스트
            interval: 간격
            **kwargs: create_labeled_dataset 추가 인자

        Returns:
            {종목코드: 성공여부}
        """
        results = {}

        logger.info(f"일괄 Label 생성 시작: {len(symbols)}개 종목")

        for symbol in symbols:
            try:
                df = self.create_labeled_dataset(
                    symbol=symbol,
                    interval=interval,
                    **kwargs
                )
                results[symbol] = df is not None

            except Exception as e:
                logger.error(f"[{symbol}] Label 생성 실패: {e}")
                results[symbol] = False

        success_count = sum(results.values())
        logger.info(f"일괄 Label 생성 완료: {success_count}/{len(symbols)} 성공")

        return results


def main():
    """테스트 코드"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    generator = LabelGenerator()

    # 테스트 종목
    test_symbols = ["005930", "000660"]

    # 일괄 Label 생성
    results = generator.batch_generate_labels(
        symbols=test_symbols,
        interval="5min",
        horizons=[3, 5, 10],
        profit_threshold=2.0,
        loss_threshold=-2.0,
        label_types=['ternary', 'binary']
    )

    print("\n=== Label 생성 결과 ===")
    for symbol, success in results.items():
        print(f"{symbol}: {'성공' if success else '실패'}")

    # Label 데이터 로드 테스트
    df = generator.load_labeled_dataset("005930", "5min")
    if df is not None:
        print(f"\n=== 삼성전자 Label 데이터 샘플 ===")
        label_cols = [col for col in df.columns if 'label' in col or 'return' in col]
        print(df[['datetime', 'close'] + label_cols[:5]].head(10))
        print(f"\n총 {len(df)}행, {len(label_cols)}개 Label")


if __name__ == "__main__":
    main()
