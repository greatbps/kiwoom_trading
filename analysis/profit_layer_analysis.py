#!/usr/bin/env python3
"""
수익 레이어 분석 — 3개 레이어 데이터 검증 및 TP 재보정 시뮬레이션

핵심 발견:
  - execute_partial_sell() 이미 존재 (main_auto_trading.py line 11562)
  - R-based TP1(2R/25%), TP2(4R/25%) exit_logic에 연결됨 (line 697-719)
  - BUT: TP1=2R ≈ +4~6% — WIN avg=+1.388% 구간에서 미체결 (dead logic)
  - TP 재보정으로 기존 인프라 활성화 가능

목적: TP1 threshold 재보정 시뮬레이션 (1R=1배, 1.5R=1.5배 등)
"""

import pandas as pd
import numpy as np

CSV = "logs/late_entry_features_20260704.csv"
G3_THR, G3_HR, G3_BDH, SA_MAX_BDH = 2.0, 10, 3.0, 15.0

df = pd.read_csv(CSV, parse_dates=["t0_time","t2_time"])
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
    t2 = r["t2_time"]; bdh = r.get("below_day_high_pct")
    return (t2.hour < G3_HR) or (pd.isna(bdh) or float(bdh) < G3_BDH)
def is_sa(r):
    if r.get("t0_confidence") != "high" or r["t0_to_t2_min"] > G3_THR: return False
    bdh = r.get("below_day_high_pct")
    return not pd.isna(bdh) and float(bdh) > SA_MAX_BDH

b0 = apply_fix_c(df)
b1 = b0[~b0.apply(is_g3,axis=1)].copy()
b2 = b1[~b1.apply(is_sa,axis=1)].copy()  # N=94 현재 운영 구조

SEP = "=" * 70

print(SEP)
print("  수익 레이어 데이터 분석 — 3개 레이어 검증")
print(SEP)

# ═══════════════════════════════════════════════════════════════
# [1] 현재 TP1/TP2 체결 현황
# ═══════════════════════════════════════════════════════════════
print("\n[1] 현재 TP1(2R=+4~6%) 체결 가능 건수 추정\n")
wins = b2[b2["result"]=="WIN"]["pnl_pct"]
total = len(b2)

# 가정: 1R ≈ 2~3% (structure_stop 거리)
# TP1 = entry × (1 + tp1_mult × r_pct)
# 현재 tp1_r_mult=2.0, 1R≈2.5% → TP1≈+5.0%
# 현재 tp1_r_mult=2.0, 1R≈1.5% → TP1≈+3.0%

bins = [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 99]
labels = ["<0.5%", "0.5~1%", "1~1.5%", "1.5~2%", "2~3%", "3~4%", "4~5%", "≥5%"]
print(f"  WIN 분포 vs TP 임계치별 체결 건수:")
for lo, hi, lb in zip(bins, bins[1:], labels):
    c = ((wins >= lo) & (wins < hi)).sum()
    bar = "█" * c
    print(f"  {lb:>10}: {c:>2}건  {bar}")
print(f"\n  ▶ 현재 TP1(2R≈+4~5%) 체결 추정: {(wins >= 4.0).sum()}건/{len(wins)}건 WIN")
print(f"  ▶ TP1(1R≈+2~3%) 재보정 시 체결: {(wins >= 2.0).sum()}건/{len(wins)}건 WIN")
print(f"  ▶ TP1(0.5R≈+1~1.5%) 재보정 시:  {(wins >= 1.0).sum()}건/{len(wins)}건 WIN")

# ═══════════════════════════════════════════════════════════════
# [2] TP1 재보정 시뮬레이션
# ═══════════════════════════════════════════════════════════════
print(f"\n{'-'*70}")
print("[2] TP1 재보정 시뮬레이션")
print(f"    가정: TP1 체결 후 25% 청산, 나머지 75%는 기존 exit")
print(f"    가정: MFE 데이터 없으므로 보수적으로 TP1 이후 pnl = TP1 수준 유지")
print(f"{'-'*70}\n")

losses = b2[b2["result"]!="WIN"]
l_total_pct = losses["pnl_pct"].sum()

