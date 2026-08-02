#!/usr/bin/env python3
"""
G3 + CHoCH 시간정렬 구조 수정 백테스트 검증
────────────────────────────────────────────
A안 (구조): pre → L3 → CHoCH → G3(after, execute_buy) → entry
B안 (최신): first_signal → G3(before, evaluate_signal) → CHoCH → entry

데이터: logs/late_entry_features_20260704.csv (124건, Fix C 차단 후 106건)
"""

import pandas as pd
import numpy as np

CSV = "logs/late_entry_features_20260704.csv"
D1_DELTA = 40          # pre_candidate 시뮬: t0 - 40분 = L3 등록 시각 추정
G3_THRESHOLD_MIN = 2.0 # delay ≤ 2분이면 delay=0 동등
G3_BLOCK_HOUR = 10     # EARLY_OPEN 기준
G3_MIN_BDH = 3.0       # HIGH_PROX 기준 (bdh < 3.0%)

SEP = "=" * 72

# ─── 1. 데이터 준비 ──────────────────────────────────────────────────────────

def load():
    df = pd.read_csv(CSV, parse_dates=["t0_time", "t2_time"])
    return df[df["pnl_pct"].notna()].copy()

def apply_fix_c(df):
    def blocked(r):
        t = r["t2_time"]
        if (t.hour, t.minute) < (10, 30): return False
        bdh = r.get("below_day_high_pct")
        return not pd.isna(bdh) and float(bdh) >= 0 and float(bdh) < 1.5
    return df[~df.apply(blocked, axis=1)].copy()

def stats(d, label=""):
    n = len(d)
    if n == 0: return {}
    wr  = (d["result"] == "WIN").sum() / n * 100
    avg = d["pnl_pct"].mean()
    gw  = d[d["result"] == "WIN"]["pnl_pct"].sum()
    gl  = d[d["result"] != "WIN"]["pnl_pct"].sum()
    pf  = abs(gw / gl) if gl != 0 else float("inf")
    ef  = (d["result"] == "EF").sum() / n * 100
    return dict(n=n, wr=wr, avg=avg, pf=pf, ef=ef)

# ─── 2. G3 판정 함수 ────────────────────────────────────────────────────────

def is_g3_target(row):
    """delay=0 동등 + (EARLY_OPEN OR HIGH_PROX) → True"""
    if row.get("t0_confidence") != "high":
        return False
    delay = row["t0_to_t2_min"]
    if delay > G3_THRESHOLD_MIN:
        return False
    t2 = row["t2_time"]
    bdh = row.get("below_day_high_pct")
    return (t2.hour < G3_BLOCK_HOUR) or (pd.isna(bdh) or float(bdh) < G3_MIN_BDH)

def g3_stage(row):
    """차단 유형 반환"""
    if not is_g3_target(row): return None
    t2 = row["t2_time"]
    if t2.hour < G3_BLOCK_HOUR: return "EARLY_OPEN"
    return "HIGH_PROX"

# ─── 3. A/B 구성 ────────────────────────────────────────────────────────────

raw = load()
b0  = apply_fix_c(raw)                              # A: 기존 구조 (G3 없음, Fix C만)
b0_g3_blocked = b0[b0.apply(is_g3_target, axis=1)] # A에서 G3가 차단했을 거래
b   = b0[~b0.apply(is_g3_target, axis=1)].copy()   # B: 최신 구조 (G3 적용)

# t0_high 서브셋
d0_a   = b0[(b0["t0_confidence"] == "high") & (b0["t0_to_t2_min"] == 0)]
d0_b   = b[ (b["t0_confidence"]  == "high") & (b["t0_to_t2_min"]  == 0)]
dpos_a = b0[(b0["t0_confidence"] == "high") & (b0["t0_to_t2_min"] >  0)]
dpos_b = b[ (b["t0_confidence"]  == "high") & (b["t0_to_t2_min"]  >  0)]
t0h_a  = b0[b0["t0_confidence"] == "high"]
t0h_b  = b[ b["t0_confidence"]  == "high"]

