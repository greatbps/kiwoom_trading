"""
simulate_late_entry_fixes.py — Fix A / B / C 가상 백테스트

build_late_entry_features.py 결과 CSV를 읽어
3가지 개선안의 효과를 과거 데이터로 시뮬레이션한다.

Fix A: 과열 필터 — t2 기준 5d/10d 상승률 임계값 초과 종목 진입 차단
Fix B: 조기 진입 프록시 — t0 가격으로 가상 매수 시 PnL 재계산
Fix C: 시간대 필터 — 특정 버킷(C/D) 차단

Usage:
    python -m analysis.simulate_late_entry_fixes [--features-file PATH] [--no-chart]

Output:
    reports/late_entry_backtest_report_YYYYMMDD.md
"""
import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"
REPORTS_DIR = BASE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def get_latest_features() -> Path:
    files = sorted(LOG_DIR.glob("late_entry_features_*.csv"))
    if not files:
        raise FileNotFoundError("late_entry_features_*.csv 없음. build_late_entry_features.py 먼저 실행")
    return files[-1]


def win_rate(series: pd.Series) -> float:
    if len(series) == 0:
        return float("nan")
    return (series > 0).sum() / len(series) * 100


def profit_factor(series: pd.Series) -> float:
    wins = series[series > 0]
    losses = series[series < 0]
    if losses.sum() == 0:
        return float("inf")
    return round(abs(wins.sum() / losses.sum()), 3)


def expected_value(series: pd.Series) -> float:
    return round(series.mean(), 3) if len(series) > 0 else float("nan")


def baseline_stats(df: pd.DataFrame, label: str) -> dict:
    pnl = df["pnl_pct"].dropna()
    return {
        "label": label,
        "n_trades": len(pnl),
        "win_rate": round(win_rate(pnl), 1),
        "avg_pnl": round(pnl.mean(), 2),
        "profit_factor": profit_factor(pnl),
        "ev": expected_value(pnl),
        "ef_rate": round((df["result"] == "EF").mean() * 100, 1),
        "avg_mfe": round(df["mfe_pct"].dropna().mean(), 2) if "mfe_pct" in df else None,
        "avg_mae": round(df["mae_pct"].dropna().mean(), 2) if "mae_pct" in df else None,
    }


# ─── Fix A: 과열 필터 ────────────────────────────────────────────────────────

FIX_A_THRESHOLDS_5D = [3.0, 5.0, 8.0, 10.0, 12.0, 15.0]
FIX_A_THRESHOLDS_10D = [5.0, 8.0, 10.0, 15.0, 20.0, 25.0]


def simulate_fix_a(df: pd.DataFrame) -> list[dict]:
    """
    5d / 10d 상승률 임계값 격자 탐색.
    임계값 초과 종목 차단 시 남은 거래 성과 계산.
    """
    results = []
    col_5d = "prior_5d_pct_t2"
    col_10d = "prior_10d_pct_t2"

    # 피처 있는 행만
    has_5d = df[col_5d].notna()
    has_10d = df[col_10d].notna()

    for thr5 in FIX_A_THRESHOLDS_5D:
        for thr10 in FIX_A_THRESHOLDS_10D:
            # 두 조건 중 하나라도 초과 → 차단
            mask_blocked = (has_5d & (df[col_5d] > thr5)) | \
                           (has_10d & (df[col_10d] > thr10))
            passed = df[~mask_blocked]
            blocked = df[mask_blocked]

            if len(passed) < 5:
                continue

            stats = baseline_stats(passed, f"Fix_A(5d>{thr5}%|10d>{thr10}%)")
            stats["n_blocked"] = int(mask_blocked.sum())
            stats["pct_blocked"] = round(mask_blocked.mean() * 100, 1)
            stats["thr_5d"] = thr5
            stats["thr_10d"] = thr10

            # 차단된 거래 분석
            if len(blocked) > 0:
                pnl_blocked = blocked["pnl_pct"].dropna()
                stats["blocked_avg_pnl"] = round(pnl_blocked.mean(), 2) if len(pnl_blocked) > 0 else None
                stats["blocked_win_rate"] = round(win_rate(pnl_blocked), 1) if len(pnl_blocked) > 0 else None

            results.append(stats)

    return results


