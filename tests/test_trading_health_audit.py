"""
tests/test_trading_health_audit.py

analysis/trading_health_audit.py 순수 함수 검증
(classify_root_cause / compute_health_score / build_health_report).

DB/파일 I/O 없음 — tests/test_gate_health_check.py와 동일한 관례를 따른다.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from analysis.trading_health_audit import (
    classify_root_cause,
    compute_health_score,
    build_health_report,
)


# ─── classify_root_cause ────────────────────────────────────────────────────

def test_candidate_count_normal_no_false_trigger():
    cause = classify_root_cause(
        avg_candidates=10, pass_count=5, total_candidates=70,
        regime_block_rate=0.1, ec_halt_rate=0.0, avg_confidence=0.5,
        total_evaluations=50,
    )
    assert cause == 'NORMAL'


def test_candidate_count_insufficient():
    cause = classify_root_cause(
        avg_candidates=2, pass_count=0, total_candidates=14,
        regime_block_rate=0.1, ec_halt_rate=0.0, avg_confidence=None,
        total_evaluations=14,
    )
    assert cause == 'SCAN_TOO_STRICT'


def test_entry_too_strict_when_candidates_plentiful_but_no_pass():
    cause = classify_root_cause(
        avg_candidates=10, pass_count=0, total_candidates=70,
        regime_block_rate=0.1, ec_halt_rate=0.0, avg_confidence=None,
        total_evaluations=70,
    )
    assert cause == 'ENTRY_TOO_STRICT'


def test_gate_over_blocking_regime_dominated():
    cause = classify_root_cause(
        avg_candidates=10, pass_count=3, total_candidates=70,
        regime_block_rate=0.75, ec_halt_rate=0.0, avg_confidence=0.5,
        total_evaluations=70,
    )
    assert cause == 'REGIME_DOMINATED'


def test_ec_halt_check_triggered():
    cause = classify_root_cause(
        avg_candidates=10, pass_count=3, total_candidates=70,
        regime_block_rate=0.1, ec_halt_rate=0.02, avg_confidence=0.5,
        total_evaluations=70,
    )
    assert cause == 'CHECK_EC_HALT'


def test_score_insufficient():
    cause = classify_root_cause(
        avg_candidates=10, pass_count=3, total_candidates=70,
        regime_block_rate=0.1, ec_halt_rate=0.0, avg_confidence=0.05,
        total_evaluations=70,
    )
    assert cause == 'LOW_SCORE_MARKET'


def test_score_insufficient_data_does_not_trigger_false_positive():
    """confidence 표본이 0건이면(avg_confidence=None) LOW_SCORE_MARKET을 유발하면 안 됨."""
    cause = classify_root_cause(
        avg_candidates=10, pass_count=3, total_candidates=70,
        regime_block_rate=0.1, ec_halt_rate=0.0, avg_confidence=None,
        total_evaluations=70,
    )
    assert cause != 'LOW_SCORE_MARKET'
    assert cause == 'NORMAL'


def test_pipeline_failure_overrides_all():
    cause = classify_root_cause(
        avg_candidates=10, pass_count=5, total_candidates=70,
        regime_block_rate=0.1, ec_halt_rate=0.0, avg_confidence=0.5,
        total_evaluations=0,
    )
    assert cause == 'PIPELINE_FAILURE'


def test_root_cause_precedence_f_beats_d():
    """total_evaluations=0 이면서 ec_halt_rate도 높아도 PIPELINE_FAILURE가 우선."""
    cause = classify_root_cause(
        avg_candidates=10, pass_count=0, total_candidates=70,
        regime_block_rate=0.9, ec_halt_rate=0.5, avg_confidence=0.01,
        total_evaluations=0,
    )
    assert cause == 'PIPELINE_FAILURE'


# ─── compute_health_score ───────────────────────────────────────────────────

def test_health_score_full_marks():
    score = compute_health_score(
        avg_candidates=10, total_evaluations=50, pass_count=5,
        regime_block_rate=0.1, confidence_stats={'n': 10, 'avg': 0.5},
    )
    assert score['candidate'] == 20
    assert score['pipeline'] == 20
    assert score['gate'] == 20
    assert score['score'] == 20
    assert score['trade'] == 20
    assert score['total'] == 100


def test_health_score_partial_candidate():
    score = compute_health_score(
        avg_candidates=2.5, total_evaluations=0, pass_count=0,
        regime_block_rate=0.5, confidence_stats={'n': 0},
    )
    assert score['candidate'] == 10  # round(20 * 2.5/5)
    assert score['pipeline'] == 0
    assert score['gate'] == 10       # round(20 * (1-0.5))
    assert score['score'] == 20      # 데이터 없음 → 페널티 없음
    assert score['trade'] == 0
    assert score['total'] == 40


def test_health_score_confidence_capped_at_20():
    score = compute_health_score(
        avg_candidates=10, total_evaluations=10, pass_count=1,
        regime_block_rate=0.0, confidence_stats={'n': 10, 'avg': 0.9},
    )
    assert score['score'] == 20  # min(0.9/0.20, 1.0) → cap


def test_health_score_low_confidence():
    score = compute_health_score(
        avg_candidates=10, total_evaluations=10, pass_count=1,
        regime_block_rate=0.0, confidence_stats={'n': 10, 'avg': 0.1},
    )
    assert score['score'] == 10  # round(20 * 0.1/0.20)


# ─── build_health_report (통합, 여전히 순수 함수) ───────────────────────────

def test_build_health_report_structure_and_root_cause():
    days = [date(2026, 7, 20), date(2026, 7, 21)]
    report = build_health_report(
        period_start=days[0], period_end=days[-1], trading_days=days,
        candidates_by_day={days[0]: 3, days[1]: 4},
        pass_count=0, total_evaluations=7,
        gate_funnel_rows=[{'code': 'PASS', 'label': 'PASS', 'entering': 0, 'rejected': 0, 'passing': 0, 'drop_pct': 0.0}],
        reject_counts={'CHOCH_MISSING': 5, 'REGIME_BLOCKED': 2},
        ec_halt_total=0, total_candidates=7,
        confidence_stats={'n': 0}, regime_by_day={},
    )
    assert report['candidate']['total'] == 7
    assert report['candidate']['avg'] == 3.5
    assert report['root_cause'] == 'SCAN_TOO_STRICT'  # avg_candidates(3.5) < 5
    assert report['top_block_reasons'][0] == ('CHOCH_MISSING', 5)
    assert 'recommendation' in report and report['recommendation']


def test_build_health_report_zero_evaluations_is_pipeline_failure():
    days = [date(2026, 7, 20)]
    report = build_health_report(
        period_start=days[0], period_end=days[0], trading_days=days,
        candidates_by_day={days[0]: 50},
        pass_count=0, total_evaluations=0,
        gate_funnel_rows=[], reject_counts={},
        ec_halt_total=0, total_candidates=50,
        confidence_stats={'n': 0}, regime_by_day={},
    )
    assert report['root_cause'] == 'PIPELINE_FAILURE'
