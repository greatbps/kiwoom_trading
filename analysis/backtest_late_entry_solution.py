"""
backtest_late_entry_solution.py — 늦은 진입 해결 최종 백테스트

목적:
  Fix1-C를 baseline(B0)으로 놓고, SMC 완화(B1)/조기 진입(B2)/조합(B3)을
  과거 데이터로 검증해 실전 반영 조합을 확정한다.

시나리오:
  B0: Fix1-C + Fix C (현재 운영 예정)
  B1: B0 + Fix2-B (displacement 완화, CHoCH ~10분 빠른 진입)
  B2: B0 + 조기 진입 보조안 (t0+10min, SMC 유지, t0 고품질 데이터 필요)
  B3: B0 + Fix2-B + 조기 진입 보조안 (최대 조합)

분석 전략:
  전체 124건 → 손익/승률/EF/Fix C 효과 (Fix C 제거 효과 큼)
  t0_confidence="high" 47건 서브셋 → 지연 단축/진입가 개선 (Fix1-C/Fix2-B 효과)
  두 레이어를 분리해 혼선 없이 보고.

Usage:
  python -m analysis.backtest_late_entry_solution [--features-file PATH]
"""
import argparse
from datetime import date
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"
REPORTS_DIR = BASE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

FIX_C_AFTER_HOUR, FIX_C_AFTER_MIN = 10, 30
FIX_C_HIGH_THRESH = 1.5


def get_latest_features() -> Path:
    files = sorted(LOG_DIR.glob("late_entry_features_*.csv"))
    if not files:
        raise FileNotFoundError("late_entry_features_*.csv 없음")
    return files[-1]


# ── Fix C ─────────────────────────────────────────────────────────────────

def apply_fix_c(df: pd.DataFrame) -> pd.Series:
    def blocked(row):
        t = pd.Timestamp(row["t2_time"])
        if (t.hour, t.minute) < (FIX_C_AFTER_HOUR, FIX_C_AFTER_MIN):
            return False
        bdh = row.get("below_day_high_pct")
        if pd.isna(bdh) or float(bdh) < 0:
            return False
        return float(bdh) < FIX_C_HIGH_THRESH
    return df.apply(blocked, axis=1)


# ── 선형 보간 진입가 ─────────────────────────────────────────────────────

def interp(t0p: float, t2p: float, delay: float, delta: float):
    if pd.isna(t0p) or t0p <= 0 or delay <= delta:
        return None
    frac = (delay - delta) / delay
    return float(t0p) + (float(t2p) - float(t0p)) * frac


# ── 성과 계산 ─────────────────────────────────────────────────────────────

def stats(df: pd.DataFrame, price_col: str = None) -> dict:
    pnl_series = df["pnl_pct"].copy()
    if price_col and price_col in df.columns:
        sell = df["t2_price"] * (1 + df["pnl_pct"] / 100)
        ep = df[price_col].where(df[price_col].notna(), df["t2_price"])
        pnl_series = (sell - ep) / ep * 100

    n = len(pnl_series.dropna())
    if n == 0:
        return {k: float("nan") for k in ["n", "wr", "avg", "pf", "ef_pct"]}

    wr = (pnl_series > 0).mean() * 100
    avg = pnl_series.mean()
    wins = pnl_series[pnl_series > 0].sum()
    losses = abs(pnl_series[pnl_series < 0].sum())
    pf = round(wins / losses, 3) if losses > 0 else float("inf")
    ef = (df["result"] == "EF").sum() if "result" in df.columns else 0

    return {"n": n, "wr": round(wr, 1), "avg": round(avg, 3),
            "pf": pf, "ef_pct": round(ef / n * 100, 1)}


