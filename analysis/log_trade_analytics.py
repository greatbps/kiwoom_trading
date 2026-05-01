"""
log_trade_events 기반 성과 리포트 + 하드스탑 판단기

사용 예:
  python3 analysis/log_trade_analytics.py
  python3 analysis/log_trade_analytics.py --days 14 --top-n 7
  python3 analysis/log_trade_analytics.py --format telegram
"""

from __future__ import annotations

import argparse
import html
import os
from dataclasses import dataclass
from typing import Any, Dict, List

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


@dataclass
class RankedMetric:
    label: str
    trades: int
    avg_pnl: float
    total_pnl: float


def get_pg_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        database=os.getenv("POSTGRES_DB", "trading_system"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def _normalize_label(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    return text if text else fallback


def _fetch_ranked(
    conn,
    group_expr: str,
    order_expr: str,
    days: int,
    limit: int,
    min_trades: int = 1,
) -> List[RankedMetric]:
    # days=0이면 오늘만, days>0이면 최근 N일
    if days == 0:
        date_condition = "trade_date = CURRENT_DATE"
    else:
        date_condition = f"trade_date >= CURRENT_DATE - ({days} * INTERVAL '1 day')"
        
    query = f"""
        SELECT
            {group_expr} AS label,
            COUNT(*) AS trades,
            COALESCE(AVG(pnl), 0) AS avg_pnl,
            COALESCE(SUM(pnl), 0) AS total_pnl
        FROM log_trade_events
        WHERE pnl IS NOT NULL
          AND {date_condition}
        GROUP BY 1
        HAVING COUNT(*) >= %s
        ORDER BY {order_expr}
        LIMIT %s
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, (min_trades, limit))
        rows = cur.fetchall()

    return [
        RankedMetric(
            label=_normalize_label(row["label"], "[EMPTY]"),
            trades=int(row["trades"] or 0),
            avg_pnl=float(row["avg_pnl"] or 0.0),
            total_pnl=float(row["total_pnl"] or 0.0),
        )
        for row in rows
    ]


def fetch_report(days: int = 30, top_n: int = 5) -> Dict[str, Any]:
    conn = get_pg_conn()
    try:
        # days=0이면 오늘만, days>0이면 최근 N일
        if days == 0:
            date_condition = "trade_date = CURRENT_DATE"
            days_display = 0
        else:
            date_condition = f"trade_date >= CURRENT_DATE - ({days} * INTERVAL '1 day')"
            days_display = days
            
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS trades,
                    COALESCE(AVG(pnl), 0) AS avg_pnl,
                    COALESCE(SUM(pnl), 0) AS total_pnl,
                    COUNT(*) FILTER (WHERE pnl > 0) AS wins,
                    COUNT(*) FILTER (WHERE pnl < 0) AS losses
                FROM log_trade_events
                WHERE pnl IS NOT NULL
                  AND {date_condition}
                """
            )
            overall = cur.fetchone() or {}

        return {
            "days": days,
            "overall": {
                "trades": int(overall.get("trades") or 0),
                "avg_pnl": float(overall.get("avg_pnl") or 0.0),
                "total_pnl": float(overall.get("total_pnl") or 0.0),
                "wins": int(overall.get("wins") or 0),
                "losses": int(overall.get("losses") or 0),
            },
            "worst_regimes": _fetch_ranked(
                conn,
                "COALESCE(NULLIF(regime, ''), '[EMPTY]')",
                "SUM(pnl) ASC, COUNT(*) DESC",
                days,
                top_n,
            ),
            "best_regimes": _fetch_ranked(
                conn,
                "COALESCE(NULLIF(regime, ''), '[EMPTY]')",
                "SUM(pnl) DESC, COUNT(*) DESC",
                days,
                top_n,
            ),
            "worst_time_buckets": _fetch_ranked(
                conn,
                "COALESCE(NULLIF(time_bucket, ''), '[EMPTY]')",
                "SUM(pnl) ASC, COUNT(*) DESC",
                days,
                top_n,
            ),
            "worst_entry_reasons": _fetch_ranked(
                conn,
                "COALESCE(NULLIF(entry_reason, ''), '[EMPTY]')",
                "AVG(pnl) ASC, COUNT(*) DESC",
                days,
                top_n,
            ),
            "best_entry_reasons": _fetch_ranked(
                conn,
                "COALESCE(NULLIF(entry_reason, ''), '[EMPTY]')",
                "AVG(pnl) DESC, COUNT(*) DESC",
                days,
                top_n,
            ),
        }
    finally:
        conn.close()


def evaluate_hard_stop(
    lookback_hours: int = 24,
    consecutive_loss_limit: int = 5,
    regime_loss_limit: float = -5.0,
    regime_min_trades: int = 2,
) -> Dict[str, Any]:
    conn = get_pg_conn()
    try:
        # lookback_hours를 일 단위로 변환 (24시간 = 1일)
        lookback_days = max(1, lookback_hours // 24)
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT timestamp, pnl, regime, exit_reason
                FROM log_trade_events
                WHERE pnl IS NOT NULL
                  AND trade_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                ORDER BY COALESCE(timestamp, ingested_at) DESC
                LIMIT 100
                """,
                (lookback_days,),
            )
            recent_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COALESCE(NULLIF(regime, ''), '[EMPTY]') AS regime,
                    COUNT(*) AS trades,
                    COALESCE(SUM(pnl), 0) AS total_pnl,
                    COALESCE(AVG(pnl), 0) AS avg_pnl
                FROM log_trade_events
                WHERE pnl IS NOT NULL
                  AND trade_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                GROUP BY 1
                HAVING COUNT(*) >= %s
                   AND COALESCE(SUM(pnl), 0) <= %s
                ORDER BY total_pnl ASC
                """,
                (lookback_days, regime_min_trades, regime_loss_limit),
            )
            breached_regimes = cur.fetchall()

        loss_streak = 0
        for row in recent_rows:
            pnl = float(row["pnl"] or 0.0)
            if pnl < 0:
                loss_streak += 1
                continue
            break

        reasons: List[str] = []
        if loss_streak >= consecutive_loss_limit:
            reasons.append(
                f"recent {loss_streak} consecutive losses in last {lookback_days}d"
            )

        disabled_regimes: List[str] = []
        for row in breached_regimes:
            regime = _normalize_label(row["regime"], "[EMPTY]")
            disabled_regimes.append(regime)
            reasons.append(
                f"regime {regime} pnl {float(row['total_pnl'] or 0.0):+.2f}% "
                f"({int(row['trades'] or 0)} trades/{lookback_days}d)"
            )

        return {
            "halted": bool(reasons),
            "loss_streak": loss_streak,
            "lookback_hours": lookback_hours,
            "lookback_days": lookback_days,
            "disabled_regimes": disabled_regimes,
            "reasons": reasons,
            "recent_trade_count": len(recent_rows),
        }
    finally:
        conn.close()


def format_console_report(report: Dict[str, Any], hard_stop: Dict[str, Any]) -> str:
    overall = report["overall"]
    total = overall["trades"]
    wins = overall["wins"]
    win_rate = (wins / total * 100.0) if total else 0.0

    lines = [
        f"[log_trade_events] 최근 {report['days']}일",
        f"- trades={total} wins={wins} losses={overall['losses']} win_rate={win_rate:.1f}%",
        f"- avg_pnl={overall['avg_pnl']:+.2f}% total_pnl={overall['total_pnl']:+.2f}%",
        "",
        "[Worst Regimes]",
    ]
    lines.extend(
        f"- {item.label}: trades={item.trades} avg={item.avg_pnl:+.2f}% total={item.total_pnl:+.2f}%"
        for item in report["worst_regimes"]
    )
    lines.append("")
    lines.append("[Worst Time Buckets]")
    lines.extend(
        f"- {item.label}: trades={item.trades} total={item.total_pnl:+.2f}% avg={item.avg_pnl:+.2f}%"
        for item in report["worst_time_buckets"]
    )
    lines.append("")
    lines.append("[Worst Entry Reasons]")
    lines.extend(
        f"- {item.label}: trades={item.trades} expectancy={item.avg_pnl:+.2f}% total={item.total_pnl:+.2f}%"
        for item in report["worst_entry_reasons"]
    )
    lines.append("")
    lines.append("[Hard Stop]")
    if hard_stop["halted"]:
        lines.append(f"- HALTED: streak={hard_stop['loss_streak']}")
        lines.extend(f"- {reason}" for reason in hard_stop["reasons"])
    else:
        lines.append(
            f"- OK: streak={hard_stop['loss_streak']} recent_trades={hard_stop['recent_trade_count']}"
        )
    return "\n".join(lines)


def format_telegram_report(report: Dict[str, Any], hard_stop: Dict[str, Any]) -> str:
    overall = report["overall"]
    total = overall["trades"]
    wins = overall["wins"]
    win_rate = (wins / total * 100.0) if total else 0.0
    
    # 기간 표시
    if report['days'] == 0:
        period_str = "당일"
    else:
        period_str = f"최근 {report['days']}일"

    lines = [
        f"📊 <b>거래 분석 리포트 ({period_str})</b>",
        "",
        f"거래: {total}건 / 승률 {win_rate:.1f}%",
        f"평균 {overall['avg_pnl']:+.2f}% / 누적 {overall['total_pnl']:+.2f}%",
        "",
        "🔻 <b>Worst Regime</b>",
    ]

    if report["worst_regimes"]:
        for item in report["worst_regimes"][:3]:
            lines.append(
                f"• {html.escape(item.label)}: {item.trades}건, {item.total_pnl:+.2f}%"
            )
    else:
        lines.append("• 데이터 없음")

    lines.append("")
    lines.append("⏰ <b>Worst Time Bucket</b>")
    if report["worst_time_buckets"]:
        for item in report["worst_time_buckets"][:3]:
            lines.append(
                f"• {html.escape(item.label)}: {item.trades}건, {item.total_pnl:+.2f}%"
            )
    else:
        lines.append("• 데이터 없음")

    lines.append("")
    lines.append("🧪 <b>Worst Entry Reason</b>")
    if report["worst_entry_reasons"]:
        for item in report["worst_entry_reasons"][:3]:
            label = item.label if len(item.label) <= 48 else item.label[:45] + "..."
            lines.append(
                f"• {html.escape(label)}: {item.trades}건, EV {item.avg_pnl:+.2f}%"
            )
    else:
        lines.append("• 데이터 없음")

    lines.append("")
    if hard_stop["halted"]:
        lines.append(
            f"🚨 <b>Hard Stop</b>: HALTED (연속 손실 {hard_stop['loss_streak']}회)"
        )
        for reason in hard_stop["reasons"][:3]:
            lines.append(f"• {html.escape(reason)}")
    else:
        lines.append(
            f"✅ <b>Hard Stop</b>: OK (연속 손실 {hard_stop['loss_streak']}회)"
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="log_trade_events 분석 리포트")
    parser.add_argument("--days", type=int, default=30, help="성과 집계 기간(일)")
    parser.add_argument("--top-n", type=int, default=5, help="섹션별 상위 개수")
    parser.add_argument(
        "--format",
        choices=["console", "telegram"],
        default="console",
        help="출력 포맷",
    )
    parser.add_argument("--lookback-hours", type=int, default=24, help="하드스탑 판단 시간창")
    parser.add_argument("--consecutive-loss-limit", type=int, default=5, help="연속 손실 한도")
    parser.add_argument("--regime-loss-limit", type=float, default=-5.0, help="레짐 손실 한도(%%)")
    parser.add_argument("--regime-min-trades", type=int, default=2, help="레짐 최소 거래수")
    args = parser.parse_args()

    report = fetch_report(days=args.days, top_n=args.top_n)
    hard_stop = evaluate_hard_stop(
        lookback_hours=args.lookback_hours,
        consecutive_loss_limit=args.consecutive_loss_limit,
        regime_loss_limit=args.regime_loss_limit,
        regime_min_trades=args.regime_min_trades,
    )

    if args.format == "telegram":
        print(format_telegram_report(report, hard_stop))
    else:
        print(format_console_report(report, hard_stop))


if __name__ == "__main__":
    main()
