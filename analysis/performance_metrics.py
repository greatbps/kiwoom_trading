"""
Performance Metrics — 전략 성과 측정

WinRate, ProfitFactor, MaxDrawdown, Expectancy 계산
strategy_optimizer.py에서 import해서 사용
"""
from typing import List, Dict


def compute_metrics(trades: List[float]) -> Dict:
    """
    거래 수익률 리스트 → 성과 지표 딕셔너리

    Args:
        trades: 각 거래의 수익률 (예: 0.012 = +1.2%, -0.006 = -0.6%)

    Returns:
        {win_rate, profit_factor, max_drawdown, expectancy, trades, avg_win, avg_loss}
    """
    if not trades:
        return _empty_metrics()

    wins   = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]

    n        = len(trades)
    n_wins   = len(wins)
    n_losses = len(losses)

    win_rate  = n_wins / n if n > 0 else 0.0
    avg_win   = sum(wins)   / n_wins   if n_wins   > 0 else 0.0
    avg_loss  = sum(losses) / n_losses if n_losses > 0 else 0.0  # 음수

    gross_profit = avg_win  * n_wins
    gross_loss   = abs(avg_loss) * n_losses
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    # Expectancy: 한 거래당 기대 수익률
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    # Max Drawdown (누적 수익 기준)
    max_drawdown = _compute_max_drawdown(trades)

    return {
        "trades":        n,
        "win_rate":      round(win_rate,      4),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown":  round(max_drawdown,  4),
        "expectancy":    round(expectancy,    6),
        "avg_win":       round(avg_win,       6),
        "avg_loss":      round(avg_loss,      6),
        "n_wins":        n_wins,
        "n_losses":      n_losses,
    }


def score(metrics: Dict, min_trades: int = 100) -> float:
    """
    전략 종합 점수 (Optimizer 정렬용)

    - trades < min_trades → 0점 (통계 불충분)
    - 기본: WinRate×0.5 + PF×0.3 + Expectancy×0.2 (정규화)
    """
    if metrics["trades"] < min_trades:
        return 0.0

    wr  = metrics["win_rate"]
    pf  = min(metrics["profit_factor"], 5.0) / 5.0   # 0~1 정규화 (5 이상 cap)
    exp = max(metrics["expectancy"], -0.05)            # 극단값 제한
    exp_norm = (exp + 0.05) / 0.10                     # -0.05~+0.05 → 0~1

    return round(wr * 0.5 + pf * 0.3 + exp_norm * 0.2, 6)


def passes_threshold(metrics: Dict, min_trades: int = 100,
                     min_win_rate: float = 0.55,
                     min_profit_factor: float = 1.3) -> bool:
    """
    전략 채택 기준 통과 여부
    - trades ≥ min_trades
    - win_rate ≥ min_win_rate
    - profit_factor ≥ min_profit_factor
    """
    return (
        metrics["trades"]        >= min_trades
        and metrics["win_rate"]      >= min_win_rate
        and metrics["profit_factor"] >= min_profit_factor
    )


def _compute_max_drawdown(trades: List[float]) -> float:
    """누적 수익률 기준 최대 낙폭"""
    peak = 0.0
    cumulative = 0.0
    max_dd = 0.0

    for t in trades:
        cumulative += t
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return max_dd


def _empty_metrics() -> Dict:
    return {
        "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "max_drawdown": 0.0, "expectancy": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0,
        "n_wins": 0, "n_losses": 0,
    }
