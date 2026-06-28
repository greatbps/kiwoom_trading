"""
analysis/regime_metrics.py — Phase 1: 시장 레짐 측정 인프라

측정 지표:
  1. ntd_cluster_density   — NTD일 수 / 최근 N거래일
  2. ntd_duration_pct      — 거래일당 평균 NTD 차단 시간 비율 (분 기준)
  3. breakout_success_rate — profit_rate >= +3% 비율 (DB, 전체 / 날짜별)
  4. ntd_vs_tradeok_outcome — NTD 진입 vs TRADE_OK 진입 수익 비교
  5. accept_on_ntd          — NTD 중 발생한 ACCEPT alpha 분포 (놓친 기회)

출력: logs/regime_metrics_YYYYMMDD.json (+ 콘솔 요약)
사용: python3 -m analysis.regime_metrics [--days 20] [--date YYYYMMDD]
"""
import argparse
import json
import re
import sys
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from market_utils import get_db_connection

LOG_DIR = Path("logs")
ACCEPT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
    r"✅ ACCEPT (\w+) @[\d,]+원 \| PID:\d+ \| conf=([\d.]+) alpha=([+-][\d.]+)"
)
NTD_CHANGE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
    r"\[MKT_CTX_CHANGE\] (\w+) → (\w+)"
)


# ── 로그 파싱 ─────────────────────────────────────────────────────────────────

