"""
Gate Health Check 자동 검증 (analysis/gate_health_check.py)

코드 변경 후에도 핵심 판정 로직(경고 발생 조건, System Status, 비율 계산,
JSON/로그 출력 형식)이 정상 동작하는지 확인한다.

build_report()는 DB/로그 파일에 접근하지 않는 순수 함수라 실제 DB 없이
합성 데이터로 테스트한다. 실거래 로직에는 영향 없음 (읽기 전용 분석 대상 테스트).
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from analysis.gate_health_check import build_report, print_report, _PRINT_ORDER


def _empty_gate_bd(total: int = 0) -> dict:
    bd = {name: 0 for name in _PRINT_ORDER}
    bd['__total__'] = total
    return bd


# ─── Case 1: EC_HALT 100% ──────────────────────────────────────────

def test_case1_ec_halt_100pct_triggers_immediate_warning():
    gate_bd = _empty_gate_bd(total=150)
    gate_bd['EC_HALT'] = 150

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=150, gate_bd=gate_bd, passed=0,
        regime_evaluated=0, entry_signal_checked=0, orders=0,
        history_days=[date(2026, 7, 10)],
        history=[{'date': '2026-07-10', 'gate_breakdown': gate_bd, 'regime_evaluated': 0}],
    )

    assert any('EC_HALT is blocking 100%' in w for w in report['warnings'])
    assert report['system_status'] == "WARNING\n\nEC_HALT blocking all candidates"
    assert report['gate_breakdown']['EC_HALT'] == 150


def test_case1_below_100_candidates_does_not_trigger_ec_halt_immediate():
    """candidate < 100이면 EC_HALT 100%여도 즉시경고는 안 뜬다 (작업지시서 조건)."""
    gate_bd = _empty_gate_bd(total=50)
    gate_bd['EC_HALT'] = 50

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=50, gate_bd=gate_bd, passed=0,
        regime_evaluated=0, entry_signal_checked=0, orders=0,
        history_days=[], history=[],
    )

    assert not any('EC_HALT is blocking 100%' in w for w in report['warnings'])


# ─── Case 2: Candidate 존재, Entry Evaluation = 0 ──────────────────

def test_case2_entry_evaluation_zero_triggers_entry_warning():
    gate_bd = _empty_gate_bd(total=0)  # EC_HALT 등 GLOBAL_GATE는 관여 안 함

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=50, gate_bd=gate_bd, passed=50,
        regime_evaluated=50, entry_signal_checked=0, orders=0,
        history_days=[], history=[],
    )

    assert any('Entry Evaluation : 0' in w for w in report['warnings'])
    assert report['system_status'] == "WARNING\n\nNo candidate reached Entry Evaluation"


# ─── Case 3: v1.4 Regime 미평가 ────────────────────────────────────

def test_case3_regime_not_evaluated_triggers_dead_gate_warning():
    gate_bd = _empty_gate_bd(total=50)
    gate_bd['KILL_SWITCH'] = 50  # EC_HALT가 아닌 다른 사유로 차단 — Dead Gate만 단독 검증

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=50, gate_bd=gate_bd, passed=0,
        regime_evaluated=0, entry_signal_checked=0, orders=0,
        history_days=[], history=[],
    )

    assert any('was not evaluated today' in w for w in report['warnings'])
    assert report['system_status'] == "WARNING\n\nv1.4 Regime Gate never evaluated today"


# ─── Case 4: 모든 Gate 정상 ────────────────────────────────────────

def test_case4_all_normal_gives_normal_status():
    gate_bd = _empty_gate_bd(total=5)
    gate_bd['MS_BLOCK'] = 5  # 정상적인 소량 차단(전량 차단 아님)

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=50, gate_bd=gate_bd, passed=45,
        regime_evaluated=45, entry_signal_checked=40, orders=3,
        history_days=[], history=[],
    )

    assert report['warnings'] == []
    assert report['system_status'] == "NORMAL"


# ─── Case 5: Candidate 0건 ─────────────────────────────────────────

def test_case5_zero_candidates_no_exception_no_warning():
    gate_bd = _empty_gate_bd(total=0)

    report = build_report(
        target_date=date(2026, 7, 12),
        candidates=0, gate_bd=gate_bd, passed=0,
        regime_evaluated=0, entry_signal_checked=0, orders=0,
        history_days=[], history=[],
    )

    assert report['warnings'] == []
    assert report['system_status'] == "NORMAL"


def test_case5_zero_candidates_pct_shows_dash_not_exception():
    """비율 계산에서 0으로 나누기 예외 없이 '(-)'로 표시되는지 print_report 출력으로 확인."""
    gate_bd = _empty_gate_bd(total=0)
    report = build_report(
        target_date=date(2026, 7, 12),
        candidates=0, gate_bd=gate_bd, passed=0,
        regime_evaluated=0, entry_signal_checked=0, orders=0,
        history_days=[], history=[],
    )

    print_report(report)  # 예외 없이 종료되어야 함


# ─── 비율 계산 검증 ─────────────────────────────────────────────────

def test_percentage_calc_via_print_report(capsys):
    gate_bd = _empty_gate_bd(total=1449)
    gate_bd['EC_HALT'] = 1449

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=1449, gate_bd=gate_bd, passed=0,
        regime_evaluated=0, entry_signal_checked=0, orders=0,
        history_days=[], history=[],
    )
    print_report(report)
    out = capsys.readouterr().out

    assert "GLOBAL_GATE Blocked      : 1,449 (100%)" in out
    assert "EC_HALT              : 1,449 (100%)" in out
    assert "GLOBAL_GATE Passed       : 0 (0%)" in out


# ─── JSON 출력 형식 검증 ────────────────────────────────────────────

def test_json_output_is_serializable_and_roundtrips():
    gate_bd = _empty_gate_bd(total=150)
    gate_bd['EC_HALT'] = 150

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=150, gate_bd=gate_bd, passed=0,
        regime_evaluated=0, entry_signal_checked=0, orders=0,
        history_days=[date(2026, 7, 10)],
        history=[{'date': '2026-07-10', 'gate_breakdown': gate_bd, 'regime_evaluated': 0}],
    )

    dumped = json.dumps(report, ensure_ascii=False)
    loaded = json.loads(dumped)

    assert loaded['candidates'] == 150
    assert loaded['system_status'] == report['system_status']
    assert isinstance(loaded['warnings'], list)


# ─── 로그(터미널) 출력 형식 검증 ────────────────────────────────────

def test_print_report_contains_required_sections(capsys):
    gate_bd = _empty_gate_bd(total=5)
    gate_bd['MS_BLOCK'] = 5

    report = build_report(
        target_date=date(2026, 7, 10),
        candidates=50, gate_bd=gate_bd, passed=45,
        regime_evaluated=45, entry_signal_checked=40, orders=3,
        history_days=[], history=[],
    )
    print_report(report)
    out = capsys.readouterr().out

    assert "Gate Chain Health Check" in out
    assert "Candidate" in out
    assert "GLOBAL_GATE Passed" in out
    assert "GLOBAL_GATE Blocked" in out
    assert "Entry Evaluation" in out
    assert "Orders Submitted" in out
    assert "System Status" in out
    assert "NORMAL" in out
