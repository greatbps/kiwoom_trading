"""
analysis/post_exit_tracker.py — 청산 후 가격 추적 (D+1~D+3)

목적: "내 청산이 너무 빨랐는지" 사후 판단
  - SELL 이후 D+1/D+2/D+3 실제 가격 조회
  - 조기청산 / 정상청산 / 횡보 자동 분류
  - 스윙 전략 청산 품질 지표

사용법:
    python -m analysis.post_exit_tracker [--days 30] [--no-save]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

_ROOT     = Path(__file__).parent.parent
_DB_PATH  = _ROOT / "data" / "trades.db"
_CACHE    = _ROOT / "data" / "post_exit_cache.json"
_REPORT   = _ROOT / "data" / "post_exit_report.json"

# D+N 추적 기간
_DAYS_TRACK = [1, 2, 3]
# 조기청산 판정: 이후 N일 수익이 exit 대비 이만큼 더 높으면 조기청산
_EARLY_EXIT_DELTA = 1.5   # %
# 정상청산 판정: 이후 N일이 하락 (-1% 이하)
_GOOD_EXIT_DELTA  = -1.0  # %


# ── DB 로드 ───────────────────────────────────────────────────────

def _load_sells(days: int = 0) -> list[dict]:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    if days > 0:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT * FROM trades
            WHERE trade_type = 'SELL' AND trade_date >= ?
            ORDER BY timestamp DESC
        """, (since,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM trades
            WHERE trade_type = 'SELL'
            ORDER BY timestamp DESC
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_buys_map() -> dict[str, dict]:
    """stock_code → 가장 최근 BUY (종목별 1건, 간이 매칭)"""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM trades
        WHERE trade_type = 'BUY'
        ORDER BY timestamp DESC
    """).fetchall()
    conn.close()
    out = {}
    for r in rows:
        r = dict(r)
        key = f"{r['stock_code']}_{r['timestamp'][:10]}"
        out[key] = r
    return out


# ── yfinance 가격 조회 ────────────────────────────────────────────

def _get_post_prices(stock_code: str, exit_date: str, cache: dict) -> dict[int, float | None]:
    """exit_date(YYYY-MM-DD) 이후 D+1~D+3 종가 반환. 캐시 활용."""
    cache_key = f"{stock_code}_{exit_date}"
    if cache_key in cache:
        return {int(k): v for k, v in cache[cache_key].items()}

    result: dict[int, float | None] = {}
    exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
    # D+3까지 커버하려면 D+7 범위로 조회 (휴장일 포함)
    fetch_end = (exit_dt + timedelta(days=8)).strftime("%Y-%m-%d")

    for suffix in (".KS", ".KQ"):
        try:
            ticker = f"{stock_code}{suffix}"
            df = yf.download(ticker, start=exit_date, end=fetch_end,
                             interval="1d", progress=False, auto_adjust=True)
            if df.empty:
                continue
            closes = df['Close'].dropna()
            # exit_date 이후의 거래일 가격 (exit_date 자체 제외)
            after = closes[closes.index > pd.Timestamp(exit_date)]
            for n in _DAYS_TRACK:
                result[n] = float(after.iloc[n - 1].item()) if len(after) >= n else None
            break
        except Exception as e:
            logger.debug(f"[POST_EXIT] yfinance {stock_code}{suffix}: {e}")

    cache[cache_key] = {str(k): v for k, v in result.items()}
    return result


# ── 분석 ─────────────────────────────────────────────────────────

def analyze(sells: list[dict], buys_map: dict, cache: dict) -> list[dict]:
    rows = []
    for s in sells:
        exit_date  = s['trade_date']  # YYYY-MM-DD
        stock_code = s['stock_code']
        exit_price = s['price']
        reason     = s.get('reason', '')
        strategy   = s.get('strategy', '')
        realized   = s.get('realized_pnl', 0) or 0

        # BUY 매칭: 같은 날 또는 직전 거래일
        entry_price = None
        for delta in range(0, 5):
            chk = (datetime.strptime(exit_date, "%Y-%m-%d") - timedelta(days=delta)).strftime("%Y-%m-%d")
            key = f"{stock_code}_{chk}"
            if key in buys_map:
                entry_price = buys_map[key]['price']
                break

        if entry_price is None or entry_price == 0:
            # realized_pnl로 역산
            qty = s.get('quantity', 1) or 1
            if qty and realized:
                entry_price = exit_price - realized / qty

        if not entry_price or entry_price == 0:
            continue

        exit_pnl = (exit_price - entry_price) / entry_price * 100

        # D+1~D+3 가격
        post = _get_post_prices(stock_code, exit_date, cache)

        d_returns = {}
        d_vs_exit  = {}
        for n in _DAYS_TRACK:
            p = post.get(n)
            if p and p > 0:
                d_returns[n] = (p - entry_price) / entry_price * 100
                d_vs_exit[n] = d_returns[n] - exit_pnl
            else:
                d_returns[n] = None
                d_vs_exit[n] = None

        # 분류
        best_d  = max((v for v in d_vs_exit.values() if v is not None), default=None)
        if best_d is None:
            verdict = "데이터없음"
        elif best_d >= _EARLY_EXIT_DELTA:
            verdict = "조기청산"     # 팔고 나서 더 올랐음
        elif best_d <= _GOOD_EXIT_DELTA:
            verdict = "정상청산"     # 팔고 나서 내렸음
        else:
            verdict = "횡보"

        rows.append({
            'date':       exit_date,
            'code':       stock_code,
            'name':       s.get('stock_name', ''),
            'entry':      round(entry_price, 0),
            'exit':       exit_price,
            'exit_pnl':   round(exit_pnl, 2),
            'reason':     reason[:40],
            'd1_pnl':     round(d_returns.get(1), 2) if d_returns.get(1) is not None else None,
            'd2_pnl':     round(d_returns.get(2), 2) if d_returns.get(2) is not None else None,
            'd3_pnl':     round(d_returns.get(3), 2) if d_returns.get(3) is not None else None,
            'd1_vs_exit': round(d_vs_exit.get(1), 2) if d_vs_exit.get(1) is not None else None,
            'd2_vs_exit': round(d_vs_exit.get(2), 2) if d_vs_exit.get(2) is not None else None,
            'd3_vs_exit': round(d_vs_exit.get(3), 2) if d_vs_exit.get(3) is not None else None,
            'verdict':    verdict,
            'strategy':   strategy,
        })

    return rows


# ── 리포트 출력 ───────────────────────────────────────────────────

def print_report(rows: list[dict]):
    if not rows:
        print("⚠️  분석 가능한 청산 데이터 없음")
        return

    total = len(rows)
    v_counts = {"조기청산": 0, "정상청산": 0, "횡보": 0, "데이터없음": 0}
    for r in rows:
        v_counts[r['verdict']] = v_counts.get(r['verdict'], 0) + 1

    print("\n" + "=" * 70)
    print("  청산 후 D+1~D+3 추적 분석")
    print("=" * 70)
    print(f"  총 {total}건 분석")
    print()

    # 판정 분포
    print("  [판정 분포]")
    for v, n in v_counts.items():
        if n == 0:
            continue
        bar = "█" * int(n / total * 20)
        print(f"  {v:8s} {n:3d}건 ({n/total:.0%}) {bar}")
    print()

    # 청산 이유별 조기청산 비율
    reason_map: dict[str, list[str]] = {}
    for r in rows:
        cat = _categorize_reason(r['reason'])
        reason_map.setdefault(cat, []).append(r['verdict'])
    print("  [청산 사유별 조기청산률]")
    print(f"  {'사유':22s} {'N':>4} {'조기청산':>8} {'정상청산':>8}")
    print("  " + "-" * 46)
    for cat, verdicts in sorted(reason_map.items(), key=lambda x: -len(x[1])):
        n    = len(verdicts)
        early = verdicts.count("조기청산") / n
        good  = verdicts.count("정상청산") / n
        flag  = " ⚠️" if early > 0.4 else ""
        print(f"  {cat[:22]:22s} {n:>4} {early:>8.0%} {good:>8.0%}{flag}")
    print()

    # 종목별 상세 (최근 20건)
    print("  [최근 청산 상세]")
    print(f"  {'날짜':10s} {'종목':8s} {'청산%':>7} {'D+1':>6} {'D+2':>6} {'D+3':>6} {'판정':8s} {'사유'}")
    print("  " + "-" * 70)
    for r in rows[:20]:
        def _fmt(v):
            return f"{v:+.1f}%" if v is not None else "  N/A"
        verdict_mark = {"조기청산": "🔴", "정상청산": "🟢", "횡보": "🟡"}.get(r['verdict'], "⬜")
        print(
            f"  {r['date']:10s} {r['name'][:8]:8s} "
            f"{r['exit_pnl']:>+6.1f}% "
            f"{_fmt(r['d1_vs_exit']):>7} {_fmt(r['d2_vs_exit']):>7} {_fmt(r['d3_vs_exit']):>7} "
            f"{verdict_mark}{r['verdict']:6s} {r['reason'][:20]}"
        )

    # D+N 이후 평균 추가 수익
    valid = [r for r in rows if r['d3_vs_exit'] is not None]
    if valid:
        avg_d3 = sum(r['d3_vs_exit'] for r in valid) / len(valid)
        print()
        print(f"  [요약] D+3 평균 추가수익: {avg_d3:+.2f}%")
        if avg_d3 > 1.0:
            print("  → 🔴 조기청산 성향 강함 (보유 기간 연장 검토)")
        elif avg_d3 < -1.0:
            print("  → 🟢 청산 타이밍 적절 (팔고 나서 하락)")
        else:
            print("  → 🟡 중립 (유의미한 패턴 없음)")

    print("=" * 70 + "\n")


def _categorize_reason(reason: str) -> str:
    r = reason.lower()
    if "hard stop" in r or "hardstop" in r:   return "hard_stop"
    if "mfe" in r:                              return "mfe_exit"
    if "early failure" in r or "ef" in r[:10]: return "early_failure"
    if "overnight" in r or "14:50" in r:       return "overnight_close"
    if "trailing" in r:                         return "trailing_stop"
    if "take profit" in r or "tp" in r[:5]:    return "take_profit"
    if "시간" in r or "time" in r:              return "time_exit"
    return "기타"


# ── 저장 ─────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict):
    _CACHE.write_text(json.dumps(cache, ensure_ascii=False))


def save_report(rows: list[dict]):
    _REPORT.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "total": len(rows),
        "rows": rows,
    }, ensure_ascii=False, indent=2))
    print(f"  → 저장: {_REPORT}")


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(message)s')

    parser = argparse.ArgumentParser(description='청산 후 D+1~D+3 추적 분석')
    parser.add_argument('--days',    type=int, default=0, help='최근 N일 (0=전체)')
    parser.add_argument('--no-save', action='store_true')
    args = parser.parse_args()

    print("📊 청산 후 가격 조회 중 (yfinance)...")
    cache  = _load_cache()
    sells  = _load_sells(days=args.days)
    buys   = _load_buys_map()

    if not sells:
        print("⚠️  SELL 데이터 없음")
        raise SystemExit(0)

    rows = analyze(sells, buys, cache)
    _save_cache(cache)

    print_report(rows)
    if not args.no_save:
        save_report(rows)
