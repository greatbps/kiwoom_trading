"""
시장 레짐 자동 분류 (Regime Detector)

3가지 레짐:
    BULL — EMA 정배열 + HH/HL 구조
    BEAR — EMA 역배열 + LH/LL 구조
    SIDE — 명확한 방향 없음 (횡보)

사용:
    from core.regime_detector import RegimeDetector, classify_regime_from_df

    detector = RegimeDetector(api)
    regime, reason = detector.get_regime()   # 캐시 포함
    # → "BULL" | "BEAR" | "SIDE"

weekly_tuner.py가 데이터 분류 시:
    from core.regime_detector import label_regime_series
    df["regime"] = label_regime_series(df)   # 봉별 레짐 레이블
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import numpy as np
import pandas as pd


# ── 파라미터 ──────────────────────────────────────────────────────────

EMA_FAST        = 20
EMA_SLOW        = 60
SWING_LOOKBACK  = 5     # HH/HL / LH/LL 판별 스윙 범위
MIN_TREND_SCORE = 2     # 3개 기준 중 몇 개 충족 시 BULL/BEAR 확정


# ── 핵심 로직 ─────────────────────────────────────────────────────────

def classify_regime_from_df(df: pd.DataFrame) -> Tuple[str, str]:
    """DataFrame에서 레짐 판별

    3개 기준:
      1. EMA20 vs EMA60 위치
      2. 최근 스윙 구조 (HH/HL or LH/LL)
      3. 가격이 EMA20 위/아래

    Returns:
        (regime: "BULL"|"BEAR"|"SIDE", reason: str)
    """
    if df is None or len(df) < EMA_SLOW + 5:
        return "SIDE", "데이터 부족"

    try:
        df = df.copy()
        df["ema20"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        df["ema60"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

        close  = float(df["close"].iloc[-1])
        ema20  = float(df["ema20"].iloc[-1])
        ema60  = float(df["ema60"].iloc[-1])

        # ① EMA 위치
        bull_score, bear_score = 0, 0
        reasons = []

        if ema20 > ema60:
            bull_score += 1
            reasons.append("EMA정배열")
        elif ema20 < ema60:
            bear_score += 1
            reasons.append("EMA역배열")

        # ② 가격 vs EMA20
        if close > ema20:
            bull_score += 1
            reasons.append("가격>EMA20")
        else:
            bear_score += 1
            reasons.append("가격<EMA20")

        # ③ 스윙 구조 (HH/HL vs LH/LL)
        swing_regime = _check_swing_structure(df, SWING_LOOKBACK)
        if swing_regime == "BULL":
            bull_score += 1
            reasons.append("HH/HL구조")
        elif swing_regime == "BEAR":
            bear_score += 1
            reasons.append("LH/LL구조")
        else:
            reasons.append("횡보구조")

        # 결정
        if bull_score >= MIN_TREND_SCORE:
            return "BULL", " | ".join(reasons)
        elif bear_score >= MIN_TREND_SCORE:
            return "BEAR", " | ".join(reasons)
        else:
            return "SIDE", " | ".join(reasons)

    except Exception as e:
        return "SIDE", f"판별 오류: {e}"


def _check_swing_structure(df: pd.DataFrame, lookback: int = 5) -> str:
    """최근 스윙 고점/저점 구조로 추세 방향 판별"""
    try:
        closes = df["close"].values
        if len(closes) < lookback * 3:
            return "SIDE"

        # 간단 스윙: N봉 단위 분할 후 고점/저점 비교
        n = lookback
        c1 = closes[-(n*3):-n*2]
        c2 = closes[-(n*2):-n]
        c3 = closes[-n:]

        h1, l1 = c1.max(), c1.min()
        h2, l2 = c2.max(), c2.min()
        h3, l3 = c3.max(), c3.min()

        # HH + HL → BULL
        if h3 > h2 > h1 and l3 > l2 > l1:
            return "BULL"
        # LH + LL → BEAR
        if h3 < h2 < h1 and l3 < l2 < l1:
            return "BEAR"
        return "SIDE"
    except Exception:
        return "SIDE"


def label_regime_series(df: pd.DataFrame, window: int = 60) -> pd.Series:
    """DataFrame 각 봉에 레짐 레이블 부여 (rolling window 기반)

    weekly_tuner.py가 데이터 분류 시 사용.

    Args:
        df: OHLCV DataFrame (DatetimeIndex)
        window: 레짐 판별에 사용할 룩백 봉 수

    Returns:
        pd.Series of "BULL" | "BEAR" | "SIDE"
    """
    labels = []
    for i in range(len(df)):
        if i < window:
            labels.append("SIDE")
            continue
        slice_ = df.iloc[max(0, i - window): i + 1]
        regime, _ = classify_regime_from_df(slice_)
        labels.append(regime)
    return pd.Series(labels, index=df.index, name="regime")


# ── RegimeDetector 클래스 (캐시 포함) ─────────────────────────────────

class RegimeDetector:
    """캐시 포함 레짐 감지기 (main_auto_trading.py 용)"""

    def __init__(self, api=None, cache_minutes: int = 30):
        self.api            = api
        self.cache_minutes  = cache_minutes
        self._cache_regime: Optional[str] = None
        self._cache_reason: Optional[str] = None
        self._cache_time:   Optional[datetime] = None

    def get_regime(self, df: Optional[pd.DataFrame] = None) -> Tuple[str, str]:
        """현재 레짐 반환 (캐시 포함)

        Args:
            df: 외부에서 df를 직접 전달할 수 있음 (캐시 우선)
        """
        now = datetime.now()
        if (self._cache_time
                and (now - self._cache_time).seconds < self.cache_minutes * 60
                and self._cache_regime):
            return self._cache_regime, self._cache_reason

        if df is not None:
            regime, reason = classify_regime_from_df(df)
        elif self.api is not None:
            df = self._fetch_kosdaq_df()
            regime, reason = classify_regime_from_df(df) if df is not None else ("SIDE", "API 실패")
        else:
            return "SIDE", "API 없음"

        self._cache_regime = regime
        self._cache_reason = reason
        self._cache_time   = now
        return regime, reason

    def _fetch_kosdaq_df(self) -> Optional[pd.DataFrame]:
        """키움 API로 KODEX 코스닥150 30분봉 조회"""
        try:
            result = self.api.get_minute_chart(
                stock_code="229200",  # KODEX 코스닥150
                tic_scope="30",
                cnt=100,
            )
            if result and "output2" in result:
                rows = result["output2"]
                df = pd.DataFrame(rows)
                col_map = {
                    "stck_bsop_date": "date", "stck_cntg_hour": "time",
                    "stck_oprc": "open", "stck_hgpr": "high",
                    "stck_lwpr": "low",  "stck_clpr": "close",
                    "cntg_vol": "volume",
                }
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                return df.dropna(subset=["close"]).reset_index(drop=True)
        except Exception:
            pass
        return None

    def reset_cache(self):
        self._cache_regime = None
        self._cache_time   = None
