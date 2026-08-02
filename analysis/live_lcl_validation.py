#!/usr/bin/env python3
"""
LCL v2.1 Live Validation Report
(2026-07-04 ~)

Usage:
  python3 analysis/live_lcl_validation.py
  python3 analysis/live_lcl_validation.py --since 2026-07-04
  python3 analysis/live_lcl_validation.py --days 14

목적:
  - LCL v2.1 실전 투입 후 트리거 실효성 / KPI 모니터링
  - Phase 1 (1~30건): 구조 작동 확인
  - Phase 2 (31~50건): KPI 안정화 확인
  - FAIL 조건 자동 감지 및 경보

데이터 소스:
  - PostgreSQL trading_system.trades (exit_time >= since)
  - logs/auto_trading_YYYYMMDD.log (execution gap 분석)

백테스트 기준선 (b2 94건):
  WIN rate:  27.7%
  LOSS avg: -1.609% (A안) / -1.462% (B안 proxy) / -0.907% (B+ 이론)
  PF:        0.330 (A안) / 0.363 (B안)
  Big Winner: 10/10 = 100%
"""

import re
import sys
import json
import argparse
import psycopg2
import glob
from datetime import datetime, date, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

SEP  = "=" * 72
SEP2 = "-" * 72

# ─── 설정 ─────────────────────────────────────────────────────────────────
DB_CONF = dict(dbname="trading_system", user="postgres",
               password=os.getenv("POSTGRES_PASSWORD"), host="localhost")

LCL_DEPLOY_DATE = "2026-07-04"

# 백테스트 기준선 (b2, LCL v2.1 proxy sim)
BASELINE = {
    "wr_a":    27.7,  "wr_b":    27.7,
    "l_avg_a": -1.609,"l_avg_b": -1.462, "l_avg_bp": -0.907,
    "pf_a":    0.330, "pf_b":    0.363,  "pf_bp":     0.585,
    "bw_rate": 100.0,                     # Big winner 보존율
    "top10_thr": 1.464,                   # Big winner 기준 pnl (b2 90th pct)
}

# LCL 트리거 패턴 (exit_reason 문자열)
LCL_TRIGGERS = {
    "EARLY_CUT":    r"\[EARLY_CUT\]",
    "TIME_STOP":    r"\[TIME_STOP\](?!_MFE)",
    "TIME_STOP_MFE":r"\[TIME_STOP_MFE\]",
    "VOL_TIGHT":    r"\[VOL_TIGHT_STOP\]",
    "MAE_WORSENING":r"\[MAE_WORSENING\]",
}

# Phase 기준
PHASE1_N = 30
PHASE2_N = 50

# KPI 허용 범위 (단계별)
KPI_PHASE1 = {
    "wr_min": 20.0,    # Phase 1: 느슨한 기준 (n 작음)
    "loss_max": -2.0,  # LOSS avg ≤ -2.0% (초기 허용)
    "pf_min": 0.25,
}
KPI_PHASE2 = {
    "wr_min": 27.0,    # Phase 2: 백테스트 기준 복원
    "loss_max": -1.5,  # LOSS avg ≤ -1.5%
    "pf_min": 0.35,
}

# FAIL 조건 (즉시 알람)
FAIL_CONDITIONS = {
    "bw_loss": True,       # Big winner 손실 발생
    "wr_crash": 15.0,      # WIN rate < 15%
    "loss_spike": -3.0,    # LOSS avg < -3.0%
    "pf_collapse": 0.15,   # PF < 0.15
}

# ─── DB 쿼리 ────────────────────────────────────────────────────────────────

