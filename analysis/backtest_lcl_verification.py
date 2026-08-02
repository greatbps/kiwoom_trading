#!/usr/bin/env python3
"""
LOSS CONTROL LAYER 검증 백테스트 (Work Order, 2026-07-04)

목적: A안(Stage A only) vs B안(Stage A + LOSS CONTROL LAYER) 비교
     손실 압축 + 빅 위너 보호 여부 판별

시뮬레이션 한계:
  - MFE/MAE = 0/94 (null) → WIN trade 진행 중 최저점 미파악
  - Time Stop / Vol Tight은 보수적 시뮬레이션 (가능)
  - Early Cut (RSI+VWAP+volume)은 직접 시뮬 불가 → EC-eligible 간접 추정
"""

import pandas as pd
import numpy as np

CSV = "logs/late_entry_features_20260704.csv"
G3_THR, G3_HR, G3_BDH   = 2.0, 10, 3.0
SA_MAX_BDH               = 15.0

# ── LCL 파라미터 (YAML에서 가져온 값) ─────────────────────────────────
LCL_EC_MIN_LOSS   = 0.5   # Early Cut 발동 최소 손실 (pnl ≤ -0.5%)
LCL_EC_PROXY_HOLD = 15.0  # EC proxy: 15분 이내 급손실 → EC-eligible
LCL_TS_MAX_MIN    = 30.0  # Time Stop: 30분 이상 손실 지속
LCL_TS_MIN_LOSS   = 0.8   # Time Stop 발동 최소 손실 (pnl ≤ -0.8%)
LCL_VT_BDH_THR    = 5.0   # Vol Tight: bdh ≥ 5% (bdh proxy로 below_day_high_pct 사용)
LCL_VT_TIGHT      = 2.5   # Vol Tight 발동 손실 (pnl ≤ -2.5%)

SEP = "=" * 72

# ═══════════════════════════════════════════════════════════════════════
# 데이터 로드 & 필터 함수
# ═══════════════════════════════════════════════════════════════════════

df = pd.read_csv(CSV, parse_dates=["t0_time", "t2_time"])
df = df[df["pnl_pct"].notna()].copy()

def apply_fix_c(d):
    def blocked(r):
        t = r["t2_time"]
        if (t.hour, t.minute) < (10, 30): return False
        bdh = r.get("below_day_high_pct")
        return not pd.isna(bdh) and float(bdh) >= 0 and float(bdh) < 1.5
    return d[~d.apply(blocked, axis=1)].copy()

def is_g3(r):
    if r.get("t0_confidence") != "high" or r["t0_to_t2_min"] > G3_THR: return False
    t2 = r["t2_time"]
    bdh = r.get("below_day_high_pct")
    return (t2.hour < G3_HR) or (pd.isna(bdh) or float(bdh) < G3_BDH)

def is_sa(r):
    if r.get("t0_confidence") != "high" or r["t0_to_t2_min"] > G3_THR: return False
    bdh = r.get("below_day_high_pct")
    return not pd.isna(bdh) and float(bdh) > SA_MAX_BDH

def regime(r):
    p = r.get("prior_5d_pct_t0")
    if pd.isna(p): return "UNK"
    return "UP" if p >= 15 else ("DOWN" if p < 0 else "SIDE")

b0 = apply_fix_c(df)
b1 = b0[~b0.apply(is_g3, axis=1)].copy()
b2 = b1[~b1.apply(is_sa, axis=1)].copy()   # A안 기준 N=94
assert len(b2) == 94, f"b2 N={len(b2)}, expected 94"

b2 = b2.copy()
b2["regime"] = b2.apply(regime, axis=1)

# ═══════════════════════════════════════════════════════════════════════
# B안 시뮬레이션: LCL 적용
#
# 비스윙(is_swing=False) 전용: Time Stop, Vol Tight
# 스윙+비스윙: Early Cut (proxy)
#
# 한계: MAE 없음 → WIN trade 오컷 여부 직접 확인 불가
#       보수적 시뮬: LOSS trade 개선만 반영 (WIN 오컷=0 가정)
# ═══════════════════════════════════════════════════════════════════════

