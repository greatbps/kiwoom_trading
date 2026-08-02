"""
Phase 1 Iteration 1 — Experiment 2(진입 필터) · Experiment 3(연속 스코어)

Walk Forward 구간은 지시서 고정값이다.

    Train       2024-08-01 ~ 2025-07-31
    Validation  2025-08-01 ~ 2026-01-31
    Test        2026-02-01 ~ 2026-07-30

⚠️ Train 구간도 out-of-sample 이 아니다. Phase 0 에서 후보 생성 조건을
   고를 때 이미 이 데이터 전체를 봤다. 여기서 Train/Val/Test 를 나누는
   목적은 "한 구간에서만 좋은 것" 을 걸러내는 것이지, 미래 성과를
   추정하는 것이 아니다.

사용법:
    python -m phase1.run_iteration1
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from scipy import stats

from phase0 import data_cache as dc
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_baseline
from phase1 import features as ft

OUT = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(OUT, 'iteration1_result.json')

WF = [('Train', '2024-08-01', '2025-07-31'),
      ('Validation', '2025-08-01', '2026-01-31'),
      ('Test', '2026-02-01', '2026-07-30')]

# 최종 목표 KPI (지시서 §2)
GOAL = {'profit_factor': 2.0, 'win_rate': 50.0, 'mdd_pct': -10.0,
        'trades': 100, 'val_pf': 1.3, 'test_pf': 1.3}


def _git(*a):
    try:
        return subprocess.run(['git', *a], capture_output=True, text=True,
                              cwd=os.path.dirname(OUT)).stdout.strip()
    except Exception:
        return 'UNKNOWN'


def build_feature_table(data, sig, mkt):
    """후보 하나하나의 피처 + 스코어. 필터/스코어가 같은 값을 본다."""
    rows = {}
    for sym, days in sig.items():
        df = data[sym]
        pos = {d: i for i, d in enumerate(df.index)}
        for d in days:
            i = pos.get(d)
            if i is None:
                continue
            f = ft.compute(df, i)
            if not f:
                continue
            rs = ft.regime_ok(mkt, d, strict=True)
            f['regime'] = ft.regime_ok(mkt, d, strict=False)
            f['regime_strict'] = rs
            f['score'] = ft.score100(f, rs)
            rows[(sym, d)] = f
    return rows


def filtered_signals(sig, feats, fn):
    out = {}
    for sym, days in sig.items():
        keep = {d for d in days
                if (k := feats.get((sym, d))) is not None
                and fn(k, k.get('regime_strict'))}
        if keep:
            out[sym] = keep
    return out


def run(data, sig, days, name):
    return PortfolioBacktest(data, sig, days, sel_baseline, None, 0).run(name)


def main():
    data = dc.load_ohlcv()
    all_days = dc.trading_days(data)
    mkt = ft.market_series(data)

    base_sig = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)
    feats = build_feature_table(data, base_sig, mkt)

    print('=' * 82)
    print('  Phase 1 Iteration 1 — Experiment 2 / 3')
    print('=' * 82)
    print(f'  후보 피처 산출 {len(feats)}건   유니버스 {len(data)}종목')

    # ═══ Experiment 2 — 진입 필터 ═══════════════════════════════════════
    print('\n' + '=' * 82)
    print('  Experiment 2 — BUY Entry Quality Filter (각각 독립)')
    print('=' * 82)

    cases = {'OPS (Baseline)': base_sig}
    for label, fn in ft.FILTERS.items():
        cases[label] = filtered_signals(base_sig, feats, fn)

    exp2 = []
    for label, s in cases.items():
        row = {'case': label}
        print(f'\n  {label}')
        print(f'    {"구간":<12}{"후보":>6}{"거래":>6}{"승률%":>8}{"PF":>8}'
              f'{"기대값%":>9}{"수익%":>9}{"MDD%":>9}')
        for seg, lo, hi in [('전체', dc.START, dc.END)] + WF:
            d = [x for x in all_days if lo <= str(x.date()) <= hi]
            nc = sum(1 for v in s.values() for x in v if x in set(d))
            k = kpi(run(data, s, d, label))
            if not k.get('trades'):
                print(f'    {seg:<12}{nc:>6}{0:>6}   (거래 없음)')
                row[seg] = {'candidates': nc, 'trades': 0}
                continue
            row[seg] = {**{kk: k[kk] for kk in
                           ('trades', 'win_rate', 'profit_factor',
                            'expectancy_pct', 'total_return_pct', 'mdd_pct',
                            'max_consecutive_loss')}, 'candidates': nc}
            print(f'    {seg:<12}{nc:>6}{k["trades"]:>6}{k["win_rate"]:>8.1f}'
                  f'{k["profit_factor"]:>8.3f}{k["expectancy_pct"]:>9.3f}'
                  f'{k["total_return_pct"]:>9.2f}{k["mdd_pct"]:>9.2f}')
        exp2.append(row)

    # ═══ Experiment 3 — 연속 스코어 ═════════════════════════════════════
    print('\n' + '=' * 82)
    print('  Experiment 3 — Continuous Score (0~100) 검증')
    print('=' * 82)

    res = run(data, base_sig, all_days, 'OPS')
    recs = []
    for t in res.trades:
        f = feats.get((t.symbol, pd.Timestamp(t.signal_date)))
        if f:
            recs.append({'pnl': t.pnl_pct, 'total': f['score']['total'],
                         **{k: f['score'][k] for k in
                            ('smc', 'volume', 'trend', 'volatility')}})
    tb = pd.DataFrame(recs)
    print(f'  거래 {len(tb)}건에 스코어 부착')
    print(f'  스코어 분포  최소 {tb["total"].min():.1f}  '
          f'중앙 {tb["total"].median():.1f}  최대 {tb["total"].max():.1f}  '
          f'고유값 {tb["total"].nunique()}개  표준편차 {tb["total"].std():.2f}')

    print(f'\n  {"축":<12}{"Spearman rho":>14}{"p-value":>12}   판정')
    corr = {}
    for col in ('total', 'smc', 'volume', 'trend', 'volatility'):
        rho, p = stats.spearmanr(tb[col], tb['pnl'])
        ok = rho >= 0.2 and p < 0.05
        corr[col] = {'rho': round(float(rho), 4), 'p': round(float(p), 4),
                     'pass': bool(ok)}
        print(f'  {col:<12}{rho:>14.4f}{p:>12.4f}   '
              f'{"✅ PASS" if ok else "❌ FAIL"}')

    # 5분위 성과 — 상관계수가 0 이어도 단조성이 있을 수 있다
    tb['q'] = pd.qcut(tb['total'], 5, labels=[1, 2, 3, 4, 5],
                      duplicates='drop')
    print(f'\n  {"스코어 5분위":<14}{"건":>5}{"승률%":>8}{"평균수익%":>11}'
          f'{"스코어범위":>18}')
    quint = []
    for q, g in tb.groupby('q', observed=True):
        quint.append({'q': int(q), 'trades': len(g),
                      'win_rate': round((g['pnl'] > 0).mean() * 100, 1),
                      'avg_pnl_pct': round(g['pnl'].mean() * 100, 3)})
        print(f'  {int(q):<14}{len(g):>5}{(g["pnl"]>0).mean()*100:>8.1f}'
              f'{g["pnl"].mean()*100:>11.2f}'
              f'{f"{g['total'].min():.1f}~{g['total'].max():.1f}":>18}')

    # ═══ 목표 KPI 대조 ══════════════════════════════════════════════════
    print('\n' + '=' * 82)
    print('  목표 KPI 대조 (지시서 §2)')
    print('=' * 82)
    print(f'  {"케이스":<22}{"PF":>7}{"WR%":>7}{"MDD%":>8}{"거래":>6}'
          f'{"ValPF":>7}{"TestPF":>8}   판정')
    verdict = []
    for r in exp2:
        a = r.get('전체', {})
        if not a.get('trades'):
            continue
        v = r.get('Validation', {}).get('profit_factor') or 0
        t = r.get('Test', {}).get('profit_factor') or 0
        miss = []
        if a['profit_factor'] < GOAL['profit_factor']:
            miss.append(f'PF {a["profit_factor"]:.3f}<2.0')
        if a['win_rate'] < GOAL['win_rate']:
            miss.append(f'WR {a["win_rate"]:.1f}<50')
        if a['mdd_pct'] < GOAL['mdd_pct']:
            miss.append(f'MDD {a["mdd_pct"]:.2f}<-10')
        if a['trades'] < GOAL['trades']:
            miss.append(f'거래 {a["trades"]}<100')
        if v < GOAL['val_pf']:
            miss.append(f'ValPF {v:.3f}<1.3')
        if t < GOAL['test_pf']:
            miss.append(f'TestPF {t:.3f}<1.3')
        verdict.append({'case': r['case'], 'pass': not miss, 'missed': miss})
        print(f'  {r["case"]:<22}{a["profit_factor"]:>7.3f}{a["win_rate"]:>7.1f}'
              f'{a["mdd_pct"]:>8.2f}{a["trades"]:>6}{v:>7.3f}{t:>8.3f}   '
              f'{"✅ PASS" if not miss else "❌ FAIL"}')

    out = {
        'run_id': f'phase1-iter1-{datetime.now():%Y%m%d-%H%M%S}',
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'git_commit': _git('rev-parse', 'HEAD'),
        'walk_forward': [{'segment': a, 'start': b, 'end': c} for a, b, c in WF],
        'goal_kpi': GOAL,
        'experiment2': exp2,
        'experiment3': {'trades_scored': len(tb),
                        'score_distinct': int(tb['total'].nunique()),
                        'score_std': round(float(tb['total'].std()), 3),
                        'spearman': corr, 'quintiles': quint},
        'verdict': verdict,
        'caveats': [
            'Train 구간도 out-of-sample 이 아니다 — Phase 0 에서 후보 조건을 '
            '고를 때 이미 전 구간을 봤다.',
            'VWAP 은 20일 일봉 근사다. 운영 장중 VWAP 과 다른 지표다.',
            'Regime 은 유니버스 동일가중 지수 근사다. '
            'core/regime_detector.py 를 쓰지 않았다.',
            '청산은 일봉 스윙 근사다. 실거래 PF 0.102 와 직접 비교 불가.',
        ],
    }
    with open(RESULT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: {RESULT}')


if __name__ == '__main__':
    main()
