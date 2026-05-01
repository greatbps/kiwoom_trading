"""
DEFENSIVE + SHORT 통합 백테스트 (오프라인)

Usage:
    python -m analysis.defensive_short_backtest [--days 60] [--no-chart]

레짐 기반 전략 시뮬레이션:
  - TRADE_OK  → SMC (기존)  ← 이번 분석 범위 외
  - NO_TRADE_DAY → DEFENSIVE (반등) 또는 SHORT (추세 하락)

핵심 지표:
  - 전략별 승률 / 기대값 / Profit Factor
  - 레짐별 성과 비교
  - MDD

yfinance 5분봉 기반 (최대 60일).
"""

import argparse
import json
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── 인버스 ETF ticker (yfinance) ─────────────────────────────────────
INVERSE_TICKER = "252670.KQ"   # KODEX 200선물인버스2X
KOSDAQ_TICKER  = "229200.KQ"   # KODEX 코스닥150
KOSPI_TICKER   = "069500.KS"   # KODEX 200


# ── 데이터클래스 ──────────────────────────────────────────────────────

@dataclass
class TradeResult:
    strategy:    str        # "DEFENSIVE" | "SHORT"
    entry_time:  datetime
    exit_time:   datetime
    entry_price: float
    exit_price:  float
    pnl_pct:     float      # 수익률 %
    exit_reason: str        # "TP" | "SL" | "TRAILING" | "TIME_EXIT"
    params:      Dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    strategy:      str
    trades:        List[TradeResult]
    win_rate:      float
    avg_win:       float
    avg_loss:      float
    expectancy:    float    # (win_rate * avg_win) - (loss_rate * avg_loss)
    profit_factor: float
    mdd:           float    # 최대 낙폭 %
    total_pnl:     float
    num_trades:    int
    params:        Dict = field(default_factory=dict)


# ── yfinance 데이터 로드 ──────────────────────────────────────────────

def _fetch_yf(ticker: str, days: int, interval: str = "5m") -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        end   = datetime.now()
        start = end - timedelta(days=days)
        df = yf.download(ticker, start=start, end=end, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() if isinstance(c, str) else c[0].lower()
                      for c in df.columns]
        df = df.rename(columns={"adj close": "close"}).dropna()
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("Asia/Seoul").tz_localize(None)
        return df
    except Exception as e:
        print(f"  [WARN] yfinance 로드 실패 ({ticker}): {e}")
        return None


# ── 지표 계산 ────────────────────────────────────────────────────────

def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema60"] = df["close"].ewm(span=60, adjust=False).mean()

    # RSI-14
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # VWAP (당일 누적)
    df["date"]   = df.index.date
    df["tp"]     = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]
    df["cum_tpvol"] = df.groupby("date")["tp_vol"].cumsum()
    df["cum_vol"]   = df.groupby("date")["volume"].cumsum()
    df["vwap"]  = df["cum_tpvol"] / df["cum_vol"].replace(0, np.nan)
    df = df.drop(columns=["tp", "tp_vol", "cum_tpvol", "cum_vol", "date"])
    return df


# ── 레짐 판단 (간이 버전) ────────────────────────────────────────────
# 실거래 market_context.py의 간소화 버전
# EMA20 < EMA60 + 전봉 대비 하락 → NO_TRADE_DAY

def _classify_regime(row_idx: int, mkt_df: pd.DataFrame) -> str:
    """단순 레짐 판별: EMA20 < EMA60 AND 전봉 대비 하락 → NO_TRADE_DAY"""
    if row_idx < 2:
        return "TRADE_OK"
    try:
        ema20 = mkt_df["ema20"].iloc[row_idx]
        ema60 = mkt_df["ema60"].iloc[row_idx]
        close_now  = mkt_df["close"].iloc[row_idx]
        close_prev = mkt_df["close"].iloc[row_idx - 1]
        if ema20 < ema60 and close_now < close_prev:
            return "NO_TRADE_DAY"
    except Exception:
        pass
    return "TRADE_OK"


# ── DEFENSIVE 신호 판별 ───────────────────────────────────────────────

