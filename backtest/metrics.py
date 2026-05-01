"""
성과 지표 계산.

스윙 핵심 지표 체계:
  수익 구조: avg_mfe_pct, capture_rate, big_winner_ratio, mfe_10plus_ratio
  손실 구조: avg_mae_pct, mae_3plus_ratio, sl_hit_ratio       ← 위험도 판단
  시간 구조: ret_d1, ret_d2_5, ret_d6_14, ret_d15plus        ← 언제 돈 나오나

판독법:
  MFE 높고 MAE 낮음  → 최고 전략
  MFE 높고 MAE 높음  → 위험한 전략 (운 좋게 버텨서 익절)
  MFE 낮고 MAE 낮음  → 죽은 전략
"""
import math
import numpy as np
from dataclasses import dataclass, field
from typing import List
from .engine import Trade


@dataclass
class PerfMetrics:
    symbol:           str
    trades:           int
    win_rate:         float   # 0~1
    total_return:     float   # 합산 수익률
    avg_win:          float   # 평균 수익률 (승)
    avg_loss:         float   # 평균 수익률 (패)
    rr_ratio:         float   # |avg_win / avg_loss|
    mdd:              float   # 최대낙폭 (음수)
    profit_factor:    float   # 총이익 / 총손실
    avg_hold_bars:    float   # 평균 보유봉 수

    # ── 수익 구조 ─────────────────────────────────────────────
    avg_mfe_pct:       float  = 0.0   # 평균 MFE (%)
    capture_rate:      float  = 0.0   # avg(pnl% / mfe%) — 0~1
    big_winner_ratio:  float  = 0.0   # pnl >= +10% 비율 (계좌 성장 핵심)
    mfe_10plus_ratio:  float  = 0.0   # MFE >= 10% 도달 비율 (잠재력)

    # ── 손실 구조 (MAE) ───────────────────────────────────────
    avg_mae_pct:       float  = 0.0   # 평균 MAE (%, 양수 표시)
    mae_3plus_ratio:   float  = 0.0   # MAE > 3% 거래 비율 (위험 노출)
    sl_hit_ratio:      float  = 0.0   # SL/BE_STOP 청산 비율

    # ── 시간대별 수익 구조 (일봉 기준) ────────────────────────
    # 한국 시장 6.5h/일, daily bar 기준
    ret_d1:            float  = 0.0   # hold=1봉 평균 pnl%
    ret_d2_5:          float  = 0.0   # hold 2~5봉 평균 pnl%
    ret_d6_14:         float  = 0.0   # hold 6~14봉 평균 pnl%
    ret_d15plus:       float  = 0.0   # hold 15봉+ 평균 pnl% (진짜 스윙)
    cnt_d1:            int    = 0
    cnt_d2_5:          int    = 0
    cnt_d6_14:         int    = 0
    cnt_d15plus:       int    = 0

    equity_stability:  float  = 0.0   # 에퀴티 곡선 std
    exit_reason_dist:  dict   = field(default_factory=dict)

    def passed(self, mode: str = 'tp_sl') -> bool:
        """통과 기준."""
        if mode == 'swing':
            return (
                self.trades >= 5
                and self.mdd >= -0.10
                and self.capture_rate >= 0.40
                and self.avg_hold_bars >= 4
            )
        base = (
            self.trades >= 10
            and self.mdd >= -0.15
            and self.total_return > 0
        )
        return base and (
            (self.win_rate >= 0.55 and self.rr_ratio >= 1.5)
            or (self.win_rate >= 0.25 and self.rr_ratio >= 2.5)
        )

    def summary(self, mode: str = 'tp_sl') -> str:
        status = '✅ PASS' if self.passed(mode) else '❌ FAIL'
        base = (
            f"{self.symbol:8s} {status}  "
            f"trades={self.trades:3d}  "
            f"win={self.win_rate*100:.1f}%  "
            f"ret={self.total_return*100:+.1f}%  "
            f"RR={self.rr_ratio:.2f}  "
            f"MDD={self.mdd*100:.1f}%  "
            f"hold={self.avg_hold_bars:.1f}봉"
        )
        if mode == 'swing':
            base += (
                f"  MFE={self.avg_mfe_pct:.1f}%"
                f"  MAE={self.avg_mae_pct:.1f}%"
                f"  cap={self.capture_rate*100:.0f}%"
                f"  BW={self.big_winner_ratio*100:.0f}%"
            )
        return base


