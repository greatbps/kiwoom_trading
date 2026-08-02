"""
Code Audit Baseline — 운영 코드 변경 감사

main_auto_trading.py 실행 코드가 장중에 변경/재시작됐는지를 외부에서
감시하고, 하루 단위로 감사 로그(logs/code_audit.jsonl)에 남긴다.

설계 원칙: 감시 대상 프로세스(main_auto_trading.py)는 전혀 수정하지 않는다.
PID/시작시각은 ps로 외부 조회, 코드 상태는 git + 파일 해시로 외부 조회한다.
읽기 전용. 실거래 로직에 관여하지 않는다.

실행:
    python3 -m analysis.code_audit --check        # 장중 주기 실행 (권장: */5 8-15 평일)
    python3 -m analysis.code_audit --eod-report    # 장 마감 후 1회 실행
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).parent.parent
AUDIT_LOG = BASE / 'logs' / 'code_audit.jsonl'

WATCHED_DIRS = ['services', 'strategy', 'config']
WATCHED_FILES = ['main_auto_trading.py']
REFERENCE_DIRS = ['analysis']  # 참고용 — 감사 대상 아님, 별도 표시만

PROC_PATTERN = 'main_auto_trading.py'


# ─── Git / Process 조회 (읽기 전용) ────────────────────────────────

def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(
            cmd, cwd=BASE, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def get_git_snapshot() -> Dict[str, Any]:
    return {
        'branch': _run(['git', 'rev-parse', '--abbrev-ref', 'HEAD']),
        'commit': _run(['git', 'rev-parse', 'HEAD']),
        'status_short': _run(['git', 'status', '--short']).splitlines(),
        'dirty_files': _run(['git', 'diff', '--name-only']).splitlines()
                        + _run(['git', 'ls-files', '--others', '--exclude-standard']).splitlines(),
    }


def get_process_snapshot() -> Optional[Dict[str, Any]]:
    out = _run(['pgrep', '-f', PROC_PATTERN])
    pids = [p for p in out.splitlines() if p.strip()]
    if not pids:
        return None
    pid = pids[0]
    ps_out = _run(['ps', '-o', 'lstart=', '-p', pid])
    return {'pid': pid, 'started_at': ps_out.strip()}


def _iter_watched_files() -> List[Path]:
    files: List[Path] = []
    for f in WATCHED_FILES:
        p = BASE / f
        if p.exists():
            files.append(p)
    for d in WATCHED_DIRS:
        dp = BASE / d
        if dp.exists():
            files.extend(sorted(dp.rglob('*.py')))
            files.extend(sorted(dp.rglob('*.yaml')))
    return files


def _iter_reference_files() -> List[Path]:
    files: List[Path] = []
    for d in REFERENCE_DIRS:
        dp = BASE / d
        if dp.exists():
            files.extend(sorted(dp.rglob('*.py')))
    return files


def _hash_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return ""


def get_file_hashes(paths: List[Path]) -> Dict[str, str]:
    return {str(p.relative_to(BASE)): _hash_file(p) for p in paths}


# ─── 스냅샷 기록/조회 ───────────────────────────────────────────────

def build_snapshot(event: str) -> Dict[str, Any]:
    return {
        'event': event,
        'ts': datetime.now().isoformat(timespec='seconds'),
        'git': get_git_snapshot(),
        'process': get_process_snapshot(),
        'hashes': get_file_hashes(_iter_watched_files()),
        'reference_hashes': get_file_hashes(_iter_reference_files()),
    }


def append_snapshot(snap: Dict[str, Any]) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(snap, ensure_ascii=False) + '\n')


def read_today_snapshots(target_date: Optional[str] = None) -> List[Dict[str, Any]]:
    target_date = target_date or datetime.now().strftime('%Y-%m-%d')
    if not AUDIT_LOG.exists():
        return []
    out = []
    with open(AUDIT_LOG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get('ts', '').startswith(target_date):
                out.append(rec)
    return out


def diff_hashes(prev: Dict[str, str], curr: Dict[str, str]) -> List[str]:
    changed = []
    for k in set(prev) | set(curr):
        if prev.get(k) != curr.get(k):
            changed.append(k)
    return sorted(changed)


# ─── --check: 장중 주기 실행 ────────────────────────────────────────

def run_check() -> Dict[str, Any]:
    """직전 기록과 비교해 (1) 코드 변경 (2) 프로세스 재시작 여부를 감지하고 기록한다."""
    today = read_today_snapshots()
    curr = build_snapshot('periodic')

    if not today:
        curr['event'] = 'process_start'
        curr['warning'] = None
        append_snapshot(curr)
        return curr

    prev = today[-1]
    warnings = []

    prev_pid = (prev.get('process') or {}).get('pid')
    curr_pid = (curr.get('process') or {}).get('pid')
    restarted = prev_pid is not None and curr_pid is not None and prev_pid != curr_pid
    if prev_pid is not None and curr_pid is None:
        warnings.append(f"프로세스 중단 감지 (직전 PID={prev_pid})")
    if restarted:
        curr['event'] = 'process_restart_detected'

    changed = diff_hashes(prev.get('hashes', {}), curr.get('hashes', {}))
    if changed:
        curr['changed_files'] = changed
        if restarted:
            warnings.append(f"재시작 직전 코드 변경 감지: {changed}")
        else:
            warnings.append(f"⚠️ 프로세스 재시작 없이 감시 대상 파일 변경 감지 (라이브 리로드 의심): {changed}")

    curr['warning'] = warnings or None
    append_snapshot(curr)

    if warnings:
        for w in warnings:
            print(f"[CODE_AUDIT] WARNING: {w}")
    else:
        print(f"[CODE_AUDIT] OK — PID={curr_pid}, 변경 없음")
    return curr


# ─── --eod-report: 장 마감 후 요약 ──────────────────────────────────

def build_eod_summary(target_date: Optional[str] = None) -> Dict[str, Any]:
    """오늘자 스냅샷을 집계해 구조화된 요약을 반환한다 (다른 스크립트 재사용용)."""
    today = read_today_snapshots(target_date)
    if not today:
        return {
            'has_data': False,
            'status': None,
            'restart_count': 0,
            'code_changed': False,
            'changed_files': [],
            'changed_after_restart': False,
            'inplace_change_count': 0,
            'warnings': [],
        }

    restarts = [r for r in today if r['event'] in ('process_restart_detected', 'process_start')]
    all_changed = sorted({f for r in today for f in r.get('changed_files', [])})
    changed_after_restart = any(
        r['event'] == 'process_restart_detected' and r.get('changed_files') for r in today
    )
    inplace_changes = [r for r in today if r.get('changed_files') and r['event'] == 'periodic']
    warnings = [w for r in today for w in (r.get('warning') or [])]

    status = 'WARNING' if (all_changed or warnings) else 'NORMAL'

    return {
        'has_data': True,
        'status': status,
        'restart_count': len(restarts),
        'code_changed': bool(all_changed),
        'changed_files': all_changed,
        'changed_after_restart': changed_after_restart,
        'inplace_change_count': len(inplace_changes),
        'warnings': warnings,
        'snapshots': today,
    }


def run_eod_report() -> None:
    summary = build_eod_summary()
    today = summary.get('snapshots', [])
    print(f"\n{'='*46}")
    print("Code Audit — 장 종료 보고")
    print(f"Date : {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*46}\n")

    if not summary['has_data']:
        print("오늘 기록된 스냅샷이 없습니다 (--check가 실행되지 않음).")
        return

    restarts = [r for r in today if r['event'] in ('process_restart_detected', 'process_start')]
    all_changed = summary['changed_files']
    changed_after_restart = summary['changed_after_restart']
    inplace_changes = [r for r in today if r.get('changed_files') and r['event'] == 'periodic']

    print(f"오늘 코드 변경 여부      : {'있음' if all_changed else '없음'}")
    print(f"변경 파일 목록          : {all_changed or '-'}")
    print(f"재시작 횟수(최초 시작 포함) : {len(restarts)}")
    print(f"변경 후 재시작 여부      : {'예' if changed_after_restart else '아니오'}")
    if inplace_changes:
        print(f"⚠️ 재시작 없이 감지된 실시간 파일 변경: {len(inplace_changes)}건")

    print("\n--- 재시작/시작 상세 ---")
    for r in restarts:
        proc = r.get('process') or {}
        print(f"  [{r['event']}] ts={r['ts']} pid={proc.get('pid')} started_at={proc.get('started_at')}")
        if r.get('changed_files'):
            print(f"      변경파일: {r['changed_files']}")
        git = r.get('git', {})
        print(f"      git: branch={git.get('branch')} commit={git.get('commit', '')[:12]} "
              f"dirty={len(git.get('dirty_files', []))}개")

    print(f"\n{'='*46}")
    if all_changed:
        print("운영 감사 결과: WARNING — 장중 코드 변경이 발생했습니다. 변경 내용과 검증 결과는 별도 확인하세요.")
    else:
        print("운영 감사 결과: NORMAL — 오늘 감시 대상 코드는 변경되지 않았습니다.")
    print(f"{'='*46}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Code Audit Baseline')
    parser.add_argument('--check', action='store_true', help='현재 상태 스냅샷 + 직전 대비 변경/재시작 감지')
    parser.add_argument('--eod-report', action='store_true', help='오늘자 감사 요약 출력')
    args = parser.parse_args()

    if args.eod_report:
        run_eod_report()
    elif args.check:
        run_check()
    else:
        parser.print_help()
        sys.exit(1)
