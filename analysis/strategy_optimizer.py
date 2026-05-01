"""
PostgreSQL-backed self-optimization for entry strategy gating.

Reads `log_trade_events`, aggregates recent performance by:
- regime
- entry_reason
- time_bucket

Then derives active/inactive strategy rules with a short TTL cache so the
runtime does not hammer PostgreSQL on every entry attempt.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = int(os.getenv("SELF_OPT_LOOKBACK_DAYS", "90"))
DEFAULT_CACHE_TTL_SECONDS = 60
MIN_TRADES_SAFETY = 10
WIN_RATE_DISABLE_THRESHOLD = 0.30
EXPECTANCY_DISABLE_THRESHOLD = 0.0
HARD_STOP_RATIO_DISABLE_THRESHOLD = 0.30
WIN_RATE_MIN_TRADES = 20
EMPTY_LABEL = "[EMPTY]"

_CACHE_LOCK = threading.Lock()
_CACHE_PAYLOAD: dict[str, Any] | None = None
_CACHE_EXPIRES_AT = 0.0

_LEADING_TIME_RE = re.compile(r"^\d{2}:\d{2}\s*")
_MULTISPACE_RE = re.compile(r"\s+")
_KNOWN_ENTRY_PREFIXES = (
    "A+:",
    "RS:",
    "DEFENSIVE:",
    "EXPLORATION:",
    "EXPERIMENT:",
    "SHORT:",
)
_HARD_STOP_TOKENS = (
    "HARD STOP",
    "HARD_STOP",
    "STOP_LOSS",
    "STOP LOSS",
    "STOPLOSS",
    "자동손절",
    "긴급 손절",
    "긴급손절",
    "구조 손절",
    "구조손절",
    "손절",
)


@dataclass(frozen=True)
class TradeEvent:
    event_ts: datetime
    regime: str
    entry_reason: str
    time_bucket: str
    pnl: float
    exit_reason: str


def get_pg_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        database=os.getenv("POSTGRES_DB", "trading_system"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_text(value: str | None, fallback: str = EMPTY_LABEL) -> str:
    text = _MULTISPACE_RE.sub(" ", (value or "").strip())
    return text if text else fallback


def normalize_regime(value: str | None) -> str:
    return _clean_text(value).upper()


def normalize_time_bucket(value: str | None) -> str:
    return _clean_text(value)


def normalize_entry_reason(value: str | None) -> str:
    text = _clean_text(value)
    if text == EMPTY_LABEL:
        return text

    raw = _LEADING_TIME_RE.sub("", text).strip()
    upper = raw.upper()

    for prefix in _KNOWN_ENTRY_PREFIXES:
        if upper.startswith(prefix):
            return prefix[:-1]

    if "TREND" in upper:
        return "TREND"
    if "DEFENSIVE" in upper:
        return "DEFENSIVE"
    if "EXPLORATION" in upper:
        return "EXPLORATION"
    if "EXPERIMENT" in upper:
        return "EXPERIMENT"
    if upper.startswith("RS") or " RS" in upper:
        return "RS"
    if "A+" in upper or "A_PLUS" in upper:
        return "A_PLUS"
    if "ACCEPT" in upper or "ORCHESTRATOR" in upper:
        return "ORCHESTRATOR"
    if "SMC_OB" in upper or "ORDERBOOK" in upper or "ORDER BOOK" in upper:
        return "SMC_OB"
    if "SMC" in upper:
        return "SMC"
    if "5분봉" in raw:
        return "5분봉"
    if "30분봉" in raw:
        return "30분봉"

    return raw[:80]


def _is_hard_stop(exit_reason: str | None) -> bool:
    upper = (exit_reason or "").upper()
    return any(token.upper() in upper for token in _HARD_STOP_TOKENS)


def _fetch_trade_events(lookback_days: int) -> list[TradeEvent]:
    query = """
        SELECT
            COALESCE(timestamp, trade_date::timestamp) AS event_ts,
            regime,
            entry_reason,
            time_bucket,
            pnl,
            exit_reason
        FROM log_trade_events
        WHERE pnl IS NOT NULL
          AND COALESCE(timestamp, trade_date::timestamp) IS NOT NULL
          AND COALESCE(timestamp, trade_date::timestamp) >= NOW() - (%s * INTERVAL '1 day')
        ORDER BY COALESCE(timestamp, trade_date::timestamp) ASC, id ASC
    """
    conn = get_pg_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (lookback_days,))
            rows = cur.fetchall()
    finally:
        conn.close()

    events: list[TradeEvent] = []
    for row in rows:
        try:
            events.append(
                TradeEvent(
                    event_ts=row["event_ts"],
                    regime=str(row.get("regime") or ""),
                    entry_reason=str(row.get("entry_reason") or ""),
                    time_bucket=str(row.get("time_bucket") or ""),
                    pnl=float(row.get("pnl") or 0.0),
                    exit_reason=str(row.get("exit_reason") or ""),
                )
            )
        except Exception as exc:
            logger.debug("[SELF_OPT] skip malformed row: %s", exc)
    return events


def _evaluate_group(
    events: list[TradeEvent],
    label_getter: Callable[[TradeEvent], str],
) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    current_loss_streak: defaultdict[str, int] = defaultdict(int)

    for event in events:
        label = label_getter(event)
        item = stats.setdefault(
            label,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "hard_stop_count": 0,
                "max_consecutive_losses": 0,
            },
        )
        pnl = float(event.pnl)
        item["trades"] += 1
        item["total_pnl"] += pnl

        if pnl > 0:
            item["wins"] += 1
            current_loss_streak[label] = 0
        elif pnl < 0:
            item["losses"] += 1
            current_loss_streak[label] += 1
            item["max_consecutive_losses"] = max(
                item["max_consecutive_losses"],
                current_loss_streak[label],
            )
        else:
            current_loss_streak[label] = 0

        if _is_hard_stop(event.exit_reason):
            item["hard_stop_count"] += 1

    enriched: dict[str, dict[str, Any]] = {}
    for label, item in stats.items():
        trades = int(item["trades"])
        wins = int(item["wins"])
        hard_stop_count = int(item["hard_stop_count"])
        expectancy = (float(item["total_pnl"]) / trades) if trades else 0.0
        win_rate = (wins / trades) if trades else 0.0
        hard_stop_ratio = (hard_stop_count / trades) if trades else 0.0

        disable_reasons: list[str] = []
        active = True
        ignored = trades < MIN_TRADES_SAFETY

        if not ignored:
            if expectancy < EXPECTANCY_DISABLE_THRESHOLD:
                disable_reasons.append(f"expectancy<{EXPECTANCY_DISABLE_THRESHOLD:.2f}")
            if hard_stop_ratio > HARD_STOP_RATIO_DISABLE_THRESHOLD:
                disable_reasons.append(
                    f"hard_stop_ratio>{HARD_STOP_RATIO_DISABLE_THRESHOLD:.2f}"
                )
            if win_rate < WIN_RATE_DISABLE_THRESHOLD and trades > WIN_RATE_MIN_TRADES:
                disable_reasons.append(
                    f"win_rate<{WIN_RATE_DISABLE_THRESHOLD:.2f}_with_trades>{WIN_RATE_MIN_TRADES}"
                )
            active = not disable_reasons

        enriched[label] = {
            "trades": trades,
            "win_rate": round(win_rate, 4),
            "expectancy": round(expectancy, 4),
            "max_consecutive_losses": int(item["max_consecutive_losses"]),
            "hard_stop_ratio": round(hard_stop_ratio, 4),
            "hard_stop_count": hard_stop_count,
            "active": active,
            "ignored": ignored,
            "disable_reasons": disable_reasons,
        }

    return dict(sorted(enriched.items(), key=lambda pair: pair[0]))


def _build_snapshot(lookback_days: int) -> dict[str, Any]:
    events = _fetch_trade_events(lookback_days=lookback_days)

    grouped_metrics = {
        "regime": _evaluate_group(events, lambda e: normalize_regime(e.regime)),
        "entry_reason": _evaluate_group(
            events,
            lambda e: normalize_entry_reason(e.entry_reason),
        ),
        "time_bucket": _evaluate_group(
            events,
            lambda e: normalize_time_bucket(e.time_bucket),
        ),
    }

    active_map = {
        group_name: {
            label: bool(metrics["active"])
            for label, metrics in group_metrics.items()
        }
        for group_name, group_metrics in grouped_metrics.items()
    }

    disabled_summary = {
        group_name: [
            label
            for label, metrics in group_metrics.items()
            if not metrics["active"] and not metrics["ignored"]
        ]
        for group_name, group_metrics in grouped_metrics.items()
    }

    return {
        "generated_at": _utc_now_iso(),
        "source": "postgresql.log_trade_events",
        "lookback_days": lookback_days,
        "ttl_seconds": DEFAULT_CACHE_TTL_SECONDS,
        "thresholds": {
            "ignore_if_trades_lt": MIN_TRADES_SAFETY,
            "disable_if_expectancy_lt": EXPECTANCY_DISABLE_THRESHOLD,
            "disable_if_hard_stop_ratio_gt": HARD_STOP_RATIO_DISABLE_THRESHOLD,
            "disable_if_win_rate_lt": WIN_RATE_DISABLE_THRESHOLD,
            "disable_if_win_rate_trades_gt": WIN_RATE_MIN_TRADES,
        },
        "total_events": len(events),
        "regime": active_map["regime"],
        "entry_reason": active_map["entry_reason"],
        "time_bucket": active_map["time_bucket"],
        "metrics": grouped_metrics,
        "disabled": disabled_summary,
    }


def get_strategy_rule_snapshot(
    *,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    force_refresh: bool = False,
) -> dict[str, Any]:
    global _CACHE_EXPIRES_AT, _CACHE_PAYLOAD

    now = time.time()
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _CACHE_PAYLOAD is not None
            and now < _CACHE_EXPIRES_AT
        ):
            return _CACHE_PAYLOAD

        snapshot = _build_snapshot(lookback_days=lookback_days)
        _CACHE_PAYLOAD = snapshot
        _CACHE_EXPIRES_AT = now + max(1, int(ttl_seconds))
        return snapshot


def invalidate_strategy_rule_cache() -> None:
    global _CACHE_EXPIRES_AT, _CACHE_PAYLOAD
    with _CACHE_LOCK:
        _CACHE_PAYLOAD = None
        _CACHE_EXPIRES_AT = 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-optimization strategy snapshot")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    snapshot = get_strategy_rule_snapshot(
        lookback_days=args.lookback_days,
        force_refresh=args.force_refresh,
    )
    if args.pretty:
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
