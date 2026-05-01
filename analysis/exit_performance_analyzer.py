"""
Exit Performance Analyzer — A급 Exit Engine 성과 분석기

Usage:
    python -m analysis.exit_performance_analyzer           # 오늘
    python -m analysis.exit_performance_analyzer --days 5
    python -m analysis.exit_performance_analyzer --date 20260416
    python -m analysis.exit_performance_analyzer --days 5 --post-exit  # NO_PROGRESS 사후 가격 체크

출력:
  1. Exit 태그별 평균 R, 승률, 기대값
  2. NO_PROGRESS_EXIT 사후 추이 (--post-exit 시 yfinance 조회)
  3. A급 winner 비율 (2R+ 달성 비율)
  4. 4-Case 진단 + 파라미터 조정 권고

로그 파싱 대상:
  [TRADE_RESULT]   → 기본 매매 결과 (pnl, reason_tag, eq, choch, r_pct)
  [NO_PROGRESS_EXIT] / [PROFIT_LOCK] / [STRUCTURE_EXIT] / [A_FORCE_EXIT] / [TIME_EXIT]
"""

import argparse
import json
import re
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings('ignore')

LOG_DIR = Path(__file__).parent.parent / "logs"
OUT_DIR = Path(__file__).parent.parent / "logs"

# ── 파싱 패턴 ───────────────────────────────────────────────────────────────

# [TRADE_RESULT] 005930 | pnl=+2.15% | reason_tag=PROFIT_LOCK | eq=A | choch=A | hold=42m | r_pct=2.15
RE_RESULT = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[TRADE_RESULT\] "
    r"(\w+) \| pnl=([+-]?\d+\.\d+)% \| reason_tag=(\S+) \| "
    r"eq=(\S+) \| choch=(\S+) \| hold=(\d+)m \| r_pct=(\S+)"
)

# 날짜·시각 추출용 (앞 prefix)
RE_TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _f(v: str) -> float:
    try:
        return float(str(v).replace("%", "").replace(",", ""))
    except Exception:
        return 0.0


def _parse_log(path: Path) -> List[Dict]:
    trades = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = RE_RESULT.search(line)
                if not m:
                    continue
                ts, code, pnl, tag, eq, choch, hold, r_pct = m.groups()
                r_pct_f = _f(r_pct)
                r_mult  = _f(pnl) / r_pct_f if r_pct_f > 0 else 0.0
                trades.append({
                    "ts":       ts,
                    "code":     code,
                    "pnl":      float(pnl),
                    "tag":      tag,
                    "eq":       eq,
                    "choch":    choch,
                    "hold_min": int(hold),
                    "r_pct":    r_pct_f,
                    "r_mult":   round(r_mult, 3),
                })
    except Exception:
        pass
    return trades


def _load(days: int = 1, date: Optional[str] = None) -> List[Dict]:
    trades = []
    if date:
        paths = [LOG_DIR / f"auto_trading_{date}.log"]
    else:
        today = datetime.now().date()
        paths = [
            LOG_DIR / f"auto_trading_{(today - timedelta(days=i)).strftime('%Y%m%d')}.log"
            for i in range(days)
        ]
    for p in paths:
        if p.exists():
            trades.extend(_parse_log(p))
    return trades


# ── 통계 ────────────────────────────────────────────────────────────────────

def _by_tag(trades: List[Dict]) -> Dict[str, Dict]:
    buckets: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        buckets[t["tag"]].append(t)

    out = {}
    for tag, ts in sorted(buckets.items()):
        pnls   = [t["pnl"]    for t in ts]
        rmults = [t["r_mult"] for t in ts]
        wins   = [p for p in pnls if p > 0]
        out[tag] = {
            "n":        len(ts),
            "win_rate": len(wins) / len(pnls) if pnls else 0,
            "avg_pnl":  sum(pnls)   / len(pnls)   if pnls   else 0,
            "avg_r":    sum(rmults) / len(rmults)  if rmults else 0,
            "trades":   ts,
        }
    return out


def _a_grade_winner_rate(trades: List[Dict], win_r: float = 2.0) -> Dict:
    a_trades = [t for t in trades if t["eq"] == "A" and t["choch"] == "A"]
    winners  = [t for t in a_trades if t["r_mult"] >= win_r]
    return {
        "total":      len(a_trades),
        "winners":    len(winners),
        "winner_pct": len(winners) / len(a_trades) if a_trades else 0,
        "avg_r":      sum(t["r_mult"] for t in a_trades) / len(a_trades) if a_trades else 0,
    }


# ── 사후 가격 체크 (NO_PROGRESS_EXIT 이후 실제로 올랐는지) ─────────────────

