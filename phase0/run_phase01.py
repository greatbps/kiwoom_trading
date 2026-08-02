"""
Phase 0.1 — Score Ranking 효과 검증

    Baseline (상한 없음)  vs  Score Top3  vs  Score Top5  vs  Random 5

Random 은 시드 하나로 돌리면 그 결과가 규칙의 효과인지 그날의 운인지
구분할 수 없다. 20개 시드를 돌려 평균과 폭을 함께 본다.

사용법:
    python -m phase0.run_phase01
    python -m phase0.run_phase01 --seeds 50
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from phase0 import data_cache as dc
from phase0.portfolio import (EXIT_PROFILE, INITIAL_CAPITAL, SLOT_DIVISOR,
                              PortfolioBacktest, kpi, sel_baseline, sel_random,
                              sel_top)

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_JSON = os.path.join(OUT_DIR, 'baseline_phase0.json')


def _git(*a) -> str:
    try:
        return subprocess.run(['git', *a], capture_output=True, text=True,
                              cwd=os.path.dirname(OUT_DIR)).stdout.strip()
    except Exception:
        return 'UNKNOWN'


def funnel(data, days):
    """
    후보가 왜 이렇게 적은지 — 필터 단계별로 몇 개가 남는지 센다.

    결론이 "선별 규칙의 효과가 없다" 로 나올 때, 그게 규칙 탓인지
    후보가 애초에 없어서인지 구분하려면 이 표가 있어야 한다.
    """
    from backtest.adapter import SMCAdapter
    from backtest.daily_scan import BEST_CONFIG

    stages = [
        ('① CHoCH 원신호', dict()),
        ('② +거래량 1.5x', dict(require_volume=True)),
        ('③ +ATR 2~8%', dict(require_volume=True, atr_pct_min=0.02,
                             atr_pct_max=0.08)),
        ('④ +MA50 우상향 (운영값)',
         dict(require_volume=True, atr_pct_min=0.02, atr_pct_max=0.08,
              require_ma50_trend=True)),
    ]
    dayset = set(days)
    rows = []
    for label, kw in stages:
        ad = SMCAdapter(BEST_CONFIG, require_sweep=False, **kw)
        cnt = 0
        for sym, df in data.items():
            for i in range(len(df)):
                if df.index[i] in dayset and ad.get_signal(df, i) == 'BUY':
                    cnt += 1
        rows.append((label, cnt))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=20)
    ap.add_argument('--skip-funnel', action='store_true')
    a = ap.parse_args()

    data = dc.load_ohlcv()
    signals = dc.build_signals(data)
    days = dc.trading_days(data)

    print('=' * 72)
    print('  Phase 0.1 — Score Ranking 효과 검증')
    print('=' * 72)
    print(f'  기간      {dc.START} ~ {dc.END}   ({len(days)} 거래일)')
    print(f'  유니버스  {len(data)} 종목 (DEFAULT_CANDIDATES)')
    print(f'  신호      {sum(1 for s in signals.values() for d in s if d in set(days))} 건')
    print(f'  패턴점수  OFF (작업지시서 Step 3)')
    print()

    # ── 후보 밀도: 실험이 성립하는지부터 본다 ────────────────────────────
    per_day = pd.Series([sum(1 for s in signals.values() if d in s)
                         for d in days], index=days)
    print('-' * 72)
    print('  ⓪ 실험 성립 조건 — 하루 후보 수가 슬롯보다 많아야 "고르는" 의미가 있다')
    print('-' * 72)
    print(f'  평균 {per_day.mean():.2f}   중앙값 {per_day.median():.0f}   '
          f'최대 {per_day.max()}')
    for k, tag in ((3, 'Top3'), (5, 'Top5')):
        n = int((per_day > k).sum())
        print(f'  후보 > {k} 인 날: {n:3d}일 ({n/len(days)*100:.1f}%)'
              f'  → {tag} 가 Baseline 과 달라질 수 있는 날')
    print()

    # ── 케이스 실행 ──────────────────────────────────────────────────────
    cases = [
        ('A. Baseline (상한 없음)', sel_baseline, None, 0),
        ('B. Score Top3', sel_top(3), 3, 0),
        ('C. Score Top5', sel_top(5), 5, 0),
    ]
    results, kpis = {}, []
    for name, selr, slots, seed in cases:
        r = PortfolioBacktest(data, signals, days, selr, slots, seed).run(name)
        results[name] = r
        kpis.append(kpi(r))

    # ── Random 대조군 — 다중 시드 ────────────────────────────────────────
    #
    # ⚠️ Top3 의 대조군은 Random3 이어야 한다. 슬롯 수가 다르면 '고르는
    #    규칙' 이 아니라 '몇 개나 담는가' 를 비교하게 된다.
    rand_sets = {}
    for n in (3, 5):
        ks = []
        for s in range(a.seeds):
            r = PortfolioBacktest(data, signals, days, sel_random(n), n,
                                  s).run(f'Random{n} (seed={s})')
            ks.append(kpi(r))
        rand_sets[n] = ks

    def summarize(n, ks):
        def agg(key):
            v = [k.get(key) for k in ks if k.get(key) is not None]
            return (float(np.mean(v)), float(np.std(v)),
                    float(min(v)), float(max(v))) if v else (0, 0, 0, 0)
        d = {'name': f'D. Random{n} (시드 {a.seeds}개)', 'trades': agg('trades')[0]}
        for k in ('win_rate', 'avg_return_pct', 'profit_factor',
                  'total_pnl_won', 'total_return_pct', 'mdd_pct'):
            m, sd, lo, hi = agg(k)
            d[k] = round(m, 3)
            d[k + '_sd'] = round(sd, 3)
            d[k + '_range'] = [round(lo, 3), round(hi, 3)]
        return d

    rd3, rd5 = summarize(3, rand_sets[3]), summarize(5, rand_sets[5])
    kpis += [rd3, rd5]

    def percentile(score_val, ks, key='total_return_pct'):
        """Score 결과가 Random 분포의 몇 백분위인가. 50 근처면 효과 없음."""
        v = sorted(k[key] for k in ks)
        return round(sum(1 for x in v if x < score_val) / len(v) * 100, 1)

    pct3 = percentile(b_k_ret := kpis[1]['total_return_pct'], rand_sets[3])
    pct5 = percentile(c_k_ret := kpis[2]['total_return_pct'], rand_sets[5])

    # ── 표 ───────────────────────────────────────────────────────────────
    print('-' * 72)
    print('  ① KPI 비교')
    print('-' * 72)
    hdr = f'{"케이스":<26}{"거래":>5}{"승률%":>7}{"평균%":>8}{"PF":>7}{"총수익%":>9}{"MDD%":>8}'
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    for k in kpis:
        if not k.get('trades'):
            print(f'{k["name"]:<26}{"0":>5}   (거래 없음)')
            continue
        pf = k.get('profit_factor')
        print(f'{k["name"]:<26}{k["trades"]:>5.0f}{k["win_rate"]:>7.1f}'
              f'{k["avg_return_pct"]:>8.2f}'
              f'{(f"{pf:.3f}" if pf else "-"):>7}'
              f'{k["total_return_pct"]:>9.2f}{k["mdd_pct"]:>8.2f}')
    print()
    for k in kpis[:3]:
        if k.get('trades'):
            print(f'  {k["name"]}  청산사유 {k["exit_reasons"]}  '
                  f'평균보유 {k["avg_hold_bars"]}봉')

    # ── 판정 ─────────────────────────────────────────────────────────────
    a_k, b_k, c_k = kpis[0], kpis[1], kpis[2]
    bind3, bind5 = b_k['binding_days'], c_k['binding_days']

    print()
    print('-' * 72)
    print('  ② 선별 규칙이 실제로 작동한 횟수')
    print('-' * 72)
    print('  "후보 > 빈 슬롯" 인 날에만 무엇을 고르는지가 결과를 바꾼다.')
    print(f'  Top3 : {bind3:3d}일 / {len(days)}일 '
          f'({bind3/len(days)*100:.1f}%)   버려진 후보 {b_k["dropped_candidates"]}건')
    print(f'  Top5 : {bind5:3d}일 / {len(days)}일 '
          f'({bind5/len(days)*100:.1f}%)   버려진 후보 {c_k["dropped_candidates"]}건')

    print()
    print('-' * 72)
    print('  ③ Score vs Random — 슬롯 수를 맞춘 직접 비교')
    print('-' * 72)
    for tag, sk, rk, pct in (('3슬롯', b_k, rd3, pct3), ('5슬롯', c_k, rd5, pct5)):
        print(f'  {tag}  Score {sk["total_return_pct"]:>6.2f}%   '
              f'Random {rk["total_return_pct"]:>6.2f}% ± {rk["total_return_pct_sd"]:.2f} '
              f'(범위 {rk["total_return_pct_range"][0]:.2f}~'
              f'{rk["total_return_pct_range"][1]:.2f})')
        print(f'        → Score 는 Random 분포의 {pct:.1f} 백분위')

    n_gt5 = int((per_day > 5).sum())
    n_gt3 = int((per_day > 3).sum())

    print()
    print('-' * 72)
    print('  ④ 판정')
    print('-' * 72)
    if bind5 == 0 and bind3 == 0:
        verdict = 'INSUFFICIENT'
        msg = '선별이 한 번도 구속되지 않았다 — 비교 대상 자체가 없다.'
    elif max(bind3, bind5) < 20:
        verdict = 'INSUFFICIENT'
        msg = (f'선별이 구속된 날이 Top3 {bind3}일 / Top5 {bind5}일뿐이다.\n'
               '     이 표본으로는 Score Ranking 이 Random 보다 나은지 '
               '판단할 수 없다.\n'
               '     Baseline 과 Top-N 의 차이는 "무엇을 골랐는가" 가 아니라\n'
               '     "슬롯이 차서 몇 건을 못 잡았는가" 에서 나온 것이다.')
    elif 25 <= min(pct3, pct5) <= 75:
        verdict = 'NO EFFECT'
        msg = (f'Score 가 Random 분포의 {pct3}/{pct5} 백분위 — '
               '무작위와 구분되지 않는다.')
    else:
        verdict = 'EFFECT DETECTED'
        msg = f'Score 백분위 Top3 {pct3} / Top5 {pct5}.'
    print(f'  VERDICT : {verdict}')
    print(f'     {msg}')

    # ── 퍼널 ─────────────────────────────────────────────────────────────
    fn = []
    if not a.skip_funnel:
        print()
        print('-' * 72)
        print('  ③ 후보 생성 퍼널 — 어디서 줄어드는가')
        print('-' * 72)
        fn = funnel(data, days)
        base = fn[0][1] or 1
        for label, c in fn:
            print(f'  {label:<26}{c:>6} 건   (원신호 대비 {c/base*100:5.1f}%)')

    # ── 동결 스냅샷 ──────────────────────────────────────────────────────
    cfg = {
        'period': {'start': dc.START, 'end': dc.END,
                   'warmup_start': dc.WARMUP_START,
                   'trading_days': len(days)},
        'universe': {'source': 'backtest.scanner.DEFAULT_CANDIDATES',
                     'requested': 78, 'loaded': len(data),
                     'survivorship_bias': True},
        'signal': {'config': 'backtest.daily_scan.BEST_CONFIG',
                   'adapter': 'backtest.daily_scan.BEST_ADAPTER_KWARGS'},
        'score': {'pattern_weights': 'OFF', 'min_score': 2, 'max_selected': 5},
        'exit_profile': EXIT_PROFILE,
        'capital': {'initial': INITIAL_CAPITAL, 'slot_divisor': SLOT_DIVISOR,
                    'compounding': False},
        'random_seeds': a.seeds,
    }
    cfg_s = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    baseline_hash = hashlib.sha256(cfg_s.encode()).hexdigest()[:16]
    run_id = f'phase0.1-{datetime.now():%Y%m%d-%H%M%S}'

    snap = {
        'run_id': run_id,
        'baseline_hash': baseline_hash,
        'git_commit': _git('rev-parse', 'HEAD'),
        'git_dirty': bool(_git('status', '--porcelain')),
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'config': cfg,
        'candidate_density': {
            'mean': round(float(per_day.mean()), 3),
            'median': float(per_day.median()),
            'max': int(per_day.max()),
            'days_gt_3': n_gt3, 'days_gt_5': n_gt5,
            'days_with_any': int((per_day > 0).sum()),
        },
        'verdict': verdict,
        'selection_binding': {
            'top3_days': bind3, 'top5_days': bind5,
            'top3_dropped': b_k['dropped_candidates'],
            'top5_dropped': c_k['dropped_candidates'],
        },
        'score_vs_random_percentile': {'slots_3': pct3, 'slots_5': pct5},
        'kpi': kpis,
        'funnel': [{'stage': s, 'signals': c} for s, c in fn],
    }
    with open(BASELINE_JSON, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2, default=str)

    print()
    print('-' * 72)
    print(f'  run_id        {run_id}')
    print(f'  baseline_hash {baseline_hash}')
    print(f'  git           {snap["git_commit"][:12]}'
          f'{"  (dirty)" if snap["git_dirty"] else ""}')
    print(f'  저장          {BASELINE_JSON}')
    print('-' * 72)


if __name__ == '__main__':
    main()