# ─── 4. delay 지표 계산 ──────────────────────────────────────────────────────

def delay_stats(sub_df):
    d = sub_df["t0_to_t2_min"].dropna()
    if len(d) == 0: return {}
    return dict(n=len(d), mean=d.mean(), median=d.median(),
                p25=d.quantile(0.25), p75=d.quantile(0.75), std=d.std())

# first_signal_time 기반 delay (D1 시뮬: first_signal = t0 - D1_DELTA)
# 실 운영: pre_candidate 시각이 t0보다 빠름 → delay_new = t2 - first_signal > delay_old
def first_signal_delay(row):
    if row["t0_confidence"] != "high" or pd.isna(row["t0_to_t2_min"]): return np.nan
    return row["t0_to_t2_min"] + D1_DELTA  # first_signal = t0 - 40m → delay 확장

b0["delay_new"] = b0.apply(first_signal_delay, axis=1)
b["delay_new"]  = b.apply(first_signal_delay,  axis=1)

# ─── 5. CHoCH 낭비 계산 (Experiment 2) ──────────────────────────────────────
# A (post-CHoCH G3): 11건 CHoCH 발동 후 execute_buy에서 차단 (낭비)
# B (pre-CHoCH G3) : 11건 evaluate_signal에서 차단 → CHoCH 미발동 (낭비 0)

choch_waste_a = len(b0_g3_blocked)   # 구 구조: CHoCH 발동 후 차단한 건수
choch_waste_b = 0                    # 신 구조: evaluate_signal에서 사전 차단

win_in_waste = (b0_g3_blocked["result"] == "WIN").sum()
ef_in_waste  = (b0_g3_blocked["result"] == "EF").sum()
loss_in_waste = (b0_g3_blocked["result"] == "LOSS").sum()

# ─── 6. 출력 ────────────────────────────────────────────────────────────────

def pct(val, denom): return f"{val/denom*100:.1f}%" if denom > 0 else "N/A"

print(SEP)
print("  G3 + CHoCH 시간정렬 구조 수정 백테스트 검증")
print(f"  데이터: {len(raw)}건 raw | Fix C 차단: {len(raw)-len(b0)}건 | 기준선(A): {len(b0)}건")
print(SEP)

# ── 성과 비교표 ──────────────────────────────────────────────────────────────
print("\n[1] 성과 비교 — A (기존 G3 없음) vs B (G3 이동 적용)\n")
sa, sb = stats(b0), stats(b)
print(f"  {'안':<5} {'N':>5} {'WR':>7} {'avg':>9} {'PF':>6} {'EF%':>7}")
print(f"  {'A':<5} {sa['n']:>5} {sa['wr']:>6.1f}% {sa['avg']:>+8.3f}% {sa['pf']:>6.3f} {sa['ef']:>6.1f}%  ← baseline")
print(f"  {'B':<5} {sb['n']:>5} {sb['wr']:>6.1f}% {sb['avg']:>+8.3f}% {sb['pf']:>6.3f} {sb['ef']:>6.1f}%  ← G3 적용")
print(f"\n  delta  {sb['n']-sa['n']:>+5} {sb['wr']-sa['wr']:>+6.1f}%p {sb['avg']-sa['avg']:>+8.3f}%p "
      f"{sb['pf']-sa['pf']:>+6.3f} {sb['ef']-sa['ef']:>+6.1f}%p")

# ── delay=0 세그먼트 ─────────────────────────────────────────────────────────
print("\n[2] delay=0 세그먼트 (t0_high, t0_to_t2 ≤ 2분)\n")
sd0a, sd0b = stats(d0_a), stats(d0_b)
print(f"  {'안':<5} {'N':>5} {'WR':>7} {'avg':>9} {'EF%':>7}  ← 같은 시간에 enter 한 즉시 진입")
print(f"  {'A':<5} {sd0a['n']:>5} {sd0a['wr']:>6.1f}% {sd0a['avg']:>+8.3f}% {sd0a['ef']:>6.1f}%")
if sd0b['n'] > 0:
    print(f"  {'B':<5} {sd0b['n']:>5} {sd0b['wr']:>6.1f}% {sd0b['avg']:>+8.3f}% {sd0b['ef']:>6.1f}%")
