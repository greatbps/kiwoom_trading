"""
ENTRY_QUALITY 분석기 (오프라인)

Usage:
    python -m analysis.entry_quality_analyzer          # 오늘
    python -m analysis.entry_quality_analyzer --days 5 # 최근 5일
    python -m analysis.entry_quality_analyzer --date 20260416

출력:
  1. eq 등급별 기대값표 (승률 · 평균R · 손익비)
  2. choch × eq 2D 히트맵
  3. TP1 / TP2 hit rate
  4. TIME_EXIT 비율 분석
  5. Grade A 실패 케이스 상세
  6. 파라미터 조정 권고
"""

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG_DIR = Path(__file__).parent.parent / "logs"
OUT_DIR = Path(__file__).parent.parent / "logs"

# ── 로그 파싱 패턴 ──────────────────────────────────────────────────────────

# [ENTRY_QUALITY] 005930 삼성전자 | choch=A eq=B | bars_since=4 | atr_dist=0.18 | depth=35% | vol_ratio=0.52 | 1R=2.15% | TP1=68500 TP2=72000
RE_ENTRY = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[ENTRY_QUALITY\] "
    r"(\w+) (.+?) \| choch=(\w+) eq=(\w+) \| bars_since=(\S+) \| "
    r"atr_dist=(\S+) \| depth=(\S+)% \| vol_ratio=(\S+) \| "
    r"1R=(\S+)% \| TP1=(\S+) TP2=(\S+)"
)

# [TRADE_RESULT] 005930 | pnl=+2.15% | reason_tag=R_TP1 | eq=B | choch=A | hold=35m | r_pct=2.15
RE_RESULT = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[TRADE_RESULT\] "
    r"(\w+) \| pnl=([+-]?\d+\.\d+)% \| reason_tag=(\S+) \| "
    r"eq=(\S+) \| choch=(\S+) \| hold=(\d+)m \| r_pct=(\S+)"
)


def _parse_log(path: Path) -> Tuple[List[Dict], List[Dict]]:
    """로그 파일 하나에서 entries / results 추출"""
    entries, results = [], []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = RE_ENTRY.search(line)
                if m:
                    ts, code, name, choch, eq, bars, atr_d, depth, vol_r, r1, tp1, tp2 = m.groups()
                    entries.append({
                        "ts": ts, "code": code, "name": name.strip(),
                        "choch": choch, "eq": eq,
                        "bars_since": bars,
                        "atr_dist": _f(atr_d),
                        "depth": _f(depth),
                        "vol_ratio": _f(vol_r),
                        "r1_pct": _f(r1),
                        "tp1": _f(tp1), "tp2": _f(tp2),
                    })
                    continue
                m = RE_RESULT.search(line)
                if m:
                    ts, code, pnl, tag, eq, choch, hold, r_pct = m.groups()
                    results.append({
                        "ts": ts, "code": code,
                        "pnl": float(pnl), "reason_tag": tag,
                        "eq": eq, "choch": choch,
                        "hold_min": int(hold),
                        "r_pct": _f(r_pct),
                    })
    except Exception:
        pass
    return entries, results


def _f(v: str) -> float:
    try:
        return float(v.replace("%", "").replace(",", ""))
    except Exception:
        return 0.0


def _load_days(days: int = 1, date: Optional[str] = None) -> Tuple[List[Dict], List[Dict]]:
    all_entries, all_results = [], []
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
            e, r = _parse_log(p)
            all_entries.extend(e)
            all_results.extend(r)
    return all_entries, all_results


# ── 통계 계산 ───────────────────────────────────────────────────────────────

def _grade_stats(results: List[Dict], key: str = "eq") -> Dict:
    """eq 또는 choch 등급별 통계"""
    buckets: Dict[str, List[float]] = defaultdict(list)
    for r in results:
        g = r.get(key, "-")
        buckets[g].append(r["pnl"])
    out = {}
    for g, pnls in sorted(buckets.items()):
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        avg_win  = sum(wins)  / len(wins)  if wins   else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        ev = (len(wins) / len(pnls)) * avg_win + (len(losses) / len(pnls)) * avg_loss
        out[g] = {
            "n":        len(pnls),
            "wins":     len(wins),
            "win_rate": len(wins) / len(pnls) if pnls else 0,
            "avg_pnl":  sum(pnls) / len(pnls) if pnls else 0,
            "avg_win":  avg_win,
            "avg_loss": avg_loss,
            "rr":       rr,
            "ev":       ev,
        }
    return out


def _heatmap(results: List[Dict]) -> Dict[Tuple[str, str], Dict]:
    """choch × eq 2D 히트맵 데이터"""
    cells: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for r in results:
        cells[(r.get("choch", "-"), r.get("eq", "-"))].append(r["pnl"])
    out = {}
    for k, pnls in cells.items():
        wins = sum(1 for p in pnls if p > 0)
        out[k] = {
            "n": len(pnls),
            "win_rate": wins / len(pnls) if pnls else 0,
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
        }
    return out


