#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils/backtest_integration.py

조건검색 결과를 백테스트 입력 형식으로 변환
"""

import asyncio
import logging
import pandas as pd
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


async def convert_vwap_results_to_backtest_input(
    validated_stocks: List[Dict[str, Any]],
    feature_calculator=None
) -> pd.DataFrame:
    """
    VWAP 검증 통과 종목을 백테스트 입력 형식으로 변환

    Args:
        validated_stocks: KiwoomVWAPPipeline.validated_stocks 결과
                         [{'stock_code': '005930', 'stock_name': '삼성전자',
                           'stats': {...}, 'ticker': '005930.KS', ...}, ...]
        feature_calculator: FeatureCalculator 인스턴스 (None이면 임시값 사용)

    Returns:
        백테스트용 DataFrame with columns:
        - code: 종목코드
        - name: 종목명
        - entry_price: 진입가 (현재가)
        - vwap: VWAP 값
        - volume: 거래량
        - volume_avg_20d: 20일 평균 거래량
        - volume_std_20d: 20일 거래량 표준편차
        - vwap_backtest_winrate: VWAP 백테스트 승률
        - vwap_avg_profit: VWAP 평균 수익률
        - recent_return_5d: 최근 5일 수익률
        - market_volatility: 시장 변동성
        - sector_strength: 업종 강도
        - price_momentum: 가격 모멘텀
    """
    candidates_list = []

    logger.info(f"백테스트 입력 데이터 변환 시작: {len(validated_stocks)}개 종목")

    for idx, stock in enumerate(validated_stocks, 1):
        stats = stock.get('stats', {})
        code = stock.get('stock_code', '')
        name = stock.get('stock_name', code)

        logger.info(f"[{idx}/{len(validated_stocks)}] {name} ({code}) 처리 중...")

        # Feature 계산
        if feature_calculator:
            try:
                # 실제 Feature 계산
                features = await feature_calculator.calculate_all_features(
                    stock_code=code,
                    vwap_stats=stats
                )

                # 현재가 재조회 (features에서 사용한 값)
                entry_price = await feature_calculator._get_current_price(code)
                if entry_price is None:
                    entry_price = 10000  # 폴백
                    logger.warning(f"{code}: 현재가 조회 실패, 기본값 사용")

                # VWAP 계산 (features에서 사용한 차트 데이터 재활용 가능)
                chart_data = await feature_calculator._get_recent_chart_data(code, days=20)
                if chart_data:
                    vwap = feature_calculator._calculate_vwap_from_chart(chart_data)

                    # 거래량 통계
                    volumes = [float(d.get('volume', d.get('stk_trd_qty', 0)))
                              for d in chart_data[-20:]]
                    if volumes:
                        volume = volumes[-1]
                        volume_avg_20d = sum(volumes) / len(volumes)
                        import numpy as np
                        volume_std_20d = np.std(volumes)
                    else:
                        volume = 1000000
                        volume_avg_20d = 1000000
                        volume_std_20d = 100000
                else:
                    vwap = entry_price * 0.99
                    volume = 1000000
                    volume_avg_20d = 1000000
                    volume_std_20d = 100000

                candidates_list.append({
                    'code': code,
                    'name': name,
                    'entry_price': entry_price,
                    'vwap': vwap,
                    'volume': volume,
                    'volume_avg_20d': volume_avg_20d,
                    'volume_std_20d': volume_std_20d,
                    **features  # features 딕셔너리 언팩
                })

            except Exception as e:
                logger.error(f"{code} feature 계산 실패: {e}")
                # 에러 시 기본값 사용
                candidates_list.append(_get_default_candidate(code, name, stats))

        else:
            # FeatureCalculator 없으면 기본값 사용
            logger.warning(f"{code}: FeatureCalculator 없음, 기본값 사용")
            candidates_list.append(_get_default_candidate(code, name, stats))

    logger.info(f"백테스트 입력 데이터 변환 완료: {len(candidates_list)}개")

    return pd.DataFrame(candidates_list)


def _get_default_candidate(code: str, name: str, stats: Dict) -> Dict:
    """기본값으로 candidate 생성"""
    win_rate = stats.get('win_rate', 50.0) / 100.0
    avg_profit = stats.get('avg_profit_pct', 0.0)

    return {
        'code': code,
        'name': name,
        'entry_price': 10000,
        'vwap': 9900,
        'volume': 1000000,
        'volume_avg_20d': 900000,
        'volume_std_20d': 100000,
        'vwap_backtest_winrate': win_rate,
        'vwap_avg_profit': avg_profit,
        'recent_return_5d': 0.0,
        'market_volatility': 15.0,
        'sector_strength': 0.5,
        'price_momentum': 0.0,
    }


def extract_condition_search_codes(condition_stocks: Dict[str, List[str]]) -> List[str]:
    """
    조건검색 결과에서 종목 코드 리스트 추출 (중복 제거)

    Args:
        condition_stocks: {seq: [stock_codes]} 형식

    Returns:
        중복 제거된 종목 코드 리스트
    """
    all_codes = set()
    for codes in condition_stocks.values():
        all_codes.update(codes)
    return list(all_codes)
