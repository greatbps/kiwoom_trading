"""
analysis/trade_decision_audit.py — Trade Decision Audit & Strategy Quality Score

"필터 통과 후 트레이드 품질이 스윙 전략과 일치하는가"를 수치화.

SQS (Strategy Quality Score, 0-100):
  Entry Quality  (0-40) — CHoCH 등급 × 레짐 × RVOL
  Hold Quality   (0-30) — 보유 시간이 스윙 전략 구간인가
  Exit Quality   (0-30) — 청산 사유가 전략 철학에 맞는가 + MFE 포착률

사용법:
    python3 -m analysis.trade_decision_audit [--days N] [--verbose]
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from analysis.ai_data_quality import _categorize_exit

_ROOT    = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "trades.db"

# ──────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────

# exit category → 기본 exit quality 점수
_EXIT_BASE_SCORE: dict[str, int] = {
    "trailing_stop":  30,  # 추세 포착 후 규칙적 청산 — 최상
    "take_profit":    25,  # 목표 달성 후 익절
    "time_exit":      15,  # 장 마감 시간청산 — 규칙 준수
    "mfe_exit":       15,  # 방향 안 잡힌 포지션 정리 — 중립
    "overnight_close": 10, # B급 룰 적용 강제청산 — 규칙 준수
    "conservative_exit": 10,
    "ef_no_follow":    8,  # 진입은 맞았으나 추종 실패
    "early_failure":   8,
    "stop_loss":       5,
    "forced_close":    5,
    "manual_hts":     10,
    "ef_no_demand":    3,  # 진입 자체가 틀렸음
    "hard_stop":       0,  # 대형 손실 — 최악
    "unknown":        10,
}

# hold time (분) → hold quality 점수
_HOLD_BUCKETS = [
    (5,    0,  "SCALP(<5m)"),
    (15,   5,  "SHORT(5-15m)"),
    (30,  12,  "MARGINAL(15-30m)"),
    (120, 22,  "INTRADAY(30m-2h)"),
    (480, 30,  "SWING(2-8h)"),
    (2880, 28, "MULTI_SESSION(8h-2d)"),
    (float("inf"), 22, "LONGHOLD(>2d)"),
]


# ──────────────────────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────────────────────

@dataclass
class TradePair:
    stock_code:    str
    stock_name:    str
    buy_ts:        datetime
    sell_ts:       datetime
    entry_price:   float
    exit_price:    float
    realized_pnl:  float
    exit_reason:   str
    exit_category: str
    strategy:      str
    choch_grade:   Optional[str]   = None
    market_regime: Optional[str]   = None
    rvol_at_entry: Optional[float] = None
    mfe_pct:       Optional[float] = None
    mae_pct:       Optional[float] = None

    @property
    def hold_min(self) -> float:
        return (self.sell_ts - self.buy_ts).total_seconds() / 60

    @property
    def profit_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price * 100

    @property
    def mfe_capture_rate(self) -> Optional[float]:
        """profit / mfe (0~1+). None if mfe unavailable or mfe=0."""
        if self.mfe_pct is None or self.mfe_pct <= 0.05:
            return None
        return self.profit_pct / self.mfe_pct


@dataclass
class TradeScore:
    pair:          TradePair
    total:         float          # 0-100
    entry_score:   float          # 0-40
    hold_score:    float          # 0-30
    exit_score:    float          # 0-30
    hold_bucket:   str
    flags:         list[str] = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.total >= 80: return "A"
        if self.total >= 60: return "B"
        if self.total >= 40: return "C"
        return "D"


@dataclass
class AuditReport:
    total_pairs:    int
    algo_pairs:     int
    scores:         list[TradeScore]
    avg_sqs:        float
    avg_hold_min:   float
    swing_rate:     float          # % of trades with hold > 30 min and non-EF/hard_stop exit
    mfe_capture_avg: Optional[float]
    exit_dist:      dict[str, int]
    sqs_by_exit:    dict[str, float]
    sqs_by_grade:   dict[str, float]
    grade_dist:     dict[str, int]
    alerts:         list[str]


# ──────────────────────────────────────────────────────────────
# 스코어링 로직
# ──────────────────────────────────────────────────────────────

def _score_entry(pair: TradePair) -> float:
    """진입 품질 (0-40). 데이터 없으면 부분 점수."""
    score = 0.0

    # CHoCH 등급 (0-15)
    _grade_pts = {"A+": 15, "A": 12, "A-": 10, "B": 6, "C": 3}
    if pair.choch_grade:
        score += _grade_pts.get(pair.choch_grade.strip(), 4)
    else:
        score += 5  # 데이터 없음 → 중립 부분 점수

    # 시장 레짐 (0-10)
    if pair.market_regime:
        regime_pts = {"TREND": 10, "NEUTRAL": 5, "REVERSAL": 0}
        score += regime_pts.get(pair.market_regime.upper(), 3)
    else:
        score += 5  # 중립

    # RVOL (0-15)
    if pair.rvol_at_entry is not None:
        rvol = pair.rvol_at_entry
        if rvol >= 3.0:   score += 15
        elif rvol >= 2.0: score += 10
        elif rvol >= 1.5: score += 6
        elif rvol >= 1.0: score += 3
    else:
        score += 7  # 중립

    return min(40.0, score)


def _score_hold(pair: TradePair) -> tuple[float, str]:
    """보유 시간 품질 (0-30) + 버킷 이름."""
    hold = pair.hold_min
    for limit, pts, label in _HOLD_BUCKETS:
        if hold < limit:
            return float(pts), label
    return 22.0, "LONGHOLD(>2d)"


def _score_exit(pair: TradePair) -> float:
    """청산 품질 (0-30). 기본 점수 + MFE 포착률 보정."""
    base = float(_EXIT_BASE_SCORE.get(pair.exit_category, 10))

    # MFE 포착률 보정 (mfe_pct 데이터 있을 때만)
    cap = pair.mfe_capture_rate
    if cap is not None:
        if cap >= 0.70:   base += 5
        elif cap >= 0.40: base += 0
        elif cap >= 0.20: base -= 5
        else:             base -= 10

    return max(0.0, min(30.0, base))


def _assign_flags(pair: TradePair, score: TradeScore) -> list[str]:
    flags = []
    if pair.hold_min < 15:
        flags.append("SCALP")
    if pair.exit_category in ("hard_stop",) and pair.hold_min > 400:
        flags.append("OVERNIGHT_HARDSTOP")
    if pair.exit_category == "trailing_stop" and pair.profit_pct > 0:
        flags.append("SWING_SUCCESS")
    if pair.exit_category in ("ef_no_demand",):
        flags.append("ENTRY_WAS_WRONG")
    if pair.choch_grade in ("A", "A+") and pair.market_regime == "TREND":
        flags.append("STRONG_ENTRY")
    cap = pair.mfe_capture_rate
    if cap is not None and cap < 0.20 and (pair.mfe_pct or 0) > 1.0:
        flags.append("MFE_SQUANDER")
    if pair.hold_min >= 30 and pair.exit_category not in ("hard_stop", "ef_no_demand", "ef_no_follow", "early_failure"):
        flags.append("SWING_QUALIFIED")
    return flags


def score_pair(pair: TradePair) -> TradeScore:
    entry = _score_entry(pair)
    hold, bucket = _score_hold(pair)
    exit_ = _score_exit(pair)
    total = entry + hold + exit_
    s = TradeScore(pair=pair, total=round(total, 1),
                   entry_score=round(entry, 1), hold_score=round(hold, 1),
                   exit_score=round(exit_, 1), hold_bucket=bucket)
    s.flags = _assign_flags(pair, s)
    return s


# ──────────────────────────────────────────────────────────────
# DB 로드 및 페어 매칭
# ──────────────────────────────────────────────────────────────

def _load_pairs(db_path: Path, days: int) -> tuple[int, int, list[TradePair]]:
    """
    Returns (total_rows, algo_pairs_count, pairs)
    MANUAL/kiwoom 전략 제외, SELL → 직전 BUY FIFO 매칭.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    all_rows = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE trade_date >= ?", (cutoff,)
    ).fetchone()[0]

    rows = conn.execute("""
        SELECT stock_code, stock_name, trade_type, timestamp, price,
               realized_pnl, reason, strategy,
               choch_grade, market_regime, rvol_at_entry, mfe_pct, mae_pct
        FROM trades
        WHERE trade_date >= ?
          AND strategy NOT IN ('MANUAL', 'kiwoom')
        ORDER BY timestamp
    """, (cutoff,)).fetchall()
    conn.close()

    rows = [dict(r) for r in rows]

    # FIFO 매칭: stock별 BUY 큐 → SELL 도착 시 pop
    open_buys: dict[str, list[dict]] = defaultdict(list)
    pairs: list[TradePair] = []

    for r in rows:
        code = r["stock_code"]
        if r["trade_type"] == "BUY":
            open_buys[code].append(r)
        elif r["trade_type"] == "SELL" and open_buys[code]:
            b = open_buys[code].pop(0)
            s = r
            try:
                bt = datetime.fromisoformat(b["timestamp"])
                st = datetime.fromisoformat(s["timestamp"])
            except Exception:
                continue
            if st <= bt:  # 역전된 타임스탬프(마이그레이션 아티팩트) 제외
                continue

            pairs.append(TradePair(
                stock_code    = code,
                stock_name    = r["stock_name"],
                buy_ts        = bt,
                sell_ts       = st,
                entry_price   = float(b["price"]),
                exit_price    = float(s["price"]),
                realized_pnl  = float(s["realized_pnl"] or 0),
                exit_reason   = s["reason"] or "",
                exit_category = _categorize_exit(s["reason"] or ""),
                strategy      = b["strategy"] or "UNKNOWN",
                choch_grade   = b.get("choch_grade"),
                market_regime = b.get("market_regime"),
                rvol_at_entry = b.get("rvol_at_entry"),
                mfe_pct       = s.get("mfe_pct"),
                mae_pct       = s.get("mae_pct"),
            ))

    return all_rows, len(pairs), pairs


