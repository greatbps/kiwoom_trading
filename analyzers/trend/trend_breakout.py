"""
analyzers/trend/trend_breakout.py — Trend Breakout Strategy (v1.1)

전략 컨셉:
  "Sweep 없는 상승장 = Continuation 시장 → 눌림/돌파 매수"

진입 유형 2가지:
  1. BREAKOUT  : N봉 고점 돌파 + 거래량 급증 + EMA 정배열 + 주식 자체 트렌드
  2. PULLBACK  : EMA20 눌림 + EMA20 상승 중 + 거래량 수축→확장 + 추세 유지

등급:
  STRONG  → 100% size (거래량 2x+, 강한 캔들, EMA 정배열 모두)
  NORMAL  → 60% size  (기본 조건 충족)
  WEAK    → 40% size  (눌림 진입, 조건 일부)

v1.1 변경사항 (2026-03-21):
  - max_extension_pct: 5.0 → 2.5 (추격 매수 차단)
  - BREAKOUT: require_above_ema60 추가 (주식 자체 중기 추세 확인)
  - PULLBACK: EMA20 기울기 필터 (추세 끝 낙폭 진입 차단)
  - PULLBACK: prior_breakout 감지 로직 강화
  - trend_score 계산 (로깅 및 STRONG 등급 판단)

설계 원칙:
  - SMC와 독립 모듈 (import 없음)
  - 모든 파라미터 config에서 읽기
  - 신호 → (bool, reason, details) 반환

2026-03-21 최초 작성 / v1.1 당일 개선
"""

import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _candle_body_ratio(row: pd.Series) -> float:
    """캔들 몸통 비율 (0~1). 1 = 완전 양봉."""
    rng = row["high"] - row["low"]
    if rng <= 0:
        return 0.0
    return max(0.0, (row["close"] - row["open"]) / rng)


def _ema_slope(ema_series: pd.Series, lookback: int = 5) -> float:
    """EMA 기울기 — 최근 N봉 기준 변화율(%)."""
    if len(ema_series) < lookback + 1:
        return 0.0
    prev = float(ema_series.iloc[-lookback - 1])
    curr = float(ema_series.iloc[-1])
    return (curr - prev) / prev * 100 if prev > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# TrendBreakoutStrategy
# ─────────────────────────────────────────────────────────────────────────────

