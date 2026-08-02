"""
Phase 0.1 부록 — Score 의 분해능

"Score Ranking 이 Random 과 구분되지 않는다" 는 결과가 나왔을 때
원인은 둘 중 하나다.

    (a) 점수는 잘 나뉘는데 그 순위가 수익률과 무관하다
    (b) 점수 자체가 후보들을 거의 나누지 못한다 (동점 천지)

(b) 라면 "순위를 매긴다" 는 말 자체가 성립하지 않는다 — 동점을 정렬하면
정렬 안정성에 따라 임의로 갈리고, 그건 정의상 Random 이다.
어느 쪽인지 먼저 확인해야 다음 작업이 갈린다.

사용법:
    python -m phase0.score_resolution
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from phase0 import data_cache as dc
from phase0.portfolio import PortfolioBacktest, sel_top


def main():
    data = dc.load_ohlcv()
    signals = dc.build_signals(data)
    days = dc.trading_days(data)

    pb = PortfolioBacktest(data, signals, days, sel_top(5), 5, 0)

    print('=' * 72)
    print('  Score 분해능 진단')
    print('=' * 72)

    # ── 전체 후보의 점수 분포 ────────────────────────────────────────────
    all_scores, multi_day_groups = [], []
    for d in days:
        c = [s for s in signals if d in signals[s]]
        if not c:
            continue
        sc = [pb._score(s, d) for s in c]
        all_scores += sc
        if len(c) > 1:
            multi_day_groups.append(sc)

    dist = Counter(round(x, 2) for x in all_scores)
    print(f'\n  후보 총 {len(all_scores)}건의 점수 분포')
    for v in sorted(dist):
        n = dist[v]
        print(f'    {v:>5.2f} : {n:4d}건 ({n/len(all_scores)*100:5.1f}%)  '
              f'{"█" * int(n / len(all_scores) * 50)}')
    print(f'\n    서로 다른 점수값: {len(dist)}개   '
          f'표준편차 {np.std(all_scores):.3f}')

    # ── 같은 날 후보끼리 갈리는가 ────────────────────────────────────────
    print(f'\n  후보가 2개 이상인 날: {len(multi_day_groups)}일')
    tied = sum(1 for g in multi_day_groups if len(set(g)) == 1)
    print(f'    그중 전원 동점: {tied}일 '
          f'({tied/max(1,len(multi_day_groups))*100:.1f}%)')
    print('    → 동점이면 정렬 순서가 임의로 갈린다. 정의상 Random 이다.')

    # ── 1등이 정말 더 벌었는가 ───────────────────────────────────────────
    r = PortfolioBacktest(data, signals, days, sel_top(5), None, 0).run('all')
    df = pd.DataFrame([{'rank': t.rank, 'score': t.score, 'pnl': t.pnl_pct,
                        'ncand': t.n_candidates} for t in r.trades])

    print(f'\n  점수 구간별 실제 성과 (상한 없이 전 후보를 잡은 경우, '
          f'{len(df)}건)')
    print(f'    {"점수":>6}{"건수":>6}{"승률%":>8}{"평균수익%":>10}')
    for v, g in df.groupby(df['score'].round(2)):
        print(f'    {v:>6.2f}{len(g):>6}{(g["pnl"] > 0).mean()*100:>8.1f}'
              f'{g["pnl"].mean()*100:>10.2f}')

    if df['score'].nunique() > 1:
        c = np.corrcoef(df['score'], df['pnl'])[0, 1]
        print(f'\n    점수 ↔ 수익률 상관계수: {c:+.3f}')
        print('    (0 근처면 점수가 높다고 더 버는 것이 아니라는 뜻이다)')

    multi = df[df['ncand'] > 1]
    if len(multi):
        print(f'\n  후보가 여럿이던 날의 등수별 성과 ({len(multi)}건)')
        print(f'    {"등수":>6}{"건수":>6}{"승률%":>8}{"평균수익%":>10}')
        for v, g in multi.groupby('rank'):
            print(f'    {v:>6}{len(g):>6}{(g["pnl"] > 0).mean()*100:>8.1f}'
                  f'{g["pnl"].mean()*100:>10.2f}')


if __name__ == '__main__':
    main()
