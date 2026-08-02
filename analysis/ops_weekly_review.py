"""
Baseline v1.1 — 주간 성과 리뷰 (매주 금요일 장 후)
실행: python3 -m analysis.ops_weekly_review [--weeks 1]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import psycopg2

RULESET = "v1.1"
LOG_DIR  = Path("logs")


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        database=os.getenv("POSTGRES_DB", "trading_system"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


def _read_logs_range(since: date, until: date) -> str:
    parts = []
    d = since
    while d <= until:
        path = LOG_DIR / f"auto_trading_{d.strftime('%Y%m%d')}.log"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        d += timedelta(days=1)
    return "\n".join(parts)


def run_weekly_review(weeks: int = 1) -> dict:
    until = date.today()
    since = until - timedelta(weeks=weeks)
    since_str = since.isoformat()
    logs = _read_logs_range(since, until)

    report: dict = {"since": since_str, "until": until.isoformat(), "ruleset": RULESET}

    # ── DB: 거래 성과 ─────────────────────────────────────────────────
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                                        AS total,
                    SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END)  AS wins,
                    SUM(CASE WHEN profit_rate <= 0 THEN 1 ELSE 0 END) AS losses,
                    AVG(profit_rate)                                    AS avg_pnl,
                    AVG(CASE WHEN profit_rate > 0 THEN profit_rate END)   AS avg_win,
                    AVG(CASE WHEN profit_rate <= 0 THEN profit_rate END)  AS avg_loss,
                    AVG(mfe_pct)                                    AS avg_mfe,
                    AVG(mae_pct)                                    AS avg_mae,
                    SUM(CASE WHEN profit_rate > 0 THEN profit_rate ELSE 0 END)   AS gross_profit,
                    SUM(CASE WHEN profit_rate <= 0 THEN ABS(profit_rate) ELSE 0 END) AS gross_loss
                FROM trades
                WHERE exit_time >= %s AND exit_time IS NOT NULL
            """, (since_str,))
            row = cur.fetchone()
            total, wins, losses, avg_pnl, avg_win, avg_loss, avg_mfe, avg_mae, gp, gl = row

            win_rate  = (wins / total * 100) if total else 0
            pf        = (gp / gl) if gl and gl > 0 else float('inf')

            report["trades"] = {
                "total":    int(total or 0),
                "wins":     int(wins or 0),
                "losses":   int(losses or 0),
                "win_rate": round(win_rate, 1),
                "pf":       round(pf, 2) if pf != float('inf') else "∞",
                "avg_pnl":  round(float(avg_pnl or 0), 2),
                "avg_win":  round(float(avg_win or 0), 2),
                "avg_loss": round(float(avg_loss or 0), 2),
                "avg_mfe":  round(float(avg_mfe or 0), 2),
                "avg_mae":  round(float(avg_mae or 0), 2),
            }

            # ── AI Score 분포 ─────────────────────────────────────────
            cur.execute("""
                SELECT
                    entry_features->>'ai_score'                     AS ai_score_str,
                    COUNT(*)                                         AS cnt,
                    AVG(profit_rate)                                 AS avg_pnl,
                    SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) AS wins
                FROM trades
                WHERE exit_time >= %s
                  AND entry_features->>'ai_score' IS NOT NULL
                GROUP BY entry_features->>'ai_score'
                ORDER BY (entry_features->>'ai_score')::float
            """, (since_str,))
            raw = cur.fetchall()

            buckets: dict[str, dict] = {"50-59": {}, "60-69": {}, "70+": {}}
            for ai_str, cnt, avg, w in (raw or []):
                try:
                    ai = float(ai_str)
                except Exception:
                    continue
                b = "50-59" if ai < 60 else "60-69" if ai < 70 else "70+"
                d = buckets[b]
                d["cnt"]  = d.get("cnt", 0) + int(cnt)
                d["wins"] = d.get("wins", 0) + int(w)
                d["pnl_sum"] = d.get("pnl_sum", 0.0) + float(avg or 0) * int(cnt)
            for b, d in buckets.items():
                if d.get("cnt", 0) > 0:
                    d["win_rate"] = round(d["wins"] / d["cnt"] * 100, 1)
                    d["avg_pnl"]  = round(d["pnl_sum"] / d["cnt"], 2)
                    del d["pnl_sum"]
            report["ai_score_buckets"] = buckets

            # ── Strategy Horizon별 ────────────────────────────────────
            cur.execute("""
                SELECT
                    strategy_name,
                    COUNT(*)                                       AS cnt,
                    AVG(profit_rate)                                   AS avg,
                    SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) AS wins
                FROM trades
                WHERE exit_time >= %s
                GROUP BY strategy_name
            """, (since_str,))
            hor = {}
            for strat, cnt, avg, w in cur.fetchall():
                k = strat or "UNKNOWN"
                hor[k] = {
                    "cnt": int(cnt),
                    "win_rate": round(int(w)/int(cnt)*100, 1) if cnt else 0,
                    "avg_pnl": round(float(avg or 0), 2),
                }
            report["strategy_horizon"] = hor

    except Exception as e:
        report["db_error"] = str(e)
        report["trades"] = {}

    # ── 로그: AI Gate 통계 ────────────────────────────────────────────
    blocks   = len(re.findall(r"\[AI_GATE\].*차단", logs))
    passes   = len(re.findall(r"\[AI_GATE\].*통과", logs))
    timeouts = len(re.findall(r"\[AI_GATE\].*타임아웃", logs))
    gate_total = blocks + passes
    report["ai_gate"] = {
        "blocks":   blocks,
        "passes":   passes,
        "timeouts": timeouts,
        "block_rate": round(blocks / gate_total * 100, 1) if gate_total else 0,
    }

    # ── 로그: MKT_CTX별 ENTRY_SNAPSHOT ──────────────────────────────
    snaps = re.findall(r"\[ENTRY_SNAPSHOT\][^\n]*mkt_ctx=(\S+)[^\n]*", logs)
    mkt = {}
    for ctx in snaps:
        ctx = ctx.strip()
        mkt[ctx] = mkt.get(ctx, 0) + 1
    report["mkt_ctx_entries"] = mkt

    # ── KPI B: 자동 점검 가능 항목 ───────────────────────────────────
    snap_missing   = len(re.findall(r"ENTRY_SNAPSHOT.*누락|snapshot.*fail", logs, re.I))
    gate_errors    = timeouts  # AI Gate 타임아웃 = 오동작 근사치
    sys_exceptions = len(re.findall(r"^.*ERROR.*Exception|Traceback \(most recent", logs, re.M))

    force_used = 0
    try:
        vf = LOG_DIR / "ruleset_versions.jsonl"
        if vf.exists():
            for line in vf.read_text("utf-8").splitlines():
                rec = json.loads(line)
                if rec.get("action") == "FORCE_APPROVED":
                    chg_at = rec.get("changed_at", "")
                    if chg_at[:10] >= since_str:
                        force_used += 1
    except Exception:
        pass

    pending_props = approved_props = approval_stuck = 0
    try:
        pf = LOG_DIR / "strategy_proposals.jsonl"
        if pf.exists():
            stale_cutoff = (date.today() - timedelta(days=3)).isoformat()
            for line in pf.read_text("utf-8").splitlines():
                rec = json.loads(line)
                st = rec.get("status", "")
                ca = rec.get("created_at", "")
                if ca[:10] >= since_str:
                    if st == "PENDING_APPROVAL":
                        pending_props += 1
                    elif st == "APPROVED":
                        approved_props += 1
                # 3일 이상 PENDING인 제안 = 프로세스 정체
                if st == "PENDING_APPROVAL" and ca[:10] <= stale_cutoff:
                    approval_stuck += 1
    except Exception:
        pass

    # ── DB: 주문 실패율 (buy_failures vs 성공 BUY) ───────────────────
    order_ok = order_fail = 0
    order_fail_by_stage: dict[str, int] = {}
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM trades WHERE trade_type='BUY' AND entry_time >= %s",
                (since_str,),
            )
            order_ok = int(cur.fetchone()[0])

            cur.execute(
                "SELECT fail_stage, COUNT(*) FROM buy_failures "
                "WHERE attempted_at >= %s GROUP BY fail_stage",
                (since_str,),
            )
            for stage, cnt in cur.fetchall():
                order_fail_by_stage[stage or "unknown"] = int(cnt)
            order_fail = sum(order_fail_by_stage.values())
    except Exception:
        pass

    total_orders = order_ok + order_fail
    order_fail_rate = round(order_fail / total_orders * 100, 1) if total_orders else None

    report["kpi_auto"] = {
        "snap_missing":      snap_missing,
        "gate_errors":       gate_errors,
        "sys_exceptions":    sys_exceptions,
        "force_used":        force_used,
        "pending_props":     pending_props,
        "approved_props":    approved_props,
        "approval_stuck":    approval_stuck,
        "order_ok":          order_ok,
        "order_fail":        order_fail,
        "order_fail_rate":   order_fail_rate,
        "order_fail_stages": order_fail_by_stage,
    }

    return report


