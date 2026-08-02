"""
Baseline v1.1 — 매일 장 종료 후 5분 점검
실행: python3 -m analysis.ops_daily_check [--date YYYYMMDD]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import psycopg2

RULESET = "v1.1"
LOG_DIR  = Path("logs")
PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        database=os.getenv("POSTGRES_DB", "trading_system"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


def _read_log(log_date: str) -> str:
    path = LOG_DIR / f"auto_trading_{log_date}.log"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def check_ops(target_date: str | None = None) -> dict:
    today = target_date or date.today().strftime("%Y%m%d")
    log   = _read_log(today)

    results: dict[str, dict] = {}

    def _r(key, ok, detail="", warn=False):
        results[key] = {"ok": ok, "detail": detail, "warn": warn}

    # ── 1. 주문 실패 0건 ───────────────────────────────────────────────
    order_fail = len(re.findall(r"주문.*실패|ORDER.*FAIL|매수.*실패|매도.*실패", log, re.I))
    _r("주문 실패", order_fail == 0, f"{order_fail}건")

    # ── 2. API 오류 0건 ───────────────────────────────────────────────
    api_err = len(re.findall(r"API.*오류|API.*error|kiwoom.*error", log, re.I))
    _r("API 오류", api_err == 0, f"{api_err}건")

    # ── 3. AI Timeout 횟수 ────────────────────────────────────────────
    timeouts = len(re.findall(r"\[AI_GATE\].*타임아웃", log))
    _r("AI Timeout 횟수", True, f"{timeouts}건", warn=(timeouts > 2))

    # ── 4. AI Gate 차단 건수 ──────────────────────────────────────────
    gate_blocks = len(re.findall(r"\[AI_GATE\].*차단", log))
    _r("AI Gate 차단 건수", True, f"{gate_blocks}건")

    # ── 5. ENTRY_SNAPSHOT 누락 ────────────────────────────────────────
    snap_count   = len(re.findall(r"\[ENTRY_SNAPSHOT\]", log))
    buy_ok_count = len(re.findall(r"매수완료|BUY_OK|execute_buy.*완료", log))
    missing_snap = max(0, buy_ok_count - snap_count)
    _r("ENTRY_SNAPSHOT 누락", missing_snap == 0,
       f"snapshot={snap_count} buy={buy_ok_count}")

    # ── 6. DB 저장 실패 ───────────────────────────────────────────────
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM trades WHERE DATE(entry_time)=%s",
                (today[:4]+"-"+today[4:6]+"-"+today[6:],)
            )
            db_count = cur.fetchone()[0]
        db_ok = True
        db_detail = f"DB={db_count}건"
    except Exception as e:
        db_ok = False
        db_detail = f"DB 접속 실패: {e}"
    _r("DB 저장 실패", db_ok, db_detail)

    # ── 7. EXPLORATION T+1 정상 청산 ─────────────────────────────────
    expl_t1 = len(re.findall(r"\[EXPLORATION_T1_EXIT\]", log))
    expl_open = len(re.findall(r"EXPLORATION.*진입", log))
    _r("EXPLORATION T+1 청산", True,
       f"T+1청산={expl_t1}건 EXPL진입={expl_open}건")

    # ── 8. 예외(Exception) 발생 여부 ─────────────────────────────────
    exceptions = len(re.findall(r"Traceback|Exception|ERROR", log))
    _r("예외(Exception) 발생", exceptions == 0, f"{exceptions}건", warn=(exceptions > 0))

    # ── 9. 로그 파일 생성 정상 ────────────────────────────────────────
    log_path = LOG_DIR / f"auto_trading_{today}.log"
    log_exists = log_path.exists() and log_path.stat().st_size > 0
    _r("로그 파일 생성", log_exists,
       f"{log_path.stat().st_size//1024}KB" if log_exists else "파일 없음")

    # ── 10. ruleset_version=v1.1 확인 ────────────────────────────────
    ruleset_in_log = f"ruleset={RULESET}" in log
    snap_today = bool(re.search(r"\[ENTRY_SNAPSHOT\].*ruleset=v1\.1", log))
    if snap_count == 0:
        _r(f"ruleset_version={RULESET}", True, "진입 없음 (확인 불필요)")
    else:
        _r(f"ruleset_version={RULESET}", snap_today,
           "ENTRY_SNAPSHOT에서 확인됨" if snap_today else "누락!")

    return {
        "date":     today,
        "ruleset":  RULESET,
        "checks":   results,
    }


def print_report(report: dict) -> None:
    checks = report["checks"]
    d = report["date"]
    print(f"\n{'═'*55}")
    print(f"  Baseline {report['ruleset']} 일일 점검 — {d}")
    print(f"{'═'*55}")

    passed = failed = warned = 0
    for item, v in checks.items():
        if v["warn"]:
            icon = WARN
            warned += 1
        elif v["ok"]:
            icon = PASS
            passed += 1
        else:
            icon = FAIL
            failed += 1
        detail = f"  ({v['detail']})" if v["detail"] else ""
        print(f"  {icon} {item}{detail}")

    total = len(checks)
    print(f"\n  결과: {passed}/{total} 정상" +
          (f" | {warned}건 주의" if warned else "") +
          (f" | {failed}건 실패" if failed else ""))

    if failed == 0 and warned == 0:
        print(f"  ✅ 오늘 운영 정상")
    elif failed == 0:
        print(f"  ⚠️  운영 중이나 주의 항목 확인 필요")
    else:
        print(f"  ❌ {failed}건 즉시 확인 필요")
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Baseline v1.1 일일 점검")
    parser.add_argument("--date", default=None, help="YYYYMMDD (기본: 오늘)")
    args = parser.parse_args()
    report = check_ops(args.date)
    print_report(report)