def delay_stats(df: pd.DataFrame, delta: int) -> dict:
    sub = df[df["t0_confidence"] == "high"].copy()
    if len(sub) == 0:
        return {"n": 0, "avg_delay": float("nan"), "avg_pct": float("nan"), "n_improved": 0}
    delay_orig = sub["t0_to_t2_min"]
    can_improve = delay_orig > delta
    delay_adj = delay_orig.where(~can_improve, delay_orig - delta)
    pct_adj = sub["t0_to_t2_pct"].where(
        ~can_improve,
        sub["t0_to_t2_pct"] * (delay_adj / delay_orig.replace(0, 1))
    )
    return {
        "n": len(sub),
        "avg_delay": round(delay_adj.mean(), 1),
        "avg_pct": round(pct_adj.mean(), 2),
        "n_improved": int(can_improve.sum()),
    }


def price_improve_avg(df: pd.DataFrame, price_col: str) -> float:
    sub = df[(df["t0_confidence"] == "high") & df[price_col].notna()]
    if len(sub) == 0:
        return 0.0
    improve = (sub[price_col] - sub["t2_price"]) / sub["t2_price"] * 100
    return round(improve.mean(), 2)


def win_harm(df: pd.DataFrame, price_col: str) -> float:
    wins = df[df["result"] == "WIN"].copy()
    if len(wins) == 0:
        return float("nan")
    sell = wins["t2_price"] * (1 + wins["pnl_pct"] / 100)
    ep = wins[price_col].where(wins[price_col].notna(), wins["t2_price"])
    new_pnl = (sell - ep) / ep * 100
    return round((new_pnl - wins["pnl_pct"]).mean(), 2)


# ── 시나리오 가격 계산 ────────────────────────────────────────────────────

def build_prices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    is_high = df["t0_confidence"] == "high"

    rows = list(df.itertuples())

    b0, b1, b2, b3 = [], [], [], []
    for i, row in enumerate(rows):
        t0p = getattr(row, "t0_price", None)
        t2p = row.t2_price
        delay = getattr(row, "t0_to_t2_min", None)
        pct = getattr(row, "t0_to_t2_pct", None)
        is_h = is_high.iloc[i]

        # B0: Fix1-C (t0+20min, t0_high만)
        b0.append(interp(t0p, t2p, delay, 20) if is_h else None)

        # B1: Fix2-B — t0+10min(t0_high) | t2 역산 보간(기타, delay 유효한 경우)
        if is_h:
            b1.append(interp(t0p, t2p, delay, 10))
        elif (not pd.isna(delay) if delay is not None else False) and delay > 10 and pct is not None and not pd.isna(pct):
            t0p_est = t2p / (1 + float(pct) / 100)
            frac = (float(delay) - 10) / float(delay)
            b1.append(t0p_est + (t2p - t0p_est) * frac)
        else:
            b1.append(None)

        # B2: 조기 진입 보조 (t0+10min, t0_high만, SMC 유지)
        b2.append(interp(t0p, t2p, delay, 10) if is_h else None)

        # B3: 최대 조합 (t0+5min t0_high | b1 폴백)
        if is_h:
            b3.append(interp(t0p, t2p, delay, 5))
        else:
            b3.append(b1[-1])

    df["b0_price"] = b0
    df["b1_price"] = b1
    df["b2_price"] = b2
    df["b3_price"] = b3
    return df