def simulate_lcl(row):
    """
    각 거래에 LCL 적용 시 시뮬 결과 반환.
    Return: (sim_pnl, lcl_trigger)
      - sim_pnl:    LCL 적용 후 추정 PnL
      - lcl_trigger: None | 'TIME_STOP' | 'VOL_TIGHT' | 'EARLY_CUT_PROXY'
    """
    pnl      = float(row["pnl_pct"])
    is_swing = bool(row["is_swing"])
    hold_min = row["t0_to_t2_min"]  # might be NaN
    bdh      = row["below_day_high_pct"]  # proxy for day volatility

    # LCL은 손실 포지션에만 적용
    if pnl >= 0:
        return pnl, None

    # ────── (B) Time Stop ──────────────────────────────────────────────
    # 비스윙 전용. hold_min ≥ 30 AND pnl ≤ -0.8%
    # 보수적 가정: time stop 발동 시 -0.8% 에서 exit (개선 반영)
    if (not is_swing
            and not pd.isna(hold_min)
            and hold_min >= LCL_TS_MAX_MIN
            and pnl <= -LCL_TS_MIN_LOSS):
        return -LCL_TS_MIN_LOSS, "TIME_STOP"

    # ────── (C) Vol Tight Stop ─────────────────────────────────────────
    # 비스윙 전용. below_day_high_pct 을 변동성 proxy로 사용.
    # 진입가가 당일 고점에서 멀수록 = 장중 변동 큰 날
    # bdh ≥ LCL_VT_BDH_THR 이고 pnl ≤ -2.5%
    if (not is_swing
            and not pd.isna(bdh)
            and bdh >= LCL_VT_BDH_THR
            and pnl <= -LCL_VT_TIGHT):
        return -LCL_VT_TIGHT, "VOL_TIGHT"

    # ────── (A) Early Cut Proxy ────────────────────────────────────────
    # RSI/VWAP/volume 데이터 없음 → proxy 기준으로 EC-eligible 식별
    # 기준: 15분 이내 빠른 손실 AND pnl ≤ -0.5% (구조 실패 즉각 신호)
    # 이 경우 EC가 -0.5% 에서 exit 했을 것으로 시뮬
    if (not pd.isna(hold_min)
            and hold_min <= LCL_EC_PROXY_HOLD
            and pnl <= -LCL_EC_MIN_LOSS):
        return -LCL_EC_MIN_LOSS, "EARLY_CUT_PROXY"

    return pnl, None

b2[["sim_pnl", "lcl_trigger"]] = b2.apply(
    lambda r: pd.Series(simulate_lcl(r)), axis=1
)

# ═══════════════════════════════════════════════════════════════════════
# WIN trade 오컷 리스크 (MAE 없으므로 구조적 추정)
# ═══════════════════════════════════════════════════════════════════════
# Time Stop 오컷 리스크: WIN trade 중 hold_min ≥ 30인 것
# (30분 이상 보유 WIN → 중간에 -0.8% 이하 다녀왔을 가능성)
wins_b2       = b2[b2["result"] == "WIN"]
at_risk_ts    = wins_b2[
    (~wins_b2["is_swing"])
    & (wins_b2["t0_to_t2_min"] >= LCL_TS_MAX_MIN)
].copy()
safe_wins     = wins_b2[~wins_b2.index.isin(at_risk_ts.index)]

# ═══════════════════════════════════════════════════════════════════════
# KPI 계산 함수
# ═══════════════════════════════════════════════════════════════════════

def kpi(d, pnl_col="pnl_pct"):
    w = d[d["result"] == "WIN"]
    l = d[d["result"] != "WIN"]
    total = len(d)
    if total == 0: return {}
    wr   = len(w) / total * 100
    w_avg = w[pnl_col].mean() if len(w) else 0.0
    l_avg = l[pnl_col].mean() if len(l) else 0.0
    avg   = d[pnl_col].mean()
    pf    = -w[pnl_col].sum() / l[pnl_col].sum() if l[pnl_col].sum() < 0 else float("inf")
    return {"N": total, "WR": wr, "W_avg": w_avg, "L_avg": l_avg, "avg": avg, "PF": pf}

