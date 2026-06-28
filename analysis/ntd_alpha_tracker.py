"""
analysis/ntd_alpha_tracker.py — NTD 구간 ACCEPT Forward Outcome Tracker

목적:
  NO_TRADE_DAY 구간에 발생한 ACCEPT 신호가 실제로 follow-through 했는지 측정.
  "약세장에서도 살아남는 리더 패턴" 데이터셋 구축 (Shadow Mode Phase 1).

수집 Feature (종목당 1레코드):
  입력:  alpha, conf, ntd_reason (NTD 발동 이유)
  당일:  day_return, recovery_strength, value_ratio, rs_vs_kosdaq
  익일+: next_1d_ret, next_2d_ret, next_5d_ret, next_day_gap
  분류:  survived (next_5d_ret >= +3%)

중복 처리: 같은 날 같은 종목이 여러 번 ACCEPT → alpha 최고값 1건만 유지.

사용:
  python3 -m analysis.ntd_alpha_tracker [--days 30] [--date YYYYMMDD] [--no-price]
출력:
  logs/ntd_alpha_YYYYMMDD.json
  logs/ntd_alpha_YYYYMMDD.csv
"""
import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

LOG_DIR = Path("logs")
ACCEPT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+.*?"
    r"✅ ACCEPT (\w+) @([\d,]+)원 \| PID:\d+ \| conf=([\d.]+) alpha=([+-][\d.]+)"
)
NTD_CHANGE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+.*?"
    r"\[MKT_CTX_CHANGE\] (\w+) → (NO_TRADE_DAY|TRADE_OK)"
)
SURVIVE_THRESHOLD = 3.0   # +3% 이상 = "survived"
KOSDAQ_TICKER     = "^KQ11"


# ── 로그 파싱 유틸 ─────────────────────────────────────────────────────────────

def _trading_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _parse_ntd_windows(log_path: Path) -> list[tuple[datetime, datetime, str]]:
    """(시작, 종료, ntd_reason) 목록 반환."""
    if not log_path.exists():
        return []
    windows, ntd_start, ntd_reason = [], None, ""
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = NTD_CHANGE_RE.search(line)
            if not m:
                continue
            ts  = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            frm, to = m.group(2), m.group(3)
            if to == "NO_TRADE_DAY" and ntd_start is None:
                ntd_start = ts
                # reason: 첫 번째 block_reason 파싱
                reason_part = line.split("NO_TRADE_DAY")[-1].strip().lstrip("|").strip()
                ntd_reason = reason_part[:60]
            elif to == "TRADE_OK" and ntd_start is not None:
                windows.append((ntd_start, ts, ntd_reason))
                ntd_start = None
    if ntd_start:
        close = ntd_start.replace(hour=15, minute=30, second=0)
        if ntd_start < close:
            windows.append((ntd_start, close, ntd_reason))
    return windows


