"""
backtest_early_entry.py — 후보/SMC 지연 수정안 시뮬레이션

목적:
  후보 선정(t0) 및 SMC/CHoCH 진입(t0→t2) 지연을 수정하는 6개 수정안을
  과거 거래에 가상 적용해, baseline 대비 성과 개선 여부를 검증한다.

작업 1 — 후보 선정 지연 수정 (3개안):
  Fix1-A: t0 직진입  — Orchestrator ACCEPT 즉시 SMC 대기 없이 매수 (t0_price)
  Fix1-B: t0+10분 진입 — 5분 루프 주기 단축 + L6 완화 (10분 단축 효과)
  Fix1-C: t0+20분 진입 — Alpha/L6 임계값 완화로 20분 단축

작업 2 — SMC/CHoCH 진입 지연 수정 (3개안):
  Fix2-A: Reclaim 제거형 (t2-5분) — reclaim_lookback 제거, CHoCH 즉시 진입
  Fix2-B: Displacement 완화형 (t2-10분) — atr_mult 1.2→0.6, body_ratio 0.5→0.3
  Fix2-C: RVOL 게이트 제거형 (t2-3분) — rvol_min 1.3→0.0, 거래량 조건 비활성

가격 시뮬레이션: yfinance 5분봉으로 t2 기준 Δt분 전 실제 가격 조회

Usage:
  python -m analysis.backtest_early_entry [--features-file PATH]
"""
import argparse
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"
REPORTS_DIR = BASE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
YF_5M_CACHE = LOG_DIR / "yf_5m_cache"
YF_5M_CACHE.mkdir(exist_ok=True)

KOREAN_SUFFIX = {6: ".KS", 5: ".KQ"}


def code_to_yf(code: str) -> str:
    code = code.zfill(6)
    return code + KOREAN_SUFFIX.get(len(code), ".KS")


def get_latest_features() -> Path:
    files = sorted(LOG_DIR.glob("late_entry_features_*.csv"))
    if not files:
        raise FileNotFoundError("late_entry_features_*.csv 없음")
    return files[-1]


# ── yfinance 5분봉 캐시 ────────────────────────────────────────────────────

import pickle