def _bucket_ret(trades: List[Trade], lo: int, hi: int) -> tuple[float, int]:
    """hold_bars in [lo, hi] 구간 평균 pnl% 및 거래 수."""
    subset = [t for t in trades if lo <= t.hold_bars <= hi]
    if not subset:
        return 0.0, 0
    return round(float(np.mean([t.pnl_pct * 100 for t in subset])), 2), len(subset)


def calculate(symbol: str, trades: List[Trade]) -> PerfMetrics:
    if not trades:
        return PerfMetrics(symbol, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    pnls   = [t.pnl_pct for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n      = len(trades)

    win_rate      = len(wins) / n
    total_return  = sum(pnls)
    avg_win       = float(np.mean(wins))   if wins   else 0.0
    avg_loss      = float(np.mean(losses)) if losses else 0.0
    rr_ratio      = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else float('inf')
    avg_hold      = float(np.mean([t.hold_bars for t in trades]))

    # MDD
    equity = np.cumsum(pnls)
    peak   = np.maximum.accumulate(equity)
    mdd    = float((equity - peak).min())
    equity_stability = float(np.std(equity)) if n > 1 else 0.0

    # ── 수익 구조 ──────────────────────────────────────────────────────────
    mfe_list    = [t.mfe_pct for t in trades]
    avg_mfe_pct = float(np.mean(mfe_list)) if mfe_list else 0.0

    cap_pairs = [(t.pnl_pct * 100, t.mfe_pct) for t in trades if t.mfe_pct > 0.1]
    if cap_pairs:
        ratios = [pnl / mfe for pnl, mfe in cap_pairs]
        capture_rate = max(-2.0, min(float(np.mean(ratios)), 1.0))
    else:
        capture_rate = 0.0

    big_winner_ratio = sum(1 for t in trades if t.pnl_pct * 100 >= 10.0) / n
    mfe_10plus_ratio = sum(1 for t in trades if t.mfe_pct >= 10.0) / n

    # ── 손실 구조 (MAE) ────────────────────────────────────────────────────
    # mae_pct는 음수로 저장됨 → 양수로 변환
    mae_list    = [abs(t.mae_pct) for t in trades]
    avg_mae_pct = float(np.mean(mae_list)) if mae_list else 0.0
    mae_3plus_ratio = sum(1 for v in mae_list if v > 3.0) / n

    sl_reasons  = {'SL', 'BE_STOP'}
    sl_hit_ratio = sum(1 for t in trades if t.exit_reason in sl_reasons) / n

    # ── 시간대별 수익 구조 (일봉 기준) ────────────────────────────────────
    ret_d1,     cnt_d1     = _bucket_ret(trades,  1,  1)
    ret_d2_5,   cnt_d2_5   = _bucket_ret(trades,  2,  5)
    ret_d6_14,  cnt_d6_14  = _bucket_ret(trades,  6, 14)
    ret_d15plus, cnt_d15plus = _bucket_ret(trades, 15, 9999)

    # exit_reason 분포
    reason_dist: dict = {}
    for t in trades:
        reason_dist[t.exit_reason] = reason_dist.get(t.exit_reason, 0) + 1

    return PerfMetrics(
        symbol            = symbol,
        trades            = n,
        win_rate          = round(win_rate, 3),
        total_return      = round(total_return, 4),
        avg_win           = round(avg_win, 4),
        avg_loss          = round(avg_loss, 4),
        rr_ratio          = round(rr_ratio, 2),
        mdd               = round(mdd, 4),
        profit_factor     = round(profit_factor, 2),
        avg_hold_bars     = round(avg_hold, 1),
        avg_mfe_pct       = round(avg_mfe_pct, 2),
        capture_rate      = round(capture_rate, 3),
        big_winner_ratio  = round(big_winner_ratio, 3),
        mfe_10plus_ratio  = round(mfe_10plus_ratio, 3),
        avg_mae_pct       = round(avg_mae_pct, 2),
        mae_3plus_ratio   = round(mae_3plus_ratio, 3),
        sl_hit_ratio      = round(sl_hit_ratio, 3),
        ret_d1            = ret_d1,
        ret_d2_5          = ret_d2_5,
        ret_d6_14         = ret_d6_14,
        ret_d15plus       = ret_d15plus,
        cnt_d1            = cnt_d1,
        cnt_d2_5          = cnt_d2_5,
        cnt_d6_14         = cnt_d6_14,
        cnt_d15plus       = cnt_d15plus,
        equity_stability  = round(equity_stability, 4),
        exit_reason_dist  = reason_dist,
    )


def _safe(v: float, default: float = 0.0) -> float:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return default
    return v


def aggregate(metrics_list: List[PerfMetrics], mode: str = 'tp_sl') -> dict:
    """전 종목 합산 지표."""
    non_empty    = [m for m in metrics_list if m.trades > 0]
    total_trades = sum(m.trades for m in metrics_list)
    all_wins     = sum(m.trades * m.win_rate for m in non_empty)

    def _avg(lst): return float(np.mean(lst)) if lst else 0.0

    rr_list  = [m.rr_ratio        for m in non_empty if math.isfinite(m.rr_ratio)]
    total_ret = sum(m.total_return for m in metrics_list)
    passed    = sum(1 for m in metrics_list if m.passed(mode))

    # 거래 수 가중 평균 (시간대별은 거래 수가 달라서 단순 평균보다 정확)
    def _wavg(vals, weights):
        total_w = sum(weights)
        if total_w == 0:
            return 0.0
        return sum(v * w for v, w in zip(vals, weights)) / total_w

    return {
        'total_trades':       total_trades,
        'overall_win_rate':   round(all_wins / total_trades, 3) if total_trades else 0,
        'avg_rr':             round(_safe(_avg(rr_list)), 2),
        'avg_mdd':            round(_safe(_avg([m.mdd for m in non_empty])), 4),
        'total_return':       round(total_ret, 4),
        'passed_symbols':     f'{passed}/{len(metrics_list)}',
        # 수익 구조
        'avg_capture_rate':   round(_safe(_avg([m.capture_rate    for m in non_empty])), 3),
        'avg_mfe_pct':        round(_safe(_avg([m.avg_mfe_pct     for m in non_empty])), 2),
        'big_winner_ratio':   round(_safe(_avg([m.big_winner_ratio for m in non_empty])), 3),
        'mfe_10plus_ratio':   round(_safe(_avg([m.mfe_10plus_ratio for m in non_empty])), 3),
        # 손실 구조
        'avg_mae_pct':        round(_safe(_avg([m.avg_mae_pct      for m in non_empty])), 2),
        'mae_3plus_ratio':    round(_safe(_avg([m.mae_3plus_ratio  for m in non_empty])), 3),
        'sl_hit_ratio':       round(_safe(_avg([m.sl_hit_ratio     for m in non_empty])), 3),
        # 시간대별 수익 (거래 수 가중 평균)
        'ret_d1':     round(_safe(_wavg(
            [m.ret_d1     for m in non_empty],
            [m.cnt_d1     for m in non_empty])), 2),
        'ret_d2_5':   round(_safe(_wavg(
            [m.ret_d2_5   for m in non_empty],
            [m.cnt_d2_5   for m in non_empty])), 2),
        'ret_d6_14':  round(_safe(_wavg(
            [m.ret_d6_14  for m in non_empty],
            [m.cnt_d6_14  for m in non_empty])), 2),
        'ret_d15plus': round(_safe(_wavg(
            [m.ret_d15plus for m in non_empty],
            [m.cnt_d15plus for m in non_empty])), 2),
        'cnt_d1':     sum(m.cnt_d1      for m in non_empty),
        'cnt_d2_5':   sum(m.cnt_d2_5    for m in non_empty),
        'cnt_d6_14':  sum(m.cnt_d6_14   for m in non_empty),
        'cnt_d15plus': sum(m.cnt_d15plus for m in non_empty),
        'equity_stability': round(_safe(_avg([m.equity_stability for m in non_empty])), 4),
    }
