"""
core/drawdown_engine.py — 실시간 Drawdown 기반 리스크 컨트롤 (v1.1)

역할:
  1. 계좌 전체 당일 누적 실현 손익 추적 → HALT 시 전면 차단
  2. 전략별 독립 drawdown 추적 → 죽은 전략만 끄고 살아있는 전략 유지

드로우다운 레벨 (전체 계좌):
  NORMAL  : drawdown > -warning_pct (-1.5%)      → size × 1.0
  CAUTION : drawdown ≤ -warning_pct (-1.5%)      → size × 0.7
  DANGER  : drawdown ≤ -danger_pct  (-3.0%)      → size × 0.4
  HALT    : drawdown ≤ -halt_pct    (-5.0%)      → 진입 전면 차단

전략별 halt (독립):
  전략 drawdown ≤ -strategy_halt_pct (-4.0%)     → 해당 전략만 차단
  전체 계좌 HALT와 무관하게 작동 (OR 조건)

설계 원칙:
  - 계좌 전체 + 전략별 두 개의 독립 게이트
  - daily_routine 시 전부 reset()
  - execute_sell 후 record_pnl(pnl_pct, strategy) 호출

v1.0 2026-04-03: 최초 작성 (전체 계좌만)
v1.1 2026-04-04: 전략별 독립 drawdown 추가
"""
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 드로우다운 레벨 상수
LEVEL_NORMAL  = "NORMAL"
LEVEL_CAUTION = "CAUTION"
LEVEL_DANGER  = "DANGER"
LEVEL_HALT    = "HALT"


class _StrategyDDTracker:
    """단일 전략의 당일 drawdown 추적기 (내부 사용)."""

    def __init__(self, strategy: str, halt_pct: float):
        self.strategy   = strategy
        self.halt_pct   = halt_pct  # 음수 (e.g. -4.0)
        self.daily_pnl  = 0.0
        self.peak_pnl   = 0.0
        self.drawdown   = 0.0
        self.halted     = False

    def record(self, pnl: float):
        self.daily_pnl += pnl
        if self.daily_pnl > self.peak_pnl:
            self.peak_pnl = self.daily_pnl
        self.drawdown = self.daily_pnl - self.peak_pnl   # ≤ 0

        if not self.halted and self.drawdown <= self.halt_pct:
            self.halted = True
            logger.warning(
                f"[DD_STRATEGY_HALT] {self.strategy} 전략 차단: "
                f"daily={self.daily_pnl:.2f}% drawdown={self.drawdown:.2f}% "
                f"(한도={self.halt_pct}%)"
            )

    def reset(self):
        if self.halted:
            logger.info(f"[DRAWDOWN] {self.strategy} 전략 drawdown 리셋 (익일)")
        self.daily_pnl = 0.0
        self.peak_pnl  = 0.0
        self.drawdown  = 0.0
        self.halted    = False


