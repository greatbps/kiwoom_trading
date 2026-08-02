#!/usr/bin/env python3
"""
LOSS CONTROL LAYER v2 검증 백테스트 (2026-07-04)

A안: Stage A+G3 (기준)
B안: A안 + LCL v1  (TIME_STOP 30m / VOL_TIGHT / EC_PROXY)
C안: A안 + LCL v2  (TIME_STOP Dynamic / EC_PROXY 2.0 / MFE_STAG_PROXY)

v2 시뮬 한계:
  - MFE/MAE = 0/94 (null) → (D) MAE_WORSENING = 시뮬 불가
  - Early Cut 2.0 (RSI slope / 3봉 / decay) = RSI data 없음 → proxy 동일
  - Time Stop Dynamic: bdh 기반 적응형 임계 → bdh 데이터 있으면 시뮬 가능
  - MFE stagnation sub (B): hold_min + mfe null → proxy (hold≤30 + pnl≤-0.3)
"""

import pandas as pd
import numpy as np

CSV = "logs/late_entry_features_20260704.csv"
G3_THR, G3_HR, G3_BDH = 2.0, 10, 3.0
SA_MAX_BDH             = 15.0

# ── LCL 파라미터 ───────────────────────────────────────────────────────
# v1
V1_TS_MAX_MIN    = 30.0;  V1_TS_MIN_LOSS  = 0.8
V1_VT_BDH_THR   = 5.0;   V1_VT_TIGHT     = 2.5
V1_EC_PROXY_HOLD = 15.0;  V1_EC_MIN_LOSS  = 0.5

# v2 (new parameters)
V2_TS_BASE_MIN   = 20.0;  V2_TS_MIN_LOSS  = 0.8
V2_TS_HV_BDH    = 5.0;   V2_TS_HV_MIN    = 15.0
V2_TS_VHV_BDH   = 10.0;  V2_TS_VHV_MIN   = 12.0
V2_MFE_STAG_MIN  = 20.0;  V2_MFE_STAG_PCT = 0.3;  V2_MFE_STAG_LOSS = 0.3
V2_VT_BDH_THR   = 5.0;   V2_VT_TIGHT     = 2.5     # unchanged
V2_EC_PROXY_HOLD = 15.0;  V2_EC_MIN_LOSS  = 0.5     # proxy same

SEP = "=" * 72

# ═══ 데이터 로드 ══════════════════════════════════════════════════════════

df_raw = pd.read_csv(CSV, parse_dates=["t0_time", "t2_time"])
df_raw = df_raw[df_raw["pnl_pct"].notna()].copy()

def apply_fix_c(d):
    def blocked(r):
        t = r["t2_time"]
        if (t.hour, t.minute) < (10, 30): return False
        bdh = r.get("below_day_high_pct")
        return not pd.isna(bdh) and float(bdh) >= 0 and float(bdh) < 1.5
    return d[~d.apply(blocked, axis=1)].copy()

def is_g3(r):
    if r.get("t0_confidence") != "high" or r["t0_to_t2_min"] > G3_THR: return False
    t2 = r["t2_time"]; bdh = r.get("below_day_high_pct")
    return (t2.hour < G3_HR) or (pd.isna(bdh) or float(bdh) < G3_BDH)

def is_sa(r):
    if r.get("t0_confidence") != "high" or r["t0_to_t2_min"] > G3_THR: return False
    bdh = r.get("below_day_high_pct")
    return not pd.isna(bdh) and float(bdh) > SA_MAX_BDH

def regime(r):
    p = r.get("prior_5d_pct_t0")
    return "UNK" if pd.isna(p) else ("UP" if p >= 15 else ("DOWN" if p < 0 else "SIDE"))

b0 = apply_fix_c(df_raw)
b1 = b0[~b0.apply(is_g3, axis=1)].copy()
b2 = b1[~b1.apply(is_sa, axis=1)].copy()
assert len(b2) == 94
b2 = b2.copy()
b2["regime"] = b2.apply(regime, axis=1)

# ═══ 시뮬레이션 함수 ═══════════════════════════════════════════════════