# ──────────────────────────────────────────────────────────────
# 집계
# ──────────────────────────────────────────────────────────────

def _aggregate(scores: list[TradeScore]) -> AuditReport:
    if not scores:
        return AuditReport(0, 0, [], 0, 0, 0, None, {}, {}, {}, {}, ["데이터 없음"])

    # 기본 집계
    avg_sqs    = sum(s.total for s in scores) / len(scores)
    avg_hold   = sum(s.pair.hold_min for s in scores) / len(scores)
    swing_cnt  = sum(1 for s in scores if "SWING_QUALIFIED" in s.flags)
    swing_rate = swing_cnt / len(scores) * 100

    # MFE 포착률
    cap_rates = [s.pair.mfe_capture_rate for s in scores if s.pair.mfe_capture_rate is not None]
    mfe_cap_avg = sum(cap_rates) / len(cap_rates) if cap_rates else None

    # 청산 사유 분포
    exit_dist: dict[str, int] = {}
    for s in scores:
        cat = s.pair.exit_category
        exit_dist[cat] = exit_dist.get(cat, 0) + 1

    # 청산 사유별 평균 SQS
    sqs_by_exit: dict[str, list[float]] = defaultdict(list)
    for s in scores:
        sqs_by_exit[s.pair.exit_category].append(s.total)
    sqs_by_exit_avg = {k: round(sum(v) / len(v), 1) for k, v in sqs_by_exit.items()}

    # CHoCH 등급별 평균 SQS
    sqs_by_grade: dict[str, list[float]] = defaultdict(list)
    for s in scores:
        g = s.pair.choch_grade or "?"
        sqs_by_grade[g].append(s.total)
    sqs_by_grade_avg = {k: round(sum(v) / len(v), 1) for k, v in sqs_by_grade.items()}

    # SQS 등급 분포
    grade_dist: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
    for s in scores:
        grade_dist[s.grade] += 1

    # 경고 생성
    alerts = _make_alerts(scores, avg_sqs, swing_rate, exit_dist)

    return AuditReport(
        total_pairs   = -1,  # caller sets this
        algo_pairs    = len(scores),
        scores        = scores,
        avg_sqs       = round(avg_sqs, 1),
        avg_hold_min  = round(avg_hold, 1),
        swing_rate    = round(swing_rate, 1),
        mfe_capture_avg = round(mfe_cap_avg * 100, 1) if mfe_cap_avg is not None else None,
        exit_dist     = exit_dist,
        sqs_by_exit   = sqs_by_exit_avg,
        sqs_by_grade  = sqs_by_grade_avg,
        grade_dist    = grade_dist,
        alerts        = alerts,
    )


