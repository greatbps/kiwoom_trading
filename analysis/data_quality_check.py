"""
데이터 무결성 헬스체크

매일 장 마감 후 실행해서 데이터 파이프라인 이상을 조기 감지.

실행:
    python3 -m analysis.data_quality_check
    python3 -m analysis.data_quality_check --days 7   # 최근 7일
    python3 -m analysis.data_quality_check --json     # JSON 출력

점검 항목:
  [P1] NULL 비율 — mfe_pct, mae_pct, r_multiple, entry_features, confidence
  [P2] BUY/SELL 페어 — BUY 없는 SELL, SELL 없는 BUY (미청산 제외)
  [P3] partial exit 중복 — 동일 trade_id SELL 2건 이상
  [P4] MAE/MFE 논리 오류 — mfe_pct < 0, mae_pct < 0, mfe_pct > 50
  [P5] swing_features 누락 — 스윙 BUY에 swing_features 없음
  [P6] exit_reason 표준화 — 분류 불가 exit_reason 비율
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
import psycopg2.extras

_PG_DSN = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "trading_system"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

# exit_reason 표준 카테고리 키워드 (대소문자 포함)
_EXIT_CATEGORIES = {
    "STOP":        ["Hard Stop", "손절", "stop_loss", "hard_stop", "HARD_STOP"],
    "TRAILING":    ["Trailing", "trailing", "TRAILING", "TRAILING_STOP"],
    "TIME_EXIT":   ["Time Exit", "시간", "오버나이트", "익일보유", "강제청산", "TIME_EXIT"],
    "EF":          ["Early Failure", "ef_no_follow", "ef_no_demand"],
    "PARTIAL":     ["부분청산", "partial", "Partial"],
    "TAKE_PROFIT": ["Take Profit", "익절", "A+ TP"],
}


def _get_conn():
    return psycopg2.connect(**_PG_DSN)


def _categorize_exit(reason: str) -> str:
    if not reason:
        return "UNKNOWN"
    for cat, keywords in _EXIT_CATEGORIES.items():
        if any(k in reason for k in keywords):
            return cat
    return "UNKNOWN"


def run_checks(days: int = 30) -> dict:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    results = {"checked_at": datetime.now().isoformat(), "since": since, "checks": {}}

    # ── P1: NULL 비율 (P1 마이그레이션 이후 레코드만 의미 있음) ──────────────
    # P1 마이그레이션 완료일: 2026-05-09. 그 이전 레코드는 신규 컬럼 모두 NULL이 정상.
    P1_SINCE = "2026-05-09"
    cur.execute("""
        SELECT
            COUNT(*)                                         AS total,
            COUNT(*) FILTER (WHERE trade_type = 'SELL')     AS sell_count,
            COUNT(*) FILTER (WHERE trade_type = 'BUY')      AS buy_count,
            -- MFE/MAE (SELL에만 의미 있음)
            COUNT(*) FILTER (WHERE trade_type = 'SELL' AND mfe_pct IS NULL)  AS mfe_null,
            COUNT(*) FILTER (WHERE trade_type = 'SELL' AND mae_pct IS NULL)  AS mae_null,
            -- r_multiple
            COUNT(*) FILTER (WHERE trade_type = 'SELL' AND r_multiple IS NULL) AS r_mult_null,
            -- entry_features (BUY에만 의미 있음)
            COUNT(*) FILTER (WHERE trade_type = 'BUY' AND entry_features IS NULL) AS ef_null,
            -- confidence, position_size_mult
            COUNT(*) FILTER (WHERE trade_type = 'BUY' AND confidence IS NULL)    AS conf_null,
            COUNT(*) FILTER (WHERE trade_type = 'BUY' AND position_size_mult IS NULL) AS size_null,
            -- exit_market_regime (SELL)
            COUNT(*) FILTER (WHERE trade_type = 'SELL' AND exit_market_regime IS NULL) AS exit_regime_null
        FROM trades
        WHERE created_at >= %s
    """, (P1_SINCE,))
    row = dict(cur.fetchone())
    sell_n = max(row["sell_count"], 1)
    buy_n  = max(row["buy_count"], 1)
    results["checks"]["null_rates"] = {
        "note":               f"P1 마이그레이션({P1_SINCE}) 이후 레코드 기준",
        "total_trades":       row["total"],
        "buy_count":          row["buy_count"],
        "sell_count":         row["sell_count"],
        "mfe_null_pct":       round(row["mfe_null"]  / sell_n * 100, 1),
        "mae_null_pct":       round(row["mae_null"]  / sell_n * 100, 1),
        "r_multiple_null_pct":round(row["r_mult_null"]/ sell_n * 100, 1),
        "entry_features_null_pct": round(row["ef_null"] / buy_n * 100, 1),
        "confidence_null_pct":round(row["conf_null"] / buy_n * 100, 1),
        "size_mult_null_pct": round(row["size_null"] / buy_n * 100, 1),
        "exit_regime_null_pct": round(row["exit_regime_null"] / sell_n * 100, 1),
        "status": "OK",
    }
    # 경고 기준: 핵심 필드 NULL > 30%
    nr = results["checks"]["null_rates"]
    for field in ("mfe_null_pct", "mae_null_pct", "r_multiple_null_pct"):
        if nr[field] > 30:
            nr["status"] = "WARN"
        if nr[field] > 80:
            nr["status"] = "CRITICAL"

    # ── P2: 당일/전일 BUY 중 SELL row가 없는 건 ─────────────────────────────
    # 이 시스템은 BUY/SELL이 별도 row. 당일 BUY에 대응하는 당일 SELL이 없으면 의심.
    # 단, 스윙/오버나이트 포지션은 정상적으로 다음날 청산됨 → 최근 2거래일만 검사.
    two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE trade_type = 'SELL') AS sell_today,
            COUNT(*) FILTER (WHERE trade_type = 'BUY')  AS buy_today
        FROM trades
        WHERE created_at >= %s
    """, (two_days_ago,))
    p2_today = dict(cur.fetchone())
    # stock_code 기준 BUY에 대응 SELL 없는 종목 (같은 날 BUY만 있고 SELL 없음)
    cur.execute("""
        SELECT b.stock_code, b.trade_id, b.created_at::date AS buy_date,
               b.stock_name
        FROM trades b
        WHERE b.trade_type = 'BUY'
          AND b.created_at >= %s
          AND b.stock_code NOT LIKE 'TEST%%'
          AND NOT EXISTS (
              SELECT 1 FROM trades s
              WHERE s.trade_type = 'SELL'
                AND s.stock_code = b.stock_code
                AND DATE(s.created_at) = DATE(b.created_at)
          )
        ORDER BY b.created_at DESC
        LIMIT 10
    """, (two_days_ago,))
    unmatched = [dict(r) for r in cur.fetchall()]
    p2 = {
        "buy_count_2d":   p2_today["buy_today"],
        "sell_count_2d":  p2_today["sell_today"],
        "unmatched_buys": len(unmatched),
        "examples":       unmatched[:5],
        "status": "WARN" if len(unmatched) > 0 else "OK",
    }
    results["checks"]["buy_sell_pairs"] = p2

    # ── P3: SELL 중복 (동일 stock_code × 같은 날 2건 이상 SELL) ─────────────
    cur.execute("""
        SELECT stock_code, DATE(trade_time) AS trade_date, COUNT(*) AS cnt
        FROM trades
        WHERE trade_type = 'SELL'
          AND created_at >= %s
        GROUP BY stock_code, DATE(trade_time)
        HAVING COUNT(*) >= 2
        ORDER BY cnt DESC
        LIMIT 10
    """, (since,))
    dups = [dict(r) for r in cur.fetchall()]
    results["checks"]["sell_duplicates"] = {
        "duplicate_groups": len(dups),
        "examples":         dups[:5],
        "status": "WARN" if dups else "OK",
    }

    # ── P4: MAE/MFE 논리 오류 ────────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE mfe_pct < 0)    AS mfe_negative,
            COUNT(*) FILTER (WHERE mae_pct < 0)    AS mae_negative,
            COUNT(*) FILTER (WHERE mfe_pct > 50)   AS mfe_extreme,
            COUNT(*) FILTER (WHERE mae_pct > 50)   AS mae_extreme,
            COUNT(*) FILTER (WHERE mfe_pct IS NOT NULL) AS mfe_valid,
            ROUND(AVG(mfe_pct)::numeric, 3)        AS mfe_avg,
            ROUND(AVG(mae_pct)::numeric, 3)        AS mae_avg,
            ROUND(AVG(r_multiple)::numeric, 3)     AS r_mult_avg
        FROM trades
        WHERE trade_type = 'SELL'
          AND created_at >= %s
    """, (since,))
    p4 = dict(cur.fetchone())
    p4["status"] = "WARN" if (p4["mfe_negative"] or p4["mae_negative"]) else "OK"
    if p4["mfe_extreme"] or p4["mae_extreme"]:
        p4["status"] = "WARN"
    results["checks"]["mfe_mae_sanity"] = p4

    # ── P5: swing_features 누락 ──────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*) AS swing_buy_count,
            COUNT(sf.id) AS with_features,
            COUNT(*) - COUNT(sf.id) AS missing_features
        FROM trades t
        LEFT JOIN swing_features sf ON sf.trade_id = t.trade_id
        WHERE t.trade_type = 'BUY'
          AND t.swing_pattern IS NOT NULL
          AND t.created_at >= %s
    """, (since,))
    p5 = dict(cur.fetchone())
    if p5["swing_buy_count"] > 0:
        p5["coverage_pct"] = round(p5["with_features"] / p5["swing_buy_count"] * 100, 1)
        p5["status"] = "WARN" if p5["coverage_pct"] < 70 else "OK"
    else:
        p5["coverage_pct"] = None
        p5["status"] = "OK"
    results["checks"]["swing_features_coverage"] = p5

    # ── P6: exit_reason 분류 불가 비율 ────────────────────────────────────────
    cur.execute("""
        SELECT exit_reason, COUNT(*) AS cnt
        FROM trades
        WHERE trade_type = 'SELL'
          AND created_at >= %s
        GROUP BY exit_reason
        ORDER BY cnt DESC
    """, (since,))
    rows = cur.fetchall()
    cat_counts: dict[str, int] = {}
    unknown_examples = []
    for r in rows:
        cat = _categorize_exit(r["exit_reason"] or "")
        cat_counts[cat] = cat_counts.get(cat, 0) + r["cnt"]
        if cat == "UNKNOWN":
            unknown_examples.append({"reason": r["exit_reason"], "cnt": r["cnt"]})
    total_sell = sum(cat_counts.values()) or 1
    unknown_pct = round(cat_counts.get("UNKNOWN", 0) / total_sell * 100, 1)
    results["checks"]["exit_reason_coverage"] = {
        "category_counts": cat_counts,
        "unknown_pct": unknown_pct,
        "unknown_examples": unknown_examples[:5],
        "status": "WARN" if unknown_pct > 20 else "OK",
    }

    # ── ml_dataset 커버리지 (BUY trade_id 기준) ─────────────────────────────
    # ml_dataset.trade_id = BUY의 trade_id. P1 이후 BUY 기준으로 커버리지 측정.
    cur.execute("""
        SELECT
            COUNT(DISTINCT t.trade_id) AS buy_count,
            COUNT(DISTINCT m.trade_id) AS in_ml_dataset,
            COUNT(DISTINCT m.trade_id) FILTER (WHERE m.label_pnl IS NOT NULL) AS labeled,
            COUNT(DISTINCT m.trade_id) FILTER (WHERE m.entry_features IS NOT NULL) AS with_ef,
            COUNT(DISTINCT m.trade_id) FILTER (WHERE m.r_multiple IS NOT NULL) AS with_r_mult
        FROM trades t
        LEFT JOIN ml_dataset m ON m.trade_id = t.trade_id
        WHERE t.trade_type = 'BUY'
          AND t.created_at >= %s
    """, (P1_SINCE,))
    p7 = dict(cur.fetchone())
    base = max(p7["buy_count"], 1)
    p7["ml_coverage_pct"]     = round(p7["in_ml_dataset"] / base * 100, 1)
    p7["label_coverage_pct"]  = round(p7["labeled"]       / base * 100, 1)
    p7["ef_coverage_pct"]     = round(p7["with_ef"]       / base * 100, 1)
    p7["r_mult_coverage_pct"] = round(p7["with_r_mult"]   / base * 100, 1)
    p7["note"] = f"P1 마이그레이션({P1_SINCE}) 이후 BUY 기준"
    p7["status"] = "WARN" if p7["ml_coverage_pct"] < 50 and p7["buy_count"] > 3 else "OK"
    results["checks"]["ml_dataset_coverage"] = p7

    cur.close()
    conn.close()
    return results


