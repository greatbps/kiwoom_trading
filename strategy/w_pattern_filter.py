"""
strategy/w_pattern_filter.py — W Pattern Pullback Entry Filter (실험적, v1.5.0)

기존 SMC 기반 Entry를 더 정교하게 거르기 위한 추가 Entry Quality Filter.
진입 횟수 증가가 목적이 아니라 진입 품질 향상이 목적 — 기본 config는 enabled=false.

Score Engine / SMC Logic / CHoCH·BOS Detection / Regime Gate / Exit Logic /
Position Sizing은 일체 건드리지 않는다. CHoCH·BOS 판정 결과는 이미 계산된
값을 파라미터로 전달받아 사용할 뿐, 이 모듈 내에서 재계산하지 않는다.

패턴 시퀀스 (STEP1-9):
  1. 최근 5봉 급락 (<= min_drop_pct) AND RVOL >= min_rvol
  2. 첫 번째 저점(L1) — 기존 find_swing_points() 재사용
  3. L1 대비 반등 >= min_rebound_pct
  4. 두 번째 저점(L2) — L2 < L1*(1-max_bottom_diff_pct%) 면 실패 (L2_BREAK)
  5. (옵션) 거래량 감소: Volume(L2) < Volume(L1)
  6. CHoCH — 호출자가 전달한 기존 판정 결과 사용 (하드 게이트)
  7. BOS — 호출자가 전달한 기존 판정 결과 사용, 신뢰도 가점 전용(하드 게이트 아님).
     analyzers/smc/smc_signals.py의 SMC 파이프라인은 CHoCH가 감지된 분기에서는
     BOS를 계산하지 않는다(상호 배타) — STEP6에서 이미 CHoCH를 필수로 요구하므로
     BOS를 AND 조건으로 걸면 필터가 영구히 통과 불가능해진다. 따라서 BOS는
     confidence 가점(20점)에만 반영하고 PASS/FAIL 판정에는 관여시키지 않는다.
  8. (옵션) VWAP: 현재가 > VWAP — 기존 VWAP 계산 재사용 (상태 없는 순수 변환이라
     여기서 재호출해도 "재사용 원칙"에 위배되지 않음, CHoCH/BOS와는 성격이 다름)
  9. (옵션) EMA20: 현재가 > EMA20 — trend_breakout.py의 기존 _ema() 헬퍼와 동일한
     패턴을 그대로 복제 (이 코드베이스는 공용 EMA 유틸이 없고 모듈별 소형 헬퍼가 관례)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

import pandas as pd

from analyzers.smc.smc_utils import SwingPoint, find_swing_points
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


@dataclass
class WPatternResult:
    detected: bool
    confidence: float       # 0~100
    reason: str              # 'PASS' | 'NO_DROP' | 'NO_REBOUND' | 'L2_BREAK' |
                              # 'VOLUME_NOT_DECLINED' | 'NO_CHOCH' | 'NO_VWAP' | 'NO_EMA20'
    l1_price: float = 0.0
    l2_price: float = 0.0
    rebound_pct: float = 0.0
    volume_ratio: float = 0.0   # Volume(L2) / Volume(L1)


def _fail(reason: str, l1: float = 0.0, l2: float = 0.0,
          rebound_pct: float = 0.0, volume_ratio: float = 0.0) -> WPatternResult:
    return WPatternResult(
        detected=False, confidence=0.0, reason=reason,
        l1_price=l1, l2_price=l2, rebound_pct=rebound_pct, volume_ratio=volume_ratio,
    )


class WPatternFilter:
    """W(눌림목) 패턴 Entry Quality 필터. config['w_pattern']을 읽는다."""

    def __init__(self, config: dict):
        cfg = (config or {}).get('w_pattern', {}) or {}
        self.enabled              = cfg.get('enabled', False)
        self.timeframe            = cfg.get('timeframe', '3m')
        self.min_drop_pct         = cfg.get('min_drop_pct', -3.0)
        self.max_bottom_diff_pct  = cfg.get('max_bottom_diff_pct', 0.3)
        self.min_rebound_pct      = cfg.get('min_rebound_pct', 1.5)
        self.min_rvol             = cfg.get('min_rvol', 2.0)
        self.require_volume_decline = cfg.get('require_volume_decline', True)
        self.require_vwap         = cfg.get('require_vwap', True)
        self.require_ema20        = cfg.get('require_ema20', True)

    def evaluate(
        self,
        df: pd.DataFrame,
        current_price: float,
        choch_detected: bool,
        bos_detected: bool = False,
        symbol: str = '',
    ) -> WPatternResult:
        """
        Args:
            df: OHLCV DataFrame (open/high/low/close/volume, 시간순 정렬)
            current_price: 현재가
            choch_detected: 기존 SMC 파이프라인이 이미 판정한 CHoCH 감지 여부
            bos_detected: 기존 SMC 파이프라인이 이미 판정한 BOS 감지 여부 (신뢰도 가점용)
            symbol: 로깅용 종목코드

        Returns:
            WPatternResult
        """
        if df is None or len(df) < 25:
            return _fail('NO_DROP')

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        swings: List[SwingPoint] = find_swing_points(df, lookback=5, min_swing_size_pct=0.0)
        lows = [s for s in swings if s.type == 'low']

        if len(lows) < 2:
            self._log_fail(symbol, 'NO_REBOUND')
            return _fail('NO_REBOUND')

        l1, l2 = lows[-2], lows[-1]

        # ── STEP1: L1 형성 직전 급락 + RVOL ─────────────────────────────
        drop_start_idx = max(0, l1.index - 5)
        drop_start_close = float(df['close'].iloc[drop_start_idx])
        drop_pct = (l1.price - drop_start_close) / drop_start_close * 100 if drop_start_close > 0 else 0.0

        rvol_window_start = max(0, l1.index - 20)
        avg_vol = float(df['volume'].iloc[rvol_window_start:l1.index].mean()) if l1.index > rvol_window_start else 0.0
        l1_vol = float(df['volume'].iloc[l1.index])
        rvol = l1_vol / avg_vol if avg_vol > 0 else 0.0

        if not (drop_pct <= self.min_drop_pct and rvol >= self.min_rvol):
            self._log_fail(symbol, 'NO_DROP')
            return _fail('NO_DROP', l1=l1.price)

        # ── STEP3: L1 → L2 사이 반등 ─────────────────────────────────────
        if l2.index > l1.index + 1:
            rebound_high = float(df['high'].iloc[l1.index + 1: l2.index].max())
        else:
            rebound_high = l1.price
        rebound_pct = (rebound_high - l1.price) / l1.price * 100 if l1.price > 0 else 0.0

        if rebound_pct < self.min_rebound_pct:
            self._log_fail(symbol, 'NO_REBOUND')
            return _fail('NO_REBOUND', l1=l1.price, l2=l2.price, rebound_pct=rebound_pct)

        # ── STEP4: L2 이탈 체크 ──────────────────────────────────────────
        l2_held = l2.price >= l1.price * (1 - self.max_bottom_diff_pct / 100)
        l2_vol = float(df['volume'].iloc[l2.index])
        volume_ratio = l2_vol / l1_vol if l1_vol > 0 else 0.0

        if not l2_held:
            self._log_fail(symbol, 'L2_BREAK')
            return _fail('L2_BREAK', l1=l1.price, l2=l2.price, rebound_pct=rebound_pct, volume_ratio=volume_ratio)

        # ── STEP5: 거래량 감소 (옵션) ─────────────────────────────────────
        volume_declined = l2_vol < l1_vol
        if self.require_volume_decline and not volume_declined:
            self._log_fail(symbol, 'VOLUME_NOT_DECLINED')
            return _fail('VOLUME_NOT_DECLINED', l1=l1.price, l2=l2.price,
                         rebound_pct=rebound_pct, volume_ratio=volume_ratio)

        # ── STEP6: CHoCH (하드 게이트, 기존 판정 결과 사용) ────────────────
        if not choch_detected:
            self._log_fail(symbol, 'NO_CHOCH')
            return _fail('NO_CHOCH', l1=l1.price, l2=l2.price,
                         rebound_pct=rebound_pct, volume_ratio=volume_ratio)

        # ── STEP8: VWAP (옵션, 신뢰도 가점은 항상 계산) ────────────────────
        vwap_df = EntryTimingAnalyzer().calculate_vwap(df.copy(), use_rolling=True, rolling_window=20)
        vwap_val = float(vwap_df['vwap'].iloc[-1]) if not vwap_df['vwap'].empty else None
        vwap_ok = vwap_val is not None and not pd.isna(vwap_val) and current_price > vwap_val

        if self.require_vwap and not vwap_ok:
            self._log_fail(symbol, 'NO_VWAP')
            return _fail('NO_VWAP', l1=l1.price, l2=l2.price,
                         rebound_pct=rebound_pct, volume_ratio=volume_ratio)

        # ── STEP9: EMA20 (옵션, 신뢰도 가점은 항상 계산) ───────────────────
        ema20_val = float(_ema(df['close'], 20).iloc[-1])
        ema20_ok = current_price > ema20_val

        if self.require_ema20 and not ema20_ok:
            self._log_fail(symbol, 'NO_EMA20')
            return _fail('NO_EMA20', l1=l1.price, l2=l2.price,
                         rebound_pct=rebound_pct, volume_ratio=volume_ratio)

        # ── PASS: Confidence 계산 ────────────────────────────────────────
        confidence = (
            40.0
            + (20.0 if choch_detected else 0.0)
            + (20.0 if bos_detected else 0.0)
            + (10.0 if vwap_ok else 0.0)
            + (10.0 if ema20_ok else 0.0)
        )

        logger.info(
            f"[W_PATTERN] L1={l1.price:.0f} L2={l2.price:.0f} "
            f"Rebound={rebound_pct:.1f}% VolumeRatio={volume_ratio:.2f} "
            f"Confidence={confidence:.0f} PASS"
        )

        return WPatternResult(
            detected=True, confidence=confidence, reason='PASS',
            l1_price=l1.price, l2_price=l2.price,
            rebound_pct=rebound_pct, volume_ratio=volume_ratio,
        )

    @staticmethod
    def _log_fail(symbol: str, reason: str) -> None:
        logger.info(f"[W_PATTERN] FAIL Reason={reason}" + (f" ({symbol})" if symbol else ""))
