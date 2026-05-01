"""
Monte Carlo 시뮬레이터 (오프라인)

실매매 거래 내역을 기반으로 랜덤 순서 셔플 시뮬레이션을 N회 수행,
파산 확률·MDD 분포·수익 분포를 계산한다.

Usage:
    python -m analysis.monte_carlo [--days 90] [--n 1000] [--balance 10000000] [--ruin-pct 20]
"""

import argparse
import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 경로 ──
_ROOT = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "trades.db"
_LOGS_DIR = _ROOT / "logs"

TRADING_DAYS_PER_YEAR = 252
MIN_TRADE_COUNT = 10

# ── 백분위 레벨 ──
PERCENTILES = [5, 25, 50, 75, 95]


# ─────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────

def _load_from_trades(days: int) -> List[float]:
    """
    trades 테이블의 SELL 행에서 realized_pnl을 로드한다.
    days=0 이면 전체 기간.
    반환: realized_pnl 값 리스트 (원화 금액)
    """
    if not _DB_PATH.exists():
        logger.warning("DB 파일 없음: %s", _DB_PATH)
        return []

    conn = sqlite3.connect(str(_DB_PATH))
    try:
        if days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            sql = (
                "SELECT realized_pnl FROM trades "
                "WHERE trade_type = 'SELL' AND realized_pnl != 0 "
                "AND trade_date >= ? "
                "ORDER BY trade_date, timestamp"
            )
            rows = conn.execute(sql, (cutoff,)).fetchall()
        else:
            sql = (
                "SELECT realized_pnl FROM trades "
                "WHERE trade_type = 'SELL' AND realized_pnl != 0 "
                "ORDER BY trade_date, timestamp"
            )
            rows = conn.execute(sql).fetchall()
    finally:
        conn.close()

    return [float(r[0]) for r in rows if r[0] is not None]


def _load_from_entry_features(days: int) -> List[float]:
    """
    entry_features 테이블의 outcome_pnl_pct를 로드한다 (fallback).
    반환: pnl_pct 값 리스트 (%, 예: 1.5 = +1.5%)
    """
    if not _DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(_DB_PATH))
    try:
        # 테이블 존재 확인
        tbl_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entry_features'"
        ).fetchone()
        if not tbl_check:
            return []

        if days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            sql = (
                "SELECT outcome_pnl_pct FROM entry_features "
                "WHERE outcome_pnl_pct IS NOT NULL "
                "AND trade_date >= ? "
                "ORDER BY trade_date"
            )
            rows = conn.execute(sql, (cutoff,)).fetchall()
        else:
            sql = (
                "SELECT outcome_pnl_pct FROM entry_features "
                "WHERE outcome_pnl_pct IS NOT NULL "
                "ORDER BY trade_date"
            )
            rows = conn.execute(sql).fetchall()
    finally:
        conn.close()

    return [float(r[0]) for r in rows if r[0] is not None]


def load_trade_pnls(days: int, initial_balance: float) -> Tuple[List[float], str]:
    """
    거래 손익을 로드한다. realized_pnl(원화)을 % 단위로 변환.
    부족 시 entry_features.outcome_pnl_pct로 fallback.

    반환: (pnl_pct_list, source_label)
    """
    raw = _load_from_trades(days)
    if len(raw) >= MIN_TRADE_COUNT:
        # 원화 → 잔고 대비 %
        pnl_pcts = [v / initial_balance * 100.0 for v in raw]
        logger.info("trades 테이블 로드: %d건", len(pnl_pcts))
        return pnl_pcts, "trades.realized_pnl"

    logger.warning(
        "trades 테이블 데이터 부족 (%d건 < %d건), entry_features fallback 시도",
        len(raw), MIN_TRADE_COUNT
    )
    fallback = _load_from_entry_features(days)
    if fallback:
        logger.info("entry_features 로드: %d건", len(fallback))
        return fallback, "entry_features.outcome_pnl_pct"

    # 두 소스 모두 없으면 trades 원본 반환 (길이 부족 경고는 호출자에서 처리)
    pnl_pcts = [v / initial_balance * 100.0 for v in raw]
    return pnl_pcts, "trades.realized_pnl (부족)"


# ─────────────────────────────────────────────
# 단일 시뮬레이션 계산
# ─────────────────────────────────────────────

def _compute_equity_curve(pnl_pcts: np.ndarray) -> np.ndarray:
    """누적 수익률 곡선 (% 기준, 시작 = 0)"""
    return np.cumsum(pnl_pcts)


def _max_drawdown(equity: np.ndarray) -> float:
    """최대 낙폭(MDD) % — 양수로 반환"""
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    drawdowns = peak - equity
    return float(np.max(drawdowns))


