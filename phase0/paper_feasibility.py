"""
Phase 0.3 부록 — Paper 20거래일 기준이 달성 가능한가

작업지시서의 Pass 기준은 **20거래일** 창에 걸린 값이다.

    거래 >= 10   ·   PF >= 1.3   ·   MDD < -10%   ·   평균손실 <= 기존 +20%

그런데 이 기준을 2년짜리 백테스트 숫자에 그대로 대면 비교가 성립하지
않는다. 20일 창은 낙폭이 쌓일 시간 자체가 짧고, 거래도 몇 건 안 나온다.

그래서 과거 데이터를 **20거래일 창으로 잘라** 그 창들에서 기준이
몇 번이나 충족됐는지 센다. 충족 비율이 0 에 가까우면, Paper 를 4주
돌려도 통과할 수 없다는 뜻이다 — 시작하기 전에 알아야 한다.

사용법:
    python -m phase0.paper_feasibility
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from phase0 import data_cache as dc
from phase0 import profiles as pf
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, sel_top

WINDOW = 20          # 거래일
PASS_TRADES = 10
PASS_PF = 1.30
PASS_MDD = -10.0

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'phase0.3_paper_feasibility.json')


def main():
    data = dc.load_ohlcv()
    days = dc.trading_days(data)

    print('=' * 78)
    print(f'  Paper {WINDOW}거래일 기준 달성 가능성 — 과거 창으로 미리 센다')
    print('=' * 78)
    print(f'  기준: 거래>={PASS_TRADES} · PF>={PASS_PF} · MDD>{PASS_MDD}%')
    print(f'  전체 {len(days)}거래일 → {len(days)-WINDOW+1}개 창\n')

    out = {}
    for n in ('OPS', 'E', 'G'):
        sig = ParamCandidateGen(**pf.gen_kwargs(n)).scan(data)
        res = PortfolioBacktest(data, sig, days, sel_top(pf.max_selected(n)),
                                pf.max_selected(n), 0).run(n)

        # 청산일 기준으로 거래를 날짜 축에 올린다
        by_day = {}
        for t in res.trades:
            by_day.setdefault(t.exit_date, []).append(t)

        eq = res.equity
        rows = []
        for s in range(len(days) - WINDOW + 1):
            win = days[s:s + WINDOW]
            ws = {str(d.date()) for d in win}
            tr = [t for d in ws for t in by_day.get(d, [])]
            sub = eq.loc[win[0]:win[-1]]
            dd = ((sub - sub.cummax()) / sub.cummax()).min() * 100
            if tr:
                w = np.array([t.pnl_won for t in tr])
                gp, gl = w[w > 0].sum(), -w[w <= 0].sum()
                p = gp / gl if gl > 0 else np.inf
            else:
                p = np.nan
            rows.append({'n': len(tr), 'pf': p, 'mdd': dd})

        df = pd.DataFrame(rows)
        n_ok_tr = int((df['n'] >= PASS_TRADES).sum())
        n_ok_mdd = int((df['mdd'] > PASS_MDD).sum())
        both = df[(df['n'] >= PASS_TRADES)]
        n_all = int(((df['n'] >= PASS_TRADES) & (df['pf'] >= PASS_PF)
                     & (df['mdd'] > PASS_MDD)).sum())
        tot = len(df)

        print(f'  {n}  ({pf.VERSION[n]})')
        print(f'    20일 창 거래 수   평균 {df["n"].mean():.1f}   '
              f'중앙값 {df["n"].median():.0f}   최대 {df["n"].max()}')
        print(f'    거래>={PASS_TRADES} 충족 창   {n_ok_tr:4d}/{tot} '
              f'({n_ok_tr/tot*100:5.1f}%)')
        print(f'    MDD>{PASS_MDD}% 충족 창  {n_ok_mdd:4d}/{tot} '
              f'({n_ok_mdd/tot*100:5.1f}%)')
        print(f'    3기준 동시 충족    {n_all:4d}/{tot} '
              f'({n_all/tot*100:5.1f}%)')
        if len(both):
            print(f'    (거래 10건 이상인 창의 PF 중앙값 '
                  f'{both["pf"].replace(np.inf, np.nan).median():.3f})')
        print()

        out[n] = {'version': pf.VERSION[n], 'windows': tot,
                  'trades_mean': round(float(df['n'].mean()), 2),
                  'trades_median': float(df['n'].median()),
                  'trades_max': int(df['n'].max()),
                  'pass_trades_pct': round(n_ok_tr / tot * 100, 1),
                  'pass_mdd_pct': round(n_ok_mdd / tot * 100, 1),
                  'pass_all_pct': round(n_all / tot * 100, 1)}

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'window_trading_days': WINDOW,
                   'criteria': {'trades': PASS_TRADES, 'pf': PASS_PF,
                                'mdd': PASS_MDD},
                   'profiles': out,
                   'note': '과거 데이터를 20거래일 창으로 잘라 센 값이다. '
                           'Paper 를 돌렸을 때 기대되는 통과 확률의 근사치.'},
                  f, ensure_ascii=False, indent=2)
    print(f'  저장: {OUT}')


if __name__ == '__main__':
    main()