def _check_defensive_signal(df: pd.DataFrame, i: int, params: Dict) -> bool:
    """DEFENSIVE 진입 조건 (main_auto_trading._check_defensive_entry 미러)"""
    if i < 22:
        return False
    try:
        max_rsi     = params.get("max_rsi", 30)
        ema_dev_min = params.get("min_ema20_deviation_pct", -1.5) / 100
        vol_ratio   = params.get("min_volume_ratio", 2.0)

        close  = float(df["close"].iloc[i])
        high_  = float(df["high"].iloc[i])
        rsi    = float(df["rsi"].iloc[i])
        ema20  = float(df["ema20"].iloc[i])
        vwap   = float(df["vwap"].iloc[i]) if "vwap" in df.columns else 0

        if rsi >= max_rsi:
            return False
        if ema20 <= 0:
            return False
        if (close - ema20) / ema20 > ema_dev_min:
            return False

        # 거래량 스파이크
        vol_now = float(df["volume"].iloc[i])
        vol_avg = float(df["volume"].iloc[max(0, i-21):i].mean())
        if vol_avg <= 0 or vol_now / vol_avg < vol_ratio:
            return False

        # VWAP reclaim
        if vwap > 0 and i >= 1:
            close_prev = float(df["close"].iloc[i - 1])
            vwap_prev  = float(df["vwap"].iloc[i - 1])
            if not (close_prev < vwap_prev and high_ >= vwap):
                return False

        # 반등 캔들
        if i >= 1:
            if close <= float(df["close"].iloc[i - 1]):
                return False

        return True
    except Exception:
        return False


# ── SHORT 신호 판별 ───────────────────────────────────────────────────

def _check_short_signal(df: pd.DataFrame, i: int, params: Dict) -> bool:
    """SHORT 진입 조건 (main_auto_trading._check_short_entry 미러, v2 필터 포함)"""
    if i < 30:
        return False
    try:
        breakdown_thr = params.get("min_breakdown_pct", -0.5)
        vol_min       = params.get("min_volume_ratio", 1.2)

        close  = float(df["close"].iloc[i])
        open_  = float(df["open"].iloc[i])
        high_  = float(df["high"].iloc[i])
        low_   = float(df["low"].iloc[i])
        ema20  = float(df["ema20"].iloc[i])
        ema60  = float(df["ema60"].iloc[i])

        # EMA 약세
        if ema20 >= ema60:
            return False

        # breakdown
        recent_low = float(df["low"].iloc[max(0, i-6):i].min())
        if recent_low <= 0:
            return False
        bd_pct = (close - recent_low) / recent_low * 100
        if bd_pct > breakdown_thr:
            return False

        # 거래량
        vol_now = float(df["volume"].iloc[i])
        vol_avg = float(df["volume"].iloc[max(0, i-21):i].mean())
        if vol_avg <= 0 or vol_now / vol_avg < vol_min:
            return False

        # 리테스트 실패
        recent_high = float(df["high"].iloc[max(0, i-6):i].max())
        if close >= recent_high:
            return False

        # VWAP 아래
        if "vwap" in df.columns:
            vwap = float(df["vwap"].iloc[i])
            if vwap > 0 and close >= vwap:
                return False

        # RSI 반등 아님
        if i >= 1:
            rsi_now  = float(df["rsi"].iloc[i])
            rsi_prev = float(df["rsi"].iloc[i - 1])
            if rsi_now > rsi_prev:
                return False

        # 음봉 (몸통 >= 레인지 50%)
        candle_range = high_ - low_
        body = abs(close - open_)
        if candle_range > 0:
            if not (close < open_ and body / candle_range >= 0.5):
                return False

        return True
    except Exception:
        return False


# ── 포지션 시뮬레이션 ────────────────────────────────────────────────

def _simulate_defensive(entry_i: int, df: pd.DataFrame, params: Dict) -> TradeResult:
    """DEFENSIVE 포지션 청산 시뮬레이션"""
    entry_price = float(df["close"].iloc[entry_i])
    entry_time  = df.index[entry_i]
    sl_price    = entry_price * (1 - params.get("stop_loss_pct", 0.8) / 100)
    tp_price    = entry_price * (1 + params.get("take_profit_pct", 1.0) / 100)
    max_hold    = params.get("max_hold_minutes", 10)
    bar_limit   = max_hold  # 5분봉 기준 (1봉 = 5분)

    for j in range(1, min(bar_limit + 1, len(df) - entry_i)):
        idx   = entry_i + j
        high_ = float(df["high"].iloc[idx])
        low_  = float(df["low"].iloc[idx])
        close = float(df["close"].iloc[idx])

        if low_ <= sl_price:
            pnl = (sl_price - entry_price) / entry_price * 100
            return TradeResult("DEFENSIVE", entry_time, df.index[idx],
                               entry_price, sl_price, pnl, "SL", params)
        if high_ >= tp_price:
            pnl = (tp_price - entry_price) / entry_price * 100
            return TradeResult("DEFENSIVE", entry_time, df.index[idx],
                               entry_price, tp_price, pnl, "TP", params)

    # 시간 초과
    exit_idx   = min(entry_i + bar_limit, len(df) - 1)
    exit_price = float(df["close"].iloc[exit_idx])
    pnl        = (exit_price - entry_price) / entry_price * 100
    return TradeResult("DEFENSIVE", entry_time, df.index[exit_idx],
                       entry_price, exit_price, pnl, "TIME_EXIT", params)