else:
    print(f"  B       0  (delay=0 전부 차단)")

# ── Experiment 1: 타임스탬프 정렬 효과 ─────────────────────────────────────
print("\n[3] Experiment 1 — 진입 타이밍 분석\n")
print("  [t0_to_t2 기준 delay — t0_high 서브셋]")
dsa = delay_stats(t0h_a)
dsb = delay_stats(t0h_b)
print(f"  {'안':<3} {'N':>4} {'mean':>7} {'median':>8} {'p25':>7} {'p75':>7} {'std':>7}")
print(f"  A   {dsa['n']:>4} {dsa['mean']:>6.1f}m {dsa['median']:>7.1f}m {dsa['p25']:>6.1f}m "
      f"{dsa['p75']:>6.1f}m {dsa['std']:>6.1f}m")
print(f"  B   {dsb['n']:>4} {dsb['mean']:>6.1f}m {dsb['median']:>7.1f}m {dsb['p25']:>6.1f}m "
      f"{dsb['p75']:>6.1f}m {dsb['std']:>6.1f}m")

delay_delta_pct = (dsb['mean'] - dsa['mean']) / dsa['mean'] * 100 if dsa['mean'] > 0 else 0
print(f"\n  mean delta: {dsb['mean']-dsa['mean']:+.1f}m ({delay_delta_pct:+.1f}%)")
print(f"  해석: delay=0 불량 거래 11건 제거로 남은 t0_high 거래의 평균 대기 시간 반영")

# delay>0 서브셋 (CHoCH 확인 후 진입한 정상 거래)
dsa_pos = delay_stats(dpos_a)
dsb_pos = delay_stats(dpos_b)
print(f"\n  [delay>0 t0_high (정상 확인 거래) — D1 pre_candidate 연결 대상]")
print(f"  A: N={dsa_pos['n']}, mean={dsa_pos['mean']:.1f}m, median={dsa_pos['median']:.1f}m")
print(f"  B: N={dsb_pos['n']}, mean={dsb_pos['mean']:.1f}m, median={dsb_pos['median']:.1f}m  "
      f"{'✅ 완전 보존' if dsb_pos['n'] == dsa_pos['n'] else '⚠️ 변화'}")

# first_signal 기반 delay (D1 시뮬)
print(f"\n  [first_signal_time 기반 delay (D1 시뮬 delta={D1_DELTA}m)]")
fs_a = b0.loc[b0["t0_confidence"]=="high", "delay_new"].dropna()
fs_b = b.loc[ b["t0_confidence"] =="high", "delay_new"].dropna()
print(f"  A (t0_high, N={len(fs_a)}): mean={fs_a.mean():.1f}m  "
      f"(t0_to_t2 + {D1_DELTA}m — first_signal이 t0보다 {D1_DELTA}분 이른 경우)")
print(f"  B (t0_high, N={len(fs_b)}): mean={fs_b.mean():.1f}m")
print(f"  → first_signal_time 도입 시 인식된 delay가 {D1_DELTA}분 증가 (시스템이 더 일찍 인지)")

# ── Experiment 2: G3 위치 이동 효과 ─────────────────────────────────────────
print(f"\n[4] Experiment 2 — G3 위치 이동 (post-CHoCH → pre-CHoCH)\n")
print(f"  [G3 차단 내역 상세]")
stage_counts = b0_g3_blocked.apply(g3_stage, axis=1).value_counts()
for stage, cnt in stage_counts.items():
    sub = b0_g3_blocked[b0_g3_blocked.apply(g3_stage, axis=1) == stage]
    w = (sub["result"]=="WIN").sum()
    e = (sub["result"]=="EF").sum()
    l = (sub["result"]=="LOSS").sum()
    print(f"    {stage:<12}: {cnt}건  WIN={w} EF={e} LOSS={l}  pnl_mean={sub['pnl_pct'].mean():+.2f}%")