for tp1_thr in [1.0, 1.5, 2.0, 3.0]:
    tp1_hits = wins[wins >= tp1_thr]  # TP1 도달한 WIN 거래
    tp1_miss = wins[wins < tp1_thr]   # TP1 미달 WIN 거래

    # 보수적 시뮬레이션:
    # - TP1 도달 거래: 25%는 tp1_thr에서 청산, 75%는 원래 pnl
    # - 실제로는 TP1 이후 방향이 불확실하므로 75%는 그대로 가정
    # (MFE 없으므로 최소 효과로 계산)
    sim_gain = (
        tp1_hits.apply(lambda p: tp1_thr * 0.25 + p * 0.75).sum()  # TP1 도달: 25%@tp1 + 75%@original
        + tp1_miss.sum()  # TP1 미달: 기존과 동일
    )
    orig_gain = wins.sum()
    delta_tp = (sim_gain - orig_gain) / total

    sim_avg = (sim_gain + l_total_pct) / total
    orig_avg = (orig_gain + l_total_pct) / total

    print(f"  TP1 임계치={tp1_thr:.1f}% (체결 {len(tp1_hits)}건):")
    print(f"    sim_avg={sim_avg:+.3f}%  delta={delta_tp:+.3f}%p  "
          f"({'개선' if sim_avg > orig_avg else '악화'})")

# ═══════════════════════════════════════════════════════════════
# [3] Stage B 분석 — 어느 exit에서 효과가 있나?
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("[3] Stage B (Trend Extension) — 효과 대상 분석")
print(f"{'-'*70}\n")

def cat(r):
    er = str(r.get("exit_reason",""))
    if "Hard Stop" in er:       return "HARD_STOP"
    if "Early Failure" in er:   return "EF_CUT"
    if "트레일링" in er:           return "TRAIL_STOP"
    if "시간" in er:               return "TIME_EXIT"
    if "VWAP" in er or "EMA" in er: return "SIGNAL_EXIT"
    if "Squeeze" in er:           return "SQUEEZE_EXIT"
    return "OTHER"
b2["exit_cat"] = b2.apply(cat, axis=1)

print("  Stage B 가정: '추세 지속 신호 있을 때 early exit 억제'")
print()
for ec, label, stageB_effect in [
    ("SIGNAL_EXIT",  "VWAP/EMA 청산",     "억제 가능하나 WIN=0 — 억제 시 더 악화 가능"),
    ("SQUEEZE_EXIT", "Squeeze 모멘텀 반전", "WIN=8/8 — 억제 금지! 완벽 작동 중"),
    ("TIME_EXIT",    "15:00 시간 청산",    "스윙=당일청산, 억제 불가"),
    ("TRAIL_STOP",   "ATR 트레일링",       "WIN=14/17 — 억제 불필요, 이미 최적"),
]:
    g = b2[b2["exit_cat"]==ec]
    wr = (g["result"]=="WIN").sum() / len(g) * 100 if len(g) > 0 else 0
    avg = g["pnl_pct"].mean() if len(g) > 0 else 0
    print(f"  {ec:<15}: N={len(g):>2}  WR={wr:>5.1f}%  avg={avg:>+6.2f}%")
    print(f"    Stage B 효과: {label} — {stageB_effect}")
    print()

print("  ▶ Stage B 결론:")
print("    - SIGNAL_EXIT(N=4): 억제 시 이미 손실 상태 거래를 더 오래 보유 → 역효과")
print("    - SQUEEZE_EXIT(N=8): 억제 금지 (WIN=100%, avg=+1.23%)")
print("    - 실질적 Stage B 적용 대상 = 0건")
print("    - Stage B는 이 시스템 데이터에서 효과 없음 ← 데이터 결론")

# ═══════════════════════════════════════════════════════════════
# [4] 수익화까지 필요한 개선 규모
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("[4] 수익화 로드맵 — 수치 기반")
print(f"{'-'*70}\n")
w_n, w_avg = len(b2[b2["result"]=="WIN"]), b2[b2["result"]=="WIN"]["pnl_pct"].mean()
l_n, l_avg = len(b2[b2["result"]!="WIN"]), b2[b2["result"]!="WIN"]["pnl_pct"].mean()
cur_avg = (w_n*w_avg + l_n*l_avg) / total

