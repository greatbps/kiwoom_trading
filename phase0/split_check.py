"""
Phase 0.2 부록 — 슬롯 스윕 + 기간 분할 검증

두 가지를 본다.

  ① 슬롯 상한 5~8 스윕
     본 실험은 '상한 없음' 으로 돌렸지만 운영에는 상한이 있다
     (`ScoreEngine.MAX_SELECTED = 5`). 후보를 늘리면 동시 노출이 함께
     늘어 MDD 가 벌어지므로, 상한을 씌운 상태에서도 기준을 넘는지 봐야
     "채택 가능" 이라고 말할 수 있다.

  ② 전후반 분할
     완화 파라미터는 결국 이 데이터를 보고 고른 값이다. 전반에만 통하고
     후반에 무너지면 그건 신호가 아니라 곡선 맞추기다.

     ⚠️ 이건 out-of-sample 검증이 **아니다.** 같은 2년을 반으로 자른
        것뿐이라, 두 구간 모두 파라미터 선택에 이미 사용됐다.
        진짜 확인은 별도 기간 또는 Paper Trading 으로 해야 한다.

사용법:
    python -m phase0.split_check
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase0 import data_cache as dc
from phase0.candidate_gen import CASES, SUPPLEMENTARY, ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_top

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'phase02_slot_split.json')

# 합격 기준 (작업지시서)
MIN_TRADES = 120
MIN_PF = 1.50
MDD_FLOOR = -15.0
MIN_CANDIDATES = 200
TARGET_TRADES = 150
TARGET_PF = 1.70


def main():
    data = dc.load_ohlcv()
    days = dc.trading_days(data)
    ds = set(days)

    cfg = {'A. Baseline (현행)': CASES['A. Baseline (현행)'],
           'E. 조합 (B+C+D)': CASES['E. 조합 (B+C+D)'],
           **SUPPLEMENTARY}
    sigs = {n: ParamCandidateGen(**kw).scan(data) for n, kw in cfg.items()}
    ncand = {n: sum(1 for s in sig.values() for d in s if d in ds)
             for n, sig in sigs.items()}

    print('=' * 78)
    print('  ① 슬롯 상한 스윕 — 운영 상한을 씌워도 기준을 넘는가')
    print('=' * 78)
    print(f'  최소기준: 후보>={MIN_CANDIDATES} · 거래>={MIN_TRADES} · '
          f'PF>={MIN_PF} · MDD>{MDD_FLOOR}%')
    print(f'\n  {"케이스":<22}{"후보":>5}{"상한":>5}{"거래":>6}{"PF":>8}'
          f'{"수익%":>9}{"MDD%":>9}{"기대값%":>9}   판정')
    rows = []
    for n, sig in sigs.items():
        for s in (5, 6, 7, 8):
            k = kpi(PortfolioBacktest(data, sig, days, sel_top(s), s, 0).run(n))
            ok = (ncand[n] >= MIN_CANDIDATES and k['trades'] >= MIN_TRADES
                  and k['profit_factor'] >= MIN_PF
                  and k['mdd_pct'] > MDD_FLOOR)
            pref = ok and k['profit_factor'] >= TARGET_PF
            k.update(case=n, slots=s, candidates=ncand[n], accept=ok,
                     preferred=pref)
            rows.append(k)
            tag = ('✅ 우선후보' if pref else '✅ 최소충족' if ok else '')
            print(f'  {n:<22}{ncand[n]:>5}{s:>5}{k["trades"]:>6}'
                  f'{k["profit_factor"]:>8.3f}{k["total_return_pct"]:>9.2f}'
                  f'{k["mdd_pct"]:>9.2f}{k["expectancy_pct"]:>9.3f}   {tag}')

    # ── 전후반 ───────────────────────────────────────────────────────────
    mid = days[len(days) // 2]
    print()
    print('=' * 78)
    print(f'  ② 기간 분할 (경계 {mid.date()}) — 슬롯 5 · '
          '한쪽에만 통하면 곡선 맞추기다')
    print('=' * 78)
    print(f'  {"케이스":<22}{"전반거래":>8}{"전반PF":>8}{"전반%":>8}'
          f'{"후반거래":>8}{"후반PF":>8}{"후반%":>8}   PF 안정성')
    split = {}
    for n, sig in sigs.items():
        halves = []
        for lo, hi in ((days[0], mid), (mid, days[-1])):
            sub = [d for d in days if lo <= d <= hi]
            halves.append(kpi(PortfolioBacktest(data, sig, sub, sel_top(5), 5,
                                                0).run(n)))
        a, b = halves
        drop = b['profit_factor'] - a['profit_factor']
        split[n] = {'first': a, 'second': b, 'pf_delta': round(drop, 3)}
        note = ('양 구간 PF>=1.7' if min(a['profit_factor'],
                                       b['profit_factor']) >= 1.7
                else f'{drop:+.3f}')
        print(f'  {n:<22}{a["trades"]:>8}{a["profit_factor"]:>8.3f}'
              f'{a["total_return_pct"]:>8.2f}{b["trades"]:>8}'
              f'{b["profit_factor"]:>8.3f}{b["total_return_pct"]:>8.2f}   {note}')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'slot_sweep': rows, 'split': split,
                   'split_boundary': str(mid.date()),
                   'note': '전후반 분할은 out-of-sample 이 아니다 — '
                           '같은 2년을 반으로 자른 것뿐이다.'},
                  f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: {OUT}')


if __name__ == '__main__':
    main()