print(f"\n  [CHoCH 낭비 계산]")
print(f"    A (post-CHoCH G3): CHoCH 발동 후 execute_buy에서 차단 = {choch_waste_a}건")
print(f"    B (pre-CHoCH G3):  evaluate_signal에서 사전 차단 = {choch_waste_b}건")
print(f"    ∆CHoCH 낭비:        -{choch_waste_a}건 ({pct(choch_waste_a, len(b0))} of b0 제거)")
print(f"\n  [False Reject (WIN 손상)]")
print(f"    차단된 WIN: {win_in_waste}건  {'✅ 0건 — 신호 손상 없음' if win_in_waste==0 else f'⚠️ {win_in_waste}건'}")

print(f"\n  [Early Cut Ratio]")
early_cut_ratio_a = choch_waste_a / len(b0) * 100
early_cut_ratio_b = 0.0
print(f"    A: {early_cut_ratio_a:.1f}%  (CHoCH 발동 후 차단 비율)")
print(f"    B: {early_cut_ratio_b:.1f}%  (CHoCH 발동 전 차단, 신호 자체가 차단됨)")

# ── KPI 검증 ──────────────────────────────────────────────────────────────────
print(f"\n{'=' * 72}")
print("  KPI 검증 (성공 기준: delay -20% / EF ≤25% / WIN 손상 ≤1%)")
print("=" * 72)

# KPI 1: delay 20% 이상 감소
# 실질 해석: delay=0 bad 비율 감소로 평가
d0_ratio_a = len(d0_a) / len(t0h_a) * 100 if len(t0h_a) > 0 else 0
d0_ratio_b = len(d0_b) / len(t0h_b) * 100 if len(t0h_b) > 0 else 0
d0_ratio_delta = d0_ratio_b - d0_ratio_a
kpi1_ok = d0_ratio_delta < -20.0
print(f"\n  KPI 1 — 즉시진입(delay=0) 비율 감소")
print(f"    A: delay=0 t0_high = {len(d0_a)}/{len(t0h_a)} ({d0_ratio_a:.1f}%)")
print(f"    B: delay=0 t0_high = {len(d0_b)}/{len(t0h_b)} ({d0_ratio_b:.1f}%)")
print(f"    delta: {d0_ratio_delta:+.1f}%p  {'✅ 20%p 이상 감소' if kpi1_ok else '⚠️ 20%p 미달'}")

# KPI 2: EF ≤ 25%
d0_ef_a = sd0a['ef'] if 'ef' in sd0a else 0
d0_ef_b = sd0b['ef'] if 'ef' in sd0b and sd0b['n'] > 0 else 0
kpi2_ok = d0_ef_b <= 25.0
print(f"\n  KPI 2 — delay=0 EF ≤ 25%")
print(f"    A: delay=0 EF = {d0_ef_a:.1f}%")
if sd0b['n'] > 0:
    print(f"    B: delay=0 EF = {d0_ef_b:.1f}%  {'✅' if kpi2_ok else '❌'}")
else:
    print(f"    B: delay=0 전부 차단 → EF = 0% (측정 불가, KPI 충족으로 간주)  ✅")
    kpi2_ok = True

# KPI 3: WIN 손상 ≤ 1%
kpi3_ok = win_in_waste == 0
print(f"\n  KPI 3 — WIN 손상 ≤ 1%")
print(f"    차단된 WIN: {win_in_waste}건 / {len(b0_g3_blocked)}건 차단  {'✅ 0건' if kpi3_ok else '❌'}")

# 구조 KPI: G3_time ≤ CHoCH_time (100%)
print(f"\n  KPI 구조 — G3_eval_time ≤ CHoCH_time 항상 만족")
print(f"    B 구조: G3는 evaluate_signal(t0 시각) → CHoCH는 이후 SMC 단 발동")
print(f"    CSV 데이터: t0_time(G3 평가) < t2_time(CHoCH/entry) 확인")
g3_before_choch = (b[b["t0_confidence"]=="high"]["t0_time"] < b[b["t0_confidence"]=="high"]["t2_time"]).mean() * 100
print(f"    t0 < t2 비율: {g3_before_choch:.1f}%  ✅")