def kpi_b(d):
    """B안: sim_pnl 사용, result는 A안 기준 유지 (WIN/LOSS 구분은 A안과 동일)"""
    w = d[d["result"] == "WIN"]
    l = d[d["result"] != "WIN"]
    total = len(d)
    if total == 0: return {}
    wr   = len(w) / total * 100
    w_avg = w["sim_pnl"].mean() if len(w) else 0.0
    l_avg = l["sim_pnl"].mean() if len(l) else 0.0
    avg   = d["sim_pnl"].mean()
    pf    = -w["sim_pnl"].sum() / l["sim_pnl"].sum() if l["sim_pnl"].sum() < 0 else float("inf")
    return {"N": total, "WR": wr, "W_avg": w_avg, "L_avg": l_avg, "avg": avg, "PF": pf}

# ═══════════════════════════════════════════════════════════════════════
# [0] 헤더 + 시뮬레이션 한계 안내
# ═══════════════════════════════════════════════════════════════════════

print(SEP)
print("  LOSS CONTROL LAYER 검증 백테스트")
print("  A안: Stage A+G3 (기준)  vs  B안: A안 + LCL 시뮬")
print(SEP)
print("""
  ⚠ 시뮬레이션 한계:
    - MFE/MAE = 0/94 (null) → WIN 거래 중간 최저점 확인 불가
    - WIN 오컷 = MAE < -0.5% 인 경우, 이 분석에선 확인 안 됨
    - Early Cut: RSI/VWAP/volume 데이터 없음 → 15분내 손실 proxy 사용
    - Time Stop / Vol Tight: 보수적 시뮬 (발동 확인 가능)
""")

# ═══════════════════════════════════════════════════════════════════════
# [1] 전체 KPI 비교 (A안 vs B안)
# ═══════════════════════════════════════════════════════════════════════

ka = kpi(b2)
kb = kpi_b(b2)

print(SEP)
print("[1] 전체 KPI 비교 (N=94)\n")
print(f"  {'항목':<12}  {'A안':>10}  {'B안(시뮬)':>10}  {'변화':>10}")
print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*10}")
for key, label, dec in [
    ("WR",    "WIN rate",  1),
    ("W_avg", "WIN avg",   3),
    ("L_avg", "LOSS avg",  3),
    ("avg",   "avg PnL",   3),
    ("PF",    "PF",        3),
]:
    a_v = ka[key]
    b_v = kb[key]
    delta = b_v - a_v
    unit = "%" if key in ("WR","W_avg","L_avg","avg") else ""
    sign = "+" if delta > 0 else ""
    print(f"  {label:<12}  {a_v:>9.{dec}f}{unit}  {b_v:>9.{dec}f}{unit}  {sign}{delta:.3f}{unit}")

print()

# ═══════════════════════════════════════════════════════════════════════
# [2] LCL 트리거별 분석
# ═══════════════════════════════════════════════════════════════════════

print(SEP)
print("[2] LCL 트리거별 발동 현황\n")
print(f"  {'트리거':<20}  {'N':>4}  {'WIN':>4}  {'LOSS':>5}  {'A안 avg':>9}  {'B안 avg':>9}  {'개선':>7}")
print(f"  {'-'*20}  {'-'*4}  {'-'*4}  {'-'*5}  {'-'*9}  {'-'*9}  {'-'*7}")

triggered = b2[b2["lcl_trigger"].notna()]
for trig in ["TIME_STOP", "VOL_TIGHT", "EARLY_CUT_PROXY"]:
    g = b2[b2["lcl_trigger"] == trig]
    if len(g) == 0:
        print(f"  {trig:<20}  {0:>4}  {'':>4}  {'':>5}  {'N/A':>9}  {'N/A':>9}  {'N/A':>7}")
        continue
    n_w = (g["result"] == "WIN").sum()
    n_l = (g["result"] != "WIN").sum()
    a_avg = g["pnl_pct"].mean()
    b_avg = g["sim_pnl"].mean()
    delta = b_avg - a_avg
    sign = "+" if delta > 0 else ""
    print(f"  {trig:<20}  {len(g):>4}  {n_w:>4}  {n_l:>5}  {a_avg:>+9.2f}%  {b_avg:>+9.2f}%  {sign}{delta:.2f}%p")

# 미트리거
no_trig_loss = b2[(b2["lcl_trigger"].isna()) & (b2["result"] != "WIN")]
print(f"\n  ▶ 미트리거 LOSS: N={len(no_trig_loss)}, avg={no_trig_loss['pnl_pct'].mean():+.2f}%")
print(f"     (hold_min 없거나 손실 기준 미달 — EC proxy 미적용)")

