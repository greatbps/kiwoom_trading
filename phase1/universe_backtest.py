"""
Iteration 7-2 — 유니버스 크기별 백테스트

━━━ 독립변수는 유니버스 크기 하나뿐이다 ━━━━━━━━━━━━━━━━━━━━━━━

  CHoCH 알고리즘 · BEST_CONFIG · BEST_ADAPTER_KWARGS · 청산 · 리스크 ·
  Top-3 상한 — 전부 고정. 종목 수만 78 → 120 → 160 → 220 으로 바꾼다.

사용법:
    python -m phase1.universe_backtest
"""
from __future__ import annotations

import csv
import json
import os
import pickle
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest.adapter import SMCAdapter
from backtest.daily_scan import BEST_ADAPTER_KWARGS, BEST_CONFIG
from phase0 import data_cache as dc
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_top

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'universe')
TIERS_JSON = os.path.join(OUT, 'tiers.json')
CACHE_PKL = os.path.join(OUT, 'ohlcv_expanded.pkl')

# 전 계층 공통 — 절대 바꾸지 않는다
EXIT_CASE3 = dict(sl_pct=-0.05, min_hold_bars=3, max_hold_bars=60)
TOP_N = 3
START, END = dc.START, dc.END
MONTHS = 24.0


def _stage_adapters():
    k = dict(BEST_ADAPTER_KWARGS)
    return [
        ('CHoCH', SMCAdapter(BEST_CONFIG, **{**k, 'require_volume': False,
                                             'atr_pct_min': None,
                                             'atr_pct_max': None,
                                             'require_ma50_trend': False})),
        ('RVOL', SMCAdapter(BEST_CONFIG, **{**k, 'atr_pct_min': None,
                                            'atr_pct_max': None,
                                            'require_ma50_trend': False})),
        ('ATR', SMCAdapter(BEST_CONFIG, **{**k,
                                           'require_ma50_trend': False})),
        ('MA50', SMCAdapter(BEST_CONFIG, **k)),
    ]


