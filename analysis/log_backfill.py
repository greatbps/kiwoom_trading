"""
analysis/log_backfill.py

auto_trading_YYYYMMDD.log → log_trade_events 역주입 (backfill)

동작:
  1. 로그 파싱: 신호(TREND/EXPLORATION/SMC) + BUY 체결 매칭
  2. 기존 DB 행 UPDATE: regime / entry_reason / time_bucket NULL 채우기
  3. DB에 없는 신규 체결 UPSERT

매칭 알고리즘 (실측 기반):
  - 신호→BUY 시간차: 0초 (즉시)
  - 중복: 완전 동일 타임스탬프 2줄 → (ticker, ts, tag) dedup
  - 1분 간격 재평가 → first signal wins (2분 윈도우)
  - SMC 구버전(2026-02): [SMC_SIG] 태그 없음 → CHoCH 상세 로그 fallback
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─── 경로 ─────────────────────────────────────────────────────────
KIWOOM_DIR      = Path(os.getenv("KIWOOM_DIR", "/home/greatbps/projects/kiwoom_trading"))
DEFAULT_LOG_DIR = KIWOOM_DIR / "logs"

# ─── DB 연결 ──────────────────────────────────────────────────────
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB",   "trading_system")
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")

# ─── 매칭 파라미터 ────────────────────────────────────────────────
SIGNAL_WINDOW = timedelta(minutes=2)   # 신호→BUY 최대 허용 시간차

# ─── 정규식 ───────────────────────────────────────────────────────
_TS_RE        = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_TS_INLINE_RE = re.compile(r"시간:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")  # 구버전: 매수블록 내부
_BUY_RE = re.compile(r"✓ 거래 기록: (.+?) \((\d{6})\) BUY (\d+)주 @ ([\d,]+)원")
_SELL_RE = re.compile(
    r"🔔 매도 신호 발생: .+? \((\d{6})\).*?수익률:\s*([+-]?\d+\.?\d*)%.*?사유:\s*(.+?)(?:\n|$)",
    re.DOTALL,
)

# 신호 태그별 (컴파일된 패턴, regime, entry_reason_template)
_SIGNAL_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\[TREND_SIG\] (\d{6}) .+?TREND (BREAKOUT|PULLBACK)\[(\w+)\]"),
        "TREND",
        "TREND_{type}_{grade}",
    ),
    (
        re.compile(r"\[EXPLORATION_ENTRY\] (\d{6})"),
        "EXPLORATION",
        "EXPLORATION",
    ),
    (
        re.compile(r"\[SMC_SIG\] (\d{6})"),
        "SMC",
        "SMC",
    ),
    (
        re.compile(r"\[C_GRADE_FALLBACK\] (\d{6})"),
        "SMC",
        "SMC_C_FALLBACK",
    ),
    (
        re.compile(r"\[SWEEP_FALLBACK\] (\d{6})"),
        "SMC",
        "SMC_B_NOSWEEP",
    ),
]

# SMC 구버전 fallback: "ticker ... CHoCH[A/B급] 상승 발생"
_SMC_LEGACY_RE = re.compile(r"(\d{6}).*?CHoCH\[([AB])급\].*?상승 발생")

# 구버전 매수 신호 블록: "🔔 매수 신호 발생: 종목명 (ticker)"
_BUY_SIG_LEGACY_RE = re.compile(r"🔔 매수 신호 발생: .+? \((\d{6})\)")

# 크로스데이 SELL: 다른 날 로그에서 BUY 정보 보완 (진입가 블록)
# "매수가: 24,700원" or "진입가: 24,700원"
_ENTRY_PRICE_RE = re.compile(r"(?:매수가|진입가):\s*([\d,]+)원")


# ─── 데이터 클래스 ────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalContext:
    ticker: str
    regime: str
    entry_reason: str
    signal_ts: datetime
    tag: str          # dedup용 태그 식별자


@dataclass
class TradeContext:
    ticker: str
    date_str: str     # YYYYMMDD
    symbol: str
    entry_price: int
    entry_ts: datetime
    time_bucket: str
    regime: str
    entry_reason: str
    exit_reason: str | None = None
    pnl_pct: float | None = None
    result: str | None = None


# ─── 내부 유틸 ────────────────────────────────────────────────────

def _get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=int(DB_PORT), database=DB_NAME,
        user=DB_USER, password=DB_PASS,
    )


def _parse_ts(line: str) -> datetime | None:
    """줄 앞 타임스탬프 (신버전) 또는 '시간:' 인라인 타임스탬프 (구버전) 추출."""
    m = _TS_RE.match(line)
    if not m:
        m = _TS_INLINE_RE.search(line)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1))
    except ValueError:
        return None


def _time_bucket(ts: datetime) -> str:
    m = (ts.minute // 30) * 30
    return f"{ts.hour:02d}:{m:02d}"


def _extract_date_from_path(log_path: Path) -> str | None:
    m = re.search(r"(\d{8})", log_path.stem)
    return m.group(1) if m else None


# ─── 핵심: 단일 로그 파일 파싱 ───────────────────────────────────

def parse_log_file(log_path: Path) -> list[TradeContext]:
    """
    단일 auto_trading_YYYYMMDD.log → TradeContext 목록

    매칭 알고리즘:
      1. 신호 수집: (ticker, ts_sec, tag) dedup → signal_map[ticker]
      2. BUY 수집
      3. BUY마다 2분 윈도우 내 첫 신호 선택 (first wins)
      4. SELL 매칭
      5. SMC fallback: 신호 없는 BUY에 CHoCH 로그 매핑
    """
    date_str = _extract_date_from_path(log_path)
    if not date_str:
        logger.warning("[BACKFILL] 날짜 추출 실패: %s", log_path)
        return []

    try:
        text = log_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("[BACKFILL] 파일 읽기 실패: %s — %s", log_path, exc)
        return []

    lines = text.splitlines()

    # ── 신호 수집 + dedup ─────────────────────────────────────────
    signal_map: dict[str, list[SignalContext]] = {}   # ticker → [SignalContext]
    seen_sigs: set[tuple[str, str, str]] = set()      # (ticker, ts_sec, tag_id)
    legacy_smc: dict[str, list[tuple[datetime, str]]] = {}  # ticker → [(ts, grade)]
    current_ts: datetime | None = None

    for line in lines:
        ts = _parse_ts(line)
        if ts:
            current_ts = ts
        if not current_ts:
            continue

        for pattern, regime, reason_tmpl in _SIGNAL_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue

            ticker = m.group(1)
            if "{type}" in reason_tmpl and len(m.groups()) >= 3:
                entry_reason = reason_tmpl.format(
                    type=m.group(2).upper(),
                    grade=m.group(3).upper(),
                )
            else:
                entry_reason = reason_tmpl

            tag_id = pattern.pattern[:15]
            dedup_key = (ticker, current_ts.strftime("%Y%m%d%H%M%S"), tag_id)
            if dedup_key not in seen_sigs:
                seen_sigs.add(dedup_key)
                signal_map.setdefault(ticker, []).append(
                    SignalContext(
                        ticker=ticker,
                        regime=regime,
                        entry_reason=entry_reason,
                        signal_ts=current_ts,
                        tag=tag_id,
                    )
                )
            break

        # SMC legacy fallback: CHoCH 상세 로그
        m_leg = _SMC_LEGACY_RE.search(line)
        if m_leg:
            legacy_smc.setdefault(m_leg.group(1), []).append(
                (current_ts, m_leg.group(2))
            )

        # 구버전 매수 신호 블록 ("🔔 매수 신호 발생")
        m_buy_sig = _BUY_SIG_LEGACY_RE.search(line)
        if m_buy_sig:
            ticker_leg = m_buy_sig.group(1)
            dedup_key = (ticker_leg, current_ts.strftime("%Y%m%d%H%M%S"), "BUY_SIG")
            if dedup_key not in seen_sigs:
                seen_sigs.add(dedup_key)
                signal_map.setdefault(ticker_leg, []).append(
                    SignalContext(
                        ticker=ticker_leg,
                        regime="SMC",
                        entry_reason="SMC",
                        signal_ts=current_ts,
                        tag="BUY_SIG",
                    )
                )

    # ── BUY 수집 ──────────────────────────────────────────────────
    @dataclass
    class BuyRecord:
        symbol: str
        ticker: str
        entry_price: int
        entry_ts: datetime

    buys: list[BuyRecord] = []
    current_ts = None
    for line in lines:
        ts = _parse_ts(line)
        if ts:
            current_ts = ts
        m = _BUY_RE.search(line)
        if m and current_ts:
            symbol, ticker, _, price_str = m.groups()
            buys.append(BuyRecord(
                symbol=symbol,
                ticker=ticker,
                entry_price=int(price_str.replace(",", "")),
                entry_ts=current_ts,
            ))

    # ── SELL 수집 (당일 로그 내) ──────────────────────────────────
    sell_map: dict[str, tuple[float, str]] = {}
    for m in _SELL_RE.finditer(text):
        try:
            sell_map[m.group(1)] = (float(m.group(2)), m.group(3).strip())
        except ValueError:
            pass

    # ── BUY + 신호 매칭 ───────────────────────────────────────────
    used_sig_ids: set[int] = set()
    trades: list[TradeContext] = []

    for buy in buys:
        regime, entry_reason = "SMC", "SMC"

        candidates = [
            ctx for ctx in signal_map.get(buy.ticker, [])
            if id(ctx) not in used_sig_ids
            and ctx.signal_ts <= buy.entry_ts
            and buy.entry_ts - ctx.signal_ts <= SIGNAL_WINDOW
        ]

        if candidates:
            best = candidates[0]
            used_sig_ids.add(id(best))
            regime = best.regime
            entry_reason = best.entry_reason
        else:
            # SMC legacy fallback
            for ts_leg, grade in legacy_smc.get(buy.ticker, []):
                if ts_leg <= buy.entry_ts and buy.entry_ts - ts_leg <= SIGNAL_WINDOW:
                    regime = "SMC"
                    entry_reason = f"SMC_{grade}_GRADE"
                    break

        sell = sell_map.get(buy.ticker)
        pnl_pct = sell[0] if sell else None
        exit_reason = sell[1] if sell else None

        trades.append(TradeContext(
            ticker=buy.ticker,
            date_str=date_str,
            symbol=buy.symbol,
            entry_price=buy.entry_price,
            entry_ts=buy.entry_ts,
            time_bucket=_time_bucket(buy.entry_ts),
            regime=regime,
            entry_reason=entry_reason,
            exit_reason=exit_reason,
            pnl_pct=pnl_pct,
            result=("WIN" if pnl_pct and pnl_pct > 0 else "LOSS") if pnl_pct is not None else None,
        ))

    return trades


# ─── DB 조작 ──────────────────────────────────────────────────────

def _db_update(cur, trade: TradeContext, force: bool) -> int:
    trade_date = datetime.strptime(trade.date_str, "%Y%m%d").date()
    if force:
        sql = """
            UPDATE log_trade_events
            SET regime       = %s,
                entry_reason = %s,
                time_bucket  = %s,
                timestamp    = COALESCE(timestamp, %s)
            WHERE ticker = %s AND trade_date = %s
              AND kind = 'trade' AND pnl IS NOT NULL
        """
    else:
        sql = """
            UPDATE log_trade_events
            SET regime       = COALESCE(regime,       %s),
                entry_reason = COALESCE(entry_reason, %s),
                time_bucket  = COALESCE(time_bucket,  %s),
                timestamp    = COALESCE(timestamp,    %s)
            WHERE ticker = %s AND trade_date = %s
              AND kind = 'trade' AND pnl IS NOT NULL
              AND (regime IS NULL OR entry_reason IS NULL OR time_bucket IS NULL)
        """
    cur.execute(sql, (
        trade.regime, trade.entry_reason, trade.time_bucket, trade.entry_ts,
        trade.ticker, trade_date,
    ))
    return cur.rowcount


def _db_upsert(cur, trade: TradeContext) -> int:
    trade_date = datetime.strptime(trade.date_str, "%Y%m%d").date()
    cur.execute(
        """
        INSERT INTO log_trade_events (
            timestamp, trade_date, time_bucket,
            source_file, source_tag, kind,
            ticker, symbol, regime, entry_reason,
            exit_reason, price, result, pnl
        )
        VALUES (%s,%s,%s,%s,'LOG_BACKFILL','trade',%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING
        """,
        (
            trade.entry_ts, trade_date, trade.time_bucket,
            f"auto_trading_{trade.date_str}.log",
            trade.ticker, trade.symbol,
            trade.regime, trade.entry_reason,
            trade.exit_reason, trade.entry_price,
            trade.result, trade.pnl_pct,
        ),
    )
    return cur.rowcount


def _exists_in_db(cur, ticker: str, date_str: str) -> bool:
    trade_date = datetime.strptime(date_str, "%Y%m%d").date()
    cur.execute(
        """SELECT 1 FROM log_trade_events
           WHERE ticker=%s AND trade_date=%s AND kind='trade' AND pnl IS NOT NULL LIMIT 1""",
        (ticker, trade_date),
    )
    return cur.fetchone() is not None


# ─── Public API ───────────────────────────────────────────────────

def backfill_db(
    *,
    days: int = 90,
    dry_run: bool = False,
    force: bool = False,
    log_dir: Path | None = None,
    date_str: str | None = None,
) -> dict[str, Any]:
    """로그 → DB backfill 메인 함수."""
    base_dir = log_dir or DEFAULT_LOG_DIR
    result: dict[str, Any] = {
        "days": days, "files_scanned": 0,
        "trades_parsed": 0, "updated": 0,
        "inserted": 0, "skipped": 0,
        "errors": 0, "dry_run": dry_run,
    }

    if date_str:
        files = list(base_dir.glob(f"auto_trading_{date_str}.log"))
    else:
        cutoff = datetime.now() - timedelta(days=days)
        files = []
        for f in sorted(base_dir.glob("auto_trading_????????.log")):
            d = _extract_date_from_path(f)
            if d:
                try:
                    if datetime.strptime(d, "%Y%m%d") >= cutoff:
                        files.append(f)
                except ValueError:
                    pass

    if not files:
        logger.info("[BACKFILL] 처리할 로그 파일 없음")
        return result

    result["files_scanned"] = len(files)

    # 크로스데이 SELL 보완: 전체 파일에서 SELL 맵 먼저 수집
    # {ticker: (pnl_pct, exit_reason)} — 최신 값 우선
    global_sell_map: dict[str, tuple[float, str]] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in _SELL_RE.finditer(text):
            try:
                global_sell_map[m.group(1)] = (float(m.group(2)), m.group(3).strip())
            except ValueError:
                pass

    if dry_run:
        for f in files:
            trades = parse_log_file(f)
            result["trades_parsed"] += len(trades)
            for t in trades:
                print(f"  [DRY] {t.date_str} {t.ticker} {t.symbol:<12} "
                      f"regime={t.regime:<12} reason={t.entry_reason:<25} "
                      f"bucket={t.time_bucket} pnl={t.pnl_pct}")
        return result

    try:
        conn = _get_conn()
    except Exception as exc:
        logger.error("[BACKFILL] DB 연결 실패: %s", exc)
        result["errors"] += 1
        return result

    try:
        with conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            for f in files:
                try:
                    trades = parse_log_file(f)
                    result["trades_parsed"] += len(trades)
                    # 크로스데이 SELL 보완: 당일 sell 없으면 global에서 채움
                    for trade in trades:
                        if trade.exit_reason is None and trade.ticker in global_sell_map:
                            pnl, reason = global_sell_map[trade.ticker]
                            object.__setattr__(trade, "exit_reason", reason) if hasattr(trade, "__dataclass_fields__") else None
                            trade.exit_reason = reason
                            trade.pnl_pct    = pnl
                            trade.result     = "WIN" if pnl > 0 else "LOSS"

                    for trade in trades:
                        try:
                            if _exists_in_db(cur, trade.ticker, trade.date_str):
                                n = _db_update(cur, trade, force=force)
                                if n > 0:
                                    result["updated"] += n
                                    logger.debug("[BACKFILL] UPDATE %s %s", trade.date_str, trade.ticker)
                                else:
                                    result["skipped"] += 1
                            else:
                                n = _db_upsert(cur, trade)
                                if n > 0:
                                    result["inserted"] += 1
                                    logger.info("[BACKFILL] INSERT %s %s regime=%s", trade.date_str, trade.ticker, trade.regime)
                                else:
                                    result["skipped"] += 1
                        except Exception as exc:
                            logger.warning("[BACKFILL] 레코드 실패 %s %s: %s", trade.date_str, trade.ticker, exc)
                            result["errors"] += 1
                except Exception as exc:
                    logger.warning("[BACKFILL] 파일 실패 %s: %s", f, exc)
                    result["errors"] += 1
    finally:
        conn.close()

    logger.info(
        "[BACKFILL] 완료 — 파일=%d 파싱=%d UPDATE=%d INSERT=%d SKIP=%d ERR=%d",
        result["files_scanned"], result["trades_parsed"],
        result["updated"], result["inserted"], result["skipped"], result["errors"],
    )
    return result


def backfill_summary(*, days: int = 30) -> str:
    """DB NULL 현황 집계."""
    try:
        conn = _get_conn()
    except Exception as exc:
        return f"[BACKFILL] DB 연결 실패: {exc}"

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cutoff = (datetime.now() - timedelta(days=days)).date()
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN regime       IS NULL THEN 1 ELSE 0 END) AS regime_null,
                    SUM(CASE WHEN entry_reason IS NULL THEN 1 ELSE 0 END) AS reason_null,
                    SUM(CASE WHEN time_bucket  IS NULL THEN 1 ELSE 0 END) AS bucket_null
                FROM log_trade_events
                WHERE kind='trade' AND pnl IS NOT NULL
                  AND (trade_date IS NULL OR trade_date >= %s)
                """,
                (cutoff,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row or not row["total"]:
        return f"[BACKFILL 현황] 최근 {days}일 — 데이터 없음"

    total = int(row["total"])
    rn = int(row["regime_null"] or 0)
    an = int(row["reason_null"] or 0)
    bn = int(row["bucket_null"] or 0)

    lines = [
        f"[BACKFILL 현황] 최근 {days}일",
        f"  전체 trade 행:     {total}",
        f"  regime NULL:       {rn} ({rn/total*100:.1f}%)",
        f"  entry_reason NULL: {an} ({an/total*100:.1f}%)",
        f"  time_bucket NULL:  {bn} ({bn/total*100:.1f}%)",
    ]
    lines.append(
        "\n권장: python3 -m analysis.log_backfill --days " + str(days)
        if rn + an + bn > 0 else "\n✓ 모든 컬럼 채워짐"
    )
    return "\n".join(lines)


# ─── CLI ──────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="log → DB backfill")
    parser.add_argument("--days",    type=int,           default=90)
    parser.add_argument("--date",    type=str,           default=None, help="YYYYMMDD")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.summary:
        print(backfill_summary(days=args.days))
        return 0

    result = backfill_db(
        days=args.days,
        dry_run=args.dry_run,
        force=args.force,
        date_str=args.date,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