def _sharpe(pnl_pcts: np.ndarray, trading_days: int = TRADING_DAYS_PER_YEAR) -> float:
    """연환산 Sharpe Ratio (무위험 수익률 0 가정)"""
    if len(pnl_pcts) < 2:
        return 0.0
    mean = np.mean(pnl_pcts)
    std = np.std(pnl_pcts, ddof=1)
    if std < 1e-12:
        return 0.0
    # 거래 단위 → 연환산 (거래당 Sharpe × √거래수/년)
    # 실거래 빈도 추정: 연간 trading_days 중 얼마나 거래하는지 알 수 없으므로
    # 단순히 거래 수 기준 연환산 적용
    return float(mean / std * math.sqrt(trading_days))


def run_single_simulation(
    pnl_pcts: np.ndarray,
    rng: np.random.Generator,
) -> Dict:
    """거래 순서를 셔플하여 하나의 시뮬레이션을 수행한다."""
    shuffled = pnl_pcts.copy()
    rng.shuffle(shuffled)
    equity = _compute_equity_curve(shuffled)
    final_pnl = float(equity[-1])
    mdd = _max_drawdown(equity)
    sharpe = _sharpe(shuffled)
    return {"final_pnl": final_pnl, "mdd": mdd, "sharpe": sharpe}


# ─────────────────────────────────────────────
# 메인 시뮬레이터
# ─────────────────────────────────────────────

class MonteCarloSimulator:
    """Monte Carlo 시뮬레이터"""

    def __init__(
        self,
        days: int = 90,
        n_simulations: int = 1000,
        initial_balance: float = 10_000_000,
        ruin_threshold_pct: float = 20.0,
        seed: Optional[int] = None,
    ):
        self.days = days
        self.n_simulations = n_simulations
        self.initial_balance = initial_balance
        self.ruin_threshold_pct = ruin_threshold_pct
        self.rng = np.random.default_rng(seed)

        self.pnl_pcts: List[float] = []
        self.source_label: str = ""
        self.sim_results: List[Dict] = []
        self.summary: Dict = {}

    # ── 로드 ──

    def load(self) -> int:
        self.pnl_pcts, self.source_label = load_trade_pnls(
            self.days, self.initial_balance
        )
        return len(self.pnl_pcts)

    # ── 실행 ──

    def run(self) -> Dict:
        arr = np.array(self.pnl_pcts, dtype=float)

        logger.info(
            "Monte Carlo 시작: N=%d, 거래수=%d, 초기잔고=%.0f, 파산기준=%.0f%%",
            self.n_simulations, len(arr), self.initial_balance, self.ruin_threshold_pct,
        )

        self.sim_results = [
            run_single_simulation(arr, self.rng)
            for _ in range(self.n_simulations)
        ]

        self.summary = self._compute_summary(arr)
        return self.summary

    def _compute_summary(self, arr: np.ndarray) -> Dict:
        final_pnls = np.array([r["final_pnl"] for r in self.sim_results])
        mdds = np.array([r["mdd"] for r in self.sim_results])
        sharpes = np.array([r["sharpe"] for r in self.sim_results])

        # 파산 비율
        p_ruin = float(np.mean(mdds > self.ruin_threshold_pct))

        # 백분위
        pnl_pctiles = {
            f"p{p}": float(np.percentile(final_pnls, p)) for p in PERCENTILES
        }
        mdd_pctiles = {
            f"p{p}": float(np.percentile(mdds, p)) for p in PERCENTILES
        }

        # 개별 거래 통계
        wins = arr[arr > 0]
        losses = arr[arr < 0]
        win_rate = float(len(wins) / len(arr)) if len(arr) > 0 else 0.0
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
        expectancy = float(np.mean(arr)) if len(arr) > 0 else 0.0

        return {
            "meta": {
                "source": self.source_label,
                "days": self.days,
                "n_simulations": self.n_simulations,
                "n_trades": len(arr),
                "initial_balance": self.initial_balance,
                "ruin_threshold_pct": self.ruin_threshold_pct,
                "run_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "ruin": {
                "p_ruin": p_ruin,
                "p_ruin_pct": round(p_ruin * 100, 2),
            },
            "final_pnl_pct": pnl_pctiles,
            "mdd_pct": mdd_pctiles,
            "sharpe": {
                f"p{p}": float(np.percentile(sharpes, p)) for p in PERCENTILES
            },
            "trade_stats": {
                "win_rate": round(win_rate * 100, 2),
                "avg_win_pct": round(avg_win, 4),
                "avg_loss_pct": round(avg_loss, 4),
                "expectancy_pct": round(expectancy, 4),
                "total_trades": int(len(arr)),
                "win_count": int(len(wins)),
                "loss_count": int(len(losses)),
                "rr_ratio": round(
                    abs(avg_win / avg_loss) if avg_loss != 0 else 0.0, 3
                ),
            },
        }

    # ── 출력 ──

    def print_summary(self) -> None:
        s = self.summary
        if not s:
            print("[MonteCarloSimulator] 아직 run()을 호출하지 않았습니다.")
            return

        sep = "─" * 58

        def row(label: str, value: str) -> str:
            return f"  {label:<32} {value}"

        def pct_row(label: str, d: Dict, key_fmt: str = "p{}") -> str:
            vals = "  ".join(
                f"P{p}={d[key_fmt.format(p)]:+.2f}%" for p in PERCENTILES
            )
            return f"  {label:<32} {vals}"

        lines = [
            "",
            "╔══════════════════════════════════════════════════════╗",
            "║          Monte Carlo 시뮬레이션 결과 요약              ║",
            "╚══════════════════════════════════════════════════════╝",
            sep,
            row("데이터 소스", s["meta"]["source"]),
            row("분석 기간",   f"최근 {s['meta']['days']}일"),
            row("거래 수",     f"{s['meta']['n_trades']}건"),
            row("시뮬레이션 횟수", f"{s['meta']['n_simulations']:,}회"),
            row("초기 자본",   f"{s['meta']['initial_balance']:,.0f}원"),
            row("파산 기준",   f"MDD > {s['meta']['ruin_threshold_pct']:.0f}%"),
            sep,
            "[ 파산 위험 ]",
            row("파산 확률 (p_ruin)", f"{s['ruin']['p_ruin_pct']:.2f}%"),
            sep,
            "[ 최종 수익률 분포 (N 시뮬 기준) ]",
            pct_row("최종 PnL %", s["final_pnl_pct"], "p{}"),
            sep,
            "[ 최대낙폭 분포 (MDD%) ]",
            pct_row("MDD %", s["mdd_pct"], "p{}"),
            sep,
            "[ Sharpe Ratio 분포 ]",
            pct_row("Sharpe", s["sharpe"], "p{}"),
            sep,
            "[ 개별 거래 통계 ]",
            row("승률",        f"{s['trade_stats']['win_rate']:.2f}%"),
            row("평균 수익 (승)",  f"{s['trade_stats']['avg_win_pct']:+.4f}%"),
            row("평균 손실 (패)",  f"{s['trade_stats']['avg_loss_pct']:+.4f}%"),
            row("기대값 (per trade)", f"{s['trade_stats']['expectancy_pct']:+.4f}%"),
            row("손익비 (RR)",  f"{s['trade_stats']['rr_ratio']:.3f}"),
            row("승/패/총",
                f"{s['trade_stats']['win_count']}승 / "
                f"{s['trade_stats']['loss_count']}패 / "
                f"{s['trade_stats']['total_trades']}건"),
            sep,
        ]

        # 파산 확률 경고
        p_ruin_pct = s["ruin"]["p_ruin_pct"]
        if p_ruin_pct >= 50:
            lines.append("  [CRITICAL] 파산 확률 50% 이상 — 전략 재검토 필요")
        elif p_ruin_pct >= 20:
            lines.append("  [WARNING]  파산 확률 20% 이상 — 포지션 규모 축소 권장")
        elif p_ruin_pct >= 5:
            lines.append("  [CAUTION]  파산 확률 5% 이상 — 리스크 관리 주의")
        else:
            lines.append("  [OK]       파산 확률 낮음")

        # 기대값 경고
        exp = s["trade_stats"]["expectancy_pct"]
        if exp < 0:
            lines.append(f"  [WARNING]  기대값 음수 ({exp:+.4f}%) — 손익 개선 필요")
        lines.append(sep)

        print("\n".join(lines))

    # ── 저장 ──

    def save(self) -> Path:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        fname = _LOGS_DIR / f"monte_carlo_{datetime.now().strftime('%Y%m%d')}.json"
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(self.summary, f, ensure_ascii=False, indent=2)
        logger.info("결과 저장: %s", fname)
        return fname


