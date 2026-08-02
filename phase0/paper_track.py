"""
Phase 0.3 — Paper Trading 수집기

운영에 반영하기 전, 세 프로파일(OPS/E/G)이 **앞으로** 어떤 후보를 내는지
매일 기록한다. 백테스트는 파라미터를 고른 데이터를 다시 보는 것이지만,
여기 쌓이는 것은 선택 이후의 데이터다 — 그게 out-of-sample 이다.

━━━ 왜 매일 기록하는가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  후보 생성은 OHLCV 로부터 결정적이므로 나중에 한 번에 재계산할 수도
  있다. 그런데 데이터 제공처가 과거 봉을 수정하면(수정주가·정정) 그
  재계산은 조용히 달라진다. **그날 실제로 무엇이 나왔는지**를 그날
  적어 두어야 나중에 말을 바꾸지 않는다.

━━━ 주문은 내지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  기록만 한다. 운영 코드도, 계좌도 건드리지 않는다.

사용법:
    python -m phase0.paper_track --record          # 매 거래일 장 마감 후
    python -m phase0.paper_track --report          # 누적 결과 판정
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from phase0 import profiles as pf
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_top

from backtest.loader import load_multi
from backtest.scanner import DEFAULT_CANDIDATES

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper')
LOG = os.path.join(DIR, 'paper_log.jsonl')
REPORT = os.path.join(DIR, 'paper_report.json')

# 판정 기준 (작업지시서)
PASS_PF = 1.30
PASS_MDD = -10.0
PASS_TRADES = 10

# ⚠️ 지시서의 20거래일로는 어느 프로파일도 거래 10건을 못 채운다.
#    과거 463개 창에서 거래>=10 달성률: OPS 1.5% · E 4.8% · G 14.5%.
#    60거래일이면 E 85.6% · G 88.7% 로 올라간다. 근거는
#    phase0.3_paper_feasibility.json 참고.
TARGET_DAYS_SPEC = 20
TARGET_DAYS_RECOMMENDED = 60

LOOKBACK_DAYS = 260      # 신호/MA50 계산용 과거 봉


def _load(end: str):
    start = (datetime.strptime(end, '%Y-%m-%d')
             - timedelta(days=LOOKBACK_DAYS * 2)).strftime('%Y-%m-%d')
    d = load_multi(DEFAULT_CANDIDATES, start, end)
    return {s: df[df.index <= end] for s, df in d.items() if len(df)}


def record(ref: str | None = None):
    os.makedirs(DIR, exist_ok=True)
    end = ref or datetime.today().strftime('%Y-%m-%d')
    data = _load(end)
    if not data:
        print('[PAPER] 데이터 없음 — 기록하지 않는다')
        return

    # 마지막 확정 거래일
    last = max(df.index[-1] for df in data.values())
    day = str(last.date())

    seen = set()
    if os.path.exists(LOG):
        with open(LOG, encoding='utf-8') as f:
            seen = {json.loads(l)['date'] for l in f if l.strip()}
    if day in seen:
        print(f'[PAPER] {day} 는 이미 기록됨 — 건너뛴다')
        return

    rec = {'date': day, 'recorded_at': datetime.now().isoformat(
        timespec='seconds'), 'symbols_loaded': len(data), 'candidates': {}}
    for n in ('OPS', 'E', 'G'):
        gen = ParamCandidateGen(**pf.gen_kwargs(n))
        hits = []
        for sym, df in data.items():
            if last not in df.index:
                continue
            i = df.index.get_loc(last)
            if gen.get_signal(df, i) == 'BUY':
                hits.append(sym)
        rec['candidates'][n] = sorted(hits)

    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print(f'[PAPER] {day} 기록 — ' +
          '  '.join(f'{n} {len(v)}건' for n, v in rec['candidates'].items()))


def report():
    if not os.path.exists(LOG):
        print('기록이 없다. --record 를 먼저 돌려야 한다.')
        return
    with open(LOG, encoding='utf-8') as f:
        recs = [json.loads(l) for l in f if l.strip()]
    if not recs:
        print('기록이 비어 있다.')
        return

    dates = sorted(r['date'] for r in recs)
    n_days = len(dates)
    print('=' * 78)
    print('  Paper Trading 결과')
    print('=' * 78)
    print(f'  수집 {dates[0]} ~ {dates[-1]}   {n_days} 거래일')
    print(f'  기준 거래>={PASS_TRADES} · PF>={PASS_PF} · MDD>{PASS_MDD}%')
    if n_days < TARGET_DAYS_RECOMMENDED:
        print(f'  ⚠️ 권장 {TARGET_DAYS_RECOMMENDED}거래일 미달 — '
              f'{TARGET_DAYS_RECOMMENDED - n_days}일 더 필요하다.')
        print(f'     지시서의 {TARGET_DAYS_SPEC}거래일로는 거래 10건이 '
              '거의 나오지 않는다 (근거: paper_feasibility).')

    data = _load(dates[-1])
    days = [d for d in sorted({i for df in data.values() for i in df.index})
            if dates[0] <= str(d.date()) <= dates[-1]]

    out = {}
    print(f'\n  {"프로파일":<8}{"후보":>6}{"거래":>6}{"승률%":>8}{"PF":>8}'
          f'{"수익%":>9}{"MDD%":>9}   판정')
    for n in ('OPS', 'E', 'G'):
        sig = {}
        for r in recs:
            d = pd.Timestamp(r['date'])
            for s in r['candidates'].get(n, []):
                sig.setdefault(s, set()).add(d)
        ncand = sum(len(v) for v in sig.values())
        res = PortfolioBacktest(data, sig, days, sel_top(pf.max_selected(n)),
                                pf.max_selected(n), 0).run(n)
        k = kpi(res)
        if not k.get('trades'):
            print(f'  {n:<8}{ncand:>6}     0   (청산된 거래 없음)')
            out[n] = {'candidates': ncand, 'trades': 0, 'pass': False}
            continue
        ok = (k['trades'] >= PASS_TRADES and k['profit_factor'] >= PASS_PF
              and k['mdd_pct'] > PASS_MDD)
        k['pass'] = ok
        k['candidates'] = ncand
        out[n] = k
        print(f'  {n:<8}{ncand:>6}{k["trades"]:>6}{k["win_rate"]:>8.1f}'
              f'{k["profit_factor"]:>8.3f}{k["total_return_pct"]:>9.2f}'
              f'{k["mdd_pct"]:>9.2f}   {"✅ PASS" if ok else "❌ FAIL"}')

    os.makedirs(DIR, exist_ok=True)
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump({'collected_days': n_days, 'first': dates[0],
                   'last': dates[-1],
                   'criteria': {'trades': PASS_TRADES, 'pf': PASS_PF,
                                'mdd': PASS_MDD},
                   'spec_days': TARGET_DAYS_SPEC,
                   'recommended_days': TARGET_DAYS_RECOMMENDED,
                   'sufficient': n_days >= TARGET_DAYS_RECOMMENDED,
                   'profiles': out}, f, ensure_ascii=False, indent=2,
                  default=str)
    print(f'\n  저장: {REPORT}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--date', help='기준일 (기본 오늘)')
    a = ap.parse_args()
    if a.record:
        record(a.date)
    elif a.report:
        report()
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
