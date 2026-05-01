"""
PostgreSQL Log Trade Extractor

- Input: auto_trading_*.log
- Output: PostgreSQL table only (no CSV/SQLite)
- Driver: psycopg2

Usage:
    python3 -m analysis.log_trade_extractor
    python3 -m analysis.log_trade_extractor --days 7
    python3 -m analysis.log_trade_extractor --glob "logs/auto_trading_202604*.log"
    python3 -m analysis.log_trade_extractor --table log_trade_events --truncate
"""

from __future__ import annotations

import argparse
import glob
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv()

RE_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+\w+\s+-\s+(?P<msg>.*)$"
)

RE_MKT_CTX = re.compile(r"\[MKT_CTX\].*")
RE_MKT_NOTRADE = re.compile(r"NO_TRADE_DAY")
RE_MKT_TRADE_OK = re.compile(r"TRADE_OK")
RE_MKT_BAD = re.compile(r"context=BAD_MARKET")

RE_ACCEPT = re.compile(
    r"✅ ACCEPT (?P<code>\w+) @(?P<price>\d+)원 \| PID:(?P<pid>\d+) \| conf=(?P<conf>[\d.]+) alpha=(?P<alpha>[+-][\d.]+) pos_mult=(?P<pos>[\d.]+)"
)
RE_SMC_SIG = re.compile(r"\[SMC_SIG\]\s+(?P<code>\w+)\s+(?P<name>[^:]+):\s*(?P<reason>.+)")
RE_TREND_SIG_CODED = re.compile(r"\[TREND_SIG\]\s+(?P<code>\w+)\s+(?P<name>[^:]+):\s*(?P<reason>.+)")
RE_TREND_SIG_GENERIC = re.compile(
    r"\[TREND_SIG\]\s+TREND\s+(?P<etype>BREAKOUT|PULLBACK)\[(?P<grade>\w+)\]:\s*(?P<detail>.+)"
)
RE_EXPL_ENTRY = re.compile(
    r"\[EXPLORATION_ENTRY\]\s+(?P<code>\w+)\s+(?P<name>[^:]+):\s*(?P<reason>.+?)\s*\|\s*size=(?P<size>[0-9.]+)"
)
RE_RS_RESULT = re.compile(
    r"\[RS_RESULT\]\s+(?P<code>\w+)\s+\|\s+exit=(?P<exit>\w+)\s+\|\s+"
    r"pnl=(?P<pnl>-?\d+\.\d+)%\s+\|\s+hold=(?P<hold>\d+\.\d+)m\s+\|\s+"
    r"MFE=(?P<mfe>-?\d+\.\d+)%\s+MAE=(?P<mae>-?\d+\.\d+)%\s+\|\s+"
    r"rs_score=(?P<score>[0-9.?]+)\s+\|\s+entry=(?P<entry>.+)"
)
RE_DEF_RESULT = re.compile(
    r"\[DEF_RESULT\]\s+(?P<code>\w+)\s+\|\s+exit=(?P<exit>[^|]+)\s+\|\s+"
    r"pnl=(?P<pnl>-?\d+\.\d+)%\s+\|\s+hold=(?P<hold>\d+\.\d+)m\s+\|\s+"
    r"MFE=(?P<mfe>-?\d+\.\d+)%\s+MAE=(?P<mae>-?\d+\.\d+)%\s+\|\s+entry=(?P<entry>.+)"
)
RE_A_PLUS_RESULT = re.compile(
    r"\[A_PLUS_RESULT:(?P<result>\w+)\]\s+(?P<code>\w+)\s+\|\s+"
    r"pnl=(?P<pnl>[+-]?\d+\.\d+)%\s+\|\s+reason=(?P<reason>[^|]+)\|\s+today_count=(?P<count>\d+)"
)
RE_CONS_HARD = re.compile(
    r"\[CONSERVATIVE_MODE\].*?\|\s*symbol=(?P<symbol>[^,|]+),\s*pnl=(?P<pnl>-?\d+\.\d+)%"
)

RE_VOL_RVOL = re.compile(r"RVOL[=:]([0-9.]+)x")
RE_VOL_MULT = re.compile(r"Vol×([0-9.]+)")
RE_BREAKOUT_PRICE = re.compile(r"\((?P<p1>\d+)→(?P<p2>\d+)")


@dataclass
class EventRow:
    timestamp: Optional[datetime]
    trade_date: Optional[datetime.date]
    time_bucket: str
    source_file: str
    source_tag: str
    kind: str
    ticker: str
    symbol: str
    regime: str
    market_context: str
    entry_reason: str
    exit_reason: str
    price: Optional[Decimal]
    volume_spike: Optional[int]
    result: str
    pnl: Optional[Decimal]
    duration_min: Optional[Decimal]


