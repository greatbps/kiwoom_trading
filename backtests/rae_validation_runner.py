"""
RAE Validation Runner — v1.3 Finalization Patch

목적: S1(Baseline) / S2(Current v1.3) / S3(Conservative v1.3) 3개 시나리오를
      기존 거래 DB 데이터로 비교하여 RAE 활성화 기준을 판단한다.

실행:
    python3 -m backtests.rae_validation_runner [--days 30] [--output reports/]
    python3 -m backtests.rae_validation_runner --help

출력:
    reports/rae_validation_summary_YYYYMMDD.json
    reports/rae_validation_summary_YYYYMMDD.md
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

# ── DB ────────────────────────────────────────────────────────────────────────
try:
    import psycopg2
    import psycopg2.extras
    _HAS_DB = True
except ImportError:
    _HAS_DB = False

DB_PARAMS = dict(dbname='trading_system', user='postgres', password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로딩
# ─────────────────────────────────────────────────────────────────────────────

def load_trades(days: int = 60) -> List[Dict]:
    """DB에서 최근 N일 완결 거래(BUY+SELL 모두 있는)를 불러온다."""
    if not _HAS_DB:
        raise RuntimeError("psycopg2 미설치 — pip install psycopg2-binary")

    since = datetime.now() - timedelta(days=days)
    sql = """
        SELECT
            trade_id, stock_code, stock_name,
            trade_type, trade_time, price, quantity, amount,
            entry_reason, exit_reason,
            realized_profit, profit_rate, holding_minutes,
            mfe_pct, mae_pct, r_multiple,
            strategy_name, entry_type,
            exit_category, exit_subreason,
            entry_features, filter_scores, entry_context,
            market_regime, position_size_mult,
            entry_time, exit_time, candidate_first_time
        FROM trades
        WHERE trade_time >= %s
        ORDER BY trade_time ASC
    """
    with psycopg2.connect(**DB_PARAMS) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (since,))
            rows = cur.fetchall()

    return [dict(r) for r in rows]


def pair_trades(rows: List[Dict]) -> List[Dict]:
    """
    BUY / SELL을 stock_code 기준으로 매칭하여 완결 거래 목록을 반환.
    SELL 레코드에 realized_profit이 있으면 그것을 사용.
    """
    buys: Dict[str, Dict] = {}
    pairs: List[Dict] = []

    for r in rows:
        code = r.get('stock_code', '')
        tt   = r.get('trade_type', '').upper()

        if tt == 'BUY' or r.get('entry_type') in ('SMC_OB', 'SMC_RAE', 'SMC_PRIMARY', 'SMC'):
            buys[code] = r
        elif tt == 'SELL' and code in buys:
            buy = buys.pop(code)
            rp = float(r.get('realized_profit') or 0)
            pr = float(r.get('profit_rate') or 0)
            pairs.append({
                'stock_code':     code,
                'stock_name':     buy.get('stock_name', ''),
                'buy_price':      float(buy.get('price') or 0),
                'sell_price':     float(r.get('price') or 0),
                'realized_profit': rp,
                'profit_rate':    pr,
                'holding_minutes': int(r.get('holding_minutes') or buy.get('holding_minutes') or 0),
                'exit_reason':    r.get('exit_reason') or r.get('exit_category') or '',
                'exit_category':  r.get('exit_category') or '',
                'entry_reason':   buy.get('entry_reason') or '',
                'strategy_name':  buy.get('strategy_name') or buy.get('entry_type') or '',
                'entry_features': buy.get('entry_features') or {},
                'mfe_pct':        float(r.get('mfe_pct') or buy.get('mfe_pct') or 0),
                'mae_pct':        float(r.get('mae_pct') or buy.get('mae_pct') or 0),
                'r_multiple':     float(r.get('r_multiple') or buy.get('r_multiple') or 0),
                'market_regime':  buy.get('market_regime') or '',
                'entry_time':     buy.get('trade_time') or buy.get('entry_time'),
                'exit_time':      r.get('trade_time') or r.get('exit_time'),
                'position_size_mult': float(buy.get('position_size_mult') or 1.0),
            })

    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# 시나리오 필터링
# ─────────────────────────────────────────────────────────────────────────────

def _entry_route(t: Dict) -> str:
    """거래의 entry_route 결정 (SMC_RAE vs PRIMARY)"""
    ef = t.get('entry_features') or {}
    if isinstance(ef, str):
        try: ef = json.loads(ef)
        except Exception: ef = {}
    route = ef.get('entry_route', '')
    if route in ('RAE', 'SMC_RAE'):
        return 'RAE'
    strat = (t.get('strategy_name') or '').upper()
    if 'RAE' in strat:
        return 'RAE'
    return 'PRIMARY'


def _entry_time_bucket(t: Dict) -> str:
    et = t.get('entry_time')
    if et is None:
        return 'UNKNOWN'
    if isinstance(et, str):
        try: et = datetime.fromisoformat(et)
        except Exception: return 'UNKNOWN'
    h, m = et.hour, et.minute
    if h < 10:
        return 'OPEN'
    if h == 10 and m <= 30:
        return 'OPEN_LATE'
    if h < 13:
        return 'MID'
    return 'LATE'


def scenario_s1_baseline(trades: List[Dict]) -> List[Dict]:
    """S1: Primary only (RAE 제외, G3 hard block 기준)"""
    return [t for t in trades if _entry_route(t) == 'PRIMARY']


def scenario_s2_current(trades: List[Dict]) -> List[Dict]:
    """S2: Current v1.3 (Primary + RAE + TED + G3 soft penalty 모두 포함)"""
    return trades  # 전체


def scenario_s3_conservative(trades: List[Dict]) -> List[Dict]:
    """
    S3: Conservative v1.3 — RAE 포함하되 엄격 필터 적용
    (RAE score < conservative_min_score 는 제외, RAE size 0.5 재계산)
    """
    result = []
    conservative_min_score = 8  # 기본 7 → 8로 상향
    for t in trades:
        if _entry_route(t) == 'RAE':
            ef = t.get('entry_features') or {}
            if isinstance(ef, str):
                try: ef = json.loads(ef)
                except Exception: ef = {}
            rae_score = ef.get('rae_score', 7)
            if rae_score < conservative_min_score:
                continue
            # size를 0.5로 재계산 (시뮬레이션)
            t2 = dict(t)
            scale = 0.5 / max(t.get('position_size_mult', 0.7), 0.01)
            t2['realized_profit'] = t.get('realized_profit', 0) * scale
            t2['_conservative_scaled'] = True
            result.append(t2)
        else:
            result.append(t)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 성과 집계
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(trades: List[Dict], label: str = '') -> Dict:
    """거래 목록에서 성과 지표를 집계한다."""
    if not trades:
        return {'label': label, 'trade_count': 0, 'note': 'no_data'}

    profits = [float(t.get('realized_profit') or 0) for t in trades]
    pct_list = [float(t.get('profit_rate') or 0) for t in trades]
    r_list   = [float(t.get('r_multiple') or 0) for t in trades]

    wins  = [p for p in profits if p > 0]
    losses = [p for p in profits if p <= 0]
    win_count = len(wins)
    loss_count = len(losses)

    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float('inf') if wins else 0
    win_rate = win_count / len(trades) if trades else 0

    # MDD (누적 최대 낙폭)
    cumulative = []
    running = 0
    for p in profits:
        running += p
        cumulative.append(running)
    peak = 0
    mdd = 0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = (peak - c)
        if dd > mdd:
            mdd = dd

    # avg R / median R
    avg_r = sum(r_list) / len(r_list) if r_list else 0
    sorted_r = sorted(r_list)
    n = len(sorted_r)
    median_r = sorted_r[n//2] if n else 0

    # 최대 연속 손실
    max_consec_loss = 0
    curr_loss = 0
    for p in profits:
        if p <= 0:
            curr_loss += 1
            max_consec_loss = max(max_consec_loss, curr_loss)
        else:
            curr_loss = 0

    # entry route 분리
    primary_trades = [t for t in trades if _entry_route(t) == 'PRIMARY']
    rae_trades     = [t for t in trades if _entry_route(t) == 'RAE']

    # exit reason 분리
    exit_breakdown: Dict[str, Dict] = defaultdict(lambda: {'count': 0, 'profit': 0.0, 'wins': 0})
    for t in trades:
        ex = (t.get('exit_category') or t.get('exit_reason') or 'unknown').lower()
        ex = ex.replace(' ', '_')[:40]
        exit_breakdown[ex]['count'] += 1
        exit_breakdown[ex]['profit'] += float(t.get('realized_profit') or 0)
        if float(t.get('realized_profit') or 0) > 0:
            exit_breakdown[ex]['wins'] += 1

    # time bucket 분리
    bucket_breakdown: Dict[str, Dict] = defaultdict(lambda: {'count': 0, 'profit': 0.0, 'wins': 0})
    for t in trades:
        b = _entry_time_bucket(t)
        bucket_breakdown[b]['count'] += 1
        bucket_breakdown[b]['profit'] += float(t.get('realized_profit') or 0)
        if float(t.get('realized_profit') or 0) > 0:
            bucket_breakdown[b]['wins'] += 1

    return {
        'label':            label,
        'trade_count':      len(trades),
        'win_count':        win_count,
        'loss_count':       loss_count,
        'win_rate':         round(win_rate, 4),
        'profit_factor':    round(pf, 4) if pf != float('inf') else 999.0,
        'total_profit':     round(sum(profits), 0),
        'avg_profit':       round(sum(pct_list) / len(pct_list), 4) if pct_list else 0,
        'avg_r':            round(avg_r, 4),
        'median_r':         round(median_r, 4),
        'mdd':              round(mdd, 0),
        'max_consec_loss':  max_consec_loss,
        'primary': _route_summary(primary_trades, 'PRIMARY'),
        'rae':     _route_summary(rae_trades, 'RAE'),
        'exit_breakdown':  dict(exit_breakdown),
        'time_bucket':     dict(bucket_breakdown),
    }


def _route_summary(trades: List[Dict], label: str) -> Dict:
    if not trades:
        return {'label': label, 'count': 0}
    profits = [float(t.get('realized_profit') or 0) for t in trades]
    pct_list = [float(t.get('profit_rate') or 0) for t in trades]
    r_list   = [float(t.get('r_multiple') or 0) for t in trades]
    wins  = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 999.0
    wr = len(wins) / len(profits) if profits else 0
    avg_r = sum(r_list) / len(r_list) if r_list else 0
    avg_hold = sum(t.get('holding_minutes', 0) for t in trades) / len(trades)
    avg_mfe = sum(t.get('mfe_pct', 0) for t in trades) / len(trades)
    avg_mae = sum(t.get('mae_pct', 0) for t in trades) / len(trades)

    # LCL/EF 비율
    lcl_ef = sum(1 for t in trades if any(
        x in (t.get('exit_category') or t.get('exit_reason') or '').lower()
        for x in ('lcl', 'early_cut', 'early_failure', 'ef_')
    ))
    early_exit_pct = lcl_ef / len(trades) if trades else 0

    # 10분 내 종료 비율
    under10m = sum(1 for t in trades if int(t.get('holding_minutes') or 99) < 10)
    under10m_pct = under10m / len(trades) if trades else 0

    return {
        'label':          label,
        'count':          len(trades),
        'win_rate':       round(wr, 4),
        'profit_factor':  round(pf, 4),
        'avg_r':          round(avg_r, 4),
        'avg_hold_min':   round(avg_hold, 1),
        'avg_mfe_pct':    round(avg_mfe, 4),
        'avg_mae_pct':    round(avg_mae, 4),
        'lcl_ef_rate':    round(early_exit_pct, 4),
        'under10m_rate':  round(under10m_pct, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# RAE 활성화 기준 판정
# ─────────────────────────────────────────────────────────────────────────────

RAE_GUARD_DEFAULTS = {
    'min_total_rae_trades': 20,
    'min_win_rate':         0.35,
    'min_pf':               1.10,
    'min_avg_r':            0.25,
    'max_mdd_degradation_pct': 15.0,
}


def check_rae_readiness(s1: Dict, s2: Dict, guard: Optional[Dict] = None) -> Dict:
    """
    S1(baseline)과 S2(current)를 비교하여 RAE 활성화 기준 충족 여부 판정.

    Returns:
        {
            'verdict':  'RAE_GO_LIVE_READY' | 'RAE_SHADOW_ONLY',
            'checks':   {criterion: passed},
            'reason':   str,
        }
    """
    g = {**RAE_GUARD_DEFAULTS, **(guard or {})}
    rae_m = s2.get('rae', {})
    checks = {}
    reasons_fail = []

    # 1. RAE 거래 수 ≥ min_total_rae_trades
    rae_count = rae_m.get('count', 0)
    checks['rae_count_ok'] = rae_count >= g['min_total_rae_trades']
    if not checks['rae_count_ok']:
        reasons_fail.append(f"RAE 거래수 {rae_count} < {g['min_total_rae_trades']}")

    # 2. RAE 단독 win_rate ≥ min_win_rate
    rae_wr = rae_m.get('win_rate', 0)
    checks['rae_wr_ok'] = rae_wr >= g['min_win_rate']
    if not checks['rae_wr_ok']:
        reasons_fail.append(f"RAE 승률 {rae_wr:.1%} < {g['min_win_rate']:.1%}")

    # 3. RAE 단독 PF ≥ min_pf
    rae_pf = rae_m.get('profit_factor', 0)
    checks['rae_pf_ok'] = rae_pf >= g['min_pf']
    if not checks['rae_pf_ok']:
        reasons_fail.append(f"RAE PF {rae_pf:.2f} < {g['min_pf']}")

    # 4. RAE avg R ≥ min_avg_r
    rae_avg_r = rae_m.get('avg_r', 0)
    checks['rae_avg_r_ok'] = rae_avg_r >= g['min_avg_r']
    if not checks['rae_avg_r_ok']:
        reasons_fail.append(f"RAE avg R {rae_avg_r:.2f} < {g['min_avg_r']}")

    # 5. 전체 PF가 baseline 이상 (S2 ≥ S1)
    s2_pf = s2.get('profit_factor', 0)
    s1_pf = s1.get('profit_factor', 0)
    checks['pf_not_degraded'] = s2_pf >= s1_pf * 0.95  # 5% 이내 허용
    if not checks['pf_not_degraded']:
        reasons_fail.append(f"전체 PF 악화: {s2_pf:.2f} < baseline {s1_pf:.2f}×0.95")

    # 6. MDD 악화 ≤ max_mdd_degradation_pct%
    s2_mdd = s2.get('mdd', 0)
    s1_mdd = s1.get('mdd', 0)
    if s1_mdd > 0:
        mdd_change_pct = (s2_mdd - s1_mdd) / s1_mdd * 100
    else:
        mdd_change_pct = 0
    checks['mdd_ok'] = mdd_change_pct <= g['max_mdd_degradation_pct']
    if not checks['mdd_ok']:
        reasons_fail.append(f"MDD 악화 {mdd_change_pct:.1f}% > {g['max_mdd_degradation_pct']}%")

    # 7. RAE LCL/EF 비율이 과도하지 않음 (< 60%)
    rae_lcl_ef = rae_m.get('lcl_ef_rate', 0)
    checks['rae_lcl_ef_ok'] = rae_lcl_ef < 0.60
    if not checks['rae_lcl_ef_ok']:
        reasons_fail.append(f"RAE LCL/EF 비율 {rae_lcl_ef:.1%} ≥ 60% — 손절 구조 검토 필요")

    all_pass = all(checks.values())
    verdict = 'RAE_GO_LIVE_READY' if all_pass else 'RAE_SHADOW_ONLY'
    reason = '모든 기준 충족' if all_pass else ' | '.join(reasons_fail)

    return {
        'verdict':  verdict,
        'checks':   checks,
        'reason':   reason,
        'rae_count': rae_count,
        'rae_wr':    round(rae_wr, 4),
        'rae_pf':    round(rae_pf, 4),
        'rae_avg_r': round(rae_avg_r, 4),
        's1_pf':     round(s1_pf, 4),
        's2_pf':     round(s2_pf, 4),
        'mdd_change_pct': round(mdd_change_pct, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 리포트 생성
# ─────────────────────────────────────────────────────────────────────────────

def build_report(s1_m: Dict, s2_m: Dict, s3_m: Dict, readiness: Dict, days: int) -> str:
    """Markdown 리포트 생성"""
    today = date.today().strftime('%Y-%m-%d')
    verdict = readiness['verdict']
    verdict_emoji = '🟢' if verdict == 'RAE_GO_LIVE_READY' else '🔴'

    lines = [
        f"# RAE Validation Summary — {today}",
        f"> 분석 기간: 최근 {days}일  |  ruleset: v1.3.1-final",
        "",
        "---",
        "",
        "## A. 요약",
        "",
        f"| 지표 | S1 Baseline | S2 Current v1.3 | S3 Conservative |",
        f"|------|-------------|-----------------|-----------------|",
        _row('거래수',   s1_m, s2_m, s3_m, 'trade_count', fmt='d'),
        _row('승률',     s1_m, s2_m, s3_m, 'win_rate',    fmt='pct'),
        _row('PF',       s1_m, s2_m, s3_m, 'profit_factor'),
        _row('avg R',    s1_m, s2_m, s3_m, 'avg_r'),
        _row('median R', s1_m, s2_m, s3_m, 'median_r'),
        _row('MDD',      s1_m, s2_m, s3_m, 'mdd', fmt='d'),
        _row('최대연속손실', s1_m, s2_m, s3_m, 'max_consec_loss', fmt='d'),
        "",
        "---",
        "",
        "## B. Entry Route별 성과",
        "",
        "### S2 (Current v1.3)",
        _route_table(s2_m),
        "",
        "### S3 (Conservative)",
        _route_table(s3_m),
        "",
        "---",
        "",
        "## C. Exit Reason Breakdown (S2)",
        "",
        _exit_table(s2_m),
        "",
        "---",
        "",
        "## D. Time Bucket별 성과 (S2)",
        "",
        _bucket_table(s2_m),
        "",
        "---",
        "",
        "## E. 승인 판정",
        "",
        f"### {verdict_emoji} 판정: `{verdict}`",
        "",
        f"- **RAE 거래수**: {readiness['rae_count']}건",
        f"- **RAE 승률**: {readiness['rae_wr']:.1%}",
        f"- **RAE PF**: {readiness['rae_pf']:.2f}",
        f"- **RAE avg R**: {readiness['rae_avg_r']:.3f}",
        f"- **전체 PF 변화**: {readiness['s1_pf']:.2f} → {readiness['s2_pf']:.2f}",
        f"- **MDD 변화**: {readiness['mdd_change_pct']:+.1f}%",
        "",
        "**체크 결과:**",
        "",
    ]
    for k, v in readiness['checks'].items():
        icon = '✅' if v else '❌'
        lines.append(f"- {icon} `{k}`")
    lines.append("")
    if readiness['reason'] != '모든 기준 충족':
        lines.append(f"**미충족 사유:** {readiness['reason']}")
    else:
        lines.append("모든 기준을 충족했습니다. 운영자 승인 후 `rae.enabled: true`로 전환 가능합니다.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return '\n'.join(lines)


def _row(label, s1, s2, s3, key, fmt='f'):
    v1 = s1.get(key, 0)
    v2 = s2.get(key, 0)
    v3 = s3.get(key, 0)
    def f(v):
        if fmt == 'd': return str(int(v))
        if fmt == 'pct': return f'{float(v):.1%}'
        return f'{float(v):.3f}'
    return f'| {label} | {f(v1)} | {f(v2)} | {f(v3)} |'


def _route_table(m: Dict) -> str:
    headers = ['Route', '거래수', '승률', 'avg R', 'PF', 'avg hold(m)', 'avg MFE%', 'LCL/EF율']
    lines = ['| ' + ' | '.join(headers) + ' |',
             '|' + '---|' * len(headers)]
    for route_key in ('primary', 'rae'):
        rm = m.get(route_key, {})
        if not rm.get('count'):
            continue
        lines.append(
            f"| {rm.get('label','?')} | {rm.get('count',0)} "
            f"| {rm.get('win_rate',0):.1%} "
            f"| {rm.get('avg_r',0):.3f} "
            f"| {rm.get('profit_factor',0):.2f} "
            f"| {rm.get('avg_hold_min',0):.0f} "
            f"| {rm.get('avg_mfe_pct',0):.2%} "
            f"| {rm.get('lcl_ef_rate',0):.1%} |"
        )
    return '\n'.join(lines)


def _exit_table(m: Dict) -> str:
    eb = m.get('exit_breakdown', {})
    if not eb:
        return '*(데이터 없음)*'
    headers = ['Exit Reason', '건수', '총손익', '승수']
    lines = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    for reason, d in sorted(eb.items(), key=lambda x: -x[1]['count']):
        wins = d.get('wins', 0)
        cnt  = d.get('count', 1)
        lines.append(f"| {reason} | {cnt} | {d.get('profit',0):,.0f} | {wins}/{cnt} |")
    return '\n'.join(lines)


def _bucket_table(m: Dict) -> str:
    bb = m.get('time_bucket', {})
    if not bb:
        return '*(데이터 없음)*'
    headers = ['시간대', '건수', '총손익', '승수']
    lines = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    order = ['OPEN', 'OPEN_LATE', 'MID', 'LATE', 'UNKNOWN']
    for b in order:
        d = bb.get(b)
        if not d or not d.get('count'):
            continue
        lines.append(f"| {b} | {d['count']} | {d.get('profit',0):,.0f} | {d.get('wins',0)}/{d['count']} |")
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────

def run(days: int = 60, output_dir: str = 'reports', guard_overrides: Optional[Dict] = None):
    print(f"[RAE_VALIDATION] 최근 {days}일 거래 로드 중...")
    rows = load_trades(days)
    trades = pair_trades(rows)
    print(f"[RAE_VALIDATION] 완결 거래 {len(trades)}건 로드 완료")

    if not trades:
        print("[RAE_VALIDATION] ⚠️  데이터 없음 — 실거래/리플레이 데이터 필요")
        # 빈 데이터로도 리포트 생성
        empty = compute_metrics([], 'EMPTY')
        result = {
            'generated_at': datetime.now().isoformat(),
            'days_analyzed': days,
            'total_raw_rows': len(rows),
            'total_trades': 0,
            'note': '데이터 부족 — 실거래 데이터 축적 후 재실행',
            's1': empty, 's2': empty, 's3': empty,
            'readiness': {
                'verdict': 'RAE_SHADOW_ONLY',
                'checks': {},
                'reason': '거래 데이터 없음',
                'rae_count': 0, 'rae_wr': 0, 'rae_pf': 0,
                'rae_avg_r': 0, 's1_pf': 0, 's2_pf': 0, 'mdd_change_pct': 0,
            }
        }
    else:
        s1_t = scenario_s1_baseline(trades)
        s2_t = scenario_s2_current(trades)
        s3_t = scenario_s3_conservative(trades)

        s1_m = compute_metrics(s1_t, 'S1_Baseline')
        s2_m = compute_metrics(s2_t, 'S2_Current_v1.3')
        s3_m = compute_metrics(s3_t, 'S3_Conservative')

        readiness = check_rae_readiness(s1_m, s2_m, guard_overrides)

        result = {
            'generated_at': datetime.now().isoformat(),
            'days_analyzed': days,
            'total_raw_rows': len(rows),
            'total_trades': len(trades),
            's1': s1_m,
            's2': s2_m,
            's3': s3_m,
            'readiness': readiness,
        }

        md = build_report(s1_m, s2_m, s3_m, readiness, days)
        today_str = date.today().strftime('%Y%m%d')
        os.makedirs(output_dir, exist_ok=True)
        md_path   = os.path.join(output_dir, f'rae_validation_summary_{today_str}.md')
        json_path = os.path.join(output_dir, f'rae_validation_summary_{today_str}.json')

        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        print(f"[RAE_VALIDATION] 판정: {readiness['verdict']}")
        print(f"[RAE_VALIDATION] 리포트: {md_path}")
        print(f"[RAE_VALIDATION] JSON:   {json_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description='RAE Validation Runner v1.3')
    parser.add_argument('--days', type=int, default=60, help='분석 기간 (기본 60일)')
    parser.add_argument('--output', default='reports', help='리포트 출력 디렉토리')
    args = parser.parse_args()
    result = run(days=args.days, output_dir=args.output)
    verdict = result.get('readiness', {}).get('verdict', 'UNKNOWN')
    print(f"\n최종 판정: {verdict}")
    if verdict == 'RAE_GO_LIVE_READY':
        print("  → rae.enabled: true 전환 가능 (수동 승인 필요)")
    else:
        print("  → rae.enabled: false 유지 — 추가 데이터 축적 필요")


if __name__ == '__main__':
    main()