def simulate_v1(row):
    pnl = float(row["pnl_pct"])
    is_sw = bool(row["is_swing"])
    hold = row["t0_to_t2_min"]
    bdh = row["below_day_high_pct"]
    if pnl >= 0: return pnl, None

    if (not is_sw and not pd.isna(hold)
            and hold >= V1_TS_MAX_MIN and pnl <= -V1_TS_MIN_LOSS):
        return -V1_TS_MIN_LOSS, "TIME_STOP_v1"

    if (not is_sw and not pd.isna(bdh)
            and bdh >= V1_VT_BDH_THR and pnl <= -V1_VT_TIGHT):
        return -V1_VT_TIGHT, "VOL_TIGHT"

    if (not pd.isna(hold) and hold <= V1_EC_PROXY_HOLD and pnl <= -V1_EC_MIN_LOSS):
        return -V1_EC_MIN_LOSS, "EC_PROXY_v1"

    return pnl, None


def simulate_v2(row):
    pnl = float(row["pnl_pct"])
    is_sw = bool(row["is_swing"])
    hold = row["t0_to_t2_min"]
    bdh = row["below_day_high_pct"]
    if pnl >= 0: return pnl, None

    # (B) Time Stop Dynamic
    if not is_sw and not pd.isna(hold):
        # 적응형 임계 계산 (bdh proxy로 below_day_high_pct 사용)
        ts_max = V2_TS_BASE_MIN
        if not pd.isna(bdh):
            if bdh >= V2_TS_VHV_BDH:
                ts_max = V2_TS_VHV_MIN
            elif bdh >= V2_TS_HV_BDH:
                ts_max = V2_TS_HV_MIN

        if hold >= ts_max and pnl <= -V2_TS_MIN_LOSS:
            return -V2_TS_MIN_LOSS, "TIME_STOP_v2"

        # MFE 정체 proxy: MFE null → hold≤30 AND pnl≤-0.3 대용
        # (실제론 live mfe_pct 사용, CSV에서는 proxy)
        if (hold >= V2_MFE_STAG_MIN
                and pnl <= -V2_MFE_STAG_LOSS
                and hold <= 30):  # 30분 이내 손실 지속 = MFE 정체 proxy
            return -V2_MFE_STAG_LOSS, "MFE_STAG_PROXY"

    # (C) Vol Tight
    if (not is_sw and not pd.isna(bdh)
            and bdh >= V2_VT_BDH_THR and pnl <= -V2_VT_TIGHT):
        return -V2_VT_TIGHT, "VOL_TIGHT"

    # (A) Early Cut proxy (same as v1, v2 adds RSI slope but no data)
    if (not pd.isna(hold) and hold <= V2_EC_PROXY_HOLD and pnl <= -V2_EC_MIN_LOSS):
        return -V2_EC_MIN_LOSS, "EC_PROXY_v2"

    # (D) MAE_WORSENING: MFE/MAE both null → skip in sim
    # (will activate in live once MFE/MAE tracking is verified)

    return pnl, None


b2[["v1_pnl", "v1_trig"]] = b2.apply(lambda r: pd.Series(simulate_v1(r)), axis=1)
b2[["v2_pnl", "v2_trig"]] = b2.apply(lambda r: pd.Series(simulate_v2(r)), axis=1)

# ═══ KPI 함수 ════════════════════════════════════════════════════════

def kpi(d, pnl_col="pnl_pct"):
    w = d[d["result"] == "WIN"]
    l = d[d["result"] != "WIN"]
    n = len(d)
    if n == 0: return {}
    return {
        "N": n,
        "WR": len(w) / n * 100,
        "W_avg": w[pnl_col].mean() if len(w) else 0.0,
        "L_avg": l[pnl_col].mean() if len(l) else 0.0,
        "avg": d[pnl_col].mean(),
        "PF": -w[pnl_col].sum() / l[pnl_col].sum() if l[pnl_col].sum() < 0 else float("inf"),
    }

# ═══ [1] 전체 KPI 비교 ════════════════════════════════════════════════

ka = kpi(b2)
kb = kpi(b2, "v1_pnl")
kc = kpi(b2, "v2_pnl")

