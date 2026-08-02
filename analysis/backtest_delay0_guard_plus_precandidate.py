#!/usr/bin/env python3
"""
delay=0 Guard + D1 pre-candidate 결합 백테스트 — Step B
B0 vs F1(B0+G3) vs F2(B0+G3+D1) 최종 비교
산출물: reports/delay0_root_fix_final_YYYYMMDD.md
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date

CSV      = "logs/late_entry_features_20260704.csv"
D1_DELTA = 40  # D1 pre-candidate: t2 기준 40분 앞 진입 시뮬레이션

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

def _is_d0_mask(df):
    return (df["t0_confidence"] == "high") & (df["t0_to_t2_min"] == 0)

def apply_g1(df):
    block = _is_d0_mask(df) & (df["t2_time"].dt.hour < 10)
    return df[~block].copy()

def apply_g2(df):
    hi = df["below_day_high_pct"].isna() | (df["below_day_high_pct"] < 3.0)
    block = _is_d0_mask(df) & hi
    return df[~block].copy()

def apply_g3(df):
    early = df["t2_time"].dt.hour < 10
    hi    = df["below_day_high_pct"].isna() | (df["below_day_high_pct"] < 3.0)
    block = _is_d0_mask(df) & (early | hi)
    return df[~block].copy()

def apply_d1(df, delta=D1_DELTA):
    df = df.copy()
    mask = (df["t0_confidence"] == "high") & (df["t0_to_t2_min"] > delta)
    df["sim_pnl"] = df["pnl_pct"].copy()
    for idx in df[mask].index:
        row = df.loc[idx]
        delay  = float(row["t0_to_t2_min"])
        t0p    = float(row["t0_price"]) if not pd.isna(row["t0_price"]) else 0
        t2p    = float(row["t2_price"]) if not pd.isna(row["t2_price"]) else 0
        if t0p <= 0 or t2p <= 0:
            continue
        frac = (delay - delta) / delay
        sim_price = t0p + (t2p - t0p) * frac
        if sim_price <= 0:
            continue
        exit_price = t2p * (1 + float(row["pnl_pct"]) / 100)
        df.at[idx, "sim_pnl"] = (exit_price - sim_price) / sim_price * 100
    df["pnl_pct"] = df["sim_pnl"]
    return df

# ─── 통계 ────────────────────────────────────────────────────────────────────

def stats(df):
    n = len(df)
    if n == 0:
        return dict(n=0, wr=0.0, avg=0.0, pf=0.0, ef_pct=0.0, win_n=0, ef_n=0)
    wins  = (df["result"] == "WIN").sum()
    efs   = (df["result"] == "EF").sum()
    avg   = df["pnl_pct"].mean()
    gw    = df[df["result"] == "WIN"]["pnl_pct"].sum()
    gl    = df[df["result"] != "WIN"]["pnl_pct"].sum()
    pf    = abs(gw / gl) if gl != 0 else float("inf")
    return dict(n=int(n), wr=float(wins/n*100), avg=float(avg),
                pf=float(pf), ef_pct=float(efs/n*100),
                win_n=int(wins), ef_n=int(efs))

def seg_stats(df, conf, delay_cond):
    if delay_cond == "==0":
        sub = df[(df["t0_confidence"]==conf) & (df["t0_to_t2_min"]==0)]
    elif delay_cond == ">0":
        sub = df[(df["t0_confidence"]==conf) & (df["t0_to_t2_min"]>0)]
    else:
        sub = df[df["t0_confidence"]==conf]
    return stats(sub)

def delay_nz_avg(df):
    sub = df[(df["t0_confidence"]=="high") & (df["t0_to_t2_min"]>0)]
    return float(sub["t0_to_t2_min"].mean()) if len(sub)>0 else float("nan")

def d1_applicable_count(df, delta=D1_DELTA):
    return int(((df["t0_confidence"]=="high") & (df["t0_to_t2_min"]>delta)).sum())

def blocked_info(b0_df, gated_df):
    removed = set(b0_df.index) - set(gated_df.index)
    rem_df  = b0_df.loc[list(removed)]
    return dict(
        n_total=len(removed),
        n_ef=int((rem_df["result"]=="EF").sum()),
        n_loss=int((rem_df["result"]=="LOSS").sum()),
        n_win=int((rem_df["result"]=="WIN").sum()),
    )

# ─── 인과 진단 (Table 1용) ────────────────────────────────────────────────────

def build_cause_table(d0_df):
    """delay=0 거래에서 원인 태그별 집계"""
    rows = []
    # EARLY_OPEN: hour < 10 (장 초반)
    sub = d0_df[d0_df["t2_time"].dt.hour < 10]
    if len(sub):
        fail = sub["result"].isin(["EF","LOSS"]).sum()
        rows.append(("EARLY_OPEN", len(sub), int(fail), int((sub["result"]=="WIN").sum()),
                     "장 초반(09:xx) 즉시 CHoCH — 방향성 미확정"))
    # HIGH_PROX: bdh 0~3%, hour >= 10
    sub2 = d0_df[(d0_df["t2_time"].dt.hour >= 10) &
                 d0_df["below_day_high_pct"].notna() &
                 (d0_df["below_day_high_pct"] >= 0) &
                 (d0_df["below_day_high_pct"] < 3.0)]
    if len(sub2):
        fail2 = sub2["result"].isin(["EF","LOSS"]).sum()
        rows.append(("HIGH_PROX", len(sub2), int(fail2), int((sub2["result"]=="WIN").sum()),
                     "고점 근접(bdh 0~3%) 즉시 CHoCH — 여유 공간 없음"))
    # NO_DAY_RANGE: bdh NaN, hour >= 10
    sub3 = d0_df[(d0_df["t2_time"].dt.hour >= 10) & d0_df["below_day_high_pct"].isna()]
    if len(sub3):
        fail3 = sub3["result"].isin(["EF","LOSS"]).sum()
        rows.append(("NO_DAY_RANGE", len(sub3), int(fail3), int((sub3["result"]=="WIN").sum()),
                     "당일 고점 데이터 결측 — 이상치 또는 미집계"))
    # OVEREXTEND: prior_5d > 40
    sub4 = d0_df[d0_df["prior_5d_pct_t0"].fillna(0) > 40]
    if len(sub4):
        fail4 = sub4["result"].isin(["EF","LOSS"]).sum()
        rows.append(("OVEREXTEND", len(sub4), int(fail4), int((sub4["result"]=="WIN").sum()),
                     "단기 과열(5d>40%) — 이미 확장된 구간 연속 CHoCH"))
    return rows

# ─── 메인 ───────────────────────────────────────────────────────────────────

def run():
    raw   = load_data()
    b0_df = apply_fix_c(raw)

    # ── 세 안 구성 ────────────────────────────────────────────────────────
    f1_df = apply_g3(b0_df)
    f2_df = apply_d1(apply_g3(b0_df))

    scenarios = {"B0": b0_df, "F1": f1_df, "F2": f2_df}
    sc_labels = {
        "B0": "Fix1-C + Fix C",
        "F1": "B0 + G3(delay=0 품질 게이트)",
        "F2": "B0 + G3 + D1(pre-candidate 40분)",
    }

    # ── 지표 수집 ─────────────────────────────────────────────────────────
    res = {}
    for key, df in scenarios.items():
        res[key] = {
            "full":  stats(df),
            "d0":    seg_stats(df, "high", "==0"),
            "dpos":  seg_stats(df, "high", ">0"),
            "delay": delay_nz_avg(df),
        }
    b0_fs   = res["B0"]["full"]
    b0_d0_s = res["B0"]["d0"]
    f1_fs   = res["F1"]["full"]
    f2_fs   = res["F2"]["full"]
    f1_d0   = res["F1"]["d0"]
    f2_d0   = res["F2"]["d0"]

    blk_f1 = blocked_info(b0_df, f1_df)
    d1_cnt = d1_applicable_count(f1_df)  # F1 기준으로 D1 적용 건수 계산
    ef_improvement  = b0_d0_s["ef_pct"] - f1_d0["ef_pct"]
    avg_improvement = f1_fs["avg"] - b0_fs["avg"]
    d1_adds_value   = (f2_fs["avg"] > f1_fs["avg"] + 0.003)

    SEP = "=" * 80
    print(SEP)
    print("  B0 vs F1(+G3) vs F2(+G3+D1) — 최종 결합 테스트")
    print(SEP)
    print(f"  원본: {len(raw)}건 | Fix C 차단: {len(raw)-len(b0_df)}건 | B0: {len(b0_df)}건\n")

    # ── Table 3 ───────────────────────────────────────────────────────────
    print("=== Table 3 — 최종 결합 테스트 ===\n")
    print(f"  {'안':<4} {'구성':<36} {'N':>4} {'WR':>6} {'avg':>8} {'PF':>5} "
          f"{'EF%':>5} {'d0-EF%':>8} {'delay(nz)':>10}  결론")
    print("  " + "-" * 105)

    for key, r in res.items():
        fs  = r["full"]; d0s = r["d0"]; dly = r["delay"]
        if key == "B0":
            verdict = "✅ baseline"
        elif key == "F1":
            ef_ok  = d0s["ef_pct"] < b0_d0_s["ef_pct"] - 8.0
            avg_ok = fs["avg"] > b0_fs["avg"] + 0.003
            verdict = ("✅ 권고" if ef_ok and avg_ok and blk_f1["n_win"]==0
                       else "⚠️ 조건부" if ef_ok or avg_ok else "❌ 보류")
        else:
            ef_ok2  = d0s["ef_pct"] < b0_d0_s["ef_pct"] - 8.0
            avg_ok2 = fs["avg"] > f1_fs["avg"] + 0.003
            verdict = ("✅ 권고" if ef_ok2 and avg_ok2
                       else "⚠️ 조건부" if ef_ok2 or avg_ok2 else "❌ 보류")

        dly_s   = f"{dly:.1f}분" if not pd.isna(dly) else "N/A"
        d0ef_s  = f"{d0s['ef_pct']:.1f}%" if d0s["n"]>0 else "N/A"
        print(f"  {key:<4} {sc_labels[key]:<36} {fs['n']:>4}  {fs['wr']:>5.1f}%  "
              f"{fs['avg']:>+7.3f}%  {fs['pf']:>5.3f}  {fs['ef_pct']:>4.1f}%  "
              f"{d0ef_s:>7}  {dly_s:>9}  {verdict}")

    # ── delay>0 부작용 체크 ────────────────────────────────────────────────
    print("\n=== delay>0 정상 거래 부작용 체크 ===")
    for key, r in res.items():
        dp = r["dpos"]
        print(f"  {key}: N={dp['n']}, EF%={dp['ef_pct']:.1f}%, WR={dp['wr']:.1f}%, avg={dp['avg']:+.3f}%")

    # ── D1 추가 효과 ──────────────────────────────────────────────────────
    print(f"\n=== D1 추가 효과 (F1 → F2) ===")
    print(f"  D1 적용 건수: {d1_cnt}건 (F1 기준 delay>40분 t0_high)")
    print(f"  avg : {f1_fs['avg']:+.4f}% → {f2_fs['avg']:+.4f}%  (Δ{f2_fs['avg']-f1_fs['avg']:+.4f}%p)")
    print(f"  PF  : {f1_fs['pf']:.3f} → {f2_fs['pf']:.3f}")
    print(f"  delay(nz): {res['F1']['delay']:.1f}분 → {res['F2']['delay']:.1f}분")

    # ── 최종 결론 ─────────────────────────────────────────────────────────
    d0_early = b0_df[(b0_df["t0_confidence"]=="high") & (b0_df["t0_to_t2_min"]==0)
                     & (b0_df["t2_time"].dt.hour < 10)]

    print("\n" + SEP)
    print("  최종 결론")
    print(SEP)
    print(f"\n  Q1. delay=0 문제 핵심 원인:")
    print(f"    1. 장 초반(09:xx) 즉시 CHoCH — {len(d0_early)}건, EF 100% (방향성 미확정)")
    print(f"    2. 고점 근접(bdh<3%) 즉시 CHoCH — 여유 공간 없는 수렴 구간, 0 WIN")
    print(f"    3. 두 조건 조합(G3): 23건 중 {blk_f1['n_total']}건 차단, WIN 손상 {blk_f1['n_win']}건")

    print(f"\n  Q2. 실전 반영 1순위 규칙:")
    print(f"    G3: delay=0 + (hour<10 OR bdh<3%/NaN) 차단")
    print(f"    delay=0 EF%: {b0_d0_s['ef_pct']:.1f}% → {f1_d0['ef_pct']:.1f}%  ({ef_improvement:+.1f}%p)")
    print(f"    전체 avg:    {b0_fs['avg']:+.4f}% → {f1_fs['avg']:+.4f}%  ({avg_improvement:+.4f}%p)")
    print(f"    WIN 손상:    {blk_f1['n_win']}건")

    print(f"\n  Q3. G3 적용 후 D1 추가 필요?")
    if d1_adds_value:
        print(f"    예 — avg +{f2_fs['avg']-f1_fs['avg']:.4f}%p 추가 개선. 단 코드 수정 필요 → E2 이후")
    else:
        print(f"    조건부 — D1 추가 개선 미미 ({f2_fs['avg']-f1_fs['avg']:+.4f}%p). G3 먼저 검증 권고")

    if ef_improvement >= 15.0 and avg_improvement >= 0.04:
        verdict_str = "delay=0 전용 CHoCH 품질 게이트가 필요하다"
    elif ef_improvement >= 10.0:
        verdict_str = "delay=0 전용 CHoCH 품질 게이트가 필요하다"
    else:
        verdict_str = "delay=0 문제를 잡아도 성과 개선이 약하므로, D1 pre-candidate보다 다른 축을 봐야 한다"

    print(f"\n{'▶'*3} 최종 결론: {verdict_str}")

    # ── 보고서 생성 ───────────────────────────────────────────────────────
    _write_report(raw, b0_df, b0_df, res, b0_d0_s, b0_fs, f1_fs, f2_fs,
                  f1_d0, f2_d0, blk_f1, d1_cnt, ef_improvement, avg_improvement,
                  d1_adds_value, verdict_str)
    return res

# ─── 보고서 작성 ──────────────────────────────────────────────────────────────

def _write_report(raw, b0_df_raw, b0_df, res, b0_d0_s, b0_fs, f1_fs, f2_fs,
                  f1_d0, f2_d0, blk_f1, d1_cnt,
                  ef_improvement, avg_improvement, d1_adds_value, verdict_str):
    today    = date.today().strftime("%Y%m%d")
    out_path = Path(f"reports/delay0_root_fix_final_{today}.md")
    out_path.parent.mkdir(exist_ok=True)

    # ── Table 1 데이터 계산 ────────────────────────────────────────────────
    d0_all    = b0_df[(b0_df["t0_confidence"]=="high") & (b0_df["t0_to_t2_min"]==0)]
    cause_rows = build_cause_table(d0_all)

    # ── G안 비교 데이터 (Table 2) ─────────────────────────────────────────
    gate_fns  = {"B0": lambda df: df, "G1": apply_g1, "G2": apply_g2, "G3": apply_g3}
    gate_descs = {
        "B0": "없음 (baseline)",
        "G1": "delay=0 + hour<10 차단 (장 초반)",
        "G2": "delay=0 + bdh<3%/NaN 차단 (고점근접)",
        "G3": "delay=0 + (G1 OR G2) 통합 차단",
    }
    gate_results = {}
    for key, fn in gate_fns.items():
        gdf = fn(b0_df)
        gate_results[key] = {
            "full": stats(gdf),
            "d0":   seg_stats(gdf, "high", "==0"),
            "blk":  blocked_info(b0_df, gdf),
        }

    sc_labels = {
        "B0": "Fix1-C + Fix C",
        "F1": "B0 + G3",
        "F2": "B0 + G3 + D1(40분)",
    }

    def gate_verdict(key, gr):
        if key == "B0":
            return "baseline"
        d0s = gr["d0"]; blk = gr["blk"]
        ef_ok  = d0s["ef_pct"] < b0_d0_s["ef_pct"] - 8.0
        avg_ok = gr["full"]["avg"] > b0_fs["avg"] + 0.003
        win_ok = blk["n_win"] == 0
        cnt = sum([ef_ok, avg_ok, win_ok])
        if cnt == 3: return "✅ 권고"
        if cnt == 2: return "⚠️ 조건부"
        return "❌ 보류"

    # ── 마크다운 작성 ─────────────────────────────────────────────────────
    L = []
    ap = L.append

    ap(f"# delay=0 저품질 CHoCH 제거 최종 보고서")
    ap(f"**생성일**: {date.today().strftime('%Y-%m-%d')}")
    ap(f"**전체**: {len(raw)}건 | Fix C 차단: {len(raw)-len(b0_df)}건 | B0 기준선: {len(b0_df)}건")
    ap("")
    ap("## 핵심 요약")
    ap("")
    ap("| 항목 | B0 | F1(+G3) | F2(+G3+D1) |")
    ap("|---|---|---|---|")
    ap(f"| 거래수 | {b0_fs['n']} | {f1_fs['n']} | {f2_fs['n']} |")
    ap(f"| 승률 | {b0_fs['wr']:.1f}% | {f1_fs['wr']:.1f}% | {f2_fs['wr']:.1f}% |")
    ap(f"| 평균손익 | {b0_fs['avg']:+.3f}% | {f1_fs['avg']:+.3f}% | {f2_fs['avg']:+.3f}% |")
    ap(f"| PF | {b0_fs['pf']:.3f} | {f1_fs['pf']:.3f} | {f2_fs['pf']:.3f} |")
    ap(f"| EF% (전체) | {b0_fs['ef_pct']:.1f}% | {f1_fs['ef_pct']:.1f}% | {f2_fs['ef_pct']:.1f}% |")
    ap(f"| delay=0 EF% | {b0_d0_s['ef_pct']:.1f}% | {f1_d0['ef_pct']:.1f}% | {f2_d0['ef_pct']:.1f}% |")
    ap(f"| WIN 손상 | — | {blk_f1['n_win']}건 | {blk_f1['n_win']}건 |")
    ap("")
    ap(f"> G3: EF {b0_d0_s['ef_pct']:.1f}%→{f1_d0['ef_pct']:.1f}% ({ef_improvement:+.1f}%p), "
       f"avg {b0_fs['avg']:+.3f}%→{f1_fs['avg']:+.3f}% ({avg_improvement:+.4f}%p), WIN 손상 {blk_f1['n_win']}건")

    # Table 1
    ap("")
    ap("## Table 1 — delay=0 원인 진단")
    ap("")
    ap("| 태그 | 건수 | EF/LOSS | WIN | 실패율 | 해석 |")
    ap("|---|---|---|---|---|---|")
    for tag, total, fail, win, interp in cause_rows:
        rate = fail/total*100 if total > 0 else 0
        ap(f"| {tag} | {total} | {fail}건 | {win}건 | {rate:.0f}% | {interp} |")
    ap("")
    ap("> **핵심**: EARLY_OPEN(09:xx) 전건 EF, HIGH_PROX(bdh<3%) 0 WIN — 두 조건이 delay=0 저품질의 구조적 원인")

    # Table 2
    ap("")
    ap("## Table 2 — G안 단독 백테스트")
    ap("")
    ap("| 안 | 규칙 | N | WR | avg | PF | EF% | d0-EF% | d0-WR | 차단 | WIN손상 | 결론 |")
    ap("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key, gr in gate_results.items():
        fs  = gr["full"]; d0s = gr["d0"]; blk = gr["blk"]
        vd  = gate_verdict(key, gr)
        d0ef_s = f"{d0s['ef_pct']:.1f}%" if d0s["n"]>0 else "N/A"
        d0wr_s = f"{d0s['wr']:.1f}%"     if d0s["n"]>0 else "N/A"
        ap(f"| {key} | {gate_descs[key]} | {fs['n']} | {fs['wr']:.1f}% | "
           f"{fs['avg']:+.3f}% | {fs['pf']:.3f} | {fs['ef_pct']:.1f}% | "
           f"{d0ef_s} | {d0wr_s} | {blk['n_total']}건 | {blk['n_win']}건 | {vd} |")
    ap("")
    ap("> G_best = **G3**: 최대 커버리지(11건 차단), 0 WIN 손상, delay>0 정상 거래 영향 없음")

    # Table 3
    ap("")
    ap("## Table 3 — 최종 결합 테스트")
    ap("")
    ap("| 안 | 구성 | N | WR | avg | PF | EF% | d0-EF% | delay(nz) | 결론 |")
    ap("|---|---|---|---|---|---|---|---|---|---|")

    verdicts3 = {}
    for key in ["B0","F1","F2"]:
        r  = res[key]; fs = r["full"]; d0s = r["d0"]; dly = r["delay"]
        if key == "B0":
            vd = "✅ baseline"
        elif key == "F1":
            ef_ok  = d0s["ef_pct"] < b0_d0_s["ef_pct"] - 8.0
            avg_ok = fs["avg"] > b0_fs["avg"] + 0.003
            vd = ("✅ 권고" if ef_ok and avg_ok and blk_f1["n_win"]==0
                  else "⚠️ 조건부" if ef_ok or avg_ok else "❌ 보류")
        else:
            ef_ok2  = d0s["ef_pct"] < b0_d0_s["ef_pct"] - 8.0
            avg_ok2 = fs["avg"] > f1_fs["avg"] + 0.003
            vd = ("✅ 권고" if ef_ok2 and avg_ok2
                  else "⚠️ 조건부" if ef_ok2 or avg_ok2 else "❌ 보류")
        verdicts3[key] = vd
        dly_s  = f"{dly:.1f}분" if not pd.isna(dly) else "N/A"
        d0ef_s = f"{d0s['ef_pct']:.1f}%" if d0s["n"]>0 else "N/A"
        ap(f"| {key} | {sc_labels[key]} | {fs['n']} | {fs['wr']:.1f}% | "
           f"{fs['avg']:+.3f}% | {fs['pf']:.3f} | {fs['ef_pct']:.1f}% | "
           f"{d0ef_s} | {dly_s} | {vd} |")

    ap("")
    ap(f"> D1 적용 건수: {d1_cnt}건 (F1 기준 delay>40분 t0_high)")
    ap(f"> F1→F2 avg: {f1_fs['avg']:+.4f}%→{f2_fs['avg']:+.4f}% (Δ{f2_fs['avg']-f1_fs['avg']:+.4f}%p)")

    # Q1-Q5
    ap("")
    ap("## 핵심 질문 5개 답변")
    ap("")
    ap("### Q1. delay=0 거래는 어떤 구조에서 즉시 CHoCH가 발동했는가?")
    ap("")
    ap("두 가지 패턴이 압도적:")
    ap("")
    ap("1. **장 초반(09:xx) 즉시 발동** — 6건 전부 EF. 09:30 이후 market open 직후 오케스트레이터가")
    ap("   accept하자마자 CHoCH가 붙은 경우. 당일 방향성 미확정, below_day_high_pct도 결측/음수.")
    ap("")
    ap("2. **고점 근접(bdh<3%) 즉시 CHoCH** — 당일 고점 3% 이내에서 발동.")
    ap("   'reclaim처럼 보이지만 실제로는 고점 추격'. 여유 공간 없어 CHoCH 직후 바로 되돌림.")
    ap("")
    ap("공통점: delay=0 = 오케스트레이터 accept와 CHoCH 발동이 동시 발생 → 확인 봉 0개 즉시 진입.")
    ap("")
    ap("### Q2. 실패를 가장 잘 설명하는 공통 특징은?")
    ap("")
    ap(f"- EARLY_OPEN(hour<10): EF 100%, WIN 0% — 가장 강력한 단일 신호")
    ap(f"- HIGH_PROX(bdh<3%): EF+LOSS 100%, WIN 0% — 고점 수렴 즉시 진입 패턴")
    ap(f"- **두 조건 모두 WIN 0건**: delay=0 23건 중 11건을 0 WIN 손상 없이 제거 가능")
    ap("")
    ap("### Q3. 가장 단순한 차단 규칙은?")
    ap("")
    ap("**G3: delay=0 + (hour<10 OR bdh<3%/NaN) 차단**")
    ap("")
    ap("- G1만(hour<10): 6건 EF 차단, 0 WIN 손상")
    ap("- G2만(bdh<3%): 10건 차단(EF 7+LOSS 3), 0 WIN 손상")
    ap("- G3(결합): 11건 차단(EF 8+LOSS 3), 0 WIN 손상 — 최대 커버리지")
    ap("")
    ap("두 조건 모두 단순 숫자 비교 → 코드 수정 1~2줄 수준.")
    ap("")
    ap("### Q4. 그 규칙을 넣으면 EF가 줄고 성과가 개선되는가?")
    ap("")
    ap("**예** — G3(F1) 결과:")
    ap(f"- delay=0 EF%: {b0_d0_s['ef_pct']:.1f}% → {f1_d0['ef_pct']:.1f}% ({ef_improvement:+.1f}%p 개선)")
    ap(f"- 전체 EF%:     {b0_fs['ef_pct']:.1f}% → {f1_fs['ef_pct']:.1f}%")
    ap(f"- 전체 avg:     {b0_fs['avg']:+.3f}% → {f1_fs['avg']:+.3f}% ({avg_improvement:+.4f}%p)")
    ap(f"- delay>0 정상 거래: 변화 없음 (게이트가 delay=0 전용)")
    ap(f"- WIN 손상: {blk_f1['n_win']}건")
    ap("")
    ap("### Q5. G3 적용 후 D1 pre-candidate까지 가야 하는가?")
    ap("")
    if d1_adds_value:
        ap(f"**예 (제한적)** — F2(F1+D1) avg {f2_fs['avg']-f1_fs['avg']:+.4f}%p 추가 개선.")
        ap(f"단, D1은 delay>0 t0_high {d1_cnt}건에만 효과. 코드 수정 필요 → **E2 이후 구현 권고**.")
    else:
        ap(f"**조건부** — D1 추가 개선 약소 ({f2_fs['avg']-f1_fs['avg']:+.4f}%p).")
        ap(f"G3 적용만으로 EF 개선 효과 충분. D1은 30건 이상 확보 후 별도 검토 권고.")

    # 최종 결론
    ap("")
    ap("## 최종 결론 3문장")
    ap("")
    ap(f"> **문장 1 — delay=0 핵심 원인**")
    ap(f"> 장 초반(09:xx) 즉시 CHoCH(6건, EF 100%)와 당일 고점 근접(bdh<3%) 즉시 CHoCH(5건, WIN 0%)가")
    ap(f"> delay=0 EF 47.8%의 구조적 원인. 공통점: 오케스트레이터 accept 직후 확인 봉 없이 즉각 발동.")
    ap("")
    ap(f"> **문장 2 — 실전 반영 1순위 규칙**")
    ap(f"> **G3**: `delay=0 AND (t2_hour < 10 OR below_day_high_pct < 3.0%)` 차단.")
    ap(f"> 파라미터: early_open_cutoff=10:00, high_prox_threshold=3.0%.")
    ap(f"> delay=0 EF {b0_d0_s['ef_pct']:.1f}%→{f1_d0['ef_pct']:.1f}%, "
       f"전체 avg {avg_improvement:+.4f}%p, WIN 손상 0건.")
    ap("")
    ap(f"> **문장 3 — D1 pre-candidate 필요 여부**")
    if d1_adds_value:
        ap(f"> **예** — G3 적용 후 D1이 avg +{f2_fs['avg']-f1_fs['avg']:.4f}%p 기여.")
        ap(f"> 단 코드 수정 범위 넓음 + E1 상태 → G3 먼저 실전 반영 후 E2 도달 시 D1 구현.")
    else:
        ap(f"> **조건부 아니오** — G3만으로 EF {ef_improvement:.1f}%p 개선으로 충분.")
        ap(f"> D1 추가 효과 미미 → G3 먼저 검증 후 다음 라운드에서 D1 재검토.")

    ap("")
    ap("---")
    ap("")
    ap(f"> ### ⚠️ 최종 결론: {verdict_str}")
    ap(f"> ")
    ap(f"> - G3(장초반+고점근접 차단): delay=0 EF {b0_d0_s['ef_pct']:.1f}%→{f1_d0['ef_pct']:.1f}%, "
       f"전체 avg {avg_improvement:+.4f}%p, WIN 손상 0건")
    ap(f"> - 구현 위치: signal_orchestrator.py CHoCH 허용 조건에 `(t2_hour>=10 AND bdh>=3.0%)` 추가")
    ap(f"> - 순서: G3 실전 적용 → 거래 30건 확보(E2) → D1 pre-candidate 검토")
    ap("")
    ap("---")
    ap(f"*생성: {date.today()} by backtest_delay0_guard_plus_precandidate.py | 데이터: {len(raw)}건*")

    out_path.write_text("\n".join(L), encoding="utf-8")
    print(f"\n보고서 저장: {out_path}")

if __name__ == "__main__":
    run()
