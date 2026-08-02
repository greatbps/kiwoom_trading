"""
Iteration 8 — Entry Parity: Pullback(Live) vs CHoCH(Backtest)

    A. Pullback Entry   analyzers/swing/signal_engine.SignalEngine (Live 실물)
    B. CHoCH Entry      backtest/adapter.SMCAdapter (백테스트 실물)

청산·리스크·포트폴리오는 **동일**하게 두고 진입만 바꾼다.
청산은 Iteration 7-2 에서 채택 후보로 정해진 Case3 프로파일을 쓴다.

━━━ 슬롯 상한을 함께 본다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Live swing_runner 에는 Top-3 상한이 있다. 상한 없이 비교하면
  "규칙이 좋은가" 가 아니라 "몇 건 잡았는가" 를 비교하게 된다.
  상한 없음 / Top-3 둘 다 낸다.

사용법:
    python -m phase1.entry_parity
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase0 import data_cache as dc
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import (PortfolioBacktest, kpi, sel_baseline, sel_top)
from phase1.swing_entry_adapter import SwingEntryAdapter

OUT = os.path.dirname(os.path.abspath(__file__))

# Iteration 7-2 Case3 — 진입 비교의 공통 청산 조건
EXIT_CASE3 = dict(sl_pct=-0.05, min_hold_bars=3, max_hold_bars=60)

WF = [('전체', dc.START, dc.END),
      ('Train', '2024-08-01', '2025-07-31'),
      ('Validation', '2025-08-01', '2026-01-31'),
      ('Test', '2026-02-01', '2026-07-30')]

GOAL = {'profit_factor': 1.5, 'win_rate': 40.0, 'mdd': -15.0}


def _run(data, sig, days, slots, name):
    selr = sel_baseline if slots is None else sel_top(slots)
    pb = PortfolioBacktest(data, sig, days, selr, slots, 0)
    for k, v in EXIT_CASE3.items():
        setattr(pb.engine, k, v)
    return kpi(pb.run(name))


HDR = (f'  {"케이스":<28}{"후보":>6}{"거래":>6}{"승률%":>7}{"PF":>8}'
       f'{"기대값%":>9}{"수익%":>9}{"MDD%":>9}{"보유":>7}{"연속손실":>7}')


def _line(k, name, nc):
    return (f'  {name:<28}{nc:>6}{k["trades"]:>6}{k["win_rate"]:>7.1f}'
            f'{k["profit_factor"]:>8.3f}{k["expectancy_pct"]:>9.3f}'
            f'{k["total_return_pct"]:>9.2f}{k["mdd_pct"]:>9.2f}'
            f'{k["avg_hold_bars"]:>7.1f}{k["max_consecutive_loss"]:>7}')


def main():
    data = dc.load_ohlcv()
    all_days = dc.trading_days(data)
    dayset = set(all_days)

    print('=' * 100)
    print('  Iteration 8 — Entry Parity: Pullback(Live) vs CHoCH(Backtest)')
    print('=' * 100)
    print(f'  기간 {dc.START} ~ {dc.END}   {len(all_days)}거래일   '
          f'{len(data)}종목')
    print(f'  공통 청산 (Case3): SL {EXIT_CASE3["sl_pct"]*100:.0f}% · '
          f'min_hold {EXIT_CASE3["min_hold_bars"]}봉 · '
          f'max_hold {EXIT_CASE3["max_hold_bars"]}봉 · 오버나이트 허용')

    print('\n  [신호 생성]')
    sig_b = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)
    nb = sum(1 for v in sig_b.values() for d in v if d in dayset)
    print(f'    B. CHoCH   {nb:>5}건 / {len(sig_b)}종목')

    sig_a, detail = SwingEntryAdapter().scan(data)
    na = sum(1 for v in sig_a.values() for d in v if d in dayset)
    print(f'    A. Pullback {na:>4}건 / {len(sig_a)}종목   '
          f'(CHoCH 대비 {na/max(1,nb):.1f}배)')

    overlap = sum(1 for s in set(sig_a) & set(sig_b)
                  for d in (sig_a[s] & sig_b[s]) if d in dayset)
    print(f'    두 규칙이 같은 날 같은 종목에 신호: {overlap}건 '
          f'({overlap/max(1,nb)*100:.1f}% of CHoCH)')

    rows = []
    for slots, tag in ((None, '상한 없음'), (3, 'Top-3 (Live 상한)')):
        print('\n' + '-' * 100)
        print(f'  슬롯 {tag}')
        print('-' * 100)
        print(HDR)
        for seg, lo, hi in WF:
            days = [d for d in all_days if lo <= str(d.date()) <= hi]
            ds = set(days)
            for name, sig in (('A. Pullback (Live)', sig_a),
                              ('B. CHoCH (Backtest)', sig_b)):
                nc = sum(1 for v in sig.values() for d in v if d in ds)
                k = _run(data, sig, days, slots, name)
                if not k.get('trades'):
                    print(f'  {seg} {name:<20}{nc:>6}     0  (거래 없음)')
                    continue
                k.update(entry=name, segment=seg, slots=tag, candidates=nc)
                rows.append(k)
                print(_line(k, f'{seg} · {name}', nc))

    # ── 성공 기준 ────────────────────────────────────────────────────────
    print('\n' + '-' * 100)
    print('  성공 기준: PF>=1.5 · WR>=40% · MDD>=-15%')
    print('-' * 100)
    print(f'  {"케이스":<40}{"PF":>8}{"WR%":>8}{"MDD%":>9}   판정')
    for r in rows:
        if r['segment'] != '전체':
            continue
        miss = []
        if r['profit_factor'] < GOAL['profit_factor']:
            miss.append('PF')
        if r['win_rate'] < GOAL['win_rate']:
            miss.append('WR')
        if r['mdd_pct'] < GOAL['mdd']:
            miss.append('MDD')
        r['pass'] = not miss
        print(f'  {r["slots"] + " · " + r["entry"]:<40}'
              f'{r["profit_factor"]:>8.3f}{r["win_rate"]:>8.1f}'
              f'{r["mdd_pct"]:>9.2f}   '
              f'{"✅ PASS" if not miss else "❌ " + "·".join(miss)}')

    # ── 구간 안정성 ─────────────────────────────────────────────────────
    print('\n' + '-' * 100)
    print('  구간 안정성 (Top-3)')
    print('-' * 100)
    print(f'  {"진입 규칙":<24}{"Train PF":>10}{"Val PF":>10}{"Test PF":>10}')
    for name in ('A. Pullback (Live)', 'B. CHoCH (Backtest)'):
        pf = {}
        for r in rows:
            if r['entry'] == name and r['slots'].startswith('Top-3'):
                pf[r['segment']] = r['profit_factor']
        print(f'  {name:<24}{pf.get("Train", 0):>10.3f}'
              f'{pf.get("Validation", 0):>10.3f}{pf.get("Test", 0):>10.3f}')

    cols = ['entry', 'slots', 'segment', 'candidates', 'trades', 'win_rate',
            'profit_factor', 'expectancy_pct', 'total_return_pct', 'mdd_pct',
            'avg_hold_bars', 'max_consecutive_loss', 'total_pnl_won']
    with open(os.path.join(OUT, 'entry_parity.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, 'entry_parity.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'exit_profile': EXIT_CASE3, 'goal': GOAL,
                   'signals': {'pullback': na, 'choch': nb,
                               'overlap': overlap},
                   'rows': rows}, f, ensure_ascii=False, indent=2, default=str)
    print('\n  저장: entry_parity.csv · entry_parity.json')


if __name__ == '__main__':
    main()
