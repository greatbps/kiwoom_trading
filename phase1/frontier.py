"""
Phase 1 — 목표 KPI 가 동시에 달성 가능한가 (Pareto Frontier)

Iteration 1 에서 필터 4종이 전부 FAIL 했다. 그런데 실패 이유가 서로
반대다.

    C(RVOL 상승)  PF 2.232 · WR 50.0% · MDD -5.46%  → 성능은 목표 충족,
                  그런데 거래가 16건 (목표 100건)
    나머지        거래는 충분한데 PF 가 1.7~1.85 (목표 2.0)

필터는 후보를 **줄이는** 장치다. 거래를 100건으로 늘리려면 후보를 더
만들어야 하는데, Phase 0.2 는 후보를 늘리면 PF 가 떨어진다는 것을 이미
보였다. 두 목표가 서로를 밀어낸다면 어떤 필터를 더 시도해도 소용없다.

그래서 조합을 전수로 돌려 **(거래 >=100 AND PF >=2.0) 인 점이 하나라도
존재하는지** 본다. 존재하지 않으면 목표 자체를 조정해야 한다.

사용법:
    python -m phase1.frontier
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from phase0 import data_cache as dc
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_baseline
from phase1 import features as ft
from phase1.run_iteration1 import WF, build_feature_table, filtered_signals

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'frontier.json')

GOAL_PF = 2.0
GOAL_TRADES = 100
GOAL_WR = 50.0
GOAL_MDD = -10.0


def main():
    data = dc.load_ohlcv()
    days = dc.trading_days(data)
    mkt = ft.market_series(data)

    keys = list(ft.FILTERS)
    combos = [()] + [(k,) for k in keys] + \
        list(itertools.combinations(keys, 2)) + \
        list(itertools.combinations(keys, 3)) + [tuple(keys)]

    print('=' * 84)
    print('  목표 KPI 동시 달성 가능성 — 후보 프로파일 3종 × 필터 조합 16종')
    print('=' * 84)
    print(f'  목표: 거래>={GOAL_TRADES}  PF>={GOAL_PF}  '
          f'WR>={GOAL_WR}%  MDD>{GOAL_MDD}%')

    rows = []
    for prof in ('OPS', 'E', 'G'):
        sig0 = ParamCandidateGen(**pfp.gen_kwargs(prof)).scan(data)
        feats = build_feature_table(data, sig0, mkt)
        for cb in combos:
            s = sig0
            for k in cb:
                s = filtered_signals(s, feats, ft.FILTERS[k])
            if not s:
                continue
            k_ = kpi(PortfolioBacktest(data, s, days, sel_baseline, None,
                                       0).run('x'))
            if not k_.get('trades'):
                continue
            rows.append({'profile': prof,
                         'filters': [c.split('.')[0] for c in cb] or ['none'],
                         'trades': k_['trades'], 'pf': k_['profit_factor'],
                         'wr': k_['win_rate'], 'mdd': k_['mdd_pct'],
                         'ret': k_['total_return_pct'],
                         'exp': k_['expectancy_pct']})

    df = pd.DataFrame(rows)
    hit = df[(df['trades'] >= GOAL_TRADES) & (df['pf'] >= GOAL_PF)]
    hit_all = hit[(hit['wr'] >= GOAL_WR) & (hit['mdd'] > GOAL_MDD)]

    print(f'\n  조합 {len(df)}개 평가')
    print(f'  거래>={GOAL_TRADES} AND PF>={GOAL_PF} 인 점: {len(hit)}개')
    print(f'  4개 목표 전부 만족하는 점 : {len(hit_all)}개')

    print(f'\n  [거래 >= {GOAL_TRADES} 인 조합 중 PF 상위]')
    top = df[df['trades'] >= GOAL_TRADES].nlargest(8, 'pf')
    print(f'  {"프로파일":<7}{"필터":<18}{"거래":>6}{"PF":>8}{"WR%":>7}'
          f'{"MDD%":>8}{"수익%":>8}')
    for _, r in top.iterrows():
        print(f'  {r["profile"]:<7}{"+".join(r["filters"]):<18}'
              f'{r["trades"]:>6}{r["pf"]:>8.3f}{r["wr"]:>7.1f}'
              f'{r["mdd"]:>8.2f}{r["ret"]:>8.2f}')

    print(f'\n  [PF >= {GOAL_PF} 인 조합 중 거래 상위]')
    top2 = df[df['pf'] >= GOAL_PF].nlargest(8, 'trades')
    if len(top2):
        print(f'  {"프로파일":<7}{"필터":<18}{"거래":>6}{"PF":>8}{"WR%":>7}'
              f'{"MDD%":>8}{"수익%":>8}')
        for _, r in top2.iterrows():
            print(f'  {r["profile"]:<7}{"+".join(r["filters"]):<18}'
                  f'{r["trades"]:>6}{r["pf"]:>8.3f}{r["wr"]:>7.1f}'
                  f'{r["mdd"]:>8.2f}{r["ret"]:>8.2f}')
    else:
        print('  없음')

    # 거래수 ↔ PF 관계 — 두 목표가 정말 서로를 밀어내는가
    c = df[['trades', 'pf']].corr(method='spearman').iloc[0, 1]
    print(f'\n  거래수 ↔ PF Spearman 상관: {c:+.3f}')
    print('  (음수면 거래를 늘릴수록 PF 가 떨어진다 — 두 목표가 상충)')

    best_pf = df.loc[df['pf'].idxmax()]
    print(f'\n  전 조합 최고 PF: {best_pf["pf"]:.3f} '
          f'({best_pf["profile"]} + {"+".join(best_pf["filters"])}, '
          f'거래 {best_pf["trades"]}건)')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'goal': {'trades': GOAL_TRADES, 'pf': GOAL_PF,
                            'win_rate': GOAL_WR, 'mdd': GOAL_MDD},
                   'combos_evaluated': len(df),
                   'hits_trades_and_pf': len(hit),
                   'hits_all_four': len(hit_all),
                   'trades_pf_spearman': round(float(c), 4),
                   'rows': rows}, f, ensure_ascii=False, indent=2,
                  default=str)
    print(f'\n  저장: {OUT}')


if __name__ == '__main__':
    main()
