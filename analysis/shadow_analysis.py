"""
Shadow Mode 분석 — ML 필터 threshold 결정 도구.

3가지 핵심 분석:
  1. Calibration   — prob 구간별 실제 승률 (모델 신뢰도 검증)
  2. Shadow Split  — PASS vs BLOCK 그룹 성과 비교 (필터 가치)
  3. False Negative— 막았지만 실제 수익이 좋았던 거래 (기회 손실)
  4. Threshold Sweep — 0.25~0.70 구간 최적 threshold 탐색

사용법:
    python -m analysis.shadow_analysis [--days 30] [--min-decisions 10]
    python -m analysis.shadow_analysis --model-version 20260426

자동 호출: auto_retrain.py 에서 일일 리포트 생성 후 호출 가능
"""

import os
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_PG_DSN = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "trading_system"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '8252382230:AAEPiPmgvoe73_Z1matB7GTNvqhyNKTPpGM')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID',   '19196452')

THRESHOLDS_TO_SWEEP = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
FALSE_NEG_CUTOFF    = 1.5   # later_outcome > 이 값 → False Negative


def _get_conn():
    import psycopg2
    return psycopg2.connect(**_PG_DSN)


def load_decisions(days: int = 90, model_version: str = None) -> list[dict]:
    """
    ml_decisions 로드 (later_outcome 있는 것만).
    """
    since = (datetime.now() - timedelta(days=days)).isoformat()
    sql = """
        SELECT prob, threshold, shadow_mode, blocked,
               entry_type, rvol, vwap_distance, volume_trend,
               later_outcome, decision_time
        FROM ml_decisions
        WHERE later_outcome IS NOT NULL
          AND decision_time >= %(since)s
    """
    params: dict = {'since': since}
    if model_version:
        sql += " AND model_version = %(mv)s"
        params['mv'] = model_version

    import psycopg2.extras
    conn = _get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ─────────────────────────────────────────────────────────────
# 1. Calibration
# ─────────────────────────────────────────────────────────────

def calibration_analysis(rows: list[dict], bins=None) -> list[dict]:
    """
    prob 구간별 실제 승률 비교.
    잘 보정된 모델: avg_prob ≈ actual_win_rate (대각선에 가까울수록 좋음)
    """
    if bins is None:
        bins = [0.0, 0.20, 0.35, 0.50, 0.65, 0.80, 1.0]

    result = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        bucket = [r for r in rows if r['prob'] is not None and lo <= r['prob'] < hi]
        if not bucket:
            continue
        probs    = [r['prob'] for r in bucket]
        outcomes = [r['later_outcome'] for r in bucket]
        wins     = [1 if o > 0 else 0 for o in outcomes]
        avg_pnl  = float(np.mean(outcomes))
        result.append({
            'bin':          f"{lo:.2f}~{hi:.2f}",
            'n':            len(bucket),
            'avg_prob':     round(float(np.mean(probs)), 3),
            'actual_wr':    round(float(np.mean(wins)), 3),
            'calib_error':  round(abs(float(np.mean(probs)) - float(np.mean(wins))), 3),
            'avg_pnl':      round(avg_pnl, 3),
        })
    return result


# ─────────────────────────────────────────────────────────────
# 2. Shadow Split
# ─────────────────────────────────────────────────────────────