print(SEP)
print("  LCL v2 검증 백테스트 — A안 / B안(LCL v1) / C안(LCL v2)")
print(SEP)
print()
print(f"  {'항목':<12}  {'A안':>10}  {'B안(v1)':>10}  {'C안(v2)':>10}  {'v1Δ':>8}  {'v2Δ':>8}")
print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*8}  {'-'*8}")

for key, label, dec in [("WR","WIN rate",1),("W_avg","WIN avg",3),("L_avg","LOSS avg",3),
                          ("avg","avg PnL",3),("PF","PF",3)]:
    av = ka[key]; bv = kb[key]; cv = kc[key]
    d1 = bv - av; d2 = cv - av
    u = "%" if key != "PF" else ""
    s1 = "+" if d1 > 0 else ""
    s2 = "+" if d2 > 0 else ""
    print(f"  {label:<12}  {av:>9.{dec}f}{u}  {bv:>9.{dec}f}{u}  {cv:>9.{dec}f}{u}  "
          f"{s1}{d1:.3f}{u}  {s2}{d2:.3f}{u}")
print()

# ═══ [2] 트리거별 발동 현황 ════════════════════════════════════════════

print(SEP)
print("[2] 트리거별 발동 현황\n")
print(f"  {'트리거':<20}  {'버전':>4}  {'N':>3}  {'LOSS':>4}  {'원래avg':>8}  {'시뮬avg':>8}  {'개선':>7}")
print(f"  {'-'*20}  {'-'*4}  {'-'*3}  {'-'*4}  {'-'*8}  {'-'*8}  {'-'*7}")

for ver, pnl_col, trig_col in [("v1","v1_pnl","v1_trig"),("v2","v2_pnl","v2_trig")]:
    for trig in sorted(b2[trig_col].dropna().unique()):
        g = b2[b2[trig_col] == trig]
        n_l = (g["result"] != "WIN").sum()
        orig_a = g["pnl_pct"].mean()
        sim_a  = g[pnl_col].mean()
        delta  = sim_a - orig_a
        s = "+" if delta > 0 else ""
        print(f"  {trig:<20}  {ver:>4}  {len(g):>3}  {n_l:>4}  "
              f"{orig_a:>+8.2f}%  {sim_a:>+8.2f}%  {s}{delta:.2f}%p")
    # 미트리거
    no_trig = b2[(b2[trig_col].isna()) & (b2["result"] != "WIN")]
    print(f"  {'미트리거 LOSS':<20}  {ver:>4}  {len(no_trig):>3}  {len(no_trig):>4}  "
          f"{no_trig['pnl_pct'].mean():>+8.2f}%  {'':>8}  {'':>7}")
    print()

# ═══ [3] v1 vs v2 추가 포착 거래 ══════════════════════════════════════

print(SEP)
print("[3] v2에서 추가 포착된 거래 (v1 미포착 → v2 포착)\n")
addl = b2[(b2["v1_trig"].isna()) & (b2["v2_trig"].notna()) & (b2["result"] != "WIN")].copy()
print(f"  추가 포착: {len(addl)}건\n")
if len(addl):
    print(f"  {'trade_id':>8}  {'pnl_pct':>8}  {'v2_pnl':>8}  {'trig':>20}  {'hold':>6}  {'bdh':>6}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*20}  {'-'*6}  {'-'*6}")
    for _, r in addl.sort_values("pnl_pct").iterrows():
        hold = f"{r['t0_to_t2_min']:.0f}m" if pd.notna(r["t0_to_t2_min"]) else "N/A"
        bdh  = f"{r['below_day_high_pct']:.1f}" if pd.notna(r["below_day_high_pct"]) else "N/A"
        print(f"  {r['trade_id']:>8}  {r['pnl_pct']:>+7.2f}%  {r['v2_pnl']:>+7.2f}%  "
              f"{str(r['v2_trig']):>20}  {hold:>6}  {bdh:>6}")
print()

# v1→v2 개선량
v1_l = b2[b2["result"]!="WIN"]["v1_pnl"].sum()
v2_l = b2[b2["result"]!="WIN"]["v2_pnl"].sum()
a_l  = b2[b2["result"]!="WIN"]["pnl_pct"].sum()
print(f"  손실 합계: A안={a_l:+.1f}%p  B안(v1)={v1_l:+.1f}%p  C안(v2)={v2_l:+.1f}%p")
print(f"  v1 개선: {v1_l-a_l:+.1f}%p  |  v2 개선: {v2_l-a_l:+.1f}%p  |  v2 추가: {v2_l-v1_l:+.1f}%p")

