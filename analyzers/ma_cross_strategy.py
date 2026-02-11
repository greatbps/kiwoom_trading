#!/usr/bin/env python3
"""
MA 골든크로스/데드크로스 전략 (단순 버전)
- 5분봉 MA5/MA10 골든크로스 → 매수
- 5분봉 MA5/MA10 데드크로스 → 매도
- 추가 조건 없음
"""

from typing import Tuple, Dict, Optional
import pandas as pd
from rich.console import Console

console = Console()


class MACrossStrategy:
    """MA 골든크로스/데드크로스 전략 (5분봉)"""

    def __init__(self):
        """초기화"""
        self.ma_short = 5   # 단기 이평
        self.ma_long = 10   # 장기 이평

        # 통계
        self.stats = {
            'total_signals': 0,
            'golden_cross': 0,
            'dead_cross': 0
        }

    def calculate_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        이동평균 계산

        Args:
            df: OHLCV 데이터프레임

        Returns:
            MA5, MA10이 추가된 데이터프레임
        """
        if 'close' not in df.columns:
            console.print("[red]❌ close 컬럼이 없습니다[/red]")
            return df

        df = df.copy()
        df['ma5'] = df['close'].rolling(window=self.ma_short).mean()
        df['ma10'] = df['close'].rolling(window=self.ma_long).mean()

        return df

    def check_golden_cross(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        골든크로스 감지 (5분봉 기준)

        Args:
            df: MA5, MA10이 계산된 데이터프레임

        Returns:
            (is_golden_cross, reason)
        """
        if len(df) < self.ma_long + 1:
            return False, f"데이터 부족 (최소 {self.ma_long + 1}개 필요)"

        # 최신 2개 봉 확인
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        # 골든크로스: 이전 봉에서 MA5 <= MA10, 현재 봉에서 MA5 > MA10
        if pd.isna(prev['ma5']) or pd.isna(prev['ma10']) or pd.isna(curr['ma5']) or pd.isna(curr['ma10']):
            return False, "MA 계산 오류 (NaN)"

        if prev['ma5'] <= prev['ma10'] and curr['ma5'] > curr['ma10']:
            self.stats['golden_cross'] += 1
            return True, f"골든크로스 (MA5: {curr['ma5']:.0f}, MA10: {curr['ma10']:.0f})"

        return False, "골든크로스 미발생"

    def check_dead_cross(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        데드크로스 감지 (5분봉 기준)

        Args:
            df: MA5, MA10이 계산된 데이터프레임

        Returns:
            (is_dead_cross, reason)
        """
        if len(df) < self.ma_long + 1:
            return False, f"데이터 부족 (최소 {self.ma_long + 1}개 필요)"

        # 최신 2개 봉 확인
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        # 데드크로스: 이전 봉에서 MA5 >= MA10, 현재 봉에서 MA5 < MA10
        if pd.isna(prev['ma5']) or pd.isna(prev['ma10']) or pd.isna(curr['ma5']) or pd.isna(curr['ma10']):
            return False, "MA 계산 오류 (NaN)"

        if prev['ma5'] >= prev['ma10'] and curr['ma5'] < curr['ma10']:
            self.stats['dead_cross'] += 1
            return True, f"데드크로스 (MA5: {curr['ma5']:.0f}, MA10: {curr['ma10']:.0f})"

        return False, "데드크로스 미발생"

    def check_entry_signal(
        self,
        df_5min: pd.DataFrame,
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        진입 신호 체크 (5분봉 골든크로스만)

        Args:
            df_5min: 5분봉 OHLCV 데이터
            debug: 디버그 로그 출력 여부

        Returns:
            (signal, reason, details)
        """
        self.stats['total_signals'] += 1
        details = {}

        if debug:
            console.print("[cyan]📊 MA Cross 전략 진입 체크 (5분봉 골든크로스)[/cyan]")

        # 1. 5분봉 MA 계산
        df_5min = self.calculate_ma(df_5min)

        # 2. 골든크로스 확인
        is_golden, gc_reason = self.check_golden_cross(df_5min)
        details['golden_cross'] = {
            'passed': is_golden,
            'reason': gc_reason
        }

        if debug:
            status = "✓" if is_golden else "✗"
            console.print(f"  {status} [5분봉] 골든크로스: {gc_reason}")

        if not is_golden:
            return False, f"골든크로스 미발생: {gc_reason}", details

        # ✅ 골든크로스 발생
        if debug:
            console.print("[green]  ✅ 5분봉 골든크로스 발생! (MA5 > MA10)[/green]")

        return True, "5분봉 골든크로스", details

    def check_exit_signal(
        self,
        df_5min: pd.DataFrame,
        debug: bool = True
    ) -> Tuple[bool, str, Dict]:
        """
        청산 신호 체크 (5분봉 데드크로스)

        Args:
            df_5min: 5분봉 OHLCV 데이터
            debug: 디버그 로그 출력 여부

        Returns:
            (should_exit, reason, details)
        """
        details = {}

        # 5분봉 MA 계산
        df_5min = self.calculate_ma(df_5min)

        # 데드크로스 확인
        is_dead, dc_reason = self.check_dead_cross(df_5min)
        details['dead_cross'] = {
            'passed': is_dead,
            'reason': dc_reason
        }

        if debug and is_dead:
            console.print(f"[red]  ❌ 5분봉 데드크로스: {dc_reason}[/red]")

        if is_dead:
            return True, f"5분봉 데드크로스 청산: {dc_reason}", details

        return False, "데드크로스 미발생", details

    def get_stats(self) -> Dict:
        """통계 반환"""
        return self.stats.copy()