def _simulate_short(entry_i: int, inv_df: pd.DataFrame, params: Dict) -> Optional[TradeResult]:
    """SHORT 포지션 청산 시뮬레이션 (인버스 ETF 매수)"""
    if entry_i >= len(inv_df) - 1:
        return None
    entry_price  = float(inv_df["close"].iloc[entry_i])
    entry_time   = inv_df.index[entry_i]
    sl_price     = entry_price * (1 - params.get("stop_loss_pct", 1.2) / 100)
    tp_activate  = entry_price * (1 + params.get("take_profit_pct", 2.0) / 100)
    trailing_pct = params.get("trailing_stop_pct", 0.8) / 100
    trailing_stop: Optional[float] = None
    highest      = entry_price
    max_bars     = 60  # 최대 5시간 (5분봉 60봉)

    for j in range(1, min(max_bars + 1, len(inv_df) - entry_i)):
        idx   = entry_i + j
        high_ = float(inv_df["high"].iloc[idx])
        low_  = float(inv_df["low"].iloc[idx])

        # 고점 갱신 → 트레일링 스탑 업데이트
        if high_ > highest:
            highest = high_
        if highest >= tp_activate:
            trailing_stop = highest * (1 - trailing_pct)

        # 손절
        if low_ <= sl_price and trailing_stop is None:
            pnl = (sl_price - entry_price) / entry_price * 100
            return TradeResult("SHORT", entry_time, inv_df.index[idx],
                               entry_price, sl_price, pnl, "SL", params)

        # 트레일링 스탑
        if trailing_stop and low_ <= trailing_stop:
            pnl = (trailing_stop - entry_price) / entry_price * 100
            return TradeResult("SHORT", entry_time, inv_df.index[idx],
                               entry_price, trailing_stop, pnl, "TRAILING", params)

    # 시간 초과 청산
    exit_idx   = min(entry_i + max_bars, len(inv_df) - 1)
    exit_price = float(inv_df["close"].iloc[exit_idx])
    pnl        = (exit_price - entry_price) / entry_price * 100
    return TradeResult("SHORT", entry_time, inv_df.index[exit_idx],
                       entry_price, exit_price, pnl, "TIME_EXIT", params)


# ── 지표 계산 ────────────────────────────────────────────────────────

def _calc_metrics(trades: List[TradeResult], strategy: str, params: Dict) -> BacktestResult:
    if not trades:
        return BacktestResult(strategy, [], 0, 0, 0, 0, 0, 0, 0, 0, params)

    wins   = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    win_rate  = len(wins) / len(trades)
    avg_win   = float(np.mean([t.pnl_pct for t in wins]))  if wins   else 0.0
    avg_loss  = float(np.mean([abs(t.pnl_pct) for t in losses])) if losses else 0.0
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    total_profit = sum(t.pnl_pct for t in wins)
    total_loss   = sum(abs(t.pnl_pct) for t in losses)
    profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

    # MDD (누적 손익 기반)
    cum_pnl = np.cumsum([t.pnl_pct for t in trades])
    peak    = np.maximum.accumulate(cum_pnl)
    drawdowns = peak - cum_pnl
    mdd     = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0

    total_pnl = sum(t.pnl_pct for t in trades)

    return BacktestResult(
        strategy=strategy,
        trades=trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=profit_factor,
        mdd=mdd,
        total_pnl=total_pnl,
        num_trades=len(trades),
        params=params,
    )


# ── 메인 백테스트 실행 ────────────────────────────────────────────────