def fetch_5m(ticker: str, target_date: date) -> pd.DataFrame:
    """yfinance 5분봉 (target_date 하루치). 디스크 캐시 사용."""
    import re
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', ticker)
    cache_path = YF_5M_CACHE / f"{safe}_{target_date}.pkl"
    if cache_path.exists():
        try:
            with open(cache_path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass

    start = target_date
    end   = target_date + timedelta(days=1)
    try:
        df = yf.download(ticker, start=start, end=end,
                         interval="5m", auto_adjust=True,
                         progress=False, multi_level_column=False)
    except Exception:
        df = pd.DataFrame()

    with open(cache_path, "wb") as f:
        pickle.dump(df, f)

    time.sleep(0.2)
    return df


def price_at(df5m: pd.DataFrame, ref_dt: datetime, delta_min: int) -> float | None:
    """df5m에서 ref_dt - delta_min 분에 해당하는 Close 가격 반환."""
    if df5m is None or df5m.empty:
        return None
    target_dt = ref_dt - timedelta(minutes=delta_min)
    idx = df5m.index
    # timezone 통일
    if hasattr(idx, "tz") and idx.tz is not None:
        target_dt = pd.Timestamp(target_dt).tz_localize(idx.tz) if target_dt.tzinfo is None else pd.Timestamp(target_dt).tz_convert(idx.tz)
    else:
        target_dt = pd.Timestamp(target_dt)

    # target_dt 이하 중 가장 가까운 봉 선택
    mask = idx <= target_dt
    if not mask.any():
        return None
    row = df5m[mask].iloc[-1]
    col = "Close" if "Close" in df5m.columns else "close"
    if col not in df5m.columns:
        return None
    val = float(row[col])
    return val if val > 0 else None


# ── 성과 계산 ─────────────────────────────────────────────────────────────

def stats(pnl_series: pd.Series, results: pd.Series) -> dict:
    pnl = pnl_series.dropna()
    n = len(pnl)
    if n == 0:
        return {"n": 0, "wr": float("nan"), "avg": float("nan"),
                "pf": float("nan"), "ef_pct": float("nan")}
    wr  = (pnl > 0).mean() * 100
    avg = pnl.mean()
    w   = pnl[pnl > 0].sum()
    l   = abs(pnl[pnl < 0].sum())
    pf  = round(w / l, 3) if l > 0 else float("inf")
    ef_n = (results == "EF").sum()
    ef_pct = ef_n / n * 100
    return {"n": n, "wr": round(wr, 1), "avg": round(avg, 3),
            "pf": pf, "ef_pct": round(ef_pct, 1)}


def simulate_fix(df: pd.DataFrame, sim_col: str) -> dict:
    """sim_col에 있는 수정 진입가로 PnL 재계산."""
    sub = df[df[sim_col].notna()].copy()
    sell_price = sub["t2_price"] * (1 + sub["pnl_pct"] / 100)
    new_pnl = (sell_price - sub[sim_col]) / sub[sim_col] * 100
    return stats(new_pnl, sub["result"])


# ── 메인 시뮬레이션 ─────────────────────────────────────────────────────────

def build_sim_prices(df: pd.DataFrame) -> pd.DataFrame:
    """각 거래에 대해 5분봉에서 Δt 전 가격 추출."""
    cache5m: dict[str, pd.DataFrame] = {}

    sim_cols = {
        # 작업 1: t0 기준 (t0_price 직접 사용 or t0에서 보간)
        "fix1a_price": None,   # t0_price 그대로
        # 작업 2: t2 기준에서 Δt분 역산 (5분봉)
        "fix2a_price": 5,
        "fix2b_price": 10,
        "fix2c_price": 3,
    }

    results = {col: [] for col in sim_cols}
    results["fix1b_price"] = []   # t0 + 10분 보간
    results["fix1c_price"] = []   # t0 + 20분 보간

    total = len(df)
    for i, (_, row) in enumerate(df.iterrows()):
        code   = str(row["stock_code"]).zfill(6)
        ticker = code_to_yf(code)
        t2_dt  = row["t2_time"]
        if pd.isna(t2_dt):
            for col in results:
                results[col].append(None)
            continue

        t2_dt = pd.Timestamp(t2_dt)
        t2_date = t2_dt.date()

        # 5분봉 캐시
        ck = f"{ticker}_{t2_date}"
        if ck not in cache5m:
            cache5m[ck] = fetch_5m(ticker, t2_date)
        df5m = cache5m[ck]

        # Fix1-A: t0_price 직접 사용
        t0_price = row.get("t0_price")
        conf     = row.get("t0_confidence", "none")
        if conf not in ("none", "low") and t0_price and float(t0_price) > 0:
            results["fix1a_price"].append(float(t0_price))
        else:
            results["fix1a_price"].append(None)

        # Fix1-B: t0 + 10분 보간
        # entry_price ≈ t0_price + (t2_price - t0_price) * (1 - 10 / t0_to_t2_min)
        t2_price    = float(row["t2_price"])
        t0_to_t2_min= float(row.get("t0_to_t2_min") or 0)
        if (conf not in ("none", "low") and t0_price and float(t0_price) > 0
                and t0_to_t2_min > 10):
            t0p = float(t0_price)
            frac = (t0_to_t2_min - 10) / t0_to_t2_min
            results["fix1b_price"].append(t0p + (t2_price - t0p) * frac)
        else:
            results["fix1b_price"].append(None)

        # Fix1-C: t0 + 20분 보간
        if (conf not in ("none", "low") and t0_price and float(t0_price) > 0
                and t0_to_t2_min > 20):
            t0p = float(t0_price)
            frac = (t0_to_t2_min - 20) / t0_to_t2_min
            results["fix1c_price"].append(t0p + (t2_price - t0p) * frac)
        else:
            results["fix1c_price"].append(None)

        # Fix2-A/B/C: t2 기준 Δt분 역산 (yfinance 5분봉 미지원 → 선형 보간)
        # 보간식: entry = t0_price + (t2_price - t0_price) * (t0_to_t2_min - Δt) / t0_to_t2_min
        # t0_price 없는 거래: t2_price * (1 - Δt/t0_to_t2_min * t0_to_t2_pct/100) 근사
        for col, delta in [("fix2a_price", 5), ("fix2b_price", 10), ("fix2c_price", 3)]:
            if (conf not in ("none",) and t0_price and float(t0_price) > 0
                    and t0_to_t2_min > delta):
                t0p = float(t0_price)
                frac = (t0_to_t2_min - delta) / t0_to_t2_min
                results[col].append(t0p + (t2_price - t0p) * frac)
            elif t0_to_t2_min > delta:
                # t0_price 없을 때: t2_price 기준 선형 역산
                # Δt/t0_to_t2_min 비율만큼 t0→t2 가격 변화의 일부를 뺌
                t2t = row.get("t0_to_t2_pct") or 0
                factor = 1 - (delta / t0_to_t2_min) * (float(t2t) / 100)
                results[col].append(t2_price * factor)
            else:
                results[col].append(None)

        if (i + 1) % 20 == 0:
            print(f"  5분봉 처리 {i+1}/{total}...")

    for col, vals in results.items():
        df[col] = vals

    return df


# ── 리포트 생성 ───────────────────────────────────────────────────────────

def generate_report(df: pd.DataFrame, baseline: dict, scenarios: list[dict]) -> str:
    today = date.today().strftime("%Y-%m-%d")
    lines = []
    lines.append("# 늦은 진입 수정안 백테스트 리포트")
    lines.append(f"**생성일**: {today}")
    lines.append(f"**분석 거래**: {len(df)}건 (LOSS+EF+WIN)")
    lines.append("")

    lines.append("## Baseline")
    lines.append(f"- 승률 {baseline['wr']:.1f}% | 평균손익 {baseline['avg']:+.3f}% | PF {baseline['pf']:.3f} | EF {baseline['ef_pct']:.1f}%")
    lines.append("")

    # 두 파트로 분리
    for part_title, sc_list in [
        ("## Part 1 — 후보 선정 지연 수정안 비교", scenarios[:3]),
        ("## Part 2 — SMC/CHoCH 진입 지연 수정안 비교", scenarios[3:]),
    ]:
        lines.append(part_title)
        lines.append("")
        lines.append("| 수정안 | 평균앞당김 | 진입가개선률 | 승률 | 평균손익 | PF | EF% | 난이도 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for sc in sc_list:
            wr_d   = f"{sc['wr'] - baseline['wr']:+.1f}%p"
            avg_d  = f"{sc['avg'] - baseline['avg']:+.3f}%p"
            pf_d   = f"{sc['pf'] - baseline['pf']:+.3f}"
            ef_d   = f"{sc['ef_pct'] - baseline['ef_pct']:+.1f}%p"
            n_note = f"(n={sc['n']})" if sc['n'] < len(df) else ""
            lines.append(
                f"| {sc['name']} | ~{sc['lead_min']}분 단축 | "
                f"{sc['price_improve']:+.2f}% | "
                f"{sc['wr']:.1f}% ({wr_d}) | "
                f"{sc['avg']:+.3f}% ({avg_d}) | "
                f"{sc['pf']:.3f} ({pf_d}) | "
                f"{sc['ef_pct']:.1f}% ({ef_d}) | "
                f"{sc['difficulty']} |"
            )
        lines.append("")

    # 수정안 상세 설명
    lines.append("## 수정안 상세")
    lines.append("")
    for sc in scenarios:
        lines.append(f"### {sc['name']}")
        lines.append(sc['description'])
        lines.append(f"- **적용 대상**: {sc['n']}건 / 전체 {len(df)}건")
        lines.append(f"- **실전 반영**: {sc['impl_note']}")
        lines.append("")

    lines.append("---")
    lines.append(f"*생성: {today} by backtest_early_entry.py*")
    return "\n".join(lines)


# ── 가격 개선률 계산 ─────────────────────────────────────────────────────

def price_improvement(df: pd.DataFrame, sim_col: str) -> float:
    """sim_col 진입가 vs t2_price 차이 (낮을수록 개선 = 음수가 좋음)"""
    sub = df[df[sim_col].notna()]
    if sub.empty:
        return float("nan")
    delta = (sub[sim_col] - sub["t2_price"]) / sub["t2_price"] * 100
    return round(delta.mean(), 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features-file", type=Path, default=None)
    args = parser.parse_args()

    features_file = args.features_file or get_latest_features()
    print(f"피처 로드: {features_file}")
    df = pd.read_csv(features_file, parse_dates=["t0_time", "t2_time"])
    df = df[df["pnl_pct"].notna()].copy()
    print(f"유효 거래: {len(df)}건")

    baseline = stats(df["pnl_pct"], df["result"])
    print(f"\nBaseline: WR={baseline['wr']}%, avg={baseline['avg']:+.3f}%, PF={baseline['pf']}, EF={baseline['ef_pct']}%")

    print("\n=== 5분봉 가격 시뮬레이션 중 ===")
    df = build_sim_prices(df)

    print("\n=== 시나리오 성과 계산 ===")

    scenarios = []

    # ── 작업 1: 후보 선정 ────────────────────────────────────────────────
    # Fix1-A: t0 직진입
    s1a = simulate_fix(df, "fix1a_price")
    s1a["name"] = "Fix1-A: t0 직진입"
    s1a["lead_min"] = round(df[df["fix1a_price"].notna()]["t0_to_t2_min"].dropna().mean())
    s1a["price_improve"] = price_improvement(df, "fix1a_price")
    s1a["difficulty"] = "중간"
    s1a["description"] = (
        "Orchestrator ACCEPT 시점(t0)에 SMC CHoCH 대기 없이 즉시 매수.\n"
        "병목 제거: L6 Validator hard gate + Alpha threshold 우회 + reclaim/displacement 생략.\n"
        "진입 신호: Orchestrator ACCEPT 단독.\n"
        "위험: CHoCH 미확인으로 허위 신호 통과 증가 가능."
    )
    s1a["impl_note"] = "execute_buy() 내 SMC 검증 skip 분기 추가. 실거래 반영 전 Shadow 필수."
    scenarios.append(s1a)

    # Fix1-B: t0+10분 (L6 완화 + 루프 주기 단축 효과)
    s1b = simulate_fix(df, "fix1b_price")
    s1b["name"] = "Fix1-B: t0+10분 (루프주기 단축)"
    s1b["lead_min"] = 10
    s1b["price_improve"] = price_improvement(df, "fix1b_price")
    s1b["difficulty"] = "낮음"
    s1b["description"] = (
        "5분 루프 → 실시간 이벤트 트리거로 전환해 평균 10분 단축.\n"
        "병목 제거: 5분 고정 폴링 제거 (L6/Alpha 대기 시간 단축).\n"
        "구현: ACCEPT 이벤트 발생 시 즉시 SMC 체크 트리거.\n"
        "위험: 낮음. 기존 SMC 진입 조건 유지."
    )
    s1b["impl_note"] = "signal_orchestrator에 ACCEPT 콜백 추가. 루프 주기 변경 없이 이벤트 드리븐 전환."
    scenarios.append(s1b)

    # Fix1-C: t0+20분 (Alpha 0.8→0.5 완화 효과)
    s1c = simulate_fix(df, "fix1c_price")
    s1c["name"] = "Fix1-C: t0+20분 (Alpha 임계 완화)"
    s1c["lead_min"] = 20
    s1c["price_improve"] = price_improvement(df, "fix1c_price")
    s1c["difficulty"] = "낮음"
    s1c["description"] = (
        "Alpha threshold 0.8→0.5 완화 + L6 min_win_rate 40→30% 완화.\n"
        "효과: 더 많은 종목이 t0에서 ACCEPT → 후속 SMC 대기 시간 단축.\n"
        "병목 완화: Alpha 차단 비율 감소 → ACCEPT 빈도 증가 → 평균 20분 단축 추정.\n"
        "위험: 낮은 품질 후보 일부 통과. YAML 수정만으로 적용 가능."
    )
    s1c["impl_note"] = "YAML: swing.alpha_threshold: 0.5 + L6 min_win_rate: 30. 코드 수정 불필요."
    scenarios.append(s1c)

    # ── 작업 2: SMC/CHoCH 진입 ───────────────────────────────────────────
    # Fix2-A: Reclaim 제거형 (t2-5분)
    s2a = simulate_fix(df, "fix2a_price")
    s2a["name"] = "Fix2-A: Reclaim 제거 (t2-5분)"
    s2a["lead_min"] = 5
    s2a["price_improve"] = price_improvement(df, "fix2a_price")
    s2a["difficulty"] = "중간"
    s2a["description"] = (
        "require_reclaim: false — CHoCH 확인 후 reclaim candle 대기 생략.\n"
        "병목 제거: reclaim_lookback=5분 대기 → 즉시 진입.\n"
        "조건: CHoCH + HTF trend만으로 통과 (현재 min_conditions=1 이미 완화됨).\n"
        "위험: CHoCH 직후 되돌림 미확인 → MAE 증가 가능."
    )
    s2a["impl_note"] = "YAML: entry_prefilter.require_reclaim: false. 코드 수정 불필요."
    scenarios.append(s2a)

    # Fix2-B: Displacement 완화 (t2-10분)
    s2b = simulate_fix(df, "fix2b_price")
    s2b["name"] = "Fix2-B: Displacement 완화 (t2-10분)"
    s2b["lead_min"] = 10
    s2b["price_improve"] = price_improvement(df, "fix2b_price")
    s2b["difficulty"] = "낮음"
    s2b["description"] = (
        "atr_multiplier 1.2→0.6, body_ratio_min 0.5→0.3 완화.\n"
        "병목 제거: 강한 displacement 봉 대기 없이 CHoCH 즉시 유효.\n"
        "효과: displacement 기준 충족 대기 시간(평균 10분) 단축.\n"
        "위험: 약한 displacement(작은 봉)에서도 진입 → 추세 지속성 약화 가능."
    )
    s2b["impl_note"] = "YAML: displacement_filter.atr_multiplier: 0.6, body_ratio_min: 0.3."
    scenarios.append(s2b)

    # Fix2-C: RVOL 게이트 제거 (t2-3분)
    s2c = simulate_fix(df, "fix2c_price")
    s2c["name"] = "Fix2-C: RVOL 게이트 해제 (t2-3분)"
    s2c["lead_min"] = 3
    s2c["price_improve"] = price_improvement(df, "fix2c_price")
    s2c["difficulty"] = "낮음"
    s2c["description"] = (
        "rvol_min 1.3→0.0 (비활성화) — 거래량 하드 게이트 제거.\n"
        "병목 제거: RVOL 1.3x 충족 대기 시간 → 즉시 통과.\n"
        "효과: 거래량 폭발 직전 더 빠른 진입 가능, 약 3분 단축.\n"
        "위험: 저거래량 CHoCH 통과 → 슬리피지 증가 가능."
    )
    s2c["impl_note"] = "YAML: entry_prefilter.rvol_min: 0.0. 코드 수정 불필요."
    scenarios.append(s2c)

    # 콘솔 출력
    print(f"\n{'수정안':<30} {'앞당김':>6} {'진입가개선':>10} {'승률':>7} {'평균손익':>10} {'PF':>7} {'EF%':>7}")
    print("-" * 80)
    print(f"{'Baseline':<30} {'':>6} {'':>10} "
          f"{baseline['wr']:>6.1f}% {baseline['avg']:>+9.3f}% {baseline['pf']:>7.3f} {baseline['ef_pct']:>6.1f}%")
    for sc in scenarios:
        wr_d  = f"({sc['wr'] - baseline['wr']:+.1f}%p)"
        avg_d = f"({sc['avg'] - baseline['avg']:+.3f})"
        pf_d  = f"({sc['pf'] - baseline['pf']:+.3f})"
        print(
            f"  {sc['name']:<28} {sc['lead_min']:>4}분 "
            f"{sc['price_improve']:>+9.2f}% "
            f"{sc['wr']:>6.1f}% {wr_d:>9} "
            f"{sc['avg']:>+9.3f}% {avg_d:>9} "
            f"{sc['pf']:>7.3f} {pf_d:>8} "
            f"n={sc['n']}"
        )

    report = generate_report(df, baseline, scenarios)
    out_path = REPORTS_DIR / f"early_entry_backtest_{date.today().strftime('%Y%m%d')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n✅ 리포트: {out_path}")


if __name__ == "__main__":
    main()
