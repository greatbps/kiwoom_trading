"""
Intraday Policy Monitor

장중 정기 실행(09:30/10:30/11:30/13:30/14:30/15:20) — RISK_OFF(v1.4 REGIME_BLOCK)
정책이 지금까지 누적 기준으로 얼마나/어떻게 차단하고 있는지 "운영 상태"만 확인한다.

손익 평가(Net Policy Value)는 하지 않는다 — 그건 장 종료 후
analysis/regime_block_simulator.py의 역할이다 (역할 분리, 서로 혼용하지 않음).

기존 analysis/gate_health_check.py의 수집 함수를 그대로 재사용한다
(Candidate/GLOBAL_GATE/EC_HALT/Entry Evaluation/Orders — 새로 만들지 않음).
REGIME_BLOCK 비율 + 최근 20거래일 평균 대비 비교만 이 스크립트에서 추가한다.

실거래 로직은 변경하지 않는다. 읽기 전용.

실행:
    python3 -m analysis.intraday_policy_monitor
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from analysis.gate_health_check import (  # 기존 로직 재사용
    _get_conn,
    _candidate_count,
    _gate_breakdown,
    _passed_global_gate,
    _orders_submitted,
    _recent_trading_days,
    _log_tag_counts,
)


# ─── 수집 ─────────────────────────────────────────────────────────

def _collect_today(cur, target_date: date) -> Dict[str, Any]:
    candidates = _candidate_count(cur, target_date)
    gate_bd    = _gate_breakdown(cur, target_date)
    passed     = _passed_global_gate(cur, target_date)
    orders     = _orders_submitted(cur, target_date)
    logs       = _log_tag_counts(target_date)
    entry_checked = max(0, logs['regime_evaluated'] - logs['regime_block']
                         - logs['afternoon_cutoff_block'] - logs['early_window_block'])
    return {
        'candidates':       candidates,
        'passed':           passed,
        'blocked':          gate_bd.get('__total__', 0),
        'ec_halt':          gate_bd.get('EC_HALT', 0),
        'regime_evaluated': logs['regime_evaluated'],
        'regime_block':     logs['regime_block'],
        'entry_checked':    entry_checked,
        'orders':           orders,
    }


def _collect_20d_avg(cur, target_date: date) -> Dict[str, Any]:
    """최근 20거래일(오늘 제외) 평균 — gate_health_check의 일자별 수집 함수 재사용."""
    days = [d for d in _recent_trading_days(cur, target_date, 21) if d != target_date][-20:]
    if not days:
        return {}

    cand_list: List[int] = []
    block_rate_list: List[float] = []
    entry_rate_list: List[float] = []
    for d in days:
        cand_list.append(_candidate_count(cur, d))
        lg = _log_tag_counts(d)
        if lg['regime_evaluated'] > 0:
            block_rate_list.append(lg['regime_block'] / lg['regime_evaluated'] * 100)
            entry_checked_d = max(0, lg['regime_evaluated'] - lg['regime_block']
                                   - lg['afternoon_cutoff_block'] - lg['early_window_block'])
            entry_rate_list.append(entry_checked_d / lg['regime_evaluated'] * 100)

    return {
        'n_days':               len(days),
        'avg_candidates':       sum(cand_list) / len(cand_list) if cand_list else 0.0,
        'avg_regime_block_rate': sum(block_rate_list) / len(block_rate_list) if block_rate_list else 0.0,
        'avg_entry_eval_rate':  sum(entry_rate_list) / len(entry_rate_list) if entry_rate_list else 0.0,
    }


def _arrow(today: float, avg: float) -> str:
    diff = today - avg
    if abs(diff) < 0.5:
        return f"= {diff:+.0f}%"
    return f"{'▲' if diff > 0 else '▼'} {diff:+.0f}%"


# ─── 리포트 ───────────────────────────────────────────────────────

def run_monitor(target_date: Optional[date] = None) -> Dict[str, Any]:
    target_date = target_date or date.today()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        today = _collect_today(cur, target_date)
        avg = _collect_20d_avg(cur, target_date)
    finally:
        conn.close()

    regime_block_rate = (
        today['regime_block'] / today['regime_evaluated'] * 100
        if today['regime_evaluated'] > 0 else 0.0
    )
    entry_eval_rate = (
        today['entry_checked'] / today['regime_evaluated'] * 100
        if today['regime_evaluated'] > 0 else 0.0
    )

    warnings: List[str] = []
    if regime_block_rate >= 90:
        warnings.append(f"REGIME_BLOCK Rate {regime_block_rate:.0f}% >= 90%")
    if today['ec_halt'] > 0:
        warnings.append(f"EC_HALT 발생 {today['ec_halt']}건 — 즉시 확인 필요")
    if today['candidates'] > 0 and today['entry_checked'] == 0:
        warnings.append("Candidate 존재하나 Entry Evaluation = 0 — 상위 게이트 잠김 의심")
    if today['candidates'] > 0 and today['regime_evaluated'] == 0:
        warnings.append("Candidate 존재하나 v1.4 Regime 미평가")

    system_status = "WARNING" if warnings else "NORMAL"

    return {
        'time':               datetime.now().strftime('%H:%M'),
        'date':               str(target_date),
        'today':              today,
        'avg':                avg,
        'regime_block_rate':  regime_block_rate,
        'entry_eval_rate':    entry_eval_rate,
        'warnings':           warnings,
        'system_status':      system_status,
    }


def print_report(r: Dict[str, Any]) -> None:
    t = r['today']
    avg = r['avg']

    print(f"\n{'='*41}")
    print("INTRADAY POLICY MONITOR")
    print(f"Time : {r['time']}")
    print(f"{'='*41}\n")

    print(f"Candidate              : {t['candidates']}\n")
    print(f"GLOBAL_GATE Passed     : {t['passed']}")
    print(f"GLOBAL_GATE Blocked    : {t['blocked']}\n")
    print(f"REGIME_BLOCK           : {t['regime_block']}")
    print(f"REGIME_BLOCK Rate      : {r['regime_block_rate']:.0f}%\n")
    print(f"Entry Evaluation       : {t['entry_checked']}")
    print(f"Orders Submitted       : {t['orders']}")
    print(f"EC_HALT                : {t['ec_halt']}\n")
    print(f"System Status          : {r['system_status']}")
    print(f"{'='*41}")

    if avg:
        print(f"\n최근 {avg['n_days']}거래일 평균 대비\n")
        print("Candidate")
        print(f"  Today   : {t['candidates']}")
        print(f"  20D Avg : {avg['avg_candidates']:.1f}")
        print(f"  {_arrow(t['candidates'], avg['avg_candidates'])}\n")
        print("REGIME_BLOCK Rate")
        print(f"  Today   : {r['regime_block_rate']:.0f}%")
        print(f"  20D Avg : {avg['avg_regime_block_rate']:.0f}%")
        print(f"  {_arrow(r['regime_block_rate'], avg['avg_regime_block_rate'])}\n")
        print("Entry Evaluation Rate")
        print(f"  Today   : {r['entry_eval_rate']:.0f}%")
        print(f"  20D Avg : {avg['avg_entry_eval_rate']:.0f}%")
        print(f"  {_arrow(r['entry_eval_rate'], avg['avg_entry_eval_rate'])}")
    else:
        print("\n(과거 거래일 데이터 없음 — 20일 비교 생략)")

    if r['warnings']:
        for w in r['warnings']:
            print(f"\nWARNING\n\n{w}")
        print()


if __name__ == '__main__':
    report = run_monitor()
    print_report(report)
