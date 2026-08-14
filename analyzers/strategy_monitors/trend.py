"""seq 35 — Supertrend + EMA + RSI. WI-15 Rev.2: WI-9/10/14에서 코드 구현이
없다고 확인했으나, 사용자가 실제 HTS [0150] 조건검색식 화면(docs/슈퍼트렌드.jpg)을
직접 확보해 제공함(2026-08-11). 조건식 이름과 달리 실제 수식에는 ATR 기반
Supertrend 밴드 계산이 없다 — 화면에 있는 그대로 구현한다(이름을 보고 진짜
Supertrend 지표를 새로 발명하지 않는다):

    A. 주가이평비교:[일]0봉전 (종가5)이평 > (종가20)이평, 2회이상
    B. 주가등락비교:[일]0봉전 RSI(14) 40 이상
    C. 주가등락률:[일]0봉전(중가) 종가대비 0봉전 종가등락률 10%이상
    D. 주가등락률:[일]1봉전(중가) 종가대비 0봉전 종가등락률 1%이상
    E. 0봉전 몸통길이(직전봉 종가,0,0,1.0) 윗그림자(0.5) 아래그림자(0)

⚠️ C/D/E는 스크린샷 해상도상 완전히 명료하지 않아 아래와 같이 해석했다
   (WI-15 Rev.2 보고서에 원문 그대로 기록, 재확인 권장):
   - "(중)" = 중가(고가+저가)/2 로 해석
   - C: (0봉전 종가 - 0봉전 중가) / 0봉전 중가 * 100 >= 10%
   - D: (0봉전 종가 - 1봉전 중가) / 1봉전 중가 * 100 >= 1%
   - E: 양봉(종가>시가), 윗그림자 <= 0.5×몸통, 아래그림자 <= 0(사실상 없음)
   - A의 "2회이상"은 화면에 lookback 봉수가 안 보여 최근 5봉 중 2회이상으로
     가정(표준 Kiwoom 기본 lookback 추정치, 원문에 명시 없음)

전부 AND 결합으로 가정(화면에 별도 OR 그룹 표시 없음).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import pandas_ta as ta

from analyzers.technical_analyzer import TechnicalAnalyzer

from .base import StrategyMonitor, StrategyMonitorResult

MIN_BARS = 25
LOOKBACK = 5  # "N회이상" 판정에 쓰는 최근 봉 수 (원문에 미명시, 가정값)


class TrendMonitor(StrategyMonitor):
    seq = 35
    strategy_name = 'Supertrend + EMA + RSI'

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        if df is None or len(df) < MIN_BARS:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{MIN_BARS}봉) — MA20/RSI(14) 최소 요구량 미달')
        try:
            ma5 = ta.sma(df['close'], length=5)
            ma20 = ta.sma(df['close'], length=20)
            recent_cross = (ma5.tail(LOOKBACK) > ma20.tail(LOOKBACK))
            cond_a = bool(recent_cross.sum() >= 2)

            rsi = TechnicalAnalyzer().calculate_rsi(df, period=14)
            rsi_val = float(rsi.iloc[-1])
            cond_b = rsi_val >= 40

            today_close = float(df['close'].iloc[-1])
            today_open = float(df['open'].iloc[-1])
            today_high = float(df['high'].iloc[-1])
            today_low = float(df['low'].iloc[-1])
            today_mid = (today_high + today_low) / 2
            prev_high = float(df['high'].iloc[-2])
            prev_low = float(df['low'].iloc[-2])
            prev_mid = (prev_high + prev_low) / 2

            rate_c = (today_close - today_mid) / today_mid * 100 if today_mid else 0.0
            cond_c = rate_c >= 10

            rate_d = (today_close - prev_mid) / prev_mid * 100 if prev_mid else 0.0
            cond_d = rate_d >= 1

            body = today_close - today_open
            upper_shadow = today_high - max(today_open, today_close)
            lower_shadow = min(today_open, today_close) - today_low
            cond_e = bool(body > 0 and upper_shadow <= 0.5 * body and lower_shadow <= 0)
        except Exception as e:
            return self._error_result(symbol, e)

        core_conditions = cond_a and cond_b and cond_c and cond_d and cond_e
        reasons = [
            f'A.ma5>ma20 최근{LOOKBACK}봉중 {int(recent_cross.sum())}회(>=2 -> {cond_a})',
            f'B.rsi14={rsi_val:.1f}(>=40 -> {cond_b})',
            f'C.종가대비중가등락률={rate_c:.2f}%(>=10 -> {cond_c}, 해석값)',
            f'D.종가대비전일중가등락률={rate_d:.2f}%(>=1 -> {cond_d}, 해석값)',
            f'E.몸통={body:.0f} 윗그림자={upper_shadow:.0f} 아래그림자={lower_shadow:.0f} -> {cond_e}(해석값)',
        ]
        state = 'TREND_CONFIRMING' if core_conditions else 'TREND_CONDITIONS_NOT_MET'

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=core_conditions, reasons=reasons,
        )