def _make_alerts(
    scores: list[TradeScore],
    avg_sqs: float,
    swing_rate: float,
    exit_dist: dict[str, int],
) -> list[str]:
    alerts = []
    n = len(scores)

    hard_stop_n = exit_dist.get("hard_stop", 0)
    ef_n = exit_dist.get("ef_no_demand", 0) + exit_dist.get("ef_no_follow", 0) + exit_dist.get("early_failure", 0)
    scalp_n = sum(1 for s in scores if "SCALP" in s.flags)

    if hard_stop_n / n > 0.30:
        alerts.append(
            f"🔴 Hard Stop 비율 {hard_stop_n/n:.0%} (기준 <30%) "
            "— 스윙이 아닌 리스크 전략으로 변질 신호"
        )
    if ef_n / n > 0.40:
        alerts.append(
            f"🔴 EF 청산 비율 {ef_n/n:.0%} (기준 <40%) "
            "— 진입 품질 또는 시장 선택 문제"
        )
    if scalp_n / n > 0.25:
        alerts.append(
            f"⚠️  SCALP 거래 {scalp_n/n:.0%} (기준 <25%) "
            "— 15분 미만 청산 과다, 스윙 전략 희석"
        )
    if swing_rate < 50:
        alerts.append(
            f"⚠️  Swing Qualified 비율 {swing_rate:.0f}% (기준 ≥50%) "
            "— 전략 정합성 낮음"
        )
    if avg_sqs < 50:
        alerts.append(f"🔴 평균 SQS {avg_sqs:.0f} — 전략 전반 재검토 필요")
    elif avg_sqs < 65:
        alerts.append(f"⚠️  평균 SQS {avg_sqs:.0f} — 진입·청산 기준 미세조정 권장")

    overnight_n = exit_dist.get("overnight_close", 0)
    if overnight_n / n > 0.40:
        alerts.append(
            f"ℹ️  overnight_close {overnight_n/n:.0%} — B급 필터 강화 또는 carry_override 조건 검토"
        )

    if not alerts:
        alerts.append("✅ 전략 정합성 양호 — 모든 기준 충족")

    return alerts