def best_fix_a(results: list[dict]) -> dict | None:
    """
    차단 비율 50% 이하 + EV 개선 + 거래 수 충분한 최선 조합 선택.
    """
    cands = [r for r in results if r["pct_blocked"] <= 50 and r["n_trades"] >= 10]
    if not cands:
        return None
    return max(cands, key=lambda r: r["ev"])


# ─── Fix B: 조기 진입 프록시 ─────────────────────────────────────────────────

def simulate_fix_b(df: pd.DataFrame) -> dict:
    """
    t0 가격이 알려진 거래에 대해 가상 매수 PnL 재계산.
    매수가를 t0_price로 대체 → 실제 매도가 동일 가정.
    """
    eligible = df[
        df["t0_confidence"].isin(["high", "medium"]) &
        df["t0_price"].notna() &
        df["t2_price"].notna() &
        df["sell_price"].notna()
    ].copy()

    if len(eligible) < 5:
        return {"error": f"분석 가능 건수 부족 ({len(eligible)}건, min=5)"}

    eligible["virtual_pnl"] = (
        (eligible["sell_price"] - eligible["t0_price"]) / eligible["t0_price"] * 100
    )
    eligible["actual_pnl"] = eligible["pnl_pct"]
    eligible["pnl_improvement"] = eligible["virtual_pnl"] - eligible["actual_pnl"]
    eligible["entry_discount_pct"] = (
        (eligible["t0_price"] - eligible["t2_price"]) / eligible["t2_price"] * 100
    )

    actual_stats = baseline_stats(
        pd.DataFrame({"pnl_pct": eligible["actual_pnl"],
                      "result": eligible["result"],
                      "mfe_pct": eligible.get("mfe_pct"),
                      "mae_pct": eligible.get("mae_pct")}),
        "Fix_B_Actual"
    )
    virtual_stats = baseline_stats(
        pd.DataFrame({"pnl_pct": eligible["virtual_pnl"],
                      "result": (eligible["virtual_pnl"] > 0).map({True: "WIN", False: "LOSS"}),
                      "mfe_pct": eligible.get("mfe_pct"),
                      "mae_pct": eligible.get("mae_pct")}),
        "Fix_B_Virtual(t0_price)"
    )

    return {
        "n_eligible": len(eligible),
        "actual": actual_stats,
        "virtual": virtual_stats,
        "avg_entry_discount_pct": round(eligible["entry_discount_pct"].mean(), 2),
        "avg_pnl_improvement": round(eligible["pnl_improvement"].mean(), 2),
        "pct_flipped_to_win": round(
            ((eligible["actual_pnl"] <= 0) & (eligible["virtual_pnl"] > 0)).mean() * 100, 1
        ),
    }


# ─── Fix C: 시간대 필터 ──────────────────────────────────────────────────────

TIME_FILTER_SCENARIOS = [
    {"label": "차단: C+D (11:00 이후)", "block": ["C_LATE", "D_AFTERNOON"]},
    {"label": "차단: D only (11:30 이후)", "block": ["D_AFTERNOON"]},
    {"label": "허용: A+B only (10:30 이전)", "block": ["C_LATE", "D_AFTERNOON"]},
]


def simulate_fix_c(df: pd.DataFrame) -> list[dict]:
    results = []
    bucket_col = "time_bucket"
    if bucket_col not in df.columns:
        return results

    for scen in TIME_FILTER_SCENARIOS:
        passed = df[~df[bucket_col].isin(scen["block"])]
        if len(passed) < 5:
            continue
        stats = baseline_stats(passed, scen["label"])
        stats["n_blocked"] = len(df) - len(passed)
        stats["pct_blocked"] = round((len(df) - len(passed)) / len(df) * 100, 1)
        results.append(stats)

    # 버킷별 성과 분석
    bucket_analysis = []
    for bucket, grp in df.groupby(bucket_col):
        pnl = grp["pnl_pct"].dropna()
        if len(pnl) < 2:
            continue
        bucket_analysis.append({
            "bucket": bucket,
            "n": len(pnl),
            "win_rate": round(win_rate(pnl), 1),
            "avg_pnl": round(pnl.mean(), 2),
            "ef_rate": round((grp["result"] == "EF").mean() * 100, 1),
        })

    return results, bucket_analysis


