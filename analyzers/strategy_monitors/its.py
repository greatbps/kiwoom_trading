"""seq 39 — ITS. WI-10/14에서 코드/설정/문서/git history 전수 검색 0건이었으나,
ITS의 실체는 코드가 아니라 HTS 조건검색식 자체였다 — 사용자가 [0150] 화면
(docs/ITS.jpg)을 직접 확보해 제공함(2026-08-11). 이름의 약어 의미는 여전히
불명이지만(창작하지 않음), 조건식 원문은 아래와 같이 명확히 확보됐다:

    A. 주가등락률:[30분]1봉전(중가) 종가대비 0봉전 종가등락률 -0.3%이상
    B. 주가등락률:[일]1봉전(중가) 종가대비 0봉전 종가등락률 -0.3%이상
    C. [일]거래대금(백만원, 분단위표시):0봉전 50,000 이상 999,999,990 이하
       (=500억원 이상, 상한은 사실상 무제한)
    D. 주가이평비교:[일]0봉전 (종가1)이평 > (종가20)이평, 1회이상
    E. 주가이평비교:[일]0봉전 (종가5)이평 > (종가10)이평, 1회이상

⚠️ A(30분봉)는 이 Monitor가 쓰는 데이터가 일봉(WI-9 §Phase1 "Monitor 데이터
   소스" 결정 — 신규 API 호출 없이 기존 조회된 일봉 재사용)이라 30분봉 해상도로
   확인할 수 없다(Breakout Monitor의 5분봉 해상도 불일치와 동일한 종류의
   제약, WI-10 architecture.md에 이미 문서화된 패턴). B(일봉 버전, 사실상 동일
   취지)로 대체 검증하고 A는 reasons에 명시적으로 "미검증"이라고 남긴다.
   C는 거래대금 원본 필드가 없어 종가×거래량으로 근사한다.
   D/E의 "1회이상"은 원문에 lookback 봉수가 없어 A(2회이상)와 동일하게
   최근 5봉 기준으로 판정한다(가정값, trend.py와 동일 관례).

전부 AND 결합으로 가정(화면에 별도 OR 그룹 표시 없음).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import pandas_ta as ta

from .base import StrategyMonitor, StrategyMonitorResult

MIN_BARS = 22
LOOKBACK = 5  # "N회이상" 판정에 쓰는 최근 봉 수 (원문에 미명시, 가정값)


class ITSMonitor(StrategyMonitor):
    seq = 39
    strategy_name = 'ITS'

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        if df is None or len(df) < MIN_BARS:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{MIN_BARS}봉) — MA20 최소 요구량 미달')
        try:
            prev_close = float(df['close'].iloc[-2])
            today_close = float(df['close'].iloc[-1])
            rate_b = (today_close - prev_close) / prev_close * 100 if prev_close else 0.0
            cond_b = rate_b >= -0.3

            today_volume = float(df['volume'].iloc[-1])
            trade_value_million = (today_close * today_volume) / 1_000_000
            cond_c = 50_000 <= trade_value_million <= 999_999_990

            ma1 = df['close']  # 1기간 이평 = 종가 자체
            ma20 = ta.sma(df['close'], length=20)
            recent_d = (ma1.tail(LOOKBACK) > ma20.tail(LOOKBACK))
            cond_d = bool(recent_d.sum() >= 1)

            ma5 = ta.sma(df['close'], length=5)
            ma10 = ta.sma(df['close'], length=10)
            recent_e = (ma5.tail(LOOKBACK) > ma10.tail(LOOKBACK))
            cond_e = bool(recent_e.sum() >= 1)
        except Exception as e:
            return self._error_result(symbol, e)

        core_conditions = cond_b and cond_c and cond_d and cond_e
        reasons = [
            'A.30분봉 등락률>=-0.3%: 미검증(Monitor는 일봉만 사용, 해상도 제약)',
            f'B.일봉등락률(전일대비)={rate_b:.2f}%(>=-0.3 -> {cond_b})',
            f'C.거래대금(근사,종가x거래량)={trade_value_million:.0f}백만원'
            f'(50000~999999990 -> {cond_c})',
            f'D.종가>MA20 최근{LOOKBACK}봉중 {int(recent_d.sum())}회(>=1 -> {cond_d})',
            f'E.MA5>MA10 최근{LOOKBACK}봉중 {int(recent_e.sum())}회(>=1 -> {cond_e})',
        ]
        state = 'ITS_CONFIRMING' if core_conditions else 'ITS_CONDITIONS_NOT_MET'

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=core_conditions, reasons=reasons,
        )
