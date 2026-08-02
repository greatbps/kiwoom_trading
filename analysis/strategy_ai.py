"""
Stage 9: Strategy AI
Scientist AI 결과를 읽어 YAML 파라미터 변경 제안을 생성한다.
절대 자동 적용하지 않는다. 승인 요청만 생성한다.

실행:
  python3 -m analysis.strategy_ai                # 최근 Scientist NB 기준
  python3 -m analysis.strategy_ai --nb NB-003   # 특정 NB 기준
  python3 -m analysis.strategy_ai --dry-run      # 저장 없이 출력만
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import psycopg2

PROPOSALS_LOG = Path("logs/strategy_proposals.jsonl")
YAML_PATH     = Path("config/strategy_hybrid.yaml")

# governance_ai를 평가 엔진으로 활용
from analysis.governance_ai import run_governance

DB_CONF = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=int(os.getenv("POSTGRES_PORT", 5432)),
    database=os.getenv("POSTGRES_DB", "trading_system"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD"),
)


def _latest_notebook(conn, nb_no: str | None = None) -> dict | None:
    cur = conn.cursor()
    if nb_no:
        cur.execute(
            "SELECT id, notebook_no, title, findings, conclusion, confidence, tags "
            "FROM research_notebook WHERE notebook_no=%s LIMIT 1",
            (nb_no,)
        )
    else:
        cur.execute(
            "SELECT id, notebook_no, title, findings, conclusion, confidence, tags "
            "FROM research_notebook ORDER BY created_at DESC LIMIT 1"
        )
    row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "notebook_no", "title", "findings", "conclusion", "confidence", "tags"]
    return dict(zip(cols, row))


def _build_proposal_text(nb: dict) -> str:
    """Scientist NB를 governance_ai에 전달할 proposal 텍스트로 변환"""
    return (
        f"Scientist AI {nb['notebook_no']} 기반 전략 파라미터 검토 요청\n\n"
        f"제목: {nb['title']}\n"
        f"주요 발견: {nb['findings']}\n"
        f"결론: {nb['conclusion']}\n"
        f"신뢰도: {nb['confidence']}/5\n"
        f"태그: {', '.join(nb['tags'] or [])}\n\n"
        f"Ruleset: {_current_ruleset()}\n"
        f"요청: 위 분석을 바탕으로 strategy_hybrid.yaml에서 조정할 파라미터를 제안하라. "
        f"자동 적용 금지. 승인 요청만 생성."
    )


def _current_ruleset() -> str:
    import yaml
    try:
        return yaml.safe_load(YAML_PATH.read_text("utf-8")).get("ruleset_version", "unknown")
    except Exception:
        return "unknown"


def _classify_evidence_level(trade_count: int, kpi_met: bool, repeated: bool = False) -> dict:
    """
    증거 등급(Evidence Level) 판정
    E0: 가설 (표본 부족, <10건)      → 정책 변경 불가
    E1: 초기 경향 (10~29건)          → 정책 변경 불가
    E2: 통계적 근거 (30건+, KPI 충족) → 검토 가능
    E3: 반복 재현 (여러 기간 동일 결과) → v1.2 후보
    """
    if repeated and trade_count >= 30 and kpi_met:
        level, label = "E3", "반복 재현 — v1.2 후보"
        can_change = True
    elif trade_count >= 30 and kpi_met:
        level, label = "E2", "통계적 근거 확보 — 검토 가능"
        can_change = True
    elif trade_count >= 10:
        level, label = "E1", "초기 경향 — 정책 변경 불가"
        can_change = False
    else:
        level, label = "E0", "가설(표본 부족) — 정책 변경 불가"
        can_change = False
    return {
        "level":       level,
        "label":       label,
        "trade_count": trade_count,
        "kpi_met":     kpi_met,
        "can_change":  can_change,
    }


def _count_closed_trades() -> int:
    try:
        conn = psycopg2.connect(**DB_CONF)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL")
        cnt = cur.fetchone()[0]
        conn.close()
        return int(cnt)
    except Exception:
        return 0


def _build_evidence_snapshot(nb: dict) -> dict:
    """
    제안서에 첨부할 증거 스냅샷.
    승인자가 파일 하나로 "왜 E2인지" 확인할 수 있어야 한다.
    """
    snap: dict = {
        "generated_at": datetime.now().isoformat(),
        "source_nb":    nb.get("notebook_no"),
        "window":       None,
        "trade_count":  0,
        "kpi_pass":     False,
        "affected_scope": "UNKNOWN",
        "metrics": {
            "win_rate":       None,
            "profit_factor":  None,
            "avg_return_pct": None,
            "avg_mfe_pct":    None,
            "avg_mae_pct":    None,
        },
        "elevel_reason": "",
    }
    try:
        conn = psycopg2.connect(**DB_CONF)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*)                                                   AS total,
                MIN(entry_time)::date                                      AS first_trade,
                MAX(exit_time)::date                                       AS last_trade,
                AVG(CASE WHEN profit_rate > 0 THEN 1.0 ELSE 0.0 END)      AS win_rate,
                SUM(CASE WHEN profit_rate > 0 THEN profit_rate ELSE 0 END)
                  / NULLIF(SUM(CASE WHEN profit_rate <= 0
                                   THEN ABS(profit_rate) ELSE 0 END), 0)  AS profit_factor,
                AVG(profit_rate)                                           AS avg_return,
                AVG(mfe_pct)                                               AS avg_mfe,
                AVG(mae_pct)                                               AS avg_mae
            FROM trades
            WHERE exit_time IS NOT NULL
        """)
        row = cur.fetchone()
        conn.close()

        if row and row[0]:
            total, first, last, wr, pf, avg_r, avg_mfe, avg_mae = row
            snap["trade_count"] = int(total)
            snap["window"]      = f"{first}~{last}"
            snap["metrics"]     = {
                "win_rate":       round(float(wr or 0), 3),
                "profit_factor":  round(float(pf or 0), 2) if pf else None,
                "avg_return_pct": round(float(avg_r or 0), 3),
                "avg_mfe_pct":    round(float(avg_mfe or 0), 2) if avg_mfe else None,
                "avg_mae_pct":    round(float(avg_mae or 0), 2) if avg_mae else None,
            }
            kpi = int(total) >= 30
            snap["kpi_pass"]    = kpi
            snap["elevel_reason"] = (
                f"실거래 {total}건, 승률 {round(float(wr or 0)*100,1)}%, "
                f"PF {round(float(pf or 0), 2) if pf else 'N/A'}, "
                f"KPI {'충족' if kpi else '미달'}"
            )
    except Exception as e:
        snap["error"] = str(e)

    # affected_scope — NB 태그에서 추론
    tags = nb.get("tags") or []
    tags_lower = [str(t).lower() for t in tags]
    if "exploration" in tags_lower:
        snap["affected_scope"] = "EXPLORATION"
    elif "swing" in tags_lower:
        snap["affected_scope"] = "SWING"
    elif tags_lower:
        snap["affected_scope"] = "SWING+EXPLORATION"

    return snap


