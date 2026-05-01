"""
백테스트 엔진 — bar-by-bar 시뮬레이션.

청산 모드:
  tp_sl  (기본) : 고정 TP/SL %
  swing         : 최소보유 + trailing stop + BE 전환 + MFE 추적
"""
import pandas as pd
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    symbol:       str
    entry_date:   str
    exit_date:    str
    entry_price:  float
    exit_price:   float
    pnl_pct:      float        # 수익률 (소수점, 예: 0.03 = +3%)
    pnl_won:      float        # 수익금 (주당)
    exit_reason:  str          # 'TP'|'SL'|'TRAIL'|'BE_STOP'|'MAX_HOLD'
    hold_bars:    int
    mfe_pct:      float = 0.0  # Max Favorable Excursion (최대 유리 이동, %)
    mae_pct:      float = 0.0  # Max Adverse Excursion (최대 불리 이동, %)


@dataclass
class BacktestResult:
    symbol:  str
    trades:  list[Trade] = field(default_factory=list)


class BacktestEngine:
    """
    단일 종목 bar-by-bar 백테스트.

    Args:
        tp_pct:          익절 목표 % (None이면 TP 없음 — 스윙 트레일링만 사용)
        sl_pct:          손절 라인 % (음수, 기본 -0.02)
        min_hold_bars:   최소 보유 봉 수 (스윙 모드)
        trailing_pct:    트레일링 스탑 활성화 수익률 % (스윙 모드)
        be_trigger_pct:  BE 스탑 전환 수익률 % (스윙 모드, trailing_pct 이하)
        max_hold_bars:   최대 보유 봉 수 (None=무제한)
        swing_mode:      True → 스윙 청산 모드
        signal_func:     (df, i) → 'BUY' | None
        commission:      편도 수수료+세금+슬리피지 합산
        sl_atr_mult:     ATR 기반 SL 배수 (설정 시 sl_pct 대신 ATR×mult 사용)
        trail_atr_mult:  ATR 기반 trailing stop 배수 (설정 시 고정 % 대신 사용)
        atr_period:      ATR 계산 기간 (기본 14봉)
    """

    def __init__(
        self,
        tp_pct:          Optional[float] = 0.03,
        sl_pct:          float           = -0.02,
        min_hold_bars:   int             = 0,
        trailing_pct:    float           = 0.03,    # +X% 달성 시 trailing 시작
        be_trigger_pct:  float           = 0.02,    # +X% 달성 시 BE 전환
        max_hold_bars:   Optional[int]   = None,
        swing_mode:      bool            = False,
        signal_func                      = None,
        commission:      float           = 0.0035,
        sl_atr_mult:     Optional[float] = None,    # ATR SL (설정 시 sl_pct 대체)
        trail_atr_mult:  Optional[float] = None,    # ATR trailing (설정 시 trailing_pct % 대체)
        atr_period:      int             = 14,
        on_trade_complete = None,                   # callable(symbol, trade) — fitness tracker 연동용
    ):
        self.tp_pct          = tp_pct
        self.sl_pct          = sl_pct
        self.min_hold_bars   = min_hold_bars
        self.trailing_pct    = trailing_pct
        self.be_trigger_pct  = be_trigger_pct
        self.max_hold_bars   = max_hold_bars
        self.swing_mode      = swing_mode
        self.signal_func     = signal_func
        self.commission      = commission
        self.sl_atr_mult        = sl_atr_mult
        self.trail_atr_mult     = trail_atr_mult
        self.atr_period         = atr_period
        self.on_trade_complete  = on_trade_complete

    def run(self, df: pd.DataFrame, symbol: str = '') -> BacktestResult:
        result   = BacktestResult(symbol=symbol)
        position: Optional[dict] = None

        dates = df.index.strftime('%Y-%m-%d').tolist() if hasattr(df.index, 'strftime') else list(range(len(df)))

        for i in range(len(df)):
            row   = df.iloc[i]
            close = float(row['close'])
            high  = float(row.get('high', close))
            low   = float(row.get('low',  close))

            if position is not None and i > position['entry_i']:
                ep      = position['entry_price']
                bars    = i - position['entry_i']
                chg     = (close - ep) / ep

                # MFE/MAE 갱신
                position['mfe'] = max(position['mfe'], (high - ep) / ep)
                position['mae'] = min(position['mae'], (low  - ep) / ep)

                exit_reason = None
                exit_price  = close

                if self.swing_mode:
                    exit_reason, exit_price = self._check_swing_exit(
                        position, close, high, low, chg, bars, ep
                    )
                else:
                    # ── TP/SL 모드 ──────────────────────────────────────
                    min_ok = bars >= self.min_hold_bars
                    if self.tp_pct is not None and chg >= self.tp_pct and min_ok:
                        exit_reason = 'TP'
                        exit_price  = ep * (1 + self.tp_pct)
                    elif chg <= self.sl_pct:
                        exit_reason = 'SL'
                        exit_price  = ep * (1 + self.sl_pct)

                if exit_reason:
                    pnl = (exit_price - ep) / ep - self.commission * 2
                    trade = Trade(
                        symbol      = symbol,
                        entry_date  = position['entry_date'],
                        exit_date   = dates[i],
                        entry_price = ep,
                        exit_price  = round(exit_price, 0),
                        pnl_pct     = round(pnl, 4),
                        pnl_won     = round(exit_price - ep, 0),
                        exit_reason = exit_reason,
                        hold_bars   = bars,
                        mfe_pct     = round(position['mfe'] * 100, 2),
                        mae_pct     = round(position['mae'] * 100, 2),
                    )
                    result.trades.append(trade)
                    if self.on_trade_complete is not None:
                        self.on_trade_complete(symbol, trade)
                    position = None
                    continue

            # ── 진입 신호 (포지션 없을 때) ────────────────────────────────────
            if position is None and self.signal_func is not None:
                signal = self.signal_func(df, i)
                if signal == 'BUY' and i + 1 < len(df):
                    entry_price = float(df.iloc[i + 1]['open'])
                    atr_val = self._calc_atr(df, i)   # 신호봉 기준 ATR
                    position = {
                        'entry_i':     i + 1,
                        'entry_price': entry_price,
                        'entry_date':  dates[i + 1],
                        'mfe':         0.0,
                        'mae':         0.0,
                        'peak_price':  entry_price,   # trailing용 고점
                        'be_raised':   False,
                        'trail_active': False,
                        'atr':         atr_val,       # ATR SL/trailing용
                    }
                    logger.debug(f'[ENGINE] {symbol} BUY @ {entry_price:.0f} ({dates[i+1]})')

        return result

    def _calc_atr(self, df: pd.DataFrame, i: int) -> float:
        """ATR(atr_period) — bar i 기준."""
        p   = self.atr_period
        end = i
        start = max(0, i - p - 1)
        sub = df.iloc[start:end]
        if len(sub) < 2:
            return float(df.iloc[i]['high'] - df.iloc[i]['low'])
        h = sub['high'].values
        l = sub['low'].values
        c = sub['close'].values
        tr = np.maximum(h[1:] - l[1:],
                        np.maximum(np.abs(h[1:] - c[:-1]),
                                   np.abs(l[1:] - c[:-1])))
        return float(np.mean(tr[-p:]))

    def _sl_price(self, position: dict) -> float:
        """실효 SL 가격 계산."""
        ep  = position['entry_price']
        atr = position.get('atr', 0.0)
        if self.sl_atr_mult is not None and atr > 0:
            return ep - atr * self.sl_atr_mult
        return ep * (1 + self.sl_pct)

    def _trail_stop_price(self, position: dict) -> float:
        """실효 trailing stop 가격 계산 (peak 기준)."""
        peak = position['peak_price']
        atr  = position.get('atr', 0.0)
        if self.trail_atr_mult is not None and atr > 0:
            return peak - atr * self.trail_atr_mult
        return peak * (1 - self.trailing_pct)   # 기존 방식 (trailing_pct = 고점 대비 하락 %)

    def _check_swing_exit(
        self, position: dict, close: float, high: float, low: float,
        chg: float, bars: int, ep: float,
    ) -> tuple[Optional[str], float]:
        """스윙 모드 청산 로직."""
        # 고점 갱신 (trailing용)
        if high > position['peak_price']:
            position['peak_price'] = high

        peak     = position['peak_price']
        sl_price = self._sl_price(position)

        # 1. 최대 보유봉 초과 → 청산
        if self.max_hold_bars and bars >= self.max_hold_bars:
            return 'MAX_HOLD', close

        # 2. 최소 보유 이전은 SL만 허용
        if bars < self.min_hold_bars:
            if close <= sl_price:
                return 'SL', sl_price
            return None, close

        # 3. BE 전환 (be_trigger_pct 달성 시)
        if chg >= self.be_trigger_pct and not position['be_raised']:
            position['be_raised']  = True
            position['stop_price'] = ep   # BE = 진입가

        # 4. Trailing 활성화 (trailing_pct 달성 시)
        if chg >= self.trailing_pct:
            position['trail_active'] = True

        # 5. TP (설정 시 최대 수익 한도)
        if self.tp_pct is not None and chg >= self.tp_pct:
            return 'TP', ep * (1 + self.tp_pct)

        # 6. Trailing stop 발동 (ATR 또는 고정 % 기반)
        if position['trail_active']:
            ts = self._trail_stop_price(position)
            if close <= ts:
                return 'TRAIL', close

        # 7. BE stop (진입가 밑으로)
        if position.get('be_raised'):
            stop = position.get('stop_price', ep)
            if close <= stop:
                return 'BE_STOP', stop

        # 8. Hard SL (최소보유 이후에도)
        if close <= sl_price:
            return 'SL', sl_price

        return None, close