def _trading_days(start: date, end: date) -> list[date]:
    """주말 제외 거래일 목록."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _parse_ntd_intervals(log_path: Path) -> list[tuple[datetime, datetime]]:
    """로그 파일에서 NO_TRADE_DAY 구간 [(시작, 종료)] 추출."""
    if not log_path.exists():
        return []

    intervals: list[tuple[datetime, datetime]] = []
    ntd_start: datetime | None = None

    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = NTD_CHANGE_RE.search(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            prev_state, next_state = m.group(2), m.group(3)

            if next_state == "NO_TRADE_DAY" and ntd_start is None:
                ntd_start = ts
            elif prev_state == "NO_TRADE_DAY" and ntd_start is not None:
                intervals.append((ntd_start, ts))
                ntd_start = None

    # 로그 끝까지 NO_TRADE_DAY 유지 → 장마감(15:30) 기준 닫기
    if ntd_start is not None:
        close_time = ntd_start.replace(hour=15, minute=30, second=0)
        if ntd_start < close_time:
            intervals.append((ntd_start, close_time))

    return intervals


def _parse_accepts(log_path: Path) -> list[dict]:
    """signal_orchestrator.log 에서 ACCEPT 이벤트 파싱."""
    if not log_path.exists():
        return []
    accepts = []
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = ACCEPT_RE.search(line)
            if m:
                accepts.append({
                    "ts":    datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"),
                    "code":  m.group(2),
                    "conf":  float(m.group(3)),
                    "alpha": float(m.group(4)),
                })
    return accepts


# ── NTD 클러스터 / 지속 시간 ─────────────────────────────────────────────────

def _ntd_stats(days: list[date]) -> dict:
    """날짜 목록에서 NTD 통계 계산."""
    ntd_days: list[date] = []
    day_ntd_minutes: dict[date, float] = {}
    all_intervals: dict[date, list] = {}

    for d in days:
        log = LOG_DIR / f"auto_trading_{d.strftime('%Y%m%d')}.log"
        intervals = _parse_ntd_intervals(log)
        if not intervals:
            day_ntd_minutes[d] = 0.0
            continue
        total_min = sum((e - s).total_seconds() / 60 for s, e in intervals)
        day_ntd_minutes[d] = total_min
        all_intervals[d] = intervals
        if total_min > 0:
            ntd_days.append(d)

    trading_minutes = 6 * 60 + 30  # 09:00~15:30

    return {
        "ntd_cluster_density": round(len(ntd_days) / max(len(days), 1), 3),
        "ntd_day_count":       len(ntd_days),
        "total_days":          len(days),
        "ntd_days":            [str(d) for d in ntd_days],
        "avg_ntd_minutes":     round(
            sum(day_ntd_minutes.values()) / max(len(ntd_days), 1), 1
        ),
        "avg_ntd_duration_pct": round(
            sum(day_ntd_minutes.values()) / max(len(ntd_days), 1)
            / trading_minutes * 100,
            1,
        ),
        "_day_ntd_minutes":    {str(k): round(v, 1) for k, v in day_ntd_minutes.items()},
        "_all_intervals":      {
            str(d): [(str(s), str(e)) for s, e in iv]
            for d, iv in all_intervals.items()
        },
    }


# ── NTD 중 ACCEPT (놓친 기회) ────────────────────────────────────────────────

def _accepts_on_ntd(days: list[date]) -> dict:
    """NTD 구간 중 발생한 ACCEPT alpha 분포 계산."""
    # NTD 구간 수집
    ntd_windows: list[tuple[datetime, datetime]] = []
    for d in days:
        log = LOG_DIR / f"auto_trading_{d.strftime('%Y%m%d')}.log"
        ntd_windows.extend(_parse_ntd_intervals(log))

    orch_log = LOG_DIR / "signal_orchestrator.log"
    accepts_all = _parse_accepts(orch_log)

    # 날짜 범위 필터
    start_dt = datetime.combine(days[0],  datetime.min.time())
    end_dt   = datetime.combine(days[-1], datetime.max.time())
    accepts_all = [a for a in accepts_all if start_dt <= a["ts"] <= end_dt]

    ntd_accepts, ok_accepts = [], []
    for a in accepts_all:
        in_ntd = any(s <= a["ts"] <= e for s, e in ntd_windows)
        (ntd_accepts if in_ntd else ok_accepts).append(a["alpha"])

    def _agg(alphas: list[float]) -> dict:
        if not alphas:
            return {"count": 0}
        return {
            "count":    len(alphas),
            "avg":      round(sum(alphas) / len(alphas), 3),
            "max":      round(max(alphas), 3),
            "above_2":  sum(1 for a in alphas if a >= 2.0),
            "above_15": sum(1 for a in alphas if a >= 1.5),
        }

    return {
        "ntd":      _agg(ntd_accepts),
        "trade_ok": _agg(ok_accepts),
    }


# ── DB: 브레이크아웃 성공률 / 날짜별 outcome ────────────────────────────────

def _db_trade_stats(days: list[date]) -> dict:
    """DB에서 기간 내 SELL 수익 통계 + 날짜별 NTD 여부 매핑."""
    start_str = days[0].strftime("%Y-%m-%d")
    end_str   = (days[-1] + timedelta(days=1)).strftime("%Y-%m-%d")

    # NTD 날짜 세트
    ntd_day_set: set[date] = set()
    for d in days:
        log = LOG_DIR / f"auto_trading_{d.strftime('%Y%m%d')}.log"
        intervals = _parse_ntd_intervals(log)
        if sum((e - s).total_seconds() for s, e in intervals) > 0:
            ntd_day_set.add(d)

    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT entry_time, exit_time, stock_code, profit_rate, exit_reason
            FROM trades
            WHERE trade_type='SELL'
              AND entry_time >= %s AND entry_time < %s
            ORDER BY entry_time
        """, (start_str, end_str))
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return {"error": str(e)}

    if not rows:
        return {"sell_count": 0}

    profits = [float(r[3]) for r in rows if r[3] is not None]
    ntd_entry_profits, ok_entry_profits = [], []

    for entry_time, _, _, profit_rate, _ in rows:
        if profit_rate is None:
            continue
        entry_date = entry_time.date() if entry_time else None
        if entry_date in ntd_day_set:
            ntd_entry_profits.append(float(profit_rate))
        else:
            ok_entry_profits.append(float(profit_rate))

    def _outcome(ps: list[float]) -> dict:
        if not ps:
            return {"count": 0}
        wins = [p for p in ps if p >= 0]
        hits3 = [p for p in ps if p >= 3.0]
        return {
            "count":          len(ps),
            "win_rate":       round(len(wins) / len(ps) * 100, 1),
            "avg_profit":     round(sum(ps) / len(ps), 2),
            "breakout_success_rate": round(len(hits3) / len(ps) * 100, 1),
            "max":            round(max(ps), 2),
            "min":            round(min(ps), 2),
        }

    return {
        "sell_count":           len(profits),
        "overall":              _outcome(profits),
        "ntd_entry_days":       _outcome(ntd_entry_profits),
        "tradeok_entry_days":   _outcome(ok_entry_profits),
        "breakout_success_rate": round(
            sum(1 for p in profits if p >= 3.0) / max(len(profits), 1) * 100, 1
        ),
    }


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(days_back: int = 20, target_date: date | None = None) -> dict:
    end_day   = target_date or date.today()
    start_day = end_day - timedelta(days=days_back * 2)  # 주말 여유
    all_days  = _trading_days(start_day, end_day)
    days      = all_days[-days_back:] if len(all_days) >= days_back else all_days

    ntd    = _ntd_stats(days)
    accept = _accepts_on_ntd(days)
    trades = _db_trade_stats(days)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period":       {"start": str(days[0]), "end": str(days[-1]), "days": len(days)},
        "ntd":          ntd,
        "accept_alpha": accept,
        "trades":       trades,
    }

    return result