def shadow_split_analysis(rows: list[dict]) -> dict:
    """
    PASS vs SHADOW_BLOCK 그룹 성과 비교.
    SHADOW_BLOCK avg_pnl이 낮을수록 필터가 가치 있음.
    """
    pass_g  = [r for r in rows if not r['blocked']]
    block_g = [r for r in rows if r['blocked']]

    def _stats(group):
        if not group:
            return {'n': 0, 'win_rate': None, 'avg_pnl': None, 'avg_prob': None}
        outcomes = [r['later_outcome'] for r in group]
        probs    = [r['prob'] for r in group if r['prob'] is not None]
        return {
            'n':        len(group),
            'win_rate': round(float(np.mean([1 if o > 0 else 0 for o in outcomes])), 3),
            'avg_pnl':  round(float(np.mean(outcomes)), 3),
            'avg_prob': round(float(np.mean(probs)), 3) if probs else None,
        }

    pass_stats  = _stats(pass_g)
    block_stats = _stats(block_g)

    # 필터 가치: BLOCK 그룹 avg_pnl이 낮을수록 유효
    if pass_stats['avg_pnl'] is not None and block_stats['avg_pnl'] is not None:
        filter_value = round(pass_stats['avg_pnl'] - block_stats['avg_pnl'], 3)
    else:
        filter_value = None

    return {
        'pass':         pass_stats,
        'block':        block_stats,
        'filter_value': filter_value,   # 양수 → 필터 유효
        'verdict':      (
            '✅ 필터 유효' if (filter_value or 0) > 0.3
            else '⚠️ 필터 약함' if (filter_value or 0) > 0
            else '❌ 필터 무효 (재검토 필요)'
        ),
    }


# ─────────────────────────────────────────────────────────────
# 3. False Negative
# ─────────────────────────────────────────────────────────────

def false_negative_analysis(rows: list[dict], cutoff: float = FALSE_NEG_CUTOFF) -> dict:
    """
    차단됐지만 실제 수익이 좋았던 거래 (기회 손실 측정).
    많으면 threshold 낮추거나 rollout 점진 적용 필요.
    """
    blocked = [r for r in rows if r['blocked'] and r['later_outcome'] is not None]
    fn      = [r for r in blocked if r['later_outcome'] > cutoff]

    if not blocked:
        return {'n_blocked': 0, 'n_fn': 0, 'fn_rate': None, 'avg_fn_outcome': None}

    fn_rate    = round(len(fn) / len(blocked), 3)
    avg_fn_out = round(float(np.mean([r['later_outcome'] for r in fn])), 3) if fn else None
    max_fn_out = round(max((r['later_outcome'] for r in fn), default=0), 3)

    # 기회 손실 심각도 판단
    if fn_rate > 0.30:
        severity = f'❗ 심각 ({fn_rate:.0%}) — threshold 낮출 것'
    elif fn_rate > 0.15:
        severity = f'⚠️ 주의 ({fn_rate:.0%}) — 모니터링'
    else:
        severity = f'✅ 양호 ({fn_rate:.0%})'

    return {
        'n_blocked':    len(blocked),
        'n_fn':         len(fn),
        'fn_rate':      fn_rate,
        'avg_fn_outcome': avg_fn_out,
        'max_fn_outcome': max_fn_out,
        'cutoff':       cutoff,
        'severity':     severity,
    }


# ─────────────────────────────────────────────────────────────
# 4. Threshold Sweep
# ─────────────────────────────────────────────────────────────

def threshold_sweep(rows: list[dict], thresholds=None) -> list[dict]:
    """
    각 threshold에서 PASS 그룹 성과 시뮬레이션.
    Expected Value = avg_pnl × win_rate × n_pass (트레이드 기회 반영)
    """
    if thresholds is None:
        thresholds = THRESHOLDS_TO_SWEEP

    valid = [r for r in rows if r['prob'] is not None and r['later_outcome'] is not None]
    if not valid:
        return []

    results = []
    for thr in thresholds:
        pass_g  = [r for r in valid if r['prob'] >= thr]
        block_g = [r for r in valid if r['prob'] < thr]

        if not pass_g:
            results.append({
                'threshold': thr, 'n_pass': 0, 'n_block': len(block_g),
                'pass_wr': None, 'pass_avg_pnl': None,
                'block_wr': None, 'block_avg_pnl': None,
                'expected_value': None,
            })
            continue

        p_out   = [r['later_outcome'] for r in pass_g]
        b_out   = [r['later_outcome'] for r in block_g] if block_g else []
        p_wins  = [1 if o > 0 else 0 for o in p_out]

        pass_wr  = float(np.mean(p_wins))
        pass_pnl = float(np.mean(p_out))
        ev       = pass_pnl * pass_wr * len(pass_g)   # Sharpe-like expected value

        results.append({
            'threshold':      thr,
            'n_pass':         len(pass_g),
            'n_block':        len(block_g),
            'pass_wr':        round(pass_wr, 3),
            'pass_avg_pnl':   round(pass_pnl, 3),
            'block_avg_pnl':  round(float(np.mean(b_out)), 3) if b_out else None,
            'expected_value': round(ev, 3),
        })

    return results


