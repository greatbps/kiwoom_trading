#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BB(30,1) 관측 모듈

목적: 실제 5분봉에서 BB(30,1) 신호 로깅 (관측 전용, 진입 X)
배경: 일봉 백테스트 결과 폐기 결정, 5분봉에서 실데이터 검증 진행

사용법:
    observer = BB30Observer()
    observer.observe(stock_code, df_5min)  # 신호만 로깅

주의:
    - 이 모듈은 진입 신호를 생성하지 않음
    - 오직 로깅 목적으로만 사용
    - 1-2주 관측 후 폐기 또는 재검토 결정

폐기 조건:
    - 2주 관측 후 BB(30,1) 돌파가 추세 초입 포착에 유의미하지 않으면 완전 폐기
    - 유의미하면 entry_mode: bb_squeeze 재검토

생성일: 2026-01-23
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple
from pathlib import Path

# 전용 로거 설정
log_dir = Path(__file__).parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

bb30_logger = logging.getLogger("bb30_observer")
bb30_logger.setLevel(logging.INFO)

if not bb30_logger.handlers:
    handler = logging.FileHandler(log_dir / "bb30_observation.log", encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    bb30_logger.addHandler(handler)


class BB30Observer:
    """
    BB(30,1) 관측 전용 클래스

    역할:
    - 5분봉에서 BB(30,1) 돌파 신호 로깅
    - Squeeze 상태 동시 기록
    - 진입 신호 생성 X (관측만)
    """

    def __init__(self, bb_length: int = 30, bb_std: float = 1.0):
        self.bb_length = bb_length
        self.bb_std = bb_std
        self.observation_count = 0
        self.signal_count = 0

    def calculate_bb(self, df: pd.DataFrame) -> pd.DataFrame:
        """볼린저 밴드 계산"""
        df = df.copy()
        df['bb30_mid'] = df['close'].rolling(window=self.bb_length).mean()
        df['bb30_std'] = df['close'].rolling(window=self.bb_length).std()
        df['bb30_upper'] = df['bb30_mid'] + (df['bb30_std'] * self.bb_std)
        df['bb30_lower'] = df['bb30_mid'] - (df['bb30_std'] * self.bb_std)
        return df

    def check_squeeze_state(self, df: pd.DataFrame, idx: int) -> Dict:
        """스퀴즈 상태 확인"""
        if idx < 1:
            return {'squeeze_on': False, 'momentum': 0, 'momentum_rising': False}

        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        squeeze_on = row.get('sqz_on', False)
        momentum = row.get('sqz_momentum', 0)
        prev_momentum = prev_row.get('sqz_momentum', 0)
        momentum_rising = momentum > prev_momentum if momentum > 0 else False

        return {
            'squeeze_on': squeeze_on,
            'momentum': momentum,
            'momentum_rising': momentum_rising
        }

    def observe(
        self,
        stock_code: str,
        stock_name: str,
        df: pd.DataFrame,
        current_price: float = None
    ) -> Optional[Dict]:
        """
        BB(30,1) 신호 관측 및 로깅

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            df: 5분봉 데이터 (sqz_on, sqz_momentum 포함)
            current_price: 현재가 (없으면 df에서 추출)

        Returns:
            신호 발생 시 상세 정보, 없으면 None
        """
        if df is None or len(df) < self.bb_length + 5:
            return None

        self.observation_count += 1

        # BB(30,1) 계산
        df = self.calculate_bb(df)

        # 마지막 봉 기준
        idx = len(df) - 1
        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        close = row['close']
        bb_upper = row.get('bb30_upper', 0)
        bb_mid = row.get('bb30_mid', 0)
        prev_close = prev_row['close']
        prev_bb_upper = prev_row.get('bb30_upper', 0)

        if pd.isna(bb_upper) or bb_upper == 0:
            return None

        # BB(30,1) 상단 돌파 체크 (이전 봉: 밴드 내 → 현재 봉: 밴드 돌파)
        breakout = (prev_close <= prev_bb_upper) and (close > bb_upper)

        if not breakout:
            return None

        # 스퀴즈 상태 확인
        squeeze_state = self.check_squeeze_state(df, idx)

        # 신호 정보 구성
        signal_info = {
            'timestamp': datetime.now(),
            'stock_code': stock_code,
            'stock_name': stock_name,
            'close': close,
            'bb_upper': bb_upper,
            'bb_mid': bb_mid,
            'breakout_pct': (close - bb_upper) / bb_upper * 100,
            'squeeze_on': squeeze_state['squeeze_on'],
            'momentum': squeeze_state['momentum'],
            'momentum_rising': squeeze_state['momentum_rising']
        }

        # 신호 조합 분류
        if squeeze_state['squeeze_on'] and squeeze_state['momentum_rising']:
            signal_type = "STRONG"  # 스퀴즈 ON + 모멘텀 상승 + BB 돌파
        elif squeeze_state['squeeze_on']:
            signal_type = "MEDIUM"  # 스퀴즈 ON + BB 돌파
        else:
            signal_type = "WEAK"    # BB 돌파만

        signal_info['signal_type'] = signal_type
        self.signal_count += 1

        # 로깅
        log_msg = (
            f"{signal_type} | {stock_name}({stock_code}) | "
            f"종가:{close:,.0f} > BB상단:{bb_upper:,.0f} (+{signal_info['breakout_pct']:.2f}%) | "
            f"Squeeze:{'ON' if squeeze_state['squeeze_on'] else 'OFF'} | "
            f"Momentum:{squeeze_state['momentum']:.2f} ({'↑' if squeeze_state['momentum_rising'] else '→'})"
        )
        bb30_logger.info(log_msg)

        return signal_info

    def get_stats(self) -> Dict:
        """관측 통계"""
        return {
            'observations': self.observation_count,
            'signals': self.signal_count,
            'signal_rate': (self.signal_count / self.observation_count * 100)
                           if self.observation_count > 0 else 0
        }

    def log_daily_summary(self):
        """일간 요약 로깅"""
        stats = self.get_stats()
        bb30_logger.info(
            f"=== DAILY SUMMARY === | "
            f"관측: {stats['observations']}회 | "
            f"신호: {stats['signals']}회 | "
            f"발생률: {stats['signal_rate']:.1f}%"
        )


# 싱글톤 인스턴스
_observer_instance = None


def get_bb30_observer() -> BB30Observer:
    """BB30Observer 싱글톤 인스턴스 반환"""
    global _observer_instance
    if _observer_instance is None:
        _observer_instance = BB30Observer()
    return _observer_instance
