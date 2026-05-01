"""
스윙 보유 관리 (MA5/MA20 기반)

MA5는 단기 추세의 핵심 기준:
    - 종가 < MA5 2일 연속 → EXIT (단기 추세 이탈)
    - 종가 < MA20 → EXIT 즉시 (추세 자체 붕괴, 하드 스톱)
    - 종가 ≈ MA5 (±1%) + 양봉 전환 → ADD (눌림 후 수요 확인)
    - 보유 7일+ 수익 7%+ → TRAIL (트레일링 모드)

추가매수는 최대 2회 (포지션 당 최대 30%+20% 추가).

평가 시점: EOD 전용 (swing_runner.py 15:35 이후 호출). 장중 평가 없음.
"""

from __future__ import annotations

import logging

import pandas as pd

from .state_machine import SwingPosition

logger = logging.getLogger(__name__)


class HoldingManager:
    """
    보유 포지션 평가 및 행동 결정 (EOD 전용).

    반환값 의미:
        HOLD  - 현재 상태 유지
        ADD   - MA5 눌림 + 양봉 전환 추가매수 조건 충족
        EXIT  - MA5 2일 연속 이탈 / MA20 이탈 / 드로우다운 한도 초과
        TRAIL - 수익 7%+ 달성, 트레일링 모드 전환

    우선순위: 드로우다운 → 최대보유일 → MA20 하드 → MA5 2일 → ADD → TRAIL → HOLD
    """

    ADD_MA5_THRESHOLD: float = 1.0   # MA5 ±1% 이내 = "근접"
    TRAIL_MIN_DAYS: int = 7
    TRAIL_MIN_PROFIT_PCT: float = 7.0
    ADD_MIN_DAYS: int = 3
    MAX_ADD_COUNT: int = 2
    MA5_CONSECUTIVE_EXIT: int = 2    # MA5 연속 이탈 기준일

    def __init__(self, config: dict):
        self._config = config
        hold_cfg = config.get('swing', {}).get('hold', {})
        self._drawdown_exit_pct = hold_cfg.get('drawdown_exit_pct', 5.0)
        self._max_hold_days = hold_cfg.get('max_hold_days', 30)
        self._trail_start_days = hold_cfg.get('trail_start_days', self.TRAIL_MIN_DAYS)
        self._trail_start_profit = hold_cfg.get('trail_start_profit_pct', self.TRAIL_MIN_PROFIT_PCT)
        self._ma5_consecutive = hold_cfg.get('ma5_consecutive_days', self.MA5_CONSECUTIVE_EXIT)
        self._ma20_hard_exit = hold_cfg.get('ma20_hard_exit', True)

    def evaluate(self, pos: SwingPosition, df_daily: pd.DataFrame) -> str:
        """보유 포지션 상태 평가 → 행동 결정 (EOD 전용)."""
        if len(df_daily) < 20:
            return 'HOLD'

        close = float(df_daily['close'].iloc[-1])
        open_ = float(df_daily['open'].iloc[-1])

        ma5_series = df_daily['close'].rolling(5).mean()
        ma5 = float(ma5_series.iloc[-1])
        ma20_series = df_daily['close'].rolling(20).mean()
        ma20 = float(ma20_series.iloc[-1])

        if pd.isna(ma5) or ma5 <= 0:
            return 'HOLD'

        # MA5 이격 갱신
        pos.ma5_distance_pct = (close - ma5) / ma5 * 100

        # 수익률 및 드로우다운 갱신
        if pos.entry_price > 0:
            profit_pct = (close - pos.entry_price) / pos.entry_price * 100
            pos.max_profit_pct = max(pos.max_profit_pct, profit_pct)
            pos.drawdown_pct = pos.max_profit_pct - profit_pct
        else:
            profit_pct = 0.0

        pos.holding_days += 1

        # ① 드로우다운 초과 → 즉시 청산
        if pos.drawdown_pct >= self._drawdown_exit_pct:
            logger.info(
                f"[HOLDING] {pos.stock_code} EXIT - 드로우다운 "
                f"{pos.drawdown_pct:.1f}% >= {self._drawdown_exit_pct}%"
            )
            return 'EXIT'

        # ② 최대 보유일 초과
        if pos.holding_days >= self._max_hold_days:
            logger.info(f"[HOLDING] {pos.stock_code} EXIT - 최대 보유일 {self._max_hold_days}일 도달")
            return 'EXIT'

        # ③ MA20 이탈 → 하드 스톱 (추세 자체 붕괴)
        if self._ma20_hard_exit and not pd.isna(ma20) and ma20 > 0 and close < ma20:
            logger.info(
                f"[HOLDING] {pos.stock_code} EXIT - MA20 이탈(하드) "
                f"close={close:.0f} ma20={ma20:.0f}"
            )
            return 'EXIT'

        # ④ MA5 연속 이탈 추적 → 2일 연속 이탈 시 EXIT
        if close < ma5:
            pos.ma5_below_days += 1
        else:
            pos.ma5_below_days = 0

        if pos.ma5_below_days >= self._ma5_consecutive:
            logger.info(
                f"[HOLDING] {pos.stock_code} EXIT - MA5 {self._ma5_consecutive}일 연속 이탈 "
                f"close={close:.0f} ma5={ma5:.0f}"
            )
            return 'EXIT'

        # ⑤ 추가매수: MA5 ±1% 근접 + 양봉 전환 + 전일 대비 상승 + 추가매수 여유 + 보유 3일 이상
        prev_close = float(df_daily['close'].iloc[-2])
        near_ma5 = abs(pos.ma5_distance_pct) < self.ADD_MA5_THRESHOLD
        is_bullish = close > open_         # 당일 양봉 (매수세 확인)
        is_rising = close > prev_close     # 전일 대비 상승 (반등 초입 아닌 '확인 후' 진입)
        if (near_ma5 and is_bullish and is_rising
                and pos.add_count < self.MAX_ADD_COUNT
                and pos.holding_days >= self.ADD_MIN_DAYS):
            logger.info(
                f"[HOLDING] {pos.stock_code} ADD - MA5 근접+양봉 "
                f"dist={pos.ma5_distance_pct:.1f}% add_count={pos.add_count}"
            )
            return 'ADD'

        # ⑥ 트레일링: 보유 7일+ AND 최고 수익 7%+
        if pos.holding_days >= self._trail_start_days and pos.max_profit_pct >= self._trail_start_profit:
            logger.info(
                f"[HOLDING] {pos.stock_code} TRAIL - "
                f"days={pos.holding_days} max_profit={pos.max_profit_pct:.1f}%"
            )
            return 'TRAIL'

        return 'HOLD'

    def add_lot_size(self, pos: SwingPosition) -> float:
        """추가매수 비중: 1차 30%, 2차 20%."""
        return 0.30 if pos.add_count == 0 else 0.20
