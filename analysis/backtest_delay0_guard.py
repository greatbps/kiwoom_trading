#!/usr/bin/env python3
"""
delay=0 저품질 CHoCH 차단 규칙 백테스트 — Step A
G1 / G2 / G3 단독 테스트 (B0 기준선)
"""

import pandas as pd
import numpy as np

CSV = "logs/late_entry_features_20260704.csv"

# ─── 공통 로직 ───────────────────────────────────────────────────────────────

def load_data():
    df = pd.read_csv(CSV, parse_dates=["t0_time", "t2_time"])
    return df[df["pnl_pct"].notna()].copy()

def apply_fix_c(df):
    def blocked(row):
        t = row["t2_time"]
        if (t.hour, t.minute) < (10, 30):
            return False
        bdh = row.get("below_day_high_pct")
        return not pd.isna(bdh) and float(bdh) >= 0 and float(bdh) < 1.5
    return df[~df.apply(blocked, axis=1)].copy()

def is_d0(df):
    return (df["t0_confidence"] == "high") & (df["t0_to_t2_min"] == 0)

def is_dpos(df):
    return (df["t0_confidence"] == "high") & (df["t0_to_t2_min"] > 0)

# ─── Gate 정의 ────────────────────────────────────────────────────────────────

GATES = {
    "B0": {
        "desc": "Fix1-C + Fix C (baseline)",
        "rule": "없음",
        "fn": lambda df: df,  # Fix C already applied
    },
    "G1": {
        "desc": "delay=0 + hour<10 차단",
        "rule": "t0_high delay=0 거래 중 09:xx 진입 차단 (장 초반 방향성 미확정)",
        "fn": lambda df: df[~(is_d0(df) & (df["t2_time"].dt.hour < 10))].copy(),
    },
    "G2": {
        "desc": "delay=0 + bdh<3%/NaN 차단",
        "rule": "t0_high delay=0 거래 중 당일 고점 3% 이내 또는 데이터 결측 차단",
        "fn": lambda df: df[
            ~(is_d0(df) & (df["below_day_high_pct"].isna() | (df["below_day_high_pct"] < 3.0)))
        ].copy(),
    },
    "G3": {
        "desc": "delay=0 + (hour<10 OR bdh<3%/NaN) 차단",
        "rule": "G1 OR G2 — 장 초반 + 고점 근접 통합 차단",
        "fn": lambda df: df[
            ~(is_d0(df) & (
                (df["t2_time"].dt.hour < 10) |
                df["below_day_high_pct"].isna() |
                (df["below_day_high_pct"] < 3.0)
            ))
        ].copy(),
    },
}

# ─── 통계 함수 ───────────────────────────────────────────────────────────────

def stats(df, label=""):
    n = len(df)
    if n == 0:
        return dict(n=0, wr=0.0, avg=0.0, pf=0.0, ef_pct=0.0,
                    win_n=0, ef_n=0, loss_n=0)
    wins  = (df["result"] == "WIN").sum()
    efs   = (df["result"] == "EF").sum()
    losses = (df["result"] == "LOSS").sum()
    avg   = df["pnl_pct"].mean()
    gw    = df[df["result"] == "WIN"]["pnl_pct"].sum()
    gl    = df[df["result"] != "WIN"]["pnl_pct"].sum()
    pf    = abs(gw / gl) if gl != 0 else float("inf")
    return dict(n=int(n), wr=float(wins/n*100), avg=float(avg),
                pf=float(pf), ef_pct=float(efs/n*100),
                win_n=int(wins), ef_n=int(efs), loss_n=int(losses))

def delay_nz_avg(df):
    """delay>0 t0_high 거래의 평균 delay (분)"""
    sub = df[(df["t0_confidence"] == "high") & (df["t0_to_t2_min"] > 0)]
    return sub["t0_to_t2_min"].mean() if len(sub) > 0 else float("nan")

# ─── 차단 분석: WIN 손상 추적 ─────────────────────────────────────────────────

def blocked_analysis(b0_df, gated_df, gate_name):
    """B0 대비 게이트 적용 후 차단된 거래 분석"""
    b0_ids   = set(b0_df.index)
    gate_ids = set(gated_df.index)
    removed  = b0_ids - gate_ids
    removed_df = b0_df.loc[list(removed)]
    n_removed = len(removed_df)
    n_d0  = ((b0_df["t0_confidence"] == "high") & (b0_df["t0_to_t2_min"] == 0)).sum()
    n_win_lost  = (removed_df["result"] == "WIN").sum()
    n_ef_removed = (removed_df["result"] == "EF").sum()
    n_loss_removed = (removed_df["result"] == "LOSS").sum()
    return dict(
        n_removed=n_removed,
        n_ef_removed=int(n_ef_removed),
        n_loss_removed=int(n_loss_removed),
        n_win_lost=int(n_win_lost),
        n_d0_before=int(n_d0),
    )

# ─── 메인 ───────────────────────────────────────────────────────────────────