def _parse_orchestrator_accepts(start_dt: datetime, end_dt: datetime) -> list[dict]:
    """signal_orchestrator.log에서 기간 내 ACCEPT 파싱."""
    records = []
    orch_log = LOG_DIR / "signal_orchestrator.log"
    if not orch_log.exists():
        return records
    with open(orch_log, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = ACCEPT_RE.search(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            if not (start_dt <= ts <= end_dt):
                continue
            records.append({
                "ts":    ts,
                "code":  m.group(2),
                "price": float(m.group(3).replace(",", "")),
                "conf":  float(m.group(4)),
                "alpha": float(m.group(5)),
            })
    return records


# ── 가격 데이터 ───────────────────────────────────────────────────────────────

def _fetch_ohlcv(code: str, start: date, days: int = 10) -> dict | None:
    """yfinance에서 OHLCV 조회. .KQ → .KS 순서로 시도."""
    # end: start 기준 days 이후와 오늘+7일 중 더 늦은 날짜 (forward return 확보)
    end = max(start + timedelta(days=days + 5),
              date.today() + timedelta(days=7))
    for suffix in (".KQ", ".KS"):
        ticker = f"{code}{suffix}"
        try:
            df = yf.download(ticker, start=str(start), end=str(end),
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 2:
                continue
            # MultiIndex 컬럼 → 단일 레벨로 플래튼
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            return {"df": df, "ticker": ticker}
        except Exception:
            pass
        time.sleep(0.1)
    return None


def _kosdaq_returns(start: date, end: date | None = None) -> dict[date, float]:
    """KOSDAQ 일별 수익률 {date: pct}."""
    fetch_end = max(
        (end or start) + timedelta(days=15),
        date.today() + timedelta(days=7),
    )
    try:
        df = yf.download(KOSDAQ_TICKER, start=str(start - timedelta(days=5)),
                         end=str(fetch_end), progress=False, auto_adjust=True)
        if df is None or len(df) < 2:
            return {}
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        closes = df["Close"].squeeze()
        ret: dict[date, float] = {}
        dates = list(closes.index)
        for i in range(1, len(dates)):
            # Timestamp or date → 항상 date 객체로 변환
            raw = dates[i]
            d = raw.date() if hasattr(raw, "date") else raw
            prev = float(closes.iloc[i - 1])
            cur  = float(closes.iloc[i])
            ret[d] = round((cur - prev) / prev * 100, 4) if prev > 0 else 0.0
        return ret
    except Exception:
        return {}


def _compute_features(record: dict, ohlcv_data: dict | None,
                       kosdaq_rets: dict[date, float]) -> dict:
    """당일 / 익일 feature 계산."""
    entry_date = record["ts"].date()
    out = {
        "date":       str(entry_date),
        "code":       record["code"],
        "entry_time": record["ts"].strftime("%H:%M"),
        "alpha":      record["alpha"],
        "conf":       record["conf"],
        "ntd_reason": record.get("ntd_reason", ""),
    }

    if ohlcv_data is None:
        out["price_unavailable"] = True
        return out

    df = ohlcv_data["df"]

    # 날짜 인덱스 → date 키로 변환
    try:
        price_rows = {r.date(): i for i, r in enumerate(df.index)}
    except Exception:
        out["price_unavailable"] = True
        return out

    if entry_date not in price_rows:
        out["price_unavailable"] = True
        return out

    idx = price_rows[entry_date]
    row = df.iloc[idx]

    def _f(v):
        try:
            return round(float(v), 4)
        except Exception:
            return None

    entry_open  = _f(row.get("Open",  row.get("open",  0)))
    entry_high  = _f(row.get("High",  row.get("high",  0)))
    entry_low   = _f(row.get("Low",   row.get("low",   0)))
    entry_close = _f(row.get("Close", row.get("close", 0)))
    entry_vol   = _f(row.get("Volume",row.get("volume",0)))

    # 당일 수익률 (종가 vs 전일 종가)
    prev_close = _f(df.iloc[idx - 1]["Close"]) if idx > 0 else None
    day_return = round((entry_close - prev_close) / prev_close * 100, 2) \
        if prev_close and prev_close > 0 else None

    # Recovery strength: (close - low) / (high - low)
    hl_range = entry_high - entry_low if entry_high and entry_low else 0
    recovery_strength = round((entry_close - entry_low) / hl_range, 3) \
        if hl_range > 0 else None

    # 거래대금 유지율: 당일 거래대금 / 20일 평균
    trade_val = entry_close * entry_vol if entry_close and entry_vol else 0
    past_vols  = df["Volume"].iloc[max(0, idx - 20):idx]
    past_close = df["Close"].iloc[max(0, idx - 20):idx]
    if len(past_vols) >= 5:
        avg_val = float((past_close * past_vols).mean())
        value_ratio = round(trade_val / avg_val, 2) if avg_val > 0 else None
    else:
        value_ratio = None

    # RS vs KOSDAQ
    kq_ret = kosdaq_rets.get(entry_date)
    rs_vs_kosdaq = round(day_return - kq_ret, 2) \
        if day_return is not None and kq_ret is not None else None

    # Forward returns (entry_date 기준 close-to-close)
    def _fwd_ret(offset: int) -> float | None:
        future_dates = sorted(d for d in price_rows if d > entry_date)
        if len(future_dates) < offset:
            return None
        target_idx = price_rows[future_dates[offset - 1]]
        target_close = _f(df.iloc[target_idx]["Close"])
        return round((target_close - entry_close) / entry_close * 100, 2) \
            if target_close and entry_close else None

    # 다음날 갭: (next_open - today_close) / today_close
    future_sorted = sorted(d for d in price_rows if d > entry_date)
    next_day_gap = None
    if future_sorted:
        nxt_row   = df.iloc[price_rows[future_sorted[0]]]
        nxt_open  = _f(nxt_row.get("Open", nxt_row.get("open", 0)))
        if nxt_open and entry_close:
            next_day_gap = round((nxt_open - entry_close) / entry_close * 100, 2)

    next_1d = _fwd_ret(1)
    next_2d = _fwd_ret(2)
    next_5d = _fwd_ret(5)

    # 5일 내 최대 수익 / 최대 손실
    highs5 = [_f(df.iloc[price_rows[d]]["High"]) for d in future_sorted[:5]
               if d in price_rows]
    lows5  = [_f(df.iloc[price_rows[d]]["Low"])  for d in future_sorted[:5]
               if d in price_rows]
    max_5d_ret = round((max(highs5) - entry_close) / entry_close * 100, 2) \
        if highs5 and entry_close else None
    max_5d_dd  = round((min(lows5) - entry_close) / entry_close * 100, 2) \
        if lows5 and entry_close else None

    survived = next_5d is not None and next_5d >= SURVIVE_THRESHOLD

    # leader_score: 각 컴포넌트를 0~1로 정규화 후 가중 합산
    #   alpha (0~3) × 0.35, recovery_strength (0~1) × 0.25,
    #   day_return (0~30%) × 0.25, persistence_score (0~1) × 0.15
    persistence_count = record.get("persistence_count", 0)
    consecutive_days  = record.get("consecutive_days", 1)
    persistence_score = min(persistence_count / 5.0, 1.0)
    alpha_norm        = min(record["alpha"] / 3.0, 1.0)
    rec_norm          = recovery_strength if recovery_strength is not None else 0.0
    day_norm          = min(max(day_return or 0.0, 0.0) / 30.0, 1.0)
    leader_score      = round(
        alpha_norm * 0.35 + rec_norm * 0.25 + day_norm * 0.25 + persistence_score * 0.15, 3
    )

    # Leader Archetype 분류
    #   TYPE_A (Explosion): 단일 폭발, day≥15% + recovery≥0.85
    #   TYPE_B (Persistent): 시간축 지속, consecutive≥2 + recovery≥0.50
    #   TYPE_AB: A + B 조건 동시 충족 (가장 강한 유형)
    #   TYPE_C (Noise): 나머지
    is_explosion  = (day_return or 0) >= 15.0 and (recovery_strength or 0) >= 0.85
    is_persistent = consecutive_days >= 2 and (recovery_strength or 0) >= 0.50
    if is_explosion and is_persistent:
        archetype = "TYPE_AB"
    elif is_explosion:
        archetype = "TYPE_A"
    elif is_persistent:
        archetype = "TYPE_B"
    else:
        archetype = "TYPE_C"

    out.update({
        "entry_close":        entry_close,
        "day_return":         day_return,
        "recovery_strength":  recovery_strength,
        "value_ratio":        value_ratio,
        "rs_vs_kosdaq":       rs_vs_kosdaq,
        "next_day_gap":       next_day_gap,
        "next_1d_ret":        next_1d,
        "next_2d_ret":        next_2d,
        "next_5d_ret":        next_5d,
        "max_5d_ret":         max_5d_ret,
        "max_5d_dd":          max_5d_dd,
        "survived":           survived,
        "persistence_count":  persistence_count,
        "consecutive_days":   consecutive_days,
        "persistence_score":  round(persistence_score, 2),
        "archetype":          archetype,
        "leader_score":       leader_score,
    })
    return out


# ── 메인 분석 ─────────────────────────────────────────────────────────────────

def run(days_back: int = 30, target_date: date | None = None,
        fetch_price: bool = True) -> list[dict]:
    end_day   = target_date or date.today()
    start_day = end_day - timedelta(days=days_back * 2)
    all_days  = _trading_days(start_day, end_day)
    days      = all_days[-days_back:]

    start_dt = datetime.combine(days[0],  datetime.min.time())
    end_dt   = datetime.combine(days[-1], datetime.max.time())

    # NTD 구간 수집
    ntd_windows: list[tuple[datetime, datetime, str]] = []
    for d in days:
        log = LOG_DIR / f"auto_trading_{d.strftime('%Y%m%d')}.log"
        ntd_windows.extend(_parse_ntd_windows(log))

    if not ntd_windows:
        print("NTD 구간 없음.")
        return []

    # ACCEPT 파싱 (NTD 구간 내 것만)
    accepts = _parse_orchestrator_accepts(start_dt, end_dt)
    ntd_accepts = []
    for a in accepts:
        for ws, we, wr in ntd_windows:
            if ws <= a["ts"] <= we:
                a["ntd_reason"] = wr
                ntd_accepts.append(a)
                break

    if not ntd_accepts:
        print("NTD 구간 내 ACCEPT 없음.")
        return []

    # persistence_count: 각 종목이 이전 5 거래일 NTD 구간에 몇 번 등장했는가
    # ntd_accepts 전체(중복 포함)에서 날짜별 등장 기록 구축
    _all_days_set = set(_trading_days(days[0], days[-1]))
    _trading_day_list = sorted(_all_days_set)

    # 종목별 NTD ACCEPT 날짜 집합 (중복 제거)
    _stock_ntd_dates: dict[str, set[date]] = defaultdict(set)
    for a in ntd_accepts:
        _stock_ntd_dates[a["code"]].add(a["ts"].date())

    def _persistence_count(code: str, ref_date: date) -> int:
        """ref_date 포함, 이전 5 거래일(당일 포함) 내 NTD ACCEPT 출현 날짜 수."""
        try:
            idx = _trading_day_list.index(ref_date)
        except ValueError:
            return 0
        window = set(_trading_day_list[max(0, idx - 4): idx + 1])
        return len(window & _stock_ntd_dates.get(code, set()))

    def _consecutive_days(code: str, ref_date: date) -> int:
        """ref_date 기준 역방향으로 연속 거래일 몇 일 NTD에 등장했는가.
        (ref_date 포함 카운트: 1이면 오늘만, 2이면 어제+오늘, ...)
        """
        try:
            idx = _trading_day_list.index(ref_date)
        except ValueError:
            return 1
        hit = _stock_ntd_dates.get(code, set())
        count = 0
        for i in range(idx, -1, -1):
            if _trading_day_list[i] in hit:
                count += 1
            else:
                break
        return max(count, 1)  # 현재일은 항상 포함이므로 최소 1

    # 중복 제거: 날짜 + 종목 기준 alpha 최고값 1건
    best: dict[tuple, dict] = {}
    for a in ntd_accepts:
        key = (a["ts"].date(), a["code"])
        if key not in best or a["alpha"] > best[key]["alpha"]:
            best[key] = a

    # persistence / consecutive 주입
    for a in best.values():
        ref = a["ts"].date()
        a["persistence_count"] = _persistence_count(a["code"], ref)
        a["consecutive_days"]  = _consecutive_days(a["code"], ref)

    unique_accepts = sorted(best.values(), key=lambda x: (x["ts"].date(), -x["alpha"]))

    print(f"\n분석 대상: {len(unique_accepts)}건 (원본 {len(ntd_accepts)}건 → 중복 제거)")

    if not fetch_price:
        return [{"date": a["ts"].strftime("%Y-%m-%d"), "code": a["code"],
                 "alpha": a["alpha"], "conf": a["conf"],
                 "ntd_reason": a.get("ntd_reason", "")} for a in unique_accepts]

    # 가격 데이터 일괄 수집 (종목별 1회)
    code_dates: dict[str, date] = {}
    for a in unique_accepts:
        d = a["ts"].date()
        if a["code"] not in code_dates or d < code_dates[a["code"]]:
            code_dates[a["code"]] = d

    print(f"가격 조회: {len(code_dates)}개 종목 (yfinance)...")
    ohlcv_cache: dict[str, dict | None] = {}
    for code, first_date in code_dates.items():
        print(f"  {code}...", end=" ", flush=True)
        ohlcv_cache[code] = _fetch_ohlcv(code, first_date - timedelta(days=25))
        print("ok" if ohlcv_cache[code] else "실패")

    # KOSDAQ 일별 수익률 (전체 분석 기간 커버)
    kosdaq_rets = _kosdaq_returns(days[0], end=days[-1])

    # Feature 계산
    results = []
    for a in unique_accepts:
        rec = _compute_features(a, ohlcv_cache.get(a["code"]), kosdaq_rets)
        results.append(rec)

    return results


# ── 출력 ──────────────────────────────────────────────────────────────────────

def _alpha_bucket(alpha: float) -> str:
    if alpha >= 2.0:  return "A:≥2.0"
    if alpha >= 1.5:  return "B:1.5~2.0"
    return                    "C:<1.5"


def print_summary(results: list[dict]) -> None:
    valid = [r for r in results if not r.get("price_unavailable")]
    na    = [r for r in results if r.get("price_unavailable")]

    print(f"\n{'='*64}")
    print(f"NTD Alpha Tracker  총 {len(results)}건  (가격 없음: {len(na)}건)")
    print(f"{'='*64}")

    if not valid:
        print("가격 데이터 없음 — forward return 분석 불가")
        return

    # Alpha bucket별 집계
    buckets: dict[str, list] = defaultdict(list)
    for r in valid:
        buckets[_alpha_bucket(r["alpha"])].append(r)

    print(f"\n[Alpha Bucket 별 Outcome]")
    print(f"  {'Bucket':12s}  {'건수':>4}  {'day_ret':>8}  {'rec_str':>8}  "
          f"{'rs_kq':>8}  {'1d':>7}  {'5d':>7}  {'gap':>7}  {'ldr_sc':>7}  {'survived':>8}")
    print(f"  {'-'*88}")
    for bucket in ["A:≥2.0", "B:1.5~2.0", "C:<1.5"]:
        rs = buckets.get(bucket, [])
        if not rs:
            continue
        def _avg(key):
            vs = [r[key] for r in rs if r.get(key) is not None]
            return f"{sum(vs)/len(vs):+.1f}" if vs else "  N/A"
        surv = sum(1 for r in rs if r.get("survived"))
        surv_pct = f"{surv/len(rs)*100:.0f}%" if rs else "N/A"
        print(f"  {bucket:12s}  {len(rs):>4}  {_avg('day_return'):>8}  "
              f"{_avg('recovery_strength'):>8}  {_avg('rs_vs_kosdaq'):>8}  "
              f"{_avg('next_1d_ret'):>7}  {_avg('next_5d_ret'):>7}  "
              f"{_avg('next_day_gap'):>7}  {_avg('leader_score'):>7}  {surv_pct:>8}")

    # Archetype 분포
    print(f"\n[Leader Archetype 분류]")
    archetype_order = ["TYPE_AB", "TYPE_A", "TYPE_B", "TYPE_C"]
    arch_groups: dict[str, list] = defaultdict(list)
    for r in valid:
        arch_groups[r.get("archetype", "TYPE_C")].append(r)
    arch_desc = {
        "TYPE_AB": "Explosion+Persistent (최강)",
        "TYPE_A":  "Explosion  (day≥15%+rec≥0.85)",
        "TYPE_B":  "Persistent (연속≥2일+rec≥0.50)",
        "TYPE_C":  "Noise      (기준 미달)",
    }
    for at in archetype_order:
        rs = arch_groups.get(at, [])
        if not rs:
            continue
        codes = ", ".join(f"{r['code']}({r['date'][5:]})" for r in rs)
        def _avg_at(key):
            vs = [r[key] for r in rs if r.get(key) is not None]
            return f"{sum(vs)/len(vs):+.1f}" if vs else "N/A"
        print(f"  {at:10s} ({arch_desc[at]})")
        print(f"            {len(rs):>2}건  [{codes}]")
        print(f"            day={_avg_at('day_return')}%  rec={_avg_at('recovery_strength')}  "
              f"ldr={_avg_at('leader_score')}  5d={_avg_at('next_5d_ret')}")

    # Leader Score 상위 종목
    print(f"\n[Leader Score 상위 종목]")
    top = sorted(valid, key=lambda r: r.get("leader_score") or 0, reverse=True)[:8]
    print(f"  {'날짜':10s}  {'종목':8s}  {'arch':>7}  {'alpha':>6}  {'day%':>7}  "
          f"{'rec':>5}  {'rs_kq':>6}  {'1d':>7}  {'5d':>7}  {'cons':>5}  {'ldr_sc':>7}")
    print(f"  {'-'*90}")
    def _fmt(v, fmt="+.1f"):
        return f"{v:{fmt}}" if v is not None else "  N/A"
    for r in top:
        print(
            f"  {r['date']:10s}  {r['code']:8s}  {r.get('archetype','?'):>7s}  "
            f"{r['alpha']:>+6.2f}  {_fmt(r.get('day_return')):>7}  "
            f"{_fmt(r.get('recovery_strength'), '.2f'):>5}  "
            f"{_fmt(r.get('rs_vs_kosdaq')):>6}  "
            f"{_fmt(r.get('next_1d_ret')):>7}  {_fmt(r.get('next_5d_ret')):>7}  "
            f"{r.get('consecutive_days', 1):>5d}  {r.get('leader_score') or 0:>7.3f}"
        )

    # Recovery strength 구간별
    print(f"\n[Recovery Strength 구간별 5일 수익률]")
    def _rs_bucket(r):
        v = r.get("recovery_strength")
        if v is None:   return "N/A"
        if v >= 0.8:    return "강(≥0.8)"
        if v >= 0.5:    return "중(0.5~)"
        return                 "약(<0.5)"
    rs_buckets: dict[str, list] = defaultdict(list)
    for r in valid:
        rs_buckets[_rs_bucket(r)].append(r)
    for label in ["강(≥0.8)", "중(0.5~)", "약(<0.5)"]:
        rs = rs_buckets.get(label, [])
        if not rs: continue
        fives = [r["next_5d_ret"] for r in rs if r.get("next_5d_ret") is not None]
        avg5  = f"{sum(fives)/len(fives):+.1f}%" if fives else "N/A"
        surv  = sum(1 for r in rs if r.get("survived"))
        print(f"  {label:10s}: {len(rs):>3}건  avg 5d={avg5}  "
              f"survived={surv}/{len(rs)} ({surv/len(rs)*100:.0f}%)")

    # 날짜별 NTD 요약
    print(f"\n[날짜별 NTD ACCEPT 요약]")
    by_date: dict[str, list] = defaultdict(list)
    for r in valid:
        by_date[r["date"]].append(r)
    for d in sorted(by_date):
        rs = by_date[d]
        alphas = [r["alpha"] for r in rs]
        fives  = [r["next_5d_ret"] for r in rs if r.get("next_5d_ret") is not None]
        avg5   = f"{sum(fives)/len(fives):+.1f}%" if fives else "N/A"
        reason = rs[0].get("ntd_reason", "")[:40]
        print(f"  {d}  {len(rs):>3}건  max_alpha={max(alphas):+.2f}  "
              f"5d_avg={avg5}  [{reason}]")

    print(f"\n{'='*64}\n")


def save_outputs(results: list[dict], target_date: date | None = None) -> None:
    d    = target_date or date.today()
    stem = d.strftime("%Y%m%d")
    json_path = LOG_DIR / f"ntd_alpha_{stem}.json"
    csv_path  = LOG_DIR / f"ntd_alpha_{stem}.csv"

    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: {json_path}")

    if results:
        fieldnames = list(results[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
        print(f"CSV:  {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NTD Alpha Forward Outcome Tracker")
    parser.add_argument("--days",     type=int,  default=30)
    parser.add_argument("--date",     type=str,  default=None)
    parser.add_argument("--no-price", action="store_true")
    parser.add_argument("--no-save",  action="store_true")
    args = parser.parse_args()

    target = date.today()
    if args.date:
        from datetime import datetime as _dt
        target = _dt.strptime(args.date, "%Y%m%d").date()

    results = run(days_back=args.days, target_date=target,
                  fetch_price=not args.no_price)
    print_summary(results)

    if not args.no_save and results:
        save_outputs(results, target)
