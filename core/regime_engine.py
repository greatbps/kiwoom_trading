"""
core/regime_engine.py — 레짐 자동 판단 + 전략 비중 자동 조절 (v1.2)

역할:
  1. 시장 레짐 3-state 판단 (VOLATILE / TRENDING / NEUTRAL)
  2. Edge 기반 동적 사이징 (win_rate × RR → Kelly-like)
  3. Kill-switch: N연패 OR Regime 불일치 연패

레짐 정의:
  VOLATILE  : ATR 확장(>1.3x) or NO_TRADE_DAY
              → DEFENSIVE ↑  RS ↓
  TRENDING  : EMA 정배열 강함(gap ≥ threshold) + ATR 정상
              → RS ↑  DEFENSIVE ↓
  NEUTRAL   : 판단 불명확
              → 균등 비중

Edge 기반 사이징 (v1.2):
  edge = win_rate(0~1) × (avg_win / avg_loss)   ← Kelly 분자
  > edge_high (1.2): size × up_mult (1.15)
  < edge_low  (0.6): size × dn_mult (0.85)
  else            : size × 1.0
  n < min_samples  : no adjustment
  → EMA smoothing (α=0.7) 적용으로 급격한 변화 완충

Kill-switch (v1.2):
  조건 ①: N연패 (기본 5)
  조건 ②: Regime 불일치 N연패 (기본 3)
    - RS가 VOLATILE 레짐에서 연패
    - DEF가 TRENDING 레짐에서 연패 (이 경우 DEF 자체가 작동 안 하므로 실질적 의미 낮음)
  → 둘 중 하나 충족 시 당일 해당 전략 차단
  → 익일 daily_routine 시 자동 해제

v1.0 2026-04-03: 최초 작성
v1.1 2026-04-03: Smoothing + Kill-switch 추가
v1.2 2026-04-04: Edge 기반 사이징 + Regime 불일치 Kill 추가
"""
import re
import logging
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

VOLATILE = "VOLATILE"
TRENDING = "TRENDING"
NEUTRAL  = "NEUTRAL"