def load_live_trades(since_date: str) -> list:
    conn = psycopg2.connect(**DB_CONF)
    cur  = conn.cursor()
    cur.execute("""
        SELECT
            trade_id,
            stock_code,
            stock_name,
            profit_rate,
            exit_reason,
            entry_time,
            exit_time,
            holding_minutes,
            mfe_pct,
            mae_pct,
            exit_context,
            entry_context,
            market_regime,
            exit_category,
            strategy_name
        FROM trades
        WHERE trade_type = 'SELL'
          AND exit_time  >= %s
          AND profit_rate IS NOT NULL
        ORDER BY exit_time ASC
    """, (since_date,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()

    # hold_minutes 보완: exit_time - entry_time
    for r in rows:
        if r["holding_minutes"] is None and r["entry_time"] and r["exit_time"]:
            delta = r["exit_time"] - r["entry_time"]
            r["holding_minutes"] = int(delta.total_seconds() / 60)

        # mfe_pct: DB column → exit_context fallback
        if r["mfe_pct"] is None and r["exit_context"]:
            ctx = r["exit_context"]
            if isinstance(ctx, str):
                try: ctx = json.loads(ctx)
                except: ctx = {}
            r["mfe_pct"] = ctx.get("mfe_pct")

    return rows

# ─── LCL 트리거 분류 ─────────────────────────────────────────────────────────

def detect_lcl_trigger(exit_reason: str | None) -> str | None:
    if not exit_reason:
        return None
    for name, pattern in LCL_TRIGGERS.items():
        if re.search(pattern, exit_reason):
            return name
    return None

def tag_trades(trades: list) -> list:
    for t in trades:
        t["lcl_trigger"] = detect_lcl_trigger(t.get("exit_reason"))
        t["is_win"]      = (t["profit_rate"] or 0) > 0
        t["is_big_win"]  = (t["profit_rate"] or 0) >= BASELINE["top10_thr"]
    return trades

# ─── KPI 계산 ────────────────────────────────────────────────────────────────

def calc_kpi(trades: list) -> dict:
    if not trades: return {}
    wins   = [t for t in trades if t["is_win"]]
    losses = [t for t in trades if not t["is_win"]]
    w_sum  = sum(t["profit_rate"] for t in wins)
    l_sum  = sum(t["profit_rate"] for t in losses)
    n      = len(trades)
    wr     = len(wins) / n * 100
    w_avg  = w_sum / len(wins) if wins else 0
    l_avg  = l_sum / len(losses) if losses else 0
    avg    = sum(t["profit_rate"] for t in trades) / n
    pf     = (-w_sum / l_sum) if l_sum < 0 else float("inf")
    pf_s   = f"{pf:.3f}" if pf != float("inf") else "∞"
    return {"n":n,"wins":len(wins),"losses":len(losses),"wr":wr,
            "w_avg":w_avg,"l_avg":l_avg,"avg":avg,"pf":pf,"pf_s":pf_s}

# ─── 로그 파싱 (execution gap) ────────────────────────────────────────────────

def parse_log_lcl_events(since_date: str) -> list:
    """오늘 이후 로그에서 LCL 트리거 이벤트 추출"""
    events = []
    pattern_re = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - (?:INFO|WARNING) - (.+)")
    lcl_re     = re.compile(r"\[(EARLY_CUT|TIME_STOP(?:_MFE)?|VOL_TIGHT_STOP|MAE_WORSENING)\]")
    exec_re    = re.compile(r"⚡|🚨")
    since_dt   = datetime.strptime(since_date, "%Y-%m-%d")

    log_files = sorted(glob.glob("logs/auto_trading_*.log"))
    for lf in log_files:
        try:
            with open(lf, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = pattern_re.match(line)
                    if not m: continue
                    ts_str, msg = m.group(1), m.group(2)
                    try:
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except: continue
                    if ts < since_dt: continue
                    trig_m = lcl_re.search(msg)
                    if trig_m:
                        events.append({
                            "ts": ts, "trigger": trig_m.group(1),
                            "msg": msg.strip()[:80],
                        })
        except Exception:
            pass
    return events

# ─── 메인 출력 ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=LCL_DEPLOY_DATE)
    parser.add_argument("--days",  type=int, default=None)
    args = parser.parse_args()

    since = args.since
    if args.days:
        since = (date.today() - timedelta(days=args.days)).isoformat()

    print(SEP)
    print(f"  LCL v2.1 Live Validation Report  (since: {since})")
    print(f"  실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)

    # ── 데이터 로드 ───────────────────────────────────────────────────────────
    try:
        trades = load_live_trades(since)
    except Exception as e:
        print(f"\n  [ERROR] DB 연결 실패: {e}")
        sys.exit(1)

    trades = tag_trades(trades)
    n = len(trades)

    # ── Phase 판단 ────────────────────────────────────────────────────────────
    if n == 0:
        print(f"\n  ⏳ 라이브 데이터 없음 ({since} 이후 SELL 0건)")
        print(f"  → LCL v2.1 배포 완료. 첫 거래 발생 대기 중.")
        print(f"  → Phase 1 목표: {PHASE1_N}건 수집 후 트리거 발동 여부 확인")
        _print_baseline()
        _print_fail_conditions()
        print(SEP)
        return

    phase = 1 if n <= PHASE1_N else (2 if n <= PHASE2_N else 3)
    phase_kpi = KPI_PHASE1 if phase == 1 else KPI_PHASE2

    print(f"\n  Phase {phase}  ({n}/{PHASE1_N if phase<=1 else PHASE2_N}건)")
    phase_bar = "█" * n + "░" * max(0, (PHASE1_N if phase==1 else PHASE2_N) - n)
    print(f"  [{phase_bar[:40]}] {n}건\n")

    kpi = calc_kpi(trades)

    # ── [1] KPI vs 기준선 비교 ────────────────────────────────────────────────
    print(f"{SEP2}")
    print("[1] KPI — 실전 vs 백테스트 기준선\n")
    print(f"  {'항목':<13}  {'실전(live)':>10}  {'B안(proxy)':>10}  {'A안(baseline)':>13}  {'상태':>6}")
    print(f"  {'-'*13}  {'-'*10}  {'-'*10}  {'-'*13}  {'-'*6}")

    def chk(live_val, target, higher_better=True, warn_margin=0.05):
        ok = (live_val >= target) if higher_better else (live_val <= target)
        margin_ok = (live_val >= target - warn_margin) if higher_better else (live_val <= target + warn_margin)
        return "✅" if ok else ("⚠ " if margin_ok else "❌")

    items = [
        ("WIN rate",  f"{kpi['wr']:.1f}%",   f"{BASELINE['wr_b']:.1f}%",   f"{BASELINE['wr_a']:.1f}%",
         chk(kpi['wr'], phase_kpi['wr_min'])),
        ("WIN avg",   f"{kpi['w_avg']:+.3f}%",f"1.388%",                    f"1.388%",   "—"),
        ("LOSS avg",  f"{kpi['l_avg']:+.3f}%",f"{BASELINE['l_avg_b']:+.3f}%",f"{BASELINE['l_avg_a']:+.3f}%",
         chk(kpi['l_avg'], phase_kpi['loss_max'], higher_better=True)),
        ("avg PnL",   f"{kpi['avg']:+.3f}%",  "—",                          "—",         "—"),
        ("PF",        f"{kpi['pf_s']}",        f"{BASELINE['pf_b']:.3f}",   f"{BASELINE['pf_a']:.3f}",
         chk(kpi['pf'] if kpi['pf'] != float('inf') else 9.9, phase_kpi['pf_min'])),
    ]
    for label, live, b, a, status in items:
        print(f"  {label:<13}  {live:>10}  {b:>10}  {a:>13}  {status}")

    # ── [2] LCL 트리거 발동 현황 ──────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[2] LCL v2.1 트리거 발동 현황\n")

    lcl_hit  = [t for t in trades if t["lcl_trigger"] is not None]
    lcl_loss = [t for t in trades if t["lcl_trigger"] is not None and not t["is_win"]]
    total_loss = [t for t in trades if not t["is_win"]]
    hit_rate = len(lcl_hit) / n * 100 if n else 0
    eff_rate = len(lcl_hit) / len(total_loss) * 100 if total_loss else 0

    print(f"  전체 트리거 발동: {len(lcl_hit)}/{n}건  ({hit_rate:.1f}%)  "
          f"LOSS 중 발동: {len(lcl_loss)}/{len(total_loss)}건  ({eff_rate:.1f}%)")
    print(f"  실효성 ≥ 70% 기준: {'✅' if eff_rate >= 70 else '⚠  미달'}\n")

    # 트리거별
    print(f"  {'트리거':<18}  {'발동N':>5}  {'WIN':>4}  {'LOSS':>4}  {'avg pnl':>8}  {'기대avg':>8}")
    print(f"  {'-'*18}  {'-'*5}  {'-'*4}  {'-'*4}  {'-'*8}  {'-'*8}")
    expected = {
        "EARLY_CUT":     -0.50, "TIME_STOP":  -0.80,
        "TIME_STOP_MFE": -0.30, "VOL_TIGHT":  -2.50,
        "MAE_WORSENING": -0.30,
    }
    for trig in LCL_TRIGGERS:
        g = [t for t in trades if t["lcl_trigger"] == trig]
        if not g: continue
        gw = [t for t in g if t["is_win"]]
        gl = [t for t in g if not t["is_win"]]
        avg_p = sum(t["profit_rate"] for t in g) / len(g)
        exp   = expected.get(trig, 0.0)
        ok    = "✅" if avg_p >= exp - 0.3 else "⚠"
        print(f"  {trig:<18}  {len(g):>5}  {len(gw):>4}  {len(gl):>4}  {avg_p:>+7.2f}%  {exp:>+7.2f}% {ok}")

    if not lcl_hit:
        print(f"  [EARLY_CUT]      발동 없음  — RSI<38+VWAP3봉+volume decay 조건 대기")
        print(f"  [TIME_STOP]      발동 없음  — 20분+ 손실 지속 조건 대기")
        print(f"  [VOL_TIGHT]      발동 없음  — bdh≥5% 고변동 + pnl≤-2.5% 조건 대기")
        print(f"  [TIME_STOP_MFE]  발동 없음  — MFE<0.3% + 20분+ 조건 대기")
        print(f"  [MAE_WORSENING]  발동 없음  — MFE<0.3% + 신저점 조건 대기")

    # ── [3] Big Winner 보호 ────────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[3] Big Winner 보호\n")

    big_wins  = [t for t in trades if t["is_big_win"]]
    bw_cut    = [t for t in big_wins if t["lcl_trigger"] is not None]
    bw_rate   = (len(big_wins) - len(bw_cut)) / len(big_wins) * 100 if big_wins else 100.0
    bw_status = "✅" if not bw_cut else "❌ FAIL"

    print(f"  Big Winner 기준: pnl ≥ {BASELINE['top10_thr']:.3f}%  (백테스트 Top 10%)")
    print(f"  발생: {len(big_wins)}건  |  LCL 오컷: {len(bw_cut)}건  |  보존율: {bw_rate:.0f}%  {bw_status}")
    if big_wins:
        print()
        print(f"  {'ID':>6}  {'stock':>8}  {'pnl':>8}  {'lcl_trig':>14}  {'exit_reason':<40}")
        for t in sorted(big_wins, key=lambda x: -x["profit_rate"]):
            trig_s = t["lcl_trigger"] or "—"
            reason = str(t.get("exit_reason",""))[:38]
            warn = " ⚠" if t["lcl_trigger"] else ""
            print(f"  {t['trade_id']:>6}  {t['stock_code']:>8}  {t['profit_rate']:>+7.2f}%  "
                  f"{trig_s:>14}  {reason}{warn}")

    # ── [4] WIN Trade 안전성 ───────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[4] WIN Trade 안전성\n")

    wins = [t for t in trades if t["is_win"]]
    win_trig = [t for t in wins if t["lcl_trigger"]]
    print(f"  WIN 전체: {len(wins)}건  LCL 트리거 발동 WIN: {len(win_trig)}건")
    if win_trig:
        print(f"  ⚠  WIN trade에 LCL 트리거 발동 — 조기 컷 가능성 검토 필요:")
        for t in win_trig:
            print(f"    ID={t['trade_id']}  {t['stock_code']}  pnl={t['profit_rate']:+.2f}%  "
                  f"trig={t['lcl_trigger']}  reason={str(t.get('exit_reason',''))[:40]}")
    else:
        print(f"  ✅ WIN trade 오컷 없음 (pnl>0 조건이 구조적으로 보호)")

    # ── [5] LOSS 구조 분석 ─────────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[5] LOSS 구조 분석\n")

    losses = [t for t in trades if not t["is_win"]]
    if losses:
        # exit_reason 패턴 분류
        exit_cats = {}
        for t in losses:
            er = str(t.get("exit_reason",""))
            if t["lcl_trigger"]:
                cat = f"LCL:{t['lcl_trigger']}"
            elif "Hard Stop" in er or "[HARD_STOP]" in er:
                cat = "HARD_STOP"
            elif "Early Failure" in er or "Early_Failure" in er:
                cat = "EF"
            elif "구조 손절" in er:
                cat = "STRUCT_STOP"
            elif "15:00" in er or "시간 기반" in er:
                cat = "TIME_EXIT"
            elif "오버나이트" in er:
                cat = "OVERNIGHT"
            else:
                cat = "OTHER"
            exit_cats.setdefault(cat, []).append(t)

        print(f"  {'exit_cat':<22}  {'N':>3}  {'avg pnl':>8}  {'min':>8}  비중")
        print(f"  {'-'*22}  {'-'*3}  {'-'*8}  {'-'*8}  {'-'*6}")
        total_loss_sum = sum(t["profit_rate"] for t in losses)
        for cat in sorted(exit_cats, key=lambda c: sum(t["profit_rate"] for t in exit_cats[c])):
            g = exit_cats[cat]
            avg_p = sum(t["profit_rate"] for t in g) / len(g)
            min_p = min(t["profit_rate"] for t in g)
            pct   = sum(t["profit_rate"] for t in g) / total_loss_sum * 100
            print(f"  {cat:<22}  {len(g):>3}  {avg_p:>+7.2f}%  {min_p:>+7.2f}%  {pct:>+5.1f}%")

        # LOSS 분포
        l_vals = [t["profit_rate"] for t in losses]
        print(f"\n  tail risk 현황:")
        for thr in [-1.0, -1.5, -2.0, -3.0]:
            cnt = sum(1 for v in l_vals if v <= thr)
            pct = cnt / len(l_vals) * 100 if l_vals else 0
            print(f"    ≤{thr:.1f}%: {cnt}건 ({pct:.1f}%)")

    # ── [6] MFE/MAE 추적 현황 ─────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[6] MFE/MAE 추적 현황\n")

    mfe_notna = [t for t in trades if t.get("mfe_pct") is not None]
    mae_notna = [t for t in trades if t.get("mae_pct") is not None]
    print(f"  MFE 데이터: {len(mfe_notna)}/{n}건")
    print(f"  MAE 데이터: {len(mae_notna)}/{n}건")
    print(f"  → [MAE_WORSENING] / [TIME_STOP_MFE] 발동 가능 여부: "
          f"{'✅ MFE 있음' if mfe_notna else '⚠  MFE null — 트리거 발동 불가'}")
    print()
    if mfe_notna:
        mfe_vals = [t["mfe_pct"] for t in mfe_notna]
        print(f"  MFE 통계: avg={sum(mfe_vals)/len(mfe_vals):.3f}%  "
              f"min={min(mfe_vals):.3f}%  max={max(mfe_vals):.3f}%")
        low_mfe = [t for t in mfe_notna if t["mfe_pct"] < 0.3 and not t["is_win"]]
        print(f"  MFE<0.3% LOSS 거래: {len(low_mfe)}건 → [MAE_WORSENING] 잠재 대상")
    else:
        print("  → main_auto_trading.py가 position['mfe_pct']를 DB에 기록하면 활성화")

    # ── [7] Execution Gap 분석 ────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[7] Execution Gap 분석 (로그 기반)\n")

    log_events = parse_log_lcl_events(since)
    if log_events:
        print(f"  로그에서 LCL 이벤트 감지: {len(log_events)}건")
        for ev in log_events[:10]:
            print(f"    {ev['ts'].strftime('%H:%M:%S')}  [{ev['trigger']}]  {ev['msg'][:55]}")
        if len(log_events) > 10:
            print(f"    ... 외 {len(log_events)-10}건")

        # DB exit_time과 log timestamp 비교 (execution gap)
        for ev in log_events[:5]:
            # 같은 시간대에 LCL 트리거된 거래 찾기
            near_trades = [
                t for t in trades if t["lcl_trigger"]
                and t["exit_time"]
                and abs((t["exit_time"] - ev["ts"]).total_seconds()) < 300
            ]
            for nt in near_trades:
                gap = (nt["exit_time"] - ev["ts"]).total_seconds()
                print(f"    → 체결 gap: {gap:.0f}초  (트리거→DB exit_time)")
    else:
        print(f"  로그 이벤트 없음 ({since} 이후)")
        print(f"  → 트리거 발동 시 [EARLY_CUT]/[TIME_STOP] 로그 라인이 자동 기록됨")

    # ── [8] FAIL 조건 검사 ────────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[8] FAIL 조건 자동 감지\n")

    fail_flags = []
    if bw_cut:
        fail_flags.append(f"❌ Big winner 오컷 {len(bw_cut)}건 — 즉시 LCL 점검")
    if n >= 10 and kpi["wr"] < FAIL_CONDITIONS["wr_crash"]:
        fail_flags.append(f"❌ WIN rate {kpi['wr']:.1f}% < 15% — 구조 붕괴 경보")
    if n >= 10 and kpi["l_avg"] < FAIL_CONDITIONS["loss_spike"]:
        fail_flags.append(f"❌ LOSS avg {kpi['l_avg']:+.3f}% < -3.0% — 손실 급증 경보")
    if n >= 10 and isinstance(kpi["pf"], float) and kpi["pf"] != float("inf") and kpi["pf"] < FAIL_CONDITIONS["pf_collapse"]:
        fail_flags.append(f"❌ PF {kpi['pf_s']} < 0.15 — PF 붕괴 경보")

    if fail_flags:
        for f in fail_flags:
            print(f"  {f}")
        print()
        print("  ▶ 액션: LCL 비활성화(enabled: false) → 원인 파악 → 재활성화")
    else:
        print(f"  ✅ FAIL 조건 없음 (n={n}건, Phase {phase})")

    # ── [9] Phase별 PASS/FAIL ─────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print(f"[9] Phase {phase} PASS / FAIL 판정\n")

    if n < 5:
        print(f"  ⏳ 데이터 부족 ({n}건 < 5건) — 판정 보류")
    else:
        crits = [
            ("WIN rate ≥ {:.0f}%".format(phase_kpi["wr_min"]),
             kpi["wr"] >= phase_kpi["wr_min"],
             f"{kpi['wr']:.1f}%"),
            ("LOSS avg ≤ {:.1f}%".format(phase_kpi["loss_max"]),
             kpi["l_avg"] >= phase_kpi["loss_max"] if kpi["losses"] else True,
             f"{kpi['l_avg']:+.3f}%"),
            ("PF ≥ {:.2f}".format(phase_kpi["pf_min"]),
             kpi["pf"] >= phase_kpi["pf_min"],
             kpi["pf_s"]),
            ("Big winner 오컷 없음",   not bw_cut,   f"{len(bw_cut)}건 오컷"),
            ("WIN trade 오컷 없음",    not win_trig,  f"{len(win_trig)}건 오컷"),
        ]

        all_ok = True
        print(f"  {'기준':<30}  {'판정':>8}  상세")
        print(f"  {'-'*30}  {'-'*8}  {'-'*20}")
        for name, ok, detail in crits:
            sym = "✅ PASS" if ok else "❌ FAIL"
            if not ok: all_ok = False
            print(f"  {name:<30}  {sym:>8}  {detail}")

        print()
        if all_ok:
            if n >= PHASE2_N:
                verdict = "✅ Phase 2 PASS → 구조 확정, LCL v2.1 FINAL SYSTEM"
                action  = "→ 정상 운영 유지, 50건 후 파라미터 미세조정 검토"
            elif n >= PHASE1_N:
                verdict = "✅ Phase 1 PASS → Phase 2 진입"
                action  = f"→ {PHASE2_N - n}건 추가 수집 후 최종 검증"
            else:
                verdict = f"✅ Phase 1 진행 중 ({n}/{PHASE1_N}건)"
                action  = f"→ {PHASE1_N - n}건 추가 수집 대기"
        else:
            verdict = f"❌ Phase {phase} FAIL → 트리거별 원인 분석"
            action  = "→ 개별 FAIL 항목 점검, 필요 시 LCL 파라미터 조정"

        print(f"  ▶ {verdict}")
        print(f"  ▶ {action}")

    # ── [10] 다음 액션 ────────────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("[10] 다음 액션\n")

    remaining_1 = max(0, PHASE1_N - n)
    remaining_2 = max(0, PHASE2_N - n)

    milestones = [
        (f"{PHASE1_N}건 달성 (현재 {n}건, {remaining_1}건 남음)",
         n >= PHASE1_N,
         "[EARLY_CUT]/[TIME_STOP]/[VOL_TIGHT] 발동 여부 + MFE<0.3% 패턴 확인"),
        (f"{PHASE2_N}건 달성 ({remaining_2}건 남음)",
         n >= PHASE2_N,
         "LOSS avg 실측 vs -1.3% 목표 비교 → LCL v2.1 파라미터 확정"),
        ("MAE_WORSENING 발동 확인",
         any(t["lcl_trigger"] == "MAE_WORSENING" for t in trades),
         "position['mfe_pct'] tracking 활성 여부 확인 → MFE DB 기록 검토"),
        ("EARLY_CUT 발동 확인",
         any(t["lcl_trigger"] == "EARLY_CUT" for t in trades),
         "RSI<38+slope+VWAP3봉+volume decay 조건 실전 발동 → avg pnl vs -0.5% 목표"),
    ]

    for name, done, note in milestones:
        status = "✅" if done else "⏳"
        print(f"  {status} {name}")
        print(f"      {note}")
    print(SEP)


def _print_baseline():
    print(f"\n{SEP2}")
    print("  [기준선 — 백테스트 b2, 94건]\n")
    print(f"  A안 (G3+Stage A):    WR={BASELINE['wr_a']:.1f}%  LOSS={BASELINE['l_avg_a']:+.3f}%  PF={BASELINE['pf_a']:.3f}")
    print(f"  B안 (proxy sim):     WR={BASELINE['wr_b']:.1f}%  LOSS={BASELINE['l_avg_b']:+.3f}%  PF={BASELINE['pf_b']:.3f}")
    print(f"  B+ (이론 full):      WR={BASELINE['wr_b']:.1f}%  LOSS={BASELINE['l_avg_bp']:+.3f}%  PF={BASELINE['pf_bp']:.3f}")
    print(f"  Big Winner (Top 10%): 10/10 = 100%  기준 pnl ≥ {BASELINE['top10_thr']:.3f}%")


def _print_fail_conditions():
    print(f"\n{SEP2}")
    print("  [즉시 알람 FAIL 조건]\n")
    print(f"  ❌ Big winner 오컷 → 즉시 LCL 비활성화(enabled: false)")
    print(f"  ❌ WIN rate < {FAIL_CONDITIONS['wr_crash']:.0f}% (n≥10건)")
    print(f"  ❌ LOSS avg < {FAIL_CONDITIONS['loss_spike']:+.1f}% (n≥10건)")
    print(f"  ❌ PF < {FAIL_CONDITIONS['pf_collapse']:.2f} (n≥10건)")
    print(f"\n  [LCL 비활성화 방법]")
    print(f"    config/strategy_hybrid.yaml:")
    print(f"      risk_control.loss_control_layer.enabled: false")


if __name__ == "__main__":
    main()