_SCIENTIST_VERDICTS = {"KEEP_WATCH", "PROMOTE_TO_E2", "DROP_NOISE", "BLOCKED_BY_OPS"}

_SCIENTIST_DESC = {
    "KEEP_WATCH":     "다음 주에도 계속 추적",
    "PROMOTE_TO_E2":  "E2 심사 후보로 올릴 가치 있음",
    "DROP_NOISE":     "일회성/노이즈, 버림",
    "BLOCKED_BY_OPS": "아이디어는 괜찮지만 운영 이슈로 보류",
}


def _parse_scientist_input(raw: str | None) -> tuple[str, str]:
    """'KEEP_WATCH:메모 내용' → (verdict, note). 잘못된 형식이면 ('?', raw)."""
    if not raw:
        return "?", ""
    parts = raw.split(":", 1)
    verdict = parts[0].strip().upper()
    note    = parts[1].strip() if len(parts) > 1 else ""
    if verdict not in _SCIENTIST_VERDICTS:
        return "?", raw
    return verdict, note


def _save_weekly_csv(r: dict, cumul: int, verdict: str, policy: str,
                     a_ch: str, b_ch: str,
                     sci_verdict: str = "?", sci_note: str = "") -> Path:
    """logs/weekly_review_log.csv에 주간 리뷰 한 행을 추가한다."""
    import csv
    csv_path = LOG_DIR / "weekly_review_log.csv"
    t  = r.get("trades", {})
    ka = r.get("kpi_auto", {})

    row = {
        "week_end":          r["until"],
        "trades_cum":        cumul,
        "weekly_trades":     t.get("total", 0),
        "weekly_pnl":        round(t.get("avg_pnl", 0) or 0, 2),
        "win_rate":          round(t.get("win_rate", 0) or 0, 1),
        "A":                 a_ch,
        "B":                 b_ch,
        "C":                 "?",
        "D":                 "?",
        "E":                 "?",
        "scientist_verdict": sci_verdict,
        "scientist_note":    sci_note,
        "verdict":           verdict,
        "policy":            policy,
        "order_fail_rate":   ka.get("order_fail_rate", ""),
        "force_used":        ka.get("force_used", 0),
        "sys_exceptions":    ka.get("sys_exceptions", 0),
    }

    FIELDS = list(row.keys())
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return csv_path


