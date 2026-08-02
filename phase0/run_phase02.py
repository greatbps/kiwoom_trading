"""
Phase 0.2 — Candidate Generation Optimization Validation

Phase 0.1 이 밝힌 것: 문제는 "무엇을 고를까" 가 아니라 "고를 것이 없다"
였다. 2년 117건. 여기서는 후보 생성 필터를 완화해 후보를 늘리되,
**늘어난 것이 쓸 만한 신호인지 쓰레기인지**를 함께 본다.

    A. 현행                      CHoCH + RVOL1.5 + ATR 2~8% + MA50 상승
    B. MA50 완화                 MA50 기울기 >= -2%
    C. Volume 완화               RVOL >= 1.2
    D. ATR 확대                  ATR 1.5~10%
    E. 조합                      B + C + D

━━━ 후보를 늘리는 것 자체는 성과가 아니다 ━━━━━━━━━━━━━━━━━━━━━━

  필터를 풀면 후보는 반드시 는다. 물어야 할 것은 "늘어난 후보의 PF 가
  기존 후보와 비교해 어떤가" 이다. 신규분 PF 가 1 미만이면 그 완화는
  거래 수만 늘리고 돈을 잃는 것이다. §3 에서 기존/신규를 분리해 본다.

사용법:
    python -m phase0.run_phase02
    python -m phase0.run_phase02 --seeds 30
"""
from __future__ import annotations

import argparse
import csv
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
from phase0.candidate_gen import CASES, ParamCandidateGen, verify_parity
from phase0.portfolio import (EXIT_PROFILE, INITIAL_CAPITAL, SLOT_DIVISOR,
                              PortfolioBacktest, kpi, sel_baseline, sel_random,
                              sel_top)

OUT = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(OUT, 'phase02_results.csv')
JSON_PATH = os.path.join(OUT, 'phase02_results.json')

# 합격 기준 (작업지시서)
MIN_CANDIDATES = 200
MIN_PF_RATIO = 0.85          # Baseline PF × 0.85
MDD_FLOOR = -15.0            # 이보다 나쁘면 탈락
PREF_PF = 1.70
PREF_CANDIDATES = 180


def _git(*a):
    try:
        return subprocess.run(['git', *a], capture_output=True, text=True,
                              cwd=os.path.dirname(OUT)).stdout.strip()
    except Exception:
        return 'UNKNOWN'


def in_range(sig: dict, days_set) -> int:
    return sum(1 for s in sig.values() for d in s if d in days_set)


