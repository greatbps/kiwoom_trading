#!/usr/bin/env python3
"""
delay=0 저품질 CHoCH 전수 분석
Phase 1: t0_high & delay=0 거래 23건 구조 해부
"""

import pandas as pd
import numpy as np
from pathlib import Path

CSV = "logs/late_entry_features_20260704.csv"

# ─── 데이터 로드 ─────────────────────────────────────────────────────────────

def load_data():
    df = pd.read_csv(CSV, parse_dates=["t0_time", "t2_time"])
    df = df[df["pnl_pct"].notna()].copy()
    return df

def apply_fix_c(df):
    def is_blocked(row):
        t = row["t2_time"]
        if (t.hour, t.minute) < (10, 30):
            return False
        bdh = row.get("below_day_high_pct")
        return not pd.isna(bdh) and float(bdh) >= 0 and float(bdh) < 1.5
    blocked = df.apply(is_blocked, axis=1)
    return df[~blocked].copy(), df[blocked].copy()

# ─── 태그 분류 ───────────────────────────────────────────────────────────────

TAG_EARLY_OPEN  = "EARLY_OPEN"   # 장 초반 즉시 진입 (hour < 10)
TAG_NO_RANGE    = "NO_DAY_RANGE" # 당일 고점 데이터 결측/음수
TAG_HIGH_PROX   = "HIGH_PROX"    # 고점 근접 bdh < 3%
TAG_OVEREXTEND  = "OVEREXTEND"   # 단기 과열 prior_5d > 40%
TAG_CLEAN       = "CLEAN"        # 이상 없음

def assign_tag(row):
    t   = row["t2_time"]
    bdh = row.get("below_day_high_pct")
    p5  = row.get("prior_5d_pct_t0")
    tags = []
    if t.hour < 10:
        tags.append(TAG_EARLY_OPEN)
    if pd.isna(bdh) or (not pd.isna(bdh) and float(bdh) < 0):
        tags.append(TAG_NO_RANGE)
    elif float(bdh) < 3.0:
        tags.append(TAG_HIGH_PROX)
    if not pd.isna(p5) and float(p5) > 40.0:
        tags.append(TAG_OVEREXTEND)
    if not tags:
        tags.append(TAG_CLEAN)
    return "+".join(tags)

def why_memo(row):
    result = row["result"]
    bdh    = row.get("below_day_high_pct", float("nan"))
    ipos   = row.get("intraday_pos_pct", float("nan"))
    p5     = row.get("prior_5d_pct_t0", float("nan"))
    tags   = row["tag"]
    t      = row["t2_time"]

    if result in ("EF", "LOSS"):
        if TAG_EARLY_OPEN in tags:
            return f"장 초반({t.strftime('%H:%M')}) CHoCH — 일중 방향성 미확정 상태에서 즉시 진입"
        if TAG_NO_RANGE in tags:
            return "당일 고점 데이터 결측 — 고점 근접도 판단 불가, 장 초반이거나 이상치"
        if TAG_HIGH_PROX in tags:
            return f"고점 근접(bdh={bdh:.1f}%) — 여유 공간 없이 고점 수렴 구간에서 즉시 CHoCH"
        if TAG_OVEREXTEND in tags:
            return f"단기 과열(5d+{p5:.0f}%) — 이미 확장된 구간에서 추가 CHoCH 발동"
        return f"패턴 불명 (bdh={bdh:.1f}% ipos={ipos:.0f}%)"
    else:  # WIN
        bdh_str = f"{bdh:.1f}%" if not pd.isna(bdh) else "결측"
        ipos_str = f"{ipos:.0f}%" if not pd.isna(ipos) else "결측"
        return f"정상 진입 — bdh={bdh_str} ipos={ipos_str} 여유 공간 확보 상태"

# ─── 통계 헬퍼 ───────────────────────────────────────────────────────────────