# ─────────────────────────────────────────────
# CLI 진입점
# ─────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monte Carlo 시뮬레이터 — 실매매 거래 기반 리스크 분석"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="분석 기간(일). 0=전체 기간 (default: 90)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=1000,
        help="시뮬레이션 횟수 (default: 1000)",
    )
    parser.add_argument(
        "--balance",
        type=float,
        default=10_000_000,
        help="초기 자본금(원) (default: 10000000)",
    )
    parser.add_argument(
        "--ruin-pct",
        type=float,
        default=20.0,
        dest="ruin_pct",
        help="파산 기준 MDD%% (default: 20)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="난수 시드 (재현성, default: None)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="결과 JSON 저장 안 함",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    args = _parse_args()

    sim = MonteCarloSimulator(
        days=args.days,
        n_simulations=args.n,
        initial_balance=args.balance,
        ruin_threshold_pct=args.ruin_pct,
        seed=args.seed,
    )

    n = sim.load()

    if n < MIN_TRADE_COUNT:
        print(
            f"\n[WARNING] 거래 데이터 부족 ({n}건 < {MIN_TRADE_COUNT}건). "
            "시뮬레이션을 건너뜁니다.\n"
            "  → trades 테이블에 SELL 거래가 충분히 쌓인 후 재실행하세요."
        )
        return

    sim.run()
    sim.print_summary()

    if not args.no_save:
        saved_path = sim.save()
        print(f"\n  결과 저장 완료: {saved_path}\n")


if __name__ == "__main__":
    main()
