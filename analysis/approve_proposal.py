"""
Stage 10: Human Approval
전략 제안 검토 → 승인/반려 → 버전 증가 or Rollback

실행:
  python3 -m analysis.approve_proposal --list
  python3 -m analysis.approve_proposal --approve PROP-2026-07-02-XXXXXX
  python3 -m analysis.approve_proposal --reject  PROP-2026-07-02-XXXXXX
  python3 -m analysis.approve_proposal --rollback v1.0
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PROPOSALS_LOG = Path("logs/strategy_proposals.jsonl")
YAML_PATH     = Path("config/strategy_hybrid.yaml")
VERSIONS_LOG  = Path("logs/ruleset_versions.jsonl")


def _load_proposals() -> list[dict]:
    if not PROPOSALS_LOG.exists():
        return []
    records = []
    for line in PROPOSALS_LOG.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _save_all(records: list[dict]) -> None:
    PROPOSALS_LOG.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8"
    )


def _current_version() -> str:
    import yaml
    try:
        return yaml.safe_load(YAML_PATH.read_text("utf-8")).get("ruleset_version", "v1.0")
    except Exception:
        return "unknown"


def _bump_version(current: str) -> str:
    """v1.1 → v1.2 / v1.9 → v2.0"""
    m = re.match(r"v(\d+)\.(\d+)", current)
    if not m:
        return current + "_next"
    major, minor = int(m.group(1)), int(m.group(2))
    if minor >= 9:
        return f"v{major + 1}.0"
    return f"v{major}.{minor + 1}"


def _set_version(new_version: str) -> None:
    text = YAML_PATH.read_text("utf-8")
    text = re.sub(
        r'^(ruleset_version:\s*")[^"]*(")',
        f'\\g<1>{new_version}\\g<2>',
        text,
        flags=re.MULTILINE,
    )
    YAML_PATH.write_text(text, "utf-8")


def _record_version_change(old: str, new: str, proposal_id: str, action: str) -> None:
    record = {
        "changed_at":   datetime.now().isoformat(),
        "action":       action,
        "from_version": old,
        "to_version":   new,
        "proposal_id":  proposal_id,
    }
    VERSIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(VERSIONS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def cmd_list() -> None:
    records = _load_proposals()
    if not records:
        print("  제안 없음.")
        return
    print(f"\n{'─'*60}")
    print(f"  전략 제안 목록 (총 {len(records)}건)")
    print(f"{'─'*60}")
    for r in records:
        status = r.get("status", "?")
        icon   = "⏳" if status == "PENDING_APPROVAL" else "✅" if status == "APPROVED" else "❌"
        e_lvl  = r.get("evidence_level", "?")
        can    = r.get("can_change", False)
        gate   = "승인 가능" if can else f"⛔ {e_lvl} — 승인 불가 (E2 필요)"
        print(f"  {icon} [{status}] {r['proposal_id']}")
        print(f"     기반: {r.get('source_nb')}  위험도: {r.get('governance_risk')}  "
              f"증거등급: {e_lvl}  생성: {r.get('created_at', '')[:10]}")
        print(f"     판정: {r.get('governance_verdict')}  |  {gate}")
        print()


def cmd_approve(proposal_id: str, comment: str = "", force: bool = False) -> None:
    records = _load_proposals()
    target  = next((r for r in records if r["proposal_id"] == proposal_id), None)
    if target is None:
        print(f"  ❌ 제안 {proposal_id} 없음")
        return
    if target["status"] != "PENDING_APPROVAL":
        print(f"  ⚠️  이미 처리됨: {target['status']}")
        return

    # 승인 사유 필수 — 나중에 "왜 사람이 승인했는지" 추적하기 위해
    if not comment.strip():
        print(f"\n  ❌ --comment 필수")
        print(f"  예시: --comment \"E2 충족, KPI 8개 통과, SWING PF 1.4 확인\"")
        print(f"  사유 없이 승인하면 나중에 검증 불가. 반드시 작성하세요.")
        return

    # 증거 등급 게이트 — E2 미만은 승인 불가
    e_lvl    = target.get("evidence_level", "E0")
    can_chg  = target.get("can_change", False)
    if not can_chg and not force:
        trade_count = target.get("evidence_trade_count", 0)
        print(f"\n  ⛔ 승인 차단 — 증거 등급 {e_lvl} (정책 변경 불가)")
        print(f"  현재 실거래: {trade_count}건 / 필요: 30건")
        print(f"  E2 달성 후 재시도하세요.")
        print(f"  --force는 장애/버그/API변경 대응 시에만 허용됩니다. (CHANGE_CONTROL.md 참고)")
        return

    old_ver = _current_version()
    new_ver = _bump_version(old_ver)

    force_note = " [⚠️ --force 사용]" if force and not can_chg else ""
    print(f"\n  ✅ 승인{force_note}: {proposal_id}")
    print(f"  ruleset_version: {old_ver} → {new_ver}")
    print(f"  사유: {comment}")
    _set_version(new_ver)
    _record_version_change(old_ver, new_ver, proposal_id, "APPROVED" if can_chg else "FORCE_APPROVED")

    target["status"]        = "APPROVED"
    target["approved_at"]   = datetime.now().isoformat()
    target["approve_comment"] = comment
    target["force_used"]    = force and not can_chg
    target["version_bump"]  = {"from": old_ver, "to": new_ver}
    _save_all(records)

    print(f"\n  다음 단계:")
    print(f"    1. strategy_hybrid.yaml 수동 파라미터 반영")
    print(f"    2. python3 -m analysis.e2e_integration_test  (회귀 검증)")
    print(f"    3. Dry Run 1일 확인 후 실거래 적용")
    print(f"\n  Rollback: python3 -m analysis.approve_proposal --rollback {old_ver}\n")


def cmd_reject(proposal_id: str, comment: str = "") -> None:
    records = _load_proposals()
    target  = next((r for r in records if r["proposal_id"] == proposal_id), None)
    if target is None:
        print(f"  ❌ 제안 {proposal_id} 없음")
        return
    if not comment.strip():
        print(f"\n  ❌ --comment 필수")
        print(f"  예시: --comment \"30건 충족했지만 EXPLORATION 성과 악화로 보류\"")
        return
    target["status"]         = "REJECTED"
    target["rejected_at"]    = datetime.now().isoformat()
    target["reject_comment"] = comment
    _save_all(records)
    print(f"  ❌ 반려됨: {proposal_id}")
    print(f"  사유: {comment}")


def cmd_rollback(target_version: str) -> None:
    import yaml
    old_ver = _current_version()
    _set_version(target_version)
    _record_version_change(old_ver, target_version, "manual_rollback", "ROLLBACK")
    print(f"  🔄 Rollback: {old_ver} → {target_version}")
    print(f"  ⚠️  코드 변경이 있었다면 git checkout 또는 수동 복구 필요")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stage 10: Human Approval")
    ap.add_argument("--list",     action="store_true",  help="대기 중인 제안 목록")
    ap.add_argument("--approve",  default=None,         help="승인: 제안 ID")
    ap.add_argument("--reject",   default=None,         help="반려: 제안 ID")
    ap.add_argument("--rollback", default=None,         help="Rollback: 대상 버전 (예: v1.0)")
    ap.add_argument("--force",    action="store_true",  help="E2 미달이어도 강제 승인 (장애/버그 대응만 허용)")
    ap.add_argument("--comment",  default="",           help="승인/반려 사유 (필수)")
    args = ap.parse_args()

    if args.list:
        cmd_list()
    elif args.approve:
        cmd_approve(args.approve, comment=args.comment, force=args.force)
    elif args.reject:
        cmd_reject(args.reject, comment=args.comment)
    elif args.rollback:
        cmd_rollback(args.rollback)
    else:
        ap.print_help()
