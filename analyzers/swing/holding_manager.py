"""
스윙 보유 관리 (MA5/MA20 기반)

액션 종류:
    HOLD         — 현재 상태 유지
    ADD_VALUE    — 눌림 추가매수: 평균단가 아래 + MA5 근접 → 단가 개선
    ADD_MOMENTUM — 모멘텀 피라미딩: 수익 중 + 거래량 폭발 + 신고가 돌파 → 수익 극대화
    REDUCE       — 부분 청산: 수익 환원 or 약세 신호 → 리스크 축소
    EXIT         — 전량 청산: MA5 2일 연속 이탈 / MA20 이탈 / 드로우다운 한도
    TRAIL        — 트레일링 모드 전환 (주문 없음)

evaluate() 반환값: tuple[action, exit_reason]
    exit_reason 목록: DRAWDOWN_STOP / MA20_EXIT / MA5_EXIT / TIME_EXIT /
                      PARTIAL_REDUCE / TRAIL / ADD_VALUE / ADD_MOMENTUM / HOLD

우선순위: 드로우다운 → 최대보유일 → MA20 하드 → REDUCE → MA5 2일 → ADD_VALUE/ADD_MOMENTUM → TRAIL → HOLD
"""

from __future__ import annotations

import logging

import pandas as pd

from .state_machine import SwingPosition

logger = logging.getLogger(__name__)