def _parse_prefix(line: str) -> tuple[Optional[datetime], str]:
    m = RE_PREFIX.match(line)
    if not m:
        return None, line.strip()
    ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
    return ts, m.group("msg").strip()


def _bucket(ts: Optional[datetime]) -> tuple[Optional[datetime.date], str]:
    if ts is None:
        return None, ""
    return ts.date(), ts.strftime("%H:%M")


def _result_from_pnl(pnl: Optional[float]) -> str:
    if pnl is None:
        return ""
    if pnl > 0:
        return "WIN"
    if pnl < 0:
        return "LOSS"
    return "BE"


def _parse_volume_spike(text: str, default_threshold: float = 1.5) -> Optional[int]:
    m1 = RE_VOL_RVOL.search(text)
    if m1:
        return 1 if float(m1.group(1)) >= default_threshold else 0
    m2 = RE_VOL_MULT.search(text)
    if m2:
        return 1 if float(m2.group(1)) >= default_threshold else 0
    return None


def _parse_price_from_reason(reason: str) -> Optional[Decimal]:
    m = RE_BREAKOUT_PRICE.search(reason)
    if m:
        return Decimal(m.group("p2"))
    return None


def _d(v: Optional[float]) -> Optional[Decimal]:
    if v is None:
        return None
    return Decimal(str(v))


def extract_from_files(paths: Iterable[str]) -> list[EventRow]:
    rows: list[EventRow] = []
    seen: set[tuple] = set()

    for path in sorted(paths):
        mkt_ctx = ""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                ts, msg = _parse_prefix(raw.rstrip("\n"))
                d_val, t_bucket = _bucket(ts)

                if RE_MKT_CTX.search(msg):
                    if RE_MKT_BAD.search(msg) or RE_MKT_NOTRADE.search(msg):
                        mkt_ctx = "BAD_MARKET"
                    elif RE_MKT_TRADE_OK.search(msg):
                        mkt_ctx = "TRADE_OK"

                m = RE_ACCEPT.search(msg)
                if m:
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "ACCEPT", "signal",
                                   m.group("code"), "", "ORCHESTRATOR", mkt_ctx,
                                   f"ACCEPT conf={m.group('conf')} alpha={m.group('alpha')}", "",
                                   _d(float(m.group("price"))), None, "", None, None)
                    key = (row.timestamp, row.source_tag, row.ticker, row.entry_reason)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_SMC_SIG.search(msg)
                if m:
                    reason = m.group("reason")
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "SMC_SIG", "signal",
                                   m.group("code"), m.group("name").strip(), "SMC", mkt_ctx,
                                   reason, "", _parse_price_from_reason(reason), _parse_volume_spike(reason),
                                   "", None, None)
                    key = (row.timestamp, row.source_tag, row.ticker, row.entry_reason)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_TREND_SIG_CODED.search(msg)
                if m:
                    reason = m.group("reason")
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "TREND_SIG", "signal",
                                   m.group("code"), m.group("name").strip(), "TREND", mkt_ctx,
                                   reason, "", _parse_price_from_reason(reason), _parse_volume_spike(reason),
                                   "", None, None)
                    key = (row.timestamp, row.source_tag, row.ticker, row.entry_reason)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_EXPL_ENTRY.search(msg)
                if m:
                    reason = m.group("reason")
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "EXPLORATION_ENTRY", "signal",
                                   m.group("code"), m.group("name").strip(), "EXPLORATION", mkt_ctx,
                                   reason, "", _parse_price_from_reason(reason), _parse_volume_spike(reason),
                                   "", None, None)
                    key = (row.timestamp, row.source_tag, row.ticker, row.entry_reason)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_RS_RESULT.search(msg)
                if m:
                    pnl = float(m.group("pnl"))
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "RS_RESULT", "result",
                                   m.group("code"), "", "RS", mkt_ctx,
                                   m.group("entry").strip(), m.group("exit"),
                                   None, None, _result_from_pnl(pnl), _d(pnl), _d(float(m.group("hold"))))
                    key = (row.timestamp, row.source_tag, row.ticker, row.exit_reason, row.pnl)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_DEF_RESULT.search(msg)
                if m:
                    pnl = float(m.group("pnl"))
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "DEF_RESULT", "result",
                                   m.group("code"), "", "DEFENSIVE", mkt_ctx,
                                   m.group("entry").strip(), m.group("exit").strip(),
                                   None, None, _result_from_pnl(pnl), _d(pnl), _d(float(m.group("hold"))))
                    key = (row.timestamp, row.source_tag, row.ticker, row.exit_reason, row.pnl)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_A_PLUS_RESULT.search(msg)
                if m:
                    pnl = float(m.group("pnl"))
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "A_PLUS_RESULT", "result",
                                   m.group("code"), "", "A_PLUS", mkt_ctx,
                                   "A+", m.group("reason").strip(),
                                   None, None, m.group("result").strip().upper() or _result_from_pnl(pnl),
                                   _d(pnl), None)
                    key = (row.timestamp, row.source_tag, row.ticker, row.exit_reason, row.pnl)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_CONS_HARD.search(msg)
                if m:
                    pnl = float(m.group("pnl"))
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "CONSERVATIVE_MODE", "risk",
                                   "", m.group("symbol").strip(), "RISK_CONTROL", mkt_ctx,
                                   "", "Hard Stop", None, None,
                                   _result_from_pnl(pnl), _d(pnl), None)
                    key = (row.timestamp, row.source_tag, row.symbol, row.pnl)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    continue

                m = RE_TREND_SIG_GENERIC.search(msg)
                if m:
                    reason = f"TREND {m.group('etype')}[{m.group('grade')}]: {m.group('detail')}"
                    row = EventRow(ts, d_val, t_bucket, os.path.basename(path), "TREND_SIG_GENERIC", "signal",
                                   "", "", "TREND", mkt_ctx,
                                   reason, "", _parse_price_from_reason(reason), _parse_volume_spike(reason),
                                   "", None, None)
                    key = (row.timestamp, row.source_tag, row.entry_reason)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)

    return rows