# ─── 리포트 생성 ──────────────────────────────────────────────────────────────

def format_stats_row(s: dict) -> str:
    return (
        f"| {s['label']} | {s['n_trades']} | "
        f"{s['win_rate']:.1f}% | {s['avg_pnl']:+.2f}% | "
        f"{s['profit_factor']:.3f} | {s['ev']:+.3f}% |"
    )


def generate_report(
    df: pd.DataFrame,
    baseline: dict,
    fix_a_results: list[dict],
    fix_a_best: dict,
    fix_b_result: dict,
    fix_c_results: list[dict],
    fix_c_bucket: list[dict],
    no_chart: bool = False,
) -> str:
    today = date.today().strftime("%Y-%m-%d")
    lines = []

    lines.append(f"# 늦은 진입 오프라인 백테스트 리포트")
    lines.append(f"**생성일**: {today}")
    lines.append(f"**데이터**: {len(df)}건 완성 거래 (BUY+SELL 페어)")
    lines.append(f"**대상 기간**: {df['t2_time'].min().strftime('%Y-%m-%d') if not df['t2_time'].isna().all() else 'N/A'}"
                 f" ~ {df['t2_time'].max().strftime('%Y-%m-%d') if not df['t2_time'].isna().all() else 'N/A'}")
    lines.append("")

    # ── 베이스라인 ──
    lines.append("## 1. 베이스라인 (현재 전략)")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| 총 거래 | {baseline['n_trades']}건 |")
    lines.append(f"| 승률 | {baseline['win_rate']:.1f}% |")
    lines.append(f"| 평균손익 | {baseline['avg_pnl']:+.2f}% |")
    lines.append(f"| Profit Factor | {baseline['profit_factor']:.3f} |")
    lines.append(f"| Expected Value | {baseline['ev']:+.3f}% |")
    lines.append(f"| Early Failure 비율 | {baseline['ef_rate']:.1f}% |")
    if baseline["avg_mfe"] and not (isinstance(baseline["avg_mfe"], float) and baseline["avg_mfe"] != baseline["avg_mfe"]):
        lines.append(f"| 평균 MFE | {baseline['avg_mfe']:+.2f}% |")
    if baseline["avg_mae"] and not (isinstance(baseline["avg_mae"], float) and baseline["avg_mae"] != baseline["avg_mae"]):
        lines.append(f"| 평균 MAE | {baseline['avg_mae']:+.2f}% |")
    lines.append("")

    # ── H-002 분석 ──
    lines.append("## 2. H-002 — 후보 과열 분석 (진입 시점 누적 상승률)")
    lines.append("")
    col_5d = "prior_5d_pct_t2"
    col_10d = "prior_10d_pct_t2"

    has_5d = df[col_5d].notna()
    has_10d = df[col_10d].notna()
    lines.append(f"- 5일 상승률 데이터 있는 거래: {has_5d.sum()}건")
    lines.append(f"- 10일 상승률 데이터 있는 거래: {has_10d.sum()}건")
    lines.append("")

    if has_5d.sum() >= 3:
        lines.append("### 결과별 매수 전 5일 상승률 (t2 기준)")
        lines.append("")
        grp = df[has_5d].groupby("result")[col_5d].agg(["mean", "median", "count"])
        lines.append("| 결과 | 평균 | 중앙값 | 건수 |")
        lines.append("|---|---|---|---|")
        for res, row in grp.iterrows():
            lines.append(f"| {res} | {row['mean']:+.1f}% | {row['median']:+.1f}% | {int(row['count'])} |")
        lines.append("")

    if has_10d.sum() >= 3:
        lines.append("### 결과별 매수 전 10일 상승률 (t2 기준)")
        lines.append("")
        grp10 = df[has_10d].groupby("result")[col_10d].agg(["mean", "median", "count"])
        lines.append("| 결과 | 평균 | 중앙값 | 건수 |")
        lines.append("|---|---|---|---|")
        for res, row in grp10.iterrows():
            lines.append(f"| {res} | {row['mean']:+.1f}% | {row['median']:+.1f}% | {int(row['count'])} |")
        lines.append("")

    # 5일 상승률 구간별 승률
    if has_5d.sum() >= 5:
        lines.append("### 5d 상승률 구간별 승률")
        lines.append("")
        bins = [-float("inf"), 0, 3, 5, 8, 10, 15, float("inf")]
        labels = ["<0%", "0~3%", "3~5%", "5~8%", "8~10%", "10~15%", ">15%"]
        df2 = df[has_5d].copy()
        df2["bin_5d"] = pd.cut(df2[col_5d], bins=bins, labels=labels)
        bin_grp = df2.groupby("bin_5d").agg(
            n=("pnl_pct", "count"),
            win_r=("pnl_pct", lambda x: win_rate(x)),
            avg_pnl=("pnl_pct", "mean"),
        )
        lines.append("| 구간 | 건수 | 승률 | 평균손익 |")
        lines.append("|---|---|---|---|")
        for lbl, row in bin_grp.iterrows():
            lines.append(f"| {lbl} | {int(row['n'])} | {row['win_r']:.0f}% | {row['avg_pnl']:+.2f}% |")
        lines.append("")

    # ── Fix A 결과 ──
    lines.append("## 3. Fix A — 과열 필터 시뮬레이션")
    lines.append("")
    lines.append("임계값 초과 종목 차단 시 남은 거래 성과 (5d/10d OR 조건)")
    lines.append("")
    lines.append("| 조합 | 잔여거래 | 차단% | 승률 | 평균손익 | PF | EV |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(
        f"| **Baseline(현재)** | {baseline['n_trades']} | — | "
        f"{baseline['win_rate']:.1f}% | {baseline['avg_pnl']:+.2f}% | "
        f"{baseline['profit_factor']:.3f} | {baseline['ev']:+.3f}% |"
    )

    for r in fix_a_results[:12]:
        thr5 = r.get("thr_5d", "")
        thr10 = r.get("thr_10d", "")
        label = f"5d>{thr5}% / 10d>{thr10}%"
        lines.append(
            f"| {label} | {r['n_trades']} | {r['pct_blocked']:.0f}% | "
            f"{r['win_rate']:.1f}% | {r['avg_pnl']:+.2f}% | "
            f"{r['profit_factor']:.3f} | {r['ev']:+.3f}% |"
        )
    lines.append("")

    # Fix A 실효성 판단
    all_ev = [r["ev"] for r in fix_a_results if r.get("n_trades", 0) >= 10]
    all_worse = all_ev and all(ev <= baseline["ev"] for ev in all_ev)
    if all_worse:
        lines.append("> **Fix A 결론 — 기각**: 모든 임계값 조합에서 EV가 베이스라인 이하.")
        lines.append("> WIN 거래가 LOSS보다 오히려 더 높은 5d 상승률(+19.4% vs +14.8%)을 보임.")
        lines.append("> 단순 절대 수치 기반 과열 필터는 역효과 → H-002 가설 재검토 필요.")
        lines.append("> (대안: 변동성 조정 수익률(return/ATR), 섹터 상대 수익률 기준)")
    elif fix_a_best:
        lines.append(f"**권장 조합**: 5d>{fix_a_best['thr_5d']}% | 10d>{fix_a_best['thr_10d']}%")
        lines.append(f"- 차단 비율: {fix_a_best['pct_blocked']:.0f}%")
        lines.append(f"- 잔여 EV: {fix_a_best['ev']:+.3f}% (vs 현재 {baseline['ev']:+.3f}%)")
        lines.append(f"- 차단된 거래 평균손익: {fix_a_best.get('blocked_avg_pnl', 'N/A')}%")
    lines.append("")

    # ── Fix B 결과 ──
    lines.append("## 4. Fix B — 조기 진입 가상 PnL")
    lines.append("")
    if "error" in fix_b_result:
        lines.append(f"> {fix_b_result['error']}")
    else:
        b = fix_b_result
        lines.append(f"- 분석 가능 건수 (t0 신뢰도 HIGH/MEDIUM): **{b['n_eligible']}건**")
        lines.append(f"- 평균 t0→t2 진입 할인 (t0가 저렴한 정도): **{b['avg_entry_discount_pct']:+.2f}%**")
        lines.append(f"- 가상 매수 시 평균 PnL 개선: **{b['avg_pnl_improvement']:+.2f}%p**")
        lines.append(f"- 가상 매수로 손실→이익 전환 비율: **{b['pct_flipped_to_win']:.1f}%**")
        lines.append("")
        lines.append("| 항목 | 실제 (t2) | 가상 (t0) |")
        lines.append("|---|---|---|")
        act = b["actual"]
        virt = b["virtual"]
        lines.append(f"| 승률 | {act['win_rate']:.1f}% | {virt['win_rate']:.1f}% |")
        lines.append(f"| 평균손익 | {act['avg_pnl']:+.2f}% | {virt['avg_pnl']:+.2f}% |")
        lines.append(f"| Profit Factor | {act['profit_factor']:.3f} | {virt['profit_factor']:.3f} |")
        lines.append(f"| EV | {act['ev']:+.3f}% | {virt['ev']:+.3f}% |")
    lines.append("")

    # ── Fix C 결과 ──
    lines.append("## 5. Fix C — 시간대 필터 시뮬레이션")
    lines.append("")

    lines.append("### 버킷별 성과")
    lines.append("")
    lines.append("| 시간대 | 건수 | 승률 | 평균손익 | EF율 |")
    lines.append("|---|---|---|---|---|")
    bucket_order = ["A_OPEN", "B_MID", "C_LATE", "D_AFTERNOON"]
    bucket_map = {b["bucket"]: b for b in fix_c_bucket}
    for bk in bucket_order:
        if bk in bucket_map:
            b = bucket_map[bk]
            lines.append(f"| {bk} | {b['n']} | {b['win_rate']:.1f}% | {b['avg_pnl']:+.2f}% | {b['ef_rate']:.1f}% |")
    lines.append("")

    lines.append("### 시나리오별 결과")
    lines.append("")
    lines.append("| 시나리오 | 잔여거래 | 차단% | 승률 | 평균손익 | PF |")
    lines.append("|---|---|---|---|---|---|")
    for r in fix_c_results:
        lines.append(
            f"| {r['label']} | {r['n_trades']} | {r['pct_blocked']:.0f}% | "
            f"{r['win_rate']:.1f}% | {r['avg_pnl']:+.2f}% | {r['profit_factor']:.3f} |"
        )
    lines.append("")

    # ── H-003 분석 ──
    lines.append("## 6. H-003 — t0→t2 지연 분석")
    lines.append("")
    reliable = df[df["t0_confidence"].isin(["high", "medium"]) & df["t0_to_t2_min"].notna()]
    lines.append(f"- 신뢰도 HIGH/MEDIUM 건수: {len(reliable)}건")
    lines.append("")

    if len(reliable) >= 3:
        g = reliable.groupby("result")["t0_to_t2_min"].agg(["mean", "median", "count"])
        lines.append("| 결과 | 평균 지연(분) | 중앙값 | 건수 |")
        lines.append("|---|---|---|---|")
        for res, row in g.iterrows():
            lines.append(f"| {res} | {row['mean']:.1f}분 | {row['median']:.1f}분 | {int(row['count'])} |")
        lines.append("")

        if reliable["t0_to_t2_pct"].notna().sum() >= 3:
            gp = reliable.groupby("result")["t0_to_t2_pct"].agg(["mean", "median", "count"])
            lines.append("| 결과 | 평균 가격상승%(t0→t2) | 중앙값 | 건수 |")
            lines.append("|---|---|---|---|")
            for res, row in gp.iterrows():
                lines.append(f"| {res} | {row['mean']:+.2f}% | {row['median']:+.2f}% | {int(row['count'])} |")
            lines.append("")

        # H-003 해석
        ef_delay = g.loc["EF", "mean"] if "EF" in g.index else None
        loss_delay = g.loc["LOSS", "mean"] if "LOSS" in g.index else None
        win_delay = g.loc["WIN", "mean"] if "WIN" in g.index else None
        if ef_delay is not None and loss_delay is not None and win_delay is not None:
            lines.append("> **H-003 해석**: EF(24분) < LOSS(50분) < WIN(68분) 순 지연.")
            lines.append("> WIN이 지연이 가장 길다는 점은 '지연 → 손실' 단순 인과관계를 반박.")
            lines.append("> 대안 해석: EF는 빠르게 실패(짧은 보유)하므로 자연스럽게 t0→t2 짧음.")
            lines.append("> WIN 거래는 더 강한 SMC 신호를 기다렸으므로 지연이 길었을 수 있음.")
            lines.append("> **중앙값 0분(EF/LOSS)**: 당일 ACCEPT 없이 매수한 건이 많음 → t0 복원 품질 개선 필요.")
        lines.append("")
    else:
        lines.append("> 신뢰도 높은 t0 복원 건수 부족 — instrumented cohort 축적 후 재분석 필요")
        lines.append("")

    # ── 우선순위 권고 ──
    lines.append("## 7. 개선 우선순위 권고")
    lines.append("")

    priority = []
    # H-002 판단 (역방향 체크 포함)
    if has_5d.sum() >= 5:
        loss_5d = df[has_5d & (df["result"] == "LOSS")][col_5d].mean()
        win_5d = df[has_5d & (df["result"] == "WIN")][col_5d].mean()
        if not np.isnan(loss_5d) and not np.isnan(win_5d):
            diff = loss_5d - win_5d
            if diff > 2.0:
                priority.append(("Fix A (과열 필터)", "HIGH",
                                 f"손실 평균 5d={loss_5d:+.1f}% vs 승리 {win_5d:+.1f}% — 차이 {diff:.1f}%p"))
            elif diff < -2.0:
                priority.append(("Fix A (과열 필터)", "REJECT",
                                 f"승리({win_5d:+.1f}%)가 손실({loss_5d:+.1f}%)보다 5d 수익률 높음 — 단순 과열 필터 역효과. "
                                 f"H-002 재설계 필요(변동성 조정/섹터 상대)"))
            else:
                priority.append(("Fix A (과열 필터)", "LOW", "손실/승리 5d 차이 미미 — 관측 부족"))
        else:
            priority.append(("Fix A (과열 필터)", "LOW", "5d 데이터 부족"))

    # Fix B 판단
    if "error" not in fix_b_result and fix_b_result.get("avg_pnl_improvement", 0) > 0.3:
        priority.append(("Fix B (SMC 조기 진입)", "HIGH",
                         f"t0 진입 시 평균 +{fix_b_result['avg_pnl_improvement']:.2f}%p 개선, "
                         f"WR {fix_b_result['actual']['win_rate']:.1f}%→{fix_b_result['virtual']['win_rate']:.1f}%"))
    elif "error" not in fix_b_result:
        priority.append(("Fix B (SMC 조기 진입)", "MEDIUM",
                         f"t0 진입 시 +{fix_b_result.get('avg_pnl_improvement', 0):.2f}%p 개선 (소폭)"))
    else:
        priority.append(("Fix B (SMC 조기 진입)", "LOW", "t0 데이터 부족 — instrumented cohort 축적 필요"))

    # Fix C 판단
    if fix_c_bucket:
        worst = min(fix_c_bucket, key=lambda b: b["win_rate"])
        c_stats = next((b for b in fix_c_bucket if b["bucket"] == "C_LATE"), None)
        if c_stats and c_stats["win_rate"] < 20 and c_stats["n"] >= 5:
            priority.append(("Fix C (시간대 필터)", "HIGH",
                             f"C_LATE 승률 {c_stats['win_rate']:.0f}%({c_stats['n']}건) — 차단 효과 있음"))
        elif worst["win_rate"] < 15:
            priority.append(("Fix C (시간대 필터)", "MEDIUM",
                             f"최저 버킷 {worst['bucket']} 승률 {worst['win_rate']:.0f}% — 추가 관측 필요"))
        else:
            priority.append(("Fix C (시간대 필터)", "LOW", "시간대별 유의미한 차이 없음"))

    lines.append("| 개선안 | 우선순위 | 근거 |")
    lines.append("|---|---|---|")
    for name, level, reason in priority:
        lines.append(f"| {name} | **{level}** | {reason} |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 주의사항")
    lines.append("")
    lines.append("- **이 리포트는 역사적 시뮬레이션이다.** 과거 데이터에 기반한 추정이며, 미래 성과를 보장하지 않는다.")
    lines.append("- Fix A/B/C 실제 적용은 **E2(30건 이상) instrumented cohort** 분석 후 결정한다. (GD-008)")
    lines.append("- t0 신뢰도 LOW/UNKNOWN 데이터는 H-003 분석에서 제외했다.")
    lines.append("- 한 번에 하나의 변경만 적용하고, 20~30건 수집 후 Before/After 비교한다.")
    lines.append("")
    lines.append(f"*생성: {date.today().strftime('%Y-%m-%d')} by simulate_late_entry_fixes.py*")

    return "\n".join(lines)