def split_quality(trades, base_sig):
    """
    거래를 '기존 조건에서도 잡혔을 후보' 와 '완화로 새로 생긴 후보' 로 나눈다.

    기준은 신호일이다. 진입일로 나누면 하루 밀린 거래가 엉뚱한 쪽으로 간다.
    """
    old, new = [], []
    for t in trades:
        sd = pd.Timestamp(t.signal_date)
        (old if sd in base_sig.get(t.symbol, set()) else new).append(t)

    def stat(g):
        if not g:
            return {'trades': 0, 'win_rate': None, 'pf': None, 'avg_pct': None,
                    'total_won': 0}
        w = np.array([x.pnl_won for x in g])
        r = np.array([x.pnl_pct for x in g])
        gp, gl = w[w > 0].sum(), -w[w <= 0].sum()
        return {'trades': len(g),
                'win_rate': round((r > 0).mean() * 100, 1),
                'pf': round(gp / gl, 3) if gl > 0 else None,
                'avg_pct': round(r.mean() * 100, 2),
                'total_won': int(w.sum())}

    return stat(old), stat(new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=30)
    a = ap.parse_args()

    data = dc.load_ohlcv()
    cached = dc.build_signals(data)
    days = dc.trading_days(data)
    days_set = set(days)

    print('=' * 78)
    print('  Phase 0.2 — Candidate Generation Optimization')
    print('=' * 78)
    print(f'  기간 {dc.START} ~ {dc.END}   {len(days)}거래일   {len(data)}종목')

    # ── 패리티: 재구현이 운영과 같은가 ───────────────────────────────────
    ok, msg = verify_parity(data, cached)
    print(f'  패리티 검사 (Case A == 운영 신호): {"✅ " if ok else "❌ "}{msg}')
    if not ok:
        print('\n  중단한다. Case A 가 운영과 다르면 A~E 비교는 읽을 수 없다.')
        sys.exit(1)
    print()

    # ── 케이스별 후보 생성 + 백테스트 ────────────────────────────────────
    base_sig = None
    rows, detail = [], {}
    for name, kw in CASES.items():
        sig = ParamCandidateGen(**kw).scan(data)
        if base_sig is None:
            base_sig = sig
        n_cand = in_range(sig, days_set)

        res = PortfolioBacktest(data, sig, days, sel_baseline, None, 0).run(name)
        k = kpi(res)
        k['candidates'] = n_cand
        k['params'] = kw or 'ops default'
        old, new = split_quality(res.trades, base_sig)
        k['quality_old'], k['quality_new'] = old, new
        rows.append(k)
        detail[name] = res
        print(f'  · {name:<28} 후보 {n_cand:4d}  거래 {k.get("trades",0):4d}')

    base = rows[0]
    pf_gate = round(base['profit_factor'] * MIN_PF_RATIO, 3)

    # ── ① KPI 표 ────────────────────────────────────────────────────────
    print()
    print('-' * 78)
    print('  ① KPI 비교 (동일 Exit · 동일 Risk · 동일 Portfolio · 상한 없음)')
    print('-' * 78)
    h = (f'{"케이스":<28}{"후보":>5}{"거래":>5}{"승률%":>7}{"PF":>7}'
         f'{"수익%":>8}{"MDD%":>8}{"기대값%":>8}{"연속손실":>7}')
    print(h)
    print('  ' + '-' * (len(h) - 4))
    for k in rows:
        pf = k.get('profit_factor')
        print(f'{k["name"]:<28}{k["candidates"]:>5}{k.get("trades",0):>5}'
              f'{k.get("win_rate",0):>7.1f}{(f"{pf:.3f}" if pf else "-"):>7}'
              f'{k.get("total_return_pct",0):>8.2f}{k.get("mdd_pct",0):>8.2f}'
              f'{k.get("expectancy_pct",0):>8.3f}'
              f'{k.get("max_consecutive_loss",0):>7}')

    # ── ② 후보 품질: 기존 vs 신규 ────────────────────────────────────────
    print()
    print('-' * 78)
    print('  ② 후보 품질 — 완화로 새로 생긴 후보는 쓸 만한가')
    print('-' * 78)
    print('  기존 = Case A 에서도 나왔을 신호 / 신규 = 완화로 생긴 신호')
    print(f'  {"케이스":<28}{"기존건":>6}{"기존PF":>8}{"신규건":>7}{"신규PF":>8}'
          f'{"신규평균%":>10}{"신규손익":>12}')
    for k in rows[1:]:
        o, n = k['quality_old'], k['quality_new']
        print(f'  {k["name"]:<28}{o["trades"]:>6}'
              f'{(f"{o['pf']:.3f}" if o["pf"] else "-"):>8}'
              f'{n["trades"]:>7}{(f"{n['pf']:.3f}" if n["pf"] else "-"):>8}'
              f'{(n["avg_pct"] if n["avg_pct"] is not None else 0):>10.2f}'
              f'{n["total_won"]:>12,}')
    print('\n  ⚠️ 신규 PF < 1.0 이면 그 완화는 거래만 늘리고 돈을 잃는 것이다.')

    # ── ③ 합격 판정 ──────────────────────────────────────────────────────
    print()
    print('-' * 78)
    print('  ③ 합격 판정')
    print('-' * 78)
    print(f'  기준: 후보 >= {MIN_CANDIDATES}  AND  PF >= {pf_gate} '
          f'(Baseline {base["profit_factor"]} × {MIN_PF_RATIO})  '
          f'AND  MDD > {MDD_FLOOR}%')
    passed = []
    for k in rows:
        pf = k.get('profit_factor') or 0
        c1 = k['candidates'] >= MIN_CANDIDATES
        c2 = pf >= pf_gate
        c3 = k.get('mdd_pct', -99) > MDD_FLOOR
        okk = c1 and c2 and c3
        pref = pf >= PREF_PF and k['candidates'] >= PREF_CANDIDATES
        k['accept'] = okk
        k['preferred'] = pref
        if okk:
            passed.append(k)
        mark = '✅ 채택가능' if okk else '❌ 탈락'
        why = []
        if not c1:
            why.append(f'후보 {k["candidates"]}<{MIN_CANDIDATES}')
        if not c2:
            why.append(f'PF {pf:.3f}<{pf_gate}')
        if not c3:
            why.append(f'MDD {k.get("mdd_pct")}<={MDD_FLOOR}')
        print(f'  {k["name"]:<28}{mark:<12}'
              f'{("우선후보" if pref else ""):<8}{" · ".join(why)}')

    # ── ④ Score 재검증 (후보 200+ 확보 시) ───────────────────────────────
    score_recheck = {}
    rich = [k for k in rows if k['candidates'] >= MIN_CANDIDATES]
    print()
    print('-' * 78)
    print('  ④ Score 재검증 — 후보를 확보한 뒤에도 순위가 무의미한가')
    print('-' * 78)
    if not rich:
        print(f'  후보 {MIN_CANDIDATES}건 이상인 케이스가 없다 — 재검증 생략.')
    else:
        target = max(rich, key=lambda x: x['candidates'])
        sig = ParamCandidateGen(**(CASES[target['name']])).scan(data)
        print(f'  대상: {target["name"]}  (후보 {target["candidates"]}건)')
        out = {}
        for n in (3, 5):
            t = kpi(PortfolioBacktest(data, sig, days, sel_top(n), n, 0)
                    .run(f'Top{n}'))
            rs = [kpi(PortfolioBacktest(data, sig, days, sel_random(n), n, s)
                      .run(f'Rand{n}')) for s in range(a.seeds)]
            rv = sorted(x['total_return_pct'] for x in rs)
            pct = round(sum(1 for x in rv if x < t['total_return_pct'])
                        / len(rv) * 100, 1)
            m, sd = float(np.mean(rv)), float(np.std(rv))
            bind = t['binding_days']
            print(f'  {n}슬롯  Score {t["total_return_pct"]:>7.2f}%  '
                  f'Random {m:>7.2f}% ± {sd:.2f}  → {pct:.1f} 백분위   '
                  f'(선별 구속 {bind}일)')
            out[f'slots_{n}'] = {
                'score_return': t['total_return_pct'], 'score_pf':
                t['profit_factor'], 'random_mean': round(m, 3),
                'random_sd': round(sd, 3), 'percentile': pct,
                'binding_days': bind, 'trades': t['trades']}
        score_recheck = {'case': target['name'], 'results': out}
        pcts = [v['percentile'] for v in out.values()]
        if all(25 <= p <= 75 for p in pcts):
            print('  → 여전히 무작위와 구분되지 않는다. Phase 0.1 결론 유지.')
        else:
            print('  → Phase 0.1 과 다른 결과. 후보 확보가 전제였을 수 있다.')

    # ── 저장 ─────────────────────────────────────────────────────────────
    cols = ['name', 'candidates', 'trades', 'win_rate', 'profit_factor',
            'total_return_pct', 'mdd_pct', 'expectancy_pct', 'expectancy_won',
            'max_consecutive_loss', 'avg_return_pct', 'avg_win_pct',
            'avg_loss_pct', 'total_pnl_won', 'avg_hold_bars', 'accept',
            'preferred']
    with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(cols + ['old_trades', 'old_pf', 'new_trades', 'new_pf',
                           'new_avg_pct', 'new_total_won'])
        for k in rows:
            o, n = k['quality_old'], k['quality_new']
            w.writerow([k.get(c) for c in cols] +
                       [o['trades'], o['pf'], n['trades'], n['pf'],
                        n['avg_pct'], n['total_won']])

    cfg = {'period': {'start': dc.START, 'end': dc.END,
                      'trading_days': len(days)},
           'universe_loaded': len(data),
           'exit_profile': EXIT_PROFILE,
           'capital': {'initial': INITIAL_CAPITAL,
                       'slot_divisor': SLOT_DIVISOR, 'compounding': False},
           'cases': {k: (v or 'ops default') for k, v in CASES.items()},
           'gates': {'min_candidates': MIN_CANDIDATES, 'pf_gate': pf_gate,
                     'mdd_floor': MDD_FLOOR}}
    h = hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str)
                       .encode()).hexdigest()[:16]
    run_id = f'phase0.2-{datetime.now():%Y%m%d-%H%M%S}'
    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump({'run_id': run_id, 'baseline_hash': h,
                   'git_commit': _git('rev-parse', 'HEAD'),
                   'parity_ok': ok,
                   'created_at': datetime.now().isoformat(timespec='seconds'),
                   'config': cfg, 'kpi': rows,
                   'accepted': [k['name'] for k in passed],
                   'score_recheck': score_recheck},
                  f, ensure_ascii=False, indent=2, default=str)

    print()
    print('-' * 78)
    print(f'  run_id        {run_id}')
    print(f'  baseline_hash {h}')
    print(f'  CSV           {CSV_PATH}')
    print(f'  JSON          {JSON_PATH}')
    print('-' * 78)


if __name__ == '__main__':
    main()