def _post_exit_check(trades: List[Dict], window_bars: int = 6) -> List[Dict]:
    """NO_PROGRESS_EXIT 이후 N봉 가격 변화를 yfinance로 확인 (선택 기능)"""
    np_trades = [t for t in trades if t["tag"] == "NO_PROGRESS_EXIT"]
    if not np_trades:
        return []
    try:
        import yfinance as yf
    except ImportError:
        return [{"error": "yfinance 없음 — pip install yfinance"}]

    results = []
    for t in np_trades[:10]:  # 최대 10건
        code    = t["code"]
        ts_str  = t["ts"]
        try:
            exit_dt  = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            end_dt   = exit_dt + timedelta(minutes=window_bars * 5 + 30)
            ticker   = yf.Ticker(f"{code}.KS")
            hist     = ticker.history(
                start=exit_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"),
                interval="5m",
            )
            if hist.empty:
                results.append({"code": code, "ts": ts_str, "post_chg": None})
                continue
            # exit 직후 첫 봉 대비 window_bars봉 후 변화
            exit_close  = hist["Close"].iloc[0]
            after_close = hist["Close"].iloc[min(window_bars, len(hist) - 1)]
            post_chg    = (after_close - exit_close) / exit_close * 100
            results.append({
                "code":     code,
                "ts":       ts_str,
                "pnl":      t["pnl"],
                "post_chg": round(post_chg, 2),
                "was_wrong": post_chg > 1.0,   # 1% 이상 상승하면 "조기 청산"
            })
        except Exception as e:
            results.append({"code": code, "ts": ts_str, "post_chg": None, "err": str(e)})
    return results


# ── 전체 통계 ───────────────────────────────────────────────────────────────