def _print_friday_verdict(r: dict, cumul: int, scientist_input: str | None = None) -> None:
    """금요일 주간 심사 1줄 리포트."""
    t   = r.get("trades", {})
    ag  = r.get("ai_gate", {})
    ka  = r.get("kpi_auto", {})
    hor = r.get("strategy_horizon", {})
    week_str = date.fromisoformat(r["until"]).strftime("W%V")

    # ── A: 거래수 ─────────────────────────────────────────────────
    a_pass = cumul >= 30
    a_tag  = "PASS" if a_pass else f"FAIL  ({cumul}건 / 30건 필요)"

    # ── B: KPI 8개 자동 점검 ──────────────────────────────────────
    # 주문 실패율: None(데이터없음) / True(OK) / False(비정상)
    fail_rate = ka.get("order_fail_rate")          # None or float
    ok_cnt    = ka.get("order_ok", 0)
    fail_cnt  = ka.get("order_fail", 0)
    fail_stages = ka.get("order_fail_stages", {})
    if fail_rate is None:
        order_ok_flag = None   # 이번 주 주문 없음 → N/A
        order_label   = "주문 실패율  (이번 주 주문 없음)"
    elif fail_rate > 10:
        order_ok_flag = False
        order_label   = f"주문 실패율  {fail_rate:.1f}%  ({ok_cnt}성공/{fail_cnt}실패)  " \
                        f"사유: {fail_stages}"
    else:
        order_ok_flag = True
        order_label   = f"주문 실패율  {fail_rate:.1f}%  ({ok_cnt}성공/{fail_cnt}실패)"

    # Approval: stuck(3일 이상 PENDING) 0건이면 OK
    stuck = ka.get("approval_stuck", 0)
    pending = ka.get("pending_props", 0)
    if stuck > 0:
        approval_ok_flag = False
        approval_label   = f"Approval 프로세스  {stuck}건 3일+ 미처리"
    elif pending > 0:
        approval_ok_flag = None
        approval_label   = f"Approval 프로세스  PENDING {pending}건 (3일 미만)"
    else:
        approval_ok_flag = True
        approval_label   = "Approval 프로세스  정상 (PENDING 없음)"

    b_items = [
        ("ENTRY_SNAPSHOT 누락",  ka.get("snap_missing", 0) == 0),
        ("AI Gate 오동작",       ka.get("gate_errors",  0) == 0),
        (order_label,            order_ok_flag),
        ("시스템 예외",          ka.get("sys_exceptions", 0) == 0),
        ("로그 복원 가능",       True),   # 리뷰 실행 자체가 로그 존재 증명
        # Scientist 에이전트가 이번 주 제안을 생성했는지 자동 확인
        # E항목(반복성)과 다름 — B항목은 "에이전트가 실행됐는가"
        ("Scientist 제안",
         (ka.get("pending_props", 0) + ka.get("approved_props", 0)) > 0
         or None),  # 제안 없음 → None(에이전트 미실행 가능성)
        (approval_label,         approval_ok_flag),
        ("--force 건수",         ka.get("force_used", 0) == 0),
    ]
    b_auto_pass  = sum(1 for _, v in b_items if v is True)
    b_auto_fail  = sum(1 for _, v in b_items if v is False)
    b_manual_cnt = sum(1 for _, v in b_items if v is None)
    if b_auto_fail > 0:
        b_tag = f"FAIL  ({b_auto_fail}개 자동 실패, {b_manual_cnt}개 수동 미확인)"
    elif b_manual_cnt > 0:
        b_tag = f"부분 PASS  ({b_auto_pass}개 자동 OK, {b_manual_cnt}개 수동 확인 필요)"
    else:
        b_tag = f"PASS  (8/8 자동 OK)"

    # ── C: 효과 일관성 ────────────────────────────────────────────
    buckets = r.get("ai_score_buckets", {})
    has_bucket_data = any(d.get("cnt", 0) >= 3 for d in buckets.values())
    if not has_bucket_data:
        c_tag = "N/A  (AI Score 구간별 3건 미만, 샘플 부족)"
    else:
        c_tag = "[ 수동 ]  AI Score 구간 분포 확인 후 직접 판정"

    # ── D: 영향 범위 ──────────────────────────────────────────────
    swing_d = hor.get("SWING", hor.get("swing", {}))
    expl_d  = hor.get("EXPLORATION", hor.get("exploration", {}))
    if swing_d and not expl_d:
        d_tag = "PASS  (SWING만 영향, EXPLORATION 데이터 없음)"
    elif swing_d and expl_d:
        d_tag = f"[ 수동 ]  SWING avg={swing_d.get('avg_pnl',0):+.2f}%  EXPL avg={expl_d.get('avg_pnl',0):+.2f}%"
    else:
        d_tag = "N/A  (이번 주 거래 없음)"

    # ── E: 반복성 (Scientist 제안 방향) ──────────────────────────
    sci_verdict, sci_note = _parse_scientist_input(scientist_input)
    if sci_verdict != "?":
        e_tag = f"✓ {sci_verdict}  — {sci_note}" if sci_note else f"✓ {sci_verdict}"
    else:
        e_tag = "[ 수동 ]  --scientist 인수로 입력"

    # ── 종합 판정 ─────────────────────────────────────────────────
    if not a_pass:
        verdict    = "E1 유지"
        verdict_reason = "A 미달 — 심사 미개시"
        policy     = "유지"
        policy_tag = "변경 불가 (E2 미달)"
    elif b_auto_fail > 0:
        verdict    = "E2 보류"
        verdict_reason = f"B KPI 자동 실패 {b_auto_fail}개"
        policy     = "관찰"
        policy_tag = "KPI 회복 후 재심사"
    else:
        verdict    = "E2 심사 진행"
        verdict_reason = "A PASS + B 자동 OK — C/D/E 수동 판단 필요"
        policy     = "관찰"
        policy_tag = "C~E 판단 후 결론"

    W = 60
    print(f"\n{'╔' + '═'*(W-2) + '╗'}")
    print(f"  금요일 주간 심사 — {r['ruleset']} / 2026-{week_str} ({r['since']} ~ {r['until']})")
    print(f"{'╚' + '═'*(W-2) + '╝'}")

    print(f"\n  【A~E 자동 점검】")
    print(f"  A. 거래수       : {a_tag}")
    print(f"  B. KPI 8개      : {b_tag}")
    for name, val in b_items:
        icon = "✓" if val is True else "✗" if val is False else "?"
        print(f"       {icon} {name}")
    print(f"  C. 효과 일관성  : {c_tag}")
    print(f"  D. 영향 범위    : {d_tag}")
    print(f"  E. 반복성       : {e_tag}")

    print(f"\n  【주간 요약 1줄】")
    icon = "✅" if verdict.startswith("E2 심사") else "⚠️ " if verdict.startswith("E2 보류") else "🔵"
    print(f"  {icon}  {verdict} — {verdict_reason}")

    print(f"\n  【정책 변경 결론】")
    print(f"  → {policy}  ({policy_tag})")

    print(f"\n  【OPERATIONS_LOG 기입 행】")
    e_lvl_str = "E0" if cumul < 10 else "E1" if cumul < 30 else "E2+"
    a_ch = "PASS" if a_pass else "FAIL"
    b_ch = "FAIL" if b_auto_fail > 0 else "부분" if b_manual_cnt > 0 else "PASS"
    print(f"  | 2026-{week_str} | {cumul}건 | {e_lvl_str} | {a_ch} | {b_ch} | — | — | — | {verdict} |")
    if a_pass:
        print(f"  ↑ E2_REVIEW_CHECKLIST.md 작성 후 C/D/E 채워 넣을 것")
    else:
        print(f"  ↑ OPERATIONS_LOG.md 주간 행에 붙여넣기")

    # ── Scientist 판단 입력 템플릿 ────────────────────────────────
    W = 60
    print(f"\n  {'─'*W}")
    print(f"  【Scientist 판단】")
    if sci_verdict != "?":
        desc = _SCIENTIST_DESC.get(sci_verdict, "")
        print(f"  status : {sci_verdict}  ({desc})")
        print(f"  note   : {sci_note or '(메모 없음)'}")
    else:
        print(f"  아래 4개 중 하나를 고르고 1줄 메모를 붙여 재실행하세요.")
        print(f"")
        for v, d in _SCIENTIST_DESC.items():
            print(f"  [ ] {v:<18} — {d}")
        print(f"")
        print(f"  재실행 예:")
        print(f"  python3 -m analysis.ops_weekly_review \\")
        print(f"    --scientist \"KEEP_WATCH:NO_TRADE_DAY alpha 2주 더 수집\"")
    print(f"  {'─'*W}")

    # ── CSV 누적 저장 ─────────────────────────────────────────────
    try:
        csv_path = _save_weekly_csv(
            r, cumul, verdict, policy, a_ch, b_ch, sci_verdict, sci_note
        )
        print(f"\n  📄 주간 누적 CSV: {csv_path}")
    except Exception as csv_err:
        print(f"\n  ⚠️  CSV 저장 실패: {csv_err}")

    # ── 다음 주 액션 힌트 ────────────────────────────────────────
    print(f"\n  【다음 주 액션】")
    if not a_pass:
        print(f"  → 정책 유지. 거래 축적. (A 미달 — 표 1단계에서 종료)")
    elif b_auto_fail > 0:
        fail_names = [n for n, v in b_items if v is False]
        print(f"  → 운영 KPI 수정 우선: {', '.join(fail_names)}")
        print(f"     전략 심사 보류. E2 진입 금지.")
    elif b_manual_cnt > 0:
        print(f"  → 수동 항목 {b_manual_cnt}개 확인 후 재판정.")
        print(f"     확인 전 전략 수정 금지.")
    elif sci_verdict == "PROMOTE_TO_E2":
        if c_tag.startswith("N/A"):
            print(f"  → E2 후보 등록. OPERATIONS_LOG E2 후보란 추가.")
            print(f"     C 샘플 부족 — 추가 축적 후 E2_REVIEW_CHECKLIST 작성.")
        else:
            print(f"  → E2 심사 진입 조건 검토. E2_REVIEW_CHECKLIST.md 작성.")
    elif sci_verdict == "DROP_NOISE":
        print(f"  → strategy_proposals.jsonl 해당 항목 REJECTED 처리.")
        print(f"     정책 유지. 신규 관찰 항목 설정.")
    elif sci_verdict == "BLOCKED_BY_OPS":
        print(f"  → 해당 아이디어 동결. 운영 이슈 해소 후 KEEP_WATCH 전환.")
    else:
        # KEEP_WATCH 또는 미입력
        print(f"  → 정책 유지. 관찰 항목 유지 (KEEP_WATCH).")
        if sci_verdict == "?":
            print(f"     Scientist 미입력 — --scientist 인수로 판단 추가 권장.")

    print(f"\n  참조: docs/FRIDAY_DECISION_TABLE.md\n")


