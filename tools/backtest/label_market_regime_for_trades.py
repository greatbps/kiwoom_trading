"""
tools/backtest/label_market_regime_for_trades.py
Market Regime Gate v1.4 백테스트 검증 스크립트

목적:
    과거 거래 히스토리에 v1.4 레짐 라벨을 붙이고,
    v1.3.1-final / v1.3.2 / v1.4 3개 버전의 성과를 비교한다.

핵심 원칙:
    - Look-ahead 금지: 진입 당일(T) 데이터는 사용하지 않음. T-1 종가까지만.
    - 역사적 KOSPI/KOSDAQ: yfinance ^KS11, ^KQ11
    - 레짐 score: 현재 config/strategy_hybrid.yaml과 동일한 규칙 적용

Usage:
    python -m tools.backtest.label_market_regime_for_trades
    python -m tools.backtest.label_market_regime_for_trades --days 120
    python -m tools.backtest.label_market_regime_for_trades --from 2025-11-01 --to 2026-07-01

Output:
    reports/regime/trade_regime_labeled_YYYYMMDD.csv
    reports/regime/v14_removed_trades_YYYYMMDD.csv
    reports/regime/v14_regime_backtest_summary_YYYYMMDD.md

2026-07-05 최초 작성
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent.parent
REPORT_DIR = ROOT / "reports" / "regime"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DB_CFG = dict(dbname='trading_system', user='postgres', password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

TODAY = datetime.now().strftime('%Y%m%d')

# ── 기본 설정 (config/strategy_hybrid.yaml market_regime 섹션과 동일) ──────

DEFAULT_CFG = {
    "ema_period": 20,
    "score_thresholds": {"trend_up_min": 3, "risk_off_max": -3},
    "risk_off_rules": {"kospi_day_drop": -0.015, "kosdaq_day_drop": -0.020},
    "trend_up_size_mult": 1.0,
    "neutral_size_mult": 0.7,
    "risk_off_size_mult": 0.0,
    "allow_rae_in_trend_up": True,
    "allow_rae_in_neutral": False,
    "allow_rae_in_risk_off": False,
    "fallback_on_error": "NEUTRAL",
}

# ── v1.3.2 필터 기준 ──────────────────────────────────────────────────────────

EARLY_WINDOW_START = 9 * 60       # 09:00
EARLY_WINDOW_END   = 9 * 60 + 30  # 09:30
MIN_GRADE_V132     = "A"          # B급 차단


# ═══════════════════════════════════════════════════════════════════════════
# 1. 역사적 지수 데이터 로드 (yfinance)
# ═══════════════════════════════════════════════════════════════════════════

def _load_index_daily(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """^KS11, ^KQ11 일봉 로드 (look-ahead 없는 날짜 필터링을 위해 충분히 앞에서 로드)."""
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df is None or len(df) < 5:
            return None
        # MultiIndex 정리
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df = df[['Close']].copy()
        df.columns = ['close']
        df.index = pd.to_datetime(df.index).date
        df.index.name = 'date'
        df = df.sort_index()
        df['close'] = df['close'].astype(float)
        return df
    except Exception as e:
        print(f"  [WARN] {ticker} 데이터 로드 실패: {e}")
        return None


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


class HistoricalRegimeCalculator:
    """
    특정 날짜(asof_date) 기준으로, 그 이전 일봉 데이터만 사용해서 레짐을 계산.

    핵심: T일 진입 → T-1까지의 일봉 데이터만 사용 (look-ahead 방지)
    """

    def __init__(self, kospi_df: Optional[pd.DataFrame],
                 kosdaq_df: Optional[pd.DataFrame],
                 cfg: Dict = None):
        self.kospi_df  = kospi_df
        self.kosdaq_df = kosdaq_df
        self.cfg = cfg or DEFAULT_CFG
        self._cache: Dict[date, Dict] = {}

    def compute_for_entry(self, entry_dt: datetime) -> Dict:
        """
        진입 시점 기준 레짐 계산.

        핵심 규칙:
        - 장중 진입(T일): T-1까지 확정된 일봉만 사용
        - T일 종가는 절대 사용 안 함
        """
        entry_date = entry_dt.date()
        # T-1 일봉까지만 사용 (당일 종가 look-ahead 방지)
        cutoff_date = entry_date - timedelta(days=1)

        if cutoff_date in self._cache:
            return self._cache[cutoff_date]

        result = self._compute_regime(cutoff_date)
        self._cache[cutoff_date] = result
        return result

    def _compute_regime(self, cutoff_date: date) -> Dict:
        """cutoff_date까지의 일봉으로 레짐 계산."""
        ema_period = int(self.cfg.get("ema_period", 20))
        score = 0
        reasons = []
        details = {}

        # KOSPI 점수
        k_score, k_reasons, k_last_date = self._score_index(
            self.kospi_df, "KOSPI", ema_period, cutoff_date,
            self.cfg["risk_off_rules"]["kospi_day_drop"]
        )
        # KOSDAQ 점수
        q_score, q_reasons, q_last_date = self._score_index(
            self.kosdaq_df, "KOSDAQ", ema_period, cutoff_date,
            self.cfg["risk_off_rules"]["kosdaq_day_drop"]
        )

        score += k_score + q_score
        reasons += k_reasons + q_reasons
        details["kospi_last_date"] = str(k_last_date) if k_last_date else None
        details["kosdaq_last_date"] = str(q_last_date) if q_last_date else None

        if not reasons:
            regime = self.cfg.get("fallback_on_error", "NEUTRAL")
            reasons = ["INSUFFICIENT_HISTORY_FALLBACK"]
            score = 0
        else:
            thresholds = self.cfg["score_thresholds"]
            if score >= thresholds["trend_up_min"]:
                regime = "TREND_UP"
            elif score <= thresholds["risk_off_max"]:
                regime = "RISK_OFF"
            else:
                regime = "NEUTRAL"

        return {
            "regime": regime,
            "score": score,
            "reasons": reasons,
            "cutoff_date": str(cutoff_date),
            "kospi_last_date": details.get("kospi_last_date"),
            "kosdaq_last_date": details.get("kosdaq_last_date"),
        }

    def _score_index(
        self,
        df: Optional[pd.DataFrame],
        label: str,
        ema_period: int,
        cutoff_date: date,
        drop_threshold: float,
    ) -> Tuple[int, List[str], Optional[date]]:
        """단일 지수의 score, reasons, 마지막 사용 날짜 반환."""
        if df is None or len(df) == 0:
            return 0, [], None

        # cutoff_date까지만 slice
        sliced = df[df.index <= cutoff_date]
        if len(sliced) < ema_period + 2:
            return 0, [f"{label}_INSUFFICIENT_{len(sliced)}bars"], None

        last_date = sliced.index[-1]
        close = sliced["close"]
        ema = _calc_ema(close, ema_period)

        current_close = float(close.iloc[-1])
        current_ema   = float(ema.iloc[-1])
        prev_ema      = float(ema.iloc[-2])

        score = 0
        reasons = []

        # 피처 1: 종가 > EMA20
        if current_close > current_ema:
            score += 1
            reasons.append(f"{label}_above_EMA{ema_period}")
        else:
            score -= 1
            reasons.append(f"{label}_below_EMA{ema_period}")

        # 피처 2: EMA 기울기
        if current_ema > prev_ema:
            score += 1
            reasons.append(f"{label}_EMA_slope_up")
        else:
            score -= 1
            reasons.append(f"{label}_EMA_slope_down")

        # 피처 3: 일간 수익률 급락
        if len(close) >= 2:
            prev_close = float(close.iloc[-2])
            if prev_close > 0:
                day_ret = (current_close - prev_close) / prev_close
                if day_ret <= drop_threshold:
                    score -= 1
                    reasons.append(f"{label}_day_drop_{day_ret*100:.1f}pct")

        return score, reasons, last_date


# ═══════════════════════════════════════════════════════════════════════════
# 2. 거래 DB 쿼리
# ═══════════════════════════════════════════════════════════════════════════

PAIR_QUERY = """
WITH paired AS (
  SELECT DISTINCT ON (b.trade_id)
    b.trade_id,
    b.stock_code,
    b.stock_name,
    b.trade_time AS buy_time,
    b.price      AS buy_price,
    b.entry_reason,
    b.position_size_mult,
    b.condition_name,
    EXTRACT(HOUR FROM b.trade_time)*60 + EXTRACT(MINUTE FROM b.trade_time) AS entry_min,
    s.profit_rate,
    s.exit_reason,
    s.exit_category,
    s.trade_time AS sell_time
  FROM trades b
  JOIN trades s ON b.stock_code = s.stock_code
    AND s.trade_type = 'SELL'
    AND s.trade_time > b.trade_time
    AND s.trade_time <= b.trade_time + INTERVAL '30 hours'
  WHERE b.trade_type = 'BUY'
    AND s.profit_rate IS NOT NULL
    {date_filter}
  ORDER BY b.trade_id, s.trade_time
)
SELECT * FROM paired ORDER BY buy_time
"""


def fetch_trades(date_from: str = None, date_to: str = None) -> List[Dict]:
    clauses = []
    if date_from:
        clauses.append(f"AND b.trade_time >= '{date_from}'")
    if date_to:
        clauses.append(f"AND b.trade_time <= '{date_to}'")

    conn = psycopg2.connect(**DB_CFG)
    try:
        cur = conn.cursor()
        cur.execute(PAIR_QUERY.format(date_filter=' '.join(clauses)))
        rows = cur.fetchall()
        cols  = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# 3. v1.3.x / v1.4 필터 판정
# ═══════════════════════════════════════════════════════════════════════════

def classify_entry_grade(entry_reason: str) -> str:
    r = entry_reason or ''
    if 'A+' in r or 'A급' in r or 'CHoCH[A급]' in r or 'CHoCH[A+]' in r:
        return 'A'
    if 'B급' in r or 'CHoCH[B급]' in r:
        return 'B'
    if 'CHoCH' in r or 'SMC' in r.upper():
        return 'UNKNOWN'
    return 'LEGACY'


def apply_v131_filters(trade: Dict) -> Dict:
    """v1.3.1-final: 필터 없음 (기준선)."""
    return {
        "blocked": False,
        "block_reason": None,
        "size_mult": 1.0,
    }


def apply_v132_filters(trade: Dict) -> Dict:
    """v1.3.2: B급 차단 + 09:00~09:30 차단."""
    entry_min = float(trade.get("entry_min") or 0)
    entry_reason = trade.get("entry_reason") or ''

    # [1] Early Window Block
    if EARLY_WINDOW_START <= entry_min < EARLY_WINDOW_END:
        return {"blocked": True, "block_reason": "EARLY_WINDOW_BLOCK", "size_mult": 0.0}

    # [2] B급 차단
    grade = classify_entry_grade(entry_reason)
    if grade == 'B':
        return {"blocked": True, "block_reason": "CHOCH_GRADE_BLOCK", "size_mult": 0.0}

    return {"blocked": False, "block_reason": None, "size_mult": 1.0}


def apply_v14_filters(trade: Dict, regime_result: Dict) -> Dict:
    """v1.4: v1.3.2 + Market Regime Gate."""
    # v1.3.2 필터 먼저
    v132 = apply_v132_filters(trade)
    if v132["blocked"]:
        return {**v132, "regime": regime_result["regime"], "regime_score": regime_result["score"]}

    regime = regime_result["regime"]
    cfg = DEFAULT_CFG

    if regime == "RISK_OFF":
        return {
            "blocked": True,
            "block_reason": "REGIME_BLOCK_RISK_OFF",
            "size_mult": 0.0,
            "regime": regime,
            "regime_score": regime_result["score"],
        }
    elif regime == "NEUTRAL":
        return {
            "blocked": False,
            "block_reason": None,
            "size_mult": cfg["neutral_size_mult"],
            "regime": regime,
            "regime_score": regime_result["score"],
        }
    else:  # TREND_UP
        return {
            "blocked": False,
            "block_reason": None,
            "size_mult": cfg["trend_up_size_mult"],
            "regime": regime,
            "regime_score": regime_result["score"],
        }


# ═══════════════════════════════════════════════════════════════════════════
# 4. 성과 집계
# ═══════════════════════════════════════════════════════════════════════════

def compute_mdd(pnls: List[float]) -> float:
    """누적 손익 기준 최대 낙폭."""
    if not pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > mdd:
            mdd = dd
    return round(mdd, 3)


def aggregate(trades: List[Dict], size_key: str = "size_mult") -> Dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "avg_pnl": 0.0,
                "total_pnl": 0.0, "mdd": 0.0, "alpha_rate": 0.0}
    pnls_raw = [float(t["profit_rate"] or 0) * t.get(size_key, 1.0) for t in trades]
    pnls_raw_base = [float(t["profit_rate"] or 0) for t in trades]
    wins  = [p for p in pnls_raw_base if p > 0]
    losses = [abs(p) for p in pnls_raw_base if p < 0]
    alpha_n = sum(1 for t in trades
                  if (t.get("exit_category") or '').upper() == 'ALPHA_EXIT')
    return {
        "n": len(trades),
        "wr": round(len(wins) / len(pnls_raw_base) * 100, 1) if pnls_raw_base else 0,
        "pf": round(sum(wins) / sum(losses), 3) if losses and sum(losses) > 0 else 0.0,
        "avg_pnl": round(sum(pnls_raw_base) / len(pnls_raw_base), 3) if pnls_raw_base else 0,
        "total_pnl": round(sum(pnls_raw), 3),
        "mdd": compute_mdd(pnls_raw),
        "alpha_rate": round(alpha_n / len(trades) * 100, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. 메인 실행
# ═══════════════════════════════════════════════════════════════════════════

def run(date_from: str = None, date_to: str = None):
    print(f"\n{'='*60}")
    print("v1.4 Market Regime Gate 백테스트 검증")
    print(f"  기간: {date_from or '전체'} ~ {date_to or '전체'}")
    print(f"  기준: look-ahead 없음 (진입T 기준 T-1 일봉까지)")
    print(f"{'='*60}\n")

    # ── 1. 역사적 지수 데이터 로드 ──────────────────────────────────────
    print("■ 지수 데이터 로드 (yfinance)...")
    hist_start = "2025-10-01"   # EMA20 계산 버퍼
    hist_end   = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    kospi_df  = _load_index_daily("^KS11", hist_start, hist_end)
    kosdaq_df = _load_index_daily("^KQ11", hist_start, hist_end)
    print(f"  KOSPI:  {len(kospi_df) if kospi_df is not None else 'FAIL'}봉")
    print(f"  KOSDAQ: {len(kosdaq_df) if kosdaq_df is not None else 'FAIL'}봉")

    calc = HistoricalRegimeCalculator(kospi_df, kosdaq_df, DEFAULT_CFG)

    # ── 2. 거래 데이터 로드 ──────────────────────────────────────────────
    print("\n■ 거래 데이터 로드...")
    trades_raw = fetch_trades(date_from, date_to)
    print(f"  BUY-SELL 페어링: {len(trades_raw)}건")

    if not trades_raw:
        print("  ⚠️ 분석할 거래 없음. 종료.")
        return

    # ── 3. 레짐 라벨링 ──────────────────────────────────────────────────
    print("\n■ 레짐 라벨링 중...")
    labeled = []
    for t in trades_raw:
        entry_dt = t["buy_time"]
        if entry_dt is None:
            regime_result = {"regime": "NEUTRAL", "score": 0,
                             "reasons": ["NULL_ENTRY_TIME"],
                             "cutoff_date": None,
                             "kospi_last_date": None, "kosdaq_last_date": None}
        else:
            regime_result = calc.compute_for_entry(entry_dt)

        v131 = apply_v131_filters(t)
        v132 = apply_v132_filters(t)
        v14  = apply_v14_filters(t, regime_result)

        labeled.append({
            **t,
            "buy_time_str": entry_dt.strftime("%Y-%m-%d %H:%M") if entry_dt else "",
            "entry_date":   entry_dt.strftime("%Y-%m-%d") if entry_dt else "",
            "entry_grade":  classify_entry_grade(t.get("entry_reason") or ""),
            # 레짐 정보
            "regime":               regime_result["regime"],
            "regime_score":         regime_result["score"],
            "regime_reasons":       "|".join(regime_result["reasons"]),
            "regime_cutoff_date":   regime_result.get("cutoff_date"),
            "kospi_last_date":      regime_result.get("kospi_last_date"),
            "kosdaq_last_date":     regime_result.get("kosdaq_last_date"),
            # v1.3.1 판정
            "v131_blocked":         v131["blocked"],
            "v131_block_reason":    v131["block_reason"],
            "v131_size_mult":       v131["size_mult"],
            # v1.3.2 판정
            "v132_blocked":         v132["blocked"],
            "v132_block_reason":    v132["block_reason"],
            "v132_size_mult":       v132["size_mult"],
            # v1.4 판정
            "v14_blocked":          v14["blocked"],
            "v14_block_reason":     v14["block_reason"],
            "v14_size_mult":        v14["size_mult"],
        })

    # ── 4. 버전별 거래 분류 ──────────────────────────────────────────────
    v131_trades = [t for t in labeled if not t["v131_blocked"]]
    v132_trades = [t for t in labeled if not t["v132_blocked"]]
    v14_trades  = [t for t in labeled if not t["v14_blocked"]]

    # v1.4에서 NEUTRAL 거래에 size_mult 적용
    for t in v14_trades:
        t["v14_adj_size"] = t["v14_size_mult"]

    # ── 5. 성과 집계 ────────────────────────────────────────────────────
    stats_v131 = aggregate(v131_trades)
    stats_v132 = aggregate(v132_trades)
    stats_v14  = aggregate(v14_trades, size_key="v14_adj_size")

    # ── 6. 제거 거래 분석 ────────────────────────────────────────────────
    removed_v132 = [t for t in labeled if t["v132_blocked"]]
    removed_v14_only = [t for t in labeled if t["v14_blocked"] and not t["v132_blocked"]]

    # 제거 사유별 집계
    def group_by_reason(trades, reason_key):
        from collections import defaultdict
        g = defaultdict(list)
        for t in trades:
            g[t[reason_key]].append(t)
        return dict(g)

    removed_v14_by_reason = group_by_reason(
        [t for t in labeled if t["v14_blocked"]], "v14_block_reason"
    )

    # ── 7. 레짐별 성과 (v1.4 통과 거래 기준) ───────────────────────────
    regime_groups = {}
    for r in ["TREND_UP", "NEUTRAL", "RISK_OFF"]:
        grp = [t for t in v14_trades if t["regime"] == r]
        regime_groups[r] = {"trades": grp, "stats": aggregate(grp, size_key="v14_adj_size")}

    # ── 8. NEUTRAL size 0.7 효과 분석 ───────────────────────────────────
    neutral_trades = [t for t in v14_trades if t["regime"] == "NEUTRAL"]
    neutral_full   = aggregate(neutral_trades)                          # 원래 사이즈 (1.0)
    neutral_adj    = aggregate(neutral_trades, size_key="v14_adj_size") # 0.7 적용

    # ── 9. Look-ahead 검증 샘플 ─────────────────────────────────────────
    # 강세장/중립/급락 3개 샘플
    samples = []
    for regime_target in ["TREND_UP", "NEUTRAL", "RISK_OFF"]:
        candidates = [t for t in labeled if t["regime"] == regime_target]
        if candidates:
            samples.append(candidates[0])

    # ── 10. 결과 파일 저장 ──────────────────────────────────────────────
    print("\n■ 결과 저장...")

    # 10-A. 전체 레짐 라벨 CSV
    label_csv = REPORT_DIR / f"trade_regime_labeled_{TODAY}.csv"
    with open(label_csv, "w", newline="", encoding="utf-8") as f:
        if labeled:
            writer = csv.DictWriter(f, fieldnames=list(labeled[0].keys()))
            writer.writeheader()
            writer.writerows(labeled)
    print(f"  ✅ {label_csv}")

    # 10-B. 제거 거래 CSV
    removed_csv = REPORT_DIR / f"v14_removed_trades_{TODAY}.csv"
    removed_all = [t for t in labeled if t["v14_blocked"]]
    if removed_all:
        with open(removed_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(removed_all[0].keys()))
            writer.writeheader()
            writer.writerows(removed_all)
    print(f"  ✅ {removed_csv} ({len(removed_all)}건)")

    # 10-C. 요약 보고서 생성
    summary_md = REPORT_DIR / f"v14_regime_backtest_summary_{TODAY}.md"
    _write_summary_report(
        summary_md, labeled, v131_trades, v132_trades, v14_trades,
        stats_v131, stats_v132, stats_v14,
        removed_v14_by_reason, regime_groups,
        neutral_full, neutral_adj,
        samples, date_from, date_to
    )
    print(f"  ✅ {summary_md}")

    # ── 11. 콘솔 요약 출력 ──────────────────────────────────────────────
    _print_console_summary(
        labeled, stats_v131, stats_v132, stats_v14,
        removed_v14_by_reason, regime_groups
    )


def _print_console_summary(labeled, s131, s132, s14, removed_by_reason, regime_groups):
    print(f"\n{'='*60}")
    print("■ v1.3.1 / v1.3.2 / v1.4 성과 비교")
    print(f"{'='*60}")
    print(f"{'버전':<20} {'거래수':>5} {'WR':>7} {'PF':>6} {'avg_pnl':>8} {'total_pnl':>10} {'MDD':>7} {'ALPHA%':>7}")
    print("-" * 80)
    for ver, s in [("v1.3.1-final", s131), ("v1.3.2", s132), ("v1.4", s14)]:
        print(f"{ver:<20} {s['n']:>5} {s['wr']:>6.1f}% {s['pf']:>6.3f} {s['avg_pnl']:>7.3f}% {s['total_pnl']:>9.1f}% {s['mdd']:>7.1f}% {s['alpha_rate']:>6.1f}%")

    print(f"\n■ v1.4 제거 거래 (v1.4에서 차단된 {sum(len(v) for v in removed_by_reason.values())}건)")
    for reason, trades in removed_by_reason.items():
        s = aggregate(trades)
        print(f"  {reason:<30} {s['n']}건 | WR={s['wr']:.1f}% avg={s['avg_pnl']:.3f}%")

    print(f"\n■ v1.4 레짐별 통과 거래 성과")
    for r in ["TREND_UP", "NEUTRAL", "RISK_OFF"]:
        info = regime_groups.get(r, {})
        s = info.get("stats", {"n": 0})
        if s["n"] > 0:
            print(f"  {r:<10} {s['n']}건 WR={s['wr']:.1f}% PF={s['pf']:.3f} avg={s['avg_pnl']:.3f}%")
        else:
            print(f"  {r:<10} 0건 (모두 차단)")


def _write_summary_report(md_path, labeled, v131_trades, v132_trades, v14_trades,
                           s131, s132, s14, removed_by_reason, regime_groups,
                           neutral_full, neutral_adj, samples, date_from, date_to):
    from collections import defaultdict

    # 레짐별 월간 성과
    def monthly_stats(trades):
        m = defaultdict(list)
        for t in trades:
            dt = t.get("buy_time_str", "")
            if dt:
                m[dt[:7]].append(t)
        rows = []
        for mo in sorted(m.keys()):
            s = aggregate(m[mo])
            rows.append((mo, s))
        return rows

    # 승인 결론 판단
    pf_improved   = s14["pf"]   > s132["pf"]
    avg_improved  = s14["avg_pnl"] > s132["avg_pnl"]
    mdd_improved  = s14["mdd"]  < s132["mdd"]
    improve_count = sum([pf_improved, avg_improved, mdd_improved])

    risk_off_n = len(removed_by_reason.get("REGIME_BLOCK_RISK_OFF", []))
    risk_off_trades = removed_by_reason.get("REGIME_BLOCK_RISK_OFF", [])
    risk_off_stats  = aggregate(risk_off_trades)

    look_ahead_ok = all(
        (s.get("kospi_last_date") is not None and
         s.get("kospi_last_date") < s.get("entry_date", "9999"))
        for s in samples if s.get("kospi_last_date")
    )

    if look_ahead_ok and improve_count >= 1 and (risk_off_n == 0 or risk_off_stats["avg_pnl"] < 0):
        verdict = "ON_APPROVED"
        verdict_str = "**v1.4 실전 활성화 승인한다.**"
    elif not look_ahead_ok:
        verdict = "HOLD_LOOKAHEAD"
        verdict_str = "**look-ahead 문제 발견 — 보류.**"
    else:
        verdict = "HOLD_INSUFFICIENT"
        verdict_str = "**성과 개선 증거 부족 — 보류 또는 임계값 조정 후 재검증.**"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# v1.4 Market Regime Gate 백테스트 검증 보고서",
        f"",
        f"> **생성일**: {now_str}",
        f"> **분석 기간**: {date_from or '전체'} ~ {date_to or '전체'}",
        f"> **총 거래**: {len(labeled)}건 (BUY-SELL 페어링)",
        f"> **최종 결론**: {verdict}",
        f"",
        f"---",
        f"",
        f"## 1. 데이터 범위",
        f"",
        f"| 항목 | 값 |",
        f"|------|-----|",
        f"| 분석 거래 수 | {len(labeled)}건 |",
        f"| 기간 | {date_from or '전체'} ~ {date_to or '전체'} |",
        f"| KOSPI 일봉 데이터 | yfinance ^KS11 |",
        f"| KOSDAQ 일봉 데이터 | yfinance ^KQ11 |",
        f"| Look-ahead 방지 | 진입 T일 기준 T-1 일봉까지만 사용 |",
        f"| EMA 기간 | 20일 |",
        f"| Score 임계값 | TREND_UP≥+3, RISK_OFF≤-3 |",
        f"",
        f"---",
        f"",
        f"## 2. 검증 방법",
        f"",
        f"방식: **사후 재평가 (방식 A)**",
        f"- 과거 {len(labeled)}건 거래를 기준으로 각 진입 시점의 레짐을 재계산",
        f"- 버전별로 차단/허용/사이즈 조정 여부를 판정",
        f"- 차단된 거래의 실제 손익으로 '잘라낸 구간의 품질' 검증",
        f"",
        f"---",
        f"",
        f"## 3. Look-ahead 검증",
        f"",
    ]

    for i, s in enumerate(samples):
        entry_str = s.get("buy_time_str", "")
        kospi_last = s.get("kospi_last_date", "N/A")
        kosdaq_last = s.get("kosdaq_last_date", "N/A")
        entry_date = s.get("entry_date", "")
        la_check = "✅ look-ahead 없음" if (kospi_last and kospi_last < entry_date) else "⚠️ 검증 필요"
        lines += [
            f"### 샘플 {i+1} — {s.get('regime','?')}",
            f"- 진입 시각: `{entry_str}`",
            f"- 레짐: `{s.get('regime','?')}` (score={s.get('regime_score',0)})",
            f"- KOSPI 일봉 마지막 날짜: `{kospi_last}` → 진입일 `{entry_date}` 대비 → {la_check}",
            f"- KOSDAQ 일봉 마지막 날짜: `{kosdaq_last}`",
            f"- Score 근거: `{s.get('regime_reasons','')}`",
            f"",
        ]

    lines += [
        f"---",
        f"",
        f"## 4. 버전 비교표",
        f"",
        f"| 버전 | 거래수 | WR | PF | avg_pnl | total_pnl | MDD | ALPHA율 |",
        f"|------|-------:|---:|---:|--------:|----------:|----:|--------:|",
        f"| v1.3.1-final | {s131['n']} | {s131['wr']:.1f}% | {s131['pf']:.3f} | {s131['avg_pnl']:.3f}% | {s131['total_pnl']:.1f}% | {s131['mdd']:.1f}% | {s131['alpha_rate']:.1f}% |",
        f"| v1.3.2 | {s132['n']} | {s132['wr']:.1f}% | {s132['pf']:.3f} | {s132['avg_pnl']:.3f}% | {s132['total_pnl']:.1f}% | {s132['mdd']:.1f}% | {s132['alpha_rate']:.1f}% |",
        f"| **v1.4** | **{s14['n']}** | **{s14['wr']:.1f}%** | **{s14['pf']:.3f}** | **{s14['avg_pnl']:.3f}%** | **{s14['total_pnl']:.1f}%** | **{s14['mdd']:.1f}%** | **{s14['alpha_rate']:.1f}%** |",
        f"",
        f"### 개선 여부",
        f"- PF: {s132['pf']:.3f} → {s14['pf']:.3f} {'✅ 개선' if pf_improved else '❌ 악화/동일'}",
        f"- avg_pnl: {s132['avg_pnl']:.3f}% → {s14['avg_pnl']:.3f}% {'✅ 개선' if avg_improved else '❌ 악화/동일'}",
        f"- MDD: {s132['mdd']:.1f}% → {s14['mdd']:.1f}% {'✅ 개선(감소)' if mdd_improved else '❌ 악화/동일'}",
        f"",
        f"---",
        f"",
        f"## 5. 레짐별 성과표 (v1.4 통과 거래 기준)",
        f"",
        f"| 레짐 | 거래수 | WR | PF | avg_pnl | total_pnl |",
        f"|------|-------:|---:|---:|--------:|----------:|",
    ]

    for r in ["TREND_UP", "NEUTRAL", "RISK_OFF"]:
        info = regime_groups.get(r, {})
        s = info.get("stats", {"n": 0, "wr": 0, "pf": 0, "avg_pnl": 0, "total_pnl": 0})
        lines.append(f"| {r} | {s['n']} | {s['wr']:.1f}% | {s['pf']:.3f} | {s['avg_pnl']:.3f}% | {s['total_pnl']:.1f}% |")

    lines += [
        f"",
        f"---",
        f"",
        f"## 6. 제거 거래 분석표",
        f"",
        f"| 제거 사유 | 거래수 | 실제 WR | 실제 avg_pnl | 실제 total_pnl |",
        f"|-----------|-------:|--------:|------------:|--------------:|",
    ]

    for reason, trades in removed_by_reason.items():
        s = aggregate(trades)
        lines.append(f"| {reason} | {s['n']} | {s['wr']:.1f}% | {s['avg_pnl']:.3f}% | {s['total_pnl']:.1f}% |")

    if not removed_by_reason:
        lines.append("| (없음) | 0 | — | — | — |")

    lines += [
        f"",
        f"### 해석",
    ]

    if risk_off_n > 0:
        lines.append(f"- RISK_OFF 차단 {risk_off_n}건 실제 avg_pnl = {risk_off_stats['avg_pnl']:.3f}% "
                     + ("(손실 구간 — 차단 효과 ✅)" if risk_off_stats['avg_pnl'] < 0 else "(이익 구간 — 과도한 차단 ⚠️)"))
    else:
        lines.append("- RISK_OFF 차단 거래 없음 (분석 기간 내 RISK_OFF 없었거나 데이터 부족)")

    lines += [
        f"",
        f"---",
        f"",
        f"## 7. NEUTRAL 사이즈 0.7배 효과 분석",
        f"",
        f"| 항목 | 원래 사이즈(×1.0) | 0.7배 적용 |",
        f"|------|------------------:|----------:|",
        f"| NEUTRAL 거래수 | {neutral_full['n']} | {neutral_adj['n']} |",
        f"| NEUTRAL avg_pnl | {neutral_full['avg_pnl']:.3f}% | {neutral_adj['avg_pnl']:.3f}% |",
        f"| NEUTRAL total_pnl | {neutral_full['total_pnl']:.1f}% | {neutral_adj['total_pnl']:.1f}% |",
        f"| NEUTRAL MDD | {neutral_full['mdd']:.1f}% | {neutral_adj['mdd']:.1f}% |",
        f"",
    ]

    if neutral_full["total_pnl"] < 0:
        lines.append(f"- NEUTRAL 구간 손익 합이 음수: 0.7배 적용으로 절대 손실 {neutral_full['total_pnl']:.1f}% → {neutral_adj['total_pnl']:.1f}% 감소")
    else:
        lines.append(f"- NEUTRAL 구간 손익 합이 양수: 0.7배 적용으로 이익 일부 포기")

    lines += [
        f"",
        f"---",
        f"",
        f"## 8. 월별 성과 추이 (v1.4)",
        f"",
        f"| 월 | 거래수 | WR | PF | avg_pnl |",
        f"|----|-------:|---:|---:|--------:|",
    ]

    for mo, s in monthly_stats(v14_trades):
        lines.append(f"| {mo} | {s['n']} | {s['wr']:.1f}% | {s['pf']:.3f} | {s['avg_pnl']:.3f}% |")

    lines += [
        f"",
        f"---",
        f"",
        f"## 9. 최종 결론",
        f"",
    ]

    # 결론 문구
    if verdict == "ON_APPROVED":
        lines.append(
            "> 결론: v1.4 Market Regime Gate는 과거 거래 기준으로 "
            f"PF/avg_pnl/MDD 중 {improve_count}개 이상을 개선했고, "
            "look-ahead 문제도 확인되지 않았다. "
            "**따라서 v1.4를 실전 활성화 승인한다.**"
        )
    elif verdict == "HOLD_LOOKAHEAD":
        lines.append(
            "> 결론: v1.4 Market Regime Gate 설계/구현은 완료되었으나, "
            "look-ahead 검증에서 문제가 발견되었다. "
            "**따라서 현재는 실전 활성화를 보류하고, 데이터 시점 정합성 수정 후 재검증한다.**"
        )
    else:
        lines.append(
            "> 결론: v1.4 Market Regime Gate는 설계/구현은 완료되었으나, "
            "과거 거래 기준 성과 개선 폭이 충분히 확인되지 않았다 "
            f"(PF {'개선' if pf_improved else '미개선'}, "
            f"avg_pnl {'개선' if avg_improved else '미개선'}, "
            f"MDD {'개선' if mdd_improved else '미개선'}). "
            "**따라서 현재는 실전 활성화를 보류하고, score threshold 또는 neutral sizing 조정 후 재검증한다.**"
        )

    lines += [
        f"",
        f"### 후속 조치",
    ]

    if verdict == "ON_APPROVED":
        lines += [
            "1. `config/strategy_hybrid.yaml` — `market_regime.enabled: true` 유지 (이미 true)",
            "2. 실전 운영 30건 후 레짐별 실거래 성과 재확인",
            "3. 레짐 전환 민감도 분석 (threshold 캘리브레이션)",
        ]
    else:
        lines += [
            "1. `config/strategy_hybrid.yaml` — `market_regime.enabled: false`로 유지",
            "2. 아래 파라미터 조정 후 재실행:",
            "   - `score_thresholds.trend_up_min`: 3 → 2 또는 4",
            "   - `score_thresholds.risk_off_max`: -3 → -2 또는 -4",
            "   - `neutral_size_mult`: 0.7 → 0.5 또는 0.8",
            "3. 재실행: `python -m tools.backtest.label_market_regime_for_trades`",
        ]

    md_path.write_text('\n'.join(lines), encoding='utf-8')


# ═══════════════════════════════════════════════════════════════════════════
# 6. CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v1.4 Market Regime Gate 백테스트")
    parser.add_argument("--days",  type=int, default=None, help="최근 N일")
    parser.add_argument("--from",  dest="date_from", default=None, help="시작일 YYYY-MM-DD")
    parser.add_argument("--to",    dest="date_to",   default=None, help="종료일 YYYY-MM-DD")
    args = parser.parse_args()

    date_from = args.date_from
    date_to   = args.date_to
    if args.days and not date_from:
        date_from = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    run(date_from, date_to)