def print_summary(r: dict) -> None:
    ntd    = r["ntd"]
    acc    = r["accept_alpha"]
    tr     = r.get("trades", {})
    period = r["period"]

    print(f"\n{'='*60}")
    print(f"Regime Metrics  {period['start']} ~ {period['end']}  ({period['days']}일)")
    print(f"{'='*60}")

    print(f"\n[NTD Cluster]")
    print(f"  NTD 발생일   : {ntd['ntd_day_count']} / {ntd['total_days']}일  "
          f"(density={ntd['ntd_cluster_density']:.0%})")
    print(f"  NTD일 목록   : {', '.join(ntd['ntd_days']) or '없음'}")
    print(f"  평균 차단시간 : {ntd['avg_ntd_minutes']:.0f}분  "
          f"({ntd['avg_ntd_duration_pct']:.0f}% of 거래시간)")

    print(f"\n[ACCEPT Alpha — NTD 중 vs TRADE_OK]")
    an, ao = acc.get("ntd", {}), acc.get("trade_ok", {})
    if an.get("count", 0) > 0:
        print(f"  NTD 중 ACCEPT   : {an['count']}건  avg={an['avg']:+.2f}  "
              f"max={an['max']:+.2f}  (alpha≥2.0: {an['above_2']}건  ≥1.5: {an['above_15']}건)")
    else:
        print(f"  NTD 중 ACCEPT   : 없음")
    if ao.get("count", 0) > 0:
        print(f"  TRADE_OK ACCEPT : {ao['count']}건  avg={ao['avg']:+.2f}  max={ao['max']:+.2f}")

    print(f"\n[Trade Outcomes]")
    if tr.get("sell_count", 0) == 0:
        print("  SELL 데이터 없음")
    else:
        ov = tr.get("overall", {})
        print(f"  전체 SELL : {tr['sell_count']}건  "
              f"승률={ov.get('win_rate', 0):.0f}%  avg={ov.get('avg_profit', 0):+.2f}%  "
              f"BSR(≥+3%)={tr.get('breakout_success_rate', 0):.0f}%")
        nt = tr.get("ntd_entry_days", {})
        ok = tr.get("tradeok_entry_days", {})
        if nt.get("count", 0):
            print(f"  NTD 진입일   : {nt['count']}건  승률={nt['win_rate']:.0f}%  "
                  f"avg={nt['avg_profit']:+.2f}%  BSR={nt['breakout_success_rate']:.0f}%")
        if ok.get("count", 0):
            print(f"  TRADE_OK 진입: {ok['count']}건  승률={ok['win_rate']:.0f}%  "
                  f"avg={ok['avg_profit']:+.2f}%  BSR={ok['breakout_success_rate']:.0f}%")

    print(f"\n{'='*60}\n")


def save_json(r: dict, target_date: date | None = None) -> Path:
    d = target_date or date.today()
    out = LOG_DIR / f"regime_metrics_{d.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regime Metrics Phase 1")
    parser.add_argument("--days",   type=int,  default=20, help="분석 기간 (거래일 수)")
    parser.add_argument("--date",   type=str,  default=None, help="기준일 YYYYMMDD")
    parser.add_argument("--no-save", action="store_true", help="JSON 저장 생략")
    args = parser.parse_args()

    target = date.today()
    if args.date:
        target = datetime.strptime(args.date, "%Y%m%d").date()

    result = run(days_back=args.days, target_date=target)
    print_summary(result)

    if not args.no_save:
        path = save_json(result, target)
        print(f"저장: {path}")
