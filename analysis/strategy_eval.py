"""
analysis/strategy_eval.py — 전략 검증 평가기

2주 데이터 기준으로 각 전략 컴포넌트의 패스/페일을 판정하고
"인버스 ETF 전략 추가 가능 여부"를 GO / NOT YET / NO-GO로 반환한다.

사용법:
    python3 -m analysis.strategy_eval              # 최근 14일
    python3 -m analysis.strategy_eval --days 7    # 최근 7일
    python3 -m analysis.strategy_eval --json      # JSON 출력
"""

import sys
import os
import json
from datetime import date, timedelta
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.log_analyzer import analyze_day


# ─── 평가 기준 (숫자는 변경 가능한 임계값) ────────────────────────────────────

CRITERIA = {
    # MKT_CTX 완화 효과
    # 차단율 60~80% → 과도 차단, 30~55% → 적정, 30% 미만 → 과완화
    "mkt_ctx_block_rate": {
        "label":   "MKT_CTX 차단율",
        "pass_range": (0.30, 0.55),   # 30~55%
        "warn_above":  0.60,           # 60% 이상 → 여전히 과도
        "warn_below":  0.25,           # 25% 미만 → 너무 풀림
        "unit":    "%",
        "weight":  2,                  # 중요도 (GO 판정에 반영)
    },
    # TRADE_OK인 날 중 실제 진입 발생 비율
    # 너무 낮으면 SMC 조건 자체가 너무 빡셈
    "entry_rate_on_trade_days": {
        "label":   "거래가능일 중 진입 발생 비율",
        "pass_min": 0.30,              # 30% 이상이면 OK
        "unit":    "%",
        "weight":  2,
    },
    # 누적 ACCEPT 건수 (Orchestrator 신호 품질)
    # 너무 적으면 Orchestrator 자체 문제
    "total_accepts": {
        "label":   "Orchestrator ACCEPT 총 건수",
        "pass_min": 30,                # 2주 30건 이상 (일 평균 3건 이상)
        "unit":    "건",
        "weight":  1,
    },
    # CHoCH 감지 건수
    # 너무 적으면 SMC 구조 자체가 감지 안 됨
    "total_chochs": {
        "label":   "CHoCH 총 감지 건수",
        "pass_min": 5,                 # 2주 5건 이상
        "unit":    "건",
        "weight":  1,
    },
    # TREND_SIG 실거래 발생 여부 (최소 1건)
    "trend_signals_exist": {
        "label":   "TREND 신호 실거래 발생",
        "pass_min": 1,                 # 1건 이상 → 전략이 살아있음
        "unit":    "건",
        "weight":  1,
    },
    # 일 평균 진입 건수 (0이면 전략 사실상 동작 안 함)
    "avg_accepts_per_trade_day": {
        "label":   "거래가능일 평균 ACCEPT",
        "pass_min": 2.0,               # 하루 평균 2건 이상
        "unit":    "건/일",
        "weight":  1,
    },
}

# GO 판정 기준: weight 합산 기준 75% 이상 통과 시 GO
GO_THRESHOLD    = 0.75
NOTYET_THRESHOLD = 0.50


# ─── 집계 ────────────────────────────────────────────────────────────────────

def _aggregate(summaries: list[dict]) -> dict:
    """N일 요약 리스트 → 집계 dict."""
    trading_days     = [s for s in summaries if not s.get("no_trade_day")]
    no_trade_days    = [s for s in summaries if s.get("no_trade_day")]
    days_with_entry  = [s for s in trading_days if s.get("accept_count", 0) > 0]

    total_accepts = sum(s.get("accept_count", 0) for s in summaries)
    total_chochs  = sum(s.get("choch_count",  0) for s in summaries)
    total_trends  = sum(s.get("trend_count",  0) for s in summaries)

    n_total    = len(summaries)
    n_trading  = len(trading_days)
    n_no_trade = len(no_trade_days)

    block_rate = n_no_trade / n_total if n_total else 0.0
    entry_rate = len(days_with_entry) / n_trading if n_trading else 0.0
    avg_accepts = total_accepts / n_trading if n_trading else 0.0

    return {
        "n_total":        n_total,
        "n_trading":      n_trading,
        "n_no_trade":     n_no_trade,
        "block_rate":     block_rate,
        "entry_rate":     entry_rate,
        "total_accepts":  total_accepts,
        "total_chochs":   total_chochs,
        "total_trends":   total_trends,
        "avg_accepts_per_trade_day": avg_accepts,
        "trend_signals_exist":       total_trends,
    }


# ─── 패스/페일 판정 ───────────────────────────────────────────────────────────

