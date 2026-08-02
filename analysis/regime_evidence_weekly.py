"""
Regime Evidence Weekly — Regime Gate 주간 Evidence 자동 평가 (읽기 전용)

작업지시서 "Regime Gate 실거래 Evidence 확보 계획" 작업6. 매주 금요일 실행.
Gate Precision/Recall + Evidence Level(E0~E3)을 자동 산출한다. 정책/threshold는
건드리지 않는다 — 판단 기준 문서는 reports/regime_validation_plan.md 참조.

정의:
    True Positive  = REGIME_BLOCK 시뮬레이션 결과 LOSS (차단이 맞았음)
    False Positive = REGIME_BLOCK 시뮬레이션 결과 WIN  (차단이 기회손실)
    True Negative  = PASS 후 실제 체결 결과 WIN (통과가 맞았음)
    False Negative = PASS 후 실제 체결 결과 LOSS (통과했는데 손실 — 게이트가 걸렀어야 했음)
    Gate Precision = TP / (TP+FP)   — "차단한 것 중 진짜 손실 비율"
    Gate Recall    = TP / (TP+FN)   — "손실이었던 것 중 게이트가 막은 비율" (PASS 표본 필요)

실행:
    python3 -m analysis.regime_evidence_weekly            # 최근 5거래일(1주)
    python3 -m analysis.regime_evidence_weekly --days 20
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

import psycopg2

from analysis.regime_block_simulator import DETAIL_CSV_PATH
from analysis.regime_evidence_common import classify_evidence_level

REPORT_PATH = BASE / 'reports' / 'regime_evidence_weekly.md'


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


def _detail_rows_in_window(start: date, end: date) -> List[Dict[str, str]]:
    if not DETAIL_CSV_PATH.exists():
        return []
    with open(DETAIL_CSV_PATH, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if start <= date.fromisoformat(r['trade_date']) <= end]


def _pass_outcomes_in_window(cur, start: date, end: date) -> Dict[str, int]:
    """PASS 후 실제 손익 부호 기준 TN/FN — research.decision_outcomes 뷰 재사용(존재 시)."""
    try:
        cur.execute("""
            SELECT return_5d FROM research.decision_outcomes
            WHERE decided_at::date BETWEEN %s AND %s AND decision='PASS' AND return_5d IS NOT NULL
        """, (str(start), str(end)))
        rows = cur.fetchall()
    except Exception:
        return {'tn': 0, 'fn': 0}
    tn = sum(1 for (r,) in rows if r is not None and r > 0)
    fn = sum(1 for (r,) in rows if r is not None and r <= 0)
    return {'tn': tn, 'fn': fn}


def _cumulative_counts(cur) -> Dict[str, int]:
    cur.execute("SELECT COUNT(*) FROM research.decision_ledger WHERE decision_reason_code='REGIME_BLOCKED'")
    regime_block_n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM research.decision_ledger WHERE decision='PASS'")
    realized_trade_n = cur.fetchone()[0]
    simulated_block_n = 0
    if DETAIL_CSV_PATH.exists():
        with open(DETAIL_CSV_PATH, newline='', encoding='utf-8') as f:
            simulated_block_n = sum(1 for _ in csv.DictReader(f))
    return {'regime_block_n': regime_block_n, 'realized_trade_n': realized_trade_n, 'simulated_block_n': simulated_block_n}


def build_weekly_report(days: int = 5) -> Dict[str, Any]:
    end = date.today()
    start = end - timedelta(days=days * 2)  # 주말 포함 여유

    rows = _detail_rows_in_window(start, end)
    tp = sum(1 for r in rows if r['exit_logic_result'] == 'LOSS')
    fp = sum(1 for r in rows if r['exit_logic_result'] == 'WIN')
    flat = sum(1 for r in rows if r['exit_logic_result'] == 'FLAT')

    conn = _get_conn()
    try:
        cur = conn.cursor()
        pass_outcomes = _pass_outcomes_in_window(cur, start, end)
        cum = _cumulative_counts(cur)
    finally:
        conn.close()

    tn, fn = pass_outcomes['tn'], pass_outcomes['fn']
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    # 🔧 PASS 실거래 표본(tn+fn)이 전혀 없으면 fn=0은 "진짜 0"이 아니라 "측정 불가" —
    # tp+fn>0만으로는 fn=0/PASS표본없음을 구분 못해 recall이 허위로 100%가 되는 것을 방지
    recall = tp / (tp + fn) if (tn + fn) > 0 else None

    opportunity_cost = sum(float(r['ret_1d']) for r in rows if r['exit_logic_result'] == 'WIN' and r.get('ret_1d')) \
        if rows else 0.0
    avoided_loss = sum(float(r['exit_logic_return_pct']) for r in rows if r['exit_logic_result'] == 'LOSS' and r.get('exit_logic_return_pct')) \
        if rows else 0.0

    evidence = classify_evidence_level(cum['realized_trade_n'], cum['regime_block_n'], cum['simulated_block_n'])

    return {
        'period_start': str(start), 'period_end': str(end),
        'sample_n': len(rows),
        'regime_block_n_window': len(rows),
        'tp': tp, 'fp': fp, 'flat': flat, 'tn': tn, 'fn': fn,
        'precision': precision, 'recall': recall,
        'opportunity_cost_pct': opportunity_cost, 'avoided_loss_pct': avoided_loss,
        'evidence': evidence, 'cumulative': cum,
    }


def render_markdown(r: Dict[str, Any]) -> str:
    prec_str = f"{r['precision']*100:.1f}%" if r['precision'] is not None else "N/A (FP+TP=0)"
    rec_str = f"{r['recall']*100:.1f}%" if r['recall'] is not None else "N/A (PASS 실거래 표본 없음)"
    ev = r['evidence']
    lines = [
        f"# Regime Evidence Weekly — {r['period_start']} ~ {r['period_end']}",
        "",
        "## 표본",
        "",
        f"- REGIME_BLOCK 시뮬레이션 표본(기간 내): {r['sample_n']}건",
        f"- 누적(전체 기간): 실거래 {r['cumulative']['realized_trade_n']}건 / "
        f"REGIME_BLOCK {r['cumulative']['regime_block_n']}건 / 시뮬레이션 {r['cumulative']['simulated_block_n']}건",
        "",
        "## Confusion Matrix (REGIME_BLOCK 사후 시뮬레이션 + PASS 실거래 결과)",
        "",
        "| | 실제 손실 | 실제 이익 |",
        "|---|---:|---:|",
        f"| Gate가 차단(BLOCK) | TP={r['tp']} | FP={r['fp']} |",
        f"| Gate가 통과(PASS) | FN={r['fn']} | TN={r['tn']} |",
        "",
        f"(FLAT 무승부 {r['flat']}건은 분모에서 제외)",
        "",
        f"- **Gate Precision**: {prec_str} — 차단한 것 중 진짜 손실이었던 비율",
        f"- **Gate Recall**: {rec_str} — 손실이었던 것 중 게이트가 막은 비율",
        "",
        "## Opportunity Loss / Avoided Loss (기간 합산)",
        "",
        f"- Opportunity Cost(놓친 수익 합, WIN이었을 것들의 1일 수익률 합): {r['opportunity_cost_pct']:+.2f}%",
        f"- Avoided Loss(막은 손실 합, LOSS였을 것들의 실현손익 합): {r['avoided_loss_pct']:+.2f}%",
        "",
        "## Evidence Level",
        "",
        f"**{ev['level']}** — {ev['reason']}",
        "",
        f"판정 기준: {ev['criteria']}",
        "",
        "상세 threshold 변경 조건은 `reports/regime_validation_plan.md` 참조.",
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Regime Evidence Weekly (읽기 전용)')
    parser.add_argument('--days', type=int, default=5, help='최근 N거래일 근사 윈도우 (기본 5 = 1주)')
    args = parser.parse_args()

    report = build_weekly_report(args.days)
    md = render_markdown(report)
    REPORT_PATH.write_text(md, encoding='utf-8')
    print(f"✅ {REPORT_PATH}")
    print(md)