def _status_icon(status: str) -> str:
    return {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🔴"}.get(status, "❓")


def print_report(results: dict) -> None:
    print(f"\n{'='*60}")
    print(f"데이터 무결성 헬스체크  {results['checked_at'][:16]}")
    print(f"조회 기간: {results['since']} ~ 오늘")
    print(f"{'='*60}")

    c = results["checks"]

    # NULL 비율
    nr = c["null_rates"]
    icon = _status_icon(nr["status"])
    print(f"\n{icon} [P1] NULL 비율  (BUY={nr['buy_count']}건 / SELL={nr['sell_count']}건)  [{nr.get('note','')}]")
    print(f"   mfe_pct={nr['mfe_null_pct']}%  mae_pct={nr['mae_null_pct']}%  "
          f"r_multiple={nr['r_multiple_null_pct']}%")
    print(f"   entry_features={nr['entry_features_null_pct']}%  "
          f"confidence={nr['confidence_null_pct']}%  "
          f"exit_regime={nr['exit_regime_null_pct']}%")

    # BUY/SELL 페어
    p2 = c["buy_sell_pairs"]
    icon = _status_icon(p2["status"])
    print(f"\n{icon} [P2] 당일 BUY/SELL 매칭  (최근 2거래일)")
    print(f"   BUY={p2['buy_count_2d']}건  SELL={p2['sell_count_2d']}건  "
          f"당일 SELL 없는 BUY={p2['unmatched_buys']}건")
    for ex in p2["examples"]:
        print(f"   ⚠️  {ex['stock_code']} {ex['stock_name']} (BUY {ex['buy_date']})")

    # 중복 SELL
    d = c["sell_duplicates"]
    icon = _status_icon(d["status"])
    print(f"\n{icon} [P3] SELL 중복  ({d['duplicate_groups']}건 중복 그룹)")
    for ex in d["examples"]:
        print(f"   {ex['stock_code']} {ex['trade_date']} — {ex['cnt']}건")

    # MFE/MAE 논리
    p4 = c["mfe_mae_sanity"]
    icon = _status_icon(p4["status"])
    print(f"\n{icon} [P4] MFE/MAE 논리  ({p4['mfe_valid']}건 유효)")
    print(f"   avg mfe={p4['mfe_avg']}%  avg mae={p4['mae_avg']}%  "
          f"avg r_multiple={p4['r_mult_avg']}")
    if p4["mfe_negative"] or p4["mae_negative"]:
        print(f"   ⚠️  음수: mfe_negative={p4['mfe_negative']}건 "
              f"mae_negative={p4['mae_negative']}건")

    # swing_features
    p5 = c["swing_features_coverage"]
    icon = _status_icon(p5["status"])
    print(f"\n{icon} [P5] swing_features 커버리지  "
          f"({p5['with_features']}/{p5['swing_buy_count']}건 "
          f"= {p5['coverage_pct']}%)")

    # exit_reason 분류
    er = c["exit_reason_coverage"]
    icon = _status_icon(er["status"])
    print(f"\n{icon} [P6] exit_reason 분류  (미분류={er['unknown_pct']}%)")
    for cat, cnt in sorted(er["category_counts"].items(), key=lambda x: -x[1]):
        print(f"   {cat}: {cnt}건")
    if er["unknown_examples"]:
        print("   미분류 예시:")
        for ex in er["unknown_examples"][:3]:
            print(f"     '{ex['reason']}' ({ex['cnt']}건)")

    # ml_dataset
    p7 = c["ml_dataset_coverage"]
    icon = _status_icon(p7["status"])
    print(f"\n{icon} [P7] ml_dataset 커버리지  [{p7.get('note','')}]")
    print(f"   BUY={p7['buy_count']}건  ml_coverage={p7['ml_coverage_pct']}%  "
          f"labeled={p7['label_coverage_pct']}%")
    print(f"   entry_features={p7['ef_coverage_pct']}%  "
          f"r_multiple={p7['r_mult_coverage_pct']}%")

    # 종합
    all_statuses = [v["status"] for v in c.values()]
    critical = all_statuses.count("CRITICAL")
    warn     = all_statuses.count("WARN")
    ok       = all_statuses.count("OK")
    print(f"\n{'='*60}")
    print(f"종합: ✅ OK={ok}  ⚠️ WARN={warn}  🔴 CRITICAL={critical}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="데이터 무결성 헬스체크")
    parser.add_argument("--days",  type=int, default=30, help="조회 기간 (기본: 30일)")
    parser.add_argument("--json",  action="store_true",  help="JSON 출력")
    args = parser.parse_args()

    results = run_checks(days=args.days)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(results)