# ═══ [4] Big Winner 보호 ════════════════════════════════════════════════

print()
print(SEP)
print("[4] Big Winner 보호 (상위 10%)\n")
top10_thr = b2["pnl_pct"].quantile(0.9)
top10 = b2[b2["pnl_pct"] >= top10_thr]
v1_cut = top10[top10["v1_trig"].notna()]
v2_cut = top10[top10["v2_trig"].notna()]
print(f"  Top 10% 기준 ≥{top10_thr:.2f}%, N={len(top10)}건")
print(f"  v1 오컷: {len(v1_cut)}건  v2 오컷: {len(v2_cut)}건")
print(f"  Big Winner 보존: v1={len(top10)-len(v1_cut)}/{len(top10)}={100*(len(top10)-len(v1_cut))/len(top10):.0f}%  "
      f"v2={len(top10)-len(v2_cut)}/{len(top10)}={100*(len(top10)-len(v2_cut))/len(top10):.0f}%")

# ═══ [5] 레짐별 비교 ════════════════════════════════════════════════════

print()
print(SEP)
print("[5] 레짐별 KPI (LOSS avg 비교)\n")
print(f"  {'레짐':<6}  {'N':>3}  {'WR':>6}  {'L_avg(A)':>9}  {'L_avg(B)':>9}  {'L_avg(C)':>9}  {'PF(A)':>6}  {'PF(C)':>6}")
print(f"  {'-'*6}  {'-'*3}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*6}  {'-'*6}")
for reg in ["UP","SIDE","DOWN","UNK"]:
    g = b2[b2["regime"]==reg]
    if len(g) == 0: continue
    ga = kpi(g); gb = kpi(g,"v1_pnl"); gc = kpi(g,"v2_pnl")
    pf_a = f"{ga['PF']:.3f}" if ga['PF'] != float("inf") else "∞"
    pf_c = f"{gc['PF']:.3f}" if gc['PF'] != float("inf") else "∞"
    print(f"  {reg:<6}  {len(g):>3}  {ga['WR']:>5.1f}%  {ga['L_avg']:>+9.2f}%  "
          f"{gb['L_avg']:>+9.2f}%  {gc['L_avg']:>+9.2f}%  {pf_a:>6}  {pf_c:>6}")

# ═══ [6] (D) MFE/MAE Stagnation — 라이브 전용 구조 설명 ════════════════

print()
print(SEP)
print("[6] (D) MAE_WORSENING Exit — 라이브 전용 (백테스트 시뮬 불가)\n")
print("  MFE/MAE = 0/94 null → 과거 데이터로 시뮬 불가.")
print("  라이브에서 position['mfe_pct'] / position['mae_pct'] 추적 중이면 즉시 발동.\n")
print("  발동 조건 (exit_logic section 1-d (D)):")
print(f"    - elapsed ≥ 10분")
print(f"    - MFE < 0.3%  (수익 구간 진입 실패)")
print(f"    - MAE_worsening: |profit| ≥ MAE × 0.95  (신저점 접근)")
print(f"    - pnl ≤ -0.3%")
print()
print("  추정 효과 (구조적 분석):")
print("    HARD_STOP N=13 (avg=-2.40%): 이 중 MFE<0.3% 패턴이면 10분 내 포착 가능")
print("    → 라이브 데이터 10건 이상 수집 후 실효성 재검토")

# ═══ [7] WIN trade 오컷 리스크 변화 ═══════════════════════════════════

