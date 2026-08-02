"""
E2 Trade Analytics — v1.4.x Strategy Freeze 기간 실거래 검증 리포트

docs/E2_STRATEGY_FREEZE.md 정책 하에서, Entry/Exit/Regime/Risk/SCORE_ENGINE/
Candidate Pipeline은 전혀 건드리지 않고 이미 기록된 trades/decision_ledger
데이터를 읽어서 KPI·레짐별 성과·Entry 품질·Exit 품질·Risk 분석을 계산한다.

읽기 전용. 매매 로직/DB 스키마에 관여하지 않는다. 새 테이블을 만들지 않는다
(trades, research.decision_ledger, research.candidates 기존 테이블만 조회).

실행:
    python3 -m analysis.e2_trade_analytics                    # 누적 전체
    python3 -m analysis.e2_trade_analytics --since 2026-07-01
    python3 -m analysis.e2_trade_analytics --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

import psycopg2
import psycopg2.extras


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _fetch_trades(since: Optional[str]) -> List[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.cursor()
    where = "WHERE exit_time IS NOT NULL" + (" AND exit_time::date >= %s" if since else "")
    params = (since,) if since else ()
    cur.execute(f"""
        SELECT trade_id, stock_code, trade_time, entry_time, exit_time,
               profit_rate, realized_profit, holding_minutes, holding_duration,
               mfe_pct, mae_pct, market_regime, exit_market_regime, exit_category,
               exit_subreason, position_size_mult, r_multiple, entry_context, exit_context,
               candidate_first_time, entry_signal_time
        FROM trades
        {where}
        ORDER BY exit_time
    """, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── 1. 거래별 기록 뷰 (요청된 필드 매핑) ───────────────────────────

def build_trade_records(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in trades:
        ec = t.get('entry_context') or {}
        out.append({
            'date':          str(t['exit_time'])[:10] if t['exit_time'] else None,
            'stock_code':    t['stock_code'],
            'regime':        t.get('market_regime') or ec.get('market_regime'),
            'entry_reason':  ec.get('pattern'),
            'exit_reason':   t.get('exit_subreason') or t.get('exit_category'),
            'holding_min':   t.get('holding_minutes') or t.get('holding_duration'),
            'pnl_pct':       t.get('profit_rate'),
            'pnl_amount':    t.get('realized_profit'),
            'position_size_mult': t.get('position_size_mult'),
            'score':         ec.get('score'),
            'choch_grade':   None,   # trades 테이블엔 미기록 — decision_ledger.feature_snapshot 참조 필요(아래 참고)
            'mfe_pct':       t.get('mfe_pct'),
            'mae_pct':       t.get('mae_pct'),
        })
    return out


# ─── 2. KPI ─────────────────────────────────────────────────────────

def compute_kpi(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {'trades': 0, 'note': '거래 없음'}

    pnls = [float(t['profit_rate']) for t in trades if t.get('profit_rate') is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

    # 최대 연속 손실
    max_consec_loss = cur_streak = 0
    for p in pnls:
        if p <= 0:
            cur_streak += 1
            max_consec_loss = max(max_consec_loss, cur_streak)
        else:
            cur_streak = 0

    # 최대 Drawdown (누적 pnl_pct 기준 근사치 — 실제 자산곡선은 equity_controller가 별도 관리)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    holdings = [t['holding_minutes'] for t in trades if t.get('holding_minutes')]
    avg_hold = sum(holdings) / len(holdings) if holdings else None

    return {
        'trades':            n,
        'win_rate_pct':       round(win_rate, 1),
        'pf':                round(pf, 2) if pf != float('inf') else 'inf',
        'expectancy_pct':     round(expectancy, 3),
        'avg_win_pct':        round(avg_win, 2),
        'avg_loss_pct':       round(avg_loss, 2),
        'reward_risk_ratio':  round(rr, 2) if rr != float('inf') else 'inf',
        'avg_holding_min':    round(avg_hold, 1) if avg_hold is not None else None,
        'max_consecutive_losses': max_consec_loss,
        'max_drawdown_pct_approx': round(max_dd, 2),
    }


# ─── 3. Regime별 성과 ───────────────────────────────────────────────

def compute_regime_performance(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    unknown = []
    for t in trades:
        ec = t.get('entry_context') or {}
        regime = t.get('market_regime') or ec.get('market_regime')
        if not regime:
            unknown.append(t)
            continue
        buckets.setdefault(regime, []).append(t)

    result = {}
    for regime, ts in buckets.items():
        result[regime] = compute_kpi(ts)
    if unknown:
        result['UNKNOWN(미기록)'] = {'trades': len(unknown), 'note': 'market_regime 필드 미기록 거래 — 집계 제외'}
    return result


# ─── 4. Entry 품질 분석 ─────────────────────────────────────────────

def compute_entry_quality(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    delays = []
    for t in trades:
        cft, est = t.get('candidate_first_time'), t.get('entry_signal_time')
        if cft and est:
            delays.append((est - cft).total_seconds() / 60)

    if not delays:
        return {
            'note': (
                'candidate_first_time/entry_signal_time 필드가 trades 테이블에 기록된 거래가 0건 — '
                'Entry Delay/Breakout Distance/ATR Ratio 계산 불가. '
                '이 필드들은 Entry 로직(execute_buy)에서 채워야 하는데, 현재 Strategy Freeze 대상이라 '
                '임의로 수정하지 않음 — Bug Fix(A) 분류로 볼지 사용자 판단 필요.'
            ),
            'avg_entry_delay_min': None,
        }
    return {'avg_entry_delay_min': round(sum(delays) / len(delays), 1), 'sample': len(delays)}


# ─── 5. Exit 품질 분석 ──────────────────────────────────────────────

def compute_exit_quality(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    mfes = [float(t['mfe_pct']) for t in trades if t.get('mfe_pct') is not None]
    maes = [float(t['mae_pct']) for t in trades if t.get('mae_pct') is not None]
    pnls = [float(t['profit_rate']) for t in trades if t.get('profit_rate') is not None]

    reasons = [t.get('exit_subreason') or t.get('exit_category') or 'UNKNOWN' for t in trades]
    n = len(trades) or 1
    reason_dist = {}
    for r in reasons:
        reason_dist[r] = reason_dist.get(r, 0) + 1

    trailing_n = reason_dist.get('TRAILING_STOP', 0)
    time_exit_n = reason_dist.get('TIME_EXIT', 0)

    give_back = None
    if mfes and pnls and len(mfes) == len(pnls):
        gb = [m - p for m, p in zip(mfes, pnls) if m > 0]
        give_back = round(sum(gb) / len(gb), 2) if gb else None

    return {
        'avg_mfe_pct':          round(sum(mfes) / len(mfes), 2) if mfes else None,
        'avg_mae_pct':          round(sum(maes) / len(maes), 2) if maes else None,
        'avg_realized_pnl_pct': round(sum(pnls) / len(pnls), 2) if pnls else None,
        'avg_profit_giveback_pct': give_back,
        'trailing_stop_rate_pct':  round(trailing_n / n * 100, 1),
        'time_exit_rate_pct':      round(time_exit_n / n * 100, 1),
        'exit_reason_distribution': reason_dist,
    }


# ─── 6. Risk 분석 ───────────────────────────────────────────────────

def compute_risk_analysis(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    sizes = [float(t['position_size_mult']) for t in trades if t.get('position_size_mult') is not None]
    if not sizes:
        return {'note': 'position_size_mult 기록된 거래 0건 — Risk 분석(사이징 vs 레짐/DD) 불가'}

    by_regime: Dict[str, List[float]] = {}
    for t in trades:
        if t.get('position_size_mult') is None:
            continue
        ec = t.get('entry_context') or {}
        regime = t.get('market_regime') or ec.get('market_regime') or 'UNKNOWN'
        by_regime.setdefault(regime, []).append(float(t['position_size_mult']))

    return {
        'avg_position_size_mult': round(sum(sizes) / len(sizes), 3),
        'by_regime': {k: round(sum(v) / len(v), 3) for k, v in by_regime.items()},
        'sample': len(sizes),
    }


# ─── 7. RISK_OFF 차단/후보 현황 (research 스키마, 별도 조회) ────────

def compute_risk_off_blocking(since: Optional[str]) -> Dict[str, Any]:
    conn = _get_conn()
    cur = conn.cursor()
    where = "WHERE 1=1" + (" AND decided_at::date >= %s" if since else "")
    params = (since,) if since else ()
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM research.decision_ledger
        {where} AND decision_reason_code IN ('REGIME_BLOCKED','OTHER')
    """, params)
    blocked = cur.fetchone()['n']
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM research.candidates
        {where.replace('decided_at', 'observed_at')}
    """, params)
    candidates = cur.fetchone()['n']
    conn.close()
    return {'regime_blocked': blocked, 'candidates': candidates, 'note': 'RISK_OFF 원칙상 신규진입 없음 — 차단/후보 건수만 참고'}


# ─── 종합 ───────────────────────────────────────────────────────────

def run_report(since: Optional[str]) -> Dict[str, Any]:
    trades = _fetch_trades(since)
    return {
        'generated_at':      datetime.now().isoformat(timespec='seconds'),
        'since':             since or '(전체)',
        'e2_target_progress': {
            'sample': len(trades),
            'stage_30':  'PASS' if len(trades) >= 30 else f'{30 - len(trades)}건 부족',
            'stage_50':  'PASS' if len(trades) >= 50 else f'{50 - len(trades)}건 부족',
            'stage_100': 'PASS' if len(trades) >= 100 else f'{100 - len(trades)}건 부족',
        },
        'kpi':               compute_kpi(trades),
        'regime_performance': compute_regime_performance(trades),
        'entry_quality':     compute_entry_quality(trades),
        'exit_quality':      compute_exit_quality(trades),
        'risk_analysis':     compute_risk_analysis(trades),
        'risk_off_blocking': compute_risk_off_blocking(since),
    }


def print_report(r: Dict[str, Any]) -> None:
    print('=' * 55)
    print('E2 TRADE ANALYTICS')
    print(f"since={r['since']}  generated_at={r['generated_at']}")
    print('=' * 55)

    print('\n[E2 표본 진행률]')
    p = r['e2_target_progress']
    print(f"  누적 표본: {p['sample']}건")
    print(f"  1차(30건): {p['stage_30']}")
    print(f"  중간(50건): {p['stage_50']}")
    print(f"  최종(100건): {p['stage_100']}")

    print('\n[KPI]')
    for k, v in r['kpi'].items():
        print(f'  {k}: {v}')

    print('\n[Regime별 성과]')
    for regime, kpi in r['regime_performance'].items():
        print(f'  --- {regime} ---')
        for k, v in kpi.items():
            print(f'    {k}: {v}')

    print('\n[Entry 품질]')
    for k, v in r['entry_quality'].items():
        print(f'  {k}: {v}')

    print('\n[Exit 품질]')
    for k, v in r['exit_quality'].items():
        print(f'  {k}: {v}')

    print('\n[Risk 분석]')
    for k, v in r['risk_analysis'].items():
        print(f'  {k}: {v}')

    print('\n[RISK_OFF 차단 현황]')
    for k, v in r['risk_off_blocking'].items():
        print(f'  {k}: {v}')
    print('=' * 55)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='E2 Trade Analytics (Read-Only)')
    parser.add_argument('--since', default=None, help='YYYY-MM-DD 이후만 (기본: 전체 누적)')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    report = run_report(args.since)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)
