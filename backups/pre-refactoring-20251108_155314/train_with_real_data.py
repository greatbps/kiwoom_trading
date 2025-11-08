#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
실제 키움 API 데이터로 ML 모델 학습
"""
import sys
import os

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kiwoom_api import KiwoomAPI
from ai.feature_engineer import FeatureEngineer
from ai.ml_model_trainer import MLModelTrainer
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


def fetch_stock_data(api: KiwoomAPI, symbol: str, days: int = 200) -> pd.DataFrame:
    """
    키움 API에서 주식 일봉 데이터 가져오기

    Args:
        api: KiwoomAPI 인스턴스
        symbol: 종목코드 (예: '005930' - 삼성전자)
        days: 가져올 일수

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    logger.info(f"📊 종목 {symbol}의 일봉 데이터 가져오는 중... ({days}일)")

    try:
        all_data = []
        cont_yn = "N"
        next_key = ""
        max_iterations = 10  # 최대 10번 반복 (안전장치)

        # 연속 조회로 데이터 수집
        for iteration in range(max_iterations):
            result = api.get_daily_chart(
                stock_code=symbol,
                cont_yn=cont_yn,
                next_key=next_key
            )

            # 응답 데이터 확인
            if 'output2' in result and isinstance(result['output2'], list):
                all_data.extend(result['output2'])
                logger.info(f"   {iteration + 1}차 조회: {len(result['output2'])}건 (누적: {len(all_data)}건)")

            # 충분한 데이터를 수집했거나 더 이상 데이터가 없으면 중단
            if len(all_data) >= days:
                logger.info(f"   목표 데이터 수집 완료: {len(all_data)}건")
                break

            # 연속 조회 정보 확인
            if result.get('cont_yn') == 'Y' and result.get('next_key'):
                cont_yn = "Y"
                next_key = result['next_key']
            else:
                logger.info(f"   더 이상 조회할 데이터 없음")
                break

        if not all_data or len(all_data) == 0:
            raise ValueError(f"종목 {symbol}의 데이터를 가져올 수 없습니다.")

        # DataFrame으로 변환
        df = pd.DataFrame(all_data[:days])  # 필요한 일수만큼만 사용

        # 컬럼명 정규화 (키움 API 응답 형식에 맞게)
        column_mapping = {
            'stck_bsop_date': 'date',
            'stck_oprc': 'open',
            'stck_hgpr': 'high',
            'stck_lwpr': 'low',
            'stck_clpr': 'close',
            'acml_vol': 'volume'
        }

        # 실제 컬럼명 확인 및 매핑
        logger.debug(f"   원본 컬럼: {df.columns.tolist()}")
        df = df.rename(columns=column_mapping)

        # 필요한 컬럼만 선택
        required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        available_cols = [col for col in required_cols if col in df.columns]

        if len(available_cols) < len(required_cols):
            missing = set(required_cols) - set(available_cols)
            logger.warning(f"   일부 컬럼 누락: {missing}")
            # 누락된 컬럼 추가 (기본값 0)
            for col in missing:
                df[col] = 0

        df = df[required_cols]

        # 데이터 타입 변환
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # NaN 제거
        df = df.dropna()

        # 날짜순 정렬 (오래된 것부터)
        df = df.sort_values('date').reset_index(drop=True)

        logger.info(f"✅ {len(df)}일치 데이터 가져오기 완료")
        logger.info(f"   기간: {df['date'].min()} ~ {df['date'].max()}")
        logger.info(f"   최근 종가: {df['close'].iloc[-1]:,.0f}원")

        return df

    except Exception as e:
        logger.error(f"❌ 데이터 가져오기 실패: {e}")
        raise