class TrendBreakoutStrategy:
    """
    상승 추세장 전용 진입 전략.
    SMC Sweep이 나오지 않는 강한 트렌드 구간에서 사용.
    """

    def __init__(self, config: dict):
        cfg = config.get("trend", {})
        self.enabled             = cfg.get("enabled", False)
        self.breakout_lookback   = cfg.get("breakout_lookback", 20)
        self.pullback_ema        = cfg.get("pullback_ema", 20)
        self.volume_ratio        = cfg.get("volume_ratio", 1.5)
        self.volume_ratio_pb     = cfg.get("volume_ratio_pullback", 1.2)
        self.candle_body_min     = cfg.get("candle_body_min", 0.55)
        self.max_ext_pct         = cfg.get("max_extension_pct", 2.5)      # v1.1: 5.0 → 2.5
        self.strong_vol_ratio    = cfg.get("strong_volume_ratio", 2.0)
        self.strong_body_min     = cfg.get("strong_body_min", 0.70)
        self.require_above_ema60    = cfg.get("require_above_ema60", True)      # v1.1: 중기 추세 게이트
        self.ema20_slope_min        = cfg.get("ema20_slope_min_pct", 0.1)     # v1.1: EMA20 최소 기울기
        self.pullback_near_pct      = cfg.get("pullback_near_pct", 0.8)       # EMA20 ±0.8%
        self.trend_score_ext_max    = cfg.get("trend_score_ext_max_pct", 2.0) # trend_score 이격 임계
        self.trend_score_strong_min = cfg.get("trend_score_strong_min", 4)    # STRONG 등급 최소 점수
        # ── 2026-04-03: EMA9 momentum gate ──────────────────────────────────
        _eq9 = cfg.get("ema9_filter", {})
        self.ema9_enabled = _eq9.get("enabled", True)
        self.ema9_period  = _eq9.get("period", 9)

    def check_entry(
        self,
        df: pd.DataFrame,
        debug: bool = False,
    ) -> Tuple[bool, str, dict]:
        """
        Trend Breakout / Pullback 진입 체크.

        Args:
            df: 5분봉 DataFrame (open/high/low/close/volume)
            debug: 상세 로그 출력 여부

        Returns:
            (signal, reason, details)
            details keys:
              entry_type    : "breakout" | "pullback"
              grade         : "STRONG" | "NORMAL" | "WEAK"
              size_mult     : 0.4 | 0.6 | 1.0
              trend_score   : int (0~5, 트렌드 강도)
              breakout_high : float
              volume_ratio  : float
              ema_aligned   : bool
              ema20_slope   : float (%)
              ext_pct       : float (EMA20 이격율)
        """
        details: dict = {
            "entry_type": None,
            "grade": None,
            "size_mult": 0.0,
            "breakout_high": 0.0,
            "volume_ratio": 0.0,
            "ema_aligned": False,
            "trend_score": 0,
            "ema20_slope": 0.0,
            "ext_pct": 0.0,
            "ema9": 0.0,
            "above_ema9": True,
        }

        if not self.enabled:
            return False, "TREND: 비활성화", details

        if df is None or len(df) < self.breakout_lookback + 10:
            return False, f"TREND: 데이터 부족({0 if df is None else len(df)}봉)", details

        # ── 기본 지표 계산 ──────────────────────────────────────────────────
        close   = df["close"]
        volume  = df["volume"]
        current = float(close.iloc[-1])
        # 거래량은 항상 직전 완성 봉 기준 — 현재 진행 중인 봉([-1])은 미집계 상태
        curr_vol = float(volume.iloc[-2]) if len(volume) >= 2 else float(volume.iloc[-1])
        avg_vol  = float(volume.iloc[-22:-2].mean()) if len(volume) >= 23 else float(volume.iloc[:-2].mean() if len(volume) >= 3 else volume.mean())
        if curr_vol == 0:
            return False, "TREND: 거래량 데이터 없음", details

        ema5_s  = _ema(close, 5)
        ema20_s = _ema(close, 20)
        ema60_s = _ema(close, 60)

        ema5_cur  = float(ema5_s.iloc[-1])
        ema20_cur = float(ema20_s.iloc[-1])
        ema60_cur = float(ema60_s.iloc[-1])

        # EMA 정배열 (5 > 20 > 60)
        ema_aligned = ema5_cur > ema20_cur > ema60_cur
        details["ema_aligned"] = ema_aligned

        # EMA20 기울기 (최근 5봉 변화율)
        ema20_slope = _ema_slope(ema20_s, lookback=5)
        details["ema20_slope"] = round(ema20_slope, 3)

        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 0.0
        details["volume_ratio"] = round(vol_ratio, 2)

        # 현재 캔들 몸통 비율
        last_row   = df.iloc[-1]
        body_ratio = _candle_body_ratio(last_row)

        # EMA20 이격율 (%) — 양수면 EMA20 위, 음수면 아래
        ext_pct = (current - ema20_cur) / ema20_cur * 100 if ema20_cur > 0 else 0.0
        details["ext_pct"] = round(ext_pct, 2)

        # ── ① 주식 중기 추세 게이트 (EMA60 상위) ──────────────────────────
        # 갭상승 3%+ 장에선 EMA60이 과거 기준이라 의미 없음 → 면제
        above_ema60 = current > ema60_cur
        if self.require_above_ema60 and not above_ema60:
            gap_exempt_pct = cfg.get("ema60_gap_exempt_pct", 3.0)
            open_price  = float(df["open"].iloc[-1]) if "open" in df.columns else 0.0
            prev_close  = float(close.iloc[-2]) if len(close) >= 2 else open_price
            gap_pct     = (open_price - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
            is_gap_up   = gap_pct >= gap_exempt_pct
            if is_gap_up:
                if debug:
                    logger.info(
                        f"[TREND_EMA60_GAP_EXEMPT] 갭상승 {gap_pct:.1f}% >= {gap_exempt_pct}% "
                        f"→ EMA60 조건 면제 (ema60={ema60_cur:.0f})"
                    )
            else:
                reason = (
                    f"TREND: EMA60 하단 ({current:.0f} < {ema60_cur:.0f}) "
                    f"— 종목 중기 추세 미확인"
                )
                if debug:
                    logger.info(f"[TREND_BLOCK] {reason}")
                return False, reason, details

        # ── ① ② EMA9 Momentum Gate (2026-04-03) ───────────────────────────
        # close < EMA9 = 단기 모멘텀 하락 중 → 반등 잡기 차단
        ema9_s   = _ema(close, self.ema9_period)
        ema9_cur = float(ema9_s.iloc[-1])
        above_ema9 = current >= ema9_cur
        details["ema9"]       = round(ema9_cur, 0)
        details["above_ema9"] = above_ema9

        if self.ema9_enabled and not above_ema9:
            # 갭상승 눌림 예외 — 갭상승 종목은 EMA9 아래 눌림이 매수 자리
            _ema9_gap_exempt_pct = cfg.get("ema9_gap_exempt_pct", 3.0)
            _open_p   = float(df["open"].iloc[-1]) if "open" in df.columns else current
            _prev_c   = float(close.iloc[-2]) if len(close) >= 2 else _open_p
            _gap_pct9 = (_open_p - _prev_c) / _prev_c * 100 if _prev_c > 0 else 0.0
            _min_vol  = cfg.get("ema9_gap_exempt_min_vol", 0.5)
            _is_gap_up9 = _gap_pct9 >= _ema9_gap_exempt_pct and vol_ratio >= _min_vol

            if _is_gap_up9:
                logger.info(
                    f"[TREND_EMA9_GAP_EXEMPT] 갭상승 {_gap_pct9:.1f}% >= {_ema9_gap_exempt_pct}% "
                    f"+ vol_r={vol_ratio:.2f} >= {_min_vol} → EMA9 눌림 면제 "
                    f"(ema9={ema9_cur:.0f}, cur={current:.0f})"
                )
            else:
                # MISSED_BY_EMA9: EMA9 없었으면 들어갔을 등급 계산
                _lookback_hi = float(df["high"].iloc[-(self.breakout_lookback + 1):-1].max())
                _would_breakout = (
                    current > _lookback_hi
                    and vol_ratio >= self.volume_ratio
                    and ema_aligned
                    and body_ratio >= self.candle_body_min
                    and ext_pct <= self.max_ext_pct
                )
                _would_grade: Optional[str] = None
                if _would_breakout:
                    _strong = (
                        trend_score >= self.trend_score_strong_min
                        and vol_ratio >= self.strong_vol_ratio
                        and body_ratio >= self.strong_body_min
                    )
                    _would_grade = "STRONG" if _strong else "NORMAL"
                details["would_be_grade"] = _would_grade

                _miss_tag = f" [MISSED_BY_EMA9:{_would_grade}]" if _would_grade else ""
                reason = (
                    f"TREND: EMA9 하단 ({current:.0f} < {ema9_cur:.0f}) "
                    f"— 단기 모멘텀 하락 중 진입 차단{_miss_tag}"
                )
                logger.info(f"[TREND_EMA9_BLOCK] {reason}")
                return False, reason, details

        # ── trend_score 계산 (0~5점, 진입 품질 지표) ──────────────────────
        # 로깅 + STRONG 등급 판단에 활용
        trend_score = 0
        if ema_aligned:                            trend_score += 1  # EMA 정배열
        if vol_ratio >= self.volume_ratio:         trend_score += 1  # 거래량
        if body_ratio >= self.candle_body_min:     trend_score += 1  # 강한 캔들
        if ema20_slope >= self.ema20_slope_min:    trend_score += 1  # EMA20 상승 중
        if ext_pct <= self.trend_score_ext_max:    trend_score += 1  # 적정 이격
        details["trend_score"] = trend_score

        if debug:
            logger.info(
                f"[TREND] EMA: {ema5_cur:.0f}/{ema20_cur:.0f}/{ema60_cur:.0f} "
                f"slope={ema20_slope:.3f}% aligned={ema_aligned} "
                f"vol_r={vol_ratio:.2f} ext={ext_pct:.1f}% score={trend_score}/5"
            )

        # ── 2. BREAKOUT 진입 체크 ───────────────────────────────────────────
        # 직전 N봉 (현재 캔들 제외) 의 최고가
        lookback_high = float(df["high"].iloc[-(self.breakout_lookback + 1):-1].max())
        details["breakout_high"] = lookback_high

        is_breakout = (
            current > lookback_high                         # 고점 돌파
            and vol_ratio >= self.volume_ratio              # 거래량 확인
            and ema_aligned                                 # EMA 정배열
            and body_ratio >= self.candle_body_min          # 강한 캔들
            and ext_pct <= self.max_ext_pct                 # 과매수 아님 (2.5%)
        )

        if is_breakout:
            is_strong = (
                trend_score >= self.trend_score_strong_min
                and vol_ratio >= self.strong_vol_ratio
                and body_ratio >= self.strong_body_min
            )
            grade     = "STRONG" if is_strong else "NORMAL"
            size_mult = 1.0 if is_strong else 0.6

            details = {
                **details,
                "entry_type": "breakout",
                "grade": grade,
                "size_mult": size_mult,
                "body_ratio": round(body_ratio, 2),
            }

            reason = (
                f"TREND BREAKOUT[{grade}]: "
                f"고점돌파({lookback_high:.0f}→{current:.0f}) "
                f"Vol×{vol_ratio:.1f} 몸통{body_ratio:.0%} "
                f"점수{trend_score}/5 이격{ext_pct:.1f}%"
            )
            logger.info(f"[TREND_SIG] {reason}")
            return True, reason, details

        # ── 3. PULLBACK 진입 체크 ───────────────────────────────────────────
        # 조건:
        #   a. 최근 N봉에서 명확한 상승 추세 (최고가 갱신 이력)
        #   b. 현재가 EMA20 ±0.8% 이내 (눌림)
        #   c. EMA20 기울기 양수 (추세 끝이 아닌 건전한 조정)  ← v1.1 핵심
        #   d. 거래량 반등 확인
        #   e. EMA 정배열 유지

        # 최근 N봉 최고가 vs 이전 N봉 최고가 → 상승 이력 확인
        recent_high = float(df["high"].iloc[-self.breakout_lookback:].max())
        prior_window_end = max(0, len(df) - self.breakout_lookback)
        prior_window_start = max(0, prior_window_end - 15)   # 이전 15봉 범위
        if prior_window_start < prior_window_end:
            prior_high = float(df["high"].iloc[prior_window_start:prior_window_end].max())
            prior_breakout = recent_high > prior_high * 1.005  # 0.5% 이상 고점 갱신
        else:
            prior_breakout = False

        near_ema20 = abs(current - ema20_cur) / ema20_cur * 100 <= self.pullback_near_pct

        # v1.1: EMA20 기울기 양수 필수 (추세 유지 확인)
        ema20_rising = ema20_slope >= self.ema20_slope_min

        is_pullback = (
            prior_breakout
            and near_ema20
            and ema_aligned
            and ema20_rising                               # v1.1: 추세 끝 진입 차단
            and vol_ratio >= self.volume_ratio_pb
            and ext_pct <= self.max_ext_pct
        )

        if is_pullback:
            details = {
                **details,
                "entry_type": "pullback",
                "grade": "WEAK",
                "size_mult": 0.4,
                "near_ema20": near_ema20,
                "ema20_rising": ema20_rising,
            }
            reason = (
                f"TREND PULLBACK[WEAK]: "
                f"EMA20눌림({ema20_cur:.0f}, 이격{ext_pct:.1f}%) "
                f"기울기{ema20_slope:.3f}% "
                f"Vol×{vol_ratio:.1f} 점수{trend_score}/5"
            )
            logger.info(f"[TREND_SIG] {reason}")
            return True, reason, details

        # ── 신호 없음 — 차단 이유 정리 ────────────────────────────────────
        reasons = []
        if not ema_aligned:
            reasons.append(f"EMA미정렬({ema5_cur:.0f}/{ema20_cur:.0f}/{ema60_cur:.0f})")
        if current <= lookback_high:
            reasons.append(f"돌파실패(현재{current:.0f}≤고점{lookback_high:.0f})")
        if vol_ratio < self.volume_ratio:
            reasons.append(f"거래량부족({vol_ratio:.1f}x<{self.volume_ratio}x)")
        if ext_pct > self.max_ext_pct:
            reasons.append(f"과매수({ext_pct:.1f}%>{self.max_ext_pct}%)")
        if body_ratio < self.candle_body_min:
            reasons.append(f"캔들약({body_ratio:.0%}<{self.candle_body_min:.0%})")
        if not ema20_rising and near_ema20:
            reasons.append(f"EMA20하락중(기울기{ema20_slope:.3f}%<{self.ema20_slope_min}%)")
        if not prior_breakout and near_ema20:
            reasons.append("이전고점갱신없음")

        reason = "TREND: " + " / ".join(reasons) if reasons else "TREND: 조건 미충족"
        return False, reason, details


# ─────────────────────────────────────────────────────────────────────────────
# 레짐 판단 헬퍼 (market_context와 독립)
# ─────────────────────────────────────────────────────────────────────────────

def detect_trend_regime(df_index: pd.DataFrame, config: dict) -> Tuple[str, str]:
    """
    지수 5분봉 데이터로 현재 레짐 판단.

    Returns:
        (regime, reason)
        regime = "TREND" | "REVERSAL" | "NEUTRAL"
    """
    cfg = config.get("trend", {}).get("regime_detection", {})
    ema_fast   = cfg.get("ema_fast", 20)
    ema_slow   = cfg.get("ema_slow", 60)
    gap_thresh = cfg.get("trend_ema_gap_pct", 0.5)

    if df_index is None or len(df_index) < ema_slow + 5:
        return "NEUTRAL", "데이터 부족"

    close  = df_index["close"]
    e_fast = float(_ema(close, ema_fast).iloc[-1])
    e_slow = float(_ema(close, ema_slow).iloc[-1])

    if e_slow <= 0:
        return "NEUTRAL", "EMA 산출불가"

    gap_pct = (e_fast - e_slow) / e_slow * 100
    is_bull = e_fast > e_slow

    if is_bull and gap_pct >= gap_thresh:
        return "TREND",    f"강한상승트렌드 EMA{ema_fast}>{ema_slow} gap={gap_pct:+.2f}%"
    elif not is_bull:
        return "REVERSAL", f"하락/횡보 EMA{ema_fast}<{ema_slow} gap={gap_pct:+.2f}%"
    else:
        return "NEUTRAL",  f"약한추세 EMA gap={gap_pct:+.2f}%<{gap_thresh}%"