def get_pg_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        database=os.getenv("POSTGRES_DB", "trading_system"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def ensure_schema(conn, schema_sql_path: str):
    with conn.cursor() as cur:
        with open(schema_sql_path, "r", encoding="utf-8") as f:
            cur.execute(f.read())
    conn.commit()


def truncate_table(conn, table: str):
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {table}")
    conn.commit()


def insert_rows(conn, table: str, rows: list[EventRow]):
    if not rows:
        return
    q = f"""
        INSERT INTO {table} (
            timestamp, trade_date, time_bucket, source_file, source_tag, kind,
            ticker, symbol, regime, market_context, entry_reason, exit_reason,
            price, volume_spike, result, pnl, duration_min
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
    """
    payload = [
        (
            r.timestamp, r.trade_date, r.time_bucket, r.source_file, r.source_tag, r.kind,
            r.ticker, r.symbol, r.regime, r.market_context, r.entry_reason, r.exit_reason,
            r.price, r.volume_spike, r.result, r.pnl, r.duration_min,
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        execute_batch(cur, q, payload, page_size=1000)
    conn.commit()


def print_summary(rows: list[EventRow]) -> None:
    print("\n=== Log Trade Extract Summary (PostgreSQL) ===")
    print(f"rows: {len(rows)}")
    if not rows:
        return

    by_tag = defaultdict(int)
    for r in rows:
        by_tag[r.source_tag] += 1

    print("\n[tag counts]")
    for k in sorted(by_tag.keys()):
        print(f"  {k:20s} {by_tag[k]:5d}")

    trade_rows = [r for r in rows if r.pnl is not None]
    if not trade_rows:
        print("\n[pnl summary] no pnl rows found")
        return

    pnls = [float(r.pnl) for r in trade_rows]
    wins = sum(1 for v in pnls if v > 0)
    print("\n[pnl summary]")
    print(f"  trades: {len(pnls)}")
    print(f"  win_rate: {wins / len(pnls) * 100:.1f}%")
    print(f"  avg_pnl: {sum(pnls) / len(pnls):+.3f}%")
    print(f"  total_pnl: {sum(pnls):+.3f}%")


def _resolve_paths(project_root: str, log_glob: str, days: int) -> list[str]:
    if days > 0:
        today = datetime.now().date()
        pats = []
        for i in range(days):
            d = (today - timedelta(days=i)).strftime("%Y%m%d")
            p = os.path.join(project_root, "logs", f"auto_trading_{d}.log")
            if os.path.exists(p):
                pats.append(p)
        return pats

    if not os.path.isabs(log_glob):
        log_glob = os.path.join(project_root, log_glob)
    return sorted(glob.glob(log_glob))


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract log events and load into PostgreSQL")
    parser.add_argument("--glob", default="logs/auto_trading_*.log", help="glob pattern for input logs")
    parser.add_argument("--days", type=int, default=0, help="recent N days (overrides --glob)")
    parser.add_argument("--table", default="log_trade_events", help="target PostgreSQL table")
    parser.add_argument("--truncate", action="store_true", help="truncate table before insert")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_sql_path = os.path.join(project_root, "db", "schema", "log_trade_events.sql")

    paths = _resolve_paths(project_root, args.glob, args.days)
    if not paths:
        print("no log files found")
        return

    rows = extract_from_files(paths)
    print_summary(rows)

    conn = get_pg_conn()
    try:
        ensure_schema(conn, schema_sql_path)
        if args.truncate:
            truncate_table(conn, args.table)
        insert_rows(conn, args.table, rows)

        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {args.table}")
            total = cur.fetchone()[0]
        print("\npostgres")
        print(f"  table: {args.table}")
        print(f"  total_rows_in_table: {total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
