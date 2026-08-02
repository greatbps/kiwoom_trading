"""
backtest_fix_c_v2.py — Fix C v2 과거 거래 가상 적용 + 임계값 그리드 탐색

분석 목표:
  "손실 거래를 얼마나 막고, 수익 거래를 얼마나 덜 잘라먹는지"

Fix C v2 조건 (OR 또는 2/3 충족, 10:30 이후에만):
  조건1: cand_rise_pct    > max_cand_rise_pct           (t0 후 추격)
  조건2: below_day_high   < max_day_high_proximity_pct  (당일 고점 근처)
  조건3: cand_to_entry_min > max_cand_to_entry_min       (지연 신호)

그리드 탐색 시나리오:
  현재안:  OR / 1.5 / 1.5 / 60
  완화안1: OR / 2.0 / 1.5 / 60
  완화안2: 2개 이상 충족 / 1.5 / 1.5 / 60
  완화안3: 2개 이상 충족 / 2.0 / 2.0 / 60

Usage:
    python -m analysis.backtest_fix_c_v2 [--features-file PATH]

Output:
    reports/fix_c_v2_backtest_YYYYMMDD.md
"""
import argparse
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"
REPORTS_DIR = BASE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

AFTER_TIME_HOUR, AFTER_TIME_MIN = 10, 30


def get_latest_features() -> Path:
    files = sorted(LOG_DIR.glob("late_entry_features_*.csv"))
    if not files:
        raise FileNotFoundError("late_entry_features_*.csv 없음")
    return files[-1]


def is_after_10_30(dt) -> bool:
    if pd.isna(dt):
        return False
    t = dt if isinstance(dt, datetime) else pd.Timestamp(dt)
    return (t.hour, t.minute) >= (AFTER_TIME_HOUR, AFTER_TIME_MIN)


# ── 조건 계산 ──────────────────────────────────────────────────────────────

def _cond1(row, thr: float) -> bool:
    """조건1: 후보 후 가격 상승 (cand_rise_pct)"""
    v = row.get("t0_to_t2_pct")
    if pd.isna(v) or row.get("t0_confidence", "none") in ("none", "low"):
        return False
    return float(v) > thr


def _cond2(row, thr: float) -> bool:
    """조건2: 당일 고점 근처 (below_day_high_pct가 thr 미만 → 고점 근처)"""
    v = row.get("below_day_high_pct")
    if pd.isna(v):
        return False
    return float(v) < thr


def _cond3(row, thr: float) -> bool:
    """조건3: 후보→진입 지연 (cand_to_entry_min)"""
    v = row.get("t0_to_t2_min")
    if pd.isna(v) or row.get("t0_confidence", "none") in ("none",):
        return False
    return float(v) > thr


def apply_fix_c_v2(
    df: pd.DataFrame,
    thr1: float,
    thr2: float,
    thr3: float,
    min_conditions: int = 1,  # 1=OR, 2=2개 이상
) -> pd.Series:
    """
    각 행에 Fix C v2 차단 여부 반환 (True = 차단).
    10:30 이전 거래는 항상 False.
    """
    def _check(row):
        if not is_after_10_30(row["t2_time"]):
            return False
        hits = sum([
            _cond1(row, thr1),
            _cond2(row, thr2),
            _cond3(row, thr3),
        ])
        return hits >= min_conditions

    return df.apply(_check, axis=1)


# ── 성과 계산 ─────────────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame, blocked: pd.Series) -> dict:
    total = len(df)
    after_1030 = df[df["t2_time"].apply(is_after_10_30)]
    n_after = len(after_1030)
    n_blocked = int(blocked.sum())

    blocked_trades = df[blocked]
    passed_trades  = df[~blocked]

    # 차단된 거래 분류
    good_block = blocked_trades[blocked_trades["result"].isin(["LOSS", "EF"])]
    bad_block  = blocked_trades[blocked_trades["result"] == "WIN"]

    # 통과된 거래 성과
    passed_pnl = passed_trades["pnl_pct"].dropna()
    blocked_pnl = blocked_trades["pnl_pct"].dropna()

    def wr(pnl): return (pnl > 0).mean() * 100 if len(pnl) > 0 else float("nan")
    def ev(pnl): return pnl.mean() if len(pnl) > 0 else float("nan")
    def pf(pnl):
        w, l = pnl[pnl > 0].sum(), pnl[pnl < 0].sum()
        return round(abs(w / l), 3) if l != 0 else float("inf")

    return {
        "total": total,
        "n_after_1030": n_after,
        "n_blocked": n_blocked,
        "pct_blocked_of_total": round(n_blocked / total * 100, 1) if total > 0 else 0,
        "pct_blocked_of_after": round(n_blocked / n_after * 100, 1) if n_after > 0 else 0,
        "good_block": len(good_block),         # 차단 + 원래 LOSS/EF (잘한 차단)
        "bad_block":  len(bad_block),           # 차단 + 원래 WIN  (아까운 차단)
        "precision":  round(len(good_block) / n_blocked * 100, 1) if n_blocked > 0 else float("nan"),
        "passed_n":   len(passed_trades),
        "passed_wr":  round(wr(passed_pnl), 1),
        "passed_ev":  round(ev(passed_pnl), 3),
        "passed_pf":  pf(passed_pnl),
        "blocked_avg_pnl": round(ev(blocked_pnl), 2),
        "blocked_win_rate": round(wr(blocked_pnl), 1),
    }


