"""
Operations Daily Summary

장 종료 후(16:05) 하루 운영 상태를 한 장으로 요약한다.

기존 모듈의 결과만 읽어서 출력한다 — 새로운 계산 로직을 추가하지 않는다.
  * Gate Health      → analysis.gate_health_check.run_gate_health_check() 재사용
  * Decision Health  → analysis.decision_health_check.run_health_check() 재사용
  * Policy Evaluation→ analysis.regime_block_simulator가 이미 기록한
                        analysis/data/regime_block_history.csv 오늘자 행을 읽음 (재계산 안 함)
  * Code Audit       → analysis.code_audit.build_eod_summary() 재사용

실거래 코드(main_auto_trading.py)는 참조하지 않는다. DB 스키마/테이블 변경 없음.

실행:
    python3 -m analysis.operations_daily_summary          # 오늘
    python3 -m analysis.operations_daily_summary --date 2026-07-13
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

LOG_PATH = BASE / 'logs' / 'operations_daily_summary.log'
HISTORY_CSV_PATH = BASE / 'analysis' / 'data' / 'regime_block_history.csv'


# ─── 기존 모듈 결과 수집 (읽기 전용, 재계산 없음) ──────────────────────

def _collect_code_audit(target_date: str) -> Dict[str, Any]:
    from analysis.code_audit import build_eod_summary
    try:
        s = build_eod_summary(target_date)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {'ok': True, **s}


def _collect_gate_health(target_date: date) -> Dict[str, Any]:
    from analysis.gate_health_check import run_gate_health_check, _log_tag_counts
    try:
        report = run_gate_health_check(target_date)
        regime_block = _log_tag_counts(target_date).get('regime_block', 0)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {
        'ok': True,
        'system_status': report['system_status'],
        'ec_halt': report['gate_breakdown'].get('EC_HALT', 0),
        'candidate': report['candidates'],
        'global_gate_passed': report['passed_global_gate'],
        'global_gate_blocked': report['gate_breakdown'].get('__total__', 0),
        'regime_block': regime_block,
        'entry_evaluation': report['entry_signal_checked'],
        'orders_submitted': report['orders_submitted'],
        'warnings': report['warnings'],
    }


def _collect_decision_health(target_date: date) -> Dict[str, Any]:
    from analysis.decision_health_check import run_health_check
    try:
        report = run_health_check(target_date)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {
        'ok': True,
        'recording_success_pct': report['kpi']['recording_success_pct'],
        'missing_trace_ids': report['missing_trace_ids'],
        'broken_lifecycle': report['broken_lifecycle'],
        'insert_failure_pct': report['kpi']['insert_failure_pct'],
        # [Pipeline Health, 2026-08-17] 구조화된 이벤트 카운터 — 로그 grep 아님.
        # decision.get('ok')와 무관하게 항상 존재(run_health_check 자체가 실패하면
        # 이 딕셔너리를 아예 못 만들므로 위 except에서 'ok': False로 걸러짐).
        'persistence_failures': report['kpi']['persistence_failures'],
        'candidate_persist_failures': report['kpi']['candidate_persist_failures'],
        'decision_persist_failures': report['kpi']['decision_persist_failures'],
    }


def _collect_pipeline_health_chain(target_date: date) -> Dict[str, Any]:
    from analysis.pipeline_health_chain import run_chain_check
    try:
        chain = run_chain_check(target_date)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {'ok': True, **chain}


def _collect_policy_evaluation(target_date: str) -> Dict[str, Any]:
    """regime_block_simulator가 이미 써놓은 CSV에서 오늘자 행만 읽는다 (재시뮬레이션 안 함)."""
    if not HISTORY_CSV_PATH.exists():
        return {'ok': False, 'error': 'history csv not found (regime_block_simulator 미실행)'}
    with open(HISTORY_CSV_PATH, newline='', encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r.get('trade_date') == target_date]
    if not rows:
        return {'ok': False, 'error': f'{target_date} 행 없음 (regime_block_simulator 미실행)'}
    row = rows[-1]
    return {
        'ok': True,
        'opportunity_cost_pct': float(row['opportunity_cost']),
        'avoided_loss_pct': float(row['avoided_loss']),
        'net_policy_value_pct': float(row['net_policy_value']),
        'result': row['result'],
    }


# ─── 판정 ───────────────────────────────────────────────────────

def _judge_overall(code: Dict, gate: Dict, decision: Dict, policy: Dict,
                    chain: Optional[Dict] = None) -> str:
    # CRITICAL
    if not gate.get('ok'):
        return 'CRITICAL'
    if not code.get('ok'):
        return 'CRITICAL'
    if gate.get('ec_halt', 0) > 0:
        return 'CRITICAL'
    if decision.get('ok') and decision.get('insert_failure_pct', 0) > 0:
        return 'CRITICAL'
    # [Pipeline Health, 2026-08-17] insert_failure_pct는 candidates 행이 존재하는
    # 경우만 잡는다(분모=행 수라서 완전 실패는 분모째 0이 되어 놓친다 — 실제로
    # 2026-08-12~14 사흘간 이렇게 놓쳤었다). persistence_failures는 행 존재 여부와
    # 무관하게 명시적 실패 이벤트를 세므로 반드시 별도로 확인한다.
    if decision.get('ok') and decision.get('persistence_failures', 0) > 0:
        return 'CRITICAL'
    # [Pipeline Health Chain, 2026-08-17] Candidate/Decision 단건 카운터를 넘어
    # Data→...→Future Return 전체 체인 reconciliation에서 FAIL(=unexplained drop
    # 또는 persistence failure)이 하나라도 나오면 CRITICAL로 승격한다. 기존
    # insert_failure_pct/persistence_failures 체크와 겹칠 수 있지만(둘 다 CANDIDATE/
    # DECISION을 보므로), 체인은 SUBMIT_FILL/TRADE/FUTURE_RETURN까지 보는 상위 집합이라
    # 별도로 유지한다 — 아래 체크가 항상 True거나 항상 False가 아니라 서로 다른 실패를
    # 잡을 수 있다.
    if chain is not None and chain.get('ok') and chain.get('pipeline_status') == 'FAIL':
        return 'CRITICAL'
    if not policy.get('ok'):
        return 'CRITICAL'

    # WARNING
    warnings = []
    if code.get('code_changed'):
        warnings.append('code_changed')
    if code.get('restart_count', 0) > 1:
        warnings.append('restart')
    if gate.get('system_status') != 'NORMAL' or gate.get('warnings'):
        warnings.append('gate_health_warning')
    if policy.get('result') == 'REGIME TOO STRICT':
        warnings.append('policy_warning')
    if not decision.get('ok'):
        warnings.append('decision_health_unavailable')
    if chain is not None and chain.get('ok') and chain.get('pipeline_status') == 'WARNING':
        warnings.append('pipeline_chain_warning')
    if chain is not None and not chain.get('ok'):
        warnings.append('pipeline_chain_unavailable')

    return 'WARNING' if warnings else 'NORMAL'


# ─── 출력 ───────────────────────────────────────────────────────

def _fmt(v, suffix=''):
    return '미확인' if v is None else f"{v}{suffix}"


def build_report_text(target_date: date) -> str:
    d_str = str(target_date)
    code = _collect_code_audit(d_str)
    gate = _collect_gate_health(target_date)
    decision = _collect_decision_health(target_date)
    policy = _collect_policy_evaluation(d_str)
    chain = _collect_pipeline_health_chain(target_date)
    overall = _judge_overall(code, gate, decision, policy, chain)

    lines = []
    lines.append('=' * 50)
    lines.append('OPERATIONS DAILY SUMMARY')
    lines.append(d_str)
    lines.append('=' * 50)

    lines.append('\n[Code Audit]\n')
    if code.get('ok'):
        lines.append(f"Status            : {code.get('status') or 'NO_DATA'}")
        lines.append(f"Restart Count     : {code.get('restart_count', 0)}")
        lines.append(f"Code Change       : {'Yes — ' + str(code.get('changed_files')) if code.get('code_changed') else 'No'}")
        lines.append(f"Warning           : {code.get('warnings') or '-'}")
    else:
        lines.append(f"Status            : FAILED ({code.get('error')})")
    lines.append('-' * 45)

    lines.append('\n[Gate Health]\n')
    if gate.get('ok'):
        lines.append(f"System Status         : {gate['system_status']}")
        lines.append(f"EC_HALT               : {gate['ec_halt']}")
        lines.append(f"Candidate             : {gate['candidate']}")
        lines.append(f"GLOBAL_GATE Passed    : {gate['global_gate_passed']}")
        lines.append(f"GLOBAL_GATE Blocked   : {gate['global_gate_blocked']}")
        lines.append(f"REGIME_BLOCK          : {gate['regime_block']}")
        lines.append(f"Entry Evaluation      : {gate['entry_evaluation']}")
        lines.append(f"Orders Submitted      : {gate['orders_submitted']}")
    else:
        lines.append(f"System Status         : FAILED ({gate.get('error')})")
    lines.append('-' * 45)

    lines.append('\n[Decision Health]\n')
    if decision.get('ok'):
        lines.append(f"Recording Success     : {decision['recording_success_pct']}%")
        lines.append(f"Missing Trace ID      : {decision['missing_trace_ids']}")
        lines.append(f"Broken Lifecycle      : {decision['broken_lifecycle']}")
        lines.append(f"Insert Failure        : {decision['insert_failure_pct']}%")
        lines.append(f"Persistence Failures  : {decision['persistence_failures']}"
                     f"  (Candidate={decision['candidate_persist_failures']}"
                     f" Decision={decision['decision_persist_failures']})")
    else:
        lines.append(f"Recording Success     : FAILED ({decision.get('error')})")
    lines.append('-' * 45)

    lines.append('\n[Pipeline Health Chain]\n')
    if chain.get('ok'):
        from analysis.pipeline_health_chain import render_chain_report
        # render_chain_report()가 자체 헤더/구분선을 포함하므로 그대로 인용한다
        # (§10: 기존 리포트와 충돌하지 않게 섹션으로만 삽입, 재계산 없음).
        lines.append(render_chain_report(chain))
    else:
        lines.append(f"Status                : FAILED ({chain.get('error')})")
    lines.append('-' * 45)

    lines.append('\n[Policy Evaluation]\n')
    if policy.get('ok'):
        lines.append(f"Opportunity Cost      : {policy['opportunity_cost_pct']:+.2f}%")
        lines.append(f"Avoided Loss          : {policy['avoided_loss_pct']:+.2f}%")
        lines.append(f"Net Policy Value      : {policy['net_policy_value_pct']:+.2f}%")
        lines.append(f"Policy Status         : {policy['result']}")
    else:
        lines.append(f"Policy Status         : FAILED ({policy.get('error')})")
    lines.append('-' * 45)

    lines.append(f"\nOVERALL STATUS\n\n{overall}\n")
    lines.append('=' * 50)
    return '\n'.join(lines)


def run(target_date: Optional[date] = None) -> str:
    target_date = target_date or date.today()
    text = build_report_text(target_date)
    print(text)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"\n### generated_at={datetime.now().isoformat(timespec='seconds')} ###\n")
        f.write(text + '\n')
    return text


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Operations Daily Summary')
    parser.add_argument('--date', default=None, help='날짜 (YYYY-MM-DD). 기본: 오늘')
    args = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else date.today()
    run(target)