def _tp_hit_rates(results: List[Dict]) -> Dict:
    tp1 = sum(1 for r in results if "R_TP1" in r["reason_tag"])
    tp2 = sum(1 for r in results if "R_TP2" in r["reason_tag"])
    te  = sum(1 for r in results if "TIME_EXIT" in r["reason_tag"])
    n   = len(results)
    return {"n": n, "tp1": tp1, "tp2": tp2, "time_exit": te,
            "tp1_rate": tp1 / n if n else 0,
            "tp2_rate": tp2 / n if n else 0,
            "te_rate":  te  / n if n else 0}


def _grade_a_failures(results: List[Dict]) -> List[Dict]:
    return [r for r in results if r.get("eq") == "A" and r["pnl"] < 0]


# ── 출력 ────────────────────────────────────────────────────────────────────

_BAR = "━" * 64

def _print_grade_table(title: str, stats: Dict):
    print(f"\n{_BAR}")
    print(f"  {title}")
    print(_BAR)
    print(f"  {'등급':^4}  {'건수':^5}  {'승률':^7}  {'평균PnL':^9}  {'평균이익':^9}  {'평균손실':^9}  {'손익비':^6}  {'기대값':^9}")
    print(f"  {'-'*4}  {'-'*5}  {'-'*7}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*6}  {'-'*9}")
    for g, s in stats.items():
        wr_str  = f"{s['win_rate']:.0%}"
        rr_str  = f"{s['rr']:.2f}" if s['rr'] != float('inf') else "∞"
        ev_col  = f"{s['ev']:+.2f}%"
        flag    = " ⚠️" if s.get("ev", 0) < -0.5 else (" ✅" if s.get("ev", 0) > 0.3 else "")
        print(
            f"  {g:^4}  {s['n']:^5}  {wr_str:^7}  {s['avg_pnl']:+.2f}%   "
            f"{s['avg_win']:+.2f}%   {s['avg_loss']:+.2f}%   {rr_str:^6}  {ev_col}{flag}"
        )


def _print_heatmap(heatmap: Dict):
    chochs = sorted({k[0] for k in heatmap})
    eqs    = sorted({k[1] for k in heatmap})
    print(f"\n{_BAR}")
    print(f"  choch × eq 2D 히트맵  (승률% / 평균PnL% / 건수)")
    print(_BAR)
    header = f"  {'':^5}" + "".join(f"  eq={e:^22}" for e in eqs)
    print(header)
    print(f"  {'-'*5}" + "  " + "-"*70)
    for c in chochs:
        row = f"  {c:^5}"
        for e in eqs:
            cell = heatmap.get((c, e))
            if cell:
                row += f"  {cell['win_rate']:.0%} / {cell['avg_pnl']:+.1f}% / {cell['n']}건     "
            else:
                row += f"  {'—':^28}"
        print(row)


def _print_exit_analysis(tp: Dict):
    print(f"\n{_BAR}")
    print(f"  청산 구조 분석  (총 {tp['n']}건)")
    print(_BAR)
    print(f"  R_TP1 (1.5R 익절)  : {tp['tp1']:3}건  {tp['tp1_rate']:.0%}")
    print(f"  R_TP2 (3R  익절)   : {tp['tp2']:3}건  {tp['tp2_rate']:.0%}")
    print(f"  TIME_EXIT (횡보)   : {tp['time_exit']:3}건  {tp['te_rate']:.0%}")
    other = tp['n'] - tp['tp1'] - tp['tp2'] - tp['time_exit']
    print(f"  기타              : {other:3}건  {other/tp['n']:.0%}" if tp['n'] else "")

    if tp['te_rate'] > 0.30:
        print("\n  ⚠️  TIME_EXIT 30% 초과 → 진입이 늦거나 TP가 너무 멀다")
        print("      → G2 기준 강화(ema9_touch_atr_mult 축소) 또는 tp2_r_mult 조정")
    if tp['tp2_rate'] < 0.1 and tp['n'] >= 5:
        print("\n  💡 TP2 달성률 10% 미만 → 3R 너무 멀다")
        print("      → tp2_r_mult: 3.0 → 2.5 시도 권장")


def _print_a_failures(failures: List[Dict]):
    print(f"\n{_BAR}")
    print(f"  Grade A 실패 케이스 (eq=A AND pnl < 0)  총 {len(failures)}건")
    print(_BAR)
    if not failures:
        print("  없음 ✅")
        return
    for r in failures:
        print(
            f"  {r['code']:8}  pnl={r['pnl']:+.2f}%  "
            f"reason={r['reason_tag']:12}  hold={r['hold_min']}m  "
            f"choch={r['choch']}  ts={r['ts'][11:16]}"
        )
    if len(failures) >= 3:
        tags = defaultdict(int)
        for r in failures:
            tags[r["reason_tag"]] += 1
        top_tag = max(tags, key=tags.get)
        print(f"\n  → 주요 실패 패턴: {top_tag} ({tags[top_tag]}건)")
        print("    atr_dist/vol_ratio 분포를 ENTRY_QUALITY 로그에서 확인하세요.")