# ── 그리드 탐색 ───────────────────────────────────────────────────────────

SCENARIOS = [
    {"label": "현재안 (OR/1.5/1.5/60)",       "thr1": 1.5, "thr2": 1.5, "thr3": 60, "min_cond": 1},
    {"label": "완화안1 (OR/2.0/1.5/60)",       "thr1": 2.0, "thr2": 1.5, "thr3": 60, "min_cond": 1},
    {"label": "완화안2 (2/3이상/1.5/1.5/60)",  "thr1": 1.5, "thr2": 1.5, "thr3": 60, "min_cond": 2},
    {"label": "완화안3 (2/3이상/2.0/2.0/60)",  "thr1": 2.0, "thr2": 2.0, "thr3": 60, "min_cond": 2},
    # 추가 탐색
    {"label": "강화안 (OR/1.0/1.0/45)",        "thr1": 1.0, "thr2": 1.0, "thr3": 45, "min_cond": 1},
    {"label": "조건3만 (OR/99/99/60)",          "thr1": 99., "thr2": 99., "thr3": 60, "min_cond": 1},
    {"label": "조건2만 (OR/99/1.5/99)",         "thr1": 99., "thr2": 1.5, "thr3": 999,"min_cond": 1},
    {"label": "조건1만 (OR/1.5/99/99)",         "thr1": 1.5, "thr2": 99., "thr3": 999,"min_cond": 1},
]


def run_scenarios(df: pd.DataFrame) -> list[dict]:
    results = []
    for sc in SCENARIOS:
        blocked = apply_fix_c_v2(df, sc["thr1"], sc["thr2"], sc["thr3"], sc["min_cond"])
        ev_result = evaluate(df, blocked)
        ev_result["label"] = sc["label"]
        results.append(ev_result)
    return results


# ── 개별 거래 상세 ────────────────────────────────────────────────────────

def per_trade_detail(df: pd.DataFrame, thr1=1.5, thr2=1.5, thr3=60, min_cond=1) -> pd.DataFrame:
    """현재안 조건으로 개별 거래 차단 여부 및 차단 이유 상세"""
    rows = []
    for _, row in df.iterrows():
        after = is_after_10_30(row["t2_time"])
        c1 = _cond1(row, thr1) if after else False
        c2 = _cond2(row, thr2) if after else False
        c3 = _cond3(row, thr3) if after else False
        hits = int(c1) + int(c2) + int(c3)
        blocked = after and hits >= min_cond
        rows.append({
            "stock_code": row["stock_code"],
            "stock_name": row.get("stock_name", ""),
            "t2_time": str(row["t2_time"])[:16],
            "result": row["result"],
            "pnl_pct": row["pnl_pct"],
            "after_1030": after,
            "cond1_rise": round(float(row.get("t0_to_t2_pct") or 0), 2),
            "cond2_below_high": round(float(row.get("below_day_high_pct") or 99), 2),
            "cond3_delay_min": round(float(row.get("t0_to_t2_min") or 0), 1),
            "c1_hit": c1, "c2_hit": c2, "c3_hit": c3,
            "n_conds": hits,
            "blocked": blocked,
            "verdict": ("👍잘막음" if (blocked and row["result"] in ("LOSS","EF"))
                        else "💸아까움" if (blocked and row["result"] == "WIN")
                        else "✅통과" if not blocked
                        else "?"),
        })
    return pd.DataFrame(rows)


