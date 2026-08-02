"""
Iteration 9 Phase 6 — 거래량 확대: 독립 신호 조사

⚠️ **CHoCH 조건을 완화하지 않는다.** Phase 0.2~0.3 에서 완화는 전부
   실패했다 (MA50 완화 · Volume 완화 · ATR 확대 · RVOL 1.35 과최적화).

대신 CHoCH 와 **독립적인** 신호를 붙여 거래를 늘릴 수 있는지 본다.

    BOS                     추세 지속 돌파
    Liquidity Sweep         유동성 사냥 후 회복
    Order Block             OB 되돌림 진입
    False Breakout          가짜 돌파 후 반전
    Spring (Wyckoff)        지지 이탈 후 즉시 회복
    Volatility Compression  변동성 수축 후 확장

각 신호에 대해 측정:
    PF · 승률 · MDD · CHoCH 와의 중복률 · 증분 거래수

기각 기준 (하나라도 미달 → 즉시 기각):
    PF >= 1.5 · WR >= 40% · MDD >= -15%

━━━ 이 조사는 탐색이지 채택이 아니다 ━━━━━━━━━━━━━━━━━━━━━━━━━━

  통과한 신호가 있어도 곧바로 붙이면 안 된다. 같은 데이터에서
  6개를 재는 순간 다중비교 문제가 생긴다 — 우연히 하나는 좋아 보인다.
  통과분은 **별도 out-of-sample 검증 대상**으로 올린다.

사용법:
    python -m phase1.independent_signals
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from phase0 import data_cache as dc
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_top

OUT = os.path.dirname(os.path.abspath(__file__))
EXIT_CASE3 = dict(sl_pct=-0.05, min_hold_bars=3, max_hold_bars=60)
SLOTS = 3
GOAL = {'pf': 1.5, 'wr': 40.0, 'mdd': -15.0}

W = 60          # 관측 창
MIN_BARS = 60


# ── 신호 정의 ────────────────────────────────────────────────────────────
#
# 전부 df.iloc[:i] (확정봉)만 본다. i 봉은 아직 진행 중으로 취급한다.
def _win(df, i):
    return df.iloc[max(0, i - W): i]


def _rvol(w):
    v = w['volume']
    if len(v) < 21:
        return 0.0
    avg = float(v.iloc[-21:-1].mean())
    return float(v.iloc[-1]) / avg if avg else 0.0


def sig_bos(df, i):
    """직전 20봉 고점을 종가로 돌파 + 거래량 확인."""
    w = _win(df, i)
    if len(w) < 25:
        return False
    hi = float(w['high'].iloc[-21:-1].max())
    return float(w['close'].iloc[-1]) > hi and _rvol(w) >= 1.5


def sig_sweep(df, i):
    """직전 20봉 저점을 꼬리로 이탈했다가 종가는 위로 복귀."""
    w = _win(df, i)
    if len(w) < 25:
        return False
    lo = float(w['low'].iloc[-21:-1].min())
    r = w.iloc[-1]
    return float(r['low']) < lo and float(r['close']) > lo


def sig_order_block(df, i):
    """
    직전 강한 상승봉(OB)의 시가대로 되돌아왔다가 반등 마감.
    """
    w = _win(df, i)
    if len(w) < 10:
        return False
    body = (w['close'] - w['open']) / w['open']
    idx = body.iloc[-10:-2]
    if len(idx) == 0 or idx.max() < 0.03:
        return False
    ob = w.iloc[-10:-2].loc[idx.idxmax()]
    r = w.iloc[-1]
    return (float(r['low']) <= float(ob['open'])
            and float(r['close']) > float(ob['open']))


def sig_false_breakout(df, i):
    """직전 봉이 20봉 고점을 넘었다가 당봉이 그 아래로 되밀린 뒤 반등."""
    w = _win(df, i)
    if len(w) < 25:
        return False
    hi = float(w['high'].iloc[-22:-2].max())
    p, r = w.iloc[-2], w.iloc[-1]
    return (float(p['high']) > hi and float(p['close']) < hi
            and float(r['close']) > float(p['close']))


def sig_spring(df, i):
    """20봉 저점 이탈 후 2봉 안에 회복 (Wyckoff Spring)."""
    w = _win(df, i)
    if len(w) < 25:
        return False
    lo = float(w['low'].iloc[-23:-3].min())
    last3 = w.iloc[-3:]
    return bool((last3['low'] < lo).any()
                and float(w['close'].iloc[-1]) > lo)


def sig_vol_compression(df, i):
    """
    변동성 수축(최근 ATR% 가 직전 대비 60% 이하) 후 상승 확장.
    """
    w = _win(df, i)
    if len(w) < 40:
        return False
    tr = (w['high'] - w['low']) / w['close']
    recent, prior = float(tr.iloc[-10:].mean()), float(tr.iloc[-30:-10].mean())
    if prior <= 0 or recent / prior > 0.6:
        return False
    return float(w['close'].iloc[-1]) > float(w['close'].iloc[-6])


SIGNALS = {
    'BOS': sig_bos,
    'Liquidity Sweep': sig_sweep,
    'Order Block': sig_order_block,
    'False Breakout': sig_false_breakout,
    'Spring (Wyckoff)': sig_spring,
    'Volatility Compression': sig_vol_compression,
}


def scan(data, fn, dayset):
    out = {}
    for sym, df in data.items():
        got = {df.index[i] for i in range(MIN_BARS, len(df))
               if df.index[i] in dayset and fn(df, i)}
        if got:
            out[sym] = got
    return out


def main():
    data = dc.load_ohlcv()
    days = dc.trading_days(data)
    dayset = set(days)
    choch = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)
    n_choch = sum(1 for v in choch.values() for d in v if d in dayset)

    print('=' * 96)
    print('  Phase 6 — 독립 신호 조사 (CHoCH 는 건드리지 않는다)')
    print('=' * 96)
    print(f'  기준 CHoCH {n_choch}건   공통 청산 Case3   슬롯 Top-{SLOTS}')
    print(f'  기각 기준: PF<{GOAL["pf"]} 또는 WR<{GOAL["wr"]}% '
          f'또는 MDD<{GOAL["mdd"]}% → 즉시 기각')

    print(f'\n  {"신호":<24}{"신호수":>7}{"거래":>6}{"승률%":>7}{"PF":>8}'
          f'{"MDD%":>8}{"CHoCH중복":>10}{"증분거래":>9}   판정')
    rows = []
    base_k = _kpi(data, choch, days, 'CHoCH')
    for name, fn in SIGNALS.items():
        sig = scan(data, fn, dayset)
        n = sum(len(v) for v in sig.values())
        if n == 0:
            print(f'  {name:<24}{0:>7}   신호 없음')
            continue
        k = _kpi(data, sig, days, name)
        if not k.get('trades'):
            print(f'  {name:<24}{n:>7}{0:>6}   거래 없음')
            continue

        # CHoCH 와 겹치는 신호 비율
        ov = sum(1 for s in sig for d in (sig[s] & choch.get(s, set())))
        ov_pct = ov / n * 100

        # 합집합을 붙였을 때 늘어나는 거래 수
        union = {s: sig.get(s, set()) | choch.get(s, set())
                 for s in set(sig) | set(choch)}
        k_u = _kpi(data, union, days, f'{name}+CHoCH')
        inc = k_u['trades'] - base_k['trades']

        miss = []
        if k['profit_factor'] < GOAL['pf']:
            miss.append('PF')
        if k['win_rate'] < GOAL['wr']:
            miss.append('WR')
        if k['mdd_pct'] < GOAL['mdd']:
            miss.append('MDD')
        rows.append({'signal': name, 'signals': n, 'trades': k['trades'],
                     'win_rate': k['win_rate'],
                     'profit_factor': k['profit_factor'],
                     'mdd_pct': k['mdd_pct'],
                     'choch_overlap_pct': round(ov_pct, 1),
                     'incremental_trades': inc,
                     'union_pf': k_u['profit_factor'],
                     'union_wr': k_u['win_rate'],
                     'union_mdd': k_u['mdd_pct'],
                     'verdict': 'PASS' if not miss else '기각: ' + '·'.join(miss)})
        print(f'  {name:<24}{n:>7}{k["trades"]:>6}{k["win_rate"]:>7.1f}'
              f'{k["profit_factor"]:>8.3f}{k["mdd_pct"]:>8.2f}'
              f'{ov_pct:>9.1f}%{inc:>9}   '
              f'{"✅ PASS" if not miss else "❌ " + "·".join(miss)}')

    print(f'\n  참고) CHoCH 단독: 거래 {base_k["trades"]} · '
          f'승률 {base_k["win_rate"]}% · PF {base_k["profit_factor"]} · '
          f'MDD {base_k["mdd_pct"]}%')

    passed = [r for r in rows if r['verdict'] == 'PASS']
    print(f'\n  통과 {len(passed)}/{len(rows)}')
    if passed:
        print('  ⚠️ 통과했다고 바로 붙이면 안 된다. 같은 데이터에서 6개를 재면')
        print('     우연히 하나는 좋아 보인다 (다중비교). out-of-sample 검증 대상으로만 올린다.')
        print(f'\n  {"신호":<24}{"합집합 PF":>10}{"합집합 WR":>10}'
              f'{"합집합 MDD":>11}{"증분거래":>9}')
        for r in passed:
            print(f'  {r["signal"]:<24}{r["union_pf"]:>10.3f}'
                  f'{r["union_wr"]:>10.1f}{r["union_mdd"]:>11.2f}'
                  f'{r["incremental_trades"]:>9}')

    with open(os.path.join(OUT, 'independent_signals.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    with open(os.path.join(OUT, 'independent_signals.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'baseline_choch': base_k, 'goal': GOAL, 'rows': rows,
                   'note': '탐색 결과다. 다중비교 보정 없이 채택하면 안 된다.'},
                  f, ensure_ascii=False, indent=2, default=str)
    print('\n  저장: independent_signals.csv · independent_signals.json')


def _kpi(data, sig, days, name):
    pb = PortfolioBacktest(data, sig, days, sel_top(SLOTS), SLOTS, 0)
    for k, v in EXIT_CASE3.items():
        setattr(pb.engine, k, v)
    return kpi(pb.run(name))


if __name__ == '__main__':
    main()