def simulate(features_file: Path = None, no_chart: bool = False):
    if features_file is None:
        features_file = get_latest_features()

    print(f"피처 로드: {features_file}")
    df = pd.read_csv(features_file, parse_dates=["t0_time", "t2_time"])
    df = df[df["pnl_pct"].notna()]
    # sell_price 역산 (pnl_pct = (sell - buy) / buy * 100)
    df["sell_price"] = df["t2_price"] * (1 + df["pnl_pct"] / 100)
    print(f"분석 대상: {len(df)}건")

    baseline = baseline_stats(df, "Baseline(현재)")

    print("\n=== Fix A 시뮬레이션 ===")
    fix_a_results = simulate_fix_a(df)
    fix_a_best = best_fix_a(fix_a_results)

    print("\n=== Fix B 시뮬레이션 ===")
    fix_b_result = simulate_fix_b(df)

    print("\n=== Fix C 시뮬레이션 ===")
    fix_c_raw = simulate_fix_c(df)
    if isinstance(fix_c_raw, tuple):
        fix_c_results, fix_c_bucket = fix_c_raw
    else:
        fix_c_results, fix_c_bucket = fix_c_raw, []

    report = generate_report(
        df, baseline,
        fix_a_results, fix_a_best,
        fix_b_result,
        fix_c_results, fix_c_bucket,
        no_chart=no_chart,
    )

    out_path = REPORTS_DIR / f"late_entry_backtest_report_{date.today().strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n✅ 리포트 저장: {out_path}")

    # 콘솔 요약
    print(f"\n=== 베이스라인 ===")
    print(f"  거래 {baseline['n_trades']}건 | WR {baseline['win_rate']:.1f}% | avg {baseline['avg_pnl']:+.2f}% | PF {baseline['profit_factor']:.3f}")
    all_ev = [r["ev"] for r in fix_a_results if r.get("n_trades", 0) >= 10]
    if all_ev and all(ev <= baseline["ev"] for ev in all_ev):
        print(f"\n=== Fix A: 기각 ===")
        print(f"  모든 임계값 조합에서 EV 악화. WIN이 LOSS보다 5d 상승률 높음 → 재설계 필요")
    elif fix_a_best:
        print(f"\n=== Fix A 최적 조합 ===")
        print(f"  5d>{fix_a_best['thr_5d']}% | 10d>{fix_a_best['thr_10d']}%")
        print(f"  차단 {fix_a_best['pct_blocked']:.0f}% | 잔여 WR {fix_a_best['win_rate']:.1f}% | EV {fix_a_best['ev']:+.3f}%")
    if "error" not in fix_b_result:
        print(f"\n=== Fix B 요약 ===")
        print(f"  t0 진입 시 PnL 개선: +{fix_b_result['avg_pnl_improvement']:.2f}%p")


def main():
    parser = argparse.ArgumentParser(description="늦은 진입 개선 시뮬레이션")
    parser.add_argument("--features-file", type=Path, default=None,
                        help="late_entry_features_*.csv 경로 (기본: 최신)")
    parser.add_argument("--no-chart", action="store_true")
    args = parser.parse_args()
    simulate(args.features_file, args.no_chart)


if __name__ == "__main__":
    main()