def create_training_data(price_data: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    가격 데이터에서 ML 학습용 데이터 생성

    Args:
        price_data: 가격 데이터 DataFrame
        symbol: 종목코드

    Returns:
        Features + Target이 포함된 DataFrame
    """
    logger.info("🔧 Feature Engineering 중...")

    feature_engineer = FeatureEngineer()
    features_list = []

    # 각 시점에 대한 features 생성
    min_required = 60  # FeatureEngineer가 요구하는 최소 데이터 수

    for i in range(min_required, len(price_data)):
        try:
            # 해당 시점까지의 데이터로 feature 추출
            df_slice = price_data.iloc[:i+1].copy()

            fs = feature_engineer.extract_features(df_slice, symbol=symbol)
            fd = fs.to_dict()

            # Target 생성: 다음날 가격이 오르면 1, 내리면 0
            if i < len(price_data) - 1:
                future_price = price_data.iloc[i + 1]['close']
                current_price = price_data.iloc[i]['close']
                fd['target'] = 1 if future_price > current_price else 0
                fd['future_return'] = (future_price - current_price) / current_price
            else:
                # 마지막 데이터는 미래를 모르므로 제외
                continue

            features_list.append(fd)

            if (i - min_required + 1) % 50 == 0:
                logger.info(f"   진행: {i - min_required + 1}/{len(price_data) - min_required - 1} 샘플 생성")

        except Exception as e:
            logger.debug(f"인덱스 {i} 스킵: {e}")
            continue

    # DataFrame으로 변환
    df = pd.DataFrame(features_list)

    # 불필요한 컬럼 제거
    cols_to_drop = ['symbol', 'timestamp']
    df = df.drop(columns=[c for c in cols_to_drop if c in df.columns])

    logger.info(f"✅ Feature Engineering 완료: {len(df)} 샘플")
    logger.info(f"   Feature 수: {len(df.columns) - 2} 개")

    # Target 분포
    target_dist = df['target'].value_counts()
    logger.info(f"   Target 분포: 상승={target_dist.get(1, 0)}, 하락={target_dist.get(0, 0)}")

    return df


def main():
    """메인 실행 함수"""

    logger.info("=" * 80)
    logger.info("🚀 실제 키움 API 데이터로 ML 모델 학습 시작")
    logger.info("=" * 80)

    try:
        # 1. 키움 API 초기화
        logger.info("\n1️⃣ 키움 API 연결 중...")
        api = KiwoomAPI()
        logger.info("✅ 키움 API 초기화 완료")

        # 2. 학습할 종목 설정
        SYMBOLS = [
            '005930',  # 삼성전자
            '000660',  # SK하이닉스
            '035420',  # NAVER
            '051910',  # LG화학
            '035720',  # 카카오
        ]

        logger.info(f"\n📋 학습 대상 종목: {', '.join(SYMBOLS)}")

        # 3. 모든 종목의 데이터 수집 및 학습 데이터 생성
        all_training_data = []

        for symbol in SYMBOLS:
            logger.info(f"\n{'='*80}")
            logger.info(f"📊 종목: {symbol}")
            logger.info(f"{'='*80}")

            try:
                # 일봉 데이터 가져오기
                price_data = fetch_stock_data(api, symbol, days=200)

                # 학습 데이터 생성
                training_data = create_training_data(price_data, symbol)

                all_training_data.append(training_data)

                logger.info(f"✅ {symbol} 데이터 준비 완료: {len(training_data)} 샘플")

            except Exception as e:
                logger.error(f"❌ {symbol} 처리 실패: {e}")
                continue

        if not all_training_data:
            raise ValueError("학습 데이터가 없습니다!")

        # 4. 모든 종목 데이터 통합
        logger.info(f"\n{'='*80}")
        logger.info("🔗 모든 종목 데이터 통합 중...")
        df_combined = pd.concat(all_training_data, ignore_index=True)
        logger.info(f"✅ 통합 완료: 총 {len(df_combined)} 샘플")
        logger.info(f"   Feature 수: {len(df_combined.columns) - 2} 개")

        # Target 분포
        target_dist = df_combined['target'].value_counts()
        logger.info(f"   전체 Target 분포: 상승={target_dist.get(1, 0)}, 하락={target_dist.get(0, 0)}")

        # 5. 모델 학습
        logger.info(f"\n{'='*80}")
        logger.info("🤖 ML 모델 학습 시작...")
        logger.info(f"{'='*80}")

        trainer = MLModelTrainer(model_type='lightgbm')
        model, metrics = trainer.train(
            df=df_combined,
            target_column='target',
            test_size=0.2
        )

        # 6. 결과 출력
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

        # 7. 모델 저장
        logger.info("\n" + "=" * 80)
        logger.info("💾 모델 저장 중...")
        model_path = trainer.save_model(model, metrics)
        logger.info(f"✅ 모델 저장 완료: {model_path}")

        # 8. Feature Importance 출력
        logger.info("\n" + "=" * 80)
        logger.info("📊 주요 Feature Importance (Top 10):")
        logger.info("=" * 80)
        feature_importance = trainer.get_feature_importance(top_n=10)
        for i, (feat, importance) in enumerate(feature_importance, 1):
            logger.info(f"  {i:2d}. {feat:30s}: {importance:.4f}")

        logger.info("\n" + "=" * 80)
        logger.info("🎉 모든 작업이 성공적으로 완료되었습니다!")
        logger.info("=" * 80)

        return 0

    except Exception as e:
        logger.error(f"\n❌ 오류 발생: {e}", exc_info=True)
        return 1
    finally:
        # API 연결 종료
        try:
            api.close()
            logger.info("✅ 키움 API 연결 종료")
        except:
            pass


if __name__ == '__main__':
    sys.exit(main())
