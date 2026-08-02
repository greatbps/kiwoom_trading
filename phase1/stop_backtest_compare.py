"""
Phase 1 Iteration 6 / Phase 3 — 손절선 변경 전후 백테스트

Iteration 5 가 밝힌 것: 스윙 포지션의 **실효 손절선이 -12%** 였다
(structure_stop_price 유실 → swing_hard_stop_pct fallback).
설계값은 -1.40% ~ -5.34% 이고, `max_stop_pct=5.0` 으로 캡이 걸리므로
정상 동작 시 실효 손절선은 **-5% 이내**가 된다.

이 스크립트는 그 차이를 백테스트로 잰다.

    Before   sl_pct = -12%   (유실 상태)
    After    sl_pct = -5%    (구조손절 캡 = max_stop_pct)
    참고     -3% / -2%       (더 타이트한 구조손절)

━━━ 손절을 조이면 성격이 바뀐다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  손실은 줄지만 승률과 평균 보유기간도 함께 움직인다. "손실만 줄고
  나머지는 그대로" 를 기대하면 안 된다. 그래서 KPI 를 한 줄이 아니라
  표로 낸다.

사용법:
    python -m phase1.stop_backtest_compare
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase0 import data_cache as dc
from phase0 import portfolio as pf
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_baseline

OUT = os.path.dirname(os.path.abspath(__file__))

CASES = [
    ('Before  손절 -12% (유실 상태)', -0.12),
    ('After   손절 -5% (구조손절 캡)', -0.05),
    ('참고    손절 -3%', -0.03),
    ('참고    손절 -2%', -0.02),
]

WF = [('전체', dc.START, dc.END),
      ('Train', '2024-08-01', '2025-07-31'),
      ('Validation', '2025-08-01', '2026-01-31'),
      ('Test', '2026-02-01', '2026-07-30')]


def run(data, sig, days, sl, name):
    """
    ⚠️ EXIT_PROFILE 전역을 건드리면 이후 실험이 오염된다.
       엔진 인스턴스의 sl_pct 만 바꾼다.
    """
    pb = PortfolioBacktest(data, sig, days, sel_baseline, None, 0)
    pb.engine.sl_pct = sl
    return kpi(pb.run(name))


def main():
    data = dc.load_ohlcv()
    all_days = dc.trading_days(data)
    sig = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)

    print('=' * 88)
    print('  Phase 3 — 손절선 변경 전후 백테스트')
    print('=' * 88)
    print(f'  기간 {dc.START} ~ {dc.END}   {len(all_days)}거래일   '
          f'후보 {sum(1 for v in sig.values() for d in v if d in set(all_days))}건')
    print(f'  다른 조건은 전부 고정 (TP {pf.EXIT_PROFILE["tp_pct"]*100:.0f}% · '
          f'trail {pf.EXIT_PROFILE["trailing_pct"]*100:.0f}% · '
          f'BE {pf.EXIT_PROFILE["be_trigger_pct"]*100:.0f}% · '
          f'min_hold {pf.EXIT_PROFILE["min_hold_bars"]}봉)')

    rows = []
    for seg, lo, hi in WF:
        days = [d for d in all_days if lo <= str(d.date()) <= hi]
        print('\n' + '-' * 88)
        print(f'  {seg}   {lo} ~ {hi}   {len(days)}거래일')
        print('-' * 88)
        print(f'  {"케이스":<28}{"거래":>5}{"승률%":>7}{"PF":>8}{"기대값%":>9}'
              f'{"수익%":>9}{"MDD%":>9}{"평균보유":>8}{"연속손실":>7}')
        for name, sl in CASES:
            k = run(data, sig, days, sl, name)
            if not k.get('trades'):
                print(f'  {name:<28}{0:>5}   (거래 없음)')
                continue
            k.update(segment=seg, case=name, sl_pct=sl * 100)
            rows.append(k)
            print(f'  {name:<28}{k["trades"]:>5}{k["win_rate"]:>7.1f}'
                  f'{k["profit_factor"]:>8.3f}{k["expectancy_pct"]:>9.3f}'
                  f'{k["total_return_pct"]:>9.2f}{k["mdd_pct"]:>9.2f}'
                  f'{k["avg_hold_bars"]:>8.1f}'
                  f'{k["max_consecutive_loss"]:>7}')

    # 전체 구간 Before/After 델타
    a = next(r for r in rows if r['segment'] == '전체' and r['sl_pct'] == -12)
    b = next(r for r in rows if r['segment'] == '전체' and r['sl_pct'] == -5)
    print('\n' + '-' * 88)
    print('  Before(-12%) → After(-5%) 변화 · 전체 구간')
    print('-' * 88)
    for key, label, fmt in (('trades', '거래수', '{:+.0f}'),
                            ('win_rate', '승률', '{:+.1f}pp'),
                            ('profit_factor', 'PF', '{:+.3f}'),
                            ('expectancy_pct', '기대값', '{:+.3f}%p'),
                            ('total_return_pct', '총수익', '{:+.2f}%p'),
                            ('mdd_pct', 'MDD', '{:+.2f}%p'),
                            ('avg_hold_bars', '평균보유', '{:+.1f}봉'),
                            ('max_consecutive_loss', '최대연속손실', '{:+.0f}')):
        d = (b[key] or 0) - (a[key] or 0)
        print(f'  {label:<14}{a[key]:>10} → {b[key]:>10}   '
              f'{fmt.format(d)}')

    with open(os.path.join(OUT, 'stop_backtest_compare.csv'), 'w',
              newline='', encoding='utf-8-sig') as f:
        cols = ['segment', 'case', 'sl_pct', 'trades', 'win_rate',
                'profit_factor', 'expectancy_pct', 'total_return_pct',
                'mdd_pct', 'avg_hold_bars', 'max_consecutive_loss',
                'avg_win_pct', 'avg_loss_pct', 'total_pnl_won']
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, 'stop_backtest_compare.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'cases': [{'name': n, 'sl_pct': s * 100} for n, s in CASES],
                   'exit_profile_fixed': pf.EXIT_PROFILE,
                   'rows': rows}, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: stop_backtest_compare.csv · stop_backtest_compare.json')


if __name__ == '__main__':
    main()
