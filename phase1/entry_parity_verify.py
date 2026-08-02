"""
Iteration 9 Phase 3·5 — Entry Parity 검증 + 거래 감소 Funnel

Phase 3  ChochSignalEngine(Live 경로) 이 backtest SMCAdapter 와
         **같은 신호**를 내는가. 목표 Signal Match ≥95%.

Phase 5  CHoCH 117건이 실제 거래 몇 건이 되는가 — 단계별 감소율.

━━━ 왜 Match 가 100% 가 아닐 수 있는가 ━━━━━━━━━━━━━━━━━━━━━━━━━

  ChochSignalEngine 은 SMCAdapter 를 그대로 호출하지만, `run()` 은
  **마지막 봉 하나**만 판정한다 (Live 는 매일 최신 봉으로 돈다).
  백테스트는 전 구간을 훑는다. 같은 데이터를 주면 같은 답이 나와야
  하고, 안 나오면 그 자체가 결함이다.

사용법:
    python -m phase1.entry_parity_verify
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzers.swing.choch_engine import ChochSignalEngine, MIN_BARS
from phase0 import data_cache as dc
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import PortfolioBacktest, kpi, sel_top
from phase1.swing_entry_adapter import SwingEntryAdapter

OUT = os.path.dirname(os.path.abspath(__file__))
EXIT_CASE3 = dict(sl_pct=-0.05, min_hold_bars=3, max_hold_bars=60)


def main():
    data = dc.load_ohlcv()
    days = dc.trading_days(data)
    dayset = set(days)

    print('=' * 88)
    print('  Phase 3 — Entry Parity 검증 (Live CHoCH 경로 vs Backtest)')
    print('=' * 88)

    # ── 기준: 백테스트 SMCAdapter ────────────────────────────────────────
    sig_bt = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)

    # ── Live 경로: ChochSignalEngine 을 매 봉 호출 ───────────────────────
    sig_live: dict[str, set] = {}
    detail = []
    for sym, df in data.items():
        got = set()
        for i in range(MIN_BARS, len(df)):
            sub = df.iloc[:i + 1]
            s = ChochSignalEngine(sub, {}).run()
            if s:
                got.add(df.index[i])
                detail.append({'symbol': sym, 'date': str(df.index[i].date()),
                               'engine': 'choch_live', **{
                                   k: s[k] for k in
                                   ('pattern', 'final_score', 'entry',
                                    'stop', 'target', 'trigger')}})
        if got:
            sig_live[sym] = got

    n_bt = sum(1 for v in sig_bt.values() for d in v if d in dayset)
    n_lv = sum(1 for v in sig_live.values() for d in v if d in dayset)
    syms = set(sig_bt) | set(sig_live)
    inter = sum(1 for s in syms
                for d in (sig_bt.get(s, set()) & sig_live.get(s, set()))
                if d in dayset)
    only_bt = sum(1 for s in syms
                  for d in (sig_bt.get(s, set()) - sig_live.get(s, set()))
                  if d in dayset)
    only_lv = sum(1 for s in syms
                  for d in (sig_live.get(s, set()) - sig_bt.get(s, set()))
                  if d in dayset)
    match = inter / max(1, n_bt) * 100

    print(f'  Backtest SMCAdapter   {n_bt:>5}건')
    print(f'  Live ChochEngine      {n_lv:>5}건')
    print(f'  일치                  {inter:>5}건   '
          f'Backtest 전용 {only_bt}   Live 전용 {only_lv}')
    print(f'\n  Signal Match {match:.1f}%   목표 95% → '
          f'{"✅ PASS" if match >= 95 else "❌ FAIL"}')

    # 진입가 차이 — 같은 신호일의 entry 값 비교
    diffs = []
    for d in detail:
        s, dt = d['symbol'], d['date']
        import pandas as pd
        ts = pd.Timestamp(dt)
        if ts in sig_bt.get(s, set()):
            close = float(data[s].loc[ts]['close'])
            diffs.append(abs(d['entry'] - close))
    print(f'  Entry Price 차이: 최대 {max(diffs) if diffs else 0:.4f}원 '
          f'(같은 봉 종가 대비)')

    with open(os.path.join(OUT, 'ENTRY_SIGNAL_COMPARE.csv'), 'w',
              newline='', encoding='utf-8-sig') as f:
        if detail:
            w = csv.DictWriter(f, fieldnames=list(detail[0]))
            w.writeheader()
            w.writerows(detail)

    # ═══ Phase 5 — Funnel ═══════════════════════════════════════════════
    print('\n' + '=' * 88)
    print('  Phase 5 — 거래 빈도 Funnel')
    print('=' * 88)

    sig_pb, _ = SwingEntryAdapter().scan(data)
    n_pb = sum(1 for v in sig_pb.values() for d in v if d in dayset)

    # 후보 생성 단계별 (backtest.adapter 필터 순서)
    from backtest.adapter import SMCAdapter
    from backtest.daily_scan import BEST_CONFIG
    stages = [
        ('Pullback 원신호 (현행 Live)', None),
        ('CHoCH 원신호', dict(require_sweep=False)),
        ('  +거래량 1.5x', dict(require_sweep=False, require_volume=True)),
        ('  +ATR 2~8%', dict(require_sweep=False, require_volume=True,
                             atr_pct_min=0.02, atr_pct_max=0.08)),
        ('  +MA50 우상향 (최종)', dict(require_sweep=False,
                                    require_volume=True, atr_pct_min=0.02,
                                    atr_pct_max=0.08,
                                    require_ma50_trend=True)),
    ]
    funnel = []
    prev = None
    print(f'  {"단계":<28}{"신호":>7}{"직전대비":>10}{"원신호대비":>11}')
    for label, kw in stages:
        if kw is None:
            n = n_pb
        else:
            ad = SMCAdapter(BEST_CONFIG, **kw)
            n = sum(1 for sym, df in data.items() for i in range(len(df))
                    if df.index[i] in dayset
                    and ad.get_signal(df, i) == 'BUY')
        base = funnel[1]['signals'] if len(funnel) > 1 else n
        funnel.append({'stage': label.strip(), 'signals': n,
                       'vs_prev_pct': round(n / prev * 100, 1) if prev else 100.0,
                       'vs_raw_pct': round(n / base * 100, 1) if base else 100.0})
        print(f'  {label:<28}{n:>7}'
              f'{(f"{n/prev*100:.1f}%" if prev else "-"):>10}'
              f'{(f"{n/base*100:.1f}%" if base else "-"):>11}')
        prev = n

    # 포트폴리오 단계 — 슬롯 상한이 몇 건을 자르는가
    print(f'\n  {"포트폴리오 단계":<28}{"거래":>7}{"신호대비":>10}')
    n_final = funnel[-1]['signals']
    for slots, tag in ((None, '상한 없음'), (5, 'Top-5'), (3, 'Top-3 (Live)')):
        pb = PortfolioBacktest(data, sig_bt, days,
                               (lambda r, n_, g: [x for x, _ in r])
                               if slots is None else sel_top(slots), slots, 0)
        for k, v in EXIT_CASE3.items():
            setattr(pb.engine, k, v)
        k_ = kpi(pb.run(tag))
        t = k_.get('trades', 0)
        funnel.append({'stage': f'포트폴리오 {tag}', 'signals': t,
                       'vs_prev_pct': round(t / n_final * 100, 1),
                       'vs_raw_pct': round(t / n_final * 100, 1)})
        print(f'  {tag:<28}{t:>7}{t/n_final*100:>9.1f}%')

    with open(os.path.join(OUT, 'FUNNEL.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['stage', 'signals', 'vs_prev_pct',
                                          'vs_raw_pct'])
        w.writeheader()
        w.writerows(funnel)

    with open(os.path.join(OUT, 'ENTRY_PARITY.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'signal_match_pct': round(match, 1),
                   'pass': match >= 95,
                   'backtest_signals': n_bt, 'live_signals': n_lv,
                   'intersection': inter, 'only_backtest': only_bt,
                   'only_live': only_lv,
                   'max_entry_price_diff': max(diffs) if diffs else 0,
                   'funnel': funnel}, f, ensure_ascii=False, indent=2)
    print('\n  저장: ENTRY_SIGNAL_COMPARE.csv · FUNNEL.csv · ENTRY_PARITY.json')


if __name__ == '__main__':
    main()
