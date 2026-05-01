"""
Active strategy gate backed by the self-optimization snapshot.

The canonical source is PostgreSQL `log_trade_events` via
`analysis.strategy_optimizer`.
"""

from __future__ import annotations

import logging
from typing import Any

from analysis.strategy_optimizer import (
    DEFAULT_CACHE_TTL_SECONDS,
    get_strategy_rule_snapshot,
    normalize_entry_reason,
    normalize_regime,
    normalize_time_bucket,
)

logger = logging.getLogger(__name__)


def _safe_snapshot() -> dict[str, Any]:
    try:
        return get_strategy_rule_snapshot(ttl_seconds=DEFAULT_CACHE_TTL_SECONDS)
    except Exception as exc:
        logger.warning("[AI_RULES] optimizer snapshot unavailable: %s", exc)
        return {
            "generated_at": "",
            "source": "fallback",
            "ttl_seconds": DEFAULT_CACHE_TTL_SECONDS,
            "regime": {},
            "entry_reason": {},
            "time_bucket": {},
            "metrics": {
                "regime": {},
                "entry_reason": {},
                "time_bucket": {},
            },
            "disabled": {
                "regime": [],
                "entry_reason": [],
                "time_bucket": [],
            },
        }


def get_active_strategy_map() -> dict[str, dict[str, bool]]:
    snapshot = _safe_snapshot()
    return {
        "regime": dict(snapshot.get("regime", {})),
        "entry_reason": dict(snapshot.get("entry_reason", {})),
        "time_bucket": dict(snapshot.get("time_bucket", {})),
    }


def is_strategy_allowed(
    regime: str = "",
    entry_reason: str = "",
    time_bucket: str = "",
    **_: Any,
) -> tuple[bool, str]:
    """
    Runtime entry gate.

    Unknown labels default to allowed so that unseen strategies are not blocked
    solely due to sparse data.
    """
    snapshot = _safe_snapshot()

    checks = (
        ("regime", normalize_regime(regime)),
        ("entry_reason", normalize_entry_reason(entry_reason)),
        ("time_bucket", normalize_time_bucket(time_bucket)),
    )

    for group_name, label in checks:
        if not label or label == "[EMPTY]":
            continue

        allowed_map = snapshot.get(group_name, {})
        if label not in allowed_map:
            continue

        if not bool(allowed_map[label]):
            metrics = (
                snapshot.get("metrics", {})
                .get(group_name, {})
                .get(label, {})
            )
            detail = (
                f"expectancy={float(metrics.get('expectancy', 0.0)):+.2f}, "
                f"win_rate={float(metrics.get('win_rate', 0.0)):.0%}, "
                f"hard_stop_ratio={float(metrics.get('hard_stop_ratio', 0.0)):.0%}, "
                f"trades={int(metrics.get('trades', 0))}"
            )
            return False, f"[SELF_OPT] {group_name}={label} disabled ({detail})"

    return True, ""


def should_block_entry(
    regime: str = "",
    entry_reason: str = "",
    time_bucket: str = "",
    **kwargs: Any,
) -> tuple[bool, str]:
    allowed, reason = is_strategy_allowed(
        regime=regime,
        entry_reason=entry_reason,
        time_bucket=time_bucket,
        **kwargs,
    )
    return (not allowed), reason


def get_active_rules_summary() -> dict[str, Any]:
    snapshot = _safe_snapshot()
    return {
        "generated_at": snapshot.get("generated_at", ""),
        "source": snapshot.get("source", "fallback"),
        "ttl_seconds": snapshot.get("ttl_seconds", DEFAULT_CACHE_TTL_SECONDS),
        "active": get_active_strategy_map(),
        "disabled": snapshot.get("disabled", {}),
    }