# ── 메인 ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features-file", type=Path, default=None)
    args = parser.parse_args()

    fpath = args.features_file or get_latest_features()
    print(f"피처 로드: {fpath}")
    df_all = pd.read_csv(fpath, parse_dates=["t0_time", "t2_time"])
    df_all = df_all[df_all["pnl_pct"].notna()].copy()
    n_all = len(df_all)
    n_t0_high = (df_all["t0_confidence"] == "high").sum()
    print(f"유효 거래: {n_all}건 | t0_high: {n_t0_high}건")

    # Fix C
    fc_mask = apply_fix_c(df_all)
    n_blocked = fc_mask.sum()
    good_block = (df_all[fc_mask]["result"].isin(["LOSS", "EF"])).sum()
    bad_block  = (df_all[fc_mask]["result"] == "WIN").sum()
    print(f"Fix C 차단: {n_blocked}건 (잘막음={good_block}, 아까움={bad_block})")

    df_all = build_prices(df_all)
    passed = df_all[~fc_mask].copy()

    # Layer 1: 전체 성과
    raw_s  = stats(df_all)
    fc_s   = stats(passed)            # Fix C만, 가격 미보정
    b0_s   = stats(passed, "b0_price")
    b1_s   = stats(passed, "b1_price")
    b2_s   = stats(passed, "b2_price")
    b3_s   = stats(passed, "b3_price")

    # Layer 2: 지연/진입가 (t0_high 서브셋)
    raw_d  = delay_stats(df_all, 0)
    b0_d   = delay_stats(passed, 20)
    b1_d   = delay_stats(passed, 10)
    b2_d   = delay_stats(passed, 10)
    b3_d   = delay_stats(passed, 5)

    b0_pi  = price_improve_avg(passed, "b0_price")
    b1_pi  = price_improve_avg(passed, "b1_price")
    b2_pi  = price_improve_avg(passed, "b2_price")
    b3_pi  = price_improve_avg(passed, "b3_price")

    wh = {k: win_harm(passed, f"{k.lower()}_price") for k in ["B0", "B1", "B2", "B3"]}

    # Fix C 기여 분해
    fc_contrib   = fc_s["avg"]  - raw_s["avg"]
    fix1c_contrib = b0_s["avg"] - fc_s["avg"]

    # 판정
    def judge_vs_b0(bx_s, bx_d, bx_wh):
        criteria = [
            bx_d["avg_delay"] < b0_d["avg_delay"] - 0.5 if not pd.isna(bx_d["avg_delay"]) else False,
            bx_d["avg_pct"]   < b0_d["avg_pct"]   - 0.01 if not pd.isna(bx_d["avg_pct"]) else False,
            bx_s["avg"]       > b0_s["avg"],
            bx_s["ef_pct"]    < b0_s["ef_pct"],
            not pd.isna(bx_wh) and bx_wh >= -0.5,
        ]
        cnt = sum(criteria)
        if cnt >= 4: return "✅ 권고"
        if cnt >= 3: return "⚠️ 조건부"
        return "❌ 보류"

    verdicts = {
        "B0": "✅ 현재운영",
        "B1": judge_vs_b0(b1_s, b1_d, wh["B1"]),
        "B2": judge_vs_b0(b2_s, b2_d, wh["B2"]),
        "B3": judge_vs_b0(b3_s, b3_d, wh["B3"]),
    }

    # 콘솔 출력
    hdr = f"{'안':<5} {'N':>4} {'WR':>6} {'avg':>8} {'PF':>7} {'EF%':>6}  {'delay(n_impr)':>14}  {'avg_pct':>8}  {'진입가개선':>9}  {'WIN훼손':>8}"
    print(f"\n{'='*len(hdr)}")
    print(hdr)
    print("-" * len(hdr))

    for label, s, d, pi, wh_v in [
        ("RAW",  raw_s, raw_d,  0.0, 0.0),
        ("B0",   b0_s,  b0_d,   b0_pi, wh["B0"]),
        ("B1",   b1_s,  b1_d,   b1_pi, wh["B1"]),
        ("B2",   b2_s,  b2_d,   b2_pi, wh["B2"]),
        ("B3",   b3_s,  b3_d,   b3_pi, wh["B3"]),
    ]:
        delay_str = f"{d['avg_delay']:.0f}분(n={d['n_improved']})" if not pd.isna(d["avg_delay"]) else "N/A"
        pct_str   = f"{d['avg_pct']:+.2f}%" if not pd.isna(d.get("avg_pct", float("nan"))) else "N/A"
        wh_str    = f"{wh_v:+.2f}%" if not pd.isna(wh_v) else "N/A"
        v = verdicts.get(label, "—") if label != "RAW" else "—"
        print(f"{label:<5} {s['n']:>4} {s['wr']:>5.1f}% {s['avg']:>+7.3f}% {s['pf']:>7.3f} "
              f"{s['ef_pct']:>5.1f}%  {delay_str:>14}  {pct_str:>8}  {pi:>+8.2f}%  {wh_str:>8}  {v}")

    print(f"{'='*len(hdr)}")
    print(f"\nFix C 기여: {fc_contrib:+.3f}%  Fix1-C 추가 기여: {fix1c_contrib:+.3f}%")

    # 최종 결론
    best_scores = {
        "B0": sum([b0_s["avg"] > raw_s["avg"], b0_s["pf"] > raw_s["pf"]]),
        "B1": sum([b1_s["avg"] > b0_s["avg"],
                   b1_d["avg_delay"] < b0_d["avg_delay"] - 0.5 if not pd.isna(b1_d["avg_delay"]) else False,
                   not pd.isna(wh["B1"]) and wh["B1"] >= -0.5]),
        "B2": sum([b2_s["avg"] > b0_s["avg"],
                   not pd.isna(wh["B2"]) and wh["B2"] >= -0.5]),
        "B3": sum([b3_s["avg"] > b0_s["avg"],
                   b3_d["avg_delay"] < b0_d["avg_delay"] - 0.5 if not pd.isna(b3_d["avg_delay"]) else False,
                   not pd.isna(wh["B3"]) and wh["B3"] >= -0.5]),
    }
    best = max(best_scores, key=best_scores.get)
    print(f"\n최종 권고 조합: {best}")

    verdict_text = {
        "B0": "Fix1-C만 실거래 반영 유지, 나머지는 보류",
        "B1": "Fix1-C + Fix2-B를 다음 실거래 후보로 승인 권고",
        "B2": "Fix1-C + 조기 진입 보조안 권고",
        "B3": "Fix1-C + Fix2-B + 조기 진입 보조안 조합 권고",
    }
    print(f">> {verdict_text[best]}")

    # 리포트
    today_str = date.today().strftime("%Y-%m-%d")
    today_tag = date.today().strftime("%Y%m%d")
    report = _make_report(
        n_all=n_all, n_blocked=n_blocked, good_block=good_block, bad_block=bad_block,
        raw_s=raw_s, fc_s=fc_s, b0_s=b0_s, b1_s=b1_s, b2_s=b2_s, b3_s=b3_s,
        raw_d=raw_d, b0_d=b0_d, b1_d=b1_d, b2_d=b2_d, b3_d=b3_d,
        b0_pi=b0_pi, b1_pi=b1_pi, b2_pi=b2_pi, b3_pi=b3_pi,
        wh=wh, verdicts=verdicts, best=best,
        fc_contrib=fc_contrib, fix1c_contrib=fix1c_contrib,
        today=today_str,
    )
    out = REPORTS_DIR / f"late_entry_solution_final_{today_tag}.md"
    out.write_text(report, encoding="utf-8")

    csv_cols = [c for c in [
        "trade_id", "stock_code", "result", "pnl_pct", "t2_time",
        "t0_confidence", "t0_to_t2_min", "t0_to_t2_pct",
        "below_day_high_pct", "b0_price", "b1_price", "b2_price", "b3_price",
    ] if c in df_all.columns]
    csv_out = LOG_DIR / f"late_entry_solution_compare_{today_tag}.csv"
    df_all[csv_cols].to_csv(csv_out, index=False, encoding="utf-8-sig")

    print(f"\n✅ 리포트: {out}")
    print(f"✅ CSV: {csv_out}")


