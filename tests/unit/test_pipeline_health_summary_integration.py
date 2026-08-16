"""
tests/unit/test_pipeline_health_summary_integration.py

Pipeline Health 집계 → operations_daily_summary CRITICAL 판정 연동 테스트 (2026-08-17).

analysis/decision_health_check._persistence_failure_count()와
analysis/operations_daily_summary._judge_overall()이 새 persistence_failures
필드를 정확히 소비해서 CRITICAL로 승격하는지 확인한다. 실제 DB는 쓰지 않는다
(mock cursor로 SQL 결과만 흉내낸다).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from analysis.decision_health_check import _persistence_failure_count
from analysis.operations_daily_summary import _judge_overall


class _MockCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


class TestPersistenceFailureCount:
    def test_no_failures_returns_zero(self):
        cur = _MockCursor(rows=[])
        result = _persistence_failure_count(cur, date(2026, 8, 14))
        assert result['total'] == 0
        assert result['candidate_insert_failures'] == 0
        assert result['decision_insert_failures'] == 0

    def test_mixed_failure_types_counted_by_type(self):
        cur = _MockCursor(rows=[
            ('CANDIDATE_INSERT', 745),
            ('DECISION_INSERT', 12),
            (None, 3),  # failure_type 파싱 실패 등 예외 케이스
        ])
        result = _persistence_failure_count(cur, date(2026, 8, 12))
        assert result['total'] == 760
        assert result['candidate_insert_failures'] == 745
        assert result['decision_insert_failures'] == 12
        assert result['other_failures'] == 3


class TestJudgeOverallCritical:
    """§5 — Persistence Failures > 0 이면 반드시 CRITICAL이어야 한다.
    기존 insert_failure_pct가 0%(행 자체가 없어 분모가 0이 되는 케이스)로
    위장하더라도, persistence_failures가 그 갭을 메워야 한다 — 이게 이번
    작업 전체의 핵심 목표다."""

    def _base_ok_dicts(self):
        code = {'ok': True, 'code_changed': False, 'restart_count': 0}
        gate = {'ok': True, 'ec_halt': 0, 'system_status': 'NORMAL', 'warnings': None}
        policy = {'ok': True, 'result': 'EFFECTIVE'}
        return code, gate, policy

    def test_zero_persistence_failures_does_not_force_critical(self):
        code, gate, policy = self._base_ok_dicts()
        decision = {
            'ok': True, 'insert_failure_pct': 0.0,
            'persistence_failures': 0, 'candidate_persist_failures': 0, 'decision_persist_failures': 0,
        }
        assert _judge_overall(code, gate, decision, policy) != 'CRITICAL'

    def test_insert_failure_pct_zero_but_persistence_failures_nonzero_is_still_critical(self):
        """이게 이번 실제 장애를 재현하는 핵심 테스트다 — insert_failure_pct가 0.0%로
        위장돼도(2026-08-12~14에 실제로 그랬던 것처럼) persistence_failures가
        0보다 크면 반드시 CRITICAL이어야 한다."""
        code, gate, policy = self._base_ok_dicts()
        decision = {
            'ok': True, 'insert_failure_pct': 0.0,  # 실제 사고 당시처럼 0%로 위장됨
            'persistence_failures': 745, 'candidate_persist_failures': 745, 'decision_persist_failures': 0,
        }
        assert _judge_overall(code, gate, decision, policy) == 'CRITICAL'

    def test_decision_health_unavailable_does_not_crash_judgement(self):
        code, gate, policy = self._base_ok_dicts()
        decision = {'ok': False, 'error': 'db down'}
        # decision.get('ok')가 False면 persistence_failures 체크 자체를 건너뛰어야 하며
        # (KeyError 없이) WARNING으로 떨어져야 한다(기존 decision_health_unavailable 규칙).
        result = _judge_overall(code, gate, decision, policy)
        assert result in ('WARNING', 'NORMAL')

    def test_chain_omitted_is_backward_compatible(self):
        """chain 인자를 생략해도(기존 4-인자 호출) 예외 없이 그대로 동작해야 한다."""
        code, gate, policy = self._base_ok_dicts()
        decision = {'ok': True, 'insert_failure_pct': 0.0,
                    'persistence_failures': 0, 'candidate_persist_failures': 0,
                    'decision_persist_failures': 0}
        assert _judge_overall(code, gate, decision, policy) == 'NORMAL'

    def test_chain_pipeline_fail_forces_critical(self):
        code, gate, policy = self._base_ok_dicts()
        decision = {'ok': True, 'insert_failure_pct': 0.0,
                    'persistence_failures': 0, 'candidate_persist_failures': 0,
                    'decision_persist_failures': 0}
        chain = {'ok': True, 'pipeline_status': 'FAIL'}
        assert _judge_overall(code, gate, decision, policy, chain) == 'CRITICAL'

    def test_chain_pipeline_warning_does_not_force_critical(self):
        code, gate, policy = self._base_ok_dicts()
        decision = {'ok': True, 'insert_failure_pct': 0.0,
                    'persistence_failures': 0, 'candidate_persist_failures': 0,
                    'decision_persist_failures': 0}
        chain = {'ok': True, 'pipeline_status': 'WARNING'}
        assert _judge_overall(code, gate, decision, policy, chain) == 'WARNING'

    def test_chain_pass_stays_normal(self):
        code, gate, policy = self._base_ok_dicts()
        decision = {'ok': True, 'insert_failure_pct': 0.0,
                    'persistence_failures': 0, 'candidate_persist_failures': 0,
                    'decision_persist_failures': 0}
        chain = {'ok': True, 'pipeline_status': 'PASS'}
        assert _judge_overall(code, gate, decision, policy, chain) == 'NORMAL'

    def test_chain_unavailable_is_warning_not_critical(self):
        code, gate, policy = self._base_ok_dicts()
        decision = {'ok': True, 'insert_failure_pct': 0.0,
                    'persistence_failures': 0, 'candidate_persist_failures': 0,
                    'decision_persist_failures': 0}
        chain = {'ok': False, 'error': 'db down'}
        assert _judge_overall(code, gate, decision, policy, chain) == 'WARNING'


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