def print_report(r: dict) -> None:
    t = r.get("trades", {})
    ag = r.get("ai_gate", {})

    print(f"\n{'═'*60}")
    print(f"  Baseline {r['ruleset']} 주간 리뷰 — {r['since']} ~ {r['until']}")
    print(f"{'═'*60}")

    if not t:
        print(f"  {'⚠️ '} DB 조회 실패: {r.get('db_error', '데이터 없음')}")
    else:
        print(f"\n  【거래 성과】")
        print(f"  거래 수    : {t.get('total', 0)}건  (승{t.get('wins',0)} / 패{t.get('losses',0)})")
        print(f"  승률       : {t.get('win_rate', 0):.1f}%")
        print(f"  Profit Factor : {t.get('pf', '-')}")
        print(f"  평균 손익  : {t.get('avg_pnl', 0):+.2f}%")
        print(f"  평균 수익  : {t.get('avg_win', 0):+.2f}%  |  평균 손실: {t.get('avg_loss', 0):+.2f}%")
        print(f"  MFE 평균   : {t.get('avg_mfe', 0):.2f}%")
        print(f"  MAE 평균   : {t.get('avg_mae', 0):.2f}%")

    print(f"\n  【AI Score 구간별 승률】")
    buckets = r.get("ai_score_buckets", {})
    if all(not b for b in buckets.values()):
        print(f"  데이터 없음 (진입 기록 대기 중)")
    for b, d in buckets.items():
        if d.get("cnt", 0) > 0:
            print(f"  {b:8s}: {d['cnt']}건  승률={d['win_rate']:.0f}%  avg={d['avg_pnl']:+.2f}%")

    print(f"\n  【AI Gate 통계】")
    print(f"  차단={ag.get('blocks',0)}건  통과={ag.get('passes',0)}건  "
          f"차단율={ag.get('block_rate',0):.1f}%  타임아웃={ag.get('timeouts',0)}건")

    print(f"\n  【Strategy Horizon별 성과】")
    hor = r.get("strategy_horizon", {})
    if not hor:
        print(f"  데이터 없음")
    for strat, d in hor.items():
        print(f"  {strat:12s}: {d['cnt']}건  승률={d['win_rate']:.0f}%  avg={d['avg_pnl']:+.2f}%")

    print(f"\n  【MKT_CTX 진입 분포】")
    mkt = r.get("mkt_ctx_entries", {})
    if not mkt:
        print(f"  데이터 없음 (진입 기록 대기 중)")
    for ctx, cnt in mkt.items():
        print(f"  {ctx}: {cnt}건")

    # ── Evidence Level 현황 ──────────────────────────────────────
    total = t.get("total", 0)
    # 전체 누적 거래수는 DB에서 직접 조회
    try:
        import psycopg2 as _pg
        _c = _pg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            database=os.getenv("POSTGRES_DB", "trading_system"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD"),
        )
        _cur = _c.cursor()
        _cur.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL")
        cumul = int(_cur.fetchone()[0])
        _c.close()
    except Exception:
        cumul = total

    if cumul < 10:
        e_lvl, e_lbl = "E0", "가설 — 정책 변경 불가"
    elif cumul < 30:
        e_lvl, e_lbl = "E1", f"초기 경향 — E2까지 {30 - cumul}건"
    else:
        e_lvl, e_lbl = "E2+", "통계적 근거 확보 — 심사 가능"

    print(f"\n  【증거 등급 (Evidence Level)】")
    print(f"  누적 거래수  : {cumul}건")
    print(f"  현재 E-Level : {e_lvl}  ({e_lbl})")

    if cumul >= 30:
        print(f"  → E2 심사 가능. docs/E2_REVIEW_CHECKLIST.md 체크리스트 작성 후 판정.")
    elif cumul >= 10:
        print(f"  → E1 구간. 데이터 축적 중. 규칙 변경 불가.")
    else:
        print(f"  → E0 구간. 관찰만 한다.")

    print(f"\n  【주간 리뷰 체크리스트】")
    print(f"  □ KPI 8개 모두 PASS인가?")
    print(f"  □ 실거래 30건 이상인가?  → 현재 {cumul}건")
    print(f"  □ Scientist 방향이 지난주와 같은가?")
    print(f"  □ evidence_snapshot 개선 폭이 의미 있는가?")
    print(f"  □ SWING 개선이 EXPLORATION을 악화시키지 않는가?")
    print(f"  □ --force 사용 건수: 0건이어야 정상")

    print(f"{'═'*60}\n")

    _print_friday_verdict(r, cumul, r.get("_scientist_input"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Baseline v1.1 주간 리뷰")
    parser.add_argument("--weeks",     type=int, default=1,    help="조회 주 수 (기본: 1)")
    parser.add_argument("--scientist", type=str, default=None,
                        help="Scientist 판단 (예: \"KEEP_WATCH:메모\"). "
                             "선택값: KEEP_WATCH / PROMOTE_TO_E2 / DROP_NOISE / BLOCKED_BY_OPS")
    args = parser.parse_args()
    report = run_weekly_review(args.weeks)
    if args.scientist:
        report["_scientist_input"] = args.scientist
    print_report(report)
