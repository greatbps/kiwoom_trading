"""
analysis/eq_shadow_report.py — EQ Shadow Mode 성과 비교 리포트

목적: Shadow mode 기간 동안 "EQ 필터가 실제로 작동했다면?" 시뮬레이션
    - 실제 거래 결과 vs EQ 필터 적용 시 결과 비교
    - 전략별/신호별 EQ 예측 정확도 분석
    - 머지 결정을 위한 근거 데이터 제공

사용법:
    python -m analysis.eq_shadow_report [--threshold 0.40] [--days 30]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_ROOT    = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "trades.db"
_LOG_DIR = _ROOT / "logs"


# ── 데이터 로드 ───────────────────────────────────────────────────

def _load_labeled(db_path: Path, days: int = 0) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if days > 0:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT * FROM entry_features
            WHERE outcome_win IS NOT NULL AND timestamp >= ?
            ORDER BY timestamp
        """, (since,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM entry_features
            WHERE outcome_win IS NOT NULL
            ORDER BY timestamp
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── EQ 모델 로드 ──────────────────────────────────────────────────

def _load_eq_model():
    """현재 저장된 EQ 모델 로드. 피처 수 불일치 시 None 반환."""
    try:
        from ml.eq_model import EQModel, FEATURES
        m = EQModel()
        if m._model is None:
            return None
        # 피처 수 불일치 → 재학습 필요
        n_expected = len(FEATURES)
        n_actual   = getattr(m._model, 'n_features_in_', None)
        if n_actual is not None and n_actual != n_expected:
            logger.warning(
                f"[SHADOW_RPT] 모델 피처 불일치 {n_actual}→{n_expected} "
                f"— 재학습 필요 (0.5 기본값 사용)"
            )
            return None
        return m
    except Exception as e:
        logger.warning(f"[SHADOW_RPT] 모델 로드 실패: {e}")
        return None


# ── Shadow 시뮬레이션 ─────────────────────────────────────────────

def simulate(
    data: list[dict],
    threshold: float = 0.40,
    model=None,
) -> dict:
    """
    각 거래에 대해:
    - EQ P(win) 예측
    - threshold 기준 진입 여부 결정
    - 실제 outcome과 비교

    Returns: 분석 결과 dict
    """
    if not data:
        return {'error': '데이터 없음'}

    rows = []
    for r in data:
        if model is not None:
            p_win = model.predict(r)
        else:
            p_win = 0.5  # 모델 없으면 중립

        eq_would_enter = p_win >= threshold
        actual_win     = r.get('outcome_win', 0)
        pnl            = r.get('outcome_pnl_pct') or (1.0 if actual_win else -1.0)

        rows.append({
            'timestamp':    r.get('timestamp', '')[:16],
            'stock_code':   r.get('stock_code', ''),
            'p_win':        round(p_win, 3),
            'eq_enter':     eq_would_enter,
            'actual_win':   actual_win,
            'pnl':          round(pnl, 3),
            'choch_grade':  r.get('choch_grade', '?'),
            'exit_reason':  r.get('exit_reason', ''),
        })

    # ── 전체 성과 ─────────────────────────────────────────────────
    total        = len(rows)
    actual_wr    = sum(r['actual_win'] for r in rows) / total
    actual_ev    = np.mean([r['pnl'] for r in rows])

    # EQ 필터 적용 시
    eq_rows      = [r for r in rows if r['eq_enter']]
    eq_filtered  = [r for r in rows if not r['eq_enter']]
    eq_n         = len(eq_rows)
    eq_wr        = sum(r['actual_win'] for r in eq_rows) / eq_n if eq_n > 0 else 0.0
    eq_ev        = np.mean([r['pnl'] for r in eq_rows]) if eq_rows else 0.0
    coverage     = eq_n / total

    # 필터로 걸러진 것들의 실제 성과 (false positive 체크)
    filt_wr      = sum(r['actual_win'] for r in eq_filtered) / len(eq_filtered) if eq_filtered else 0.0
    filt_ev      = np.mean([r['pnl'] for r in eq_filtered]) if eq_filtered else 0.0

    # 예측 정확도
    correct_pred = sum(
        1 for r in rows
        if (r['p_win'] >= threshold) == bool(r['actual_win'])
    )
    accuracy = correct_pred / total

    # TP/FP/TN/FN
    tp = sum(1 for r in rows if r['eq_enter'] and r['actual_win'])
    fp = sum(1 for r in rows if r['eq_enter'] and not r['actual_win'])
    tn = sum(1 for r in rows if not r['eq_enter'] and not r['actual_win'])
    fn = sum(1 for r in rows if not r['eq_enter'] and r['actual_win'])
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # ── 전략별 분석 ────────────────────────────────────────────────
    def _by_key(key_fn):
        groups = {}
        for r in rows:
            k = key_fn(r)
            groups.setdefault(k, []).append(r)
        out = {}
        for k, rs in sorted(groups.items()):
            n    = len(rs)
            wr   = sum(x['actual_win'] for x in rs) / n
            ev   = np.mean([x['pnl'] for x in rs])
            eq_r = [x for x in rs if x['eq_enter']]
            eq_w = sum(x['actual_win'] for x in eq_r) / len(eq_r) if eq_r else 0.0
            eq_e = np.mean([x['pnl'] for x in eq_r]) if eq_r else 0.0
            out[k] = {
                'n': n, 'wr': round(wr, 3), 'ev': round(float(ev), 4),
                'eq_n': len(eq_r), 'eq_wr': round(eq_w, 3), 'eq_ev': round(float(eq_e), 4),
            }
        return out

    by_grade  = _by_key(lambda r: r['choch_grade'] or '?')
    by_exit   = _by_key(lambda r: r['exit_reason'] or 'unknown')

    return {
        'total':        total,
        'threshold':    threshold,
        'model_loaded': model is not None,

        'baseline': {
            'n': total, 'wr': round(actual_wr, 3), 'ev': round(float(actual_ev), 4),
        },
        'eq_filtered': {
            'n': eq_n, 'wr': round(eq_wr, 3), 'ev': round(float(eq_ev), 4),
            'coverage': round(coverage, 3),
        },
        'filtered_out': {
            'n': len(eq_filtered), 'wr': round(filt_wr, 3), 'ev': round(float(filt_ev), 4),
        },
        'prediction': {
            'accuracy':  round(accuracy, 3),
            'precision': round(precision, 3),
            'recall':    round(recall, 3),
            'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn,
        },
        'by_choch_grade': by_grade,
        'by_exit_reason': by_exit,
        'rows': rows,
    }


# ── 리포트 출력 ───────────────────────────────────────────────────

def print_report(result: dict):
    if result.get('error'):
        print(f"\n⚠️  {result['error']}")
        return

    b  = result['baseline']
    eq = result['eq_filtered']
    fo = result['filtered_out']
    pr = result['prediction']

    wr_delta = eq['wr'] - b['wr']
    ev_delta = eq['ev'] - b['ev']

    print("\n" + "=" * 64)
    print("  EQ Shadow Mode 성과 비교")
    print("=" * 64)
    print(f"  기간 데이터: {result['total']}건  "
          f"임계값: {result['threshold']:.2f}  "
          f"모델: {'✅' if result['model_loaded'] else '❌(0.5 기본값)'}")
    print()

    print(f"  {'':20s} {'N':>5} {'WR':>7} {'Avg EV':>8}")
    print("  " + "-" * 42)
    print(f"  {'전체 (기준선)':20s} {b['n']:>5} {b['wr']:>7.1%} {b['ev']:>+8.3f}%")
    print(f"  {'EQ 통과 (진입)':20s} {eq['n']:>5} {eq['wr']:>7.1%} {eq['ev']:>+8.3f}%  "
          f"({'↑' if wr_delta>0 else '↓'}{wr_delta:+.1%} WR, {ev_delta:+.3f}% EV)")
    print(f"  {'EQ 필터링 (차단)':20s} {fo['n']:>5} {fo['wr']:>7.1%} {fo['ev']:>+8.3f}%")
    print(f"  {'커버리지':20s} {'':>5} {eq['coverage']:>7.1%}")

    print()
    print("  [예측 정확도]")
    print(f"  Accuracy={pr['accuracy']:.1%}  Precision={pr['precision']:.1%}  "
          f"Recall={pr['recall']:.1%}")
    print(f"  TP={pr['tp']} FP={pr['fp']} TN={pr['tn']} FN={pr['fn']}")

    # CHoCH 등급별
    by_grade = result.get('by_choch_grade', {})
    if by_grade:
        print()
        print("  [CHoCH 등급별]")
        print(f"  {'등급':>4} {'N':>4} {'기준 WR':>8} {'EQ WR':>8} {'EQ N':>6} {'EV차':>8}")
        print("  " + "-" * 42)
        for grade, s in sorted(by_grade.items()):
            delta = s['eq_wr'] - s['wr']
            print(f"  {grade:>4} {s['n']:>4} {s['wr']:>8.1%} {s['eq_wr']:>8.1%} "
                  f"{s['eq_n']:>6} {s['eq_ev']-s['ev']:>+8.3f}%")

    # exit reason별
    by_exit = result.get('by_exit_reason', {})
    if by_exit:
        print()
        print("  [청산 사유별 EQ 효과]")
        print(f"  {'사유':22s} {'N':>4} {'기준 EV':>8} {'EQ EV':>8}")
        print("  " + "-" * 44)
        for reason, s in sorted(by_exit.items(), key=lambda x: -x[1]['n']):
            print(f"  {reason[:22]:22s} {s['n']:>4} {s['ev']:>+8.3f}% {s['eq_ev']:>+8.3f}%")

    # 판정
    print()
    print("  [머지 판정]")
    meets = []
    fails = []
    if b['n'] >= 50:
        meets.append(f"✅ 샘플 충분 ({b['n']}건)")
    else:
        fails.append(f"❌ 샘플 부족 ({b['n']}/50건)")
    if wr_delta > 0:
        meets.append(f"✅ WR 개선 {b['wr']:.1%}→{eq['wr']:.1%} ({wr_delta:+.1%})")
    else:
        fails.append(f"❌ WR 미개선 {wr_delta:+.1%}")
    if ev_delta > 0:
        meets.append(f"✅ EV 개선 {ev_delta:+.3f}%")
    else:
        fails.append(f"❌ EV 미개선 {ev_delta:+.3f}%")
    filt_wr = fo['wr']
    if filt_wr < b['wr']:
        meets.append(f"✅ 필터링된 것들 실제로 나쁨 (WR={filt_wr:.1%})")
    else:
        fails.append(f"⚠️  필터링된 것들 WR={filt_wr:.1%} (기준선과 유사)")

    for m in meets:
        print(f"  {m}")
    for f in fails:
        print(f"  {f}")

    if not fails:
        print("\n  → 🎉 머지 조건 충족! eq_promote.py --dry-run 실행 권장")
    else:
        print("\n  → ⏳ 계속 Shadow 수집")
    print("=" * 64 + "\n")


# ── 저장 ─────────────────────────────────────────────────────────

def save_report(result: dict):
    _LOG_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path  = _LOG_DIR / f"eq_shadow_report_{today}.json"
    out   = {k: v for k, v in result.items() if k != 'rows'}  # rows 제외
    out['generated_at'] = datetime.now().isoformat()
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"  → 저장: {path}")


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(message)s')

    parser = argparse.ArgumentParser(description='EQ Shadow 성과 비교')
    parser.add_argument('--threshold', type=float, default=0.40)
    parser.add_argument('--days',      type=int,   default=0, help='최근 N일 (0=전체)')
    parser.add_argument('--no-save',   action='store_true')
    args = parser.parse_args()

    data = _load_labeled(_DB_PATH, days=args.days)
    if not data:
        print("⚠️  레이블 데이터 없음")
        raise SystemExit(0)

    model  = _load_eq_model()
    result = simulate(data, threshold=args.threshold, model=model)
    print_report(result)

    if not args.no_save:
        save_report(result)
