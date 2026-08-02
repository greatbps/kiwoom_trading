"""
utils/database_guard.py

파괴적 DB 작업(TRUNCATE/전체 DROP/무조건 DELETE) 실행 전 대상이 실거래 DB가
아닌지 확인하는 공통 가드. (2026-07-27, 운영 안정성 감사 v3.1 — P0.1)

배경: tests/test_research_schema.py, tests/test_decision_service.py가 각자
POSTGRES_DB 문자열을 직접 확인하는 인라인 가드를 따로 구현하고 있었다. 이 모듈이
그 로직을 하나로 통합해, 앞으로 파괴적 작업을 추가하는 모든 코드가 동일한 규칙을
따르도록 한다.

정책:
    - DB 이름에 'test'가 포함되면 허용
    - 그렇지 않으면 ALLOW_DESTRUCTIVE_RESEARCH_TESTS=1 환경변수가 명시적으로
      설정된 경우에만 허용
    - 위 두 조건 모두 아니면 RuntimeError

주의: 이건 "테스트 DB 판별"을 위한 가드이지 운영 DB의 정상적인 애플리케이션
로직(예: database/trading_db.py의 조건부 retention DELETE)을 막기 위한 것이
아니다. 그런 코드는 이 가드를 거칠 필요가 없다 — 대상은 TRUNCATE, DROP TABLE처럼
스코프가 없는(테이블 전체를 대상으로 하는) 파괴적 작업이다.
"""

from __future__ import annotations

import os


class DestructiveOperationBlocked(RuntimeError):
    """파괴적 작업이 안전하지 않은 DB에서 시도됐을 때 발생."""


def is_test_db(db_name: str) -> bool:
    """DB 이름에 'test'가 포함되면 테스트 DB로 간주한다."""
    return 'test' in db_name.lower()


def destructive_ops_explicitly_allowed() -> bool:
    """ALLOW_DESTRUCTIVE_RESEARCH_TESTS=1 환경변수로 명시적 opt-in 여부 확인."""
    return bool(os.getenv('ALLOW_DESTRUCTIVE_RESEARCH_TESTS'))


def assert_destructive_allowed(db_name: str, *, operation: str = 'TRUNCATE/DROP') -> None:
    """
    파괴적 작업(TRUNCATE, 전체 DROP 등) 실행 전 반드시 호출한다.

    허용 조건:
      1. db_name에 'test'가 포함됨, 또는
      2. ALLOW_DESTRUCTIVE_RESEARCH_TESTS=1 환경변수가 설정됨

    두 조건 모두 아니면 DestructiveOperationBlocked을 발생시킨다.
    unittest 컨텍스트에서 스킵 처리가 필요하면 호출부에서 이 예외를 잡아
    unittest.SkipTest로 변환할 것 (get_conn() 등에서 이미 처리 중).
    """
    if is_test_db(db_name) or destructive_ops_explicitly_allowed():
        return
    raise DestructiveOperationBlocked(
        f"실거래 DB 보호: db={db_name!r}는 테스트 DB로 보이지 않아 "
        f"파괴적 작업({operation})을 차단합니다. "
        f"DB 이름에 'test'를 포함하거나 ALLOW_DESTRUCTIVE_RESEARCH_TESTS=1을 설정하세요."
    )
