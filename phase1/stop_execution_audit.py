"""
Phase 1 Iteration 5 — Stop Execution Audit

Exp 2  Swing 5건 손절 도달 → 실제 청산 지연
Exp 3  전체 Live 청산의 Stop Execution Rate
Exp 4  Backtest / Live Exit Rule Mapping

━━━ '손절 도달 시각' 은 일봉으로만 근사할 수 있다 ━━━━━━━━━━━━━━━

  분봉 데이터가 없다. 따라서 "몇 시에 손절선을 통과했는가" 는 알 수 없고,
  **어느 날 통과했는가** 까지만 말할 수 있다.
  지연은 '일 단위' 로만 산출하며, 분 단위 수치는 만들지 않는다.

사용법:
    python -m phase1.stop_execution_audit
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))

from phase0 import data_cache as dc

OUT = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(OUT, n)          # noqa: E731

SWING_CODES = ('006400', '005930', '000660', '035420')

# Backtest ↔ Live 청산 규칙 대응 (Exp 4)
EXIT_MAPPING = {
    'SL': ['HARD_STOP', 'SWING_HARD_STOP', 'STRUCTURE_STOP', 'STOP_LOSS'],
    'TP': ['TAKE_PROFIT', 'TP1', 'TP2'],
    'TRAIL': ['TRAILING', 'TRAIL'],
    'BE_STOP': ['BREAK_EVEN', 'BE_STOP'],
    'MAX_HOLD': ['TIME_EXIT'],
    '(대응 없음)': ['EARLY_FAILURE', 'OVERNIGHT_BLOCK', 'MA5_EXIT',
                   'DRAWDOWN_STOP', 'OTHER'],
}


def conn():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


def norm_reason(r: str) -> str:
    r = (r or '').upper()
    for pat, tag in [
        (r'SWING_HARD_STOP', 'SWING_HARD_STOP'),
        (r'STRUCTURE_STOP', 'STRUCTURE_STOP'),
        (r'HARD_STOP|HARD STOP', 'HARD_STOP'),
        (r'오버나이트|OVERNIGHT', 'OVERNIGHT_BLOCK'),
        (r'TRAIL', 'TRAILING'),
        (r'MA5_EXIT|MA5', 'MA5_EXIT'),
        (r'DRAWDOWN', 'DRAWDOWN_STOP'),
        (r'EARLY FAILURE|EARLY_FAIL', 'EARLY_FAILURE'),
        (r'손절|STOP', 'STOP_LOSS'),
        (r'익절|TAKE_PROFIT|\bTP\b', 'TAKE_PROFIT'),
        (r'BREAK.?EVEN|BE_', 'BREAK_EVEN'),
        (r'시간|TIME|장마감|EOD', 'TIME_EXIT'),
    ]:
        if re.search(pat, r):
            return tag
    return 'OTHER'


def main():
    data = dc.load_ohlcv()
    cur = conn().cursor()
    rep = {'created_at': datetime.now().isoformat(timespec='seconds')}

    print('=' * 84)
    print('  Phase 1 Iteration 5 — Stop Execution Audit')
    print('=' * 84)

    # ═══ Exp 2 — Swing 5건 손절 지연 ════════════════════════════════════
    print('\n' + '-' * 84)
    print('  Experiment 2 — Swing 5건 손절 도달 → 청산 지연')
    print('-' * 84)

    cur.execute("""SELECT trade_type, stock_code, stock_name, trade_time,
                          price, quantity, stop_price, exit_reason,
                          realized_profit, condition_name
                   FROM trades WHERE stock_code IN %s
                   ORDER BY stock_code, trade_time""", (SWING_CODES,))
    pend, pairs = {}, []
    for (tt, cd, nm, ts, px, qty, sp, xr, rp, cond) in cur.fetchall():
        if tt == 'BUY' and cond == 'SWING':
            pend[cd] = dict(code=cd, name=nm, bt=ts, bp=float(px),
                            qty=int(qty), stop=float(sp) if sp else None)
        elif tt == 'SELL' and cd in pend:
            p = pend.pop(cd)
            p.update(st=ts, sp_=float(px), reason=xr,
                     pnl=float(rp) if rp is not None else None)
            pairs.append(p)
    pairs.sort(key=lambda x: x['bt'])

    rows = []
    print(f'  {"종목":<10}{"매수일":<12}{"손절가":>10}{"손절도달일":<12}'
          f'{"청산일":<12}{"지연":>5}{"청산가":>10}{"초과손실":>11}')
    for p in pairs:
        df = data[p['code']]
        bd = pd.Timestamp(p['bt'].date())
        sd = pd.Timestamp(p['st'].date())
        win = df[(df.index >= bd) & (df.index <= sd)]
        breach = win[win['low'] <= p['stop']] if p['stop'] else win.iloc[0:0]
        first = breach.index[0] if len(breach) else None
        delay = (sd - first).days if first is not None else None
        slip = (p['stop'] - p['sp_']) * p['qty'] if (
            p['stop'] and p['sp_'] < p['stop']) else 0.0
        rows.append({
            'code': p['code'], 'name': p['name'],
            'buy_date': str(bd.date()), 'buy_price': round(p['bp']),
            'stop_price': round(p['stop']) if p['stop'] else None,
            'stop_pct': round((p['stop'] / p['bp'] - 1) * 100, 2)
            if p['stop'] else None,
            'stop_breach_date': str(first.date()) if first is not None else None,
            'exit_date': str(sd.date()), 'delay_days': delay,
            'exit_price': round(p['sp_']), 'exit_reason': p['reason'],
            'excess_loss_won': int(slip),
            'stop_executed': bool(p['stop'] and p['sp_'] >= p['stop']),
        })
        print(f'  {p["name"][:9]:<10}{str(bd.date()):<12}'
              f'{(round(p["stop"]) if p["stop"] else 0):>10,}'
              f'{str(first.date()) if first is not None else "-":<12}'
              f'{str(sd.date()):<12}{(delay if delay is not None else 0):>5}'
              f'{round(p["sp_"]):>10,}{int(slip):>11,}')

    n_exec = sum(1 for r in rows if r['stop_executed'])
    tot_slip = sum(r['excess_loss_won'] for r in rows)
    print(f'\n  손절가 이상으로 청산된 건: {n_exec}/{len(rows)}')
    print(f'  초과손실 합계 {tot_slip:,}원')
    print('\n  ⚠️ 분봉이 없어 "몇 시에 손절선을 통과했는가" 는 알 수 없다.')
    print('     지연은 일 단위로만 산출했다.')
    with open(P('swing_stop_delay.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rep['exp2'] = {'trades': rows, 'stop_executed': n_exec,
                   'total': len(rows), 'excess_loss_won': tot_slip}

    # ═══ Exp 3 — 전체 Live Stop Execution Rate ═════════════════════════
    print('\n' + '-' * 84)
    print('  Experiment 3 — 전체 Live 청산 (2025-11-10 ~ 2026-07-30)')
    print('-' * 84)

    cur.execute("""SELECT exit_reason, realized_profit, profit_rate,
                          stock_code, trade_time
                   FROM trades WHERE trade_type='SELL'
                   AND realized_profit IS NOT NULL
                   AND trade_time::date BETWEEN '2025-11-10' AND '2026-07-30'""")
    live = cur.fetchall()
    tags = Counter(norm_reason(r[0]) for r in live)

    # 손절 계열이 실제로 손절 수준에서 끊겼는가 — profit_rate 기준
    stop_tags = {'HARD_STOP', 'SWING_HARD_STOP', 'STRUCTURE_STOP',
                 'STOP_LOSS'}
    stop_rows = [r for r in live if norm_reason(r[0]) in stop_tags]
    with_pr = [r for r in stop_rows if r[2] is not None]
    deep = [r for r in with_pr if float(r[2]) < -5.0]
    print(f'  청산 {len(live)}건 중 손절 계열 {len(stop_rows)}건')
    if with_pr:
        print(f'    수익률 기록 있는 것 {len(with_pr)}건')
        print(f'    -5% 보다 깊게 끊긴 것 {len(deep)}건 '
              f'({len(deep)/len(with_pr)*100:.1f}%)')
        worst = min(float(r[2]) for r in with_pr)
        print(f'    최악 {worst:.2f}%')
    rate = (len(with_pr) - len(deep)) / len(with_pr) * 100 if with_pr else 0
    print(f'\n  Stop Execution Rate (설정 -5% 이내에서 끊긴 비율) '
          f'{rate:.1f}%   목표 95% → '
          f'{"✅ PASS" if rate >= 95 else "❌ FAIL"}')
    print('  ⚠️ 분봉이 없어 "조건 발생 후 N분 내 청산" 은 산출 불가.')
    print('     대신 "설정 손절폭을 넘겨 끊겼는가" 로 대체 측정했다.')
    rep['exp3'] = {'live_exits': len(live), 'stop_family': len(stop_rows),
                   'with_profit_rate': len(with_pr),
                   'deeper_than_5pct': len(deep),
                   'stop_execution_rate': round(rate, 1),
                   'pass': rate >= 95,
                   'note': '분봉 부재로 시간 지연 대신 손절폭 초과율로 대체'}

    # ═══ Exp 4 — Exit Rule Mapping ═════════════════════════════════════
    print('\n' + '-' * 84)
    print('  Experiment 4 — Backtest / Live Exit Rule Mapping')
    print('-' * 84)
    print(f'  {"Backtest 규칙":<16}{"Live 대응":<52}{"Live건":>7}')
    map_rows, mapped = [], 0
    for bt, lives in EXIT_MAPPING.items():
        n = sum(tags.get(t, 0) for t in lives)
        if bt != '(대응 없음)':
            mapped += n
        map_rows.append({'backtest_rule': bt, 'live_rules': '|'.join(lives),
                         'live_trades': n})
        print(f'  {bt:<16}{", ".join(lives)[:50]:<52}{n:>7}')
    cov = mapped / len(live) * 100
    print(f'\n  Mapping 커버리지 {mapped}/{len(live)} = {cov:.1f}%   '
          f'목표 100% → {"✅ PASS" if cov >= 100 else "❌ FAIL"}')
    print(f'  대응 없는 Live 전용 규칙: '
          f'{sum(tags.get(t,0) for t in EXIT_MAPPING["(대응 없음)"])}건')
    print('  → EARLY_FAILURE · OVERNIGHT_BLOCK · MA5_EXIT · DRAWDOWN_STOP 은')
    print('    백테스트에 대응 규칙이 없다. 백테스트가 이 청산들을 재현하지 못한다.')
    rep['exp4'] = {'mapping': map_rows, 'coverage_pct': round(cov, 1),
                   'pass': cov >= 100}

    with open(P('exit_rule_mapping.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['backtest_rule', 'live_rules',
                                          'live_trades'])
        w.writeheader()
        w.writerows(map_rows)
    with open(P('stop_execution_audit.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: swing_stop_delay.csv · exit_rule_mapping.csv · '
          f'stop_execution_audit.json')


if __name__ == '__main__':
    main()
