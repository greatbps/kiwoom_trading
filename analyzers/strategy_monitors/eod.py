"""seq 34 — EOD("종가베팅"). WI-15 Rev.2: WI-9/10/14에서 진입측 코드가 없다고
확인했으나, 사용자가 실제 HTS [0150] 조건검색식 원문을 직접 확인해 제공함
(2026-08-11):

    A. 종가 5이평이 종가 20이평을 골든크로스
    B. 현재가기준 시가총액 50십억원(500억원) 이상
    C. 5봉(1주일) 평균거래량 500,000주 이상
    D. 1봉전(어제) 종가 < 0봉전(오늘) 종가(현재가)
    E. 0봉전(오늘) 시가 < 0봉전(오늘) 종가(현재가)

전부 AND 결합(HTS 조건검색식 기본값, 화면에 별도 OR 그룹 표시 없음).

⚠️ B(시가총액)는 이 Monitor 인터페이스(symbol, OHLCV df)로는 확인할 수 없다
   — 신규 API 호출을 추가하지 않는다는 WI-9 원칙에 따라, B를 제외한 A/C/D/E
   4개 조건으로만 판정하고 그 사실을 reasons에 항상 명시한다(데이터 없음을
   숨기지 않는다).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import pandas_ta as ta

from .base import StrategyMonitor, StrategyMonitorResult

MIN_BARS = 22  # MA20을 어제/오늘 이틀치 비교하려면 최소 21봉 + 여유 1봉


class EODMonitor(StrategyMonitor):
    seq = 34
    strategy_name = 'EOD'

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        if df is None or len(df) < MIN_BARS:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{MIN_BARS}봉) — MA20 골든크로스 비교 최소 요구량 미달')
        try:
            ma5 = ta.sma(df['close'], length=5)
            ma20 = ta.sma(df['close'], length=20)
            golden_cross = bool(ma5.iloc[-1] > ma20.iloc[-1] and ma5.iloc[-2] <= ma20.iloc[-2])

            vol5_avg = float(df['volume'].tail(5).mean())
            volume_ok = vol5_avg >= 500_000

            prev_close = float(df['close'].iloc[-2])
            today_close = float(df['close'].iloc[-1])
            today_open = float(df['open'].iloc[-1])
            up_vs_yesterday = today_close > prev_close
            bullish_candle = today_open < today_close
        except Exception as e:
            return self._error_result(symbol, e)

        core_conditions = golden_cross and volume_ok and up_vs_yesterday and bullish_candle
        reasons = [
            f'A.golden_cross(MA5>MA20 today, MA5<=MA20 yesterday)={golden_cross}',
            f'B.market_cap>=500억원: 데이터소스 없음(OHLCV만 사용, 검증 제외 — 신규 API 호출 금지 원칙)',
            f'C.vol5_avg={vol5_avg:.0f}(>=500000 -> {volume_ok})',
            f'D.today_close({today_close:.0f})>prev_close({prev_close:.0f})={up_vs_yesterday}',
            f'E.today_open({today_open:.0f})<today_close({today_close:.0f})={bullish_candle}',
        ]
        state = 'EOD_CONFIRMING' if core_conditions else 'EOD_CONDITIONS_NOT_MET'

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=core_conditions, reasons=reasons,
        )
