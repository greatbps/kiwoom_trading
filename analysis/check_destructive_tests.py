"""
analysis/check_destructive_tests.py

CI 검사: 파괴적 SQL(TRUNCATE / 스코프 없는 DELETE / DROP TABLE)이
utils/database_guard 없이 사용되고 있지 않은지 정적 검사한다.
(2026-07-27, 운영 안정성 감사 v3.1 — P0.1)

배경: tests/test_research_schema.py, tests/test_decision_service.py가 각각
독립적으로 research 스키마를 TRUNCATE하다가 실거래 DB를 반복적으로 비운
사고가 있었다. 이 스크립트는 향후 같은 실수(가드 없는 파괴적 SQL 추가)를
커밋 전에 잡기 위한 것이다.

검사 대상과 정책:
  - TRUNCATE, DROP TABLE: 스코프 개념이 없는(테이블 전체 대상) 작업이므로
    항상 위험 — 파일 안에 database_guard 참조가 없으면 무조건 위반.
  - DELETE FROM: WHERE 절이 있으면 정상적인 scoped 삭제(예: 오래된 레코드
    정리)로 간주해 통과. WHERE 절이 없는 "DELETE FROM table" 형태만
    TRUNCATE와 동일한 위험도로 보고 위반 처리.

한계: 파일 단위로 "이 파일에 database_guard 참조가 있는가"만 확인하는
가벼운 검사다. 같은 파일 안에서 그 TRUNCATE/DELETE 호출 직전에 실제로
가드가 실행되는지까지는 보장하지 않는다 — 새 파괴적 SQL을 추가할 때는
반드시 코드 리뷰로 실제 가드 호출 여부를 확인할 것.

실행:
    python3 -m analysis.check_destructive_tests
    python3 -m analysis.check_destructive_tests --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, NamedTuple

BASE_DIR = Path(__file__).parent.parent

EXCLUDE_DIRS = {
    'venv', '__pycache__', '.git', 'node_modules', 'backups', 'archive',
    'htmlcov', '.pytest_cache',
}

# 이 파일 자체와 가드 모듈 정의 파일은 정의/패턴 문자열을 담고 있을 뿐
# 실제 실행이 아니므로 검사 대상에서 제외.
SELF_EXEMPT = {
    'analysis/check_destructive_tests.py',
    'utils/database_guard.py',
}

# 검토 완료된 예외: PostgreSQL research/trading 데이터가 아니라 자체 SQLite
# 로컬 캐시 파일을 다루는 코드라 실거래 데이터 유실 위험이 없다.
KNOWN_SAFE = {
    ('utils/cache.py', 'DELETE FROM cache'),
}

_TRUNCATE_RE = re.compile(r'\bTRUNCATE\b', re.IGNORECASE)
_DROP_TABLE_RE = re.compile(r'\bDROP\s+TABLE\b', re.IGNORECASE)
_DELETE_FROM_RE = re.compile(r'\bDELETE\s+FROM\s+([A-Za-z0-9_.]+)', re.IGNORECASE)
_GUARD_REF_RE = re.compile(r'database_guard')
_EXECUTE_CALL_RE = re.compile(r'\.execute\s*\(\s*f?["\']')


class Violation(NamedTuple):
    file: str
    line: int
    kind: str
    snippet: str


def _iter_py_files():
    for p in BASE_DIR.rglob('*.py'):
        rel = p.relative_to(BASE_DIR)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if str(rel).replace('\\', '/') in SELF_EXEMPT:
            continue
        yield p, rel


def _has_where_nearby(text: str, start_idx: int, window: int = 300) -> bool:
    """DELETE FROM 위치 이후 window자 이내에 WHERE가 있으면 scoped로 간주."""
    snippet = text[start_idx:start_idx + window]
    # 다음 SQL 문 경계(세미콜론 또는 3중따옴표 종료)를 만나면 그 전까지만 본다
    for terminator in (';', '"""', "'''"):
        idx = snippet.find(terminator)
        if idx != -1:
            snippet = snippet[:idx]
            break
    return re.search(r'\bWHERE\b', snippet, re.IGNORECASE) is not None


def _is_actually_executed(text: str, start_idx: int, lookback: int = 150) -> bool:
    """이 SQL 키워드가 실제 cursor.execute()류 호출 안의 문자열인지, 아니면
    governance_ai.py의 HARD_BLOCKS처럼 비교/문서용 문자열 데이터인지 구분한다.
    직전 lookback자 이내에 .execute(" 패턴이 있으면 실제 실행으로 간주."""
    before = text[max(0, start_idx - lookback):start_idx]
    return bool(_EXECUTE_CALL_RE.search(before))


def scan_file(path: Path, rel: Path) -> List[Violation]:
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return []

    has_guard_ref = bool(_GUARD_REF_RE.search(text))
    violations: List[Violation] = []

    def line_of(idx: int) -> int:
        return text.count('\n', 0, idx) + 1

    def is_known_safe(snippet: str) -> bool:
        return any(rel_str == str(rel) and marker in snippet for rel_str, marker in KNOWN_SAFE)

    if not has_guard_ref:
        for m in _TRUNCATE_RE.finditer(text):
            if not _is_actually_executed(text, m.start()):
                continue
            ln = line_of(m.start())
            snippet = text.splitlines()[ln - 1].strip()
            if is_known_safe(snippet):
                continue
            violations.append(Violation(str(rel), ln, 'TRUNCATE without guard', snippet))

        for m in _DROP_TABLE_RE.finditer(text):
            if not _is_actually_executed(text, m.start()):
                continue
            ln = line_of(m.start())
            snippet = text.splitlines()[ln - 1].strip()
            if is_known_safe(snippet):
                continue
            violations.append(Violation(str(rel), ln, 'DROP TABLE without guard', snippet))

        for m in _DELETE_FROM_RE.finditer(text):
            if _has_where_nearby(text, m.start()):
                continue  # scoped delete — 정상적인 retention/cleanup 로직으로 간주
            if not _is_actually_executed(text, m.start()):
                continue  # 실행되지 않는 문자열 데이터(예: governance_ai.py 패턴 목록)
            ln = line_of(m.start())
            snippet = text.splitlines()[ln - 1].strip()
            if is_known_safe(snippet):
                continue
            violations.append(Violation(str(rel), ln, 'unscoped DELETE FROM without guard', snippet))

    return violations


def main():
    parser = argparse.ArgumentParser(description="파괴적 SQL 가드 미적용 검사 (CI용)")
    parser.add_argument('--json', action='store_true', help='JSON 출력')
    args = parser.parse_args()

    all_violations: List[Violation] = []
    for path, rel in _iter_py_files():
        all_violations.extend(scan_file(path, rel))

    if args.json:
        print(json.dumps([v._asdict() for v in all_violations], ensure_ascii=False, indent=2))
    else:
        print()
        print("Destructive SQL Guard Check")
        print()
        if not all_violations:
            print("위반 0건 — 모든 TRUNCATE/DROP TABLE/무조건 DELETE FROM이 database_guard 참조 파일 내에 있음")
        else:
            for v in all_violations:
                print(f"  [{v.kind}] {v.file}:{v.line}")
                print(f"      {v.snippet}")
        print()
        print(f"STATUS : {'FAIL' if all_violations else 'PASS'} ({len(all_violations)}건)")
        print()

    sys.exit(1 if all_violations else 0)


if __name__ == '__main__':
    main()
