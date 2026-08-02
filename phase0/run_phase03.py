"""
Phase 0.3 — Candidate Generator Validation

Phase 0.2 에서 나온 두 후보(E · G)가 **과최적화가 아닌지** 본다.
더 좋은 파라미터를 찾는 단계가 아니다.

구간을 셋으로 나눠 돌린다.

    전체    2024-08-01 ~ 2026-07-30   (파라미터를 고른 구간 — 참고용)
    기간1   2025-01-01 ~ 2025-12-31
    기간2   2026-01-01 ~ 2026-07-30

  ⚠️ 세 구간 모두 파라미터 선택에 이미 쓰인 데이터다. 구간을 나눴다고
     out-of-sample 이 되지 않는다. 여기서 볼 수 있는 것은
     "시장 국면이 바뀌어도 무너지지 않는가" 까지다.
     진짜 검증은 Paper Trading 이고, 그건 시간이 흘러야 나온다.

사용법:
    python -m phase0.run_phase03
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from phase0 import data_cache as dc
from phase0 import profiles as pf
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import (EXIT_PROFILE, INITIAL_CAPITAL, SLOT_DIVISOR,
                              PortfolioBacktest, kpi, sel_top)

OUT = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(OUT, 'phase0.3_report.json')
TRADE_CSV = os.path.join(OUT, 'phase0.3_trade.csv')
KPI_CSV = os.path.join(OUT, 'phase0.3_kpi.csv')

PERIODS = [
    ('전체 (파라미터 선택 구간)', dc.START, dc.END),
    ('기간1 2025년', '2025-01-01', '2025-12-31'),
    ('기간2 2026 상반기', '2026-01-01', '2026-07-30'),
]

# Pass 기준 (작업지시서 — Paper 20거래일 기준값)
PASS_PF = 1.30
PASS_MDD = -10.0
PASS_TRADES = 10


def _git(*a):
    try:
        return subprocess.run(['git', *a], capture_output=True, text=True,
                              cwd=os.path.dirname(OUT)).stdout.strip()
    except Exception:
        return 'UNKNOWN'


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()

    data = dc.load_ohlcv()
    all_days = dc.trading_days(data)

    print('=' * 78)
    print('  Phase 0.3 — Candidate Generator Validation')
    print('=' * 78)
    print(f'  feature flag  ACTIVE = {pf.ACTIVE}  (기본값 = 운영 현행)')
    for n in ('OPS', 'E', 'G'):
        p = pf.get(n)
        ma = ('상승' if p['ma50_slope_min'] is None
              else f'>={p["ma50_slope_min"]*100:.0f}%')
        print(f'    {n:<4}{pf.VERSION[n]:<22} RVOL>={p["rvol_min"]:.2f}  '
              f'ATR {p["atr_min"]*100:.1f}~{p["atr_max"]*100:.0f}%  '
              f'MA50 {ma}  슬롯 {p["max_selected"]}')
    print()

    sigs = {n: ParamCandidateGen(**pf.gen_kwargs(n)).scan(data)
            for n in ('OPS', 'E', 'G')}

    rows, all_trades = [], []
    for label, lo, hi in PERIODS:
        days = [d for d in all_days if lo <= str(d.date()) <= hi]
        print('-' * 78)
        print(f'  {label}   {lo} ~ {hi}   {len(days)}거래일')
        print('-' * 78)
        print(f'  {"프로파일":<8}{"후보":>6}{"거래":>6}{"승률%":>8}{"PF":>8}'
              f'{"수익%":>9}{"MDD%":>9}{"평균손실%":>10}{"기대값%":>9}   판정')
        base_avg_loss = None
        for n in ('OPS', 'E', 'G'):
            sig = sigs[n]
            nc = sum(1 for s in sig.values() for d in s if d in set(days))
            res = PortfolioBacktest(data, sig, days, sel_top(pf.max_selected(n)),
                                    pf.max_selected(n), 0).run(n)
            k = kpi(res)
            if not k.get('trades'):
                print(f'  {n:<8}{nc:>6}     0   (거래 없음)')
                continue
            k.update(profile=n, period=label, start=lo, end=hi,
                     candidates=nc, version=pf.VERSION[n])
            if n == 'OPS':
                base_avg_loss = k['avg_loss_pct']
            # 평균손실 악화율 — 기준: 기존 대비 +20% 이내
            worse = (abs(k['avg_loss_pct'] / base_avg_loss) - 1) * 100 \
                if base_avg_loss else 0.0
            k['avg_loss_vs_ops_pct'] = round(worse, 1)
            ok = (k['trades'] >= PASS_TRADES and k['profit_factor'] >= PASS_PF
                  and k['mdd_pct'] > PASS_MDD)
            k['pass_paper_criteria'] = ok
            rows.append(k)
            for t in res.trades:
                all_trades.append({'profile': n, 'period': label,
                                   **t.__dict__})
            tag = '✅' if ok else ('❌ MDD' if k['mdd_pct'] <= PASS_MDD else '❌')
            print(f'  {n:<8}{nc:>6}{k["trades"]:>6}{k["win_rate"]:>8.1f}'
                  f'{k["profit_factor"]:>8.3f}{k["total_return_pct"]:>9.2f}'
                  f'{k["mdd_pct"]:>9.2f}{k["avg_loss_pct"]:>10.2f}'
                  f'{k["expectancy_pct"]:>9.3f}   {tag}')
        print()

    # ── 국면 안정성 ─────────────────────────────────────────────────────
    print('-' * 78)
    print('  ② 국면 안정성 — 기간1 → 기간2 에서 무너지는가')
    print('-' * 78)
    stab = {}
    for n in ('OPS', 'E', 'G'):
        a = next((r for r in rows if r['profile'] == n
                  and r['period'].startswith('기간1')), None)
        b = next((r for r in rows if r['profile'] == n
                  and r['period'].startswith('기간2')), None)
        if not (a and b):
            continue
        d = round(b['profit_factor'] - a['profit_factor'], 3)
        stab[n] = {'pf_2025': a['profit_factor'], 'pf_2026h1':
                   b['profit_factor'], 'delta': d,
                   'both_above_pass': min(a['profit_factor'],
                                          b['profit_factor']) >= PASS_PF}
        print(f'  {n:<4} PF {a["profit_factor"]:.3f} → {b["profit_factor"]:.3f}'
              f'  ({d:+.3f})   '
              f'{"양 구간 PF>=1.3 유지" if stab[n]["both_above_pass"] else "⚠ 한쪽 미달"}')

    # ── 저장 ────────────────────────────────────────────────────────────
    kcols = ['profile', 'version', 'period', 'start', 'end', 'candidates',
             'trades', 'win_rate', 'profit_factor', 'total_return_pct',
             'mdd_pct', 'expectancy_pct', 'expectancy_won', 'avg_return_pct',
             'avg_win_pct', 'avg_loss_pct', 'avg_loss_vs_ops_pct',
             'max_consecutive_loss', 'avg_hold_bars', 'total_pnl_won',
             'pass_paper_criteria']
    with open(KPI_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(kcols)
        for r in rows:
            w.writerow([r.get(c) for c in kcols])

    if all_trades:
        tcols = list(all_trades[0].keys())
        with open(TRADE_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=tcols)
            w.writeheader()
            w.writerows(all_trades)

    report = {
        'run_id': f'phase0.3-{datetime.now():%Y%m%d-%H%M%S}',
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'git_commit': _git('rev-parse', 'HEAD'),
        'feature_flag': {'env': 'KIWOOM_CANDIDATE_PROFILE',
                         'active_default': 'OPS',
                         'profiles': pf.PROFILES, 'versions': pf.VERSION},
        'frozen': ['daily_scan.py', 'adapter.py', 'score_engine.py',
                   'risk_manager', 'exit', 'position sizing'],
        'exit_profile': EXIT_PROFILE,
        'capital': {'initial': INITIAL_CAPITAL, 'slot_divisor': SLOT_DIVISOR,
                    'compounding': False},
        'pass_criteria': {'trades': PASS_TRADES, 'pf': PASS_PF,
                          'mdd': PASS_MDD,
                          'avg_loss_worse_than_ops_pct_max': 20},
        'periods': [{'label': a, 'start': b, 'end': c} for a, b, c in PERIODS],
        'kpi': rows,
        'regime_stability': stab,
        'caveats': [
            '세 구간 모두 파라미터 선택에 이미 사용된 데이터다. '
            '구간 분할은 out-of-sample 이 아니다.',
            'G 의 RVOL 1.35 는 Phase 0.2 데이터를 보고 정한 값이다.',
            'Paper Trading 20거래일 결과는 시간이 흘러야 나온다. '
            '이 파일에는 백테스트 결과만 들어 있다.',
            '청산은 일봉 스윙 근사다. 운영 5분봉 청산과 다르다.',
            'yfinance 데이터 변동성이 실제보다 크다.',
        ],
        'paper_trading': {'status': 'NOT_STARTED',
                          'required_trading_days': 20,
                          'note': '별도 수집 필요 — phase0/paper_track.py'},
    }
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print()
    print('-' * 78)
    print(f'  run_id  {report["run_id"]}')
    for p in (REPORT, TRADE_CSV, KPI_CSV):
        print(f'  저장    {p}')
    print('-' * 78)


if __name__ == '__main__':
    main()