# ── 리포트 생성 ───────────────────────────────────────────────────────────

def generate_report(df: pd.DataFrame, results: list[dict], detail: pd.DataFrame) -> str:
    today = date.today().strftime("%Y-%m-%d")
    total = len(df)
    after = df[df["t2_time"].apply(is_after_10_30)]
    lines = []

    lines.append("# Fix C v2 백테스트 리포트")
    lines.append(f"**생성일**: {today}")
    lines.append(f"**전체 거래**: {total}건 | **10:30 이후**: {len(after)}건 ({len(after)/total*100:.0f}%)")
    lines.append("")

    # 베이스라인
    pnl_all = df["pnl_pct"].dropna()
    pnl_after = after["pnl_pct"].dropna()
    lines.append("## 1. 베이스라인 (필터 없음)")
    lines.append("")
    lines.append("| 구간 | 건수 | 승률 | 평균손익 | WIN | LOSS | EF |")
    lines.append("|---|---|---|---|---|---|---|")

    def row_line(label, sub_df):
        pnl = sub_df["pnl_pct"].dropna()
        wr = (pnl > 0).mean() * 100 if len(pnl) > 0 else 0
        avg = pnl.mean() if len(pnl) > 0 else 0
        w = (sub_df["result"] == "WIN").sum()
        lo = (sub_df["result"] == "LOSS").sum()
        ef = (sub_df["result"] == "EF").sum()
        return f"| {label} | {len(sub_df)} | {wr:.1f}% | {avg:+.2f}% | {w} | {lo} | {ef} |"

    lines.append(row_line("전체", df))
    lines.append(row_line("10:30 이후", after))
    lines.append("")

    # 시나리오 비교표
    lines.append("## 2. 시나리오별 성과 비교")
    lines.append("")
    lines.append("**용어**: 정밀도(precision) = 차단된 거래 중 손실(LOSS/EF) 비율 — 높을수록 좋음")
    lines.append("")
    lines.append("| 시나리오 | 차단% | 잘막음 | 아까움 | 정밀도 | 통과승률 | 통과EV |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        pct_of_after = r["pct_blocked_of_after"]
        lines.append(
            f"| {r['label']} | {pct_of_after:.0f}%(10:30후) | "
            f"{r['good_block']} | {r['bad_block']} | "
            f"{r['precision']:.0f}% | {r['passed_wr']:.1f}% | {r['passed_ev']:+.3f}% |"
        )
    lines.append("")

    # 베이스라인 EV 대비
    base_ev = pnl_after.mean() if len(pnl_after) > 0 else 0
    lines.append(f"> 베이스라인 10:30 이후 EV: **{base_ev:+.3f}%**")
    lines.append("")

    # 조건별 단독 분석
    lines.append("## 3. 조건별 단독 발동 분석")
    lines.append("")
    lines.append("| 조건 | 발동건수 | 발동 중 LOSS+EF | 발동 중 WIN | 정밀도 | 차단 시 avg손익 |")
    lines.append("|---|---|---|---|---|---|")
    after_df = df[df["t2_time"].apply(is_after_10_30)]
    for label, cond_col, thr, cond_fn in [
        ("조건1 추격 (cand_rise>1.5%)", "t0_to_t2_pct", 1.5, lambda r: _cond1(r, 1.5)),
        ("조건2 고점근처 (<1.5%)",       "below_day_high_pct", 1.5, lambda r: _cond2(r, 1.5)),
        ("조건3 지연신호 (>60min)",      "t0_to_t2_min", 60, lambda r: _cond3(r, 60)),
    ]:
        mask = after_df.apply(cond_fn, axis=1)
        triggered = after_df[mask]
        n = len(triggered)
        if n == 0:
            lines.append(f"| {label} | 0 | — | — | — | — |")
            continue
        ng = (triggered["result"].isin(["LOSS", "EF"])).sum()
        nw = (triggered["result"] == "WIN").sum()
        prec = ng / n * 100
        avg_pnl = triggered["pnl_pct"].dropna().mean()
        lines.append(f"| {label} | {n} | {ng} | {nw} | {prec:.0f}% | {avg_pnl:+.2f}% |")
    lines.append("")

    # 개별 거래 상세 (현재안)
    lines.append("## 4. 개별 거래 상세 (현재안: OR/1.5/1.5/60)")
    lines.append("")

    blocked_detail = detail[detail["blocked"]]
    n_good = (blocked_detail["verdict"] == "👍잘막음").sum()
    n_bad  = (blocked_detail["verdict"] == "💸아까움").sum()
    lines.append(f"**차단 총 {len(blocked_detail)}건** — 잘막음 {n_good}건 / 아까움 {n_bad}건")
    lines.append("")

    lines.append("### 차단 거래 목록")
    lines.append("")
    lines.append("| 종목 | 시각 | 결과 | 손익 | C1상승 | C2고점 | C3지연 | 충족수 | 판정 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in blocked_detail.sort_values("t2_time").iterrows():
        c1_str = f"{'✓' if r['c1_hit'] else '·'}{r['cond1_rise']:+.1f}%"
        c2_str = f"{'✓' if r['c2_hit'] else '·'}{r['cond2_below_high']:.1f}%↓"
        c3_str = f"{'✓' if r['c3_hit'] else '·'}{r['cond3_delay_min']:.0f}분"
        lines.append(
            f"| {r['stock_code']} {str(r['stock_name'])[:4]} | {r['t2_time']} | "
            f"{r['result']} | {r['pnl_pct']:+.2f}% | "
            f"{c1_str} | {c2_str} | {c3_str} | "
            f"{r['n_conds']} | {r['verdict']} |"
        )
    lines.append("")

    # 핵심 지표 요약
    lines.append("## 5. 권고 및 해석")
    lines.append("")
    cur = results[0]   # 현재안
    rel1 = results[1]  # 완화안1

    if cur["precision"] >= 70:
        lines.append(f"- **현재안 정밀도 {cur['precision']:.0f}%** — 차단 거래 대부분이 손실. 현재안 유효.")
    else:
        lines.append(f"- **현재안 정밀도 {cur['precision']:.0f}%** — 수익 거래도 일부 차단. 완화 검토 필요.")

    # EV 비교
    if cur["passed_ev"] > base_ev:
        lines.append(f"- 현재안 통과 EV {cur['passed_ev']:+.3f}% > 베이스라인 {base_ev:+.3f}% → **개선**")
    else:
        lines.append(f"- 현재안 통과 EV {cur['passed_ev']:+.3f}% ≤ 베이스라인 {base_ev:+.3f}% → **성과 개선 미확인**")

    # 아까운 차단 개수
    if cur["bad_block"] == 0:
        lines.append(f"- 아까운 차단 0건 — WIN 거래를 막지 않음. 임계값 적절.")
    elif cur["bad_block"] <= 2:
        lines.append(f"- 아까운 차단 {cur['bad_block']}건 (소수) — 허용 범위 내.")
    else:
        lines.append(f"- 아까운 차단 {cur['bad_block']}건 — 완화안1/2 비교 필요.")

    lines.append("")
    lines.append("---")
    lines.append(f"*생성: {today} by backtest_fix_c_v2.py*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features-file", type=Path, default=None)
    args = parser.parse_args()

    features_file = args.features_file or get_latest_features()
    print(f"피처 로드: {features_file}")
    df = pd.read_csv(features_file, parse_dates=["t0_time", "t2_time"])
    df = df[df["pnl_pct"].notna()].copy()
    print(f"유효 거래: {len(df)}건")

    print("\n=== 시나리오 계산 중 ===")
    results = run_scenarios(df)
    detail = per_trade_detail(df, thr1=1.5, thr2=1.5, thr3=60, min_cond=1)

    # 콘솔 요약
    print("\n| 시나리오 | 차단%(10:30이후) | 잘막음 | 아까움 | 정밀도 | 통과EV |")
    print("|---|---|---|---|---|---|")
    for r in results:
        print(
            f"| {r['label']:<38} | {r['pct_blocked_of_after']:>5.1f}% | "
            f"{r['good_block']:>4} | {r['bad_block']:>4} | "
            f"{r['precision']:>5.1f}% | {r['passed_ev']:>+7.3f}% |"
        )

    blocked_cur = detail[detail["blocked"]]
    print(f"\n현재안 차단 {len(blocked_cur)}건: 잘막음={( blocked_cur['verdict']=='👍잘막음').sum()}, 아까움={(blocked_cur['verdict']=='💸아까움').sum()}")

    report = generate_report(df, results, detail)
    out_path = REPORTS_DIR / f"fix_c_v2_backtest_{date.today().strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n✅ 리포트: {out_path}")


if __name__ == "__main__":
    main()