class DrawdownEngine:
    """실시간 당일 drawdown 추적 + 사이징 축소 (전체 + 전략별)."""

    def __init__(self, config: dict):
        self.config = config

        # 전체 계좌 추적
        self._daily_pnl:      float = 0.0
        self._peak_pnl:       float = 0.0
        self._drawdown:       float = 0.0
        self._halt_triggered: bool  = False
        self._halt_at:        float = 0.0

        # 전략별 독립 추적기
        cfg = self._get_cfg()
        _strat_halt = cfg.get("strategy_halt_pct", -4.0)
        self._strategy_trackers: Dict[str, _StrategyDDTracker] = {}
        for tag in ["def", "rs", "smc", "trend", "exploration"]:
            self._strategy_trackers[tag] = _StrategyDDTracker(tag, _strat_halt)

        logger.info("[DRAWDOWN] DrawdownEngine v1.1 초기화 완료 (전략별 분리)")

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def reset_daily(self):
        """daily_routine 시작 시 전체 + 전략별 지표 초기화."""
        self._daily_pnl      = 0.0
        self._peak_pnl       = 0.0
        self._drawdown       = 0.0
        self._halt_triggered = False
        self._halt_at        = 0.0
        for tracker in self._strategy_trackers.values():
            tracker.reset()
        logger.info("[DRAWDOWN] 당일 지표 전체 초기화")

    def record_pnl(self, pnl_pct: float, strategy: Optional[str] = None):
        """
        청산 손익 기록 → 전체 + 전략별 drawdown 갱신.

        Args:
            pnl_pct:  해당 거래 손익률 (%)
            strategy: 전략 태그 ("def" / "rs" / "smc" / "trend" 등)
        """
        # 전체 계좌 갱신
        self._daily_pnl += pnl_pct
        if self._daily_pnl > self._peak_pnl:
            self._peak_pnl = self._daily_pnl
        self._drawdown = self._daily_pnl - self._peak_pnl

        cfg   = self._get_cfg()
        level = self._get_level()

        if level == LEVEL_HALT and not self._halt_triggered:
            self._halt_triggered = True
            self._halt_at        = self._daily_pnl
            logger.critical(
                f"[DD_HALT] 전체 계좌 HALT: 당일 PnL={self._daily_pnl:.2f}% "
                f"drawdown={self._drawdown:.2f}% (한도={cfg.get('halt_pct', -5.0)}%)"
            )
        elif level == LEVEL_DANGER:
            logger.warning(
                f"[DD_DANGER] 당일 PnL={self._daily_pnl:.2f}% dd={self._drawdown:.2f}%"
            )
        elif level == LEVEL_CAUTION:
            logger.info(
                f"[DD_CAUTION] 당일 PnL={self._daily_pnl:.2f}% dd={self._drawdown:.2f}%"
            )

        # 전략별 갱신
        if strategy and strategy in self._strategy_trackers:
            self._strategy_trackers[strategy].record(pnl_pct)

    def can_enter(self, strategy: Optional[str] = None) -> Tuple[bool, str]:
        """
        진입 가능 여부 반환.

        전체 HALT 또는 전략별 halt 중 하나라도 해당되면 차단.

        Args:
            strategy: 체크할 전략 태그 (None이면 전체만 체크)
        """
        # 전체 계좌 HALT
        if self._halt_triggered:
            return False, (
                f"DD_HALT: 당일 PnL={self._daily_pnl:.2f}% "
                f"drawdown={self._drawdown:.2f}%"
            )

        # 전략별 halt
        if strategy and strategy in self._strategy_trackers:
            tracker = self._strategy_trackers[strategy]
            if tracker.halted:
                return False, (
                    f"DD_STRATEGY_HALT[{strategy}]: "
                    f"daily={tracker.daily_pnl:.2f}% "
                    f"drawdown={tracker.drawdown:.2f}%"
                )

        return True, "OK"

    def get_size_mult(self, strategy: Optional[str] = None) -> Tuple[float, str]:
        """
        드로우다운 레벨별 size multiplier 반환.

        전체 계좌 레벨 기준. 전략별 halt는 can_enter()가 처리.
        """
        cfg   = self._get_cfg()
        level = self._get_level()

        mult_map = {
            LEVEL_NORMAL:  cfg.get("normal_mult",  1.0),
            LEVEL_CAUTION: cfg.get("caution_mult", 0.7),
            LEVEL_DANGER:  cfg.get("danger_mult",  0.4),
            LEVEL_HALT:    cfg.get("halt_mult",    0.0),
        }
        mult   = mult_map.get(level, 1.0)
        reason = f"dd={self._drawdown:.2f}% [{level}] → ×{mult}"

        # 전략별 drawdown 추가 정보
        if strategy and strategy in self._strategy_trackers:
            t = self._strategy_trackers[strategy]
            reason += f" | {strategy}_dd={t.drawdown:.2f}%"

        return mult, reason

    def get_status(self) -> dict:
        """현재 drawdown 상태 요약 (모니터링용)."""
        status = {
            "daily_pnl":      round(self._daily_pnl, 3),
            "peak_pnl":       round(self._peak_pnl, 3),
            "drawdown":       round(self._drawdown, 3),
            "level":          self._get_level(),
            "halt_triggered": self._halt_triggered,
            "size_mult":      self.get_size_mult()[0],
            "strategies":     {},
        }
        for tag, tracker in self._strategy_trackers.items():
            if tracker.daily_pnl != 0.0 or tracker.halted:
                status["strategies"][tag] = {
                    "daily_pnl": round(tracker.daily_pnl, 3),
                    "drawdown":  round(tracker.drawdown, 3),
                    "halted":    tracker.halted,
                }
        return status

    # ─────────────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ─────────────────────────────────────────────────────────────────

    def _get_level(self) -> str:
        cfg = self._get_cfg()
        dd  = self._drawdown

        halt_pct    = cfg.get("halt_pct",    -5.0)
        danger_pct  = cfg.get("danger_pct",  -3.0)
        warning_pct = cfg.get("warning_pct", -1.5)

        if dd <= halt_pct:
            return LEVEL_HALT
        if dd <= danger_pct:
            return LEVEL_DANGER
        if dd <= warning_pct:
            return LEVEL_CAUTION
        return LEVEL_NORMAL

    def _get_cfg(self) -> dict:
        return self.config.get("drawdown_engine", {})