def run_backtest(return_results=False):
    raw = load_data()
    b0_df = apply_fix_c(raw)

    results = {}
    SEP = "=" * 80

    print(SEP)
    print("  delay=0 Guard 백테스트 — Step A (G1 / G2 / G3)")
    print(SEP)
    print(f"  원본: {len(raw)}건 | Fix C 차단: {len(raw)-len(b0_df)}건 | B0 기준선: {len(b0_df)}건\n")

    # ── 각 안 계산 ─────────────────────────────────────────────────────────
    for key, gate in GATES.items():
        df_g  = gate["fn"](b0_df)
        d0_sub = df_g[(df_g["t0_confidence"] == "high") & (df_g["t0_to_t2_min"] == 0)]
        dpos_sub = df_g[(df_g["t0_confidence"] == "high") & (df_g["t0_to_t2_min"] > 0)]
        blk = blocked_analysis(b0_df, df_g, key)

        results[key] = {
            "full":  stats(df_g),
            "d0":    stats(d0_sub),
            "dpos":  stats(dpos_sub),
            "block": blk,
            "delay_nz": delay_nz_avg(df_g),
        }

    # ── Table 2: G안 단독 백테스트 ────────────────────────────────────────
    print("=== Table 2 — G안 단독 백테스트 ===\n")
    hdr = (f"  {'안':<4} {'N':>4} {'WR':>6} {'avg':>8} {'PF':>5} {'EF%':>5} "
           f"{'d0-EF%':>8} {'d0-WR':>7} {'d0-avg':>8} {'d0N':>4} "
           f"{'차단':>4} {'WIN손상':>7}  결론")
    print(hdr)
    print("  " + "-" * 100)

    verdicts = {}
    b0_full = results["B0"]["full"]
    b0_d0   = results["B0"]["d0"]

    for key, res in results.items():
        fs   = res["full"]
        d0s  = res["d0"]
        blk  = res["block"]

        # 판정
        if key == "B0":
            verdict = "✅ baseline"
        else:
            ef_ok  = d0s["ef_pct"]  < b0_d0["ef_pct"] - 5.0     # d0 EF 5%p 이상 개선
            avg_ok = fs["avg"]      > b0_full["avg"] + 0.003      # 전체 avg 0.3bp+ 개선
            win_ok = blk["n_win_lost"] == 0                        # WIN 손상 없음
            pf_ok  = fs["pf"]       >= b0_full["pf"] - 0.01       # PF 악화 없음

            cnt = sum([ef_ok, avg_ok, win_ok, pf_ok])
            if cnt >= 4:
                verdict = "✅ 권고"
            elif cnt >= 3:
                verdict = "⚠️ 조건부"
            else:
                verdict = "❌ 보류"

        verdicts[key] = verdict

        d0_ef_str  = f"{d0s['ef_pct']:>5.1f}%" if d0s["n"] > 0 else "  N/A"
        d0_wr_str  = f"{d0s['wr']:>5.1f}%"     if d0s["n"] > 0 else "  N/A"
        d0_avg_str = f"{d0s['avg']:>+6.3f}%"   if d0s["n"] > 0 else "    N/A"
        d0_n_str   = f"{d0s['n']:>3}"           if d0s["n"] > 0 else "  0"

        win_flag = "⚠️" if blk["n_win_lost"] > 0 else ""
        print(f"  {key:<4} {fs['n']:>4}  {fs['wr']:>5.1f}%  {fs['avg']:>+7.3f}%  "
              f"{fs['pf']:>5.3f}  {fs['ef_pct']:>4.1f}%  "
              f"{d0_ef_str}  {d0_wr_str}  {d0_avg_str}  {d0_n_str}  "
              f"{blk['n_removed']:>4}건  "
              f"{blk['n_win_lost']:>3}건{win_flag}  {verdict}")

    # ── 차단 내역 상세 ────────────────────────────────────────────────────
    print("\n=== 차단 내역 상세 ===")
    print(f"  {'안':<4} {'차단총':>6} {'EF차단':>7} {'LOSS차단':>9} {'WIN차단':>8}  메모")
    print("  " + "-" * 55)
    for key, res in results.items():
        blk = res["block"]
        if key == "B0":
            print(f"  {key:<4}     -       -          -          -   baseline")
            continue
        print(f"  {key:<4}    {blk['n_removed']:>4}건   {blk['n_ef_removed']:>4}건   "
              f"  {blk['n_loss_removed']:>4}건      {blk['n_win_lost']:>4}건   "
              f"{'WIN 손상 없음' if blk['n_win_lost']==0 else '⚠️ WIN 손상'}")

    # ── delay>0 정상 거래 부작용 체크 ─────────────────────────────────────
    print("\n=== delay>0 정상 거래 부작용 체크 ===")
    print(f"  {'안':<4} {'delay>0 N':>10} {'delay>0 EF%':>12} {'delay>0 WR':>11} {'delay>0 avg':>12}")
    print("  " + "-" * 55)
    for key, res in results.items():
        dp = res["dpos"]
        print(f"  {key:<4}       {dp['n']:>4}건       {dp['ef_pct']:>5.1f}%      "
              f"{dp['wr']:>5.1f}%     {dp['avg']:>+6.3f}%")

    # ── G_best 선정 ───────────────────────────────────────────────────────
    print("\n=== G_best 선정 ===")
    best = None
    for key in ["G3", "G2", "G1"]:
        if verdicts.get(key, "") == "✅ 권고":
            best = key
            break
    if best is None:
        best = max([k for k in results if k != "B0"],
                   key=lambda k: (
                       results["B0"]["d0"]["ef_pct"] - results[k]["d0"]["ef_pct"],
                       -results[k]["block"]["n_win_lost"]
                   ))

    print(f"  G_best = {best}  ({GATES[best]['rule']})")
    print(f"  선정 근거:")
    bs = results[best]
    print(f"    delay=0 EF%: {b0_d0['ef_pct']:.1f}% → {bs['d0']['ef_pct']:.1f}%  "
          f"({b0_d0['ef_pct'] - bs['d0']['ef_pct']:+.1f}%p 개선)")
    print(f"    전체 avg:    {b0_full['avg']:+.3f}% → {bs['full']['avg']:+.3f}%  "
          f"({bs['full']['avg'] - b0_full['avg']:+.4f}%p)")
    print(f"    WIN 손상:    {bs['block']['n_win_lost']}건")

    if return_results:
        return results, best, b0_df

    return None

if __name__ == "__main__":
    run_backtest()