# ──────────────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────────────

class TradeDecisionAudit:

    def __init__(self, db_path: str = None, days: int = 90):
        self.db_path = Path(db_path) if db_path else _DB_PATH
        self.days    = days

    def run(self) -> AuditReport:
        total_rows, algo_n, pairs = _load_pairs(self.db_path, self.days)
        if not pairs:
            r = AuditReport(total_rows, 0, [], 0, 0, 0, None, {}, {}, {}, {}, ["알고리즘 거래 없음"])
            r.total_pairs = total_rows
            return r

        scores = [score_pair(p) for p in pairs]
        report = _aggregate(scores)
        report.total_pairs = total_rows
        return report


# ──────────────────────────────────────────────────────────────
# 출력
# ──────────────────────────────────────────────────────────────

def print_report(r: AuditReport, verbose: bool = False):
    bar  = "=" * 66
    sqs  = r.avg_sqs
    icon = "✅" if sqs >= 70 else "⚠️" if sqs >= 50 else "🔴"

    print(f"\n{bar}")
    print(f"  Trade Decision Audit  {icon}  SQS avg: {sqs:.0f}/100")
    print(f"  알고리즘 거래 {r.algo_pairs}건 | 평균 보유 {r.avg_hold_min:.0f}분 "
          f"| Swing Qualified {r.swing_rate:.0f}%")
    print(bar)

    # SQS 분포
    print(f"\n  [SQS 등급 분포]")
    grade_total = sum(r.grade_dist.values())
    for g, cnt in sorted(r.grade_dist.items()):
        bar_len = int(cnt / grade_total * 30) if grade_total else 0
        pct = cnt / grade_total * 100 if grade_total else 0
        label = {"A": "80-100 탁월", "B": "60-79 양호", "C": "40-59 혼재", "D": "<40  이탈"}[g]
        print(f"    {g}  {label}  {'█' * bar_len} {cnt}건 ({pct:.0f}%)")

    # 청산 사유 × SQS
    print(f"\n  [청산 사유별 건수 / 평균 SQS]")
    for cat, cnt in sorted(r.exit_dist.items(), key=lambda x: -x[1]):
        avg = r.sqs_by_exit.get(cat, 0)
        icon2 = "✅" if avg >= 65 else "⚠️" if avg >= 45 else "🔴"
        print(f"    {icon2} {cat:<20} {cnt:2d}건  SQS={avg:.0f}")

    # MFE 포착
    if r.mfe_capture_avg is not None:
        cap = r.mfe_capture_avg
        cap_icon = "✅" if cap >= 40 else "⚠️" if cap >= 20 else "🔴"
        print(f"\n  [MFE 포착률]  {cap_icon}  {cap:.0f}%  (기준: ≥40%)")

    # 경고
    print(f"\n  [진단]")
    for alert in r.alerts:
        print(f"    {alert}")

    # 개별 거래 상세
    if verbose:
        print(f"\n  [개별 거래 SQS]")
        for s in sorted(r.scores, key=lambda x: -x.total):
            cap_str = f"cap={s.pair.mfe_capture_rate*100:.0f}%" \
                      if s.pair.mfe_capture_rate is not None else ""
            flags_str = " ".join(f"[{f}]" for f in s.flags)
            print(
                f"    {s.grade}  SQS={s.total:.0f}  "
                f"{s.pair.stock_name[:8]:<8}  hold={s.pair.hold_min:.0f}m  "
                f"pnl={s.pair.profit_pct:+.1f}%  {s.hold_bucket}  "
                f"exit={s.pair.exit_category}  {cap_str}  {flags_str}"
            )

    print(f"\n{bar}\n")


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Trade Decision Audit — SQS")
    parser.add_argument("--days",    type=int,  default=90,   help="조회 기간 (일)")
    parser.add_argument("--db",      type=str,  default="",   help="DB 경로")
    parser.add_argument("--verbose", action="store_true",      help="개별 거래 상세 출력")
    args = parser.parse_args()

    audit  = TradeDecisionAudit(db_path=args.db or None, days=args.days)
    report = audit.run()
    print_report(report, verbose=args.verbose)

    raise SystemExit(0 if report.avg_sqs >= 60 else 1)