class HoldingManager:

    ADD_MA5_THRESHOLD: float = 1.0
    TRAIL_MIN_DAYS: int = 7
    TRAIL_MIN_PROFIT_PCT: float = 7.0
    ADD_MIN_DAYS: int = 3
    MAX_ADD_COUNT: int = 2
    MA5_CONSECUTIVE_EXIT: int = 2

    # VALUE ADD: 평균단가 대비 이 비율 이하여야 단가 개선 효과
    VALUE_ADD_MAX_ABOVE_AVG: float = -0.005   # -0.5% 이하 (평균단가 아래)

    # MOMENTUM ADD: 진입가 대비 이 비율 이상 상승 + 거래량 조건
    MOMENTUM_ADD_MIN_PROFIT: float = 0.03     # +3% 이상 수익 중
    MOMENTUM_ADD_VOL_MULT: float = 1.3        # 거래량 5일 평균 1.3배 이상

    # REDUCE: 이 수익률 이상에서 후퇴 신호 오면 30% 부분 청산
    REDUCE_MIN_PROFIT: float = 0.06           # +6% 이상 수익 중
    REDUCE_DRAWDOWN_WARN: float = 2.0         # 드로우다운 2% 이상 (하드 5%보다 먼저)

    def __init__(self, config: dict):
        self._config = config
        hold_cfg = config.get('swing', {}).get('hold', {})
        self._drawdown_exit_pct    = hold_cfg.get('drawdown_exit_pct', 5.0)
        self._max_hold_days        = hold_cfg.get('max_hold_days', 30)
        self._trail_start_days     = hold_cfg.get('trail_start_days', self.TRAIL_MIN_DAYS)
        self._trail_start_profit   = hold_cfg.get('trail_start_profit_pct', self.TRAIL_MIN_PROFIT_PCT)
        self._ma5_consecutive      = hold_cfg.get('ma5_consecutive_days', self.MA5_CONSECUTIVE_EXIT)
        self._ma20_hard_exit       = hold_cfg.get('ma20_hard_exit', True)
        self._reduce_min_profit    = hold_cfg.get('reduce_min_profit_pct', self.REDUCE_MIN_PROFIT * 100) / 100
        self._reduce_dd_warn       = hold_cfg.get('reduce_drawdown_warn_pct', self.REDUCE_DRAWDOWN_WARN)

    def evaluate(self, pos: SwingPosition, df_daily: pd.DataFrame, score_drift: float = 0.0) -> tuple[str, str]:
        if len(df_daily) < 20:
            return 'HOLD', 'HOLD'

        close  = float(df_daily['close'].iloc[-1])
        open_  = float(df_daily['open'].iloc[-1])
        prev_close = float(df_daily['close'].iloc[-2])

        ma5  = float(df_daily['close'].rolling(5).mean().iloc[-1])
        ma20 = float(df_daily['close'].rolling(20).mean().iloc[-1])

        if pd.isna(ma5) or ma5 <= 0:
            return 'HOLD', 'HOLD'

        pos.ma5_distance_pct = (close - ma5) / ma5 * 100

        profit_pct = 0.0
        if pos.entry_price > 0:
            profit_pct = (close - pos.entry_price) / pos.entry_price
            pos.max_profit_pct = max(pos.max_profit_pct, profit_pct * 100)
            pos.drawdown_pct   = pos.max_profit_pct - profit_pct * 100

        pos.holding_days += 1

        # ① 드로우다운 하드 한도
        if pos.drawdown_pct >= self._drawdown_exit_pct:
            logger.info(f"[HOLDING] {pos.stock_code} EXIT — 드로우다운 {pos.drawdown_pct:.1f}%")
            return 'EXIT', 'DRAWDOWN_STOP'

        # ② 최대 보유일
        if pos.holding_days >= self._max_hold_days:
            logger.info(f"[HOLDING] {pos.stock_code} EXIT — 최대 보유일 {self._max_hold_days}일")
            return 'EXIT', 'TIME_EXIT'

        # ③ MA20 하드 스톱
        if self._ma20_hard_exit and not pd.isna(ma20) and ma20 > 0 and close < ma20:
            logger.info(f"[HOLDING] {pos.stock_code} EXIT — MA20 이탈 (close={close:.0f} ma20={ma20:.0f})")
            return 'EXIT', 'MA20_EXIT'

        # ④ REDUCE: 수익 충분 + 드로우다운 경고 + 약세 확인 (MA5 이탈)
        # score_drift <= -1 이면 dd_warn 임계값 1%p 낮춤 (더 빨리 줄임)
        reduce_dd_warn = self._reduce_dd_warn
        if score_drift <= -1:
            reduce_dd_warn = max(0.5, reduce_dd_warn - 1.0)
            logger.debug(
                f"[HOLDING] {pos.stock_code} REDUCE 임계 완화 "
                f"(score_drift={score_drift:+.1f} → dd_warn {self._reduce_dd_warn}→{reduce_dd_warn}%)"
            )
        if (profit_pct >= self._reduce_min_profit
                and pos.drawdown_pct >= reduce_dd_warn
                and close < ma5
                and pos.add_count == 0):
            logger.info(
                f"[HOLDING] {pos.stock_code} REDUCE — "
                f"profit={profit_pct*100:.1f}% drawdown={pos.drawdown_pct:.1f}% "
                f"MA5이격={pos.ma5_distance_pct:+.1f}% score_drift={score_drift:+.1f}"
            )
            return 'REDUCE', 'PARTIAL_REDUCE'

        # ⑤ MA5 연속 이탈 추적
        if close < ma5:
            pos.ma5_below_days += 1
        else:
            pos.ma5_below_days = 0

        if pos.ma5_below_days >= self._ma5_consecutive:
            logger.info(
                f"[HOLDING] {pos.stock_code} EXIT — MA5 {self._ma5_consecutive}일 연속 이탈"
            )
            return 'EXIT', 'MA5_EXIT'

        # ⑥ ADD 판단 (횟수 여유 + 보유 기간 충족)
        if pos.add_count < self.MAX_ADD_COUNT and pos.holding_days >= self.ADD_MIN_DAYS:
            add_action = self._eval_add(pos, df_daily, close, open_, prev_close, ma5, profit_pct, score_drift)
            if add_action:
                return add_action, add_action

        # ⑦ TRAIL
        if (pos.holding_days >= self._trail_start_days
                and pos.max_profit_pct >= self._trail_start_profit):
            logger.info(
                f"[HOLDING] {pos.stock_code} TRAIL — "
                f"days={pos.holding_days} max_profit={pos.max_profit_pct:.1f}%"
            )
            return 'TRAIL', 'TRAIL'

        return 'HOLD', 'HOLD'

    def _eval_add(
        self,
        pos: SwingPosition,
        df: pd.DataFrame,
        close: float,
        open_: float,
        prev_close: float,
        ma5: float,
        profit_pct: float,
        score_drift: float = 0.0,
    ) -> str | None:
        """ADD_VALUE / ADD_MOMENTUM 조건 판단. 해당 없으면 None."""

        # ── ADD_VALUE: 눌림 평균단가 개선 ─────────────────────────────────────
        # 현재가가 평균단가 -0.5% 이하 + MA5 ±1% 근접 + 양봉 + 전일比 상승
        near_ma5  = abs(pos.ma5_distance_pct) <= 1.0
        is_bull   = close > open_
        is_rising = close > prev_close
        below_avg = (pos.entry_price > 0
                     and (close - pos.entry_price) / pos.entry_price <= self.VALUE_ADD_MAX_ABOVE_AVG)

        if near_ma5 and is_bull and is_rising and below_avg:
            logger.info(
                f"[HOLDING] {pos.stock_code} ADD_VALUE — "
                f"MA5이격={pos.ma5_distance_pct:+.1f}% "
                f"단가대비={(close/pos.entry_price-1)*100:+.1f}%"
            )
            return 'ADD_VALUE'

        # ── ADD_MOMENTUM: 수익 중 피라미딩 ────────────────────────────────────
        # score_drift >= +2 → 수익 임계값 완화 (3% → 2.1%), 거래량 배율 완화 (1.3x → 1.1x)
        momentum_profit_thresh = self.MOMENTUM_ADD_MIN_PROFIT
        momentum_vol_mult = self.MOMENTUM_ADD_VOL_MULT
        if score_drift >= 2:
            momentum_profit_thresh = momentum_profit_thresh * 0.7
            momentum_vol_mult = max(1.0, momentum_vol_mult - 0.2)
            logger.debug(
                f"[HOLDING] {pos.stock_code} ADD_MOMENTUM 기준 완화 "
                f"(score_drift={score_drift:+.1f} → profit≥{momentum_profit_thresh*100:.1f}% vol≥{momentum_vol_mult:.1f}x)"
            )

        if profit_pct >= momentum_profit_thresh:
            recent_high = float(df['high'].iloc[-6:-1].max()) if len(df) >= 6 else 0
            is_new_high = close > recent_high if recent_high > 0 else False
            vol_now     = float(df['volume'].iloc[-1])
            vol_avg5    = float(df['volume'].rolling(5).mean().iloc[-2])
            vol_surge   = vol_avg5 > 0 and vol_now >= vol_avg5 * momentum_vol_mult

            # 과열 필터: MA20 대비 3 ATR 이상 이격 시 추격 금지
            atr14 = float((df['high'] - df['low']).tail(14).mean()) if len(df) >= 14 else 0
            ma20_val = float(df['close'].rolling(20).mean().iloc[-1])
            overextended = (atr14 > 0 and ma20_val > 0
                            and (close - ma20_val) / atr14 > 3.0)

            if overextended:
                logger.info(
                    f"[HOLDING] {pos.stock_code} ADD_MOMENTUM 차단 — "
                    f"MA20 대비 {(close-ma20_val)/atr14:.1f}ATR 이격 (과열)"
                )
            elif is_new_high and vol_surge:
                logger.info(
                    f"[HOLDING] {pos.stock_code} ADD_MOMENTUM — "
                    f"profit={profit_pct*100:.1f}% new_high={close:.0f}>{recent_high:.0f} "
                    f"vol={vol_now/vol_avg5:.1f}x ext={(close-ma20_val)/atr14:.1f}ATR"
                )
                return 'ADD_MOMENTUM'

        return None

    def add_lot_size(self, pos: SwingPosition, action: str) -> float:
        """추가매수 비중 결정."""
        if action == 'ADD_MOMENTUM':
            # 피라미딩은 소량 (1차 20%, 2차 10%)
            return 0.20 if pos.add_count == 0 else 0.10
        # VALUE ADD (1차 30%, 2차 20%)
        return 0.30 if pos.add_count == 0 else 0.20

    def reduce_lot_size(self, profit_pct: float = 0.0) -> float:
        """수익 구간별 부분 청산 비중 — 강한 종목은 늦게, 약한 종목은 빠르게."""
        if profit_pct >= 30:
            return 0.70
        if profit_pct >= 20:
            return 0.50
        if profit_pct >= 10:
            return 0.30
        return 0.30   # 기본값 (REDUCE_MIN_PROFIT 아슬아슬한 구간)