def _save_proposal(nb: dict, governance_result: dict, evidence: dict, dry_run: bool) -> str:
    proposal_id  = f"PROP-{date.today()}-{str(uuid.uuid4())[:6].upper()}"
    ev_snapshot  = _build_evidence_snapshot(nb)
    record = {
        "proposal_id":          proposal_id,
        "created_at":           datetime.now().isoformat(),
        "status":               "PENDING_APPROVAL",
        "source_nb":            nb["notebook_no"],
        "source_nb_id":         nb["id"],
        "current_ruleset":      _current_ruleset(),
        "evidence_level":       evidence["level"],
        "evidence_label":       evidence["label"],
        "evidence_trade_count": evidence["trade_count"],
        "can_change":           evidence["can_change"],
        "evidence_snapshot":    ev_snapshot,
        "scientist_findings":   nb["findings"],
        "governance_verdict":   governance_result.get("verdict"),
        "governance_risk":      governance_result.get("risk_level"),
        "governance_reasons":   governance_result.get("reasons", []),
        "yaml_suggestions":     governance_result.get("yaml_suggestions", []),
        "auto_applied":         False,
    }
    if not dry_run:
        PROPOSALS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(PROPOSALS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"\n  [STRATEGY_AI] 제안 저장: {PROPOSALS_LOG} (ID={proposal_id})")
    else:
        print(f"\n  [STRATEGY_AI][DRY_RUN] 제안 ID={proposal_id} (저장 안 함)")
    return proposal_id