class RegiemeEngine:
    """레짐 자동 판단 + 전략 비중 자동 조절."""

    def __init__(self, market_context, config):
        self.mc     = market_context
        self.config = config

        _cfg = config.get("regime_engine", {})
        _n   = _cfg.get("perf_lookback", 10)

        # 성과 버퍼
        self._def_pnl: deque = deque(maxlen=_n)
        self._rs_pnl:  deque = deque(maxlen=_n)

        # Kill-switch: 연패 카운터
        self._def_consecutive_loss: int = 0
        self._rs_consecutive_loss:  int = 0

        # Kill-switch: Regime 불일치 카운터
        # RS가 VOLATILE에서 연패, DEF가 TRENDING에서 연패
        self._rs_regime_mismatch_loss: int = 0
        self._def_regime_mismatch_loss: int = 0

        # Kill 플래그
        self._def_killed: bool = False
        self._rs_killed:  bool = False
        self._def_kill_reason: str = ""
        self._rs_kill_reason:  str = ""

        # EMA-smoothed edge_mult 캐시
        self._def_smoothed_mult: float = 0.0
        self._rs_smoothed_mult:  float = 0.0

        # 레짐 캐시
        self._cached_regime: Optional[str]  = None
        self._cache_dt:      Optional[datetime] = None
        self._cache_ttl_min: int = _cfg.get("cache_ttl_min", 30)

        logger.info("[REGIME] RegiemeEngine v1.2 초기화 완료")

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def reset_daily(self):
        """daily_routine 시작 시 레짐 캐시 + kill-switch 초기화."""
        self._cached_regime = None
        self._cache_dt      = None

        for attr in ("_def_killed", "_rs_killed"):
            if getattr(self, attr):
                logger.info(f"[REGIME] {attr} kill-switch 해제 (익일 리셋)")

        self._def_killed = False
        self._rs_killed  = False
        self._def_kill_reason = ""
        self._rs_kill_reason  = ""
        self._def_consecutive_loss     = 0
        self._rs_consecutive_loss      = 0
        self._rs_regime_mismatch_loss  = 0
        self._def_regime_mismatch_loss = 0

    def record_trade(self, strategy: str, pnl_pct: float):
        """
        전략 거래 결과 기록 → Edge 사이징 + Kill-switch 갱신.

        Args:
            strategy: "def" or "rs"
            pnl_pct:  수익률 (%)
        """
        regime = self._get_regime()

        if strategy == "def":
            self._def_pnl.append(pnl_pct)
            if pnl_pct < 0:
                self._def_consecutive_loss += 1
                # Regime 불일치: TRENDING에서 DEF 손실 (DEF가 작동하면 안 되는 구간)
                if regime == TRENDING:
                    self._def_regime_mismatch_loss += 1
                self._check_kill_switch("def")
            else:
                self._def_consecutive_loss     = 0
                self._def_regime_mismatch_loss = 0

        elif strategy == "rs":
            self._rs_pnl.append(pnl_pct)
            if pnl_pct < 0:
                self._rs_consecutive_loss += 1
                # Regime 불일치: VOLATILE에서 RS 손실 (RS가 작동하면 안 되는 구간)
                if regime == VOLATILE:
                    self._rs_regime_mismatch_loss += 1
                self._check_kill_switch("rs")
            else:
                self._rs_consecutive_loss     = 0
                self._rs_regime_mismatch_loss = 0

    def get_def_size_mult(self, base_mult: float) -> Tuple[float, str]:
        """DEFENSIVE 진입 시 최종 size multiplier."""
        cfg = self._get_cfg()
        if not cfg.get("enabled", True):
            return base_mult, "RegimeEngine OFF"

        if self._def_killed:
            return cfg.get("size_floor", 0.5), f"DEF kill-switch: {self._def_kill_reason}"

        regime      = self._get_regime()
        regime_mult = self._regime_mult_def(regime, cfg)
        edge_mult   = self._smoothed_edge_mult(self._def_pnl, "_def_smoothed_mult", cfg)
        final       = self._clamp(base_mult * regime_mult * edge_mult, cfg)

        reason = (
            f"base={base_mult:.2f} × regime({regime})={regime_mult:.2f} "
            f"× edge(n={len(self._def_pnl)})={edge_mult:.2f} → {final:.2f}"
        )
        logger.debug(f"[REGIME] DEF size: {reason}")
        return final, reason

    def get_rs_size_mult(self, base_mult: float) -> Tuple[float, str]:
        """RS 진입 시 최종 size multiplier."""
        cfg = self._get_cfg()
        if not cfg.get("enabled", True):
            return base_mult, "RegimeEngine OFF"

        if self._rs_killed:
            return cfg.get("size_floor", 0.5), f"RS kill-switch: {self._rs_kill_reason}"

        regime      = self._get_regime()
        regime_mult = self._regime_mult_rs(regime, cfg)
        edge_mult   = self._smoothed_edge_mult(self._rs_pnl, "_rs_smoothed_mult", cfg)
        final       = self._clamp(base_mult * regime_mult * edge_mult, cfg)

        reason = (
            f"base={base_mult:.2f} × regime({regime})={regime_mult:.2f} "
            f"× edge(n={len(self._rs_pnl)})={edge_mult:.2f} → {final:.2f}"
        )
        logger.debug(f"[REGIME] RS size: {reason}")
        return final, reason

    def is_strategy_killed(self, strategy: str) -> bool:
        """kill-switch 상태 조회."""
        return {"def": self._def_killed, "rs": self._rs_killed}.get(strategy, False)

    def get_status(self) -> Dict:
        """현재 레짐 + 성과 요약."""
        regime   = self._get_regime()
        def_edge = self._compute_edge(self._def_pnl)
        rs_edge  = self._compute_edge(self._rs_pnl)
        def_mult = self.get_def_size_mult(1.0)[0]
        rs_mult  = self.get_rs_size_mult(1.0)[0]
        return {
            "regime":              regime,
            "def_wr":              self._win_rate(self._def_pnl),
            "rs_wr":               self._win_rate(self._rs_pnl),
            "def_edge":            round(def_edge, 3),
            "rs_edge":             round(rs_edge, 3),
            "def_mult":            round(def_mult, 2),
            "rs_mult":             round(rs_mult, 2),
            "def_n":               len(self._def_pnl),
            "rs_n":                len(self._rs_pnl),
            "def_consec_loss":     self._def_consecutive_loss,
            "rs_consec_loss":      self._rs_consecutive_loss,
            "rs_regime_mismatch":  self._rs_regime_mismatch_loss,
            "def_killed":          self._def_killed,
            "rs_killed":           self._rs_killed,
            "def_kill_reason":     self._def_kill_reason,
            "rs_kill_reason":      self._rs_kill_reason,
        }

    # ─────────────────────────────────────────────────────────────────
    # Kill-switch
    # ─────────────────────────────────────────────────────────────────

    def _check_kill_switch(self, strategy: str):
        """연패 한도 OR Regime 불일치 연패 → 당일 전략 차단."""
        cfg    = self._get_cfg()
        ks_cfg = cfg.get("kill_switch", {})
        if not ks_cfg.get("enabled", True):
            return

        consec_limit   = ks_cfg.get("consecutive_loss_limit", 5)
        mismatch_limit = ks_cfg.get("regime_mismatch_limit", 3)

        if strategy == "def":
            if self._def_consecutive_loss >= consec_limit:
                self._def_killed      = True
                self._def_kill_reason = f"{self._def_consecutive_loss}연패"
                logger.warning(
                    f"[REGIME] DEF kill-switch(연패): {self._def_consecutive_loss}연패 "
                    f"→ 당일 DEF 차단"
                )
            elif self._def_regime_mismatch_loss >= mismatch_limit:
                self._def_killed      = True
                self._def_kill_reason = (
                    f"TRENDING 레짐 불일치 {self._def_regime_mismatch_loss}연패"
                )
                logger.warning(
                    f"[REGIME] DEF kill-switch(Regime): TRENDING에서 "
                    f"{self._def_regime_mismatch_loss}연패 → 당일 DEF 차단"
                )

        elif strategy == "rs":
            if self._rs_consecutive_loss >= consec_limit:
                self._rs_killed      = True
                self._rs_kill_reason = f"{self._rs_consecutive_loss}연패"
                logger.warning(
                    f"[REGIME] RS kill-switch(연패): {self._rs_consecutive_loss}연패 "
                    f"→ 당일 RS 차단"
                )
            elif self._rs_regime_mismatch_loss >= mismatch_limit:
                self._rs_killed      = True
                self._rs_kill_reason = (
                    f"VOLATILE 레짐 불일치 {self._rs_regime_mismatch_loss}연패"
                )
                logger.warning(
                    f"[REGIME] RS kill-switch(Regime): VOLATILE에서 "
                    f"{self._rs_regime_mismatch_loss}연패 → 당일 RS 차단"
                )

    # ─────────────────────────────────────────────────────────────────
    # 레짐 판단
    # ─────────────────────────────────────────────────────────────────

    def _get_regime(self) -> str:
        now = datetime.now()
        if (
            self._cached_regime is not None
            and self._cache_dt is not None
            and (now - self._cache_dt).total_seconds() < self._cache_ttl_min * 60
        ):
            return self._cached_regime

        regime = self._compute_regime()
        self._cached_regime = regime
        self._cache_dt      = now
        logger.info(f"[REGIME] 레짐 갱신: {regime}")
        return regime

    def _compute_regime(self) -> str:
        cfg = self._get_cfg()
        try:
            atr_ratio      = self._parse_atr_ratio()
            is_volatile_atr = (
                atr_ratio is not None
                and atr_ratio > cfg.get("volatile_atr_threshold", 1.3)
            )

            mc_regime, _ = self.mc.get_regime()
            is_trending   = (mc_regime == "TREND")

            mc_status = "TRADE_OK"
            if self.mc._cache_result is not None:
                mc_status = self.mc._cache_result[0]

            if mc_status == "NO_TRADE_DAY" or is_volatile_atr:
                return VOLATILE
            if is_trending:
                return TRENDING
            return NEUTRAL

        except Exception as e:
            logger.debug(f"[REGIME] 레짐 계산 오류: {e}")
            return NEUTRAL

    def _parse_atr_ratio(self) -> Optional[float]:
        try:
            if self.mc._cache_result is None:
                return None
            details    = self.mc._cache_result[2]
            vol_reason = details.get("volatility", {}).get("reason", "")
            m          = re.search(r"ATR비율=([0-9.]+)", vol_reason)
            if m:
                return float(m.group(1))
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────────────
    # 레짐별 multiplier
    # ─────────────────────────────────────────────────────────────────

    def _regime_mult_def(self, regime: str, cfg: dict) -> float:
        mults = cfg.get("regime_mult", {})
        return {
            VOLATILE: mults.get("volatile_def", 1.3),
            TRENDING: mults.get("trending_def", 0.9),
            NEUTRAL:  mults.get("neutral_def",  1.0),
        }.get(regime, 1.0)

    def _regime_mult_rs(self, regime: str, cfg: dict) -> float:
        mults = cfg.get("regime_mult", {})
        return {
            VOLATILE: mults.get("volatile_rs",  0.7),
            TRENDING: mults.get("trending_rs",  1.3),
            NEUTRAL:  mults.get("neutral_rs",   1.0),
        }.get(regime, 1.0)

    # ─────────────────────────────────────────────────────────────────
    # Edge 기반 사이징 (v1.2)
    # ─────────────────────────────────────────────────────────────────

    def _compute_edge(self, pnl_buf: deque) -> float:
        """
        edge = win_rate × (avg_win / avg_loss)

        Kelly 분자와 동일. 1.0 기준: 손익비가 승률로 설명됨.
        샘플 부족 → 1.0 반환.
        """
        if not pnl_buf:
            return 1.0
        wins   = [p for p in pnl_buf if p > 0]
        losses = [p for p in pnl_buf if p < 0]
        if not wins or not losses:
            return 1.0
        win_rate = len(wins) / len(pnl_buf)         # 0~1
        avg_win  = sum(wins)   / len(wins)
        avg_loss = abs(sum(losses) / len(losses))   # 양수로 변환
        return win_rate * (avg_win / avg_loss)

    def _edge_mult(self, pnl_buf: deque, cfg: dict) -> float:
        """Edge → raw size multiplier."""
        min_n = cfg.get("perf_min_samples", 5)
        if len(pnl_buf) < min_n:
            return 1.0

        edge     = self._compute_edge(pnl_buf)
        high_e   = cfg.get("edge_high", 1.2)
        low_e    = cfg.get("edge_low",  0.6)
        up_mult  = cfg.get("perf_up_mult", 1.15)
        dn_mult  = cfg.get("perf_dn_mult", 0.85)

        if edge >= high_e:
            return up_mult
        if edge < low_e:
            return dn_mult
        return 1.0

    def _smoothed_edge_mult(self, pnl_buf: deque, attr: str, cfg: dict) -> float:
        """EMA smoothing 적용 edge multiplier."""
        raw   = self._edge_mult(pnl_buf, cfg)
        alpha = cfg.get("perf_smooth_alpha", 0.7)
        prev  = getattr(self, attr, 0.0)
        smoothed = raw if prev == 0.0 else alpha * prev + (1 - alpha) * raw
        setattr(self, attr, smoothed)
        return round(smoothed, 4)

    # ─────────────────────────────────────────────────────────────────
    # 공용 헬퍼
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _win_rate(buf: deque) -> float:
        if not buf:
            return 0.0
        return sum(1 for p in buf if p > 0) / len(buf) * 100

    @staticmethod
    def _clamp(val: float, cfg: dict) -> float:
        lo = cfg.get("size_floor", 0.5)
        hi = cfg.get("size_cap",   2.0)
        return max(lo, min(hi, val))

    def _get_cfg(self) -> dict:
        return self.config.get("regime_engine", {})
