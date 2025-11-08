#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/data_cleaner.py

데이터 정제 모듈 (Raw → Processed)
- 결측치 처리
- 이상치 제거
- 데이터 타입 정규화
- 리샘플링
- 중복 제거
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np
from datetime import datetime

# 로거 설정
logger = logging.getLogger(__name__)


class DataCleaner:
    """데이터 정제 클래스"""

    def __init__(
        self,
        raw_dir: str = "./data/raw",
        processed_dir: str = "./data/processed"
    ):
        """
        Args:
            raw_dir: Raw 데이터 디렉토리
            processed_dir: Processed 데이터 저장 디렉토리
        """
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)

        # 디렉토리 생성
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def clean_minute_data(
        self,
        symbol: str,
        minute_interval: int = 5
    ) -> Optional[pd.DataFrame]:
        """
        분봉 데이터 정제

        Args:
            symbol: 종목코드
            minute_interval: 분봉 간격

        Returns:
            정제된 DataFrame
        """
        # Raw 데이터 로드
        raw_file = self.raw_dir / f"{symbol}_{minute_interval}min.csv"

        if not raw_file.exists():
            logger.warning(f"Raw 데이터 없음: {raw_file}")
            return None

        logger.info(f"[{symbol}] 분봉 데이터 정제 시작")

        df = pd.read_csv(raw_file)
        initial_rows = len(df)

        # 1. 데이터 타입 변환
        df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')

        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 2. 결측치 확인
        missing_before = df.isnull().sum().sum()

        # datetime 결측치가 있으면 제거
        df = df.dropna(subset=['datetime'])

        # OHLCV 결측치 처리 (forward fill)
        df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].fillna(method='ffill')

        # volume 결측치는 0으로
        df['volume'] = df['volume'].fillna(0)

        missing_after = df.isnull().sum().sum()

        # 3. 중복 제거 (같은 시간)
        duplicates = df.duplicated(subset=['datetime'], keep='first').sum()
        df = df.drop_duplicates(subset=['datetime'], keep='first')

        # 4. 이상치 제거
        # - 가격이 0 이하
        # - High < Low
        # - Close가 High/Low 범위 밖
        anomaly_mask = (
            (df['close'] <= 0) |
            (df['high'] < df['low']) |
            (df['close'] > df['high']) |
            (df['close'] < df['low'])
        )
        anomalies = anomaly_mask.sum()
        df = df[~anomaly_mask]

        # 5. 정렬 (시간 순)
        df = df.sort_values('datetime').reset_index(drop=True)

        # 6. OHLC 일관성 검증
        df['high'] = df[['open', 'high', 'close']].max(axis=1)
        df['low'] = df[['open', 'low', 'close']].min(axis=1)

        # 7. 메타데이터 추가
        df['symbol'] = symbol
        df['interval'] = f"{minute_interval}min"

        # 8. 데이터 품질 메트릭
        final_rows = len(df)
        data_quality = {
            'initial_rows': initial_rows,
            'final_rows': final_rows,
            'removed_rows': initial_rows - final_rows,
            'missing_values_before': int(missing_before),
            'missing_values_after': int(missing_after),
            'duplicates_removed': int(duplicates),
            'anomalies_removed': int(anomalies),
            'data_retention_rate': final_rows / initial_rows if initial_rows > 0 else 0
        }

        logger.info(f"[{symbol}] 정제 완료:")
        logger.info(f"  - 원본: {initial_rows}행 → 정제: {final_rows}행")
        logger.info(f"  - 결측치 제거: {missing_before} → {missing_after}")
        logger.info(f"  - 중복 제거: {duplicates}개")
        logger.info(f"  - 이상치 제거: {anomalies}개")
        logger.info(f"  - 유지율: {data_quality['data_retention_rate']:.1%}")

        # 9. Processed 데이터 저장
        processed_file = self.processed_dir / f"{symbol}_{minute_interval}min.parquet"
        df.to_parquet(processed_file, index=False, compression='snappy')

        # 메타데이터 저장
        metadata = {
            'symbol': symbol,
            'interval': f"{minute_interval}min",
            'data_quality': data_quality,
            'date_range': {
                'start': df['datetime'].min().isoformat(),
                'end': df['datetime'].max().isoformat()
            },
            'rows': final_rows,
            'columns': list(df.columns),
            'processed_at': datetime.now().isoformat()
        }

        metadata_file = self.processed_dir / f"{symbol}_{minute_interval}min_meta.json"
        import json
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        logger.info(f"  - 저장: {processed_file}")

        return df

    def clean_daily_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        일봉 데이터 정제

        Args:
            symbol: 종목코드

        Returns:
            정제된 DataFrame
        """
        raw_file = self.raw_dir / f"{symbol}_daily.csv"

        if not raw_file.exists():
            logger.warning(f"Raw 데이터 없음: {raw_file}")
            return None

        logger.info(f"[{symbol}] 일봉 데이터 정제 시작")

        df = pd.read_csv(raw_file)
        initial_rows = len(df)

        # 1. 데이터 타입 변환
        df['date'] = pd.to_datetime(df['date'], errors='coerce')

        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 2. 결측치 처리
        df = df.dropna(subset=['date'])
        df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].fillna(method='ffill')
        df['volume'] = df['volume'].fillna(0)

        # 3. 중복 제거
        df = df.drop_duplicates(subset=['date'], keep='first')

        # 4. 이상치 제거
        anomaly_mask = (
            (df['close'] <= 0) |
            (df['high'] < df['low']) |
            (df['close'] > df['high']) |
            (df['close'] < df['low'])
        )
        df = df[~anomaly_mask]

        # 5. 정렬
        df = df.sort_values('date').reset_index(drop=True)

        # 6. OHLC 일관성
        df['high'] = df[['open', 'high', 'close']].max(axis=1)
        df['low'] = df[['open', 'low', 'close']].min(axis=1)

        # 7. 메타데이터
        df['symbol'] = symbol

        # 8. 저장
        final_rows = len(df)
        processed_file = self.processed_dir / f"{symbol}_daily.parquet"
        df.to_parquet(processed_file, index=False, compression='snappy')

        logger.info(f"[{symbol}] 정제 완료: {initial_rows}행 → {final_rows}행")
        logger.info(f"  - 저장: {processed_file}")

        return df

    def load_processed(
        self,
        symbol: str,
        interval: str = "5min"
    ) -> Optional[pd.DataFrame]:
        """
        정제된 데이터 로드

        Args:
            symbol: 종목코드
            interval: 간격 (5min, daily 등)

        Returns:
            DataFrame 또는 None
        """
        if interval == "daily":
            processed_file = self.processed_dir / f"{symbol}_daily.parquet"
        else:
            processed_file = self.processed_dir / f"{symbol}_{interval}.parquet"

        if not processed_file.exists():
            logger.warning(f"Processed 데이터 없음: {processed_file}")
            return None

        df = pd.read_parquet(processed_file)

        # 날짜 컬럼 파싱
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])

        return df

    def batch_clean(
        self,
        symbols: list,
        interval: str = "5min"
    ) -> Dict[str, bool]:
        """
        여러 종목 일괄 정제

        Args:
            symbols: 종목 리스트
            interval: 간격

        Returns:
            {종목코드: 성공여부}
        """
        results = {}

        logger.info(f"일괄 정제 시작: {len(symbols)}개 종목")

        for symbol in symbols:
            try:
                if interval == "daily":
                    df = self.clean_daily_data(symbol)
                else:
                    minute_interval = int(interval.replace('min', ''))
                    df = self.clean_minute_data(symbol, minute_interval)

                results[symbol] = df is not None

            except Exception as e:
                logger.error(f"[{symbol}] 정제 실패: {e}")
                results[symbol] = False

        success_count = sum(results.values())
        logger.info(f"일괄 정제 완료: {success_count}/{len(symbols)} 성공")

        return results


def main():
    """테스트 코드"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    cleaner = DataCleaner()

    # 테스트 종목
    test_symbols = ["005930", "000660"]

    # 일괄 정제
    results = cleaner.batch_clean(test_symbols, interval="5min")

    print("\n=== 정제 결과 ===")
    for symbol, success in results.items():
        print(f"{symbol}: {'성공' if success else '실패'}")

    # 정제된 데이터 로드 테스트
    df = cleaner.load_processed("005930", "5min")
    if df is not None:
        print(f"\n=== 삼성전자 데이터 샘플 ===")
        print(df.head())
        print(f"\n총 {len(df)}행")


if __name__ == "__main__":
    main()