def recommend_threshold(sweep_results: list[dict]) -> dict:
    """Expected Value 기준 최적 threshold 추천."""
    valid = [r for r in sweep_results if r['expected_value'] is not None and r['n_pass'] >= 5]
    if not valid:
        return {'threshold': None, 'reason': '데이터 부족'}
    best = max(valid, key=lambda r: r['expected_value'])
    return {
        'threshold':      best['threshold'],
        'expected_value': best['expected_value'],
        'pass_wr':        best['pass_wr'],
        'n_pass':         best['n_pass'],
        'reason':         f"EV={best['expected_value']:.2f} (WR={best['pass_wr']:.1%} × PNL={best['pass_avg_pnl']:.2f}% × n={best['n_pass']})",
    }


# ─────────────────────────────────────────────────────────────
# 종합 리포트
# ─────────────────────────────────────────────────────────────

def run_analysis(days: int = 90, min_decisions: int = 10,
                 model_version: str = None) -> dict:
    rows = load_decisions(days=days, model_version=model_version)
    n    = len(rows)

    report = {
        'timestamp':    datetime.now().isoformat(),
        'n_decisions':  n,
        'days':         days,
    }

    if n < min_decisions:
        report['error'] = f'데이터 부족: {n}건 (최소 {min_decisions}건 필요)'
        return report

    report['calibration']    = calibration_analysis(rows)
    report['shadow_split']   = shadow_split_analysis(rows)
    report['false_negative'] = false_negative_analysis(rows)
    report['threshold_sweep']= threshold_sweep(rows)
    report['recommendation'] = recommend_threshold(report['threshold_sweep'])
    return report


