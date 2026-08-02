#!/usr/bin/env python3
"""
analysis/compression_shadow.py — Compression Entry Shadow Mode

오케스트레이터 ACCEPT 종목의 압축 구조(compression structure) 측정.
SMC sweep 없이 작동하는 "고점 압축 후 재돌파" 패턴의 expectancy 기록.

핵심 발견 배경:
  - SMC ENTRY = 0 (전 기간): 한국 NTD alpha 종목은 스윙 저점을 유지
  - 즉, SMC sweep 조건과 오케스트레이터 선별 기준이 구조적으로 충돌
  - 이 모듈은 sweep 없이 compression 기반으로 진입했을 때의 결과를 추적

compression 구성 요소 (일봉 기반):
  1. range_contraction  — 최근 3일 range / 이전 10일 range (낮을수록 압축)
  2. ema_alignment      — EMA20 > EMA60 (추세 정배열)
  3. above_ema20        — 종가 > EMA20 (단기 지지)
  4. volume_dry_ratio   — 최근 3일 거래량 / 이전 10일 거래량 (낮을수록 건조)
  5. pullback_shallow   — 20일 고점 대비 현재 하락율 (낮을수록 고점 근처)

compression_score = 가중 합산 (0~1)

출력: logs/compression_shadow_YYYYMMDD.json
사용: python3 -m analysis.compression_shadow [--date YYYYMMDD] [--no-save]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from pykrx import stock as krx
except ImportError:
    print("ERROR: pykrx 미설치. pip install pykrx")
    sys.exit(1)

LOG_DIR = Path("logs")

ACCEPT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
    r"(?:✅ ACCEPT|🟡 CANDIDATE_ACCEPT) (\w+) @([\d,]+)원 \| PID:\d+ \| conf=([\d.]+) alpha=([+-][\d.]+)"
)
MKT_CTX_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[MKT_CTX_CHANGE\] \w+ → (\w+)"
)


# ── 로그 파싱 ─────────────────────────────────────────────────────────────────

def _parse_accepts(log_path: Path) -> dict:
    """ACCEPT 이벤트 → 종목별 집계."""
    if not log_path.exists():
        return {}
    by_code = {}
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = ACCEPT_RE.search(line)
            if not m:
                continue
            ts_str, code, price_str, conf_str, alpha_str = m.groups()
            ts    = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            price = int(price_str.replace(",", ""))
            conf  = float(conf_str)
            alpha = float(alpha_str)
            if code not in by_code:
                by_code[code] = {
                    "first_ts":    ts,
                    "first_price": price,
                    "last_price":  price,
                    "count":       0,
                    "alphas":      [],
                    "confs":       [],
                }
            by_code[code]["last_price"] = price
            by_code[code]["count"]     += 1
            by_code[code]["alphas"].append(alpha)
            by_code[code]["confs"].append(conf)
    return by_code


def _parse_mkt_context(log_path: Path) -> str:
    """장 중 MKT_CTX 최종 상태 반환 (TRADE_OK / NO_TRADE_DAY / ...)."""
    if not log_path.exists():
        return "UNKNOWN"
    last = "UNKNOWN"
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = MKT_CTX_RE.search(line)
            if m:
                last = m.group(2)
    return last


# ── 압축 지표 계산 ────────────────────────────────────────────────────────────

def _compute_compression(code: str, ref_date: date, accept_price: int | None = None) -> dict:
    """pykrx 일봉 기반 압축 구조 지표 계산."""
    try:
        end_str   = ref_date.strftime("%Y%m%d")
        start_str = (ref_date - timedelta(days=90)).strftime("%Y%m%d")
        df = krx.get_market_ohlcv_by_date(start_str, end_str, code)
        if df is None or len(df) < 20:
            return {"error": "데이터 부족", "compression_score": None}

        closes = df["종가"].values.tolist()
        highs  = df["고가"].values.tolist()
        lows   = df["저가"].values.tolist()
        opens  = df["시가"].values.tolist()
        vols   = df["거래량"].values.tolist()
        ranges = [h - l for h, l in zip(highs, lows)]

        def _ema(arr, n):
            k, result = 2 / (n + 1), [arr[0]]
            for v in arr[1:]:
                result.append(v * k + result[-1] * (1 - k))
            return result

        ema20_vals = _ema(closes, 20)
        ema60_vals = _ema(closes, 60)

        close_now = closes[-1]
        e20       = ema20_vals[-1]
        e60       = ema60_vals[-1]

        # 1. Range contraction: 최근 3일 / 이전 10일
        recent_range = sum(ranges[-3:]) / 3
        prior_range  = sum(ranges[-13:-3]) / 10 if len(ranges) >= 13 else sum(ranges[:-3]) / max(len(ranges) - 3, 1)
        range_ratio  = round(recent_range / prior_range, 3) if prior_range > 0 else 1.0

        # 2. EMA alignment
        ema_ok    = bool(e20 > e60)
        above_e20 = bool(close_now > e20)

        # 3. Volume dry: 최근 3일 / 이전 10일
        recent_vol = sum(vols[-3:]) / 3
        prior_vol  = sum(vols[-13:-3]) / 10 if len(vols) >= 13 else sum(vols[:-3]) / max(len(vols) - 3, 1)
        vol_ratio  = round(recent_vol / prior_vol, 3) if prior_vol > 0 else 1.0

        # 4. Pullback from 20-day high
        high20       = max(highs[-20:])
        pullback_pct = round((high20 - close_now) / high20 * 100, 2)

        # 5. ATR(14) 기반 range_vs_atr
        atr_vals = []
        for i in range(1, min(15, len(ranges))):
            tr = max(ranges[-i],
                     abs(highs[-i] - closes[-i - 1]),
                     abs(lows[-i]  - closes[-i - 1]))
            atr_vals.append(tr)
        atr         = sum(atr_vals) / len(atr_vals) if atr_vals else ranges[-1]
        range_vs_atr = round(ranges[-1] / atr, 3) if atr > 0 else 1.0

        # Compression Score (0~1)
        s_range  = max(0.0, 1.0 - range_ratio)             # 압축할수록 높음
        s_ema    = 1.0 if ema_ok else 0.0
        s_above  = 1.0 if above_e20 else 0.0
        s_vol    = max(0.0, 1.0 - vol_ratio)               # 건조할수록 높음
        s_pull   = max(0.0, 1.0 - pullback_pct / 5.0)     # 고점 근처일수록 높음 (5% 이내 만점)

        score = round(
            s_range * 0.30 +
            s_ema   * 0.20 +
            s_above * 0.15 +
            s_vol   * 0.20 +
            s_pull  * 0.15,
            3,
        )

        # OR position — 일봉 시가/고가/저가 기준 proxy (pykrx 분봉 미지원)
        # or_position_pct: 0=저가, 1=고가 (1 초과 = 고가 돌파 후 ACCEPT)
        or_position: dict | None = None
        if accept_price is not None:
            day_open  = opens[-1]
            day_high  = highs[-1]
            day_low   = lows[-1]
            day_range = day_high - day_low
            or_pos_pct = round((accept_price - day_low) / day_range, 3) if day_range > 0 else 0.5
            or_position = {
                "day_open":                  int(day_open),
                "day_high":                  int(day_high),
                "day_low":                   int(day_low),
                "above_open":                bool(accept_price > day_open),
                "accept_vs_open_pct":        round((accept_price - day_open) / day_open * 100, 2),
                "or_position_pct":           or_pos_pct,
                "distance_from_high_pct":    round((accept_price - day_high) / day_high * 100, 2),
                "close_vs_open_pct":         round((close_now - day_open) / day_open * 100, 2),
            }

        return {
            "compression_score":      score,
            "range_contraction":      range_ratio,    # <1.0 = 압축
            "ema_alignment":          ema_ok,
            "above_ema20":            above_e20,
            "volume_dry_ratio":       vol_ratio,      # <1.0 = 건조
            "pullback_from_high_pct": pullback_pct,
            "range_vs_atr":           range_vs_atr,
            "ema20":                  round(e20),
            "ema60":                  round(e60),
            "high20d":                int(high20),
            "close":                  int(close_now),
            "or_position":            or_position,
        }
    except Exception as e:
        return {"error": str(e), "compression_score": None}


# ── 다음 거래일 수익률 ────────────────────────────────────────────────────────

def _forward_return(code: str, accept_price: int, ref_date: date) -> dict | None:
    """다음 거래일 종가 기준 수익률."""
    try:
        start = (ref_date + timedelta(days=1)).strftime("%Y%m%d")
        end   = (ref_date + timedelta(days=7)).strftime("%Y%m%d")
        df    = krx.get_market_ohlcv_by_date(start, end, code)
        if df is None or df.empty:
            return None
        next_close = int(df.iloc[0]["종가"])
        ret_1d     = round((next_close - accept_price) / accept_price * 100, 2)
        next_open  = int(df.iloc[0]["시가"])
        ret_gap    = round((next_open - accept_price) / accept_price * 100, 2)
        return {
            "next_date":  str(df.index[0].date()),
            "next_open":  next_open,
            "next_close": next_close,
            "ret_gap":    ret_gap,    # gap (open 기준)
            "ret_1d":     ret_1d,     # close-to-close
        }
    except Exception:
        return None


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(target_date: date) -> dict:
    log_path = LOG_DIR / f"auto_trading_{target_date.strftime('%Y%m%d')}.log"
    mkt_ctx  = _parse_mkt_context(log_path)
    accepts  = _parse_accepts(log_path)

    if not accepts:
        print(f"  ACCEPT 이벤트 없음 ({log_path.name})")
        return {
            "date": str(target_date),
            "mkt_context": mkt_ctx,
            "source_log": log_path.name,
            "entries": [],
            "summary": {"count": 0},
        }

    print(f"  ACCEPT 종목 {len(accepts)}개  mkt_ctx={mkt_ctx}")
    print(f"  {'코드':8s}  {'score':>6s}  {'rng':>5s}  {'vol':>5s}  {'pull%':>6s}  {'or_pos':>6s}  {'alpha':>6s}  {'fwd_1d':>7s}")
    print(f"  {'-'*65}")

    entries = []
    for code, info in sorted(accepts.items()):
        comp = _compute_compression(code, target_date, info["first_price"])
        fwd  = _forward_return(code, info["first_price"], target_date)

        score_str = f"{comp['compression_score']:.3f}" if comp.get("compression_score") is not None else "ERR"
        fwd_str   = f"{fwd['ret_1d']:+.2f}%" if fwd else "N/A"
        rng_str   = f"{comp.get('range_contraction', 0):.2f}" if "range_contraction" in comp else "-"
        vol_str   = f"{comp.get('volume_dry_ratio', 0):.2f}"  if "volume_dry_ratio" in comp else "-"
        pull_str  = f"{comp.get('pullback_from_high_pct', 0):.1f}%" if "pullback_from_high_pct" in comp else "-"
        alpha_str = f"{sum(info['alphas'])/len(info['alphas']):+.2f}"
        _or = comp.get("or_position") or {}
        or_str    = f"{_or['or_position_pct']:.2f}" if "or_position_pct" in _or else "-"

        print(f"  {code:8s}  {score_str:>6s}  {rng_str:>5s}  {vol_str:>5s}  {pull_str:>6s}  {or_str:>6s}  {alpha_str:>6s}  {fwd_str:>7s}")

        entries.append({
            "date":              str(target_date),
            "code":              code,
            "mkt_context":       mkt_ctx,
            "first_accept_ts":   info["first_ts"].isoformat(timespec="seconds"),
            "first_accept_price":info["first_price"],
            "last_accept_price": info["last_price"],
            "accept_count":      info["count"],
            "avg_alpha":         round(sum(info["alphas"]) / len(info["alphas"]), 3),
            "avg_conf":          round(sum(info["confs"])  / len(info["confs"]),  3),
            "compression":       comp,
            "forward":           fwd,
        })

    # 요약
    valid    = [e for e in entries if e["forward"] is not None]
    fwd_rets = [e["forward"]["ret_1d"] for e in valid]
    summary  = {
        "count":         len(entries),
        "with_forward":  len(valid),
        "avg_fwd_1d":    round(sum(fwd_rets) / len(fwd_rets), 2) if fwd_rets else None,
        "win_rate_pct":  round(sum(1 for r in fwd_rets if r > 0) / len(fwd_rets) * 100, 1) if fwd_rets else None,
    }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date":         str(target_date),
        "mkt_context":  mkt_ctx,
        "source_log":   log_path.name,
        "entries":      entries,
        "summary":      summary,
    }


def save_json(result: dict, target_date: date) -> Path:
    out = LOG_DIR / f"compression_shadow_{target_date.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compression Entry Shadow Mode")
    parser.add_argument("--date",    type=str,            default=None)
    parser.add_argument("--no-save", action="store_true", help="JSON 저장 생략")
    args = parser.parse_args()

    target = datetime.strptime(args.date, "%Y%m%d").date() if args.date else date.today() - timedelta(days=1)

    print(f"\n{'='*62}")
    print(f"Compression Shadow  {target}")
    print(f"{'='*62}")
    result = run(target)
    s = result["summary"]
    if s["count"]:
        print(f"\n  종목 수: {s['count']}  forward 확보: {s['with_forward']}  "
              f"avg_fwd={s['avg_fwd_1d']:+.2f}%  win={s['win_rate_pct']:.0f}%"
              if s.get("avg_fwd_1d") is not None else "")
    if not args.no_save and result["entries"]:
        path = save_json(result, target)
        print(f"저장: {path}")
    print()