def run_strategy_ai(nb_no: str | None = None, dry_run: bool = False) -> dict:
    """
    Scientist NB → governance 평가 → 승인 요청 생성 (자동 적용 없음)
    """
    print(f"\n{'='*60}")
    print(f"  Strategy AI — Scientist 결과 → YAML 제안 생성")
    print(f"  자동 적용 없음. 승인 후 v1.2로 증가.")
    print(f"{'='*60}\n")

    conn = psycopg2.connect(**DB_CONF)
    try:
        nb = _latest_notebook(conn, nb_no)
    finally:
        conn.close()

    if nb is None:
        print("  [STRATEGY_AI] Scientist NB 없음 → 분석 데이터 부족")
        return {"status": "no_data"}

    # 증거 등급 판정
    trade_count = _count_closed_trades()
    # KPI 간이 체크: 30건 이상이면 KPI 달성 조건 충족으로 간주 (ops_daily_check가 상세 판단)
    kpi_met = trade_count >= 30
    evidence = _classify_evidence_level(trade_count, kpi_met)

    print(f"  기반 NB: {nb['notebook_no']}  신뢰도={nb['confidence']}/5")
    print(f"  실거래 수: {trade_count}건  →  증거 등급: {evidence['level']} ({evidence['label']})")
    print(f"  발견: {nb['findings'][:120]}...")

    if not evidence["can_change"]:
        print(f"\n  [STRATEGY_AI] 증거 등급 {evidence['level']} — 정책 변경 근거 부족")
        print(f"  → 제안은 기록하되, 승인 가능 등급(E2+) 도달 시까지 적용 불가.")

    if (nb.get("confidence") or 0) < 3:
        print(f"\n  [STRATEGY_AI] 신뢰도 {nb['confidence']}/5 — 최소 기준(3) 미달")
        print(f"  → 데이터 더 필요. 제안 생성 보류.")
        return {"status": "low_confidence", "nb_no": nb["notebook_no"]}

    print(f"\n  Governance AI에 평가 요청 중...\n")
    proposal_text = _build_proposal_text(nb)
    evidence_text = (
        f"Scientist 결론: {nb['conclusion']} "
        f"(신뢰도 {nb['confidence']}/5, NB {nb['notebook_no']}, "
        f"실거래 {trade_count}건, 증거등급 {evidence['level']})"
    )

    try:
        gov_result = run_governance(
            proposal=proposal_text,
            evidence=evidence_text,
            dry_run=dry_run,
        )
    except Exception as e:
        print(f"  [STRATEGY_AI] Governance 평가 실패: {e}")
        return {"status": "governance_error", "error": str(e)}

    proposal_id = _save_proposal(nb, gov_result, evidence, dry_run)

    print(f"\n  ─────────────────────────────────────────")
    print(f"  제안 ID    : {proposal_id}")
    print(f"  증거 등급  : {evidence['level']} — {evidence['label']}")
    if evidence["can_change"]:
        print(f"  상태       : PENDING_APPROVAL (승인 가능)")
        print(f"  승인 명령  : python3 -m analysis.approve_proposal --approve {proposal_id}")
    else:
        print(f"  상태       : PENDING_APPROVAL (⛔ E2 미달 — 승인 불가)")
        print(f"  승인 가능  : 실거래 {30 - trade_count}건 추가 후 재검토")
    print(f"  반려 명령  : python3 -m analysis.approve_proposal --reject {proposal_id}")
    print(f"  ─────────────────────────────────────────\n")

    return {
        "status":         "pending_approval",
        "proposal_id":    proposal_id,
        "nb_no":          nb["notebook_no"],
        "verdict":        gov_result.get("verdict"),
        "risk":           gov_result.get("risk_level"),
        "evidence_level": evidence["level"],
        "can_change":     evidence["can_change"],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stage 9: Strategy AI")
    ap.add_argument("--nb",       default=None, help="특정 NB 번호 (예: NB-003)")
    ap.add_argument("--dry-run",  action="store_true")
    args = ap.parse_args()
    run_strategy_ai(nb_no=args.nb, dry_run=args.dry_run)