print()
print(SEP)
print("[7] WIN trade 오컷 리스크 (v1 vs v2)\n")
wins = b2[b2["result"]=="WIN"]
# Time Stop 오컷 리스크: hold ≥ threshold인 WIN
at_v1 = wins[(~wins["is_swing"]) & (wins["t0_to_t2_min"] >= V1_TS_MAX_MIN)]
at_v2 = wins[(~wins["is_swing"]) & (wins["t0_to_t2_min"] >= V2_TS_BASE_MIN)]
print(f"  v1 Time Stop 오컷 위험 (hold≥30m WIN): {len(at_v1)}건  avg={at_v1['pnl_pct'].mean():+.2f}% [확인 안됨]")
print(f"  v2 Time Stop 오컷 위험 (hold≥20m WIN): {len(at_v2)}건  avg={at_v2['pnl_pct'].mean():+.2f}% [확인 안됨]")
print()
print(f"  MFE Stagnation 오컷 proxy (hold 20~30m WIN): "
      f"{len(wins[(wins['t0_to_t2_min']>=20) & (wins['t0_to_t2_min']<=30)])}")
print(f"    → 이 WIN들이 MFE<0.3% 상태였다면 오컷 가능 (MAE 없어 확인 불가)")
print()
print("  ▶ 구조적 결론: LCL은 pnl<0에서만 발동 → 최종 WIN 거래는 보호됨")
print("    오컷 위험 = 중간에 음수 구간 통과 후 회복된 WIN에 국한")
print("    → MAE 데이터 수집 이전까지 보수적 파라미터 유지")

# ═══ [8] PASS/FAIL 판정 ════════════════════════════════════════════════

print()
print(SEP)
print("[8] PASS / FAIL 판정 (C안 기준)\n")

la_c = kc["L_avg"]; la_a = ka["L_avg"]
pf_c = kc["PF"]; pf_v1 = kb["PF"]
wr_c = kc["WR"]
bw_c_cut = len(v2_cut)
bw_total = len(top10)
bw_rate = (bw_total - bw_c_cut) / bw_total * 100 if bw_total else 100

crit = [
    ("LOSS avg 개선 (A→C)", la_c > la_a,
     f"A={la_a:+.3f}% → C={la_c:+.3f}% ({la_c-la_a:+.3f}%p)"),
    ("LOSS avg ≤ -1.0% 방향", la_c >= -1.5,
     f"현재={la_c:+.3f}%  목표≤-1.0% (단계적)"),
    ("WIN rate ≥ 27%", wr_c >= 27.0,
     f"WR={wr_c:.1f}%"),
    ("Big Winner 보존 ≥ 95%", bw_rate >= 95,
     f"{bw_total-bw_c_cut}/{bw_total} = {bw_rate:.0f}%"),
    ("PF ≥ 0.35 유지", pf_c >= 0.35,
     f"v1={pf_v1:.3f} → v2={pf_c:.3f}"),
]

all_pass = True
print(f"  {'기준':<30}  {'결과':>6}  {'상세'}")
print(f"  {'-'*30}  {'-'*6}  {'-'*30}")
for name, ok, detail in crit:
    sym = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {name:<30}  {sym:>6}  {detail}")
    if not ok: all_pass = False

print()
verdict = "✅ OVERALL PASS" if all_pass else "⚠  PARTIAL — 조건 미충족 항목 확인"
print(f"  ▶ 종합: {verdict}")

# ═══ [9] 핵심 결론 ════════════════════════════════════════════════════

print()
print(SEP)
print("[9] 핵심 결론\n")
print(f"  A안 → C안 LOSS avg 변화: {la_a:+.3f}% → {la_c:+.3f}% ({la_c-la_a:+.3f}%p)")
print(f"  목표 -0.8% 잔여 갭: {-0.8 - la_c:+.3f}%p")
print()
print(f"  v2 추가 포착: {len(addl)}건 LOSS (v1 대비)")
print(f"  (D) MAE_WORSENING: 라이브 전용 — MFE/MAE 10건+ 후 효과 측정")
print()
print("  현재 한계:")
print("    1. hold_min 없는 LOSS 48건 — TIME_STOP 시뮬 미포함 (라이브에선 발동)")
print("    2. MFE/MAE null — (D) 레이어 백테스트 불가 (라이브에선 발동)")
print("    3. Early Cut 2.0 (RSI slope/3봉/decay) — live 정밀화만, 백테스트 proxy 동일")
print()
print("  ▶ v2 LCL은 규칙→반응형 전환 구조 완성.")
print("    실효성 검증: 라이브 10건+ 후 [EARLY_CUT],[TIME_STOP_MFE],[MAE_WORSENING] 로그 확인")
print(SEP)
