"""
Iteration 7-2 §6 — Exit 규칙 백테스트 비교

    Case 1  현재 Backtest Exit 유지
    Case 2  Live Exit 성격을 Backtest 에 이식
    Case 3  Swing 정의 Exit (구조손절 + TP + TRAIL + MAX_HOLD + 오버나이트 허용)

    5-1     Overnight  A(차단 유지) / B(허용) / C(조건부 — 손실일 때만 차단)

━━━ 일봉으로 Live 를 그대로 재현할 수 없다 ━━━━━━━━━━━━━━━━━━━━━━

  Live 청산의 38.4% 가 15:00~15:10 시간청산이고 19.0% 가 8.7분짜리
  조기실패컷이다. 분봉이 없으므로 **성격만 이식**한다.

      시간청산 · 오버나이트 차단  →  max_hold_bars = 1  (당일~1일 보유)
      조기실패컷(평균 -1.31%)     →  sl_pct = -0.02, min_hold_bars = 0

  숫자 자체를 Live 성과 예측으로 읽으면 안 된다. 규칙 성격이 성과에
  어떤 방향으로 작용하는지만 본다.

사용법:
    python -m phase1.exit_backtest_compare
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase0 import data_cache as dc
from phase0 import portfolio as pfmod
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_baseline

OUT = os.path.dirname(os.path.abspath(__file__))

BASE = dict(pfmod.EXIT_PROFILE)

CASES = {
    'Case1 현재 Exit 유지': dict(),
    'Case2 Live Exit 이식': dict(max_hold_bars=1, min_hold_bars=0,
                                sl_pct=-0.02),
    'Case3 Swing 정의 Exit': dict(max_hold_bars=60, min_hold_bars=3,
                                 sl_pct=-0.05),
}

OVERNIGHT = {
    'A 차단 유지 (당일청산)': dict(max_hold_bars=1, min_hold_bars=0),
    'B 허용 (스윙 정의)': dict(max_hold_bars=30, min_hold_bars=3),
    'C 조건부 (손실만 차단)': 'conditional',
}

# 성공 기준 (§7)
GOAL = {'win_rate': 40.0, 'profit_factor': 1.5, 'mdd': -15.0,
        'avg_hold': 5.0}


def _run(data, sig, days, over: dict, name: str, conditional=False):
    pb = PortfolioBacktest(data, sig, days, sel_baseline, None, 0)
    for k, v in over.items():
        setattr(pb.engine, k, v)
    if conditional:
        # ⚠️ 조건부 오버나이트 — 손실 중이면 하루 만에 끊고, 이익이면 보유.
        #    엔진 규칙을 고치지 않고 감싼다.
        _orig = pb.engine._check_swing_exit

        def _wrapped(position, close, high, low, chg, bars, ep):
            r, px = _orig(position, close, high, low, chg, bars, ep)
            if r:
                return r, px
            if bars >= 1 and chg < 0:
                return 'OVERNIGHT_BLOCK', close
            return None, close
        pb.engine._check_swing_exit = _wrapped
    return kpi(pb.run(name))


def _line(k, name):
    return (f'  {name:<26}{k["trades"]:>5}{k["win_rate"]:>7.1f}'
            f'{k["profit_factor"]:>8.3f}{k["expectancy_pct"]:>9.3f}'
            f'{k["total_return_pct"]:>9.2f}{k["mdd_pct"]:>9.2f}'
            f'{k["avg_hold_bars"]:>8.1f}{k["max_consecutive_loss"]:>7}')


HDR = (f'  {"케이스":<26}{"거래":>5}{"승률%":>7}{"PF":>8}{"기대값%":>9}'
       f'{"수익%":>9}{"MDD%":>9}{"평균보유":>8}{"연속손실":>7}')


def main():
    data = dc.load_ohlcv()
    days = dc.trading_days(data)
    sig = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)

    print('=' * 92)
    print('  Iteration 7-2 §6 — Exit 규칙 백테스트 비교')
    print('=' * 92)
    print(f'  기간 {dc.START} ~ {dc.END}   {len(days)}거래일   '
          f'후보 {sum(1 for v in sig.values() for d in v if d in set(days))}건')
    print('  ⚠️ 일봉 근사다. 분봉이 없어 Live 의 15:00 청산·8.7분 컷을 '
          '그대로 재현할 수 없다.')

    rows = []
    print('\n' + '-' * 92)
    print('  §6 Exit 규칙 3안')
    print('-' * 92)
    print(HDR)
    for name, over in CASES.items():
        k = _run(data, sig, days, over, name)
        k['case'] = name
        k['params'] = {**{a: BASE[a] for a in
                          ('sl_pct', 'min_hold_bars', 'max_hold_bars')},
                       **over}
        rows.append(k)
        print(_line(k, name))

    print('\n' + '-' * 92)
    print('  5-1 Overnight 처리 3안')
    print('-' * 92)
    print(HDR)
    for name, over in OVERNIGHT.items():
        if over == 'conditional':
            k = _run(data, sig, days, dict(max_hold_bars=30, min_hold_bars=3),
                     name, conditional=True)
        else:
            k = _run(data, sig, days, over, name)
        k['case'] = name
        rows.append(k)
        print(_line(k, name))

    # ── 성공 기준 대조 ───────────────────────────────────────────────────
    print('\n' + '-' * 92)
    print('  §7 성공 기준 대조')
    print('-' * 92)
    print(f'  기준: 승률>={GOAL["win_rate"]}%  PF>={GOAL["profit_factor"]}  '
          f'MDD>={GOAL["mdd"]}%  평균보유>={GOAL["avg_hold"]}일')
    print(f'  {"케이스":<26}{"승률":>7}{"PF":>8}{"MDD":>9}{"보유":>7}   판정')
    for k in rows:
        miss = []
        if k['win_rate'] < GOAL['win_rate']:
            miss.append('승률')
        if k['profit_factor'] < GOAL['profit_factor']:
            miss.append('PF')
        if k['mdd_pct'] < GOAL['mdd']:
            miss.append('MDD')
        if k['avg_hold_bars'] < GOAL['avg_hold']:
            miss.append('보유')
        k['pass'] = not miss
        k['missed'] = miss
        print(f'  {k["case"]:<26}{k["win_rate"]:>7.1f}'
              f'{k["profit_factor"]:>8.3f}{k["mdd_pct"]:>9.2f}'
              f'{k["avg_hold_bars"]:>7.1f}   '
              f'{"✅ PASS" if not miss else "❌ " + "·".join(miss)}')

    cols = ['case', 'trades', 'win_rate', 'profit_factor', 'expectancy_pct',
            'total_return_pct', 'mdd_pct', 'avg_hold_bars',
            'max_consecutive_loss', 'avg_win_pct', 'avg_loss_pct',
            'total_pnl_won', 'pass']
    with open(os.path.join(OUT, 'exit_backtest_compare.csv'), 'w',
              newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, 'exit_backtest_compare.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'base_profile': BASE, 'goal': GOAL, 'rows': rows},
                  f, ensure_ascii=False, indent=2, default=str)
    print('\n  저장: exit_backtest_compare.csv · exit_backtest_compare.json')


if __name__ == '__main__':
    main()