def _overall_stats(trades: List[Dict]) -> Dict:
    if not trades:
        return {"n": 0, "win_rate": 0, "avg_r": 0, "avg_pnl": 0}
    pnls   = [t["pnl"]    for t in trades]
    rmults = [t["r_mult"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    return {
        "n":        len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_r":    sum(rmults) / len(rmults),
        "avg_pnl":  sum(pnls)  / len(pnls),
    }


# ── 4-Case 진단 (avg_R × win_rate 2축) ──────────────────────────────────────
#
#  avg_R = 결과 (얼마나 벌었나)
#  win_rate = 원인 방향 (왜 깨졌나)
#
#  ① avg_R < -0.5  AND  win_rate ≥ 40%  → 손절 타이밍 문제 (진입은 방향 맞음)
#  ② avg_R < -0.5  AND  win_rate < 40%  → 진입 품질 문제 (방향 자체가 틀림)
#  ③ avg_R < +0.8  AND  win_rate > 50%  → 익절 너무 빠름 (방향 맞는데 못 먹음)
#  ④ avg_R 정상    AND  win_rate 불안정 → R:R 구조 점검

def _diagnose(tag_stats: Dict, overall: Dict, post_exit: List[Dict]) -> List[str]:
    np_s = tag_stats.get("NO_PROGRESS_EXIT", {})
    af_s = tag_stats.get("A_FORCE_EXIT",     {})
    pl_s = tag_stats.get("PROFIT_LOCK",      {})

    np_n      = np_s.get("n",        0)
    np_avg_r  = np_s.get("avg_r",    0)
    np_wr     = np_s.get("win_rate", 0)
    af_n      = af_s.get("n",        0)
    pl_avg_r  = pl_s.get("avg_r",    0)
    pl_n      = pl_s.get("n",        0)

    all_avg_r = overall.get("avg_r",    0)
    all_wr    = overall.get("win_rate", 0)
    all_n     = overall.get("n",        0)

    wrong_exits = sum(1 for r in post_exit if r.get("was_wrong"))
    wrong_ratio = wrong_exits / len(post_exit) if post_exit else 0

    MIN_N = 3   # 최소 샘플
    recs  = []

    if all_n < MIN_N:
        recs.append(f"📊 샘플 {all_n}건 — 최소 {MIN_N}건 이상 쌓인 후 재분석")
        return recs

    # ─ CASE ① 손절 타이밍 문제 ─────────────────────────────────────────────
    # avg_R 나쁜데 win_rate는 정상 → 방향은 맞는데 손절에서 깎임
    if all_avg_r < -0.5 and all_wr >= 0.40:
        recs.append(
            "🟡 CASE ①: avg_R 저조 + win_rate 정상 → 손절 타이밍 문제\n"
            "   원인: 맞는 방향인데 손절에서 R을 잃음\n"
            "   → NO_PROGRESS_EXIT: no_progress_bars +3 (15→18)\n"
            "   → PROFIT_LOCK: mfe_r 1.5→1.2 (더 빨리 보호선 잠금)"
        )

    # ─ CASE ② 진입 품질 문제 ────────────────────────────────────────────────
    # avg_R도 나쁘고 win_rate도 낮음 → 방향 자체가 틀림
    if all_avg_r < -0.5 and all_wr < 0.40:
        recs.append(
            "🔴 CASE ②: avg_R 저조 + win_rate 낮음 → 진입 품질 문제\n"
            "   원인: 신호 방향 자체가 틀림\n"
            "   → 지금은 YAML 건드리지 말고 3~5일 더 관찰\n"
            "   → ENTRY_QUALITY: eq=C 진입 비율 확인 (entry_quality_analyzer)"
        )

    # ─ CASE ③ 익절 너무 빠름 ────────────────────────────────────────────────
    # avg_R이 낮은데 win_rate는 높음 → 이기고 있는데 조금씩만 먹음
    if all_avg_r < 0.8 and all_wr > 0.50:
        recs.append(
            "🟡 CASE ③: avg_R 저조 + win_rate 높음 → 익절 너무 빠름\n"
            "   원인: 이기긴 하는데 R 확보 부족\n"
            "   → PROFIT_LOCK: floor_r 0.5→0.3 (보호선 낮춰서 수익 더 키우기)\n"
            "   → no_progress_bars +3 (더 기다리기)"
        )

    # ─ CASE ④ R:R 구조 문제 ─────────────────────────────────────────────────
    # avg_R은 괜찮은데 전체 P&L 불안정 → R:R 비대칭 문제
    if all_avg_r >= 0.3 and all_wr < 0.35:
        recs.append(
            "🟡 CASE ④: avg_R 정상 + win_rate 낮음 → R:R 구조 점검\n"
            "   원인: 크게 먹지만 지는 빈도가 너무 많음\n"
            "   → TP1 거리 확인 (r_tp.tp1_r_mult 1.5→1.2 축소 검토)"
        )

    # ─ 세부 태그 진단 ────────────────────────────────────────────────────────
    if np_n >= MIN_N:
        if -0.3 <= np_avg_r <= 0.3:
            recs.append(f"✅ NO_PROGRESS_EXIT 정상 (avg_R={np_avg_r:+.2f}) — 건드리지 마라")
        elif np_avg_r < -0.5 and np_wr >= 0.40:
            recs.append(
                f"⚠️  NO_PROGRESS_EXIT avg_R={np_avg_r:+.2f}, win_rate={np_wr:.0%}\n"
                "   → 타이밍 문제: no_progress_bars +3"
            )

    if wrong_ratio > 0.3:
        recs.append(
            f"⚠️  NO_PROGRESS 이후 상승 {wrong_ratio:.0%} — 조기 청산 의심\n"
            "   → no_progress_bars 15 → 18"
        )

    if pl_n >= 2 and pl_avg_r >= 0.8:
        recs.append(f"✅ PROFIT_LOCK 정상 (avg_R={pl_avg_r:+.2f}) — 수익 보호 유효")
    elif pl_n >= 2 and pl_avg_r < 0.5:
        recs.append(
            f"⚠️  PROFIT_LOCK avg_R={pl_avg_r:+.2f} 저조\n"
            "   → mfe_r 티어 낮추기 (1.5→1.2) 또는 floor_r 축소"
        )

    # ─ 전체 OK ───────────────────────────────────────────────────────────────
    if all_avg_r >= 0.5 and all_wr >= 0.40:
        recs.append(
            f"🚀 전략 정상 작동 (avg_R={all_avg_r:+.2f}, win_rate={all_wr:.0%})\n"
            "   → 건드리지 말고 데이터 계속 쌓기"
        )

    return recs if recs else ["📊 케이스 미해당 — 추가 데이터 대기"]


# ── 출력 ────────────────────────────────────────────────────────────────────

_BAR = "━" * 66


def _print_tag_table(tag_stats: Dict):
    PRIORITY = ["NO_PROGRESS_EXIT", "A_FORCE_EXIT", "PROFIT_LOCK",
                "STRUCTURE_EXIT", "STRUCT_WEAK", "TIME_EXIT", "R_TP1", "R_TP2", "OTHER"]
    ordered = sorted(
        tag_stats.items(),
        key=lambda x: PRIORITY.index(x[0]) if x[0] in PRIORITY else 99
    )

    print(f"\n{_BAR}")
    print("  Exit 태그별 성과  (R = pnl ÷ 진입시 1R 크기)")
    print(_BAR)
    print(f"  {'태그':<22}  {'건':^4}  {'승률':^7}  {'avg_PnL':^9}  {'avg_R':^7}  {'판단'}")
    print(f"  {'-'*22}  {'-'*4}  {'-'*7}  {'-'*9}  {'-'*7}  {'-'*10}")

    for tag, s in ordered:
        avg_r  = s['avg_r']
        if avg_r > 0.5:
            judge = "✅ 정상"
        elif -0.3 <= avg_r <= 0.5:
            judge = "⚪ 중립"
        else:
            judge = "🔴 점검"
        print(
            f"  {tag:<22}  {s['n']:^4}  {s['win_rate']:.0%}   "
            f"  {s['avg_pnl']:+.2f}%    {avg_r:+.2f}     {judge}"
        )


def _print_winner(w: Dict):
    print(f"\n{_BAR}")
    print("  A급 (eq=A + choch=A) Winner 분석")
    print(_BAR)
    if w["total"] == 0:
        print("  A급 거래 없음")
        return
    print(f"  총 A급 거래  : {w['total']}건")
    print(f"  2R+ 달성     : {w['winners']}건  ({w['winner_pct']:.0%})")
    print(f"  평균 R       : {w['avg_r']:+.2f}R")
    if w["winner_pct"] < 0.3 and w["total"] >= 5:
        print("  ⚠️  2R+ 달성률 30% 미만 → 진입 필터 재검토 필요")
    elif w["winner_pct"] >= 0.5:
        print("  ✅ A급 절반 이상이 2R 달성 — 연장 보유 전략 유효")


def _print_post_exit(results: List[Dict]):
    if not results:
        return
    if results and "error" in results[0]:
        print(f"\n  ⚠️  사후 가격 조회: {results[0]['error']}")
        return
    print(f"\n{_BAR}")
    print("  NO_PROGRESS_EXIT 사후 추이  (exit 후 30분 가격 변화)")
    print(_BAR)
    wrong = [r for r in results if r.get("was_wrong")]
    print(f"  분석 건수: {len(results)}건  |  조기청산(이후 +1%+): {len(wrong)}건  "
          f"({len(wrong)/len(results):.0%})")
    print()
    for r in results:
        post = r.get("post_chg")
        flag = "⚠️ 조기" if r.get("was_wrong") else "  OK  "
        post_str = f"{post:+.2f}%" if post is not None else "조회실패"
        print(f"  {flag}  {r['code']:8}  pnl={r['pnl']:+.2f}%  → 사후={post_str}  ({r['ts'][11:16]})")


def _print_recs(recs: List[str]):
    print(f"\n{_BAR}")
    print("  진단 & 파라미터 권고")
    print(_BAR)
    for r in recs:
        print(f"  {r}")
    print()


# ── 메인 ────────────────────────────────────────────────────────────────────

def run(days: int = 1, date: Optional[str] = None,
        post_exit: bool = False, save_json: bool = True):

    trades = _load(days, date)

    print(f"\n{'='*66}")
    print(f"  Exit Performance Analyzer  |  {len(trades)}건 거래 파싱")
    tag = date or datetime.now().strftime("%Y%m%d")
    print(f"  기간: {tag}  |  days={days}")
    print(f"{'='*66}")

    if not trades:
        print("\n  ⚠️  [TRADE_RESULT] 로그가 없습니다.")
        print("     실매매 발생 후 재실행하세요.")
        return

    tag_stats  = _by_tag(trades)
    overall    = _overall_stats(trades)
    winner     = _a_grade_winner_rate(trades)
    post       = _post_exit_check(trades) if post_exit else []
    recs       = _diagnose(tag_stats, overall, post)

    # 전체 요약 한 줄
    print(f"\n  전체  n={overall['n']}  avg_R={overall['avg_r']:+.2f}  "
          f"win_rate={overall['win_rate']:.0%}  avg_pnl={overall['avg_pnl']:+.2f}%")

    _print_tag_table(tag_stats)
    _print_winner(winner)
    if post_exit:
        _print_post_exit(post)
    _print_recs(recs)

    if save_json:
        out = {
            "generated_at": datetime.now().isoformat(),
            "days": days, "date": date,
            "n_trades": len(trades),
            "overall": overall,
            "tag_stats": {k: {kk: vv for kk, vv in v.items() if kk != "trades"}
                          for k, v in tag_stats.items()},
            "winner": winner,
            "post_exit": post,
            "recommendations": recs,
        }
        out_path = OUT_DIR / f"exit_performance_{tag}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"  JSON: {out_path}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Exit Performance Analyzer")
    p.add_argument("--days",       type=int,            default=1)
    p.add_argument("--date",       type=str,            default=None)
    p.add_argument("--post-exit",  action="store_true", help="NO_PROGRESS 사후 가격 yfinance 조회")
    p.add_argument("--no-json",    action="store_true")
    args = p.parse_args()
    run(days=args.days, date=args.date,
        post_exit=args.post_exit, save_json=not args.no_json)