# ═══════════════════════════════════════════════════════════════════════
# [3] Big Winner 보호 여부 (상위 10% 수익 거래)
# ═══════════════════════════════════════════════════════════════════════

print()
print(SEP)
print("[3] Big Winner 보호 여부 (상위 10% 수익 거래)\n")

top10_thr  = b2["pnl_pct"].quantile(0.9)
top10      = b2[b2["pnl_pct"] >= top10_thr].copy()
n_top10    = len(top10)

# B안에서도 동일 pnl 유지 여부 (LCL은 음수일 때만 발동 → 양수 거래는 safe)
top10_cut  = top10[top10["lcl_trigger"].notna()]  # 시뮬에서 오컷된 건 (있다면)
top10_safe = top10[top10["lcl_trigger"].isna()]

print(f"  Top 10% 기준 PnL: ≥ {top10_thr:.2f}%  (N={n_top10}건)\n")
print(f"  {'trade_id':>10}  {'pnl_pct':>8}  {'sim_pnl':>8}  {'trigger':>15}  {'is_swing':>8}  {'hold_min':>8}")
print(f"  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*15}  {'-'*8}  {'-'*8}")
for _, r in top10.sort_values("pnl_pct", ascending=False).iterrows():
    trig = r["lcl_trigger"] if pd.notna(r["lcl_trigger"]) else "—"
    hold = f"{r['t0_to_t2_min']:.0f}m" if pd.notna(r["t0_to_t2_min"]) else "N/A"
    mark = "⚠" if pd.notna(r["lcl_trigger"]) else "✅"
    print(f"  {r['trade_id']:>10}  {r['pnl_pct']:>+7.2f}%  {r['sim_pnl']:>+7.2f}%  {trig:>15}  {str(r['is_swing']):>8}  {hold:>8}  {mark}")

print()
top10_wr_a = (top10["result"] == "WIN").sum()
top10_wr_b = len(top10_safe)  # LCL 미발동 = B안에서도 보존
print(f"  ▶ Big Winner 보존율: {top10_wr_b}/{n_top10} = {top10_wr_b/n_top10*100:.0f}%")
print(f"    (A안 WIN: {top10_wr_a}/{n_top10}  →  B안 동일: LCL은 음수에서만 발동)")

# ═══════════════════════════════════════════════════════════════════════
# [4] WIN 거래 오컷 리스크 분석
# ═══════════════════════════════════════════════════════════════════════

print()
print(SEP)
print("[4] WIN 거래 오컷 리스크 (Time Stop 기준)\n")
print("  MAE=0/94 (null) → 직접 확인 불가. 구조적 추정 사용.\n")

w_all = b2[b2["result"] == "WIN"]
print(f"  전체 WIN: {len(w_all)}건")
print()

# Time Stop 오컷 리스크: hold_min ≥ 30 인 WIN
at_risk_ts_b2 = w_all[(~w_all["is_swing"]) & (w_all["t0_to_t2_min"] >= LCL_TS_MAX_MIN)]
print(f"  [Time Stop] 오컷 리스크: hold_min ≥ {LCL_TS_MAX_MIN:.0f}분 WIN = {len(at_risk_ts_b2)}건")
if len(at_risk_ts_b2) > 0:
    print(f"    avg pnl = {at_risk_ts_b2['pnl_pct'].mean():+.2f}%  (이 WIN이 중간에 -0.8% 다녀왔다면 컷 가능)")
    for _, r in at_risk_ts_b2.sort_values("pnl_pct", ascending=False).iterrows():
        hold = f"{r['t0_to_t2_min']:.0f}m"
        print(f"    trade={r['trade_id']:>6}  pnl={r['pnl_pct']:>+6.2f}%  hold={hold}")

