"""
SMC 어댑터 — 일봉 백테스트용 독립 구현.

핵심 로직: Bullish CHoCH + 선행 Sweep → BUY 신호

note: smc_structure.py는 실시간 증분 업데이트용으로 설계됨.
      일봉 배치 백테스트에는 독립 구현이 더 정확.
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backtest.fitness import RollingFitnessTracker

logger = logging.getLogger(__name__)

MIN_BARS = 20  # 최소 lookback 봉 수


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _find_pivots(df: pd.DataFrame, lb: int) -> tuple[list, list]:
    """
    단순 피벗 고점/저점 탐지.
    Returns: (swing_highs, swing_lows) — 각 원소는 (index, price)
    """
    highs, lows = [], []
    closes = df['close'].values
    high_v = df['high'].values
    low_v  = df['low'].values
    n = len(closes)

    for i in range(lb, n - lb):
        if all(high_v[i] >= high_v[i-j] for j in range(1, lb+1)) and \
           all(high_v[i] >= high_v[i+j] for j in range(1, lb+1)):
            highs.append((i, float(high_v[i])))

        if all(low_v[i] <= low_v[i-j] for j in range(1, lb+1)) and \
           all(low_v[i] <= low_v[i+j] for j in range(1, lb+1)):
            lows.append((i, float(low_v[i])))

    return highs, lows


def _is_bearish_structure(highs: list, lows: list) -> tuple[bool, Optional[float]]:
    """
    하락 구조 (LH + LL) 확인.
    Returns: (is_bearish, last_lh_price)
    """
    if len(highs) < 2 or len(lows) < 2:
        return False, None

    # 최근 2개 고점 비교 → LH (Lower High)
    lh = highs[-1][1] < highs[-2][1]
    # 최근 2개 저점 비교 → LL (Lower Low)
    ll = lows[-1][1] < lows[-2][1]

    if lh and ll:
        return True, highs[-1][1]
    return False, None


def _has_sweep(df: pd.DataFrame, lows: list, before_idx: int, lb: int) -> bool:
    """
    CHoCH 이전에 swing low sweep 발생 여부.
    wick이 swing low 아래로 돌파 후 종가는 위로 복귀.
    """
    if not lows:
        return False

    recent_lows = [(i, p) for i, p in lows if i < before_idx]
    if not recent_lows:
        return False

    sweep_level = recent_lows[-1][1]
    # CHoCH 이전 최근 lb봉 내에서 sweep 탐지
    start = max(0, before_idx - lb)
    for j in range(start, before_idx):
        row = df.iloc[j]
        if row['low'] < sweep_level and row['close'] > sweep_level:
            return True
    return False


# ── 메인 어댑터 ───────────────────────────────────────────────────────────────

class SMCAdapter:
    """
    일봉 bar-by-bar SMC 신호 생성기.

    config 파라미터:
        swing_lookback: 피벗 탐지 좌우 봉 수 (기본 3)
        sweep_lookback: sweep 탐지 범위 봉 수 (기본 15)
        window_size:    구조 분석 lookback 봉 수 (기본 60)

    진입 타이밍 필터 (MAE 개선):
        ema_extension_limit:  EMA20 대비 최대 이격 (예: 0.02 = 2% 이내)
        breakout_dist_limit:  CHoCH 돌파 수준 대비 최대 추격 거리 (예: 0.015)
        require_pullback:     최근 2봉 내 눌림 확인 (꼭대기 추격 방지)
        atr_pct_limit:        ATR(14) / close 최대 비율 (예: 0.06 = 6%)

    종목 적합성 필터 (레짐/종목 선택):
        atr_pct_min:          ATR% 하한 — 너무 조용한 종목 제외 (예: 0.02)
        atr_pct_max:          ATR% 상한 — 에코프로형 고변동성 제외 (예: 0.08)
        require_ma50_trend:   MA50 우상향 + 종가 > MA50 필터 (하락추세 제거)
        ma50_slope_bars:      MA50 기울기 판단 기간 (기본 10봉)
    """

    def __init__(
        self,
        config: dict = None,
        require_sweep:    bool  = True,
        require_htf:      bool  = False,   # EMA20 상승 필터
        require_volume:   bool  = False,   # 거래량 1.5x 필터
        require_position: bool  = False,   # 하단 40% 위치 필터
        require_ema60:    bool  = False,   # EMA60 상승 구간 필터
        # ── 진입 타이밍 필터 ───────────────────────────────────────────────
        ema_extension_limit:  Optional[float] = None,
        breakout_dist_limit:  Optional[float] = None,
        require_pullback:     bool  = False,
        atr_pct_limit:        Optional[float] = None,  # (레거시, atr_pct_max와 동일)
        # ── 종목 적합성 필터 ───────────────────────────────────────────────
        atr_pct_min:          Optional[float] = None,  # ATR% 하한 (기본 비활성)
        atr_pct_max:          Optional[float] = None,  # ATR% 상한 (기본 비활성)
        require_ma50_trend:   bool  = False,           # MA50 우상향 필터
        ma50_slope_bars:      int   = 10,              # MA50 기울기 판단 기간
        # ── Fitness Tracker ────────────────────────────────────────────────
        fitness_tracker  = None,                       # RollingFitnessTracker 인스턴스
        symbol:           str   = '',                  # 현재 종목 코드
    ):
        cfg = config or {}
        self.lb               = cfg.get('swing_lookback', 3)
        self.sweep_lb         = cfg.get('sweep_lookback', 15)
        self.window           = cfg.get('window_size', 60)
        self.require_sweep    = require_sweep
        self.require_htf      = require_htf
        self.require_volume   = require_volume
        self.require_position = require_position
        self.require_ema60    = require_ema60
        self.ema_extension_limit = ema_extension_limit
        self.breakout_dist_limit = breakout_dist_limit
        self.require_pullback    = require_pullback
        self.atr_pct_limit       = atr_pct_limit   # 레거시
        self.atr_pct_min         = atr_pct_min
        self.atr_pct_max         = atr_pct_max or atr_pct_limit  # 호환
        self.require_ma50_trend  = require_ma50_trend
        self.ma50_slope_bars     = ma50_slope_bars
        self.fitness_tracker     = fitness_tracker
        self.symbol              = symbol

    def get_signal(self, df: pd.DataFrame, i: int) -> str | None:
        """
        i번째 봉 기준 신호 반환 (i봉은 아직 진행중 → i-1까지 확정봉).

        Returns: 'BUY' | None
        """
        if i < MIN_BARS + self.lb:
            return None

        # 확정봉 window (bar i는 아직 미확정)
        w = df.iloc[max(0, i - self.window): i].copy()
        if len(w) < MIN_BARS:
            return None

        last_confirmed = i - 1   # 분석 window 내 마지막 봉 = w.iloc[-1]

        try:
            highs, lows = _find_pivots(w, self.lb)
            if not highs or not lows:
                return None

            is_bear, last_lh = _is_bearish_structure(highs, lows)
            if not is_bear or last_lh is None:
                return None

            # Bullish CHoCH: 마지막 확정봉 종가가 last_lh 상향 돌파
            last_row = w.iloc[-1]
            if not (last_row['close'] > last_lh and last_row['high'] > last_lh):
                return None

            # body 50% 이상 (꼬리 돌파 차단)
            c_range = last_row['high'] - last_row['low']
            c_body  = abs(last_row['close'] - last_row['open'])
            if c_range > 0 and (c_body / c_range) < 0.5:
                return None

            # Sweep 필터 (A전략)
            if self.require_sweep:
                if not _has_sweep(w, lows, before_idx=len(w) - 1, lb=self.sweep_lb):
                    return None

            # HTF 필터: close > EMA20 AND EMA20 상승 중
            if self.require_htf:
                closes = w['close']
                if len(closes) < 21:
                    return None
                ema20 = closes.ewm(span=20, adjust=False).mean()
                if not (last_row['close'] > ema20.iloc[-1] and ema20.iloc[-1] > ema20.iloc[-3]):
                    return None

            # 거래량 필터: 진입봉 volume >= 20봉 평균 * 1.5
            if self.require_volume:
                vols = w['volume']
                if len(vols) < 21:
                    return None
                vol_avg = vols.iloc[-21:-1].mean()
                if vol_avg > 0 and last_row['volume'] < vol_avg * 1.5:
                    return None

            # 위치 필터: 최근 20봉 기준 하단 40% 구간
            if self.require_position:
                recent = w.iloc[-20:]
                r_high = recent['high'].max()
                r_low  = recent['low'].min()
                r_range = r_high - r_low
                if r_range > 0:
                    pos = (last_row['close'] - r_low) / r_range
                    if pos > 0.4:
                        return None

            # EMA60 상승 필터: close > EMA60 AND EMA60 우상향 (전체 df 기준)
            if self.require_ema60:
                full_closes = df['close'].iloc[:i]
                if len(full_closes) < 65:
                    return None
                ema60 = full_closes.ewm(span=60, adjust=False).mean()
                if not (last_row['close'] > ema60.iloc[-1] and ema60.iloc[-1] > ema60.iloc[-5]):
                    return None

            # ── 진입 타이밍 필터 ─────────────────────────────────────────────

            # (A) EMA20 이격 제한: EMA20 대비 X% 이상 위면 이미 늦음
            if self.ema_extension_limit is not None:
                closes = w['close']
                if len(closes) >= 21:
                    ema20 = closes.ewm(span=20, adjust=False).mean().iloc[-1]
                    if ema20 > 0:
                        extension = (last_row['close'] - ema20) / ema20
                        if extension > self.ema_extension_limit:
                            return None

            # (B) CHoCH 돌파 추격 거리 제한: last_lh 대비 X% 이상 위면 추격
            if self.breakout_dist_limit is not None and last_lh is not None:
                dist = (last_row['close'] - last_lh) / last_lh
                if dist > self.breakout_dist_limit:
                    return None

            # (D) 최근 눌림 확인: 진입봉 직전 2봉 내 하락봉 또는 위꼬리 존재
            if self.require_pullback:
                if len(w) >= 3:
                    prev1 = w.iloc[-2]
                    prev2 = w.iloc[-3]
                    # 조건: 전전봉 또는 전봉에서 눌림 발생
                    # 눌림 = 종가 < 시가(하락봉) OR (고가-종가)/고가 >= 2%(위꼬리)
                    dip1 = prev1['close'] < prev1['open']
                    dip2 = prev2['close'] < prev2['open']
                    wick1 = (prev1['high'] - prev1['close']) / prev1['high'] >= 0.02 if prev1['high'] > 0 else False
                    if not (dip1 or dip2 or wick1):
                        return None

            # (E) ATR% 변동성 필터 — 공통 ATR 계산 (min/max 모두 사용)
            atr_pct_active = (self.atr_pct_max is not None or self.atr_pct_min is not None)
            if atr_pct_active and len(w) >= 15:
                highs_v  = w['high'].values[-15:]
                lows_v   = w['low'].values[-15:]
                closes_v = w['close'].values[-15:]
                tr = np.maximum(
                    highs_v[1:] - lows_v[1:],
                    np.maximum(
                        np.abs(highs_v[1:] - closes_v[:-1]),
                        np.abs(lows_v[1:] - closes_v[:-1]),
                    )
                )
                atr_pct = float(np.mean(tr)) / last_row['close'] if last_row['close'] > 0 else 0.0
                if self.atr_pct_max is not None and atr_pct > self.atr_pct_max:
                    return None   # 고변동성 종목 (에코프로형) 제거
                if self.atr_pct_min is not None and atr_pct < self.atr_pct_min:
                    return None   # 너무 조용한 종목 제거

            # ── 종목 적합성 필터 ─────────────────────────────────────────────

            # MA50 추세 필터: 종가 > MA50 AND MA50 기울기 양수
            if self.require_ma50_trend:
                full_closes = df['close'].iloc[:i]
                if len(full_closes) < 55:
                    return None
                ma50 = full_closes.rolling(50).mean()
                ma50_now  = float(ma50.iloc[-1])
                ma50_prev = float(ma50.iloc[-self.ma50_slope_bars - 1])
                if ma50_now != ma50_now or ma50_prev != ma50_prev:  # NaN 체크
                    return None
                if not (last_row['close'] > ma50_now and ma50_now > ma50_prev):
                    return None   # 하락추세 또는 MA50 아래

            # ── Fitness Score 필터 ────────────────────────────────────────────
            if self.fitness_tracker is not None and self.symbol:
                if not self.fitness_tracker.is_qualified(self.symbol):
                    return None   # EXCLUDED 종목 — 누적 MFE/MAE 비율 불량

            return 'BUY'

        except Exception as e:
            logger.debug(f'[ADAPTER] bar {i} 오류: {e}')

        return None