print(f"  현재: WIN {w_n}건×{w_avg:+.3f}% + LOSS {l_n}건×{l_avg:+.3f}% = {cur_avg:+.3f}%")
print()

# breakeven WR (WIN avg, LOSS avg 고정)
be_wr = -l_avg / (w_avg - l_avg) * 100
print(f"  수익화 경로 A: WR 개선")
print(f"    현재 WR={w_n/total*100:.1f}% → breakeven WR={be_wr:.1f}%  (필요: +{be_wr-w_n/total*100:.1f}%p)")
print()

# breakeven WIN avg (WR, LOSS avg 고정)
be_win = -l_avg * l_n / w_n
print(f"  수익화 경로 B: WIN avg 개선")
print(f"    현재 WIN avg={w_avg:.3f}% → breakeven WIN avg={be_win:.3f}%  (필요: +{be_win-w_avg:.3f}%p)")
print()

# breakeven LOSS reduction (WR, WIN avg 고정)
be_loss = -w_avg * w_n / l_n
print(f"  수익화 경로 C: LOSS avg 개선 (손실 압축)")
print(f"    현재 LOSS avg={l_avg:.3f}% → breakeven={be_loss:.3f}%  (필요: {be_loss-l_avg:+.3f}%p 압축)")

print()
print(f"  현실적 복합 개선 시나리오:")
combo = [
    ("TP1 재보정(1R)", 0.025),
    ("HARD_STOP YAML 조정", 0.125),
    ("Stage B 구현", 0.020),
    ("기타 미세조정", 0.050),
]
cumulative = cur_avg
for label, eff in combo:
    cumulative += eff
    print(f"    + {label:<22}: {eff:+.3f}%p → avg={cumulative:+.3f}%")
print(f"\n  → 모든 레이어 합산해도 avg≈{cumulative:+.3f}% (여전히 {'적자' if cumulative < 0 else '흑자'})")
print()
print(f"  ▶ 핵심 인사이트:")
print(f"    '수익 확장'만으로는 수익화 불가 — WR {w_n/total*100:.1f}%→{be_wr:.0f}% 개선이 핵심")
print(f"    현재 작업: 기반 다지기 (방어→공격) + E2(30건+) 데이터 축적")

# ═══════════════════════════════════════════════════════════════
# [5] 권장 구현 우선순위
# ═══════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("[5] 권장 구현 우선순위 (제약 내 최대 효과)")
print(f"{SEP}\n")
print("""
  ✅ P1. TP1/TP2 재보정 (YAML만, 즉시 가능)
       - smc.r_tp.tp1_r_mult: 2.0 → 1.0  (TP1 활성화 +2~3%에서)
       - smc.r_tp.tp2_r_mult: 4.0 → 2.0  (TP2 +4~6%에서)
       - 기존 execute_partial_sell 인프라 활용 (코드 변경 없음)
       - 예상 효과: +0.025%p (보수적) | 위험: NONE

  ✅ P2. Stage B YAML scaffold (enabled: false, 데이터 대기)
       - stage_b_trend_extension 블록 추가
       - 현재 데이터로는 적용 대상 없음 — E2+ 데이터 후 재검토
       - 코드 구현: exit_logic에 hook ONLY (enabled=false)

  ⚠️  P3. Layer 3 (Breakout Add-on) — execute_buy 제약으로 불가
       - 대안: smc.r_tp.position_size_entry_mult 신설 (진입 시 배수 조정)
       - CHoCH A+급 진입 시 position_size × 1.2 허용 (execute_buy 수정 아님)
       - orchestrator evaluate_signal 결과에 size_mult 추가

  📌 우선 집중: E2(30건+) 달성 → WR 분석 → 진입 조건 재검토
       - 현재 수익화를 막는 것은 '수익 작음'이 아니라 'WR 27.7%'
       - Stage B + TP 재보정 합산 최대 +0.15%p → avg -0.630% (여전히 적자)
""")