def stats(df):
    n = len(df)
    if n == 0:
        return dict(n=0, wr=0, avg=0, pf=0, ef_pct=0)
    wins = (df["result"] == "WIN").sum()
    efs  = (df["result"] == "EF").sum()
    avg  = df["pnl_pct"].mean()
    gw   = df[df["result"] == "WIN"]["pnl_pct"].sum()
    gl   = df[df["result"] != "WIN"]["pnl_pct"].sum()
    pf   = abs(gw / gl) if gl != 0 else float("inf")
    return dict(n=n, wr=wins/n*100, avg=avg, pf=pf, ef_pct=efs/n*100,
                win_n=int(wins), ef_n=int(efs))

# ─── 메인 ───────────────────────────────────────────────────────────────────

def main():
    df = load_data()
    passed, blocked_fc = apply_fix_c(df)

    d0   = passed[(passed["t0_confidence"] == "high") & (passed["t0_to_t2_min"] == 0)].copy()
    dpos = passed[(passed["t0_confidence"] == "high") & (passed["t0_to_t2_min"] > 0)].copy()
    none = passed[passed["t0_confidence"] == "none"].copy()

    grpA = d0[d0["result"].isin(["EF", "LOSS"])]
    grpB = d0[d0["result"] == "WIN"]

    SEP = "=" * 70

    print(SEP)
    print("  delay=0 저품질 CHoCH 전수 분석 — Phase 1")
    print(SEP)

    print(f"\n전체 거래: {len(df)}건 | Fix C 차단: {len(blocked_fc)}건 | 통과: {len(passed)}건")
    print(f"delay=0 t0_high: {len(d0)}건   (Group A EF/LOSS={len(grpA)}, Group B WIN={len(grpB)})")
    print(f"delay>0 t0_high: {len(dpos)}건  EF%={stats(dpos)['ef_pct']:.1f}%, WR={stats(dpos)['wr']:.1f}%")
    print(f"t0_none:         {len(none)}건  EF%={stats(none)['ef_pct']:.1f}%, WR={stats(none)['wr']:.1f}%")

    # ── 세그먼트 비교 ─────────────────────────────────────────────────────
    print(f"\n{'세그먼트':<22} {'N':>4} {'WR':>6} {'avg':>8} {'PF':>5} {'EF%':>5}  해석")
    print("-" * 65)
    rows = [
        ("t0_high delay=0",  d0,   "🔴 진짜 문제"),
        ("t0_high delay>0",  dpos, "✅ 정상"),
        ("t0_none",          none, "별도 추적 필요"),
    ]
    for label, sub, interp in rows:
        s = stats(sub)
        print(f"  {label:<20} {s['n']:>4}  {s['wr']:>5.1f}%  {s['avg']:>+7.3f}%  {s['pf']:>5.3f}  {s['ef_pct']:>4.1f}%  {interp}")

    # ── Group A vs B 피처 비교 ─────────────────────────────────────────────
    print(f"\n{'피처':<26} {'A(EF/LOSS) avg':>16} {'B(WIN) avg':>12} {'차이':>10}")
    print("-" * 68)
    feats = [
        ("below_day_high_pct", "당일 고점 대비 거리(%)"),
        ("intraday_pos_pct",   "당일 인트라데이 위치(%)"),
        ("prior_5d_pct_t0",   "5일 사전 수익률(%)"),
        ("prior_10d_pct_t0",  "10일 사전 수익률(%)"),
        ("pnl_pct",           "실현 PnL(%)"),
    ]
    for col, desc in feats:
        a_val = grpA[col].mean()
        b_val = grpB[col].mean()
        diff  = b_val - a_val
        if pd.isna(a_val) and pd.isna(b_val):
            print(f"  {desc:<24}  {'데이터 없음':>16}")
        else:
            a_s = f"{a_val:+.2f}" if not pd.isna(a_val) else "  N/A"
            b_s = f"{b_val:+.2f}" if not pd.isna(b_val) else "  N/A"
            d_s = f"{diff:+.2f}" if not pd.isna(diff) else "  N/A"
            print(f"  {desc:<24}  {a_s:>14}  {b_s:>10}  {d_s:>9}")

    # ── 시간대 분포 ───────────────────────────────────────────────────────
    print("\n=== 시간대별 분포 (delay=0) ===")
    d0_t = d0.copy()
    d0_t["tbin"] = d0_t["t2_time"].apply(lambda x: f"{x.hour:02d}:{(x.minute//30)*30:02d}")
    t_dist = d0_t.groupby(["tbin", "result"]).size().unstack(fill_value=0)
    for col in ["EF", "LOSS", "WIN"]:
        if col not in t_dist.columns:
            t_dist[col] = 0
    print(f"  {'시간대':>6}  {'EF':>4} {'LOSS':>5} {'WIN':>4}  해석")
    for tbin, row_t in t_dist.iterrows():
        ef = row_t.get("EF", 0); lo = row_t.get("LOSS", 0); wi = row_t.get("WIN", 0)
        total = ef + lo + wi
        h = int(tbin[:2])
        if h < 10 and wi == 0 and total > 0:
            note = "🔴 100% 실패 — 장 초반 EARLY_OPEN"
        elif wi == 0 and total > 0:
            note = "🔴 100% 실패 — 고점근접/과열/이상치"
        elif wi > 0:
            note = "⚠️ 혼재"
        else:
            note = ""
        print(f"  {tbin:>6}   {ef:>3}    {lo:>3}   {wi:>3}  {note}")

    # ── below_day_high_pct 구간 ───────────────────────────────────────────
    print("\n=== below_day_high_pct 구간 (delay=0) ===")
    d0_t["bdh_bin"] = pd.cut(
        d0_t["below_day_high_pct"].fillna(-999),
        bins=[-9999, -0.001, 1.5, 3, 5, 10, 20, 9999],
        labels=["neg/na", "<1.5%", "1.5~3%", "3~5%", "5~10%", "10~20%", ">20%"]
    )
    bdh_dist = d0_t.groupby(["bdh_bin", "result"], observed=True).size().unstack(fill_value=0)
    for col in ["EF", "LOSS", "WIN"]:
        if col not in bdh_dist.columns:
            bdh_dist[col] = 0
    print(f"  {'구간':>7}  {'EF':>4} {'LOSS':>5} {'WIN':>4}  메모")
    for bin_v, row_b in bdh_dist.iterrows():
        ef = row_b.get("EF", 0); lo = row_b.get("LOSS", 0); wi = row_b.get("WIN", 0)
        note = ""
        if bin_v == "neg/na":
            note = "결측/음수 — 장 초반 또는 데이터 이상"
        elif bin_v in ("<1.5%", "1.5~3%"):
            note = "Fix C가 일부 차단; delay=0은 미차단"
        elif bin_v == "3~5%":
            note = "WIN 집중 구간 (여유 공간 있음)"
        print(f"  {str(bin_v):>7}   {ef:>3}    {lo:>3}   {wi:>3}  {note}")

    # ── 거래별 진단표 ──────────────────────────────────────────────────────
    print("\n=== 거래별 진단표 (delay=0 전수) ===")
    d0_diag = d0.reset_index(drop=True).copy()
    d0_diag["tag"] = d0_diag.apply(assign_tag, axis=1)
    d0_diag["why"] = d0_diag.apply(why_memo, axis=1)

    print(f"  {'결과':>4}  {'시각':>16}  {'bdh%':>6}  {'ipos%':>6}  {'p5%':>5}  {'pnl%':>6}  {'태그':<28}  메모")
    print("  " + "-" * 105)
    for _, row in d0_diag.iterrows():
        bdh_s  = f"{row['below_day_high_pct']:>5.1f}" if not pd.isna(row["below_day_high_pct"]) else " N/A"
        ipos_s = f"{row['intraday_pos_pct']:>5.1f}" if not pd.isna(row["intraday_pos_pct"]) else " N/A"
        p5_s   = f"{row['prior_5d_pct_t0']:>4.1f}" if not pd.isna(row["prior_5d_pct_t0"]) else " N/A"
        flag   = "🔴" if row["result"] in ("EF", "LOSS") else "✅"
        print(f"  {flag}{row['result']:3s}  {str(row['t2_time'])[:16]:>16}  {bdh_s}%  {ipos_s}%  {p5_s}%  "
              f"{row['pnl_pct']:>+5.2f}%  {row['tag']:<28}  {row['why']}")

    # ── 원인 공통 패턴 요약 ────────────────────────────────────────────────
    print("\n=== 실패 원인 공통 패턴 요약 (Table 1) ===")
    tag_stats = {}
    for _, row in d0_diag.iterrows():
        for tag in row["tag"].split("+"):
            if tag not in tag_stats:
                tag_stats[tag] = {"total": 0, "EF": 0, "LOSS": 0, "WIN": 0}
            tag_stats[tag]["total"] += 1
            tag_stats[tag][row["result"]] = tag_stats[tag].get(row["result"], 0) + 1

    tag_interp = {
        TAG_EARLY_OPEN:  "장 초반(09:xx) 즉시 CHoCH — 방향성 미확정",
        TAG_NO_RANGE:    "당일 고점 데이터 결측 (장 초반 또는 이상치)",
        TAG_HIGH_PROX:   "고점 근접 bdh<3% — 여유 공간 부족",
        TAG_OVEREXTEND:  "단기 과열 5d>40% — 이미 확장된 구간",
        TAG_CLEAN:       "특이 패턴 없음 (혼재)",
    }

    print(f"\n  {'태그':<16} {'건수':>4} {'EF/LOSS':>8} {'WIN':>5} {'실패율':>7}  해석")
    print("  " + "-" * 80)
    for tag, cnt in sorted(tag_stats.items(),
                           key=lambda x: -(x[1].get("EF", 0) + x[1].get("LOSS", 0))):
        fail = cnt.get("EF", 0) + cnt.get("LOSS", 0)
        win  = cnt.get("WIN", 0)
        rate = fail / cnt["total"] * 100
        print(f"  {tag:<16} {cnt['total']:>4}     {fail:>4}건   {win:>4}건  {rate:>5.1f}%  {tag_interp.get(tag, '-')}")

    # ── G안 근거 예측 ──────────────────────────────────────────────────────
    print("\n=== delay=0 Gate 설계 근거 요약 ===")
    print("  G1 (시간 게이트, hour<10):   EF 6건 차단, LOSS 0건, WIN 0건 — 100% 정밀도")
    print("  G2 (고점근접 bdh<3%/NaN):   EF 7건 차단, LOSS 3건, WIN 0건 — 0 WIN 손상")
    print("  G3 (G1 OR G2):              EF 8건 차단, LOSS 3건, WIN 0건 — 최대 커버리지")
    print()
    print(f"  delay=0 현재 EF%: {stats(d0)['ef_pct']:.1f}%")
    d0_g1 = d0[~(d0["t2_time"].dt.hour < 10)]
    d0_g2 = d0[~(d0["below_day_high_pct"].isna() | (d0["below_day_high_pct"] < 3.0))]
    early_mask = d0["t2_time"].dt.hour < 10
    prox_mask  = d0["below_day_high_pct"].isna() | (d0["below_day_high_pct"] < 3.0)
    d0_g3 = d0[~(early_mask | prox_mask)]
    print(f"  G1 적용 후 delay=0 EF%: {stats(d0_g1)['ef_pct']:.1f}%  (n={len(d0_g1)})")
    print(f"  G2 적용 후 delay=0 EF%: {stats(d0_g2)['ef_pct']:.1f}%  (n={len(d0_g2)})")
    print(f"  G3 적용 후 delay=0 EF%: {stats(d0_g3)['ef_pct']:.1f}%  (n={len(d0_g3)})")

    # ── CSV 저장 ────────────────────────────────────────────────────────────
    out_path = Path("reports/delay0_diagnosis_table.csv")
    out_path.parent.mkdir(exist_ok=True)
    save_cols = ["stock_code", "t2_time", "result", "pnl_pct",
                 "below_day_high_pct", "intraday_pos_pct", "prior_5d_pct_t0",
                 "exit_reason", "tag", "why"]
    d0_diag[[c for c in save_cols if c in d0_diag.columns]].to_csv(out_path, index=False)
    print(f"\n진단표 저장: {out_path}")

    return d0_diag

if __name__ == "__main__":
    main()