def _print_recommendations(eq_stats: Dict, tp: Dict, failures: List[Dict]):
    print(f"\n{_BAR}")
    print("  파라미터 조정 권고")
    print(_BAR)
    recs = []

    c_stat = eq_stats.get("C")
    b_stat = eq_stats.get("B")
    a_stat = eq_stats.get("A")

    if c_stat and c_stat["n"] >= 3 and c_stat["ev"] < -0.3:
        recs.append("🔴 eq=C 기대값 음수 → 진입 차단 권장 (YAML: min_eq_grade: B)")
    if b_stat and b_stat["n"] >= 3 and b_stat["ev"] < -0.2:
        recs.append("🟡 eq=B 기대값 저조 → 포지션 50% 축소 또는 필터 강화")
    if a_stat and a_stat["win_rate"] < 0.45 and a_stat["n"] >= 5:
        recs.append("🟡 eq=A 승률 45% 미만 → G2/G3 기준 강화 (atr_dist, depth 조건 상향)")
    if tp["te_rate"] > 0.30:
        recs.append("🟡 TIME_EXIT 과다 → TP1 거리 단축 또는 진입 시점 앞당기기")
    if tp["tp2_rate"] < 0.10 and tp["n"] >= 5:
        recs.append("💡 TP2 달성 희소 → tp2_r_mult 3.0→2.5 조정 검토")
    if a_stat and len(failures) / max(a_stat["n"], 1) > 0.4:
        recs.append("⚠️  eq=A 실패율 40% 초과 → 등급 기준 재검토 필요")

    if not recs:
        recs.append("✅ 현재 파라미터 이상 없음 — 데이터 더 쌓인 후 재분석")

    for r in recs:
        print(f"  {r}")


# ── 메인 ────────────────────────────────────────────────────────────────────

def run(days: int = 1, date: Optional[str] = None, save_json: bool = True):
    entries, results = _load_days(days, date)

    print(f"\n{'='*64}")
    print(f"  ENTRY QUALITY 분석기  |  파싱: 진입 {len(entries)}건 / 결과 {len(results)}건")
    print(f"{'='*64}")

    if not results:
        print("\n  ⚠️  [TRADE_RESULT] 로그가 없습니다.")
        print("     • EMA9 눌림 필터(ema9_wait_pullback.enabled: true) 후 첫 거래 필요")
        print("     • 오늘 날짜 로그 경로:", LOG_DIR / f"auto_trading_{datetime.now().strftime('%Y%m%d')}.log")
        if entries:
            print(f"\n  진입 시도 {len(entries)}건 감지:")
            for e in entries[:5]:
                print(f"    {e['code']} {e['name']} choch={e['choch']} eq={e['eq']} depth={e['depth']:.0%}")
        return

    eq_stats   = _grade_stats(results, "eq")
    tp_stats   = _tp_hit_rates(results)
    heatmap    = _heatmap(results)
    a_failures = _grade_a_failures(results)

    _print_grade_table("eq 등급별 성과 (눌림 품질 A=최고)", eq_stats)
    _print_grade_table("choch 등급별 성과 (CHoCH 품질)", _grade_stats(results, "choch"))
    _print_heatmap(heatmap)
    _print_exit_analysis(tp_stats)
    _print_a_failures(a_failures)
    _print_recommendations(eq_stats, tp_stats, a_failures)

    print(f"\n{_BAR}\n")

    if save_json:
        tag = date or datetime.now().strftime("%Y%m%d")
        out_path = OUT_DIR / f"entry_quality_report_{tag}.json"
        report = {
            "generated_at": datetime.now().isoformat(),
            "days": days, "date": date,
            "n_entries": len(entries), "n_results": len(results),
            "eq_stats": eq_stats,
            "tp_stats": tp_stats,
            "heatmap": {f"{k[0]}x{k[1]}": v for k, v in heatmap.items()},
            "a_failures": a_failures,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  JSON 저장: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ENTRY_QUALITY 로그 분석기")
    parser.add_argument("--days",    type=int,   default=1,    help="분석 일수 (기본: 1)")
    parser.add_argument("--date",    type=str,   default=None, help="특정 날짜 YYYYMMDD")
    parser.add_argument("--no-json", action="store_true",      help="JSON 저장 안 함")
    args = parser.parse_args()
    run(days=args.days, date=args.date, save_json=not args.no_json)