def main():
    with open(TIERS_JSON, encoding='utf-8') as f:
        tiers = json.load(f)['tiers']
    with open(CACHE_PKL, 'rb') as f:
        full = pickle.load(f)

    adapters = _stage_adapters()
    results, funnels = [], []

    for size in ('78', '120', '160', '220'):
        codes = [c for c in tiers[size] if c in full]
        data = {c: full[c] for c in codes}
        idx = pd.DatetimeIndex([])
        for df in data.values():
            idx = idx.union(df.index)
        days = [d for d in idx if START <= str(d.date()) <= END]
        dayset = set(days)

        # ── Funnel + Scan time ──────────────────────────────────────────
        t_all, stage_n = [], defaultdict(int)
        n_cand = 0
        for d in days:
            t0 = time.perf_counter()
            for sym, df in data.items():
                if d not in df.index:
                    continue
                i = df.index.get_loc(d)
                if i < 60:
                    continue
                n_cand += 1
                for name, ad in adapters:
                    try:
                        if ad.get_signal(df, i) == 'BUY':
                            stage_n[name] += 1
                    except Exception:
                        pass
            t_all.append(time.perf_counter() - t0)

        sig = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)
        n_sig = sum(1 for v in sig.values() for x in v if x in dayset)

        pb = PortfolioBacktest(data, sig, days, sel_top(TOP_N), TOP_N, 0)
        for k_, v in EXIT_CASE3.items():
            setattr(pb.engine, k_, v)
        k = kpi(pb.run(f'U{size}'))

        r = {'universe': int(size), 'symbols': len(codes),
             'candidates': n_cand,
             'choch': stage_n['CHoCH'], 'rvol': stage_n['RVOL'],
             'atr': stage_n['ATR'], 'ma50': stage_n['MA50'],
             'signals': n_sig,
             'trades': k.get('trades', 0),
             'win_rate': k.get('win_rate', 0),
             'profit_factor': k.get('profit_factor', 0),
             'avg_win_pct': k.get('avg_win_pct', 0),
             'avg_loss_pct': k.get('avg_loss_pct', 0),
             'total_return_pct': k.get('total_return_pct', 0),
             'mdd_pct': k.get('mdd_pct', 0),
             'expectancy_pct': k.get('expectancy_pct', 0),
             'max_consecutive_loss': k.get('max_consecutive_loss', 0),
             'avg_hold_bars': k.get('avg_hold_bars', 0),
             'trades_per_month': round(k.get('trades', 0) / MONTHS, 2),
             'scan_avg': round(statistics.mean(t_all), 4),
             'scan_max': round(max(t_all), 4),
             'scan_p95': round(sorted(t_all)[int(len(t_all) * 0.95) - 1], 4)}
        results.append(r)

        prev = n_cand
        for name in ('CHoCH', 'RVOL', 'ATR', 'MA50'):
            n = stage_n[name]
            funnels.append({'universe': size, 'stage': name, 'passed': n,
                            'removed': prev - n,
                            'removed_pct': round((prev - n) / prev * 100, 2)
                            if prev else 0})
            prev = n
        funnels.append({'universe': size, 'stage': 'Top3/Trades',
                        'passed': r['trades'],
                        'removed': n_sig - r['trades'],
                        'removed_pct': round((n_sig - r['trades']) /
                                             n_sig * 100, 2) if n_sig else 0})

        # ── 터미널 출력 (§8 형식) ───────────────────────────────────────
        print('\n==============================')
        print('UNIVERSE BACKTEST RESULT')
        print('==============================')
        print(f'Universe Size          {size} ({len(codes)}종목)')
        print(f'Candidate Count        {n_cand:,}')
        print(f'CHoCH                  {stage_n["CHoCH"]:,}')
        print(f'RVOL                   {stage_n["RVOL"]:,}')
        print(f'ATR                    {stage_n["ATR"]:,}')
        print(f'MA50                   {stage_n["MA50"]:,}')
        print(f'Trades                 {r["trades"]}')
        print(f'Win Rate               {r["win_rate"]}%')
        print(f'Profit Factor          {r["profit_factor"]}')
        print(f'Average Win            {r["avg_win_pct"]}%')
        print(f'Average Loss           {r["avg_loss_pct"]}%')
        print(f'MDD                    {r["mdd_pct"]}%')
        print(f'Average Trades / Month {r["trades_per_month"]}')

    # ── §9 비교 리포트 ──────────────────────────────────────────────────
    b = results[0]
    print('\n' + '=' * 88)
    print('  UNIVERSE COMPARISON')
    print('=' * 88)
    print(f'  {"Universe":<10}{"Trade":>7}{"WR%":>8}{"PF":>8}{"MDD%":>9}'
          f'{"Avg Trades/Month":>19}')
    for r in results:
        print(f'  {r["universe"]:<10}{r["trades"]:>7}{r["win_rate"]:>8.1f}'
              f'{r["profit_factor"]:>8.3f}{r["mdd_pct"]:>9.2f}'
              f'{r["trades_per_month"]:>19.2f}')

    print(f'\n  기준(78) 대비 변화율')
    print(f'  {"Universe":<10}{"Trade 증가":>12}{"PF 변화":>11}'
          f'{"WR 변화":>11}{"MDD 변화":>12}')
    for r in results[1:]:
        dt = (r['trades'] / b['trades'] - 1) * 100 if b['trades'] else 0
        dp = (r['profit_factor'] / b['profit_factor'] - 1) * 100 \
            if b['profit_factor'] else 0
        dw = r['win_rate'] - b['win_rate']
        dm = r['mdd_pct'] - b['mdd_pct']
        r.update(trade_delta_pct=round(dt, 1), pf_delta_pct=round(dp, 1),
                 wr_delta_pp=round(dw, 1), mdd_delta_pp=round(dm, 2))
        print(f'  {r["universe"]:<10}{dt:>+11.1f}%{dp:>+10.1f}%'
              f'{dw:>+10.1f}pp{dm:>+11.2f}pp')

    # ── §10 Funnel 비교 ─────────────────────────────────────────────────
    print('\n' + '=' * 88)
    print('  FUNNEL COMPARISON')
    print('=' * 88)
    print(f'  {"Universe":<10}{"Candidates":>12}{"CHoCH":>9}{"RVOL":>8}'
          f'{"ATR":>8}{"MA50":>8}{"Trades":>8}')
    for r in results:
        print(f'  {r["universe"]:<10}{r["candidates"]:>12,}{r["choch"]:>9}'
              f'{r["rvol"]:>8}{r["atr"]:>8}{r["ma50"]:>8}{r["trades"]:>8}')
    print(f'\n  단계별 제거율 (%)')
    print(f'  {"Universe":<10}{"CHoCH":>9}{"RVOL":>9}{"ATR":>9}{"MA50":>9}'
          f'{"Top3":>9}')
    for size in ('78', '120', '160', '220'):
        row = {f['stage']: f['removed_pct'] for f in funnels
               if f['universe'] == size}
        print(f'  {size:<10}{row.get("CHoCH",0):>8.2f}%{row.get("RVOL",0):>8.2f}%'
              f'{row.get("ATR",0):>8.2f}%{row.get("MA50",0):>8.2f}%'
              f'{row.get("Top3/Trades",0):>8.2f}%')

    # ── §11 성능 ────────────────────────────────────────────────────────
    print('\n' + '=' * 88)
    print('  SCAN PERFORMANCE')
    print('=' * 88)
    print(f'  {"Universe":<10}{"Avg(s)":>10}{"Max(s)":>10}{"95pct(s)":>11}'
          f'{"Avg 증가율":>12}')
    for r in results:
        inc = (r['scan_avg'] / b['scan_avg'] - 1) * 100 if b['scan_avg'] else 0
        r['scan_delta_pct'] = round(inc, 1)
        print(f'  {r["universe"]:<10}{r["scan_avg"]:>10.4f}{r["scan_max"]:>10.4f}'
              f'{r["scan_p95"]:>11.4f}{inc:>+11.1f}%')

    cols = list(results[0])
    with open(os.path.join(OUT, 'universe_backtest.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(results)
    with open(os.path.join(OUT, 'universe_funnel.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['universe', 'stage', 'passed',
                                          'removed', 'removed_pct'])
        w.writeheader()
        w.writerows(funnels)
    with open(os.path.join(OUT, 'universe_backtest.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'exit_profile': EXIT_CASE3, 'top_n': TOP_N,
                   'period': [START, END], 'results': results,
                   'funnel': funnels}, f, ensure_ascii=False, indent=2,
                  default=str)
    print(f'\n  저장: universe_backtest.csv · universe_funnel.csv · '
          f'universe_backtest.json')


if __name__ == '__main__':
    main()
