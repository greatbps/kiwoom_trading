"""
실시간 성능 드리프트 감지 (Drift Detector)

전략이 백테스트 기준치에서 얼마나 멀어졌는지 실시간 추적.
상태에 따라 자동 조치(사이즈 축소 / 리트레이닝 트리거 / 긴급 정지) 결정.

드리프트 레벨:
    OK            — 정상 범위
    REDUCE_SIZE   — 사이즈 50% 축소 권고
    RETRAIN       — 주간 튜닝 즉시 재실행 권고
    EMERGENCY_STOP — 당일 신규 진입 전면 차단

기준:
    ① live_win_rate  < baseline_wr - 0.10       → RETRAIN
    ② avg_pnl_20     < 0 (최근 20거래 평균 음수)  → REDUCE_SIZE
    ③ live_mdd       > 10%                      → EMERGENCY_STOP
    ④ RETRAIN + REDUCE_SIZE 동시                 → EMERGENCY_STOP (복합 악화)

상태 영속:
    data/drift_detector_state.json

사용:
    from analysis.drift_detector import DriftDetector

    dd = DriftDetector()
    dd.record_trade(pnl_pct=1.2, strategy="defensive")
    level, reason = dd.get_drift_level()
    # → DriftLevel.OK | REDUCE_SIZE | RETRAIN | EMERGENCY_STOP
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# ── 경로 ─────────────────────────────────────────────────────────────

STATE_PATH = Path("data/drift_detector_state.json")


# ── 드리프트 기준값 ────────────────────────────────────────────────────

DRIFT_CONFIG = {
    "window":              20,     # 최근 N거래 rolling
    "min_samples":          5,     # 최소 샘플 (이하면 OK 반환)
    "winrate_drop_thresh": 0.10,   # baseline_wr - 10pp 이하 → RETRAIN
    "avg_pnl_floor":       0.0,    # 평균 PnL < 0 → REDUCE_SIZE
    "mdd_emergency":       0.10,   # MDD > 10% → EMERGENCY
    "reduce_size_mult":    0.50,   # REDUCE_SIZE 시 포지션 배율
}


# ── 드리프트 레벨 ─────────────────────────────────────────────────────

class DriftLevel(str, Enum):
    OK             = "OK"
    REDUCE_SIZE    = "REDUCE_SIZE"
    RETRAIN        = "RETRAIN"
    EMERGENCY_STOP = "EMERGENCY_STOP"


# ── 거래 기록 ─────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    timestamp: str        # ISO datetime
    pnl_pct:   float      # 손익률 %
    strategy:  str        # "defensive" | "short" | "smc" | "trend"
    stock:     str = ""


# ── 드리프트 상태 ─────────────────────────────────────────────────────

@dataclass
class DriftState:
    """영속화 대상 상태"""
    # 백테스트 기준값 (setup 시 설정)
    baseline_win_rate: Dict[str, float] = field(default_factory=dict)
    # {strategy: baseline_wr}  예: {"defensive": 0.55, "short": 0.50}

    # 최근 trades (영속)
    recent_trades: List[Dict] = field(default_factory=list)  # TradeRecord as dict

    # 현재 드리프트 레벨
    drift_level:  str = "OK"
    drift_reason: str = ""
    last_updated: str = ""

    # 피크 잔고 (MDD 계산용)
    equity_peak: float = 0.0
    equity_cur:  float = 0.0

    # 강제 리트레인 플래그
    retrain_requested: bool = False

    # 당일 비상 정지 날짜 (날짜 비교로 자동 리셋)
    emergency_date: str = ""


# ── DriftDetector ─────────────────────────────────────────────────────

class DriftDetector:
    """실시간 드리프트 감지 + 조치 판단"""

    def __init__(
        self,
        state_path: Path = STATE_PATH,
        cfg: Optional[Dict] = None,
    ):
        self.state_path = state_path
        self.cfg        = cfg or DRIFT_CONFIG
        self._state     = self._load_state()

    # ── 거래 기록 ───────────────────────────────────────────────────

    def record_trade(
        self,
        pnl_pct:   float,
        strategy:  str  = "smc",
        stock:     str  = "",
        equity:    Optional[float] = None,
    ):
        """매도 완료 후 거래 결과 기록

        Args:
            pnl_pct:  손익률 % (양수=수익, 음수=손실)
            strategy: 전략 이름
            stock:    종목코드 (로그용)
            equity:   현재 잔고 (MDD 추적용, 없으면 PnL 누적으로 추정)
        """
        record = TradeRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            pnl_pct=pnl_pct,
            strategy=strategy,
            stock=stock,
        )
        self._state.recent_trades.append(asdict(record))

        # window 초과 분 제거
        win = self.cfg["window"]
        if len(self._state.recent_trades) > win * 2:
            self._state.recent_trades = self._state.recent_trades[-(win * 2):]

        # Equity 업데이트 (MDD 추적)
        if equity is not None:
            self._update_equity(equity)
        else:
            # PnL%로 간접 추적
            prev = self._state.equity_cur or 100.0
            cur  = prev * (1 + pnl_pct / 100)
            self._update_equity(cur)

        # 상태 재평가 + 저장
        self._evaluate()
        self._save_state()

    def _update_equity(self, equity: float):
        if self._state.equity_peak == 0:
            self._state.equity_peak = equity
        if equity > self._state.equity_peak:
            self._state.equity_peak = equity
        self._state.equity_cur = equity

    # ── 드리프트 평가 ───────────────────────────────────────────────

    def _evaluate(self):
        """전체 조건 평가 → drift_level 갱신"""
        trades = self._state.recent_trades
        win    = self.cfg["window"]
        min_s  = self.cfg["min_samples"]

        recent = trades[-win:]   # 최근 window개
        if len(recent) < min_s:
            self._set_level(DriftLevel.OK, f"샘플 부족 ({len(recent)}/{min_s})")
            return

        pnls     = [t["pnl_pct"] for t in recent]
        wins     = [p for p in pnls if p > 0]
        live_wr  = len(wins) / len(pnls)
        avg_pnl  = float(np.mean(pnls))

        # MDD 계산
        peak = self._state.equity_peak
        cur  = self._state.equity_cur
        live_mdd = (peak - cur) / peak if peak > 0 else 0.0

        reasons = []
        retrain_flag    = False
        reduce_flag     = False
        emergency_flag  = False

        # ① Win Rate 하락 → RETRAIN
        for strategy, base_wr in self._state.baseline_win_rate.items():
            strat_trades = [t for t in recent if t["strategy"] == strategy]
            if len(strat_trades) >= min_s:
                strat_pnls = [t["pnl_pct"] for t in strat_trades]
                strat_wr   = len([p for p in strat_pnls if p > 0]) / len(strat_pnls)
                drop       = base_wr - strat_wr
                if drop >= self.cfg["winrate_drop_thresh"]:
                    reasons.append(
                        f"[{strategy}] WR {strat_wr*100:.0f}% "
                        f"vs baseline {base_wr*100:.0f}% "
                        f"(drop={drop*100:.0f}pp)"
                    )
                    retrain_flag = True

        # ② 전체 평균 PnL 음수 → REDUCE_SIZE
        if avg_pnl < self.cfg["avg_pnl_floor"]:
            reasons.append(f"avg_pnl_20={avg_pnl:+.2f}% < 0")
            reduce_flag = True

        # ③ MDD > 10% → EMERGENCY
        if live_mdd > self.cfg["mdd_emergency"]:
            reasons.append(f"live_mdd={live_mdd*100:.1f}% > {self.cfg['mdd_emergency']*100:.0f}%")
            emergency_flag = True

        # ④ 복합 악화 → EMERGENCY
        if retrain_flag and reduce_flag:
            emergency_flag = True
            reasons.append("WR하락+음수PnL 복합")

        # 레벨 결정 (우선순위: EMERGENCY > RETRAIN > REDUCE_SIZE > OK)
        if emergency_flag:
            # 당일 비상정지 설정
            today = date.today().isoformat()
            self._state.emergency_date = today
            self._set_level(DriftLevel.EMERGENCY_STOP, " | ".join(reasons))
        elif retrain_flag:
            self._state.retrain_requested = True
            self._set_level(DriftLevel.RETRAIN, " | ".join(reasons))
        elif reduce_flag:
            self._set_level(DriftLevel.REDUCE_SIZE, " | ".join(reasons))
        else:
            self._set_level(DriftLevel.OK, f"WR={live_wr*100:.0f}% avg={avg_pnl:+.2f}%")

    def _set_level(self, level: DriftLevel, reason: str):
        self._state.drift_level  = level.value
        self._state.drift_reason = reason
        self._state.last_updated = datetime.now().isoformat(timespec="seconds")

    # ── 조회 API ────────────────────────────────────────────────────

    def get_drift_level(self) -> Tuple[DriftLevel, str]:
        """현재 드리프트 레벨 반환

        Returns:
            (DriftLevel, reason_str)
        """
        # 비상정지: 당일만 유효 (익일 자동 해제)
        if self._state.drift_level == DriftLevel.EMERGENCY_STOP:
            today = date.today().isoformat()
            if self._state.emergency_date != today:
                self._set_level(DriftLevel.OK, "비상정지 해제 (익일 자동)")
                self._state.emergency_date = ""
                self._save_state()

        return DriftLevel(self._state.drift_level), self._state.drift_reason

    def get_size_mult(self) -> float:
        """현재 레벨에 따른 포지션 배율

        Returns:
            1.0 (OK), 0.5 (REDUCE_SIZE), 0.5 (RETRAIN), 0.0 (EMERGENCY)
        """
        level, _ = self.get_drift_level()
        return {
            DriftLevel.OK:             1.0,
            DriftLevel.REDUCE_SIZE:    self.cfg["reduce_size_mult"],
            DriftLevel.RETRAIN:        self.cfg["reduce_size_mult"],
            DriftLevel.EMERGENCY_STOP: 0.0,
        }.get(level, 1.0)

    def is_entry_blocked(self) -> bool:
        """진입 차단 여부"""
        level, _ = self.get_drift_level()
        return level == DriftLevel.EMERGENCY_STOP

    def needs_retrain(self) -> bool:
        """즉시 리트레이닝 필요 여부"""
        return self._state.retrain_requested

    def ack_retrain(self):
        """리트레인 실행 후 플래그 해제"""
        self._state.retrain_requested = False
        self._save_state()

    # ── 기준값 설정 ─────────────────────────────────────────────────

    def set_baseline(self, strategy: str, win_rate: float):
        """백테스트 기준 승률 설정

        Args:
            strategy: "defensive" | "short" | "smc" | "trend"
            win_rate: 백테스트 승률 0~1
        """
        self._state.baseline_win_rate[strategy] = win_rate
        self._save_state()

    def set_baselines_from_result(self, tuning_result: Dict):
        """weekly_tuner 결과에서 기준값 일괄 설정

        Args:
            tuning_result: run_weekly_tuning() 반환값
        """
        for strategy in ("defensive", "short"):
            wr_key = f"{strategy}_win_rate"
            if wr_key in tuning_result:
                self.set_baseline(strategy, tuning_result[wr_key])

    def reset_equity(self, equity: float):
        """자본 리셋 (일 시작 시 호출)"""
        self._state.equity_peak = equity
        self._state.equity_cur  = equity
        self._save_state()

    # ── 상태 출력 ───────────────────────────────────────────────────

    def print_status(self):
        level, reason = self.get_drift_level()
        trades = self._state.recent_trades[-self.cfg["window"]:]

        print("\n  ── Drift Detector 상태 ──────────────────────")
        print(f"  레벨    : {level.value}")
        print(f"  이유    : {reason}")
        print(f"  최근거래 : {len(trades)}건 (window={self.cfg['window']})")

        if trades:
            pnls = [t["pnl_pct"] for t in trades]
            wins = [p for p in pnls if p > 0]
            print(f"  승률    : {len(wins)/len(pnls)*100:.0f}%")
            print(f"  평균손익 : {np.mean(pnls):+.2f}%")

        if self._state.equity_peak > 0:
            mdd = (self._state.equity_peak - self._state.equity_cur) / self._state.equity_peak
            print(f"  Live MDD: {mdd*100:.1f}%")

        print(f"  기준승률 : {self._state.baseline_win_rate}")
        print(f"  리트레인 : {'요청됨' if self._state.retrain_requested else '없음'}")
        print(f"  갱신시각 : {self._state.last_updated}")
        print()

    def generate_report(self) -> Dict:
        """로그/JSON 저장용 리포트"""
        level, reason = self.get_drift_level()
        trades = self._state.recent_trades[-self.cfg["window"]:]
        pnls   = [t["pnl_pct"] for t in trades] if trades else []

        report: Dict = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "drift_level":  level.value,
            "drift_reason": reason,
            "n_trades":     len(trades),
        }
        if pnls:
            wins = [p for p in pnls if p > 0]
            report.update({
                "live_win_rate": round(len(wins) / len(pnls), 3),
                "avg_pnl":       round(float(np.mean(pnls)), 3),
                "max_single_loss": round(min(pnls), 3),
            })
        if self._state.equity_peak > 0:
            mdd = (self._state.equity_peak - self._state.equity_cur) / self._state.equity_peak
            report["live_mdd"] = round(mdd, 4)

        report["baseline_win_rates"]  = self._state.baseline_win_rate
        report["retrain_requested"]   = self._state.retrain_requested
        report["size_multiplier"]     = self.get_size_mult()

        # 전략별 분해
        by_strat: Dict[str, Dict] = {}
        for strat in set(t["strategy"] for t in trades):
            s_trades = [t for t in trades if t["strategy"] == strat]
            s_pnls   = [t["pnl_pct"] for t in s_trades]
            s_wins   = [p for p in s_pnls if p > 0]
            by_strat[strat] = {
                "n": len(s_trades),
                "wr": round(len(s_wins) / len(s_pnls), 3) if s_pnls else 0,
                "avg_pnl": round(float(np.mean(s_pnls)), 3) if s_pnls else 0,
            }
        report["by_strategy"] = by_strat

        return report

    # ── 영속화 ──────────────────────────────────────────────────────

    def _load_state(self) -> DriftState:
        if self.state_path.exists():
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    data = json.load(f)
                state = DriftState()
                for k, v in data.items():
                    if hasattr(state, k):
                        setattr(state, k, v)
                return state
            except Exception as e:
                print(f"  [DriftDetector] state load 실패: {e} → 초기화")
        return DriftState()

    def _save_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self._state), f, ensure_ascii=False, indent=2)


# ── 전략 통합 헬퍼 ────────────────────────────────────────────────────

_global_detector: Optional[DriftDetector] = None


def get_detector() -> DriftDetector:
    """싱글톤 DriftDetector (main_auto_trading.py용)"""
    global _global_detector
    if _global_detector is None:
        _global_detector = DriftDetector()
    return _global_detector


def record_trade_result(
    pnl_pct:  float,
    strategy: str = "smc",
    stock:    str = "",
    equity:   Optional[float] = None,
):
    """execute_sell 후 단순 호출용 헬퍼"""
    get_detector().record_trade(pnl_pct, strategy, stock, equity)


def check_entry_drift() -> Tuple[bool, str, float]:
    """execute_buy 전 드리프트 게이트

    Returns:
        (ok: bool, reason: str, size_mult: float)
        ok=False → 진입 차단
        size_mult → 포지션 배율 (0.5 등)
    """
    dd    = get_detector()
    level, reason = dd.get_drift_level()
    mult  = dd.get_size_mult()

    if level == DriftLevel.EMERGENCY_STOP:
        return False, f"[DRIFT_BLOCK] {reason}", 0.0

    prefix = {
        DriftLevel.REDUCE_SIZE: "[DRIFT_REDUCE]",
        DriftLevel.RETRAIN:     "[DRIFT_RETRAIN]",
    }.get(level, "[DRIFT_OK]")

    return True, f"{prefix} {reason}", mult


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Drift Detector 상태 확인")
    parser.add_argument("--status",  action="store_true", help="현재 상태 출력")
    parser.add_argument("--report",  action="store_true", help="JSON 리포트 출력")
    parser.add_argument("--reset",   action="store_true", help="상태 초기화")
    parser.add_argument("--demo",    action="store_true", help="데모 시뮬레이션")
    args = parser.parse_args()

    dd = DriftDetector()

    if args.reset:
        dd._state = DriftState()
        dd._save_state()
        print("  상태 초기화 완료")

    elif args.report:
        import json as _json
        print(_json.dumps(dd.generate_report(), ensure_ascii=False, indent=2))

    elif args.demo:
        print("  ── 드리프트 데모 시뮬레이션 ──")
        dd._state = DriftState()
        dd.set_baseline("defensive", 0.60)
        dd.set_baseline("short", 0.55)
        dd.reset_equity(10_000_000)

        # 정상 구간
        print("\n  [Phase 1: 정상]")
        for pnl in [1.2, -0.6, 0.8, 1.0, -0.7, 1.5, 0.9, -0.5]:
            dd.record_trade(pnl, strategy="defensive", equity=10_000_000 * (1 + pnl/100))
        dd.print_status()

        # 악화 구간 (연속 손실)
        print("  [Phase 2: 성능 악화]")
        for pnl in [-1.0, -0.8, -1.2, -0.9, -1.1, -0.7, -0.8, -1.0, -0.6, -1.3]:
            dd.record_trade(pnl, strategy="defensive", equity=10_000_000 * (1 - 0.01))
        dd.print_status()

        # MDD 폭증
        print("  [Phase 3: MDD 급등]")
        dd._state.equity_peak = 10_000_000
        dd._state.equity_cur  =  8_900_000   # -11% MDD
        dd._evaluate()
        dd._save_state()
        dd.print_status()

        ok, reason, mult = check_entry_drift()
        print(f"  진입 가능: {ok}  mult={mult}  ({reason})")

    else:
        dd.print_status()