def print_report(r: dict) -> None:
    print(f"\n{'═'*60}")
    print(f"  Shadow Analysis  ({r['timestamp'][:19]})")
    print(f"  데이터: {r['n_decisions']}건  최근 {r['days']}일")
    print(f"{'═'*60}")

    if 'error' in r:
        print(f"  ⚠️  {r['error']}")
        return

    # 1. Calibration
    print(f"\n── 1. Calibration (prob 구간 vs 실제 승률) ──────────")
    print(f"  {'구간':>10}  {'n':>4}  {'avg_prob':>8}  {'실제WR':>7}  {'오차':>6}  {'avg_pnl':>8}")
    for c in r['calibration']:
        err_flag = ' ⚠' if c['calib_error'] > 0.15 else ''
        print(f"  {c['bin']:>10}  {c['n']:>4}  {c['avg_prob']:>8.3f}  "
              f"{c['actual_wr']:>7.1%}  {c['calib_error']:>6.3f}{err_flag}  "
              f"{c['avg_pnl']:>+8.2f}%")

    # 2. Shadow Split
    ss = r['shadow_split']
    print(f"\n── 2. Shadow 성과 분리 ───────────────────────────────")
    def _fmt(g):
        if g['n'] == 0: return "n=0"
        return (f"n={g['n']}  WR={g['win_rate']:.1%}  "
                f"avg={g['avg_pnl']:+.2f}%  prob={g['avg_prob']:.3f}")
    print(f"  PASS : {_fmt(ss['pass'])}")
    print(f"  BLOCK: {_fmt(ss['block'])}")
    print(f"  필터 가치: {ss['filter_value']:+.3f}%  → {ss['verdict']}")

    # 3. False Negative
    fn = r['false_negative']
    print(f"\n── 3. False Negative (차단 → 실제 수익 {FALSE_NEG_CUTOFF}%↑) ────")
    print(f"  차단 {fn['n_blocked']}건 중 FN={fn['n_fn']}건  "
          f"비율={fn['fn_rate']:.1%}  avg={fn.get('avg_fn_outcome',0) or 0:+.2f}%  "
          f"max={fn.get('max_fn_outcome',0):+.2f}%")
    print(f"  → {fn['severity']}")

    # 4. Threshold Sweep
    print(f"\n── 4. Threshold Sweep ────────────────────────────────")
    print(f"  {'thr':>5}  {'pass':>5}  {'block':>5}  {'WR':>6}  {'avg_pnl':>8}  {'blk_pnl':>8}  {'EV':>7}")
    best_ev = max((s['expected_value'] or -999) for s in r['threshold_sweep'])
    for s in r['threshold_sweep']:
        mark = ' ◀' if s['expected_value'] == best_ev and s['expected_value'] is not None else ''
        if s['pass_wr'] is None:
            print(f"  {s['threshold']:>5.2f}  {s['n_pass']:>5}  {s['n_block']:>5}  {'—':>6}  {'—':>8}  {'—':>8}  {'—':>7}")
        else:
            print(f"  {s['threshold']:>5.2f}  {s['n_pass']:>5}  {s['n_block']:>5}  "
                  f"{s['pass_wr']:>6.1%}  {s['pass_avg_pnl']:>+8.2f}%  "
                  f"{s['block_avg_pnl'] or 0:>+8.2f}%  {s['expected_value']:>7.2f}{mark}")

    # 5. 추천
    rec = r['recommendation']
    print(f"\n── 5. 권장 Threshold ─────────────────────────────────")
    if rec['threshold']:
        print(f"  👉 threshold = {rec['threshold']}  ({rec['reason']})")
        print(f"\n  적용 방법:")
        print(f"    ml_filter:")
        print(f"      threshold: {rec['threshold']}")
        print(f"      shadow_mode: false")
        print(f"      rollout_pct: 30   # 처음엔 30%만, 이상 없으면 100%로")
    else:
        print(f"  ⚠️  {rec['reason']}")
    print(f"\n{'═'*60}")


def send_telegram_report(r: dict) -> None:
    try:
        import requests
        if 'error' in r:
            msg = f"📊 [Shadow 분석] ⚠️ {r['error']}"
        else:
            rec = r['recommendation']
            ss  = r['shadow_split']
            fn  = r['false_negative']
            msg = (
                f"📊 [Shadow 분석] n={r['n_decisions']}건\n"
                f"필터 가치: {ss['filter_value']:+.3f}%  {ss['verdict']}\n"
                f"FN 비율: {fn['fn_rate']:.1%}  {fn['severity']}\n"
                f"권장 threshold: {rec['threshold']}  "
                f"(EV={rec.get('expected_value','—')})"
            )
        url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
        requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg}, timeout=10)
    except Exception as e:
        logger.warning(f"[SHADOW] 텔레그램 전송 실패: {e}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    parser = argparse.ArgumentParser(description='Shadow Mode 분석')
    parser.add_argument('--days',          type=int,  default=90,  help='분석 기간 (일)')
    parser.add_argument('--min-decisions', type=int,  default=10,  help='최소 데이터 건수')
    parser.add_argument('--model-version', type=str,  default=None,help='모델 버전 필터')
    parser.add_argument('--telegram',      action='store_true',    help='텔레그램 전송')
    args = parser.parse_args()

    report = run_analysis(days=args.days, min_decisions=args.min_decisions,
                          model_version=args.model_version)
    print_report(report)
    if args.telegram:
        send_telegram_report(report)