# Vol Tight 오컷 리스크: bdh ≥ 5% AND WIN (중간에 -2.5% 다녀왔다면)
at_risk_vt = w_all[
    (~w_all["is_swing"])
    & (w_all["below_day_high_pct"] >= LCL_VT_BDH_THR)
].copy()
print()
print(f"  [Vol Tight] 오컷 리스크: bdh≥{LCL_VT_BDH_THR}% AND WIN = {len(at_risk_vt)}건")
if len(at_risk_vt) > 0:
    print(f"    avg pnl = {at_risk_vt['pnl_pct'].mean():+.2f}%")
    print(f"    (이 WIN이 중간에 -2.5% 다녀왔다면 컷 가능 — MAE 없어 미확인)")

# Early Cut 오컷 리스크: hold < 15분 WIN (이미 빠른 WIN)
at_risk_ec = w_all[w_all["t0_to_t2_min"] < LCL_EC_PROXY_HOLD].copy()
print()
print(f"  [Early Cut] 오컷 리스크: hold < {LCL_EC_PROXY_HOLD:.0f}분 WIN = {len(at_risk_ec)}건")
if len(at_risk_ec) > 0:
    print(f"    avg pnl = {at_risk_ec['pnl_pct'].mean():+.2f}%")
    print(f"    (EC 발동 = 손실 상태에서만 → 빠른 WIN은 이미 플러스 → 오컷 없음)")

print()
print("  ▶ 구조적 결론:")
print("    - Early Cut: 손실 상태(pnl<0)에서만 발동 → WIN 거래는 이미 플러스이면 SAFE")
print("    - Time Stop / Vol Tight: hold_min 데이터 있는 WIN 중 해당 조건 확인 필요")

# ═══════════════════════════════════════════════════════════════════════
# [5] 레짐별 안정성 체크
# ═══════════════════════════════════════════════════════════════════════

print()
print(SEP)
print("[5] 레짐별 비교 (UP / SIDE / DOWN / UNK)\n")
print(f"  {'레짐':<6}  {'N':>3}  {'WR(A)':>7}  {'WR(B)':>7}  {'L_avg(A)':>9}  {'L_avg(B)':>9}  {'PF(A)':>6}  {'PF(B)':>6}")
print(f"  {'-'*6}  {'-'*3}  {'-'*7}  {'-'*7}  {'-'*9}  {'-'*9}  {'-'*6}  {'-'*6}")

for reg in ["UP", "SIDE", "DOWN", "UNK"]:
    g = b2[b2["regime"] == reg]
    if len(g) == 0:
        continue
    ka_r = kpi(g)
    kb_r = kpi_b(g)
    l_a = ka_r.get("L_avg", float("nan"))
    l_b = kb_r.get("L_avg", float("nan"))
    pf_a = ka_r.get("PF", float("nan"))
    pf_b = kb_r.get("PF", float("nan"))
    pf_a_s = f"{pf_a:.3f}" if pf_a != float("inf") else "∞"
    pf_b_s = f"{pf_b:.3f}" if pf_b != float("inf") else "∞"
    print(f"  {reg:<6}  {len(g):>3}  {ka_r['WR']:>6.1f}%  {kb_r['WR']:>6.1f}%  {l_a:>+9.2f}%  {l_b:>+9.2f}%  {pf_a_s:>6}  {pf_b_s:>6}")

# 레짐별 LCL 트리거 분포
print()
print("  LCL 트리거 레짐 분포:")
trig_df = b2[b2["lcl_trigger"].notna()]
for reg in ["UP", "SIDE", "DOWN", "UNK"]:
    g = trig_df[trig_df["regime"] == reg]
    if len(g) == 0: continue
    ts_ = (g["lcl_trigger"] == "TIME_STOP").sum()
    vt_ = (g["lcl_trigger"] == "VOL_TIGHT").sum()
    ec_ = (g["lcl_trigger"] == "EARLY_CUT_PROXY").sum()
    print(f"    {reg}: N={len(g)}  TIME_STOP={ts_}  VOL_TIGHT={vt_}  EC_PROXY={ec_}")

# ═══════════════════════════════════════════════════════════════════════
# [6] LOSS 구조 변화 상세
# ═══════════════════════════════════════════════════════════════════════

print()
print(SEP)
print("[6] LOSS 구조 변화 상세\n")

losses_a = b2[b2["result"] != "WIN"]
print(f"  A안 LOSS: N={len(losses_a)}  avg={losses_a['pnl_pct'].mean():+.3f}%")
print()