def _evaluate(agg: dict) -> dict:
    """집계 결과 → 각 기준별 PASS/WARN/FAIL 판정."""
    results = {}

    # mkt_ctx_block_rate
    br = agg["block_rate"]
    c  = CRITERIA["mkt_ctx_block_rate"]
    lo, hi = c["pass_range"]
    if lo <= br <= hi:
        status = "PASS"
    elif br > c["warn_above"] or br < c["warn_below"]:
        status = "FAIL"
    else:
        status = "WARN"
    results["mkt_ctx_block_rate"] = {"value": br, "status": status}

    # entry_rate_on_trade_days
    er = agg["entry_rate"]
    c  = CRITERIA["entry_rate_on_trade_days"]
    results["entry_rate_on_trade_days"] = {
        "value":  er,
        "status": "PASS" if er >= c["pass_min"] else "FAIL",
    }

    # total_accepts
    ta = agg["total_accepts"]
    c  = CRITERIA["total_accepts"]
    results["total_accepts"] = {
        "value":  ta,
        "status": "PASS" if ta >= c["pass_min"] else "FAIL",
    }

    # total_chochs
    tc = agg["total_chochs"]
    c  = CRITERIA["total_chochs"]
    results["total_chochs"] = {
        "value":  tc,
        "status": "PASS" if tc >= c["pass_min"] else "FAIL",
    }

    # trend_signals_exist
    tt = agg["trend_signals_exist"]
    c  = CRITERIA["trend_signals_exist"]
    results["trend_signals_exist"] = {
        "value":  tt,
        "status": "PASS" if tt >= c["pass_min"] else "FAIL",
    }

    # avg_accepts_per_trade_day
    av = agg["avg_accepts_per_trade_day"]
    c  = CRITERIA["avg_accepts_per_trade_day"]
    results["avg_accepts_per_trade_day"] = {
        "value":  av,
        "status": "PASS" if av >= c["pass_min"] else "FAIL",
    }

    return results


# ─── GO / NOT YET / NO-GO 판정 ────────────────────────────────────────────────

def _verdict(eval_results: dict) -> tuple[str, str]:
    """평가 결과 → GO / NOT YET / NO-GO + 사유."""
    total_weight = sum(CRITERIA[k]["weight"] for k in CRITERIA)
    pass_weight  = sum(
        CRITERIA[k]["weight"]
        for k, v in eval_results.items()
        if v["status"] == "PASS"
    )

    ratio = pass_weight / total_weight if total_weight else 0

    # TREND 신호 0건이면 무조건 NOT YET (핵심 검증 미완)
    if eval_results.get("trend_signals_exist", {}).get("status") == "FAIL":
        return "NOT YET", f"TREND 신호 미발생 (점수 {ratio:.0%}, TREND 검증 필수)"

    if ratio >= GO_THRESHOLD:
        return "GO", f"검증 통과 ({ratio:.0%})"
    if ratio >= NOTYET_THRESHOLD:
        return "NOT YET", f"일부 기준 미달 ({ratio:.0%})"
    return "NO-GO", f"핵심 기준 다수 실패 ({ratio:.0%})"


# ─── 출력 ────────────────────────────────────────────────────────────────────

def _print_report(summaries: list[dict], agg: dict, eval_r: dict, verdict: str, reason: str):
    width = 62
    print(f"\n{'='*width}")
    print(f"  🔬 Strategy Eval — {summaries[0]['date']} ~ {summaries[-1]['date']} ({agg['n_total']}일)")
    print(f"{'='*width}")

    # 기간 개요
    print(f"\n  [기간 개요]")
    print(f"    전체 거래일  : {agg['n_total']}일")
    print(f"    TRADE_OK    : {agg['n_trading']}일  ({agg['n_trading']/agg['n_total']*100:.0f}%)")
    print(f"    NO_TRADE_DAY: {agg['n_no_trade']}일  ({agg['block_rate']*100:.0f}%)")

    # 각 기준 결과
    print(f"\n  [기준별 평가]")
    icons = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
    for key, cr in CRITERIA.items():
        res     = eval_r[key]
        icon    = icons[res["status"]]
        val     = res["value"]
        unit    = cr["unit"]
        label   = cr["label"]
        # 값 포맷
        if unit == "%":
            val_str = f"{val*100:.1f}%"
        elif unit == "건/일":
            val_str = f"{val:.1f}건/일"
        else:
            val_str = f"{int(val)}{unit}"
        print(f"    {icon} {label:<28} {val_str}")

    # 최종 판정
    verdict_icons = {"GO": "🟢", "NOT YET": "🟡", "NO-GO": "🔴"}
    icon = verdict_icons.get(verdict, "⚪")
    print(f"\n{'─'*width}")
    print(f"  인버스 ETF 전략 추가 판정: {icon} {verdict}")
    print(f"  사유: {reason}")
    print(f"{'='*width}\n")


# ─── 진입점 ──────────────────────────────────────────────────────────────────

def run_eval(days: int = 14, as_json: bool = False):
    today = date.today()
    summaries = [analyze_day(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    # 주말 (accept=0, choch=0, no_trade=False 이지만 로그 없음) 은 제외하지 않음 — 있는 그대로 집계

    agg     = _aggregate(summaries)
    eval_r  = _evaluate(agg)
    verdict, reason = _verdict(eval_r)

    if as_json:
        out = {
            "period":   {"from": summaries[0]["date"], "to": summaries[-1]["date"]},
            "aggregate": agg,
            "eval":      eval_r,
            "verdict":   verdict,
            "reason":    reason,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(summaries, agg, eval_r, verdict, reason)

    return verdict


def main():
    args    = sys.argv[1:]
    as_json = "--json" in args
    args    = [a for a in args if a != "--json"]

    days = 14
    if "--days" in args:
        idx  = args.index("--days")
        days = int(args[idx + 1]) if idx + 1 < len(args) else 14

    run_eval(days=days, as_json=as_json)


if __name__ == "__main__":
    main()