class DefensiveShortBacktest:
    """DEFENSIVE + SHORT 통합 백테스트"""

    def __init__(self, days: int = 60):
        self.days = days
        self.mkt_df:  Optional[pd.DataFrame] = None  # KOSDAQ 지수 (레짐 판별)
        self.sig_df:  Optional[pd.DataFrame] = None  # 신호 판별용 df (같은 지수)
        self.inv_df:  Optional[pd.DataFrame] = None  # 인버스 ETF df

    def load_data(self) -> bool:
        print(f"[BT] 데이터 로드 중 (최근 {self.days}일)...")
        self.mkt_df = _fetch_yf(KOSDAQ_TICKER, self.days, "5m")
        self.inv_df = _fetch_yf(INVERSE_TICKER, self.days, "5m")

        if self.mkt_df is None or self.inv_df is None:
            print("  [ERROR] 데이터 로드 실패")
            return False

        self.mkt_df = _add_indicators(self.mkt_df)
        self.inv_df = _add_indicators(self.inv_df)
        self.sig_df = self.mkt_df  # 신호 판별 = KOSDAQ 지수 기준
        print(f"  KOSDAQ: {len(self.mkt_df)}봉 | 인버스ETF: {len(self.inv_df)}봉")
        return True

    def run_defensive(self, params: Dict) -> BacktestResult:
        """DEFENSIVE 전략 백테스트"""
        trades: List[TradeResult] = []
        if self.sig_df is None:
            return _calc_metrics([], "DEFENSIVE", params)

        in_position = False
        for i in range(30, len(self.sig_df) - 1):
            if in_position:
                continue

            regime = _classify_regime(i, self.mkt_df)
            if regime != "NO_TRADE_DAY":
                continue

            # 시간 필터: 10:30~13:30
            t = self.sig_df.index[i]
            if not (10 * 60 + 30 <= t.hour * 60 + t.minute <= 13 * 60 + 30):
                continue

            if _check_defensive_signal(self.sig_df, i, params):
                result = _simulate_defensive(i, self.sig_df, params)
                trades.append(result)
                in_position = True
                # 간이: 청산 후 다음 봉부터 재진입 허용
                in_position = False

        return _calc_metrics(trades, "DEFENSIVE", params)

    def run_short(self, params: Dict) -> BacktestResult:
        """SHORT 전략 백테스트"""
        trades: List[TradeResult] = []
        if self.sig_df is None or self.inv_df is None:
            return _calc_metrics([], "SHORT", params)

        in_position = False
        for i in range(30, len(self.sig_df) - 1):
            if in_position:
                continue

            regime = _classify_regime(i, self.mkt_df)
            if regime != "NO_TRADE_DAY":
                continue

            # DEFENSIVE 신호 있으면 SHORT 금지
            def_params = {"max_rsi": 30, "min_ema20_deviation_pct": -1.5,
                          "min_volume_ratio": 2.0}
            if _check_defensive_signal(self.sig_df, i, def_params):
                continue

            # 시간 필터: 09:30~14:30
            t = self.sig_df.index[i]
            if not (9 * 60 + 30 <= t.hour * 60 + t.minute <= 14 * 60 + 30):
                continue

            if _check_short_signal(self.sig_df, i, params):
                # 인버스 ETF에서 같은 시점 찾기
                try:
                    inv_i = self.inv_df.index.get_indexer([self.sig_df.index[i]],
                                                           method="nearest")[0]
                except Exception:
                    continue
                if inv_i < 0 or inv_i >= len(self.inv_df) - 1:
                    continue

                result = _simulate_short(inv_i, self.inv_df, params)
                if result:
                    trades.append(result)
                in_position = False

        return _calc_metrics(trades, "SHORT", params)

    def print_report(self, def_result: BacktestResult, short_result: BacktestResult):
        """결과 출력"""
        sep = "=" * 60
        print(f"\n{sep}")
        print(f"  DEFENSIVE + SHORT 백테스트 결과 (최근 {self.days}일)")
        print(sep)

        for r in [def_result, short_result]:
            print(f"\n  [{r.strategy}] 파라미터: {r.params}")
            print(f"    거래 수     : {r.num_trades}회")
            if r.num_trades == 0:
                print("    → 신호 없음")
                continue
            print(f"    승률        : {r.win_rate*100:.1f}%")
            print(f"    평균 수익   : +{r.avg_win:.2f}%")
            print(f"    평균 손실   : -{r.avg_loss:.2f}%")
            print(f"    기대값(E)   : {r.expectancy:+.3f}%  {'✅' if r.expectancy > 0 else '❌'}")
            print(f"    Profit Factor: {r.profit_factor:.2f}  {'✅' if r.profit_factor >= 1.5 else '⚠️'}")
            print(f"    MDD         : -{r.mdd:.2f}%  {'✅' if r.mdd < 15 else '❌'}")
            print(f"    총 손익     : {r.total_pnl:+.2f}%")

            # 청산 이유 분포
            from collections import Counter
            reasons = Counter(t.exit_reason for t in r.trades)
            print(f"    청산 이유   : {dict(reasons)}")

        # 종합 평가
        print(f"\n  {'─'*56}")
        print("  종합 평가:")
        combined_e = (def_result.expectancy * def_result.num_trades
                      + short_result.expectancy * short_result.num_trades) \
                     / max(def_result.num_trades + short_result.num_trades, 1)
        print(f"    통합 기대값 : {combined_e:+.3f}%")
        if combined_e > 0.25:
            print("    → ✅ 실전 투입 검토 가능 (E > 0.25)")
        elif combined_e > 0:
            print("    → ⚠️ 추가 튜닝 필요 (E > 0 but < 0.25)")
        else:
            print("    → ❌ 전략 재검토 필요 (E < 0)")
        print(f"{sep}\n")

    def save_results(self, def_result: BacktestResult, short_result: BacktestResult):
        """결과 JSON 저장"""
        os.makedirs("logs", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = {
            "generated_at": ts,
            "days": self.days,
            "defensive": {
                "params":        def_result.params,
                "num_trades":    def_result.num_trades,
                "win_rate":      round(def_result.win_rate, 4),
                "expectancy":    round(def_result.expectancy, 4),
                "profit_factor": round(def_result.profit_factor, 4),
                "mdd":           round(def_result.mdd, 4),
                "total_pnl":     round(def_result.total_pnl, 4),
            },
            "short": {
                "params":        short_result.params,
                "num_trades":    short_result.num_trades,
                "win_rate":      round(short_result.win_rate, 4),
                "expectancy":    round(short_result.expectancy, 4),
                "profit_factor": round(short_result.profit_factor, 4),
                "mdd":           round(short_result.mdd, 4),
                "total_pnl":     round(short_result.total_pnl, 4),
            },
        }
        path = f"logs/defensive_short_bt_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"  결과 저장: {path}")
        return path

    def visualize(self, def_result: BacktestResult, short_result: BacktestResult):
        """누적 손익 차트"""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(1, 2, figsize=(14, 5))
            fig.suptitle(f"DEFENSIVE + SHORT Backtest (최근 {self.days}일)", fontsize=13)

            for ax, r in zip(axes, [def_result, short_result]):
                if r.num_trades == 0:
                    ax.set_title(f"{r.strategy}: 신호 없음")
                    continue
                cum = np.cumsum([t.pnl_pct for t in r.trades])
                ax.plot(cum, marker="o", markersize=3,
                        color="steelblue" if r.strategy == "DEFENSIVE" else "tomato")
                ax.axhline(0, color="gray", linestyle="--", linewidth=0.8)
                ax.set_title(
                    f"{r.strategy}  E={r.expectancy:+.3f}% | WR={r.win_rate*100:.0f}% | "
                    f"PF={r.profit_factor:.2f} | MDD={r.mdd:.1f}%"
                )
                ax.set_xlabel("거래 #")
                ax.set_ylabel("누적 손익 %")

            plt.tight_layout()
            os.makedirs("logs", exist_ok=True)
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = f"logs/defensive_short_bt_{ts}.png"
            plt.savefig(out, dpi=120)
            plt.close()
            print(f"  차트 저장: {out}")
        except Exception as e:
            print(f"  [WARN] 차트 생성 실패: {e}")


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DEFENSIVE + SHORT 백테스트")
    parser.add_argument("--days",     type=int, default=60, help="백테스트 기간 (일)")
    parser.add_argument("--no-chart", action="store_true",  help="차트 출력 생략")
    args = parser.parse_args()

    bt = DefensiveShortBacktest(days=args.days)
    if not bt.load_data():
        return

    # 기본 파라미터로 실행
    def_params   = {"max_rsi": 30, "min_ema20_deviation_pct": -1.5,
                    "min_volume_ratio": 2.0, "stop_loss_pct": 0.8,
                    "take_profit_pct": 1.0, "max_hold_minutes": 10}
    short_params = {"min_breakdown_pct": -0.5, "min_volume_ratio": 1.2,
                    "stop_loss_pct": 1.2, "take_profit_pct": 2.0,
                    "trailing_stop_pct": 0.8}

    print("[BT] DEFENSIVE 백테스트 실행...")
    def_r   = bt.run_defensive(def_params)
    print("[BT] SHORT 백테스트 실행...")
    short_r = bt.run_short(short_params)

    bt.print_report(def_r, short_r)
    bt.save_results(def_r, short_r)
    if not args.no_chart:
        bt.visualize(def_r, short_r)


if __name__ == "__main__":
    main()