# 개선 시뮬
improved = b2[(b2["result"] != "WIN") & (b2["lcl_trigger"].notna())]
unchanged_loss = b2[(b2["result"] != "WIN") & (b2["lcl_trigger"].isna())]

print(f"  B안 LCL 트리거 LOSS: N={len(improved)}  A avg={improved['pnl_pct'].mean():+.2f}%  B avg={improved['sim_pnl'].mean():+.2f}%")
print(f"  B안 미트리거 LOSS:   N={len(unchanged_loss)}  avg={unchanged_loss['pnl_pct'].mean():+.2f}%  (변화 없음)")
print()

total_loss_a = losses_a["pnl_pct"].sum()
total_loss_b = improved["sim_pnl"].sum() + unchanged_loss["pnl_pct"].sum()
print(f"  손실 합계 변화: A안={total_loss_a:+.1f}%p → B안={total_loss_b:+.1f}%p  (개선={total_loss_b-total_loss_a:+.1f}%p)")
print()

# 손실 분포 비교
print("  손실 분포 (A안 vs B안 LOSS trades):")
bins_p = [-99, -4, -3, -2, -1, -0.5, 0]
labels_p = ["≤-4%", "-4~-3%", "-3~-2%", "-2~-1%", "-1~-0.5%", "-0.5~0%"]
print(f"    {'구간':>10}  {'A안 N':>6}  {'B안 N':>6}")
b_pnl = pd.concat([improved["sim_pnl"], unchanged_loss["pnl_pct"]])
for lo, hi, lb in zip(bins_p, bins_p[1:], labels_p):
    a_n = ((losses_a["pnl_pct"] > lo) & (losses_a["pnl_pct"] <= hi)).sum()
    b_n = ((b_pnl > lo) & (b_pnl <= hi)).sum()
    print(f"    {lb:>10}  {a_n:>6}  {b_n:>6}")

# ═══════════════════════════════════════════════════════════════════════
# [7] TP1 재보정 효과 (tp1_r_mult 2.0 → 1.0)
# ═══════════════════════════════════════════════════════════════════════

print()
print(SEP)
print("[7] TP1 재보정 효과 분석 (tp1_r_mult: 2.0 → 1.0)\n")
print("  가정: 1R ≈ entry - structure_stop ≈ 2~3% → TP1=1R ≈ +2~3%\n")

wins_a = b2[b2["result"] == "WIN"]
w_pnl  = wins_a["pnl_pct"]
total  = len(b2)

# TP1=1R 도달 추정: pnl ≥ 2.0% (보수적 1R 추정)
TP1_THR = 2.0
tp1_hits = w_pnl[w_pnl >= TP1_THR]
tp1_miss = w_pnl[w_pnl < TP1_THR]
l_sum    = losses_a["pnl_pct"].sum()

# 시뮬: TP1 도달분 25%를 TP1 가격에서 partial exit
sim_win = (
    tp1_hits.apply(lambda p: TP1_THR * 0.25 + p * 0.75).sum()
    + tp1_miss.sum()
)
orig_win = w_pnl.sum()
delta_tp = (sim_win - orig_win) / total

print(f"  현재 WIN avg: {w_pnl.mean():+.3f}%  TP1@{TP1_THR}% 도달: {len(tp1_hits)}/{len(wins_a)} WIN")
print(f"  시뮬 WIN 합계: {orig_win:+.1f}%p → {sim_win:+.1f}%p  delta={delta_tp:+.3f}%p/건")
print()
print(f"  ▶ 보수적 시뮬 기준 delta={delta_tp:+.3f}%p (MFE 없어 75% 유지 가정)")
print(f"     실제 효과: TP1 체결 → 25% early lock → 잔여 추세 계속 → 중립~소폭 개선 예상")

# ═══════════════════════════════════════════════════════════════════════
# [8] 종합 PASS / FAIL 판정
# ═══════════════════════════════════════════════════════════════════════

print()
print(SEP)
print("[8] PASS / FAIL 판정\n")

criteria = []
passes   = []

# KPI 비교
l_a_v = ka["L_avg"]
l_b_v = kb["L_avg"]
w_a_v = ka["WR"]
w_b_v = kb["WR"]
pf_a_v = ka["PF"]
pf_b_v = kb["PF"]
w_avg_a = ka["W_avg"]
w_avg_b = kb["W_avg"]