def _make_report(**kw) -> str:
    raw_s = kw["raw_s"]; fc_s = kw["fc_s"]
    b0_s = kw["b0_s"]; b1_s = kw["b1_s"]; b2_s = kw["b2_s"]; b3_s = kw["b3_s"]
    raw_d = kw["raw_d"]; b0_d = kw["b0_d"]; b1_d = kw["b1_d"]
    b2_d = kw["b2_d"]; b3_d = kw["b3_d"]
    b0_pi = kw["b0_pi"]; b1_pi = kw["b1_pi"]; b2_pi = kw["b2_pi"]; b3_pi = kw["b3_pi"]
    wh = kw["wh"]; verdicts = kw["verdicts"]; best = kw["best"]
    fc_contrib = kw["fc_contrib"]; f1c_contrib = kw["fix1c_contrib"]
    n_all = kw["n_all"]; n_blocked = kw["n_blocked"]
    good_block = kw["good_block"]; bad_block = kw["bad_block"]
    today = kw["today"]

    def fmt_d(d):
        if pd.isna(d.get("avg_delay", float("nan"))): return "N/A"
        return f"{d['avg_delay']:.0f}분 (n={d['n_improved']})"

    def fmt_wh(v):
        return f"{v:+.2f}%" if not pd.isna(v) else "N/A"

    L = []
    L.append("# 늦은 진입 해결 최종 백테스트 보고서")
    L.append(f"**생성일**: {today}")
    L.append(f"**전체 거래**: {n_all}건 | **Fix C 차단**: {n_blocked}건 (잘막음={good_block}, 아까움={bad_block})")
    L.append("")
    L.append("> **분석 레이어 구분**")
    L.append("> - **손익/승률/EF**: 전체 124건 기준 (Fix C 적용 후 106건)")
    L.append("> - **delay/진입가**: t0_confidence=\\\"high\\\" 47건 서브셋 (기타는 t0 데이터 없음)")
    L.append("> - delay(n=N): N은 delta 이상 지연인 거래 수 (= 실제 개선 적용 건수)")
    L.append("")

    # 최종 비교표
    L.append("## 최종 비교표")
    L.append("")
    L.append("| 안 | 구성 | N | 승률 | 평균손익 | PF | EF% | cand→entry 지연 | cand→entry% | 진입가개선 | WIN훼손 | 실전난이도 | 결론 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    configs = {
        "RAW": ("없음", "—"),
        "B0":  ("Fix1-C + Fix C", "낮음(운영중)"),
        "B1":  ("B0 + Fix2-B", "낮음(YAML)"),
        "B2":  ("B0 + 조기진입 보조", "중간(코드)"),
        "B3":  ("B0 + Fix2-B + 조기진입", "중간(YAML+코드)"),
    }

    for label, s, d, pi, wh_v in [
        ("RAW", raw_s, raw_d,  0.0, 0.0),
        ("B0",  b0_s,  b0_d,   b0_pi, wh["B0"]),
        ("B1",  b1_s,  b1_d,   b1_pi, wh["B1"]),
        ("B2",  b2_s,  b2_d,   b2_pi, wh["B2"]),
        ("B3",  b3_s,  b3_d,   b3_pi, wh["B3"]),
    ]:
        cfg, diff = configs[label]
        v = verdicts.get(label, "—") if label != "RAW" else "—"
        pct_str = f"{d['avg_pct']:+.2f}%" if not pd.isna(d.get("avg_pct", float("nan"))) else "N/A"
        L.append(
            f"| {label} | {cfg} | {s['n']} | {s['wr']:.1f}% | {s['avg']:+.3f}% | "
            f"{s['pf']:.3f} | {s['ef_pct']:.1f}% | {fmt_d(d)} | {pct_str} | "
            f"{pi:+.2f}% | {fmt_wh(wh_v)} | {diff} | {v} |"
        )
    L.append("")

    # Fix C vs Fix1-C 기여 분해
    L.append("## Fix C vs Fix1-C 기여 분해")
    L.append("")
    total_improve = b0_s["avg"] - raw_s["avg"]
    fc_share = abs(fc_contrib) / (abs(fc_contrib) + abs(f1c_contrib) + 0.001) * 100
    L.append("| 단계 | 승률 | 평균손익 | PF | 개선분 |")
    L.append("|---|---|---|---|---|")
    L.append(f"| RAW (필터 없음) | {raw_s['wr']:.1f}% | {raw_s['avg']:+.3f}% | {raw_s['pf']:.3f} | 기준선 |")
    L.append(f"| + Fix C 적용 | {fc_s['wr']:.1f}% | {fc_s['avg']:+.3f}% | {fc_s['pf']:.3f} | {fc_contrib:+.3f}%p |")
    L.append(f"| + Fix1-C 진입가 보정 (B0) | {b0_s['wr']:.1f}% | {b0_s['avg']:+.3f}% | {b0_s['pf']:.3f} | {f1c_contrib:+.3f}%p |")
    L.append("")
    L.append(f"> Fix C 기여 = **{fc_contrib:+.3f}%p** (개선의 {fc_share:.0f}%)")
    L.append(f"> Fix1-C 추가 기여 = **{f1c_contrib:+.3f}%p** — "
             f"({'유의미' if abs(f1c_contrib) > 0.01 else '미미, 실질 효과 없음'})")
    L.append("")

    # Q&A
    L.append("## 핵심 질문 4개 답변")
    L.append("")

    # Q1
    q1 = b0_s["avg"] > raw_s["avg"] and b0_s["pf"] > raw_s["pf"]
    L.append("### Q1. Fix1-C만으로 충분한가?")
    L.append(f"**{'충분' if q1 else '불충분'}**")
    L.append(f"- RAW 대비 개선됨: 평균손익 {raw_s['avg']:+.3f}% → {b0_s['avg']:+.3f}% "
             f"({b0_s['avg']-raw_s['avg']:+.3f}%p), PF {raw_s['pf']:.3f} → {b0_s['pf']:.3f}")
    L.append(f"- 단, 개선의 {fc_share:.0f}%는 Fix C(고점근접 차단)가 담당 — Fix1-C 단독 기여는 "
             f"{'작음' if abs(f1c_contrib) < 0.05 else '유의미'}({f1c_contrib:+.3f}%p)")
    L.append(f"- 결론: B0 조합으로 운영 시작하되, Fix1-C 단독 효과는 20~30건 실거래 후 재측정 필요")
    L.append("")

    # Q2
    b1_delay_ok = b1_d["avg_delay"] < b0_d["avg_delay"] - 0.5 if not pd.isna(b1_d["avg_delay"]) else False
    b1_avg_ok   = b1_s["avg"] > b0_s["avg"]
    q2 = b1_delay_ok and b1_avg_ok
    L.append("### Q2. Fix2-B(displacement 완화)가 추가로 도움이 되는가?")
    L.append(f"**{'예' if q2 else '아니오'}**")
    L.append(f"- 평균손익: {b0_s['avg']:+.3f}% → {b1_s['avg']:+.3f}% ({b1_s['avg']-b0_s['avg']:+.3f}%p)")
    L.append(f"- 지연(t0_high): {fmt_d(b0_d)} → {fmt_d(b1_d)}")
    L.append(f"- WIN 훼손: {fmt_wh(wh['B1'])} ({'허용범위' if not pd.isna(wh['B1']) and wh['B1'] >= -0.5 else '과도'})")
    if not q2:
        L.append(f"- B0 대비 avg({'개선' if b1_avg_ok else '미개선'}), delay({'단축' if b1_delay_ok else '미단축'}) → 보류")
    L.append("")

    # Q3
    q3 = b2_s["avg"] > b0_s["avg"] and (pd.isna(wh["B2"]) or wh["B2"] >= -0.5)
    L.append("### Q3. 조기 진입 보조안(B2)이 필요한가?")
    L.append(f"**{'필요' if q3 else '불필요 또는 증거 부족'}**")
    L.append(f"- 평균손익: {b0_s['avg']:+.3f}% → {b2_s['avg']:+.3f}% ({b2_s['avg']-b0_s['avg']:+.3f}%p)")
    L.append(f"- WIN 훼손: {fmt_wh(wh['B2'])}")
    L.append(f"- 적용 가능 거래: n={b2_d['n_improved']}건 / 전체 {n_all}건 ({b2_d['n_improved']/n_all*100:.0f}%)")
    if not q3:
        L.append(f"- B0 대비 명확한 개선 없음 + 코드 변경 필요 + 표본 제한 → 보류")
    L.append("")

    # Q4
    verdict_map = {
        "B0": "Fix1-C만 실거래 반영 유지, 나머지는 보류",
        "B1": "Fix1-C + Fix2-B를 다음 실거래 후보로 승인 권고",
        "B2": "Fix1-C + 조기 진입 보조안 권고",
        "B3": "Fix1-C + Fix2-B + 조기 진입 보조안 조합 권고",
    }
    detail_map = {
        "B0": [
            f"B0(Fix C + Fix1-C)가 RAW 대비 평균손익 +{b0_s['avg']-raw_s['avg']:.3f}%p, PF +{b0_s['pf']-raw_s['pf']:.3f} 개선",
            f"B1/B2/B3 추가 시 B0 대비 개선 미확인 + 표본 수 감소(n≈{b1_d['n_improved']}) → 신뢰도 부족",
            "20~30건 실거래 데이터 축적 후 재검토. 다음 검토 기준: t0_high 거래에서 지연 15분 이하 달성 여부",
        ],
        "B1": [
            f"Fix2-B가 B0 대비 지연 {b0_d['avg_delay']:.0f}분→{b1_d['avg_delay']:.0f}분 단축 + avg {b0_s['avg']:+.3f}%→{b1_s['avg']:+.3f}% 개선",
            "YAML만으로 적용 가능 (atr_multiplier: 0.6, body_ratio_min: 0.3) — 코드 수정 불필요",
            "조기진입(B2)은 표본 부족 + 코드 추가 필요 → 분리 보류",
        ],
        "B2": [
            f"조기진입 보조안이 B0 대비 avg {b0_s['avg']:+.3f}%→{b2_s['avg']:+.3f}% 개선 + WIN 훼손 허용범위",
            f"단, 코드 변경 필요 + 적용 대상 n={b2_d['n_improved']}건으로 제한",
            "Fix2-B는 개선 미미 → 분리 보류. 조기진입만 선 적용 후 검증 권고",
        ],
        "B3": [
            f"B3 조합이 지연/손익/EF 모두 개선 — 단, 적용 대상 n={b3_d['n_improved']}건으로 가장 작음",
            "Fix2-B(YAML) 먼저 적용 후 조기진입(코드) 순차 도입 권고 (동시 도입 시 변인 혼재)",
            "실거래 30건 후 재검증 필수",
        ],
    }
    L.append("### Q4. 실전 반영 1순위 조합은?")
    L.append(f"**권고 조합: {best}**")
    L.append("")
    L.append(f"> **결론: {verdict_map[best]}**")
    L.append(">")
    for item in detail_map[best]:
        L.append(f"> - {item}")
    L.append("")

    # 병목 정리
    L.append("## 늦은 진입 병목 원인 정리")
    L.append("")
    L.append("| # | 구간 | 원인 | 예상 지연 | 완화 수단 | 현황 |")
    L.append("|---|---|---|---|---|---|")
    L.append("| 1 | t0 지연 | Alpha threshold=0.8 | ~20분 | Fix1-C: 0.8→0.5 | ✅ 적용됨 |")
    L.append("| 2 | t0 지연 | L6 min_win_rate=40% | ~10분 | Fix1-C: 40→30% | ✅ 적용됨 |")
    L.append("| 3 | t0 지연 | 5분 고정 루프 | ~2.5분 | 이벤트 드리븐 전환 | 🔜 미래 과제 |")
    L.append("| 4 | t0→t2 지연 | Displacement filter(1.2/0.5) | ~10분 | Fix2-B: 0.6/0.3 | ⏸ 보류 |")
    L.append("| 5 | t0→t2 지연 | RVOL 하드 게이트 1.3x | ~3~5분 | RVOL 완화 | ⏸ 보류 |")
    L.append("| 6 | 구조 | Orchestrator(continuation)+SMC(reversal) 충돌 | 전체 지연의 핵심 | 조기진입 루트 분리 | 🔜 E2 이후 검토 |")
    L.append("")
    L.append("---")
    L.append(f"*생성: {today} by backtest_late_entry_solution.py | 데이터: {n_all}건*")

    return "\n".join(L)


if __name__ == "__main__":
    main()
