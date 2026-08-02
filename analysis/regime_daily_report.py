"""
Regime Daily Report — Regime Gate 일일 KPI/Opportunity Loss 리포트 (읽기 전용)

작업지시서 "Regime Gate 실거래 Evidence 확보 계획" 작업3(Opportunity Loss)+
작업5(Regime KPI)+작업8(운영 Dashboard 데이터)을 하나의 일일 리포트로 통합한다.
전략/게이트 로직에는 관여하지 않는다.

기존 스크립트 재사용:
    analysis.regime_block_simulator.run_simulation()  — 오늘 REGIME_BLOCK 사후 시뮬레이션
    analysis.gate_funnel._query_funnel()               — 후보/PASS/REGIME_BLOCK 집계
    analysis.performance_metrics.compute_metrics()      — 실거래 KPI
    analysis.regime_evidence_common.classify_evidence_level()

실행:
    python3 -m analysis.regime_daily_report            # 오늘
    python3 -m analysis.regime_daily_report --date 2026-07-24
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

import psycopg2

from analysis.gate_funnel import _query_funnel
from analysis.performance_metrics import compute_metrics
from analysis.regime_block_simulator import run_simulation, DETAIL_CSV_PATH
from analysis.regime_evidence_common import classify_evidence_level

REPORT_DIR = BASE / 'reports'
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _latest_regime_snapshot(cur, target_date: date) -> Dict[str, Any]:
    """오늘 decision_ledger에 기록된 가장 최근 REGIME 관련 feature_snapshot 조회."""
    cur.execute("""
        SELECT feature_snapshot FROM research.decision_ledger
        WHERE decided_at::date = %s AND feature_snapshot ? 'regime_score'
        ORDER BY decided_at DESC LIMIT 1
    """, (str(target_date),))
    row = cur.fetchone()
    return row[0] if row else {}


def _today_realized_trades(cur, target_date: date) -> List[float]:
    """오늘 체결된 BUY-SELL 페어의 profit_rate 리스트 (research.decision_ledger 기준 실거래 KPI)."""
    cur.execute("""
        SELECT DISTINCT ON (b.trade_id) s.profit_rate
        FROM trades b
        JOIN trades s ON b.stock_code = s.stock_code
            AND s.trade_type = 'SELL' AND s.trade_time > b.trade_time
            AND s.trade_time <= b.trade_time + INTERVAL '30 hours'
        WHERE b.trade_type = 'BUY' AND s.profit_rate IS NOT NULL
          AND b.trade_time::date = %s
        ORDER BY b.trade_id, s.trade_time
    """, (str(target_date),))
    return [float(r[0]) / 100 for r in cur.fetchall() if r[0] is not None]


def _cumulative_counts(cur) -> Dict[str, int]:
    """Evidence Level 판정용 누적 카운트 (전체 기간)."""
    cur.execute("SELECT COUNT(*) FROM research.decision_ledger WHERE decision_reason_code='REGIME_BLOCKED'")
    regime_block_n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM research.decision_ledger WHERE decision='PASS'")
    realized_trade_n = cur.fetchone()[0]
    simulated_block_n = 0
    if DETAIL_CSV_PATH.exists():
        with open(DETAIL_CSV_PATH, newline='', encoding='utf-8') as f:
            simulated_block_n = sum(1 for _ in csv.DictReader(f))
    return {'regime_block_n': regime_block_n, 'realized_trade_n': realized_trade_n, 'simulated_block_n': simulated_block_n}


def _today_detail_rows(target_date: date) -> List[Dict[str, str]]:
    if not DETAIL_CSV_PATH.exists():
        return []
    with open(DETAIL_CSV_PATH, newline='', encoding='utf-8') as f:
        return [r for r in csv.DictReader(f) if r.get('trade_date') == str(target_date)]


def build_daily_report(target_date: date) -> Dict[str, Any]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        funnel_data = _query_funnel(cur, target_date, target_date)
        snapshot = _latest_regime_snapshot(cur, target_date)
        realized = _today_realized_trades(cur, target_date)
        cum = _cumulative_counts(cur)
    finally:
        conn.close()

    regime_block_today = funnel_data['reject_counts'].get('REGIME_BLOCKED', 0)
    pass_today = funnel_data['pass_count']
    candidates_today = funnel_data['total_candidates']

    sim_report = run_simulation(target_date)
    detail_rows = _today_detail_rows(target_date)

    trade_metrics = compute_metrics(realized) if realized else None
    evidence = classify_evidence_level(cum['realized_trade_n'], cum['regime_block_n'], cum['simulated_block_n'])

    return {
        'date': str(target_date),
        'regime': snapshot.get('regime') or snapshot.get('market_regime'),
        'regime_score': snapshot.get('regime_score'),
        'kospi_close': snapshot.get('kospi_close'), 'kospi_ema20': snapshot.get('kospi_ema20'),
        'kosdaq_close': snapshot.get('kosdaq_close'), 'kosdaq_ema20': snapshot.get('kosdaq_ema20'),
        'candidates_today': candidates_today,
        'regime_block_today': regime_block_today,
        'pass_today': pass_today,
        'opportunity_loss': sim_report,
        'detail_rows': detail_rows,
        'realized_trades': realized,
        'trade_metrics': trade_metrics,
        'evidence': evidence,
        'cumulative': cum,
    }


def _fmt(v, nd=2):
    return f"{v:,.{nd}f}" if isinstance(v, (int, float)) else "N/A"


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        f"# Regime Daily Report — {report['date']}",
        "",
        "## Today's Regime",
        "",
        f"| 항목 | 값 |",
        f"|---|---|",
        f"| Regime | {report['regime'] or 'N/A'} |",
        f"| Score | {report['regime_score'] if report['regime_score'] is not None else 'N/A'} |",
        f"| KOSPI 종가/EMA20 | {_fmt(report['kospi_close'], 0)} / {_fmt(report['kospi_ema20'], 1)} |",
        f"| KOSDAQ 종가/EMA20 | {_fmt(report['kosdaq_close'], 0)} / {_fmt(report['kosdaq_ema20'], 1)} |",
        "",
        "## 오늘 Funnel",
        "",
        f"| 후보 | REGIME_BLOCK | PASS |",
        f"|---:|---:|---:|",
        f"| {report['candidates_today']} | {report['regime_block_today']} | {report['pass_today']} |",
        "",
        "## Opportunity Loss (오늘 REGIME_BLOCK 사후 시뮬레이션, exit-logic 재생)",
        "",
    ]
    ol = report['opportunity_loss']
    if ol['blocked_trades'] == 0:
        lines.append("차단 종목 없음 또는 가격데이터 미확보")
    else:
        lines += [
            f"- 차단 종목 수: {ol['blocked_trades']}",
            f"- Virtual Win Rate: {ol['win_rate_pct']:.1f}%",
            f"- 평균 수익률: {ol['avg_return_pct']:+.2f}%",
            f"- Opportunity Cost(놓친수익 합): {ol['opportunity_cost_pct']:+.2f}%",
            f"- Avoided Loss(막은손실 합): {ol['avoided_loss_pct']:+.2f}%",
            f"- Net Policy Value: {ol['net_policy_value_pct']:+.2f}% ({ol['result']})",
        ]
    if report['detail_rows']:
        lines.append("")
        lines.append("| 종목 | regime_score | ret_1d | ret_3d | ret_5d | MFE | MAE |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for r in report['detail_rows']:
            lines.append(f"| {r['stock_name']} | {r['regime_score']} | {r['ret_1d']} | {r['ret_3d']} | {r['ret_5d']} | {r['mfe_pct']} | {r['mae_pct']} |")

    lines += ["", "## 실제 거래 (오늘 체결분)", ""]
    tm = report['trade_metrics']
    if tm is None:
        lines.append("오늘 실제 체결 없음")
    else:
        lines += [
            f"- 거래수: {tm['trades']} | Win Rate: {tm['win_rate']*100:.1f}% | PF: {tm['profit_factor']:.3f} | "
            f"평균손익: {tm['expectancy']*100:+.3f}%",
        ]

    ev = report['evidence']
    lines += [
        "",
        "## Evidence Status",
        "",
        f"**{ev['level']}** — {ev['reason']}",
        f"",
        f"판정 기준: {ev['criteria']}",
        f"",
        f"(누적: 실거래 {report['cumulative']['realized_trade_n']}건 / REGIME_BLOCK {report['cumulative']['regime_block_n']}건 / "
        f"시뮬레이션 표본 {report['cumulative']['simulated_block_n']}건)",
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Regime Daily Report (읽기 전용)')
    parser.add_argument('--date', default=None, help='날짜 (YYYY-MM-DD). 기본: 오늘')
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    report = build_daily_report(target)
    md = render_markdown(report)

    out_path = REPORT_DIR / f"regime_daily_report_{target.strftime('%Y%m%d')}.md"
    out_path.write_text(md, encoding='utf-8')
    print(f"✅ {out_path}")
    print(md)