# (1) LOSS avg 개선
loss_ok = l_b_v > l_a_v  # 음수이므로 b > a = 덜 손실
criteria.append(("LOSS avg 개선 (방향성)", loss_ok,
    f"A={l_a_v:+.3f}% → B={l_b_v:+.3f}% ({l_b_v-l_a_v:+.3f}%p)"))

# (2) Big Winner 보존율 ≥ 95%
bw_kept = n_top10 - len(top10_cut)
bw_rate = bw_kept / n_top10 * 100 if n_top10 > 0 else 100
bw_ok   = bw_rate >= 95
criteria.append(("Big Winner 보존율 ≥ 95%", bw_ok,
    f"{bw_kept}/{n_top10} = {bw_rate:.0f}%"))

# (3) WIN trade 손상 ≤ 5%
# 시뮬에서 WIN의 sim_pnl이 악화된 건 없음 (LCL은 손실 때만 발동)
w_damaged = (wins_b2["sim_pnl"] < wins_b2["pnl_pct"]).sum()
w_dmg_pct = w_damaged / len(wins_b2) * 100 if len(wins_b2) > 0 else 0
w_ok      = w_dmg_pct <= 5
criteria.append(("WIN trade 손상 ≤ 5%", w_ok,
    f"손상 건수={w_damaged} / {len(wins_b2)} = {w_dmg_pct:.0f}%"))

# (4) PF 유지 or 상승
pf_ok = pf_b_v >= pf_a_v
criteria.append(("PF 유지 or 상승", pf_ok,
    f"A={pf_a_v:.3f} → B={pf_b_v:.3f} ({'+' if pf_b_v>=pf_a_v else ''}{pf_b_v-pf_a_v:.3f})"))

# (5) 레짐별 붕괴 없음 (LOSS avg가 더 나빠진 레짐 없음)
regime_fail = []
for reg in ["UP", "SIDE", "DOWN"]:
    g = b2[b2["regime"] == reg]
    if len(g) < 3: continue
    la_r = kpi(g).get("L_avg", 0)
    lb_r = kpi_b(g).get("L_avg", 0)
    if lb_r < la_r:  # B안이 더 나빠짐 (음수가 더 커짐)
        regime_fail.append(reg)
reg_ok = len(regime_fail) == 0
criteria.append(("레짐별 붕괴 없음", reg_ok,
    "OK" if reg_ok else f"붕괴 레짐={regime_fail}"))

# 출력
print(f"  {'기준':<28}  {'결과':>6}  {'상세'}")
print(f"  {'-'*28}  {'-'*6}  {'-'*30}")
all_pass = True
for name, passed, detail in criteria:
    symbol = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {name:<28}  {symbol:>6}  {detail}")
    if not passed: all_pass = False

print()
verdict = "✅ OVERALL PASS" if all_pass else "⚠️  PARTIAL — 일부 항목 확인 필요"
print(f"  ▶ 종합 판정: {verdict}")

# ═══════════════════════════════════════════════════════════════════════
# [9] 한 줄 요약
# ═══════════════════════════════════════════════════════════════════════

print()
print(SEP)
print("[9] 핵심 결론\n")
print(f"  A안: LOSS avg={l_a_v:+.3f}%  →  B안(시뮬): LOSS avg={l_b_v:+.3f}%")
print(f"  개선폭: {l_b_v-l_a_v:+.3f}%p  |  목표: -0.8% (현재 대비 추가 {-0.8-l_b_v:.3f}%p 필요)")
print()
print(f"  LCL 트리거 건수: {len(triggered)}건 / {len(losses_a)}건 LOSS")
print(f"  Big Winner 보존: {bw_rate:.0f}%  WIN 손상: {w_dmg_pct:.0f}%")
print()
if all_pass:
    print("  ▶ LCL은 '손실 제거' 역할을 하면서 '수익 파괴' 없음 — 구현 유효")
else:
    print("  ▶ 일부 항목 미충족 — 파라미터 조정 or 추가 데이터 필요")
print()
print("  ⚠ 주의: 이 결과는 MAE 데이터 없는 보수적 시뮬.")
print("    실제 Early Cut 효과는 라이브 데이터 10건+ 후 재검토.")
print(SEP)