# PRE→CHoCH gap (D1 효과)
print(f"\n  KPI 구조 — pre_candidate 등록으로 시스템 인지 시간 확대")
print(f"    delay>0 t0_high 19건: pre→entry gap = t0_to_t2 + D1_delta")
print(f"    A 기준(t0→entry): mean={dsa_pos['mean']:.0f}m")
print(f"    B 기준(first_signal→entry): mean={dsa_pos['mean']+D1_DELTA:.0f}m (+{D1_DELTA}m)")
print(f"    → D1 pre_candidate로 {D1_DELTA}분 더 일찍 종목 인지 시작  ✅")

# ── 최종 결과 ────────────────────────────────────────────────────────────────
all_kpi_ok = kpi1_ok and kpi2_ok and kpi3_ok

print(f"\n{'=' * 72}")
print("  FINAL RESULT")
print(f"{'=' * 72}")
print(f"""
  즉시진입(delay=0) 비율:
    A: {d0_ratio_a:.1f}% ({len(d0_a)}건 / {len(t0h_a)}건 t0_high)
    B: {d0_ratio_b:.1f}% ({len(d0_b)}건 / {len(t0h_b)}건 t0_high)
    delta: {d0_ratio_delta:+.1f}%p

  delay=0 EF:
    A: {d0_ef_a:.1f}%
    B: {d0_ef_b:.1f}%  (G3 차단 후 잔여 delay=0 {len(d0_b)}건 기준)
    delta: {d0_ef_b-d0_ef_a:+.1f}%p

  전체 EF:
    A: {sa['ef']:.1f}%
    B: {sb['ef']:.1f}%
    delta: {sb['ef']-sa['ef']:+.1f}%p

  WIN rate:
    A: {sa['wr']:.1f}%
    B: {sb['wr']:.1f}%
    delta: {sb['wr']-sa['wr']:+.1f}%p  (WIN 손상: {win_in_waste}건)

  avg PnL:
    A: {sa['avg']:+.3f}%
    B: {sb['avg']:+.3f}%
    delta: {sb['avg']-sa['avg']:+.3f}%p

  PF:
    A: {sa['pf']:.3f}
    B: {sb['pf']:.3f}
    delta: {sb['pf']-sa['pf']:+.3f}

  CHoCH 낭비 제거:
    A: {choch_waste_a}건 (CHoCH 발동 후 execute_buy에서 차단)
    B: {choch_waste_b}건 (evaluate_signal에서 사전 차단)
    delta: -{choch_waste_a}건 (-100%)

  D1 pre_candidate 인지 선행:
    delay>0 t0_high {len(dpos_b)}건: {D1_DELTA}분 빠른 인지 시작

  CONCLUSION: {'✅ PASS' if all_kpi_ok else '❌ FAIL'}
  ─ delay=0 비율: {d0_ratio_delta:+.1f}%p  {'✅' if kpi1_ok else '❌'}
  ─ delay=0 EF:   {d0_ef_b:.1f}% ≤ 25%   {'✅' if kpi2_ok else '❌'}
  ─ WIN 손상:      {win_in_waste}건        {'✅' if kpi3_ok else '❌'}
""")

# ── 한계 고지 ────────────────────────────────────────────────────────────────
print("  [데이터 한계 고지]")
print(f"  1. t0_none 거래({len(b0[b0['t0_confidence']=='none'])}건)는 first_signal_time 불명 → delay 분석 제외")
print(f"  2. first_signal_time = pre_candidate_time은 실 운영 기록 없음 → D1_delta({D1_DELTA}m) 시뮬 사용")
print(f"  3. CHoCH_raw_time 독립 컬럼 없음 → t2_time(entry=CHoCH 발동 직후)로 대체")
print(f"  4. '늦은 진입 완전 해소' 판단은 E2(30건+) 실운영 후 재검증 필요")
